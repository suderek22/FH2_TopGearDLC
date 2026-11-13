import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_object_bounds import fix_object_bounds, mesh_bboxes


def model(*boxes):
    """Minimal burG bundle with one Mesh blob per BBox."""
    count = len(boxes)
    header = bytearray(b"burG" + struct.pack("<HHII", 1, count, 0, 0))
    meta_base = 16 + 24 * count
    meta = bytearray()
    for index, box in enumerate(boxes):
        offset = meta_base + len(meta)
        header += b"Mesh"[::-1] + struct.pack("<BBHIIII", 1, 7, 1, offset, 0, 0, 0)
        meta += b"BBox"[::-1] + struct.pack("<HH", 24 << 4, 8) + struct.pack("<6f", *box)
    return bytes(header + meta)


def group(record):
    return {"record": struct.pack("<20f", *record).hex(), "references": [0xFFFFFFFF] * 4}


class ObjectBoundsTests(unittest.TestCase):
    def root(self):
        real = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 5, 5, 5, 1, 2, 2, 2, 0]
        return {
            "groups": [group([0] * 15 + [1] + [0] * 4), group(real), group([0] * 20)],
            "primary_draws": [{"model_id": 0}, {"model_id": 1}],
        }

    def test_mesh_bbox_union(self):
        boxes = mesh_bboxes(model((-1, 0, -1, 1, 2, 1), (-3, -1, 0, 0, 1, 4)))
        self.assertEqual(boxes, [((-1, 0, -1), (1, 2, 1)), ((-3, -1, 0), (0, 1, 4))])

    def test_only_used_zero_bound_models_change(self):
        root = self.root()
        data = {0: model((-0.5, 0, -0.25, 0.5, 2, 0.25), (-1, 0, 0, 0, 1, 0.5))}
        result, changed = fix_object_bounds(root, data.__getitem__)
        self.assertEqual(list(changed), [0])
        values = struct.unpack("<20f", bytes.fromhex(result["groups"][0]["record"]))
        self.assertEqual(values[12:16], (-0.25, 1.0, 0.125, 1.0))
        self.assertEqual(values[16:20], (0.75, 1.0, 0.375, 0.0))
        self.assertEqual(values[:12], (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0))
        self.assertEqual(result["groups"][1:], root["groups"][1:])
        self.assertEqual(root, self.root())

    def test_rejects_zero_bound_model_without_mesh_bounds(self):
        with self.assertRaises(ValueError):
            fix_object_bounds(self.root(), lambda _: model())


if __name__ == "__main__":
    unittest.main()
