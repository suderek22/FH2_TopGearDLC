"""Native FH2 HEXY v100/v101 visibility grid schema and coordinate lookup."""
import math
import struct


def read_hex(data):
    if len(data) < 32:
        raise ValueError("Truncated HEXY header")
    magic, version, radius, x, z, count, columns, rows = struct.unpack_from("<2I3f3I", data)
    if magic != 0x59584548 or version not in (100, 101):
        raise ValueError("Unsupported HEXY tag/version")
    if not all(math.isfinite(v) for v in (radius, x, z)) or radius <= 0:
        raise ValueError("Invalid HEXY spatial dimensions")
    if not count or not columns or not rows or columns * rows > 10000000:
        raise ValueError("Invalid HEXY table dimensions")
    end = 32 + columns * rows * 4
    expected = end + count * (9 if version == 101 else 8)
    if expected != len(data):
        raise ValueError("Invalid HEXY payload size")
    cells = list(struct.unpack_from("<%di" % (columns * rows), data, 32))
    coordinates = [list(row) for row in struct.iter_unpack("<2i", data[end:end + count * 8])]
    masks = list(data[end + count * 8:]) if version == 101 else None
    if any(zone < -1 or zone >= count for zone in cells):
        raise ValueError("HEXY cell references an invalid zone")
    if len(set(tuple(row) for row in coordinates)) != count:
        raise ValueError("Duplicate HEXY zone coordinates")
    for zone, (column, row) in enumerate(coordinates):
        if not 0 <= column < columns or not 0 <= row < rows or cells[row * columns + column] != zone:
            raise ValueError("HEXY zone coordinates disagree with the cell table")
    if masks is not None and any(mask & ~0x3F for mask in masks):
        raise ValueError("Invalid HEXY neighbor mask")
    return {"version": version, "radius": radius, "origin": [x, z],
            "columns": columns, "rows": rows, "cells": cells,
            "coordinates": coordinates, "neighbor_masks": masks}


def write_hex(grid):
    data = bytearray(struct.pack("<2I3f3I", 0x59584548, grid["version"], grid["radius"],
                                 *grid["origin"], len(grid["coordinates"]),
                                 grid["columns"], grid["rows"]))
    data.extend(struct.pack("<%di" % len(grid["cells"]), *grid["cells"]))
    for row in grid["coordinates"]:
        data.extend(struct.pack("<2i", *row))
    if grid["neighbor_masks"] is not None:
        data.extend(bytes(grid["neighbor_masks"]))
    result = bytes(data)
    if read_hex(result) != grid:
        raise ValueError("HEXY serialization differs from input")
    return result


def f32(value):
    try:
        result = struct.unpack("<f", struct.pack("<f", value))[0]
    except OverflowError as error:
        raise ValueError("HEXY value exceeds native float range") from error
    if not math.isfinite(result):
        raise ValueError("Non-finite HEXY value")
    return result


def steps(radius):
    step_x = f32(radius + f32(radius * 0.5))
    step_z = f32(f32(f32(math.sqrt(0.75)) * 2.0) * radius)
    if step_x <= 0 or step_z <= 0:
        raise ValueError("HEXY radius is below native float precision")
    return step_x, step_z


def center(grid, zone):
    column, row = grid["coordinates"][zone]
    step_x, step_z = steps(grid["radius"])
    return [f32(grid["origin"][0] + column * step_x + grid["radius"]),
            f32(grid["origin"][1] + row * step_z + ((column & 1) + 1) * step_z * 0.5)]


def covering_grid(bounds, radius=100.0):
    if (len(bounds) != 4 or any(not math.isfinite(value) for value in bounds) or
            bounds[0] > bounds[2] or bounds[1] > bounds[3] or
            not math.isfinite(radius) or radius <= 0):
        raise ValueError("Invalid HEXY coverage bounds/radius")
    radius = f32(radius)
    step_x, step_z = steps(radius)
    origin = [f32((math.floor(bounds[0] / step_x) - 2) * step_x),
              f32((math.floor(bounds[1] / step_z) - 2) * step_z)]
    columns = math.ceil((bounds[2] - origin[0]) / step_x) + 3
    rows = math.ceil((bounds[3] - origin[1]) / step_z) + 3
    if columns * rows > 65535:
        raise ValueError("HEXY coverage exceeds native root range capacity")
    coordinates = [[column, row] for row in range(rows) for column in range(columns)]
    grid = {"version": 101, "radius": radius, "origin": origin, "columns": columns,
            "rows": rows, "coordinates": coordinates,
            "cells": list(range(len(coordinates))), "neighbor_masks": [0x3F] * len(coordinates)}
    read_hex(write_hex(grid))
    return grid


def zone_at(grid, x, z):
    """Mirror FH2 0x1889D20/0x1887DB0 with native single-precision operations."""
    if not math.isfinite(x) or not math.isfinite(z):
        raise ValueError("Non-finite HEXY query")
    radius = grid["radius"]
    half_radius = f32(radius * 0.5)
    step_x, step_z = steps(radius)
    half_z = f32(step_z * 0.5)
    dx, dz = f32(f32(x) - grid["origin"][0]), f32(f32(z) - grid["origin"][1])
    column = math.trunc(f32(dx / step_x))
    if dx < 0:
        column -= 1
    if column & 1:
        dz = f32(dz - half_z)
    row = math.trunc(f32(dz / step_z))
    if dz < 0:
        row -= 1
    local_x = f32(dx - f32(step_x * f32(column)))
    local_z = f32(dz - f32(step_z * f32(row)))
    if local_x < half_radius:
        ratio_x = f32(local_x / half_radius)
        if local_z < half_z:
            ratio_z = f32(local_z / half_z)
            if ratio_z < f32(1.0 - ratio_x):
                column -= 1
                if column & 1:
                    row -= 1
        else:
            ratio_z = f32(f32(local_z - half_z) / half_z)
            if ratio_z > ratio_x:
                column -= 1
                if not column & 1:
                    row += 1
    if not 0 <= column < grid["columns"] or not 0 <= row < grid["rows"]:
        return -1
    return grid["cells"][row * grid["columns"] + column]


def neighbors(grid, zone):
    if type(zone) is not int or not 0 <= zone < len(grid["coordinates"]):
        raise ValueError("Invalid HEXY neighbor query")
    column, row = grid["coordinates"][zone]
    positions = [[column, row - 1], [column - 1, row], [column - 1, row],
                 [column, row + 1], [column + 1, row], [column + 1, row]]
    if column & 1:
        positions[2][1] += 1
        positions[4][1] += 1
    else:
        positions[1][1] -= 1
        positions[5][1] -= 1
    mask = grid["neighbor_masks"][zone] if grid["neighbor_masks"] is not None else 0x3F
    result = []
    for bit, (x, z) in enumerate(positions):
        if mask & (1 << bit) and 0 <= x < grid["columns"] and 0 <= z < grid["rows"]:
            value = grid["cells"][z * grid["columns"] + x]
            if value != -1:
                result.append(value)
    return result
