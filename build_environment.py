"""Compile schema-checked track settings, locators and source-backed arrival data."""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

from build_bindings import verified_bytes, verify_archive
from collision import ground_hits, read_collision, read_physics
from fm5_world import world_tables


def static_test_companions(source_objects):
    root = ET.fromstring(source_objects)
    if root.tag != "CollObjs" or root.attrib:
        raise ValueError("Unsupported source collision-object catalog")
    names = []
    for row in root:
        if not re.fullmatch(r"Obj\d+", row.tag) or "PhysicsType" not in row.attrib:
            raise ValueError("Unsupported source collision-object entry")
        names.append(row.attrib["PhysicsType"])
    # The native binary reader serializes NumDefinitions as uint64, not uint32.
    files = {"PhysicsDefinitions.bin": bytes(8),
             "ribbon_00\\CollObjs.xml": ET.tostring(ET.Element("CollObjs"), encoding="utf-8",
                                                  xml_declaration=True)}
    return files, {"profile": "static-circuit-first-test", "dynamic_physics_definition_count": 0,
                   "dynamic_collision_object_count": 0, "omitted_source_object_count": len(names),
                   "omitted_source_physics_types": sorted(set(names)),
                   "source_objects_sha256": hashlib.sha256(source_objects).hexdigest(),
                   "road_collision_preserved": True, "engine_validated": False}


def validate_static_companions(definitions, objects):
    if definitions != bytes(8):
        raise ValueError("Static test requires the native uint64 zero-definition catalog")
    root = ET.fromstring(objects)
    if root.tag != "CollObjs" or root.attrib or len(root) or (root.text and root.text.strip()):
        raise ValueError("Static test must not register dynamic collision objects")


def shape(element):
    return (element.tag, tuple(sorted(element.attrib)), element.get("Version"),
            tuple(shape(child) for child in element))


def track_settings(source_data, native_data):
    source, native = ET.fromstring(source_data), ET.fromstring(native_data)
    if (source.tag != "TrackSettings" or source.attrib != {"Version": "81"} or
            native.tag != "TrackSettings" or native.attrib != {"Version": "83"}):
        raise ValueError("Expected FM5 v81 and FH2 v83 track settings schemas")
    for root in (source, native):
        repeated = {child.tag for child in root if sum(row.tag == child.tag for row in root) > 1}
        if repeated - {"bloomPersist"}:
            raise ValueError("Unreviewed repeated track settings field")
        for element in root.iter():
            if (element.text and element.text.strip()) or (element.tail and element.tail.strip()):
                raise ValueError("Unexpected track settings text payload")
            if any("game:" in value.casefold() or "\\" in value or "/" in value
                   for value in element.attrib.values()):
                raise ValueError("Track settings contain an unresolved asset path")
            for value in element.attrib.values():
                if value.casefold() in {"nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"}:
                    raise ValueError("Non-finite track settings value")
    source_fields = {}
    for child in source:
        source_fields.setdefault(child.tag, []).append(child)
    if set(source_fields) - {child.tag for child in native}:
        raise ValueError("Unsupported source track settings field")
    result = ET.Element("TrackSettings", {"Version": "83"})
    copied, defaults = [], []
    for native_field in native:
        name = native_field.tag
        fields = source_fields.get(name, [])
        source_field = fields.pop(0) if fields else None
        if source_field is None:
            if name != "screenAreaDistances":
                raise ValueError("Unreviewed FH2-only track settings field: " + name)
            result.append(copy.deepcopy(native_field))
            defaults.append(name)
        elif name == "FileChunkSize":
            if (source_field.attrib.keys() != {"value"} or native_field.attrib.keys() != {"value", "limit"} or
                    len(source_field) or len(native_field)):
                raise ValueError("Unexpected file chunk size schema")
            size, limit = int(source_field.get("value")), int(native_field.get("limit"))
            if not 1 <= size <= limit:
                raise ValueError("Source file chunk size exceeds native limit")
            result.append(ET.Element(name, {"value": str(size), "limit": str(limit)}))
        else:
            if shape(source_field) != shape(native_field):
                raise ValueError("Source/native track settings schema differs: " + name)
            result.append(copy.deepcopy(source_field))
            copied.append(name)
    if any(source_fields.values()):
        raise ValueError("Source settings have excess repeated fields")
    data = ET.tostring(result, encoding="utf-8", xml_declaration=True)
    if shape(ET.fromstring(data)) != shape(native):
        raise ValueError("Converted track settings do not match the native schema")
    return data, {"preserved_source_fields": copied, "native_default_fields": defaults,
                  "adapted_fields": ["FileChunkSize"], "source_version": 81, "target_version": 83}


