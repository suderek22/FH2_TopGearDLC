"""Bounded native NAVW/OWWF schemas, distinct from FM5's MLP race-route tables."""
import math
import struct


NAV_WIDTHS = (40, 24, 8, 16, 16, 8, 8, 1, 1)
NAV_LINKS = ((0, 0, 3), (0, 8, 4), (1, 0, 2), (1, 8, 4),
             (2, 0, 0), (3, 0, 1), (4, 0, 5), (4, 8, 6),
             (5, 0, 7), (6, 0, 8))
FIRST_TEST_PROPERTIES = {"ai_disabled": "true", "display_name": "Dunsfold Aerodrome",
                         "profile_name": "none_10000", "road_type": "none",
                         "traffic_density_name": "none", "traffic_disabled": "true"}
# MapProfileHud*.xml draw nav roads only for these road_type values ("none" is not drawn).
HUD_ROAD_TYPES = ("none", "dirt", "b", "a", "freeway")


def road_properties(road_type="none"):
    if road_type not in HUD_ROAD_TYPES:
        raise ValueError("Unsupported custom navigation road_type: " + str(road_type))
    return dict(FIRST_TEST_PROPERTIES, road_type=road_type)


def fm5_route_positions(positions):
    """FM5 Track.geo stores route points as (x, z, height); FH2 NAVW uses world (x, y, z)."""
    return [[float(p[0]), float(p[2]), float(p[1])] for p in positions]


def grid_covers(grid, points):
    size, cells = grid["cell_size"], {tuple(row) for row in grid["coordinates"]}
    return all((math.floor(p[0] / size), math.floor(p[2] / size)) in cells for p in points)


def envelope(data, magic, version):
    if len(data) < 32:
        raise ValueError("Truncated native navigation envelope")
    tag, actual_version, stamp, size = struct.unpack_from("<4s3I", data)
    if tag != magic or actual_version != version:
        raise ValueError("Unsupported native navigation magic/version")
    if size % 16 or size + 32 != len(data) or data[-16:] != data[:16]:
        raise ValueError("Native navigation payload size/footer disagrees")
    return stamp, data[16:-16]


def wrap(payload, magic, version, stamp):
    if type(stamp) is not int or not 0 <= stamp <= 0xFFFFFFFF:
        raise ValueError("Invalid native navigation integrity stamp")
    if len(payload) % 16:
        raise ValueError("Native navigation payload is not aligned")
    header = struct.pack("<4s3I", magic, version, stamp, len(payload))
    return header + payload + header


def read_navw(data):
    stamp, payload = envelope(data, b"WVAN", 0x100)
    if len(payload) < 112:
        raise ValueError("Truncated native navigation table header")
    counts = list(struct.unpack_from("<9i", payload, 72))
    if any(not 0 <= count <= 1000000 for count in counts):
        raise ValueError("Invalid native navigation table count")
    offset, tables = 112, []
    for count, width in zip(counts, NAV_WIDTHS):
        size = count * width
        end, aligned = offset + size, offset + ((size + 15) & ~15)
        if aligned > len(payload):
            raise ValueError("Native navigation table exceeds its payload")
        tables.append({"data": payload[offset:end].hex(), "padding": payload[end:aligned].hex()})
        offset = aligned
    if offset != len(payload):
        raise ValueError("Unparsed native navigation payload")
    return {"stamp": stamp, "header": payload[:112].hex(), "counts": counts, "tables": tables}


def write_navw(world):
    header = bytes.fromhex(world["header"])
    if len(header) != 112 or len(world["tables"]) != 9 or len(world["counts"]) != 9:
        raise ValueError("Invalid native navigation table header")
    if list(struct.unpack_from("<9i", header, 72)) != world["counts"]:
        raise ValueError("Native navigation header/table counts disagree")
    payload = bytearray(header)
    for count, width, table in zip(world["counts"], NAV_WIDTHS, world["tables"]):
        data, padding = bytes.fromhex(table["data"]), bytes.fromhex(table["padding"])
        if (type(count) is not int or not 0 <= count <= 1000000 or len(data) != count * width or
                len(padding) != (-len(data)) % 16):
            raise ValueError("Invalid native navigation table length/padding")
        payload.extend(data)
        payload.extend(padding)
    result = wrap(payload, b"WVAN", 0x100, world["stamp"])
    if read_navw(result) != world:
        raise ValueError("Native navigation serialization disagrees")
    return result


