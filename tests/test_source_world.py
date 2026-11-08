from pathlib import Path
import struct
import tempfile
import unittest

from fh2_world import draw_placements50, read_draw_placements50, read_draw_placements60
from fm5_world import bind_models, build_component, world_tables
import test_pvs


def string(value):
    value = value.encode()
    return struct.pack("<I", len(value)) + value


def placement(index, position=(202.15, -0.724, 31.536)):
    return (struct.pack("<IQ3H4HB3f9e4f", 3, 0x400000FFF0004, index, 65535, 65535,
                        0x7C00, 0x7C00, 0x7C00, 0xBC00, 0, *position,
                        1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0))


def source_world(group_count=1):
    data = test_pvs.SourcePVSTests.sample()[:-64]
    for index in range(8):
        if index == 0:
            data += struct.pack("<I", 1) + string("material") + string("material")
            data += struct.pack("<I", 4) + b"burG"
            data += struct.pack("<III", 1, 0x66070541, 0)
        else:
            data += struct.pack("<I", 0)
        data += struct.pack("<IIIBIH", 1, 1, 1, 0, 0, 65535)
    data += struct.pack("<I", 7)
    data += b"".join(placement(index) for index in range(7))
    group = struct.pack("<IIB", 1, 0, 0)
    group += struct.pack("<16f4f4I5B2H", 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0,
                        202.15, -0.724, 31.536, 1, 0, 0, 0, 0,
                        0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0, 0, 0, 0, 0, 0, 0)
    data += struct.pack("<I", group_count) + group * group_count
    data += struct.pack("<I", 1) + string("16b339e6-f0db-49a9-97dc-0acb5a6ec507")
    data += struct.pack("<4fB2IBB", 1, 2, 3, 4, 0, 39, 40, 0, 1)
    data += struct.pack("<3I", 0, 0, 0)
    data += struct.pack("<I", 1) + placement(7)
    data += string("TopGearout") + struct.pack("<2B", 1, 0)
    data += struct.pack("<I", 1) + string("worker")
    data += struct.pack("<I", 0)
    data += struct.pack("<I3H", 1, 0, 65535, 7)
    data += struct.pack("<IIHB", 1, 123, 0, 1)
    data += struct.pack("<II2H", 1, 2, 12, 34)
    data += struct.pack("<III", 1, 0x12345678, 0)
    return data + b"\0"


class SourceWorldTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "track.pvs"

    def test_decodes_all_tables_and_preserves_source_transforms(self):
        self.path.write_bytes(source_world())
        tables = world_tables(self.path)
        self.assertTrue(tables["fully_parsed"])
        self.assertEqual(tables["parsed_bytes"], self.path.stat().st_size)
        self.assertEqual(tables["remaining_bytes"], 0)
        self.assertEqual((len(tables["primary_draws"]), len(tables["secondary_draws"])), (7, 1))
        self.assertEqual(len(tables["groups"]), 1)
        self.assertEqual(tables["instances"][0]["texture_slot_sets"], [[[[0, 0, 65535]]]])
        self.assertEqual(tables["instances"][0]["material_overrides"][0]["material_bundle"], "62757247")
        self.assertEqual(tables["texture_alias_count"], 1)
        self.assertEqual(tables["zone_u16_data"], ["0c002200"])
        draw = tables["secondary_draws"][0]
        self.assertEqual(draw["instance_index"], 7)
        self.assertAlmostEqual(draw["position"][0], 202.15, places=4)
        self.assertEqual(draw["rotation_rows"], [1, 0, 0, 0, 1, 0, 0, 0, 1])
        self.assertEqual(draw["limit_half_bits"], [0x7C00, 0x7C00, 0x7C00, 0xBC00])

    def test_rejects_trailers_duplicate_instances_nonfinite_transforms_and_extra_tree(self):
        data = source_world()
        duplicate = data.replace(placement(7), placement(6), 1)
        nonfinite = data.replace(placement(7), placement(7, (float("nan"), 0, 0)), 1)
        for invalid in (data[:-1], data + b"\0", duplicate, nonfinite, data[:-1] + b"\1"):
            self.path.write_bytes(invalid)
            with self.subTest(size=len(invalid)), self.assertRaises(ValueError):
                world_tables(self.path)

    def test_converts_native_v50_vectors_and_preserves_every_record_byte(self):
        self.path.write_bytes(source_world())
        tables = world_tables(self.path)
        data = draw_placements50(tables["primary_draws"])
        self.assertEqual(len(data), 4 + 7 * 74)
        converted = read_draw_placements50(data)
        for source, target in zip(tables["primary_draws"], converted):
            self.assertEqual(source["source_record"], target["source_record"])
            self.assertEqual(source["position"], target["position"])
            self.assertEqual(source["rotation_rows"], target["rotation_rows"])
        for invalid in (data[:-1], data + b"\0", data[:77] + b"\1" + data[78:]):
            with self.assertRaises(ValueError):
                read_draw_placements50(invalid)

    def test_component_build_verifies_draw_assets_and_never_overwrites(self):
        self.path.write_bytes(source_world())
        fixture = test_pvs.SourcePVSTests()
        fixture.path = self.path
        archive = fixture.archive()
        output = self.path.parent / "world"
        report = build_component(self.path, archive, output)
        self.assertTrue(report["verified"])
        self.assertFalse(report["playable_map"])
        self.assertEqual((report["draws"], report["groups"], report["material_overrides"]), (8, 1, 1))
        self.assertTrue((output / "WorldSource.json").exists())
        self.assertEqual(len(read_draw_placements50((output / "ModelDraws50.dat").read_bytes())), 7)
        with self.assertRaises(ValueError):
            build_component(self.path, archive, output)
        tables = world_tables(self.path)
        tables["primary_draws"][0]["model_id"] = 999
        with self.assertRaises(ValueError):
            bind_models(tables, archive)

    def test_v60_component_migrates_all_draws_without_overwriting_legacy_output(self):
        self.path.write_bytes(source_world(group_count=4))
        fixture = test_pvs.SourcePVSTests()
        fixture.path = self.path
        output = self.path.parent / "world60"
        report = build_component(self.path, fixture.archive(), output, target_version=60)
        self.assertEqual(report["target_version"], 60)
        self.assertFalse(report["playable_map"])
        rows = read_draw_placements60((output / "ModelDraws60.dat").read_bytes())
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[0]["source_record"], placement(0).hex())
        self.assertEqual(rows[0]["metadata"], "00000000")
        self.assertEqual(len(read_draw_placements60((output / "StaticModelDraws60.dat").read_bytes())), 1)


if __name__ == "__main__":
    unittest.main()
