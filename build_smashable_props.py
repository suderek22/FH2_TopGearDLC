"""Turn the Top Gear H posts and KA brake boards into native FH2 smashables.

FH2 places smashables through three linked resources (all verified against Anthem):

* a ``SMASHABLETEMPLATE.plib`` entry per template, whose LOD references are root
  draw indices of a *template draw* (model at the origin, identity rotation);
* a ChunkMap type-0 placement cell listing ``[0, global_id, block, 0, count, -1, 0, 0]``
  groups and 32-byte records (u16 cell-relative position, model X/Y axes);
* ``PhysicsDefinitions.bin[global_id]``: a single-body smashable whose
  RenderModelIndex is the AssetManifest model id.

Zones request the cell through a new runtime node (unflagged + 0x80000000 flagged
dependency, like Anthem) and keep only the template draws; the static per-prop
draws are removed from every zone. Five Anthem sign templates with no children are
reused by name and id so the plib and definition tables keep their native layout.
"""
import argparse
import bisect
import copy
import json
import math
from pathlib import Path
import re
import struct

from build_media import media_catalog
from build_object_bounds import mesh_bboxes
from build_prop_test import _copy_pack, _update_track_catalog, atomic_write, digest
import fh2_physdefs
from fh2_pvs import read_root60, write_root60
from fh2_smash import Cell, TemplateBlock, make_placement, write_cell, parse_cell
from fh2_world import MiniZip, Resource, read_pvs_zone60, write_minizip, write_pvs_zone60

# Top Gear model id -> reused Anthem template (name, PhysicsDefinitions/global id).
TEMPLATES = {
    26: ("ITA_OBJ_Sign_Parking_3D", 201),                  # CO_TOPG_H_001
    50: ("ITA_OBJ_Sign_Noparking_3D", 200),                # SIGN_KA_100m
    51: ("ITA_OBJ_Sign_Noparking2Sides_3D", 199),          # SIGN_KA_50m
    52: ("ITA_OBJ_Sign_Tri_Junc_Minor_L_50m_3D", 228),     # SIGN_KA_Turn_001
    53: ("ITA_OBJ_Sign_Tri_Junc_Minor_R_50m_3D", 230),     # SIGN_KA_Turn_002
}
TEMPLATE_FLAGS = 0x003C00400FFF0004  # Anthem template draw descriptor flags
CELL_RESOURCE_ID = 0
CELL_NAME = "topgear_smashables_cellx0z0_section0"
LIBRARY = "SMASHABLETEMPLATE"
PLIB_RELATIVE = Path("ribbon_00") / "InstanceTemplates" / "objects" / "SMASHABLETEMPLATE.plib"
REF_MARK = b"\xff\xff\xff\x7f\x01\x00\x00\x00"
CURVE = re.compile(rb"SMASHABLE_3D[A-Z_]*DRAWDISTANCE")


def template_record(model_id, index):
    """73-byte root draw record matching Anthem's smashable template draws."""
    if not 0 <= index <= 0xFFFF:
        raise ValueError("Template draw index exceeds the native u16 slot")
    identity = struct.pack("<9e", 1, 0, 0, 0, 1, 0, 0, 0, 1)
    return (struct.pack("<IQH", model_id, TEMPLATE_FLAGS, index) + b"\xff" * 4 +
            bytes.fromhex("00bc00bc00bc00bc00") + bytes(12) + identity + bytes(16))


def plib_entries(data):
    """[(offset, name, bbox_offset)] for every template entry (name followed by a curve)."""
    entries = []
    for match in CURVE.finditer(data):
        curve_at = match.start() - 4
        if struct.unpack_from("<I", data, curve_at)[0] != len(match.group(0)):
            continue
        for length in range(3, 96):
            start = curve_at - 4 - length
            if start >= 0 and struct.unpack_from("<I", data, start)[0] == length and \
                    all(32 < c < 127 for c in data[start + 4:curve_at]):
                entries.append((start, data[start + 4:curve_at].decode("ascii"), match.end()))
                break
    return entries


