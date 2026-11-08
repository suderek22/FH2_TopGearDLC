import copy
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest

from build_bindings import bind_zones, build, validate_bindings
from collision import build_physics
from fh2_world import MiniZip, Resource, read_pvs_zone60, write_minizip, write_pvs_zone60
from spatial import covering_grid, write_hex
from test_collision import collision_fixture


def fixture(path):
    physics = path / "physics"
    source = path / "track.col"
    source.write_bytes(collision_fixture())
    build_physics(source, physics)
    spatial = path / "spatial"
    spatial.mkdir()
    grid = covering_grid([0, 0, 10, 10])
    count = len(grid["coordinates"])
    zone = {"draws": [2], "models": [3], "textures": [4], "qualities": [257],
            "dependencies": [], "auxiliary": [], "final": []}
    payload = write_pvs_zone60(zone)
    chunk = spatial / "GeoChunk1.minizip"
    chunk_map = write_minizip(chunk, [Resource("zone%d" % i, i, 12) for i in range(count)],
                             lambda resource: payload)
    files = {"TopGearCPSout_00.hex": write_hex(grid), "ChunkMap1.dat": chunk_map,
             "ChunkAssign.dat": struct.pack("<2I", 100, count) + struct.pack("<%dI" % count, *([1] * count))}
    for name, data in files.items():
        (spatial / name).write_bytes(data)
    report = {"component": "native-free-roam-spatial-geometry-visibility",
              "native_grid_roundtrip_verified": True, "zone_count": count, "source_sha256": "source-world",
              "hex_sha256": hashlib.sha256(files["TopGearCPSout_00.hex"]).hexdigest(),
              "lookup_sha256": hashlib.sha256(chunk_map).hexdigest(),
              "chunk_assignment_sha256": hashlib.sha256(files["ChunkAssign.dat"]).hexdigest(),
              "chunk_sha256": hashlib.sha256(chunk.read_bytes()).hexdigest(),
              "zones": [{"index": i, "sha256": hashlib.sha256(payload).hexdigest()} for i in range(count)]}
    (spatial / "spatial.json").write_text(json.dumps(report), encoding="utf-8")
    return spatial, physics, zone


class BindingTests(unittest.TestCase):
    def test_node_ids_are_not_resource_ordinals_and_geometry_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            spatial, physics, zone = fixture(path)
            output = path / "bound"
            report = build(spatial, physics, output)
            count = report["zone_count"]
            self.assertEqual(report["required_node_capacity"], count + 1)
            self.assertTrue(report["binding_consistency_verified"])
            self.assertFalse(report["playable_map"])
            self.assertFalse(report["engine_validated"])
            archive = MiniZip(output / "GeoChunk1.minizip")
            bound = [read_pvs_zone60(archive.read(i)) for i in range(count)]
            lookup = [row[0] for row in struct.iter_unpack("<I", (output / "PVSZLookup.dat").read_bytes())]
            validate_bindings(bound, lookup, report["nodes"], 1)
            for row in bound:
                self.assertEqual(row["dependencies"], [[count, 0x01000000]])
                self.assertEqual(dict(row, dependencies=[]), zone)
            for name in ("TopGearCPSout_00.hex", "ChunkAssign.dat", "ChunkMap1.dat"):
                self.assertEqual((output / name).read_bytes(), (spatial / name).read_bytes())
            self.assertEqual((output / "CollisionRoots60.dat").read_bytes(),
                             (physics / "CollisionRoots60.dat").read_bytes())
            with self.assertRaises(ValueError):
                build(spatial, physics, output)

    def test_checksum_failure_prevents_output_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            spatial, physics, _ = fixture(path)
            for parent, name in ((spatial, "GeoChunk1.minizip"), (physics, "ChunkMap2.dat"),
                                 (physics, "CollisionRoots60.dat")):
                target = parent / name
                original = target.read_bytes()
                target.write_bytes(original + b"\0")
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, "checksum"):
                    build(spatial, physics, path / "output")
                self.assertFalse((path / "output").exists())
                target.write_bytes(original)

    def test_mismatched_node_keys_lookup_and_duplicate_requests_are_rejected(self):
        zones = [{"dependencies": [], "draws": [], "models": [], "textures": [], "qualities": [],
                  "auxiliary": [], "final": []}] * 2
        bound, lookup, nodes = bind_zones(zones, [0, 1])
        validate_bindings(bound, lookup, nodes, 2)
        invalid_nodes = copy.deepcopy(nodes)
        invalid_nodes[2]["key"] = 0x01000001
        invalid_zones = copy.deepcopy(bound)
        invalid_zones[0]["dependencies"].append(invalid_zones[0]["dependencies"][0])
        for z, l, n in ((bound, [1, 0], nodes), (bound, lookup, invalid_nodes),
                        (invalid_zones, lookup, nodes)):
            with self.assertRaises(ValueError):
                validate_bindings(z, l, n, 2)
        with self.assertRaises(ValueError):
            bind_zones(zones, [1])
        with self.assertRaises(ValueError):
            bind_zones(bound, [0, 1])


if __name__ == "__main__":
    unittest.main()
