import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

from build_openworld import build
from test_collision import collision_fixture
from test_navigation import waypoint_fixture

from openworld_navigation import NAV_WIDTHS, closed_road, fm5_route_positions, grid_covers, nav_links, read_navw, read_owwf, validate_closed_road, waypoint_grid, wrap, write_navw, write_owwf
from openworld_navigation import FIRST_TEST_PROPERTIES
from unittest.mock import patch


def nav_fixture():
    counts = [1] * 9
    header = bytearray(112)
    struct.pack_into("<9i", header, 72, *counts)
    payload = bytes(header)
    for width in NAV_WIDTHS:
        payload += bytes(width) + b"?" * ((-width) % 16)
    return wrap(payload, b"WVAN", 0x100, 123)


class OpenWorldNavigationTests(unittest.TestCase):
    def test_native_navw_alignment_and_footer_roundtrip(self):
        data = nav_fixture()
        world = read_navw(data)
        self.assertEqual(write_navw(world), data)
        self.assertEqual(world["counts"], [1] * 9)
        self.assertEqual(len(bytes.fromhex(world["tables"][0]["padding"])), 8)
        self.assertTrue(all(row["native_null_reference_count"] == 0 for row in nav_links(world)))
        world["tables"][0]["data"] = (struct.pack("<q", -1) + bytes(32)).hex()
        self.assertEqual(nav_links(world)[0]["native_null_reference_count"], 1)

    def test_native_navw_rejects_bad_counts_sizes_versions_and_footer(self):
        data = nav_fixture()
        invalid = bytearray(data)
        struct.pack_into("<i", invalid, 88, -1)
        for value in (data[:20], data + b"x", data[:-1] + b"x", bytes(invalid)):
            with self.subTest(value=value[:16]), self.assertRaises(ValueError):
                read_navw(value)
        invalid = bytearray(data)
        struct.pack_into("<I", invalid, 4, 0x101)
        invalid[-16:] = invalid[:16]
        with self.assertRaisesRegex(ValueError, "version"):
            read_navw(bytes(invalid))
        world = read_navw(data)
        world["tables"][0]["padding"] = ""
        with self.assertRaisesRegex(ValueError, "padding"):
            write_navw(world)

    def test_owwf_signed_cells_and_preserved_reserved_bytes(self):
        world = {"stamp": 123, "cell_size": 200.0, "coordinates": [[-2, 3], [0, 4]],
                 "reserved": b"?" * 8, "padding": b"?" * 8}
        world["reserved"], world["padding"] = world["reserved"].hex(), world["padding"].hex()
        data = write_owwf(world)
        self.assertEqual(read_owwf(data), world)
        self.assertEqual(data[:16], data[-16:])
        for field, value in (("coordinates", []), ("coordinates", [[0, 0], [0, 0]]),
                             ("cell_size", float("nan")), ("coordinates", [[32768, 0], [0, 0]])):
            invalid = dict(world, **{field: value})
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                write_owwf(invalid)

    def test_source_closed_road_has_exact_positions_and_reverse_incidences(self):
        positions = [[0.0, 1.0, 0.0], [20.0, 1.5, 0.0], [20.0, 2.0, 20.0]]
        world = read_navw(write_navw(closed_road(positions, 123)))
        validate_closed_road(world, positions)
        self.assertEqual(world["counts"][:4], [3, 1, 4, 4])
        self.assertIn(b"ai_disabled\0", bytes.fromhex(world["tables"][7]["data"]))
        self.assertIn(b"traffic_disabled\0", bytes.fromhex(world["tables"][7]["data"]))
        invalid = dict(world, tables=[dict(row) for row in world["tables"]])
        invalid["tables"][8]["data"] = bytes.fromhex(invalid["tables"][8]["data"]).replace(b"true", b"fals").hex()
        with self.assertRaisesRegex(ValueError, "disabled AI"):
            validate_closed_road(invalid, positions)
        world["tables"][3]["data"] = bytes(len(bytes.fromhex(world["tables"][3]["data"]))).hex()
        with self.assertRaisesRegex(ValueError, "incidence"):
            validate_closed_road(world, positions)
        with self.assertRaises(ValueError):
            closed_road([[float("nan"), 0, 0], [0, 0, 0]], 123)

    def test_hud_road_type_keeps_ai_and_traffic_disabled(self):
        positions = [[0.0, 1.0, 0.0], [20.0, 1.5, 0.0], [20.0, 2.0, 20.0]]
        world = read_navw(write_navw(closed_road(positions, 123, "a")))
        validate_closed_road(world, positions)
        validate_closed_road(world, positions, "a")
        with self.assertRaisesRegex(ValueError, "disabled AI"):
            validate_closed_road(world, positions, "none")
        with self.assertRaises(ValueError):
            closed_road(positions, 123, "motorway")

    def test_existing_navigation_with_legacy_name_remains_valid(self):
        positions = [[0.0, 1.0, 0.0], [20.0, 1.5, 0.0]]
        with patch.dict(FIRST_TEST_PROPERTIES, {"display_name": "Top Gear (CPS)"}):
            world = read_navw(write_navw(closed_road(positions, 123, "a")))
        validate_closed_road(world, positions, "a")

    def test_fm5_route_axes_become_fh2_world_axes(self):
        # FM5 Track.geo route point (x, z, height) from the Top Gear Full Circuit start.
        route = fm5_route_positions([[280.6, 196.1, -0.02]])
        self.assertEqual(route, [[280.6, -0.02, 196.1]])
        grid = waypoint_grid([[0.0, 0.0, 0.0], [399.0, 0.0, 399.0]], 123)
        self.assertTrue(grid_covers(grid, route))
        self.assertFalse(grid_covers(grid, [[280.6, 196.1, -0.02]]))

    def test_waypoint_grid_covers_negative_and_boundary_coordinates(self):
        points = [[-200.1, 0.0, -0.1], [200.0, 0.0, 200.0]]
        grid = read_owwf(write_owwf(waypoint_grid(points, 123)))
        self.assertEqual(len(grid["coordinates"]), 12)
        self.assertIn([-2, -1], grid["coordinates"])
        self.assertIn([1, 1], grid["coordinates"])
        with self.assertRaises(ValueError):
            waypoint_grid([], 123)

    def test_build_preserves_source_geometry_and_disables_traffic(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with zipfile.ZipFile(path / "AI.zip", "w") as archive:
                archive.writestr("Track.geo", waypoint_fixture())
            collision = collision_fixture()
            (path / "track.col").write_bytes(collision)
            report = build(path / "AI.zip", path / "track.col", path / "navigation")
            self.assertEqual(report["source_collision_sha256"], hashlib.sha256(collision).hexdigest())
            self.assertEqual(report["waypoint_count"], 3)
            self.assertTrue(report["ai_disabled"])
            self.assertTrue(report["traffic_disabled"])
            self.assertFalse(report["first_test_ready"])
            nav = read_navw((path / "navigation" / "TopGearCPS_00.nav").read_bytes())
            oww = read_owwf((path / "navigation" / "TopGearCPS_00.oww").read_bytes())
            self.assertEqual(nav["stamp"], oww["stamp"])
            with self.assertRaisesRegex(ValueError, "new native navigation"):
                build(path / "AI.zip", path / "track.col", path / "navigation")


if __name__ == "__main__":
    unittest.main()
