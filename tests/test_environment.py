import copy
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from build_environment import (apply_lighting_fog, arrival, locators, track_settings,
                               static_test_companions, validate_static_companions)
from collision import build_physics
from test_collision import collision_fixture


SOURCE = b'<TrackSettings Version="81"><LightAmbient r="0.2" g="0.3"/><Fog Version="4"><Enabled value="1"/></Fog><FileChunkSize value="4194304"/></TrackSettings>'
NATIVE = b'<TrackSettings Version="83"><LightAmbient r="0.4" g="0.5"/><Fog Version="4"><Enabled value="0"/></Fog><FileChunkSize value="1048576" limit="4194304"/><screenAreaDistances value="1"/></TrackSettings>'
LIGHTING_FOG = (b'<TrackSettings Version="83"><LightAmbient r="0.2" g="0.4" b="0.6"/>'
                b'<SHLighting SHDirRed="0.8" SHDirGreen="0.6" SHDirBlue="0.4"/>'
                b'<Fog><Settings0><Enabled value="1"/><Far value="660"/>'
                b'<FarDensity value="0.005"/></Settings0><Settings1><Enabled value="0"/>'
                b'<Far value="500"/><FarDensity value="0.01"/></Settings1></Fog></TrackSettings>')


def locator_xml(position=(2.0, 2.0, 2.0)):
    root = ET.Element("TrackLocators", {"Version": "2"})
    locator = ET.SubElement(root, "Locator", {"Version": "3"})
    ET.SubElement(locator, "Name", {"value": "321Root"})
    ET.SubElement(locator, "GUID", {"value": "db185efe-020c-4382-b625-e7dda7a7535d"})
    matrix = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, *position, 1]
    ET.SubElement(locator, "SceneTransform", {"value._%d%d" % (row, column): str(matrix[(row - 1) * 4 + column - 1])
                                            for row in range(1, 5) for column in range(1, 5)})
    ET.SubElement(locator, "AttachTo")
    return ET.tostring(root)


