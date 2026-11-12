"""Compile native first-test road navigation; traffic and AI remain disabled."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

from collision import read_collision
from navigation import geometry
from openworld_navigation import closed_road, fm5_route_positions, read_navw, read_owwf, validate_closed_road, waypoint_grid, write_navw, write_owwf


def build(source_ai, source_collision, output, road_type="none"):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a new native navigation component directory: " + str(output))
    with zipfile.ZipFile(source_ai) as archive:
        data = archive.read("Track.geo")
    positions = fm5_route_positions(geometry(data)["positions"])
    collision_data = Path(source_collision).read_bytes()
    collision = read_collision(collision_data)
    vertices = [row[:3] for cell in collision["cells"] for row in cell["vertices"]]
    if not vertices:
        raise ValueError("Custom navigation coverage needs source collision geometry")
    generation = hashlib.sha256(data + collision_data).digest()
    stamp = int.from_bytes(generation[:4], "little")
    nav = write_navw(closed_road(positions, stamp, road_type))
    grid = waypoint_grid(positions + vertices, stamp)
    oww = write_owwf(grid)
    validate_closed_road(read_navw(nav), positions)
    if read_owwf(oww) != grid:
        raise ValueError("Custom waypoint grid roundtrip disagrees")
    output.mkdir(parents=True)
    files = {"TopGearCPS_00.nav": nav, "TopGearCPS_00.oww": oww}
    for name, payload in files.items():
        (output / name).write_bytes(payload)
    validate_closed_road(read_navw((output / "TopGearCPS_00.nav").read_bytes()), positions)
    if read_owwf((output / "TopGearCPS_00.oww").read_bytes()) != grid:
        raise ValueError("Persisted custom waypoint grid differs from source")
    report = {
        "component": "native-first-test-open-world-navigation", "media_name": "TopGearCPS", "schema_verified": True,
        "engine_validated": False, "first_test_ready": False, "playable_map": False,
        "waypoint_count": len(positions), "road_count": 1, "closed_road": True,
        "source_waypoint_order_and_positions_verified": True,
        "ai_disabled": True, "traffic_disabled": True, "road_profile": "none_10000", "road_type": road_type,
        "waypoint_cell_count": len(grid["coordinates"]), "waypoint_cell_size": grid["cell_size"],
        "generation_stamp": stamp, "source_geometry_sha256": hashlib.sha256(data).hexdigest(),
        "source_collision_sha256": hashlib.sha256(collision_data).hexdigest(),
        "files": [{"name": name, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
                  for name, payload in files.items()],
        "remaining": [
            "This is a source-backed closed road graph, not a complete FH2 road/profile/traffic bake.",
            "Native disabled-road metadata uses none_10000, observed on stock disabled roads; profile resolution needs engine confirmation.",
            "The common NAVW/OWWF generation stamp is deterministic, not an invented MLP iCRC; runtime stamp selection needs confirmation.",
            "Grid bounds cover all collision vertices and route samples; native cell-query behavior still needs engine validation.",
            "Pack environment/locators, custom load/travel, arrival and safe return remain unfinished.",
        ],
    }
    (output / "openworld.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-ai", type=Path, required=True)
    parser.add_argument("--source-collision", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--road-type", default="none", choices=("none", "dirt", "b", "a", "freeway"),
                        help="nav road_type; HUD minimap profiles draw every value except none")
    args = parser.parse_args()
    try:
        report = build(args.source_ai, args.source_collision, args.output, args.road_type)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print("error: " + str(error), file=sys.stderr)
        return 1
    print("Verified native navigation: %d waypoints, %d cells; AI/traffic disabled; engine testing remains blocked." %
          (report["waypoint_count"], report["waypoint_cell_count"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