def nav_links(world):
    """Expose serialized indices; native code turns out-of-range references into null."""
    result = []
    for source, offset, target in NAV_LINKS:
        data = bytes.fromhex(world["tables"][source]["data"])
        count, nulls = world["counts"][source], 0
        for index in range(count):
            value = struct.unpack_from("<q", data, index * NAV_WIDTHS[source] + offset)[0]
            if not 0 <= value < world["counts"][target]:
                nulls += 1
        result.append({"source_table": source, "field_offset": offset, "target_table": target,
                       "reference_count": count, "native_null_reference_count": nulls})
    return result


def read_owwf(data):
    stamp, payload = envelope(data, b"FWWO", 0x102)
    if len(payload) < 16:
        raise ValueError("Truncated open-world waypoint header")
    count, radius = struct.unpack_from("<if", payload)
    if not 1 <= count <= 1000000 or not math.isfinite(radius) or radius <= 0:
        raise ValueError("Invalid open-world waypoint count/cell size")
    size = count * 4
    if len(payload) != 16 + ((size + 15) & ~15):
        raise ValueError("Open-world waypoint coordinates exceed their payload")
    coordinates = [list(row) for row in struct.iter_unpack("<2h", payload[16:16 + size])]
    if len({tuple(row) for row in coordinates}) != count:
        raise ValueError("Duplicate open-world waypoint cell")
    return {"stamp": stamp, "cell_size": radius, "reserved": payload[8:16].hex(),
            "coordinates": coordinates, "padding": payload[16 + size:].hex()}


def write_owwf(world):
    coordinates, radius = world["coordinates"], world["cell_size"]
    if not 1 <= len(coordinates) <= 1000000 or not math.isfinite(radius) or radius <= 0:
        raise ValueError("Invalid open-world waypoint count/cell size")
    reserved, padding = bytes.fromhex(world["reserved"]), bytes.fromhex(world["padding"])
    if len(reserved) != 8 or len(padding) != (-(len(coordinates) * 4)) % 16:
        raise ValueError("Invalid open-world waypoint reserved bytes/padding")
    payload = bytearray(struct.pack("<if", len(coordinates), radius) + reserved)
    for row in coordinates:
        if len(row) != 2 or any(type(value) is not int or not -32768 <= value <= 32767 for value in row):
            raise ValueError("Invalid signed open-world waypoint cell coordinate")
        payload.extend(struct.pack("<2h", *row))
    payload.extend(padding)
    result = wrap(payload, b"FWWO", 0x102, world["stamp"])
    if read_owwf(result) != world:
        raise ValueError("Open-world waypoint serialization disagrees")
    return result


