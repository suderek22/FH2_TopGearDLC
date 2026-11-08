"""Decode FM5 v50 world placements without treating its schema as FH2-compatible."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
import xml.etree.ElementTree as ET
import zipfile

from fm5_pvs import Reader, asset_tables, verify_assets
from fh2_world import (draw_placements50, read_draw_placements50, draw_placements60,
                       read_draw_placements60, migrate_draws60)


def boolean(reader):
    value = reader.unpack("B")[0]
    if value not in (0, 1):
        raise ValueError("Invalid world boolean at byte %d" % (reader.offset - 1))
    return bool(value)


def finite(values, label):
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Non-finite " + label)
    return list(values)


def draw(reader):
    start = reader.offset
    model, flags = reader.unpack("IQ")
    instance, secondary, tertiary = reader.unpack("3H")
    limits = reader.unpack("4H")
    attribute = reader.unpack("B")[0]
    position = finite(reader.unpack("3f"), "draw position")
    rotation = finite(reader.unpack("9e"), "draw rotation")
    bounds = finite(reader.unpack("4f"), "draw bounds")
    return {"model_id": model, "instance_index": instance, "flags": flags,
            "secondary_index": secondary, "tertiary_index": tertiary,
            "limit_half_bits": list(limits), "attribute": attribute,
            "position": position, "rotation_rows": rotation, "bounds": bounds,
            "source_offset": start, "source_record": reader.data[start:reader.offset].hex()}


def read_instances(reader):
    """Shared FM5 v50/FH2 v60 override and texture-slot tables."""
    instances = []
    for index in range(reader.count(8)):
        start = reader.offset
        overrides = []
        for _ in range(reader.count(12)):
            key, name = reader.string(), reader.string()
            material = reader.take(reader.count())
            textures = [list(reader.unpack("2I")) for _ in range(reader.count(8))]
            overrides.append({"key": key, "name": name, "material_bundle": material.hex(),
                              "texture_hash_index_pairs": textures})
        slot_sets = []
        for _ in range(reader.count(4)):
            groups = []
            for _ in range(reader.count(4)):
                slots = [list(reader.unpack("BIH")) for _ in range(reader.count(7))]
                if len({slot[0] for slot in slots}) != len(slots):
                    raise ValueError("Duplicate texture slot in source instance")
                groups.append(slots)
            slot_sets.append(groups)
        instances.append({"index": index, "material_overrides": overrides,
                          "texture_slot_sets": slot_sets, "source_offset": start})
    return instances


def world_tables(path):
    tables = asset_tables(path)
    data = Path(path).read_bytes()
    reader = Reader(data)
    reader.offset = tables["instances_offset"]
    instances = read_instances(reader)
    primary = [draw(reader) for _ in range(reader.count(73))]
    groups = []
    for index in range(reader.count(110)):
        textures = reader.u32_array()
        enabled = boolean(reader)
        transform = finite(reader.unpack("16f"), "group transform")
        bounds = finite(reader.unpack("4f"), "group bounds")
        references = list(reader.unpack("4I"))
        attributes = [boolean(reader) for _ in range(5)]
        distances = list(reader.unpack("2H"))
        groups.append({"index": index, "textures": textures, "enabled": enabled,
                       "transform_rows": transform, "bounds": bounds,
                       "references": references, "attributes": attributes,
                       "distances": distances})
    objects = []
    for _ in range(reader.count(67)):
        guid = reader.string()
        if len(guid) != 36:
            raise ValueError("Invalid source world object GUID")
        bounds = finite(reader.unpack("4f"), "object bounds")
        enabled = boolean(reader)
        indexes = list(reader.unpack("2I"))
        attributes = [boolean(reader), boolean(reader)]
        objects.append({"guid": guid, "bounds": bounds, "enabled": enabled,
                        "indexes": indexes, "attributes": attributes})
    object_maps = [reader.u32_array() for _ in range(3)]
    secondary = [draw(reader) for _ in range(reader.count(73))]
    scene = reader.string()
    scene_flags = [boolean(reader), boolean(reader)]
    names = [[reader.string() for _ in range(reader.count(4))] for _ in range(2)]
    triples = [list(reader.unpack("3H")) for _ in range(reader.count(6))]
    attributes = []
    for _ in range(reader.count(7)):
        key, index = reader.unpack("IH")
        attributes.append([key, index, boolean(reader)])
    zone_data = [reader.take(reader.count(2) * 2).hex() for _ in range(reader.count(4))]
    texture_lookup = [list(reader.unpack("2I")) for _ in range(reader.count(8))]
    if len({key for key, _ in texture_lookup}) != len(texture_lookup):
        raise ValueError("Duplicate final texture lookup key")
    has_extra_tree = boolean(reader)
    if has_extra_tree:
        raise ValueError("FM5 extra world tree serialization is not implemented")
    if reader.offset != len(data):
        raise ValueError("Unparsed FM5 world trailer: %d bytes" % (len(data) - reader.offset))
    textures = tables["textures"]
    texture_count = len(textures)
    for instance in instances:
        for override in instance["material_overrides"]:
            for key, index in override["texture_hash_index_pairs"]:
                if index >= texture_count or key != int(textures[index]["hash"], 16):
                    raise ValueError("Instance override texture hash/index mismatch")
        for groups_of_slots in instance["texture_slot_sets"]:
            for slots in groups_of_slots:
                if any(slot[1] >= texture_count for slot in slots):
                    raise ValueError("Instance texture slot references an invalid descriptor")
    for group in groups:
        if any(index >= texture_count for index in group["textures"]):
            raise ValueError("World group references an invalid texture")
    for key, index in texture_lookup:
        if index >= texture_count:
            raise ValueError("Final world texture lookup references an invalid descriptor")
    draws = primary + secondary
    indexes = [row["instance_index"] for row in draws]
    if sorted(indexes) != list(range(len(instances))):
        raise ValueError("Source draw tables do not cover each instance exactly once")
    if len(zone_data) != len(tables["zones"]):
        raise ValueError("Source zone data count differs from visibility zones")
    positions = [row["position"] for row in draws]
    tables.update({"format": 2, "fully_parsed": True, "parsed_bytes": reader.offset,
                   "remaining_bytes": 0, "source_sha256": hashlib.sha256(data).hexdigest(),
                   "instances": instances, "primary_draws": primary, "secondary_draws": secondary,
                   "groups": groups, "objects": objects, "object_index_maps": object_maps,
                   "scene": scene, "scene_flags": scene_flags, "name_tables": names,
                   "reference_triples": triples, "reference_attributes": attributes,
                   "zone_u16_data": zone_data, "texture_hash_lookup": texture_lookup,
                   "texture_alias_count": sum(key != int(textures[index]["hash"], 16)
                                              for key, index in texture_lookup),
                   "has_extra_tree": has_extra_tree,
                   "placement_bounds": [[min(row[axis] for row in positions),
                                         max(row[axis] for row in positions)] for axis in range(3)]
                                       if positions else [],
                   "world_note": "Source fields and native row-vector transforms are preserved. "
                                 "Opaque flags, reference triples and zone uint16 data are decoded "
                                 "structurally, not assigned speculative semantics. FH2 root, zone "
                                 "dependencies, material overrides and collision still need wiring."})
    return tables


def bind_models(tables, archive):
    tables = verify_assets(tables, archive)
    model_ids = set(tables["model_resource_ids"])
    for row in tables["primary_draws"] + tables["secondary_draws"]:
        if row["model_id"] not in model_ids:
            raise ValueError("World draw references a missing model")
    tables["draw_model_binding_verified"] = True
    return tables


def build_component(source, archive, output, target_version=50):
    if target_version not in (50, 60):
        raise ValueError("Unsupported target draw version")
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a new world component output directory: " + str(output))
    tables = bind_models(world_tables(source), archive)
    output.mkdir(parents=True)
    components = []
    for prefix, key in (("ModelDraws", "primary_draws"),
                        ("StaticModelDraws", "secondary_draws")):
        name = "%s%d.dat" % (prefix, target_version)
        if target_version == 60:
            data = draw_placements60(migrate_draws60(tables[key], tables["groups"]))
            decoded = read_draw_placements60(data)
        else:
            data = draw_placements50(tables[key])
            decoded = read_draw_placements50(data)
        for original, converted in zip(tables[key], decoded):
            if (original["source_record"] != converted["source_record"] or
                    original["model_id"] != converted["model_id"] or
                    original["position"] != converted["position"] or
                    original["rotation_rows"] != converted["rotation_rows"]):
                raise ValueError("FH2 placement roundtrip changed a source transform")
        path = output / name
        path.write_bytes(data)
        if path.read_bytes() != data:
            raise ValueError("Persisted FH2 placement component differs from serialized data")
        components.append({"name": name, "count": len(decoded), "size": len(data),
                           "sha256": hashlib.sha256(data).hexdigest()})
    (output / "WorldSource.json").write_text(json.dumps(tables, indent=2, allow_nan=False),
                                           encoding="utf-8")
    report = {"format": 1, "component": "fh2-v%d-draw-placement-vectors" % target_version,
              "target_version": target_version,
              "verified": True, "engine_validated": False, "playable_map": False,
              "source_sha256": tables["source_sha256"],
              "source_world_bytes": tables["parsed_bytes"],
              "draws": len(tables["instances"]), "groups": len(tables["groups"]),
              "material_overrides": sum(len(row["material_overrides"]) for row in tables["instances"]),
              "placement_bounds": tables["placement_bounds"], "files": components,
              "remaining": [
                  "These are counted native v%d draw vectors, not a complete FPVS/PVSZ world." % target_version,
                  "FH2 reads but discards the legacy uint16 instance index; target instance/material bindings must be rebuilt.",
                  "Optional FH2 per-draw settings are absent in FM5 and are serialized as absent, not invented.",
                  "Opaque source flags are preserved; their FH2 runtime semantics still need verification.",
                  "Visibility dependencies, instance/material binding, resident texture wiring, collision/navigation and travel remain.",
              ]}
    if target_version == 60:
        report["v60_migration"] = {
            "metadata": "7f7f7f7f", "extra_vector": [0, 1, 0, 0],
            "provenance": "FH2 native draw constructor RVA 0x110FA80; serializer RVA 0x114C8D0.",
            "record_bytes_without_settings": 94,
            "note": "Constructor defaults are not assumed to replace source material overrides."}
    (output / "world.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument("--output", type=Path)
    output.add_argument("--component-output", type=Path)
    parser.add_argument("--target-version", type=int, choices=(50, 60), default=50)
    args = parser.parse_args()
    try:
        if args.component_output:
            report = build_component(args.source, args.archive, args.component_output, args.target_version)
            print("FH2 v%d placement vectors verified: %d draws; %d preserved transform groups." %
                  (args.target_version, report["draws"], report["groups"]))
            print("This is a world conversion component, not a complete playable map.")
            return 0
        tables = bind_models(world_tables(args.source), args.archive)
        with args.output.open("x", encoding="utf-8") as file:
            json.dump(tables, file, indent=2, allow_nan=False)
    except (OSError, ValueError, ET.ParseError, zipfile.BadZipFile, KeyError) as error:
        print("error: " + str(error), file=sys.stderr)
        return 1
    print("Parsed all %d world bytes: %d draws, %d transform groups, %d material overrides." %
          (tables["parsed_bytes"], len(tables["instances"]), len(tables["groups"]),
           sum(len(row["material_overrides"]) for row in tables["instances"])))
    print("Source world decoding is complete for this layout; FH2 serialization is not.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
