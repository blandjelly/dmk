#!/usr/bin/env python3
"""Generate the per-key Rapid Trigger and actuation tabs of keyboards/hhkb/via.json.

Key names and matrix positions come from the layout in via.json itself
(layout option 0,0 = ANSI, 0,1 = JIS). The firmware addresses a key as
row * matrix.cols + col after the value ID (VIA IDs 57 and 58), and reports
the layout with ID 59, so VIA shows only the matching list.

The generated tabs sit between the Rapid Trigger and LED tabs and are
replaced on every run; the rest of the file is kept byte for byte.

    python tools/hhkb/generate_per_key_menus.py           # update via.json
    python tools/hhkb/generate_per_key_menus.py --check   # fail if out of date
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

KEY_RT_ID = 57
KEY_ACT_ID = 58
# Per-key Rapid Trigger profile; firmware accepts 0..3 (HHKB_RT_PROFILES).
RT_PROFILE_OPTIONS = [["Default", 0], ["Profile 1", 1], ["Profile 2", 2], ["Profile 3", 3]]
LAYOUTS = (("0,0", 0, "ansi"), ("0,1", 1, "jis"))
# The Rapid Trigger tab ends with this line; the LED tab follows the generated tabs.
START_ANCHOR = '"showIf": "{id_firmware_version} == 2 || {id_firmware_version} == 4"'
END_ANCHOR = '"label": "LED",'
TAB_INDENT = 8

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_VIA = ROOT / "keyboards" / "hhkb" / "via.json"


def layout_keys(definition, option):
    """Return (row, col, legend) for every key of one layout option, in KLE order."""
    keys = []
    for row in definition["layouts"]["keymap"]:
        for item in row:
            if not isinstance(item, str):
                continue
            parts = item.split("\n")
            if len(parts) < 4 or parts[3] != option:
                continue
            r, c = (int(v) for v in parts[0].split(","))
            keys.append((r, c, parts[2] or parts[1]))
    return keys


def key_labels(keys):
    """Readable names; keys that appear twice (Shift, Fn, ...) get Left/Right."""
    totals = Counter(legend for _, _, legend in keys)
    seen = Counter()
    labels = []
    for r, c, legend in keys:
        name = legend[:1].upper() + legend[1:]
        if totals[legend] == 2:
            name = ("Left " if seen[legend] == 0 else "Right ") + name
        elif totals[legend] > 2:
            name = f"{name} {seen[legend] + 1}"
        seen[legend] += 1
        labels.append((r, c, name))
    return labels


def find_control(node, control_id):
    if isinstance(node, dict):
        content = node.get("content")
        if isinstance(content, list) and content and content[0] == control_id:
            return node
        node = list(node.values())
    if isinstance(node, list):
        for child in node:
            found = find_control(child, control_id)
            if found:
                return found
    return None


def build_tabs(definition):
    cols = definition["matrix"]["cols"]
    shared = find_control(definition["menus"], "id_hhkb_actuation")
    if not shared:
        sys.exit("via.json has no id_hhkb_actuation control")
    act_options = [["Shared", 0]] + shared["options"]

    tabs = []
    for option, layout, name in LAYOUTS:
        keys = key_labels(layout_keys(definition, option))
        if not keys:
            sys.exit(f"via.json has no keys for layout option {option}")
        tabs.append({
            "label": "Rapid Trigger keys",
            "showIf": f"{{id_hhkb_layout}} == {layout} && {{id_hhkb_rapid_trigger}} != 0",
            "content": [{
                "label": "Profiles",
                "type": "label",
                "content": ["Set profile sensitivity in the Rapid Trigger tab. Applies on every layer."],
            }, {
                "label": "Default",
                "type": "label",
                "content": ["No Rapid Trigger in Selected keys mode; Profile 1 in All keys mode."],
            }] + [{
                "label": label,
                "type": "dropdown",
                "options": RT_PROFILE_OPTIONS,
                "content": [f"id_hhkb_key_rt_{name}_{r}_{c}", 0, KEY_RT_ID, r * cols + c],
            } for r, c, label in keys],
        })
    for option, layout, name in LAYOUTS:
        keys = key_labels(layout_keys(definition, option))
        tabs.append({
            "label": "Key actuation",
            "showIf": f"{{id_hhkb_layout}} == {layout}",
            "content": [{
                "label": "Per-key actuation",
                "type": "label",
                "content": ["Shared uses the Actuation point from the Calibration tab, on every layer."],
            }] + [{
                "label": label,
                "type": "dropdown",
                "options": act_options,
                "content": [f"id_hhkb_key_act_{name}_{r}_{c}", 0, KEY_ACT_ID, r * cols + c],
            } for r, c, label in keys],
        })
    return tabs


def serialize(value, indent):
    """json.dumps(indent=2) with scalar-only arrays kept on one line."""
    inner = " " * (indent + 2)
    if isinstance(value, dict):
        items = [f"{inner}{json.dumps(k, ensure_ascii=False)}: {serialize(v, indent + 2)}" for k, v in value.items()]
        return "{\n" + ",\n".join(items) + "\n" + " " * indent + "}"
    if isinstance(value, list) and any(isinstance(v, (dict, list)) for v in value):
        items = [inner + serialize(v, indent + 2) for v in value]
        return "[\n" + ",\n".join(items) + "\n" + " " * indent + "]"
    return json.dumps(value, ensure_ascii=False)


def generate(raw):
    definition = json.loads(raw)
    eol = "\r\n" if "\r\n" in raw else "\n"
    if raw.count(START_ANCHOR) != 1 or raw.count(END_ANCHOR) != 1:
        sys.exit("Cannot find the Rapid Trigger and LED tabs in via.json")
    start = raw.index("},", raw.index(START_ANCHOR)) + 2
    end = raw.rindex("{", 0, raw.index(END_ANCHOR))
    pad = " " * TAB_INDENT
    block = eol + "".join(pad + serialize(tab, TAB_INDENT).replace("\n", eol) + "," + eol
                          for tab in build_tabs(definition)) + pad
    result = raw[:start] + block + raw[end:]
    json.loads(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("via_json", nargs="?", type=Path, default=DEFAULT_VIA)
    parser.add_argument("--check", action="store_true", help="exit with 1 if via.json is out of date")
    args = parser.parse_args()

    with open(args.via_json, encoding="utf-8", newline="") as f:
        raw = f.read()
    result = generate(raw)
    if args.check:
        if result != raw:
            sys.exit(f"{args.via_json} is out of date; run {Path(__file__).name}")
        return
    if result != raw:
        with open(args.via_json, "w", encoding="utf-8", newline="") as f:
            f.write(result)
    print(f"{args.via_json}: per-key tabs up to date")


if __name__ == "__main__":
    main()
