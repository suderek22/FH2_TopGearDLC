"""Stage verified native world components without installing or enabling a map."""
import argparse
import hashlib
import json
import math
from pathlib import Path, PureWindowsPath
import re
import shutil
import struct
import sys
import zipfile
import zlib
import xml.etree.ElementTree as ET

from build_bindings import validate_bindings
from build_environment import collision_arrival, locators, validate_static_companions
from collision import read_physics
from fh2_pvs import read_root60
from fh2_world import MiniZip, read_pvs_zone60
from shader_scenes import program_members, shader_scenes
from openworld_navigation import read_navw, read_owwf, validate_closed_road
from spatial import read_hex


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


NATIVE_DATA_OFFSET = 0x1123


def write_native_bin_zip(directory, names, path):
    """Pack bin\\ like stock FH2 tracks (media\\tracks\\Anthem\\bin.zip): deflated members, local headers
    without extras, and a central 0x1123 extra holding each member's compressed-data offset."""
    central = []
    with Path(path).open("xb") as out:
        for name in names:
            data = (Path(directory) / Path(PureWindowsPath(name))).read_bytes()
            compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
            packed = compressor.compress(data) + compressor.flush()
            encoded = name.replace("\\", "/").encode("ascii")
            crc, offset = zlib.crc32(data), out.tell()
            if offset + 30 + len(encoded) + len(packed) > 0xFFFFFFFF:
                raise ValueError("Native bin archive exceeds the 4 GiB zip limit")
            out.write(struct.pack("<IHHHHHIIIHH", 0x04034B50, 10, 0, 8, 0, 0, crc, len(packed), len(data),
                                  len(encoded), 0) + encoded)
            data_offset = out.tell()
            out.write(packed)
            central.append(struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 10, 10, 0, 8, 0, 0, crc, len(packed),
                                       len(data), len(encoded), 8, 0, 0, 0, 0, offset) + encoded +
                           struct.pack("<HHI", NATIVE_DATA_OFFSET, 4, data_offset))
        start, blob = out.tell(), b"".join(central)
        if len(central) > 0xFFFF:
            raise ValueError("Native bin archive has too many members")
        out.write(blob)
        out.write(struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(central), len(central), len(blob), start, 0))


def verify_native_bin_zip(path, expected):
    """Check members/digests and that every 0x1123 extra points at the member's compressed data."""
    with zipfile.ZipFile(path) as archive, Path(path).open("rb") as raw:
        rows = archive.infolist()
        if [row.filename for row in rows] != [name.replace("\\", "/") for name, _ in expected]:
            raise ValueError("Native bin archive member order/set differs")
        for row, (_, sha256) in zip(rows, expected):
            tag, size, data_offset = struct.unpack("<HHI", row.extra)
            raw.seek(row.header_offset)
            header = struct.unpack("<IHHHHHIIIHH", raw.read(30))
            if (tag, size, row.compress_type, header[10]) != (NATIVE_DATA_OFFSET, 4, zipfile.ZIP_DEFLATED, 0) or \
                    data_offset != row.header_offset + 30 + header[9]:
                raise ValueError("Native bin archive layout differs from stock FH2 bin.zip")
            if hashlib.sha256(archive.read(row)).hexdigest() != sha256:
                raise ValueError("Native bin archive member differs: " + row.filename)


