"""Offline tests for ArucoMarkers/aruco_settings (the dialogs' memory).

Stdlib only; no Fusion. Command inputs are stand-ins that answer the same
`objectType` / `value` / `listItems` the real ones do.

Run:  python tests/test_aruco_settings.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ArucoMarkers"))

import aruco_settings as S  # noqa: E402

PASS = 0
FAIL = []


def check(label, condition, detail=""):
    global PASS
    if condition:
        PASS += 1
        print(f"  ok  {label}")
    else:
        FAIL.append(label)
        print(f"FAIL  {label}  {detail}")


# ------------------------------------------------------------ stand-ins

class _Item:
    def __init__(self, owner, name):
        self._owner, self.name = owner, name

    @property
    def isSelected(self):
        return self._owner.selected == self.name

    @isSelected.setter
    def isSelected(self, on):                      # single-select, like Fusion
        if on:
            self._owner.selected = self.name


class _Items:
    def __init__(self, owner, names):
        self._items = [_Item(owner, n) for n in names]

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]


class DropDown:
    objectType = "adsk::core::DropDownCommandInput"

    def __init__(self, names, selected):
        self.selected = selected
        self.listItems = _Items(self, names)

    @property
    def selectedItem(self):
        return next((it for it in self.listItems._items if it.name == self.selected), None)


class Simple:
    def __init__(self, kind, value, lo=None, hi=None):
        self.objectType = "adsk::core::" + kind
        self.value = value
        if lo is not None:
            self.minimumValue, self.maximumValue = lo, hi


class Group:
    objectType = "adsk::core::GroupCommandInput"


class Inputs:
    def __init__(self, **inputs):
        self._d = inputs

    def itemById(self, cid):
        return self._d.get(cid)


PAGES = [("US Letter", "letter"), ("A4", "a4")]
ORIENT = [("Flat: pattern down (auto)", "flat"), ("As modelled", "cad")]
ROTATE = [("0°", 0), ("90°", 90), ("180°", 180), ("270°", 270)]
TABLES = {"page": PAGES, "orient": ORIENT, "rotate": ROTATE}


def export_dialog():
    return Inputs(
        page=DropDown([n for n, _ in PAGES], "US Letter"),
        orient=DropDown([n for n, _ in ORIENT], "Flat: pattern down (auto)"),
        rotate=DropDown([n for n, _ in ROTATE], "0°"),
        dictionary=DropDown(["DICT_4X4_50", "DICT_6X6_250"], "DICT_4X4_50"),
        landscape=Simple("BoolValueCommandInput", False),
        bed_w=Simple("ValueCommandInput", 25.0),
        cols=Simple("IntegerSpinnerCommandInput", 3, 1, 40),
        quiet=Simple("FloatSpinnerCommandInput", 1.0, 0.0, 4.0),
        key=Simple("StringValueCommandInput", ""),
        parts=Group())


def main():
    tmp = tempfile.mkdtemp(prefix="aruco_settings_test_")
    path = os.path.join(tmp, "sub", "_aruco_settings.json")

    print("file location")
    home = os.path.expanduser("~")
    check("lives in ~/CADExporter/_aruco_settings.json",
          S.settings_path() == os.path.join(home, "CADExporter", "_aruco_settings.json"),
          S.settings_path())

    print("missing file")
    data = S.load(path)
    check("missing -> empty, right shape",
          data == {"schema": S.SCHEMA, "commands": {}, "designs": {}}, data)
    check("recall of a missing file is {}", S.recall("bamArucoExport", "doc:x", path) == {})

    print("remember / recall (global)")
    S.remember("bamArucoExport", {"page": "a4", "bed_w": 30.0, "open_slicer": True}, path=path)
    check("file written (parent folder created)", os.path.isfile(path))
    check("valid JSON with schema",
          json.loads(Path(path).read_text(encoding="utf-8"))["schema"] == S.SCHEMA)
    check("no temp file left beside it",
          [n for n in os.listdir(os.path.dirname(path)) if n.endswith(".tmp")] == [])
    check("recall returns the values",
          S.recall("bamArucoExport", path=path) == {"page": "a4", "bed_w": 30.0,
                                                    "open_slicer": True})
    check("other commands untouched", S.recall("bamArucoTag", path=path) == {})

    print("merge")
    S.remember("bamArucoExport", {"bed_w": 35.0, "landscape": True}, path=path)
    r = S.recall("bamArucoExport", path=path)
    check("a later OK overrides a value", r["bed_w"] == 35.0, r)
    check("a later OK keeps keys it did not send", r.get("page") == "a4", r)
    check("and adds new keys", r.get("landscape") is True, r)
    S.remember("bamArucoTag", {"layout": "grid"}, path=path)
    check("a second command keeps the first",
          S.recall("bamArucoExport", path=path)["bed_w"] == 35.0
          and S.recall("bamArucoTag", path=path) == {"layout": "grid"})

    print("per design")
    A, B = S.design_key("Robot v3", "urn:lineage:A"), S.design_key("Other v1", None)
    S.remember("bamArucoExport", {"page": "letter"}, design=A,
               design_values={"page": "letter", "parts": {"Arm": True, "Base": False}},
               design_name="Robot v3", path=path)
    rA = S.recall("bamArucoExport", A, path=path)
    check("design A: its own page wins", rA["page"] == "letter", rA)
    check("design A: its parts", rA["parts"] == {"Arm": True, "Base": False}, rA)
    check("design A: falls back to the global bed", rA["bed_w"] == 35.0, rA)
    rB = S.recall("bamArucoExport", B, path=path)
    check("design B (never exported): no parts leak from A", "parts" not in rB, rB)
    check("design B: gets the last-used global page", rB["page"] == "letter", rB)
    S.remember("bamArucoExport", {"page": "a4"}, design=B,
               design_values={"page": "a4", "parts": {"Gripper": True}}, path=path)
    check("A keeps its page after B's export",
          S.recall("bamArucoExport", A, path=path)["page"] == "letter")
    check("B its own", S.recall("bamArucoExport", B, path=path)["page"] == "a4")
    S.remember("bamArucoExport", {}, design=A, design_values={"parts": {"Base": True,
                                                                       "Wrist": False}},
               path=path)
    check("parts merge by component name",
          S.recall("bamArucoExport", A, path=path)["parts"]
          == {"Arm": True, "Base": True, "Wrist": False})
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    check("design section records its name and time",
          raw["designs"][A]["name"] == "Robot v3" and raw["designs"][A]["updated"] > 0)
    check("parts are not written to the global section",
          "parts" not in raw["commands"]["bamArucoExport"])
    check("recall hands out a copy",
          (S.recall("bamArucoExport", A, path=path)["parts"].clear() or True)
          and S.recall("bamArucoExport", A, path=path)["parts"] != {})

    print("design keys")
    check("saved: file id", S.design_key("Robot v3", "urn:x") == "file:urn:x")
    check("saved: id is stable across versions",
          S.design_key("Robot v3", "urn:x") == S.design_key("Robot v4", "urn:x"))
    check("unsaved: name without version",
          S.design_key("Robot v12", None) == S.design_key("Robot", None) == "doc:Robot")
    check("unsaved: empty name", S.design_key("", None) == "doc:Design")
    check("a name that merely ends in v is kept", S.design_key("Servo", None) == "doc:Servo")

    print("pruning")
    p2 = os.path.join(tmp, "prune.json")
    old = S.MAX_DESIGNS
    S.MAX_DESIGNS = 3
    try:
        for i in range(5):
            S.remember("bamArucoIds", {}, design=f"doc:D{i}", design_values={"level": "x"},
                       path=p2)
        kept = set(S.load(p2)["designs"])
        check("oldest design sections dropped past MAX_DESIGNS",
              kept == {"doc:D2", "doc:D3", "doc:D4"}, kept)
    finally:
        S.MAX_DESIGNS = old

    print("corrupt / odd files")
    for label, text in (("garbage", "{not json"), ("empty", ""), ("a list", "[1, 2]"),
                        ("wrong shapes", json.dumps({"commands": [1], "designs": {"k": 5}})),
                        ("bad entries", json.dumps({"commands": {"bamArucoTag": 7,
                                                                 "bamArucoIds": {"rebuild": False}},
                                                    "designs": {"doc:a": {"commands": 3}}})),
                        ("binary", None)):
        p = os.path.join(tmp, f"bad_{label.replace(' ', '_')}.json")
        if text is None:
            Path(p).write_bytes(b"\xff\xfe\x00garbage\x80")
        else:
            Path(p).write_text(text, encoding="utf-8")
        d = S.load(p)
        check(f"{label}: load does not raise and has the right shape",
              set(d) >= {"schema", "commands", "designs"}
              and isinstance(d["commands"], dict) and isinstance(d["designs"], dict), d)
        S.remember("bamArucoTag", {"layout": "charuco"}, design="doc:z",
                   design_values={"x": 1}, path=p)
        check(f"{label}: the next OK replaces it with a good file",
              S.recall("bamArucoTag", "doc:z", p) == {"layout": "charuco", "x": 1})
    d = S.load(os.path.join(tmp, "bad_bad_entries.json"))
    check("good entries next to bad ones survive",
          d["commands"].get("bamArucoIds") == {"rebuild": False}, d)

    print("snapshot")
    ins = export_dialog()
    ins.itemById("page").selected = "A4"
    ins.itemById("rotate").selected = "90°"
    ins.itemById("bed_w").value = 30.0
    snap = S.snapshot(ins, ("page", "orient", "rotate", "dictionary", "landscape", "bed_w",
                            "cols", "quiet", "key", "parts", "missing"), TABLES)
    check("dropdown with a table -> its code", snap["page"] == "a4" and snap["rotate"] == 90,
          snap)
    check("dropdown without a table -> its name", snap["dictionary"] == "DICT_4X4_50", snap)
    check("bool / value (cm) / int / float / string",
          snap["landscape"] is False and snap["bed_w"] == 30.0 and snap["cols"] == 3
          and snap["quiet"] == 1.0 and snap["key"] == "", snap)
    check("groups and missing ids skipped", "parts" not in snap and "missing" not in snap,
          snap)
    check("snapshot is JSON-able", json.loads(json.dumps(snap)) == snap)

    print("apply")
    ins = export_dialog()
    done = S.apply(ins, {"page": "a4", "orient": "cad", "rotate": 270,
                         "dictionary": "DICT_6X6_250", "landscape": True, "bed_w": 30.5,
                         "cols": 7, "quiet": 2.5, "key": "x"}, TABLES)
    check("every input set", sorted(done) == sorted(
        ["page", "orient", "rotate", "dictionary", "landscape", "bed_w", "cols", "quiet",
         "key"]), done)
    check("dropdowns by code", ins.itemById("page").selected == "A4"
          and ins.itemById("orient").selected == "As modelled"
          and ins.itemById("rotate").selected == "270°")
    check("dropdown by name", ins.itemById("dictionary").selected == "DICT_6X6_250")
    check("values", ins.itemById("landscape").value is True
          and ins.itemById("bed_w").value == 30.5 and ins.itemById("cols").value == 7
          and ins.itemById("quiet").value == 2.5)
    check("dropdown by its label also works (older files)",
          S.apply(ins, {"page": "US Letter"}, TABLES) == ["page"]
          and ins.itemById("page").selected == "US Letter")
    ins = export_dialog()
    done = S.apply(ins, {"page": "tabloid", "dictionary": "DICT_ARUCO_GONE",
                         "landscape": "yes", "bed_w": "250 mm", "cols": 99, "quiet": 9.0,
                         "key": 5, "parts": {"A": True}, "nope": 1, "rotate": "90"}, TABLES)
    check("stale / wrong-typed / out-of-range values are skipped", done == [], done)
    check("...and the defaults stay",
          ins.itemById("page").selected == "US Letter"
          and ins.itemById("dictionary").selected == "DICT_4X4_50"
          and ins.itemById("landscape").value is False and ins.itemById("bed_w").value == 25.0
          and ins.itemById("cols").value == 3 and ins.itemById("quiet").value == 1.0
          and ins.itemById("rotate").selected == "0°")
    check("a float for an integer spinner is refused unless whole",
          S.apply(ins, {"cols": 4.5}) == [] and S.apply(ins, {"cols": 4.0}) == ["cols"])
    check("bool is not a number", S.apply(ins, {"bed_w": True}) == [])

    print("round trip through the file")
    ins = export_dialog()
    ins.itemById("page").selected = "A4"
    ins.itemById("orient").selected = "As modelled"
    ins.itemById("bed_w").value = 18.0
    p3 = os.path.join(tmp, "rt.json")
    S.remember("bamArucoExport", S.snapshot(ins, ("page", "orient", "bed_w"), TABLES), path=p3)
    fresh = export_dialog()
    S.apply(fresh, S.recall("bamArucoExport", path=p3), TABLES)
    check("a new dialog opens as the last OK left it",
          fresh.itemById("page").selected == "A4"
          and fresh.itemById("orient").selected == "As modelled"
          and fresh.itemById("bed_w").value == 18.0)

    print("PrusaSlicer")
    p4 = os.path.join(tmp, "exe.json")
    fake = os.path.join(tmp, "prusa-slicer.exe")
    Path(fake).write_bytes(b"")
    check("a hint that exists is used", S.find_prusaslicer(fake, path=p4) == fake)
    Path(p4).write_text(json.dumps({"prusaslicer_exe": fake}), encoding="utf-8")
    check("the settings file's override wins",
          S.find_prusaslicer(os.path.join(tmp, "nope.exe"), path=p4) == fake)
    check("an override survives load/save", S.load(p4).get("prusaslicer_exe") == fake)
    found = S.find_prusaslicer(os.path.join(tmp, "nope.exe"), path=os.path.join(tmp, "x.json"))
    check("a missing hint falls through to a real install or None",
          found is None or (os.path.isfile(found)
                            and os.path.basename(found).lower().startswith(("prusa-slicer",
                                                                            "prusaslicer"))),
          found)
    print(f"      (this machine: {found})")

    print(f"\n{PASS} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
