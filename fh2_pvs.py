"""Native FH2 v60 streamed FPVS root schema (serializer RVA 0x114B0C0)."""
import struct
import argparse
import hashlib
import json
from pathlib import Path
import sys

from fm5_pvs import Reader
from fm5_world import boolean, finite, read_instances
from fh2_world import read_draw_vector60, draw_placements60


def settings(reader):
    start = reader.offset
    count = reader.unpack("B")[0]
    if count > 19:
        raise ValueError("FH2 settings exceed native half-value storage")
    reader.take(count * 2 + 4 + 32)
    return reader.data[start:reader.offset].hex()


def read_root_groups60(reader):
    root_groups = []
    for _ in range(reader.count(105)):
        raw = reader.take(104)
        finite(struct.unpack("<24f", raw[:96]), "root group vectors")
        present = boolean(reader)
        root_groups.append({"record": raw.hex(), "settings": settings(reader) if present else None})
    return root_groups


def write_root_groups60(groups):
    data = bytearray(struct.pack("<I", len(groups)))
    for row in groups:
        raw = bytes.fromhex(row["record"])
        if len(raw) != 104:
            raise ValueError("Invalid FH2 root group record size")
        data.extend(raw)
        value = row["settings"]
        data.append(int(value is not None))
        if value is not None:
            data.extend(bytes.fromhex(value))
    result = bytes(data)
    reader = Reader(result, "FH2 collision roots")
    if read_root_groups60(reader) != groups or reader.offset != len(result):
        raise ValueError("FH2 collision root serialization differs from its input")
    return result


def read_root60(data):
    reader = Reader(data, "FH2 PVS")
    magic, version, stamp, build = reader.unpack("4I")
    if magic != 0x46505653 or version != 60:
        raise ValueError("Expected a native FH2 v60 FPVS root")
    root = {"stamp": stamp, "build": build, "header_fields": list(reader.unpack("5I"))}
    root["root_groups"] = read_root_groups60(reader)
    root["spatial_bounds"] = finite(reader.unpack("6f"), "spatial bounds")
    root["spatial_keys"] = reader.u32_array()
    root["spatial_maps"] = [
        [list(reader.unpack("2H")) for _ in range(reader.count(4))] for _ in range(2)]
    root["flags"], root["grid"] = reader.unpack("2H")
    root["inline_zones"] = boolean(reader)
    if root["inline_zones"]:
        raise ValueError("Inline FH2 root zones are not supported by this streamed schema")
    root["zone_lookup"] = [list(reader.unpack("2i")) for _ in range(reader.count(8))]
    root["zone_count"] = reader.unpack("I")[0]
    root["ranges"] = [list(reader.unpack("2HI")) for _ in range(reader.count(8))]
    root["has_atlas"] = boolean(reader)
    root["textures"] = [reader.take(56).hex() for _ in range(reader.count(56))]
    for name in ("shaders", "materials"):
        root[name] = [{"hash": reader.unpack("I")[0], "path": reader.string()}
                      for _ in range(reader.count(8))]
    root["instances"] = read_instances(reader)
    root["primary_draws"], reader.offset = read_draw_vector60(data, reader.offset)
    groups = []
    for _ in range(reader.count(110)):
        textures = reader.u32_array()
        enabled = boolean(reader)
        raw = reader.take(80)
        finite(struct.unpack("<20f", raw), "group transform/bounds")
        groups.append({"textures": textures, "enabled": enabled, "record": raw.hex(),
                       "references": list(reader.unpack("4I")),
                       "attributes": [boolean(reader) for _ in range(5)],
                       "distances": list(reader.unpack("2H"))})
    root["groups"] = groups
    root["objects"] = [
        {"guid": reader.string(), "bounds": finite(reader.unpack("4f"), "object bounds"),
         "enabled": boolean(reader), "indexes": list(reader.unpack("2I")),
         "attributes": [boolean(reader), boolean(reader)]}
        for _ in range(reader.count(32))]
    root["object_index_maps"] = [reader.u32_array() for _ in range(3)]
    root["secondary_draws"], reader.offset = read_draw_vector60(data, reader.offset)
    root["scene"] = reader.string()
    root["scene_flags"] = [boolean(reader) for _ in range(3)]
    root["name_tables"] = [[reader.string() for _ in range(reader.count(4))] for _ in range(2)]
    root["reference_triples"] = [list(reader.unpack("3H")) for _ in range(reader.count(6))]
    root["reference_attributes"] = [list(reader.unpack("IHB")) for _ in range(reader.count(7))]
    if not root["scene_flags"][2]:
        root["zone_u16_data"] = [reader.take(reader.count(2) * 2).hex()
                                for _ in range(reader.count(4))]
    else:
        root["zone_u16_data"] = None
    root["zone_maxima"] = list(reader.unpack("3i"))
    root["texture_hash_lookup"] = [list(reader.unpack("2I")) for _ in range(reader.count(8))]
    if boolean(reader):
        raise ValueError("Extra FH2 world tree serialization is not implemented")
    if reader.offset != len(data):
        raise ValueError("Unparsed FH2 root trailer: %d bytes" % (len(data) - reader.offset))
    texture_count = len(root["textures"])
    for instance in root["instances"]:
        for override in instance["material_overrides"]:
            if any(index >= texture_count for _, index in override["texture_hash_index_pairs"]):
                raise ValueError("FH2 material override references an invalid texture")
        for slots in instance["texture_slot_sets"]:
            for group in slots:
                if any(slot[1] >= texture_count for slot in group):
                    raise ValueError("FH2 instance slot references an invalid texture")
    if any(index >= texture_count for group in groups for index in group["textures"]):
        raise ValueError("FH2 transform group references an invalid texture")
    if any(index >= texture_count for _, index in root["texture_hash_lookup"]):
        raise ValueError("FH2 alias references an invalid texture")
    return root


