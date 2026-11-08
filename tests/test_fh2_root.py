import copy
import struct
import unittest

from fh2_pvs import read_root60, write_root60
from fh2_world import draw_placements60, draw_lod_metadata, migrate_draws60, read_draw_placements60
from test_source_world import placement


def root_fixture():
    return {
        "stamp": 14000000, "build": 0, "header_fields": [0, 1, 1, 1, 1],
        "root_groups": [{"record": struct.pack("<24f2i", *([0] * 24), 0, 0).hex(),
                         "settings": None}],
        "spatial_bounds": [0, 0, 100, 100, 100, 100],
        "spatial_keys": [12], "spatial_maps": [[[1, 2]], [[3, 4]]],
        "flags": 0, "grid": 50, "inline_zones": False,
        "zone_lookup": [[0, 1]], "zone_count": 1, "ranges": [[0, 1, 0]],
        "has_atlas": False, "textures": [bytes(56).hex()],
        "shaders": [{"hash": 123, "path": "shader"}],
        "materials": [{"hash": 456, "path": "material"}],
        "instances": [{"material_overrides": [
            {"key": "override", "name": "material", "material_bundle": "62757247",
             "texture_hash_index_pairs": [[12345, 0]]}],
            "texture_slot_sets": [[[[1, 0, 65535]]]]}],
        "primary_draws": migrate_draws60([{"source_record": placement(0).hex()}]),
        "groups": [{"textures": [0], "enabled": False,
                    "record": struct.pack("<20f", *([0] * 20)).hex(),
                    "references": [0xFFFFFFFF] * 4,
                    "attributes": [False] * 5, "distances": [100, 100]}],
        "objects": [{"guid": "16b339e6-f0db-49a9-97dc-0acb5a6ec507", "bounds": [1, 2, 3, 4],
                     "enabled": False, "indexes": [0, 1], "attributes": [False, True]}],
        "object_index_maps": [[0], [], []], "secondary_draws": [],
        "scene": "TopGearout", "scene_flags": [True, False, False],
        "name_tables": [["name"], []], "reference_triples": [[0, 65535, 1]],
        "reference_attributes": [[123, 65535, 1]], "zone_u16_data": ["0c002200"],
        "zone_maxima": [3, 4, 5], "texture_hash_lookup": [[123456, 0]]}


class NativeRootTests(unittest.TestCase):
    def test_grouped_lod_offsets_match_stock_signed_band_selection(self):
        groups = [{"references": [0, 1, 2, 3]} for _ in range(4)]
        self.assertEqual([draw_lod_metadata(i, groups) for i in range(4)],
                         ["00010203", "ff000102", "feff0001", "fdfeff00"])
        groups = [{"references": [0, 1, 2, 2]} for _ in range(3)]
        self.assertEqual([draw_lod_metadata(i, groups) for i in range(3)],
                         ["00010202", "ff000101", "feff0000"])
        groups = [{"references": [0xFFFFFFFF] * 4}]
        self.assertEqual(draw_lod_metadata(0, groups), "00000000")
        self.assertEqual(draw_lod_metadata(0, [{"references": [0xFFFFFFFF, 0, 0, 0]}]), "7f000000")
        raw = bytearray(placement(0))
        struct.pack_into("<I", raw, 0, 0)
        rows = migrate_draws60([{"source_record": raw.hex()}], groups)
        self.assertEqual(rows[0]["source_record"], raw.hex())
        self.assertEqual(rows[0]["metadata"], "00000000")
        with self.assertRaisesRegex(ValueError, "outside"):
            draw_lod_metadata(1, groups)
        with self.assertRaisesRegex(ValueError, "invalid"):
            draw_lod_metadata(0, [{"references": [0, 0, 0, 1]}])

    def test_v60_field_order_matches_native_constructor_and_preserves_source(self):
        rows = migrate_draws60([{"source_record": placement(3).hex()}])
        data = draw_placements60(rows)
        self.assertEqual(len(data), 4 + 94)
        self.assertEqual(data[4:77], placement(3))
        self.assertEqual(data[77:82], bytes.fromhex("7f7f7f7f00"))
        self.assertEqual(data[82:], struct.pack("<4f", 0, 1, 0, 0))
        self.assertEqual(draw_placements60(read_draw_placements60(data)), data)

    def test_optional_settings_preserve_multiple_halves_material_and_channels(self):
        rows = migrate_draws60([{"source_record": placement(0).hex()}])
        rows[0]["metadata"] = "80ff007f"
        rows[0]["settings"] = (struct.pack("<B2HI", 2, 0x7C00, 0xBC00, 123)
                               + bytes(range(32))).hex()
        data = draw_placements60(rows)
        self.assertEqual(read_draw_placements60(data)[0]["settings"], rows[0]["settings"])
        self.assertEqual(draw_placements60(read_draw_placements60(data)), data)
        for invalid in (data[:-1], data + b"\0", data[:81] + b"\2" + data[82:]):
            with self.assertRaises(ValueError):
                read_draw_placements60(invalid)
        rows[0]["settings"] = "ff"
        with self.assertRaises(ValueError):
            draw_placements60(rows)

    def test_streamed_root_roundtrips_every_table_and_alias(self):
        data = write_root60(root_fixture())
        parsed = read_root60(data)
        self.assertEqual(write_root60(parsed), data)
        self.assertEqual(parsed["instances"][0]["texture_slot_sets"], [[[[1, 0, 65535]]]])
        self.assertEqual(parsed["texture_hash_lookup"], [[123456, 0]])
        self.assertEqual(parsed["groups"][0]["references"], [0xFFFFFFFF] * 4)
        self.assertEqual(parsed["zone_u16_data"], ["0c002200"])

    def test_root_optional_settings_and_absent_zone_data(self):
        root = root_fixture()
        root["root_groups"][0]["settings"] = (struct.pack("<BI", 0, 0xFFFFFFFF) + bytes(32)).hex()
        root["scene_flags"][2] = True
        root["zone_u16_data"] = None
        data = write_root60(root)
        parsed = read_root60(data)
        self.assertIsNone(parsed["zone_u16_data"])
        self.assertEqual(write_root60(parsed), data)

    def test_root_rejects_corruption_and_invalid_texture_references(self):
        data = write_root60(root_fixture())
        for invalid in (data[:-1], data + b"\0", data[:4] + struct.pack("<I", 50) + data[8:],
                        data[:-1] + b"\1"):
            with self.assertRaises(ValueError):
                read_root60(invalid)
        for name in ("override", "slot", "group", "alias"):
            root = copy.deepcopy(root_fixture())
            if name == "override":
                root["instances"][0]["material_overrides"][0]["texture_hash_index_pairs"] = [[1, 1]]
            elif name == "slot":
                root["instances"][0]["texture_slot_sets"] = [[[[0, 1, 0]]]]
            elif name == "group":
                root["groups"][0]["textures"] = [1]
            else:
                root["texture_hash_lookup"] = [[1, 1]]
            with self.subTest(name=name), self.assertRaises(ValueError):
                write_root60(root)


if __name__ == "__main__":
    unittest.main()
