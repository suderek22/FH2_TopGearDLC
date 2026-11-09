import unittest
import configparser
import hashlib
import json
from pathlib import Path
import tempfile
from PIL import Image

from build_thumbnails import decode_fm5, decode_native, install, letterbox


class ThumbnailTests(unittest.TestCase):
    def artwork_fixture(self, root):
        manifest = root / "track.ini"
        manifest.write_text("[Track]\nId=10000\n[Media]\nCatalogSha256=unchanged\n", encoding="ascii")
        artwork = {}
        for size in ("Large", "Small"):
            filename = f"TopGear_{size}.swatchbin"
            data = size.encode("ascii")
            (root / filename).write_bytes(data)
            artwork[size.lower()] = {"file": filename, "sha256": hashlib.sha256(data).hexdigest()}
        (root / "thumbnails.json").write_text(json.dumps({
            "format": "TLCPS-THUMBNAILS-1", "tracks": {"TopGear": artwork}}))
        return manifest

    def test_artwork_install_preserves_world_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self.artwork_fixture(root)
            install(root, "TopGear", manifest)
            ini = configparser.ConfigParser()
            ini.read(manifest)
            self.assertEqual(ini["Track"]["Id"], "10000")
            self.assertEqual(ini["Media"]["CatalogSha256"], "unchanged")
            self.assertEqual((root / "browser" / "poster.swatchbin").read_bytes(), b"Large")
            self.assertEqual(ini["Browser"]["LogoSha256"], hashlib.sha256(b"Small").hexdigest())

    def test_corruption_does_not_change_track_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self.artwork_fixture(root)
            before = manifest.read_bytes()
            (root / "TopGear_Small.swatchbin").write_bytes(b"corrupt")
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                install(root, "TopGear", manifest)
            self.assertEqual(manifest.read_bytes(), before)
            self.assertFalse((root / "browser").exists())

    def test_letterbox_preserves_aspect_and_transparency(self):
        image = Image.new("RGBA", (200, 200), (250, 20, 10, 127))
        result = letterbox(image, (290, 179))
        self.assertEqual(result.size, (290, 179))
        self.assertEqual(result.getpixel((0, 0)), (0, 0, 0, 0))
        self.assertEqual(result.getpixel((145, 89)), (250, 20, 10, 127))
        self.assertEqual(result.getbbox(), (55, 0, 234, 179))

    def test_letterbox_rejects_invalid_dimensions(self):
        with self.assertRaises(ValueError):
            letterbox(Image.new("RGBA", (10, 10)), (0, 100))

    def test_unknown_descriptor_is_not_guessed(self):
        class Swatch:
            @staticmethod
            def read_header(data):
                return 1085, 611, 1, bytes(35), b"payload"

        with self.assertRaisesRegex(ValueError, "descriptor"):
            decode_fm5(b"", Swatch)
        with self.assertRaisesRegex(ValueError, "descriptor"):
            decode_native(b"", Swatch)

    def test_payload_size_is_verified_and_computer_released(self):
        class Computer:
            ok = True
            closed = False

            def size(self):
                return 10

            def close(self):
                self.closed = True

        computer = Computer()

        class Swatch:
            @staticmethod
            def read_header(data):
                return 200, 200, 1, bytes(31) + bytes.fromhex("ae010090"), b"short"

            @staticmethod
            def _Computer(*args):
                return computer

        with self.assertRaisesRegex(ValueError, "size mismatch"):
            decode_fm5(b"", Swatch)
        self.assertTrue(computer.closed)
