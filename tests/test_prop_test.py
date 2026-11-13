import unittest
import xml.etree.ElementTree as ET

from build_prop_test import all_cone_draws, filter_cone_objects, filter_zone_draws, locate_cone_draws
from fh2_world import read_pvs_zone60, write_pvs_zone60


class PropTestProfileTests(unittest.TestCase):
    def test_removes_cones_and_preserves_other_collision_objects(self):
        source = (
            b'<CollObjs><Obj0 PhysicsType="Cone"><Pos x="1" y="2" z="3"/></Obj0>'
            b'<Obj1 PhysicsType="TrafficSign"><Pos x="4" y="5" z="6"/></Obj1></CollObjs>'
        )
        filtered, cones, types = filter_cone_objects(source)
        root = ET.fromstring(filtered)
        self.assertEqual(len(cones), 1)
        self.assertEqual(types, ["TrafficSign"])
        self.assertEqual([row.tag for row in root], ["Obj0"])
        self.assertEqual(root[0].get("PhysicsType"), "TrafficSign")

    def test_cone_rows_must_match_one_world_draw_each(self):
        source = b'<CollObjs><Obj0 PhysicsType="Cone"><Pos x="1" y="2" z="3"/></Obj0></CollObjs>'
        _, cones, _ = filter_cone_objects(source)
        draws = [{"instance_index": 8, "model_id": 4, "position": [1.0, 2.0, 3.0]}]
        self.assertEqual(locate_cone_draws(cones, draws), {8: (4, (1.0, 2.0, 3.0))})
        with self.assertRaisesRegex(ValueError, "exactly one"):
            locate_cone_draws(cones, draws + [dict(draws[0], instance_index=9)])

    def test_each_world_zone_hides_all_cone_draws_only(self):
        zone = {"draws": [0, 1, 2, 3], "models": [4], "textures": [8],
                "qualities": [257], "dependencies": [], "auxiliary": [], "final": []}
        updated = filter_zone_draws(zone, {1, 3})
        self.assertEqual(updated["draws"], [0, 2])
        self.assertEqual(updated["models"], zone["models"])
        self.assertEqual(zone["draws"], [0, 1, 2, 3])
        with self.assertRaisesRegex(ValueError, "every cone"):
            filter_zone_draws(zone, {4})

    def test_cone_filter_preserves_secondary_view_flags_on_retained_draws(self):
        zone = {"draws": [0x80000000, 0xC0000001, 0x40000002], "models": [4],
                "textures": [], "qualities": [], "dependencies": [], "auxiliary": [], "final": []}
        self.assertEqual(filter_zone_draws(zone, {1})["draws"], [0x80000000, 0x40000002])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            filter_zone_draws(dict(zone, draws=[1, 0xC0000001]), {1})

    def test_removes_visual_only_instances_of_each_registered_cone_model(self):
        source = (
            b'<CollObjs><Obj0 PhysicsType="Cone"><Pos x="1" y="2" z="3"/></Obj0>'
            b'<Obj1 PhysicsType="Sign"><Pos x="4" y="5" z="6"/></Obj1></CollObjs>'
        )
        retained, cones, _ = filter_cone_objects(source)
        draws = [
            {"instance_index": 0, "model_id": 14, "position": [1, 2, 3]},
            {"instance_index": 1, "model_id": 14, "position": [7, 8, 9]},
            {"instance_index": 2, "model_id": 20, "position": [4, 5, 6]},
        ]
        removed = all_cone_draws(cones, draws, retained)
        self.assertEqual(set(removed), {0, 1})
        zone = {"draws": [0, 1, 2], "models": [14, 20], "textures": [],
                "qualities": [], "dependencies": [], "auxiliary": [], "final": []}
        persisted = read_pvs_zone60(write_pvs_zone60(filter_zone_draws(zone, removed)))
        self.assertEqual(persisted["draws"], [2])
        self.assertEqual(draws[2]["model_id"], 20)

    def test_rejects_removing_a_model_shared_with_a_retained_prop(self):
        source = (
            b'<CollObjs><Obj0 PhysicsType="Cone"><Pos x="1" y="2" z="3"/></Obj0>'
            b'<Obj1 PhysicsType="Sign"><Pos x="4" y="5" z="6"/></Obj1></CollObjs>'
        )
        retained, cones, _ = filter_cone_objects(source)
        draws = [
            {"instance_index": 0, "model_id": 14, "position": [1, 2, 3]},
            {"instance_index": 1, "model_id": 14, "position": [4, 5, 6]},
        ]
        with self.assertRaisesRegex(ValueError, "shared"):
            all_cone_draws(cones, draws, retained)


if __name__ == "__main__":
    unittest.main()
