"""ArUco Extrude: the tag as ONE timeline feature (Fusion Custom Features).

Zach, 2026-10-08: "We call it Aruco Extrude ... I think that can be the main
entry point... and it can work like extrude. you can extrude a normal face
and you can also extrude a sketch ... extrude just the white or only the
black. have the white and black the same height (or different ones, etc) ...
If we click the aruco extrude icon in the timeline then we can edit the
settings". The mechanics were proven by aruco_cf_probe
(docs/research_aruco_timeline_feature.md); this is the production version.

The heights (2026-10-08, Zach: "please use same language as extrude ...
there is only the height and then an offset from that surface ... positive
and negative values"): each colour is one extrude from the face / sketch
plane, Offset then Height further, either sign (bam_cad_core.aruco_slabs).

    white  Offset 0,   Height +2     the tile (White only: the white pattern)
    black  Offset 2,   Height -0.6   0.6 down from the white top: a flush
                                     inlay (Black only: Offset 0, Height -0.6)

What one ArUco Extrude is, in timeline order (all inside the one node; the
roles present are aruco_slabs.plan(...).parts, kept in the spec as "parts"):

    Base Feature  "tag tile <key>"     the white tile (mode "both")
    Combine join                       tile -> the body under it (op "join")
    Base Feature  "aruco pin <key>"    locating pins under a NEW tile
    Combine join                       pins -> the tile
    Base Feature  "aruco pocket <key>" cut tool: the black's footprint from
    Combine cut                        its bottom up to the white top, out of
                                       the tile / the joined body
    Base Feature  "aruco cells <key>"  the black cells (modes both / black)
               or "aruco white <key>"  the white pattern (mode white: the
                                       body under it prints black)
    Base Feature  "aruco recess <key>" cut tool: where a slab goes below the
    Combine cut                        face, and the pins' holes, out of the
                                       body under

    custom parameters  aruco_<key>_marker_id, _size, _square, _white_offset,
                       _white_height, _black_offset, _black_height, _pin_size,
                       _pin_length, _pin_tol   (Modify > Change Parameters,
                       expressions welcome; editing one recomputes). In White +
                       black, _black_offset IS the expression "max(<white_offset>;
                       <white_offset> + <white_height>) + <step>": a white change
                       carries the black (flush stays flush, a step is kept).
    named value "spec" everything that is not a number (JSON, SPEC_VERSION)
    dependency "target" the planar face, or the sketch profile

The Joint Origin "aruco <key>" IS OpenCV's frame, always (Zach, 2026-10-09:
"100% I always want the frame to match perfectly OpenCV. If OpenCV doesn't
support it I don't want to either"): a single marker's centre (x right, y
up, z out), a board's top-left corner (x right, y down, z into it). The
Frame option of 2026-10-08 is gone; a node or record that still carries a
"frame" is read as OpenCV's frame and loses it on its next write.

Origin (settings "turn", bam_cad_core.aruco_origin): how the pattern lies
on its footprint after the fit -- where OpenCV's frame lands. Four choices
for every layout: a single marker turned about its centre, a board's
origin on any of the face's four corners. A rectangular board at 90 / 270
deg is laid with its cols / rows swapped (a 4 x 2 as OpenCV's 2 x 4, on its
side: the same footprint, size and count), so ids, cells, the corner mark,
pins, variants, the PDF and the detector frame all come from that one spec.
A node made before 2026-10-09 (no "origin_v") keeps a rectangular board's
90 / 270 as laid out, as it was. A face tag's x is fixed at creation
("x_base", "x_ref"): an upstream edit that reverses the face's longest edge
-- or, on a square face, picks a perpendicular one -- cannot turn it.

Corner mark (settings "corner_mark": "none" / "cut" / "triangle", Zach
2026-10-08: a printer has no grey ink; 2026-10-09: "the ability to add the
black corner to the white"): the origin corner -- the pattern's top-left
after the Origin, OpenCV's corner 0 / the board origin -- marked by shape
(bam_cad_core.aruco_chamfer, measured in docs/proposal_aruco_chamfer.md).
"cut": a tile of its own (White + black, New body) gets its corner cut at
45 deg, 0.7 module, the part shows through; a ChArUco board its top-left
black square's outer corner cut, 2 modules, the white fills it, in every
mode. "triangle": a black triangle in the quiet zone's corner of any white
-- the tile stays a plain rectangle (White only: a notch in the white).
Asked where it cannot be, a mark is the other form (an info note). The old
checkbox ("chamfer": true) reads as "cut", which makes what it made. All
inside the existing parts (tile, pocket, pattern, recess), so changing it
is a compute; the record keeps it as fusion.mark for the copies' variants.

A node made before (parameters _height, _thickness, _above; no "parts")
keeps computing as it was (aruco_slabs.from_legacy maps its numbers); its
next edit rebuilds it with the parameters above. A parameter change that
adds or removes a part (a black moved out of the white, say) cannot be done
by compute: the node says so, and its next edit rebuilds it.

compute (Fusion calls it when a parameter or anything upstream changes)
re-evaluates the frame and the layout and swaps the base features' bodies.
It may touch nothing else -- the record attribute (bam_aruco/tags) and the
Joint Origin at the optical frame are refreshed by the commands (create,
edit) and by refresh_live() (export).

Where it lives (2026-10-08, Zach's "GIM4305 Aruco Tag": a ROOT sketch drawn
on a disc of Component1:1). The body under a profile is looked for in the
sketch's component AND every occurrence below it (aruco_tags.
candidate_bodies); the features go into that body's NATIVE component (so
every copy of the part carries the tag, as for a face), the frame computed
from the sketch is taken there by host_xform (stored at creation: the
inverse of the occurrence's transform), and so are the record and the Joint
Origin. A body of an externally referenced component cannot take a
combine: the tag is then bodies of its own. A face click inside a visible
sketch's closed area on that face means the area (profile_at), like Extrude.

Colours (measured 2026-10-08): customFeatures.add() resets its base-feature
bodies to the component's default appearance, and so does updateBody() in
compute, and a timeline roll that replays a base feature. paint() runs
after add() and after every edit (paint_all), compute repaints inside its
own base-feature edit, and a joined tile's faces carry face-level white
through the join. A tile the pocket cuts into pieces (a black that reaches
the tile's underside: Fusion names the second piece "tag tile <key> (1)",
2026-10-09) is white in every piece: paint() finds them by name, and the
tile's faces carry white through the cut.
"""
from __future__ import annotations

import dataclasses
import json
import math
import os
import sys
import time
import traceback
import types

import adsk.core
import adsk.fusion

import aruco_core
import aruco_tags

HERE = os.path.dirname(os.path.abspath(__file__))
DEF_ID = "bamAruco.ArucoExtrude"
DEF_NAME = "ArUco Extrude"
EDIT_CMD_ID = "bamArucoExtrudeEdit"
AUTO_CMD_ID = "bamArucoAutoExec"
ICON_DIR = os.path.join(HERE, "resources", "bamArucoExtrude")
STATE_KEY = "_aruco_feature_state"          # outside every module we reload
SPEC_VERSION = 2
MODES = ("both", "black", "white")
OPS = ("join", "new")
HEIGHT_PARAMS = ("white_offset", "white_height", "black_offset", "black_height")
PIN_PARAMS = ("pin_size", "pin_length", "pin_tol")
PARAMS = ("marker_id", "size", "square") + HEIGHT_PARAMS + PIN_PARAMS
LEGACY_PARAMS = ("height", "thickness", "above")       # nodes made before 2026-10-08
# autosize notes that only inform (the dialog shows them; the timeline node
# gets a warning only for the others: clamped, unprintable, ...); the corner
# mark's "the other form" (aruco_chamfer.INSTEAD)
INFO_NOTES = ("not a rectangle", "origin ", "corner mark form: ")
INFO_IN = (" fit, but ",)       # a board capped at its dictionary's ids
# settings derived from the target at creation; an edit keeps them
TARGET_KEYS = ("source", "z_sign", "x_sketch", "has_body", "key", "host_xform",
               # a face tag's x, fixed at creation (native component frame):
               # x_base the face frame's, x_ref the fitted pattern's (before
               # the Origin turn) -- an upstream edit cannot turn it 180°
               "x_base", "x_ref")
PARAM_LABELS = {"marker_id": "Marker ID (first)", "size": "Marker size",
                "square": "Checker size", "white_offset": "White offset",
                "white_height": "White height", "black_offset": "Black offset",
                "black_height": "Black height", "pin_size": "Pin size",
                "pin_length": "Pin length", "pin_tol": "Pin hole tolerance"}
WHITE_BODY = "aruco white"
CELLS_BODY = "aruco cells"
TILE_BODY = "tag tile"
PIN_BODY = "aruco pin"
POCKET_BODY = "aruco pocket"
RECESS_BODY = "aruco recess"
PIN_JOIN_OVERLAP_CM = 0.001     # the pins reach 10 um into the tile: a clean join
EDIT_ROUTES = ("APIFeatureEditCommand", "FusionEditFeatureCommand", EDIT_CMD_ID)


# ------------------------------------------------------------------ state

def _st():
    st = sys.modules.get(STATE_KEY)
    if st is None:
        st = types.ModuleType(STATE_KEY)
        st.definition = None
        st.compute = None
        st.starting = None
        st.edit_created = None
        st.auto_created = None
        st.cmd_handlers = []
        st.pending_cf = None
        st.edit = None             # {"cf", "restore", "rolled", "auto"} during an edit
        st.auto = None             # in_command() request
        st.compute_log = []
        st.register_log = []
        sys.modules[STATE_KEY] = st
    return st


def _app():
    return adsk.core.Application.get()


def _ui():
    return _app().userInterface


def M():
    return aruco_core.module("markers")


def A():
    return aruco_core.module("aruco_autosize")


def SL():
    return aruco_core.module("aruco_slabs")


def CH():
    return aruco_core.module("aruco_chamfer")


def OR():
    return aruco_core.module("aruco_origin")


def is_info(note):
    """An autosize note that only informs (no warning on the node)."""
    return note.startswith(INFO_NOTES) or any(t in note for t in INFO_IN)


def is_registered():
    st = _st()
    return st.definition is not None


# --------------------------------------------------------------- handlers
# Handlers call module-level functions by name, so a dev-bridge reload of
# this module (same module dict) swaps the behaviour under live handlers.

class _Compute(adsk.fusion.CustomFeatureEventHandler):
    def notify(self, args):
        _on_compute(args)


class _Starting(adsk.core.ApplicationCommandEventHandler):
    def notify(self, args):
        _on_starting(args)


