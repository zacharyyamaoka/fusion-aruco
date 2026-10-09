"""The ArUco Extrude dialog -- one dialog for making a tag and for editing
one (double-click its node in the timeline). The model is aruco_feature, the
live ghost aruco_preview, the heights bam_cad_core.aruco_slabs.

Like Fusion's Extrude: click inside a closed sketch area (a profile) or on a
planar face, then drag the arrows. On top of that, what to print: the marker
size (auto by default, 0.5 mm steps) and, for boards, which layout.

Zach, 2026-10-08: "please use same language as extrude... there is only the
height and then an offset from that surface I need to control. For both of
those support positive and negative values." Every number is measured from
the picked face / sketch plane; each colour is one extrude:

    Extrude        Auto / White + black / Black only / White only
    White offset   where the white starts (the tile; White only: the pattern)
    White height   how far it goes from there, either sign.  Arrow.
    Black offset   where the black starts. It moves WITH the white: dragging
                   or typing the white carries the black, so flush stays
                   flush and a raise or recess is kept (Black only: from the
                   face). Typing it changes only the black.
    Black height   how far the black goes from there; the default -0.6 goes
                   0.6 mm DOWN from the white top: a flush inlay.  Arrow:
                   changes only the black.
    Locating pin   None / Triangle / Round, Size, Length (arrow), Tolerance:
                   White + black as a New body only (a tag printed on its own)
    Origin         where OpenCV's frame lands -- the Joint Origin IS OpenCV's
                   frame, always (Zach, 2026-10-09): a board's origin on one
                   of the face's four corners, a single marker's up to one of
                   its four sides (bam_cad_core.aruco_origin). The dropdown
                   jumps to one; "Rotate ↻" steps clockwise. A rectangular
                   board at 90 / 270 is OpenCV's board with its cols / rows
                   swapped, on its side (same footprint). The ghost draws
                   the Joint Origin's axes (x red, y green, z blue)
    Corner mark    None / Cut white corner / Black triangle: the origin
                   corner marked by shape (aruco_chamfer). The cut needs a
                   white tile of its own (a ChArUco board: its top-left black
                   square's corner instead, any mode); the triangle works on
                   any white. Only what the tag can have is offered; the
                   ghost shows it
    Layout         boards: the layout being built first, then what really
                   fits the face (aruco_autosize.candidates_on_loops)

Zach (2026-10-08): "Super important for this to be ergonomic is LIVE PREVIEW
with dragging just like extrude!!" While the dialog is open the result is
drawn as a ghost (custom graphics, see aruco_preview) and updated on every
change; the height inputs carry Fusion's own drag arrows. The arrows, the
fields and the ghost always say the same: a Height inside the dead zone
around 0 or past the limits is written back snapped, with a note.
"""
from __future__ import annotations

import importlib
import json
import time
import traceback

import adsk.core
import adsk.fusion

import aruco_core
import aruco_feature
import aruco_preview
import aruco_settings
import aruco_tags

CMD_ID = "bamArucoExtrude"
# Zach: the default dialog cut every label off ("Auto si...", "Dictior...")
DIALOG_SIZE = (440, 680)
DIALOG_MIN = (380, 440)

try:
    _handlers  # noqa: B018
except NameError:
    _handlers = []

LAYOUT_LABELS = [("Single marker", "single"), ("Grid board", "grid"),
                 ("ChArUco board", "charuco")]
MODE_LABELS = [("Auto", "auto"), ("White + black", "both"),
               ("Black only", "black"), ("White only", "white")]
OP_LABELS = [("Join", "join"), ("New body", "new")]
PIN_LABELS = [("None", "none"), ("Triangle", "triangle"), ("Round", "round")]
TABLES = {"layout": LAYOUT_LABELS, "mode": MODE_LABELS, "op": OP_LABELS, "pin": PIN_LABELS}
# Corner mark (aruco_chamfer): every label the dropdown may show -> its
# choice ("cut" on a ChArUco board is its top-left black square's corner)
MARK_TABLE = [("None", "none"), ("Cut white corner", "cut"),
              ("Cut black square's corner", "cut"), ("Black triangle", "triangle")]
# remembered at OK (ids of the inputs). The Offset / Height of White + black
# are kept by hand below (cm, like the inputs): "white_offset",
# "white_height", "black_height", "pin_length"; the corner mark as the user
# last picked it ("corner_mark"; the old checkbox "chamfer": true = "cut").
# 2026-10-08: new keys -- the old "height" / "thickness" / "above" meant
# something else.
KEEP = ("layout", "dictionary", "auto", "mode", "op", "quiet", "grow",
        "pin", "pin_size", "pin_tol")
NOW = "Now: "
NOTHING_FITS = "Nothing fits here"
HEIGHTS = ("white_offset", "white", "black_offset", "black")
PIN_NUMS = ("pin_size", "pin_length", "pin_tol")
PIN_NOTE = "Only for White + black as a New body: a tag printed on its own."


def SL():
    return aruco_core.module("aruco_slabs")


def OR():
    return aruco_core.module("aruco_origin")


def CH():
    return aruco_core.module("aruco_chamfer")


class _X:
    """Dialog state; carried over a dev-bridge reload (see below)."""
    edit_cf = None
    cmd = None
    cand = []                  # Layouts listed in the "Layout" dropdown
    cand_key = None
    cand_src = None            # "loops" (picking sets the size) / "rect" (and forces)
    force = None
    busy = False
    tgt_key = None             # what the picked target gives, cached per target
    tgt = None
    ev_key = None              # the last evaluate(), cached per inputs
    ev = None
    ev_root = None
    mode = None
    clamp_notes = []
    swapped = None             # the face click that was taken as its profile
    manip = {}                 # input id -> the last setManipulator arguments
    last = {}                  # timings (ms) of the last refresh / preview
    touched = set()            # height / offset inputs the user set in this dialog
    delta = 0.0                # mm: the black offset over the white top (0 = flush)
    no_remember = False        # tests: OK must not overwrite the user's remembered options
    ours = {}                  # input id -> the value (mm) this code last wrote
    base = {}                  # White + black's own values (remembered / defaults), mm
    size_typed = False         # Size / Checker typed since the last Type change
    rebuild_reason = None      # edit: why OK rebuilds the node (aruco_feature)
    turn = 0                   # Origin: the pattern's turn (deg, aruco_origin)
    layout_seen = None         # the Type the last refresh saw (a repeated event is no change)
    mark_pref = "none"         # Corner mark: what the user picked (or remembered)
    mark_shown = None          # ... and what the dropdown shows (the other form where
                               # the pick cannot be had; its echo is not a pick)


_old = globals().get("_Xprev")
if _old is not None:
    for _k, _v in vars(_old).items():
        if not _k.startswith("__"):
            setattr(_X, _k, _v)
_Xprev = _X


def _ui():
    return adsk.core.Application.get().userInterface


def _mm(mm):
    return adsk.core.ValueInput.createByString(f"{round(float(mm), 6):g} mm")


def _dropdown(inputs, cid, label, items, selected):
    dd = inputs.addDropDownCommandInput(cid, label, adsk.core.DropDownStyles.TextListDropDownStyle)
    for text, value in items:
        dd.listItems.add(text, value == selected)
    return dd


