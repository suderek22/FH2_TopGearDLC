"""Validate legacy collision geometry and native FH2 streamed physics templates."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import sys

from fm5_pvs import Reader
from fh2_world import MiniZip, Resource, write_minizip
from fh2_pvs import write_root_groups60


def read_collision(data):
    if len(data) < 84:
        raise ValueError("Truncated legacy collision header")
    columns, rows = struct.unpack_from(">2I", data)
    size, version, magic = struct.unpack_from(">3I", data, 40)
    count, directory = struct.unpack_from(">2I", data, 76)
    if version != 2 or magic != 0xABCD1234 or size != len(data):
        raise ValueError("Invalid legacy collision size, envelope version or marker")
    if not 0 < columns <= 65535 or not 0 < rows <= 65535:
        raise ValueError("Invalid collision grid dimensions")
    if directory != 84 or count > columns * rows or directory + count * 24 > len(data):
        raise ValueError("Invalid collision cell directory")
    cells = []
    seen = set()
    end = directory + count * 24
    for index in range(count):
        pointer, ordinal, grid_id, offset, length, reserved = struct.unpack_from(
            ">6I", data, directory + index * 24)
        if (ordinal != index or grid_id >= columns * rows or grid_id in seen or
                offset < end or offset % 2048 or not length or length % 2048 or
                offset + length > len(data) or pointer != offset or reserved):
            raise ValueError("Invalid collision cell bounds or identity")
        seen.add(grid_id)
        end = offset + length
        block = data[offset:end]
        vertices, triangles, vertex_offset, triangle_offset, nx, nz = struct.unpack_from(">6I", block)
        grid_offset = struct.unpack_from(">I", block, 56)[0]
        if (vertex_offset != 64 or triangle_offset != 64 + vertices * 32 or
                grid_offset != triangle_offset + triangles * 32 or
                not nx or not nz or grid_offset + nx * nz * 4 > len(block)):
            raise ValueError("Invalid collision cell geometry layout")
        vertex_rows = [list(struct.unpack_from(">4fI", block, vertex_offset + i * 32))
                       for i in range(vertices)]
        triangle_rows = []
        for i in range(triangles):
            flags, a, b, c, *normal = struct.unpack_from(">4I4f", block, triangle_offset + i * 32)
            if any(p < vertex_offset or p >= triangle_offset or (p - vertex_offset) % 32
                   for p in (a, b, c)):
                raise ValueError("Collision triangle references an invalid vertex")
            triangle_rows.append({"flags": flags,
                                  "vertices": [(p - vertex_offset) // 32 for p in (a, b, c)],
                                  "normal": normal})
        if any(not math.isfinite(v) for row in vertex_rows for v in row[:4]) or any(
                not math.isfinite(v) for row in triangle_rows for v in row["normal"]):
            raise ValueError("Non-finite collision geometry")
        subcells = []
        for pointer in struct.unpack_from(">%dI" % (nx * nz), block, grid_offset):
            if pointer == 0:
                subcells.append(None)
                continue
            if pointer < grid_offset + nx * nz * 4 or pointer % 4 or pointer + 2 > len(block):
                raise ValueError("Invalid collision subcell pointer")
            size = struct.unpack_from(">H", block, pointer)[0]
            if pointer + 2 + size * 2 > len(block):
                raise ValueError("Truncated collision subcell references")
            refs = list(struct.unpack_from(">%dH" % size, block, pointer + 2))
            if any(ref >= triangles for ref in refs):
                raise ValueError("Collision subcell references an invalid triangle")
            subcells.append(refs)
        cells.append({"index": index, "grid_id": grid_id, "offset": offset, "size": length,
                      "grid_header": block[16:56].hex(), "vertices": vertex_rows,
                      "triangles": triangle_rows, "subcells": subcells,
                      "payload_sha256": hashlib.sha256(block).hexdigest()})
    margins = list(struct.unpack_from(">2f", data, 60))
    if any(not math.isfinite(v) or v < 0 for v in margins):
        raise ValueError("Invalid collision cell border")
    return {"columns": columns, "rows": rows, "envelope_version": version,
            "cell_border": margins,
            "source_sha256": hashlib.sha256(data).hexdigest(), "cells": cells}


def ground_hits(world, x, z):
    if not math.isfinite(x) or not math.isfinite(z):
        raise ValueError("Non-finite collision query")
    hits = []
    for cell in world["cells"]:
        for index, triangle in enumerate(cell["triangles"]):
            (x1, y1, z1, *_), (x2, y2, z2, *_), (x3, y3, z3, *_) = [
                cell["vertices"][v] for v in triangle["vertices"]]
            denominator = (z2 - z3) * (x1 - x3) + (x3 - x2) * (z1 - z3)
            if abs(denominator) < 1e-12:
                continue
            u = ((z2 - z3) * (x - x3) + (x3 - x2) * (z - z3)) / denominator
            v = ((z3 - z1) * (x - x3) + (x1 - x3) * (z - z3)) / denominator
            w = 1 - u - v
            if min(u, v, w) >= -1e-7:
                hits.append({"height": u * y1 + v * y2 + w * y3,
                             "flags": triangle["flags"], "cell": cell["index"], "triangle": index})
    return hits


def read_physics(data):
    reader = Reader(data, "FH2 physics template")
    result = {"name": reader.string(), "bounds": list(reader.unpack("8f")),
              "additional_bounds": list(reader.unpack("4f")),
              "grid": list(reader.unpack("2I8f"))}
    result["vertices"] = [list(reader.unpack("4fI")) for _ in range(reader.count(20))]
    result["triangles"] = [list(reader.unpack("2B3I4f")) for _ in range(reader.count(30))]
    result["grid_offsets"] = list(reader.unpack("%di" % reader.count(4)))
    result["grid_refs"] = list(reader.unpack("%dH" % reader.count(2)))
    if reader.offset != len(data):
        raise ValueError("Unparsed FH2 physics template trailer")
    nx, nz = result["grid"][:2]
    if nx <= 0 or nz <= 0 or nx * nz != len(result["grid_offsets"]):
        raise ValueError("Invalid FH2 physics grid dimensions")
    if any(index >= len(result["vertices"]) for t in result["triangles"] for index in t[2:5]):
        raise ValueError("FH2 physics triangle references an invalid vertex")
    for offset in result["grid_offsets"]:
        if offset == -1:
            continue
        refs = result["grid_refs"]
        if offset < 0 or offset >= len(refs) or offset + 1 + refs[offset] > len(refs):
            raise ValueError("Invalid FH2 physics subcell reference offset")
        if any(ref >= len(result["triangles"]) for ref in refs[offset + 1:offset + 1 + refs[offset]]):
            raise ValueError("FH2 physics subcell references an invalid triangle")
    values = result["bounds"] + result["additional_bounds"] + result["grid"][2:]
    values += [v for row in result["vertices"] for v in row[:4]]
    values += [v for row in result["triangles"] for v in row[5:]]
    if not all(math.isfinite(v) for v in values):
        raise ValueError("Non-finite FH2 physics template")
    return result


def write_physics(template):
    name = template["name"].encode("utf-8")
    data = bytearray(struct.pack("<I", len(name)) + name)
    data.extend(struct.pack("<8f4f2I8f", *template["bounds"],
                            *template["additional_bounds"], *template["grid"]))
    for key, fmt in (("vertices", "4fI"), ("triangles", "2B3I4f"),
                     ("grid_offsets", "i"), ("grid_refs", "H")):
        data.extend(struct.pack("<I", len(template[key])))
        for row in template[key]:
            data.extend(struct.pack("<" + fmt, *(row if isinstance(row, list) else [row])))
    result = bytes(data)
    if read_physics(result) != template:
        raise ValueError("FH2 physics serialization differs from its input")
    return result


# FM5 surfaces absent from FH2's surfaceTypes.xml, mapped to the FH2 surface with the same physics.
SURFACE_FALLBACKS = {"AsphaltTrackPenalty": "AsphaltTrack", "OffTrackVehicleBarrier": "ConcreteBarrier"}


def surface_names(path):
    import xml.etree.ElementTree as ET
    table = ET.parse(path).getroot().find("SurfaceTypes")
    if table is None or not len(table):
        raise ValueError("No SurfaceTypes table: " + str(path))
    return [row.tag for row in table]


def surface_remap(source_xml, target_xml):
    """Map source surfaceTypes indices to target indices by surface name.

    The triangle's second attribute byte indexes the game's SurfaceTypes list; FM5 and FH2
    lists diverge (FM5 119 AsphaltTrackPenalty is FH2 119 Snow), so bytes must not be copied.
    """
    source, target = surface_names(source_xml), surface_names(target_xml)
    index = {name: position for position, name in enumerate(target)}
    if len(index) != len(target) or len(target) > 256:
        raise ValueError("Target surface names are not unique byte indices")
    mapping = {}
    for position, name in enumerate(source):
        name = name if name in index else SURFACE_FALLBACKS.get(name)
        if name in index:
            mapping[position] = index[name]
    return mapping


def remap_triangle_surfaces(triangles, mapping):
    """Rewrite the surface byte of [flags, surface, ...] rows; unknown surfaces fail."""
    missing = sorted({row[1] for row in triangles} - set(mapping))
    if missing:
        raise ValueError("Collision uses surfaces with no target mapping: %s" % missing)
    return [[row[0], mapping[row[1]], *row[2:]] for row in triangles]


def physics_templates(world, surfaces=None):
    templates = []
    for cell in world["cells"]:
        grid = list(struct.unpack(">2I8f", bytes.fromhex(cell["grid_header"])))
        bx, bz = world["cell_border"]
        minimum_x, minimum_z, maximum_x, maximum_z = grid[2:6]
        inner = [minimum_x + bx, minimum_z + bz, maximum_x - bx, maximum_z - bz]
        if inner[0] > inner[2] or inner[1] > inner[3] or not cell["vertices"]:
            raise ValueError("Collision cell has invalid inner bounds or no vertices")
        ys = [row[1] for row in cell["vertices"]]
        offsets, refs = [], []
        for subcell in cell["subcells"]:
            offsets.append(-1 if subcell is None else len(refs))
            if subcell is not None:
                refs.extend([len(subcell), *subcell])
        triangles = []
        for row in cell["triangles"]:
            flags = row["flags"]
            if flags & 0xFFFF:
                raise ValueError("Unexpected legacy triangle padding; refusing to discard it")
            triangles.append([flags >> 24, (flags >> 16) & 255,
                              *row["vertices"], *row["normal"]])
        if surfaces is not None:
            triangles = remap_triangle_surfaces(triangles, surfaces)
        templates.append({
            "name": "topgear_cell%04d.phys" % cell["grid_id"],
            "bounds": [inner[0], min(ys), inner[1], 0.0, inner[2], max(ys), inner[3], 0.0],
            "additional_bounds": inner, "grid": grid,
            "vertices": [[*row[:3], 1.0, row[4]] for row in cell["vertices"]],
            "triangles": triangles, "grid_offsets": offsets, "grid_refs": refs})
    return templates


def build_physics(source, output, ground_at=None, surfaces=None):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a new physics component output directory: " + str(output))
    world = read_collision(Path(source).read_bytes())
    templates = physics_templates(world, surfaces)
    payloads = [write_physics(row) for row in templates]
    resources = [Resource(row["name"], index, 1) for index, row in enumerate(templates)]
    payload_by_name = {r.name: payload for r, payload in zip(resources, payloads)}
    output.mkdir(parents=True)
    chunk = output / "GeoChunk2.minizip"
    lookup = write_minizip(chunk, resources, lambda resource: payload_by_name[resource.name])
    archive = MiniZip(chunk)
    cells = []
    rebuilt_world = {"cells": []}
    for index, (source_cell, template, payload) in enumerate(zip(world["cells"], templates, payloads)):
        persisted = archive.read(index)
        rebuilt = read_physics(persisted)
        if persisted != payload or rebuilt != template:
            raise ValueError("Persisted physics template differs from its source conversion")
        triangles = [{"flags": (row[0] << 24) | (row[1] << 16),
                      "vertices": row[2:5], "normal": row[5:]} for row in rebuilt["triangles"]]
        expected = source_cell["triangles"]
        if surfaces is not None:
            expected = [dict(row, flags=(row["flags"] & 0xFF000000) |
                             (surfaces[(row["flags"] >> 16) & 255] << 16)) for row in expected]
        if triangles != expected or any(
                a[:3] != b[:3] or a[4] != b[4]
                for a, b in zip(rebuilt["vertices"], source_cell["vertices"])):
            raise ValueError("Converted collision geometry differs from the legacy source")
        rebuilt_world["cells"].append({"index": index, "vertices": rebuilt["vertices"],
                                       "triangles": triangles})
        cells.append({"resource_id": index, "source_grid_id": source_cell["grid_id"],
                      "name": template["name"], "bounds": template["bounds"],
                      "vertices": len(template["vertices"]), "triangles": len(template["triangles"]),
                      "sha256": hashlib.sha256(persisted).hexdigest()})
    (output / "ChunkMap2.dat").write_bytes(lookup)
    identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    roots = []
    for row in cells:
        bounds = [*row["bounds"][:7], 1.0]
        roots.append({"record": struct.pack("<24f2i", *identity, *bounds,
                                            row["resource_id"], 0).hex(), "settings": None})
    root_data = write_root_groups60(roots)
    (output / "CollisionRoots60.dat").write_bytes(root_data)
    report = {"component": "fh2-streamed-physics-templates", "schema_verified": True,
              "engine_validated": False, "playable_map": False, "cells": cells,
              "source_sha256": world["source_sha256"],
              "chunk_sha256": hashlib.sha256(chunk.read_bytes()).hexdigest(),
              "lookup_sha256": hashlib.sha256(lookup).hexdigest(),
              "collision_roots_sha256": hashlib.sha256(root_data).hexdigest(),
              "geometry_preserved": True,
              "surfaces_remapped": surfaces is not None,
              "vertex_w": "1.0 homogeneous points; legacy collision reads XYZ and discards source W.",
              "remaining": [
                  ("Surface bytes remapped to target surfaceTypes by name; the flag byte is preserved."
                   if surfaces is not None else
                   "Original triangle attribute bytes are preserved; FM5 surface indices are NOT FH2 indices."),
                  "Physics templates require collision root transforms and PVSZone dependencies before installation.",
                  "Legacy sparse grid IDs are reported separately from target resource ordinals.",
                  "This component is not a complete drivable map."]}
    if ground_at is not None:
        hits = ground_hits(world, *ground_at)
        converted_hits = ground_hits(rebuilt_world, *ground_at)
        strip = lambda rows: [dict(row, flags=0) for row in rows]
        if strip(converted_hits) != strip(hits):
            raise ValueError("Persisted converted collision ground query differs from the source")
        report["ground_query"] = {"x": ground_at[0], "z": ground_at[1], "hits": converted_hits,
                                  "persisted_conversion_matches": True}
    (output / "physics.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path)
    destination.add_argument("--component-output", type=Path)
    parser.add_argument("--ground-at", type=float, nargs=2, metavar=("X", "Z"))
    parser.add_argument("--source-surfaces", type=Path, help="source game physics\\surfaceTypes.xml")
    parser.add_argument("--target-surfaces", type=Path, help="FH2 physics\\surfaceTypes.xml")
    args = parser.parse_args()
    try:
        if args.component_output:
            if bool(args.source_surfaces) != bool(args.target_surfaces):
                raise ValueError("--source-surfaces and --target-surfaces must be given together")
            surfaces = (surface_remap(args.source_surfaces, args.target_surfaces)
                        if args.source_surfaces else None)
            report = build_physics(args.source, args.component_output, args.ground_at, surfaces)
            print("Verified %d converted FH2 physics templates; root binding remains." % len(report["cells"]))
            return 0
        world = read_collision(args.source.read_bytes())
        report = {"source_sha256": world["source_sha256"], "envelope_version": 2,
                  "grid": [world["columns"], world["rows"]], "cells": len(world["cells"]),
                  "vertices": sum(len(c["vertices"]) for c in world["cells"]),
                  "triangles": sum(len(c["triangles"]) for c in world["cells"]),
                  "geometry_verified": True, "engine_validated": False, "playable_map": False,
                  "remaining": ["Streamed template/root binding and material flags need conversion.",
                                "This validates source collision, not engine loading or safe arrival."]}
        if args.ground_at:
            report["ground_query"] = {"xz": args.ground_at, "hits": ground_hits(world, *args.ground_at)}
            if not report["ground_query"]["hits"]:
                raise ValueError("Requested arrival has no source collision surface")
        with args.output.open("x", encoding="utf-8") as file:
            json.dump(report, file, indent=2, allow_nan=False)
    except (OSError, ValueError) as error:
        print("error: " + str(error), file=sys.stderr)
        return 1
    print("Verified %d legacy collision cells and %d triangles." % (report["cells"], report["triangles"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
