import math
import struct
import unittest

from build_prop_fix import add_box, box_geometry, fill_lod_gaps, owning_templates, update_root_bounds


def cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def template():
    return {"name": "cell", "bounds": [0.0, 0.0, 0.0, 0.0, 20.0, 0.0, 20.0, 0.0],
            "additional_bounds": [0.0, 0.0, 20.0, 20.0],
            "grid": [2, 2, -5.0, -5.0, 25.0, 25.0, 15.0, 15.0, 1 / 15, 1 / 15],
            "vertices": [[0.0, 0.0, 0.0, 1.0, 0], [20.0, 0.0, 0.0, 1.0, 0], [0.0, 0.0, 20.0, 1.0, 0]],
            "triangles": [[0, 55, 0, 2, 1, 0.0, 1.0, 0.0, 0.0]],
            "grid_offsets": [0, -1, -1, -1], "grid_refs": [1, 0]}


class PropFixTests(unittest.TestCase):
    def test_band_fill_keeps_last_real_lod_until_cull(self):
        record = bytearray(73)
        struct.pack_into("<4e", record, 18, 100, 200, 350, 5000)
        root = {"primary_draws": [{"source_record": record.hex()}, {"source_record": record.hex()}]}
        updated, changes = fill_lod_gaps(root, {0: 0, 1: 1})
        bands = [struct.unpack_from("<4e", bytes.fromhex(d["source_record"]), 18)
                 for d in updated["primary_draws"]]
        self.assertEqual(bands, [(5000, 5000, 5000, 5000), (100, 5000, 5000, 5000)])
        self.assertEqual(root["primary_draws"][0]["source_record"], record.hex())
        self.assertEqual(changes[1][0], [100, 200, 350, 5000])

    def test_box_faces_point_outward_with_engine_winding(self):
        yaw = math.radians(30)
        rows = [math.cos(yaw), 0, -math.sin(yaw), 0, 1, 0, math.sin(yaw), 0, math.cos(yaw)]
        corners, faces = box_geometry([10, 2, 10], rows, [-0.5, 0.2, -0.02, 0.5, 1.2, 0.02])
        center = [sum(c[i] for c in corners) / 8 for i in range(3)]
        self.assertEqual(len(faces), 12)
        for (a, b, c), normal in faces:
            e1 = [corners[b][i] - corners[a][i] for i in range(3)]
            e2 = [corners[c][i] - corners[a][i] for i in range(3)]
            self.assertGreater(sum(x * y for x, y in zip(cross(e1, e2), normal)), 0)
            outward = [corners[a][i] - center[i] for i in range(3)]
            self.assertGreater(sum(x * y for x, y in zip(outward, normal)), 0)
        ys = [c[1] for c in corners]
        self.assertAlmostEqual(min(ys), 1.8)
        self.assertAlmostEqual(max(ys), 3.2)
        # Thin local Z is widened to the minimum collision thickness.
        width = math.dist(corners[0], corners[1])
        self.assertAlmostEqual(width, 0.2)
        # Local X follows the first rotation row.
        self.assertAlmostEqual(corners[4][0] - corners[0][0], math.cos(yaw))

    def test_add_box_extends_subcells_bounds_and_preserves_existing(self):
        corners, faces = box_geometry([9.5, 0, 9.5], [1, 0, 0, 0, 1, 0, 0, 0, 1], [-1, 0, -1, 1, 2, 1])
        source = template()
        result = add_box(source, corners, faces, 126)
        self.assertEqual(source, template())
        self.assertEqual(len(result["vertices"]), 11)
        self.assertEqual(result["triangles"][:1], source["triangles"])
        cells = [None if o == -1 else result["grid_refs"][o + 1:o + 1 + result["grid_refs"][o]]
                 for o in result["grid_offsets"]]
        new = list(range(1, 13))
        self.assertEqual(cells, [[0] + new, new, new, new])
        self.assertTrue(all(row[1] == 126 and row[0] == 0 for row in result["triangles"][1:]))
        self.assertAlmostEqual(result["bounds"][1], -0.2, places=6)
        self.assertEqual(result["bounds"][5], 2.0)

    def test_box_outside_grid_or_templates_fails(self):
        corners, faces = box_geometry([24.5, 0, 5], [1, 0, 0, 0, 1, 0, 0, 0, 1], [-1, 0, -1, 1, 1, 1])
        with self.assertRaises(ValueError):
            add_box(template(), corners, faces, 1)
        far, _ = box_geometry([100, 0, 100], [1, 0, 0, 0, 1, 0, 0, 0, 1], [-1, 0, -1, 1, 1, 1])
        with self.assertRaises(ValueError):
            owning_templates([template()], far)

    def test_root_bounds_follow_templates(self):
        identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
        record = struct.pack("<24f2i", *identity, 0, 0, 0, 0, 20, 0, 20, 1, 0, 0).hex()
        cell = template()
        cell["bounds"] = [0.0, -0.5, 0.0, 0.0, 20.0, 3.0, 20.0, 0.0]
        root = update_root_bounds({"root_groups": [{"record": record, "settings": None}]}, [cell])
        values = struct.unpack("<24f2i", bytes.fromhex(root["root_groups"][0]["record"]))
        self.assertEqual(values[16:24], (0, -0.5, 0, 0, 20, 3, 20, 1))
        self.assertEqual(values[24:], (0, 0))


if __name__ == "__main__":
    unittest.main()