def _dd(inputs, cid, items):
    dd = inputs.itemById(cid)
    name = dd.selectedItem.name if dd.selectedItem else items[0][0]
    return dict(items).get(name, items[0][1])


def _select(inputs, cid, items, value):
    dd = inputs.itemById(cid)
    for i in range(dd.listItems.count):
        it = dd.listItems.item(i)
        if dict(items).get(it.name) == value:
            it.isSelected = True
            return


def defaults():
    """The dialog's own defaults (cm for lengths, like the inputs hold them),
    for a first run and Reset."""
    S = SL()
    return {"layout": "single", "dictionary": aruco_core.module("markers").DEFAULT_DICTIONARY,
            "auto": True, "mode": "auto", "op": "join", "quiet": 1.0, "grow": False,
            "corner_mark": "none",
            "pin": "triangle", "pin_size": S.PIN_SIZE_MM / 10.0,
            "pin_tol": S.PIN_TOL_MM / 10.0, "pin_length": S.PIN_LENGTH_MM / 10.0,
            "white_offset": S.WHITE_OFFSET / 10.0, "white_height": S.WHITE_HEIGHT / 10.0,
            "black_height": S.BLACK_HEIGHT / 10.0}


def _base_from(vals):
    """White + black's own Offset / Height (mm) from remembered values (cm).
    The black always starts flush (Zach, 2026-10-08: "by default at the
    start white and black should be the same height"): a remembered black
    height is kept only as an inlay depth (negative)."""
    d = defaults()
    out = {}
    for key in ("white_offset", "white_height", "black_height"):
        v = vals.get(key, d[key])
        out[key] = float(v) * 10.0 if _num(v) else float(d[key]) * 10.0
    if out["black_height"] >= 0.0:
        out["black_height"] = float(d["black_height"]) * 10.0
    return out


def _limits(inp, lo_mm, hi_mm):
    """Min / max on a distance input, so its arrow stops there."""
    for attr, v in (("minimumValue", lo_mm / 10.0), ("maximumValue", hi_mm / 10.0)):
        try:
            setattr(inp, attr, v)
        except Exception:                          # noqa: BLE001
            pass
    for attr in ("hasMinimumValue", "hasMaximumValue"):
        try:
            setattr(inp, attr, True)
        except Exception:                          # noqa: BLE001
            pass


# ----------------------------------------------------------------- build