class EnvironmentTests(unittest.TestCase):
    def test_static_profile_uses_uint64_empty_native_catalog(self):
        source = b'<CollObjs><Obj0 PhysicsType="Cone"/><Obj1 PhysicsType="Barrel1"/></CollObjs>'
        files, report = static_test_companions(source)
        self.assertEqual(files["PhysicsDefinitions.bin"], b"\0" * 8)
        self.assertEqual(report["omitted_source_object_count"], 2)
        self.assertEqual(report["omitted_source_physics_types"], ["Barrel1", "Cone"])
        self.assertTrue(report["road_collision_preserved"])
        validate_static_companions(files["PhysicsDefinitions.bin"], files["ribbon_00\\CollObjs.xml"])
        for data in (bytes(4), bytes(12), b"\x01" + bytes(7)):
            with self.assertRaisesRegex(ValueError, "uint64"):
                validate_static_companions(data, files["ribbon_00\\CollObjs.xml"])
        with self.assertRaisesRegex(ValueError, "dynamic"):
            validate_static_companions(bytes(8), source)

    def test_settings_adapt_schema_without_replacing_source_lighting(self):
        data, report = track_settings(SOURCE, NATIVE)
        root = ET.fromstring(data)
        self.assertEqual(root.get("Version"), "83")
        self.assertEqual(root.find("LightAmbient").get("r"), "0.2")
        self.assertEqual(root.find("Fog/Enabled").get("value"), "1")
        self.assertEqual(root.find("FileChunkSize").attrib, {"value": "4194304", "limit": "4194304"})
        self.assertEqual(report["native_default_fields"], ["screenAreaDistances"])
        self.assertEqual(report["adapted_fields"], ["FileChunkSize"])
        self.assertEqual(report["preserved_source_fields"], ["LightAmbient", "Fog"])

    def test_lighting_fog_profile_scales_only_track_ambient_directional_and_enabled_fog(self):
        unchanged, default_report = apply_lighting_fog(LIGHTING_FOG)
        self.assertEqual(unchanged, LIGHTING_FOG)
        self.assertEqual(default_report["active_fog_settings"], ["Settings0"])

        data, report = apply_lighting_fog(
            LIGHTING_FOG, ambient_scale=0.75, direct_scale=0.75,
            fog_density_scale=2.0, fog_distance_scale=0.85)
        root = ET.fromstring(data)
        self.assertEqual(root.find("LightAmbient").attrib,
                         {"r": "0.150000", "g": "0.300000", "b": "0.450000"})
        self.assertEqual(root.find("SHLighting").attrib,
                         {"SHDirRed": "0.600000", "SHDirGreen": "0.450000", "SHDirBlue": "0.300000"})
        self.assertEqual(root.find("Fog/Settings0/Far").get("value"), "561.000000")
        self.assertEqual(root.find("Fog/Settings0/FarDensity").get("value"), "0.010000")
        self.assertEqual(root.find("Fog/Settings1/Far").get("value"), "500")
        self.assertEqual(root.find("Fog/Settings1/FarDensity").get("value"), "0.01")
        self.assertEqual(report["active_fog_settings"], ["Settings0"])

        source_lighting, _ = apply_lighting_fog(
            LIGHTING_FOG, ambient_scale=1.0, direct_scale=1.0,
            fog_density_scale=2.0, fog_distance_scale=0.85)
        source_root = ET.fromstring(LIGHTING_FOG)
        tuned_root = ET.fromstring(source_lighting)
        self.assertEqual(tuned_root.find("LightAmbient").attrib, source_root.find("LightAmbient").attrib)
        self.assertEqual(tuned_root.find("SHLighting").attrib, source_root.find("SHLighting").attrib)

    def test_lighting_fog_profile_rejects_invalid_scales_and_fog_overflow(self):
        for kwargs in ({"ambient_scale": -0.1}, {"direct_scale": 4.1},
                       {"fog_density_scale": float("inf")}):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, "scale"):
                apply_lighting_fog(LIGHTING_FOG, **kwargs)
        dense = LIGHTING_FOG.replace(b'value="0.005"', b'value="0.5"')
        with self.assertRaisesRegex(ValueError, "fog ranges"):
            apply_lighting_fog(dense, fog_density_scale=4)

    def test_unreviewed_fields_and_schema_shifts_fail(self):
        for source, native in (
            (SOURCE.replace(b'4194304', b'8388608'), NATIVE),
            (SOURCE.replace(b'<Fog Version="4">', b'<Fog Version="3">'), NATIVE),
            (SOURCE.replace(b'g="0.3"', b'path="game:\\\\secret"'), NATIVE),
            (SOURCE.replace(b'</TrackSettings>', b'<Unknown value="1"/></TrackSettings>'), NATIVE),
            (SOURCE, NATIVE.replace(b'</TrackSettings>', b'<Unknown value="1"/></TrackSettings>')),
            (SOURCE, NATIVE.replace(b'Version="83"', b'Version="84"')),
        ):
            with self.subTest(source=source), self.assertRaises(ValueError):
                track_settings(source, native)

    def test_native_repeated_bloom_fields_preserve_both_source_values(self):
        source = SOURCE.replace(b'</TrackSettings>', b'<bloomPersist value="0"/><bloomPersist value="0.25"/></TrackSettings>')
        native = NATIVE.replace(b'</TrackSettings>', b'<bloomPersist value="0"/><bloomPersist value="0"/></TrackSettings>')
        data, report = track_settings(source, native)
        self.assertEqual([row.get("value") for row in ET.fromstring(data).findall("bloomPersist")], ["0", "0.25"])
        self.assertEqual(report["preserved_source_fields"].count("bloomPersist"), 2)
        with self.assertRaisesRegex(ValueError, "excess repeated"):
            track_settings(source, native.replace(b'<bloomPersist value="0"/>', b'', 1))

    def test_locator_affine_schema_and_unique_ids(self):
        data = locator_xml()
        rows = locators(data)
        self.assertEqual(rows[0]["position"], [2, 2, 2])
        root = ET.fromstring(data)
        root.append(copy.deepcopy(root[0]))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            locators(ET.tostring(root))
        invalid = data.replace(b'value._44="1"', b'value._44="0"')
        with self.assertRaisesRegex(ValueError, "affine"):
            locators(invalid)

    def test_arrival_requires_persisted_ground_at_entire_footprint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            data = collision_fixture()
            (path / "track.col").write_bytes(data)
            build_physics(path / "track.col", path / "physics")
            locator = locators(locator_xml((3.0, 2.0, 3.0)))[0]
            result = arrival(data, path / "physics", locator)
            self.assertTrue(result["source_persisted_ground_equal"])
            self.assertEqual(len(result["ground_samples"]), 5)
            self.assertEqual(result["candidate_position"], [3.0, 2.8, 3.0])
            self.assertFalse(result["engine_arrival_validated"])
            locator["position"] = [1000, 2, 1000]
            with self.assertRaisesRegex(ValueError, "footprint"):
                arrival(data, path / "physics", locator)

    def test_arrival_footprint_uses_locator_orientation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            data = collision_fixture()
            (path / "track.col").write_bytes(data)
            build_physics(path / "track.col", path / "physics")
            locator = locators(locator_xml((3.0, 2.0, 3.0)))[0]
            locator["transform"][:3] = [0.0, 0.0, -1.0]
            locator["transform"][8:11] = [1.0, 0.0, 0.0]
            result = arrival(data, path / "physics", locator)
            self.assertAlmostEqual(result["ground_samples"][1]["xz"][0], 0.6)
            self.assertAlmostEqual(result["ground_samples"][1]["xz"][1], 4.2)
            self.assertTrue(result["source_persisted_ground_equal"])


if __name__ == "__main__":
    unittest.main()
