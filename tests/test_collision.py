import copy
import struct
from pathlib import Path
import tempfile
import unittest

from collision import (build_physics, ground_hits, physics_templates,
                       read_collision, read_physics, surface_remap, write_physics)
from fh2_world import MiniZip
from fh2_pvs import read_root_groups60
from fm5_pvs import Reader


def collision_fixture():
    data = bytearray(4096)
    struct.pack_into(">2I", data, 0, 1, 1)
    struct.pack_into(">3I", data, 40, len(data), 2, 0xABCD1234)
    struct.pack_into(">2I", data, 76, 1, 84)
    struct.pack_into(">6I", data, 84, 2048, 0, 0, 2048, 2048, 0)
    struct.pack_into(">6I", data, 2048, 3, 1, 64, 160, 1, 1)
    struct.pack_into(">I", data, 2104, 192)
    for i, position in enumerate([(0, 2, 0), (10, 2, 0), (0, 2, 10)]):
        struct.pack_into(">4fI", data, 2112 + i * 32, *position, 1, 0x7F800000)
    struct.pack_into(">4I4f", data, 2208, 0x770000, 64, 96, 128, 0, 1, 0, 0)
    struct.pack_into(">I2H", data, 2240, 196, 1, 0)
    return bytes(data)


class CollisionTests(unittest.TestCase):
    def test_legacy_envelope_and_triangle_pointers_are_not_version_words(self):
        world = read_collision(collision_fixture())
        self.assertEqual(world["envelope_version"], 2)
        self.assertEqual(world["cells"][0]["triangles"][0]["vertices"], [0, 1, 2])
        self.assertEqual(world["cells"][0]["subcells"], [[0]])
        hits = ground_hits(world, 1, 1)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["height"], 2)
        self.assertEqual(ground_hits(world, 20, 20), [])
        with self.assertRaises(ValueError):
            ground_hits(world, float("nan"), 0)

    def test_legacy_rejects_bad_sizes_vertices_and_subcell_references(self):
        source = collision_fixture()
        for offset, value in ((40, 0), (44, 27), (48, 0), (96, 100), (100, 0),
                              (2212, 65), (2240, 2), (2244, 0x00010001)):
            invalid = bytearray(source)
            struct.pack_into(">I", invalid, offset, value)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                read_collision(invalid)

    def test_physics_schema_roundtrips_material_bytes_and_dense_subcell_lists(self):
        template = {"name": "topgear.phys", "bounds": [0.0] * 8,
                    "additional_bounds": [0.0] * 4,
                    "grid": [1, 1] + [0.0] * 8,
                    "vertices": [[0.0, 2.0, 0.0, 1.0, 0x7F800000]],
                    "triangles": [[119, 0, 0, 0, 0, 0.0, 1.0, 0.0, 0.0]],
                    "grid_offsets": [0], "grid_refs": [1, 0]}
        data = write_physics(template)
        self.assertEqual(read_physics(data), template)
        for invalid in (data[:-1], data + b"\0"):
            with self.assertRaises(ValueError):
                read_physics(invalid)
        for key, value in (("grid_offsets", [-2]), ("grid_refs", [1, 1]),
                           ("triangles", [[0, 0, 1, 0, 0, 0.0, 1.0, 0.0, 0.0]])):
            invalid = copy.deepcopy(template)
            invalid[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                write_physics(invalid)

    def test_conversion_preserves_geometry_bytes_and_packs_subcells_without_padding(self):
        world = read_collision(collision_fixture())
        template = physics_templates(world)[0]
        self.assertEqual(template["triangles"][0][:5], [0, 119, 0, 1, 2])
        self.assertEqual(template["vertices"][0], [0, 2, 0, 1, 0x7F800000])
        self.assertEqual(template["grid_offsets"], [0])
        self.assertEqual(template["grid_refs"], [1, 0])
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.col"
            source.write_bytes(collision_fixture())
            output = Path(directory) / "physics"
            report = build_physics(source, output, [1, 1])
            self.assertTrue(report["schema_verified"])
            self.assertFalse(report["playable_map"])
            self.assertEqual((output / "ChunkMap2.dat").read_bytes(), struct.pack("<I", 0x01000000))
            self.assertEqual(read_physics(MiniZip(output / "GeoChunk2.minizip").read(0)), template)
            self.assertEqual(report["ground_query"]["hits"][0]["height"], 2)
            self.assertTrue(report["ground_query"]["persisted_conversion_matches"])
            roots = read_root_groups60(Reader((output / "CollisionRoots60.dat").read_bytes()))
            self.assertEqual(len(roots), 1)
            self.assertEqual(struct.unpack("<2i", bytes.fromhex(roots[0]["record"])[96:]), (0, 0))
            self.assertEqual(struct.unpack("<24f", bytes.fromhex(roots[0]["record"])[:96])[23], 1)
            with self.assertRaises(ValueError):
                build_physics(source, output)

    def test_conversion_keeps_empty_and_missing_subcells_distinct(self):
        world = read_collision(collision_fixture())
        world["cells"][0]["subcells"] = [None, [], [0], [0]]
        template = physics_templates(world)[0]
        self.assertEqual(template["grid_offsets"], [-1, 0, 1, 3])
        self.assertEqual(template["grid_refs"], [0, 1, 0, 1, 0])
        world["cells"][0]["triangles"][0]["flags"] = 0x34560000
        self.assertEqual(physics_templates(world)[0]["triangles"][0][:2], [0x34, 0x56])

    def surface_xml(self, path, names):
        path.write_text("<Root><SurfaceTypes>%s</SurfaceTypes></Root>" %
                        "".join("<%s Category='HardWorld'/>" % name for name in names), encoding="utf-8")
        return path

    def test_surfaces_are_remapped_by_name_with_fallbacks(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            source = self.surface_xml(path / "fm5.xml", ["Asphalt", "Grass", "AsphaltTrackPenalty", "Gone"])
            target = self.surface_xml(path / "fh2.xml", ["Asphalt", "Snow", "AsphaltTrack", "Grass"])
            mapping = surface_remap(source, target)
            self.assertEqual(mapping, {0: 0, 1: 3, 2: 2})
            world = read_collision(collision_fixture())
            world["cells"][0]["triangles"][0]["flags"] = 0x00020000
            self.assertEqual(physics_templates(world, mapping)[0]["triangles"][0][:2], [0, 2])
            world["cells"][0]["triangles"][0]["flags"] = 0x00010000
            self.assertEqual(physics_templates(world, mapping)[0]["triangles"][0][:2], [0, 3])
            world["cells"][0]["triangles"][0]["flags"] = 0x00030000
            with self.assertRaisesRegex(ValueError, "no target mapping"):
                physics_templates(world, mapping)
            source_col = path / "track.col"
            source_col.write_bytes(collision_fixture())
            report = build_physics(source_col, path / "physics", [1, 1], {119: 3})
            self.assertTrue(report["surfaces_remapped"])
            rebuilt = read_physics(MiniZip(path / "physics" / "GeoChunk2.minizip").read(0))
            self.assertEqual({row[1] for row in rebuilt["triangles"]}, {3})


if __name__ == "__main__":
    unittest.main()
