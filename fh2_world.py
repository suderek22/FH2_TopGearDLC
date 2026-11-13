"""FH2 PGZP version-100 streaming archives and resource lookup records."""
from dataclasses import dataclass
from pathlib import Path
import bisect
import struct
import math
import zlib


RESOURCE_MODEL = 4
RESOURCE_TEXTURE = 5
RESOURCE_PVS_ZONE = 12

PVS_DRAW_INDEX_MASK = 0x3FFFFFFF
PVS_DRAW_MIRROR = 0x40000000
PVS_DRAW_CUBEMAP = 0x80000000


def read_pvs_zone60(data):
    """Read v60 PVSZ, preserving the draw entries' high-bit secondary-view flags."""
    offset = 5
    if data[:5] != b"PVSZ\0":
        raise ValueError("Invalid FH2 PVSZone binary tag")

    def vector(fmt):
        nonlocal offset
        if offset + 4 > len(data):
            raise ValueError("Truncated FH2 PVSZone vector count")
        count = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        width = struct.calcsize("<" + fmt)
        end = offset + count * width
        if count > 1000000 or end > len(data):
            raise ValueError("Invalid FH2 PVSZone vector bounds")
        rows = list(struct.iter_unpack("<" + fmt, data[offset:end]))
        offset = end
        return [row[0] if len(row) == 1 else list(row) for row in rows]

    result = {"draws": vector("I"), "models": vector("I"), "textures": vector("I"),
              "qualities": vector("H"), "dependencies": vector("2I"),
              "auxiliary": vector("I"), "final": vector("I")}
    if len(result["textures"]) != len(result["qualities"]):
        raise ValueError("FH2 PVSZone texture/quality count mismatch")
    if offset != len(data):
        raise ValueError("Unparsed FH2 PVSZone trailer")
    return result


def write_pvs_zone60(zone):
    result = bytearray(b"PVSZ\0")
    for name, fmt in (("draws", "I"), ("models", "I"), ("textures", "I"),
                      ("qualities", "H"), ("dependencies", "2I"),
                      ("auxiliary", "I"), ("final", "I")):
        rows = zone[name]
        if len(rows) > 1000000:
            raise ValueError("Too many FH2 PVSZone vector entries")
        result.extend(struct.pack("<I", len(rows)))
        for row in rows:
            values = row if fmt == "2I" else [row]
            limit = 65535 if fmt == "H" else 0xFFFFFFFF
            if len(values) != (2 if fmt == "2I" else 1) or any(
                    not isinstance(value, int) or not 0 <= value <= limit for value in values):
                raise ValueError("Invalid FH2 PVSZone value in " + name)
            result.extend(struct.pack("<" + fmt, *values))
    data = bytes(result)
    if read_pvs_zone60(data) != zone:
        raise ValueError("FH2 PVSZone serialization differs from its input")
    return data


def draw_placements50(records):
    """Serialize the vector consumed by FH2 RVAs 0x110B720 and 0x114C8D0 at v50."""
    result = bytearray(struct.pack("<I", len(records)))
    for record in records:
        raw = bytes.fromhex(record["source_record"])
        if len(raw) != 73:
            raise ValueError("Expected a complete FM5 v50 draw record")
        result.extend(raw)
        result.append(0)
    return bytes(result)


def read_draw_placements50(data):
    if len(data) < 4:
        raise ValueError("Truncated FH2 draw vector")
    count = struct.unpack_from("<I", data)[0]
    if count > 1000000 or len(data) != 4 + count * 74:
        raise ValueError("Invalid FH2 v50 draw vector length")
    records = []
    for index in range(count):
        offset = 4 + index * 74
        if data[offset + 73] != 0:
            raise ValueError("FH2 optional per-draw settings require a separate serializer")
        model, flags, unused_instance, secondary, tertiary = struct.unpack_from("<IQ3H", data, offset)
        records.append({"model_id": model, "flags": flags,
                        "source_instance_index": unused_instance,
                        "secondary_index": secondary, "tertiary_index": tertiary,
                        "position": list(struct.unpack_from("<3f", data, offset + 27)),
                        "rotation_rows": list(struct.unpack_from("<9e", data, offset + 39)),
                        "source_record": data[offset:offset + 73].hex()})
    return records


