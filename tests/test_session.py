import configparser
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from build_session import build, verify_files, VISIT_PATHS
from build_media import build as stage_media
from build_openworld import build as stage_navigation
from build_visit import build as stage_visit
from build_visit import lifecycle_paths, namespace_resource, xml_bytes
import xml.etree.ElementTree as ET
from test_media import fixture, environment_fixture
from test_navigation import waypoint_fixture
import test_visit
from test_dlc import database


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        component = fixture(self.root)
        environment = environment_fixture(self.root, component, static_test=True)
        with zipfile.ZipFile(self.root / "AI.zip", "w") as archive:
            archive.writestr("Track.geo", waypoint_fixture())
        stage_navigation(self.root / "AI.zip", self.root / "track.col", self.root / "navigation")
        self.media = self.root / "media"
        stage_media(component, self.media, self.root / "navigation", environment, mediaoverride=True)
        native = test_visit.VisitTests()
        native.setUp()
        self.addCleanup(native.doCleanups)
        self.visit = self.root / "visit"
        stage_visit(native.stateflow, native.entitymodel, self.visit)
        for name, source in (("fh2.slt", False), ("fm5.slt", True)):
            db = database(self.root / name, source)
            db.close()
        self.output = self.root / "session"

    def build(self):
        return build(self.media, self.visit, self.root / "fh2.slt", self.root / "fm5.slt", self.output)

    def test_session_package_is_explicit_and_preserves_sources(self):
        before = [(self.root / name).read_bytes() for name in ("fh2.slt", "fm5.slt")]
        report = self.build()
        self.assertTrue(report["saving_disabled_until_restart"])
        self.assertFalse(report["one_visit_per_process"])
        self.assertTrue(report["persistent_activation"])
        self.assertFalse(report["in_game_validated"])
        self.assertEqual(before, [(self.root / name).read_bytes() for name in ("fh2.slt", "fm5.slt")])
        ini = configparser.ConfigParser()
        ini.read(self.output / "track.ini")
        self.assertEqual(ini["Track"]["State"], "experimental")
        self.assertEqual(ini["Experimental"]["Enabled"], "1")
        self.assertEqual(ini["Experimental"]["RestoreSavingOnReturn"], "0")
        for section, key, relative in (
                ("Media", "CatalogSha256", "media\\media.catalog"),
                ("Experimental", "VisitCatalogSha256", "visit\\media.catalog"),
                ("Experimental", "RegistrationSha256", "registration.sql")):
            self.assertEqual(ini[section][key], hashlib.sha256((self.output / relative).read_bytes()).hexdigest())
        catalog = (self.output / "visit" / "media.catalog").read_text().splitlines()
        self.assertEqual(catalog[0], "TLCPS-VISIT-1\tTopGearCPS")
        self.assertEqual({row.split("\t")[2] for row in catalog[1:]}, VISIT_PATHS)
        sql = (self.output / "registration.sql").read_text()
        self.assertNotIn("UPDATE", sql)
        self.assertNotIn("ContentOffers", sql)
        with self.assertRaisesRegex(ValueError, "new experimental"):
            self.build()

    def test_corrupt_or_old_resume_refuses_before_creating_output(self):
        manifest = self.visit / "visit.json"
        data = json.loads(manifest.read_text())
        data["return_startup"] = "free_roam_start.enter_offline"
        manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "dedicated"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_corrupt_media_refuses_before_creating_output(self):
        data = json.loads((self.media / "media.json").read_text())
        path = self.media / "mediaoverride" / data["files"][0]["media_path"]
        path.write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "Component file changed"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_component_paths_cannot_escape(self):
        with self.assertRaisesRegex(ValueError, "escaping"):
            verify_files(self.root, {"files": [{"media_path": "..\\stock"}]})

    def test_live_arrival_and_save_restoration_are_explicit(self):
        position = [283.592712, 0.381009579, 195.880096]
        facing = [-0.998773813, -0.0034530554, 0.0496612005]
        report = build(self.media, self.visit, self.root / "fh2.slt", self.root / "fm5.slt",
                       self.output, position, facing, True)
        self.assertEqual(report["arrival_position"], position)
        self.assertEqual(report["arrival_facing"], facing)
        self.assertFalse(report["saving_disabled_until_restart"])
        self.assertFalse(report["career_changes_audited"])
        ini = configparser.ConfigParser()
        ini.read(self.output / "track.ini")
        self.assertEqual(ini["Experimental"]["RestoreSavingOnReturn"], "1")
        self.assertEqual([float(v) for v in ini["Experimental"]["ArrivalPosition"].split(",")], position)

    def test_invalid_arrival_override_refuses_before_output(self):
        for position, facing in (([1, 2, 3], None), ([1, 2, 3], [0, 0, 0]),
                                 ([1, float("nan"), 3], [0, 0, 1])):
            with self.assertRaisesRegex(ValueError, "Arrival"):
                build(self.media, self.visit, self.root / "fh2.slt", self.root / "fm5.slt",
                      self.output, position, facing)
            self.assertFalse(self.output.exists())

    def test_distinct_track_id_namespace_pose_and_display_name(self):
        report_path = self.visit / "visit.json"
        report = json.loads(report_path.read_text())
        namespace = "trackloadercps_10001"
        for row in report["files"]:
            old = self.visit / "mediaoverride" / row["media_path"]
            data = xml_bytes(namespace_resource(ET.fromstring(old.read_bytes()), namespace))
            row["media_path"] = row["media_path"].replace("trackloadercps", namespace)
            new = self.visit / "mediaoverride" / row["media_path"]
            new.parent.mkdir(parents=True, exist_ok=True)
            new.write_bytes(data)
            old.unlink()
            row.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        report.update(lifecycle_namespace=namespace, return_startup=namespace + ".resume_mainland")
        report_path.write_text(json.dumps(report))
        position, facing = [10, 20, 30], [0, 0, -1]
        built = build(self.media, self.visit, self.root / "fh2.slt", self.root / "fm5.slt",
                      self.output, position, facing, True, 10001, 20001, "Second test track")
        ini = configparser.ConfigParser()
        ini.read(self.output / "track.ini")
        self.assertEqual(ini["Track"]["Id"], "10001")
        self.assertEqual(ini["Track"]["Name"], "Second test track")
        self.assertEqual(ini["LocalDLC"]["EnvironmentId"], "20001")
        self.assertEqual(ini["Experimental"]["LifecycleNamespace"], namespace)
        self.assertEqual(built["arrival_position"], position)
        self.assertEqual(built["arrival_facing"], facing)
        catalog = (self.output / "visit" / "media.catalog").read_text().splitlines()
        self.assertEqual({row.split("\t")[2] for row in catalog[1:]}, lifecycle_paths(namespace))
        sql = (self.output / "registration.sql").read_text()
        self.assertIn("Second test track", sql)
        self.assertIn("10001", sql)
        self.assertIn("20001", sql)

    def test_visit_must_use_the_world_navigation_namespace(self):
        path = self.visit / "visit.json"
        report = json.loads(path.read_text())
        report["media_name"] = "OtherMap"
        path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "navigation namespace"):
            self.build()
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
