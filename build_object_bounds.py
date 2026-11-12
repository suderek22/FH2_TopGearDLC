"""Give FM5 collidable-object visuals real FH2 culling bounds.

FM5 renders its CollObjs props (H posts, KA brake boards, runway lights, barrels,
...) through the dynamic-object system, so their PVS group OBBs are all zero.
FH2 culls promoted primary draws by that OBB, so a zero-size box makes these
props pop in only close to the player. This rewrites only those zero OBBs as the
model-space union of the model's Mesh BBox metadata; draws, zones and residency
are unchanged.
"""
import argparse
import copy
import json
from pathlib import Path
import struct

from build_media import media_catalog
from build_prop_test import _copy_pack, _link_or_copy, _update_track_catalog, atomic_write, digest
from fh2_pvs import read_root60, write_root60
from fh2_world import MiniZip


def mesh_bboxes(data):
    """Return (min xyz, max xyz) tuples from every burG Mesh blob's BBox metadata."""
    if data[:4] != b"burG" or len(data) < 16:
        raise ValueError("Model is not a burG bundle")
    count = struct.unpack_from("<H", data, 6)[0]
    boxes = []
    for index in range(count):
        entry = 16 + 24 * index
        if data[entry:entry + 4][::-1] != b"Mesh":
            continue
        meta_count, meta_offset = struct.unpack_from("<HI", data, entry + 6)
        for meta in range(meta_count):
            offset = meta_offset + 8 * meta
            if data[offset:offset + 4][::-1] != b"BBox":
                continue
            length, relative = struct.unpack_from("<HH", data, offset + 4)
            if length >> 4 < 24:
                raise ValueError("Truncated Mesh BBox metadata")
            values = struct.unpack_from("<6f", data, offset + relative)
            if any(values[axis] > values[axis + 3] for axis in range(3)):
                raise ValueError("Inverted Mesh BBox metadata")
            boxes.append((values[:3], values[3:]))
    return boxes


def is_zero_bound(record):
    return all(abs(value) < 1e-6 for value in struct.unpack("<20f", bytes.fromhex(record))[16:19])


def bound_record(boxes):
    low = [min(box[0][axis] for box in boxes) for axis in range(3)]
    high = [max(box[1][axis] for box in boxes) for axis in range(3)]
    centre = [(a + b) / 2 for a, b in zip(low, high)]
    extent = [(b - a) / 2 for a, b in zip(low, high)]
    return struct.pack("<20f", 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, *centre, 1, *extent, 0).hex()


def fix_object_bounds(root, model_data):
    """Return (root copy, {model: (centre, extent)}); model_data(model_id) -> bytes."""
    result = copy.deepcopy(root)
    used = {draw["model_id"] for draw in result["primary_draws"]}
    changed = {}
    for model in sorted(used):
        group = result["groups"][model]
        if not is_zero_bound(group["record"]):
            continue
        boxes = mesh_bboxes(model_data(model))
        if not boxes:
            raise ValueError("Zero-bound model %d has no Mesh BBox metadata" % model)
        group["record"] = bound_record(boxes)
        values = struct.unpack("<20f", bytes.fromhex(group["record"]))
        if max(values[16:19]) <= 0:
            raise ValueError("Model %d has degenerate mesh bounds" % model)
        changed[model] = (values[12:15], values[16:19])
    if not changed:
        raise ValueError("No zero-bound draw models found")
    return result, changed


def build(source_track, output):
    source_track, output = Path(source_track).resolve(), Path(output).resolve()
    if not source_track.is_dir() or output.exists():
        raise ValueError("Source pack must exist and output must be new")
    if source_track in output.parents or output in source_track.parents:
        raise ValueError("Source and output pack directories must be separate")
    source_media = source_track / "media"
    report = json.loads((source_media / "media.json").read_text(encoding="utf-8"))
    if report.get("object_bounds"):
        raise ValueError("Source already has object bounds applied")
    if digest(source_media / report["catalog_name"]) != report["catalog_sha256"]:
        raise ValueError("Source media catalog checksum mismatch")
    relative = Path(report["override_relative_path"])
    source_root = source_media / relative
    root_relative = Path("ribbon_00") / (report["media_name"] + "_00.pvs")
    root = read_root60((source_root / root_relative).read_bytes())
    keys = [row[0] for row in struct.iter_unpack("<I", (source_root / "ChunkMap0.dat").read_bytes())]
    archive = MiniZip(source_root / "GeoChunk0.minizip")
    lookup = {key: index for index, key in enumerate(keys)}

    def model_data(model):
        return archive.read(lookup[0x04000000 | model])

    updated, changed = fix_object_bounds(root, model_data)
    data = write_root60(updated)
    reread = read_root60(data)
    if write_root60(reread) != data:
        raise ValueError("Bounds root failed serialization verification")
    for key in root:
        if key != "groups" and reread[key] != root[key]:
            raise ValueError("Bounds fix changed root table %s" % key)
    for model, (before, after) in enumerate(zip(root["groups"], reread["groups"])):
        if model not in changed and before != after:
            raise ValueError("Bounds fix changed non-target model %d" % model)

    output.parent.mkdir(parents=True, exist_ok=True)
    _copy_pack(source_track, output, source_root)
    output_media = output / "media"
    _link_or_copy(source_root / "GeoChunk0.minizip", output_media / relative / "GeoChunk0.minizip")
    atomic_write(output_media / relative / root_relative, data)
    for row in report["files"]:
        if Path(row["path"]) == relative / root_relative:
            path = output_media / row["path"]
            row["bytes"], row["sha256"] = path.stat().st_size, digest(path)
    report["object_bounds"] = {
        "source_catalog_sha256": report["catalog_sha256"],
        "models": {str(model): {"centre": [round(v, 4) for v in centre],
                                "half_extent": [round(v, 4) for v in extent]}
                   for model, (centre, extent) in changed.items()},
        "draws_zones_residency_unchanged": True, "in_game_validated": False,
    }
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
    print(json.dumps({"models": len(report["object_bounds"]["models"]),
                      "catalog_sha256": report["catalog_sha256"]}, indent=2))


if __name__ == "__main__":
    main()