def read_draw_vector60(data, offset=0):
    """Read v60 records in native order, including optional material settings."""
    def take(size):
        nonlocal offset
        end = offset + size
        if offset < 0 or end > len(data):
            raise ValueError("Truncated FH2 v60 draw vector")
        raw = data[offset:end]
        offset = end
        return raw

    count = struct.unpack("<I", take(4))[0]
    if count > 1000000 or count * 94 > len(data) - offset:
        raise ValueError("Invalid FH2 v60 draw vector count")
    records = []
    for _ in range(count):
        raw = take(73)
        metadata = take(4)
        present = take(1)[0]
        if present not in (0, 1):
            raise ValueError("Invalid FH2 optional draw settings presence")
        settings = None
        if present:
            size = take(1)[0]
            if size > 19:
                raise ValueError("FH2 draw settings exceed native half-value storage")
            half_values = take(size * 2)
            material_index = take(4)
            channels = take(32)
            settings = (bytes([size]) + half_values + material_index + channels).hex()
        extra = take(16)
        floats = struct.unpack("<3f", raw[27:39]) + struct.unpack("<9e", raw[39:57])
        floats += struct.unpack("<4f", raw[57:73]) + struct.unpack("<4f", extra)
        if not all(math.isfinite(value) for value in floats):
            raise ValueError("Non-finite FH2 v60 draw transform or vector")
        records.append({"source_record": raw.hex(), "metadata": metadata.hex(),
                        "settings": settings, "extra_vector": extra.hex(),
                        "model_id": struct.unpack_from("<I", raw)[0],
                        "source_instance_index": struct.unpack_from("<H", raw, 12)[0],
                        "position": list(struct.unpack_from("<3f", raw, 27)),
                        "rotation_rows": list(struct.unpack_from("<9e", raw, 39))})
    return records, offset


def read_draw_placements60(data):
    records, end = read_draw_vector60(data)
    if end != len(data):
        raise ValueError("Unparsed FH2 v60 draw vector trailer")
    return records


def draw_placements60(records):
    result = bytearray(struct.pack("<I", len(records)))
    for row in records:
        raw = bytes.fromhex(row["source_record"])
        metadata = bytes.fromhex(row["metadata"])
        extra = bytes.fromhex(row["extra_vector"])
        if len(raw) != 73 or len(metadata) != 4 or len(extra) != 16:
            raise ValueError("Invalid FH2 v60 draw field size")
        result.extend(raw)
        result.extend(metadata)
        settings = row["settings"]
        result.append(int(settings is not None))
        if settings is not None:
            result.extend(bytes.fromhex(settings))
        result.extend(extra)
    data = bytes(result)
    parsed = read_draw_placements60(data)
    for source, target in zip(records, parsed):
        if any(source[key] != target[key] for key in ("source_record", "metadata", "settings", "extra_vector")):
            raise ValueError("FH2 v60 draw serialization differs from its input")
    return data


def draw_lod_metadata(model_id, groups):
    """Signed draw offsets to the unique models selected by the four native LOD bands."""
    if not 0 <= model_id < len(groups):
        raise ValueError("Draw model is outside its LOD group table")
    references = groups[model_id]["references"]
    if len(references) != 4:
        raise ValueError("Expected four model LOD references")
    if references == [0xFFFFFFFF] * 4:
        return "00000000"
    if any(reference != 0xFFFFFFFF and not 0 <= reference < len(groups) for reference in references):
        raise ValueError("LOD group contains an invalid model reference")
    models = list(dict.fromkeys(reference for reference in references if reference != 0xFFFFFFFF))
    if model_id not in models:
        raise ValueError("Draw model is absent from its LOD group references")
    current = models.index(model_id)
    return bytes(127 if reference == 0xFFFFFFFF else (models.index(reference) - current) & 255
                 for reference in references).hex()


