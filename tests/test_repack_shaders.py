import json
from pathlib import Path
import tempfile
import struct
import unittest
import zipfile

from build_media import media_catalog
from repack_media import (bake_default_draw_distances, digest, neutralize_legacy_ao,
                          neutralize_cubemap_ao,
                          neutralize_macro_tint, neutralize_pavement_skid,
                          neutralize_terrain_macro_tint, repair_draw_lods, guard_marking_normals,
                          repoint_material_textures)
from fh2_pvs import read_root60, write_root60
from test_fh2_root import root_fixture
from test_shader_patch import container, instructions, skid_reflection, RET, SAMPLE
from shader_patch import dxbc_checksum


class RepackShaderTests(unittest.TestCase):
    def test_material_texture_repoint_only_replaces_missing_mapped_hashes(self):
        def ref(name, value):
            return b"\\" + name + b".swatchbin" + struct.pack("<I", value) + b"\x02\x00\xd9\x8c"

        data = (b"head" + ref(b"blank_topg_swatches_mask", 0xDD286753)
                + ref(b"road_diff", 0x11111111) + ref(b"road_topg_skids_080_mask", 0xE5164915))
        patched, count = repoint_material_textures(data, {0x9ACB43CD, 0x11111111})
        self.assertEqual(count, 1)
        self.assertEqual(len(patched), len(data))
        self.assertEqual(patched, data.replace(struct.pack("<I", 0xDD286753), struct.pack("<I", 0x9ACB43CD)))
        with self.assertRaises(ValueError):
            repoint_material_textures(data, {0x11111111})
    def test_marking_normal_guard_selects_only_normal_lighting_programs(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, output = Path(temporary) / "source", Path(temporary) / "output"
            root = source / "mediaoverride" / "tracks" / "TopGearCPS"
            root.mkdir(parents=True)
            square_root = [75 | (5 << 24), 0x00100082, 2, 0x0010003A, 2]
            normal = SAMPLE.copy()
            normal[6] = 18
            original = container(normal + square_root + [RET],
                                 reflection=skid_reflection(18, b"Ch5RoadLinesNormalTextureTexture"))
            members = {
                "Shaders/test/roadlinedecal%s.%s.pso" % (scenario, platform): original
                for scenario in ("TrackLightScenario", "DebugLightingLightScenario")
                for platform in ("pc", "durango")}
            for name in ("roadlinedecalCubemapLightScenario.pc.pso",
                         "roadlinedecalShadowDepthLightScenario.pc.pso",
                         "roadlinedecalTrackLightScenario.pc.vso", "road_basicTrackLightScenario.pc.pso"):
                members["Shaders/test/" + name] = b"untouched"
            members["Materials/paint.materialbin"] = b"authored paint"
            path = root / "bin.zip"
            with zipfile.ZipFile(path, "w") as archive:
                for name, data in members.items():
                    archive.writestr(name, data)
            inventory = [{"path": str(path.relative_to(source)),
                          "media_path": "tracks\\TopGearCPS\\bin.zip",
                          "bytes": path.stat().st_size, "sha256": digest(path)}]
            catalog = source / "media.catalog"
            catalog.write_bytes(media_catalog("TopGearCPS", inventory))
            report = {"catalog_name": catalog.name, "catalog_sha256": digest(catalog),
                      "media_name": "TopGearCPS", "override_relative_path": str(root.relative_to(source)),
                      "files": inventory, "repaired_topgear_runway_markings": {"draws": 32}}
            (source / "media.json").write_text(json.dumps(report))
            hashes = {p: digest(p) for p in source.rglob("*") if p.is_file()}
            result = guard_marking_normals(source, output)
            self.assertEqual(result["guarded_marking_normals"], {"programs": 4, "normal_sqrt_guards": 4})
            self.assertEqual(result["repaired_topgear_runway_markings"], {"draws": 32})
            with zipfile.ZipFile(output / path.relative_to(source)) as archive:
                self.assertEqual(archive.namelist(), list(members))
                for name, data in members.items():
                    patched = archive.read(name)
                    if data != original:
                        self.assertEqual(patched, data)
                    else:
                        code = instructions(patched)
                        self.assertEqual(code[:len(normal)], normal)
                        self.assertEqual(code[len(normal)] & 0x7FF, 52)
                        self.assertEqual(code[len(normal) + 7:], square_root + [RET])
            self.assertEqual(hashes, {p: digest(p) for p in hashes})
            self.assertEqual(result["catalog_sha256"], digest(output / catalog.name))
            with self.assertRaisesRegex(ValueError, "already have"):
                guard_marking_normals(output, Path(temporary) / "repeat")

    def test_pavement_skid_covers_all_families_scenarios_and_platforms(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, output = Path(temporary) / "source", Path(temporary) / "output"
            root = source / "mediaoverride" / "tracks" / "TopGearCPS"
            root.mkdir(parents=True)
            diffuse, overlay = SAMPLE.copy(), SAMPLE.copy()
            diffuse[6], overlay[6] = 17, 18
            original = container(SAMPLE + diffuse + overlay + [RET],
                                 reflection=skid_reflection())
            families = ("road_basic", "road_2blend_threshold_001", "road_basic_ch5multiply",
                        "road_basic_nonorm_ch5overlay", "roadedgedecal_basic", "roadlinedecal")
            members = {
                "Shaders/test/%s%s.%s.pso" % (family, scenario, platform): original
                for family in families
                for scenario in ("TrackLightScenario", "CubemapLightScenario",
                                 "DebugLightingLightScenario")
                for platform in ("pc", "durango")}
            members["Shaders/test/terrain.pc.pso"] = container(
                SAMPLE + [RET], reflection=skid_reflection(name=b"DiffuseATexture"))
            members["Shaders/test/road_basicTrackLightScenario.pc.vso"] = original
            members["Materials/test.materialbin"] = b"authored material"
            path = root / "bin.zip"
            with zipfile.ZipFile(path, "w") as archive:
                for name, data in members.items():
                    archive.writestr(name, data)
            inventory = [{"path": str(path.relative_to(source)),
                          "media_path": "tracks\\TopGearCPS\\bin.zip",
                          "bytes": path.stat().st_size, "sha256": digest(path)}]
            catalog = source / "media.catalog"
            catalog.write_bytes(media_catalog("TopGearCPS", inventory))
            report = {"catalog_name": catalog.name, "catalog_sha256": digest(catalog),
                      "media_name": "TopGearCPS",
                      "override_relative_path": str(root.relative_to(source)), "files": inventory}
            (source / "media.json").write_text(json.dumps(report))
            hashes = digest(catalog), digest(path), digest(source / "media.json")
            result = neutralize_pavement_skid(source, output)
            self.assertEqual(result["neutralized_pavement_skid"],
                             {"programs": 36, "skid_samples": 36})
            self.assertEqual(result["catalog_sha256"], digest(output / catalog.name))
            self.assertEqual(hashes, (digest(catalog), digest(path), digest(source / "media.json")))
            with zipfile.ZipFile(output / path.relative_to(source)) as archive:
                for name, data in members.items():
                    patched = archive.read(name)
                    if not name.endswith(".pso") or "terrain" in name:
                        self.assertEqual(patched, data)
                    else:
                        self.assertEqual(instructions(patched)[:8], [
                            54 | (8 << 24), SAMPLE[1], SAMPLE[2], 0x4002,
                            0x3f800000, 0x3f800000, 0x3f800000, 0x3f800000])
                        self.assertEqual(instructions(patched)[8:], diffuse + overlay + [RET])
            with self.assertRaisesRegex(ValueError, "already have"):
                neutralize_pavement_skid(output, Path(temporary) / "duplicate")

    def test_legacy_ao_keeps_diffuse_specular_and_unrelated_programs(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, output = Path(temporary) / "source", Path(temporary) / "output"
            root = source / "mediaoverride" / "tracks" / "TopGearCPS"
            root.mkdir(parents=True)
            ao, specular = SAMPLE.copy(), SAMPLE.copy()
            ao[1], ao[6], specular[6] = 0x00100082, 6, 1
            original = container(SAMPLE + ao + specular + [RET])
            members = {
                "Shaders/test/diffnormmaskgloss%s.%s.pso" % (scenario, platform): original
                for scenario in ("TrackLightScenario", "CubemapLightScenario",
                                 "DebugLightingLightScenario")
                for platform in ("pc", "durango")}
            members["Shaders/test/other.pc.pso"] = b"unrelated"
            members["Shaders/test/diffnormmaskglossTrackLightScenario.pc.vso"] = b"vertex"
            path = root / "bin.zip"
            with zipfile.ZipFile(path, "w") as archive:
                for name, data in members.items():
                    archive.writestr(name, data)
            inventory = [{"path": str(path.relative_to(source)),
                          "media_path": "tracks\\TopGearCPS\\bin.zip",
                          "bytes": path.stat().st_size, "sha256": digest(path)}]
            catalog = source / "media.catalog"
            catalog.write_bytes(media_catalog("TopGearCPS", inventory))
            report = {"catalog_name": catalog.name, "catalog_sha256": digest(catalog),
                      "media_name": "TopGearCPS",
                      "override_relative_path": str(root.relative_to(source)), "files": inventory}
            (source / "media.json").write_text(json.dumps(report))
            hashes = digest(catalog), digest(path), digest(source / "media.json")
            result = neutralize_legacy_ao(source, output)
            self.assertEqual(result["neutralized_legacy_ao"], {"programs": 6, "ao_samples": 6})
            self.assertEqual(result["catalog_sha256"], digest(output / catalog.name))
            self.assertEqual(hashes, (digest(catalog), digest(path), digest(source / "media.json")))
            with zipfile.ZipFile(output / path.relative_to(source)) as archive:
                for name, data in members.items():
                    rewritten = archive.read(name)
                    if data != original:
                        self.assertEqual(rewritten, data)
                    else:
                        code = instructions(rewritten)
                        self.assertEqual(code[:len(SAMPLE)], SAMPLE)
                        self.assertEqual(code[len(SAMPLE)], 54 | (8 << 24))
                        self.assertEqual(code[-len(specular) - 1:], specular + [RET])
            with self.assertRaisesRegex(ValueError, "already have"):
                neutralize_legacy_ao(output, Path(temporary) / "duplicate")

    def test_cubemap_ao_changes_only_named_ao_in_both_platform_pixel_programs(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, output = Path(temporary) / "source", Path(temporary) / "output"
            root = source / "mediaoverride" / "tracks" / "TopGearCPS"
            root.mkdir(parents=True)
            ao = SAMPLE.copy()
            ao[6] = 4
            original = container(ao + SAMPLE + [RET], reflection=skid_reflection(4, b"AOMap"))
            members = {
                "Shaders/test/road_basicCubemapLightScenario.%s.pso" % platform: original
                for platform in ("pc", "durango")}
            for scenario in ("TrackLightScenario", "DebugLightingLightScenario",
                             "ShadowDepthLightScenario"):
                members["Shaders/test/road_basic%s.durango.pso" % scenario] = original
            members["Shaders/test/road_basicCubemapLightScenario.durango.vso"] = original
            members["Shaders/test/diffCubemapLightScenario.pc.pso"] = container(
                ao + [RET], reflection=skid_reflection(4, b"DiffuseATexture"))
            members["Materials/test.materialbin"] = b"authored material"
            path = root / "bin.zip"
            with zipfile.ZipFile(path, "w") as archive:
                for name, data in members.items():
                    archive.writestr(name, data)
            inventory = [{"path": str(path.relative_to(source)),
                          "media_path": "tracks\\TopGearCPS\\bin.zip",
                          "bytes": path.stat().st_size, "sha256": digest(path)}]
            catalog = source / "media.catalog"
            catalog.write_bytes(media_catalog("TopGearCPS", inventory))
            report = {"catalog_name": catalog.name, "catalog_sha256": digest(catalog),
                      "media_name": "TopGearCPS",
                      "override_relative_path": str(root.relative_to(source)), "files": inventory}
            (source / "media.json").write_text(json.dumps(report))
            hashes = digest(catalog), digest(path), digest(source / "media.json")
            result = neutralize_cubemap_ao(source, output)
            self.assertEqual(result["neutralized_cubemap_ao"], {"programs": 2, "ao_samples": 2})
            self.assertEqual(result["catalog_sha256"], digest(output / catalog.name))
            self.assertEqual(hashes, (digest(catalog), digest(path), digest(source / "media.json")))
            with zipfile.ZipFile(output / path.relative_to(source)) as archive:
                self.assertEqual(archive.namelist(), list(members))
                for name, data in members.items():
                    patched = archive.read(name)
                    if name.startswith("Shaders/test/road_basicCubemap") and name.endswith(".pso"):
                        self.assertEqual(instructions(patched),
                                         [54 | (8 << 24), ao[1], ao[2], 0x4002,
                                          0x3f800000, 0x3f800000, 0x3f800000, 0x3f800000]
                                         + SAMPLE + [RET])
                        self.assertEqual(patched[4:20], dxbc_checksum(patched))
                    else:
                        self.assertEqual(patched, data)
            with self.assertRaisesRegex(ValueError, "already have"):
                neutralize_cubemap_ao(output, Path(temporary) / "duplicate")

    def test_default_distances_change_only_all_minus_one_draws(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, output = Path(temporary) / "source", Path(temporary) / "output"
            root = root_fixture()
            raw = bytearray.fromhex(root["primary_draws"][0]["source_record"])
            struct.pack_into("<I", raw, 0, 0)
            struct.pack_into("<4e", raw, 18, -1, -1, -1, -1)
            root["primary_draws"][0]["source_record"] = raw.hex()
            authored = dict(root["primary_draws"][0])
            second = bytearray(raw)
            struct.pack_into("<H", second, 12, 1)
            struct.pack_into("<4e", second, 18, 0, 0, 80, 160)
            authored["source_record"] = second.hex()
            root["primary_draws"].append(authored)
            root["instances"].append(root["instances"][0])
            original = write_root60(root)
            relative = Path("mediaoverride") / "tracks" / "TopGearCPS"
            path = source / relative / "ribbon_00" / "TopGearCPS_00.pvs"
            path.parent.mkdir(parents=True)
            path.write_bytes(original)
            inventory = [{"path": str(path.relative_to(source)),
                          "media_path": "tracks\\TopGearCPS\\ribbon_00\\TopGearCPS_00.pvs",
                          "bytes": len(original), "sha256": digest(path)}]
            catalog = source / "media.catalog"
            catalog.write_bytes(media_catalog("TopGearCPS", inventory))
            report = {"catalog_name": catalog.name, "catalog_sha256": digest(catalog),
                      "media_name": "TopGearCPS", "override_relative_path": str(relative), "files": inventory}
            (source / "media.json").write_text(json.dumps(report))
            result = bake_default_draw_distances(source, output, [100, 200, 350, 500])
            rewritten = (output / path.relative_to(source)).read_bytes()
            at = original.index(bytes(raw)) + 18
            self.assertEqual(rewritten, original[:at] + struct.pack("<4e", 100, 200, 350, 500) +
                             original[at + 8:])
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(result["baked_default_draw_distances"],
                             {"bands": [100, 200, 350, 500], "draws": 1})
            self.assertEqual(result["catalog_sha256"], digest(output / catalog.name))
            for bands in ([0, 100, 200, 500], [100, 100, 200, 500],
                          [100, 200, 350, float("nan")], [100, 200, 350, 65504]):
                with self.assertRaises(ValueError):
                    bake_default_draw_distances(source, Path(temporary) / "invalid", bands)

    def test_draw_lod_repair_preserves_every_other_root_byte(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, output = Path(temporary) / "source", Path(temporary) / "output"
            root = root_fixture()
            raw = bytearray.fromhex(root["primary_draws"][0]["source_record"])
            struct.pack_into("<I", raw, 0, 0)
            root["primary_draws"][0]["source_record"] = raw.hex()
            original = write_root60(root)
            relative = Path("mediaoverride") / "tracks" / "TopGearCPS"
            path = source / relative / "ribbon_00" / "TopGearCPS_00.pvs"
            path.parent.mkdir(parents=True)
            path.write_bytes(original)
            inventory = [{"path": str(path.relative_to(source)),
                          "media_path": "tracks\\TopGearCPS\\ribbon_00\\TopGearCPS_00.pvs",
                          "bytes": len(original), "sha256": digest(path)}]
            catalog = source / "media.catalog"
            catalog.write_bytes(media_catalog("TopGearCPS", inventory))
            report = {"catalog_name": catalog.name, "catalog_sha256": digest(catalog),
                      "media_name": "TopGearCPS", "override_relative_path": str(relative), "files": inventory}
            (source / "media.json").write_text(json.dumps(report))
            result = repair_draw_lods(source, output)
            rewritten = (output / path.relative_to(source)).read_bytes()
            parsed = read_root60(rewritten)
            self.assertEqual(parsed["primary_draws"][0]["metadata"], "00000000")
            at = original.index(bytes(raw) + bytes.fromhex("7f7f7f7f")) + 73
            self.assertEqual(rewritten, original[:at] + bytes(4) + original[at + 4:])
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(result["repaired_draw_lods"], {"draws": 1})
            self.assertEqual(result["catalog_sha256"], digest(output / catalog.name))

    def test_both_platforms_preserve_alpha_other_shaders_and_source_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, output = Path(temporary) / "source", Path(temporary) / "output"
            root = source / "mediaoverride" / "tracks" / "TopGearCPS"
            root.mkdir(parents=True)
            original = container(SAMPLE + [RET])
            members = {}
            for family in ("grass_blend_002", "terrain_3blend_ch4overlay"):
                for platform in ("pc", "durango"):
                    members["Shaders/test/%sTrackLightScenario.%s.pso" % (family, platform)] = original
            members["Shaders/test/grass_blendTrackLightScenario.durango.pso"] = b"unrelated"
            members["Shaders/test/grass_blend_002TrackLightScenario.durango.vso"] = b"vertex"
            diffuse, secondary, blend = SAMPLE.copy(), SAMPLE.copy(), SAMPLE.copy()
            diffuse[6], secondary[6], blend[6] = 17, 18, 19
            terrain = container(SAMPLE + diffuse + secondary + blend + [RET])
            for scenario in ("TrackLightScenario", "CubemapLightScenario", "DebugLightingLightScenario"):
                for platform in ("pc", "durango"):
                    members["Shaders/test/terrain_3blend_macrodiffuse%s.%s.pso" %
                            (scenario, platform)] = terrain
            members["Shaders/test/terrain_3blend_macrodiffuseTrackLightScenario.pc.vso"] = b"terrain vertex"
            members["Shaders/test/terrain_3blend_macrodiffuseShadowDepthLightScenario.pc.pso"] = terrain
            archive_path = root / "bin.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for name, data in members.items():
                    archive.writestr(name, data)
            inventory = [{"path": str(archive_path.relative_to(source)),
                          "media_path": "tracks\\TopGearCPS\\bin.zip",
                          "bytes": archive_path.stat().st_size, "sha256": digest(archive_path)}]
            catalog = source / "media.catalog"
            catalog.write_bytes(media_catalog("TopGearCPS", inventory))
            report = {"catalog_name": catalog.name, "catalog_sha256": digest(catalog),
                      "media_name": "TopGearCPS",
                      "override_relative_path": str(root.relative_to(source)), "files": inventory}
            (source / "media.json").write_text(json.dumps(report))
            hashes = digest(catalog), digest(archive_path), digest(source / "media.json")
            result = neutralize_macro_tint(source, output)
            self.assertEqual(result["neutralized_macro_tint"], {"programs": 4, "rgb_samples": 4})
            self.assertEqual(result["catalog_sha256"], digest(output / catalog.name))
            self.assertEqual(hashes, (digest(catalog), digest(archive_path), digest(source / "media.json")))
            with zipfile.ZipFile(output / root.relative_to(source) / "bin.zip") as archive:
                self.assertEqual(archive.namelist(), list(members))
                for name, data in members.items():
                    rewritten = archive.read(name)
                    if data != original:
                        self.assertEqual(rewritten, data)
                    else:
                        self.assertEqual(instructions(rewritten)[:len(SAMPLE)], SAMPLE)
                        self.assertEqual(instructions(rewritten)[len(SAMPLE) + 1], 0x00100072)
            with self.assertRaisesRegex(ValueError, "already have"):
                neutralize_macro_tint(output, Path(temporary) / "duplicate")
            terrain_output = Path(temporary) / "terrain"
            result = neutralize_terrain_macro_tint(output, terrain_output)
            self.assertEqual(result["neutralized_terrain_macro_tint"], {"programs": 6, "rgb_samples": 6})
            self.assertEqual(result["neutralized_macro_tint"], {"programs": 4, "rgb_samples": 4})
            with zipfile.ZipFile(output / root.relative_to(source) / "bin.zip") as before, \
                    zipfile.ZipFile(terrain_output / root.relative_to(source) / "bin.zip") as after:
                self.assertEqual(before.namelist(), after.namelist())
                for name in before.namelist():
                    old, new = before.read(name), after.read(name)
                    if ("terrain_3blend_macrodiffuse" not in name or not name.endswith(".pso")
                            or "ShadowDepth" in name):
                        self.assertEqual(old, new)
                        continue
                    code = instructions(new)
                    self.assertEqual(code[:len(SAMPLE)], SAMPLE)
                    self.assertEqual(code[len(SAMPLE):len(SAMPLE) + 8], [
                        54 | (8 << 24), 0x00100072, SAMPLE[2], 0x4002,
                        0x3f800000, 0x3f800000, 0x3f800000, 0x3f800000])
                    self.assertEqual(code[len(SAMPLE) + 8:], diffuse + secondary + blend + [RET])
            self.assertEqual(hashes, (digest(catalog), digest(archive_path), digest(source / "media.json")))
            self.assertEqual(result["catalog_sha256"], digest(terrain_output / catalog.name))
            for row in result["files"]:
                self.assertEqual(row["sha256"], digest(terrain_output / row["path"]))
            with self.assertRaisesRegex(ValueError, "already have"):
                neutralize_terrain_macro_tint(terrain_output, Path(temporary) / "terrain-duplicate")


if __name__ == "__main__":
    unittest.main()
