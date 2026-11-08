"""Validate named MLP navigation tables and native waypoint-based visibility."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import sys
import zipfile

from fm5_world import world_tables


FORMATS = {"int8": "b", "uint8": "B", "int16": "h", "uint16": "H",
           "int32": "i", "int": "i", "uint32": "I", "float": "f", "char": "B"}
GEOMETRY_FIELDS = ("fWaypointX", "fWaypointY", "fWaypointZ",
                   "fNormalX", "fNormalY", "fNormalZ",
                   "fLeftWallChi", "fRightWallChi", "fChiOptimal",
                   "fLeftCurbChi", "fRightCurbChi")


def read_mlp(data):
    marker = b"MLPDataEnd:\n"
    end = data.find(marker)
    if not data.startswith(b"MLPDataStart:\n") or end < 0 or end > 65536:
        raise ValueError("Invalid named MLP table header")
    payload_start = end + len(marker)
    try:
        lines = data[14:end].decode("ascii").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError("Non-ASCII MLP field directory") from error
    fields = {}
    spans = []
    for line in lines:
        match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_]*):([a-z0-9]+):(\d+):(\d+):\s*(\d+)", line)
        if match is None:
            raise ValueError("Invalid MLP field descriptor")
        name, kind, rows, columns, offset = match.groups()
        rows, columns, offset = int(rows), int(columns), int(offset)
        if kind not in FORMATS or name in fields or not rows or not columns:
            raise ValueError("Unsupported, empty or duplicate MLP field " + name + " (" + kind + ")")
        width = struct.calcsize("<" + FORMATS[kind])
        count = rows * columns
        limit = offset + count * width
        if count > 10000000 or offset < payload_start or limit > len(data):
            raise ValueError("MLP field exceeds its payload")
        values = list(struct.unpack_from("<%d%s" % (count, FORMATS[kind]), data, offset))
        if kind == "float" and any(not math.isfinite(value) for value in values):
            raise ValueError("Non-finite MLP field " + name)
        fields[name] = {"kind": kind, "rows": rows, "columns": columns,
                        "offset": offset, "values": values}
        spans.append((offset, limit))
    position = payload_start
    for start, limit in sorted(spans):
        if start != position:
            raise ValueError("MLP payload has overlapping fields or unparsed gaps")
        position = limit
    if position != len(data):
        raise ValueError("Unparsed MLP trailer")
    return fields


def geometry(data):
    fields = read_mlp(data)
    length = fields.get("iLength")
    kind = fields.get("iType")
    if length is None or length["kind"] != "int32" or len(length["values"]) != 1:
        raise ValueError("MLP geometry lacks an integer waypoint count")
    count = length["values"][0]
    if count < 2 or count > 1000000 or kind is None or kind["kind"] != "int8" or len(kind["values"]) != 1:
        raise ValueError("Invalid MLP geometry waypoint count/type")
    for name in GEOMETRY_FIELDS:
        row = fields.get(name)
        if row is None or row["kind"] != "float" or row["rows"] != count or row["columns"] != 1:
            raise ValueError("Invalid waypoint geometry field " + name)
    crc = fields.get("iCRC")
    if crc is not None and (crc["kind"] != "uint32" or crc["rows"] != 6 or crc["columns"] != 1):
        raise ValueError("Invalid FH2 geometry integrity field")
    return {"count": count, "type": kind["values"][0], "fields": fields,
            "positions": [list(row) for row in zip(*(fields[name]["values"]
                                                    for name in GEOMETRY_FIELDS[:3]))]}


def route_visibility(world, waypoint_count):
    step = world["grid"]
    if type(step) is not int or not 0 < step <= 65535 or waypoint_count < 2:
        raise ValueError("Invalid native waypoint visibility step/count")
    ranges = [list(struct.unpack("<2HI", bytes.fromhex(row))) for row in world["ranges"]]
    if not ranges:
        raise ValueError("Source PVS has no route visibility ranges")
    route_count = (waypoint_count + step - 1) // step
    if ranges[0] != [route_count, 0, 0]:
        raise ValueError("Waypoint count/step does not match source route zone range")
    next_zone = route_count
    for count, start, identifier in ranges[1:]:
        if count != 1 or start != next_zone or identifier == 0:
            raise ValueError("Unsupported source special-zone range")
        next_zone += count
    if next_zone != len(world["zones"]):
        raise ValueError("Source ranges do not cover all visibility zones")
    return {"waypoints_per_zone": step, "route_zone_count": route_count,
            "special_zone_count": next_zone - route_count, "ranges": ranges,
            "waypoint_zone_ids": [index // step for index in range(waypoint_count)]}


def build(source_ai, reference_ai, source_pvs, output):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a new navigation component directory: " + str(output))
    with zipfile.ZipFile(source_ai) as archive:
        entries = archive.infolist()
        names = [row.filename for row in entries]
        if len(names) != len(set(name.casefold() for name in names)):
            raise ValueError("Duplicate AI archive entry")
        payloads = {}
        tables = {}
        for entry in entries:
            if (entry.is_dir() or not re.fullmatch(r"[A-Za-z0-9_]+\.(geo|irt|rl|seg|sp)", entry.filename)
                    or entry.file_size > 16000000):
                raise ValueError("Unexpected or oversized AI archive member")
            data = archive.read(entry)
            payloads[entry.filename] = data
            tables[entry.filename] = read_mlp(data)
    if "Track.geo" not in payloads or "Track.seg" not in payloads:
        raise ValueError("Source AI lacks track geometry/segments")
    source = geometry(payloads["Track.geo"])
    with zipfile.ZipFile(reference_ai) as reference:
        target_data = reference.read("Track.geo")
        target = geometry(target_data)
    for name in ("iLength", "iType", *GEOMETRY_FIELDS):
        if source["fields"][name]["kind"] != target["fields"][name]["kind"]:
            raise ValueError("Source waypoint field type differs from FH2")
    world = world_tables(source_pvs)
    visibility = route_visibility(world, source["count"])
    segments = tables["Track.seg"]
    if (segments.get("iTrackLength", {}).get("values") != [source["count"]] or
            len(segments.get("iNumSegments", {}).get("values", [])) != 1):
        raise ValueError("Source segment track length differs from geometry")
    segment_count = segments["iNumSegments"]["values"][0]
    if segment_count <= 0:
        raise ValueError("Source track has no navigation segments")
    for name in ("iStart", "iApex", "iEnd"):
        row = segments.get(name)
        if (row is None or row["kind"] != "int32" or len(row["values"]) != segment_count or
                any(not 0 <= index < source["count"] for index in row["values"])):
            raise ValueError("Source navigation segment references an invalid waypoint")
    report = {
        "component": "source-navigation-and-native-route-visibility",
        "playable_map": False, "engine_validated": False,
        "waypoint_count": source["count"], "segment_count": segment_count,
        "route_visibility": visibility,
        "coordinate_bounds": [[min(row[i] for row in source["positions"]),
                               max(row[i] for row in source["positions"])] for i in range(3)],
        "source_waypoint_sha256": hashlib.sha256(payloads["Track.geo"]).hexdigest(),
        "reference_waypoint_sha256": hashlib.sha256(target_data).hexdigest(),
        "geometry_field_types_match_fh2": True,
        "source_has_crc": "iCRC" in source["fields"],
        "reference_has_crc": "iCRC" in target["fields"],
        "members": [{"name": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                     "fields": list(tables[name])} for name, data in payloads.items()],
        "remaining": [
            "FH2 reference geometry has iCRC integrity data absent from FM5; loader requirements remain unresolved.",
            "Named field compatibility does not prove engine navigation compatibility.",
            "Waypoint-based PVS fallback needs root registration and the original AI route in the engine.",
            "Special zones and free-roam spatial behavior must be wired separately.",
        ],
    }
    output.mkdir(parents=True)
    points = b"".join(struct.pack("<3f", *point) for point in source["positions"])
    (output / "WaypointPositions.dat").write_bytes(struct.pack("<I", source["count"]) + points)
    persisted = (output / "WaypointPositions.dat").read_bytes()
    if persisted[4:] != points or struct.unpack_from("<I", persisted)[0] != source["count"]:
        raise ValueError("Persisted waypoint component differs from source")
    report["waypoint_component_sha256"] = hashlib.sha256(persisted).hexdigest()
    report["waypoint_component_note"] = "Counted XYZ conversion component; not a native installed AI file."
    (output / "navigation.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-ai", type=Path, required=True)
    parser.add_argument("--reference-ai", type=Path, required=True)
    parser.add_argument("--source-pvs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = build(args.source_ai, args.reference_ai, args.source_pvs, args.output)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print("error: " + str(error), file=sys.stderr)
        return 1
    print("Verified %d waypoints and %d source route zones; navigation activation remains blocked." %
          (report["waypoint_count"], report["route_visibility"]["route_zone_count"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
