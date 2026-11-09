"""Build disabled, additive registration for a local TrackLoaderCPS map pack."""
import argparse
import configparser
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys


BLOCKERS = [
    "Complete native FPVS root, spatial lookup and streaming dependencies.",
    "FH2 materials/shaders, physical surface semantics and resident resource wiring.",
    "Native custom-track travel and arrival/return flow; no raw manager-field writes.",
    "In-game load, rendering, collision, driving and return-to-mainland validation.",
]


def connect_readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def table_rows(connection, table):
    connection.row_factory = sqlite3.Row
    return [dict(row) for row in connection.execute('SELECT * FROM "%s" ORDER BY 1' % table)]


def literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    if isinstance(value, (int, float)):
        return json.dumps(value, allow_nan=False)
    raise ValueError("Unsupported registration database value")


def insertion(table, row):
    columns = ", ".join('"%s"' % name for name in row)
    values = ", ".join(literal(value) for value in row.values())
    return 'INSERT INTO "%s" (%s) VALUES (%s);\n' % (table, columns, values)


def registration(connection, source_connection, track_id=10000,
                 environment_id=10000, media_name="TopGearCPS", experimental=False,
                 display_name="Top Gear (CPS)", source_media_name="topgear"):
    if type(experimental) is not bool:
        raise ValueError("Experimental registration must be explicitly Boolean")
    if (type(track_id) is not int or type(environment_id) is not int or
            not 10000 <= track_id <= 2147483647 or not 10000 <= environment_id <= 2147483647):
        raise ValueError("Custom track/environment IDs must be signed integers >= 10000")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,47}", media_name):
        raise ValueError("Invalid custom media namespace")
    if (not isinstance(display_name, str) or not display_name.strip() or
            len(display_name) > 100 or any(ord(c) < 32 for c in display_name)):
        raise ValueError("Invalid custom track display name")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,47}", source_media_name):
        raise ValueError("Invalid source media namespace")
    tracks = table_rows(connection, "Tracks")
    environments = table_rows(connection, "Environments")
    if any(row["id"] == track_id or str(row["MediaName"]).casefold() == media_name.casefold()
           for row in tracks):
        raise ValueError("Custom track ID or media namespace already exists")
    if any(row["Id"] == environment_id or str(row["MediaName"]).casefold() == media_name.casefold()
           for row in environments):
        raise ValueError("Custom environment ID or media namespace already exists")
    template = next((row for row in tracks if row["id"] == 542), None)
    if template is None or template["MediaName"] != "Anthem":
        raise ValueError("Expected native FH2 mainland track template 542")
    environment = next((row for row in environments if row["Id"] == template["EnvironmentId"]), None)
    if environment is None:
        raise ValueError("Mainland environment template is missing")
    sources = table_rows(source_connection, "Tracks")
    source = next((row for row in sources
                   if row["MediaName"].casefold() == source_media_name.casefold() and row["RibbonIndex"] == 0), None)
    if source is None:
        raise ValueError("FM5 %s ribbon 0 metadata is missing" %
                         ("Top Gear" if source_media_name.casefold() == "topgear" else source_media_name))
    source_environment = next((row for row in table_rows(source_connection, "Environments")
                               if row["Id"] == source["EnvironmentId"]), None)
    if source_environment is None:
        raise ValueError("FM5 Top Gear environment metadata is missing")
    track = dict(template)
    track.update({
        "id": track_id, "EnvironmentId": environment_id, "MediaName": media_name,
        "MediaTrack": track_id, "RibbonIndex": 0, "Route": 0,
        "DisplayName": "Top Gear (CPS)", "ShortDisplayName": "Top Gear",
        "ShortDisplayNameAllCaps": "TOP GEAR", "Description": "Local custom map for TrackLoaderCPS",
        "Length": source["Length"], "IsRealWorld": source["IsRealWorld"],
        "OnTrackFrictionScale": source["OnTrackFrictionScale"],
        "ContentId": 0, "Active": 0, "Renderable": 0, "IsInstalled": 0, "IsPurchased": 0,
    })
    env = dict(environment)
    env.update({
        "Id": environment_id, "MediaName": media_name, "DisplayName": "Top Gear (CPS)",
        "ShortDisplayName": "Top Gear", "Location": "Top Gear", "LocationAllCaps": "TOP GEAR",
        "Description": "Local custom map for TrackLoaderCPS",
        "CountryId": source_environment["CountryId"], "ShowOnMap": 0,
    })
    if experimental:
        track.update({"Active": 1, "Renderable": 1, "IsInstalled": 1, "IsPurchased": 1})
    if display_name != "Top Gear (CPS)":
        track.update({"DisplayName": display_name, "ShortDisplayName": display_name,
                      "ShortDisplayNameAllCaps": display_name.upper()})
        env.update({"DisplayName": display_name, "ShortDisplayName": display_name,
                    "Location": display_name, "LocationAllCaps": display_name.upper()})
    guard = "TrackLoaderCPS_RegistrationGuard"
    sql = (('-- Session-only experimental registration; NOT a paid DLC offer or a validated map.\n'
            if experimental else '-- Disabled local-map registration; NOT a paid DLC offer or a playable map.\n') +
           '-- Run only through the platform per-mod transaction after pack verification.\n'
           'CREATE TEMP TABLE "%s" (ok INTEGER NOT NULL CHECK (ok = 1));\n' % guard)
    sql += ('INSERT INTO "%s" VALUES (NOT EXISTS (SELECT 1 FROM Tracks WHERE id = %d '
            'OR MediaName = %s COLLATE NOCASE) AND NOT EXISTS '
            '(SELECT 1 FROM Environments WHERE Id = %d OR MediaName = %s COLLATE NOCASE));\n'
            % (guard, track_id, literal(media_name), environment_id, literal(media_name)))
    sql += insertion("Environments", env) + insertion("Tracks", track)
    sql += 'DROP TABLE "%s";\n' % guard
    return sql, track, env, source


