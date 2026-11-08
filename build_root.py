"""Assemble a native FPVS candidate; runtime travel still gates installation."""
import argparse
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import struct
import sys
import zipfile
import xml.etree.ElementTree as ET

from build_bindings import validate_bindings, verified_bytes, verify_archive
from fh2_pvs import read_root60, read_root_groups60, write_root60
from fh2_world import MiniZip, Resource, migrate_draws60, read_pvs_zone60, write_minizip, write_pvs_zone60
from fm5_pvs import Reader
from fm5_world import bind_models, world_tables
from shader_scenes import program_members, shader_scenes
from spatial import read_hex


def relocate(path, media_name):
    prefix = "game:\\media\\tracks\\topgear\\bin\\"
    if not path.casefold().startswith(prefix):
        raise ValueError("Unexpected source world asset path: " + path)
    suffix = path[len(prefix):]
    parts = PureWindowsPath(suffix).parts
    if not suffix or ".." in parts or PureWindowsPath(suffix).is_absolute():
        raise ValueError("Invalid source world asset namespace")
    return "game:\\media\\tracks\\" + media_name + "\\bin\\" + suffix


def assemble(world, zones, secondary_instances, root_groups, nodes, descriptors, grid, media_name):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,47}", media_name):
        raise ValueError("Invalid custom world media namespace")
    count = len(zones)
    if count != len(grid["coordinates"]) or count != len(secondary_instances):
        raise ValueError("World spatial zone counts disagree")
    draws = sorted(world["primary_draws"] + world["secondary_draws"], key=lambda row: row["instance_index"])
    if [row["instance_index"] for row in draws] != list(range(len(world["instances"]))):
        raise ValueError("Canonical draw ordinals do not cover the source instances")
    if any(row["model_id"] >= len(world["groups"]) for row in draws):
        raise ValueError("World draw does not reference a valid native model group")
    secondary_ids = {row["instance_index"] for row in world["secondary_draws"]}
    rebuilt_zones = []
    for zone, secondary in zip(zones, secondary_instances):
        if len(set(secondary)) != len(secondary) or not set(secondary).issubset(secondary_ids):
            raise ValueError("Spatial zone references an invalid secondary source instance")
        instances = []
        for ordinal in zone["draws"]:
            if not 0 <= ordinal < len(world["primary_draws"]):
                raise ValueError("Spatial zone references an invalid primary draw")
            instances.append(world["primary_draws"][ordinal]["instance_index"])
        instances += secondary
        if len(set(instances)) != len(instances):
            raise ValueError("Duplicate spatial visibility request")
        if any(draws[index]["model_id"] not in zone["models"] for index in instances):
            raise ValueError("Spatial model list omits a promoted draw's model")
        rebuilt_zones.append(dict(zone, draws=sorted(instances)))
    unseen = set(range(len(draws))) - {index for zone in rebuilt_zones for index in zone["draws"]}
    extra_models, extra_textures = set(), set()
    for index in sorted(unseen):
        draw = draws[index]
        group = world["groups"][draw["model_id"]]
        extra_models.add(draw["model_id"])
        extra_textures.update(group["textures"])
        instance = world["instances"][index]
        for slot_groups in instance["texture_slot_sets"]:
            for slots in slot_groups:
                extra_textures.update(slot[1] for slot in slots)
        for override in instance["material_overrides"]:
            extra_textures.update(pair[1] for pair in override["texture_hash_index_pairs"])
    extra_levels = {}
    for index in sorted(extra_textures):
        if not 0 <= index < len(world["textures"]):
            raise ValueError("Unrequested placement references an invalid texture")
        texture = world["textures"][index]
        levels = [part["streaming_level"] for part in texture["payloads"]]
        if not levels and not texture["resident"]["size"]:
            raise ValueError("Unrequested placement requires an unavailable texture")
        extra_levels[index] = max(levels, default=0)
    # FM5 static placements are absent from its route views. Keep their complete
    # resource closure global until a native object/animation visibility bake exists.
    for zone in rebuilt_zones:
        zone["draws"] = sorted(set(zone["draws"]) | unseen)
        zone["models"] = sorted(set(zone["models"]) | extra_models)
        levels = dict(zip(zone["textures"], zone["qualities"]))
        for index, level in extra_levels.items():
            levels[index] = max(levels.get(index, 1), 1 | (level << 8))
        zone["textures"] = sorted(levels)
        zone["qualities"] = [levels[index] for index in zone["textures"]]
    validate_bindings(rebuilt_zones, list(range(count)), nodes, len(root_groups))
    bounds = [struct.unpack("<24f", bytes.fromhex(row["record"])[:96]) for row in root_groups]
    if not bounds:
        raise ValueError("Cannot assemble a world without collision roots")
    root = {
        "stamp": world["stamp"], "build": world["build"],
        # Serialized order differs from the native layout; these are safe total/peak capacities.
        "header_fields": [0, len(nodes), len(root_groups), len(root_groups),
                          max(len(row["dependencies"]) for row in rebuilt_zones)],
        "root_groups": root_groups,
        "spatial_bounds": [min(row[16] for row in bounds), min(row[18] for row in bounds),
                           max(row[20] for row in bounds), max(row[22] for row in bounds),
                           grid["radius"], grid["radius"]],
        "spatial_keys": [], "spatial_maps": [[], []], "flags": world["flags"],
        "grid": world["grid"], "inline_zones": False, "zone_lookup": [], "zone_count": count,
        "ranges": [[count, 0, 0], [1, count, 1], [1, count + 1, 2], [1, count + 2, 3]],
        "has_atlas": world["has_atlas"], "textures": descriptors,
        "instances": world["instances"], "primary_draws": migrate_draws60(draws, world["groups"]),
        "secondary_draws": [], "groups": [
            {"textures": row["textures"], "enabled": row["enabled"],
             "record": struct.pack("<20f", *row["transform_rows"], *row["bounds"]).hex(),
             "references": row["references"], "attributes": row["attributes"],
             "distances": row["distances"]} for row in world["groups"]],
        "objects": world["objects"], "object_index_maps": world["object_index_maps"],
        "scene": media_name + "out", "scene_flags": [*world["scene_flags"], True],
        "name_tables": world["name_tables"], "reference_triples": world["reference_triples"],
        "reference_attributes": world["reference_attributes"],
        # Native serializer 0x114C1F4 supports an absent optional legacy visibility-data table.
        "zone_u16_data": None,
        "zone_maxima": [max(len(row[key]) for row in rebuilt_zones)
                        for key in ("textures", "models", "draws")],
        "texture_hash_lookup": world["texture_hash_lookup"],
    }
    for name in ("shaders", "materials"):
        root[name] = [{"hash": int(row["hash"], 16), "path": relocate(row["path"], media_name)}
                      for row in world[name]]
    return root, rebuilt_zones


