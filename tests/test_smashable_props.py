import math
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fh2_physdefs as P
from build_smashable_props import (CELL_RESOURCE_ID, add_zone_request, build_cell, patch_plib,
                                   template_record)
from fh2_smash import decode_unit, encode_unit, make_placement, parse_cell


def donor():
    smash = {"render_model_index": 772, "visible_distance": 200.0, "surface_type_index": 200,
             "is_trigger": 0, "break_speed_mph": 1.0, "unique_index": 85, "object_name_hash": 1,
             "object_type_index": 0, "secondary_type_index": 8, "mesh_index": 0,
             "connection_list": b"", "bend": (20.0, 0.0, 0.0), "low_speed_behaviour": 0,
             "allow_cloth_attachments": 0}
    zero9 = (0.0,) * 9
    return P.Definition(20, P.SMASHABLE, 0.01, zero9, zero9, (1, 1, 1), 1.0, (0, 0, 0), (0, 0, 0),
                        smash, b"", [P.Shape(P.SHAPE_POINTS, b"", P.point_cloud_blob([(0, 0, 0)]))], [])


def text(value):
    return struct.pack("<I", len(value)) + value


def library(name, model_path):
    ref = text(model_path) + struct.pack("<IIII", 0, 0x7FFFFFFF, 1, 70000)
    entry = text(name) + text(b"SMASHABLE_3D_TALL_DRAWDISTANCE") + bytes(32) + ref + ref
    other = text(b"GEN_OBJ_Other_3D") + text(b"SMASHABLE_3D_DRAWDISTANCE") + bytes(32) + \
        text(b"Tracks\\Other\\GEN_OBJ_Other.max") + struct.pack("<IIII", 0, 0x7FFFFFFF, 1, 80000)
    return b"head" + entry + other


