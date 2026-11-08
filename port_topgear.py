"""Prepare an owned FM5 track for investigation; never claim copying is conversion."""
import argparse
import collections
import hashlib
import json
from pathlib import Path, PureWindowsPath
import shutil
import struct
import sys
import xml.etree.ElementTree as ET
import zipfile


def read_u32(data, offset):
    if offset < 0 or offset + 4 > len(data):
        raise ValueError("Truncated instance map at offset %d" % offset)
    return struct.unpack_from("<I", data, offset)[0], offset + 4


def read_string(data, offset):
    length, offset = read_u32(data, offset)
    if length > 4096 or offset + length > len(data):
        raise ValueError("Invalid instance-map string at offset %d" % offset)
    value = data[offset:offset + length].decode("utf-8")
    return value, offset + length


def instance_map(path, *, allow_reference_extensions=False):
    data = Path(path).read_bytes()
    versioned = data.startswith(b"PAMI")
    offset = 0
    version = None
    if versioned:
        version, offset = read_u32(data, 4)
        if version != 1:
            raise ValueError("Unsupported instance-map version: %d" % version)
    count, offset = read_u32(data, offset)
    if count > 100000:
        raise ValueError("Invalid instance-map model count")
    records = []
    for _ in range(count):
        name, offset = read_string(data, offset)
        references, offset = read_u32(data, offset)
        if references > 100000:
            raise ValueError("Invalid instance-map reference count")
        width = 7 if versioned else 8
        end = offset + references * width
        if end > len(data):
            raise ValueError("Truncated instance-map references")
        raw = data[offset:end]
        offset = end
        mapping = None
        if versioned:
            mapping_count, offset = read_u32(data, offset)
            if mapping_count > 100000 or offset + mapping_count * 4 > len(data):
                raise ValueError("Invalid instance-map additional model mappings")
            mapping = list(struct.unpack_from("<%dI" % mapping_count, data, offset))
            offset += mapping_count * 4
        guid_count, offset = read_u32(data, offset)
        if guid_count > 100000:
            raise ValueError("Invalid instance-map GUID count")
        guids = []
        for _ in range(guid_count):
            value, offset = read_string(data, offset)
            if len(value) != 36:
                raise ValueError("Invalid instance-map GUID")
            guids.append(value)
        records.append({"source": name, "references": references, "guids": guid_count,
                        "reference_sample": raw[:width].hex(), "mapping": mapping})
    placement_count, offset = read_u32(data, offset)
    if placement_count > 1000000:
        raise ValueError("Invalid instance-map placement count")
    placement_references = 0
    placement_width = 6 if versioned else 4
    for _ in range(placement_count):
        guid, offset = read_string(data, offset)
        if len(guid) != 36:
            raise ValueError("Invalid instance-map placement GUID")
        references, offset = read_u32(data, offset)
        if references > 1000000 or offset + references * placement_width > len(data):
            raise ValueError("Invalid instance-map placement references")
        offset += references * placement_width
        placement_references += references
    extension_bytes = len(data) - offset
    if extension_bytes and not (versioned and allow_reference_extensions):
        raise ValueError("Unparsed instance-map trailer: %d bytes" % (len(data) - offset))
    return {"version": version, "record_width": 7 if versioned else 8,
            "models": count, "records": records, "placement_guids": placement_count,
            "placement_references": placement_references, "placement_width": placement_width,
            "unparsed_reference_extension_bytes": extension_bytes,
            "parsed_bytes": offset, "total_bytes": len(data)}


def pvs_header(path):
    with Path(path).open("rb") as file:
        data = file.read(40)
    if len(data) < 40 or data[:4] != b"SVPF":
        raise ValueError("Invalid FPVS header: " + str(path))
    return {"version": struct.unpack_from("<I", data, 4)[0],
            "header_words": list(struct.unpack("<10I", data)),
            "size": Path(path).stat().st_size}


def manifest_assets(track):
    with zipfile.ZipFile(track / "bin.zip") as archive:
        bad = archive.testzip()
        if bad is not None:
            raise ValueError("Corrupt track archive entry: " + bad)
        entries = {}
        for info in archive.infolist():
            name = info.filename.replace("/", "\\").casefold()
            if name in entries:
                raise ValueError("Duplicate track archive entry: " + info.filename)
            relative = PureWindowsPath(info.filename)
            if relative.is_absolute() or relative.drive or ".." in relative.parts:
                raise ValueError("Unsafe track archive entry: " + info.filename)
            entries[name] = info
        root = ET.fromstring(archive.read("AssetManifest.xml"))
        sections = {section.tag: len(section) for section in root}
        models = [item.attrib["Name"] for item in root.findall("./Models/Model")]
        missing_models = [name for name in models if name.casefold() not in entries]
        formats = collections.Counter(PureWindowsPath(info.filename).suffix.casefold()
                                      for info in archive.infolist())
        shaders = [{"name": item.attrib["Name"], "source": item.attrib["Source"]}
                   for item in root.findall("./Shaders/Shader")]
        return {"archive_entries": len(entries), "archive_formats": dict(formats),
                "manifest_sections": sections, "missing_models": missing_models,
                "shaders": shaders}


