import configparser
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest

from build_dlc import registration
from build_session import write_visit_catalog
from build_visit import build as build_visit
from build_media import write_native_bin_zip
from fh2_world import Resource, write_minizip
from test_dlc import database
import test_visit


PROJECT = Path(__file__).resolve().parents[1]
HOST = PROJECT / "bin" / "native_host.exe"
DLL = PROJECT / "bin" / "TrackLoaderCPS.dll"


@unittest.skipUnless(HOST.is_file() and DLL.is_file(), "Build native_host.exe and TrackLoaderCPS.dll first")
class CatalogRuntimeTests(unittest.TestCase):
    dll = DLL
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        native = test_visit.VisitTests()
        native.setUp()
        self.addCleanup(native.doCleanups)
        self.native = native
        self.target = database(self.root / "fh2.slt")
        self.source = database(self.root / "fm5.slt", True)
        self.addCleanup(self.target.close)
        self.addCleanup(self.source.close)

    def pack(self, root_name, directory, track_id, media_name, namespace, position):
        pack = self.root / "fh2-mods" / root_name / directory
        media = pack / "media"
        media.mkdir(parents=True)
        catalog = "TLCPS-MEDIA-1\t" + media_name + "\n"
        # Valid native archive containers, but not playable world geometry.
        for filename in ("bin.zip", "GeoChunk0.minizip", "GeoChunk1.minizip"):
            relative = "tracks\\" + media_name + "\\" + filename
            path = media / "mediaoverride" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if filename == "bin.zip":
                source = pack / "fixture.bin"
                source.write_bytes(b"fixture")
                write_native_bin_zip(pack, ["fixture.bin"], path)
            else:
                write_minizip(path, [Resource("fixture", 0, 3)], lambda resource: b"fixture")
            payload = path.read_bytes()
            catalog += "%s\t%d\t%s\n" % (hashlib.sha256(payload).hexdigest(), len(payload), relative)
        (media / "media.catalog").write_text(catalog, encoding="ascii", newline="")
        visit = pack / "visit"
        report = build_visit(self.native.stateflow, self.native.entitymodel, visit, media_name, namespace)
        write_visit_catalog(visit, report, media_name)
        sql, _, _, _ = registration(self.target, self.source, track_id, track_id, media_name, experimental=True)
        (pack / "registration.sql").write_text(sql, encoding="utf-8", newline="")
        digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        ini = configparser.ConfigParser()
        ini.optionxform = str
        ini["Track"] = {"Id": str(track_id), "Name": directory, "State": "experimental", "Reason": "Host fixture"}
        ini["LocalDLC"] = {"Format": "1", "EnvironmentId": str(track_id), "MediaName": media_name,
                          "Delivery": "mod-managed", "Registration": "registration.sql"}
        ini["Media"] = {"Directory": "media", "CatalogSha256": digest(media / "media.catalog")}
        ini["Experimental"] = {
            "Enabled": "1", "LifecycleNamespace": namespace, "VisitDirectory": "visit",
            "VisitCatalogSha256": digest(visit / "media.catalog"),
            "RegistrationSha256": digest(pack / "registration.sql"),
            "ArrivalPosition": position, "ArrivalFacing": "0,0,-1", "RestoreSavingOnReturn": "1"}
        with (pack / "track.ini").open("w", encoding="utf-8") as stream:
            ini.write(stream)
        return pack

    def run_host(self, expected, second_dll=None):
        command = [str(HOST), str(self.dll), str(self.root), "catalog", str(expected)]
        if second_dll:
            command.append(str(second_dll))
        result = subprocess.run(command,
                                cwd=PROJECT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Catalog host passed", result.stdout)
        return result.stdout

    def test_custom_and_legacy_packs_publish_independent_resources(self):
        first = self.pack("TrackPacks", "TopGear", 10000, "TopGearCPS", "trackloadercps", "1,2,3")
        self.pack("CustomTracks", "SecondMap", 10001, "SecondMap", "trackloadercps_10001", "10,20,30")
        before = (first / "track.ini").read_bytes()
        self.run_host(2)
        self.assertEqual((first / "track.ini").read_bytes(), before)

    def test_duplicate_lifecycle_is_rejected_before_overwriting(self):
        self.pack("TrackPacks", "TopGear", 10000, "TopGearCPS", "trackloadercps", "1,2,3")
        self.pack("CustomTracks", "SecondMap", 10001, "SecondMap", "trackloadercps", "10,20,30")
        self.assertIn("lifecycle namespace is already used", self.run_host(1))

    def test_duplicate_id_is_rejected_across_roots(self):
        self.pack("TrackPacks", "TopGear", 10000, "TopGearCPS", "trackloadercps", "1,2,3")
        self.pack("CustomTracks", "SecondMap", 10000, "SecondMap", "trackloadercps_10001", "10,20,30")
        self.assertIn("invalid or duplicate", self.run_host(1))

    def test_duplicate_media_or_environment_is_rejected(self):
        self.pack("TrackPacks", "TopGear", 10000, "TopGearCPS", "trackloadercps", "1,2,3")
        self.pack("CustomTracks", "SecondMap", 10001, "TopGearCPS", "trackloadercps_10001", "10,20,30")
        self.assertIn("invalid or duplicate", self.run_host(1))

    def test_legacy_namespace_and_activation_fallback_are_preserved(self):
        first = self.pack("TrackPacks", "TopGear", 10000, "TopGearCPS", "trackloadercps", "1,2,3")
        path = first / "track.ini"
        ini = configparser.ConfigParser()
        ini.read(path)
        del ini["Experimental"]["LifecycleNamespace"]
        del ini["Experimental"]["Enabled"]
        ini["Experimental"]["SessionOnly"] = "1"
        with path.open("w") as stream:
            ini.write(stream)
        before = path.read_bytes()
        self.run_host(1)
        self.assertEqual(path.read_bytes(), before)

    def test_duplicate_environment_id_is_rejected(self):
        self.pack("TrackPacks", "TopGear", 10000, "TopGearCPS", "trackloadercps", "1,2,3")
        second = self.pack("CustomTracks", "SecondMap", 10001, "SecondMap", "trackloadercps_10001", "10,20,30")
        path = second / "track.ini"
        ini = configparser.ConfigParser()
        ini.read(path)
        ini["LocalDLC"]["EnvironmentId"] = "10000"
        with path.open("w") as stream:
            ini.write(stream)
        self.assertIn("invalid or duplicate", self.run_host(1))

    def test_corrupt_visit_is_rejected_without_publishing_its_media(self):
        self.pack("TrackPacks", "TopGear", 10000, "TopGearCPS", "trackloadercps", "1,2,3")
        second = self.pack("CustomTracks", "SecondMap", 10001, "SecondMap", "trackloadercps_10001", "10,20,30")
        flow = second / "visit" / "mediaoverride" / "stateflow" / "trackloadercps_10001.xml"
        flow.write_bytes(b"corrupt")
        self.assertIn("media checksum/size mismatch", self.run_host(1))

    def test_mixed_variants_do_not_publish_twice(self):
        second = PROJECT / "bin" / ("TrackLoader.dll" if self.dll == DLL else "TrackLoaderCPS.dll")
        self.assertTrue(second.is_file(), "Build both DLL variants first")
        self.pack("TrackPacks", "TopGear", 10000, "TopGearCPS", "trackloadercps", "1,2,3")
        self.assertIn("Mixed variants refused", self.run_host(1, second))


@unittest.skipUnless((PROJECT / "bin" / "TrackLoader.dll").is_file(), "Build TrackLoader.dll first")
class StandaloneCatalogRuntimeTests(CatalogRuntimeTests):
    dll = PROJECT / "bin" / "TrackLoader.dll"
