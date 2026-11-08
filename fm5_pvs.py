"""FM5 version-50 visibility asset tables, before its instance/spatial sections."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import struct
import sys
import xml.etree.ElementTree as ET
import zipfile


class Reader:
    def __init__(self, data, label="FM5 PVS"):
        self.data = data
        self.offset = 0
        self.label = label

    def take(self, size):
        if size < 0 or self.offset + size > len(self.data):
            raise ValueError("Truncated %s at byte %d" % (self.label, self.offset))
        data = self.data[self.offset:self.offset + size]
        self.offset += size
        return data

    def unpack(self, fmt):
        return struct.unpack("<" + fmt, self.take(struct.calcsize("<" + fmt)))

    def count(self, width=1):
        count = self.unpack("I")[0]
        if count > 1000000 or count * width > len(self.data) - self.offset:
            raise ValueError("Invalid %s array count at byte %d" % (self.label, self.offset - 4))
        return count

    def string(self):
        return self.take(self.count()).decode("utf-8")

    def u32_array(self):
        return list(self.unpack("%dI" % self.count(4)))


def asset_tables(path):
    data = Path(path).read_bytes()
    reader = Reader(data)
    magic, version, stamp, build = reader.unpack("4I")
    if magic != 0x46505653 or version != 50:
        raise ValueError("Expected an FM5 version-50 FPVS file")
    flags, grid = reader.unpack("2H")
    zones = []
    for index in range(reader.count(16)):
        instances = reader.u32_array()
        models = reader.u32_array()
        textures = reader.u32_array()
        qualities = list(reader.take(reader.count()))
        if len(textures) != len(qualities):
            raise ValueError("FM5 visibility zone texture/quality count mismatch")
        zones.append({"index": index, "instances": instances, "models": models,
                      "textures": textures, "qualities": qualities})
    ranges = [reader.take(8).hex() for _ in range(reader.count(8))]
    has_atlas = reader.unpack("B")[0]
    if has_atlas not in (0, 1):
        raise ValueError("Invalid FM5 texture-atlas flag")
    textures = []
    seen = set()
    for index in range(reader.count(76)):
        raw = reader.take(76)
        texture_hash = struct.unpack_from("<I", raw)[0]
        if texture_hash in seen:
            raise ValueError("Duplicate FM5 PVS texture hash")
        seen.add(texture_hash)
        textures.append({"index": index, "hash": "%08X" % texture_hash,
                         "raw_descriptor": raw.hex(),
                         "fh2_descriptor": (raw[:4] + raw[24:]).hex()})
    paths = {}
    for section in ("shaders", "materials"):
        rows = []
        for _ in range(reader.count(8)):
            key = reader.unpack("I")[0]
            rows.append({"hash": "%08X" % key, "path": reader.string()})
        paths[section] = rows
    for zone in zones:
        if any(index >= len(textures) for index in zone["textures"]):
            raise ValueError("FM5 zone references an invalid texture")
    instances_offset = reader.offset
    instance_count = reader.count(8)
    for zone in zones:
        if any(index >= instance_count for index in zone["instances"]):
            raise ValueError("FM5 zone references an invalid instance")
    return {"format": 1, "version": version, "stamp": stamp, "build": build,
            "flags": flags, "grid": grid, "zones": zones, "ranges": ranges,
            "has_atlas": bool(has_atlas), "textures": textures, **paths,
            "instance_count": instance_count, "instances_offset": instances_offset,
            "parsed_bytes": instances_offset, "remaining_bytes": len(data) - instances_offset,
            "schema_note": "FM5 v50 writes two uint16 root fields followed by zones. "
                           "FH2's v50 reader instead expects five uint32 fields and additional tables. "
                           "Equal FPVS version numbers do not establish format compatibility."}


def verify_assets(tables, archive):
    with zipfile.ZipFile(archive) as source:
        names = source.namelist()
        if len({name.casefold() for name in names}) != len(names):
            raise ValueError("Duplicate case-insensitive archive names")
        manifest = ET.fromstring(source.read("AssetManifest.xml"))
        manifest_hashes, levels = [], {}
        for item in manifest.findall("./Textures/Texture"):
            match = re.fullmatch(r"_0x([0-9a-f]{8})\.swatchbin",
                                item.attrib["Name"], re.IGNORECASE)
            if not match:
                raise ValueError("Unexpected manifest texture name")
            manifest_hashes.append(match[1].upper())
            levels[match[1].upper()] = int(item.attrib["QualityLevels"])
            if not 0 <= levels[match[1].upper()] <= 16:
                raise ValueError("Invalid manifest quality level count")
        if manifest_hashes != [item["hash"] for item in tables["textures"]]:
            raise ValueError("PVS texture ordinal/hash table differs from the asset manifest")
        payloads = {}
        model_ids = set()
        for name in names:
            model = re.fullmatch(r"TopGearout\.(\d{5})\.modelbin", name, re.IGNORECASE)
            if model:
                model_ids.add(int(model[1]))
            if not name.casefold().endswith(".swatchbin"):
                continue
            match = re.fullmatch(r"_0x([0-9a-f]{8})(?:_(\d+))?\.swatchbin",
                                name, re.IGNORECASE)
            if not match or (match[2] is not None and int(match[2]) < 2):
                raise ValueError("Unexpected texture payload name")
            payloads.setdefault(match[1].upper(), []).append(
                {"name": name, "source_variant": int(match[2]) if match[2] else 0,
                 "size": source.getinfo(name).file_size})
        if set(payloads) - set(manifest_hashes):
            raise ValueError("Archive has texture hashes absent from PVS")
        for texture in tables["textures"]:
            parts = sorted(payloads.get(texture["hash"], []), key=lambda part: part["source_variant"])
            count = levels[texture["hash"]]
            expected = [0] + list(range(2, count + 1)) if count else []
            present = [part["source_variant"] for part in parts]
            if any(variant not in expected for variant in present):
                raise ValueError("Payload variant exceeds manifest quality levels for " + texture["hash"])
            texture["missing_streaming_variants"] = [variant for variant in expected if variant not in present]
            for part in parts:
                level = part["source_variant"] or 1
                part["streaming_level"] = level
                part["resource_id"] = (texture["index"] << 4) | (level - 1)
            texture["payloads"] = parts
            texture["source_quality_levels"] = count
        resident_name = "TopGear.stx.bin"
        resident = source.read(resident_name)
        if len(resident) < 16 or resident[:4] != b"burG":
            raise ValueError("Invalid resident texture bundle")
        version, count, start, total = struct.unpack_from("<HHII", resident, 4)
        if (version != 1 or count != len(tables["textures"]) or total != len(resident) or
                not 16 + count * 24 <= start <= total):
            raise ValueError("Resident texture bundle does not match the PVS descriptor table")
        end = start
        for index, texture in enumerate(tables["textures"]):
            offset = 16 + index * 24
            tag = resident[offset:offset + 4]
            major, minor, metadata_count, metadata_offset, payload, size, unpacked = struct.unpack_from(
                "<BBHIIII", resident, offset + 4)
            if (tag != b"xeTS" or (major, minor) != (0, 0) or metadata_count or
                    metadata_offset > start or payload < end or payload + size > total or size != unpacked):
                raise ValueError("Invalid resident STex blob")
            end = payload + size
            raw = resident[payload:end]
            texture["resident"] = {"offset": payload, "size": size,
                                   "sha256": hashlib.sha256(raw).hexdigest()}
        tables["resident_store"] = {"name": resident_name, "count": count, "size": total,
                                   "sha256": hashlib.sha256(resident).hexdigest()}
        for zone in tables["zones"]:
            if any(index not in model_ids for index in zone["models"]):
                raise ValueError("FM5 zone references a missing model")
    tables["archive_binding_verified"] = True
    tables["model_count"] = len(model_ids)
    tables["model_resource_ids"] = sorted(model_ids)
    tables["texture_payload_count"] = sum(len(row["payloads"]) for row in tables["textures"])
    tables["zero_quality_descriptors"] = sum(not row["source_quality_levels"] for row in tables["textures"])
    tables["playable_map"] = False
    tables["missing_streaming_payloads"] = sum(len(row["missing_streaming_variants"])
                                              for row in tables["textures"])
    tables["quality_note"] = ("FH2 native RVA 0x1340F9D constructs type-5 keys as "
                              "0x05000000 + (descriptor_index << 4) + (streaming_level - 1). "
                              "ChunkMap records are uint24 resource ID plus uint8 type, "
                              "not uint16 ID plus a quality byte. Resident STex blobs are "
                              "a separate level, not interchangeable with streamed payloads.")
    tables["descriptor_note"] = ("FH2 version >=50 serializes the source hash and trailing "
                                 "52 bytes, excluding FM5's second uint32 and four floats. "
                                 "These 56-byte records are not a complete FH2 PVS root.")
    return tables


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        tables = verify_assets(asset_tables(args.source), args.archive)
        with args.output.open("x", encoding="utf-8") as file:
            json.dump(tables, file, indent=2)
    except (OSError, ValueError, ET.ParseError, zipfile.BadZipFile, KeyError) as error:
        print("error: " + str(error), file=sys.stderr)
        return 1
    print("Verified %d zones, %d model IDs and %d descriptors binding %d texture payloads." %
          (len(tables["zones"]), tables["model_count"], len(tables["textures"]),
           tables["texture_payload_count"]))
    print("Instance/spatial tail and FH2 world serialization still require conversion.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
