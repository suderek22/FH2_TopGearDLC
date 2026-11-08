from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

from build_geometry import build
from fh2_world import MiniZip
from fm5_pvs import asset_tables, verify_assets


class SourcePVSTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "track.pvs"

    @staticmethod
    def sample():
        data = struct.pack("<4I2HI", 0x46505653, 50, 1, 2, 0, 25, 1)
        for value in (7, 3, 0):
            data += struct.pack("<II", 1, value)
        data += struct.pack("<IB", 1, 2)
        data += struct.pack("<IBI", 0, 1, 1)
        data += struct.pack("<I", 0x66070541) + bytes(range(72))
        data += struct.pack("<III", 0, 0, 8) + bytes(64)
        return data

    def test_known_tables_are_decoded_without_claiming_the_tail(self):
        self.path.write_bytes(self.sample())
        result = asset_tables(self.path)
        self.assertEqual(result["zones"][0]["instances"], [7])
        self.assertEqual(result["zones"][0]["qualities"], [2])
        self.assertEqual(result["textures"][0]["hash"], "66070541")
        self.assertEqual(len(bytes.fromhex(result["textures"][0]["fh2_descriptor"])), 56)
        self.assertEqual(bytes.fromhex(result["textures"][0]["fh2_descriptor"]),
                         struct.pack("<I", 0x66070541) + bytes(range(20, 72)))
        self.assertEqual(result["instance_count"], 8)
        self.assertEqual(result["remaining_bytes"], 68)

    def test_rejects_wrong_version_truncation_and_bad_reference(self):
        data = self.sample()
        invalid = [data[:8], data[:-1], data[:4] + struct.pack("<I", 60) + data[8:]]
        bad = bytearray(data)
        struct.pack_into("<I", bad, 44, 1)
        invalid.append(bytes(bad))
        for value in invalid:
            self.path.write_bytes(value)
            with self.subTest(size=len(value)), self.assertRaises(ValueError):
                asset_tables(self.path)

    def archive(self, levels=3, variants=(0, 2, 3), model=True, tag=b"xeTS"):
        archive = self.path.with_suffix(".zip")
        texture = b"burG" + struct.pack("<HHII", 1, 1, 40, 40)
        texture += b"BCXT" + struct.pack("<BBHIIII", 2, 0, 0, 40, 40, 0, 0)
        resident = b"burG" + struct.pack("<HHII", 1, 1, 40, 40 + len(texture))
        resident += tag + struct.pack("<BBHIIII", 0, 0, 0, 40, 40, len(texture), len(texture))
        resident += texture
        with zipfile.ZipFile(archive, "w") as file:
            file.writestr("AssetManifest.xml",
                          '<Assets><Textures><Texture Name="_0x66070541.swatchbin" '
                          'QualityLevels="%d"/></Textures></Assets>' % levels)
            file.writestr("TopGear.stx.bin", resident)
            if model:
                model_data = b"burG" + struct.pack("<HHII", 1, 3, 88, 88)
                for tag_name, major, minor in ((b"ldoM", 1, 1), (b"hseM", 1, 7), (b"yaLV", 1, 1)):
                    model_data += tag_name + struct.pack("<BBHIIII", major, minor, 0, 88, 88, 0, 0)
                file.writestr("TopGearout.00003.modelbin", model_data)
            for variant in variants:
                suffix = "_%d" % variant if variant else ""
                file.writestr("_0x66070541%s.swatchbin" % suffix, texture)
        return archive

    def tables(self):
        self.path.write_bytes(self.sample())
        return asset_tables(self.path)

    def test_resident_and_streaming_levels_bind_to_the_same_descriptor(self):
        tables = verify_assets(self.tables(), self.archive())
        self.assertEqual([part["resource_id"] for part in tables["textures"][0]["payloads"]],
                         [0, 1, 2])
        self.assertEqual(tables["resident_store"]["count"], 1)
        self.assertEqual(tables["texture_payload_count"], 3)
        self.assertEqual(tables["missing_streaming_payloads"], 0)

    def test_source_resident_only_and_missing_variants_are_explicit(self):
        tables = verify_assets(self.tables(), self.archive(levels=0, variants=()))
        self.assertEqual(tables["textures"][0]["payloads"], [])
        self.assertEqual(tables["zero_quality_descriptors"], 1)
        tables = verify_assets(self.tables(), self.archive(variants=(0, 3)))
        self.assertEqual(tables["textures"][0]["missing_streaming_variants"], [2])
        self.assertEqual(tables["missing_streaming_payloads"], 1)

    def test_rejects_missing_models_invalid_levels_and_resident_layout(self):
        for kwargs in ({"model": False}, {"levels": 1}, {"tag": b"fail"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                verify_assets(self.tables(), self.archive(**kwargs))

    def test_geometry_component_contains_bound_keys_and_converted_descriptors(self):
        self.tables()
        archive = self.archive()
        output = self.path.parent / "geometry"
        report = build(archive, output, self.path)
        self.assertTrue(report["verified"])
        self.assertFalse(report["playable_map"])
        self.assertEqual((report["models"], report["textures"], report["resident_texture_count"]),
                         (1, 3, 1))
        self.assertEqual((output / "ChunkMap0.dat").read_bytes(),
                         struct.pack("<4I", 0x04000003, 0x05000000, 0x05000001, 0x05000002))
        descriptors = (output / "TextureDescriptors.dat").read_bytes()
        self.assertEqual(descriptors, struct.pack("<II", 1, 0x66070541) + bytes(range(20, 72)))
        self.assertEqual(len(MiniZip(output / "GeoChunk0.minizip").entries), 4)
        with self.assertRaises(ValueError):
            build(archive, output, self.path)


if __name__ == "__main__":
    unittest.main()
