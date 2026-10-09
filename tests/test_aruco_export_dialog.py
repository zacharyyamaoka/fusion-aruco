"""Offline test of the ArUco Export dialog (aruco_commands' Export section):
the type list at the top and what each type shows, the info line, OK's
validity, Reset, what OK passes to aruco_export.export and remembers, old
settings, and the dialog of before the types that a running Fusion keeps
registered.

No Fusion: `adsk` is a stand-in (inputs with ids, values, visibility and
parent groups), aruco_tags / aruco_core / aruco_export are stubs, and the
settings file lives in a temporary home. The real dialog is checked in
Fusion only in a test window (ArucoMarkers/aruco_export_lab.py).

Run:  python tests/test_aruco_export_dialog.py
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ArucoMarkers"))
HOME = tempfile.mkdtemp(prefix="aruco_export_dialog_")
os.environ["USERPROFILE"] = HOME          # aruco_settings' file: never the user's
os.environ["HOME"] = HOME

PASS = 0
FAIL = []


def check(label, condition, detail=""):
    global PASS
    if condition:
        PASS += 1
        print(f"  ok  {label}")
    else:
        FAIL.append(label)
        print(f"  FAIL {label}  {detail}")


# ------------------------------------------------------------------ stand-in adsk
adsk = types.ModuleType("adsk")
core = types.ModuleType("adsk.core")
fusion = types.ModuleType("adsk.fusion")
adsk.core, adsk.fusion = core, fusion
sys.modules.update({"adsk": adsk, "adsk.core": core, "adsk.fusion": fusion})


def _core_attr(name):
    if name.endswith("Handler"):                  # every event handler base
        cls = type(name, (), {})
        setattr(core, name, cls)
        return cls
    raise AttributeError(name)


core.__getattr__ = _core_attr
core.DropDownStyles = types.SimpleNamespace(TextListDropDownStyle=1)
core.ValueInput = types.SimpleNamespace(
    createByString=lambda s: float(re.match(r"([-\d.]+)\s*mm", s).group(1)) / 10.0)
MESSAGES, STARTED = [], []


class _UI:
    activeCommand = "SelectCommand"

    def messageBox(self, m):
        MESSAGES.append(m)


class _App:
    userInterface = _UI()

    def log(self, m):
        pass


core.Application = types.SimpleNamespace(get=lambda: _App())


class _Input:
    def __init__(self, reg, kind, cid, name, parent):
        self.objectType = "adsk::core::" + kind
        self.id, self.name, self.parentCommandInput = cid, name, parent
        self.isVisible = self.isEnabled = True
        self.tooltip = self.tooltipDescription = ""
        reg[cid] = self

    @property
    def shown(self):
        node = self
        while node is not None:
            if not node.isVisible:
                return False
            node = node.parentCommandInput
        return True


class _Item:
    def __init__(self, dd, name, icon):
        self.dd, self.name, self.icon, self._sel = dd, name, icon, False

    @property
    def isSelected(self):
        return self._sel

    @isSelected.setter
    def isSelected(self, v):
        if v:
            for it in self.dd.items:
                it._sel = False
        self._sel = bool(v)


class _Items:
    def __init__(self, dd):
        self.dd = dd

    def add(self, name, selected, icon="", before=-1):
        it = _Item(self.dd, name, icon)
        self.dd.items.append(it)
        if selected:
            it.isSelected = True
        return it

    @property
    def count(self):
        return len(self.dd.items)

    def item(self, i):
        return self.dd.items[i]


class _DropDown(_Input):
    def __init__(self, *a):
        super().__init__(*a)
        self.items = []
        self.listItems = _Items(self)

    @property
    def selectedItem(self):
        return next((i for i in self.items if i._sel), None)


class _Inputs:
    def __init__(self, reg=None, parent=None):
        self.reg = {} if reg is None else reg
        self.parent = parent

    def itemById(self, cid):
        return self.reg.get(cid)

    def addDropDownCommandInput(self, cid, name, style):
        return _DropDown(self.reg, "DropDownCommandInput", cid, name, self.parent)

    def addBoolValueInput(self, cid, name, is_check, res, value):
        b = _Input(self.reg, "BoolValueCommandInput", cid, name, self.parent)
        b.value, b.isCheckBox = value, is_check
        return b

    def addValueInput(self, cid, name, unit, value):
        b = _Input(self.reg, "ValueCommandInput", cid, name, self.parent)
        b.value = value
        return b

    def addGroupCommandInput(self, cid, name):
        g = _Input(self.reg, "GroupCommandInput", cid, name, self.parent)
        g.children = _Inputs(self.reg, g)
        return g

    def addTextBoxCommandInput(self, cid, name, text, rows, read_only):
        t = _Input(self.reg, "TextBoxCommandInput", cid, name, self.parent)
        t.formattedText = text
        return t


class _Event:
    def __init__(self):
        self.handlers = []

    def add(self, h):
        self.handlers.append(h)


class _Command:
    def __init__(self):
        self.commandInputs = _Inputs()
        for e in ("execute", "inputChanged", "validateInputs", "destroy"):
            setattr(self, e, _Event())
        self.size = None

    def setDialogInitialSize(self, w, h):
        self.size = (w, h)

    def setDialogMinimumSize(self, w, h):
        pass


# ------------------------------------------------------------------ stubs
class _Body:
    def __init__(self, solid=True):
        self.isSolid = solid


class _Comp:
    def __init__(self, name, solids):
        self.name = name
        self.bRepBodies = [_Body() for _ in range(solids)] + [_Body(False)]


class _Doc:
    name = "Rig v3"
    isSaved = False
    dataFile = None


class _Design:
    allComponents = [_Comp("Tag", 2), _Comp("Mount", 3), _Comp("Bracket", 1)]
    parentDocument = _Doc()


class _Inst:
    def __init__(self, hidden):
        self.hidden = hidden


class _Res:
    instances = [_Inst(False), _Inst(False), _Inst(False), _Inst(True)]
    dups = {}

    def duplicates(self):
        return self.dups


tags = types.ModuleType("aruco_tags")
tags.design = lambda: _Design()
tags.resolve_live = lambda des=None: _Res()
sys.modules["aruco_tags"] = tags
sys.modules["aruco_core"] = types.ModuleType("aruco_core")
CALLS = []
export_mod = types.ModuleType("aruco_export")
export_mod.tag_parts = lambda des=None: [("Tag", 2, ["front"]), ("Mount", 0, ["side", "top"])]
export_mod.export_dir = lambda des: os.path.join(HOME, "CADExporter", "Rig_v3")


def _fake_export(out_dir=None, **kw):
    CALLS.append(dict(kw, out_dir=out_dir))
    kind = kw.get("kind")
    return {"out_dir": out_dir or export_mod.export_dir(None), "files": [],
            "stl_files": ["aruco/stl/Tag__tile.stl"] if kind == "stl" else [],
            "stl_dir": os.path.join(HOME, "nowhere") if kind == "stl" else None,
            "pdf": None, "project": None, "beds": None}


export_mod.export = _fake_export
sys.modules["aruco_export"] = export_mod

import aruco_commands as C  # noqa: E402
import aruco_settings  # noqa: E402

check("the settings file is the temporary one",
      aruco_settings.settings_path().startswith(HOME), aruco_settings.settings_path())


def shown(inputs):
    return sorted(cid for cid, inp in inputs.reg.items() if inp.shown and cid != "info")


def pick(inputs, code):
    label = {c: lab for lab, c in C.EXPORT_TYPES}[code]
    for it in inputs.itemById("export_type").items:
        if it.name == label:
            it.isSelected = True
    C.export_changed(inputs, "export_type")


PARTS = ["part_0", "part_1", "parts"]
SHOWS = {"prusaslicer": sorted(["export_type", "orient", "bed_w", "bed_d", "open_slicer",
                                "reset"] + PARTS),
         "stl": sorted(["export_type", "orient", "open_folder", "reset"] + PARTS),
         "pdf": sorted(["export_type", "page", "landscape", "margin", "open", "reset"])}

# ------------------------------------------------------------------ first run
print("first run (nothing remembered)")
cmd = _Command()
C.export_created(cmd)
inp = cmd.commandInputs
check("the type list is the first input, labelled Export",
      next(iter(inp.reg)) == "export_type" and inp.itemById("export_type").name == "Export")
check("three types, PrusaSlicer project first and selected",
      [i.name for i in inp.itemById("export_type").items]
      == ["PrusaSlicer project", "STL files", "PDF print sheet"]
      and C._export_type(inp) == "prusaslicer")
icons = [i.icon for i in inp.itemById("export_type").items]
check("each entry has its own icon folder, with a 16 px icon in it",
      [os.path.basename(i) for i in icons]
      == ["bamArucoSendPrusa", "bamArucoExportStl", "bamArucoExportPdf"]
      and all(os.path.isfile(os.path.join(i, "16x16.png"))
              and os.path.isfile(os.path.join(i, "16x16-dark.png")) for i in icons), icons)
check("the dialog opens wider than the shared size", cmd.size == C.EXPORT_DIALOG_SIZE
      and C.EXPORT_DIALOG_SIZE[0] > C.DIALOG_SIZE[0], cmd.size)
check("a part with a visible copy starts ticked, one without starts unticked",
      inp.itemById("part_0").value is True and inp.itemById("part_1").value is False)
check("part labels are short; tags and counts are in the tooltip",
      inp.itemById("part_1").name == "Mount  ×0"
      and "side, top" in inp.itemById("part_1").tooltip
      and "3 bodies" in inp.itemById("part_1").tooltip, inp.itemById("part_1").tooltip)
check("Reset: a short label and the button's own text, not the same words twice",
      inp.itemById("reset").name == "Defaults" and inp.itemById("reset").text == "Reset")
check("the open-when-done boxes are on by default",
      all(inp.itemById(c).value is True for c in ("open", "open_slicer", "open_folder")))
infos = {}
for code in ("prusaslicer", "stl", "pdf"):
    pick(inp, code)
    check(f"{code}: only its own inputs show", shown(inp) == SHOWS[code], shown(inp))
    infos[code] = inp.itemById("info").formattedText
here = os.path.normpath(os.path.join(HOME, "CADExporter", "Rig_v3")) + os.sep
check("info names the folder", all(here in t for t in infos.values()), infos["pdf"])
check("info, every type: the URDF records",
      all("aruco.json + joint_origins.json" in t for t in infos.values()))
check("info, PrusaSlicer: the project, parts, copies, bed",
      "aruco_tags.3mf</b>: 1 part, 2 copies on 250 × 210 mm beds" in infos["prusaslicer"],
      infos["prusaslicer"])
check("info, STL: the folder, one STL per body of the ticked parts, laid flat",
      f"aruco{os.sep}stl{os.sep}</b>: 2 STLs, one per body of 1 part, laid flat by the tag"
      in infos["stl"], infos["stl"])
check("info, PDF: the file name, the visible markers, the paper",
      "print_letter.pdf</b>: 3 markers at true size, US Letter portrait" in infos["pdf"],
      infos["pdf"])
check("info: no STL / 3MF mentioned for the PDF, no PDF for the 3D types",
      ".stl" not in infos["pdf"].lower() and "3mf" not in infos["pdf"]
      and ".pdf" not in infos["stl"] and ".pdf" not in infos["prusaslicer"])

# ------------------------------------------------------------------ validity, reset
print("OK's validity and Reset")
pick(inp, "stl")
check("a part ticked: OK enabled", C.export_valid(inp))
inp.itemById("part_0").value = False
C.export_changed(inp, "part_0")
check("no part ticked: OK disabled for STL files", not C.export_valid(inp))
check("...and the info line says so", "Tick at least one part" in
      inp.itemById("info").formattedText)
pick(inp, "prusaslicer")
check("...and for the PrusaSlicer project", not C.export_valid(inp))
pick(inp, "pdf")
check("the PDF needs no part", C.export_valid(inp))
inp.itemById("page").listItems.item(1).isSelected = True
inp.itemById("bed_w").value = 30.0
inp.itemById("open").value = False
C.export_changed(inp, "reset")
check("Reset: every option back (page, bed, open, parts), the type kept",
      C._export_type(inp) == "pdf" and C._export_args(inp)["page"] == "letter"
      and inp.itemById("bed_w").value == 25.0 and inp.itemById("open").value is True
      and inp.itemById("part_0").value is True and inp.itemById("part_1").value is False)
_Res.dups = {"DICT_4X4_50#0": [1, 2]}
cmd_d = _Command()
C.export_created(cmd_d)
check("duplicated markers: a red line in the info", "run ArUco IDs first" in
      cmd_d.commandInputs.itemById("info").formattedText)
C.export_destroy()
_Res.dups = {}

# ------------------------------------------------------------------ OK
print("OK")
pick(inp, "stl")
inp.itemById("open_folder").value = False
C._ExportState.test = {"out_dir": os.path.join(HOME, "test_out")}
C.export_execute(cmd)
check("test hook: export(kind='stl') with the dialog's parts and orientation, into the "
      "test folder", CALLS[-1]["kind"] == "stl" and CALLS[-1]["include"] == ["Tag"]
      and CALLS[-1]["orientation"] == "flat"
      and CALLS[-1]["out_dir"] == os.path.join(HOME, "test_out"), CALLS[-1])
check("test hook: nothing remembered",
      "bamArucoExport" not in aruco_settings.load()["commands"])
C.export_destroy()
check("closing the dialog clears the test hook", C._ExportState.test is None
      and C._ExportState.cmd is None)
inp.itemById("orient").listItems.item(1).isSelected = True
C.export_execute(cmd)
check("OK: export(kind='stl', orientation='cad') into the default folder",
      CALLS[-1]["kind"] == "stl" and CALLS[-1]["orientation"] == "cad"
      and CALLS[-1]["out_dir"] is None, CALLS[-1])
saved = aruco_settings.load()
check("OK: the type and every type's options are remembered",
      saved["commands"]["bamArucoExport"].get("export_type") == "stl"
      and saved["commands"]["bamArucoExport"].get("open_folder") is False
      and saved["commands"]["bamArucoExport"].get("open") is True, saved["commands"])
check("OK: the ticked parts are remembered for this design",
      any(d["commands"]["bamArucoExport"].get("parts") == {"Tag": True, "Mount": False}
          for d in saved["designs"].values()), saved["designs"])
check("OK: no message box, nothing opened (open_folder off)", not MESSAGES, MESSAGES)
pick(inp, "pdf")
C.export_execute(cmd)
check("OK on the PDF: kind='pdf' with the paper options",
      CALLS[-1]["kind"] == "pdf" and CALLS[-1]["page"] == "letter"
      and CALLS[-1]["margin_mm"] == 10.0, CALLS[-1])

print("reopen")
cmd2 = _Command()
C.export_created(cmd2)
i2 = cmd2.commandInputs
check("reopen: the last type (PDF) with its options",
      C._export_type(i2) == "pdf" and i2.itemById("open").value is True)
check("reopen: the STL type's own option is kept while hidden",
      i2.itemById("open_folder").value is False and not i2.itemById("open_folder").shown)
check("reopen: only the PDF inputs show", shown(i2) == SHOWS["pdf"], shown(i2))
C.export_destroy()

# ------------------------------------------------------------------ old settings
print("settings saved by the dialog of before the types")
aruco_settings.save({"schema": "bam_aruco_settings/1", "commands": {"bamArucoExport": {
    "page": "a4", "landscape": True, "margin": 0.5, "stl": False, "beds": True,
    "bed_w": 30.0, "bed_d": 20.0, "orient": "cad", "open": False, "open_slicer": False}},
    "designs": {}})
cmd3 = _Command()
C.export_created(cmd3)
i3 = cmd3.commandInputs
check("load without errors", not MESSAGES, MESSAGES)
check("no STLs then -> opens on the PDF type", C._export_type(i3) == "pdf")
check("paper, landscape, margin, bed, orientation, PrusaSlicer box come back",
      C._export_args(i3)["page"] == "a4" and i3.itemById("landscape").value is True
      and i3.itemById("margin").value == 0.5 and i3.itemById("bed_w").value == 30.0
      and C._export_args(i3)["orientation"] == "cad"
      and i3.itemById("open_slicer").value is False)
check("their 'open' (the PDF beside the project, off then) is dropped",
      i3.itemById("open").value is True)
aruco_settings.save({"schema": "bam_aruco_settings/1", "commands": {"bamArucoExport": {
    "stl": True, "beds": True, "orient": "nonsense", "bed_w": "wide"}}, "designs": {}})
cmd4 = _Command()
C.export_created(cmd4)
check("STLs then -> the PrusaSlicer project; bad values keep the defaults",
      C._export_type(cmd4.commandInputs) == "prusaslicer"
      and C._export_args(cmd4.commandInputs)["orientation"] == "flat"
      and cmd4.commandInputs.itemById("bed_w").value == 25.0)
C.export_destroy()

# ------------------------------------------------------------------ the old dialog
print("the dialog of before the types (a running Fusion's old handler)")
legacy = _Command()
li = legacy.commandInputs
C._dropdown(li, "page", "Paper", C.PAGES, "letter")
li.addBoolValueInput("landscape", "Landscape", True, "", False)
li.addValueInput("margin", "Page margin", "mm", 1.0)
li.addBoolValueInput("stl", "STLs", True, "", True)
li.addBoolValueInput("beds", "PrusaSlicer project", True, "", True)
li.addValueInput("bed_w", "Bed width", "mm", 25.0)
li.addValueInput("bed_d", "Bed depth", "mm", 21.0)
C._dropdown(li, "orient", "Lay parts", C.ORIENT_LABELS, "flat")
g = li.addGroupCommandInput("parts", "Parts")
g.children.addBoolValueInput("part_0", "Tag", True, "", True)
g.children.addBoolValueInput("part_1", "Mount", True, "", False)
li.addBoolValueInput("open", "Open the PDF", True, "", False)
li.addBoolValueInput("open_slicer", "Open in PrusaSlicer", True, "", False)
check("its keys are still in EXPORT_DEFAULTS",
      all(k in C.EXPORT_DEFAULTS for k in ("stl", "beds", "open", "open_slicer")))
li.itemById("stl").value = False
C.export_changed(li, "stl")
check("changed: the old enable rule", li.itemById("beds").isEnabled is False)
C.export_changed(li, "reset")
check("reset works on it", li.itemById("stl").value is True)
li.itemById("open").value = False
li.itemById("open_slicer").value = False
C.export_execute(legacy)
check("OK: the old all-in-one export (no kind)",
      "kind" not in CALLS[-1] and CALLS[-1]["stl"] is True and CALLS[-1]["beds"] is True
      and CALLS[-1]["include"] == ["Tag"], CALLS[-1])
check("valid", C.export_valid(li))

shutil.rmtree(HOME, ignore_errors=True)

print("\n%d passed, %d failed" % (PASS, len(FAIL)))
if FAIL:
    for f in FAIL:
        print("  FAILED:", f)
    sys.exit(1)