def closed_road(positions, stamp, road_type="none"):
    if not 2 <= len(positions) <= 1000000 or any(
            len(row) != 3 or any(not math.isfinite(value) for value in row) for row in positions):
        raise ValueError("Invalid custom navigation road positions")
    properties = road_properties(road_type)
    names, values = sorted(properties), sorted(set(properties.values()))

    def strings(rows):
        blob, offsets = bytearray(), bytearray()
        for row in rows:
            offsets.extend(struct.pack("<q", len(blob)))
            blob.extend(row.encode("utf-8") + b"\0")
        return offsets, blob

    name_offsets, name_blob = strings(names)
    value_offsets, value_blob = strings(values)
    nodes, incidences = bytearray(), bytearray()
    count = len(positions)
    for index, position in enumerate(positions):
        start = len(incidences) // 16
        offsets = [0, count] if index == 0 else [index]
        for offset in offsets:
            incidences.extend(struct.pack("<qII", 0, offset, 0))
        nodes.extend(struct.pack("<2qI3f2I", start, -1, index, *position, len(offsets), 0))
    roads = struct.pack("<2q2I", 0, 0, count + 1, len(names))
    node_references = struct.pack("<%dq" % (count + 1), *range(count), 0)
    pairs = b"".join(struct.pack("<2q", names.index(name), values.index(properties[name])) for name in names)
    data = [nodes, roads, node_references, incidences, pairs,
            name_offsets, value_offsets, name_blob, value_blob]
    counts = [len(row) // width for row, width in zip(data, NAV_WIDTHS)]
    header = bytearray(112)
    struct.pack_into("<9i", header, 72, *counts)
    world = {"stamp": stamp, "header": header.hex(), "counts": counts,
             "tables": [{"data": bytes(row).hex(), "padding": bytes((-len(row)) % 16).hex()} for row in data]}
    validate_closed_road(world, positions, road_type)
    return world


def validate_closed_road(world, positions, road_type=None):
    tables = [bytes.fromhex(row["data"]) for row in world["tables"]]
    count = len(positions)
    if world["counts"][:4] != [count, 1, count + 1, count + 1]:
        raise ValueError("Custom closed-road navigation counts disagree")
    if list(struct.unpack("<%dq" % (count + 1), tables[2])) != [*range(count), 0]:
        raise ValueError("Custom navigation road is not closed in source order")
    start, property_start, node_count, property_count = struct.unpack("<2q2I", tables[1])
    if start != 0 or node_count != count + 1 or property_start < 0 or property_start + property_count > world["counts"][4]:
        raise ValueError("Custom navigation road/property bounds disagree")

    def strings(index, blob):
        values = []
        for (offset,) in struct.iter_unpack("<q", tables[index]):
            if not 0 <= offset < len(tables[blob]) or b"\0" not in tables[blob][offset:]:
                raise ValueError("Invalid custom navigation property string")
            values.append(tables[blob][offset:].split(b"\0", 1)[0].decode("utf-8"))
        return values

    names, values = strings(5, 7), strings(6, 8)
    properties = {}
    for name, value in struct.iter_unpack("<2q", tables[4][property_start * 16:(property_start + property_count) * 16]):
        if not 0 <= name < len(names) or not 0 <= value < len(values) or names[name] in properties:
            raise ValueError("Invalid custom navigation road property")
        properties[names[name]] = values[value]
    if road_type is None:
        road_type = properties.get("road_type")
    expected = road_properties(road_type) if road_type in HUD_ROAD_TYPES else {}
    legacy = dict(expected, display_name="Top Gear (CPS)")
    if road_type not in HUD_ROAD_TYPES or properties not in (expected, legacy):
        raise ValueError("Custom navigation road does not preserve disabled AI/traffic metadata")
    seen = set()
    for index, position in enumerate(positions):
        start, _, node_id, x, y, z, incidences, properties = struct.unpack_from("<2qI3f2I", tables[0], index * 40)
        expected = struct.unpack("<3f", struct.pack("<3f", *position))
        if node_id != index or (x, y, z) != expected or properties or start < 0:
            raise ValueError("Custom navigation node differs from its source waypoint")
        for ordinal in range(start, start + incidences):
            if ordinal >= world["counts"][3] or ordinal in seen:
                raise ValueError("Invalid or duplicate custom navigation incidence")
            road, offset = struct.unpack_from("<qI", tables[3], ordinal * 16)
            if road != 0 or not 0 <= offset <= count or (offset % count) != index:
                raise ValueError("Custom navigation incidence disagrees with its road")
            seen.add(ordinal)
    if seen != set(range(count + 1)):
        raise ValueError("Custom navigation incidence coverage is incomplete")


def waypoint_grid(points, stamp, cell_size=200.0):
    if not points or not math.isfinite(cell_size) or cell_size <= 0 or any(
            len(row) != 3 or any(not math.isfinite(value) for value in row) for row in points):
        raise ValueError("Invalid custom open-world waypoint coverage")
    columns = [math.floor(row[0] / cell_size) for row in points]
    rows = [math.floor(row[2] / cell_size) for row in points]
    left, right, bottom, top = min(columns), max(columns), min(rows), max(rows)
    if ((right - left + 1) * (top - bottom + 1) > 1000000 or
            any(not -32768 <= value <= 32767 for value in (left, right, bottom, top))):
        raise ValueError("Custom open-world waypoint grid exceeds native limits")
    coordinates = [[x, z] for z in range(bottom, top + 1) for x in range(left, right + 1)]
    return {"stamp": stamp, "cell_size": cell_size, "reserved": bytes(8).hex(),
            "coordinates": coordinates, "padding": bytes((-(len(coordinates) * 4)) % 16).hex()}