def verify_registration(connection, sql, track, environment):
    with sqlite3.connect(":memory:") as candidate:
        for table in ("Environments", "Tracks"):
            definition = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if definition is None:
                raise ValueError("Missing native registration table " + table)
            candidate.execute(definition[0])
            rows = table_rows(connection, table)
            if rows:
                placeholders = ",".join("?" for _ in rows[0])
                candidate.executemany('INSERT INTO "%s" VALUES (%s)' % (table, placeholders),
                                      [tuple(row.values()) for row in rows])
        candidate.commit()
        candidate.executescript("BEGIN;\n" + sql + "\nCOMMIT;")
        for table, expected, key in (("Tracks", track, "id"), ("Environments", environment, "Id")):
            before = table_rows(connection, table)
            after = table_rows(candidate, table)
            if ([row for row in after if row[key] != expected[key]] != before or
                    [row for row in after if row[key] == expected[key]] != [expected]):
                raise ValueError("Registration altered stock rows or failed to insert its custom row")
    return True


def build(database, source_database, output, track_id=10000, environment_id=10000,
          media_name="TopGearCPS"):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a new DLC registration component directory: " + str(output))
    connection = connect_readonly(database)
    source_connection = connect_readonly(source_database)
    try:
        sql, track, environment, source = registration(
            connection, source_connection, track_id, environment_id, media_name)
        verify_registration(connection, sql, track, environment)
    finally:
        connection.close()
        source_connection.close()
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser["Track"] = {"Id": str(track_id), "Name": "Top Gear (CPS)",
                       "Author": "suderek", "Version": "0.3.2-beta", "State": "blocked",
                       "Reason": "Full world, surface/material wiring and native custom travel are unfinished."}
    parser["LocalDLC"] = {"Format": "1", "EnvironmentId": str(environment_id),
                          "MediaName": media_name, "Delivery": "mod-managed",
                          "Registration": "registration.sql"}
    media_path = "tracks\\" + media_name
    report = {
        "component": "local-dlc-disabled-registration", "delivery": "mod-managed",
        "track_id": track_id, "environment_id": environment_id, "media_name": media_name,
        "game_media_path": "game:\\media\\" + media_path,
        "override_relative_path": "mediaoverride\\" + media_path,
        "root_path": media_path + "\\ribbon_00\\" + media_name + "_00.pvs",
        "source_track_id": source["id"], "source_length": source["Length"],
        "registration_verified": True, "stock_rows_preserved": True,
        "playable_map": False, "engine_validated": False,
        "registration_sha256": hashlib.sha256(sql.encode("utf-8")).hexdigest(),
        "native_travel_constraint": {
            "function_rva": "0x93F0F0",
            "stock_track_ids": [542, 549],
            "other_track_behavior": "Falls through to the mainland transition, not a custom target.",
            "custom_travel_implemented": False,
        },
        "remaining": BLOCKERS,
        "notes": [
            "Adds only Tracks and Environments; does not update stock maps, saves or content offers.",
            "ContentId 0 is local mod content, not Storm Island's paid offer or platform entitlement.",
            "New track visibility, rendering, installation and purchase flags are all disabled.",
            "Native DLC travel is not generalized by adding database rows.",
            "Inherited FH2 UI/start-grid fields are provisional, not converted FM5 UI or navigation.",
            "No media is installed or mounted by this registration component.",
        ],
    }
    output.mkdir(parents=True)
    (output / "registration.sql").write_text(sql, encoding="utf-8")
    with (output / "track.ini").open("w", encoding="utf-8") as file:
        parser.write(file)
    (output / "dlc.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--source-database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--track-id", type=int, default=10000)
    parser.add_argument("--environment-id", type=int, default=10000)
    parser.add_argument("--media-name", default="TopGearCPS")
    args = parser.parse_args()
    try:
        report = build(args.database, args.source_database, args.output,
                       args.track_id, args.environment_id, args.media_name)
    except (OSError, ValueError, sqlite3.Error, KeyError) as error:
        print("error: " + str(error), file=sys.stderr)
        return 1
    print("Verified additive local DLC registration: track %d; stock rows untouched; activation blocked."
          % report["track_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
