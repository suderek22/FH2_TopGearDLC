"""Clear the FM5-only group attribute 2 on promoted dynamic-object models.

FM5 sets group attribute index 2 only on its 44 secondary (CollObjs visual)
models. Native FH2 Anthem never sets it on any of 17,221 groups, and in FH2 those
models pop in at about 100 m although their bands, bounds and draw flags were
already ruled out. Only that attribute changes.
"""
import argparse
import copy
import json
from pathlib import Path

from build_media import media_catalog
from build_prop_test import _copy_pack, _link_or_copy, _update_track_catalog, atomic_write, digest
from fh2_pvs import read_root60, write_root60


ATTRIBUTE = 2


def clear_object_attribute(root):
    """Return (root copy, changed model ids)."""
    result = copy.deepcopy(root)
    changed = []
    for model, group in enumerate(result["groups"]):
        if group["attributes"][ATTRIBUTE]:
            group["attributes"][ATTRIBUTE] = False
            changed.append(model)
    if not changed:
        raise ValueError("No groups carry the FM5 dynamic-object attribute")
    return result, changed


def build(source_track, output):
    source_track, output = Path(source_track).resolve(), Path(output).resolve()
    if not source_track.is_dir() or output.exists():
        raise ValueError("Source pack must exist and output must be new")
    if source_track in output.parents or output in source_track.parents:
        raise ValueError("Source and output pack directories must be separate")
    source_media = source_track / "media"
    report = json.loads((source_media / "media.json").read_text(encoding="utf-8"))
    if report.get("object_attribute_cleared"):
        raise ValueError("Source already has the object attribute cleared")
    if digest(source_media / report["catalog_name"]) != report["catalog_sha256"]:
        raise ValueError("Source media catalog checksum mismatch")
    relative = Path(report["override_relative_path"])
    source_root = source_media / relative
    root_relative = Path("ribbon_00") / (report["media_name"] + "_00.pvs")
    root = read_root60((source_root / root_relative).read_bytes())

    updated, changed = clear_object_attribute(root)
    data = write_root60(updated)
    reread = read_root60(data)
    if write_root60(reread) != data:
        raise ValueError("Attribute root failed serialization verification")
    for key in root:
        if key != "groups" and reread[key] != root[key]:
            raise ValueError("Attribute fix changed root table %s" % key)
    for model, (before, after) in enumerate(zip(root["groups"], reread["groups"])):
        expected = copy.deepcopy(before)
        if model in changed:
            expected["attributes"][ATTRIBUTE] = False
        if after != expected:
            raise ValueError("Attribute fix changed model %d unexpectedly" % model)

    output.parent.mkdir(parents=True, exist_ok=True)
    _copy_pack(source_track, output, source_root)
    output_media = output / "media"
    _link_or_copy(source_root / "GeoChunk0.minizip", output_media / relative / "GeoChunk0.minizip")
    atomic_write(output_media / relative / root_relative, data)
    for row in report["files"]:
        if Path(row["path"]) == relative / root_relative:
            path = output_media / row["path"]
            row["bytes"], row["sha256"] = path.stat().st_size, digest(path)
    report["object_attribute_cleared"] = {
        "source_catalog_sha256": report["catalog_sha256"], "attribute": ATTRIBUTE,
        "models": changed, "in_game_validated": False,
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
    summary = dict(report["object_attribute_cleared"], catalog_sha256=report["catalog_sha256"])
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