def media_catalog(media_name, inventory):
    lines = ["TLCPS-MEDIA-1\t" + media_name]
    prefix = "tracks\\" + media_name + "\\"
    seen = set()
    for row in inventory:
        name = row["media_path"]
        parts = PureWindowsPath(name).parts
        if (not name.startswith(prefix) or not name.isascii() or
                any(not re.fullmatch(r"[A-Za-z0-9_.# -]+", part) or part in {".", ".."} or
                    part.endswith((".", " ")) for part in parts) or name.casefold() in seen or
                not 0 < row["bytes"] <= 2147483647 or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"])):
            raise ValueError("Unsupported native media catalog entry")
        seen.add(name.casefold())
        lines.append("%s\t%d\t%s" % (row["sha256"], row["bytes"], name))
    if not 0 < len(inventory) <= 4096:
        raise ValueError("Native media catalog requires 1..4096 entries")
    return ("\n".join(lines) + "\n").encode("ascii")


def checked_path(directory, name, expected):
    path = directory / name
    if digest(path) != expected:
        raise ValueError("Component checksum mismatch: " + str(path))
    return path


def chunk_keys(directory, index):
    data = (directory / ("ChunkMap%d.dat" % index)).read_bytes()
    if len(data) % 4:
        raise ValueError("Invalid native chunk map length")
    keys = [row[0] for row in struct.iter_unpack("<I", data)]
    if len(set(keys)) != len(keys):
        raise ValueError("Duplicate native chunk resource key")
    archive = MiniZip(directory / ("GeoChunk%d.minizip" % index))
    if len(archive.entries) != len(keys):
        raise ValueError("Chunk map/archive counts disagree")
    return keys, archive


def validate_media(directory, media_name, arrival_report=None):
    root = read_root60((directory / "ribbon_00" / (media_name + "_00.pvs")).read_bytes())
    grid = read_hex((directory / "ribbon_00" / (media_name + "out_00.hex")).read_bytes())
    count = root["zone_count"]
    if count != len(grid["coordinates"]) or root["secondary_draws"]:
        raise ValueError("Native spatial/root placement configuration disagrees")
    maps = [chunk_keys(directory, index) for index in range(3)]
    geometry, physics = set(maps[0][0]), set(maps[2][0])
    if maps[1][0] != [0x0C000000 | index for index in range(count)]:
        raise ValueError("Native spatial zone resource IDs disagree")
    lookup_data = (directory / "PVSZLookup.dat").read_bytes()
    if len(lookup_data) != count * 4:
        raise ValueError("Native spatial lookup count disagrees")
    lookup = [row[0] for row in struct.iter_unpack("<I", lookup_data)]
    physics_count = len(root["root_groups"])
    if physics != {0x01000000 | index for index in range(physics_count)}:
        raise ValueError("Native physics templates are not densely bound")
    collision_points = []
    collision_cells = []
    for index in range(len(maps[2][0])):
        collision = read_physics(maps[2][1].read(index))
        collision_points.extend(row[:3] for row in collision["vertices"])
        if arrival_report is not None:
            collision_cells.append({"index": index, "vertices": collision["vertices"],
                                    "triangles": [{"vertices": row[2:5], "flags": (row[0] << 24) | (row[1] << 16)}
                                                  for row in collision["triangles"]]})
    if arrival_report is not None:
        locator_rows = locators((directory / "ribbon_00" / "Locators.xml").read_bytes())
        selected = [row for row in locator_rows if row["name"] == arrival_report["locator"]]
        if len(selected) != 1:
            raise ValueError("Staged arrival locator is missing or ambiguous")
        expected = collision_arrival({"cells": collision_cells}, selected[0])
        if any(arrival_report.get(key) != value for key, value in expected.items()):
            raise ValueError("Arrival metadata disagrees with staged collision/locator")
    definitions = directory / "PhysicsDefinitions.bin"
    objects = directory / "ribbon_00" / "CollObjs.xml"
    static_test = definitions.exists() or objects.exists()
    if static_test:
        validate_static_companions(definitions.read_bytes(), objects.read_bytes())
    if any(struct.unpack_from("<i", bytes.fromhex(row["record"]), 96)[0] != index
           for index, row in enumerate(root["root_groups"])):
        raise ValueError("Native collision root/template ordinals disagree")
    nodes = [{"index": index, "key": 0x0C000000 | index} for index in range(count)]
    nodes += [{"index": count + index, "key": 0x01000000 | index} for index in range(physics_count)]
    zones = [read_pvs_zone60(maps[1][1].read(index)) for index in range(count)]
    validate_bindings(zones, lookup, nodes, physics_count)
    visible = set()
    for zone in zones:
        for index in zone["draws"]:
            if not 0 <= index < len(root["primary_draws"]):
                raise ValueError("Native spatial draw ordinal is out of bounds")
            model = root["primary_draws"][index]["model_id"]
            if model not in zone["models"]:
                raise ValueError("Native zone omits a visible model")
        if any((0x04000000 | model) not in geometry for model in zone["models"]):
            raise ValueError("Native zone requires a missing model resource")
        for texture, quality in zip(zone["textures"], zone["qualities"]):
            if not 0 <= texture < len(root["textures"]) or quality & 0xFF != 1:
                raise ValueError("Invalid native zone texture request")
            level = quality >> 8
            if level and (0x05000000 | (texture << 4) | (level - 1)) not in geometry:
                raise ValueError("Native zone requires a missing streamed texture")
        visible.update(zone["draws"])
    if visible != set(range(len(root["primary_draws"]))):
        raise ValueError("Native media visibility omits a placement")
    if root["header_fields"][1] < len(nodes):
        raise ValueError("Native root runtime-node capacity is insufficient")
    assignments = (directory / "ChunkAssign.dat").read_bytes()
    if assignments != struct.pack("<2I", 100, count) + struct.pack("<%dI" % count, *([1] * count)):
        raise ValueError("Native chunk assignments disagree")
    names = [str(path.relative_to(directory / "bin")) for path in (directory / "bin").rglob("*")
             if path.is_file()]
    for shader in root["shaders"]:
        prefix = "game:\\media\\tracks\\" + media_name + "\\bin\\"
        if not shader["path"].casefold().startswith(prefix.casefold()):
            raise ValueError("Native shader escapes the map namespace")
        name = shader["path"][len(prefix):]
        data = (directory / "bin" / Path(name)).read_bytes()
        program_members(name, shader_scenes(data), names)
    for material in root["materials"]:
        prefix = "game:\\media\\tracks\\" + media_name + "\\bin\\"
        if not material["path"].casefold().startswith(prefix.casefold()):
            raise ValueError("Native material escapes the map namespace")
        if not (directory / "bin" / material["path"][len(prefix):]).is_file():
            raise ValueError("Native material resource is missing")
    if not (directory / "bin" / (media_name + ".stx.bin")).is_file():
        raise ValueError("Native resident texture store is missing")
    result = {"placement_count": len(visible), "zone_count": count, "physics_count": physics_count,
              "runtime_node_count": len(nodes), "navigation_present": False,
              "static_test_companions_present": static_test}
    nav_path = directory / "ribbon_00" / (media_name + "_00.nav")
    oww_path = directory / "ribbon_00" / (media_name + "_00.oww")
    if nav_path.exists() or oww_path.exists():
        nav, oww = read_navw(nav_path.read_bytes()), read_owwf(oww_path.read_bytes())
        if nav["stamp"] != oww["stamp"]:
            raise ValueError("Native navigation/waypoint generation stamps disagree")
        data = bytes.fromhex(nav["tables"][0]["data"])
        positions = [list(struct.unpack_from("<3f", data, index * 40 + 20)) for index in range(nav["counts"][0])]
        validate_closed_road(nav, positions)
        cells = {tuple(row) for row in oww["coordinates"]}
        if any((math.floor(row[0] / oww["cell_size"]), math.floor(row[2] / oww["cell_size"])) not in cells
               for row in positions + collision_points):
            raise ValueError("Native waypoint grid does not cover navigation/collision geometry")
        result.update(navigation_present=True, navigation_waypoint_count=len(positions), waypoint_cell_count=len(cells))
    return result


def build(component, output, navigation=None, environment=None, mediaoverride=False, native_bin_zip=False):
    component, output = Path(component).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a new staged media component directory: " + str(output))
    report = json.loads((component / "root.json").read_text(encoding="utf-8"))
    media_name = report["media_name"]
    if (not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,47}", media_name) or
            report["root_name"] != media_name + "_00.pvs" or
            not report["schema_verified"] or not report["all_source_placements_requested"]):
        raise ValueError("A complete schema-verified placement root is required")
    geometry, physics = Path(report["geometry_component"]), Path(report["physics_component"])
    geometry_report = json.loads((geometry / "geometry.json").read_text(encoding="utf-8"))
    physics_report = json.loads((physics / "physics.json").read_text(encoding="utf-8"))
    if not geometry_report["verified"] or not physics_report["schema_verified"]:
        raise ValueError("Unverified geometry/physics component")
    files = {}
    for parent, name, expected, target in (
        (geometry, "GeoChunk0.minizip", geometry_report["chunk_sha256"], "GeoChunk0.minizip"),
        (geometry, "ChunkMap0.dat", geometry_report["lookup_sha256"], "ChunkMap0.dat"),
        (physics, "GeoChunk2.minizip", physics_report["chunk_sha256"], "GeoChunk2.minizip"),
        (physics, "ChunkMap2.dat", physics_report["lookup_sha256"], "ChunkMap2.dat"),
        (component, report["root_name"], report["root_sha256"], "ribbon_00\\" + report["root_name"]),
        (component, media_name + "out_00.hex", report["hex_sha256"], "ribbon_00\\" + media_name + "out_00.hex"),
        (component, "GeoChunk1.minizip", report["zone_chunk_sha256"], "GeoChunk1.minizip"),
        (component, "ChunkMap1.dat", report["zone_chunk_map_sha256"], "ChunkMap1.dat"),
        (component, "PVSZLookup.dat", report["pvs_lookup_sha256"], "PVSZLookup.dat"),
        (component, "ChunkAssign.dat", report["chunk_assignment_sha256"], "ChunkAssign.dat"),
    ):
        files[target] = checked_path(parent, name, expected)
    support = checked_path(component, "bin.zip", report["support_archive_sha256"])
    navigation_report = None
    if navigation is not None:
        navigation = Path(navigation).resolve()
        navigation_report = json.loads((navigation / "openworld.json").read_text(encoding="utf-8"))
        if (navigation_report["component"] != "native-first-test-open-world-navigation" or
                navigation_report["media_name"] != media_name or not navigation_report["schema_verified"] or
                navigation_report["source_collision_sha256"] != physics_report["source_sha256"] or
                not navigation_report["ai_disabled"] or not navigation_report["traffic_disabled"]):
            raise ValueError("Native navigation provenance/configuration disagrees with the map")
        expected_names = {media_name + "_00.nav", media_name + "_00.oww"}
        if {row["name"] for row in navigation_report["files"]} != expected_names:
            raise ValueError("Native navigation component member set disagrees")
        for row in navigation_report["files"]:
            files["ribbon_00\\" + row["name"]] = checked_path(navigation, row["name"], row["sha256"])
    environment_report = None
    if environment is not None:
        environment = Path(environment).resolve()
        environment_report = json.loads((environment / "environment.json").read_text(encoding="utf-8"))
        if (environment_report["component"] != "native-settings-locators-and-arrival" or
                environment_report["media_name"] != media_name or not environment_report["schema_verified"] or
                environment_report["world_source_sha256"] != report["source_sha256"] or
                environment_report["source_collision_sha256"] != physics_report["source_sha256"] or
                not environment_report["arrival"]["source_persisted_ground_equal"]):
            raise ValueError("Environment provenance/arrival disagrees with native map components")
        expected_names = {"TrackSettings.xml", "ribbon_00\\Locators.xml", "smoke.dds", "staticcarcubemap.xpr"}
        if "static_test" in environment_report:
            static = environment_report["static_test"]
            if (static["profile"] != "static-circuit-first-test" or
                    static["dynamic_physics_definition_count"] != 0 or
                    static["dynamic_collision_object_count"] != 0 or not static["road_collision_preserved"]):
                raise ValueError("Unsupported static test profile")
            expected_names.update({"PhysicsDefinitions.bin", "ribbon_00\\CollObjs.xml"})
        names = [row["name"] for row in environment_report["files"]]
        if len(names) != len(expected_names) or set(names) != expected_names:
            raise ValueError("Environment component member set disagrees")
        for row in environment_report["files"]:
            files[row["name"]] = checked_path(environment, row["name"], row["sha256"])
        if "static_test" in environment_report:
            validate_static_companions(files["PhysicsDefinitions.bin"].read_bytes(),
                                       files["ribbon_00\\CollObjs.xml"].read_bytes())
        settings = ET.fromstring(files["TrackSettings.xml"].read_bytes())
        if settings.tag != "TrackSettings" or settings.attrib != {"Version": "83"}:
            raise ValueError("Staged environment settings are not native v83")
        rows = locators(files["ribbon_00\\Locators.xml"].read_bytes())
        selected = [row for row in rows if row["name"] == environment_report["arrival"]["locator"]]
        if len(selected) != 1 or selected[0]["transform"] != environment_report["arrival"]["locator_transform"]:
            raise ValueError("Environment arrival does not bind a persisted source locator")
    layout = "mediaoverride" if mediaoverride else "media"
    media = output / layout / "tracks" / media_name
    with zipfile.ZipFile(support) as archive:
        names = archive.namelist()
        if len({str(PureWindowsPath(name)).casefold() for name in names}) != len(names):
            raise ValueError("Duplicate support archive asset name")
        for name in names:
            path = PureWindowsPath(name)
            if path.is_absolute() or ".." in path.parts or ":" in name or name.endswith(("/", "\\")):
                raise ValueError("Unsafe support archive asset name")
        for target, source in files.items():
            path = media / target
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, path)
        for name in names:
            path = media / "bin" / Path(PureWindowsPath(name))
            path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as source, path.open("xb") as target:
                shutil.copyfileobj(source, target)
    metrics = validate_media(media, media_name, environment_report["arrival"] if environment_report else None)
    support_digests = {str(Path("bin") / Path(PureWindowsPath(row["name"]))): row["sha256"]
                       for row in report["support_assets"]}
    if native_bin_zip:
        members = [(name, support_digests[str(Path("bin") / Path(PureWindowsPath(name)))]) for name in names]
        write_native_bin_zip(media / "bin", names, media / "bin.zip")
        verify_native_bin_zip(media / "bin.zip", members)
        shutil.rmtree(media / "bin")
        support_digests = {"bin.zip": digest(media / "bin.zip")}
    inventory = []
    for path in sorted(media.rglob("*")):
        if path.is_file():
            relative = str(path.relative_to(media))
            media_path = str(PureWindowsPath("tracks") / media_name / relative)
            inventory.append({"path": str(path.relative_to(output)), "bytes": path.stat().st_size,
                              "sha256": digest(path), "media_path": media_path,
                              "game_media_path": "game:\\media\\" + media_path,
                              "override_relative_path": str(PureWindowsPath("mediaoverride") / media_path)})
    expected = {str(Path(target)): digest(source) for target, source in files.items()}
    expected.update(support_digests)
    actual = {str(Path(row["path"]).relative_to(Path(layout) / "tracks" / media_name)): row["sha256"]
              for row in inventory}
    if actual != expected:
        raise ValueError("Persisted media inventory differs from verified components")
    result = {"component": "staged-native-world-media", "media_name": media_name,
              "staging_layout": layout, "game_media_path": "game:\\media\\tracks\\" + media_name,
              "bin_layout": "native-bin-zip" if native_bin_zip else "unpacked-override",
              "override_relative_path": "mediaoverride\\tracks\\" + media_name,
              "file_mount_enabled": False, "registration_enabled": False,
              "native_resource_closure_verified": True, "first_test_ready": False,
              "engine_validated": False, "playable_map": False, **metrics, "files": inventory,
              "remaining": report["remaining"] + [
                  "TrackSettings, native navigation/integrity, locators and environment companion resources remain to be integrated.",
                  "No database registration, file override, custom travel or game installation is enabled.",
              ]}
    if navigation_report:
        result["navigation_component"] = str(navigation)
        result["remaining"] += navigation_report["remaining"]
        result["remaining"].remove(
            "TrackSettings, native navigation/integrity, locators and environment companion resources remain to be integrated.")
        result["remaining"].append("TrackSettings, locators and environment companion resources remain to be integrated.")
    if environment_report:
        result["environment_component"] = str(environment)
        result["arrival"] = environment_report["arrival"]
        result["settings_and_locators_present"] = True
        if "static_test" in environment_report:
            result["static_test"] = environment_report["static_test"]
        result["remaining"] += environment_report["remaining"]
        if navigation_report:
            result["remaining"].remove("TrackSettings, locators and environment companion resources remain to be integrated.")
        else:
            result["remaining"].remove(
                "TrackSettings, native navigation/integrity, locators and environment companion resources remain to be integrated.")
            result["remaining"].append("Native navigation/integrity still needs integration.")
    catalog = media_catalog(media_name, inventory)
    (output / "media.catalog").write_bytes(catalog)
    result["catalog_sha256"] = hashlib.sha256(catalog).hexdigest()
    result["catalog_name"] = "media.catalog"
    (output / "media.json").write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--navigation", type=Path)
    parser.add_argument("--environment", type=Path)
    parser.add_argument("--mediaoverride", action="store_true",
                        help="Stage the Extra Options Mount-relative mediaoverride tree; never modify the game")
    parser.add_argument("--native-bin-zip", action="store_true",
                        help="Ship bin\\ as a stock-format bin.zip (required for TrackLoaderCPS Mount\\media publication)")
    args = parser.parse_args()
    try:
        report = build(args.root, args.output, args.navigation, args.environment, args.mediaoverride,
                       args.native_bin_zip)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, ET.ParseError) as error:
        print("error: " + str(error), file=sys.stderr)
        return 1
    print("Staged and verified %d native media files; first in-game test remains blocked." % len(report["files"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