def build(cmd, edit_cf=None):
    """Inputs + handlers. edit_cf: the node being edited (aruco_feature wires
    that command's activate/execute/destroy)."""
    S = SL()
    _X.edit_cf = edit_cf
    _X.cmd = cmd
    _X.cand, _X.cand_key, _X.cand_src, _X.force = [], None, None, None
    _X.tgt_key = _X.tgt = _X.ev_key = _X.ev = _X.ev_root = None
    _X.manip, _X.last, _X.clamp_notes = {}, {}, []
    _X.touched, _X.delta, _X.ours, _X.size_typed = set(), 0.0, {}, False
    _X.turn, _X.rebuild_reason = 0, None
    _X.mark_pref, _X.mark_shown = "none", None
    d = defaults()
    _X.base = _base_from(d)
    try:
        cmd.setDialogInitialSize(*DIALOG_SIZE)
        cmd.setDialogMinimumSize(*DIALOG_MIN)
    except Exception:                              # noqa: BLE001
        pass
    inputs = cmd.commandInputs
    dicts = list(aruco_core.module("fiducials").ARUCO_DICTS)
    sel = inputs.addSelectionInput(
        "target", "Profile",
        "Click inside a closed sketch area, like Extrude -- or pick a planar face.")
    sel.addSelectionFilter("Profiles")
    sel.addSelectionFilter("PlanarFaces")
    sel.setSelectionLimits(1, 1)
    sel.tooltip = ("A closed sketch area (click inside the rectangle you drew; its holes "
                   "are kept free) or a planar face. The tag grows out of it, like Extrude.")
    t = _dropdown(inputs, "layout", "Type", LAYOUT_LABELS, d["layout"])
    t.tooltip = ("One marker, a grid board of markers, or a ChArUco board. Changing it "
                 "turns Auto size back on, unless you typed a size since the last change.")
    a = inputs.addBoolValueInput("auto", "Auto size", True, "", d["auto"])
    a.tooltip = ("Pick the size from the face's largest clear rectangle (holes and notches "
                 "kept free): one marker's sticker fills its largest clear square; a board "
                 "spans 70 % of that rectangle's short side. Rounded to 0.5 mm. Type a size "
                 "to take over.")
    sz = inputs.addValueInput("size", "Size", "mm", _mm(20))
    sz.tooltip = ("Size = the BLACK square of one marker, its black border included: what "
                  "OpenCV calls the marker length. The white border around it (the quiet zone, "
                  "in Advanced, 1 module by default) comes on top: the sticker you see is "
                  "Size + 2 modules (Size x 8/6 for 4x4).")
    sq = inputs.addValueInput("square", "Checker", "mm", _mm(20))
    sq.tooltip = "ChArUco: one chessboard square. The marker inside it follows (0.75 for 4x4)."
    fit = inputs.addDropDownCommandInput("fit", "Layout", adsk.core.DropDownStyles.TextListDropDownStyle)
    fit.listItems.add(NOTHING_FITS, True)
    fit.tooltip = ("Boards: the layout being built first, then every layout that really fits "
                   "this face or area (holes and notches count), biggest marker first. "
                   "Picking one sets the size.")
    og = _dropdown(inputs, "origin", "Origin", OR().items(d["layout"]), 0)
    og.tooltip = ("Where OpenCV's frame lands -- the tag's Joint Origin is always exactly "
                  "the frame OpenCV reports. A board: its origin on this corner of the face "
                  "(x right, y down, z into the board). A single marker: its centre, its up "
                  "towards this side (x right, y up, z out). A rectangular board turned a "
                  "quarter is OpenCV's board with its columns and rows swapped, lying on its "
                  "side: the same footprint and markers. The ghost shows the Joint Origin: "
                  "x red, y green, z blue.")
    # a short label + the button's own text (an empty name showed the text
    # twice on the Reset button)
    rt = inputs.addBoolValueInput("rotate", "Turn", False, "", False)
    try:
        rt.text = "Rotate ↻"
    except Exception:                              # noqa: BLE001
        pass
    rt.tooltip = ("The next Origin clockwise: the pattern turns 90° clockwise in its own "
                  "footprint (top left -> top right -> bottom right -> bottom left).")
    cm = _dropdown(inputs, "corner_mark", "Corner mark", MARK_TABLE[:2] + MARK_TABLE[3:],
                   d["corner_mark"])
    cm.tooltip = ("Mark the origin corner by shape (a printer has no grey for the PDF's "
                  "corner triangle): the top-left of the pattern as the Origin lays it, "
                  "OpenCV's corner 0 / the board's origin. Cut white corner: a white tile of "
                  "its own (White + black, New body) gets its corner cut at 45°, 0.7 module, "
                  "the part shows through (a ChArUco board: its top-left black square's outer "
                  "corner, 2 modules, the white fills it). Black triangle: a 0.6-module black "
                  "triangle in the white border's corner -- any white, the tile stays a plain "
                  "rectangle. Only what this tag can have is listed. Never the marker's own "
                  "black corner: that costs detection (measured).")
    dd = _dropdown(inputs, "dictionary", "Dictionary", [(n, n) for n in dicts], d["dictionary"])
    dd.tooltip = "The ArUco dictionary (OpenCV's name). 4X4_50 suits small printed tags."
    mid = inputs.addIntegerSpinnerCommandInput("marker_id", "ID", 0, 2319, 1, 0)
    mid.tooltip = "The marker's id. A board: its first id; the others follow."
    md = _dropdown(inputs, "mode", "Extrude", MODE_LABELS, d["mode"])
    md.tooltip = ("Auto: black only into a body under it, else white + black. "
                  "White + black: a white tile with the black pattern. Black only: just the "
                  "black, into or onto the body (the body is the white). White only: just "
                  "the white, on a body that prints black.")
    op = _dropdown(inputs, "op", "Operation", OP_LABELS, d["op"])
    op.tooltip = "Join the white tile to the body under it, or make it a body of its own."
    wo = inputs.addValueInput("white_offset", "White offset", "mm", _mm(_X.base["white_offset"]))
    wo.tooltip = ("Where the white extrude starts, measured from the face or sketch plane, "
                  "like Extrude's Start: Offset. Negative: below it. White + black: the tile; "
                  "White only: the white pattern.")
    w = inputs.addDistanceValueCommandInput("white", "White height", _mm(_X.base["white_height"]))
    w.tooltip = ("How far the white extrudes from its offset, like Extrude's Distance. "
                 "Negative goes the other way, into what is under it. Drag the arrow at the "
                 "tile's edge.")
    bo = inputs.addValueInput("black_offset", "Black offset", "mm",
                              _mm(_X.base["white_offset"] + _X.base["white_height"]))
    bo.tooltip = ("Where the black extrude starts, from the same face or sketch plane. It "
                  "moves with the white: dragging or typing the white height or offset carries "
                  "the black, so a flush black stays flush and a raised or recessed one keeps "
                  "its step. Typing it changes only the black. Black only: from the face.")
    b = inputs.addDistanceValueCommandInput("black", "Black height", _mm(_X.base["black_height"]))
    b.tooltip = ("How far the black extrudes from its offset, either sign. The default -0.6 "
                 "goes 0.6 mm down from the white top: an inlay level with the white. "
                 "Positive: it stands proud. The arrow on the pattern changes only the black.")
    for di in (w, b):
        di.isEnabled = False          # no arrow at the origin before a target is picked
        _limits(di, -S.LIMIT_MM, S.LIMIT_MM)
    fl = inputs.addBoolValueInput("flip", "Flip", True, "", False)
    fl.tooltip = "A sketch with no body under it: grow the tag to the other side."
    pg = inputs.addGroupCommandInput("pin_group", "Locating pin")
    pg.isExpanded = True
    pc = pg.children
    ps = _dropdown(pc, "pin", "Shape", PIN_LABELS, d["pin"])
    ps.tooltip = ("A tag printed on its own and pressed onto the part: pins under the white "
                  "tile, matching holes in the body under it. Triangle: one, at the tag's "
                  "top-left corner (OpenCV's corner 0, where the PDF's corner mark is); it "
                  "keys the rotation by itself. Round: two, at the two top corners.")
    pz = pc.addValueInput("pin_size", "Size", "mm", _mm(d["pin_size"] * 10.0))
    pz.tooltip = "Triangle: the length of its two legs. Round: the pins' diameter."
    pl = pc.addDistanceValueCommandInput("pin_length", "Length", _mm(d["pin_length"] * 10.0))
    pl.tooltip = "How far the pins reach down from the tile's underside. Drag the arrow."
    pl.isEnabled = False
    _limits(pl, S.PIN_LIMITS["pin_length"][0], S.PIN_LIMITS["pin_length"][1])
    pt = pc.addValueInput("pin_tol", "Tolerance", "mm", _mm(d["pin_tol"] * 10.0))
    pt.tooltip = ("The holes in the body under it: the pin grown this much all round, and "
                  "this much deeper than the pin is long.")
    pc.addTextBoxCommandInput("pin_note", "", PIN_NOTE, 1, True)
    grp = inputs.addGroupCommandInput("advanced", "Advanced")
    grp.isExpanded = False
    g = grp.children
    q = g.addFloatSpinnerCommandInput("quiet", "Quiet zone", "", 0.0, 4.0, 0.5, d["quiet"])
    q.tooltip = "White margin around the pattern, in modules (single markers and grids)."
    gr = g.addBoolValueInput("grow", "Fuse corners", True, "", d["grow"])
    gr.tooltip = "Grow every black cell by 10 µm so cells that touch at a corner fuse."
    k = g.addStringValueInput("key", "Name", "")
    k.tooltip = "The tag's name (optional): its parameters are aruco_<name>_..."
    # a short label + the button's own text: Zach saw "Reset to defaults" twice
    # (label and button) with the name empty and text set
    rs = g.addBoolValueInput("reset", "Defaults", False, "", False)
    try:
        rs.text = "Reset"
    except Exception:                              # noqa: BLE001
        pass
    rs.tooltip = "Every option back to the add-in's defaults (remembered at OK)."
    inputs.addTextBoxCommandInput("info", "", "Pick a sketch profile or a planar face.", 7, True)
    if edit_cf is None:
        _restore(inputs)
        _fresh_id(inputs)
        cmd.execute.add(_keep(_CreateExecute()))
    else:
        _fill_from(inputs, edit_cf)
    _X.layout_seen = _dd(inputs, "layout", LAYOUT_LABELS)
    _origin_items(inputs, _X.layout_seen)          # this Type's words from the start
    cmd.inputChanged.add(_keep(_Changed()))
    cmd.executePreview.add(_keep(_Preview()))
    cmd.destroy.add(_keep(_Destroy()))
    if edit_cf is None:
        # an edit refreshes from aruco_feature's activate, AFTER the timeline
        # is rolled to the node (so the face is the one the tag was made on)
        cmd.activate.add(_keep(_Activate()))
    cmd.okButtonText = "OK"


def _keep(h):
    _handlers.append(h)
    del _handlers[:-80]
    return h


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _set(inputs, cid, mm):
    """Write a length (mm) into an input; remembered as ours, so a change
    event it may echo is not taken for the user's."""
    inp = inputs.itemById(cid)
    if inp is None:
        return
    if abs(inp.value * 10.0 - mm) > 1e-7:
        inp.value = mm / 10.0
    _X.ours[cid] = round(float(mm), 6)


def _by_user(inputs, cid):
    inp = inputs.itemById(cid)
    if inp is None:
        return False
    mine = _X.ours.get(cid)
    return mine is None or abs(round(inp.value * 10.0, 6) - mine) > 1e-6