def plib_refs(data):
    """[(index_offset, max_basename)] for every model reference in the library."""
    refs = []
    for match in re.finditer(re.escape(REF_MARK), data):
        at = match.start()
        path_end = at - 4
        for length in range(5, 256):
            start = path_end - length - 4
            if start < 0:
                break
            if struct.unpack_from("<I", data, start)[0] == length and data[start + 4:path_end].endswith(b".max"):
                name = data[start + 4:path_end].decode("ascii").rsplit("\\", 1)[-1][:-4]
                refs.append((at + 8, name))
                break
        else:
            raise ValueError("Library reference at %d has no model path" % at)
    return refs


def patch_plib(data, templates, fallback_draw):
    """templates: {name: (draw_index, lo, hi)}. Returns (bytes, {name: ref_count}).

    Every unused Anthem reference is pointed at ``fallback_draw`` (a template draw)
    so no library entry can index outside the Top Gear root.
    """
    result = bytearray(data)
    entries = plib_entries(data)
    by_name = {}
    for offset, name, bbox_at in entries:
        by_name.setdefault(name, []).append(bbox_at)
    counts = {}
    for name, (_, lo, hi) in templates.items():
        if len(by_name.get(name, [])) != 1:
            raise ValueError("Library does not contain exactly one %s entry" % name)
        struct.pack_into("<8f", result, by_name[name][0], *lo, 0.0, *hi, 0.0)
        counts[name] = 0
    starts = [offset for offset, _, _ in entries]
    for index_at, model in plib_refs(data):
        owner = entries[bisect.bisect_right(starts, index_at) - 1][1] if index_at > starts[0] else None
        draw = fallback_draw
        if owner in templates:
            if owner != model + "_3D":
                raise ValueError("Library reference %s does not belong to %s" % (model, owner))
            draw = templates[owner][0]
            counts[owner] += 1
        struct.pack_into("<I", result, index_at, draw)
    if any(count == 0 for count in counts.values()):
        raise ValueError("A reused template has no model references")
    return bytes(result), counts


def placement_axes(rotation_rows):
    x_axis, y_axis = rotation_rows[0:3], rotation_rows[3:6]
    for axis in (x_axis, y_axis):
        if abs(math.hypot(*axis) - 1) > 0.02:
            raise ValueError("Prop rotation is not orthonormal")
    return x_axis, y_axis


def build_cell(groups, boxes, global_ids, names):
    """groups: {model: [draw]}; boxes: {model: (lo, hi)}. Returns the cell bytes."""
    half_diagonals = {model: math.dist(*boxes[model]) / 2 for model in groups}
    scale = max(half_diagonals.values())
    positions = [draw["position"] for draws in groups.values() for draw in draws]
    pad = scale + 1.0
    bmin = tuple(min(p[a] for p in positions) - pad for a in range(3))
    bmax = tuple(max(p[a] for p in positions) + pad for a in range(3))
    order = sorted(groups, key=lambda model: names[model])
    cell = Cell(CELL_NAME, 0, 5, bmin + (0.0,), bmax + (1.0,), LIBRARY, scale, "", 0,
                [(0, global_ids[model], block, 0, len(groups[model]), -1, 0, 0)
                 for block, model in enumerate(order)], 0)
    for model in order:
        cell.templates.append(TemplateBlock(names[model], [
            make_placement(draw["position"], bmin, bmax, *placement_axes(draw["rotation_rows"]))
            for draw in groups[model]]))
    cell.trailer = b"\0\0\0\0"
    data = write_cell(cell)
    if write_cell(parse_cell(data)) != data:
        raise ValueError("Placement cell failed serialization verification")
    return data, cell


def add_zone_request(zone, node, drop_draws):
    result = dict(zone)
    draws = zone["draws"]
    if not drop_draws <= {draw & 0x3FFFFFFF for draw in draws}:
        raise ValueError("Zone does not reference every replaced prop draw")
    result["draws"] = [draw for draw in draws if draw & 0x3FFFFFFF not in drop_draws]
    deps = [list(row) for row in zone["dependencies"]]
    if any(row[0] >= node for row in deps):
        raise ValueError("Zone already uses the smashable runtime node")
    result["dependencies"] = deps + [[node, CELL_RESOURCE_ID], [node, 0x80000000 | CELL_RESOURCE_ID]]
    return result


