import copy
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from build_media import media_catalog
from build_session import verify_files
from fh2_pvs import read_root60, write_root60
from fh2_world import MiniZip, Resource, read_pvs_zone60, write_minizip, write_pvs_zone60
from repack_media import digest, enable_secondary_views, main, map_wide_visibility, repair_topgear_markings
from test_fh2_root import root_fixture


class MapVisibilityTests(unittest.TestCase):
    def test_crashing_secondary_view_cli_is_disabled(self):
        error = io.StringIO()
        with patch("sys.argv", ["repack_media.py", "--source", "unused-source",
                                "--output", "unused-output", "--secondary-view-visibility"]), \
                patch("repack_media.enable_secondary_views") as transform, redirect_stderr(error):
            self.assertEqual(main(), 1)
            transform.assert_not_called()
        self.assertIn("disabled", error.getvalue())
        self.assertIn("ExecuteCommandList object/device mismatch", error.getvalue())

    def fixture(self, source):
        relative = Path("mediaoverride") / "tracks" / "TopGearCPS"
        folder = source / relative
        (folder / "ribbon_00").mkdir(parents=True)
        root = root_fixture()
        root["zone_count"] = 2
        root["zone_u16_data"] = ["", ""]
        root["groups"].append(copy.deepcopy(root["groups"][0]))
        root["groups"][0]["references"] = [0, 1, 1, 1]
        root["textures"].append(bytes(56).hex())
        root["groups"][1]["textures"] = [1]
        for index, bands in enumerate(((100, 150, 200, -1), (0, 1, 10000, 10000))):
            draw = copy.deepcopy(root["primary_draws"][0])
            raw = bytearray.fromhex(draw["source_record"])
            struct.pack_into("<I", raw, 0, 0)
            struct.pack_into("<H", raw, 12, index)
            struct.pack_into("<4e", raw, 18, *bands)
            draw["source_record"] = raw.hex()
            if index:
                root["primary_draws"].append(draw)
                root["instances"].append(copy.deepcopy(root["instances"][0]))
            else:
                root["primary_draws"][0] = draw
        root_data = write_root60(root)
        (folder / "ribbon_00" / "TopGearCPS_00.pvs").write_bytes(root_data)
        (folder / "ChunkAssign.dat").write_bytes(struct.pack("<4I", 100, 2, 0, 0))
        zones = [{
            "draws": [index], "models": [0], "textures": [0], "qualities": [0x101],
            "dependencies": [[10 + index, 0x01000000]], "auxiliary": [index], "final": [index + 1]}
            for index in range(2)]
        payloads = {
            0x01000000: b"physics", 0x04000000: b"model0", 0x04000001: b"lod-model",
            0x05000000: b"low-mip", 0x05000001: b"high-mip",
            0x0C000000: write_pvs_zone60(zones[0]), 0x0C000001: write_pvs_zone60(zones[1])}
        resources = [Resource(str(key), key & 0xFFFFFF, key >> 24) for key in payloads]
        lookup = write_minizip(folder / "GeoChunk0.minizip", resources,
                               lambda resource: payloads[int(resource.name)])
        (folder / "ChunkMap0.dat").write_bytes(lookup)
        (folder / "bin.zip").write_bytes(b"unchanged shaders")
        inventory = [{"path": str(path.relative_to(source)),
                      "media_path": str(path.relative_to(source / "mediaoverride")),
                      "bytes": path.stat().st_size, "sha256": digest(path)}
                     for path in folder.rglob("*") if path.is_file()]
        catalog = source / "media.catalog"
        catalog.write_bytes(media_catalog("TopGearCPS", inventory))
        report = {"catalog_name": catalog.name, "catalog_sha256": digest(catalog),
                  "media_name": "TopGearCPS", "override_relative_path": str(relative), "files": inventory}
        (source / "media.json").write_text(json.dumps(report))
        return relative, root_data, zones, payloads

    def test_all_draws_global_closure_highest_mips_and_preserved_physics(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, output = Path(temporary) / "source", Path(temporary) / "output"
            relative, original, zones, payloads = self.fixture(source)
            hashes = {p: digest(p) for p in source.rglob("*") if p.is_file()}
            result = map_wide_visibility(source, output, 5000)
            self.assertEqual(result["map_wide_visibility"], {
                "minimum_cull_metres": 5000, "draws": 2, "models": 2, "textures": 2, "zones": 2,
                "highest_available_texture_levels": True})
            rewritten = read_root60((output / relative / "ribbon_00" / "TopGearCPS_00.pvs").read_bytes())
            expected = read_root60(original)
            for draw in expected["primary_draws"]:
                raw = bytearray.fromhex(draw["source_record"])
                bands = struct.unpack_from("<4e", raw, 18)
                struct.pack_into("<e", raw, 24, max(5000, *bands))
                draw["source_record"] = raw.hex()
            expected["zone_maxima"] = [2, 2, 2]
            self.assertEqual(rewritten, read_root60(write_root60(expected)))
            archive = MiniZip(output / relative / "GeoChunk0.minizip")
            for index, key in enumerate(payloads):
                data = archive.read(index)
                if key >> 24 != 12:
                    self.assertEqual(data, payloads[key])
                else:
                    expected_zone = dict(zones[key & 0xFFFFFF], draws=[0, 1], models=[0, 1],
                                         textures=[0, 1], qualities=[0x201, 1])
                    self.assertEqual(read_pvs_zone60(data), expected_zone)
            self.assertEqual(result["catalog_sha256"], digest(output / "media.catalog"))
            self.assertEqual(hashes, {p: digest(p) for p in hashes})

    def test_invalid_thresholds_and_missing_lod_resources_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, output = Path(temporary) / "source", Path(temporary) / "output"
            relative, _, _, _ = self.fixture(source)
            for distance in (0, -1, float("nan"), float("inf"), 65504):
                with self.assertRaises(ValueError):
                    map_wide_visibility(source, output, distance)
                self.assertFalse(output.exists())
            path = source / relative / "ribbon_00" / "TopGearCPS_00.pvs"
            root = read_root60(path.read_bytes())
            root["groups"][0]["references"] = [0, 2, 2, 2]
            path.write_bytes(write_root60(root))
            with self.assertRaisesRegex(ValueError, "missing model"):
                map_wide_visibility(source, output, 5000)
            self.assertFalse(output.exists())

    def test_secondary_view_flags_preserve_indices_resources_and_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source, output = base / "source", base / "output"
            relative, original, zones, payloads = self.fixture(source)
            zones[0]["draws"] = [0x40000000]
            zones[1]["draws"] = [0x80000001]
            for index, zone in enumerate(zones):
                payloads[0x0C000000 | index] = write_pvs_zone60(zone)
            folder = source / relative
            (folder / "GeoChunk0.minizip").unlink()
            resources = [Resource(str(key), key & 0xFFFFFF, key >> 24) for key in payloads]
            lookup = write_minizip(folder / "GeoChunk0.minizip", resources,
                                   lambda resource: payloads[int(resource.name)])
            self.assertEqual(lookup, (folder / "ChunkMap0.dat").read_bytes())
            report = json.loads((source / "media.json").read_text())
            for row in report["files"]:
                path = source / row["path"]
                row["bytes"], row["sha256"] = path.stat().st_size, digest(path)
            (source / "media.catalog").write_bytes(media_catalog("TopGearCPS", report["files"]))
            report["catalog_sha256"] = digest(source / "media.catalog")
            (source / "media.json").write_text(json.dumps(report))
            hashes = {path: digest(path) for path in source.rglob("*") if path.is_file()}

            result = enable_secondary_views(source, output)
            self.assertEqual(result["secondary_view_visibility_test"]["zones_changed"], 2)
            self.assertEqual(result["secondary_view_visibility_test"]["draw_references_changed"], 2)
            archive = MiniZip(output / relative / "GeoChunk0.minizip")
            for index, key in enumerate(payloads):
                if key >> 24 == 12:
                    zone = zones[key & 0xFFFFFF]
                    expected = dict(zone, draws=[entry | 0xC0000000 for entry in zone["draws"]])
                    self.assertEqual(read_pvs_zone60(archive.read(index)), expected)
                else:
                    self.assertEqual(archive.read(index), payloads[key])
            for row in result["files"]:
                if Path(row["path"]).name != "GeoChunk0.minizip":
                    self.assertEqual((output / row["path"]).read_bytes(),
                                     (source / row["path"]).read_bytes())
            self.assertEqual((output / relative / "ribbon_00" / "TopGearCPS_00.pvs").read_bytes(),
                             original)
            verify_files(output, result)
            self.assertEqual((output / "media.catalog").read_bytes(),
                             media_catalog("TopGearCPS", result["files"]))
            self.assertEqual(result["catalog_sha256"], digest(output / "media.catalog"))
            self.assertEqual(hashes, {path: digest(path) for path in hashes})
            with self.assertRaisesRegex(ValueError, "already enabled"):
                enable_secondary_views(output, base / "repeat")
            self.assertFalse((base / "repeat").exists())

            global_output = base / "global"
            global_report = map_wide_visibility(output, global_output, 5000)
            self.assertEqual(global_report["map_wide_visibility"]["draws"], 2)
            merged = MiniZip(global_output / relative / "GeoChunk0.minizip")
            for index, key in enumerate(payloads):
                if key >> 24 == 12:
                    self.assertEqual(read_pvs_zone60(merged.read(index))["draws"],
                                     [0xC0000000, 0xC0000001])

    def test_marking_flags_preserve_original_payloads_and_other_draws(self):
        def model(materials):
            blobs = [(b"ldoM", 1, 1, b""), (b"hseM", 1, 7, b""),
                     (b"yaLV", 1, 1, b"")]
            blobs += [(b"ItaM", 1, 0, material) for material in materials]
            start = 16 + 24 * len(blobs)
            data = b"burG" + struct.pack("<HHII", 1, len(blobs), start,
                                        start + sum(len(blob[3]) for blob in blobs))
            position = start
            for tag, major, minor, payload in blobs:
                data += struct.pack("<4sBBHIIII", tag, major, minor, 0, start,
                                    position, len(payload), len(payload))
                position += len(payload)
            return data + b"".join(blob[3] for blob in blobs)

        with tempfile.TemporaryDirectory() as temporary:
            source, output = Path(temporary) / "source", Path(temporary) / "output"
            relative, _, _, payloads = self.fixture(source)
            root_path = source / relative / "ribbon_00" / "TopGearCPS_00.pvs"
            root = read_root60(root_path.read_bytes())
            base = root["primary_draws"][0]
            root["primary_draws"] = []
            cases = [(0, 0x1FFF1214), (1, 0x1FFF1214), (2, 0x1FFF1214),
                     (3, 0x1FFF1214), (0, 0x0FFF1024), (0, 0x12345678)]
            for ordinal, flags in cases:
                row = copy.deepcopy(base)
                raw = bytearray.fromhex(row["source_record"])
                struct.pack_into("<IQ", raw, 0, ordinal, flags)
                row["source_record"] = raw.hex()
                root["primary_draws"].append(row)
            root["secondary_draws"] = [copy.deepcopy(root["primary_draws"][0])]
            original = write_root60(root)
            root_path.write_bytes(original)
            line = b"Game:\\Media\\tracks\\topgear\\materials\\roadlines_topg_white.materialbin"
            road = b"Game:\\Media\\tracks\\topgear\\materials\\road_topg_default.materialbin"
            grate = b"Game:\\Media\\tracks\\topgear\\materials\\topg_runwaygrates.materialbin"
            payloads.update({0x04000000: model([line.upper(), line]),
                             0x04000001: model([line, road]),
                             0x04000002: model([road]), 0x04000003: model([])})
            for ordinal, companion in enumerate((
                    grate, b"Game:\\Media\\tracks\\topgear\\materials\\topg_reflactor_001.materialbin",
                    b"Game:\\Media\\tracks\\topgear\\materials\\topg_harrierlandinglights_001.materialbin",
                    b"Game:\\Media\\tracks\\topgear\\materials\\topg_harrierlandinglights_002.materialbin",
                    b"Game:\\Media\\tracks\\topgear\\materials\\topg_harrierpane.materialbin"), 4):
                payloads[0x04000000 | ordinal] = model([line, companion])
                row = copy.deepcopy(base)
                raw = bytearray.fromhex(row["source_record"])
                struct.pack_into("<IQ", raw, 0, ordinal, 0x1FFF1214)
                row["source_record"] = raw.hex()
                root["primary_draws"].append(row)
            payloads[0x04000009] = model([grate])
            row = copy.deepcopy(base)
            raw = bytearray.fromhex(row["source_record"])
            struct.pack_into("<IQ", raw, 0, 9, 0x1FFF1214)
            row["source_record"] = raw.hex()
            root["primary_draws"].append(row)
            original = write_root60(root)
            root_path.write_bytes(original)
            resources = [Resource(str(key), key & 0xFFFFFF, key >> 24) for key in payloads]
            (source / relative / "GeoChunk0.minizip").unlink()
            lookup = write_minizip(source / relative / "GeoChunk0.minizip", resources,
                                   lambda resource: payloads[int(resource.name)])
            (source / relative / "ChunkMap0.dat").write_bytes(lookup)
            hashes = {p: digest(p) for p in source.rglob("*") if p.is_file()}
            result = repair_topgear_markings(source, output)
            self.assertEqual(result["repaired_topgear_markings"], {"draws": 2, "models": [0]})
            expected = read_root60(original)
            for row in (expected["primary_draws"][0], expected["secondary_draws"][0]):
                raw = bytearray.fromhex(row["source_record"])
                struct.pack_into("<Q", raw, 4, 0x0FFF1024)
                row["source_record"] = raw.hex()
            self.assertEqual((output / root_path.relative_to(source)).read_bytes(),
                             write_root60(expected))
            for path in hashes:
                self.assertEqual(digest(path), hashes[path])
                if path not in (root_path, source / "media.json", source / "media.catalog"):
                    self.assertEqual(digest(output / path.relative_to(source)), hashes[path])
            self.assertEqual(result["catalog_sha256"], digest(output / "media.catalog"))
            for row in result["files"]:
                self.assertEqual(row["sha256"], digest(output / row["path"]))
            with self.assertRaisesRegex(ValueError, "No Top Gear marking"):
                repair_topgear_markings(output, Path(temporary) / "repeat")
            self.assertFalse((Path(temporary) / "repeat").exists())
            with self.assertRaisesRegex(ValueError, "new media output"):
                repair_topgear_markings(source, output)
            runway_output = Path(temporary) / "runway"
            result = repair_topgear_markings(output, runway_output, include_runway=True)
            self.assertEqual(result["repaired_topgear_runway_markings"],
                             {"draws": 5, "models": [4, 5, 6, 7, 8]})
            self.assertEqual(result["repaired_topgear_markings"], {"draws": 2, "models": [0]})
            expected = read_root60(write_root60(expected))
            for row in expected["primary_draws"]:
                if row["model_id"] in range(4, 9):
                    raw = bytearray.fromhex(row["source_record"])
                    struct.pack_into("<Q", raw, 4, 0x0FFF1024)
                    row["source_record"] = raw.hex()
            self.assertEqual((runway_output / root_path.relative_to(source)).read_bytes(),
                             write_root60(expected))
            for path in hashes:
                if path not in (root_path, source / "media.json", source / "media.catalog"):
                    self.assertEqual(digest(runway_output / path.relative_to(source)), hashes[path])
            self.assertEqual(result["catalog_sha256"], digest(runway_output / "media.catalog"))
            with self.assertRaisesRegex(ValueError, "No Top Gear marking"):
                repair_topgear_markings(runway_output, Path(temporary) / "runway-repeat",
                                       include_runway=True)


if __name__ == "__main__":
    unittest.main()