def _restore(inputs):
    try:
        vals = aruco_settings.recall(CMD_ID, None)
        conv = {k: v for k, v in vals.items() if k in KEEP}
        aruco_settings.apply(inputs, conv, TABLES)
        _X.base = _base_from(vals)
        if _num(vals.get("pin_length")):
            _set(inputs, "pin_length", float(vals["pin_length"]) * 10.0)
        _set(inputs, "white_offset", _X.base["white_offset"])
        _set(inputs, "white", _X.base["white_height"])
        _set(inputs, "black", _X.base["black_height"])
        # the corner mark as last picked; the old checkbox ("chamfer": true)
        # is "cut", which gives what it gave
        _X.mark_pref = CH().choice_of(vals)
        _show_mark(inputs, ["cut", "triangle"], _X.mark_pref)
    except Exception:                              # noqa: BLE001
        adsk.core.Application.get().log("ArUco Extrude: options not restored\n"
                                        + traceback.format_exc())


def remember(inputs):
    """At OK: the options, and White + black's Offset / Height (only from a
    White + black or Black only tag -- White only's white is the pattern,
    not a tile height to start the next tag with)."""
    if _X.no_remember:
        return
    try:
        vals = aruco_settings.snapshot(inputs, KEEP, TABLES)
        vals["pin_length"] = inputs.itemById("pin_length").value
        vals["corner_mark"] = _X.mark_pref if _X.mark_pref in CH().CHOICES else "none"
        vals["chamfer"] = None                     # the old checkbox: superseded
        mode = _X.mode or "both"
        if mode == "both":
            vals["white_offset"] = inputs.itemById("white_offset").value
            vals["white_height"] = inputs.itemById("white").value
        if mode in ("both", "black"):
            vals["black_height"] = inputs.itemById("black").value
        aruco_settings.remember(CMD_ID, vals)
    except Exception:                              # noqa: BLE001
        adsk.core.Application.get().log("ArUco Extrude: options not remembered\n"
                                        + traceback.format_exc())


def _fill_from(inputs, cf):
    """Edit: the node's settings and numbers into the dialog."""
    s, n = aruco_feature.settings_of(cf), aruco_feature.numbers_of(cf)
    sel = inputs.itemById("target")
    sel.isVisible = False
    sel.setSelectionLimits(0, 1)
    _select(inputs, "layout", LAYOUT_LABELS, s["layout"])
    _select(inputs, "mode", MODE_LABELS, s["mode"])
    _select(inputs, "op", OP_LABELS, s.get("op", "join"))
    # the Origin it is built with (a node made before 2026-10-09 kept a
    # rectangular board's 90 / 270 as laid out: that is what it shows)
    _X.turn = aruco_feature.origin_of(cf)
    _X.mark_pref = CH().choice_of(s)
    _show_mark(inputs, ["cut", "triangle"], _X.mark_pref)
    _select(inputs, "dictionary", [(x, x) for x in
                                   aruco_core.module("fiducials").ARUCO_DICTS], s["dictionary"])
    _select(inputs, "pin", PIN_LABELS, s.get("pin") or "none")
    inputs.itemById("auto").value = False
    inputs.itemById("size").value = n["size"] / 10.0
    inputs.itemById("square").value = max(n["square"], 0.1) / 10.0
    inputs.itemById("marker_id").value = int(round(n["marker_id"]))
    for cid, key in (("white_offset", "white_offset"), ("white", "white_height"),
                     ("black_offset", "black_offset"), ("black", "black_height")):
        _set(inputs, cid, n[key])
    for cid in PIN_NUMS:
        _set(inputs, cid, n[cid])
    # what the node has is the user's (a Black only node has no white yet:
    # its white fields keep White + black's defaults for a switch)
    have = set(aruco_feature.params_for(dict(s, parts=s.get("parts") or [])))
    _X.touched = {cid for cid, key in (("white_offset", "white_offset"), ("white", "white_height"),
                                       ("black", "black_height")) if key in have}
    # where the black starts against the white top (Black only: the face)
    _X.delta = n["black_offset"] - SL().white_top(s["mode"], n)
    inputs.itemById("quiet").value = float(s["quiet_modules"])
    inputs.itemById("grow").value = bool(s.get("grow"))
    inputs.itemById("flip").value = bool(s.get("flip"))
    k = inputs.itemById("key")
    k.value = s.get("key", "")
    k.isEnabled = False
    _X.force = tuple(s["force"]) if s.get("force") else None
    try:
        _X.rebuild_reason = aruco_feature.rebuild_reason(cf)
    except Exception:                              # noqa: BLE001
        _X.rebuild_reason = None


def _fresh_id(inputs):
    try:
        s, n = values(inputs)
        spec = aruco_core.module("markers").TagSpec(dictionary=s["dictionary"],
                                                   layout=s["layout"])
        count = 1 if s["layout"] == "single" else 2
        inputs.itemById("marker_id").value = aruco_tags.next_free_id(spec.dictionary, count)
    except Exception:                              # noqa: BLE001
        pass


# ----------------------------------------------------------------- values

def target_of(inputs):
    if _X.edit_cf is not None:
        return aruco_feature.target_of(_X.edit_cf)
    sel = inputs.itemById("target")
    return sel.selection(0).entity if sel.selectionCount else None


def values(inputs):
    """-> (settings, numbers mm) as the dialog says. 'auto' size -> size 0;
    mode 'auto' stays for resolve_mode(). The Offsets and Heights are what
    the fields show (refresh keeps the black offset at the white top +
    _X.delta)."""
    auto = inputs.itemById("auto").value
    s = {"layout": _dd(inputs, "layout", LAYOUT_LABELS),
         "dictionary": inputs.itemById("dictionary").selectedItem.name,
         "quiet_modules": float(inputs.itemById("quiet").value),
         "grow": bool(inputs.itemById("grow").value),
         # the user's pick: evaluate() gives the other form where this tag
         # cannot have it, exactly as the list shows it (aruco_chamfer.choose)
         "corner_mark": _X.mark_pref if _X.mark_pref in CH().CHOICES else "none",
         "turn": int(_X.turn),
         "origin_v": OR().ORIGIN_RULE,        # this dialog offers every Origin
         "size_auto": bool(auto),
         "force": list(_X.force) if (_X.force and not auto) else None,
         "flip": bool(inputs.itemById("flip").value),
         "mode": _dd(inputs, "mode", MODE_LABELS),
         "op": _dd(inputs, "op", OP_LABELS),
         "pin": _dd(inputs, "pin", PIN_LABELS),
         "key": inputs.itemById("key").value.strip()}

    def mm(cid):
        return float(inputs.itemById(cid).value) * 10.0
    n = {"marker_id": int(inputs.itemById("marker_id").value),
         "size": 0.0 if auto else mm("size"),
         "square": 0.0 if auto else mm("square"),
         "white_offset": mm("white_offset"), "white_height": mm("white"),
         "black_offset": mm("black_offset"), "black_height": mm("black"),
         "pin_size": mm("pin_size"), "pin_length": mm("pin_length"), "pin_tol": mm("pin_tol")}
    if s["layout"] != "charuco":
        n["square"] = 0.0
    if _X.edit_cf is not None:
        s.pop("key", None)
    return s, n


def resolve_mode(s, has_body):
    if s.get("mode") == "auto":
        s["mode"] = "black" if has_body else "both"
    return s


