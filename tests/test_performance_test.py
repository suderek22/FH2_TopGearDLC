import copy
import json
from pathlib import Path
import struct
import tempfile
import unittest

from build_media import media_catalog
from build_performance_test import build, cull_zone, optimize_root
from build_prop_test import digest
from fh2_pvs import read_root60, write_root60
from fh2_world import MiniZip, draw_lod_metadata, read_pvs_zone60
from repack_media import map_wide_visibility
from spatial import center, write_hex
from test_fh2_root import root_fixture
import test_map_visibility


def fixture_root():
    root = read_root60(write_root60(root_fixture()))
    root["groups"][0]["record"] = struct.pack("<20f", *([0] * 16), 1, 2, 1, 0).hex()
    root["groups"][0]["references"] = [0, 0, 0, 0]
    draw = root["primary_draws"][0]
    raw = bytearray.fromhex(draw["source_record"])
    struct.pack_into("<I", raw, 0, 0)
    struct.pack_into("<4e", raw, 18, 100, 200, 350, 5000)
    struct.pack_into("<3f", raw, 27, 100, 0, 100)
    struct.pack_into("<9e", raw, 39, 1, 0, 0, 0, 1, 0, 0, 0, 1)
    draw["source_record"] = raw.hex()
    draw["metadata"] = draw_lod_metadata(0, root["groups"])
    return read_root60(write_root60(root))


def grid_fixture():
    return {"version": 101, "radius": 100.0, "origin": [0.0, 0.0],
            "columns": 2, "rows": 1, "cells": [0, 1],
            "coordinates": [[0, 0], [1, 0]], "neighbor_masks": [0, 0]}