def audit(source, target, track_id):
    source, target = Path(source).resolve(), Path(target).resolve()
    if not 10000 <= track_id <= 2147483647:
        raise ValueError("Custom track IDs must be in 10000..2147483647")
    source_imap = instance_map(source / "ribbon_00" / "TopGear_00.imap")
    target_imap = instance_map(target / "ribbon_00" / "anthem_00.imap", allow_reference_extensions=True)
    source_assets, target_assets = manifest_assets(source), manifest_assets(target)
    target_shaders = {PureWindowsPath(item["source"]).name.casefold()
                      for item in target_assets["shaders"]}
    unmatched = [item for item in source_assets["shaders"]
                 if PureWindowsPath(item["source"]).name.casefold() not in target_shaders]
    chunks = sorted(file.name for file in target.glob("GeoChunk*.minizip"))
    return {
        "format": 1, "name": "TopGear", "reserved_track_id": track_id,
        "source": str(source), "target_reference": str(target),
        "playable": False, "conversion_state": "blocked",
        "source_imap": source_imap, "target_imap": {
            key: value for key, value in target_imap.items() if key != "records"},
        "source_pvs": pvs_header(source / "ribbon_00" / "TopGear_00.pvs"),
        "target_pvs": pvs_header(target / "ribbon_00" / "anthem_00.pvs"),
        "source_assets": source_assets, "target_manifest_sections": target_assets["manifest_sections"],
        "unmatched_shader_names": unmatched, "reference_geometry_chunks": chunks,
        "blockers": [
            "IMAP references use different record layouts (FM5 8 bytes; FH2 7 bytes "
            "plus a counted additional model-mapping array); FH2 also has additional "
            "spatial/procedural lookup tables that this tool has not decoded. "
            "A header-only patch does not convert geometry references.",
            "FM5 geometry and textures are in bin.zip; the FH2 reference uses GeoChunk/ChunkMap streaming. "
            "The legacy loading path and required dependency mapping are not verified.",
            "Shader filename matches do not establish compatible material parameters or compiled shaders.",
            "FH2 navigation, spawn/arrival and native custom-track selection are not implemented or tested.",
        ],
        "pvs_note": "FM5 and FH2 FPVS version-50 root schemas differ: FM5 writes two uint16 "
                    "fields followed by zones; FH2 expects five uint32 fields and additional tables. "
                    "Neither retaining version 50 nor changing it to 60 converts the payload.",
        "fts_note": "ForzaTechStudio documentation describes car/model/texture conversion, "
                    "not a full track/world/navigation conversion.",
    }


def stage(report, output):
    source = Path(report["source"])
    output = Path(output).resolve()
    if output == source or source in output.parents or output in source.parents:
        raise ValueError("Staging output must be separate from the source")
    if output.exists():
        raise ValueError("Staging output already exists; choose a new directory: " + str(output))
    output.mkdir(parents=True)
    files = []
    try:
        for path in sorted(source.rglob("*")):
            if path.is_symlink() or (path.is_dir() and path.is_junction()):
                raise ValueError("Track source contains a redirected path: " + str(path))
            if not path.is_file():
                continue
            if source not in path.resolve().parents:
                raise ValueError("Track source resolves outside its folder: " + str(path))
            relative = path.relative_to(source)
            destination = output / "source" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            with path.open("rb") as file:
                source_hash = hashlib.file_digest(file, "sha256").hexdigest()
            with destination.open("rb") as file:
                target_hash = hashlib.file_digest(file, "sha256").hexdigest()
            if source_hash != target_hash:
                raise OSError("Staging checksum mismatch: " + str(relative))
            files.append({"path": str(relative), "size": path.stat().st_size, "sha256": target_hash})
        report["staged_files"] = files
        report["staged_bytes"] = sum(file["size"] for file in files)
        (output / "compatibility.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (output / "track.ini").write_text(
            "[Track]\nName=TopGear\nId=%d\nState=blocked\n"
            "Reason=Port incomplete: geometry references, shaders, navigation and native travel unverified.\n"
            % report["reserved_track_id"], encoding="ascii")
    except (OSError, ValueError, shutil.Error):
        # Keep the specific failed workspace for diagnosis, but never mark it installable.
        (output / "STAGING-FAILED.txt").write_text(
            "Staging failed. Do not install this incomplete workspace.\n", encoding="ascii")
        raise
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Owned FM5 tracks\\topgear directory")
    parser.add_argument("--reference", type=Path, required=True, help="FH2 tracks\\anthem directory")
    parser.add_argument("--output", type=Path, required=True, help="New local workspace (not a game override)")
    parser.add_argument("--track-id", type=int, default=10000)
    args = parser.parse_args()
    try:
        report = audit(args.source, args.reference, args.track_id)
        directory = stage(report, args.output)
    except (OSError, ValueError, zipfile.BadZipFile, ET.ParseError) as error:
        print("error: " + str(error), file=sys.stderr)
        return 1
    print("Staged owned source assets with SHA256 verification: " + str(directory))
    print("Reserved track ID: %d. NOT converted/playable; see compatibility.json." % args.track_id)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
