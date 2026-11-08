from pathlib import Path
import struct
import tempfile
import unittest
import zlib

from build_geometry import resources_for, validate_container
from fh2_world import MiniZip, Resource, write_minizip


class MiniZipTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "GeoChunk0.minizip"

    def test_roundtrip_multiple_subchunks_and_resource_types(self):
        resources = [Resource("%d.modelbin" % index, index, 4) for index in range(513)]
        resources.append(Resource("texture.swatchbin", 0, 5))
        def content(resource):
            return (resource.name.encode() + b"\0") * 100
        lookup = write_minizip(self.path, resources, content)
        archive = MiniZip(self.path)
        self.assertEqual(len(archive.entries), 514)
        self.assertEqual(archive.folders, (0, 514))
        for index, resource in enumerate(resources):
            self.assertEqual(archive.read(index), content(resource))
            self.assertEqual(lookup[index * 4:index * 4 + 4], resource.lookup())
        with self.assertRaises(ValueError):
            write_minizip(self.path, resources, content)

    def test_invalid_ids_and_duplicate_keys_are_rejected(self):
        for resources in ([Resource("bad", 0x1000000, 4)],
                          [Resource("a", 0, 4), Resource("b", 0, 4)]):
            with self.subTest(resources=resources), self.assertRaises(ValueError):
                write_minizip(self.path, resources, lambda resource: b"x")
        self.assertFalse(self.path.exists())

    def test_resource_key_uses_all_24_id_bits(self):
        self.assertEqual(Resource("texture", (8193 << 4) | 2, 5).lookup(),
                         struct.pack("<I", 0x05020012))
        self.assertEqual(Resource("texture", (635 << 4) | 1, 5).lookup(),
                         bytes.fromhex("b1270005"))

    def test_failed_write_removes_only_partial_file(self):
        def fail(resource):
            raise OSError("source failed")
        with self.assertRaises(OSError):
            write_minizip(self.path, [Resource("a", 0, 4)], fail)
        self.assertFalse(self.path.exists())
        self.assertFalse(self.path.with_name(self.path.name + ".partial").exists())

    def test_rejects_truncation_and_invalid_deflate_payload(self):
        write_minizip(self.path, [Resource("a", 0, 4)], lambda resource: bytes(1024))
        archive = MiniZip(self.path)
        data = bytearray(self.path.read_bytes())
        data[archive.entries[0].offset] ^= 0xFF
        self.path.write_bytes(data)
        with self.assertRaises((ValueError, zlib.error)):
            MiniZip(self.path).read(0)
        self.path.write_bytes(data[:-1])
        with self.assertRaises(ValueError):
            MiniZip(self.path)

    def test_resource_ids_preserve_models_and_are_independent_of_zip_order(self):
        names = ["TopGearout.00054.modelbin", "TopGearout.00011.modelbin",
                 "_0xBBBB.swatchbin", "_0xAAAA.swatchbin"]
        textures = [{"payloads": [{"name": "_0xAAAA.swatchbin", "resource_id": 16},
                                  {"name": "_0xBBBB.swatchbin", "resource_id": 32}]}]
        resources = resources_for(names, textures)
        self.assertEqual(resources, resources_for(reversed(names), textures))
        self.assertEqual([(resource.resource_id, resource.kind) for resource in resources],
                         [(11, 4), (54, 4), (16, 5), (32, 5)])

    def test_container_validation_rejects_unknown_version_and_bounds(self):
        for data in (b"", b"burG" + bytes(12),
                     b"burG" + struct.pack("<HHII", 2, 0, 16, 16)):
            with self.subTest(data=data), self.assertRaises(ValueError):
                validate_container(data)


if __name__ == "__main__":
    unittest.main()