class _EditCreated(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        _on_edit_created(args)


class _AutoCreated(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        _on_auto_created(args)


class _CmdEvent(adsk.core.CommandEventHandler):
    def __init__(self, fn_name):
        super().__init__()
        self.fn_name = fn_name

    def notify(self, args):
        globals()[self.fn_name](args)


def _keep(h):
    st = _st()
    st.cmd_handlers.append(h)
    del st.cmd_handlers[:-60]
    return h


def register(host="ArucoMarkers"):
    """Definition + edit command + handlers. MUST run inside the add-in's
    run() (measured: a definition made from a custom event makes every
    customFeatures.add fail with "scriptInfoFound"), and the add-in's
    manifest needs an "id" (else "make params invalid")."""
    st = _st()
    ui = _ui()
    out = {}
    for cid, name, attr, cls in ((EDIT_CMD_ID, "Edit ArUco Extrude", "edit_created", _EditCreated),
                                 (AUTO_CMD_ID, "ArUco (scripted step)", "auto_created", _AutoCreated)):
        d = ui.commandDefinitions.itemById(cid)
        if d is None:
            d = ui.commandDefinitions.addButtonDefinition(cid, name, name, "")
            setattr(st, attr, None)
        if getattr(st, attr) is None:
            h = cls()
            d.commandCreated.add(h)
            setattr(st, attr, h)
    if st.starting is None:
        h = _Starting()
        ui.commandStarting.add(h)
        st.starting = h
    icons = ICON_DIR if os.path.isdir(ICON_DIR) else os.path.join(HERE, "resources", "bamArucoTag")
    d = adsk.fusion.CustomFeatureDefinition.create(DEF_ID, DEF_NAME, icons)
    if d is None:
        raise RuntimeError("CustomFeatureDefinition.create returned None")
    d.editCommandId = EDIT_CMD_ID
    if st.compute is None or st.definition is None or d != st.definition:
        h = _Compute()
        d.customFeatureCompute.add(h)
        st.compute = h
    st.definition = d
    out.update(id=d.id, name=d.defaultName, icon=d.iconFolder,
               editCommandId=d.editCommandId, host=host, at=round(time.time(), 3))
    st.register_log = (st.register_log + [out])[-10:]
    return out


def unregister():
    st = _st()
    ui = _ui()
    if st.definition is not None and st.compute is not None:
        try:
            st.definition.customFeatureCompute.remove(st.compute)
        except Exception:                          # noqa: BLE001
            pass
    st.compute = None
    st.definition = None
    if st.starting is not None:
        try:
            ui.commandStarting.remove(st.starting)
        except Exception:                          # noqa: BLE001
            pass
    st.starting = None
    for cid in (EDIT_CMD_ID, AUTO_CMD_ID):
        d = ui.commandDefinitions.itemById(cid)
        if d is not None:
            d.deleteMe()
    st.edit_created = st.auto_created = None
    st.cmd_handlers = []


# --------------------------------------------------------------- settings

def default_settings():
    return {"v": SPEC_VERSION, "key": "", "mode": "black", "op": "join",
            "layout": "single", "dictionary": M().DEFAULT_DICTIONARY,
            "quiet_modules": 1.0, "grow": False, "rotate_deg": 0, "legacy": False,
            "size_auto": True, "force": None, "flip": False,
            # locating pins (White + black, New body): "none" / "triangle" / "round"
            "pin": "none",
            # Origin: the pattern turned on its own footprint (degrees, CCW
            # about the face normal): where OpenCV's frame lands, 4 choices
            # for every layout (bam_cad_core.aruco_origin; a rectangular
            # board at 90 / 270 swaps its cols / rows). create() stamps
            # "origin_v" (aruco_origin.ORIGIN_RULE); a node without it keeps
            # the old rule. The old "rotate_deg" turned the frame BEFORE the
            # fit; it stays for nodes that have it, the dialog never sets it.
            "turn": 0,
            # Corner mark (bam_cad_core.aruco_chamfer): "none" / "cut" (the
            # white tile's corner; a ChArUco board's top-left black square)
            # / "triangle" (a black triangle in any white corner). Inside the
            # existing parts: changing it is a compute, never a rebuild. The
            # old checkbox "chamfer": true reads as "cut" (normalise).
            "corner_mark": "none",
            # scripts: start the black at the white top (Black only: the
            # face). create() resolves it and does not store it; in White +
            # black the black offset parameter is ALWAYS the white top + a
            # kept delta (an expression), see link_black.
            "black_follows": False,
            # set at creation from the target
            "source": "", "z_sign": 1, "x_sketch": None, "has_body": False,
            "host_xform": None}


def normalise(s, partial=False):
    """Settings as this code keeps them (a copy): the old checkbox
    "chamfer" becomes "corner_mark" (true: "cut", which makes what it made);
    the removed Frame ("frame", 2026-10-08) is dropped -- the Joint Origin is
    OpenCV's frame. partial: an edit's changes (no default filled in)."""
    s = dict(s or {})
    if "chamfer" in s:
        on = bool(s.pop("chamfer"))
        if s.get("corner_mark") in (None, "none"):
            s["corner_mark"] = "cut" if on else "none"
    if not partial:
        s.setdefault("corner_mark", "none")
    if "corner_mark" in s and s["corner_mark"] not in CH().CHOICES:
        s["corner_mark"] = "none"
    s.pop("frame", None)
    return s


def default_numbers(mode="both"):
    """mm (marker_id unitless): Offset / Height per colour, pins."""
    n = {"marker_id": 0, "size": 0.0, "square": 0.0}
    n.update(SL().default_numbers(mode if mode in MODES else "both"))
    n.update(SL().pin_defaults())
    return n


def effective_op(s):
    """'join' needs a body under the target; otherwise the tile is new."""
    if s.get("mode") != "both":
        return None
    return "join" if (s.get("op") == "join" and s.get("has_body")) else "new"


def resolve_follow(s, n):
    """n with the black offset at the white top (the face for Black only)
    when s says it follows -- what the dialog and create() build."""
    if s.get("black_follows") and s.get("mode") in ("both", "black"):
        n = dict(n, black_offset=SL().white_top(s["mode"], n))
    return n


def plan_of(s, n):
    """aruco_slabs.plan for these settings and numbers."""
    return SL().plan(s["mode"], s.get("op", "join"), bool(s.get("has_body")), n,
                     s.get("pin") or "none")


def structure(s, n):
    """What features the node holds; a change here means rebuild, not compute."""
    return plan_of(s, n).structure + (bool(s.get("has_body")),)


def rebuild_reason(cf):
    """Why OK in the edit dialog will rebuild this node instead of updating
    it, or None. Fusion shows only "Compute Failed" on a node whose new
    parameters need other parts, so the dialog says it."""
    s = settings_of(cf)
    if not s:
        return None
    if is_legacy(s):
        return ("made by an older ArUco Extrude: OK rebuilds it with Offset / Height "
                "parameters (same geometry)")
    try:
        built, want = node_structure(s), structure(s, numbers_of(cf))
    except Exception:                              # noqa: BLE001
        return None
    if built != want:
        return ("its parameters now need " + ", ".join(want[2]) + " (it has "
                + ", ".join(built[2]) + "): OK rebuilds it")
    return None


def node_structure(s):
    """The structure a node was BUILT with (its spec's "parts"), or None for
    a node made before 2026-10-08 (no "parts": rebuilt on its next edit)."""
    if not s or not s.get("parts"):
        return None
    return (s["mode"], effective_op(s), tuple(s["parts"]), bool(s.get("has_body")))


# ---------------------------------------------------------- frames (cm)

def _v(p):
    return [p.x, p.y, p.z]


def _unit(a):
    n = math.sqrt(sum(c * c for c in a)) or 1.0
    return [c / n for c in a]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _rotz(x, z, deg):
    if not deg:
        return x
    return _unit(aruco_tags._rot(x, z, deg))


def native_of(ent):
    try:
        if ent is not None and ent.assemblyContext is not None:
            return ent.nativeObject
    except Exception:                              # noqa: BLE001
        pass
    return ent


def _profile_points(profile):
    sk = profile.parentSketch
    pts = []
    for loop in profile.profileLoops:
        for pc in loop.profileCurves:
            try:
                ev = pc.geometry.evaluator
                ok, p0, p1 = ev.getParameterExtents()
                ok, strokes = ev.getStrokes(p0, p1, 0.01)
                pts += [_v(sk.sketchToModelSpace(q)) for q in (strokes if ok else [])]
            except Exception:                      # noqa: BLE001
                pass
    return pts


def sketch_axes(sk):
    o0 = _v(sk.sketchToModelSpace(adsk.core.Point3D.create(0, 0, 0)))
    ex = [a - b for a, b in zip(_v(sk.sketchToModelSpace(adsk.core.Point3D.create(1, 0, 0))), o0)]
    ey = [a - b for a, b in zip(_v(sk.sketchToModelSpace(adsk.core.Point3D.create(0, 1, 0))), o0)]
    return o0, _unit(ex), _unit(ey), _unit(_cross(ex, ey))


def profile_rect(profile, s):
    """(origin, x, y, z, w, h) cm of a profile in its component, with the x
    direction and side fixed at creation (s["x_sketch"], s["z_sign"]) so a
    resized rectangle never flips the marker by 90°."""
    profile = native_of(profile)
    sk = profile.parentSketch
    o0, ex, ey, ez = sketch_axes(sk)
    xs = s.get("x_sketch") or [1.0, 0.0]
    x = _unit([ex[i] * xs[0] + ey[i] * xs[1] for i in range(3)])
    sign = float(s.get("z_sign", 1)) * (-1.0 if s.get("flip") else 1.0)
    z = [c * sign for c in ez]
    x = _rotz(x, z, float(s.get("rotate_deg", 0)))
    y = _cross(z, x)
    pts = _profile_points(profile)
    if not pts:
        raise ValueError("the profile has no curves")
    p0 = pts[0]
    us = [_dot([a - b for a, b in zip(p, p0)], x) for p in pts]
    vs = [_dot([a - b for a, b in zip(p, p0)], y) for p in pts]
    cu, cv = (min(us) + max(us)) / 2.0, (min(vs) + max(vs)) / 2.0
    o = [p0[i] + x[i] * cu + y[i] * cv for i in range(3)]
    # the plane through the sketch, not a stroke sample off it
    off = _dot([a - b for a, b in zip(o, o0)], ez)
    o = [o[i] - ez[i] * off for i in range(3)]
    return o, x, y, z, max(us) - min(us), max(vs) - min(vs)


def _matrix(arr):
    m = adsk.core.Matrix3D.create()
    m.setWithArray([float(v) for v in arr])
    return m


def _xf_point(m, p):
    q = adsk.core.Point3D.create(*p)
    q.transformBy(m)
    return [q.x, q.y, q.z]


def _xf_vector(m, v):
    q = adsk.core.Vector3D.create(*v)
    q.transformBy(m)
    return _unit([q.x, q.y, q.z])


def _xf_frame(m, fr):
    """(o, x, y, z, *rest) through a rigid matrix."""
    o, x, y, z = fr[:4]
    return (_xf_point(m, o), _xf_vector(m, x), _xf_vector(m, y), _xf_vector(m, z)) + tuple(fr[4:])


def to_root_matrix(target, s):
    """Host-component space -> root space (None = identity): what the ghost
    and the dialog's arrows need. The inverse of host_xform takes the frame
    back into the target's own component; the target's occurrence (it is a
    proxy when picked below the root) takes it to the root."""
    m = None
    if s and s.get("host_xform"):
        m = _matrix(s["host_xform"])
        m.invert()
    ctx = getattr(target, "assemblyContext", None)
    if ctx is not None:
        t = ctx.transform2
        if m is None:
            m = t.copy()
        else:
            m.transformBy(t)
    return m


def ev_in_root(ev, m):
    """evaluate()'s frames through to_root_matrix (a shallow copy)."""
    if m is None:
        return ev
    out = dict(ev)
    out["tag"] = _xf_frame(m, ev["tag"])
    out["base"] = _xf_frame(m, ev["base"])
    for k in ("tile", "pin_frame"):
        if ev.get(k) is not None:
            out[k] = _xf_frame(m, ev[k])
    return out


def host_of(target, body, s=None):
    """Where an ArUco Extrude's features live: the NATIVE component of the
    body it joins / pockets (so every copy of that part carries the tag, as
    for a face), else the target's own component -- or the root, for a face
    of an external reference (creation_settings set host_xform for it)."""
    target = native_of(target)
    if body is not None:
        return native_of(body).parentComponent
    if isinstance(target, adsk.fusion.Profile):
        return target.parentSketch.parentComponent
    comp = target.body.parentComponent
    if s and s.get("host_xform"):
        return adsk.fusion.Design.cast(comp.parentDesign).rootComponent
    return comp


def body_under(target):
    """(body or None, note or None) an ArUco Extrude on `target` would build
    into. A profile's body is found in the sketch component's space through
    occurrences (a proxy when it lives below); a body of an externally
    referenced component is read-only here and does not count."""
    tgt = native_of(target)
    if isinstance(tgt, adsk.fusion.Profile):
        body = aruco_tags.profile_frame(tgt)[7]
    else:
        body = target.body if target is not None else None
    if body is None:
        return None, None
    xref = aruco_tags.external_owner(body)
    if xref is None and not isinstance(tgt, adsk.fusion.Profile):
        xref = aruco_tags.external_owner(target)
    if xref is not None:
        return None, (f"'{body.name}' belongs to the external reference '{xref.name}' "
                      "(read-only here): the tag is made as a body of its own")
    return body, None


def creation_settings(target, s):
    """Fill the target-derived settings: source, has_body, z_sign, x_sketch,
    host_xform (sketch space -> the host component's space, when the body
    under a profile lives in an occurrence: the frame is computed from the
    sketch and the features are built in the body's component)."""
    s = dict(s)
    body, _note = body_under(target)
    picked = target
    target = native_of(target)
    s["host_xform"] = None
    if isinstance(target, adsk.fusion.Profile):
        o, x, y, z, w, h, face, _b = aruco_tags.profile_frame(target)
        sk = target.parentSketch
        _o0, ex, ey, ez = sketch_axes(sk)
        s["source"] = "profile"
        s["has_body"] = body is not None
        s["z_sign"] = 1 if _dot(z, ez) >= 0 else -1
        s["x_sketch"] = [round(_dot(x, ex), 9), round(_dot(x, ey), 9)]
        ctx = getattr(body, "assemblyContext", None) if body is not None else None
        if ctx is not None:
            # the body's occurrence places its component in the sketch's
            # component; the inverse takes sketch-space frames into it
            m = ctx.transform2.copy()
            m.invert()
            s["host_xform"] = [round(v, 12) for v in m.asArray()]
        return s, body
    s["source"] = "face"
    s["has_body"] = body is not None
    s["flip"] = False
    ctx = getattr(picked, "assemblyContext", None)
    if body is None and ctx is not None:
        # a face of an external reference: the tag is bodies of its own in
        # the root, its frame taken through the occurrence the face was
        # picked in
        s["host_xform"] = [round(v, 12) for v in ctx.transform2.asArray()]
    return s, body


# ------------------------------------------------------------- evaluate

def _layout_rect(kind, w_m, h_m, s, n):
    """Autosize in a w x h box (m). Size given -> keep it (clamp if needed)."""
    a = A()
    kw = dict(dictionary=s["dictionary"], quiet_modules=float(s["quiet_modules"]))
    L = float(n.get("size") or 0.0) / 1000.0
    S = float(n.get("square") or 0.0) / 1000.0
    if L <= 0.0:                    # the same default as a clicked face (fit_on_loops)
        return a.face_default(kind, w_m, h_m, **kw)
    force = tuple(s["force"]) if s.get("force") and kind != "single" else None
    lay = a.layout_for_size(kind, w_m, h_m, L, S, force=force, **kw)
    # a grid where only one marker fits comes back as a SINGLE (ok, noted):
    # a typed board size must not quietly turn into one marker
    if (not lay.ok or lay.kind != kind) and force:
        lay = a.layout_for_size(kind, w_m, h_m, L, S, **kw)
    if not lay.ok or lay.kind != kind:
        lay = a.clamp_to_fit(kind, w_m, h_m, L, S, **kw)
    return lay


def _chain(segs, tol=1e-4):
    """Stroked curves of one loop (any order, any direction) -> one polygon."""
    if not segs:
        return []
    d = lambda a, b: sum((p - q) ** 2 for p, q in zip(a, b))
    out, rest = list(segs[0]), [list(s_) for s_ in segs[1:]]
    while rest:
        end = out[-1]
        i = min(range(len(rest)), key=lambda k: min(d(end, rest[k][0]), d(end, rest[k][-1])))
        seg = rest.pop(i)
        if d(end, seg[-1]) < d(end, seg[0]):
            seg.reverse()
        out += seg[1:]
    if len(out) > 1 and d(out[0], out[-1]) < tol * tol:
        out.pop()
    return out


def profile_loops_2d(profile, o, x, y, tol_cm=0.005):
    """Every loop of a sketch profile as a 2D polygon (cm) in (o, x, y),
    the outer loop first -- what fit_on_loops wants, like a face's loops."""
    sk = profile.parentSketch
    loops = []
    for loop in profile.profileLoops:
        segs = []
        for pc in loop.profileCurves:
            try:
                ev = pc.geometry.evaluator
                ok, p0, p1 = ev.getParameterExtents()
                ok, strokes = ev.getStrokes(p0, p1, tol_cm)
                if ok and strokes:
                    segs.append([_v(sk.sketchToModelSpace(q)) for q in strokes])
            except Exception:                      # noqa: BLE001
                pass
        pts = _chain(segs)
        uv = [(_dot([a - b for a, b in zip(p, o)], x), _dot([a - b for a, b in zip(p, o)], y))
              for p in pts]
        if len(uv) >= 3:
            loops.append((0 if loop.isOuter else 1, uv))
    loops.sort(key=lambda t: t[0])
    return [uv for _k, uv in loops]


def _inside_loops(loops, u, v):
    """Even-odd: is (u, v) inside the region these closed polygons bound?"""
    inside = False
    for lp in loops:
        n = len(lp)
        for i in range(n):
            (x0, y0), (x1, y1) = lp[i - 1], lp[i]
            if (y0 > v) != (y1 > v) and u < x0 + (v - y0) * (x1 - x0) / (y1 - y0):
                inside = not inside
    return inside


def profile_at(point, tol_cm=1e-3, des=None):
    """The closed sketch area a click on a planar face meant, like Extrude:
    the smallest profile of a VISIBLE sketch whose plane passes through the
    picked point (root space) and that contains it -- as the entity the
    selection wants (a proxy for a sketch in an occurrence) -- or None.

    Zach (2026-10-08): "it should be like extrude that can work via clicking
    on closed areas in sketches or faces". His rectangle was drawn ON the
    disc's face, so a click inside it has both the profile and the face under
    the cursor; which one an API selection input gets first is Fusion's
    business, so the dialog asks this when it got the face."""
    des = des or aruco_tags.design()
    root = des.rootComponent
    places = [(root, None)]
    try:
        places += [(occ.component, occ) for occ in root.allOccurrences if occ.isVisible]
    except Exception:                              # noqa: BLE001
        pass
    best, best_area = None, None
    for comp, occ in places:
        if occ is not None:
            m = occ.transform2.copy()
            m.invert()
            p = point.copy()
            p.transformBy(m)
        else:
            p = point
        for sk in comp.sketches:
            try:
                if not sk.isVisible or sk.profiles.count == 0:
                    continue
                q = sk.modelToSketchSpace(p)
            except Exception:                      # noqa: BLE001
                continue
            if abs(q.z) > tol_cm:
                continue
            for i in range(sk.profiles.count):
                pr = sk.profiles.item(i)
                bb = pr.boundingBox
                if not (bb.minPoint.x - tol_cm <= q.x <= bb.maxPoint.x + tol_cm
                        and bb.minPoint.y - tol_cm <= q.y <= bb.maxPoint.y + tol_cm):
                    continue
                loops = []
                for loop in pr.profileLoops:
                    segs = []
                    for pc in loop.profileCurves:
                        try:
                            ev = pc.geometry.evaluator
                            ok, a, b = ev.getParameterExtents()
                            ok, st = ev.getStrokes(a, b, 0.002)
                            if ok and st:
                                segs.append([[s_.x, s_.y, 0.0] for s_ in st])
                        except Exception:          # noqa: BLE001
                            pass
                    pts = _chain(segs)
                    if len(pts) >= 3:
                        loops.append([(c[0], c[1]) for c in pts])
                if not loops or not _inside_loops(loops, q.x, q.y):
                    continue
                area = (bb.maxPoint.x - bb.minPoint.x) * (bb.maxPoint.y - bb.minPoint.y)
                if best is None or area < best_area:
                    best = pr.createForAssemblyContext(occ) if occ is not None else pr
                    best_area = area
    return best


def _profile_loops_m(profile, o, x, y):
    bb = profile.boundingBox
    key = ("p", profile.parentSketch.entityToken, profile.profileLoops.count,
           tuple(round(v, 6) for v in _v(bb.minPoint) + _v(bb.maxPoint)),
           tuple(round(c, 7) for c in o + x))
    if key not in _LOOP_CACHE:
        if len(_LOOP_CACHE) > 64:
            _LOOP_CACHE.clear()
        loops = profile_loops_2d(profile, o, x, y)
        _LOOP_CACHE[key] = [[(u / 100.0, v / 100.0) for (u, v) in lp] for lp in loops]
    return _LOOP_CACHE[key]


def _is_box(loops_m, w_cm, h_cm):
    """One loop that fills its w x h bounding box: a plain rectangle."""
    if len(loops_m) != 1:
        return False
    lp = loops_m[0]
    area = 0.5 * abs(sum(lp[i][0] * lp[i - 1][1] - lp[i - 1][0] * lp[i][1]
                         for i in range(len(lp))))
    box = (w_cm / 100.0) * (h_cm / 100.0)
    return box > 0 and abs(area - box) <= 1e-3 * box


_LOOP_CACHE = {}
_FIT_CACHE = {}


def _face_loops_m(face, o, x, y):
    key = (face.entityToken, round(face.area, 9), tuple(round(c, 7) for c in o + x))
    if key not in _LOOP_CACHE:
        if len(_LOOP_CACHE) > 64:
            _LOOP_CACHE.clear()
        loops = aruco_tags.face_loops_2d(face, o, x, y)
        _LOOP_CACHE[key] = [[(u / 100.0, v / 100.0) for (u, v) in lp] for lp in loops]
    return _LOOP_CACHE[key]


def evaluate(target, s, n):
    """Everything the geometry needs, from the target as it is NOW (cm frames).

    -> {"spec": TagSpec (depth/raise/recess set), "tag": (o, x, y, z) on the
        pattern's surface (the white top; else the face), "tile": (o, x, y,
        z, w, h) on the face plane or None, "base": (o, x, y, z, w, h) the
        target rectangle, "layout": Layout, "notes": [str] (autosize: the
        node warns about these), "plan": aruco_slabs.Plan (where each colour
        is, what the node is made of, the dialog's one-line notes),
        "pins": [outline mm in "pin_frame": the tile's centre, the pattern's
        (turned) axes], "turn": the Origin used (aruco_origin.effective),
        "lift_cm": the surface over the face, "mark": the corner mark
        (aruco_chamfer.Mark, mm in the pattern frame on the face plane) or
        None, "x_fit": the pattern's x before the Origin}
    "spec" is the board AS BUILT: a rectangular board at 90 / 270 has its
    cols / rows swapped ("layout" keeps the fit as laid out).
    """
    markers = M()
    target = native_of(target)
    kind = s["layout"]
    notes = []
    rot = float(s.get("rotate_deg", 0))
    L = float(n.get("size") or 0.0) / 1000.0
    S = float(n.get("square") or 0.0) / 1000.0
    angles = (0.0,) if (kind == "single" or rot) else (0.0, 90.0)
    tile_frame, pl = None, None

    def fit(loops):
        # cached: the dialog re-evaluates on every drag of a height arrow,
        # and fitting a 4-loop profile takes ~35 ms (measured)
        key = (id(loops), len(loops), tuple(len(lp) for lp in loops),
               tuple(loops[0][0]) if loops and loops[0] else None, kind, L, S,
               s["dictionary"], float(s["quiet_modules"]), angles)
        if key not in _FIT_CACHE:
            if len(_FIT_CACHE) > 64:
                _FIT_CACHE.clear()
            _FIT_CACHE[key] = A().fit_on_loops(
                kind, loops, marker_m=(L if L > 0 else None), square_m=S,
                dictionary=s["dictionary"], quiet_modules=float(s["quiet_modules"]),
                angles_deg=angles)
        return _FIT_CACHE[key]

    if isinstance(target, adsk.fusion.Profile):
        # like Extrude: the closed area, holes included -- the pattern going
        # INTO or ONTO a body keeps off the part's screw holes the profile
        # shows. White + black: the white tile is the profile's box (holes
        # filled), so the whole box is the marker's.
        o, x, y, z, w, h = profile_rect(target, s)
        tile_frame = (o, x, y, z, w, h)
        loops = (None if s.get("mode") == "both"
                 else _profile_loops_m(target, o, x, y))
        if loops is None or _is_box(loops, w, h):
            lay = _layout_rect(kind, w / 100.0, h / 100.0, s, n)     # honours the Layout pick
        else:
            lay, pl = fit(loops)
    else:
        o, x, y, z, w, h = aruco_tags.face_frame(target, rot)
        x, y, w, h = keep_x(x, y, z, w, h, s.get("x_base"))
        lay, pl = fit(_face_loops_m(target, o, x, y))
    base = (o, x, y, z, w, h)
    if pl:
        o = [o[i] + x[i] * pl["cx"] * 100.0 + y[i] * pl["cy"] * 100.0 for i in range(3)]
        x = _rotz(x, z, float(pl.get("angle_deg", 0.0)))
        y = _cross(z, x)
    xr = s.get("x_ref")
    if xr and not isinstance(target, adsk.fusion.Profile) and _dot(x, xr) < -1e-6:
        x = [-c for c in x]                         # the same, after the fit (and for
        y = _cross(z, x)                            # nodes that only have x_ref)
    x_fit = list(x)
    notes += list(getattr(lay, "notes", []) or [])
    if not getattr(lay, "ok", False):
        raise ValueError("no marker fits here: " + ("; ".join(notes) or "the target is too small"))
    spec = markers.TagSpec(dictionary=s["dictionary"], marker_id=int(round(n["marker_id"])),
                           quiet_modules=float(s["quiet_modules"]), layout=kind,
                           grow_m=markers.CELL_OVERLAP_M if s.get("grow") else 0.0,
                           legacy=bool(s.get("legacy")))
    spec = lay.apply(spec)
    pl_ = plan_of(s, n)
    L_ = pl_.layers
    spec.depth_m, spec.raise_m, spec.recess_m = (L_.depth / 1000.0, L_.raise_ / 1000.0,
                                                 L_.recess / 1000.0)
    if tile_frame is None:
        tile_frame = (o, x, y, z, spec.tile_size_m[0] * 100.0, spec.tile_size_m[1] * 100.0)
    # Origin (aruco_origin): the pattern turned on its own footprint, so
    # OpenCV's frame lands where he wants it; the fit, the tile and every
    # slab stay as they are. A rectangular board at 90 / 270 is OpenCV's
    # board with its cols / rows swapped, laid on its side: from here on
    # the spec IS that board (ids, cells, detector frame, mark, record)
    turn, turn_note = OR().effective(s.get("turn"), spec.layout, spec.cols, spec.rows,
                                     s.get("origin_v"))
    if turn_note:
        notes.append(turn_note)
    if OR().transposes(spec.layout, turn):
        spec = dataclasses.replace(spec, cols=spec.rows, rows=spec.cols)
    if turn:
        x = _rotz(x, z, float(turn))
        y = _cross(z, x)
    lift = L_.surface / 10.0                                            # mm -> cm
    tag_o = [o[i] + z[i] * lift for i in range(3)]
    tile = tile_frame if s["mode"] == "both" else None
    # the tile's sides in the turned frame (the pattern's corner 0 is its
    # top-left there)
    tile_mm = None
    if tile is not None:
        tw, th = (tile[4], tile[5]) if turn % 180 == 0 else (tile[5], tile[4])
        tile_mm = (tw * 10.0, th * 10.0)
    mark = None
    want = CH().choice_of(normalise(s, partial=True))     # the old "chamfer" too
    if want != "none":
        # the corner mark: at OpenCV's corner 0 / the board's origin, i.e.
        # the top-left of the pattern as the Origin lays it (aruco_chamfer);
        # a form this tag cannot have gives the other one (an info note)
        mk = CH().mark_for_spec(spec, s["mode"], pl_.op, tile_mm, want=want)
        if mk.instead:
            notes.append(mk.instead)
        if mk.kind == "none":
            if mk.note and mk.note != mk.instead:
                notes.append(mk.note)
        else:
            mark = mk
    pins, pin_frame = [], None
    if pl_.pin is not None and tile is not None:
        # at the pattern's corner 0, inside the tile (clear of a cut corner)
        pin_frame = (tile[0], x, y, z)
        pins = SL().pin_outlines(s.get("pin"), float(n.get("pin_size", SL().PIN_SIZE_MM)),
                                 tile_mm[0], tile_mm[1],
                                 cut=mark.leg if mark is not None and mark.kind == "tile_cut"
                                 else 0.0)
    ev = {"spec": spec, "tag": (tag_o, x, y, z), "tile": tile, "base": base,
          "layout": lay, "notes": notes, "lift_cm": lift, "plan": pl_, "pins": pins,
          "pin_frame": pin_frame, "turn": turn, "x_fit": x_fit,
          "pin_tol": float(n.get("pin_tol", SL().PIN_TOL_MM)), "mode": s["mode"],
          "mark": mark}
    if s.get("host_xform"):
        # the frame came from the target's space; the features live in the
        # host component (creation_settings)
        m = _matrix(s["host_xform"])
        for k in ("tag", "base", "tile", "pin_frame"):
            if ev[k] is not None:
                ev[k] = _xf_frame(m, ev[k])
    return ev


def turns(spec=None):
    """The Origin turns (deg) a tag can have: all four, for every layout (a
    rectangular board at 90 / 270 swaps its cols / rows: aruco_origin)."""
    return (0, 90, 180, 270)


def keep_x(x, y, z, w, h, x_base, square_tol=1e-6):
    """A face tag's x as it was made (x_base): face_frame takes x along the
    face's longest edge, start -> end, and an upstream edit can reverse
    that edge (measured 2026-10-08: the tag turned 180°) -- or, on a square
    face whose edges are all as long, pick a perpendicular one (its edge
    order and float noise decide). The creation's direction wins: a flip,
    or on a square face any quarter turn. -> (x, y, w, h)."""
    if not x_base:
        return x, y, w, h
    if abs(w - h) <= square_tol * max(w, h, 1e-9):
        cands = [(x, y, w, h), (y, [-c for c in x], h, w),
                 ([-c for c in x], [-c for c in y], w, h), ([-c for c in y], x, h, w)]
        return max(cands, key=lambda c: _dot(c[0], x_base))
    if _dot(x, x_base) < -1e-6:
        x = [-c for c in x]
        return x, _cross(z, x), w, h
    return x, y, w, h


# -------------------------------------------------------------- geometry

def _tbm():
    return adsk.fusion.TemporaryBRepManager.get()


def _box(o, x, y, z, w, h, lo_cm, hi_cm):
    """A w x h box centred on o (in x, y), from lo to hi along z (cm)."""
    c = [o[i] + z[i] * (lo_cm + hi_cm) / 2.0 for i in range(3)]
    return _tbm().createBox(adsk.core.OrientedBoundingBox3D.create(
        adsk.core.Point3D.create(*c), adsk.core.Vector3D.create(*x),
        adsk.core.Vector3D.create(*y), w, h, hi_cm - lo_cm))


def _union(parts):
    """One temporary body from several (None when there are none)."""
    body = None
    for p in parts:
        if p is None:
            continue
        if body is None:
            body = p
        else:
            _tbm().booleanOperation(body, p, adsk.fusion.BooleanTypes.UnionBooleanType)
    return body


def _cm(mm):
    return float(mm) / 10.0


def face_origin(ev):
    """The pattern's centre on the face / sketch plane (the slabs' 0)."""
    o, _x, _y, z = ev["tag"]
    return [o[i] - z[i] * ev["lift_cm"] for i in range(3)]


def tile_temp_body(tile, lo_cm, hi_cm):
    o, x, y, z, w, h = tile
    return _box(o, x, y, z, w, h, lo_cm, hi_cm)


def mark_frame(ev):
    """The frame the corner mark's polygons are in: the pattern's centre on
    the face plane, its (turned) axes."""
    _o, x, y, z = ev["tag"]
    return (face_origin(ev), x, y, z)


def _tile(ev, lo_cm, hi_cm):
    """The white tile from lo to hi over the face, its top-left corner cut
    when the mark is a tile cut (also the tool that recesses the body for a
    sunk tile: the part then fills the cut corner)."""
    return aruco_tags.mark_tile(tile_temp_body(ev["tile"], lo_cm, hi_cm), mark_frame(ev),
                                ev.get("mark"), lo_cm, hi_cm)


def white_temp_body(spec, o, x, y, z, lo_cm, hi_cm, mark=None):
    """Mode white: the quiet zone + white modules as ONE solid from lo to hi
    over the face (the tile box minus slightly-taller black cells, so no
    coplanar faces meet). The corner mark: a ChArUco board's top-left black
    square cut (the white keeps that corner), or a triangular notch at the
    top-left that the black body under shows through."""
    tw, th = (v * 100.0 for v in spec.tile_size_m)
    box = _box(o, x, y, z, tw, th, lo_cm, hi_cm)
    cells = aruco_tags.cells_temp_body(spec, o, x, y, z, (lo_cm - 0.01, hi_cm + 0.01))
    if mark is not None and mark.kind == "black_cut":
        cells = aruco_tags.mark_cells(cells, (o, x, y, z), mark, lo_cm - 0.01, hi_cm + 0.01)
    _tbm().booleanOperation(box, cells, adsk.fusion.BooleanTypes.DifferenceBooleanType)
    return aruco_tags.mark_white(box, (o, x, y, z), mark, lo_cm, hi_cm)


def _footprint(ev, lo_mm, hi_mm):
    """The pattern's own footprint (the black cells; White only: the white
    pattern) from lo to hi mm over the face, with the corner mark (a cut
    ChArUco square, the corner triangle) -- so the pattern, the pocket for
    it and its recess in the body all carry it."""
    o, (_o, x, y, z) = face_origin(ev), ev["tag"]
    mark = ev.get("mark")
    if ev.get("mode") == "white":
        return white_temp_body(ev["spec"], o, x, y, z, _cm(lo_mm), _cm(hi_mm), mark)
    cells = aruco_tags.cells_temp_body(ev["spec"], o, x, y, z, (_cm(lo_mm), _cm(hi_mm)))
    return aruco_tags.mark_cells(cells, (o, x, y, z), mark, _cm(lo_mm), _cm(hi_mm))


def pattern_temp_body(ev, mode=None):
    """The pattern layer where its Offset / Height put it."""
    lo, hi = ev["plan"].layers.pattern
    return _footprint(ev, lo, hi)


def _prism(o, x, y, z, outline, lo_cm, hi_cm):
    """A straight prism over a pin outline (mm, in the frame o/x/y)."""
    tbm = _tbm()
    if outline["kind"] == "circle":
        (u, v), r = outline["c"], outline["r"]
        c = [o[i] + x[i] * _cm(u) + y[i] * _cm(v) for i in range(3)]
        p0 = adsk.core.Point3D.create(*[c[i] + z[i] * lo_cm for i in range(3)])
        p1 = adsk.core.Point3D.create(*[c[i] + z[i] * hi_cm for i in range(3)])
        return tbm.createCylinderOrCone(p0, _cm(r), p1, _cm(r))
    pts = [(_cm(u), _cm(v)) for (u, v) in outline["pts"]]
    us, vs = [p[0] for p in pts], [p[1] for p in pts]
    u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
    c = [o[i] + x[i] * (u0 + u1) / 2.0 + y[i] * (v0 + v1) / 2.0 for i in range(3)]
    body = _box(c, x, y, z, u1 - u0, v1 - v0, lo_cm, hi_cm)
    big = 4.0 * max(u1 - u0, v1 - v0)
    eps = 1e-9
    if SL()._area2(pts) < 0:
        pts = list(reversed(pts))
    for i in range(len(pts)):
        (a, b), (p, q) = pts[i], pts[(i + 1) % len(pts)]
        # an edge on the bounding box needs no cut (and must not get a
        # coplanar one)
        if (abs(a - p) < eps and (abs(a - u0) < eps or abs(a - u1) < eps)) or \
                (abs(b - q) < eps and (abs(b - v0) < eps or abs(b - v1) < eps)):
            continue
        du, dv = p - a, q - b
        ln = math.hypot(du, dv)
        tu, tv = du / ln, dv / ln
        nu, nv = tv, -tu                                  # outward (CCW polygon)
        mu, mv = (a + p) / 2.0 + nu * big / 2.0, (b + q) / 2.0 + nv * big / 2.0
        cc = [o[i] + x[i] * mu + y[i] * mv for i in range(3)]
        ax = [x[i] * tu + y[i] * tv for i in range(3)]
        ay = [x[i] * nu + y[i] * nv for i in range(3)]
        cut = _box(cc, ax, ay, z, big * 2.0, big, lo_cm - 0.1, hi_cm + 0.1)
        tbm.booleanOperation(body, cut, adsk.fusion.BooleanTypes.DifferenceBooleanType)
    return body


def pins_temp_body(ev, lo_cm, hi_cm, grow_mm=0.0):
    """The locating pins (grown by `grow_mm` for their holes), lo..hi cm over
    the face, at the pattern's corner 0 inside the tile (ev["pin_frame"])."""
    to, tx, ty, tz = ev["pin_frame"]
    outs = [SL().grow(p, grow_mm) if grow_mm else p for p in ev["pins"]]
    return _union(_prism(to, tx, ty, tz, p, lo_cm, hi_cm) for p in outs)


def pin_body(ev):
    lo, hi = ev["plan"].pin
    return pins_temp_body(ev, _cm(lo), _cm(hi) + PIN_JOIN_OVERLAP_CM)


def pocket_body(ev):
    """Cut tool: the black's footprint from its bottom up to the white top."""
    lo, hi = ev["plan"].pocket
    return _footprint(ev, lo, hi)


def recess_body(ev, n):
    """Cut tool into the body under: every slab below the face, and the pins'
    holes (grown by the tolerance all round)."""
    parts = []
    for what, (lo, hi) in ev["plan"].recess:
        if what == "tile":
            parts.append(_tile(ev, _cm(lo), _cm(hi)))
        elif what == "pattern":
            parts.append(_footprint(ev, lo, hi))
        elif what == "hole":
            parts.append(pins_temp_body(ev, _cm(lo), _cm(hi),
                                        grow_mm=float(n.get("pin_tol", SL().PIN_TOL_MM))))
    return _union(parts)


def part_body(role, ev, n):
    """The temporary body of one base-feature role."""
    if role == "tile":
        lo, hi = ev["plan"].layers.white
        return _tile(ev, _cm(lo), _cm(hi))
    if role == "pin":
        return pin_body(ev)
    if role == "pocket":
        return pocket_body(ev)
    if role == "pattern":
        return pattern_temp_body(ev)
    if role == "recess":
        return recess_body(ev, n)
    raise ValueError(f"unknown part {role!r}")


def _base_body(comp, temp, name):
    bf = comp.features.baseFeatures.add()
    bf.startEdit()
    try:
        comp.bRepBodies.add(temp, bf)
    finally:
        bf.finishEdit()
    body = bf.bodies.item(0)
    body.name = name
    bf.name = name
    return bf, body


def _update(bf, temp, appearance=None, faces=False, log=None):
    """Swap a base feature's one body; ALWAYS leave edit mode. updateBody
    drops the body's appearance (measured 2026-10-08: black cells came back
    "Steel - Satin" after every compute), so `appearance` is put back on the
    swapped source body inside the same edit: on the body and its faces, or
    on its faces only when `faces` (a tile that is joined: face-level colour
    is what survives the join -- and what a new tile's faces carry into
    every piece a pocket cuts it into)."""
    bf.startEdit()
    try:
        try:
            src = list(bf.sourceBodies)[0]
        except Exception:                          # noqa: BLE001
            src = bf.bodies.item(0)
        ok = bool(bf.updateBody(src, temp))
        if appearance is not None:
            try:
                try:
                    body = list(bf.sourceBodies)[0]
                except Exception:                  # noqa: BLE001
                    body = bf.bodies.item(0)
                # body AND faces: the faces keep it through a join, and
                # through Fusion replaying the base feature on a roll
                for ent in ([] if faces else [body]) + list(body.faces):
                    ent.appearance = appearance
            except Exception as exc:               # noqa: BLE001
                if log is not None:
                    log.append(f"paint: {type(exc).__name__}: {exc}"[:200])
        return ok
    finally:
        bf.finishEdit()


def _combine(comp, target, tool, op, keep):
    tools = adsk.core.ObjectCollection.create()
    tools.add(tool)
    ci = comp.features.combineFeatures.createInput(target, tools)
    ci.operation = op
    ci.isKeepToolBodies = keep
    return comp.features.combineFeatures.add(ci)


def _base_feats(cf):
    bfs = [adsk.fusion.BaseFeature.cast(f) for f in cf.features]
    return [b for b in bfs if b is not None]


def parts_of(cf, s=None):
    """{role: base feature} of one node, by the roles its spec lists in
    order ("parts"); a node made before 2026-10-08 has (tile,) pattern."""
    s = s if s is not None else settings_of(cf)
    bfs = _base_feats(cf)
    roles = list((s or {}).get("parts") or [])
    if not roles:
        roles = ["tile", "pattern"] if len(bfs) > 1 else ["pattern"]
        bfs = [bfs[0], bfs[-1]] if len(bfs) > 1 else bfs
    return {r: b for r, b in zip(roles, bfs)}


def base_features(cf):
    """(tile base feature or None, pattern base feature) of one node."""
    p = parts_of(cf)
    return p.get("tile"), p.get("pattern")


# ---------------------------------------------------------- the feature

def _param(cf, pid):
    """CustomFeatureParameters.itemById RAISES for an unknown id (2705)."""
    try:
        return cf.parameters.itemById(pid)
    except RuntimeError:
        return None


def is_legacy(s):
    """A node made before 2026-10-08: parameters height / thickness / above,
    no "parts" in its spec."""
    return bool(s) and not s.get("parts")


def follow_expression(cf, delta_mm=0.0):
    """The black offset's expression in White + black: the white top -- the
    higher end of the white extrude, whichever way its Height points
    (Fusion's max() takes ';', measured: ',' is "Invalid expression") --
    plus the kept delta, built from the parameters' REAL names (a rename can
    fail on a name clash), or None when the node has no white."""
    wo, wh = _param(cf, "white_offset"), _param(cf, "white_height")
    if wo is None or wh is None:
        return None
    d = round(float(delta_mm), 6)
    tail = "" if abs(d) < 1e-9 else (f" + {d:g} mm" if d > 0 else f" - {-d:g} mm")
    return f"max({wo.name}; {wo.name} + {wh.name}){tail}"


def white_top_of(n):
    return SL().white_top("both", n)


def link_black(cf, n):
    """White + black: write the black offset parameter as the white top +
    (black offset - white top): dragging or typing the white -- here or in
    Change Parameters -- carries the black with it, flush stays flush and a
    raise or a recess is kept (Zach, 2026-10-08). -> True if written."""
    p = _param(cf, "black_offset")
    want = follow_expression(cf, float(n["black_offset"]) - white_top_of(n))
    if p is None or want is None:
        return False
    if (_norm_expr(p.expression) == _norm_expr(want)
            and abs(p.value * 10.0 - float(n["black_offset"])) < 1e-6):
        return False
    p.expression = want
    return True


def _norm_expr(e):
    return "".join(str(e or "").split())


def black_follows(cf, s=None):
    """Is the node's black offset linked to the white top (link_black)?
    Change Parameters may have replaced the expression with a number; the
    next edit links it again, keeping where the black is."""
    p = _param(cf, "black_offset")
    base = follow_expression(cf)
    if p is None or base is None:
        return False
    return _norm_expr(p.expression).startswith(_norm_expr(base))


def settings_of(cf):
    """A node's settings (normalise()d over the defaults), or None -- for no
    node at all too (2026-10-09: a copy's variant part has a record with its
    template's key and no node; a lookup by key handed None in here)."""
    if cf is None:
        return None
    try:
        nv = cf.customNamedValues
        if not nv.isExistingValue("spec"):
            return None
        stored = json.loads(nv.value("spec"))
    except (AttributeError, RuntimeError, ValueError):
        return None
    s = default_settings()
    s.update(normalise(stored))
    return s


def numbers_of(cf):
    """The node's numbers (mm): its parameters; a node made before
    2026-10-08 (height / thickness / above) mapped to Offset / Height.
    No node (None): the defaults."""
    if cf is None:
        return default_numbers("both")
    s = settings_of(cf) or {}
    mode = s.get("mode") if s.get("mode") in MODES else "both"
    n = default_numbers(mode)
    old = {}
    for pid in PARAMS + LEGACY_PARAMS:
        p = _param(cf, pid)
        if p is None:
            continue
        v = p.value if pid == "marker_id" else p.value * 10.0             # cm -> mm
        (old if pid in LEGACY_PARAMS else n)[pid] = v
    if old:
        n.update(SL().from_legacy(mode, old.get("height", 2.0), old.get("thickness", 0.6),
                                  old.get("above", 0.0)))
    return n


def target_of(cf):
    """The face / profile the node was made on, as the dependency holds it
    (root context: a proxy for a non-root component; evaluate() and the
    callers take native_of() where they need the native one)."""
    dep = cf.dependencies.itemById("target")
    return dep.entity if dep is not None else None


def is_ours(cf):
    try:
        return cf is not None and cf.definition is not None and cf.definition.id == DEF_ID
    except Exception:                              # noqa: BLE001
        return False


def _on_compute(args):
    st = _st()
    t0 = time.perf_counter()
    entry = {"at": round(time.time(), 3)}
    ea = adsk.fusion.CustomFeatureEventArgs.cast(args)
    try:
        cf = ea.customFeature
        entry["feature"] = cf.name
        s = settings_of(cf)
        if s is None:
            entry["skipped"] = "spec not written yet"          # inside add()
            return
        n = numbers_of(cf)
        target = target_of(cf)
        if target is None:
            raise ValueError("the face or profile it was made on is gone")
        if not is_legacy(s):
            built, want = node_structure(s), structure(s, n)
            if built != want:
                # compute may only swap bodies: a part to add or remove (a
                # pocket, a recess, a hole) needs the node rebuilt
                entry["structure"] = {"built": list(built[2]), "wants": list(want[2])}
                raise ValueError(
                    "these numbers change what the tag is made of ("
                    + ", ".join(want[2]) + " instead of " + ", ".join(built[2])
                    + "): double-click the ArUco Extrude node and press OK to rebuild it")
        ev = evaluate(target, s, n)
        parts = parts_of(cf, s)
        des = adsk.fusion.Design.cast(cf.parentComponent.parentDesign)
        # lookup only: create() already copied both into the design
        white = des.appearances.itemByName(aruco_tags.WHITE)
        black = des.appearances.itemByName(aruco_tags.BLACK)
        paint_log = []
        joined = effective_op(s) == "join"
        for role, bf in parts.items():
            if role == "tile" and ev["tile"] is None:
                continue
            temp = part_body(role, ev, n)
            if role == "tile":
                # a new tile: body and faces (the faces carry white into
                # every piece a pocket that reaches its underside cuts it
                # into -- "tag tile <key> (1)", 2026-10-09; paint() gives
                # those pieces their body colour). Joined: its faces only.
                _update(bf, temp, white, faces=joined, log=paint_log)
            elif role == "pin":
                _update(bf, temp, white, faces=True, log=paint_log)
            elif role == "pattern":
                _update(bf, temp, white if s["mode"] == "white" else black, log=paint_log)
            else:
                _update(bf, temp)                         # cut tools: consumed
        if paint_log:
            entry["paint"] = paint_log
        entry["spec"] = ev["spec"].label()
        if ev["notes"]:
            entry["notes"] = list(ev["notes"])
        for note in ev["notes"]:
            if not is_info(note):
                ea.computeStatus.statusMessages.addWarning("API_COMPUTE_WARNING", note)
    except Exception as exc:                       # noqa: BLE001
        entry["error"] = traceback.format_exc()[-900:]
        try:
            ea.computeStatus.statusMessages.addError("API_COMPUTE_ERROR", str(exc)[:200])
        except Exception:                          # noqa: BLE001
            pass
    finally:
        entry["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        st.compute_log = (st.compute_log + [entry])[-200:]
        st.compute_n = getattr(st, "compute_n", 0) + 1


def _dependency(inp, des, ent):
    """The target goes in in ROOT context: a non-root component's face or
    profile as its root proxy (the native one is refused: "3 : Invalid
    argument value", measured). The proxy's own occurrence is the TARGET's
    component's -- not the host's: a root sketch's profile over Component1's
    disc is a root entity already."""
    try:
        return bool(inp.addDependency("target", ent))
    except RuntimeError:
        if getattr(ent, "assemblyContext", None) is not None:
            raise
        own = (ent.parentSketch.parentComponent if isinstance(ent, adsk.fusion.Profile)
               else ent.body.parentComponent)
        occs = des.rootComponent.allOccurrencesByComponent(own)
        if own == des.rootComponent or occs.count == 0:
            raise
        return bool(inp.addDependency("target", ent.createForAssemblyContext(occs.item(0))))


def _label(key, spec, mode):
    what = {"both": "", "black": " · black only", "white": " · white only"}[mode]
    return f"ArUco {key}: {spec.label()}{what}"


def _new_key(des, comp, kind):
    taken = {r.get("key") for r in aruco_tags.read_tag_records(comp)}
    base = {"grid": "board", "charuco": "charuco"}.get(kind, "top")
    if base not in taken:
        return base
    k = 2
    while f"{base}{k}" in taken:
        k += 1
    return f"{base}{k}"


def design_of(ent):
    """The design an entity lives in -- NOT the active one: the user can
    switch documents while a scripted step pumps events (2026-10-08, Fusion
    crashed with a test's node in Zach's document's selection)."""
    try:
        e = native_of(ent)
        if isinstance(e, adsk.fusion.Profile):
            comp = e.parentSketch.parentComponent
        elif isinstance(e, adsk.fusion.BRepFace):
            comp = e.body.parentComponent
        else:
            comp = e.parentComponent
        return adsk.fusion.Design.cast(comp.parentDesign)
    except Exception:                              # noqa: BLE001
        return aruco_tags.design()


def _active_doc():
    try:
        return _app().activeDocument
    except Exception:                              # noqa: BLE001
        return None


def is_active(des):
    """Is `des` the active document's design?"""
    try:
        return des is not None and _active_doc() == des.parentDocument
    except Exception:                              # noqa: BLE001
        return False


def clear_selections():
    """Empty the UI selection: a node about to be deleted must not stay
    selected (Fusion crashed in SelectionCommandInput::onEnd ->
    DcAPIFeature::faces on a deleted, still-selected ArUco Extrude)."""
    try:
        _ui().activeSelections.clear()
    except Exception:                              # noqa: BLE001
        pass


def create(target, s, n, *, live=True, write=True):
    """Build an ArUco Extrude on a planar face or a sketch profile. Call it
    inside a command's execute (one undo step). Returns the record -- or,
    with write=False (an edit's rebuild, timeline rolled back), what
    _write_record needs once the timeline is forward again."""
    des = design_of(target)
    picked = target                     # the selection / dependency (maybe a proxy)
    s, body_under = creation_settings(picked, normalise(s))
    s["origin_v"] = OR().ORIGIN_RULE    # every Origin, a rectangular board's 90 / 270 too
    target = native_of(picked)
    # read now: a pocket that reaches the face's edge (a ChArUco board filling
    # its clear width) splits the face, and the old face object is gone after
    face_token = None if isinstance(target, adsk.fusion.Profile) else target.entityToken
    if s.get("mode") == "auto":                    # the dialog's default
        s["mode"] = "black" if s.get("has_body") else "both"
    if s["mode"] not in MODES:
        raise ValueError(f"mode {s['mode']!r}")
    comp = host_of(target, body_under, s)
    # the combines take the NATIVE body of the host component (a root sketch
    # over Component1:1's disc joins / pockets Component1's Body1)
    body_under = native_of(body_under) if body_under is not None else None
    if not s.get("key"):
        s["key"] = _new_key(des, comp, s["layout"])
    key = s["key"]
    n = resolve_follow(s, n)
    s.pop("black_follows", None)        # a script's start; the node keeps a link (link_black)
    if not isinstance(target, adsk.fusion.Profile) and not s.get("x_base"):
        s["x_base"] = [round(c, 12) for c in
                       aruco_tags.face_frame(target, float(s.get("rotate_deg", 0)))[1]]
    ev = evaluate(target, s, n)
    if not isinstance(target, adsk.fusion.Profile) and not s.get("x_ref"):
        s["x_ref"] = [round(c, 12) for c in ev["x_fit"]]
    spec = ev["spec"]
    plan = ev["plan"]
    s["parts"] = list(plan.parts)
    s["v"] = SPEC_VERSION
    if plan.pin is None:
        s["pin"] = "none"
    # the size is a NUMBER from here on (auto only picks the first value)
    n = dict(n, size=spec.marker_length_m * 1000.0,
             square=spec.square_length_m * 1000.0 if s["layout"] == "charuco" else 0.0)
    tl = des.timeline
    start = tl.markerPosition
    feats = []
    white = aruco_tags.appearance(des, aruco_tags.WHITE)
    names = {"tile": TILE_BODY, "pin": PIN_BODY, "pocket": POCKET_BODY,
             "pattern": WHITE_BODY if s["mode"] == "white" else CELLS_BODY,
             "recess": RECESS_BODY}
    made = {}
    for role in plan.parts:
        bf, body = _base_body(comp, part_body(role, ev, n), f"{names[role]} {key}")
        made[role] = bf
        feats.append(bf)
        if white is not None and role in ("pin", "tile"):
            # face-level: carried through the join, and into every piece a
            # pocket that reaches the tile's underside cuts it into
            for f in body.faces:
                f.appearance = white
        feats += _combines(comp, role, made, plan, body_under)
    bf_tile, bf_pat = made.get("tile"), made["pattern"]
    pat = bf_pat.bodies.item(0)
    tile_name = (bf_tile.bodies.item(0).name if bf_tile is not None and plan.op == "new"
                 else (body_under.name if body_under is not None else None))
    cf, group_token, live_error, group_error, how_made = None, None, None, None, None
    if live and is_registered():
        try:
            cf = _wrap(des, comp, picked, feats, s, n, spec)
        except Exception as exc:                   # noqa: BLE001
            # anything at all (2026-10-08: a half-reloaded module raised a
            # KeyError here and Zach got loose features): no node, a group
            live_error = f"{type(exc).__name__}: {exc}"[:300]
            cf = None
        if (cf is None and "face" in (live_error or "") and face_token
                and s["mode"] in ("black", "white")
                and any(adsk.fusion.CombineFeature.cast(f) is not None for f in feats)):
            # Black / White only (measured). NOT with a tile: stretched over a
            # joined tile the node kept Fusion computing until it had to be
            # killed (2026-10-08), so a white + black board across the whole
            # face stays a timeline group.
            # a board across the whole face: its pocket SPLITS the face, and a
            # split face is refused as the dependency ("2 : InternalValidation
            # Error : face", measured 2026-10-08). Wrap the base features
            # while the face is whole, then the combines, then stretch the node
            # over them.
            try:
                cf, feats = _wrap_first(des, comp, picked, face_token, feats, made, plan,
                                        body_under, s, n, spec)
                live_error, how_made = None, "base features wrapped first (the face is split)"
            except Exception as exc:               # noqa: BLE001
                live_error += f" | wrapped first: {type(exc).__name__}: {exc}"[:300]
                cf = None
    try:
        last = cf.timelineObject if cf is not None else feats[-1].timelineObject
        if tl.markerPosition <= last.index:
            last.rollTo(False)                     # never leave the new tag rolled back
    except Exception:                              # noqa: BLE001
        pass
    if cf is None:
        group_token, group_error = _group(des, feats, start, _label(key, spec, s["mode"]))
    cut = next((f for f in reversed(feats) if adsk.fusion.CombineFeature.cast(f) is not None
                and adsk.fusion.CombineFeature.cast(f).operation
                == adsk.fusion.FeatureOperations.CutFeatureOperation), None)
    # colours LAST: customFeatures.add() resets its base-feature bodies to the
    # component's default appearance (measured 2026-10-08: set black, wrap ->
    # "Steel - Satin"), which is why Zach's tags came out grey
    paint(des, s, bf_tile, bf_pat, body_under if effective_op(s) == "join" else None, ev)
    fz = {"custom_feature": cf.entityToken if cf else None,
          "base_feature": bf_pat.entityToken,
          "combine": cut.entityToken if cut else None,
          "face": face_token,
          "tile_body": tile_name, "cells_body": pat.name, "mode": s["mode"],
          "group": group_token, "rotate_deg": s.get("rotate_deg", 0)}
    if how_made:
        fz["made"] = how_made
    if live_error:
        fz["live_error"] = live_error
    if group_error:
        fz["group_error"] = group_error
    if not write:
        return {"comp": comp, "key": key, "ev": ev, "fz": fz}
    return _write_record(des, comp, key, ev, fz)


def _combines(comp, role, made, plan, body_under):
    """The combines that follow one base feature (create's order): the tile
    joined to the body (Join), the pins joined to the tile, the pocket cut
    from the tile / the joined body, the recess cut from the body under."""
    JOIN = adsk.fusion.FeatureOperations.JoinFeatureOperation
    CUT = adsk.fusion.FeatureOperations.CutFeatureOperation
    body = made[role].bodies.item(0)

    def tile():                          # a NEW tile (a joined one is the body's now)
        return made["tile"].bodies.item(0)
    if role == "tile" and plan.op == "join":
        return [_combine(comp, body_under, body, JOIN, False)]
    if role == "pin":
        return [_combine(comp, tile(), body, JOIN, False)]
    if role == "pocket":
        return [_combine(comp, body_under if plan.op == "join" else tile(), body, CUT, False)]
    if role == "recess":
        return [_combine(comp, body_under, body, CUT, False)]
    return []


def _wrap_first(des, comp, picked, face_token, feats, made, plan, body_under, s, n, spec):
    """The live node for a tag whose pocket splits its face: the combines
    undone, the base features wrapped while the face is whole, the combines
    made again after the node, the node stretched over them (with the
    marker rolled to it -- "3 : Didn't roll editing feature back"
    otherwise, measured). -> (node, its features in timeline order)."""
    clear_selections()                   # nothing selected is deleted
    for f in reversed(feats):
        if adsk.fusion.CombineFeature.cast(f) is not None:
            f.deleteMe()
    bfs = [made[r] for r in plan.parts]
    face = picked
    try:
        ok = face.isValid
    except Exception:                              # noqa: BLE001
        ok = False
    if not ok:
        face = aruco_tags._find_by_token(des, face_token)
        if face is None:
            raise ValueError("the face is gone")
    cf = _wrap(des, comp, face, bfs, s, n, spec)
    combos = []
    for role in plan.parts:
        combos += _combines(comp, role, made, plan, body_under)
    if combos:
        cf.timelineObject.rollTo(True)
        try:
            cf.setStartAndEndFeatures(bfs[0], combos[-1])
        finally:
            cf.timelineObject.rollTo(False)
    return cf, bfs + combos


def _tile_faces(body, tile, lo_cm, hi_cm, eps=1e-4):
    """The faces of `body` that came from a joined tile: inside the tile's
    footprint and its slab (lo..hi over the face plane), not lying in the
    face plane."""
    o, x, y, z, w, h = tile
    floor = max(lo_cm, 0.0)
    out = []
    for f in body.faces:
        pts = [_v(v.geometry) for v in f.vertices] + [_v(f.pointOnFace)]
        ok, top = True, False
        for p in pts:
            d = [a - b for a, b in zip(p, o)]
            u, v, t = _dot(d, x), _dot(d, y), _dot(d, z)
            if (abs(u) > w / 2.0 + eps or abs(v) > h / 2.0 + eps
                    or t < lo_cm - eps or t > hi_cm + eps):
                ok = False
                break
            top = top or t > floor + eps
        if ok and top:
            out.append(f)
    return out


def is_piece_of(name, base):
    """Is a body named `name` the body `base`, or a piece Fusion split off
    it ("<base> (1)", "<base> (2)", ...)?"""
    if name == base:
        return True
    if not name.startswith(base + " (") or not name.endswith(")"):
        return False
    return name[len(base) + 2:-1].isdigit()


def tile_pieces(bf_tile, key=None):
    """Every body a NEW tile is: the base feature's body, and the pieces a
    pocket that reaches the tile's underside cuts it into (2026-10-09: a
    Black only node rebuilt as White + black kept its black at the face,
    under the tile; the pocket -- from the black's bottom to the white top --
    went through the tile, and Fusion named the second piece "tag tile ck
    (1)": it kept the component's default appearance)."""
    out = []
    try:
        if bf_tile is None or not bf_tile.bodies.count:
            return out
        first = bf_tile.bodies.item(0)
        out.append(first)
        base = f"{TILE_BODY} {key}" if key else first.name
        comp = first.parentComponent
        for b in comp.bRepBodies:
            if b != first and is_piece_of(b.name, base):
                out.append(b)
    except Exception:                              # noqa: BLE001
        pass
    return out


def paint(des, s, bf_tile, bf_pat, joined=None, ev=None):
    """Zach (2026-10-08): "the black should always be black ... and the white
    should always be white". The pattern body black (white for White only),
    a NEW tile white -- every piece of it (tile_pieces); a tile JOINED into
    his body: only the faces that came from the tile go white (face-level --
    the rest of his body keeps its own appearance). Idempotent. -> what was
    painted."""
    white = aruco_tags.appearance(des, aruco_tags.WHITE)
    black = aruco_tags.appearance(des, aruco_tags.BLACK)
    done = {}

    def put(ent, app, label):
        if app is None or ent is None:
            return
        try:
            if ent.appearance is None or ent.appearance.name != app.name:
                ent.appearance = app
                done[label] = done.get(label, 0) + 1
        except Exception as exc:                   # noqa: BLE001
            done[label + " error"] = str(exc)[:120]

    if bf_pat is not None and bf_pat.bodies.count:
        put(bf_pat.bodies.item(0), white if s.get("mode") == "white" else black, "pattern")
    if s.get("mode") == "both":
        if effective_op(s) == "new" and bf_tile is not None and bf_tile.bodies.count:
            for i, b in enumerate(tile_pieces(bf_tile, s.get("key"))):
                put(b, white, "tile" if i == 0 else "tile piece")
        elif joined is not None and ev is not None and ev.get("tile") is not None:
            lo, hi = ev["plan"].layers.white
            for f in _tile_faces(joined, ev["tile"], _cm(lo), _cm(hi)):
                put(f, white, "tile faces")
    return done


def paint_all(des):
    """paint_node() for every ArUco Extrude in the design: after an edit
    (rolling the timeline replays later base features, and a replayed body
    can come back in the component's default appearance -- measured) and
    before an export. -> {node name: what was painted}."""
    out = {}
    for comp in des.allComponents:
        try:
            feats = comp.features.customFeatures
        except Exception:                          # noqa: BLE001
            continue
        for i in range(feats.count):
            cf = feats.item(i)
            if not is_ours(cf):
                continue
            try:
                if cf.timelineObject.isRolledBack or cf.timelineObject.isSuppressed:
                    continue
            except Exception:                      # noqa: BLE001
                pass
            try:
                done = paint_node(cf)
            except Exception as exc:               # noqa: BLE001
                done = {"error": f"{type(exc).__name__}: {exc}"[:160]}
            if done:
                out[cf.name] = done
    return out


def joined_body(cf):
    """The user's body a node's tile is joined into (its join combine's
    target), or None."""
    for f in cf.features:
        c = adsk.fusion.CombineFeature.cast(f)
        if c is not None and c.operation == adsk.fusion.FeatureOperations.JoinFeatureOperation:
            try:
                return c.targetBody
            except Exception:                      # noqa: BLE001
                return None
    return None


def paint_node(cf, ev=None):
    """paint() for an existing node (after an edit, an upstream change or an
    undo: Fusion does not keep the colours of a base-feature body it swaps)."""
    s = settings_of(cf)
    if s is None:
        return {}
    bf_tile, bf_pat = base_features(cf)
    # a joined tile's faces only with a frame from the node's OWN place in
    # the timeline (at the end the face is already pocketed); compute keeps
    # them white through the join itself (face-level paint on the tile)
    joined = joined_body(cf) if (effective_op(s) == "join" and ev is not None) else None
    des = adsk.fusion.Design.cast(cf.parentComponent.parentDesign)
    return paint(des, s, bf_tile, bf_pat, joined, ev)


def params_for(s):
    """The custom parameters a node of these settings carries (besides
    marker_id): the size, the checker (ChArUco), each colour's Offset and
    Height, the pins' numbers when it has pins."""
    out = ["size"]
    if s["layout"] == "charuco":
        out.append("square")
    if s["mode"] in ("both", "white"):
        out += ["white_offset", "white_height"]
    if s["mode"] in ("both", "black"):
        out += ["black_offset", "black_height"]
    if "pin" in (s.get("parts") or ()):
        out += list(PIN_PARAMS)
    return out


def _mm_expr(v):
    return f"{round(float(v), 6):g} mm"


def _group(des, feats, start, label):
    """The fallback when there is no live node: the tag's features as ONE
    timeline group. The range comes from the features' own timeline
    positions (then from where the marker was), so a half-made node or a
    moved marker cannot leave them loose. -> (token or None, error or None)."""
    tl = des.timeline
    errors = []
    ranges = []
    try:
        idx = [f.timelineObject.index for f in feats if f is not None and f.isValid]
        if idx:
            ranges.append((min(idx), max(idx)))
    except Exception as exc:                       # noqa: BLE001
        errors.append(f"positions: {exc}"[:120])
    try:
        ranges.append((start, tl.markerPosition - 1))
    except Exception as exc:                       # noqa: BLE001
        errors.append(f"marker: {exc}"[:120])
    for lo, hi in ranges:
        if hi <= lo:
            continue
        try:
            group = tl.timelineGroups.add(lo, hi)
            try:
                group.name = label
            except Exception:                      # noqa: BLE001
                pass
            return getattr(group, "entityToken", None) or f"group {lo}..{hi}", None
        except Exception as exc:                   # noqa: BLE001
            errors.append(f"{lo}..{hi}: {exc}"[:160])
    return None, "; ".join(errors) or "nothing to group"


def _release(cf):
    """Undo a custom feature whose set-up failed after add(): its features
    go back to the timeline as they were, the empty node goes."""
    clear_selections()
    try:
        cf.setStartAndEndFeatures(None, None)
    except Exception:                              # noqa: BLE001
        pass
    try:
        if cf.isValid:
            cf.deleteMe()
    except Exception:                              # noqa: BLE001
        pass


def _wrap(des, comp, target, feats, s, n, spec):
    """The live node around `feats`. Raises (and leaves the features as
    they were) when it cannot be made; the parameters' names, the node's
    name and the follow expression are extras that may fail on their own."""
    st = _st()
    VI = adsk.core.ValueInput
    inp = comp.features.customFeatures.createInput(st.definition)
    inp.addCustomParameter("marker_id", PARAM_LABELS["marker_id"],
                           VI.createByReal(float(n.get("marker_id", 0))), "", True)
    for pid in params_for(s):
        inp.addCustomParameter(pid, PARAM_LABELS[pid], VI.createByString(_mm_expr(n[pid])),
                               "mm", True)
    _dependency(inp, des, target)
    inp.setStartAndEndFeatures(feats[0], feats[-1])
    cf = comp.features.customFeatures.add(inp)
    try:
        cf.customNamedValues.addOrSetValue("spec", json.dumps(s, sort_keys=True))
    except Exception:
        _release(cf)                               # compute would skip it forever
        raise
    for step in ("name", "params", "follow"):
        try:
            if step == "name":
                cf.name = _label(s["key"], spec, s["mode"])
            elif step == "params":
                for pid in PARAMS:
                    p = _param(cf, pid)
                    if p is not None:
                        try:
                            p.name = f"aruco_{s['key']}_{pid}"
                        except Exception:          # noqa: BLE001
                            pass
            elif s["mode"] == "both":
                link_black(cf, n)
        except Exception:                          # noqa: BLE001
            pass
    return cf


def mark_record(ev):
    """The corner mark as the tag record keeps it (fusion.mark: mm in the
    record's frame, which is the pattern frame), or None. A copy's variant
    part is built with it (aruco_tags._build_variant)."""
    mark = ev.get("mark")
    return mark.to_dict(6) if mark is not None else None


def _write_record(des, comp, key, ev, fz):
    markers = M()
    o, x, y, z = ev["tag"]
    tag = markers.TagDef(key=key, component=aruco_tags._component_label(des, comp),
                         spec=ev["spec"], origin=list(o), x_axis=list(x), y_axis=list(y),
                         z_axis=list(z), name=f"ArUco {key}")
    rec = tag.to_dict()
    mark = mark_record(ev)
    if mark is not None:
        fz["mark"] = mark
    else:
        fz.pop("mark", None)
    rec["fusion"] = fz
    old = next((r for r in aruco_tags.read_tag_records(comp) if r.get("key") == key), None)
    for k in ("joint_origin", "joint_origin_name"):
        if old and (old.get("fusion") or {}).get(k) and k not in fz:
            fz[k] = old["fusion"][k]
    if old is not None:
        clear_selections()               # its old Joint Origin is deleted in there
    try:
        aruco_tags.ensure_joint_origin(comp, rec)
    except Exception as exc:                       # noqa: BLE001
        fz["joint_origin_error"] = f"not created: {exc}"
    records = [r for r in aruco_tags.live_tag_records(des, comp) if r.get("key") != key]
    aruco_tags.write_tag_records(comp, records + [rec])
    return rec


def find_feature(des, rec):
    tok = (rec.get("fusion") or {}).get("custom_feature")
    cf = aruco_tags._find_by_token(des, tok) if tok else None
    return adsk.fusion.CustomFeature.cast(cf) if cf is not None else None


def component_of(cf):
    return cf.parentComponent


def record_of(cf):
    comp = component_of(cf)
    s = settings_of(cf) or {}
    return comp, next((r for r in aruco_tags.read_tag_records(comp)
                       if r.get("key") == s.get("key")), None)


# ------------------------------------------------------------------ edit

def _roll_to(cf):
    """Put the marker just before the node; -> how to come back."""
    des = adsk.fusion.Design.cast(cf.parentComponent.parentDesign)
    tl = des.timeline
    at_end = tl.markerPosition >= tl.count
    restore = tl.markerPosition
    cf.timelineObject.rollTo(True)
    return {"at_end": at_end, "restore": restore}


def _roll_back(des, how):
    tl = des.timeline
    if how is None:
        return
    if how["at_end"] or how["restore"] >= tl.count:
        tl.moveToEnd()
    elif how["restore"] > 0:
        tl.item(how["restore"] - 1).rollTo(False)


def keep_black_step(mode_old, n_old, mode, n, followed=False):
    """An edit that does not say where the black starts keeps where it
    stood against its own surface -- the white top in White + black, the
    face in Black only -- like the dialog (its "Black offset" moves with
    the white): when the mode changes, and in White + black when the black
    followed the white (link_black). 2026-10-09: a Black only node rebuilt
    as White + black kept its black at the face, UNDER the new tile; the
    pocket (the black's bottom up to the white top) went through the whole
    tile and cut it in two -- the second piece, "tag tile ck (1)", stayed
    the component's grey. -> n (a copy, black offset set when it applies)."""
    S = SL()
    if mode not in ("both", "black"):
        return n
    old = mode_old if mode_old in MODES else "both"
    if mode == old and not (mode == "both" and followed):
        return n
    try:
        step = float(n_old["black_offset"]) - S.white_top(old, n_old)
        return dict(n, black_offset=S.clamp_offset(S.white_top(mode, n) + step))
    except (KeyError, TypeError, ValueError):
        return n


def apply_edit(cf, s_new, n_new):
    """Write new settings/numbers into a node (timeline rolled to it).

    Same structure (the same parts: aruco_slabs.plan): named value first,
    then only the parameters that changed (each change recomputes), the
    black offset linked to the white top (link_black).
    Different structure (mode, join/new, a pocket / recess / pins appearing
    or going), a different layout, or a node made before 2026-10-08: the
    node is deleted and made again at the same place with the same key,
    target and Joint Origin name.
    -> the new record (written after the caller rolls forward)."""
    s_old, n_old = settings_of(cf), numbers_of(cf)
    s_new = normalise(s_new, partial=True)
    s = dict(s_old)
    s.update({k: v for k, v in s_new.items()
              if k not in TARGET_KEYS})
    n = dict(n_old)
    n.update(n_new)
    if s.get("mode") == "auto":
        s["mode"] = "black" if s.get("has_body") else "both"
    # only an edit that ASKS puts the black at the white top: a node made
    # earlier today stored "black_follows", which must not undo a step the
    # user dialled in (the dialog sends the black offset it shows)
    s.pop("black_follows", None)
    if s_new.get("black_follows"):
        n = resolve_follow(dict(s, black_follows=True), n)
    elif "black_offset" not in n_new:
        n = keep_black_step(s_old.get("mode"), n_old, s["mode"], n,
                            black_follows(cf, s_old))
    if plan_of(s, n).pin is None:
        s["pin"] = "none"
    if "turn" in s_new:
        s["origin_v"] = OR().ORIGIN_RULE    # an Origin picked now: every one there is
    target = target_of(cf)
    if (is_legacy(s_old) or structure(s, n) != node_structure(s_old)
            or s["layout"] != s_old["layout"]):
        s.pop("parts", None)
        if s.get("source") == "face" and not s.get("x_ref"):
            # an older face node: rebuilt facing the way its record says,
            # not the way the face's longest edge happens to run now
            s["x_ref"] = x_ref_from_record(cf, s)
        return {"rebuild": True, "settings": s, "numbers": n, "target": target}
    s["v"] = SPEC_VERSION
    linked = s["mode"] == "both" and follow_expression(cf) is not None
    cf.customNamedValues.addOrSetValue("spec", json.dumps(s, sort_keys=True))
    wrote = False
    for pid in PARAMS:
        p = _param(cf, pid)
        if p is None or (pid == "black_offset" and linked):
            continue
        if abs(float(n[pid]) - float(n_old[pid])) < 1e-9:
            continue
        if pid == "marker_id":
            p.value = float(int(round(n[pid])))
        else:
            p.expression = _mm_expr(n[pid])
        wrote = True
    if linked:
        wrote = link_black(cf, n) or wrote
    # a dictionary / Origin / corner mark / quiet-zone change alone writes no
    # parameter, and a named value does not mark the node dirty: nudge one
    # so it recomputes
    if s != s_old and not wrote:
        p = _param(cf, "marker_id")
        if p is not None:
            v = p.value
            p.value = v + 1.0
            p.value = v
    return {"rebuild": False, "settings": s, "numbers": n, "target": target}


def edit(cf, s_new, n_new):
    """Whole edit (scripts): roll to the node, write or rebuild, roll
    forward, refresh record + Joint Origin. The dialog runs _finish_edit
    with the timeline its activate rolled."""
    return _finish_edit(cf, _roll_to(cf), s_new, n_new)


def _forget(des, comp, key):
    """Drop a tag's record and Joint Origin from a component it left."""
    recs = aruco_tags.read_tag_records(comp)
    old = next((r for r in recs if r.get("key") == key), None)
    if old is None:
        return
    aruco_tags.write_tag_records(comp, [r for r in recs if r.get("key") != key])
    fz = old.get("fusion") or {}
    jo = (aruco_tags._find_by_token(des, fz.get("joint_origin"))
          or comp.jointOrigins.itemByName(fz.get("joint_origin_name") or f"aruco {key}"))
    if jo is not None:
        clear_selections()
        try:
            jo.deleteMe()
        except Exception:                          # noqa: BLE001
            pass


def _finish_edit(cf, how, s_new, n_new):
    des = adsk.fusion.Design.cast(cf.parentComponent.parentDesign)
    try:
        res = apply_edit(cf, s_new, n_new)
        if res["rebuild"]:
            old_comp, key = cf.parentComponent, (settings_of(cf) or {}).get("key")
            clear_selections()                     # never delete a selected node
            cf.deleteMe()
            info = create(res["target"], res["settings"], res["numbers"], write=False)
            _roll_back(des, how)
            how = None
            if key and info["comp"] != old_comp:
                # a tag made before the body under it was found through the
                # occurrence (2026-10-08) moves into that body's part
                _forget(des, old_comp, key)
            rec = _write_record(des, info["comp"], info["key"], info["ev"], info["fz"])
            return {"rebuilt": True, "record": rec}
        ev = evaluate(res["target"], res["settings"], res["numbers"])
        _roll_back(des, how)
        how = None
        return {"rebuilt": False, "record": refresh_one(cf, roll=False, ev=ev)}
    finally:
        if how is not None:
            try:
                _roll_back(des, how)
            except Exception:                      # noqa: BLE001
                pass
        try:
            paint_all(des)          # the roll replayed later nodes' bodies
        except Exception:                          # noqa: BLE001
            pass


def refresh_one(cf, roll=True, ev=None):
    """Re-evaluate a node at its own place in the timeline and rewrite its
    record (+ Joint Origin) when the face moved or the layout changed."""
    des = adsk.fusion.Design.cast(cf.parentComponent.parentDesign)
    comp = cf.parentComponent
    s, n = settings_of(cf), numbers_of(cf)
    if ev is None and s.get("source") == "face" and not s.get("x_ref"):
        # a face tag made before 2026-10-08: its x from its record, so an
        # upstream edit that reverses the face's longest edge cannot turn it
        if _adopt_x_ref(cf, s):
            s = settings_of(cf)
    if ev is None:
        how = _roll_to(cf) if roll else None
        try:
            ev = evaluate(target_of(cf), s, n)
        finally:
            if how is not None:
                _roll_back(des, how)
    _c, old = record_of(cf)
    fz = dict((old or {}).get("fusion") or {})
    fz["custom_feature"] = cf.entityToken
    bf_tile, bf_pat = base_features(cf)
    if bf_pat is not None:
        fz["base_feature"] = bf_pat.entityToken
    cf.name = _label(s["key"], ev["spec"], s["mode"])
    if old is not None:
        same = (json.dumps(old.get("spec"), sort_keys=True)
                == json.dumps(ev["spec"].to_dict(), sort_keys=True)
                and all(max(abs(a - b) for a, b in zip(old.get(k) or [9e9] * 3, v)) < 1e-7
                        for k, v in zip(("origin", "x_axis", "y_axis", "z_axis"), ev["tag"]))
                # the corner mark switched on / off, or moved with the tile
                and CH().same((old.get("fusion") or {}).get("mark"), mark_record(ev))
                # a Frame chosen on 2026-10-08 (removed): the record loses it,
                # the Joint Origin goes back to OpenCV's frame
                and "frame" not in old and "frame_offset" not in old)
        # an unchanged record says nothing about the Joint Origin: re-solve
        # unless it still reads back as the record's frame (OpenCV's)
        if same and aruco_tags.joint_origin_matches(des, comp, old):
            return old
    return _write_record(des, comp, s["key"], ev, fz)


def x_ref_from_record(cf, s):
    """A face node's x as its record has it, back in the face's own
    component frame with the Origin turn undone, or None."""
    _c, rec = record_of(cf)
    x = (rec or {}).get("x_axis")
    z = (rec or {}).get("z_axis")
    if not x or not z:
        return None
    if s.get("host_xform"):
        m = _matrix(s["host_xform"])
        m.invert()
        x, z = _xf_vector(m, x), _xf_vector(m, z)
    # the Origin the record was made with (a node made before 2026-10-09
    # kept a rectangular board's 90 / 270 as laid out)
    sp = (rec or {}).get("spec") or {}
    turn = OR().effective(s.get("turn"), sp.get("layout", s.get("layout", "single")),
                          sp.get("cols", 1), sp.get("rows", 1), s.get("origin_v"))[0]
    if turn:
        x = _rotz(list(x), list(z), -float(turn))
    return [round(c, 12) for c in x]


def origin_of(cf):
    """The Origin (turn, deg) a node is built with: its setting, under the
    rule it was made with (a node made before 2026-10-09 kept a rectangular
    board's 90 / 270 as laid out -- the edit dialog shows what is there)."""
    s = settings_of(cf) or {}
    _c, rec = record_of(cf) if cf is not None else (None, None)
    sp = (rec or {}).get("spec") or {}
    return OR().effective(s.get("turn"), sp.get("layout", s.get("layout", "single")),
                          sp.get("cols", 1), sp.get("rows", 1), s.get("origin_v"))[0]


def _adopt_x_ref(cf, s):
    """Write x_ref (x_ref_from_record) into an old face node's spec. -> True
    if done."""
    x = x_ref_from_record(cf, s)
    if x is None:
        return False
    cf.customNamedValues.addOrSetValue("spec", json.dumps(dict(s, x_ref=x), sort_keys=True))
    return True


def refresh_live(des=None):
    """Every live tag's record brought up to date (export calls this):
    upstream edits move faces and resize layouts, and compute is not allowed
    to write attributes or move Joint Origins.

    One sweep: every node evaluated at its own place from the LAST to the
    first (rolling the marker back is cheap), then one move to the end, then
    the records. Rolling to each node and back recomputed everything after
    it every time -- quadratic in the number of tags (2026-10-08: a test
    design with ~35 tags kept Fusion busy for minutes)."""
    des = des or aruco_tags.design()
    out = []
    nodes = []
    for comp in des.allComponents:
        for rec in aruco_tags.read_tag_records(comp):
            cf = find_feature(des, rec)
            if cf is None or not is_ours(cf):
                continue
            s = settings_of(cf) or {}
            if s.get("source") == "face" and not s.get("x_ref"):
                try:
                    _adopt_x_ref(cf, s)            # an older face node: its x, first
                except Exception:                  # noqa: BLE001
                    pass
            try:
                nodes.append((cf.timelineObject.index, comp, rec, cf))
            except Exception as exc:               # noqa: BLE001
                out.append({"key": rec.get("key"), "component": comp.name,
                            "error": f"{type(exc).__name__}: {exc}"[:200]})
    evs, errors = {}, {}
    how = None
    try:
        for _i, comp, rec, cf in sorted(nodes, key=lambda t: -t[0]):
            r = _roll_to(cf)
            how = how or r
            try:
                evs[cf.entityToken] = evaluate(target_of(cf), settings_of(cf), numbers_of(cf))
            except Exception as exc:               # noqa: BLE001
                errors[cf.entityToken] = f"{type(exc).__name__}: {exc}"[:200]
    finally:
        if how is not None:
            _roll_back(des, how)
    for _i, comp, rec, cf in nodes:
        tok = cf.entityToken
        try:
            if tok in errors:
                raise ValueError(errors[tok])
            refresh_one(cf, roll=False, ev=evs[tok])
            out.append({"key": rec.get("key"), "component": comp.name, "ok": True})
        except Exception as exc:                   # noqa: BLE001
            out.append({"key": rec.get("key"), "component": comp.name,
                        "error": f"{type(exc).__name__}: {exc}"[:200]})
    try:
        paint_all(des)              # the rolls above replay the nodes' bodies
    except Exception:                              # noqa: BLE001
        pass
    return out


# ------------------------------------------------- edit command plumbing

def _on_starting(args):
    st = _st()
    if args.commandId not in EDIT_ROUTES:
        return
    # the double-clicked node, captured before commandCreated (where
    # activeSelections is sometimes empty -- apriltag3D's lesson)
    try:
        ui = _ui()
        for i in range(ui.activeSelections.count):
            cf = adsk.fusion.CustomFeature.cast(ui.activeSelections.item(i).entity)
            if cf is not None and is_ours(native_of(cf) or cf):
                st.pending_cf = native_of(cf) or cf
    except Exception:                              # noqa: BLE001
        pass


def _target_cf():
    st = _st()
    cf = st.pending_cf
    if cf is None or not cf.isValid:
        ui = _ui()
        cf = None
        for i in range(ui.activeSelections.count):
            c = adsk.fusion.CustomFeature.cast(ui.activeSelections.item(i).entity)
            if c is not None:
                cf = native_of(c) or c
                break
    return cf if is_ours(cf) else None


def _edit_log(what, cf, **kw):
    """The edit command's events (bridge diagnostics: live_status shows it)."""
    st = _st()
    entry = {"at": round(time.time(), 3), "what": what}
    try:
        des = adsk.fusion.Design.cast(cf.parentComponent.parentDesign)
        entry["marker"] = des.timeline.markerPosition
    except Exception:                              # noqa: BLE001
        pass
    entry.update(kw)
    log = getattr(st, "edit_log", None) or []
    st.edit_log = (log + [entry])[-20:]


def _on_edit_created(args):
    st = _st()
    cmd = args.command
    cf = _target_cf()
    st.pending_cf = None
    if cf is None:
        _ui().messageBox("Select an ArUco Extrude feature in the timeline first.")
        return
    st.edit = {"cf": cf, "how": None}
    _edit_log("created", cf)
    # the node is captured: nothing keeps it selected while it may be rebuilt
    clear_selections()
    if st.auto is not None and st.auto.get("kind") == "edit":
        cmd.isAutoExecute = True
    else:
        import aruco_extrude_cmd
        aruco_extrude_cmd.build(cmd, edit_cf=cf)
    for ev, fn in ((cmd.activate, "_edit_activate"), (cmd.execute, "_edit_execute"),
                   (cmd.destroy, "_edit_destroy")):
        ev.add(_keep(_CmdEvent(fn)))


def _edit_activate(args):
    st = _st()
    if not st.edit:
        return
    try:
        if not is_active(adsk.fusion.Design.cast(st.edit["cf"].parentComponent.parentDesign)):
            raise RuntimeError("the node's document is not the active one: not rolling")
        st.edit["how"] = _roll_to(st.edit["cf"])
        _edit_log("activate rolled", st.edit["cf"], how=st.edit["how"])
        if st.auto is None:
            # keep the roll out of preview aborts. beginStep undoes the roll
            # and FIRES EXECUTE for the step so far (measured 2026-10-08:
            # the edit was applied unchanged and the dialog then ran with the
            # timeline at its end): _edit_execute rolls there instead of
            # applying anything
            st.edit["in_activate"] = True
            try:
                args.command.beginStep()
            finally:
                st.edit["in_activate"] = False
            import aruco_extrude_cmd
            aruco_extrude_cmd.refresh(args.command.commandInputs, "activate")
        _edit_log("activate done", st.edit["cf"], how=st.edit.get("how"))
    except Exception:                              # noqa: BLE001
        st.edit["activate_error"] = traceback.format_exc()[-600:]
        _app().log("ArUco Extrude edit: roll failed\n" + traceback.format_exc())


def _edit_execute(args):
    st = _st()
    ed = st.edit
    if not ed:
        return
    cf = ed["cf"]
    _edit_log("execute", cf, how=ed.get("how"), in_activate=bool(ed.get("in_activate")))
    if not is_active(adsk.fusion.Design.cast(cf.parentComponent.parentDesign)):
        # the user switched documents while a scripted edit waited: touch nothing
        ed["result"] = {"ok": False, "error": "the active document changed: edit not run"}
        args.executeFailed = True
        if st.auto is not None:
            st.auto["result"] = ed["result"]
        return
    if ed.get("in_activate"):
        # beginStep's commit: Fusion undid the activate's roll and asks the
        # command to make the step's changes here -- so roll here (measured:
        # the marker was back at the end when this fired)
        try:
            des = adsk.fusion.Design.cast(cf.parentComponent.parentDesign)
            if cf.timelineObject.index != des.timeline.markerPosition:
                cf.timelineObject.rollTo(True)
            _edit_log("execute: step rolled", cf)
        except Exception:                          # noqa: BLE001
            ed["activate_error"] = traceback.format_exc()[-600:]
        return
    try:
        if st.auto is not None and st.auto.get("kind") == "edit":
            s_new, n_new = st.auto.get("settings", {}), st.auto.get("numbers", {})
        else:
            import aruco_extrude_cmd
            s_new, n_new = aruco_extrude_cmd.values(args.command.commandInputs)
            aruco_extrude_cmd.remember(args.command.commandInputs)
        how, ed["how"] = ed["how"], None
        if how is None:
            how = _roll_to(cf)
        res = _finish_edit(cf, how, s_new, n_new)
        ed["result"] = {"ok": True, "rebuilt": res["rebuilt"],
                        "key": (res["record"] or {}).get("key")}
        _edit_log("executed", cf, numbers=n_new, after=numbers_of(cf) if cf.isValid else None)
    except Exception as exc:                       # noqa: BLE001
        ed["result"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                        "where": traceback.format_exc()[-800:]}
        args.executeFailed = True
        if st.auto is None:
            _ui().messageBox("ArUco Extrude edit failed:\n\n" + traceback.format_exc())
    finally:
        if st.auto is not None:
            st.auto["result"] = ed.get("result")


def _edit_destroy(args):
    st = _st()
    ed = st.edit
    _edit_log("destroy", ed.get("cf") if ed else None, how=(ed or {}).get("how"))
    if ed and ed.get("how") is not None:          # cancelled: roll forward again
        try:
            des = adsk.fusion.Design.cast(ed["cf"].parentComponent.parentDesign)
            _roll_back(des, ed["how"])
        except Exception:                          # noqa: BLE001
            pass
    st.edit = None


# ----------------------------------------- scripted steps (bridge, tests)

def _on_auto_created(args):
    st = _st()
    cmd = args.command
    cmd.isAutoExecute = True
    if st.auto is None:
        return
    cmd.execute.add(_keep(_CmdEvent("_auto_execute")))


def _auto_execute(args):
    st = _st()
    req = st.auto
    try:
        if req.get("doc") is not None and _active_doc() != req["doc"]:
            raise RuntimeError("the active document changed while the step waited: not run")
        req["result"] = {"ok": True, "value": req["fn"](*req.get("args", ()), **req.get("kw", {}))}
    except Exception as exc:                       # noqa: BLE001
        req["result"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                         "where": traceback.format_exc()[-900:]}
        args.executeFailed = True


def _pump(st, timeout_s=20.0):
    t0 = time.perf_counter()
    while st.auto.get("result") is None and time.perf_counter() - t0 < timeout_s:
        adsk.doEvents()
        time.sleep(0.005)
    return st.auto.get("result")


def in_command(fn, *args, **kw):
    """Run fn inside a hidden auto-execute command: one undo step, the same
    context a dialog's OK runs in. Returns fn's value or raises."""
    st = _st()
    st.auto = {"kind": "call", "fn": fn, "args": args, "kw": kw, "result": None,
               "doc": _active_doc()}
    try:
        _ui().commandDefinitions.itemById(AUTO_CMD_ID).execute()
        res = _pump(st)
    finally:
        st.auto = None
    if res is None:
        raise RuntimeError("scripted step did not run within 20 s")
    if not res["ok"]:
        raise RuntimeError(res["error"] + "\n" + res.get("where", ""))
    return res["value"]


def scripted_edit(cf, settings=None, numbers=None, route="APIFeatureEditCommand"):
    """Edit a node the way a double-click does: select it, run Fusion's edit
    route (which must reach our edit command), auto-execute with values.
    route=None: the same edit in a hidden command, nothing selected."""
    st = _st()
    ui = _ui()
    des = adsk.fusion.Design.cast(cf.parentComponent.parentDesign)
    if not is_active(des):
        raise RuntimeError("the node's document is not the active one: no edit")
    if route is None:
        res = in_command(edit, cf, settings or {}, numbers or {})
        return {"ok": True, "rebuilt": res["rebuilt"],
                "key": (res.get("record") or {}).get("key")}
    st.auto = {"kind": "edit", "settings": settings or {}, "numbers": numbers or {},
               "result": None, "doc": des.parentDocument}
    try:
        ui.activeSelections.clear()
        sel = cf
        if cf.parentComponent != adsk.fusion.Design.cast(cf.parentComponent.parentDesign).rootComponent:
            des = adsk.fusion.Design.cast(cf.parentComponent.parentDesign)
            occs = des.rootComponent.allOccurrencesByComponent(cf.parentComponent)
            if occs.count:
                sel = cf.createForAssemblyContext(occs.item(0))
        ui.activeSelections.add(sel)
        ui.commandDefinitions.itemById(route).execute()
        res = _pump(st)
    finally:
        st.auto = None
        if is_active(des):               # never the user's selection in his document
            clear_selections()
    if res is None:
        raise RuntimeError(f"{route} did not reach the ArUco edit command")
    if not res["ok"]:
        raise RuntimeError(res["error"] + "\n" + res.get("where", ""))
    return res


def computes_since(n0):
    """(how many computes ran since compute_n was n0, their log entries):
    the log keeps the last 200, so len(compute_log) stops counting."""
    st = _st()
    k = getattr(st, "compute_n", 0) - n0
    return k, (st.compute_log[-k:] if k > 0 else [])


def status():
    st = _st()
    return {"registered": is_registered(), "def_id": DEF_ID,
            "register_log": st.register_log[-3:], "compute_log": st.compute_log[-8:]}
