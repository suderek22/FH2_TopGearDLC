"""Rebuild native free-roam HEXY cells and source-backed geometry visibility."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys
import xml.etree.ElementTree as ET
import zipfile

from build_zones import geometry_zones
from collision import read_collision
from fh2_world import MiniZip, Resource, read_pvs_zone60, write_minizip
from fm5_world import bind_models, world_tables
from navigation import geometry, route_visibility
from spatial import center, covering_grid, neighbors, read_hex, write_hex, zone_at


def spatial_zones(world, positions, grid):
    visibility = route_visibility(world, len(positions))
    route_count, step = visibility["route_zone_count"], visibility["waypoints_per_zone"]
    memberships = [set() for _ in grid["coordinates"]]
    for index, (x, _, z) in enumerate(positions):
        zone = zone_at(grid, x, z)
        if zone < 0:
            raise ValueError("Navigation waypoint is outside rebuilt spatial coverage")
        memberships[zone].add(index // step)
    source_zones = world["zones"]
    result, provenance = [], []
    for index in range(len(memberships)):
        x, z = center(grid, index)
        nearest = min(range(len(positions)),
                      key=lambda i: (positions[i][0] - x) ** 2 + (positions[i][2] - z) ** 2)
        sources = set(memberships[index])
        for adjacent in neighbors(grid, index):
            sources.update(memberships[adjacent])
        sources.add(nearest // step)
        expanded = set(sources)
        for source in sources:
            expanded.update(((source - 1) % route_count, (source + 1) % route_count))
        # Special source views remain visible everywhere until their selectors are decoded.
        expanded.update(range(route_count, len(source_zones)))
        instances, models, texture_levels = set(), set(), {}
        for source in sorted(expanded):
            row = source_zones[source]
            instances.update(row["instances"])
            models.update(row["models"])
            for texture, quality in zip(row["textures"], row["qualities"]):
                texture_levels[texture] = max(quality, texture_levels.get(texture, 0))
        textures = sorted(texture_levels)
        result.append({"index": index, "instances": sorted(instances), "models": sorted(models),
                       "textures": textures, "qualities": [texture_levels[t] for t in textures]})
        provenance.append({"index": index, "center": [x, z], "source_zones": sorted(expanded)})
    rebuilt_world = dict(world, zones=result)
    zones = geometry_zones(rebuilt_world)
    used = {instance for row in result for instance in row["instances"]}
    if not {instance for row in source_zones for instance in row["instances"]}.issubset(used):
        raise ValueError("Rebuilt spatial visibility lost a source-visible instance")
    return zones, provenance


def build(source_pvs, archive, source_ai, source_collision, output):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a new spatial component directory: " + str(output))
    world = bind_models(world_tables(source_pvs), archive)
    with zipfile.ZipFile(source_ai) as ai:
        positions = geometry(ai.read("Track.geo"))["positions"]
    collision = read_collision(Path(source_collision).read_bytes())
    points = [row[:3] for cell in collision["cells"] for row in cell["vertices"]]
    if not points:
        raise ValueError("Cannot build free-roam coverage without collision geometry")
    all_points = points + positions
    bounds = [min(row[0] for row in all_points), min(row[2] for row in all_points),
              max(row[0] for row in all_points), max(row[2] for row in all_points)]
    grid = covering_grid(bounds)
    for x, _, z in all_points:
        if zone_at(grid, x, z) < 0:
            raise ValueError("Rebuilt grid does not cover a collision vertex or navigation waypoint")
    zones, provenance = spatial_zones(world, positions, grid)
    payloads = {row["index"]: row["data"] for row in zones}
    resources = [Resource("Zone%04d.pvsz" % row["index"], row["index"], 12) for row in zones]
    output.mkdir(parents=True)
    hex_data = write_hex(grid)
    hex_path = output / "TopGearCPSout_00.hex"
    hex_path.write_bytes(hex_data)
    if read_hex(hex_path.read_bytes()) != grid:
        raise ValueError("Persisted native visibility grid differs from conversion")
    chunk = output / "GeoChunk1.minizip"
    lookup = write_minizip(chunk, resources, lambda resource: payloads[resource.resource_id])
    persisted = MiniZip(chunk)
    for index, row in enumerate(zones):
        payload = persisted.read(index)
        if payload != row["data"] or read_pvs_zone60(payload) != row["zone"]:
            raise ValueError("Persisted spatial PVSZone differs from conversion")
        provenance[index].update({
            "draws": len(row["zone"]["draws"]), "models": len(row["zone"]["models"]),
            "textures": len(row["zone"]["textures"]), "sha256": hashlib.sha256(payload).hexdigest(),
            "secondary_instances": row["secondary_instances"],
        })
    (output / "ChunkMap1.dat").write_bytes(lookup)
    # These component files assume one dedicated zone chunk; final assembly must use the same assignment.
    assignments = struct.pack("<2I", 100, len(zones)) + struct.pack("<%dI" % len(zones), *([1] * len(zones)))
    (output / "ChunkAssign.dat").write_bytes(assignments)
    report = {
        "component": "native-free-roam-spatial-geometry-visibility",
        "playable_map": False, "engine_validated": False, "visibility_coverage_verified": False,
        "native_grid_roundtrip_verified": True, "source_visible_instances_preserved": True,
        "collision_vertex_coverage_verified": True, "waypoint_coverage_verified": True,
        "zone_count": len(zones), "grid_columns": grid["columns"], "grid_rows": grid["rows"],
        "grid_radius": grid["radius"], "coordinate_bounds": bounds, "zones": provenance,
        "source_sha256": world["source_sha256"],
        "hex_sha256": hashlib.sha256(hex_data).hexdigest(),
        "lookup_sha256": hashlib.sha256(lookup).hexdigest(),
        "chunk_sha256": hashlib.sha256(chunk.read_bytes()).hexdigest(),
        "chunk_assignment_sha256": hashlib.sha256(assignments).hexdigest(),
        "remaining": [
            "Spatial geometry visibility is conservative source-view synthesis, not a complete offline occlusion bake.",
            "Source special zones are retained everywhere; their selectors and performance need validation.",
            "World FPVS root, PVSZLookup node IDs and non-geometry dependencies are not yet assembled.",
            "Static placements are reported, not registered by these geometry-only zones.",
            "Visibility away from the original route must be validated in game before activation.",
        ],
    }
    (output / "spatial.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-pvs", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--source-ai", type=Path, required=True)
    parser.add_argument("--source-collision", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = build(args.source_pvs, args.archive, args.source_ai, args.source_collision, args.output)
    except (OSError, ValueError, KeyError, ET.ParseError, zipfile.BadZipFile) as error:
        print("error: " + str(error), file=sys.stderr)
        return 1
    print("Verified %d native spatial geometry zones; full world registration remains blocked." %
          report["zone_count"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