def _target(inputs):
    """(target, {"s": target-derived settings, "body", "note", "m": to-root
    matrix}) -- cached per target (profile_frame walks every body)."""
    target = target_of(inputs)
    if target is None:
        _X.tgt_key, _X.tgt = None, None
        return None, None
    ctx = getattr(target, "assemblyContext", None)
    key = (getattr(target, "entityToken", None) or str(id(target)),
           ctx.fullPathName if ctx is not None else "", _X.edit_cf is not None)
    if key == _X.tgt_key and _X.tgt is not None:
        return target, _X.tgt
    if _X.edit_cf is not None:
        base = aruco_feature.settings_of(_X.edit_cf)
        der = {k: base.get(k) for k in aruco_feature.TARGET_KEYS}
        # an older node's Advanced > Rotate (the dialog no longer sets it)
        der["rotate_deg"] = base.get("rotate_deg", 0)
        info = {"s": der, "body": None, "note": None, "body_name": _body_name(_X.edit_cf)}
    else:
        der, body = aruco_feature.creation_settings(target, aruco_feature.default_settings())
        _b, note = aruco_feature.body_under(target)
        der = {k: der.get(k) for k in aruco_feature.TARGET_KEYS if k != "key"}
        info = {"s": der, "body": body, "note": note,
                "body_name": aruco_feature.native_of(body).name if body is not None else None}
    info["m"] = aruco_feature.to_root_matrix(target, info["s"])
    _X.tgt_key, _X.tgt = key, info
    return target, info


def _body_name(cf):
    """Edit: the name of the body under the node's target (for the info):
    a combine's target that is not the node's own tile."""
    try:
        for f in cf.features:
            c = adsk.fusion.CombineFeature.cast(f)
            if c is not None and not c.targetBody.name.startswith(aruco_feature.TILE_BODY):
                return c.targetBody.name
    except Exception:                              # noqa: BLE001
        pass
    return None


def _target_info(inputs):
    """(target, full settings incl. target-derived ones, body under) or Nones."""
    target, info = _target(inputs)
    if target is None:
        return None, None, None
    s, _n = values(inputs)
    for k, v in info["s"].items():
        if k == "key" and _X.edit_cf is None:
            continue
        s[k] = v
    return target, resolve_mode(s, s.get("has_body")), info["body"]


# ---------------------------------------------------------------- refresh

def _visibility(inputs, mode=None):
    s, _n = values(inputs)
    kind = s["layout"]
    inputs.itemById("square").isVisible = kind == "charuco"
    inputs.itemById("fit").isVisible = kind != "single"
    target, info = _target(inputs)
    is_profile = isinstance(aruco_feature.native_of(target), adsk.fusion.Profile)
    has_body = bool(info["s"].get("has_body")) if info else None
    if mode is None:
        mode = s["mode"] if s["mode"] != "auto" else ("black" if has_body else "both")
    inputs.itemById("op").isVisible = mode == "both" and bool(has_body)
    inputs.itemById("flip").isVisible = is_profile and has_body is False
    for cid in ("white", "white_offset"):
        inputs.itemById(cid).isVisible = mode in ("both", "white")
    for cid in ("black", "black_offset"):
        inputs.itemById(cid).isVisible = mode in ("both", "black")
    # the pins: a tag printed on its own (White + black, a new body)
    applies = SL().pin_applies(mode, s["op"], bool(has_body))
    shape_on = s["pin"] != "none"
    inputs.itemById("pin").isEnabled = applies
    for cid in PIN_NUMS:
        inputs.itemById(cid).isEnabled = applies and shape_on
    inputs.itemById("pin_note").isVisible = not applies
    return mode


def _sync_fields(inputs, mode):
    """The Offset / Height fields as this mode shows them: what the user set
    stays; the rest takes the mode's defaults (White only: the white pattern
    inlaid 0.6 like Black only's black; White + black: the remembered tile);
    the black offset is the white top (Black only: the face) + _X.delta, so
    a white change carries the black."""
    S = SL()
    if mode == "white":
        for cid, v in (("white_offset", 0.0), ("white", S.PATTERN_HEIGHT)):
            if cid not in _X.touched:
                _set(inputs, cid, v)
    else:
        for cid, key in (("white_offset", "white_offset"), ("white", "white_height")):
            if cid not in _X.touched:
                _set(inputs, cid, _X.base[key])
    if "black" not in _X.touched:
        _set(inputs, "black", _X.base["black_height"])
    _set(inputs, "black_offset", S.clamp_offset(_white_top_now(inputs, mode) + _X.delta))


def _white_top_now(inputs, mode):
    n = {"white_offset": inputs.itemById("white_offset").value * 10.0,
         "white_height": inputs.itemById("white").value * 10.0}
    return SL().white_top(mode, n)


def _take_change(inputs, changed, notes):
    """A height / offset / pin input the user moved: remember it is theirs,
    and write back what the model can build (a Height of 0 has no slab;
    everything has limits), so field, arrow and ghost agree."""
    S = SL()
    if changed not in HEIGHTS + PIN_NUMS or not _by_user(inputs, changed):
        return
    inp = inputs.itemById(changed)
    if not inp.isValidExpression:
        return
    v = inp.value * 10.0
    _X.touched.add(changed)
    if changed in ("white", "black"):
        lo = S.WHITE_MIN_MM if changed == "white" else S.BLACK_MIN_MM
        prev = _X.ours.get(changed)
        sv = S.snap_height(v, lo, prev)
        label = "White height" if changed == "white" else "Black height"
        if abs(sv - v) > 1e-9:
            notes.append(f"{label} {v:.2f} mm: using {sv:.2f} mm "
                         + ("(a colour needs some height)" if abs(v) < lo else "(the limit)"))
    elif changed in ("white_offset", "black_offset"):
        sv = S.clamp_offset(v)
        if abs(sv - v) > 1e-9:
            notes.append(f"offset {v:.2f} mm is past the limit: using {sv:.2f} mm")
        if changed == "black_offset":
            # the user moved the black: the step to the white top is his now
            _X.delta = sv - _white_top_now(inputs, _X.mode or "both")
    else:
        lo, hi = S.PIN_LIMITS[changed]
        sv = max(lo, min(hi, v))
        if abs(sv - v) > 1e-9:
            notes.append(f"pin {changed[4:]} {v:.2f} mm: using {sv:.2f} mm")
    _set(inputs, changed, sv)


def _manip(inp, origin, direction):
    key = tuple(round(c, 6) for c in list(origin) + list(direction))
    if _X.manip.get(inp.id) == key:
        return
    inp.setManipulator(adsk.core.Point3D.create(*origin), adsk.core.Vector3D.create(*direction))
    _X.manip[inp.id] = key