def build(source_track, anthem, output, visible_distance=None):
    source_track, anthem, output = (Path(p).resolve() for p in (source_track, anthem, output))
    if not source_track.is_dir() or output.exists():
        raise ValueError("Source pack must exist and output must be new")
    if source_track in output.parents or output in source_track.parents:
        raise ValueError("Source and output pack directories must be separate")
    source_media = source_track / "media"
    report = json.loads((source_media / "media.json").read_text(encoding="utf-8"))
    if report.get("smashable_props"):
        raise ValueError("Source already has smashable props")
    if not report.get("prop_size_cull_disabled"):
        raise ValueError("Source must be the no-size-cull prop pack")
    if digest(source_media / report["catalog_name"]) != report["catalog_sha256"]:
        raise ValueError("Source media catalog checksum mismatch")
    relative = Path(report["override_relative_path"])
    source_root = source_media / relative
    root_relative = Path("ribbon_00") / (report["media_name"] + "_00.pvs")
    root = read_root60((source_root / root_relative).read_bytes())
    keys = [row[0] for row in struct.iter_unpack("<I", (source_root / "ChunkMap0.dat").read_bytes())]
    archive = MiniZip(source_root / "GeoChunk0.minizip")
    if len(keys) != len(archive.entries) or (source_root / "ChunkMap1.dat").exists():
        raise ValueError("Expected a valid single-chunk media archive")
    if any(key >> 24 == 0 for key in keys) or root["header_fields"][0]:
        raise ValueError("Source already contains placement cells")
    lookup = {key: index for index, key in enumerate(keys)}

    groups = {model: [] for model in TEMPLATES}
    for index, draw in enumerate(root["primary_draws"]):
        if draw["model_id"] in groups:
            groups[draw["model_id"]].append(dict(draw, index=index))
    if any(not rows for rows in groups.values()):
        raise ValueError("A Top Gear prop model has no placements")
    boxes = {}
    for model in groups:
        meshes = mesh_bboxes(archive.read(lookup[0x04000000 | model]))
        boxes[model] = (tuple(min(b[0][a] for b in meshes) for a in range(3)),
                        tuple(max(b[1][a] for b in meshes) for a in range(3)))
    names = {model: TEMPLATES[model][0] for model in groups}
    global_ids = {model: TEMPLATES[model][1] for model in groups}
    template_draws = {model: rows[0]["index"] for model, rows in groups.items()}
    dropped = {row["index"] for rows in groups.values() for row in rows} - set(template_draws.values())

    updated = copy.deepcopy(root)
    for model, index in template_draws.items():
        draw = updated["primary_draws"][index]
        draw["source_record"] = template_record(model, index).hex()
    node = root["header_fields"][1]
    cell_data, cell = build_cell(groups, boxes, global_ids, names)

    zone_indexes = {key: index for index, key in enumerate(keys) if key >> 24 == 12}
    if set(zone_indexes) != {0x0C000000 | i for i in range(root["zone_count"])}:
        raise ValueError("Source media zone set differs from the root")
    new_zones, max_deps = {}, 0
    for key, index in zone_indexes.items():
        zone = add_zone_request(read_pvs_zone60(archive.read(index)), node, dropped)
        max_deps = max(max_deps, len(zone["dependencies"]))
        new_zones[key] = write_pvs_zone60(zone)
    fields = updated["header_fields"]
    updated["header_fields"] = [1, node + 1, fields[2], fields[3], max(fields[4], max_deps)]
    root_data = write_root60(updated)
    reread = read_root60(root_data)
    if write_root60(reread) != root_data:
        raise ValueError("Root failed serialization verification")
    for model, index in template_draws.items():
        draw = reread["primary_draws"][index]
        if draw["model_id"] != model or draw["position"] != [0.0, 0.0, 0.0]:
            raise ValueError("Template draw did not serialize as expected")

    physics_path = source_root / "PhysicsDefinitions.bin"
    physics = fh2_physdefs.parse(physics_path.read_bytes())
    for model, gid in global_ids.items():
        donor = physics.definitions[gid]
        physics.definitions[gid] = fh2_physdefs.box_smashable(
            donor, *boxes[model], donor.mass, model, visible_distance)
    physics_data = fh2_physdefs.write(physics)
    if fh2_physdefs.write(fh2_physdefs.parse(physics_data)) != physics_data:
        raise ValueError("Physics definitions failed serialization verification")

    anthem_plib = anthem / PLIB_RELATIVE
    plib_data, ref_counts = patch_plib(
        anthem_plib.read_bytes(),
        {names[m]: (template_draws[m], *boxes[m]) for m in groups}, template_draws[26])

    output.parent.mkdir(parents=True, exist_ok=True)
    _copy_pack(source_track, output, source_root)
    output_media = output / "media"
    output_root = output_media / relative
    resources = [Resource(str(i), key & 0xFFFFFF, key >> 24) for i, key in enumerate(keys)]
    resources.append(Resource("cell", CELL_RESOURCE_ID, 0))

    def read_resource(resource):
        if resource.name == "cell":
            return cell_data
        key = keys[int(resource.name)]
        return new_zones.get(key) or archive.read(int(resource.name))

    chunk_map = write_minizip(output_root / "GeoChunk0.minizip", resources, read_resource,
                              folder_starts=(0, len(keys)))
    atomic_write(output_root / "ChunkMap0.dat", chunk_map)
    atomic_write(output_root / root_relative, root_data)
    atomic_write(output_root / "PhysicsDefinitions.bin", physics_data)
    (output_root / PLIB_RELATIVE).parent.mkdir(parents=True, exist_ok=True)
    atomic_write(output_root / PLIB_RELATIVE, plib_data)

    changed = {relative / name for name in ("GeoChunk0.minizip", "ChunkMap0.dat", "PhysicsDefinitions.bin")}
    changed.add(relative / root_relative)
    for row in report["files"]:
        if Path(row["path"]) in changed:
            path = output_media / row["path"]
            row["bytes"], row["sha256"] = path.stat().st_size, digest(path)
            changed.discard(Path(row["path"]))
    if changed:
        raise ValueError("Media inventory is missing a rebuilt file")
    plib_path = relative / PLIB_RELATIVE
    media_path = str(Path(*plib_path.parts[1:]))
    report["files"].append({
        "path": str(plib_path), "bytes": (output_media / plib_path).stat().st_size,
        "sha256": digest(output_media / plib_path), "media_path": media_path,
        "game_media_path": "game:\\media\\" + media_path, "override_relative_path": str(plib_path),
    })
    report["smashable_props"] = {
        "source_catalog_sha256": report["catalog_sha256"],
        "templates": {names[m]: {"model": m, "global_id": global_ids[m], "template_draw": template_draws[m],
                                 "placements": len(groups[m]), "library_refs": ref_counts[names[m]],
                                 "bbox": [list(boxes[m][0]), list(boxes[m][1])]} for m in groups},
        "removed_static_draws": sorted(dropped), "runtime_node": node,
        "cell_resource_key": CELL_RESOURCE_ID, "cell_scale": cell.scale,
        "visible_distance": visible_distance, "in_game_validated": False,
    }
    catalog = output_media / report["catalog_name"]
    atomic_write(catalog, media_catalog(report["media_name"], report["files"]))
    report["catalog_sha256"] = digest(catalog)
    atomic_write(output_media / "media.json", (json.dumps(report, indent=2) + "\n").encode("utf-8"))
    _update_track_catalog(output / "track.ini", report["catalog_sha256"])

    rebuilt = MiniZip(output_root / "GeoChunk0.minizip")
    new_keys = [row[0] for row in struct.iter_unpack("<I", chunk_map)]
    if new_keys[:-1] != keys or new_keys[-1] != CELL_RESOURCE_ID:
        raise ValueError("Rebuilt chunk map changed existing resources")
    if rebuilt.read(len(keys)) != cell_data:
        raise ValueError("Placement cell did not persist")
    if rebuilt.folders != (0, len(keys), len(keys) + 1):
        raise ValueError("Placement cell is not isolated in its own archive folder")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-track", type=Path, required=True)
    parser.add_argument("--anthem", type=Path, required=True, help="Anthem track media folder")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--visible-distance", type=float)
    args = parser.parse_args()
    try:
        report = build(args.source_track, args.anthem, args.output, args.visible_distance)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    print(json.dumps(dict(report["smashable_props"], catalog_sha256=report["catalog_sha256"]), indent=2))


if __name__ == "__main__":
    main()