def build_support_archive(source_archive, output, world, resident_hash, media_name):
    report = []
    with zipfile.ZipFile(source_archive) as source:
        names = source.namelist()
        members = {name.replace("\\", "/").casefold(): name for name in names}
        if len(members) != len(names):
            raise ValueError("Duplicate case-insensitive support archive name")
        shaders = []
        for row in world["shaders"]:
            suffix = relocate(row["path"], media_name).split("\\bin\\", 1)[1]
            key = suffix.replace("\\", "/").casefold()
            if key not in members:
                raise ValueError("World shader is absent from the source support archive")
            name = members[key]
            scenes = shader_scenes(source.read(name))
            programs = program_members(name, scenes, names)
            shaders.append({"name": name, "scenes": scenes, "compiled_programs": programs})
        for row in world["materials"]:
            suffix = relocate(row["path"], media_name).split("\\bin\\", 1)[1]
            if suffix.replace("\\", "/").casefold() not in members:
                raise ValueError("World material is absent from the source support archive")
        resident_name = world["resident_store"]["name"]
        resident = source.read(resident_name)
        if hashlib.sha256(resident).hexdigest() != resident_hash:
            raise ValueError("Resident texture store differs from its geometry component")
        selected = [name for name in names if (
            name.replace("\\", "/").casefold().startswith(("shaders/", "materials/")) or
            name == resident_name or name.casefold().endswith(".gr2") or name == "AssetManifest.xml")]
        payloads = {}
        for name in selected:
            target = media_name + ".stx.bin" if name == resident_name else name.replace("\\", "/")
            if (PureWindowsPath(target).is_absolute() or ".." in PureWindowsPath(target).parts or
                    ":" in target or target.endswith("/")):
                raise ValueError("Unsafe support archive member")
            data = source.read(name)
            payloads[target] = data
            report.append({"name": target, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as target:
            for name, data in payloads.items():
                target.writestr(name, data)
    with zipfile.ZipFile(output) as persisted:
        if set(persisted.namelist()) != set(payloads):
            raise ValueError("Persisted support archive member set differs from source")
        for name, data in payloads.items():
            if persisted.read(name) != data:
                raise ValueError("Persisted shader/material/resident asset differs from source")
    return report, shaders


def build(source_pvs, source_archive, geometry, bindings, spatial, output, media_name="TopGearCPS"):
    output, geometry, bindings, spatial = [Path(value).resolve() for value in (output, geometry, bindings, spatial)]
    if output.exists():
        raise ValueError("Choose a new assembled root component directory: " + str(output))
    world = bind_models(world_tables(source_pvs), source_archive)
    geometry_report = json.loads((geometry / "geometry.json").read_text(encoding="utf-8"))
    binding_report = json.loads((bindings / "bindings.json").read_text(encoding="utf-8"))
    spatial_report = json.loads((spatial / "spatial.json").read_text(encoding="utf-8"))
    if (binding_report["world_source_sha256"] != world["source_sha256"] or
            spatial_report["source_sha256"] != world["source_sha256"] or
            not geometry_report["verified"] or not binding_report["binding_consistency_verified"] or
            not spatial_report["native_grid_roundtrip_verified"]):
        raise ValueError("World conversion component provenance disagrees")
    descriptor_data = verified_bytes(geometry / "TextureDescriptors.dat",
                                     geometry_report["texture_descriptors_sha256"])
    reader = Reader(descriptor_data, "native texture descriptors")
    descriptors = [reader.take(56).hex() for _ in range(reader.count(56))]
    if reader.offset != len(descriptor_data) or descriptors != [row["fh2_descriptor"] for row in world["textures"]]:
        raise ValueError("World texture descriptors differ from the geometry conversion")
    verify_archive(geometry / "GeoChunk0.minizip", geometry_report["chunk_sha256"])
    verified_bytes(geometry / "ChunkMap0.dat", geometry_report["lookup_sha256"])
    archive = verify_archive(bindings / "GeoChunk1.minizip", binding_report["zone_chunk_sha256"])
    count = binding_report["zone_count"]
    if len(archive.entries) != count:
        raise ValueError("Bound zone archive count differs from the root")
    zones = [read_pvs_zone60(archive.read(index)) for index in range(count)]
    lookup = verified_bytes(bindings / "PVSZLookup.dat", binding_report["pvs_lookup_sha256"])
    validate_bindings(zones, [row[0] for row in struct.iter_unpack("<I", lookup)],
                      binding_report["nodes"], binding_report["physics_count"])
    roots = verified_bytes(bindings / "CollisionRoots60.dat", binding_report["collision_roots_sha256"])
    reader = Reader(roots, "bound collision roots")
    root_groups = read_root_groups60(reader)
    if reader.offset != len(roots) or len(root_groups) != binding_report["physics_count"]:
        raise ValueError("Bound collision root table differs from its report")
    hex_data = verified_bytes(bindings / (media_name + "out_00.hex"), binding_report["hex_sha256"])
    if hashlib.sha256(hex_data).hexdigest() != spatial_report["hex_sha256"]:
        raise ValueError("Spatial provenance does not describe the bound grid")
    assignments = verified_bytes(bindings / "ChunkAssign.dat", spatial_report["chunk_assignment_sha256"])
    if assignments != struct.pack("<2I", 100, count) + struct.pack("<%dI" % count, *([1] * count)):
        raise ValueError("Bound spatial chunk assignments are invalid")
    before = {world["primary_draws"][index]["instance_index"] for zone in zones for index in zone["draws"]}
    before.update(index for zone in spatial_report["zones"] for index in zone["secondary_instances"])
    root, rebuilt_zones = assemble(world, zones, [row["secondary_instances"] for row in spatial_report["zones"]],
                                   root_groups, binding_report["nodes"], descriptors, read_hex(hex_data), media_name)
    data = write_root60(root)
    parsed = read_root60(data)
    if write_root60(parsed) != data:
        raise ValueError("Assembled native FPVS root is not byte-exact on roundtrip")
    output.mkdir(parents=True)
    root_name = media_name + "_00.pvs"
    (output / root_name).write_bytes(data)
    support, shaders = build_support_archive(source_archive, output / "bin.zip", world,
                                             geometry_report["resident_store_sha256"], media_name)
    payloads = [write_pvs_zone60(row) for row in rebuilt_zones]
    chunk = output / "GeoChunk1.minizip"
    chunk_map = write_minizip(chunk, [Resource("Zone%04d.pvsz" % i, i, 12) for i in range(count)],
                             lambda resource: payloads[resource.resource_id])
    (output / "ChunkMap1.dat").write_bytes(chunk_map)
    (output / "PVSZLookup.dat").write_bytes(lookup)
    (output / (media_name + "out_00.hex")).write_bytes(hex_data)
    (output / "ChunkAssign.dat").write_bytes(assignments)
    persisted = MiniZip(chunk)
    for index, payload in enumerate(payloads):
        if persisted.read(index) != payload:
            raise ValueError("Persisted canonical spatial visibility differs from the assembled root")
    if read_root60((output / root_name).read_bytes()) != parsed:
        raise ValueError("Persisted world root differs from verified assembly")
    visible = {index for row in rebuilt_zones for index in row["draws"]}
    if visible != set(range(len(root["primary_draws"]))):
        raise ValueError("Assembled native visibility omits a source placement")
    report = {
        "component": "fh2-native-spatial-world-root", "schema_verified": True,
        "engine_validated": False, "first_test_ready": False, "playable_map": False,
        "source_sha256": world["source_sha256"], "media_name": media_name,
        "root_name": root_name, "root_sha256": hashlib.sha256(data).hexdigest(), "root_bytes": len(data),
        "header_fields": root["header_fields"], "zone_maxima": root["zone_maxima"],
        "primary_draw_count": len(root["primary_draws"]), "secondary_draw_count": 0,
        "promoted_secondary_count": len(world["secondary_draws"]),
        "visible_placement_count": len(visible), "all_source_placements_requested": True,
        "globally_requested_source_instances": sorted(visible - before),
        "draw_ordinal_binding": "Source instance ordinal, retaining all source draw record bytes.",
        "group_count": len(root["groups"]), "texture_count": len(descriptors),
        "physics_root_count": len(root_groups), "zone_count": count,
        "support_assets": support, "shaders": shaders,
        "support_archive_sha256": hashlib.sha256((output / "bin.zip").read_bytes()).hexdigest(),
        "zone_chunk_sha256": hashlib.sha256(chunk.read_bytes()).hexdigest(),
        "zone_chunk_map_sha256": hashlib.sha256(chunk_map).hexdigest(),
        "pvs_lookup_sha256": hashlib.sha256(lookup).hexdigest(),
        "hex_sha256": hashlib.sha256(hex_data).hexdigest(),
        "chunk_assignment_sha256": hashlib.sha256(assignments).hexdigest(),
        "geometry_component": str(geometry), "physics_component": binding_report["physics_component"],
        "unbaked_spatial_visibility": True,
        "remaining": [
            "Optional opaque FM5 route visibility data is omitted through the native absent-table flag; engine behavior needs testing.",
            "Legacy shader execution and render-state compatibility still need engine testing.",
            "Material parameters, physical surfaces and animated/object behavior require engine validation.",
            "Source placements absent from route views are requested globally with their model/texture closure; runtime behavior and performance need testing.",
            "Native custom-target load/travel, environment/navigation assets and safe arrival/return remain unfinished.",
            "This component references the separate geometry and physics archives; it is not an installed or first-test-ready map.",
        ],
    }
    report["remaining"][1] = ("Legacy shader scenes and all %d compiled programs are present; shader execution "
                              "and render-state compatibility still need engine testing." %
                              len({name for shader in shaders for name in shader["compiled_programs"]}))
    (output / "root.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-pvs", "source-archive", "geometry", "bindings", "spatial", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        report = build(args.source_pvs, args.source_archive, args.geometry, args.bindings, args.spatial, args.output)
    except (OSError, ValueError, KeyError, ET.ParseError, zipfile.BadZipFile) as error:
        print("error: " + str(error), file=sys.stderr)
        return 1
    print("Native world root verified: %d draws, %d zones, %d collision roots; runtime load remains blocked." %
          (report["primary_draw_count"], report["zone_count"], report["physics_root_count"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