def _place_handles(inputs, mode, ev_root, n=None):
    """Each Height's arrow starts at its Offset, along the tag's normal (root
    space): the white's at the tile's edge (White only: on the pattern), the
    black's on the pattern's centre, the pins' Length down from the tile's
    underside at the first pin."""
    w, b, pl = inputs.itemById("white"), inputs.itemById("black"), inputs.itemById("pin_length")
    if ev_root is None or n is None:
        w.isEnabled = b.isEnabled = pl.isEnabled = False
        _X.manip = {}
        return
    w.isEnabled = b.isEnabled = True
    tag_o, x, y, z = ev_root["tag"]
    lift = ev_root.get("lift_cm", 0.0)
    centre = [tag_o[k] - z[k] * lift for k in range(3)]       # on the face plane
    edge = centre
    if mode == "both" and ev_root.get("tile") is not None:
        to, tx, ty, tz, tw, th = ev_root["tile"]
        edge = [to[k] + tx[k] * tw / 2.0 for k in range(3)]
    wo, bo = n["white_offset"] / 10.0, n["black_offset"] / 10.0
    _manip(w, [(edge if mode == "both" else centre)[k] + z[k] * wo for k in range(3)], z)
    _manip(b, [centre[k] + z[k] * bo for k in range(3)], z)
    pins = ev_root.get("pins") or []
    plan = ev_root.get("plan")
    if pins and plan is not None and plan.pin is not None and ev_root.get("tile") is not None:
        to, tx, ty, tz, _tw, _th = ev_root["tile"]
        pts = SL().outline_points(pins[0], 8)
        u = sum(p[0] for p in pts) / len(pts) / 10.0
        v = sum(p[1] for p in pts) / len(pts) / 10.0
        top = plan.pin[1] / 10.0
        _manip(pl, [to[k] + tx[k] * u + ty[k] * v + tz[k] * top for k in range(3)],
               [-c for c in tz])
    else:
        pl.isEnabled = False


def _prefer_profile(inputs):
    """A click on a planar face inside a closed area of a visible sketch
    drawn on it means that area, like Extrude (Zach, 2026-10-08: his
    rectangle was drawn on the disc's face): swap the face for the profile."""
    sel = inputs.itemById("target")
    if sel.selectionCount != 1:
        return
    picked = sel.selection(0)
    face = adsk.fusion.BRepFace.cast(picked.entity)
    if face is None:
        return
    try:
        pt = picked.point
        # a real click lies ON the face (a scripted addSelection has no point)
        plane = adsk.core.Plane.cast(face.geometry)
        d = pt.vectorTo(plane.origin).dotProduct(plane.normal) if plane else 1.0
        ok, prm = face.evaluator.getParameterAtPoint(pt)
        if abs(d) > 1e-4 or not ok or not face.evaluator.isParameterOnFace(prm):
            return
        prof = aruco_feature.profile_at(pt)
    except Exception:                              # noqa: BLE001
        prof = None
    if prof is None:
        return
    sel.clearSelection()
    sel.addSelection(prof)
    try:
        name = aruco_feature.native_of(prof).parentSketch.name
    except Exception:                              # noqa: BLE001
        name = "the sketch"
    _X.swapped = (f"the closed area of '{name}' you clicked in, like Extrude "
                  "(hide the sketch to pick the face itself)")


def _candidate_source(target, s):
    """What evaluate() fits the layout on, so the list offers what OK builds:
    ("loops", loops in m, angles) for a face (its real outline: holes and
    notches count) and for a profile fitted to its outline (Black / White
    only, not a plain rectangle); ("rect", w m, h m) for a profile's box."""
    nat = aruco_feature.native_of(target)
    kind = s["layout"]
    rot = float(s.get("rotate_deg", 0))
    angles = (0.0,) if (kind == "single" or rot) else (0.0, 90.0)
    if isinstance(nat, adsk.fusion.Profile):
        o, x, y, z, w, h = aruco_feature.profile_rect(nat, s)
        if s.get("mode") != "both":
            loops = aruco_feature._profile_loops_m(nat, o, x, y)
            if not aruco_feature._is_box(loops, w, h):
                return ("loops", loops, angles), ("p", nat.parentSketch.entityToken, s.get("mode"))
        return ("rect", w / 100.0, h / 100.0), ("r", round(w, 6), round(h, 6))
    o, x, y, z, w, h = aruco_tags.face_frame(nat, rot)
    return ("loops", aruco_feature._face_loops_m(nat, o, x, y), angles), ("f", nat.entityToken)


def _set_fit_items(inputs, names):
    fit = inputs.itemById("fit")
    have = [fit.listItems.item(i).name for i in range(fit.listItems.count)]
    if have != names:
        fit.listItems.clear()
        for i, nm in enumerate(names):
            fit.listItems.add(nm, i == 0)
    elif fit.selectedItem is None or fit.selectedItem.index != 0:
        fit.listItems.item(0).isSelected = True


def _set_items(dd, names, want):
    """A dropdown's items as `names` (rebuilt only when they differ), `want`
    selected."""
    have = [dd.listItems.item(i).name for i in range(dd.listItems.count)]
    if have != names:
        dd.listItems.clear()
        for nm in names:
            dd.listItems.add(nm, nm == want)
    elif dd.selectedItem is None or dd.selectedItem.name != want:
        for i in range(dd.listItems.count):
            if dd.listItems.item(i).name == want:
                dd.listItems.item(i).isSelected = True


def _origin_items(inputs, layout):
    """The Origin dropdown in this Type's words (a board's corners, a single
    marker's up), "Rotate"'s order, the current one selected."""
    _X.turn = OR().norm(_X.turn)
    _set_items(inputs.itemById("origin"), [lbl for lbl, _t in OR().items(layout)],
               OR().label(layout, _X.turn))


def _take_origin(inputs, changed):
    """The Origin the user asked for: a pick in the dropdown, or "Rotate"."""
    if changed == "rotate":
        _X.turn = OR().rotate_cw(_X.turn)
    elif changed == "origin":
        og = inputs.itemById("origin")
        t = OR().turn_of(_dd(inputs, "layout", LAYOUT_LABELS),
                         og.selectedItem.name) if og.selectedItem is not None else None
        if t is not None:
            _X.turn = t


def _mark_label(choice, board_cut=False):
    if choice == "cut" and board_cut:
        return "Cut black square's corner"
    return CH().CHOICE_LABELS[choice]


def _show_mark(inputs, valid, want, board_cut=False):
    """The Corner mark dropdown: None and the marks this tag can have
    (`valid`, aruco_chamfer.valid_marks); `want` shown when it is one, else
    the other form (what OK makes: aruco_feature falls back the same way)."""
    shown, _note = CH().choose(want, valid)
    names = ["None"] + [_mark_label(v, board_cut) for v in valid]
    _X.mark_shown = shown
    _set_items(inputs.itemById("corner_mark"), names, _mark_label(shown, board_cut))
    return shown


def _take_mark(inputs, changed):
    """A Corner mark the user picked becomes his preference (kept across a
    mode / Type change that cannot have it); our own re-selection is not one."""
    if changed != "corner_mark":
        return
    v = _dd(inputs, "corner_mark", MARK_TABLE)
    if v != _X.mark_shown:
        _X.mark_pref = v


def _mark_items(inputs, ev, s):
    """After evaluate(): the Corner mark's list for this tag as built."""
    spec, plan = ev["spec"], ev["plan"]
    valid = CH().valid_marks(spec.layout, s["mode"], plan.op, spec.rows, spec.legacy)
    _show_mark(inputs, valid, _X.mark_pref,
               board_cut=CH().charuco_cut(spec.layout, spec.rows, spec.legacy))


