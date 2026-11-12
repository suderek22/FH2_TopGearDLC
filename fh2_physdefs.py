"""Reader/writer for FH2 track PhysicsDefinitions.bin.

Layout reverse engineered from forza_durango_release_final.exe (loader 0x377460,
serializer 0x12DB000).  Every scalar is little endian and tightly packed.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

SMASHABLE = 0
RIGID = 1
CLOTH = 2

SHAPE_SPHERE = 0
SHAPE_BOX = 1
SHAPE_HULL = 2
SHAPE_POINTS = 5


class _R:
    def __init__(self, data: bytes, pos: int = 0):
        self.d, self.p = data, pos

    def take(self, n: int) -> bytes:
        if self.p + n > len(self.d):
            raise ValueError("PhysicsDefinitions truncated at %d" % self.p)
        out = self.d[self.p:self.p + n]
        self.p += n
        return out

    def u(self, fmt: str):
        size = struct.calcsize("<" + fmt)
        vals = struct.unpack("<" + fmt, self.take(size))
        return vals[0] if len(vals) == 1 else vals


@dataclass
class Shape:
    kind: int
    header: bytes = b""  # two u8: shape type, shape flags (sphere/box only)
    body: bytes = b""  # box: mat4 + vec4, sphere: vec4, hull/points: raw blob

    def write(self) -> bytes:
        out = struct.pack("<i", self.kind) + self.header
        if self.kind in (SHAPE_HULL, SHAPE_POINTS):
            out += struct.pack("<i", len(self.body))
        return out + self.body


@dataclass
class Definition:
    version: int
    kind: int
    mass: float
    inertia: tuple
    inverse_inertia: tuple
    half_extents: tuple
    bounding_radius: float
    graphics_offset: tuple
    aabb_centre_offset: tuple
    smash: dict = field(default_factory=dict)
    cloth: bytes = b""
    shapes: list[Shape] = field(default_factory=list)
    children: list["Definition"] = field(default_factory=list)


def _read_shapes(r: _R, version: int) -> list[Shape]:
    shapes = []
    for _ in range(r.u("i")):
        kind = r.u("i")
        if kind == SHAPE_SPHERE:
            header = r.take(2) if version >= 10 else r.take(4)
            shapes.append(Shape(kind, header, r.take(16)))
        elif kind == SHAPE_BOX:
            header = r.take(2) if version >= 10 else r.take(4)
            shapes.append(Shape(kind, header, r.take(64 + 16)))
        elif kind in (SHAPE_HULL, SHAPE_POINTS):
            shapes.append(Shape(kind, b"", r.take(r.u("i"))))
        else:
            raise ValueError("unknown shape type %d at %d" % (kind, r.p))
    return shapes


def _read_definition(r: _R) -> Definition:
    version = r.u("i")
    kind = r.u("i") if version >= 8 else SMASHABLE
    mass = r.u("f")
    inertia = r.u("9f")
    inverse = r.u("9f")
    half = r.u("3f")
    radius = r.u("f")
    goff = r.u("3f")
    num_children = r.u("i")
    aabb = r.u("3f") if version >= 12 else (0.0, 0.0, 0.0)
    d = Definition(version, kind, mass, inertia, inverse, half, radius, goff, aabb)
    if kind == SMASHABLE:
        s = d.smash
        s["render_model_index"] = r.u("i")
        s["visible_distance"] = r.u("f")
        s["surface_type_index"] = r.u("i")
        s["is_trigger"] = r.u("B")
        s["break_speed_mph"] = r.u("f")
        s["unique_index"] = r.u("i")
        s["object_name_hash"] = r.u("I")
        if version >= 9:
            s["object_type_index"] = r.u("B")
        if version >= 14:
            s["secondary_type_index"] = r.u("B")
        if version >= 15:
            s["mesh_index"] = r.u("H")
            s["connection_list"] = r.take(r.u("I"))
        if version >= 17:
            s["bend"] = r.u("3f")
            s["low_speed_behaviour"] = r.u("I")
        if version == 18:
            s["cloth_locators"] = r.take(16 * r.u("I"))
        if version >= 19:
            s["allow_cloth_attachments"] = r.u("B")
    elif kind == CLOTH:
        start = r.p
        r.take(4 + 4 * 6)
        r.take(16 * r.u("I"))
        if version >= 19:
            r.take(32)
        if version >= 20:
            r.take(8)
        d.cloth = r.d[start:r.p]
    elif kind != RIGID:
        raise ValueError("unknown definition type %d" % kind)
    d.shapes = _read_shapes(r, version)
    for _ in range(num_children):
        d.children.append(_read_definition(r))
    return d


def _write_definition(d: Definition) -> bytes:
    out = bytearray(struct.pack("<i", d.version))
    if d.version >= 8:
        out += struct.pack("<i", d.kind)
    out += struct.pack("<f9f9f3ff3fi", d.mass, *d.inertia, *d.inverse_inertia, *d.half_extents,
                       d.bounding_radius, *d.graphics_offset, len(d.children))
    if d.version >= 12:
        out += struct.pack("<3f", *d.aabb_centre_offset)
    if d.kind == SMASHABLE:
        s = d.smash
        out += struct.pack("<ifiBfiI", s["render_model_index"], s["visible_distance"], s["surface_type_index"],
                           s["is_trigger"], s["break_speed_mph"], s["unique_index"], s["object_name_hash"])
        if d.version >= 9:
            out += struct.pack("<B", s["object_type_index"])
        if d.version >= 14:
            out += struct.pack("<B", s["secondary_type_index"])
        if d.version >= 15:
            out += struct.pack("<HI", s["mesh_index"], len(s["connection_list"])) + s["connection_list"]
        if d.version >= 17:
            out += struct.pack("<3fI", *s["bend"], s["low_speed_behaviour"])
        if d.version == 18:
            out += struct.pack("<I", len(s["cloth_locators"]) // 16) + s["cloth_locators"]
        if d.version >= 19:
            out += struct.pack("<B", s["allow_cloth_attachments"])
    elif d.kind == CLOTH:
        out += d.cloth
    out += struct.pack("<i", len(d.shapes))
    for shape in d.shapes:
        out += shape.write()
    for child in d.children:
        out += _write_definition(child)
    return bytes(out)


@dataclass
class PhysicsDefinitions:
    definitions: list[Definition]
    global_to_list: list[int] | None
    child_render_models: list[list[int]] | None
    trailer: bytes = b""


def parse(data: bytes) -> PhysicsDefinitions:
    r = _R(data)
    count = r.u("Q")
    defs = [_read_definition(r) for _ in range(count)]
    g2l = None
    if defs and defs[0].version >= 11:
        g2l = list(struct.unpack("<%dH" % (n := r.u("Q")), r.take(2 * n)))
    children = None
    # Anthem ships no ChildRenderModelIndices tail; only read it when bytes remain.
    if defs and defs[0].version >= 13 and r.p < len(data):
        children = []
        for d in defs:
            n = max(1, len(d.children))
            children.append(list(struct.unpack("<%di" % n, r.take(4 * n))))
    return PhysicsDefinitions(defs, g2l, children, data[r.p:])


def point_cloud_blob(points) -> bytes:
    """Native type-5 point-cloud shape blob (layout copied from Anthem smashables)."""
    points = [tuple(float(v) for v in p) for p in points]
    if not points:
        raise ValueError("point cloud needs at least one point")
    out = bytearray(b"\x05\x07" + bytes(6))
    out += struct.pack("<QQQ", 0x30, len(points), 0x30 + 16 * len(points)) + bytes(16)
    for x, y, z in points:
        out += struct.pack("<4f", x, y, z, 0.0)
    return bytes(out)


def box_smashable(donor: Definition, lo, hi, mass: float, render_model_index: int,
                  visible_distance: float | None = None) -> Definition:
    """Single-body smashable whose collision is the model-space box [lo, hi].

    The body origin (centre of mass) is the box centre; GraphicsOffset is the model
    pivot in body space, matching Anthem's sign definitions.
    """
    if donor.kind != SMASHABLE or donor.children:
        raise ValueError("donor must be a single-body smashable")
    half = [(b - a) / 2 for a, b in zip(lo, hi)]
    if min(half) <= 0 or mass <= 0:
        raise ValueError("box and mass must be positive")
    centre = [(a + b) / 2 for a, b in zip(lo, hi)]
    corners = [(sx * half[0], sy * half[1], sz * half[2])
               for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
    hx, hy, hz = (h * h for h in half)
    diag = (mass / 3 * (hy + hz), mass / 3 * (hx + hz), mass / 3 * (hx + hy))
    smash = dict(donor.smash, render_model_index=render_model_index)
    if visible_distance is not None:
        smash["visible_distance"] = visible_distance
    return Definition(
        donor.version, SMASHABLE, mass,
        (diag[0], 0.0, 0.0, 0.0, diag[1], 0.0, 0.0, 0.0, diag[2]),
        (1 / diag[0], 0.0, 0.0, 0.0, 1 / diag[1], 0.0, 0.0, 0.0, 1 / diag[2]),
        tuple(half), (hx + hy + hz) ** 0.5, tuple(-c for c in centre),
        (0.0, 0.0, 0.0), smash, b"", [Shape(SHAPE_POINTS, b"", point_cloud_blob(corners))], [])


def write(pd: PhysicsDefinitions) -> bytes:
    out = bytearray(struct.pack("<Q", len(pd.definitions)))
    for d in pd.definitions:
        out += _write_definition(d)
    if pd.global_to_list is not None:
        out += struct.pack("<Q%dH" % len(pd.global_to_list), len(pd.global_to_list), *pd.global_to_list)
    if pd.child_render_models is not None:
        for ids in pd.child_render_models:
            out += struct.pack("<%di" % len(ids), *ids)
    return bytes(out + pd.trailer)
