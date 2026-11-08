import copy
from pathlib import Path
import struct
import tempfile
import unittest

from build_zones import build, geometry_zones
from fh2_world import MiniZip, read_pvs_zone60, write_pvs_zone60
from test_source_world import source_world
from fm5_world import bind_models, world_tables
import test_pvs


class PVSZoneTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "track.pvs"
        self.path.write_bytes(source_world())

    def world(self):
        fixture = test_pvs.SourcePVSTests()
        fixture.path = self.path
        return bind_models(world_tables(self.path), fixture.archive())

    def test_binary_schema_preserves_packed_draw_bits_and_raw_dependencies(self):
        zone = {"draws": [0xC0000003], "models": [1004], "textures": [0, 635],
                "qualities": [0x0101, 0x0202], "dependencies": [[3, 0x0C000064]],
                "auxiliary": [7], "final": [9]}
        data = write_pvs_zone60(zone)
        self.assertEqual(data[:13], b"PVSZ\0" + struct.pack("<II", 1, 0xC0000003))
        self.assertEqual(read_pvs_zone60(data), zone)
        self.assertEqual(write_pvs_zone60(read_pvs_zone60(data)), data)
        for invalid in (b"", data[:-1], data + b"\0", b"WRONG" + data[5:]):
            with self.assertRaises(ValueError):
                read_pvs_zone60(invalid)
        invalid = copy.deepcopy(zone)
        invalid["qualities"] = [0x10000, 1]
        with self.assertRaises(ValueError):
            write_pvs_zone60(invalid)
        invalid["qualities"] = [1]
        with self.assertRaises(ValueError):
            write_pvs_zone60(invalid)

    def test_source_instance_indexes_are_remapped_to_target_draw_ordinals(self):
        world = self.world()
        world["zones"][0]["instances"] = [7, 3, 0]
        world["primary_draws"].reverse()
        zone = geometry_zones(world)[0]
        self.assertEqual(zone["zone"]["draws"], [3, 6])
        self.assertEqual(zone["secondary_instances"], [7])
        self.assertEqual(zone["zone"]["qualities"], [0x0201])
        self.assertEqual(zone["zone"]["dependencies"], [])
        self.assertEqual(zone["streaming_texture_resource_ids"], [1])
        world["zones"][0]["instances"] = [8]
        with self.assertRaises(ValueError):
            geometry_zones(world)
        world["zones"][0]["instances"] = [0, 0]
        with self.assertRaises(ValueError):
            geometry_zones(world)

    def test_rejects_missing_required_models_textures_and_streaming_levels(self):
        world = self.world()
        for field, value in (("models", []), ("textures", [1]), ("qualities", [4])):
            invalid = copy.deepcopy(world)
            invalid["zones"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                geometry_zones(invalid)
        world["zones"][0]["qualities"] = [0]
        self.assertEqual(geometry_zones(world)[0]["resident_texture_requests"], 1)
        world["textures"][0]["resident"]["size"] = 0
        with self.assertRaises(ValueError):
            geometry_zones(world)

    def test_archive_component_binds_native_zone_type_and_verifies_content(self):
        fixture = test_pvs.SourcePVSTests()
        fixture.path = self.path
        archive = fixture.archive()
        output = self.path.parent / "zones"
        report = build(self.path, archive, output)
        self.assertTrue(report["schema_verified"])
        self.assertFalse(report["playable_map"])
        self.assertFalse(report["engine_validated"])
        self.assertEqual(len(report["zones"]), 1)
        self.assertEqual((output / "ChunkMap1.dat").read_bytes(), struct.pack("<I", 0x0C000000))
        data = MiniZip(output / "GeoChunk1.minizip").read(0)
        self.assertEqual(read_pvs_zone60(data)["models"], [3])
        self.assertEqual(report["zones"][0]["secondary_instances"], [7])
        with self.assertRaises(ValueError):
            build(self.path, archive, output)


if __name__ == "__main__":
    unittest.main()
