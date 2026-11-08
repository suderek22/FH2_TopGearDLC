import configparser
from pathlib import Path
import sqlite3
import tempfile
import unittest

from build_dlc import build, registration, verify_registration


def database(path, source=False):
    connection = sqlite3.connect(path)
    connection.execute("""CREATE TABLE Tracks (
        id INTEGER PRIMARY KEY, EnvironmentId INTEGER, MediaName TEXT, MediaTrack INTEGER,
        RibbonIndex INTEGER, Route INTEGER, DisplayName TEXT, ShortDisplayName TEXT,
        ShortDisplayNameAllCaps TEXT, Description TEXT, Length INTEGER, IsRealWorld INTEGER,
        OnTrackFrictionScale REAL, ContentId INTEGER, Active INTEGER, Renderable INTEGER,
        IsInstalled INTEGER, IsPurchased INTEGER)""")
    connection.execute("""CREATE TABLE Environments (
        Id INTEGER PRIMARY KEY, MediaName TEXT, DisplayName TEXT, ShortDisplayName TEXT,
        Location TEXT, LocationAllCaps TEXT, Description TEXT, CountryId INTEGER,
        ShowOnMap INTEGER)""")
    connection.execute("INSERT INTO Tracks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (220 if source else 542, 44 if source else 70,
                        "TopGear" if source else "Anthem", 220 if source else 542,
                        0, 0, "original", "original", "ORIGINAL", "original",
                        2820 if source else 5954, 1, 0.99 if source else 1.0, 0, 1, 1, 1, 1))
    connection.execute("INSERT INTO Environments VALUES (?,?,?,?,?,?,?,?,?)",
                       (44 if source else 70, "TopGear" if source else "Anthem",
                        "original", "original", "original", "ORIGINAL", "original", 9, 1))
    connection.commit()
    return connection


class LocalDLCTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.target = database(self.path / "fh2.slt")
        self.source = database(self.path / "fm5.slt", True)
        self.addCleanup(self.target.close)
        self.addCleanup(self.source.close)

    def test_additive_disabled_registration_and_unique_media_identity(self):
        sql, track, environment, source = registration(self.target, self.source)
        self.assertTrue(verify_registration(self.target, sql, track, environment))
        self.assertEqual(track["MediaTrack"], track["id"])
        self.assertEqual(track["EnvironmentId"], environment["Id"])
        self.assertEqual(track["MediaName"], environment["MediaName"])
        self.assertEqual(track["Length"], 2820)
        self.assertEqual(source["id"], 220)
        self.assertEqual([track[key] for key in
                          ("ContentId", "Active", "Renderable", "IsInstalled", "IsPurchased")], [0] * 5)
        self.assertNotIn("ContentOffers", sql)
        self.assertNotIn("UPDATE", sql)
        self.assertEqual(self.target.execute("SELECT COUNT(*) FROM Tracks").fetchone()[0], 1)

    def test_id_and_case_insensitive_namespace_collisions_fail(self):
        for kwargs in ({"track_id": 549}, {"environment_id": -1}, {"track_id": 2147483648},
                       {"track_id": True}, {"media_name": "anthem"}, {"media_name": "..\\Anthem"},
                       {"media_name": "Bad'Name"}, {"media_name": ""}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                registration(self.target, self.source, **kwargs)
    def test_explicit_session_registration_is_additive_and_not_an_offer(self):
        sql, track, environment, _ = registration(self.target, self.source, experimental=True)
        self.assertTrue(verify_registration(self.target, sql, track, environment))
        self.assertEqual([track[key] for key in ("Active", "Renderable", "IsInstalled", "IsPurchased")], [1] * 4)
        self.assertEqual(track["ContentId"], 0)
        self.assertEqual(environment["ShowOnMap"], 0)
        self.assertNotIn("ContentOffers", sql)
        with self.assertRaisesRegex(ValueError, "Boolean"):
            registration(self.target, self.source, experimental=1)

    def test_runtime_conflict_rolls_back_without_touching_stock(self):
        sql, _, _, _ = registration(self.target, self.source)
        self.target.execute("UPDATE Tracks SET MediaName='topgearcps'")
        self.target.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.target.executescript("BEGIN;\n" + sql + "\nCOMMIT;")
        self.target.rollback()
        self.assertEqual(self.target.execute("SELECT COUNT(*) FROM Environments").fetchone()[0], 1)
        self.assertEqual(self.target.execute("SELECT COUNT(*) FROM Tracks").fetchone()[0], 1)

    def test_environment_conflict_and_missing_templates_fail_before_build(self):
        self.target.execute("UPDATE Environments SET Id=10000")
        self.target.commit()
        with self.assertRaisesRegex(ValueError, "environment"):
            registration(self.target, self.source)
        self.target.execute("UPDATE Environments SET Id=70")
        self.target.commit()
        self.source.execute("DELETE FROM Tracks")
        self.source.commit()
        with self.assertRaisesRegex(ValueError, "Top Gear"):
            registration(self.target, self.source)

    def test_build_is_readonly_blocked_and_refuses_existing_output(self):
        before = (self.path / "fh2.slt").read_bytes()
        source_before = (self.path / "fm5.slt").read_bytes()
        output = self.path / "dlc"
        report = build(self.path / "fh2.slt", self.path / "fm5.slt", output)
        self.assertFalse(report["playable_map"])
        self.assertTrue(report["stock_rows_preserved"])
        self.assertEqual(report["game_media_path"], "game:\\media\\tracks\\TopGearCPS")
        manifest = configparser.ConfigParser()
        manifest.read(output / "track.ini")
        self.assertEqual(manifest["Track"]["State"], "blocked")
        self.assertEqual(manifest["LocalDLC"]["Delivery"], "mod-managed")
        self.assertEqual(before, (self.path / "fh2.slt").read_bytes())
        self.assertEqual(source_before, (self.path / "fm5.slt").read_bytes())
        with self.assertRaises(ValueError):
            build(self.path / "fh2.slt", self.path / "fm5.slt", output)


if __name__ == "__main__":
    unittest.main()
