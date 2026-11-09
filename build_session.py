"""Assemble a persistent experimental custom-track package."""
import argparse
import configparser
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import shutil

from build_dlc import connect_readonly, registration, verify_registration
from build_media import validate_media
from build_visit import lifecycle_paths, validate_lifecycle_namespace

VISIT_PATHS = lifecycle_paths()


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_files(component, report):
    seen = set()
    for row in report["files"]:
        name = row["media_path"]
        parts = name.split("\\")
        if (name.casefold() in seen or any(part in ("", ".", "..") for part in parts) or
                any("/" in part or ":" in part for part in parts)):
            raise ValueError("Duplicate or escaping component path")
        seen.add(name.casefold())
        path = component / "mediaoverride" / name
        if path.stat().st_size != row["bytes"] or sha(path) != row["sha256"]:
            raise ValueError("Component file changed: " + str(path))
    return {row["media_path"] for row in report["files"]}


def write_visit_catalog(visit, report, name):
    verify_files(visit, report)
    catalog = "TLCPS-VISIT-1\t" + name + "\n"
    for row in report["files"]:
        catalog += "%s\t%d\t%s\n" % (row["sha256"], row["bytes"], row["media_path"])
    (visit / "media.catalog").write_text(catalog, encoding="ascii", newline="")


def build(media, visit, database, source_database, output, arrival_position=None, arrival_facing=None,
          restore_saving_on_return=False, track_id=10000, environment_id=10000,
          display_name="Top Gear (CPS)", source_media_name="topgear"):
    media, visit, output = map(lambda p: Path(p).resolve(), (media, visit, output))
    if output.exists():
        raise ValueError("Choose a new experimental package directory")
    world = json.loads((media / "media.json").read_text())
    lifecycle = json.loads((visit / "visit.json").read_text())
    namespace = validate_lifecycle_namespace(lifecycle.get("lifecycle_namespace", "trackloadercps"))
    name = world["media_name"]
    if not world["native_resource_closure_verified"]:
        raise ValueError("Custom track requires a verified native resource closure")
    if lifecycle.get("media_name") != name:
        raise ValueError("Visit navigation namespace does not match the world media")
    if not world.get("navigation_present") or not world.get("settings_and_locators_present"):
        raise ValueError("First test requires native navigation, settings and locators")
    if not world.get("static_test_companions_present") or world["static_test"]["dynamic_collision_object_count"]:
        raise ValueError("First test requires the approved static collision profile")
    if (lifecycle.get("return_startup") != namespace + ".resume_mainland" or
            lifecycle.get("stock_mainland_startup_used") is not False or
            verify_files(visit, lifecycle) != lifecycle_paths(namespace)):
        raise ValueError("First test requires the five-resource dedicated native return")
    verify_files(media, world)
    validate_media(media / "mediaoverride" / "tracks" / name, name, world["arrival"])
    position = world["arrival"]["candidate_position"]
    facing = world["arrival"]["locator_transform"][8:11]
    length = math.sqrt(sum(v * v for v in facing))
    if len(position) != 3 or not all(math.isfinite(v) for v in position + facing) or length <= 0:
        raise ValueError("Invalid experimental arrival pose")
    facing = [v / length for v in facing]
    if (arrival_position is None) != (arrival_facing is None):
        raise ValueError("Arrival position and facing overrides must be supplied together")
    if arrival_position is not None:
        position, facing = list(arrival_position), list(arrival_facing)
        if (len(position) != 3 or len(facing) != 3 or
                not all(math.isfinite(v) for v in position + facing) or
                abs(sum(v * v for v in facing) - 1) > 0.001):
            raise ValueError("Arrival overrides require finite XYZ position and unit facing")
    with closing(connect_readonly(database)) as target, closing(connect_readonly(source_database)) as source:
        sql, track, environment, _ = registration(
            target, source, track_id, environment_id, name, experimental=True,
            display_name=display_name, source_media_name=source_media_name)
        verify_registration(target, sql, track, environment)
    output.mkdir(parents=True)
    shutil.copytree(media, output / "media")
    shutil.copytree(visit, output / "visit")
    write_visit_catalog(output / "visit", lifecycle, name)
    (output / "registration.sql").write_text(sql, encoding="utf-8", newline="")
    ini = configparser.ConfigParser(interpolation=None)
    ini.optionxform = str
    ini["Track"] = {"Id": str(track_id), "Name": display_name, "State": "experimental",
                    "Reason": ("Persistent experimental track; saving resumes after verified mainland return; "
                               "career changes not audited." if restore_saving_on_return else
                               "Persistent experimental track; saving disabled until game restart.")}
    ini["LocalDLC"] = {"Format": "1", "EnvironmentId": str(environment_id), "MediaName": name,
                      "Delivery": "mod-managed", "Registration": "registration.sql"}
    ini["Media"] = {"Directory": "media", "CatalogSha256": sha(output / "media" / "media.catalog")}
    ini["Experimental"] = {
        "Enabled": "1", "RestoreSavingOnReturn": str(int(restore_saving_on_return)),
        "LifecycleNamespace": namespace,
        "VisitDirectory": "visit",
        "VisitCatalogSha256": sha(output / "visit" / "media.catalog"),
        "RegistrationSha256": sha(output / "registration.sql"),
        "ArrivalPosition": ",".join(format(v, ".9g") for v in position),
        "ArrivalFacing": ",".join(format(v, ".9g") for v in facing),
    }
    with (output / "track.ini").open("w", encoding="utf-8", newline="") as stream:
        ini.write(stream)
    report = {
        "component": "persistent-native-test", "track_id": track_id, "environment_id": environment_id,
        "lifecycle_namespace": namespace,
        "saving_disabled_until_restart": not restore_saving_on_return, "one_visit_per_process": False,
        "persistent_activation": True, "career_changes_audited": False,
        "world_file_count": len(world["files"]), "visit_file_count": len(lifecycle["files"]),
        "stock_rows_preserved": True, "in_game_validated": False,
        "media_component": str(media), "visit_component": str(visit),
        "arrival_position": position, "arrival_facing": facing,
    }
    (output / "session.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("media", "visit", "database", "source-database", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--arrival-position", nargs=3, type=float)
    parser.add_argument("--arrival-facing", nargs=3, type=float)
    parser.add_argument("--track-id", type=int, default=10000)
    parser.add_argument("--environment-id", type=int, default=10000)
    parser.add_argument("--display-name", default="Top Gear (CPS)")
    parser.add_argument("--source-media-name", default="topgear")
    parser.add_argument("--restore-saving-on-return", action="store_true",
                        help="Opt in to saving after verified return without a career-state audit")
    args = parser.parse_args()
    build(args.media, args.visit, args.database, args.source_database, args.output,
          args.arrival_position, args.arrival_facing, args.restore_saving_on_return,
          args.track_id, args.environment_id, args.display_name, args.source_media_name)
    print("Built persistent experimental package; in-game rendering/driving are not yet validated.")
