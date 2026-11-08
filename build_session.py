"""Assemble the explicitly save-disabled experimental native test package."""
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

VISIT_PATHS = {
    r"stateflow\trackloadercps.xml",
    r"EntityModel\entities_trackloadercps_enter.xml",
    r"EntityModel\entities_trackloadercps_return.xml",
    r"EntityModel\entities_trackloadercps_static_route.xml",
    r"EntityModel\entities_trackloadercps_mainland.xml",
}


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


def build(media, visit, database, source_database, output):
    media, visit, output = map(lambda p: Path(p).resolve(), (media, visit, output))
    if output.exists():
        raise ValueError("Choose a new experimental package directory")
    world = json.loads((media / "media.json").read_text())
    lifecycle = json.loads((visit / "visit.json").read_text())
    name = world["media_name"]
    if name != "TopGearCPS" or not world["native_resource_closure_verified"]:
        raise ValueError("First test requires the verified TopGearCPS native package")
    if not world.get("navigation_present") or not world.get("settings_and_locators_present"):
        raise ValueError("First test requires native navigation, settings and locators")
    if not world.get("static_test_companions_present") or world["static_test"]["dynamic_collision_object_count"]:
        raise ValueError("First test requires the approved static collision profile")
    if (lifecycle.get("return_startup") != "trackloadercps.resume_mainland" or
            lifecycle.get("stock_mainland_startup_used") is not False or
            verify_files(visit, lifecycle) != VISIT_PATHS):
        raise ValueError("First test requires the five-resource dedicated native return")
    verify_files(media, world)
    validate_media(media / "mediaoverride" / "tracks" / name, name, world["arrival"])
    position = world["arrival"]["candidate_position"]
    facing = world["arrival"]["locator_transform"][8:11]
    length = math.sqrt(sum(v * v for v in facing))
    if len(position) != 3 or not all(math.isfinite(v) for v in position + facing) or length <= 0:
        raise ValueError("Invalid experimental arrival pose")
    facing = [v / length for v in facing]
    with closing(connect_readonly(database)) as target, closing(connect_readonly(source_database)) as source:
        sql, track, environment, _ = registration(target, source, experimental=True)
        verify_registration(target, sql, track, environment)
    output.mkdir(parents=True)
    shutil.copytree(media, output / "media")
    shutil.copytree(visit, output / "visit")
    catalog = "TLCPS-VISIT-1\t" + name + "\n"
    for row in lifecycle["files"]:
        catalog += "%s\t%d\t%s\n" % (row["sha256"], row["bytes"], row["media_path"])
    (output / "visit" / "media.catalog").write_text(catalog, encoding="ascii", newline="")
    (output / "registration.sql").write_text(sql, encoding="utf-8", newline="")
    ini = configparser.ConfigParser(interpolation=None)
    ini.optionxform = str
    ini["Track"] = {"Id": "10000", "Name": "Top Gear (CPS)", "State": "experimental",
                    "Reason": "Session-only native test; saving disabled until game restart."}
    ini["LocalDLC"] = {"Format": "1", "EnvironmentId": "10000", "MediaName": name,
                      "Delivery": "mod-managed", "Registration": "registration.sql"}
    ini["Media"] = {"Directory": "media", "CatalogSha256": sha(output / "media" / "media.catalog")}
    ini["Experimental"] = {
        "SessionOnly": "1", "VisitDirectory": "visit",
        "VisitCatalogSha256": sha(output / "visit" / "media.catalog"),
        "RegistrationSha256": sha(output / "registration.sql"),
        "ArrivalPosition": ",".join(format(v, ".9g") for v in position),
        "ArrivalFacing": ",".join(format(v, ".9g") for v in facing),
    }
    with (output / "track.ini").open("w", encoding="utf-8", newline="") as stream:
        ini.write(stream)
    report = {
        "component": "session-only-native-test", "track_id": 10000, "environment_id": 10000,
        "saving_disabled_until_restart": True, "one_visit_per_process": True,
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
    args = parser.parse_args()
    build(args.media, args.visit, args.database, args.source_database, args.output)
    print("Built session-only native test package; in-game rendering/driving are not yet validated.")
