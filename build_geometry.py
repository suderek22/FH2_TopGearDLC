"""Build and verify the FH2 streaming-geometry component of the Top Gear port."""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import xml.etree.ElementTree as ET
import zipfile

from fh2_world import MiniZip, Resource, RESOURCE_MODEL, RESOURCE_TEXTURE, write_minizip
from fm5_pvs import asset_tables, verify_assets


def validate_container(data, texture=False):
    if len(data) < 16 or data[:4] != b"burG":
        raise ValueError("Invalid burG asset")
    version, count, start, total = struct.unpack_from("<HHII", data, 4)
    if version != 1 or total != len(data) or not 16 + count * 24 <= start <= total:
        raise ValueError("Unsupported or truncated burG asset")
    supported = {"Skel": (0, 0), "MatI": (1, 0), "Mesh": (1, 7), "IndB": (1, 0),
                 "VLay": (1, 1), "VerB": (1, 0), "Modl": (1, 1), "Skin": (1, 0)}
    census = collections.Counter()
    for index in range(count):
        offset = 16 + index * 24
        tag = data[offset:offset + 4][::-1].decode("ascii")
        major, minor, metadata_count, metadata_offset, payload, size, decompressed = struct.unpack_from(
            "<BBHIIII", data, offset + 4)
        if (payload < start or payload + size > total or decompressed != size or
                metadata_offset + metadata_count * 8 > start):
            raise ValueError("Invalid burG blob bounds")
        expected = (2, 0) if texture and tag == "TXCB" else supported.get(tag)
        if expected != (major, minor) or (texture and tag != "TXCB"):
            raise ValueError("Unsupported FH2 blob %s version %d.%d" % (tag, major, minor))
        census["%s:%d.%d" % (tag, major, minor)] += 1
    if texture:
        if count != 1:
            raise ValueError("Expected one texture blob")
    elif not all(any(key.startswith(tag + ":") for key in census) for tag in ("Modl", "Mesh", "VLay")):
        raise ValueError("Model is missing required FH2 geometry blobs")
    return census


def resources_for(names, textures):
    models, textures_names = [], []
    for name in names:
        match = re.fullmatch(r"TopGearout\.(\d{5})\.modelbin", name, re.IGNORECASE)
        if match:
            models.append(Resource(name, int(match[1]), RESOURCE_MODEL))
        elif name.casefold().endswith(".modelbin"):
            raise ValueError("Unexpected model naming; cannot preserve its original ordinal: " + name)
        elif name.casefold().endswith(".swatchbin"):
            textures_names.append(name)
    models.sort(key=lambda resource: resource.resource_id)
    if not models or not textures:
        raise ValueError("No Top Gear geometry or textures found")
    bindings = {part["name"]: part["resource_id"] for texture in textures
                for part in texture["payloads"]}
    if set(bindings) != set(textures_names):
        raise ValueError("PVS texture bindings differ from archive names")
    return models + sorted((Resource(name, resource_id, RESOURCE_TEXTURE)
                            for name, resource_id in bindings.items()),
                           key=lambda resource: resource.resource_id)


def build(source_archive, output, source_pvs):
    source_archive, output = Path(source_archive).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a new component output directory: " + str(output))
    tables = verify_assets(asset_tables(source_pvs), source_archive)
    output.mkdir(parents=True)
    census, entries = collections.Counter(), []
    with zipfile.ZipFile(source_archive) as source:
        names = source.namelist()
        if len({name.casefold() for name in names}) != len(names):
            raise ValueError("Duplicate case-insensitive archive names")
        resources = resources_for(names, tables["textures"])

        def read_resource(resource):
            raw = source.read(resource.name)
            census.update(validate_container(raw, resource.kind == RESOURCE_TEXTURE))
            entries.append({"name": resource.name, "resource_id": resource.resource_id,
                            "type": resource.kind,
                            "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
            return raw

        chunk = output / "GeoChunk0.minizip"
        lookup = write_minizip(chunk, resources, read_resource)
        archive = MiniZip(chunk)
        if len(archive.entries) != len(entries):
            raise ValueError("Rebuilt archive count mismatch")
        for index, entry in enumerate(entries):
            raw = archive.read(index)
            if len(raw) != entry["size"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                raise ValueError("Geometry chunk roundtrip mismatch: " + entry["name"])
        (output / "ChunkMap0.dat").write_bytes(lookup)
        resident = source.read(tables["resident_store"]["name"])
        for texture in tables["textures"]:
            blob = texture["resident"]
            validate_container(resident[blob["offset"]:blob["offset"] + blob["size"]], texture=True)
        (output / "ResidentTextures.source.stx.bin").write_bytes(resident)
        descriptors = struct.pack("<I", len(tables["textures"])) + b"".join(
            bytes.fromhex(texture["fh2_descriptor"]) for texture in tables["textures"])
        (output / "TextureDescriptors.dat").write_bytes(descriptors)
        (output / "PVSAssets.json").write_text(json.dumps(tables, indent=2), encoding="utf-8")
        with chunk.open("rb") as file:
            chunk_hash = hashlib.file_digest(file, "sha256").hexdigest()
        report = {"format": 2, "component": "fh2-streaming-geometry", "verified": True,
                  "playable_map": False, "source_archive": str(source_archive),
                  "models": sum(resource.kind == RESOURCE_MODEL for resource in resources),
                  "textures": sum(resource.kind == RESOURCE_TEXTURE for resource in resources),
                  "chunk_size": chunk.stat().st_size,
                  "chunk_sha256": chunk_hash,
                  "lookup_sha256": hashlib.sha256(lookup).hexdigest(),
                  "texture_descriptor_count": len(tables["textures"]),
                  "resident_texture_count": tables["resident_store"]["count"],
                  "resident_store_sha256": tables["resident_store"]["sha256"],
                  "texture_descriptors_sha256": hashlib.sha256(descriptors).hexdigest(),
                  "texture_binding": "PVS descriptor ordinal and native four-bit streaming level",
                  "missing_source_streaming_payloads": tables["missing_streaming_payloads"],
                  "blob_versions": dict(census), "resources": entries,
                  "remaining": [
                      "Model IDs preserve FM5 original ordinals; PVS world instances must be translated to streaming zones.",
                      "Resident textures and descriptor records must be embedded into FH2 world metadata, not installed as standalone files.",
                      "Missing source streaming variants are reported explicitly; their FM5 fallback behavior needs translation.",
                      "Physics, navigation, world metadata, shader binding and runtime travel are still required.",
                  ]}
        (output / "geometry.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pvs", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = build(args.source_archive, args.output, args.pvs)
    except (OSError, ValueError, ET.ParseError, zipfile.BadZipFile, KeyError) as error:
        print("error: " + str(error), file=sys.stderr)
        return 1
    print("FH2 geometry chunk verified: %d models, %d texture payloads, %d bytes" %
          (report["models"], report["textures"], report["chunk_size"]))
    print("This is a converted archive component, not yet a complete playable map.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
