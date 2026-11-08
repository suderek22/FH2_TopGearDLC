import struct
import tempfile
import unittest
from pathlib import Path

from port_topgear import instance_map, stage


def string(value):
    data = value.encode("utf-8")
    return struct.pack("<I", len(data)) + data


class InstanceMapTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "sample.imap"

    def test_complete_legacy_and_fh2_records(self):
        for header, width, version in ((b"", 8, None), (b"PAMI" + struct.pack("<I", 1), 7, 1)):
            with self.subTest(width=width):
                mapping = struct.pack("<I", 0) if version else b""
                self.path.write_bytes(header + struct.pack("<I", 1) + string("track.max") +
                                      struct.pack("<I", 1) + bytes(width) + mapping + struct.pack("<I", 1) +
                                      string("12345678-1234-1234-1234-123456789abc") + struct.pack("<I", 0))
                result = instance_map(self.path)
                self.assertEqual(result["version"], version)
                self.assertEqual(result["record_width"], width)
                self.assertEqual(result["records"][0]["guids"], 1)

    def test_rejects_truncated_records_and_trailers(self):
        valid = (struct.pack("<I", 1) + string("track.max") + struct.pack("<I", 1) +
                 bytes(8) + struct.pack("<II", 0, 0))
        for data in (valid[:-1], valid + b"x", b"PAMI" + struct.pack("<I", 2), struct.pack("<I", 100001)):
            with self.subTest(data=data):
                self.path.write_bytes(data)
                with self.assertRaises(ValueError):
                    instance_map(self.path)

    def test_fh2_multiple_references_extra_mappings_and_placement_table(self):
        guid = "12345678-1234-1234-1234-123456789abc"
        data = (b"PAMI" + struct.pack("<II", 1, 1) + string("track.max") +
                struct.pack("<I", 2) + bytes(14) + struct.pack("<II", 1, 42) +
                struct.pack("<I", 1) + string(guid) + struct.pack("<I", 1) +
                string(guid) + struct.pack("<I", 2) + bytes(12))
        self.path.write_bytes(data)
        result = instance_map(self.path)
        self.assertEqual(result["records"][0]["mapping"], [42])
        self.assertEqual(result["placement_references"], 2)
        self.path.write_bytes(data + b"undecoded")
        with self.assertRaises(ValueError):
            instance_map(self.path)
        self.assertEqual(instance_map(self.path, allow_reference_extensions=True)
                         ["unparsed_reference_extension_bytes"], 9)

    def test_staging_verifies_content_and_never_overwrites(self):
        source = self.root / "owned"
        source.mkdir()
        (source / "data.bin").write_bytes(b"owned-source")
        output = self.root / "staged"
        report = {"source": str(source), "reserved_track_id": 10000}
        stage(report, output)
        self.assertEqual((output / "source" / "data.bin").read_bytes(), b"owned-source")
        self.assertEqual(report["staged_bytes"], 12)
        self.assertEqual(len(report["staged_files"][0]["sha256"]), 64)
        self.assertIn("State=blocked", (output / "track.ini").read_text())
        with self.assertRaises(ValueError):
            stage(report, output)
        with self.assertRaises(ValueError):
            stage(report, source / "nested")


if __name__ == "__main__":
    unittest.main()
