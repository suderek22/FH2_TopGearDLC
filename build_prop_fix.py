"""Top Gear H-sign/KA-board test: fill LOD band gaps and add static box collision.

The H sign (model 26) only has LOD0 and the KA boards (models 50-53) only LOD0/LOD1,
but their draws keep the generic 100/200/350/5000 bands. Bands from the model's last
LOD onwards are set to the final cull distance so the last real LOD keeps drawing.

Collision is static: one oriented box per draw is appended to every streamed physics
cell template whose inner area it touches, so no templates, root ordinals or zone
bindings are added. The props become solid, not knock-over objects.
"""
import argparse
import copy
import json
import math
from pathlib import Path
import struct
import sys

from build_media import media_catalog
from build_prop_test import _copy_pack, _update_track_catalog, atomic_write, digest
from collision import read_physics, surface_names, write_physics
from fh2_pvs import read_root60, write_root60
from fh2_world import MiniZip, Resource, write_minizip

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "CarPackStudio"))
from cps.bundle import Bundle  # noqa: E402

TARGET_MODELS = {26: "H sign", 50: "KA board", 51: "KA board", 52: "KA board", 53: "KA board"}
EXPECTED_DRAWS = 19
SURFACE = "BrakeMarkerMetal"
# Most common vertex word in the converted FM5 collision; its meaning is not decoded.
VERTEX_WORD = 0x7F800000
MIN_HALF_THICKNESS = 0.1
GROUND_SINK = 0.2

# (corner indices, outward local axis); winding gives cross(v1-v0, v2-v0) along the normal.
BOX_FACES = (((0, 1, 3, 2), (-1, 0, 0)), ((4, 6, 7, 5), (1, 0, 0)),
             ((0, 4, 5, 1), (0, -1, 0)), ((2, 3, 7, 6), (0, 1, 0)),
             ((0, 2, 6, 4), (0, 0, -1)), ((1, 5, 7, 3), (0, 0, 1)))


def model_lod_info(data):
    """Return (max LOD, local [min xyz, max xyz]) for a burG model resource."""
    bundle = Bundle(data)
    header = bundle.all("Modl")
    meshes = bundle.all("Mesh")
    if len(header) != 1 or not meshes:
        raise ValueError("Unexpected model bundle layout")
    boxes = [struct.unpack("<6f", mesh.meta["BBox"]) for mesh in meshes]
    lower = [min(box[axis] for box in boxes) for axis in range(3)]
    upper = [max(box[axis + 3] for box in boxes) for axis in range(3)]
    return header[0].data[9], lower + upper


def fill_lod_gaps(root, targets):
    """targets: {draw index: max LOD}. Returns a root copy and {draw: (old, new) bands}."""
    result = copy.deepcopy(root)
    changes = {}
    for index, max_lod in targets.items():
        draw = result["primary_draws"][index]
        raw = bytearray.fromhex(draw["source_record"])
        bands = list(struct.unpack_from("<4e", raw, 18))
        if not 0 <= max_lod < 3 or bands != sorted(bands) or bands[0] <= 0:
            raise ValueError("Unexpected LOD bands on draw %d: %r" % (index, bands))
        updated = bands[:max_lod] + [bands[3]] * (4 - max_lod)
        struct.pack_into("<4e", raw, 18, *updated)
        draw["source_record"] = raw.hex()
        changes[index] = (bands, updated)
    return result, changes


def box_geometry(position, rows, local):
    """World corners and faces of a model-space box placed by a draw (rows = world axes)."""
    lower, upper = list(local[:3]), list(local[3:])
    for axis in (0, 2):
        middle = (lower[axis] + upper[axis]) / 2
        half = max((upper[axis] - lower[axis]) / 2, MIN_HALF_THICKNESS)
        lower[axis], upper[axis] = middle - half, middle + half
    lower[1] = min(lower[1], 0.0) - GROUND_SINK
    axes = [rows[0:3], rows[3:6], rows[6:9]]
    corners = []
    for ix in (0, 1):
        for iy in (0, 1):
            for iz in (0, 1):
                local_point = (upper[0] if ix else lower[0], upper[1] if iy else lower[1],
                               upper[2] if iz else lower[2])
                corners.append([position[j] + sum(local_point[k] * axes[k][j] for k in range(3))
                                for j in range(3)])
    faces = []
    for quad, normal in BOX_FACES:
        world = [sum(normal[k] * axes[k][j] for k in range(3)) for j in range(3)]
        length = math.sqrt(sum(value * value for value in world))
        world = [value / length for value in world]
        faces.append(((quad[0], quad[1], quad[2]), world))
        faces.append(((quad[0], quad[2], quad[3]), world))
    return corners, faces


def _f32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _intersects(a_min, a_max, b_min, b_max):
    return all(a_min[i] <= b_max[i] and b_min[i] <= a_max[i] for i in range(len(a_min)))