def _origin_line(spec, turn):
    """The info's Joint Origin line (aruco_origin.describe): OpenCV's frame
    for this Origin, and a quarter-turned rectangular board's swap."""
    return OR().describe(spec.layout, turn, spec.cols, spec.rows) + \
        ": the ghost's axes, x red, y green, z blue"


def _fill_candidates(inputs, target, s, ev):
    """The Layout dropdown: the layout being built first (always the one
    selected: its text is what OK makes), then every layout that fits,
    each at its largest size (aruco_autosize.candidates_on_loops on a face's
    real outline, 0.5-1.4 s with holes, so cached per face / type /
    dictionary / quiet zone; candidates() on a rectangle's box)."""
    if s["layout"] == "single":
        return
    a = aruco_core.module("aruco_autosize")
    try:
        src, where = _candidate_source(target, s)
    except Exception:                              # noqa: BLE001
        src, where = None, None
    key = (where, s["layout"], s["dictionary"], float(s["quiet_modules"]),
           int(s.get("rotate_deg", 0)))
    if key != _X.cand_key:
        _X.cand_key = key
        t0 = time.perf_counter()
        try:
            if src is None:
                _X.cand, _X.cand_src = [], None
            elif src[0] == "loops":
                _X.cand = a.candidates_on_loops(s["layout"], src[1], dictionary=s["dictionary"],
                                                quiet_modules=float(s["quiet_modules"]),
                                                angles_deg=src[2], limit=8)
                _X.cand_src = "loops"
            else:
                _X.cand = a.candidates(s["layout"], src[1], src[2], dictionary=s["dictionary"],
                                       quiet_modules=float(s["quiet_modules"]))
                _X.cand_src = "rect"
        except Exception:                          # noqa: BLE001
            _X.cand = []
        _X.last["candidates_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
    names = [NOW + ev["layout"].label()]
    for lay in _X.cand:
        badge = f"  ({', '.join(lay.badges)})" if getattr(lay, "badges", None) else ""
        names.append(lay.label() + badge)
    _set_fit_items(inputs, names)


def refresh(inputs, changed=""):
    if _X.busy:
        return
    _X.busy = True
    t0 = time.perf_counter()
    try:
        _refresh(inputs, changed)
    finally:
        _X.busy = False
        _X.last["refresh_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)


def _evaluate(target, s, n):
    """evaluate(), cached per (target, settings, numbers)."""
    key = json.dumps([_X.tgt_key, s, n], sort_keys=True, default=str)
    if key != _X.ev_key or _X.ev is None:
        t0 = time.perf_counter()
        _X.ev = aruco_feature.evaluate(target, s, n)
        _X.ev_key = key
        _X.last["evaluate_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
    return _X.ev


def _refresh(inputs, changed):
    markers = aruco_core.module("markers")
    notes = []
    _take_origin(inputs, changed)
    _take_mark(inputs, changed)
    if changed == "reset":
        _X.turn = 0
        d = defaults()
        aruco_settings.apply(inputs, {k: v for k, v in d.items() if k in KEEP}, TABLES)
        _X.base = _base_from(d)
        _X.touched, _X.delta = set(), 0.0
        _set(inputs, "pin_length", d["pin_length"] * 10.0)
        _X.force = None
        _X.mark_pref = d["corner_mark"]
        _show_mark(inputs, ["cut", "triangle"], _X.mark_pref)
    if changed in ("size", "square"):
        inputs.itemById("auto").value = False
        _X.force = None
        _X.size_typed = True
    if changed == "auto" and inputs.itemById("auto").value:
        _X.force = None
    if changed == "fit":
        fit = inputs.itemById("fit")
        idx = fit.selectedItem.index if fit.selectedItem else 0
        if 0 < idx <= len(_X.cand):
            lay = _X.cand[idx - 1]
            # every entry rebuilds from its own size; a box's list also
            # needs its count forced (several counts share a size there)
            _X.force = (lay.cols, lay.rows) if _X.cand_src == "rect" else None
            inputs.itemById("auto").value = False
            inputs.itemById("size").value = lay.marker_m * 100.0
            if lay.square_m:
                inputs.itemById("square").value = lay.square_m * 100.0
    kind_now = _dd(inputs, "layout", LAYOUT_LABELS)
    if changed == "layout" and kind_now != _X.layout_seen:
        # Zach, 2026-10-08: a single marker's size left in Size made a grid
        # clamp to nothing. A new type sizes itself, unless a size was typed
        # since the last type change. (Only a real change: the same event
        # can arrive twice.)
        if not _X.size_typed:
            inputs.itemById("auto").value = True
        _X.size_typed = False
        _X.force = None
        _X.cand_key = None
    _X.layout_seen = kind_now
    _origin_items(inputs, kind_now)
    if changed in ("layout", "dictionary") and _X.edit_cf is None:
        _X.force = None
        _fresh_id(inputs)
    if changed == "target":
        _X.manip = {}
        _X.swapped = None
        if _X.edit_cf is None:
            _prefer_profile(inputs)
    _take_change(inputs, changed, notes)
    mode = _visibility(inputs)
    _sync_fields(inputs, mode)
    _X.mode = mode
    _X.clamp_notes = notes
    lines = []
    target, s, body = _target_info(inputs)
    if target is None:
        _X.ev = _X.ev_root = None
        _place_handles(inputs, mode, None)
        inputs.itemById("info").formattedText = (
            "Click inside a closed sketch area (like Extrude) or pick a planar face.")
        return
    _s0, n = values(inputs)
    try:
        ev = _evaluate(target, s, n)
    except Exception as exc:                       # noqa: BLE001
        _X.ev = _X.ev_root = None
        _place_handles(inputs, mode, None)
        if s["layout"] != "single":
            _set_fit_items(inputs, [NOTHING_FITS])
        inputs.itemById("info").formattedText = f"<span style='color:#c00'>{exc}</span>"
        return
    _X.ev_root = aruco_feature.ev_in_root(ev, _X.tgt["m"] if _X.tgt else None)
    _place_handles(inputs, s["mode"], _X.ev_root, n)
    spec = ev["spec"]
    plan = ev["plan"]
    if inputs.itemById("auto").value:
        inputs.itemById("size").value = spec.marker_length_m * 100.0
        if spec.layout == "charuco":
            inputs.itemById("square").value = spec.square_length_m * 100.0
    _fill_candidates(inputs, target, s, ev)
    _mark_items(inputs, ev, s)
    lines.append(f"<b>{spec.label()}</b>")
    lines.append(size_line(spec))
    mode = s["mode"]
    where = {"both": ("white tile, joined to the body" if plan.op == "join"
                      else "white tile, a new body"),
             "black": "on the body (the body is the white)" if s.get("has_body")
             else "a black body of its own (nothing under it)",
             "white": "on the body (print the body black)" if s.get("has_body")
             else "a white body of its own (nothing under it)"}[mode]
    lines.append(where)
    for line in SL().describe(plan):
        if line.startswith("black") and mode in ("both", "black"):
            ref = "the white top" if mode == "both" else "the face"
            d = n["black_offset"] - SL().white_top(mode, n)
            line += (f" (starts at {ref})" if abs(d) < 1e-6 else
                     f" (starts {abs(d):.2f} mm {'above' if d > 0 else 'below'} {ref})")
        lines.append(line)
    for note in plan.notes:
        lines.append(f"<span style='color:#c60'>{note}</span>")
    lines.append(_origin_line(spec, ev.get("turn", 0)))
    if ev.get("mark") is not None:
        lines.append(_mark_line(ev["mark"], _X.tgt.get("body_name") if _X.tgt else None))
    if plan.pin is not None:
        lines.append(_pin_line(s, n, plan, _X.tgt.get("body_name") if _X.tgt else None))
    if getattr(_X, "swapped", None):
        lines.append(_X.swapped)
    nat = aruco_feature.native_of(target)
    if (mode == "both" and isinstance(nat, adsk.fusion.Profile)
            and nat.profileLoops.count > 1):
        lines.append("<span style='color:#c60'>the white tile is the area's box: it covers "
                     "the holes in it (Black only keeps off them)</span>")
    if _X.tgt and _X.tgt.get("note"):
        lines.append(f"<span style='color:#c60'>{_X.tgt['note']}</span>")
    if _X.edit_cf is not None and getattr(_X, "rebuild_reason", None):
        lines.append(f"<span style='color:#c60'>{_X.rebuild_reason}</span>")
    if s.get("host_xform") and _X.edit_cf is None and body is not None:
        lines.append(f"built in '{aruco_feature.native_of(body).parentComponent.name}', "
                     "the part the sketch sits on")
    for note in _X.clamp_notes:
        lines.append(f"<span style='color:#c60'>{note}</span>")
    for note in ev["notes"]:
        if note.startswith(CH().INSTEAD):           # the Corner mark list shows it
            lines.append(f"<span style='color:#888'>{note[len(CH().INSTEAD):]}</span>")
        elif not note.startswith("origin "):
            lines.append(f"<span style='color:#c60'>{note}</span>")
    adv = markers.suggest(spec.marker_count, marker_length_m=spec.marker_length_m)
    lines.append(f"readable to ~{adv['max_distance_m']} m at 1080p/70°")
    cap = aruco_core.module("fiducials").ARUCO_DICTS[spec.dictionary]
    if spec.ids[-1] >= cap:
        lines.append(f"<span style='color:#c00'>ids {spec.ids[0]}..{spec.ids[-1]} "
                     f"exceed {spec.dictionary} (0..{cap - 1})</span>")
    if not aruco_feature.is_registered():
        lines.append("<span style='color:#c60'>live feature unavailable this session: "
                     "the tag is made as a timeline group</span>")
    inputs.itemById("info").formattedText = "<br>".join(lines)


def _mark_line(mark, body_name):
    """Corner mark: what the corner gets, in the body's name (a tile joined
    to it: why a triangle and not a cut)."""
    note = mark.note
    if body_name:
        note = note.replace("joined to the body: a cut corner would cut the body",
                            f"joined to '{body_name}': a cut corner would cut '{body_name}'")
    return note


def _pin_line(s, n, plan, body_name):
    shape = {"triangle": "1 triangle pin (top-left corner)",
             "round": "2 round pins (top corners)"}.get(s.get("pin"), "pins")
    out = (f"{shape}, {n['pin_size']:.2f} mm, {n['pin_length']:.2f} mm long: "
           "WHITE, joined to the tile (it prints with it)")
    hole = next((sl for what, sl in plan.recess if what == "hole"), None)
    if hole is not None:
        out += (f"; holes in '{body_name or 'the body under it'}' "
                f"{n['pin_tol']:.2f} mm bigger all round, {-hole[0]:.2f} mm deep")
    elif not s.get("has_body"):
        out += "; nothing under it to hole"
    return out


def _mm_text(v_mm):
    """28.0, 27.25, 37.33: one decimal at least, two at most."""
    t = "%.2f" % v_mm
    return t[:-1] if t.endswith("0") else t


def size_line(spec):
    """What Size means, in the sizes Zach sees (2026-10-08: he thinks in
    "the square I see", which is the sticker): the marker (the black square
    OpenCV measures) and the sticker (marker + the white border)."""
    L = spec.marker_length_m * 1000.0
    mod = spec.module_m * 1000.0
    tw, th = (v * 1000.0 for v in spec.tile_size_m)
    if spec.layout == "charuco":
        return (f"marker {_mm_text(L)} mm · checker {_mm_text(spec.square_length_m * 1000.0)} mm"
                f" · board {tw:.1f} × {th:.1f} mm (no outer border) · module {mod:.2f} mm")
    q = float(spec.quiet_modules)
    box = f"{tw:.1f} mm" if abs(tw - th) < 1e-6 else f"{tw:.1f} × {th:.1f} mm"
    if q <= 0:
        return f"marker {_mm_text(L)} mm · no white border (quiet zone 0) · module {mod:.2f} mm"
    return (f"marker {_mm_text(L)} mm · sticker {box} (white border {q:g} module"
            f"{'' if q == 1 else 's'} = {q * mod:.2f} mm)")


def preview(inputs):
    """The ghost for the current inputs (executePreview)."""
    t0 = time.perf_counter()
    if _X.ev_root is None:
        aruco_preview.clear()
        return None
    des = aruco_tags.design()
    res = aruco_preview.show(des, _X.ev_root, _X.mode or "both")
    _X.last["preview_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
    return res


# --------------------------------------------------------------- handlers

class _Changed(adsk.core.InputChangedEventHandler):
    def notify(self, args):
        try:
            inputs = args.input.parentCommand.commandInputs
        except Exception:                          # noqa: BLE001
            inputs = args.inputs
        try:
            refresh(inputs, args.input.id)
        except Exception:                          # noqa: BLE001
            try:
                inputs.itemById("info").formattedText = (
                    "<span style='color:#c00'>" + traceback.format_exc()[-300:] + "</span>")
            except Exception:                      # noqa: BLE001
                pass


class _Preview(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            preview(args.command.commandInputs)
        except Exception:                          # noqa: BLE001
            adsk.core.Application.get().log("ArUco Extrude preview\n" + traceback.format_exc())


class _Destroy(adsk.core.CommandEventHandler):
    def notify(self, args):
        aruco_preview.clear()
        _X.cmd = None


class _Activate(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            refresh(args.command.commandInputs, "activate")
        except Exception:                          # noqa: BLE001
            pass


class _CreateExecute(adsk.core.CommandEventHandler):
    def notify(self, args):
        aruco_preview.clear()
        try:
            inputs = args.command.commandInputs
            remember(inputs)
            target = target_of(inputs)
            s, n = values(inputs)
            _t, s_full, _b = _target_info(inputs)
            s["mode"] = s_full["mode"]
            aruco_feature.create(target, dict(aruco_feature.default_settings(), **s), n)
        except Exception:                          # noqa: BLE001
            args.executeFailed = True
            _ui().messageBox("ArUco Extrude failed:\n\n" + traceback.format_exc())


class _Created(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            importlib.reload(aruco_settings)
            build(args.command)
        except Exception:                          # noqa: BLE001
            _ui().messageBox("ArUco Extrude dialog failed:\n\n" + traceback.format_exc())
