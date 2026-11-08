"""Bind rebuilt spatial zones to verified native physics resources and node IDs."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

from fh2_pvs import read_root_groups60
from fh2_world import MiniZip, Resource, read_pvs_zone60, write_minizip, write_pvs_zone60
from fm5_pvs import Reader
from collision import read_physics
from spatial import read_hex


def verified_bytes(path, expected):
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("Component checksum differs from its report: " + str(path))
    return data


def verify_archive(path, expected):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise ValueError("Component archive checksum differs from its report: " + str(path))
    return MiniZip(path)


def bind_zones(zones, physics_ids):
    if not zones or len(zones) > 65535:
        raise ValueError("Invalid spatial zone count")
    if physics_ids != list(range(len(physics_ids))) or not 1 <= len(physics_ids) <= 65535:
        raise ValueError("Physics IDs must cover a nonempty dense native template table")
    lookup = list(range(len(zones)))
    physics_start = len(zones)
    physics_dependencies = [[physics_start + resource_id, 0x01000000 | resource_id]
                            for resource_id in physics_ids]
    bound = []
    for zone in zones:
        if zone["dependencies"]:
            raise ValueError("Input geometry zones already contain dependencies; refusing to replace them")
        # Native HomeSpace02 includes all 529 physics resources in each zone.
        bound.append(dict(zone, dependencies=[list(row) for row in physics_dependencies]))
    nodes = [{"index": index, "key": 0x0C000000 | index, "kind": "pvs-zone"}
             for index in lookup]
    nodes += [{"index": index, "key": key, "kind": "physics"}
              for index, key in physics_dependencies]
    return bound, lookup, nodes


def validate_bindings(zones, lookup, nodes, physics_count):
    if len(lookup) != len(zones) or [row["index"] for row in nodes] != list(range(len(nodes))):
        raise ValueError("Invalid runtime binding table sizes")
    node_keys = {row["index"]: row["key"] for row in nodes}
    if len(set(node_keys.values())) != len(nodes):
        raise ValueError("Duplicate runtime resource key")
    for index, node in enumerate(lookup):
        if node_keys.get(node) != (0x0C000000 | index):
            raise ValueError("PVSZLookup node does not bind its zone key")
    expected = {0x01000000 | index for index in range(physics_count)}
    for zone in zones:
        keys = []
        for node, key in zone["dependencies"]:
            if node_keys.get(node) != key:
                raise ValueError("Dependency node/key disagrees with the binding table")
            keys.append(key)
        if set(keys) != expected or len(keys) != physics_count:
            raise ValueError("Zone does not bind every expected collision template exactly once")


def build(spatial, physics, output):
    spatial, physics, output = Path(spatial).resolve(), Path(physics).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a new binding component directory: " + str(output))
    spatial_report = json.loads((spatial / "spatial.json").read_text(encoding="utf-8"))
    physics_report = json.loads((physics / "physics.json").read_text(encoding="utf-8"))
    if (spatial_report["component"] != "native-free-roam-spatial-geometry-visibility" or
            physics_report["component"] != "fh2-streamed-physics-templates" or
            not spatial_report["native_grid_roundtrip_verified"] or not physics_report["geometry_preserved"]):
        raise ValueError("Expected verified spatial and physics conversion components")
    grid_data = verified_bytes(spatial / "TopGearCPSout_00.hex", spatial_report["hex_sha256"])
    grid = read_hex(grid_data)
    zone_count = spatial_report["zone_count"]
    if zone_count != len(grid["coordinates"]) or len(spatial_report["zones"]) != zone_count:
        raise ValueError("Spatial component zone counts disagree")
    assignments = verified_bytes(spatial / "ChunkAssign.dat", spatial_report["chunk_assignment_sha256"])
    if assignments != struct.pack("<2I", 100, zone_count) + struct.pack(
            "<%dI" % zone_count, *([1] * zone_count)):
        raise ValueError("Expected dedicated spatial zone chunk 1")
    zone_map = verified_bytes(spatial / "ChunkMap1.dat", spatial_report["lookup_sha256"])
    if zone_map != struct.pack("<%dI" % zone_count, *(0x0C000000 | i for i in range(zone_count))):
        raise ValueError("Spatial keys do not cover the native grid zone IDs")
    archive = verify_archive(spatial / "GeoChunk1.minizip", spatial_report["chunk_sha256"])
    if len(archive.entries) != zone_count:
        raise ValueError("Spatial archive count differs from the native grid")
    zones = []
    for index, row in enumerate(spatial_report["zones"]):
        data = archive.read(index)
        if row["index"] != index or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError("Spatial zone payload or ordinal differs from its report")
        zones.append(read_pvs_zone60(data))
    cell_count = len(physics_report["cells"])
    physics_map = verified_bytes(physics / "ChunkMap2.dat", physics_report["lookup_sha256"])
    if physics_map != struct.pack("<%dI" % cell_count, *(0x01000000 | i for i in range(cell_count))):
        raise ValueError("Physics keys do not cover dense template resource IDs")
    physics_archive = verify_archive(physics / "GeoChunk2.minizip", physics_report["chunk_sha256"])
    if len(physics_archive.entries) != cell_count:
        raise ValueError("Physics archive count differs from its template table")
    for index, row in enumerate(physics_report["cells"]):
        data = physics_archive.read(index)
        if row["resource_id"] != index or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError("Physics template payload or ordinal differs from its report")
        template = read_physics(data)
        if template["name"] != row["name"] or template["bounds"] != row["bounds"]:
            raise ValueError("Physics template binding differs from its report")
    roots_data = verified_bytes(physics / "CollisionRoots60.dat", physics_report["collision_roots_sha256"])
    reader = Reader(roots_data, "Collision root binding")
    roots = read_root_groups60(reader)
    if len(roots) != cell_count or reader.offset != len(roots_data):
        raise ValueError("Physics root table count or trailer is invalid")
    for index, row in enumerate(roots):
        if struct.unpack("<2i", bytes.fromhex(row["record"])[96:]) != (index, 0):
            raise ValueError("Collision root does not reference its native physics template ordinal")
    bound, lookup, nodes = bind_zones(zones, list(range(cell_count)))
    validate_bindings(bound, lookup, nodes, cell_count)
    payloads = [write_pvs_zone60(zone) for zone in bound]
    resources = [Resource("Zone%04d.pvsz" % index, index, 12) for index in range(zone_count)]
    output.mkdir(parents=True)
    chunk = output / "GeoChunk1.minizip"
    chunk_map = write_minizip(chunk, resources, lambda resource: payloads[resource.resource_id])
    persisted = MiniZip(chunk)
    verified_zones, rows = [], []
    for index, payload in enumerate(payloads):
        data = persisted.read(index)
        zone = read_pvs_zone60(data)
        if data != payload or zone != bound[index]:
            raise ValueError("Persisted physics-bound zone differs from input")
        verified_zones.append(zone)
        rows.append({"index": index, "sha256": hashlib.sha256(data).hexdigest(),
                     "draws": len(zone["draws"]), "physics_dependencies": len(zone["dependencies"])})
    lookup_data = struct.pack("<%dI" % zone_count, *lookup)
    (output / "PVSZLookup.dat").write_bytes(lookup_data)
    (output / "ChunkMap1.dat").write_bytes(chunk_map)
    (output / "ChunkAssign.dat").write_bytes(assignments)
    (output / "TopGearCPSout_00.hex").write_bytes(grid_data)
    (output / "CollisionRoots60.dat").write_bytes(roots_data)
    persisted_lookup = [row[0] for row in struct.iter_unpack("<I", (output / "PVSZLookup.dat").read_bytes())]
    validate_bindings(verified_zones, persisted_lookup, nodes, cell_count)
    report = {
        "component": "spatial-zone-physics-node-bindings", "playable_map": False, "engine_validated": False,
        "binding_consistency_verified": True, "zone_count": zone_count, "physics_count": cell_count,
        "required_node_capacity": len(nodes), "nodes": nodes, "zones": rows,
        "spatial_component": str(spatial), "physics_component": str(physics),
        "physics_source_sha256": physics_report["source_sha256"],
        "world_source_sha256": spatial_report["source_sha256"],
        "hex_sha256": hashlib.sha256(grid_data).hexdigest(),
        "collision_roots_sha256": hashlib.sha256(roots_data).hexdigest(),
        "zone_chunk_sha256": hashlib.sha256(chunk.read_bytes()).hexdigest(),
        "zone_chunk_map_sha256": hashlib.sha256(chunk_map).hexdigest(),
        "pvs_lookup_sha256": hashlib.sha256(lookup_data).hexdigest(),
        "remaining": [
            "%d nodes describe only spatial zones and collision; other resource families require additional nodes." % len(nodes),
            "Final native root counters and resident/model/material registration are not yet assembled.",
            "All collision templates are requested per zone like HomeSpace02; Top Gear memory/performance needs engine validation.",
            "Physics archive remains in its verified component; this directory is not an installable map.",
            "Secondary draws, surface semantics, custom native travel and off-route rendering remain unverified.",
        ],
    }
    (output / "bindings.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spatial", type=Path, required=True)
    parser.add_argument("--physics", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = build(args.spatial, args.physics, args.output)
    except (OSError, ValueError, KeyError) as error:
        print("error: " + str(error), file=sys.stderr)
        return 1
    print("Verified %d spatial zones bound to %d collision templates; full root remains unfinished." %
          (report["zone_count"], report["physics_count"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
