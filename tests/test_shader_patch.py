import struct
import unittest

from shader_patch import (_chunks, _rewrite_instructions, bake_constants, dxbc_checksum,
                          neutralize_legacy_ao, neutralize_pavement_ao_mask, neutralize_skid_mask,
                          neutral_sun_colour, neutralize_texture_rgb, offset_world_height,
                          patch_pose_stride, guard_marking_normal, guard_normal_reconstructions,
                          patch_program,
                          scale_baked_shadow_fallback)


def container(instructions, tag=b"SHEX", reflection=b"fixture"):
    tokens = [0x50, len(instructions) + 2] + instructions
    payload = struct.pack("<%dI" % len(tokens), *tokens)
    chunks = [(b"RDEF", reflection), (tag, payload), (b"STAT", b"trailer")]
    offset = 32 + 4 * len(chunks)
    offsets, bodies = [], []
    for name, data in chunks:
        offsets.append(offset)
        body = struct.pack("<4sI", name, len(data)) + data
        bodies.append(body)
        offset += len(body)
    result = bytearray(b"DXBC" + bytes(16) + struct.pack("<3I", 1, offset, len(chunks)) +
                       struct.pack("<%dI" % len(offsets), *offsets) + b"".join(bodies))
    result[4:20] = dxbc_checksum(result)
    return bytes(result)


