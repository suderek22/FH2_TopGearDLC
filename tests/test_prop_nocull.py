import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from build_prop_nocull import NO_SIZE_CULL_FLAG, set_no_size_cull  # noqa: E402


def draw(flags, model=1):
    raw = bytearray(80)
    struct.pack_into("<I", raw, 0, model)
    struct.pack_into("<Q", raw, 4, flags)
    return {"model_id": model, "source_record": raw.hex()}


def flags_of(entry):
    return struct.unpack_from("<Q", bytes.fromhex(entry["source_record"]), 4)[0]


class PropNoCullTests(unittest.TestCase):
    def test_sets_bit_only_on_promoted_props(self):
        root = {"primary_draws": [draw(0xFFF0004), draw(0xFFF0614), draw(0x3C00000FFF0614)]}
        updated, changed = set_no_size_cull(root)
        self.assertEqual(changed, [1, 2])
        self.assertEqual(flags_of(updated["primary_draws"][0]), 0xFFF0004)
        self.assertEqual(flags_of(updated["primary_draws"][1]), 0xFFF0614 | NO_SIZE_CULL_FLAG)
        self.assertEqual(flags_of(updated["primary_draws"][2]), 0x3C00000FFF0614 | NO_SIZE_CULL_FLAG)
        self.assertEqual(flags_of(root["primary_draws"][1]), 0xFFF0614)

    def test_rejects_when_nothing_to_change(self):
        with self.assertRaises(ValueError):
            set_no_size_cull({"primary_draws": [draw(0xFFF0004), draw(0xFFF2614)]})


if __name__ == "__main__":
    unittest.main()