def apply_lighting_fog(data, ambient_scale=1.0, direct_scale=1.0,
                       fog_density_scale=1.0, fog_distance_scale=1.0):
    scales = {
        "ambient_scale": ambient_scale,
        "direct_scale": direct_scale,
        "fog_density_scale": fog_density_scale,
        "fog_distance_scale": fog_distance_scale,
    }
    for name, value in scales.items():
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                not math.isfinite(value) or value < 0 or value > 4):
            raise ValueError("Invalid lighting/fog scale: " + name)

    if all(value == 1.0 for value in scales.values()):
        root = ET.fromstring(data)
        fog = root.find("Fog")
        active = []
        if fog is not None:
            active = [settings.tag for settings in fog
                      if re.fullmatch(r"Settings\d+", settings.tag) and
                      settings.find("Enabled") is not None and
                      settings.find("Enabled").get("value") == "1"]
        return data, {
            "ambient_scale": 1.0, "direct_scale": 1.0,
            "fog_far_density_scale": 1.0, "fog_far_distance_scale": 1.0,
            "active_fog_settings": active,
        }

    root = ET.fromstring(data)
    lighting = (
        (ambient_scale, "LightAmbient", ("r", "g", "b")),
        (direct_scale, "SHLighting", ("SHDirRed", "SHDirGreen", "SHDirBlue")),
    )
    for scale, element_name, attributes in lighting:
        if scale == 1.0:
            continue
        element = root.find(element_name)
        if element is None:
            raise ValueError("Track settings lack lighting controls: " + element_name)
        for attribute in attributes:
            try:
                value = float(element.attrib[attribute])
            except (KeyError, ValueError) as error:
                raise ValueError("Invalid lighting value: " + attribute) from error
            if not math.isfinite(value) or value < 0:
                raise ValueError("Invalid lighting value: " + attribute)
            element.set(attribute, format(value * scale, ".6f"))

    fog = root.find("Fog")
    active = []
    if fog_density_scale != 1.0 or fog_distance_scale != 1.0:
        if fog is None:
            raise ValueError("Track settings lack fog controls")
        for settings in fog:
            if not re.fullmatch(r"Settings\d+", settings.tag):
                continue
            enabled = settings.find("Enabled")
            if enabled is None or enabled.get("value") != "1":
                continue
            far = settings.find("Far")
            density = settings.find("FarDensity")
            if far is None or density is None:
                raise ValueError("Active fog settings lack Far or FarDensity")
            try:
                far_value = float(far.attrib["value"])
                density_value = float(density.attrib["value"])
            except (KeyError, ValueError) as error:
                raise ValueError("Invalid active fog settings") from error
            new_far = far_value * fog_distance_scale
            new_density = density_value * fog_density_scale
            if (not math.isfinite(far_value) or far_value <= 0 or
                    not math.isfinite(density_value) or density_value < 0 or
                    not math.isfinite(new_far) or new_far <= 0 or
                    not math.isfinite(new_density) or new_density > 1):
                raise ValueError("Lighting/fog profile exceeds supported fog ranges")
            far.set("value", format(new_far, ".6f"))
            density.set("value", format(new_density, ".6f"))
            active.append(settings.tag)
    if (fog_density_scale != 1.0 or fog_distance_scale != 1.0) and not active:
        raise ValueError("Track settings contain no enabled directional fog settings")

    return ET.tostring(root, encoding="utf-8", xml_declaration=True), {
        "ambient_scale": float(ambient_scale),
        "direct_scale": float(direct_scale),
        "fog_far_density_scale": float(fog_density_scale),
        "fog_far_distance_scale": float(fog_distance_scale),
        "active_fog_settings": active,
    }


