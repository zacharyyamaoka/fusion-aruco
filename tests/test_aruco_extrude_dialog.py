"""The ArUco Extrude dialog driven offline: build() on stand-in command
inputs, then refresh() the way Fusion's inputChanged does -- the Origin
dropdown and "Rotate", the Corner mark list, the edit dialog's fill, OK's
values and what is remembered. Fusion hot-loads this module; a typo here
would only show in Zach's dialog.

The target is a stand-in sketch area (aruco_feature.profile_rect patched,
as in tests/test_aruco_chamfer_bodies.py); evaluate() runs for real.

Run:  python tests/test_aruco_extrude_dialog.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ArucoMarkers"))
TMP = tempfile.mkdtemp(prefix="aruco_extrude_dialog_")
os.environ["USERPROFILE"] = TMP                 # aruco_settings' file: ours, not Zach's
os.environ["HOME"] = TMP

try:                                    # labels carry × ↻ °: never die on cp1252
    sys.stdout.reconfigure(errors="replace")
except AttributeError:
    pass
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


# ---------------------------------------------------------------- stand-in adsk
adsk = types.ModuleType("adsk")
core = types.ModuleType("adsk.core")
fusion = types.ModuleType("adsk.fusion")
adsk.core, adsk.fusion = core, fusion
sys.modules.update({"adsk": adsk, "adsk.core": core, "adsk.fusion": fusion})


def _attr(mod):
    def get(name):
        if name.endswith("Handler"):
            cls = type(name, (), {"__init__": lambda self, *a, **k: None})
            setattr(mod, name, cls)
            return cls
        raise AttributeError(name)
    return get


core.__getattr__ = _attr(core)
fusion.__getattr__ = _attr(fusion)
core.DropDownStyles = types.SimpleNamespace(TextListDropDownStyle=1)


class _Vec:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = float(x), float(y), float(z)

    @classmethod
    def create(cls, x=0.0, y=0.0, z=0.0):
        return cls(x, y, z)

    def t(self):
        return (self.x, self.y, self.z)


core.Point3D = _Vec
core.Vector3D = _Vec


class VI:
    @staticmethod
    def createByString(s):
        m = re.match(r"\s*(-?[\d.eE+-]+)\s*mm", s)
        return float(m.group(1)) / 10.0

    @staticmethod
    def createByReal(v):
        return float(v)


core.ValueInput = VI


class UI:
    activeCommand = "SelectCommand"
    messages = []

    def messageBox(self, m):
        UI.messages.append(m)


class App:
    userInterface = UI()

    def log(self, m):
        pass


core.Application = types.SimpleNamespace(get=lambda: App())


class Profile:
    assemblyContext = None
    entityToken = "PROFILE-1"
    profileLoops = types.SimpleNamespace(count=1)


fusion.Profile = Profile
fusion.BRepFace = types.SimpleNamespace(cast=lambda x: None)


class Inp:
    def __init__(self, reg, kind, cid, name):
        self.objectType = "adsk::core::" + kind
        self.id, self.name = cid, name
        self.isVisible, self.isEnabled = True, True
        self.tooltip = ""
        reg[cid] = self


class Item:
    def __init__(self, dd, name):
        self.dd, self.name, self._sel = dd, name, False

    @property
    def isSelected(self):
        return self._sel

    @isSelected.setter
    def isSelected(self, v):
        if v:
            for it in self.dd._items:
                it._sel = False
        self._sel = bool(v)

    @property
    def index(self):
        return self.dd._items.index(self)


class Items:
    def __init__(self, dd):
        self.dd = dd

    def add(self, name, sel, icon="", before=-1):
        it = Item(self.dd, name)
        self.dd._items.append(it)
        if sel:
            it.isSelected = True
        return it

    @property
    def count(self):
        return len(self.dd._items)

    def item(self, i):
        return self.dd._items[i]

    def clear(self):
        self.dd._items = []


class DropDown(Inp):
    @property
    def selectedItem(self):
        return next((i for i in self._items if i._sel), None)


class Value(Inp):
    isValidExpression = True

    def setManipulator(self, o, d):
        self.manip = (o.t(), d.t())


class Selection(Inp):
    def __init__(self, *a):
        super().__init__(*a)
        self.items = []

    def addSelectionFilter(self, f):
        pass

    def setSelectionLimits(self, a, b=0):
        pass

    @property
    def selectionCount(self):
        return len(self.items)

    def selection(self, i):
        return types.SimpleNamespace(entity=self.items[i])

    def addSelection(self, e):
        self.items.append(e)

    def clearSelection(self):
        self.items = []


class Inputs:
    def __init__(self, reg=None):
        self.reg = {} if reg is None else reg

    def itemById(self, cid):
        return self.reg.get(cid)

    def addSelectionInput(self, cid, name, tip):
        return Selection(self.reg, "SelectionCommandInput", cid, name)

    def addDropDownCommandInput(self, cid, name, style):
        dd = DropDown(self.reg, "DropDownCommandInput", cid, name)
        dd._items = []
        dd.listItems = Items(dd)
        return dd

    def addBoolValueInput(self, cid, name, check, res, value):
        b = Inp(self.reg, "BoolValueCommandInput", cid, name)
        b.value, b.isCheckBox, b.text = value, check, ""
        return b

    def addValueInput(self, cid, name, unit, v):
        b = Value(self.reg, "ValueCommandInput", cid, name)
        b.value = v
        return b

    def addDistanceValueCommandInput(self, cid, name, v):
        b = Value(self.reg, "DistanceValueCommandInput", cid, name)
        b.value = v
        return b

    def addIntegerSpinnerCommandInput(self, cid, name, lo, hi, step, v):
        b = Inp(self.reg, "IntegerSpinnerCommandInput", cid, name)
        b.value, b.minimumValue, b.maximumValue = v, lo, hi
        return b

    def addFloatSpinnerCommandInput(self, cid, name, unit, lo, hi, step, v):
        b = Inp(self.reg, "FloatSpinnerCommandInput", cid, name)
        b.value, b.minimumValue, b.maximumValue = v, lo, hi
        return b

    def addStringValueInput(self, cid, name, v):
        b = Inp(self.reg, "StringValueCommandInput", cid, name)
        b.value = v
        return b

    def addGroupCommandInput(self, cid, name):
        g = Inp(self.reg, "GroupCommandInput", cid, name)
        g.children, g.isExpanded = Inputs(self.reg), True
        return g

    def addTextBoxCommandInput(self, cid, name, text, rows, ro):
        t = Inp(self.reg, "TextBoxCommandInput", cid, name)
        t.formattedText = t.text = text
        return t


class Event:
    def __init__(self):
        self.h = []

    def add(self, h):
        self.h.append(h)


class Cmd:
    def __init__(self):
        self.commandInputs = Inputs()
        for e in ("execute", "inputChanged", "executePreview", "destroy", "activate"):
            setattr(self, e, Event())
        self.okButtonText = ""

    def setDialogInitialSize(self, w, h):
        pass

    def setDialogMinimumSize(self, w, h):
        pass


# ---------------------------------------------------------------- the add-in
import aruco_core  # noqa: E402
import aruco_feature as F  # noqa: E402
import aruco_extrude_cmd as X  # noqa: E402
import aruco_tags as T  # noqa: E402

OR = aruco_core.module("aruco_origin")
W_MM, H_MM = 60.0, 40.0                      # the sketch area: a rectangle, a body under it
F.profile_rect = lambda t, s: ([0.0, 0.0, 0.5], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0],
                               [0.0, 0.0, 1.0], W_MM / 10.0, H_MM / 10.0)
F._profile_loops_m = lambda t, o, x, y: [[(-W_MM / 2000, -H_MM / 2000),
                                          (W_MM / 2000, -H_MM / 2000),
                                          (W_MM / 2000, H_MM / 2000),
                                          (-W_MM / 2000, H_MM / 2000)]]
T.next_free_id = lambda dictionary, count=1: 0
F.is_registered = lambda: True
TARGET_INFO = {"s": {"source": "profile", "z_sign": 1, "x_sketch": [1.0, 0.0],
                     "has_body": True, "host_xform": None, "x_base": None, "x_ref": None},
               "body": None, "note": None, "body_name": "Plate", "m": None}


def fake_target(inputs):
    sel = inputs.itemById("target")
    if X._X.edit_cf is None and not sel.selectionCount:
        X._X.tgt_key, X._X.tgt = None, None
        return None, None
    X._X.tgt_key, X._X.tgt = ("PROFILE-1", "", X._X.edit_cf is not None), TARGET_INFO
    return Profile(), TARGET_INFO


X._target = fake_target
X._candidate_source = lambda target, s: (("rect", W_MM / 1000.0, H_MM / 1000.0), ("r", W_MM, H_MM))


def dd_names(inputs, cid):
    d = inputs.itemById(cid)
    return [d.listItems.item(i).name for i in range(d.listItems.count)]


def shown(inputs, cid):
    it = inputs.itemById(cid).selectedItem
    return it.name if it else None


def pick(inputs, cid, name):
    d = inputs.itemById(cid)
    for i in range(d.listItems.count):
        if d.listItems.item(i).name == name:
            d.listItems.item(i).isSelected = True
            X.refresh(inputs, cid)
            return True
    return False


print("== build() and a first refresh")
cmd = Cmd()
X.build(cmd)
inputs = cmd.commandInputs
check("no Frame, no checkbox: Origin, Rotate and Corner mark instead",
      inputs.itemById("frame") is None and inputs.itemById("frame_z") is None
      and inputs.itemById("chamfer") is None and inputs.itemById("origin") is not None
      and inputs.itemById("rotate") is not None and inputs.itemById("corner_mark") is not None)
check("Rotate is a button with the ↻", inputs.itemById("rotate").isCheckBox is False
      and inputs.itemById("rotate").text == "Rotate ↻")
X.refresh(inputs, "activate")
check("no target: the info asks for one", "pick a planar face" in inputs.itemById(
    "info").formattedText.lower() or "closed sketch area" in inputs.itemById("info").formattedText)
inputs.itemById("target").addSelection(Profile())
X.refresh(inputs, "target")
check("a target: evaluated", X._X.ev is not None, inputs.itemById("info").formattedText[-300:])
check("a single marker: Origin in its words, Up: top",
      dd_names(inputs, "origin") == ["Up: top", "Up: right", "Up: bottom", "Up: left"]
      and shown(inputs, "origin") == "Up: top")

print("== Origin: Rotate steps clockwise, the dropdown jumps, the ghost follows")
pick(inputs, "layout", "Grid board")
check("a grid: its corners", dd_names(inputs, "origin") == ["Top left", "Top right",
                                                            "Bottom right", "Bottom left"])
lay = X._X.ev["layout"]
seen = []
for _ in range(4):
    X.refresh(inputs, "rotate")
    ev = X._X.ev
    seen.append((shown(inputs, "origin"), X._X.turn, ev["turn"], ev["spec"].cols, ev["spec"].rows))
c0, r0 = lay.cols, lay.rows
check(f"Rotate x4 on the {c0} x {r0} grid: Top right (swapped), Bottom right, Bottom left "
      "(swapped), Top left",
      [s_[:3] for s_ in seen] == [("Top right", 270, 270), ("Bottom right", 180, 180),
                                  ("Bottom left", 90, 90), ("Top left", 0, 0)]
      and [s_[3:] for s_ in seen] == [(r0, c0), (c0, r0), (r0, c0), (c0, r0)], seen)
pick(inputs, "origin", "Bottom left")
info = inputs.itemById("info").formattedText
check("the dropdown jumps to Bottom left; the info names it and the swap",
      X._X.turn == 90 and "bottom left corner" in info
      and f"OpenCV's board is {r0} × {c0}" in info, info[-400:])
s, n = X.values(inputs)
check("OK's values: the turn and the new rule", s["turn"] == 90 and s["origin_v"] == OR.ORIGIN_RULE
      and "frame" not in s and "chamfer" not in s, s)
pick(inputs, "layout", "Single marker")
check("back to a single marker: the same turn in its words", shown(inputs, "origin") == "Up: left")
pick(inputs, "layout", "Grid board")

print("== Corner mark: only what the tag can have; the pick survives a mode change")
pick(inputs, "mode", "White + black")
pick(inputs, "op", "New body")
check("White + black, New body: None, Cut white corner, Black triangle",
      dd_names(inputs, "corner_mark") == ["None", "Cut white corner", "Black triangle"])
pick(inputs, "corner_mark", "Cut white corner")
check("Cut white corner: the ghost's mark is the tile cut",
      X._X.ev["mark"] is not None and X._X.ev["mark"].kind == "tile_cut")
pick(inputs, "op", "Join")
check("Join: no cut on the list, the triangle shown, the pick kept",
      dd_names(inputs, "corner_mark") == ["None", "Black triangle"]
      and shown(inputs, "corner_mark") == "Black triangle" and X._X.mark_pref == "cut"
      and X._X.ev["mark"].kind == "triangle")
pick(inputs, "op", "New body")
check("New body again: back to the cut", shown(inputs, "corner_mark") == "Cut white corner"
      and X._X.ev["mark"].kind == "tile_cut")
pick(inputs, "corner_mark", "Black triangle")
check("Black triangle on a tile of its own: a triangle, the tile a plain rectangle",
      X._X.ev["mark"].kind == "triangle" and "plain rectangle" in X._X.ev["mark"].note)
pick(inputs, "layout", "ChArUco board")
check("ChArUco: None and the black square's cut, shown for the triangle asked for",
      dd_names(inputs, "corner_mark") == ["None", "Cut black square's corner"]
      and shown(inputs, "corner_mark") == "Cut black square's corner"
      and X._X.ev["mark"].kind == "black_cut" and X._X.mark_pref == "triangle")
s, n = X.values(inputs)
ev_ok = F.evaluate(Profile(), dict(F.default_settings(), **dict(s, **TARGET_INFO["s"],
                                                              mode="both")), n)
check("OK's value is the pick; the node falls back exactly as the list shows",
      s["corner_mark"] == "triangle" and ev_ok["mark"].kind == "black_cut", s["corner_mark"])

print("== OK remembers; the next dialog restores (and the old checkbox migrates)")
X.remember(inputs)
import aruco_settings  # noqa: E402
vals = aruco_settings.recall(X.CMD_ID, None)
check("remembered: the pick, not the checkbox or a Frame",
      vals.get("corner_mark") == "triangle" and not vals.get("chamfer")
      and "frame" not in vals, vals)
cmd2 = Cmd()
X.build(cmd2)
check("restored as the pick", X._X.mark_pref == "triangle"
      and shown(cmd2.commandInputs, "corner_mark") == "Black triangle")
aruco_settings.remember(X.CMD_ID, {"corner_mark": None, "chamfer": True})
cmd3 = Cmd()
X.build(cmd3)
check("an old remembered checkbox ('chamfer': true) restores as Cut white corner",
      X._X.mark_pref == "cut" and shown(cmd3.commandInputs, "corner_mark") == "Cut white corner")

print("== the edit dialog fills from a node (an older one keeps its Origin)")


def fake_node(settings, numbers, record_spec):
    cf = types.SimpleNamespace(isValid=True, name="ArUco k")
    saved = (F.settings_of, F.numbers_of, F.record_of, F.rebuild_reason, F.target_of)
    F.settings_of = lambda c: F.normalise(dict(F.default_settings(), **settings)) if c else None
    F.numbers_of = lambda c: dict(numbers)
    F.record_of = lambda c: (None, {"spec": record_spec})
    F.rebuild_reason = lambda c: None
    F.target_of = lambda c: Profile()
    return cf, saved


s_old = {"mode": "black", "op": "join", "layout": "grid", "turn": 90, "chamfer": True,
         "has_body": True, "key": "k", "parts": ["pattern", "recess"],
         "frame": {"corner": "top_left", "z": "out"}}
n_old = dict(F.default_numbers("black"), size=14.0, marker_id=3)
cf, saved = fake_node(s_old, n_old, {"layout": "grid", "cols": 3, "rows": 2})
try:
    cmd4 = Cmd()
    X.build(cmd4, edit_cf=cf)
    i4 = cmd4.commandInputs
    check("an older 3 x 2 grid stored at 90 (kept as laid out): the dialog shows Top left",
          X._X.turn == 0 and shown(i4, "origin") == "Top left")
    check("its checkbox shows as Cut white corner", X._X.mark_pref == "cut")
    X.refresh(i4, "activate")
    check("... and evaluates (Black only: the triangle)", X._X.ev is not None
          and X._X.ev["mark"].kind == "triangle"
          and dd_names(i4, "corner_mark") == ["None", "Black triangle"])
    s, n = X.values(i4)
    check("OK writes the turn it showed, under the new rule", s["turn"] == 0
          and s["origin_v"] == OR.ORIGIN_RULE and "key" not in s)
finally:
    F.settings_of, F.numbers_of, F.record_of, F.rebuild_reason, F.target_of = saved
check("nothing went to a message box", not UI.messages, UI.messages[-1:])

print()
print(f"{PASS} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
