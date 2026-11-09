import hashlib
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

from build_visit import (ENTER_FILE, RETURN_FILE, ROUTE_FILE, MAINLAND_FILE, RESUME_CONTRACTS, build, flow,
                         lifecycle_paths, machine_group, mainland_group, validate_entitymodel,
                         validate_lifecycle_namespace, validate_native, xml_bytes)


class VisitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stateflow = self.root / "stateflow.zip"
        self.entitymodel = self.root / "EntityModel.zip"
        self.native = {}
        for call in flow("TopGearCPS").iter("node"):
            if "." not in call.get("state"):
                continue
            group, name = call.get("state").split(".")
            state = self.native.setdefault(group, {}).setdefault(name, ET.Element("state", {"id": name}))
            for key in set(call.attrib) - {"id", "state"}:
                if state.find("parameter[@id='%s']" % key) is None:
                    value = call.get(key)
                    ET.SubElement(state, "parameter", {
                        "id": key, "type": "bool" if value in ("true", "false") else "string"})
        ET.SubElement(self.native["track_swap"]["swap_track"], "behavior",
                      {"id": "SwapTrack", "wait": "{wait}"})
        for name, behavior in RESUME_CONTRACTS.items():
            ET.SubElement(self.native["global"][name], "behavior", behavior)
        for behavior in ("AIDriversReset", "StartRace"):
            ET.SubElement(self.native["global"]["start_race"], "behavior", {"id": behavior})
        self.write_native()
        self.write_entities()

    def write_native(self):
        with zipfile.ZipFile(self.stateflow, "w") as archive:
            for group, states in self.native.items():
                root = ET.Element("stategroup", {"id": group})
                root.extend(states.values())
                archive.writestr(group + ".xml", xml_bytes(root))

    def write_entities(self, target="free_roam_start.enter_offline", inherited="entities_offline_freeroam.xml",
                       base_startup=False):
        route = ET.Element("entity_group", {"id": "track_route", "source": inherited})
        startup = ET.SubElement(route, "entity", {
            "id": "free_roam_start", "template": "free_roam_startup_template"})
        ET.SubElement(startup, "property", {
            "component": "free_roam_start_flow", "id": "TargetFlow", "value": target})
        templates = ET.Element("entity_templates")
        template = ET.SubElement(templates, "entity_template", {"name": "pause_flow"})
        component = ET.SubElement(template, "component", {"type": "GameStateMachineComponent"})
        ET.SubElement(component, "property", {"id": "Autostart", "value": "true"})
        locations = ET.Element("entity_templates")
        destination = ET.SubElement(locations, "entity_template", {"name": "activation_location_track_swap"})
        swap = ET.SubElement(destination, "component", {"type": "TrackSwapComponent"})
        ET.SubElement(swap, "property", {"id": "TrackId", "value": "Mainland"})
        ET.SubElement(swap, "property", {"id": "WorldPosition", "value": "0.0,0.0,0.0"})
        with zipfile.ZipFile(self.entitymodel, "w") as archive:
            base = ET.Element("entity_group", {"id": "track_route"})
            if base_startup:
                ET.SubElement(base, "component", {"type": "FreeRoamSetupComponent"})
            archive.writestr("entities_offline_freeroam.xml", xml_bytes(base))
            archive.writestr("entities_route_offline_freeroam.xml", xml_bytes(route))
            archive.writestr("Templates/templates_global.xml", xml_bytes(templates))
            archive.writestr("Templates/templates_locations.xml", xml_bytes(locations))

    def test_persisted_resource_hashes_and_disabled_gates(self):
        output = self.root / "visit"
        before = self.stateflow.read_bytes(), self.entitymodel.read_bytes()
        report = build(self.stateflow, self.entitymodel, output)
        self.assertEqual(len(report["files"]), 5)
        for entry in report["files"]:
            data = (output / "mediaoverride" / Path(entry["media_path"])).read_bytes()
            self.assertEqual(len(data), entry["bytes"])
            self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"])
        self.assertEqual(before, (self.stateflow.read_bytes(), self.entitymodel.read_bytes()))
        self.assertFalse(report["first_test_ready"])
        self.assertFalse(report["file_mount_enabled"])
        self.assertFalse(report["engine_validated"])
        self.assertTrue(report["career_swap_used"])
        self.assertFalse(report["stock_mainland_startup_used"])
        self.assertFalse(report["return_ai_enabled"])
        self.assertFalse(report["mulligan_recording_enabled"])
        self.assertEqual(report["return_startup"], "trackloadercps.resume_mainland")
        route = ET.parse(output / "mediaoverride" / "EntityModel" / (ROUTE_FILE + ".xml")).getroot()
        self.assertEqual(route.attrib, {"id": "track_route"})
        self.assertEqual(len(route), 1)
        self.assertNotIn("pause.xml", [entry["media_path"] for entry in report["files"]])
        with self.assertRaisesRegex(ValueError, "new native visit"):
            build(self.stateflow, self.entitymodel, output)

    def test_two_maps_have_disjoint_resources_and_navigation(self):
        first = build(self.stateflow, self.entitymodel, self.root / "first")
        namespace = "trackloadercps_10001"
        media_name = "trackloadercps_second"
        second = build(self.stateflow, self.entitymodel, self.root / "second", media_name, namespace)
        first_paths = {row["media_path"] for row in first["files"]}
        second_paths = {row["media_path"] for row in second["files"]}
        self.assertFalse(first_paths & second_paths)
        self.assertEqual(second_paths, lifecycle_paths(namespace))
        self.assertEqual(second["entry_flow"], namespace + ".enter")
        self.assertEqual(second["return_startup"], namespace + ".resume_mainland")
        root = ET.parse(self.root / "second" / "mediaoverride" / "stateflow" / (namespace + ".xml")).getroot()
        self.assertEqual(root.get("id"), namespace)
        self.assertEqual(root.find("state[@id='enter']/node[@state='track_swap.load_navigation_world']")
                         .get("track_name"), media_name)
        self.assertEqual(root.find("state[@id='return_mainland']/node[@state='track_swap.load_navigation_world']")
                         .get("track_name"), "Anthem")
        for row in second["files"]:
            data = (self.root / "second" / "mediaoverride" / row["media_path"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), row["sha256"])
            document = ET.fromstring(data)
            for element in document.iter():
                value = element.get("value", "")
                if element.get("id") == "TargetFlow":
                    self.assertTrue(value.startswith(namespace + "."))
                entity_file = element.get("entity_group_file")
                if entity_file:
                    self.assertIn("EntityModel\\" + entity_file + ".xml", second_paths)

    def test_namespace_cannot_publish_stock_flows_or_escape_paths(self):
        for namespace in ("global", "pause", "track_swap", "..\\stock", "TrackLoaderCPS",
                          "trackloadercps_", "trackloadercps_" + "x" * 20, "", None):
            with self.subTest(namespace=namespace), self.assertRaisesRegex(ValueError, "lifecycle namespace"):
                validate_lifecycle_namespace(namespace)
        self.assertEqual(validate_lifecycle_namespace("trackloadercps_10001"), "trackloadercps_10001")

    def test_native_call_order_and_return_startup(self):
        root = flow("TopGearCPS")
        enter = root.find("state[@id='enter']")
        calls = [call.get("state") for call in enter.findall("node")]
        ordered = ["track_swap.unload_navigation_world",
                   "track_swap.swap_track", "track_swap.load_navigation_world",
                   "wait_locators", "global.load_entity_group",
                   "global.restore_car_position", "global.enable_track_streaming",
                   "global.wait_for_streaming_around_local_human_car", "global.start_race",
                   "global.start_driving",
                   "global.enable_car_control", "global.release_parking_brake_human"]
        self.assertEqual([calls.index(name) for name in ordered],
                         sorted(calls.index(name) for name in ordered))
        self.assertNotIn("global.unload_player_car", calls)
        self.assertNotIn("global.load_player_car", calls)
        self.assertEqual(calls.count("global.restore_car_position"), 1)
        self.assertNotIn("global.store_car_position", calls)
        self.assertNotIn("global.micro_save", calls)
        self.assertFalse(any("career" in call or "correct_offline" in call for call in calls))
        returned = root.find("state[@id='return_mainland']")
        return_calls = [call.get("state") for call in returned.findall("node")]
        self.assertNotIn("global.load_player_car", return_calls)
        self.assertNotIn("global.unload_player_car", return_calls)
        self.assertNotIn("global.restore_car_position", return_calls)
        group = returned.findall("node")[-1]
        self.assertEqual(group.get("entity_group_file"), MAINLAND_FILE)
        self.assertEqual(group.get("wait_for_state_machine_exit"), "false")
        self.assertEqual(group.get("allow_replace_if_group_exists"), "true")
        for state in [enter, returned]:
            self.assertIsNotNone(state.find("behavior[@id='GameControlWrapper']"))
            self.assertIsNotNone(state.find("behavior[@id='BlockPauseEntryWrapper']"))

    def test_stock_mainland_preparation_preserves_entry_and_owned_resume(self):
        tested = flow("TopGearCPS")
        for name in ("enter", "resume_mainland"):
            self.assertFalse(any(n.get("state").startswith("career")
                                 for n in tested.find("state[@id='%s']" % name).iter("node")))
        returned = tested.find("state[@id='return_mainland']")
        calls = [n.get("state") for n in returned.findall("node")]
        ordered = ["track_swap.unload_navigation_world", "global.unlock_destination",
                   "career_swap.root_mainland", "track_swap.swap_track",
                   "track_swap.load_navigation_world", "wait_locators",
                   "career_swap.finish_swap", "global.load_entity_group"]
        self.assertEqual([calls.index(n) for n in ordered], sorted(calls.index(n) for n in ordered))
        self.assertEqual(calls.count("track_swap.swap_track"), 1)
        used = validate_native(tested, self.stateflow)
        self.assertIn("career_swap.root_mainland", used)
        report = build(self.stateflow, self.entitymodel, self.root / "stock-return")
        self.assertTrue(report["career_swap_used"])
        self.assertTrue(report["stock_mainland_preparation"])
        self.assertFalse(report["stock_mainland_startup_used"])
        route = ET.parse(self.root / "stock-return" / "mediaoverride" / "EntityModel" /
                         (ROUTE_FILE + ".xml")).getroot()
        self.assertEqual(len(route), 1)
        component = route.find("entity/component")
        self.assertEqual(component.attrib, {"type": "TrackSwapComponent", "id": "track_swap"})
        self.assertEqual([p.attrib for p in component.findall("property")], [
            {"id": "TrackId", "value": "Mainland"},
            {"id": "WorldPosition", "value": "425.574,110.821,-1478.082"}])

    def test_unsupported_native_destination_contract_refuses_build(self):
        with zipfile.ZipFile(self.entitymodel) as source:
            rows = {name: source.read(name) for name in source.namelist()}
        rows["Templates/templates_locations.xml"] = rows["Templates/templates_locations.xml"].replace(
            b'value="Mainland"', b'value="Island"')
        with zipfile.ZipFile(self.entitymodel, "w") as target:
            for name, data in rows.items():
                target.writestr(name, data)
        with self.assertRaisesRegex(ValueError, "TrackSwapComponent"):
            build(self.stateflow, self.entitymodel, self.root / "unsupported")
        self.assertFalse((self.root / "unsupported").exists())

    def test_dedicated_mainland_resume_without_career_or_stock_startup(self):
        root = flow("TopGearCPS")
        resume = root.find("state[@id='resume_mainland']")
        calls = [node.get("state") for node in resume.findall("node")]
        self.assertNotIn("global.load_player_car", calls)
        self.assertLess(calls.index("global.restore_car_position"), calls.index("global.enable_track_streaming"))
        self.assertLess(calls.index("global.wait_for_streaming_around_local_human_car"),
                        calls.index("global.start_driving"))
        self.assertEqual(calls.index("global.start_race") + 1, calls.index("global.start_driving"))
        self.assertEqual(calls.count("global.restore_car_position"), 1)
        for node in root.iter("node"):
            target = node.get("state")
            self.assertFalse(target.startswith(("career.", "free_roam_start.", "free_roam_start_ie.")))
            self.assertNotIn(target, ("global.micro_save", "global.save_profile",
                                     "global.snap_player_car_to_road", "global.store_car_position"))
        group = mainland_group()
        self.assertEqual(group.attrib, {"id": "track_route", "source": "entities_offline_freeroam.xml"})
        self.assertEqual(group.find(".//property[@id='TargetFlow']").get("value"),
                         "trackloadercps.resume_mainland")
        self.assertIsNotNone(group.find(".//property[@id='Autostart'][@value='true']"))
        self.assertIsNone(group.find(".//component[@type='FreeRoamSetupComponent']"))

    def test_native_resume_cannot_delegate_start_driving_to_career_flow(self):
        ET.SubElement(self.native["global"]["start_driving"], "node", {"state": "career.clear_current_career_race"})
        self.write_native()
        with self.assertRaisesRegex(ValueError, "direct native mainland resume primitive start_driving"):
            validate_native(flow("TopGearCPS"), self.stateflow)

    def test_player_car_control_is_balanced(self):
        root = flow("TopGearCPS")
        calls = lambda state: [n.get("state") for n in root.find("state[@id='%s']" % state).findall("node")]
        enter = calls("enter")
        mainland = calls("return_mainland") + calls("resume_mainland")
        for path in (enter, mainland):
            self.assertEqual(path.count("global.disable_car_control"), 1)
            self.assertEqual(path.count("global.enable_car_control"), 1)
            self.assertLess(path.index("global.disable_car_control"), path.index("global.enable_car_control"))

    def test_race_start_contract_is_verified(self):
        ET.SubElement(self.native["global"]["start_race"], "node", {"state": "career.clear_current_career_race"})
        self.write_native()
        with self.assertRaisesRegex(ValueError, "native race start contract"):
            validate_native(flow("TopGearCPS"), self.stateflow)

    def test_autostart_groups(self):
        for target, filename in [("enter", ENTER_FILE), ("return_mainland", RETURN_FILE)]:
            self.assertTrue(filename.startswith("entities_trackloadercps_"))
            group = machine_group(target)
            self.assertEqual(group.get("id"), "trackloadercps_transition")
            self.assertIsNotNone(group.find(".//property[@id='Autostart'][@value='true']"))
            self.assertEqual(group.find(".//property[@id='TargetFlow']").get("value"),
                             "trackloadercps." + target)

    def test_locator_loop_uses_native_predicate_and_frame_wait(self):
        root = flow("TopGearCPS")
        wait = root.find("state[@id='wait_locators']")
        predicate, next_frame = wait.findall("node")
        self.assertEqual(predicate.get("state"), "track_swap.is_locators_module_ready")
        self.assertEqual(predicate.find("exit[@id='yes']").get("target"), ".exit")
        self.assertEqual(predicate.find("exit[@id='no']").get("target"), next_frame.get("id"))
        self.assertEqual(next_frame.get("state"), "global.wait_for_next_frame")
        self.assertEqual(next_frame.find("exit").get("target"), predicate.get("id"))

    def test_bad_namespace_and_missing_native_state(self):
        for namespace in ["../Anthem", "Anthem\\Other", "x:y", "", "1map"]:
            with self.assertRaises(ValueError):
                flow(namespace)
        del self.native["global"]["restore_car_position"]
        self.write_native()
        with self.assertRaisesRegex(ValueError, "Missing native state"):
            build(self.stateflow, self.entitymodel, self.root / "failed")
        self.assertFalse((self.root / "failed").exists())

    def test_native_contract_mismatch(self):
        swap = self.native["track_swap"]["swap_track"].find("behavior")
        swap.set("id", "OtherSwap")
        self.write_native()
        with self.assertRaisesRegex(ValueError, "SwapTrack behavior"):
            validate_native(flow("TopGearCPS"), self.stateflow)

    def test_unsupported_or_missing_parameter(self):
        root = flow("TopGearCPS")
        root.find(".//node[@state='global.disable_mixer_snapshot']").set("unknown", "true")
        with self.assertRaisesRegex(ValueError, "Unsupported native argument"):
            validate_native(root, self.stateflow)
        root = flow("TopGearCPS")
        root.find(".//node[@state='global.disable_mixer_snapshot']").attrib.pop("snapshot")
        with self.assertRaisesRegex(ValueError, "Missing native parameter"):
            validate_native(root, self.stateflow)
        root = flow("TopGearCPS")
        root.find(".//node[@state='global.fade_in']").set("force", "1")
        with self.assertRaisesRegex(ValueError, "Boolean"):
            validate_native(root, self.stateflow)

    def test_unresolved_local_state(self):
        root = flow("TopGearCPS")
        root.find(".//node[@state='wait_locators']").set("state", "missing")
        with self.assertRaisesRegex(ValueError, "Unresolved custom state"):
            validate_native(root, self.stateflow)

    def test_mainland_startup_must_match(self):
        self.write_entities(target="career_swap.root_mainland")
        with self.assertRaisesRegex(ValueError, "startup flow"):
            validate_entitymodel(self.entitymodel)
        self.write_entities(base_startup=True)
        with self.assertRaisesRegex(ValueError, "already contains stock startup"):
            validate_entitymodel(self.entitymodel)
        self.write_entities(inherited="entities_offline_freeroam_island.xml")
        with self.assertRaisesRegex(ValueError, "route inheritance"):
            validate_entitymodel(self.entitymodel)


if __name__ == "__main__":
    unittest.main()
