"""Reader/writer for FH2 instance-template placement cells (ChunkMap type 0)."""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

POS_SCALE = 65535
RECORD_WORD = 0x028F  # bytes 6-7 of every Anthem placement record
RECORD_FLAGS = b"\x00\x00\x80\x80"
RECORD_TAIL = b"\xff\xff\xff\xff\x00\x00\x00\x00\xff\xff\xff\xff"


def encode_unit(vector) -> int:
    """Pack a unit vector as three 10-bit offset-binary fields (x low, z high)."""
    word = 0
    for axis, value in enumerate(vector):
        field_value = min(1023, max(0, int(float(value) * 511.5 + 511.5)))
        word |= field_value << (10 * axis)
    return word


def decode_unit(word: int) -> tuple[float, float, float]:
    return tuple(((word >> (10 * axis)) & 0x3FF) / 511.5 - 1.0 for axis in range(3))


def make_placement(position, bmin, bmax, x_axis, y_axis=(0.0, 1.0, 0.0)) -> "Placement":
    """Record for an object whose model X/Y axes map to the given world vectors."""
    quantized = []
    for value, low, high in zip(position, bmin, bmax):
        if not low <= value <= high or high <= low:
            raise ValueError("placement lies outside its cell bounds")
        quantized.append(round((value - low) / (high - low) * POS_SCALE))
    rotation = encode_unit(x_axis) | encode_unit(y_axis) << 32
    return Placement(struct.pack("<3HH", *quantized, RECORD_WORD) + RECORD_FLAGS +
                     struct.pack("<Q", rotation) + RECORD_TAIL)


def _rstr(data: bytes, pos: int) -> tuple[str, int]:
    (n,) = struct.unpack_from("<I", data, pos)
    pos += 4
    if pos + n > len(data):
        raise ValueError("string exceeds cell")
    return data[pos:pos + n].decode("latin-1"), pos + n


def _wstr(text: str) -> bytes:
    raw = text.encode("latin-1")
    return struct.pack("<I", len(raw)) + raw


@dataclass
class Placement:
    raw: bytes  # 32 bytes

    @property
    def qpos(self) -> tuple[int, int, int]:
        return struct.unpack_from("<3H", self.raw)

    @property
    def axes(self) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        """World directions of the model X and Y axes."""
        low, high = struct.unpack_from("<II", self.raw, 12)
        return decode_unit(low), decode_unit(high)


@dataclass
class TemplateBlock:
    name: str
    records: list[Placement]


@dataclass
class Cell:
    name: str
    unk0: int
    kind: int
    bmin: tuple[float, float, float, float]
    bmax: tuple[float, float, float, float]
    library: str
    scale: float
    tag: str
    head: int
    groups: list[tuple[int, ...]]
    sep: int
    templates: list[TemplateBlock] = field(default_factory=list)
    trailer: bytes = b""

    def position(self, rec: Placement) -> tuple[float, float, float]:
        return tuple(low + q / POS_SCALE * (high - low)
                     for q, low, high in zip(rec.qpos, self.bmin, self.bmax))


def parse_cell(data: bytes) -> Cell:
    name, pos = _rstr(data, 0)
    unk0, kind = struct.unpack_from("<II", data, pos)
    pos += 8
    box = struct.unpack_from("<8f", data, pos)
    pos += 32
    library, pos = _rstr(data, pos)
    if pos + 4 > len(data):
        raise ValueError("truncated cell header")
    (scale,) = struct.unpack_from("<f", data, pos)
    tag, pos = _rstr(data, pos + 4)
    a, n = struct.unpack_from("<II", data, pos)
    pos += 8
    if pos + 32 * n + 8 > len(data):
        raise ValueError("group table exceeds cell")
    groups = []
    for _ in range(n):
        groups.append(struct.unpack_from("<8i", data, pos))
        pos += 32
    sep, t = struct.unpack_from("<II", data, pos)
    pos += 8
    cell = Cell(name, unk0, kind, box[:4], box[4:], library, scale, tag, a, groups, sep)
    for _ in range(t):
        tname, pos = _rstr(data, pos)
        (cnt,) = struct.unpack_from("<I", data, pos)
        pos += 4
        if pos + 32 * cnt > len(data):
            raise ValueError("placement table exceeds cell")
        recs =  [Placement(data[pos + 32 * i:pos + 32 * (i + 1)]) for i in range(cnt)]
        pos += 32 * cnt
        (extra,) = struct.unpack_from("<I", data, pos)
        pos += 4
        if extra:
            raise ValueError("template extra list not supported")
        cell.templates.append(TemplateBlock(tname, recs))
    cell.trailer = data[pos:]
    return cell


def write_cell(cell: Cell) -> bytes:
    out = bytearray(_wstr(cell.name))
    out += struct.pack("<II", cell.unk0, cell.kind)
    out += struct.pack("<8f", *cell.bmin, *cell.bmax)
    out += _wstr(cell.library)
    out += struct.pack("<f", cell.scale) + _wstr(cell.tag)
    out += struct.pack("<II", cell.head, len(cell.groups))
    for g in cell.groups:
        out += struct.pack("<8i", *g)
    out += struct.pack("<II", cell.sep, len(cell.templates))
    for tb in cell.templates:
        out += _wstr(tb.name) + struct.pack("<I", len(tb.records))
        for r in tb.records:
            out += r.raw
        out += b"\0\0\0\0"
    out += cell.trailer
    return bytes(out)
