"""Cull shader-faded static grass shortly after its baked FM5 fade distance.

The grass shaders fade to zero at GRASS_FADE_END (bake_grass_fade 75/100), but the
map-wide profile keeps these draws up to 5 km, so invisible grass is still
submitted. Only the root cull band changes; zones, residency and shaders do not.
"""
import argparse
import copy
import json
from pathlib import Path
import re
import struct

from build_media import media_catalog
from build_prop_test import _copy_pack, _link_or_copy, _update_track_catalog, atomic_write, digest
from fh2_pvs import read_root60, write_root60
from fh2_world import MiniZip


GRASS_FADE_END = 100.0
GRASS_CULL = 120.0
GRASS_MATERIAL = re.compile(rb"grass_topg_001\.materialbin")
GRASS_FLAGS = bytes.fromhex("0410ff0f00007c00")


def cull_grass(root, model_data):
    """Return (root copy, changed draw indices); model_data(model_id) -> bytes."""
    result = copy.deepcopy(root)
    changed = []
    for index, draw in enumerate(result["primary_draws"]):
        raw = bytearray.fromhex(draw["source_record"])
        if bytes(raw[4:12]) != GRASS_FLAGS:
            continue
        group = result["groups"][draw["model_id"]]
        if set(group["references"]) - {0xFFFFFFFF, draw["model_id"]}:
            raise ValueError("Grass draw unexpectedly has linked LOD models")
        if not GRASS_MATERIAL.search(model_data(draw["model_id"])):
            continue
        bands = struct.unpack_from("<4e", raw, 18)
        if bands[3] < GRASS_CULL or any(value > GRASS_FADE_END for value in bands[:3]):
            raise ValueError("Unexpected grass draw bands: %r" % (bands,))
        struct.pack_into("<e", raw, 24, GRASS_CULL)
        draw["source_record"] = raw.hex()
        changed.append(index)
    if not changed:
        raise ValueError("No static grass draws found")
    return result, changed


def build(source_track, output):
    source_track, output = Path(source_track).resolve(), Path(output).resolve()
    if not source_track.is_dir() or output.exists():
        raise ValueError("Source pack must exist and output must be new")
    if source_track in output.parents or output in source_track.parents:
        raise ValueError("Source and output pack directories must be separate")
    source_media = source_track / "media"
    report = json.loads((source_media / "media.json").read_text(encoding="utf-8"))
    if report.get("grass_cull"):
        raise ValueError("Source already has the grass cull applied")
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

    updated, changed = cull_grass(root, model_data)
    data = write_root60(updated)
    if write_root60(read_root60(data)) != data:
        raise ValueError("Grass root failed serialization verification")
    before = read_root60((source_root / root_relative).read_bytes())["primary_draws"]
    after = read_root60(data)["primary_draws"]
    if len(before) != len(after) or any(
            a != b for i, (a, b) in enumerate(zip(before, after)) if i not in set(changed)):
        raise ValueError("Grass cull changed a non-grass draw")

    output.parent.mkdir(parents=True, exist_ok=True)
    _copy_pack(source_track, output, source_root)
    output_media = output / "media"
    _link_or_copy(source_root / "GeoChunk0.minizip", output_media / relative / "GeoChunk0.minizip")
    atomic_write(output_media / relative / root_relative, data)
    for row in report["files"]:
        if Path(row["path"]) == relative / root_relative:
            path = output_media / row["path"]
            row["bytes"], row["sha256"] = path.stat().st_size, digest(path)
    report["grass_cull"] = {
        "source_catalog_sha256": report["catalog_sha256"],
        "material": "grass_topg_001", "draws": len(changed),
        "shader_fade_end_metres": GRASS_FADE_END, "cull_metres": GRASS_CULL,
        "zones_residency_shaders_unchanged": True, "fps_validated": False,
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
    print(json.dumps(report["grass_cull"], indent=2))


if __name__ == "__main__":
    main()
