import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_object_attributes import clear_object_attribute


class ObjectAttributeTests(unittest.TestCase):
    def root(self):
        return {"groups": [
            {"references": [0] * 4, "attributes": [False, True, True, False, False]},
            {"references": [1] * 4, "attributes": [True, True, False, True, False]},
        ]}

    def test_clears_only_attribute_two(self):
        root = self.root()
        result, changed = clear_object_attribute(root)
        self.assertEqual(changed, [0])
        self.assertEqual(result["groups"][0]["attributes"], [False, True, False, False, False])
        self.assertEqual(result["groups"][1], root["groups"][1])
        self.assertEqual(root, self.root())

    def test_rejects_root_without_attribute(self):
        root = self.root()
        root["groups"] = root["groups"][1:]
        with self.assertRaises(ValueError):
            clear_object_attribute(root)


if __name__ == "__main__":
    unittest.main()
