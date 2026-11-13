"""Retarget FM5 track shader programs (DXBC SM5) to FH2's light-scenario constants.

FH2 inserts extra shadow, forward+ and AO data into lightScenarioCBuffer (cb3), so FM5
programs read fog/specular/projection values from unrelated FH2 registers. FM5 also
samples its baked direct-shadow map (instance Direct0 lightmap) from t3, which FH2
never binds, so static geometry receives no sunlight. This module remaps cb3
register indices to FH2's layout and replaces the static t3 sample with full light.
FH2 also appends a reflection plane to instanced poses (288 bytes instead of 272).
"""
import math
import struct

# FM5 cb3 register -> FH2 cb3 register (16-byte rows; layouts from both games' RDEF).
_FM5_TO_FH2_CB3 = {20: 53, 21: 54, 22: 55, 23: 56, 24: 57, 25: 104,
                   26: 1134, 27: 1135, 28: 1136, 29: 1137, 30: 1138, 31: 1139,
                   32: 1140, 33: 1141, 34: 1142, 35: 1143}
FH2_CB3_ROWS = 1144
LIGHT_LAYOUT_SLOT = 3
BAKED_SHADOW_SLOT = 3

_OP_CUSTOMDATA = 53
_OP_MOV = 54
_OP_DCL_CONSTANT_BUFFER = 89
_DECLARATIONS = set(range(88, 107)) | set(range(143, 184))
_SAMPLES = {69, 70, 71, 72, 73, 74, 45, 109, 110, 111, 126, 127, 128, 129, 130, 131,
            132, 133, 134, 135, 136, 137, 138, 139, 140, 141, 142}
_TYPE_IMMEDIATE32 = 4
_TYPE_RESOURCE = 7
_TYPE_CONSTANT_BUFFER = 8
_ONE = struct.unpack("<I", struct.pack("<f", 1.0))[0]
DEFAULT_BAKED_SHADOW_VISIBILITY = 0.15


def remap_cb3(register):
    if register < 20:
        return register
    if register not in _FM5_TO_FH2_CB3:
        raise ValueError("FM5 cb3 register %d has no FH2 equivalent" % register)
    return _FM5_TO_FH2_CB3[register]


def dxbc_checksum(data):
    """DXBC container hash: MD5 variant over bytes 20.. with custom length padding."""
    import math
    message = bytes(data[20:])
    size = len(message)
    shifts = [7, 12, 17, 22] * 4 + [5, 9, 14, 20] * 4 + [4, 11, 16, 23] * 4 + [6, 10, 15, 21] * 4
    constants = [int(abs(math.sin(i + 1)) * 2 ** 32) & 0xFFFFFFFF for i in range(64)]
    state = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476]

    def transform(block):
        words = struct.unpack("<16I", block)
        a, b, c, d = state
        for i in range(64):
            if i < 16:
                f, g = (b & c) | (~b & d), i
            elif i < 32:
                f, g = (d & b) | (~d & c), (5 * i + 1) % 16
            elif i < 48:
                f, g = b ^ c ^ d, (3 * i + 5) % 16
            else:
                f, g = c ^ (b | ~d), (7 * i) % 16
            f = (f + a + constants[i] + words[g]) & 0xFFFFFFFF
            a, d, c = d, c, b
            b = (b + ((f << shifts[i]) | (f >> (32 - shifts[i])))) & 0xFFFFFFFF
        for index, value in enumerate((a, b, c, d)):
            state[index] = (state[index] + value) & 0xFFFFFFFF

    full = size - size % 64
    for offset in range(0, full, 64):
        transform(message[offset:offset + 64])
    left = message[full:]
    bits = struct.pack("<I", size * 8)
    tail = struct.pack("<I", (size * 2) | 1)
    if len(left) >= 56:
        transform(left + b"\x80" + bytes(63 - len(left)))
        transform(bits + bytes(56) + tail)
    else:
        transform(bits + left + b"\x80" + bytes(55 - len(left)) + tail)
    return struct.pack("<4I", *state)


def _chunks(data):
    if data[:4] != b"DXBC" or struct.unpack_from("<I", data, 24)[0] != len(data):
        raise ValueError("Not a DXBC container")
    count = struct.unpack_from("<I", data, 28)[0]
    return [struct.unpack_from("<I", data, 32 + 4 * index)[0] for index in range(count)]


def _operand(tokens, at, end, visit):
    """Decode one operand at `at`; visit(kind, token_index, operand_info). Returns next index."""
    token = tokens[at]
    start = at
    at += 1
    while tokens[at - 1] & 0x80000000:
        at += 1
    components = token & 3
    kind = (token >> 12) & 0xFF
    dimension = (token >> 20) & 3
    if kind == _TYPE_IMMEDIATE32:
        return at + (4 if components == 2 else 1), None
    if kind == 5:  # IMMEDIATE64
        return at + (8 if components == 2 else 2), None
    indices = []
    for level in range(dimension):
        representation = (token >> (22 + 3 * level)) & 7
        if representation == 0:
            indices.append(("imm", at))
            at += 1
        elif representation == 1:
            indices.append(("imm64", at))
            at += 2
        elif representation == 2:
            at = _operand(tokens, at, end, visit)[0]
            indices.append(("rel", None))
        elif representation == 3:
            indices.append(("imm+rel", at))
            at = _operand(tokens, at + 1, end, visit)[0]
        elif representation == 4:
            indices.append(("imm64+rel", at))
            at = _operand(tokens, at + 2, end, visit)[0]
        else:
            raise ValueError("Unsupported operand index representation")
    if at > end:
        raise ValueError("Operand overruns its instruction")
    info = {"start": start, "end": at, "kind": kind, "indices": indices}
    visit(info)
    return at, info