def locators(data):
    root = ET.fromstring(data)
    if root.tag != "TrackLocators" or root.attrib != {"Version": "2"} or not len(root):
        raise ValueError("Invalid source track locator schema")
    rows, names, guids = [], set(), set()
    expected = {"value._%d%d" % (row, column) for row in range(1, 5) for column in range(1, 5)}
    for locator in root:
        if (locator.tag != "Locator" or locator.attrib != {"Version": "3"} or
                [child.tag for child in locator] != ["Name", "GUID", "SceneTransform", "AttachTo"]):
            raise ValueError("Unsupported source locator structure")
        name, guid, transform, attached = locator
        if name.attrib.keys() != {"value"} or guid.attrib.keys() != {"value"}:
            raise ValueError("Invalid locator name/GUID")
        label, identifier = name.get("value"), guid.get("value")
        if (not label or label in names or identifier in guids or not re.fullmatch(
                r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", identifier or "")):
            raise ValueError("Duplicate or invalid source locator name/GUID")
        if transform.attrib.keys() != expected or attached.attrib or len(attached):
            raise ValueError("Unsupported source locator transform/attachment")
        if any(len(child) or (child.text and child.text.strip()) for child in locator):
            raise ValueError("Unexpected source locator payload")
        values = [float(transform.get("value._%d%d" % (row, column))) for row in range(1, 5) for column in range(1, 5)]
        if (any(not math.isfinite(value) for value in values) or
                [values[index] for index in (3, 7, 11, 15)] != [0.0, 0.0, 0.0, 1.0]):
            raise ValueError("Invalid affine source locator transform")
        names.add(label)
        guids.add(identifier)
        rows.append({"name": label, "guid": identifier, "transform": values, "position": values[12:15]})
    return rows


def arrival(source, physics, locator):
    world = read_collision(source)
    report = json.loads((physics / "physics.json").read_text(encoding="utf-8"))
    if not report["schema_verified"] or report["source_sha256"] != world["source_sha256"]:
        raise ValueError("Arrival physics provenance differs from source collision")
    archive = verify_archive(physics / "GeoChunk2.minizip", report["chunk_sha256"])
    verified_bytes(physics / "ChunkMap2.dat", report["lookup_sha256"])
    cells = []
    for index in range(len(archive.entries)):
        row = read_physics(archive.read(index))
        cells.append({"index": index, "vertices": row["vertices"],
                      "triangles": [{"vertices": triangle[2:5],
                                     "flags": (triangle[0] << 24) | (triangle[1] << 16)}
                                    for triangle in row["triangles"]]})
    result = collision_arrival({"cells": cells}, locator)
    for sample in result["ground_samples"]:
        if ground_hits(world, *sample["xz"]) != sample["hits"]:
            raise ValueError("Arrival footprint has no identical source/persisted ground")
    result["source_persisted_ground_equal"] = True
    return result


def collision_arrival(world, locator):
    x, y, z = locator["position"]
    transform = locator["transform"]
    samples = []
    for dx, dz in ((0.0, 0.0), (-1.2, -2.4), (1.2, -2.4), (-1.2, 2.4), (1.2, 2.4)):
        sx, sy, sz = (base + dx * transform[column] + dz * transform[8 + column]
                      for column, base in enumerate((x, y, z)))
        hits = ground_hits(world, sx, sz)
        if not hits:
            raise ValueError("Arrival footprint has no collision ground")
        near = [row for row in hits if abs(row["height"] - sy) <= 2.0]
        if not near:
            raise ValueError("Arrival locator is not near its collision ground")
        samples.append({"xz": [sx, sz], "height": max(row["height"] for row in near),
                        "hits": hits})
    return {"locator": locator["name"], "locator_transform": locator["transform"],
            "source_position": locator["position"], "ground_samples": samples,
            "candidate_position": [x, max(row["height"] for row in samples) + 0.8, z],
            "candidate_clearance": 0.8, "engine_arrival_validated": False}


def build(source_directory, native_settings, physics, output, static_test=False,
          ambient_scale=1.0, direct_scale=1.0,
          fog_density_scale=1.0, fog_distance_scale=1.0):
    source_directory, physics, output = [Path(value).resolve() for value in (source_directory, physics, output)]
    if output.exists():
        raise ValueError("Choose a new environment component directory: " + str(output))
    source_settings = (source_directory / "TrackSettings.xml").read_bytes()
    native_data = Path(native_settings).read_bytes()
    settings, settings_report = track_settings(source_settings, native_data)
    settings, lighting_fog = apply_lighting_fog(
        settings, ambient_scale, direct_scale, fog_density_scale, fog_distance_scale)
    locator_data = (source_directory / "ribbon_00" / "Locators.xml").read_bytes()
    locator_rows = locators(locator_data)
    selected = [row for row in locator_rows if row["name"] == "321Root"]
    if len(selected) != 1:
        raise ValueError("Source Top Gear arrival locator 321Root is missing")
    world = world_tables(source_directory / "ribbon_00" / "TopGear_00.pvs")
    collision_data = (source_directory / "ribbon_00" / "TopGear_track_00.col").read_bytes()
    spawn = arrival(collision_data, physics, selected[0])
    smoke = (source_directory / "smoke.dds").read_bytes()
    cubemap = (source_directory / "staticcarcubemap.xpr").read_bytes()
    if smoke[:4] != b"DDS " or cubemap[:4] != b"XPR2":
        raise ValueError("Unsupported source smoke/cubemap companion envelope")
    files = {"TrackSettings.xml": settings, "ribbon_00\\Locators.xml": locator_data,
             "smoke.dds": smoke, "staticcarcubemap.xpr": cubemap}
    static_report = None
    if static_test:
        companions, static_report = static_test_companions(
            (source_directory / "ribbon_00" / "CollObjs.xml").read_bytes())
        validate_static_companions(companions["PhysicsDefinitions.bin"], companions["ribbon_00\\CollObjs.xml"])
        files.update(companions)
    output.mkdir(parents=True)
    inventory = []
    for name, data in files.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if path.read_bytes() != data:
            raise ValueError("Persisted environment companion differs from conversion")
        inventory.append({"name": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    report = {
        "component": "native-settings-locators-and-arrival", "media_name": "TopGearCPS",
        "schema_verified": True, "engine_validated": False, "first_test_ready": False,
        "playable_map": False, "world_source_sha256": world["source_sha256"],
        "source_collision_sha256": hashlib.sha256(collision_data).hexdigest(),
        "source_settings_sha256": hashlib.sha256(source_settings).hexdigest(),
        "native_settings_sha256": hashlib.sha256(native_data).hexdigest(),
        "settings": settings_report, "lighting_fog": lighting_fog,
        "locator_count": len(locator_rows),
        "source_locator_bytes_preserved": True, "arrival": spawn, "files": inventory,
        "remaining": [
            "Native v83 settings retain source v81 field values after recursive schema checking; rendering still needs engine validation.",
            "Only the FH2-only screenAreaDistances default is taken from the reference; no donor geometry, locators or track identity are used.",
            "Source SH probe v2 differs from native v4 and is not copied or version-spoofed; dynamic lighting integration remains.",
            "PhysicsDefinitions, runtime object/animation registration, audio and dynamic time-of-day companions remain unresolved.",
            "The collision-backed arrival has 0.8m test clearance; heading units and actual car placement need native-flow validation.",
            "Custom game-thread target preparation/load and a save-safe return path still block activation.",
        ],
    }
    if static_report is not None:
        report["static_test"] = static_report
        report["remaining"].remove(
            "PhysicsDefinitions, runtime object/animation registration, audio and dynamic time-of-day companions remain unresolved.")
        report["remaining"].append(
            "Runtime object/animation, audio and dynamic time-of-day integration remain unresolved; movable-prop physics is intentionally disabled.")
        report["remaining"].append(
            "Static-only test deliberately omits movable collision-object registrations; empty dynamic catalog still needs engine validation.")
    (output / "environment.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "native-settings", "physics", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--static-test", action="store_true",
                        help="Explicitly omit movable props; retain streamed road/ground collision")
    parser.add_argument("--ambient-scale", type=float, default=1.0,
                        help="Scale RGB ambient track lighting (per environment)")
    parser.add_argument("--direct-scale", type=float, default=1.0,
                        help="Scale SH directional track lighting (per environment)")
    parser.add_argument("--fog-density-scale", type=float, default=1.0,
                        help="Scale enabled directional fog FarDensity values (per environment)")
    parser.add_argument("--fog-distance-scale", type=float, default=1.0,
                        help="Scale enabled directional fog Far distances (per environment)")
    args = parser.parse_args()
    try:
        report = build(args.source, args.native_settings, args.physics, args.output, args.static_test,
                       args.ambient_scale, args.direct_scale,
                       args.fog_density_scale, args.fog_distance_scale)
    except (OSError, ValueError, KeyError, ET.ParseError) as error:
        print("error: " + str(error), file=sys.stderr)
        return 1
    print("Verified native environment: %d locators, %d preserved settings fields and collision-backed arrival; engine loading remains blocked." %
          (report["locator_count"], len(report["settings"]["preserved_source_fields"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
