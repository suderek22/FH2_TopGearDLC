"""Exempt promoted FM5 prop draws from FH2's small-object screen-size cull.

FH2's per-draw visibility test (forza_durango_release_final.exe 0x133adc0) hides
a draw when ``dist^2 * k1 > size * k2`` unless descriptor flag bit 13 (0x2000)
is set. The descriptor flags are copied unchanged from draw record bytes 4..12.
Small promoted FM5 props (flag 0x400, e.g. the H and KA boards) fail that test
at about 100 m. This sets only bit 0x2000 on those draws.
"""
import argparse
import copy
import json
from pathlib import Path
import struct

from build_media import media_catalog
from build_prop_test import _copy_pack, _link_or_copy, _update_track_catalog, atomic_write, digest
from fh2_pvs import read_root60, write_root60


DYNAMIC_FLAG = 0x400
NO_SIZE_CULL_FLAG = 0x2000


def set_no_size_cull(root):
    """Return (root copy, changed draw indices)."""
    result = copy.deepcopy(root)
    changed = []
    for index, draw in enumerate(result["primary_draws"]):
        raw = bytearray.fromhex(draw["source_record"])
        flags = struct.unpack_from("<Q", raw, 4)[0]
        if not flags & DYNAMIC_FLAG or flags & NO_SIZE_CULL_FLAG:
            continue
        struct.pack_into("<Q", raw, 4, flags | NO_SIZE_CULL_FLAG)
        draw["source_record"] = raw.hex()
        changed.append(index)
    if not changed:
        raise ValueError("No promoted prop draws need the no-size-cull flag")
    return result, changed


def build(source_track, output):
    source_track, output = Path(source_track).resolve(), Path(output).resolve()
    if not source_track.is_dir() or output.exists():
        raise ValueError("Source pack must exist and output must be new")
    if source_track in output.parents or output in source_track.parents:
        raise ValueError("Source and output pack directories must be separate")
    source_media = source_track / "media"
    report = json.loads((source_media / "media.json").read_text(encoding="utf-8"))
    if report.get("prop_size_cull_disabled"):
        raise ValueError("Source already has the prop size cull disabled")
    if digest(source_media / report["catalog_name"]) != report["catalog_sha256"]:
        raise ValueError("Source media catalog checksum mismatch")
    relative = Path(report["override_relative_path"])
    source_root = source_media / relative
    root_relative = Path("ribbon_00") / (report["media_name"] + "_00.pvs")
    root = read_root60((source_root / root_relative).read_bytes())

    updated, changed = set_no_size_cull(root)
    data = write_root60(updated)
    reread = read_root60(data)
    if write_root60(reread) != data:
        raise ValueError("Root failed serialization verification")
    changed_set = set(changed)
    for key in root:
        if key != "primary_draws" and reread[key] != root[key]:
            raise ValueError("Size-cull fix changed root table %s" % key)
    for index, (before, after) in enumerate(zip(root["primary_draws"], reread["primary_draws"])):
        a, b = bytes.fromhex(before["source_record"]), bytes.fromhex(after["source_record"])
        expected = before == after if index not in changed_set else (
            a[:4] + a[12:] == b[:4] + b[12:] and
            struct.unpack_from("<Q", a, 4)[0] ^ struct.unpack_from("<Q", b, 4)[0] == NO_SIZE_CULL_FLAG and
            {k: v for k, v in before.items() if k != "source_record"} ==
            {k: v for k, v in after.items() if k != "source_record"})
        if not expected:
            raise ValueError("Size-cull fix changed draw %d unexpectedly" % index)

    output.parent.mkdir(parents=True, exist_ok=True)
    _copy_pack(source_track, output, source_root)
    output_media = output / "media"
    _link_or_copy(source_root / "GeoChunk0.minizip", output_media / relative / "GeoChunk0.minizip")
    atomic_write(output_media / relative / root_relative, data)
    for row in report["files"]:
        if Path(row["path"]) == relative / root_relative:
            path = output_media / row["path"]
            row["bytes"], row["sha256"] = path.stat().st_size, digest(path)
    report["prop_size_cull_disabled"] = {
        "source_catalog_sha256": report["catalog_sha256"], "flag": NO_SIZE_CULL_FLAG,
        "draws": len(changed), "models": sorted({root["primary_draws"][i]["model_id"] for i in changed}),
        "in_game_validated": False,
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
    summary = dict(report["prop_size_cull_disabled"], catalog_sha256=report["catalog_sha256"])
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