def _rewrite_instructions(data, rewrite):
    """Run rewrite(opcode_token, instruction) -> instruction over SHEX code; rebuild DXBC."""
    data = bytearray(data)
    offsets = _chunks(data)
    shader = [offset for offset in offsets if data[offset:offset + 4] in (b"SHEX", b"SHDR")]
    if len(shader) != 1:
        raise ValueError("Expected one shader bytecode chunk")
    chunk = shader[0]
    chunk_size = struct.unpack_from("<I", data, chunk + 4)[0]
    tokens = list(struct.unpack_from("<%dI" % (chunk_size // 4), data, chunk + 8))
    if tokens[1] != len(tokens):
        raise ValueError("Shader token count mismatch")
    output = tokens[:2]
    at = 2
    while at < len(tokens):
        opcode = tokens[at] & 0x7FF
        length = tokens[at + 1] if opcode == _OP_CUSTOMDATA else (tokens[at] >> 24) & 0x7F
        if not length or at + length > len(tokens):
            raise ValueError("Invalid instruction length")
        instruction = tokens[at:at + length]
        if opcode not in _DECLARATIONS and opcode != _OP_CUSTOMDATA:
            instruction = rewrite(instruction)
        output.extend(instruction)
        at += length
    if output == tokens:
        return bytes(data)
    output[1] = len(output)
    new_chunk = struct.pack("<4sI", data[chunk:chunk + 4], 4 * len(output)) + struct.pack("<%dI" % len(output), *output)
    delta = len(new_chunk) - (8 + chunk_size)
    result = bytearray(data[:chunk]) + new_chunk + data[chunk + 8 + chunk_size:]
    for index, offset in enumerate(offsets):
        if offset > chunk:
            struct.pack_into("<I", result, 32 + 4 * index, offset + delta)
    struct.pack_into("<I", result, 24, len(result))
    result[4:20] = dxbc_checksum(result)
    return bytes(result)


def bake_constants(data, slot, rows):
    """Replace reads of cb<slot>[row] with literal vec4 immediates; rows = {row: (x, y, z, w)}.

    FM5 grass_blend reads its fade start/end distances from trackParamsCBuffer (cb7)
    row 1, which FH2 never fills. Returns (patched DXBC, replaced operand count).
    """
    replaced = [0]

    def rewrite(instruction):
        first = 1
        while instruction[first - 1] & 0x80000000:
            first += 1
        operands = []
        position = first
        while position < len(instruction):
            position, info = _operand(instruction, position, len(instruction), lambda info: None)
            operands.append(info)
        result = instruction[:first]
        cursor = first
        changed = False
        for info in operands:
            if info is None or not (info["kind"] == _TYPE_CONSTANT_BUFFER and len(info["indices"]) == 2 and
                                    all(kind == "imm" for kind, _ in info["indices"]) and
                                    instruction[info["indices"][0][1]] == slot):
                continue
            row = instruction[info["indices"][1][1]]
            if row not in rows:
                raise ValueError("cb%d[%d] has no baked value" % (slot, row))
            token = instruction[info["start"]]
            modifier = (instruction[info["start"] + 1] >> 6) & 0xFF if token & 0x80000000 else 0
            mode = (token >> 2) & 3
            if mode == 1:
                lanes = [(token >> (4 + 2 * lane)) & 3 for lane in range(4)]
            elif mode == 2:
                lanes = [(token >> 4) & 3] * 4
            else:
                raise ValueError("Unsupported constant operand selection")
            values = []
            for lane in lanes:
                value = float(rows[row][lane])
                value = abs(value) if modifier in (2, 3) else value
                values.append(-value if modifier in (1, 3) else value)
            result.extend(instruction[cursor:info["start"]])
            result.extend([2 | (_TYPE_IMMEDIATE32 << 12)] + [struct.unpack("<I", struct.pack("<f", v))[0] for v in values])
            cursor = info["end"]
            replaced[0] += 1
            changed = True
        if not changed:
            return instruction
        result.extend(instruction[cursor:])
        result[0] = (result[0] & ~(0x7F << 24)) | (len(result) << 24)
        return result

    return _rewrite_instructions(data, rewrite), replaced[0]


def offset_world_height(data, metres):
    """Offset a static vertex shader's world-position accumulator before projection."""
    import math
    if not math.isfinite(metres) or metres <= 0:
        raise ValueError("World height offset must be finite and positive")
    value = struct.unpack("<I", struct.pack("<f", metres))[0]
    replaced = 0

    def rewrite(instruction):
        nonlocal replaced
        if instruction[0] & 0x7FF != 17:
            return instruction
        operands = []
        position = 1
        while position < len(instruction):
            position, info = _operand(instruction, position, len(instruction), lambda info: None)
            operands.append(info)
        if len(operands) != 3 or any(info is None for info in operands):
            return instruction
        destination, world, matrix = operands
        if (destination["kind"] != 2 or world["kind"] != 0 or matrix["kind"] != _TYPE_CONSTANT_BUFFER or
                len(matrix["indices"]) != 2 or
                any(kind != "imm" for kind, _ in matrix["indices"]) or
                [instruction[index] for _, index in matrix["indices"]] != [2, 4]):
            return instruction
        source = instruction[world["start"]:world["end"]]
        if len(source) != 2 or source[0] != 0x00100E46:
            raise ValueError("Unsupported world-position accumulator")
        replaced += 1
        return [7 << 24, 0x00100022, source[1], 0x0010001A, source[1], 0x4001, value] + instruction

    return _rewrite_instructions(data, rewrite), replaced


def patch_pose_stride(data):
    """Retarget t64 pose declarations and structured loads to FH2's 288-byte records."""
    result = bytearray(data)
    chunk = next(offset for offset in _chunks(result)
                 if result[offset:offset + 4] in (b"SHEX", b"SHDR"))
    size = struct.unpack_from("<I", result, chunk + 4)[0]
    tokens = list(struct.unpack_from("<%dI" % (size // 4), result, chunk + 8))
    if tokens[1] != len(tokens):
        raise ValueError("Shader token count mismatch")
    at = 2
    stats = {"pose_declarations": 0, "pose_loads": 0}
    while at < len(tokens):
        opcode = tokens[at] & 0x7FF
        length = tokens[at + 1] if opcode == _OP_CUSTOMDATA else (tokens[at] >> 24) & 0x7F
        if not length or at + length > len(tokens):
            raise ValueError("Invalid instruction length")
        if opcode == 162 and length == 4 and tokens[at + 1:at + 3] == [0x00107000, 64]:
            if tokens[at + 3] not in (272, 288):
                raise ValueError("Unexpected instancing pose stride")
            if tokens[at + 3] == 272:
                tokens[at + 3] = 288
                stats["pose_declarations"] += 1
        elif opcode == 167:
            first = at + 1
            extensions = []
            while tokens[first - 1] & 0x80000000:
                extensions.append(first)
                first += 1
            operands = []
            position = first
            while position < at + length:
                position, info = _operand(tokens, position, at + length, lambda info: None)
                operands.append(info)
            pose = any(info and info["kind"] == _TYPE_RESOURCE and len(info["indices"]) == 1 and
                       info["indices"][0][0] == "imm" and tokens[info["indices"][0][1]] == 64
                       for info in operands)
            if pose:
                dimensions = [index for index in extensions if tokens[index] & 63 == 2]
                if len(dimensions) != 1:
                    raise ValueError("Pose load omits its resource dimension extension")
                index = dimensions[0]
                stride = (tokens[index] >> 11) & 0xFFF
                if stride not in (272, 288):
                    raise ValueError("Unexpected structured pose load stride")
                if stride == 272:
                    tokens[index] = (tokens[index] & ~(0xFFF << 11)) | (288 << 11)
                    stats["pose_loads"] += 1
        at += length
    if any(stats.values()):
        struct.pack_into("<%dI" % len(tokens), result, chunk + 8, *tokens)
        result[4:20] = dxbc_checksum(result)
    return bytes(result), stats


def neutralize_texture_rgb(data, slot):
    """Set a sampled macro tint's RGB to white, preserving alpha used by blend masks."""
    replaced = 0

    def rewrite(instruction):
        nonlocal replaced
        if instruction[0] & 0x7FF not in _SAMPLES:
            return instruction
        first = 1
        while instruction[first - 1] & 0x80000000:
            first += 1
        operands = []
        position = first
        while position < len(instruction):
            position, info = _operand(instruction, position, len(instruction), lambda info: None)
            operands.append(info)
        resources = [info for info in operands[1:] if info and info["kind"] == _TYPE_RESOURCE]
        if not resources:
            return instruction
        resource = resources[0]
        if len(resource["indices"]) != 1 or resource["indices"][0][0] != "imm":
            raise ValueError("Unsupported macro tint resource index")
        if instruction[resource["indices"][0][1]] != slot:
            return instruction
        destination = operands[0]
        if destination is None or destination["kind"] != 0:
            raise ValueError("Macro tint sample must write a temporary register")
        tokens = instruction[destination["start"]:destination["end"]]
        if tokens[0] & 3 != 2 or (tokens[0] >> 2) & 3 != 0:
            raise ValueError("Macro tint destination must use a component mask")
        mask = (tokens[0] >> 4) & 7
        if not mask:
            return instruction
        tokens[0] = (tokens[0] & ~(15 << 4)) | (mask << 4)
        literal = [2 | (_TYPE_IMMEDIATE32 << 12), _ONE, _ONE, _ONE, _ONE]
        replaced += 1
        return instruction + [_OP_MOV | ((1 + len(tokens) + len(literal)) << 24)] + tokens + literal

    return _rewrite_instructions(data, rewrite), replaced


def _neutralize_texture_samples(data, slot, value=_ONE):
    """Replace one texture input with a constant (white by default), keeping destination masks."""
    replaced = 0

    def rewrite(instruction):
        nonlocal replaced
        if instruction[0] & 0x7FF not in _SAMPLES:
            return instruction
        first = 1
        while instruction[first - 1] & 0x80000000:
            first += 1
        operands = []
        position = first
        while position < len(instruction):
            position, info = _operand(instruction, position, len(instruction), lambda _: None)
            operands.append(info)
        resources = [info for info in operands[1:] if info and info["kind"] == _TYPE_RESOURCE]
        if not resources:
            return instruction
        resource = resources[0]
        if len(resource["indices"]) != 1 or resource["indices"][0][0] != "imm":
            raise ValueError("Unsupported neutralized texture resource index")
        if instruction[resource["indices"][0][1]] != slot:
            return instruction
        destination = operands[0]
        if destination is None or destination["kind"] != 0:
            raise ValueError("Neutralized texture sample must write a temporary register")
        target = instruction[destination["start"]:destination["end"]]
        literal = [2 | (_TYPE_IMMEDIATE32 << 12), value, value, value, value]
        replaced += 1
        return [_OP_MOV | ((1 + len(target) + len(literal)) << 24)] + target + literal

    return _rewrite_instructions(data, rewrite), replaced


_HALF = 0x3F000000

NORMAL_TEXTURE_RESOURCES = (
    b"RoadNormalTextureTexture", b"Ch5RoadLinesNormalTextureTexture", b"Gravel_NormalTexture",
    b"NormalTexture", b"NormalATexture", b"MacroNormalTexture", b"Ch2NormalMapTexture",
    b"NormalMapTexture")


def flatten_normal_samples(data, resource_names=NORMAL_TEXTURE_RESOURCES):
    """Replace tangent-space normal map samples with 0.5 (a flat normal after *2-1 decoding)."""
    total = 0
    for resource_name in resource_names:
        slot = _named_texture_slot(data, resource_name)
        if slot is not None:
            data, count = _neutralize_texture_samples(data, slot, _HALF)
            total += count
    return data, total


def neutralize_legacy_ao(data):
    """Replace FM5's t6 baked AO samples; FH2 uses terrain AO at t107 instead."""
    return _neutralize_texture_samples(data, 6)


def _neutralize_texture_channel(data, slot, channel):
    """Force one source channel of a texture to 1 after each sample; other channels are kept."""
    replaced = 0

    def rewrite(instruction):
        nonlocal replaced
        if instruction[0] & 0x7FF not in _SAMPLES:
            return instruction
        first = 1
        while instruction[first - 1] & 0x80000000:
            first += 1
        operands = []
        position = first
        while position < len(instruction):
            position, info = _operand(instruction, position, len(instruction), lambda _: None)
            operands.append(info)
        resources = [info for info in operands[1:] if info and info["kind"] == _TYPE_RESOURCE]
        if not resources:
            return instruction
        resource = resources[0]
        if len(resource["indices"]) != 1 or resource["indices"][0][0] != "imm":
            raise ValueError("Unsupported neutralized texture resource index")
        if instruction[resource["indices"][0][1]] != slot:
            return instruction
        destination = operands[0]
        if destination is None or destination["kind"] != 0:
            raise ValueError("Neutralized texture sample must write a temporary register")
        target = list(instruction[destination["start"]:destination["end"]])
        if target[0] & 3 != 2 or (target[0] >> 2) & 3 != 0:
            raise ValueError("Neutralized texture destination must use a component mask")
        source = instruction[resource["start"]]
        if (source >> 2) & 3 != 1:
            raise ValueError("Neutralized texture resource must use a swizzle")
        mask = (target[0] >> 4) & 15
        selected = 0
        for component in range(4):
            if mask & (1 << component) and (source >> (4 + 2 * component)) & 3 == channel:
                selected |= 1 << component
        if not selected:
            return instruction
        target[0] = (target[0] & ~(15 << 4)) | (selected << 4)
        literal = [2 | (_TYPE_IMMEDIATE32 << 12), _ONE, _ONE, _ONE, _ONE]
        replaced += 1
        return instruction + [_OP_MOV | ((1 + len(target) + len(literal)) << 24)] + target + literal

    return _rewrite_instructions(data, rewrite), replaced


def _named_texture_slot(data, resource_name):
    chunks = [offset for offset in _chunks(data) if data[offset:offset + 4] == b"RDEF"]
    if len(chunks) != 1:
        raise ValueError("Expected one shader reflection chunk")
    offset = chunks[0]
    size = struct.unpack_from("<I", data, offset + 4)[0]
    reflection = data[offset + 8:offset + 8 + size]
    count, start = struct.unpack_from("<2I", reflection, 8)
    slots = []
    for index in range(count):
        name_offset, kind, _, dimension, _, slot, length, _ = struct.unpack_from(
            "<8I", reflection, start + index * 32)
        end = reflection.find(b"\0", name_offset)
        if reflection[name_offset:end] == resource_name and kind == 2 and dimension == 4:
            slots.append(slot)
    if len(slots) > 1:
        raise ValueError("Duplicate texture resource binding")
    return slots[0] if slots else None


def neutralize_ao_mask_channel(data, resource_names=(b"AOMaskTextureTexture",)):
    """Force the AO (red) channel of AO mask textures to 1, keeping decal alpha in other channels."""
    total = 0
    for resource_name in resource_names:
        slot = _named_texture_slot(data, resource_name)
        if slot is not None:
            data, count = _neutralize_texture_channel(data, slot, 0)
            total += count
    return data, total


def neutralize_cubemap_ao(data):
    """Neutralize the named legacy AO input, which cubemap shaders bind at t4, not t6."""
    return _neutralize_named_texture(data, b"AOMap")


def _neutralize_named_texture(data, resource_name):
    chunks = [offset for offset in _chunks(data) if data[offset:offset + 4] == b"RDEF"]
    if len(chunks) != 1:
        raise ValueError("Expected one shader reflection chunk")
    offset = chunks[0]
    size = struct.unpack_from("<I", data, offset + 4)[0]
    reflection = data[offset + 8:offset + 8 + size]
    if len(reflection) < 16:
        raise ValueError("Truncated shader reflection header")
    count, start = struct.unpack_from("<2I", reflection, 8)
    if start + count * 32 > len(reflection):
        raise ValueError("Invalid shader reflection resource table")
    slots = []
    for index in range(count):
        name_offset, kind, _, dimension, _, slot, length, _ = struct.unpack_from(
            "<8I", reflection, start + index * 32)
        end = reflection.find(b"\0", name_offset)
        if end < 0:
            raise ValueError("Invalid shader reflection resource name")
        if reflection[name_offset:end] == resource_name:
            if kind != 2 or dimension != 4 or length != 1:
                raise ValueError("Expected one 2D mask texture")
            slots.append(slot)
    if not slots:
        return data, 0
    if len(slots) != 1:
        raise ValueError("Duplicate mask resource binding")
    return _neutralize_texture_samples(data, slots[0])


def guard_marking_normal(data):
    """Keep RoadLineDecal normal reconstruction finite and nonzero before normalization."""
    if b"Ch5RoadLinesNormalTextureTexture\0" not in data:
        raise ValueError("Expected the RoadLineDecal normal texture binding")
    square_root = [75 | (5 << 24), 0x00100082, 2, 0x0010003A, 2]
    epsilon = struct.unpack("<I", struct.pack("<f", 0.000001))[0]
    guard = [52 | (7 << 24), 0x00100082, 2, 0x0010003A, 2, 0x4001, epsilon]
    previous = None
    found = False
    replaced = 0

    def rewrite(instruction):
        nonlocal previous, found, replaced
        if instruction[0] & 0x7FF == 75 and not found:
            if instruction != square_root:
                raise ValueError("Unsupported RoadLineDecal normal reconstruction")
            found = True
            if previous != guard:
                replaced += 1
                return guard + instruction
        previous = instruction
        return instruction

    patched = _rewrite_instructions(data, rewrite)
    if not found:
        raise ValueError("Missing RoadLineDecal normal reconstruction")
    return patched, replaced


def guard_normal_reconstructions(data):
    """Clamp every `sqrt(1 - x*x - y*y)` normal-Z reconstruction input to a small positive value.

    Filtered/mipped normal-map texels can have x*x + y*y > 1; the negative radicand turns the
    reconstructed normal into NaN and the whole lit pixel black, depending on view distance/angle.
    """
    epsilon = struct.unpack("<I", struct.pack("<f", 0.000001))[0]
    previous = None
    replaced = 0

    def temp_component(token, index, selector):
        if (token >> 12) & 0xFF != 0 or (token >> 20) & 3 != 1 or (token >> 22) & 7 or (token >> 31):
            return None
        if selector:
            return (index, (token >> 4) & 3) if (token >> 2) & 3 == 2 and token & 3 == 2 else None
        mask = (token >> 4) & 0xF
        if (token >> 2) & 3 != 0 or token & 3 != 2 or mask not in (1, 2, 4, 8):
            return None
        return index, mask.bit_length() - 1

    def rewrite(instruction):
        nonlocal previous, replaced
        last, previous = previous, instruction
        if instruction[0] & 0x7FF != 75 or len(instruction) != 5 or instruction[0] & 0x80003800:
            return instruction
        radicand = temp_component(instruction[3], instruction[4], True)
        if radicand is None or last is None or last[0] & 0x7FF != 50 or last[0] & 0x80003800:
            return instruction
        if last[1] >> 31 or temp_component(last[1], last[2], False) != radicand:
            return instruction
        if not (last[3] >> 31 and last[4] & 0x3F == 1 and (last[4] >> 6) & 0xFF == 1):
            return instruction
        register, component = radicand
        guard = [52 | (7 << 24), 0x00100002 | (0x10 << component), register,
                 0x0010000A | (component << 4), register, 0x4001, epsilon]
        replaced += 1
        return guard + instruction

    return _rewrite_instructions(data, rewrite), replaced


def diagnostic_output_color(data, rgb):
    """Diagnostic only: force o0.rgb to a flat colour after every write to o0 (alpha kept)."""
    literal = [struct.unpack("<I", struct.pack("<f", value))[0] for value in (*rgb, 1.0)]
    replaced = 0

    def rewrite(instruction):
        nonlocal replaced
        first = 1
        while instruction[first - 1] & 0x80000000:
            first += 1
        if first >= len(instruction):
            return instruction
        token = instruction[first]
        if (token >> 12) & 0xFF != 2 or (token >> 20) & 3 != 1 or (token >> 22) & 7:
            return instruction
        if instruction[first + 1] != 0 or not (token >> 4) & 7:
            return instruction
        replaced += 1
        force = [_OP_MOV | (8 << 24), 0x00102072, 0, 2 | (_TYPE_IMMEDIATE32 << 12)] + literal
        return instruction + force

    return _rewrite_instructions(data, rewrite), replaced


def _temp_count(data):
    offsets = [offset for offset in _chunks(data) if data[offset:offset + 4] in (b"SHEX", b"SHDR")]
    size = struct.unpack_from("<I", data, offsets[0] + 4)[0]
    tokens = struct.unpack_from("<%dI" % (size // 4), data, offsets[0] + 8)
    at = 2
    while at < len(tokens):
        opcode = tokens[at] & 0x7FF
        if opcode == 104:
            return offsets[0] + 8 + 4 * (at + 1), tokens[at + 1]
        at += tokens[at + 1] if opcode == _OP_CUSTOMDATA else (tokens[at] >> 24) & 0x7F
    return None, 0


def diagnostic_nan_output(data, rgb=(0.0, 1.0, 0.0)):
    """Diagnostic only: pixels whose o0.rgb would be NaN are written as a flat colour instead."""
    position, temps = _temp_count(data)
    if position is None:
        return data, 0
    value, flag = temps, temps + 1
    literal = [struct.unpack("<I", struct.pack("<f", v))[0] for v in (*rgb, 0.0)]
    replaced = 0

    def rewrite(instruction):
        nonlocal replaced
        first = 1
        while instruction[first - 1] & 0x80000000:
            first += 1
        if first >= len(instruction) or instruction[0] & 0x7FF in (_OP_CUSTOMDATA,):
            return instruction
        token = instruction[first]
        if (token >> 12) & 0xFF != 2 or (token >> 20) & 3 != 1 or (token >> 22) & 7:
            return instruction
        mask = (token >> 4) & 0xF
        if instruction[first + 1] != 0 or token & 0xC or not mask & 7:
            return instruction
        replaced += 1
        redirected = list(instruction)
        redirected[first] = token & ~(0xFF << 12)
        redirected[first + 1] = value
        all_value = [0x00100E46, value]
        test = [57 | (7 << 24), 0x00100072, flag] + all_value + all_value
        merge = [60 | (7 << 24), 0x00100012, flag, 0x0010000A, flag, 0x0010001A, flag,
                 60 | (7 << 24), 0x00100012, flag, 0x0010000A, flag, 0x0010002A, flag]
        select = [55 | (12 << 24), 0x00102002 | (mask << 4), 0, 0x00100006, flag,
                  2 | (_TYPE_IMMEDIATE32 << 12)] + literal + all_value
        return redirected + test + merge + select

    patched = bytearray(_rewrite_instructions(data, rewrite))
    if replaced:
        position, _ = _temp_count(bytes(patched))
        struct.pack_into("<I", patched, position, temps + 2)
        patched[4:20] = dxbc_checksum(patched)
    return bytes(patched), replaced


def neutralize_skid_mask(data):
    """Disable the FM5 pavement skid mask, resolving its slot from DXBC reflection."""
    return _neutralize_named_texture(data, b"SkidmaskTextureTexture")


def neutralize_pavement_ao_mask(data):
    """Replace the blended pavement's unsupported material AO-mask input with white."""
    return _neutralize_named_texture(data, b"AOMaskTextureTexture")


def _baked_shadow_destinations(data):
    offsets = _chunks(data)
    shaders = [offset for offset in offsets if data[offset:offset + 4] in (b"SHEX", b"SHDR")]
    if len(shaders) != 1:
        raise ValueError("Expected one shader bytecode chunk")
    chunk = shaders[0]
    chunk_size = struct.unpack_from("<I", data, chunk + 4)[0]
    tokens = list(struct.unpack_from("<%dI" % (chunk_size // 4), data, chunk + 8))
    if tokens[1] != len(tokens):
        raise ValueError("Shader token count mismatch")
    if (tokens[0] >> 16) != 0:
        return []
    destinations = []
    at = 2
    while at < len(tokens):
        opcode_token = tokens[at]
        opcode = opcode_token & 0x7FF
        length = tokens[at + 1] if opcode == _OP_CUSTOMDATA else (opcode_token >> 24) & 0x7F
        if not length or at + length > len(tokens):
            raise ValueError("Invalid instruction length")
        instruction = tokens[at:at + length]
        if opcode in _SAMPLES:
            position = 1
            while instruction[position - 1] & 0x80000000:
                position += 1
            operands = []
            while position < length:
                position, info = _operand(instruction, position, length, lambda _: None)
                operands.append(info)
            resources = [info for info in operands[1:]
                         if info and info["kind"] == _TYPE_RESOURCE]
            if resources:
                resource = resources[0]
                if (len(resource["indices"]) != 1 or
                        resource["indices"][0][0] != "imm"):
                    raise ValueError("Unsupported baked-shadow resource index")
                if instruction[resource["indices"][0][1]] == BAKED_SHADOW_SLOT:
                    destination = operands[0]
                    if destination is None:
                        raise ValueError("Unexpected baked shadow sample form")
                    destinations.append(tuple(instruction[destination["start"]:destination["end"]]))
        at += length
    return destinations


def scale_baked_shadow_fallback(original_data, patched_data, visibility):
    """Scale only the full-visibility MOVs inserted for original t3 shadow samples."""
    if (isinstance(visibility, bool) or not isinstance(visibility, (int, float)) or
            not math.isfinite(visibility) or not 0 <= visibility <= 1):
        raise ValueError("Baked-shadow visibility must be finite and in [0, 1]")
    targets = _baked_shadow_destinations(original_data)
    if not targets:
        return patched_data, 0
    remaining = list(targets)
    scalar = struct.unpack("<I", struct.pack("<f", float(visibility)))[0]
    changed = [0]

    def rewrite(instruction):
        if instruction[0] & 0x7FF != _OP_MOV:
            return instruction
        position, destination = _operand(instruction, 1, len(instruction), lambda _: None)
        if destination is None:
            return instruction
        target = tuple(instruction[destination["start"]:destination["end"]])
        if target not in remaining:
            return instruction
        if position >= len(instruction) or (instruction[position] >> 12) & 0xFF != _TYPE_IMMEDIATE32:
            return instruction
        source_token = instruction[position]
        if source_token & 3 != 2 or position + 5 != len(instruction):
            return instruction
        values = instruction[position + 1:position + 5]
        if len(set(values)) != 1:
            return instruction
        current_value = struct.unpack("<f", struct.pack("<I", values[0]))[0]
        if not math.isfinite(current_value) or not 0 <= current_value <= 1:
            return instruction
        del remaining[remaining.index(target)]
        if values == [scalar] * 4:
            return instruction
        changed[0] += 1
        return instruction[:position + 1] + [scalar] * 4

    patched = _rewrite_instructions(patched_data, rewrite)
    if remaining:
        raise ValueError("Could not uniquely locate every baked-shadow fallback in patched shader")
    return patched, changed[0]


def _code_instructions(data):
    data = bytes(data)
    shader = [offset for offset in _chunks(data) if data[offset:offset + 4] in (b"SHEX", b"SHDR")]
    if len(shader) != 1:
        raise ValueError("Expected one shader bytecode chunk")
    chunk_size = struct.unpack_from("<I", data, shader[0] + 4)[0]
    tokens = list(struct.unpack_from("<%dI" % (chunk_size // 4), data, shader[0] + 8))
    rows, at = [], 2
    while at < len(tokens):
        opcode = tokens[at] & 0x7FF
        length = tokens[at + 1] if opcode == _OP_CUSTOMDATA else (tokens[at] >> 24) & 0x7F
        if not length or at + length > len(tokens):
            raise ValueError("Invalid instruction length")
        if opcode not in _DECLARATIONS and opcode != _OP_CUSTOMDATA:
            rows.append(tokens[at:at + length])
        at += length
    return rows


def _sample_info(instruction):
    """(destination tokens, resource slot) for a sample instruction, else None."""
    if instruction[0] & 0x7FF not in _SAMPLES:
        return None
    position = 1
    while instruction[position - 1] & 0x80000000:
        position += 1
    operands = []
    while position < len(instruction):
        position, info = _operand(instruction, position, len(instruction), lambda _: None)
        operands.append(info)
    resources = [info for info in operands[1:] if info and info["kind"] == _TYPE_RESOURCE]
    if not resources or operands[0] is None or resources[0]["indices"][0][0] != "imm":
        return None
    return (tuple(instruction[operands[0]["start"]:operands[0]["end"]]),
            instruction[resources[0]["indices"][0][1]])


def _uniform_mov(instruction):
    """(destination tokens, literal bits) for `mov rN.m, l(v,v,v,v)`, else None."""
    if instruction[0] & 0x7FF != _OP_MOV or instruction[0] & 0x80000000:
        return None
    position, destination = _operand(instruction, 1, len(instruction), lambda _: None)
    if destination is None or position + 5 != len(instruction):
        return None
    token = instruction[position]
    values = instruction[position + 1:position + 5]
    if (token >> 12) & 0xFF != _TYPE_IMMEDIATE32 or token & 3 != 2 or len(set(values)) != 1:
        return None
    return tuple(instruction[destination["start"]:destination["end"]]), values[0]


def realign_baked_shadow_fallback(original_data, patched_data, visibility):
    """Repair fallbacks that scale_baked_shadow_fallback put on the wrong replaced sample.

    That pass matched t3 destinations by register only, so in shaders where an earlier
    neutralized sample (e.g. the skid mask) wrote the same register it scaled that one:
    the skid/AO input became `visibility` (darkening diffuse) and the real t3 stayed 1.0.
    Original and patched instructions are aligned in order (later passes only replace
    samples with MOVs or insert extra instructions); each replaced t3 sample gets
    `visibility`, every other replaced sample that holds `visibility` is reset to 1.0.
    Returns (data, {"shadow": n, "restored": n}).
    """
    original = _code_instructions(original_data)
    patched = _code_instructions(patched_data)
    scalar = struct.unpack("<I", struct.pack("<f", float(visibility)))[0]
    fixes, j = {}, 0
    for row in original:
        sample = _sample_info(row)
        while j < len(patched):
            candidate = patched[j]
            if candidate == row or (candidate[0] & 0x7FF == row[0] & 0x7FF and
                                    len(candidate) == len(row) and sample is None):
                break
            mov = _uniform_mov(candidate)
            if sample and mov and mov[0] == sample[0]:
                break
            if sample and _sample_info(candidate) and _sample_info(candidate)[1] == sample[1]:
                break
            j += 1
        if j >= len(patched):
            raise ValueError("Could not align patched shader with its FM5 original")
        mov = _uniform_mov(patched[j]) if sample else None
        if mov:
            wanted = scalar if sample[1] == BAKED_SHADOW_SLOT else (_ONE if mov[1] == scalar else mov[1])
            if wanted != mov[1]:
                fixes[j] = wanted
        j += 1
    if not fixes:
        return bytes(patched_data), {"shadow": 0, "restored": 0}
    counts = {"shadow": 0, "restored": 0}
    index = [-1]

    def rewrite(instruction):
        index[0] += 1
        if index[0] not in fixes:
            return instruction
        counts["shadow" if fixes[index[0]] == scalar else "restored"] += 1
        return instruction[:-4] + [fixes[index[0]]] * 4

    return _rewrite_instructions(patched_data, rewrite), counts


def set_uniform_mov_literal(data, old_value, new_value):
    """Replace every MOV whose immediate source is a uniform `old_value` with `new_value`."""
    old = struct.unpack("<I", struct.pack("<f", float(old_value)))[0]
    new = struct.unpack("<I", struct.pack("<f", float(new_value)))[0]
    replaced = 0

    def rewrite(instruction):
        nonlocal replaced
        mov = _uniform_mov(instruction)
        if not mov or mov[1] != old:
            return instruction
        replaced += 1
        return instruction[:-4] + [new] * 4

    return _rewrite_instructions(data, rewrite), replaced


SUN_COLOUR_ROW = 1
_OP_MAD, _OP_MUL = 50, 56


def neutral_sun_colour(data):
    """Read the lit-scenario sun colour (cb3[1].rgb) as its green channel (neutral grey).

    FH2's global time-of-day sun is yellow (~0.69/0.60/0.43 at 13:30); green tracks its
    luminance, so this removes the tint without changing brightness. Only mul/mad sources
    with a 4-component swizzle are touched (depth scenarios reuse cb3[1].x as a flag).
    """
    replaced = 0

    def rewrite(instruction):
        nonlocal replaced
        if instruction[0] & 0x7FF not in (_OP_MAD, _OP_MUL):
            return instruction
        first = 1
        while instruction[first - 1] & 0x80000000:
            first += 1
        operands = []
        position = first
        while position < len(instruction):
            position, info = _operand(instruction, position, len(instruction), lambda info: None)
            operands.append(info)
        patched = list(instruction)
        for info in operands[1:]:
            if (not info or info["kind"] != _TYPE_CONSTANT_BUFFER or len(info["indices"]) != 2 or
                    any(kind != "imm" for kind, _ in info["indices"]) or
                    [instruction[at] for _, at in info["indices"]] != [LIGHT_LAYOUT_SLOT, SUN_COLOUR_ROW]):
                continue
            token = instruction[info["start"]]
            if token & 3 != 2 or (token >> 2) & 3 != 1:
                raise ValueError("Sun colour read must use a 4-component swizzle")
            patched[info["start"]] = (token & ~(0xFF << 4)) | (0x55 << 4)
            replaced += 1
        return patched

    return _rewrite_instructions(data, rewrite), replaced


def patch_program(data, replace_baked_shadow,
                  baked_shadow_visibility=DEFAULT_BAKED_SHADOW_VISIBILITY):
    """Return (patched DXBC, stats). Raises ValueError on unsupported bytecode."""
    if (isinstance(baked_shadow_visibility, bool) or
            not isinstance(baked_shadow_visibility, (int, float)) or
            not math.isfinite(baked_shadow_visibility) or not 0 <= baked_shadow_visibility <= 1):
        raise ValueError("Baked-shadow visibility must be finite and in [0, 1]")
    data = bytearray(data)
    offsets = _chunks(data)
    shader = [offset for offset in offsets if data[offset:offset + 4] in (b"SHEX", b"SHDR")]
    if len(shader) != 1:
        raise ValueError("Expected one shader bytecode chunk")
    chunk = shader[0]
    chunk_size = struct.unpack_from("<I", data, chunk + 4)[0]
    body = chunk + 8
    tokens = list(struct.unpack_from("<%dI" % (chunk_size // 4), data, body))
    if tokens[1] != len(tokens):
        raise ValueError("Shader token count mismatch")
    is_pixel = (tokens[0] >> 16) == 0
    output = tokens[:2]
    stats = {"cb3_refs": 0, "baked_shadow_samples": 0}
    at = 2
    while at < len(tokens):
        opcode_token = tokens[at]
        opcode = opcode_token & 0x7FF
        if opcode == _OP_CUSTOMDATA:
            length = tokens[at + 1]
        else:
            length = (opcode_token >> 24) & 0x7F
        if not length or at + length > len(tokens):
            raise ValueError("Invalid instruction length")
        original_length = length
        instruction = tokens[at:at + length]
        if opcode == _OP_DCL_CONSTANT_BUFFER:
            operand = instruction[1]
            if ((operand >> 12) & 0xFF) == _TYPE_CONSTANT_BUFFER and instruction[2] == LIGHT_LAYOUT_SLOT:
                rows = instruction[3]
                instruction[3] = max(remap_cb3(row) for row in range(rows)) + 1
        elif opcode not in _DECLARATIONS and opcode != _OP_CUSTOMDATA:
            first = 1
            while instruction[first - 1] & 0x80000000:
                first += 1
            operands = []
            references = []

            def visit(info, local=instruction):
                if info["kind"] == _TYPE_CONSTANT_BUFFER and len(info["indices"]) == 2:
                    slot = info["indices"][0]
                    if slot[0] != "imm":
                        raise ValueError("Dynamic constant buffer slot")
                    if local[slot[1]] == LIGHT_LAYOUT_SLOT:
                        register = info["indices"][1]
                        if register[0] != "imm":
                            raise ValueError("Dynamically indexed FM5 light constants")
                        references.append(register[1])

            position = first
            while position < length:
                position, info = _operand(instruction, position, length, visit)
                operands.append(info)
            if position != length:
                raise ValueError("Instruction operands do not fill instruction")
            for index in references:
                instruction[index] = remap_cb3(instruction[index])
            stats["cb3_refs"] += len(references)
            resource = [info for info in operands[1:] if info and info["kind"] == _TYPE_RESOURCE]
            if (replace_baked_shadow and is_pixel and opcode in _SAMPLES and resource and
                    instruction[resource[0]["indices"][0][1]] == BAKED_SHADOW_SLOT):
                if operands[0] is None:
                    raise ValueError("Unexpected baked shadow sample form")
                destination = instruction[operands[0]["start"]:operands[0]["end"]]
                visibility = struct.unpack("<I", struct.pack("<f", float(baked_shadow_visibility)))[0]
                source = [(2 | (_TYPE_IMMEDIATE32 << 12)), visibility, visibility, visibility, visibility]
                saturate = opcode_token & (1 << 13)
                length = 1 + len(destination) + len(source)
                instruction = [_OP_MOV | saturate | (length << 24)] + destination + source
                stats["baked_shadow_samples"] += 1
        output.extend(instruction)
        at += original_length
    output[1] = len(output)
    new_chunk = struct.pack("<4sI", data[chunk:chunk + 4], 4 * len(output)) + struct.pack("<%dI" % len(output), *output)
    delta = len(new_chunk) - (8 + chunk_size)
    result = bytearray(data[:chunk]) + new_chunk + data[chunk + 8 + chunk_size:]
    for index, offset in enumerate(offsets):
        if offset > chunk:
            struct.pack_into("<I", result, 32 + 4 * index, offset + delta)
    struct.pack_into("<I", result, 24, len(result))
    result[4:20] = dxbc_checksum(result)
    return bytes(result), stats
