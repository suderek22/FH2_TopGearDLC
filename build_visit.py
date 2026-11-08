"""Build unmounted native stateflow resources for a static custom-track visit."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET
import zipfile


GROUP = "trackloadercps"
ROUTE_FILE = "entities_trackloadercps_static_route"
ENTER_FILE = "entities_trackloadercps_enter"
RETURN_FILE = "entities_trackloadercps_return"
MAINLAND_FILE = "entities_trackloadercps_mainland"
RESUME_CONTRACTS = {
    "start_driving": {"id": "StartDriving"},
    "start_time": {"id": "StopTimeOfDay", "stopped": "false"},
    "unpause_weather": {"id": "PauseWeather", "pause": "false", "gameoption": "false"},
    "ai_spawning_mode_none": {"id": "SetSpawnMode", "spawn_mode": "None", "traffic_spawn_delay": "false"},
    "restore_car_position": {"id": "RestoreCarPosition"},
    "clear_mulligan_key_frames": {
        "id": "ClearMulliganKeyFrames", "start_recording_replay": "{start_recording_replay}"},
}


def node(state, target, **arguments):
    return ET.SubElement(state, "node", {"state": target, **arguments})


def transition(root, name):
    state = ET.SubElement(root, "state", {"id": name})
    control = ET.SubElement(state, "behavior", {
        "id": "GameControlWrapper", "force_take_control": "false", "allow_failure": "true"})
    ET.SubElement(control, "transition", {"id": "onFailedToTakeControl"})
    ET.SubElement(state, "behavior", {"id": "BlockPauseEntryWrapper"})
    ET.SubElement(state, "behavior", {"id": "DisableMicroSaveWrapper"})
    node(state, "track_swap.stop_car_and_fade_out")
    node(state, "global.enable_mixer_snapshot", snapshot="BASE_Loading")
    node(state, "ui_screens.show_fmv_loading_screen_and_continue", loading_reason="")
    node(state, "global.apply_parking_brake_human")
    node(state, "global.disable_car_control")
    node(state, "track_swap.remove_all_ai_cars")
    # Like vanilla track_swap.common_track_swap, keep the player car loaded: SwapTrack's simulation
    # load phase waits for cars-ready-for-display, so unloading the car here deadlocks the swap.
    node(state, "track_swap.unload_navigation_world")
    node(state, "track_swap.swap_track", wait="true")
    return state


def flow(media_name):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,47}", media_name):
        raise ValueError("Invalid custom media namespace")
    root = ET.Element("stategroup", {"id": GROUP})
    enter = transition(root, "enter")
    node(enter, "track_swap.load_navigation_world", track_name=media_name, ribbon="0")
    node(enter, "wait_locators")
    node(enter, "global.load_entity_group", entity_group_file=ROUTE_FILE,
         wait_for_state_machine_exit="false", allow_replace_if_group_exists="true")
    node(enter, "global.ai_spawning_mode_none")
    node(enter, "global.restore_car_position")
    # PlayerCarControl is balanced like the stock route switch: transition() disabled it once,
    # so a second disable here would leave the car uncontrollable after the single enable.
    node(enter, "global.stop_car")
    node(enter, "global.apply_parking_brake_human")
    node(enter, "global.reset_camera_to_player")
    node(enter, "global.enable_track_streaming")
    node(enter, "global.wait_for_streaming_around_local_human_car")
    node(enter, "global.clear_mulligan_key_frames", start_recording_replay="false")
    node(enter, "global.disable_mixer_snapshot", snapshot="BASE_Loading")
    node(enter, "global.fade_in", force="false")
    node(enter, "ui_screens.show_hud_and_continue")
    node(enter, "global.resume_all_huds")
    # SwapTrack resets the game's race session; like stock free_roam_start.start_driving, restart it
    # or the player car never accepts input on the new map.
    node(enter, "global.start_race")
    node(enter, "global.start_driving")
    node(enter, "global.enable_car_control")
    node(enter, "global.release_parking_brake_human")

    returned = transition(root, "return_mainland")
    node(returned, "track_swap.load_navigation_world", track_name="Anthem", ribbon="0")
    node(returned, "wait_locators")
    node(returned, "global.load_entity_group",
         entity_group_file=MAINLAND_FILE,
         wait_for_state_machine_exit="false", allow_replace_if_group_exists="true")

    resume = ET.SubElement(root, "state", {"id": "resume_mainland"})
    ET.SubElement(resume, "behavior", {"id": "GameControlWrapper"})
    ET.SubElement(resume, "behavior", {"id": "BlockPauseEntryWrapper"})
    ET.SubElement(resume, "behavior", {"id": "DisableMicroSaveWrapper"})
    node(resume, "global.ai_spawning_mode_none")
    node(resume, "global.restore_car_position")
    node(resume, "global.stop_car")
    node(resume, "global.apply_parking_brake_human")
    node(resume, "global.reset_camera_to_player")
    node(resume, "global.enable_track_streaming")
    node(resume, "global.wait_for_streaming_around_local_human_car")
    node(resume, "global.clear_mulligan_key_frames", start_recording_replay="false")
    node(resume, "global.start_time")
    node(resume, "global.unpause_weather")
    node(resume, "global.disable_mixer_snapshot", snapshot="BASE_Loading")
    node(resume, "global.fade_in", force="false")
    node(resume, "ui_screens.show_hud_and_continue")
    node(resume, "global.resume_all_huds")
    node(resume, "global.start_race")
    node(resume, "global.start_driving")
    node(resume, "global.enable_car_control")
    node(resume, "global.release_parking_brake_human")

    wait = ET.SubElement(root, "state", {"id": "wait_locators"})
    check = node(wait, "track_swap.is_locators_module_ready")
    ET.SubElement(check, "exit", {"id": "yes", "target": ".exit"})
    ET.SubElement(check, "exit", {"id": "no", "target": "next_frame"})
    next_frame = ET.SubElement(wait, "node", {"id": "next_frame", "state": "global.wait_for_next_frame"})
    ET.SubElement(next_frame, "exit", {"target": "_is_locators_module_ready"})
    check.set("id", "_is_locators_module_ready")
    return root


def machine_group(target):
    root = ET.Element("entity_group", {"id": "trackloadercps_transition"})
    entity = ET.SubElement(root, "entity", {"id": "transition"})
    component = ET.SubElement(entity, "component", {
        "type": "GameStateMachineComponent", "id": "trackloadercps_flow"})
    ET.SubElement(component, "property", {"id": "TargetFlow", "value": GROUP + "." + target})
    ET.SubElement(component, "property", {"id": "Autostart", "value": "true"})
    return root


def mainland_group():
    root = ET.Element("entity_group", {
        "id": "track_route", "source": "entities_offline_freeroam.xml"})
    entity = ET.SubElement(root, "entity", {"id": "trackloadercps_mainland_resume"})
    component = ET.SubElement(entity, "component", {
        "type": "GameStateMachineComponent", "id": "trackloadercps_resume"})
    ET.SubElement(component, "property", {"id": "TargetFlow", "value": GROUP + ".resume_mainland"})
    ET.SubElement(component, "property", {"id": "Autostart", "value": "true"})
    return root


def xml_bytes(root):
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"


def validate_native(root, archive):
    groups = {}
    with zipfile.ZipFile(archive) as source:
        for name in source.namelist():
            if not name.lower().endswith(".xml"):
                continue
            group = ET.fromstring(source.read(name))
            if group.tag == "stategroup" and group.get("id") in ("global", "track_swap", "ui_screens"):
                key = group.get("id")
                if key in groups:
                    raise ValueError("Duplicate native state group " + key)
                groups[key] = {state.get("id"): state for state in group.findall("state")}
    local = {state.get("id") for state in root.findall("state")}
    used = []
    for call in root.iter("node"):
        target = call.get("state")
        if "." not in target:
            if target not in local:
                raise ValueError("Unresolved custom state " + target)
            continue
        group_name, state_name = target.split(".", 1)
        state = groups.get(group_name, {}).get(state_name)
        if state is None:
            raise ValueError("Missing native state " + target)
        parameters = {param.get("id"): param for param in state.findall("parameter")}
        arguments = set(call.attrib) - {"state", "id"}
        if arguments - parameters.keys():
            raise ValueError("Unsupported native argument for " + target)
        for name, parameter in parameters.items():
            if name not in arguments and "value" not in parameter.attrib:
                raise ValueError("Missing native parameter " + target + "." + name)
            if name in arguments and parameter.get("type") == "bool" and call.get(name) not in ("true", "false"):
                raise ValueError("Invalid native Boolean argument for " + target)
        used.append(target)
    swap = groups["track_swap"]["swap_track"].find("behavior")
    if swap is None or swap.get("id") != "SwapTrack" or swap.get("wait") != "{wait}":
        raise ValueError("Unsupported native SwapTrack behavior contract")
    restore = groups["global"]["restore_car_position"].find("behavior")
    if restore is None or restore.get("id") != "RestoreCarPosition":
        raise ValueError("Unsupported native car restore behavior contract")
    race = groups["global"]["start_race"]
    if ([behavior.get("id") for behavior in race.findall("behavior")] != ["AIDriversReset", "StartRace"] or
            race.findall("node")):
        raise ValueError("Unsupported native race start contract")
    for name, expected in RESUME_CONTRACTS.items():
        state = groups["global"][name]
        behaviors = state.findall("behavior")
        if len(behaviors) != 1 or behaviors[0].attrib != expected or state.findall("node"):
            raise ValueError("Unsupported direct native mainland resume primitive " + name)
    return sorted(set(used))


def validate_entitymodel(archive):
    with zipfile.ZipFile(archive) as source:
        base = ET.fromstring(source.read("entities_offline_freeroam.xml"))
        if base.tag != "entity_group" or base.attrib != {"id": "track_route"}:
            raise ValueError("Unsupported native mainland base route")
        if any(element.get("type") == "FreeRoamSetupComponent" or
               element.get("id") == "free_roam_start" for element in base.iter()):
            raise ValueError("Native mainland base route already contains stock startup")
        returned = ET.fromstring(source.read("entities_route_offline_freeroam.xml"))
        if returned.tag != "entity_group" or returned.get("id") != "track_route":
            raise ValueError("Unsupported native mainland route group")
        if returned.get("source") != "entities_offline_freeroam.xml":
            raise ValueError("Unsupported native mainland route inheritance")
        startup = returned.find("entity[@id='free_roam_start']")
        if startup is None or startup.get("template") != "free_roam_startup_template":
            raise ValueError("Native mainland free-roam startup is missing")
        target = startup.find("property[@component='free_roam_start_flow'][@id='TargetFlow']")
        if target is None or target.get("value") != "free_roam_start.enter_offline":
            raise ValueError("Unsupported native mainland startup flow")
        templates = ET.fromstring(source.read("Templates/templates_global.xml"))
        component = templates.find(
            "entity_template[@name='pause_flow']/component[@type='GameStateMachineComponent']")
        if component is None or component.find("property[@id='Autostart'][@value='true']") is None:
            raise ValueError("Native autostart state machine contract is missing")


def build(stateflow, entitymodel, output, media_name="TopGearCPS"):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a new native visit component directory: " + str(output))
    root = flow(media_name)
    calls = validate_native(root, stateflow)
    validate_entitymodel(entitymodel)
    resources = {
        "stateflow\\" + GROUP + ".xml": xml_bytes(root),
        "EntityModel\\" + ENTER_FILE + ".xml": xml_bytes(machine_group("enter")),
        "EntityModel\\" + RETURN_FILE + ".xml": xml_bytes(machine_group("return_mainland")),
        "EntityModel\\" + ROUTE_FILE + ".xml": xml_bytes(ET.Element("entity_group", {"id": "track_route"})),
        "EntityModel\\" + MAINLAND_FILE + ".xml": xml_bytes(mainland_group()),
    }
    report = {
        "component": "native-static-visit-flow", "media_name": media_name,
        "engine_validated": False, "file_mount_enabled": False, "first_test_ready": False,
        "native_stateflow_sha256": hashlib.sha256(Path(stateflow).read_bytes()).hexdigest(),
        "native_entitymodel_sha256": hashlib.sha256(Path(entitymodel).read_bytes()).hexdigest(),
        "native_state_calls": calls,
        "enter_group": ENTER_FILE, "return_group": RETURN_FILE,
        "entry_flow": GROUP + ".enter", "return_flow": GROUP + ".return_mainland",
        "career_swap_used": False, "custom_route_entities": 0,
        "return_startup": GROUP + ".resume_mainland",
        "return_route_group": MAINLAND_FILE,
        "stock_mainland_startup_used": False,
        "return_ai_enabled": False, "mulligan_recording_enabled": False,
        "required_runtime": [
            "Verify native save hooks and hold the save gate before dispatch; keep it held after flow abort.",
            "Capture live mainland position/facing and verify original career data without writing it.",
            "Publish media, verify additive database registration and load this independent entity group.",
            "Scope SwapTrack local arguments and RestoreCarPosition facing/position to the visit.",
            "Keep stock travel/reloads unavailable during the visit; verify transition ownership.",
            "Observe custom driving readiness and restore pose during the dedicated mainland resume.",
            "Release save protection only after verified idle mainland return and unchanged career data.",
        ],
        "files": [],
    }
    for relative, data in resources.items():
        report["files"].append({"media_path": relative, "bytes": len(data),
                                "sha256": hashlib.sha256(data).hexdigest()})
    output.mkdir(parents=True)
    for relative, data in resources.items():
        path = output / "mediaoverride" / Path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (output / "visit.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stateflow", type=Path, required=True)
    parser.add_argument("--entitymodel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--media-name", default="TopGearCPS")
    args = parser.parse_args()
    try:
        report = build(args.stateflow, args.entitymodel, args.output, args.media_name)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, ET.ParseError) as error:
        print("error: " + str(error), file=sys.stderr)
        return 1
    print("Built %d unmounted native visit resources; runtime integration remains required."
          % len(report["files"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
