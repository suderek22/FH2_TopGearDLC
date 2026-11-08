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
        self.assertTrue(report["one_visit_per_process"])
        self.assertFalse(report["in_game_validated"])
        self.assertEqual(before, [(self.root / name).read_bytes() for name in ("fh2.slt", "fm5.slt")])
        ini = configparser.ConfigParser()
        ini.read(self.output / "track.ini")
        self.assertEqual(ini["Track"]["State"], "experimental")
        self.assertEqual(ini["Experimental"]["SessionOnly"], "1")
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


if __name__ == "__main__":
    unittest.main()