def add_box(template, corners, faces, surface):
    """Append one box to a physics template; returns the updated copy."""
    result = copy.deepcopy(template)
    corners = [[_f32(value) for value in corner] for corner in corners]
    nx, nz, grid_x, grid_z, grid_max_x, grid_max_z, cell_x, cell_z = result["grid"][:8]
    xs, ys, zs = ([corner[axis] for corner in corners] for axis in range(3))
    if min(xs) < grid_x or max(xs) > grid_max_x or min(zs) < grid_z or max(zs) > grid_max_z:
        raise ValueError("Collision box leaves the physics template grid")
    base = len(result["vertices"])
    result["vertices"].extend([*corner, 1.0, VERTEX_WORD] for corner in corners)
    first = len(result["triangles"])
    for (a, b, c), normal in faces:
        result["triangles"].append([0, surface, base + a, base + b, base + c,
                                    *(_f32(value) for value in normal), 0.0])
    if len(result["triangles"]) > 0xFFFF:
        raise ValueError("Physics template exceeds the 16-bit triangle reference range")
    cells = []
    refs = result["grid_refs"]
    for offset in result["grid_offsets"]:
        cells.append(None if offset == -1 else list(refs[offset + 1:offset + 1 + refs[offset]]))
    new = list(range(first, len(result["triangles"])))
    for iz in range(max(0, int((min(zs) - grid_z) // cell_z)), min(nz - 1, int((max(zs) - grid_z) // cell_z)) + 1):
        for ix in range(max(0, int((min(xs) - grid_x) // cell_x)), min(nx - 1, int((max(xs) - grid_x) // cell_x)) + 1):
            cell = iz * nx + ix
            cells[cell] = (cells[cell] or []) + new
    offsets, refs = [], []
    for cell in cells:
        if cell is None:
            offsets.append(-1)
        else:
            if len(cell) > 0xFFFF:
                raise ValueError("Physics subcell exceeds the 16-bit reference count")
            offsets.append(len(refs))
            refs.extend([len(cell), *cell])
    if len(refs) > 0x7FFFFFFF:
        raise ValueError("Physics subcell reference table is too large")
    result["grid_offsets"], result["grid_refs"] = offsets, refs
    bounds = result["bounds"]
    for axis, values in enumerate((xs, ys, zs)):
        bounds[axis] = min(bounds[axis], min(values))
        bounds[axis + 4] = max(bounds[axis + 4], max(values))
    return result


def owning_templates(templates, corners):
    xs, zs = [c[0] for c in corners], [c[2] for c in corners]
    owners = [index for index, template in enumerate(templates)
              if _intersects([min(xs), min(zs)], [max(xs), max(zs)],
                             template["additional_bounds"][:2], template["additional_bounds"][2:])]
    if not owners:
        raise ValueError("Collision box lies outside every physics template")
    return owners


def update_root_bounds(root, templates):
    result = copy.deepcopy(root)
    for index, template in enumerate(templates):
        row = result["root_groups"][index]
        raw = bytearray.fromhex(row["record"])
        values = struct.unpack_from("<24f2i", raw)
        if values[24] != index:
            raise ValueError("Collision root ordinal does not reference its template")
        struct.pack_into("<3f", raw, 64, *template["bounds"][0:3])
        struct.pack_into("<3f", raw, 80, *template["bounds"][4:7])
        row["record"] = raw.hex()
    return result


def build(source_track, output, surface_types):
    source_track, output = Path(source_track).resolve(), Path(output).resolve()
    if not source_track.is_dir() or output.exists():
        raise ValueError("Source pack must exist and output must be new")
    if source_track in output.parents or output in source_track.parents:
        raise ValueError("Source and output pack directories must be separate")
    source_media = source_track / "media"
    report = json.loads((source_media / "media.json").read_text(encoding="utf-8"))
    if report.get("prop_fix"):
        raise ValueError("Source already has the prop fix applied")
    if digest(source_media / report["catalog_name"]) != report["catalog_sha256"]:
        raise ValueError("Source media catalog checksum mismatch")
    names = surface_names(surface_types)
    if SURFACE not in names or names.index(SURFACE) > 255:
        raise ValueError("Target surface is unavailable: " + SURFACE)
    surface = names.index(SURFACE)

    relative = Path(report["override_relative_path"])
    source_root = source_media / relative
    root_relative = Path("ribbon_00") / (report["media_name"] + "_00.pvs")
    root = read_root60((source_root / root_relative).read_bytes())
    chunk_map = source_root / "ChunkMap0.dat"
    keys = [row[0] for row in struct.iter_unpack("<I", chunk_map.read_bytes())]
    archive = MiniZip(source_root / "GeoChunk0.minizip")
    if len(keys) != len(archive.entries) or (source_root / "ChunkMap1.dat").exists():
        raise ValueError("Expected a valid single-chunk media archive")
    lookup = {key: index for index, key in enumerate(keys)}
    physics_count = len(root["root_groups"])
    if {key for key in keys if key >> 24 == 1} != {0x01000000 | i for i in range(physics_count)}:
        raise ValueError("Physics templates are not densely bound to collision roots")

    models = {model: model_lod_info(archive.read(lookup[0x04000000 | model])) for model in TARGET_MODELS}
    targets = {index: draw for index, draw in enumerate(root["primary_draws"])
               if draw["model_id"] in TARGET_MODELS}
    if len(targets) != EXPECTED_DRAWS:
        raise ValueError("Expected %d H/KA draws, found %d" % (EXPECTED_DRAWS, len(targets)))
    for index, draw in targets.items():
        refs = set(root["groups"][draw["model_id"]]["references"]) - {0xFFFFFFFF, draw["model_id"]}
        if refs or struct.unpack_from("<H", bytes.fromhex(draw["source_record"]), 10)[0]:
            raise ValueError("Target draw %d unexpectedly uses linked LOD models" % index)

    updated, band_changes = fill_lod_gaps(root, {i: models[d["model_id"]][0] for i, d in targets.items()})
    templates = [read_physics(archive.read(lookup[0x01000000 | i])) for i in range(physics_count)]
    boxes = []
    for index, draw in targets.items():
        corners, faces = box_geometry(draw["position"], draw["rotation_rows"], models[draw["model_id"]][1])
        owners = owning_templates(templates, corners)
        for owner in owners:
            templates[owner] = add_box(templates[owner], corners, faces, surface)
        boxes.append({"draw": index, "model": draw["model_id"], "templates": owners,
                      "position": [round(value, 3) for value in draw["position"]]})
    changed_templates = sorted({owner for box in boxes for owner in box["templates"]})
    updated = update_root_bounds(updated, templates)
    payloads = {0x01000000 | i: write_physics(templates[i]) for i in changed_templates}

    data = write_root60(updated)
    if write_root60(read_root60(data)) != data:
        raise ValueError("Prop-fix root failed serialization verification")
    before, after = root["primary_draws"], read_root60(data)["primary_draws"]
    if any(a != b for i, (a, b) in enumerate(zip(before, after)) if i not in targets):
        raise ValueError("Prop fix changed a non-target draw")

    output.parent.mkdir(parents=True, exist_ok=True)
    _copy_pack(source_track, output, source_root)
    output_media = output / "media"
    output_archive = output_media / relative / "GeoChunk0.minizip"
    resources = [Resource(str(index), key & 0xFFFFFF, key >> 24) for index, key in enumerate(keys)]
    rebuilt_lookup = write_minizip(
        output_archive, resources,
        lambda resource: payloads.get(keys[int(resource.name)]) or archive.read(int(resource.name)))
    if rebuilt_lookup != chunk_map.read_bytes():
        raise ValueError("Rebuilt media archive lookup differs from its source")
    atomic_write(output_media / relative / root_relative, data)
    rebuilt = MiniZip(output_archive)
    for index, key in enumerate(keys):
        expected = payloads.get(key)
        if expected is not None and rebuilt.read(index) != expected:
            raise ValueError("Rebuilt physics template differs from its payload")

    for row in report["files"]:
        if Path(row["path"]) in {relative / root_relative, relative / "GeoChunk0.minizip"}:
            path = output_media / row["path"]
            row["bytes"], row["sha256"] = path.stat().st_size, digest(path)
    report["prop_fix"] = {
        "source_catalog_sha256": report["catalog_sha256"],
        "models": {str(model): name for model, name in TARGET_MODELS.items()},
        "lod_band_fill": {str(i): {"before": old, "after": new} for i, (old, new) in band_changes.items()},
        "static_collision_boxes": boxes, "surface": SURFACE,
        "changed_physics_templates": changed_templates,
        "collision_is_static": True, "pop_in_validated": False, "collision_validated": False,
    }
    catalog = output_media / report["catalog_name"]
    atomic_write(catalog, media_catalog(report["media_name"], report["files"]))
    report["catalog_sha256"] = digest(catalog)
    atomic_write(output_media / "media.json", (json.dumps(report, indent=2) + "\n").encode("utf-8"))
    _update_track_catalog(output / "track.ini", report["catalog_sha256"])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-track", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--surface-types", type=Path, required=True,
                        help="FH2 media\\physics\\surfaceTypes.xml")
    args = parser.parse_args()
    try:
        report = build(args.source_track, args.output, args.surface_types)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    summary = {key: value for key, value in report["prop_fix"].items() if key not in ("static_collision_boxes", "lod_band_fill")}
    print(json.dumps(dict(summary, boxes=len(report["prop_fix"]["static_collision_boxes"]),
                          band_filled_draws=len(report["prop_fix"]["lod_band_fill"]),
                          catalog_sha256=report["catalog_sha256"]), indent=2))


if __name__ == "__main__":
    main()
