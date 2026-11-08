import struct
import unittest

from shader_scenes import program_members, scene_table, shader_scenes


def string(value):
    raw = value.encode()
    if len(raw) >= 128:
        raise ValueError("Fixture string is too long")
    return bytes([len(raw)]) + raw


def scene_payload(version=(1, 3), instanced=False):
    data = b"\0\1" + string("TrackLightScenario") + struct.pack("<I", 5)
    if version == (1, 4):
        data += bytes([instanced])
    data += struct.pack("<I", 1) + b"\0" + string("game:\\library\\RoadTrackLightScenario")
    if instanced:
        data += string("game:\\library\\RoadTrackLightScenarioInstanced")
    return data + struct.pack("<i", 17) + string("game:\\library\\RoadTrackLightScenario")


def shader_bundle(version=(1, 3)):
    tags = [("LSCE", *version), ("DBLS", *version), ("CBMP", 2, 0), ("TXMP", 2, 0),
            ("SPMP", 2, 0), ("DFPR", 2, 0), ("TRGT", 1, 1)]
    start = 16 + 24 * len(tags)
    payloads = [scene_payload(version) if tag in ("LSCE", "DBLS") else b"\0" for tag, _, _ in tags]
    data = bytearray(b"burG" + struct.pack("<HHII", 1, len(tags), start, start + sum(map(len, payloads))))
    offset = start
    for (tag, major, minor), payload in zip(tags, payloads):
        data += tag[::-1].encode() + struct.pack("<BBHIIII", major, minor, 0, start,
                                                 offset, len(payload), len(payload))
        offset += len(payload)
    return bytes(data) + b"".join(payloads)


class ShaderSceneTests(unittest.TestCase):
    def test_source_and_native_instanced_scene_field_order(self):
        source = scene_table(scene_payload(), (1, 3))
        native = scene_table(scene_payload((1, 4), True), (1, 4))
        self.assertFalse(source["scenarios"][0]["instanced"])
        self.assertTrue(native["scenarios"][0]["instanced"])
        self.assertEqual(native["scenarios"][0]["vertices"][0]["instanced_paths"],
                         ["game:\\library\\RoadTrackLightScenarioInstanced"])
        self.assertEqual(shader_scenes(shader_bundle())["LSCE"]["version"], [1, 3])
        self.assertEqual(shader_scenes(shader_bundle((1, 4)))["DBLS"]["version"], [1, 4])

    def test_requires_real_compiled_members_for_both_platforms(self):
        scenes = shader_scenes(shader_bundle())
        members = ["Shaders/_0x00000000/RoadTrackLightScenario.%s.%s" % (platform, stage)
                   for platform in ("durango", "pc") for stage in ("vso", "pso")]
        self.assertEqual(set(program_members("shaders\\_0x00000000\\road.shaderbin", scenes, members)),
                         set(members))
        with self.assertRaisesRegex(ValueError, "Missing compiled"):
            program_members("Shaders/_0x00000000/road.shaderbin", scenes, members[:-1])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            program_members("Shaders/_0x00000000/road.shaderbin", scenes, members + [members[0].upper()])

    def test_rejects_corrupt_versions_counts_strings_and_trailers(self):
        for data in (scene_payload()[:-1], scene_payload() + b"\0", b"\0\1\xff\xff\xff\xff\xff"):
            with self.assertRaises(ValueError):
                scene_table(data, (1, 3))
        with self.assertRaises(ValueError):
            scene_table(scene_payload(), (1, 5))
        invalid = bytearray(shader_bundle())
        invalid[21] = 5
        with self.assertRaises(ValueError):
            shader_scenes(bytes(invalid))
        with self.assertRaises(ValueError):
            shader_scenes(shader_bundle() + b"\0")


if __name__ == "__main__":
    unittest.main()
