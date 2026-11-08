from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

from navigation import FORMATS, GEOMETRY_FIELDS, build, geometry, read_mlp, route_visibility
from test_source_world import source_world


def mlp(rows):
    header = b"MLPDataStart:\n"
    for name, kind, columns, values in rows:
        header += ("%s:%s:%d:%d:%10d\n" %
                   (name, kind, len(values) // columns, columns, 0)).encode("ascii")
    header += b"MLPDataEnd:\n"
    offset = len(header)
    directory, payload = b"MLPDataStart:\n", b""
    for name, kind, columns, values in rows:
        data = struct.pack("<%d%s" % (len(values), FORMATS[kind]), *values)
        directory += ("%s:%s:%d:%d:%10d\n" %
                      (name, kind, len(values) // columns, columns, offset)).encode("ascii")
        offset += len(data)
        payload += data
    return directory + b"MLPDataEnd:\n" + payload


def waypoint_fixture(crc=False):
    rows = [("iLength", "int32", 1, [3]), ("iType", "int8", 1, [0])]
    rows += [(name, "float", 1, [0.0, 1.0, 2.0]) for name in GEOMETRY_FIELDS]
    if crc:
        rows.insert(0, ("iCRC", "uint32", 1, [1, 2, 3, 4, 5, 6]))
    return mlp(rows)


class NavigationTests(unittest.TestCase):
    def test_named_geometry_and_integrity_fields(self):
        source = geometry(waypoint_fixture())
        target = geometry(waypoint_fixture(True))
        self.assertEqual(source["positions"], [[0, 0, 0], [1, 1, 1], [2, 2, 2]])
        self.assertNotIn("iCRC", source["fields"])
        self.assertEqual(target["fields"]["iCRC"]["values"], [1, 2, 3, 4, 5, 6])
        fields = read_mlp(mlp([("trackName", "char", 1, [65, 0]), ("line", "int", 1, [1, -2])]))
        self.assertEqual(fields["line"]["values"], [1, -2])

    def test_corrupt_directory_bounds_trailers_and_nonfinite_values_fail(self):
        data = waypoint_fixture()
        for invalid in (data[:-1], data + b"\0", data.replace(b"iType:int8", b"iType:badx"),
                        data.replace(b"iLength:", b"iType__:"),
                        mlp([("bad", "float", 1, [float("nan")])]),
                        mlp([("same", "int", 1, [1]), ("same", "int", 1, [2])])):
            with self.subTest(data=invalid[:40]), self.assertRaises(ValueError):
                geometry(invalid)
        invalid = data.replace(b":       ", b":9999999", 1)
        with self.assertRaises(ValueError):
            read_mlp(invalid)

    def test_route_zones_match_native_integer_waypoint_division(self):
        world = {"grid": 25, "ranges": [
            struct.pack("<2HI", 58, 0, 0).hex(),
            struct.pack("<2HI", 1, 58, 1).hex(),
            struct.pack("<2HI", 1, 59, 2).hex(),
            struct.pack("<2HI", 1, 60, 3).hex()], "zones": [None] * 61}
        result = route_visibility(world, 1435)
        self.assertEqual(result["route_zone_count"], 58)
        self.assertEqual(result["special_zone_count"], 3)
        self.assertEqual(result["waypoint_zone_ids"][24:27], [0, 1, 1])
        self.assertEqual(result["waypoint_zone_ids"][-1], 57)
        with self.assertRaises(ValueError):
            route_visibility(world, 1500)
        world["grid"] = 0
        with self.assertRaises(ValueError):
            route_visibility(world, 1435)

    def test_build_preserves_waypoints_and_reports_crc_blocker(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            segment = mlp([("iNumSegments", "int32", 1, [1]),
                           ("iTrackLength", "int32", 1, [3]),
                           ("iStart", "int32", 1, [0]), ("iApex", "int32", 1, [1]),
                           ("iEnd", "int32", 1, [2])])
            with zipfile.ZipFile(path / "source.zip", "w") as source:
                source.writestr("Track.geo", waypoint_fixture())
                source.writestr("Track.seg", segment)
            with zipfile.ZipFile(path / "reference.zip", "w") as reference:
                reference.writestr("Track.geo", waypoint_fixture(True))
            world = source_world()
            world = world[:53] + struct.pack("<I2HI", 1, 1, 0, 0) + world[57:]
            (path / "track.pvs").write_bytes(world)
            output = path / "output"
            report = build(path / "source.zip", path / "reference.zip", path / "track.pvs", output)
            self.assertFalse(report["playable_map"])
            self.assertFalse(report["source_has_crc"])
            self.assertTrue(report["reference_has_crc"])
            self.assertEqual((output / "WaypointPositions.dat").read_bytes(),
                             struct.pack("<I9f", 3, 0, 0, 0, 1, 1, 1, 2, 2, 2))
            with self.assertRaises(ValueError):
                build(path / "source.zip", path / "reference.zip", path / "track.pvs", output)
