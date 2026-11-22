"""Check the generated per-key VIA tabs in keyboards/hhkb/via.json."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/hhkb"))
import generate_per_key_menus as gen

VIA = ROOT / "keyboards/hhkb/via.json"


def read_raw():
    with open(VIA, encoding="utf-8", newline="") as f:
        return f.read()


class PerKeyMenusTest(unittest.TestCase):
    def setUp(self):
        self.raw = read_raw()
        self.definition = json.loads(self.raw)
        self.tabs = self.definition["menus"][0]["content"]

    def controls(self, label, layout):
        tab = next(t for t in self.tabs if t["label"] == label and f"{{id_hhkb_layout}} == {layout}" in t["showIf"])
        return [c for c in tab["content"] if c["type"] != "label"]

    def test_generated_tabs_are_up_to_date(self):
        self.assertEqual(gen.generate(self.raw), self.raw)

    def test_one_control_per_key_and_layout(self):
        cols = self.definition["matrix"]["cols"]
        for layout, option, keys in ((0, "0,0", 60), (1, "0,1", 69)):
            positions = {r * cols + c for r, c, _ in gen.layout_keys(self.definition, option)}
            for label, value_id in (("Rapid Trigger keys", 57), ("Key actuation", 58)):
                controls = self.controls(label, layout)
                self.assertEqual(len(controls), keys)
                self.assertEqual({c["content"][3] for c in controls}, positions)
                self.assertTrue(all(c["content"][1:3] == [0, value_id] for c in controls))

    def test_control_names_are_unique(self):
        names = []

        def walk(node):
            if isinstance(node, dict):
                content = node.get("content")
                if "type" in node and isinstance(content, list) and len(content) >= 3:
                    names.append(content[0])
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(self.definition["menus"])
        self.assertEqual(len(names), len(set(names)))

    def test_actuation_options_match_shared_point(self):
        shared = gen.find_control(self.definition["menus"], "id_hhkb_actuation")["options"]
        options = self.controls("Key actuation", 0)[0]["options"]
        self.assertEqual(options, [["Shared", 0]] + shared)
        self.assertEqual(options[-1], ["4.5 mm", 45])

    def test_rapid_trigger_profiles(self):
        for layout in (0, 1):
            for control in self.controls("Rapid Trigger keys", layout):
                self.assertEqual(control["type"], "dropdown")
                self.assertEqual(control["options"], gen.RT_PROFILE_OPTIONS)
        press = gen.find_control(self.definition["menus"], "id_hhkb_press")["options"]
        for value_id, name in ((61, "id_hhkb_rt_p2_press"), (62, "id_hhkb_rt_p2_release"),
                               (63, "id_hhkb_rt_p3_press"), (64, "id_hhkb_rt_p3_release")):
            control = gen.find_control(self.definition["menus"], name)
            self.assertEqual(control["content"], [name, 0, value_id])
            self.assertEqual(control["options"], press)

    def test_rapid_trigger_mode_is_a_three_way_dropdown(self):
        mode = gen.find_control(self.definition["menus"], "id_hhkb_rapid_trigger")
        self.assertEqual(mode["type"], "dropdown")
        self.assertEqual([v for _, v in mode["options"]], [0, 1, 2])


if __name__ == "__main__":
    unittest.main()
