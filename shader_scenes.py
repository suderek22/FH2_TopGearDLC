"""Decode legacy and native track shader scenes without inventing shader programs."""
from pathlib import PureWindowsPath
import struct

from fm5_pvs import Reader
from fm5_world import boolean


def encoded_string(reader):
    size = 0
    for shift in range(0, 35, 7):
        value = reader.unpack("B")[0]
        if shift == 28 and value > 7:
            raise ValueError("Invalid shader string length")
        size |= (value & 127) << shift
        if not value & 128:
            return reader.take(size).decode("utf-8")
    raise ValueError("Unterminated shader string length")


def scene_table(data, version):
    if version not in ((1, 3), (1, 4)):
        raise ValueError("Unsupported shader scene blob version")
    reader = Reader(data, "shader light scenarios")
    inline = boolean(reader)
    count = reader.unpack("B")[0]
    scenarios = []
    for _ in range(count):
        name = encoded_string(reader)
        stamp = reader.unpack("I")[0]
        instanced = boolean(reader) if version == (1, 4) else False
        animations = reader.count(2)
        vertices = []
        for _ in range(animations):
            flags = reader.unpack("B")[0]
            path = encoded_string(reader)
            extra = [encoded_string(reader)] if instanced else None
            vertices.append({"flags": flags, "path": path, "instanced_paths": extra})
        stages = reader.unpack("i")[0]
        pixel = encoded_string(reader)
        scenarios.append({"name": name, "stamp": stamp, "instanced": instanced,
                          "vertices": vertices, "stages": stages, "pixel": pixel})
    if reader.offset != len(data):
        raise ValueError("Unparsed shader light-scenario trailer")
    if len({row["name"] for row in scenarios}) != len(scenarios):
        raise ValueError("Duplicate shader light scenario")
    return {"inline": inline, "scenarios": scenarios}


def shader_scenes(data):
    if len(data) < 16 or data[:4] != b"burG":
        raise ValueError("Expected a shader burG bundle")
    version, count, start, total = struct.unpack_from("<HHII", data, 4)
    if version != 1 or total != len(data) or not 16 + count * 24 <= start <= total:
        raise ValueError("Invalid shader bundle envelope")
    supported = {"LSCE": (1, 3), "DBLS": (1, 3), "CBMP": (2, 0), "TXMP": (2, 0),
                 "SPMP": (2, 0), "DFPR": (2, 0), "TRGT": (1, 1)}
    result = {}
    for index in range(count):
        offset = 16 + index * 24
        tag = data[offset:offset + 4][::-1].decode("ascii")
        major, minor, metadata_count, metadata, payload, size, decompressed = struct.unpack_from(
            "<BBHIIII", data, offset + 4)
        if (tag not in supported or tag in result or payload < start or payload + size > total or
                size != decompressed or metadata < 16 + count * 24 or
                metadata + metadata_count * 8 > start):
            raise ValueError("Invalid shader blob structure")
        if (major, minor) != supported[tag] and not (tag in ("LSCE", "DBLS") and (major, minor) == (1, 4)):
            raise ValueError("Unsupported track shader blob version: " + tag)
        if tag in ("LSCE", "DBLS"):
            result[tag] = {"version": [major, minor], **scene_table(data[payload:payload + size], (major, minor))}
        else:
            result[tag] = {"version": [major, minor]}
    if set(result) != set(supported):
        raise ValueError("Shader bundle omits a required native blob")
    return result


def program_members(shader_name, scenes, names):
    directory = PureWindowsPath(shader_name).parent
    members = {name.replace("\\", "/").casefold(): name for name in names}
    if len(members) != len(names):
        raise ValueError("Duplicate case-insensitive shader archive member")
    requested = set()
    for tag in ("LSCE", "DBLS"):
        for scenario in scenes[tag]["scenarios"]:
            programs = [(vertex["path"], "vso") for vertex in scenario["vertices"]]
            for vertex in scenario["vertices"]:
                if vertex["instanced_paths"]:
                    programs.append((vertex["instanced_paths"][0], "vso"))
            if scenario["stages"] & 16:
                programs.append((scenario["pixel"], "pso"))
            if scenario["stages"] & ~17:
                raise ValueError("Unsupported track shader stage combination")
            for path, extension in programs:
                stem = PureWindowsPath(path).name
                if not stem:
                    raise ValueError("Shader scenario has an empty compiled program path")
                for platform in ("durango", "pc"):
                    member = str(directory / (stem + "." + platform + "." + extension))
                    key = member.replace("\\", "/").casefold()
                    if key not in members:
                        raise ValueError("Missing compiled shader program: " + member)
                    requested.add(members[key])
    return sorted(requested)
