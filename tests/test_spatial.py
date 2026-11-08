import copy
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

from build_spatial import build, spatial_zones
from fh2_world import MiniZip, read_pvs_zone60
from fm5_world import bind_models, world_tables
from spatial import center, covering_grid, neighbors, read_hex, write_hex, zone_at
from test_collision import collision_fixture
from test_navigation import waypoint_fixture
from test_source_world import source_world
import test_pvs


class SpatialTests(unittest.TestCase):
    def test_native_grid_roundtrip_centers_and_neighbors(self):
        grid = covering_grid([-100, -200, 100, 200])
        for zone in range(len(grid["coordinates"])):
            self.assertEqual(zone_at(grid, *center(grid, zone)), zone)
        for version in (100, 101):
            candidate = dict(grid, version=version,
                             neighbor_masks=None if version == 100 else grid["neighbor_masks"])
            self.assertEqual(read_hex(write_hex(candidate)), candidate)
        middle = grid["columns"] * 3 + 3
        self.assertEqual(len(neighbors(grid, middle)), 6)
        grid["neighbor_masks"][middle] = 1
        self.assertEqual(len(neighbors(grid, middle)), 1)
        self.assertEqual(zone_at(grid, -1e6, 1e6), -1)

    def test_grid_rejects_invalid_coordinates_masks_and_references(self):
        grid = covering_grid([0, 0, 1, 1])
        for key, value in (("radius", -1), ("cells", [-2] * len(grid["cells"])),
                           ("neighbor_masks", [64] * len(grid["cells"])),
                           ("coordinates", [[0, 0]] * len(grid["coordinates"]))):
            invalid = copy.deepcopy(grid)
            invalid[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                write_hex(invalid)
        with self.assertRaises(ValueError):
            zone_at(grid, float("nan"), 0)
        with self.assertRaises(ValueError):
            neighbors(grid, -1)
        with self.assertRaises(ValueError):
            read_hex(write_hex(grid) + b"\0")
        for radius in (1e100, 1e-100):
            with self.subTest(radius=radius), self.assertRaises(ValueError):
                covering_grid([0, 0, 1, 1], radius)
        with self.assertRaises(ValueError):
            zone_at(grid, 1e100, 0)

    def test_rebuilt_visibility_and_archive_cover_source_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            source = source_world()
            source = source[:53] + struct.pack("<I2HI", 1, 1, 0, 0) + source[57:]
            pvs = path / "track.pvs"
            pvs.write_bytes(source)
            fixture = test_pvs.SourcePVSTests()
            fixture.path = pvs
            archive = fixture.archive()
            world = bind_models(world_tables(pvs), archive)
            world["zones"][0]["instances"] = [0, 7]
            grid = covering_grid([0, 0, 10, 10])
            zones, origins = spatial_zones(world, [[0, 2, 0], [1, 2, 1], [2, 2, 2]], grid)
            self.assertEqual(zones[0]["zone"]["draws"], [0])
            self.assertEqual(zones[0]["secondary_instances"], [7])
            self.assertEqual(origins[0]["source_zones"], [0])
            with zipfile.ZipFile(path / "AI.zip", "w") as ai:
                ai.writestr("Track.geo", waypoint_fixture())
            (path / "track.col").write_bytes(collision_fixture())
            output = path / "output"
            report = build(pvs, archive, path / "AI.zip", path / "track.col", output)
            self.assertTrue(report["collision_vertex_coverage_verified"])
            self.assertFalse(report["visibility_coverage_verified"])
            self.assertFalse(report["playable_map"])
            grid = read_hex((output / "TopGearCPSout_00.hex").read_bytes())
            self.assertEqual(len(grid["coordinates"]), report["zone_count"])
            self.assertEqual(read_pvs_zone60(MiniZip(output / "GeoChunk1.minizip").read(0))["models"], [3])
            self.assertEqual((output / "ChunkAssign.dat").read_bytes()[:8],
                             struct.pack("<2I", 100, report["zone_count"]))
            with self.assertRaises(ValueError):
                build(pvs, archive, path / "AI.zip", path / "track.col", output)
