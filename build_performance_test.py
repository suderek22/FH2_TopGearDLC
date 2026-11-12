"""Build a conservative prop-culling test without re-enabling texture streaming."""
import argparse
import copy
import json
import math
from pathlib import Path
import re
import struct

from build_media import media_catalog
from build_prop_test import _copy_pack, _update_track_catalog, atomic_write, digest
from fh2_pvs import read_root60, write_root60
from fh2_world import (MiniZip, Resource, draw_lod_metadata, read_pvs_zone60,
                       write_minizip, write_pvs_zone60)
from spatial import center, read_hex


PROP_DISTANCE = 500.0
MAX_PROP_RADIUS = 20.0


def optimize_root(root, visible_draws):
    result = copy.deepcopy(root)
    if root["secondary_draws"]:
        raise ValueError("Performance test requires promoted primary draws")
    if any(type(index) is not int or not 0 <= index < len(root["primary_draws"])
           for index in visible_draws):
        raise ValueError("Visibility references an invalid root draw")
    candidates = {}
    for index in sorted(visible_draws):
        draw = result["primary_draws"][index]
        # World-space terrain/decals use zero draw translation, not their mesh center.
        if not any(draw["position"]):
            continue
        if not 0 <= draw["model_id"] < len(root["groups"]):
            raise ValueError("Draw references an invalid model group")
        group = root["groups"][draw["model_id"]]
        models = {draw["model_id"]} | (set(group["references"]) - {0xFFFFFFFF})
        if any(not 0 <= model < len(root["groups"]) for model in models):
            raise ValueError("Prop LOD references an invalid model group")
        scale = math.sqrt(sum(value * value for value in draw["rotation_rows"]))
        radii = []
        for model in models:
            values = struct.unpack("<20f", bytes.fromhex(root["groups"][model]["record"]))
            extents = values[16:19]
            if any(value < 0 for value in extents):
                raise ValueError("Negative model bounds in performance profile")
            radius = (math.sqrt(sum(value * value for value in extents)) +
                      math.sqrt(sum(value * value for value in values[12:15]))) * scale
            radii.append(radius)
        radius = max(radii)
        if not 0 < radius <= MAX_PROP_RADIUS:
            continue
        if draw["metadata"] != draw_lod_metadata(draw["model_id"], root["groups"]):
            raise ValueError("Prop LOD offsets have not been repaired")
        raw = bytearray.fromhex(draw["source_record"])
        bands = struct.unpack_from("<4e", raw, 18)
        if not all(math.isfinite(value) for value in bands):
            raise ValueError("Non-finite prop LOD distance")
        if bands[3] < PROP_DISTANCE:
            raise ValueError("Performance test expects the map-wide distance profile")
        # Keep near LOD selection; allow a final LOD before the new cull distance.
        updated = tuple(min(value, PROP_DISTANCE - 50) for value in bands[:3]) + (PROP_DISTANCE,)
        positive = [value for value in updated if value >= 0]
        if positive != sorted(positive):
            raise ValueError("Non-monotonic prop LOD distances")
        struct.pack_into("<4e", raw, 18, *updated)
        draw["source_record"] = raw.hex()
        candidates[index] = radius
    if not candidates:
        raise ValueError("No small translated props qualify for conservative culling")
    return result, candidates


def cull_zone(zone, root, candidates, grid, index):
    x, z = center(grid, index)
    result = dict(zone)
    result["draws"] = []
    for draw_index in zone["draws"]:
        radius = candidates.get(draw_index)
        if radius is not None:
            position = root["primary_draws"][draw_index]["position"]
            # Include the entire cell and mesh sphere, not just the cell center.
            if math.hypot(position[0] - x, position[2] - z) > (
                    PROP_DISTANCE + grid["radius"] + radius):
                continue
        result["draws"].append(draw_index)
    return result


