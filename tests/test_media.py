import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

from build_openworld import build as build_navigation
from build_media import build, validate_media, media_catalog, verify_native_bin_zip, write_native_bin_zip
from build_root import assemble
from fh2_pvs import write_root60
from fh2_world import MiniZip, Resource, read_pvs_zone60, write_minizip, write_pvs_zone60
from spatial import write_hex
from test_bindings import fixture as collision_fixture
import test_root_builder
from test_shader_scenes import shader_bundle
from test_navigation import waypoint_fixture
from test_environment import SOURCE, NATIVE, locator_xml
from build_environment import arrival, track_settings, static_test_companions
from build_environment import locators


def fixture(path):
    collision_fixture(path)
    args = test_root_builder.RootBuilderTests().fixture(path)
    root, zones = assemble(*args, "TopGearCPS")
    geometry, component = path / "geometry", path / "root"
    geometry.mkdir()
    component.mkdir()
    lookup = write_minizip(geometry / "GeoChunk0.minizip",
                          [Resource("model", 3, 4), Resource("texture", 0, 5)],
                          lambda resource: b"fixture geometry")
    (geometry / "ChunkMap0.dat").write_bytes(lookup)
    report = {"verified": True, "chunk_sha256": hashlib.sha256((geometry / "GeoChunk0.minizip").read_bytes()).hexdigest(),
              "lookup_sha256": hashlib.sha256(lookup).hexdigest()}
    (geometry / "geometry.json").write_text(json.dumps(report))
    report = {"schema_verified": True, "all_source_placements_requested": True, "media_name": "TopGearCPS",
              "root_name": "TopGearCPS_00.pvs", "geometry_component": str(geometry),
              "physics_component": str(path / "physics"), "remaining": []}
    files = {
        "TopGearCPS_00.pvs": (write_root60(root), "root_sha256"),
        "TopGearCPSout_00.hex": (write_hex(args[-1]), "hex_sha256"),
        "PVSZLookup.dat": (struct.pack("<%dI" % len(zones), *range(len(zones))), "pvs_lookup_sha256"),
        "ChunkAssign.dat": (struct.pack("<2I", 100, len(zones)) +
                            struct.pack("<%dI" % len(zones), *([1] * len(zones))), "chunk_assignment_sha256"),
    }
    chunk_map = write_minizip(component / "GeoChunk1.minizip",
                             [Resource("zone%d" % i, i, 12) for i in range(len(zones))],
                             lambda resource: write_pvs_zone60(zones[resource.resource_id]))
    files["ChunkMap1.dat"] = (chunk_map, "zone_chunk_map_sha256")
    report["zone_chunk_sha256"] = hashlib.sha256((component / "GeoChunk1.minizip").read_bytes()).hexdigest()
    for name, (data, key) in files.items():
        (component / name).write_bytes(data)
        report[key] = hashlib.sha256(data).hexdigest()
    assets = {"shaders/x.shaderbin": shader_bundle(), "TopGearCPS.stx.bin": b"resident"}
    for platform in ("durango", "pc"):
        for stage in ("vso", "pso"):
            assets["shaders/RoadTrackLightScenario.%s.%s" % (platform, stage)] = b"compiled"
    with zipfile.ZipFile(component / "bin.zip", "w") as archive:
        for name, data in assets.items():
            archive.writestr(name, data)
    report["support_assets"] = [{"name": name, "sha256": hashlib.sha256(data).hexdigest()}
                               for name, data in assets.items()]
    report["support_archive_sha256"] = hashlib.sha256((component / "bin.zip").read_bytes()).hexdigest()
    (component / "root.json").write_text(json.dumps(report))
    return component


def environment_fixture(path, component, static_test=False):
    report = json.loads((component / "root.json").read_text())
    report["source_sha256"] = "world-source"
    (component / "root.json").write_text(json.dumps(report))
    physics = json.loads((path / "physics" / "physics.json").read_text())
    directory = path / "environment"
    directory.mkdir()
    settings, _ = track_settings(SOURCE, NATIVE)
    locator_data = locator_xml((3.0, 2.0, 3.0))
    files = {"TrackSettings.xml": settings, "ribbon_00\\Locators.xml": locator_data,
             "smoke.dds": b"DDS fixture", "staticcarcubemap.xpr": b"XPR fixture"}
    static_report = None
    if static_test:
        companions, static_report = static_test_companions(b'<CollObjs><Obj0 PhysicsType="Cone"/></CollObjs>')
        files.update(companions)
    rows = []
    for name, data in files.items():
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        rows.append({"name": name, "sha256": hashlib.sha256(data).hexdigest()})
    report = {"component": "native-settings-locators-and-arrival", "media_name": "TopGearCPS",
              "schema_verified": True, "world_source_sha256": "world-source",
              "source_collision_sha256": physics["source_sha256"],
              "lighting_fog": {"ambient_scale": 1.0, "direct_scale": 1.0,
                               "fog_far_density_scale": 1.0, "fog_far_distance_scale": 1.0,
                               "active_fog_settings": []},
              "files": rows,
              "arrival": arrival((path / "track.col").read_bytes(), path / "physics", locators(locator_data)[0]),
              "remaining": []}
    if static_report is not None:
        report["static_test"] = static_report
    (directory / "environment.json").write_text(json.dumps(report))
    return directory