class PerformanceTests(unittest.TestCase):
    def test_only_distance_bytes_change_and_lod_metadata_is_preserved(self):
        root = fixture_root()
        original = copy.deepcopy(root)
        updated, candidates = optimize_root(root, {0})
        self.assertEqual(root, original)
        self.assertEqual(set(candidates), {0})
        before = bytes.fromhex(root["primary_draws"][0]["source_record"])
        after = bytes.fromhex(updated["primary_draws"][0]["source_record"])
        self.assertEqual(after[:18], before[:18])
        self.assertEqual(after[26:], before[26:])
        self.assertEqual(struct.unpack_from("<4e", after, 18), (100, 200, 350, 500))
        expected = copy.deepcopy(root)
        expected["primary_draws"][0]["source_record"] = after.hex()
        self.assertEqual(updated, expected)

    def test_infinite_lod_band_gets_final_lod_before_culling(self):
        root = fixture_root()
        raw = bytearray.fromhex(root["primary_draws"][0]["source_record"])
        struct.pack_into("<4e", raw, 18, 0, 1, 10000, 10000)
        root["primary_draws"][0]["source_record"] = raw.hex()
        updated, _ = optimize_root(root, {0})
        self.assertEqual(struct.unpack_from("<4e", bytes.fromhex(
            updated["primary_draws"][0]["source_record"]), 18), (0, 1, 450, 500))

    def test_linked_lod_records_keep_matching_bands_and_signed_offsets(self):
        root = fixture_root()
        root["groups"].append(copy.deepcopy(root["groups"][0]))
        for group in root["groups"]:
            group["references"] = [0, 1, 1, 1]
        root["primary_draws"].append(copy.deepcopy(root["primary_draws"][0]))
        for model, draw in enumerate(root["primary_draws"]):
            raw = bytearray.fromhex(draw["source_record"])
            struct.pack_into("<I", raw, 0, model)
            struct.pack_into("<4e", raw, 18, 0, 1, 10000, 10000)
            draw["source_record"] = raw.hex()
            draw["metadata"] = draw_lod_metadata(model, root["groups"])
        root = read_root60(write_root60(root))
        updated, eligible = optimize_root(root, {0, 1})
        self.assertEqual(set(eligible), {0, 1})
        for before, after in zip(root["primary_draws"], updated["primary_draws"]):
            self.assertEqual(after["metadata"], before["metadata"])
            self.assertEqual(struct.unpack_from("<4e", bytes.fromhex(
                after["source_record"]), 18), (0, 1, 450, 500))
        root["primary_draws"][1]["metadata"] = "7f7f7f7f"
        with self.assertRaisesRegex(ValueError, "offsets"):
            optimize_root(root, {0, 1})

    def test_large_world_space_hidden_and_scaled_draws_are_protected(self):
        root = fixture_root()
        for case in ("large", "world", "hidden", "scaled", "large-lod"):
            candidate = copy.deepcopy(root)
            draw = copy.deepcopy(candidate["primary_draws"][0])
            raw = bytearray.fromhex(draw["source_record"])
            if case == "world":
                struct.pack_into("<3f", raw, 27, 0, 0, 0)
            if case == "scaled":
                struct.pack_into("<9e", raw, 39, 100, 0, 0, 0, 100, 0, 0, 0, 100)
            if case in ("large", "large-lod"):
                group = copy.deepcopy(root["groups"][0])
                group["record"] = struct.pack("<20f", *([0] * 16), 100, 100, 100, 0).hex()
                candidate["groups"].append(group)
                if case == "large":
                    struct.pack_into("<I", raw, 0, 1)
                else:
                    candidate["groups"][0]["references"] = [0, 1, 1, 1]
            draw["source_record"] = raw.hex()
            candidate["primary_draws"].append(draw)
            candidate = read_root60(write_root60(candidate))
            visible = {0} if case == "hidden" else {0, 1}
            if case == "large-lod":
                with self.assertRaisesRegex(ValueError, "No small"):
                    optimize_root(candidate, visible)
                continue
            updated, eligible = optimize_root(candidate, visible)
            self.assertEqual(set(eligible), {0}, case)
            self.assertEqual(updated["primary_draws"][1], candidate["primary_draws"][1], case)

    def test_cell_and_mesh_margin_preserves_draws_until_entire_cell_is_outside(self):
        root = fixture_root()
        grid = grid_fixture()
        x, z = center(grid, 0)
        zone = {"draws": [0, 1], "models": [0], "textures": [0],
                "qualities": [0x201], "dependencies": [], "auxiliary": [], "final": []}
        root["primary_draws"].append(copy.deepcopy(root["primary_draws"][0]))
        for distance, expected in ((603, [0, 1]), (603.1, [1])):
            root["primary_draws"][0]["position"] = [x + distance, 0, z]
            result = cull_zone(zone, root, {0: 3}, grid, 0)
            self.assertEqual(result["draws"], expected)
            self.assertEqual({k: v for k, v in result.items() if k != "draws"},
                             {k: v for k, v in zone.items() if k != "draws"})

    def test_culling_decodes_draw_indices_and_preserves_view_flags(self):
        root = fixture_root()
        root["primary_draws"].append(copy.deepcopy(root["primary_draws"][0]))
        grid = grid_fixture()
        x, z = center(grid, 0)
        root["primary_draws"][0]["position"] = [x + 604, 0, z]
        zone = {"draws": [0x80000000, 0xC0000001], "models": [0], "textures": [],
                "qualities": [], "dependencies": [], "auxiliary": [], "final": []}
        self.assertEqual(cull_zone(zone, root, {0: 3}, grid, 0)["draws"], [0xC0000001])

    def test_persisted_pack_keeps_source_residency_physics_shaders_and_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            media, global_media = base / "media-source", base / "global-media"
            relative, _, _, _ = test_map_visibility.MapVisibilityTests().fixture(media)
            root_path = media / relative / "ribbon_00" / "TopGearCPS_00.pvs"
            root = read_root60(root_path.read_bytes())
            root["groups"][0]["record"] = struct.pack("<20f", *([0] * 16), 1, 2, 1, 0).hex()
            root["groups"][1]["record"] = root["groups"][0]["record"]
            for index, draw in enumerate(root["primary_draws"]):
                raw = bytearray.fromhex(draw["source_record"])
                struct.pack_into("<3f", raw, 27, 100 + 1500 * index, 0, 100)
                struct.pack_into("<9e", raw, 39, 1, 0, 0, 0, 1, 0, 0, 0, 1)
                draw["source_record"] = raw.hex()
                draw["metadata"] = draw_lod_metadata(0, root["groups"])
            root_path.write_bytes(write_root60(root))
            map_wide_visibility(media, global_media, 5000)
            source = base / "track"
            source.mkdir()
            global_media.rename(source / "media")
            report_path = source / "media" / "media.json"
            report = json.loads(report_path.read_text())
            grid_path = source / "media" / relative / "ribbon_00" / "TopGearCPSout_00.hex"
            grid_path.write_bytes(write_hex(grid_fixture()))
            report["files"].append({
                "path": str(grid_path.relative_to(source / "media")),
                "media_path": str(grid_path.relative_to(source / "media" / "mediaoverride")),
                "bytes": grid_path.stat().st_size, "sha256": digest(grid_path)})
            catalog = source / "media" / report["catalog_name"]
            catalog.write_bytes(media_catalog(report["media_name"], report["files"]))
            report["catalog_sha256"] = digest(catalog)
            report_path.write_text(json.dumps(report))
            (source / "track.ini").write_text("CatalogSha256=" + report["catalog_sha256"] + "\n")
            hashes = {path: digest(path) for path in source.rglob("*") if path.is_file()}
            output = base / "performance"
            result = build(source, output)
            self.assertEqual(hashes, {path: digest(path) for path in hashes})
            stats = result["conservative_performance_test"]
            self.assertEqual(stats["optimized_visible_draws"], 2)
            self.assertEqual(stats["zone_draw_references_before"], 4)
            self.assertEqual(stats["zone_draw_references_after"], 2)
            old = MiniZip(source / "media" / relative / "GeoChunk0.minizip")
            new = MiniZip(output / "media" / relative / "GeoChunk0.minizip")
            keys = struct.unpack("<7I", (source / "media" / relative / "ChunkMap0.dat").read_bytes())
            for index, key in enumerate(keys):
                if key >> 24 == 12:
                    before, after = read_pvs_zone60(old.read(index)), read_pvs_zone60(new.read(index))
                    self.assertEqual(after["draws"], [0])
                    self.assertEqual(dict(before, draws=[]), dict(after, draws=[]))
                else:
                    self.assertEqual(old.read(index), new.read(index))
            for row in result["files"]:
                path = output / "media" / row["path"]
                self.assertEqual(row["sha256"], digest(path))
                self.assertEqual(row["bytes"], path.stat().st_size)
            self.assertEqual(result["catalog_sha256"], digest(output / "media" / "media.catalog"))
            self.assertIn(result["catalog_sha256"], (output / "track.ini").read_text())


if __name__ == "__main__":
    unittest.main()