def build(source_track, output):
    source_track, output = Path(source_track).resolve(), Path(output).resolve()
    if not source_track.is_dir() or output.exists():
        raise ValueError("Source pack must exist and output must be new")
    if source_track in output.parents or output in source_track.parents:
        raise ValueError("Source and output pack directories must be separate")
    source_media = source_track / "media"
    report = json.loads((source_media / "media.json").read_text(encoding="utf-8"))
    if not report.get("map_wide_visibility", {}).get("highest_available_texture_levels"):
        raise ValueError("Performance test requires the global high-mip residency profile")
    if report.get("conservative_performance_test"):
        raise ValueError("Source already has the performance test applied")
    if digest(source_media / report["catalog_name"]) != report["catalog_sha256"]:
        raise ValueError("Source media catalog checksum mismatch")
    ini = (source_track / "track.ini").read_text(encoding="utf-8")
    ini_catalog = re.findall(r"(?mi)^\s*CatalogSha256\s*=\s*([0-9a-f]{64})\s*$", ini)
    if ini_catalog != [report["catalog_sha256"]]:
        raise ValueError("Source track metadata and catalog differ")
    expected_catalog = media_catalog(report["media_name"], report["files"])
    if (source_media / report["catalog_name"]).read_bytes() != expected_catalog:
        raise ValueError("Source report and media catalog differ")
    for row in report["files"]:
        path = source_media / row["path"]
        if path.stat().st_size != row["bytes"] or digest(path) != row["sha256"]:
            raise ValueError("Source media file checksum mismatch: " + row["path"])

    relative = Path(report["override_relative_path"])
    source_root = source_media / relative
    root_relative = Path("ribbon_00") / (report["media_name"] + "_00.pvs")
    root = read_root60((source_root / root_relative).read_bytes())
    grid = read_hex((source_root / "ribbon_00" / (report["media_name"] + "out_00.hex")).read_bytes())
    if len(grid["coordinates"]) != root["zone_count"]:
        raise ValueError("Spatial grid and root zone counts differ")
    chunk_map = (source_root / "ChunkMap0.dat").read_bytes()
    keys = [row[0] for row in struct.iter_unpack("<I", chunk_map)]
    archive = MiniZip(source_root / "GeoChunk0.minizip")
    if (len(keys) != len(archive.entries) or len(set(keys)) != len(keys) or
            (source_root / "ChunkMap1.dat").exists()):
        raise ValueError("Expected a valid single-chunk media archive")
    zones = {key: read_pvs_zone60(archive.read(index))
             for index, key in enumerate(keys) if key >> 24 == 12}
    if set(zones) != {0x0C000000 | index for index in range(root["zone_count"])}:
        raise ValueError("Archive zone IDs differ from the root")
    visible = {index for zone in zones.values() for index in zone["draws"]}
    updated_root, candidates = optimize_root(root, visible)
    updated_zones = {
        key: cull_zone(zone, root, candidates, grid, key & 0xFFFFFF)
        for key, zone in zones.items()
    }
    payloads = {key: write_pvs_zone60(zone) for key, zone in updated_zones.items()}
    updated_data = write_root60(updated_root)
    if write_root60(read_root60(updated_data)) != updated_data:
        raise ValueError("Performance root failed serialization verification")

    output.parent.mkdir(parents=True, exist_ok=True)
    _copy_pack(source_track, output, source_root)
    output_media = output / "media"
    output_root = output_media / relative
    resources = [Resource(str(index), key & 0xFFFFFF, key >> 24) for index, key in enumerate(keys)]
    lookup = write_minizip(
        output_root / "GeoChunk0.minizip", resources,
        lambda resource: payloads[keys[int(resource.name)]]
        if keys[int(resource.name)] in payloads else archive.read(int(resource.name)))
    if lookup != chunk_map:
        raise ValueError("Performance archive changed resource IDs or ordering")
    atomic_write(output_root / root_relative, updated_data)

    rebuilt = MiniZip(output_root / "GeoChunk0.minizip")
    for index, key in enumerate(keys):
        data = rebuilt.read(index)
        if data != (payloads[key] if key in payloads else archive.read(index)):
            raise ValueError("Persisted archive differs at resource " + str(key))
    changed = {relative / root_relative, relative / "GeoChunk0.minizip"}
    for row in report["files"]:
        if Path(row["path"]) in changed:
            path = output_media / row["path"]
            row["bytes"], row["sha256"] = path.stat().st_size, digest(path)
    before = [len(zone["draws"]) for zone in zones.values()]
    after = [len(zone["draws"]) for zone in updated_zones.values()]
    report["conservative_performance_test"] = {
        "profile": "small-props-500m-global-residency",
        "source_catalog_sha256": report["catalog_sha256"],
        "prop_cull_metres": PROP_DISTANCE,
        "maximum_prop_sphere_metres": MAX_PROP_RADIUS,
        "optimized_visible_draws": len(candidates),
        "protected_visible_draws": len(visible) - len(candidates),
        "zone_draw_references_before": sum(before),
        "zone_draw_references_after": sum(after),
        "zone_draws_after_min": min(after),
        "zone_draws_after_max": max(after),
        "global_model_texture_residency_preserved": True,
        "non_zone_resource_bytes_preserved": True,
        "shader_lighting_physics_unchanged": True,
        "engine_validated": False,
        "fps_validated": False,
    }
    # This describes residency provenance; its old cull threshold is no longer current.
    report["map_wide_visibility"]["minimum_cull_metres"] = PROP_DISTANCE
    report["map_wide_visibility"]["draws_global"] = False
    catalog = output_media / report["catalog_name"]
    atomic_write(catalog, media_catalog(report["media_name"], report["files"]))
    report["catalog_sha256"] = digest(catalog)
    atomic_write(output_media / "media.json", (json.dumps(report, indent=2) + "\n").encode("utf-8"))
    _update_track_catalog(output / "track.ini", report["catalog_sha256"])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-track", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = build(args.source_track, args.output)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    print(json.dumps(report["conservative_performance_test"], indent=2))


if __name__ == "__main__":
    main()