def write_root60(root):
    result = bytearray(struct.pack("<4I", 0x46505653, 60, root["stamp"], root["build"]))

    def pack(fmt, *values):
        result.extend(struct.pack("<" + fmt, *values))

    def string(value):
        raw = value.encode("utf-8")
        pack("I", len(raw))
        result.extend(raw)

    def vector(rows, fmt):
        pack("I", len(rows))
        for row in rows:
            pack(fmt, *(row if isinstance(row, (list, tuple)) else [row]))

    pack("5I", *root["header_fields"])
    result.extend(write_root_groups60(root["root_groups"]))
    pack("6f", *root["spatial_bounds"])
    vector(root["spatial_keys"], "I")
    for rows in root["spatial_maps"]:
        vector(rows, "2H")
    pack("2HB", root["flags"], root["grid"], int(root["inline_zones"]))
    vector(root["zone_lookup"], "2i")
    pack("I", root["zone_count"])
    vector(root["ranges"], "2HI")
    pack("B", int(root["has_atlas"]))
    pack("I", len(root["textures"]))
    for row in root["textures"]:
        raw = bytes.fromhex(row)
        if len(raw) != 56:
            raise ValueError("Invalid FH2 texture descriptor size")
        result.extend(raw)
    for name in ("shaders", "materials"):
        pack("I", len(root[name]))
        for row in root[name]:
            pack("I", row["hash"])
            string(row["path"])
    pack("I", len(root["instances"]))
    for row in root["instances"]:
        pack("I", len(row["material_overrides"]))
        for override in row["material_overrides"]:
            string(override["key"])
            string(override["name"])
            raw = bytes.fromhex(override["material_bundle"])
            pack("I", len(raw))
            result.extend(raw)
            vector(override["texture_hash_index_pairs"], "2I")
        pack("I", len(row["texture_slot_sets"]))
        for slots in row["texture_slot_sets"]:
            pack("I", len(slots))
            for group in slots:
                vector(group, "BIH")
    result.extend(draw_placements60(root["primary_draws"]))
    pack("I", len(root["groups"]))
    for row in root["groups"]:
        vector(row["textures"], "I")
        pack("B", int(row["enabled"]))
        raw = bytes.fromhex(row["record"])
        if len(raw) != 80:
            raise ValueError("Invalid FH2 transform group record size")
        result.extend(raw)
        pack("4I5B2H", *row["references"], *row["attributes"], *row["distances"])
    pack("I", len(root["objects"]))
    for row in root["objects"]:
        string(row["guid"])
        pack("4fB2I2B", *row["bounds"], row["enabled"], *row["indexes"], *row["attributes"])
    for rows in root["object_index_maps"]:
        vector(rows, "I")
    result.extend(draw_placements60(root["secondary_draws"]))
    string(root["scene"])
    pack("3B", *root["scene_flags"])
    for rows in root["name_tables"]:
        pack("I", len(rows))
        for row in rows:
            string(row)
    vector(root["reference_triples"], "3H")
    vector(root["reference_attributes"], "IHB")
    if root["zone_u16_data"] is not None:
        pack("I", len(root["zone_u16_data"]))
        for row in root["zone_u16_data"]:
            raw = bytes.fromhex(row)
            if len(raw) % 2:
                raise ValueError("Invalid FH2 zone uint16 data")
            pack("I", len(raw) // 2)
            result.extend(raw)
    pack("3i", *root["zone_maxima"])
    vector(root["texture_hash_lookup"], "2I")
    pack("B", 0)
    data = bytes(result)
    read_root60(data)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        data = args.source.read_bytes()
        root = read_root60(data)
        if write_root60(root) != data:
            raise ValueError("Native FH2 root failed byte-exact roundtrip")
        report = {"source": str(args.source.resolve()), "sha256": hashlib.sha256(data).hexdigest(),
                  "bytes": len(data), "version": 60, "byte_exact_roundtrip": True,
                  "counts": {name: len(root[name]) for name in
                             ("root_groups", "groups", "primary_draws", "secondary_draws",
                              "instances", "textures", "shaders", "materials")},
                  "note": "Validates an original FH2 root schema, not Top Gear runtime compatibility."}
        with args.output.open("x", encoding="utf-8") as file:
            json.dump(report, file, indent=2, allow_nan=False)
    except (OSError, ValueError) as error:
        print("error: " + str(error), file=sys.stderr)
        return 1
    print("Byte-exact native v60 root roundtrip: %d bytes; %d primary draws." %
          (len(data), len(root["primary_draws"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
