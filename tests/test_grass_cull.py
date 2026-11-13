import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_grass_cull import GRASS_CULL, GRASS_FLAGS, cull_grass


def record(flags, bands):
    raw = bytearray(64)
    raw[4:12] = flags
    struct.pack_into("<4e", raw, 18, *bands)
    return raw.hex()


class GrassCullTests(unittest.TestCase):
    def root(self):
        other = bytes.fromhex("0400ff0f00000000")
        return {
            "groups": [{"references": [0, 0, 0, 0]}, {"references": [0xFFFFFFFF]}],
            "primary_draws": [
                {"model_id": 0, "source_record": record(GRASS_FLAGS, (100, 100, 100, 5000))},
                {"model_id": 1, "source_record": record(other, (100, 200, 350, 5000))},
                {"model_id": 1, "source_record": record(GRASS_FLAGS, (100, 100, 100, 5000))},
            ],
        }

    def test_only_grass_material_draws_are_culled(self):
        root = self.root()
        data = {0: b"x grass_topg_001.materialbin", 1: b"concrete.materialbin"}
        result, changed = cull_grass(root, data.__getitem__)
        self.assertEqual(changed, [0])
        bands = struct.unpack_from("<4e", bytes.fromhex(result["primary_draws"][0]["source_record"]), 18)
        self.assertEqual(bands, (100, 100, 100, GRASS_CULL))
        self.assertEqual(result["primary_draws"][1:], root["primary_draws"][1:])
        self.assertEqual(root, self.root())

    def test_rejects_grass_with_lod_links(self):
        root = self.root()
        root["groups"][0]["references"] = [1]
        with self.assertRaises(ValueError):
            cull_grass(root, lambda model: b"grass_topg_001.materialbin")


if __name__ == "__main__":
    unittest.main()
