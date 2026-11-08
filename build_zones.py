"""Build geometry-only FH2 v60 PVSZone resources; do not claim a complete world."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import zipfile

from fh2_world import MiniZip, Resource, RESOURCE_PVS_ZONE, read_pvs_zone60, write_minizip, write_pvs_zone60
from fm5_world import bind_models, world_tables


def geometry_zones(world):
    primary = {row["instance_index"]: index for index, row in enumerate(world["primary_draws"])}
    secondary = {row["instance_index"] for row in world["secondary_draws"]}
    draws_by_instance = {row["instance_index"]: row for row in world["primary_draws"] + world["secondary_draws"]}
    models = set(world["model_resource_ids"])
    textures = {row["index"]: row for row in world["textures"]}
    result = []
    for source in world["zones"]:
        if len(set(source["instances"])) != len(source["instances"]):
            raise ValueError("Duplicate source zone instance")
        unknown = set(source["instances"]) - set(primary) - secondary
        if unknown:
            raise ValueError("Zone references an unmapped draw instance")
        if any(model not in models for model in source["models"]):
            raise ValueError("Zone references a model absent from the geometry archive")
        if any(draws_by_instance[index]["model_id"] not in source["models"] for index in source["instances"]):
            raise ValueError("Zone model list omits a visible draw's model")
        if len(source["textures"]) != len(source["qualities"]):
            raise ValueError("Source zone texture/quality count mismatch")
        requested = []
        resident_count = 0
        for index, level in zip(source["textures"], source["qualities"]):
            if index not in textures or not isinstance(level, int) or not 0 <= level <= 16:
                raise ValueError("Zone references an invalid texture descriptor or level")
            texture = textures[index]
            if level == 0:
                if not texture["resident"]["size"]:
                    raise ValueError("Zone requires an empty resident texture")
                resident_count += 1
                continue
            parts = [part for part in texture["payloads"] if part["streaming_level"] == level]
            if len(parts) != 1 or parts[0]["resource_id"] != (index << 4) | (level - 1):
                raise ValueError("Zone requires a missing or incorrectly bound streaming texture")
            requested.append(parts[0]["resource_id"])
        draws = [primary[index] for index in source["instances"] if index in primary]
        zone = {"draws": draws, "models": source["models"], "textures": source["textures"],
                "qualities": [1 | (quality << 8) for quality in source["qualities"]],
                "dependencies": [], "auxiliary": [], "final": []}
        data = write_pvs_zone60(zone)
        result.append({"index": source["index"], "zone": zone, "data": data,
                       "secondary_instances": [index for index in source["instances"] if index in secondary],
                       "resident_texture_requests": resident_count,
                       "streaming_texture_resource_ids": requested})
    return result


def build(source, archive, output):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a new zone component output directory: " + str(output))
    world = bind_models(world_tables(source), archive)
    zones = geometry_zones(world)
    resources = [Resource("Zone%04d.pvsz" % row["index"], row["index"], RESOURCE_PVS_ZONE) for row in zones]
    data_by_name = {resource.name: row["data"] for resource, row in zip(resources, zones)}
    output.mkdir(parents=True)
    chunk = output / "GeoChunk1.minizip"
    lookup = write_minizip(chunk, resources, lambda resource: data_by_name[resource.name])
    rebuilt = MiniZip(chunk)
    reports = []
    for index, (resource, row) in enumerate(zip(resources, zones)):
        data = rebuilt.read(index)
        if data != row["data"] or read_pvs_zone60(data) != row["zone"]:
            raise ValueError("Rebuilt PVSZone roundtrip mismatch")
        reports.append({"index": row["index"], "name": resource.name,
                        "draws": len(row["zone"]["draws"]), "models": len(row["zone"]["models"]),
                        "textures": len(row["zone"]["textures"]),
                        "secondary_instances": row["secondary_instances"],
                        "resident_texture_requests": row["resident_texture_requests"],
                        "streaming_texture_resource_ids": row["streaming_texture_resource_ids"],
                        "sha256": hashlib.sha256(data).hexdigest()})
    (output / "ChunkMap1.dat").write_bytes(lookup)
    report = {"format": 1, "component": "fh2-v60-geometry-only-pvs-zones",
              "schema_verified": True, "engine_validated": False, "playable_map": False,
              "geometry_resource_bindings_verified": True,
              "source_sha256": world["source_sha256"], "zones": reports,
              "chunk_sha256": hashlib.sha256(chunk.read_bytes()).hexdigest(),
              "lookup_sha256": hashlib.sha256(lookup).hexdigest(),
              "remaining": [
                  "Geometry-only zones omit physics, light, navigation and other dependencies; this is not a complete map.",
                  "Zone IDs are local ordinals; spatial PVSZLookup and ChunkAssign wiring remain.",
                  "Draw ordinals address the primary placement vector. Upper draw metadata bits are unset, not inferred from FM5.",
                  "Secondary/static placements are reported separately and require root registration.",
                  "Quality words use the native legacy byte conversion (low byte 1, high byte source quality).",
                  "Target v60 root/draw registration, instance/material overrides and auxiliary zone tables remain.",
              ]}
    (output / "zones.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = build(args.source, args.archive, args.output)
    except (OSError, ValueError, ET.ParseError, zipfile.BadZipFile, KeyError) as error:
        print("error: " + str(error), file=sys.stderr)
        return 1
    print("Verified %d geometry-only FH2 v60 PVSZone resources." % len(report["zones"]))
    print("World registration and non-geometry dependencies still require conversion.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