def migrate_draws60(records, groups=None):
    """Add FH2 fields; source-backed LOD groups replace the constructor's unresolved offsets."""
    return [dict(row, metadata=draw_lod_metadata(struct.unpack_from("<I", bytes.fromhex(
                    row["source_record"]))[0], groups) if groups is not None else "7f7f7f7f", settings=None,
                 extra_vector=struct.pack("<4f", 0, 1, 0, 0).hex()) for row in records]


@dataclass(frozen=True)
class Resource:
    name: str
    resource_id: int
    kind: int
    def lookup(self):
        if not 0 <= self.resource_id <= 0xFFFFFF or not 0 <= self.kind <= 255:
            raise ValueError("Resource ID or kind is out of range: " + self.name)
        return struct.pack("<I", self.resource_id | (self.kind << 24))


@dataclass(frozen=True)
class ArchiveEntry:
    offset: int
    size: int
    method: int
    padding: int
    parent: int
    stored_size: int


class MiniZip:
    def __init__(self, path):
        self.path = Path(path)
        self.entries = []
        file_size = self.path.stat().st_size
        with self.path.open("rb") as file:
            header = file.read(32)
            if len(header) != 32:
                raise ValueError("Truncated MiniZip header")
            magic, self.version, directory, count, folders, per_chunk, chunks, reserved = struct.unpack("<8I", header)
            if magic != 0x505A4750 or self.version not in (100, 101):
                raise ValueError("Unsupported MiniZip magic/version")
            if (directory < 32 or not 1 <= per_chunk <= 65536 or count > 1000000 or folders > 65535 or
                    chunks != (count + per_chunk - 1) // per_chunk or not chunks or reserved != 1):
                raise ValueError("Invalid MiniZip directory header")
            folder_size = (4 * (folders + 1) + 7) & ~7
            table_size = folder_size + 8 * chunks + 12 * (count + 1)
            if directory + table_size > file_size:
                raise ValueError("Truncated MiniZip directory")
            file.seek(directory)
            folder_data = file.read(folder_size)
            self.folders = struct.unpack_from("<%dI" % (folders + 1), folder_data)
            if self.folders[0] != 0 or self.folders[-1] != count or any(
                    first > second for first, second in zip(self.folders, self.folders[1:])):
                raise ValueError("Invalid MiniZip folder boundaries")
            raw_entries = []
            remaining = count
            for _ in range(chunks):
                data_start = struct.unpack("<Q", file.read(8))[0]
                for _ in range(min(per_chunk, remaining)):
                    offset, size, flags, parent = struct.unpack("<IIHH", file.read(12))
                    shift = 12 if self.version == 101 else 14
                    method, padding = flags & ((1 << shift) - 1), flags >> shift
                    if parent >= folders:
                        raise ValueError("Invalid MiniZip parent directory")
                    raw_entries.append((data_start + offset, size, method, padding, parent))
                remaining -= min(per_chunk, remaining)
            last_offset, _, _, _ = struct.unpack("<IIHH", file.read(12))
            final_end = data_start + last_offset
            padding = file_size - final_end
            if padding < 0 or padding > 4095 or (padding and file_size % 4096):
                raise ValueError("MiniZip end sentinel exceeds sector padding allowance")
            if padding:
                file.seek(final_end)
                if file.read(padding) != bytes(padding):
                    raise ValueError("MiniZip trailing sector padding is not zero")
            self.trailing_padding = padding
            ends = [entry[0] for entry in raw_entries[1:]] + [final_end]
            for raw, end in zip(raw_entries, ends):
                offset, size, method, padding, parent = raw
                if offset < directory + table_size or end < offset + padding or end > file_size:
                    raise ValueError("Invalid MiniZip data bounds")
                self.entries.append(ArchiveEntry(offset, size, method, padding, parent, end - offset - padding))

    def read(self, index):
        entry = self.entries[index]
        with self.path.open("rb") as file:
            file.seek(entry.offset)
            data = file.read(entry.stored_size)
        if len(data) != entry.stored_size:
            raise ValueError("Truncated MiniZip entry")
        if entry.method == 8:
            if self.version == 100 and (len(data) < 2 or data[0] & 0x0F != 8 or data[0] >> 4 > 4):
                raise ValueError("FH2 v100 MiniZip entries require a zlib stream with a 4 KB window")
            inflater = zlib.decompressobj(-15 if self.version == 101 else 12)
            result = inflater.decompress(data, entry.size + 1)
            if len(result) > entry.size or inflater.unconsumed_tail or not inflater.eof or inflater.unused_data:
                raise ValueError("Invalid MiniZip deflate payload")
        elif entry.method == 0:
            result = data
        else:
            raise ValueError("Unsupported MiniZip compression method: %d" % entry.method)
        if len(result) != entry.size:
            raise ValueError("MiniZip decompressed size mismatch")
        return result


def write_minizip(path, resources, read_resource, folder_starts=(0,)):
    """Write a MiniZip archive; callers provide bytes one resource at a time.

    ``folder_starts`` lists the first resource index of each folder. FH2 streams a
    folder as one FileChunk, so resources requested through their folder (such as
    placement cells) must live in a small folder of their own.
    """
    path = Path(path)
    count = len(resources)
    if not 1 <= count <= 65535:
        raise ValueError("A geometry chunk must contain 1..65535 resources")
    lookups = [resource.lookup() for resource in resources]
    if len(set(lookups)) != len(lookups):
        raise ValueError("Duplicate resource lookup key")
    folder_starts = list(folder_starts)
    if (not folder_starts or folder_starts[0] != 0 or folder_starts[-1] >= count or len(folder_starts) > 65535 or
            any(a >= b for a, b in zip(folder_starts, folder_starts[1:]))):
        raise ValueError("Invalid MiniZip folder starts")
    boundaries = folder_starts + [count]
    folder_table = struct.pack("<%dI" % len(boundaries), *boundaries)
    folder_table += bytes((-len(folder_table)) % 8)
    parents = [bisect.bisect_right(folder_starts, index) - 1 for index in range(count)]
    per_chunk = 512
    chunks = (count + per_chunk - 1) // per_chunk
    directory_size = len(folder_table) + chunks * 8 + (count + 1) * 12
    data_start = 32 + directory_size
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    if path.exists() or partial.exists():
        raise ValueError("Refusing to overwrite geometry chunk: " + str(path))
    entries, offsets = [], []
    try:
        with partial.open("xb") as file:
            file.write(bytes(data_start))
            for index, resource in enumerate(resources):
                if index % per_chunk == 0:
                    offsets.append(file.tell())
                raw = read_resource(resource)
                if len(raw) > 0xFFFFFFFF:
                    raise ValueError("Resource exceeds MiniZip size range: " + resource.name)
                # FH2 inflates v100 entries with windowBits 12 (RVA 0x11F6ABF); larger windows are rejected.
                compressor = zlib.compressobj(6, zlib.DEFLATED, 12)
                packed = compressor.compress(raw) + compressor.flush()
                method = 8
                if len(packed) >= len(raw):
                    packed, method = raw, 0
                relative = file.tell() - offsets[-1]
                if relative > 0xFFFFFFFF:
                    raise ValueError("MiniZip subchunk exceeds relative offset range")
                padding = (-len(packed)) % 4
                entries.append((relative, len(raw), method | (padding << 14), parents[index]))
                file.write(packed + bytes(padding))
            end = file.tell()
            if end - offsets[-1] > 0xFFFFFFFF:
                raise ValueError("MiniZip end sentinel exceeds relative offset range")
            file.seek(0)
            file.write(struct.pack("<8I", 0x505A4750, 100, 32, count, len(folder_starts), per_chunk, chunks, 1))
            file.write(folder_table)
            for chunk, offset in enumerate(offsets):
                file.write(struct.pack("<Q", offset))
                for entry in entries[chunk * per_chunk:(chunk + 1) * per_chunk]:
                    file.write(struct.pack("<IIHH", *entry))
            file.write(struct.pack("<IIHH", end - offsets[-1], 0, 0, 0))
            if file.tell() != data_start:
                raise ValueError("Internal MiniZip directory size mismatch")
            file.seek(end)
            file.write(bytes((-end) % 4096))
        partial.rename(path)
    except (OSError, ValueError):
        partial.unlink(missing_ok=True)
        raise
    return b"".join(lookups)