class MediaTests(unittest.TestCase):
    def test_catalog_rejects_duplicate_escaping_and_invalid_size_records(self):
        row = {"media_path": "tracks\\TopGearCPS\\file.bin", "bytes": 2, "sha256": "a" * 64}
        expected = ("TLCPS-MEDIA-1\tTopGearCPS\n" + "a" * 64 +
                    "\t2\ttracks\\TopGearCPS\\file.bin\n").encode("ascii")
        self.assertEqual(media_catalog("TopGearCPS", [row]), expected)
        for rows in ([row, dict(row, media_path="tracks\\TopGearCPS\\FILE.bin")],
                     [dict(row, media_path="tracks\\TopGearCPS\\..\\Anthem")],
                     [dict(row, media_path="tracks\\TopGearCPS\\file:stream")],
                     [dict(row, bytes=0)], [dict(row, bytes=2147483648)],
                     [dict(row, sha256="x" * 64)]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                media_catalog("TopGearCPS", rows)

    def test_override_layout_preserves_logical_game_paths_without_mounting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            component = fixture(path)
            root_report_path = component / "root.json"
            root_report = json.loads(root_report_path.read_text())
            root_report["baked_shadow_visibility"] = {"visibility": 0.45}
            root_report_path.write_text(json.dumps(root_report))
            report = build(component, path / "staged", mediaoverride=True)
            self.assertEqual(report["staging_layout"], "mediaoverride")
            self.assertEqual(report["baked_shadow_visibility"]["visibility"], 0.45)
            self.assertFalse(report["file_mount_enabled"])
            self.assertFalse(report["registration_enabled"])
            catalog = (path / "staged" / "media.catalog").read_bytes()
            self.assertEqual(hashlib.sha256(catalog).hexdigest(), report["catalog_sha256"])
            self.assertFalse((path / "staged" / "media").exists())
            for row in report["files"]:
                self.assertEqual(row["path"], row["override_relative_path"])
                self.assertEqual(row["game_media_path"], "game:\\media\\" + row["media_path"])
                self.assertTrue(row["media_path"].startswith("tracks\\TopGearCPS\\"))
            self.assertTrue((path / "staged" / "mediaoverride" / "tracks" / "TopGearCPS" /
                             "ribbon_00" / "TopGearCPS_00.pvs").is_file())

    def test_static_profile_preserves_road_collision_and_disabled_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            component = fixture(path)
            environment = environment_fixture(path, component, static_test=True)
            report = build(component, path / "staged", environment=environment)
            self.assertTrue(report["static_test_companions_present"])
            self.assertEqual(report["physics_count"], 1)
            self.assertFalse(report["first_test_ready"])
            self.assertEqual(len(report["files"]), 22)
            media = path / "staged" / "media" / "tracks" / "TopGearCPS"
            (media / "PhysicsDefinitions.bin").write_bytes(bytes(4))
            with self.assertRaisesRegex(ValueError, "uint64"):
                validate_media(media, "TopGearCPS", report["arrival"])

    def test_stages_native_layout_without_enabling_a_map(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            component = fixture(path)
            report = build(component, path / "staged")
            self.assertTrue(report["native_resource_closure_verified"])
            self.assertFalse(report["first_test_ready"])
            self.assertFalse(report["playable_map"])
            self.assertEqual(report["placement_count"], 8)
            media = path / "staged" / "media" / "tracks" / "TopGearCPS"
            self.assertTrue((media / "ribbon_00" / "TopGearCPS_00.pvs").is_file())
            self.assertFalse((media / "bin.zip").exists())
            self.assertEqual(validate_media(media, "TopGearCPS")["physics_count"], 1)
            self.assertEqual(len(report["files"]), 16)
            (media / "bin" / "shaders" / "RoadTrackLightScenario.pc.vso").unlink()
            with self.assertRaisesRegex(ValueError, "Missing compiled"):
                validate_media(media, "TopGearCPS")
            with self.assertRaisesRegex(ValueError, "new staged"):
                build(component, path / "staged")

    def test_native_validation_decodes_packed_secondary_view_draw_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            report = build(fixture(path), path / "staged")
            media = path / "staged" / "media" / "tracks" / "TopGearCPS"
            archive = MiniZip(media / "GeoChunk1.minizip")
            payloads = []
            for index in range(len(archive.entries)):
                zone = read_pvs_zone60(archive.read(index))
                zone["draws"] = [draw | ((ordinal % 4) << 30)
                                 for ordinal, draw in enumerate(zone["draws"])]
                payloads.append(write_pvs_zone60(zone))
            (media / "GeoChunk1.minizip").unlink()
            lookup = write_minizip(
                media / "GeoChunk1.minizip",
                [Resource(str(index), index, 12) for index in range(len(payloads))],
                lambda resource: payloads[resource.resource_id])
            self.assertEqual(lookup, (media / "ChunkMap1.dat").read_bytes())
            self.assertEqual(validate_media(media, "TopGearCPS")["placement_count"],
                             report["placement_count"])

    def test_checksum_failure_prevents_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            component = fixture(path)
            (component / "ChunkMap1.dat").write_bytes(b"corrupt")
            with self.assertRaisesRegex(ValueError, "checksum"):
                build(component, path / "staged")
            self.assertFalse((path / "staged").exists())

    def test_stages_navigation_with_matching_physics_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            component = fixture(path)
            with zipfile.ZipFile(path / "AI.zip", "w") as archive:
                archive.writestr("Track.geo", waypoint_fixture())
            navigation = path / "navigation"
            build_navigation(path / "AI.zip", path / "track.col", navigation)
            report = build(component, path / "staged", navigation)
            self.assertTrue(report["navigation_present"])
            self.assertEqual(report["navigation_component"], navigation.name)
            self.assertEqual(report["navigation_waypoint_count"], 3)
            self.assertFalse(report["first_test_ready"])
            self.assertEqual(len(report["files"]), 18)
            source = json.loads((navigation / "openworld.json").read_text())
            source["source_collision_sha256"] = "wrong source"
            (navigation / "openworld.json").write_text(json.dumps(source))
            with self.assertRaisesRegex(ValueError, "provenance"):
                build(component, path / "bad-stage", navigation)
            self.assertFalse((path / "bad-stage").exists())

    def test_stages_environment_without_activating_custom_travel(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            component = fixture(path)
            environment = environment_fixture(path, component)
            report = build(component, path / "staged", environment=environment)
            self.assertTrue(report["settings_and_locators_present"])
            self.assertEqual(report["environment_component"], environment.name)
            self.assertFalse(report["first_test_ready"])
            self.assertEqual(len(report["files"]), 20)
            self.assertEqual(report["arrival"]["candidate_position"], [3.0, 2.8, 3.0])
            media = path / "staged" / "media" / "tracks" / "TopGearCPS"
            reported_arrival = dict(report["arrival"], candidate_position=[3.0, 100.0, 3.0])
            with self.assertRaisesRegex(ValueError, "Arrival metadata"):
                validate_media(media, "TopGearCPS", reported_arrival)
            source = json.loads((environment / "environment.json").read_text())
            source["world_source_sha256"] = "another world"
            (environment / "environment.json").write_text(json.dumps(source))
            with self.assertRaisesRegex(ValueError, "provenance"):
                build(component, path / "invalid-stage", environment=environment)
            self.assertFalse((path / "invalid-stage").exists())

    def test_environment_member_checksum_prevents_output_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            component = fixture(path)
            environment = environment_fixture(path, component)
            (environment / "TrackSettings.xml").write_bytes(b"corrupt")
            with self.assertRaisesRegex(ValueError, "checksum"):
                build(component, path / "staged", environment=environment)
            self.assertFalse((path / "staged").exists())


if __name__ == "__main__":
    unittest.main()


class NativeBinZipTests(unittest.TestCase):
    def test_stock_layout_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "bin" / "Materials").mkdir(parents=True)
            members = {"TopGearCPS.stx.bin": b"\x00" * 4096, "Materials/_0x00000000.materialbin": b"material"}
            for name, data in members.items():
                (root / "bin" / name).write_bytes(data)
            write_native_bin_zip(root / "bin", list(members), root / "bin.zip")
            expected = [(name, hashlib.sha256(data).hexdigest()) for name, data in members.items()]
            verify_native_bin_zip(root / "bin.zip", expected)
            with zipfile.ZipFile(root / "bin.zip") as archive:
                rows = archive.infolist()
                self.assertEqual([row.compress_type for row in rows], [zipfile.ZIP_DEFLATED] * 2)
                self.assertEqual(struct.unpack("<HHI", rows[1].extra)[:2], (0x1123, 4))
            with self.assertRaises(ValueError):
                verify_native_bin_zip(root / "bin.zip", [(expected[0][0], "0" * 64), expected[1]])
            with self.assertRaises(FileExistsError):
                write_native_bin_zip(root / "bin", list(members), root / "bin.zip")
