import copy
from pathlib import Path
import struct
import tempfile
import unittest

from build_bindings import bind_zones
from build_root import assemble, build_support_archive, relocate
from fh2_pvs import read_root60, write_root60
from fm5_world import world_tables
from spatial import covering_grid
from test_source_world import source_world
from test_bindings import fixture


class RootBuilderTests(unittest.TestCase):
    def fixture(self, path):
        source = path / "world.pvs"
        source.write_bytes(source_world())
        world = world_tables(source)
        world["groups"] *= 4
        for texture in world["textures"]:
            texture["payloads"] = [{"streaming_level": 1}]
            texture["resident"] = {"size": 1}
        world["shaders"] = [{"hash": "12345678", "path": "game:\\media\\tracks\\topgear\\bin\\shaders\\x.shaderbin"}]
        world["materials"] = []
        grid = covering_grid([0, 0, 10, 10])
        count = len(grid["coordinates"])
        zone = {"draws": [0, 2, 6], "models": [3], "textures": [0], "qualities": [257],
                "dependencies": [], "auxiliary": [], "final": []}
        zones, _, nodes = bind_zones([zone] * count, [0])
        matrix = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
        roots = [{"record": struct.pack("<24f2i", *matrix, 0, 2, 0, 0, 10, 2, 10, 1, 0, 0).hex(),
                  "settings": None}]
        return world, zones, [[7]] * count, roots, nodes, [world["textures"][0]["fh2_descriptor"]], grid

    def test_promotes_secondary_geometry_and_binds_canonical_source_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            world, zones, secondary, roots, nodes, descriptors, grid = self.fixture(Path(directory))
            root, rebuilt = assemble(world, zones, secondary, roots, nodes, descriptors, grid, "TopGearCPS")
            data = write_root60(root)
            parsed = read_root60(data)
            self.assertEqual(write_root60(parsed), data)
            self.assertEqual(len(parsed["primary_draws"]), 8)
            self.assertEqual(parsed["secondary_draws"], [])
            for ordinal, draw in enumerate(parsed["primary_draws"]):
                original = (world["primary_draws"] + world["secondary_draws"])[ordinal]
                self.assertEqual(draw["source_record"], original["source_record"])
            self.assertTrue(all(row["draws"] == list(range(8)) for row in rebuilt))
            self.assertTrue(all(row["draws"] == [0, 2, 6] for row in zones))
            self.assertEqual(parsed["header_fields"], [0, len(nodes), 1, 1, 1])
            self.assertEqual(parsed["zone_count"], len(grid["coordinates"]))
            self.assertEqual(parsed["shaders"][0]["hash"], 0x12345678)
            self.assertEqual(parsed["shaders"][0]["path"],
                             "game:\\media\\tracks\\TopGearCPS\\bin\\shaders\\x.shaderbin")
            self.assertEqual(parsed["scene"], "TopGearCPSout")
            self.assertTrue(parsed["scene_flags"][2])
            self.assertIsNone(parsed["zone_u16_data"])
            self.assertEqual(world["zone_u16_data"], ["0c002200"])

    def test_unrequested_placements_include_material_texture_closure(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.fixture(Path(directory))
            world, zones, secondary, roots, nodes, descriptors, grid = args
            texture = copy.deepcopy(world["textures"][0])
            texture["payloads"] = [{"streaming_level": 2}]
            world["textures"].append(texture)
            descriptors.append(texture["fh2_descriptor"])
            world["instances"][1]["texture_slot_sets"] = [[[[0, 1, 65535]]]]
            root, rebuilt = assemble(*args, "TopGearCPS")
            self.assertTrue(all(zone["textures"] == [0, 1] for zone in rebuilt))
            self.assertTrue(all(zone["qualities"] == [257, 513] for zone in rebuilt))
            self.assertEqual(root["zone_maxima"], [2, 1, 8])
            self.assertEqual(zones[0]["textures"], [0])
            texture["payloads"] = []
            texture["resident"] = {"size": 0}
            with self.assertRaisesRegex(ValueError, "unavailable texture"):
                assemble(*args, "TopGearCPS")

    def test_invalid_group_instance_zone_and_namespace_bindings_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.fixture(Path(directory))
            for kind in ("group", "secondary", "model", "count"):
                world, zones, secondary, roots, nodes, descriptors, grid = copy.deepcopy(args)
                if kind == "group":
                    world["groups"] = []
                elif kind == "secondary":
                    secondary[0] = [0]
                elif kind == "model":
                    zones[0]["models"] = []
                else:
                    secondary = []
                with self.subTest(kind=kind), self.assertRaises(ValueError):
                    assemble(world, zones, secondary, roots, nodes, descriptors, grid, "TopGearCPS")
        for path in ("game:\\media\\tracks\\Anthem\\bin\\x", "game:\\media\\tracks\\topgear\\bin\\..\\x"):
            with self.assertRaises(ValueError):
                relocate(path, "TopGearCPS")

    def test_support_archive_preserves_resident_bytes_and_renames_only_store_member(self):
        import zipfile
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            fixture(path)
            source = path / "source.zip"
            resident = b"resident-store"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("TopGear.stx.bin", resident)
                archive.writestr("Materials/test.materialbin", b"material")
                archive.writestr("TopGearout.00011.gr2", b"animation")
            world = {"shaders": [], "materials": [], "resident_store": {"name": "TopGear.stx.bin"}}
            rows, shaders = build_support_archive(source, path / "bin.zip", world,
                                                  hashlib.sha256(resident).hexdigest(), "TopGearCPS")
            self.assertEqual(shaders, [])
            with zipfile.ZipFile(path / "bin.zip") as archive:
                self.assertEqual(archive.read("TopGearCPS.stx.bin"), resident)
                self.assertNotIn("TopGear.stx.bin", archive.namelist())
                self.assertEqual(len(rows), len(archive.namelist()))


if __name__ == "__main__":
    unittest.main()
