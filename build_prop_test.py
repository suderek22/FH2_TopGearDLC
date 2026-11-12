"""Build a cone-free Top Gear test pack with native physics for remaining props."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import xml.etree.ElementTree as ET

from build_media import media_catalog
from fh2_pvs import read_root60
from fh2_world import MiniZip, Resource, read_pvs_zone60, write_minizip, write_pvs_zone60
from fm5_world import world_tables


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def filter_cone_objects(source_objects):
    root = ET.fromstring(source_objects)
    if root.tag != "CollObjs" or root.attrib:
        raise ValueError("Unsupported source collision-object catalog")
    retained, cone_rows = [], []
    seen = set()
    for row in root:
        if not re.fullmatch(r"Obj\d+", row.tag) or not row.get("PhysicsType"):
            raise ValueError("Unsupported source collision-object entry")
        if row.tag in seen:
            raise ValueError("Duplicate source collision-object index")
        seen.add(row.tag)
        if row.get("PhysicsType") == "Cone":
            cone_rows.append(row)
        else:
            clone = ET.fromstring(ET.tostring(row))
            clone.tag = "Obj%d" % len(retained)
            retained.append(clone)
    if not cone_rows:
        raise ValueError("Source collision-object catalog contains no cones")
    filtered = ET.Element("CollObjs")
    filtered.extend(retained)
    return (ET.tostring(filtered, encoding="utf-8", xml_declaration=True),
            cone_rows, [row.get("PhysicsType") for row in retained])


def locate_cone_draws(cone_rows, source_draws, tolerance=0.001):
    matches = {}
    for row in cone_rows:
        position = row.find("Pos")
        if position is None:
            raise ValueError("Cone placement has no position")
        target = tuple(float(position.get(axis)) for axis in ("x", "y", "z"))
        if not all(math.isfinite(value) for value in target):
            raise ValueError("Cone placement has a non-finite position")
        candidates = [draw for draw in source_draws
                      if math.dist(target, draw["position"]) <= tolerance]
        if len(candidates) != 1:
            raise ValueError("Cone placement does not identify exactly one world draw")
        draw = candidates[0]
        index = draw["instance_index"]
        if index in matches:
            raise ValueError("Cone placements resolve to a duplicate world draw")
        matches[index] = (draw["model_id"], tuple(draw["position"]))
    return matches


def all_cone_draws(cone_rows, source_draws, retained_objects):
    registered = locate_cone_draws(cone_rows, source_draws)
    models = {model for model, _ in registered.values()}
    for row in ET.fromstring(retained_objects):
        position = row.find("Pos")
        if position is None:
            raise ValueError("Retained prop has no position")
        target = tuple(float(position.get(axis)) for axis in ("x", "y", "z"))
        if any(draw["model_id"] in models and math.dist(target, draw["position"]) <= 0.001
               for draw in source_draws):
            raise ValueError("Cone model is shared by a retained collision prop")
    return {
        draw["instance_index"]: (draw["model_id"], tuple(draw["position"]))
        for draw in source_draws if draw["model_id"] in models
    }


def filter_zone_draws(zone, cone_instances):
    draws = zone["draws"]
    if len(set(draws)) != len(draws):
        raise ValueError("Zone contains duplicate draw references")
    removed = set(draws) & set(cone_instances)
    if removed != set(cone_instances):
        raise ValueError("Zone does not reference every cone placement")
    result = dict(zone)
    result["draws"] = [index for index in draws if index not in cone_instances]
    return result


def atomic_write(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + ".building")
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _link_or_copy(source, destination):
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def _media_file(report, suffix):
    suffix = suffix.replace("/", "\\").casefold()
    matches = [row for row in report["files"]
               if row["path"].replace("/", "\\").casefold().endswith(suffix)]
    if len(matches) != 1:
        raise ValueError("Expected one media file ending in " + suffix)
    return matches[0]


def _copy_pack(source, output, media_root):
    source = source.resolve()
    media_root = media_root.resolve()

    def ignore(directory, names):
        current = Path(directory).resolve()
        if current == source:
            return [name for name in names if name.startswith("shadow-profile-")]
        if current == media_root:
            return ["GeoChunk0.minizip"] if "GeoChunk0.minizip" in names else []
        return []

    shutil.copytree(source, output, copy_function=_link_or_copy, ignore=ignore)


def _update_track_catalog(track_ini, catalog_sha256):
    data = Path(track_ini).read_text(encoding="utf-8")
    updated, count = re.subn(
        r"(?mi)^(\s*CatalogSha256\s*=\s*)[0-9a-f]{64}\s*$",
        lambda match: match.group(1) + catalog_sha256,
        data,
    )
    if count != 1:
        raise ValueError("Track metadata does not contain one media catalog checksum")
    atomic_write(track_ini, updated.encode("utf-8"))


def build(source_track, source_objects, source_pvs, physics_definitions, output):
    source_track = Path(source_track).resolve()
    source_objects = Path(source_objects).resolve()
    source_pvs = Path(source_pvs).resolve()
    physics_definitions = Path(physics_definitions).resolve()
    output = Path(output).resolve()
    source_media = source_track / "media"
    if not source_track.is_dir() or output.exists():
        raise ValueError("Source pack must exist and output must be new")
    if source_track == output or source_track in output.parents or output in source_track.parents:
        raise ValueError("Source and output pack directories must be separate")

    report_path = source_media / "media.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    catalog = source_media / report["catalog_name"]
    if digest(catalog) != report["catalog_sha256"]:
        raise ValueError("Source media catalog checksum mismatch")
    track_ini = source_track / "track.ini"
    ini_text = track_ini.read_text(encoding="utf-8")
    ini_catalog = re.search(r"(?mi)^\s*CatalogSha256\s*=\s*([0-9a-f]{64})\s*$", ini_text)
    if ini_catalog is None or ini_catalog.group(1) != report["catalog_sha256"]:
        raise ValueError("Track metadata does not match the source media catalog")
    static = report.get("static_test")
    if (not static or static.get("profile") != "static-circuit-first-test" or
            static.get("dynamic_physics_definition_count") != 0 or
            static.get("dynamic_collision_object_count") != 0):
        raise ValueError("Source pack is not the expected static-only test profile")
    if digest(source_objects) != static["source_objects_sha256"]:
        raise ValueError("Source object catalog differs from the accepted Top Gear catalog")

    filtered_objects, cone_rows, retained_types = filter_cone_objects(source_objects.read_bytes())
    source_world = world_tables(source_pvs)
    source_draws = source_world["primary_draws"] + source_world["secondary_draws"]
    cone_draws = all_cone_draws(cone_rows, source_draws, filtered_objects)

    physics_data = physics_definitions.read_bytes()
    if len(physics_data) < 8:
        raise ValueError("Native physics definitions are truncated")
    definition_count = struct.unpack_from("<Q", physics_data)[0]
    if definition_count == 0:
        raise ValueError("Native physics definitions contain no definitions")

    relative = Path(report["override_relative_path"])
    source_root = source_media / relative
    root_relative = Path("ribbon_00") / (report["media_name"] + "_00.pvs")
    root = read_root60((source_root / root_relative).read_bytes())
    if root["secondary_draws"]:
        raise ValueError("Cone-free test currently requires promoted primary draws")
    root_draws = {}
    for draw in root["primary_draws"]:
        source_index = draw["source_instance_index"]
        if source_index in root_draws:
            raise ValueError("Converted world has duplicate source draw indices")
        root_draws[source_index] = draw
    if len(root_draws) != len(source_draws):
        raise ValueError("Converted world/source draw counts differ")
    for index, (model, position) in cone_draws.items():
        draw = root_draws.get(index)
        if (draw is None or draw["model_id"] != model or
                math.dist(position, draw["position"]) > 0.001):
            raise ValueError("Cone source placement does not match the converted world")

    chunk_map = source_root / "ChunkMap0.dat"
    chunk_keys = [row[0] for row in struct.iter_unpack("<I", chunk_map.read_bytes())]
    archive = MiniZip(source_root / "GeoChunk0.minizip")
    if len(chunk_keys) != len(archive.entries) or (source_root / "ChunkMap1.dat").exists():
        raise ValueError("Expected a valid single-chunk media archive")
    expected_zones = {0x0C000000 | index for index in range(root["zone_count"])}
    zone_indexes = {key: index for index, key in enumerate(chunk_keys) if key >> 24 == 12}
    if set(zone_indexes) != expected_zones:
        raise ValueError("Source media zone set differs from the converted root")
    updated_zones, removed_draws = {}, 0
    for key, index in zone_indexes.items():
        zone = read_pvs_zone60(archive.read(index))
        updated = filter_zone_draws(zone, cone_draws)
        removed_draws += len(zone["draws"]) - len(updated["draws"])
        updated_zones[key] = write_pvs_zone60(updated)
    if removed_draws != len(cone_draws) * len(zone_indexes):
        raise ValueError("Not every cone draw was hidden in every world zone")

    media_file_rows = {
        "objects": _media_file(report, "ribbon_00\\CollObjs.xml"),
        "definitions": _media_file(report, "PhysicsDefinitions.bin"),
        "world": _media_file(report, "GeoChunk0.minizip"),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    media_source_root = source_media / relative
    _copy_pack(source_track, output, media_source_root)
    output_media = output / "media"
    output_root = output_media / relative
    output_archive = output_root / "GeoChunk0.minizip"
    resources = [Resource(str(index), key & 0xFFFFFF, key >> 24)
                 for index, key in enumerate(chunk_keys)]
    lookup = write_minizip(
        output_archive, resources,
        lambda resource: updated_zones[chunk_keys[int(resource.name)]]
        if chunk_keys[int(resource.name)] in updated_zones else archive.read(int(resource.name)))
    if lookup != chunk_map.read_bytes():
        raise ValueError("Rebuilt media archive lookup differs from its source")

    objects_path = output_media / media_file_rows["objects"]["path"]
    definitions_path = output_media / media_file_rows["definitions"]["path"]
    atomic_write(objects_path, filtered_objects)
    atomic_write(definitions_path, physics_data)

    for row in report["files"]:
        if row["path"] in {media_file_rows["objects"]["path"],
                           media_file_rows["definitions"]["path"],
                           media_file_rows["world"]["path"]}:
            path = output_media / row["path"]
            row["bytes"], row["sha256"] = path.stat().st_size, digest(path)
    report.pop("static_test", None)
    report["static_test_companions_present"] = False
    report["dynamic_prop_companions_present"] = True
    report["cone_free_destructible_props_test"] = {
        "profile": "cone-free-native-physics-test",
        "omitted_cone_objects": len(cone_rows),
        "omitted_cone_world_draws": len(cone_draws),
        "omitted_visual_only_cone_draws": len(cone_draws) - len(cone_rows),
        "cone_model_ids": sorted({model for model, _ in cone_draws.values()}),
        "omitted_cone_zone_draw_references": removed_draws,
        "retained_dynamic_collision_objects": len(retained_types),
        "retained_physics_types": sorted(set(retained_types)),
        "native_physics_definition_count": definition_count,
        "native_physics_definitions_sha256": digest(physics_definitions),
        "source_objects_sha256": digest(source_objects),
        "source_world_sha256": source_world["source_sha256"],
        "engine_validated": False,
        "destruction_validated": False,
    }
    report["remaining"] = [
        item for item in report.get("remaining", [])
        if "movable-prop physics is intentionally disabled" not in item and
        "empty dynamic catalog" not in item
    ]
    report["remaining"].append(
        "Native non-cone prop collision/destruction behavior still requires in-game validation.")
    if "map_wide_visibility" in report:
        report["map_wide_visibility"]["draws"] = len(root_draws) - len(cone_draws)
    catalog_path = output_media / report["catalog_name"]
    atomic_write(catalog_path, media_catalog(report["media_name"], report["files"]))
    report["catalog_sha256"] = digest(catalog_path)
    atomic_write(output_media / "media.json",
                 (json.dumps(report, indent=2) + "\n").encode("utf-8"))
    _update_track_catalog(output / "track.ini", report["catalog_sha256"])

    rebuilt = MiniZip(output_archive)
    for key, index in zone_indexes.items():
        zone = read_pvs_zone60(rebuilt.read(index))
        if set(zone["draws"]) & set(cone_draws):
            raise ValueError("Cone draw remains visible in a rebuilt world zone")
    objects_check = ET.fromstring(objects_path.read_bytes())
    if any(row.get("PhysicsType") == "Cone" for row in objects_check):
        raise ValueError("Cone remains in the dynamic collision-object catalog")
    if len(objects_check) != len(retained_types):
        raise ValueError("Retained collision-object count differs")
    if digest(catalog_path) != report["catalog_sha256"]:
        raise ValueError("Rebuilt catalog checksum mismatch")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-track", type=Path, required=True)
    parser.add_argument("--source-objects", type=Path, required=True)
    parser.add_argument("--source-pvs", type=Path, required=True)
    parser.add_argument("--physics-definitions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = build(args.source_track, args.source_objects, args.source_pvs,
                       args.physics_definitions, args.output)
    except (OSError, ValueError, KeyError, ET.ParseError) as error:
        parser.error(str(error))
    print("Cone-free test pack built: %d cone world draws removed, %d movable props retained." %
          (report["cone_free_destructible_props_test"]["omitted_cone_world_draws"],
           report["cone_free_destructible_props_test"]["retained_dynamic_collision_objects"]))


if __name__ == "__main__":
    main()
