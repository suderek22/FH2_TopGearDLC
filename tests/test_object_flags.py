import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_object_flags import clear_dynamic_flag


def record(flags):
    raw = bytearray(73)
    struct.pack_into("<IQ", raw, 0, 7, flags)
    raw[30] = 0x55
    return raw.hex()


class ObjectFlagTests(unittest.TestCase):
    def root(self):
        return {"primary_draws": [
            {"model_id": 7, "source_record": record(0x0FFF0614)},
            {"model_id": 7, "source_record": record(0x0FFF0214)},
        ]}

    def test_clears_only_dynamic_bit(self):
        root = self.root()
        result, changed = clear_dynamic_flag(root)
        self.assertEqual(changed, [0])
        raw = bytes.fromhex(result["primary_draws"][0]["source_record"])
        self.assertEqual(struct.unpack_from("<Q", raw, 4)[0], 0x0FFF0214)
        self.assertEqual(raw[30], 0x55)
        self.assertEqual(result["primary_draws"][1], root["primary_draws"][1])
        self.assertEqual(root, self.root())

    def test_rejects_root_without_dynamic_draws(self):
        root = self.root()
        root["primary_draws"] = root["primary_draws"][1:]
        with self.assertRaises(ValueError):
            clear_dynamic_flag(root)


if __name__ == "__main__":
    unittest.main()