class SmashablePropsTest(unittest.TestCase):
    def test_unit_vectors_roundtrip(self):
        for vector in ((1, 0, 0), (0, 1, 0), (0.924, 0, 0.381), (-0.337, 0, -0.941)):
            decoded = decode_unit(encode_unit(vector))
            self.assertLess(max(abs(a - b) for a, b in zip(vector, decoded)), 0.002)
        self.assertEqual(struct.pack("<I", encode_unit((0, 1, 0))), bytes.fromhex("fffdff1f"))

    def test_placement_record_layout(self):
        record = make_placement((5, 1, -5), (0, 0, -10), (10, 2, 0), (0.6, 0, 0.8)).raw
        self.assertEqual(len(record), 32)
        self.assertEqual(struct.unpack_from("<3H", record), (32768, 32768, 32768))
        self.assertEqual(record[6:12], bytes.fromhex("8f0200008080"))
        self.assertEqual(record[16:], bytes.fromhex("fffdff1f") + b"\xff" * 4 + bytes(4) + b"\xff" * 4)
        with self.assertRaises(ValueError):
            make_placement((11, 1, -5), (0, 0, -10), (10, 2, 0), (1, 0, 0))

    def test_box_smashable_matches_native_conventions(self):
        definition = P.box_smashable(donor(), (-0.5, 0.0, -0.1), (0.5, 1.0, 0.1), 0.02, 50)
        self.assertEqual(definition.smash["render_model_index"], 50)
        self.assertEqual(definition.smash["break_speed_mph"], 1.0)
        self.assertEqual(definition.graphics_offset, (-0.0, -0.5, -0.0))
        self.assertAlmostEqual(definition.inertia[0], 0.02 / 3 * (0.25 + 0.01))
        self.assertAlmostEqual(definition.inertia[0] * definition.inverse_inertia[0], 1.0)
        self.assertAlmostEqual(definition.bounding_radius, math.sqrt(0.25 + 0.25 + 0.01))
        blob = definition.shapes[0].body
        self.assertEqual(struct.unpack_from("<QQQ", blob, 8), (0x30, 8, 0x30 + 128))
        pd = P.PhysicsDefinitions([definition], [], None)
        data = P.write(pd)
        self.assertEqual(P.write(P.parse(data)), data)

    def test_template_record_is_origin_identity(self):
        record = template_record(26, 3516)
        self.assertEqual(len(record), 73)
        self.assertEqual(struct.unpack_from("<IQH", record), (26, 0x003C00400FFF0004, 3516))
        self.assertEqual(struct.unpack_from("<3f", record, 27), (0.0, 0.0, 0.0))
        with self.assertRaises(ValueError):
            template_record(26, 70000)

    def test_patch_plib_redirects_every_reference(self):
        data = library(b"ITA_OBJ_Sign_Parking_3D", b"Tracks\\Anthem\\ITA_OBJ_Sign_Parking.max")
        patched, counts = patch_plib(data, {"ITA_OBJ_Sign_Parking_3D": (7, (-1, 0, -1), (1, 2, 1))}, 3)
        self.assertEqual(counts, {"ITA_OBJ_Sign_Parking_3D": 2})
        self.assertEqual(len(patched), len(data))
        indices = sorted(struct.unpack_from("<I", patched, i + 8)[0]
                         for i in range(len(patched)) if patched.startswith(b"\xff\xff\xff\x7f\x01\0\0\0", i))
        self.assertEqual(indices, [3, 7, 7])
        bbox_at = 4 + 4 + 23 + 4 + 30
        self.assertEqual(struct.unpack_from("<8f", patched, bbox_at), (-1, 0, -1, 0, 1, 2, 1, 0))
        with self.assertRaises(ValueError):
            patch_plib(library(b"ITA_OBJ_Sign_Parking_3D", b"Tracks\\Wrong.max"),
                       {"ITA_OBJ_Sign_Parking_3D": (7, (0, 0, 0), (1, 1, 1))}, 3)

    def test_zone_request_drops_static_props_and_adds_cell(self):
        zone = {"draws": [1, 2, 3, 4], "dependencies": [[10, 0x01000000]]}
        result = add_zone_request(zone, 11, {2, 4})
        self.assertEqual(result["draws"], [1, 3])
        self.assertEqual(result["dependencies"],
                         [[10, 0x01000000], [11, CELL_RESOURCE_ID], [11, 0x80000000 | CELL_RESOURCE_ID]])
        with self.assertRaises(ValueError):
            add_zone_request(zone, 11, {9})

    def test_cell_positions_and_groups(self):
        rotation = [0.6, 0, 0.8, 0, 1, 0, -0.8, 0, 0.6]
        groups = {50: [{"position": [10, 1, 20], "rotation_rows": rotation}],
                  26: [{"position": [-5, 0, 3], "rotation_rows": rotation},
                       {"position": [7, 2, -9], "rotation_rows": rotation}]}
        boxes = {50: ((-0.5, 0, -0.1), (0.5, 1, 0.1)), 26: ((0, 0, 0), (0.1, 0.8, 0.2))}
        data, _ = build_cell(groups, boxes, {50: 200, 26: 201}, {50: "B_3D", 26: "A_3D"})
        cell = parse_cell(data)
        self.assertEqual([g[1:5] for g in cell.groups], [(201, 0, 0, 2), (200, 1, 0, 1)])
        self.assertEqual([t.name for t in cell.templates], ["A_3D", "B_3D"])
        self.assertAlmostEqual(cell.scale, math.dist(*boxes[50]) / 2, places=5)
        for template, expected in zip(cell.templates, ([[-5, 0, 3], [7, 2, -9]], [[10, 1, 20]])):
            for record, position in zip(template.records, expected):
                self.assertLess(math.dist(cell.position(record), position), 0.01)
                x_axis, y_axis = record.axes
                self.assertLess(math.dist(x_axis, rotation[:3]), 0.005)
                self.assertLess(math.dist(y_axis, (0, 1, 0)), 0.005)


if __name__ == "__main__":
    unittest.main()