def instructions(data):
    offset = next(o for o in _chunks(data) if data[o:o + 4] in (b"SHEX", b"SHDR"))
    size = struct.unpack_from("<I", data, offset + 4)[0]
    return list(struct.unpack_from("<%dI" % (size // 4 - 2), data, offset + 16))


RET = 62 | (1 << 24)
SAMPLE = [69 | (9 << 24), 0x001000F2, 1, 0x00100E46, 2,
          0x00107E46, 16, 0x00106000, 1]


def skid_reflection(slot=16, name=b"SkidmaskTextureTexture", length=1):
    return (struct.pack("<4I", 0, 0, 1, 16) +
            struct.pack("<8I", 48, 2, 5, 4, 0, slot, length, 0) + name + b"\0")


class ShaderPatchTests(unittest.TestCase):
    def test_baked_shadow_fallback_can_be_scaled_without_touching_other_samples(self):
        shadow = SAMPLE.copy()
        shadow[6] = 3
        unrelated = SAMPLE.copy()
        original = container(shadow + unrelated + [RET])
        full_visibility, stats = patch_program(original, True)
        self.assertEqual(stats["baked_shadow_samples"], 1)
        default_profile, default_stats = patch_program(original, True)
        self.assertEqual(default_profile, patch_program(original, True, 0.15)[0])
        self.assertEqual(default_stats["baked_shadow_samples"], 1)

        scaled, count = scale_baked_shadow_fallback(original, full_visibility, 0.45)
        expected, _ = patch_program(original, True, 0.45)
        self.assertEqual(count, 1)
        self.assertEqual(scaled, expected)
        self.assertEqual(scaled[4:20], dxbc_checksum(scaled))
        self.assertEqual(scale_baked_shadow_fallback(original, scaled, 0.45),
                         (scaled, 0))
        darker, count = scale_baked_shadow_fallback(original, scaled, 0.15)
        self.assertEqual(count, 1)
        self.assertEqual(darker, patch_program(original, True, 0.15)[0])
        for invalid in (-0.1, 1.1, float("nan"), float("inf"), True):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "visibility"):
                scale_baked_shadow_fallback(original, full_visibility, invalid)

    def test_marking_normal_guard_preserves_samples_alpha_and_later_square_roots(self):
        square_root = [75 | (5 << 24), 0x00100082, 2, 0x0010003A, 2]
        later_root = [75 | (5 << 24), 0x00100082, 1, 0x0010003A, 0]
        discard = [13 | (3 << 24), 0x0010003A, 1]
        normal, diffuse = SAMPLE.copy(), SAMPLE.copy()
        normal[6], diffuse[6] = 18, 17
        reflection = skid_reflection(18, b"Ch5RoadLinesNormalTextureTexture")
        code = normal + diffuse + square_root + discard + later_root + [RET]
        guard = [52 | (7 << 24), 0x00100082, 2, 0x0010003A, 2, 0x4001,
                 struct.unpack("<I", struct.pack("<f", 0.000001))[0]]
        for tag in (b"SHEX", b"SHDR"):
            source = container(code, tag=tag, reflection=reflection)
            patched, count = guard_marking_normal(source)
            self.assertEqual(count, 1)
            self.assertEqual(instructions(patched),
                             normal + diffuse + guard + square_root + discard + later_root + [RET])
            self.assertEqual(patched[4:20], dxbc_checksum(patched))
            self.assertEqual(guard_marking_normal(patched), (patched, 0))
            for old, new in zip(_chunks(source), _chunks(patched)):
                if source[old:old + 4] != tag:
                    size = struct.unpack_from("<I", source, old + 4)[0] + 8
                    self.assertEqual(source[old:old + size], patched[new:new + size])
        for invalid in (container(code), container([RET], reflection=reflection),
                        container(later_root + [RET], reflection=reflection)):
            with self.assertRaises(ValueError):
                guard_marking_normal(invalid)

    def test_track_normal_guard_only_clamps_reconstructed_normal_z(self):
        epsilon = struct.unpack("<I", struct.pack("<f", 0.000001))[0]
        # mad r3.w, -r4.x, r4.x, r3.w ; sqrt r9.z, r3.w  (terrain/2blend form)
        reconstruct = [50 | (10 << 24), 0x00100082, 3, 0x8010000A, 0x41, 4,
                       0x0010000A, 4, 0x0010003A, 3]
        root = [75 | (5 << 24), 0x00100042, 9, 0x0010003A, 3]
        # dp3 r0.w, r1.xyz, r1.xyz ; sqrt r1.w, r0.w  (view distance, must stay untouched)
        distance = [16 | (7 << 24), 0x00100082, 0, 0x00100246, 1, 0x00100246, 1]
        distance_root = [75 | (5 << 24), 0x00100082, 1, 0x0010003A, 0]
        # unnegated mad is not a normal reconstruction
        plain = reconstruct.copy()
        plain[3], plain[4:5] = 0x0010000A, []
        plain[0] = 50 | (9 << 24)
        guard = [52 | (7 << 24), 0x00100082, 3, 0x0010003A, 3, 0x4001, epsilon]
        source = container(reconstruct + root + distance + distance_root + plain + root + [RET])
        patched, count = guard_normal_reconstructions(source)
        self.assertEqual(count, 1)
        self.assertEqual(instructions(patched), reconstruct + guard + root + distance +
                         distance_root + plain + root + [RET])
        self.assertEqual(patched[4:20], dxbc_checksum(patched))
        self.assertEqual(guard_normal_reconstructions(patched), (patched, 0))

    def test_pavement_ao_preserves_diffuse_and_threshold_mask(self):
        ao, threshold, diffuse = SAMPLE.copy(), SAMPLE.copy(), SAMPLE.copy()
        ao[1], ao[6], threshold[6], diffuse[6] = 0x00100082, 22, 19, 17
        source = container(threshold + diffuse + ao + [RET],
                           reflection=skid_reflection(22, b"AOMaskTextureTexture"))
        patched, count = neutralize_pavement_ao_mask(source)
        self.assertEqual(count, 1)
        self.assertEqual(instructions(patched), threshold + diffuse + [
            54 | (8 << 24), ao[1], 1, 0x4002,
            0x3f800000, 0x3f800000, 0x3f800000, 0x3f800000, RET])
        self.assertEqual(patched[4:20], dxbc_checksum(patched))
        self.assertEqual(neutralize_pavement_ao_mask(patched), (patched, 0))

    def test_skid_mask_resolves_named_slot_preserving_other_inputs(self):
        for slot in (16, 22):
            for mask in (0x00100082, 0x001000F2):
                skid = SAMPLE.copy()
                skid[1], skid[6] = mask, slot
                diffuse = SAMPLE.copy()
                diffuse[6] = 17
                source = container(skid + diffuse + [RET], reflection=skid_reflection(slot))
                patched, count = neutralize_skid_mask(source)
                self.assertEqual(count, 1)
                self.assertEqual(instructions(patched), [
                    54 | (8 << 24), mask, 1, 0x4002,
                    0x3f800000, 0x3f800000, 0x3f800000, 0x3f800000] + diffuse + [RET])
                self.assertEqual(patched[4:20], dxbc_checksum(patched))
                self.assertEqual(neutralize_skid_mask(patched), (patched, 0))
                for old, new in zip(_chunks(source), _chunks(patched)):
                    if source[old:old + 4] != b"SHEX":
                        size = struct.unpack_from("<I", source, old + 4)[0] + 8
                        self.assertEqual(source[old:old + size], patched[new:new + size])
        source = container(SAMPLE + [RET], reflection=skid_reflection(name=b"DiffuseATexture"))
        self.assertEqual(neutralize_skid_mask(source), (source, 0))

    def test_skid_mask_rejects_malformed_or_array_reflection(self):
        for reflection in (b"short", skid_reflection()[:-1],
                           skid_reflection(length=2), struct.pack("<4I", 0, 0, 1, 999)):
            with self.assertRaises(ValueError):
                neutralize_skid_mask(container(SAMPLE + [RET], reflection=reflection))

    def test_legacy_ao_replaces_only_t6_samples_and_preserves_destination(self):
        for mask in (0x00100082, 0x001000F2):
            ao = SAMPLE.copy()
            ao[1], ao[6] = mask, 6
            source = container(SAMPLE + ao + [RET])
            patched, count = neutralize_legacy_ao(source)
            self.assertEqual(count, 1)
            self.assertEqual(instructions(patched), SAMPLE + [
                54 | (8 << 24), mask, 1, 0x4002,
                0x3f800000, 0x3f800000, 0x3f800000, 0x3f800000, RET])
            self.assertEqual(patched[4:20], dxbc_checksum(patched))
            self.assertEqual(neutralize_legacy_ao(patched), (patched, 0))
        source = container(SAMPLE + [RET])
        self.assertEqual(neutralize_legacy_ao(source), (source, 0))

    def test_pose_stride_changes_declaration_and_load_extension_only(self):
        declaration = [162 | (4 << 24), 0x00107000, 64, 272]
        load = [167 | (11 << 24) | 0x80000000, 0x80088302, 0x199983,
                0x001000F2, 1, 0x0010100A, 7, 0x4001, 192, 0x00107E46, 64]
        source = container(declaration + load + [RET])
        patched, stats = patch_pose_stride(source)
        self.assertEqual(stats, {"pose_declarations": 1, "pose_loads": 1})
        expected = declaration + load + [RET]
        expected[3] = 288
        expected[5] = (expected[5] & ~(0xFFF << 11)) | (288 << 11)
        self.assertEqual(instructions(patched), expected)
        self.assertEqual(patched[4:20], dxbc_checksum(patched))
        self.assertEqual(patch_pose_stride(patched),
                         (patched, {"pose_declarations": 0, "pose_loads": 0}))
        declaration[2] = 63
        load[-1] = 63
        source = container(declaration + load + [RET])
        self.assertEqual(patch_pose_stride(source),
                         (source, {"pose_declarations": 0, "pose_loads": 0}))

    def test_grass_depth_changes_world_y_only_before_projection(self):
        projection = [17 | (8 << 24), 0x00102012, 6, 0x00100E46, 1, 0x00208E46, 2, 4]
        source = container(projection + [RET])
        patched, count = offset_world_height(source, 0.05)
        self.assertEqual(count, 1)
        code = instructions(patched)
        self.assertEqual(code[:6], [7 << 24, 0x00100022, 1, 0x0010001A, 1, 0x4001])
        self.assertAlmostEqual(struct.unpack("<f", struct.pack("<I", code[6]))[0], 0.05)
        self.assertEqual(code[7:], projection + [RET])
        self.assertEqual(patched[4:20], dxbc_checksum(patched))
        projection[-1] = 5
        source = container(projection + [RET])
        self.assertEqual(offset_world_height(source, 0.05), (source, 0))
        for value in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                offset_world_height(source, value)

    def test_rgb_neutralization_keeps_original_sample_and_alpha(self):
        source = container(SAMPLE + [RET])
        patched, count = neutralize_texture_rgb(source, 16)
        code = instructions(patched)
        self.assertEqual(count, 1)
        self.assertEqual(code[:len(SAMPLE)], SAMPLE)
        self.assertEqual(code[len(SAMPLE):], [
            54 | (8 << 24), 0x00100072, 1, 0x4002,
            0x3f800000, 0x3f800000, 0x3f800000, 0x3f800000, RET])
        self.assertEqual(patched[4:20], dxbc_checksum(patched))
        offsets = _chunks(patched)
        self.assertEqual(patched[offsets[-1]:], source[_chunks(source)[-1]:])
        self.assertEqual(struct.unpack_from("<I", patched, 24)[0], len(patched))

    def test_unrelated_slots_and_alpha_only_samples_are_unchanged(self):
        source = container(SAMPLE + [RET])
        self.assertEqual(neutralize_texture_rgb(source, 17), (source, 0))
        alpha = SAMPLE.copy()
        alpha[1] = 0x00100082
        source = container(alpha + [RET])
        self.assertEqual(neutralize_texture_rgb(source, 16), (source, 0))

    def test_constant_bake_preserves_swizzle_select_and_modifiers(self):
        for operand, modifier, expected in (
                (0x00208E46, [], (75, 100, 0, 0)),
                (0x0020801A, [], (100, 100, 100, 100)),
                (0x8020800A, [0x41], (-75, -75, -75, -75)),
                (0x8020800A, [0x81], (75, 75, 75, 75))):
            code = [54 | ((6 + len(modifier)) << 24), 0x001000F2, 0,
                    operand] + modifier + [7, 1]
            source = container(code + [RET], b"SHDR")
            patched, count = bake_constants(source, 7, {1: (75, 100, 0, 0)})
            self.assertEqual(count, 1)
            self.assertEqual(struct.unpack("<4f", struct.pack("<4I", *instructions(patched)[4:8])),
                             expected)
            self.assertEqual(patched[4:20], dxbc_checksum(patched))
        with self.assertRaisesRegex(ValueError, "no baked value"):
            bake_constants(source, 7, {2: (0, 0, 0, 0)})

    def test_neutral_sun_swizzles_only_lit_sun_colour_reads(self):
        mul = lambda row: [56 | (8 << 24), 0x00100072, 1, 0x00100246, 1, 0x00208246, 3, row]
        flag = [31 | (4 << 24), 0x0020800A, 3, 1]
        source = container(mul(1) + mul(0) + flag + [RET])
        patched, count = neutral_sun_colour(source)
        self.assertEqual(count, 1)
        code = instructions(patched)
        self.assertEqual(code[5], (0x00208246 & ~(0xFF << 4)) | (0x55 << 4))
        self.assertEqual(code[8:], mul(0)[:] + flag + [RET])
        self.assertEqual(patched[4:20], dxbc_checksum(patched))
        scalar = container([56 | (8 << 24), 0x00100012, 1, 0x00100006, 1, 0x0020800A, 3, 1, RET])
        with self.assertRaisesRegex(ValueError, "4-component swizzle"):
            neutral_sun_colour(scalar)

    def test_identity_rewrite_and_invalid_length(self):
        source = container([RET])
        self.assertEqual(_rewrite_instructions(source, lambda instruction: instruction), source)
        with self.assertRaisesRegex(ValueError, "Invalid instruction length"):
            _rewrite_instructions(container([54]), lambda instruction: instruction)


if __name__ == "__main__":
    unittest.main()
