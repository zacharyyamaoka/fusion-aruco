"""The tag's frame IS OpenCV's -- for every Origin choice, from the dialog to
the URDF.

Zach, 2026-10-09: "100% I always want the frame to match perfectly OpenCV.
If OpenCV doesn't support it I don't want to either." The custom Frame of
2026-10-08 (any corner, z out or in) is gone; the Origin choice is OpenCV's
own freedom: how the pattern lies on its footprint (bam_cad_core.
aruco_origin) -- a single marker turned about its centre, a board's origin
on any face corner (a rectangular board at 90 / 270 laid as OpenCV's board
with its cols / rows swapped, on its side).

    origin math  aruco_origin: the words ("Top left" ... / "Up: top" ...),
                 Rotate's clockwise order, which boards swap, the rule a
                 node made before 2026-10-09 keeps, and opencv_in_layout --
                 OpenCV's frame for each Origin from OpenCV's conventions
                 alone -- against markers.detector_frame of the board as
                 built, for every layout x Origin
    the add-in   aruco_feature.evaluate() (stand-in adsk, a sketch frame
                 turned 30 deg and moved) for single / square + rectangular
                 grid / square + rectangular ChArUco (Zach's 4 x 2) /
                 legacy ChArUco x Black only, White + black, a raised black
                 x all four Origins: the Joint Origin (the record's optical
                 frame, the ghost's triad) is OpenCV's frame worked out by
                 hand -- x right, y up, z out at a single marker's centre; x
                 right, y down, z INTO the board at the board's top-left --
                 and that lands on the face corner the Origin is named for;
                 the board swaps at 90 / 270, its footprint does not; a node
                 made before keeps its orientation; settings and records
                 written with the removed Frame read as OpenCV's frame; the
                 dialog's Origin list, Rotate and info line; the Corner mark
                 list (None / Cut white corner / Black triangle) and the old
                 checkbox's migration
    the URDF     a fusion360_raw/3 payload (a plate and two copies of a box,
                 one turned 90 deg, per-copy id overrides): the URDF and
                 markers.json are the goldens BYTE FOR BYTE (made by the code
                 at f9bba97, before the Frame); every <name>_target is
                 OpenCV's frame worked out by hand (yourdfpy FK);
                 tools/verify_aruco_urdf.check passes. The same payload as
                 2026-10-08's Frame wrote it (records with "frame" +
                 "frame_offset", Joint Origins AT the chosen frames) gives
                 the goldens' URDF too: read as OpenCV's frame.

Run:  python tests/test_aruco_frame.py         (needs numpy + yourdfpy)
      python tests/test_aruco_frame.py --write-goldens <dir>   (the goldens; run
      by the code BEFORE the Frame, f9bba97's bam_cad_core first on the path)
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
import types
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden"

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


def mm(a, b):
    return max(abs(p - q) for p, q in zip(a, b))


def matmul(a, b):
    return [sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4)) for r in range(4) for c in range(4)]


def frame4(o, x, y, z):
    return [x[0], y[0], z[0], o[0], x[1], y[1], z[1], o[1], x[2], y[2], z[2], o[2], 0, 0, 0, 1]


def cols(m):
    return ([m[3], m[7], m[11]], [m[0], m[4], m[8]], [m[1], m[5], m[9]], [m[2], m[6], m[10]])


def inv_rigid(m):
    r = [[m[0], m[1], m[2]], [m[4], m[5], m[6]], [m[8], m[9], m[10]]]
    t = (m[3], m[7], m[11])
    out = []
    for i in range(3):
        row = [r[0][i], r[1][i], r[2][i]]
        out += row + [-(row[0] * t[0] + row[1] * t[1] + row[2] * t[2])]
    return out + [0.0, 0.0, 0.0, 1.0]


def dot(a, b):
    return sum(p * q for p, q in zip(a, b))


def cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


# The corners as this test knows them, independently of the code: (sign of
# x, sign of y) in the pattern's own frame (x right, y up).
CORNER_SIGNS = {"top_left": (-1, 1), "top_right": (1, 1), "bottom_left": (-1, -1),
                "bottom_right": (1, -1), "centre": (0, 0)}


def hand_frame(origin, x, y, z, extent_m, raise_m, corner, zdir, scale=100.0):
    """A frame at a corner of the pattern's extent on the plane a detector
    measures, x along the pattern, z out (y up) or in (y down). Origin in
    `scale` units per metre (cm: 100)."""
    sx, sy = CORNER_SIGNS[corner]
    w, h = extent_m
    o = [origin[i] + (x[i] * sx * w / 2 + y[i] * sy * h / 2 + z[i] * raise_m) * scale
         for i in range(3)]
    s = 1.0 if zdir == "out" else -1.0
    return o, list(x), [c * s for c in y], [c * s for c in z]


def hand_opencv(origin, x, y, z, spec, scale=100.0):
    """OpenCV's frame by hand: a single marker's centre (x right, y up, z
    out); a board's top-left corner (x right, y down, z into the board); on
    the black's top (raise_m)."""
    if spec.layout == "single":
        return hand_frame(origin, x, y, z, spec.extent_m, spec.raise_m, "centre", "out", scale)
    return hand_frame(origin, x, y, z, spec.extent_m, spec.raise_m, "top_left", "in", scale)


# ===================================================================== payload
# A plate and two copies of one box (Box:2 turned 90 deg), cm. The tags live
# in the components, the Joint Origins on OpenCV's frame (worked out by hand,
# as Fusion would read them back) -- or, old=True, as 2026-10-08's Frame
# wrote them: records with "frame" + "frame_offset", Joint Origins AT the
# chosen frame.

def _xform(tx=0.0, ty=0.0, tz=0.0, rot=None):
    r = rot or (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    return [r[0], r[1], r[2], tx, r[3], r[4], r[5], ty, r[6], r[7], r[8], tz, 0.0, 0.0, 0.0, 1.0]


RZ90 = (0.0, -1.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
C30, S30 = math.cos(math.radians(30.0)), math.sin(math.radians(30.0))

# key, component, spec (TagSpec.to_dict shape), origin, x, y, z (component
# cm), the Frame 2026-10-08's records asked for (old=True only)
TAGS = (
    ("top", "Box", {"dictionary": "DICT_4X4_50", "marker_id": 7, "marker_length_m": 0.03,
                    "quiet_modules": 1.0, "depth_m": 0.0006, "layout": "single"},
     [0.5, -0.3, 3.0], [C30, S30, 0.0], [-S30, C30, 0.0], [0.0, 0.0, 1.0],
     {"corner": "top_left", "z": "out"}),
    ("side", "Box", {"dictionary": "DICT_4X4_50", "marker_id": 20, "marker_length_m": 0.012,
                     "quiet_modules": 1.0, "depth_m": 0.0004, "raise_m": 0.0006,
                     "layout": "grid", "rows": 2, "cols": 3, "ids": [20, 21, 22, 23, 24, 25],
                     "separation_m": 0.003},
     [3.0, 0.2, 1.5], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [1.0, 0.0, 0.0],
     {"corner": "bottom_right", "z": "in"}),
    ("board", "Plate", {"dictionary": "DICT_4X4_50", "marker_id": 30, "marker_length_m": 0.01875,
                        "quiet_modules": 1.0, "depth_m": 0.0006, "layout": "charuco",
                        "rows": 2, "cols": 4, "ids": [30, 31, 32, 33],
                        "square_length_m": 0.025, "legacy": False},
     [0.0, -12.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0],
     {"corner": "top_left", "z": "out"}),                       # Zach's
    ("plain", "Plate", {"dictionary": "DICT_4X4_50", "marker_id": 40, "marker_length_m": 0.02,
                        "quiet_modules": 1.0, "depth_m": 0.0006, "layout": "single"},
     [12.0, 12.0, 0.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0], [0.0, 0.0, 1.0],
     None),
)
OCCS = (("Plate:1", "Plate", _xform(), True), ("Box:1", "Box", _xform(10.0, 5.0, 2.0), False),
        ("Box:2", "Box", _xform(-10.0, 5.0, 2.0, RZ90), False))


class _Spec:
    """The two numbers hand_frame needs, read straight off a spec dict."""
    def __init__(self, d):
        L = d["marker_length_m"]
        if d["layout"] == "grid":
            c, r, g = d["cols"], d["rows"], d["separation_m"]
            self.extent_m = (c * L + (c - 1) * g, r * L + (r - 1) * g)
        elif d["layout"] == "charuco":
            self.extent_m = (d["cols"] * d["square_length_m"], d["rows"] * d["square_length_m"])
        else:
            self.extent_m = (L, L)
        self.layout = d["layout"]
        self.raise_m = d.get("raise_m", 0.0)


def old_offset(o, x, y, z, sp, frame):
    """2026-10-08's frame_offset, by hand (metres): chosen -> OpenCV's,
    p_chosen = F p_opencv, so OpenCV's = chosen x F."""
    C = frame4(*hand_frame([0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], sp.extent_m, sp.raise_m,
                           frame["corner"], frame["z"], scale=1.0))
    D = frame4(*hand_opencv([0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], sp, scale=1.0))
    return [v + 0.0 for v in matmul(inv_rigid(C), D)]


def frame_payload(old=False):
    """The fixture. old=False: every tag on OpenCV's frame -- what the
    goldens were made from. old=True: as 2026-10-08's Frame wrote it."""
    def occ(path, comp, xf, grounded):
        return {"path": path, "name": path, "component": comp, "parent_path": "",
                "is_grounded": grounded, "is_ground_to_parent": False,
                "is_light_bulb_on": True, "is_visible": True, "is_linked": False,
                "is_suppressed": False, "warnings": [], "transform": list(xf)}

    def joint(name, one, two, origin):
        return {"name": name, "kind": "revolute", "source": "joint", "context": "",
                "one": one, "two": two, "is_suppressed": False, "origin": list(origin),
                "axis": [0.0, 0.0, 1.0],
                "limits": {"min": -1.0, "max": 1.0, "min_enabled": True,
                           "max_enabled": True, "units": "rad"}, "warnings": []}
    tags, jos = [], []
    for key, comp, spec, o, x, y, z, frame in TAGS:
        rec = {"key": key, "component": comp, "spec": dict(spec), "origin": list(o),
               "x_axis": list(x), "y_axis": list(y), "z_axis": list(z), "units": "cm",
               "name": f"ArUco {key}"}
        sp = _Spec(spec)
        if old and frame is not None:
            rec["frame"] = dict(frame)
            rec["frame_offset"] = old_offset(o, x, y, z, sp, frame)
            jo = hand_frame(o, x, y, z, sp.extent_m, sp.raise_m, frame["corner"], frame["z"])
        else:
            jo = hand_opencv(o, x, y, z, sp)
        tags.append(rec)
        jos.append({"name": f"aruco {key}", "component": comp, "origin": jo[0],
                    "x_axis": jo[1], "y_axis": jo[2], "z_axis": jo[3],
                    "frame_source": "jointOrigin.transform"})
    return {
        "schema": "fusion360_raw/3", "exported_at": "2026-10-08 00:00:00",
        "fusion_version": "fixture", "design": {"name": "Frame Fixture", "units": "cm"},
        "root": {"name": "Frame Fixture"},
        "occurrences": [occ(*o) for o in OCCS],
        "joints": [joint("box1_pivot", "Box:1", "Plate:1", [10.0, 5.0, 2.0]),
                   joint("box2_pivot", "Box:2", "Plate:1", [-10.0, 5.0, 2.0])],
        "rigid_groups": [], "warnings": [],
        "joint_origins": jos,
        "aruco": {"schema": "bam_aruco/1", "units": "cm", "tags": tags,
                  "overrides": [{"owner": "", "occurrence": "Box:2",
                                 "values": {"|top": {"marker_id": 8},
                                            "|side": {"marker_id": 26}}}],
                  "variants": [], "warnings": []},
    }


def build_outputs(payload, core):
    """payload -> (urdf text, writer warnings, markers.json doc, cd). `core`:
    the bam_cad_core package to run (this tree's, or f9bba97's)."""
    import importlib
    cdm = importlib.import_module(core + ".cad_description")
    cfgm = importlib.import_module(core + ".cad_config")
    uw = importlib.import_module(core + ".urdf_writer")
    cd = cdm.CadDescription.from_fusion360(json.loads(json.dumps(payload)))
    text, warns = uw.cad_description_to_urdf(cd, cfgm.ModelConfig())
    doc = json.dumps(cd.aruco.to_dict(), indent=2, sort_keys=True, default=str) + "\n"
    return text, warns, doc, cd


def write_goldens(out_dir):
    """Run by the code BEFORE the Frame (f9bba97's bam_cad_core first on the
    path): the URDF + markers.json for the default fixture."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    text, _w, doc, _cd = build_outputs(frame_payload(False), "bam_cad_core")
    (out / "aruco_frame_default.urdf").write_text(text, encoding="utf-8", newline="\n")
    (out / "aruco_frame_default_markers.json").write_text(doc, encoding="utf-8", newline="\n")
    print("goldens written to", out)


# ============================================================ stand-in adsk

def install_adsk():
    """What aruco_feature / aruco_tags / aruco_preview / aruco_extrude_cmd need
    to import and to evaluate() a sketch profile (tests/test_aruco_chamfer_
    bodies.py has the full CSG version)."""
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

    class Profile:
        assemblyContext = None
    fusion.Profile = Profile
    return Profile


# ================================================================== the tests

def test_origin_math(M):
    import importlib
    OR = importlib.import_module("bam_cad_core.aruco_origin")
    print("== aruco_origin: the words, Rotate, which boards swap, the old rule")
    check("boards: Top left, Top right, Bottom right, Bottom left -- Rotate's order",
          [lbl for lbl, _t in OR.items("grid")] == ["Top left", "Top right", "Bottom right",
                                                     "Bottom left"]
          and [t for _l, t in OR.items("charuco")] == [0, 270, 180, 90])
    check("single markers by which way is up",
          [lbl for lbl, _t in OR.items("single")] == ["Up: top", "Up: right", "Up: bottom",
                                                       "Up: left"])
    t, seen = 0, []
    for _ in range(4):
        t = OR.rotate_cw(t)
        seen.append(t)
    check("Rotate: clockwise as seen (CCW turn -90), back after four", seen == [270, 180, 90, 0])
    check("turn_of reads either Type's words", OR.turn_of("grid", "Bottom left") == 90
          and OR.turn_of("single", "Up: left") == 90 and OR.turn_of("grid", "Up: right") == 270
          and OR.turn_of("grid", "nope") is None)
    check("a board swaps its cols / rows at 90 / 270 only; a single never",
          [OR.built("grid", t, 4, 2) for t in (0, 90, 180, 270)] == [(4, 2), (2, 4), (4, 2), (2, 4)]
          and OR.built("single", 90, 1, 1) == (1, 1) and not OR.transposes("single", 90))
    for rule, want in ((OR.ORIGIN_RULE, (90, "")), (None, 0)):
        got = OR.effective(90, "grid", 4, 2, rule)
        ok = got == want if isinstance(want, tuple) else (got[0] == want and got[1].startswith(
            "origin turned 90° needs a square footprint"))
        check(f"a rectangular board at 90: rule {rule} -> {want if isinstance(want, tuple) else 0}",
              ok, got)
    check("the old rule never touched a square board or a single marker",
          OR.effective(90, "grid", 3, 3, None) == (90, "")
          and OR.effective(270, "single", 1, 1, None) == (270, ""))
    check("a stored turn that is no quarter turn: as laid out, with a note",
          OR.effective(45, "single")[0] == 0 and OR.effective(45, "single")[1])

    print("== aruco_origin.opencv_in_layout = markers.detector_frame of the board as built")
    specs = {"single": M.TagSpec(marker_length_m=0.03),
             "grid 3x2": M.TagSpec(layout="grid", rows=2, cols=3, marker_length_m=0.012,
                                   separation_m=0.003),
             "grid 2x2": M.TagSpec(layout="grid", rows=2, cols=2, marker_length_m=0.012,
                                   separation_m=0.003),
             "charuco 4x2": M.TagSpec(layout="charuco", rows=2, cols=4, marker_length_m=0.01875,
                                      square_length_m=0.025),
             "charuco 5x4 legacy": M.TagSpec(layout="charuco", rows=4, cols=5,
                                             marker_length_m=0.0135, square_length_m=0.018,
                                             legacy=True)}
    for name, sp in specs.items():
        W, H = (v * 1000.0 for v in sp.extent_m)
        for t in OR.TURNS_CW:
            cb, rb = OR.built(sp.layout, t, sp.cols, sp.rows)
            built = M.TagSpec(**dict(vars(sp), cols=cb, rows=rb))
            D = M.detector_frame(built)                     # in the TURNED tag frame
            c, s = round(math.cos(math.radians(t))), round(math.sin(math.radians(t)))
            xt, yt = (c, s), (-s, c)                        # the turned axes, laid out
            o = (D[3] * 1000.0 * xt[0] + D[7] * 1000.0 * yt[0],
                 D[3] * 1000.0 * xt[1] + D[7] * 1000.0 * yt[1])
            dx = (D[0] * xt[0] + D[4] * yt[0], D[0] * xt[1] + D[4] * yt[1])
            dy = (D[1] * xt[0] + D[5] * yt[0], D[1] * xt[1] + D[5] * yt[1])
            (ho, hx, hy, hz) = OR.opencv_in_layout(sp.layout, t, W, H)
            ok = (mm(o, ho) < 1e-9 and mm(dx, hx) < 1e-12 and mm(dy, hy) < 1e-12
                  and abs(D[10] - hz) < 1e-12)
            check(f"{name} [{OR.label(sp.layout, t)}]: by hand = detector_frame of the "
                  f"{cb} x {rb} as built", ok, (o, ho, dx, hx, dy, hy))
            if sp.layout != "single":
                corner = OR.corner(t)
                sx = -1 if "left" in corner else 1
                sy = 1 if "top" in corner else -1
                check(f"{name} [{OR.label(sp.layout, t)}]: the origin on that face corner",
                      mm(ho, (sx * W / 2, sy * H / 2)) < 1e-9, ho)
                check(f"{name} [{OR.label(sp.layout, t)}]: same footprint, same ids",
                      mm(built.extent_m[::(1 if t % 180 == 0 else -1)], sp.extent_m) < 1e-15
                      and built.ids == sp.ids and built.marker_count == sp.marker_count)


def test_addin(M, Profile):
    sys.path.insert(0, str(ROOT / "ArucoMarkers"))
    import aruco_core
    import aruco_feature as F
    import aruco_preview as PV
    import aruco_tags as T
    import aruco_extrude_cmd as X
    AM = aruco_core.module("markers")
    OR = aruco_core.module("aruco_origin")
    CH = aruco_core.module("aruco_chamfer")

    A30 = math.radians(30.0)
    O = [1.0, 2.0, 0.5]
    XB = [math.cos(A30), math.sin(A30), 0.0]
    YB = [-math.sin(A30), math.cos(A30), 0.0]
    ZB = [0.0, 0.0, 1.0]

    def evaluate(layout, mode, op, w_mm, h_mm, turn, numbers=None, force=None, legacy=False,
                 rule=None, extra=None):
        w, h = w_mm / 10.0, h_mm / 10.0
        F.profile_rect = lambda t, s: (list(O), list(XB), list(YB), list(ZB), w, h)
        F._profile_loops_m = lambda t, o, x, y: [[(-w / 200, -h / 200), (w / 200, -h / 200),
                                                   (w / 200, h / 200), (-w / 200, h / 200)]]
        s = F.default_settings()
        s.update(mode=mode, op=op, layout=layout, turn=turn, has_body=(op == "join"),
                 source="profile", z_sign=1, x_sketch=[1.0, 0.0],
                 force=list(force) if force else None, legacy=legacy,
                 origin_v=OR.ORIGIN_RULE if rule is None else rule)
        if rule == "old":
            s.pop("origin_v")
        s.update(extra or {})
        n = F.default_numbers(mode)
        n.update(numbers or {})
        return F.evaluate(Profile(), s, n)

    print("== the add-in: evaluate() x every Origin -> the Joint Origin IS OpenCV's frame")
    CASES = (("single", 40.0, 40.0, {"size": 28.0}, None),
             ("grid", 40.0, 40.0, {"size": 14.0}, (2, 2)),
             ("grid", 60.0, 40.0, {"size": 14.0}, (3, 2)),
             ("charuco", 60.0, 60.0, {"size": 13.5, "square": 18.0}, (3, 3)),
             ("charuco", 110.0, 60.0, {"size": 18.75, "square": 25.0}, (4, 2)),      # Zach's
             ("charuco", 100.0, 80.0, {"size": 13.5, "square": 18.0}, (5, 4)))
    MODES = (("black", "join", {}), ("both", "new", {}),
             ("black", "join", {"black_offset": 0.0, "black_height": 0.6}))  # raised black
    n_cases = 0
    for layout, wmm, hmm, size, force in CASES:
        for legacy in ((False, True) if layout == "charuco" and force == (5, 4) else (False,)):
            for mode, op, heights in MODES:
                nums = dict(size, **heights)
                ev0 = evaluate(layout, mode, op, wmm, hmm, 0, nums, force, legacy)
                sp0 = ev0["spec"]
                what = (f"{layout} {sp0.cols}x{sp0.rows}{' legacy' if legacy else ''} {mode}"
                        f"{' raised' if sp0.raise_m else ''}")
                for turn in OR.TURNS_CW:
                    ev = evaluate(layout, mode, op, wmm, hmm, turn, nums, force, legacy)
                    n_cases += 1
                    tag_ = f"{what} [{OR.label(layout, turn)}]"
                    c, x, y, zt = ev["tag"]
                    sp = ev["spec"]
                    # the pattern's turned axes, by hand from the sketch's
                    t = math.radians(turn)
                    xt = [math.cos(t) * XB[i] + math.sin(t) * YB[i] for i in range(3)]
                    yt = cross(ZB, xt)
                    rec = AM.TagDef(key="k", component="", spec=sp, origin=list(c),
                                    x_axis=list(x), y_axis=list(y), z_axis=list(zt)).to_dict()
                    tag = AM.TagDef.from_dict(json.loads(json.dumps(rec)), print)
                    jo = T.optical_frame_cm(tag)                 # ensure_joint_origin's target
                    want = hand_opencv(c, xt, yt, ZB, sp)
                    # where that is on the face, as laid out (XB / YB)
                    W, H = (v * 1000.0 for v in sp0.extent_m)
                    (ou, ov), xd, yd, zs = OR.opencv_in_layout(layout, turn, W, H)
                    lift = sp.raise_m * 100.0
                    named = ([c[i] + (XB[i] * ou + YB[i] * ov) / 10.0 + ZB[i] * lift
                              for i in range(3)],
                             [XB[i] * xd[0] + YB[i] * xd[1] for i in range(3)],
                             [XB[i] * yd[0] + YB[i] * yd[1] for i in range(3)],
                             [ZB[i] * zs for i in range(3)])
                    ok = (mm(x, xt) < 1e-12 and mm(zt, ZB) < 1e-12
                          and (sp.cols, sp.rows) == OR.built(layout, turn, sp0.cols, sp0.rows)
                          and sp.ids == sp0.ids
                          and all(mm(a, b) < 1e-12 for a, b in zip(jo, want))
                          and all(mm(a, b) < 1e-12 for a, b in zip(jo, named))
                          and all(mm(a, b) < 1e-15 for a, b in zip(PV.triad(ev), jo))
                          and abs(dot(cross(jo[1], jo[2]), jo[3]) - 1.0) < 1e-12
                          and not {"frame", "frame_offset"} & set(rec))
                    check(f"{tag_}: the Joint Origin is OpenCV's frame, on the "
                          f"{'up ' + OR.up(turn) if layout == 'single' else OR.corner(turn)}"
                          f" ({sp.cols} x {sp.rows} as built)", ok,
                          {"jo": jo, "want": want, "named": named,
                           "built": (sp.cols, sp.rows)})
                    # the footprint on the face does not move
                    tw, th = (v * 1000.0 for v in sp.tile_size_m)
                    if turn % 180:
                        tw, th = th, tw
                    tw0, th0 = (v * 1000.0 for v in sp0.tile_size_m)
                    check(f"{tag_}: the same footprint on the face",
                          abs(tw - tw0) < 1e-9 and abs(th - th0) < 1e-9, (tw, th, tw0, th0))
    check(f"{n_cases} Origins evaluated", n_cases >= 84, n_cases)

    print("== a node made before 2026-10-09 keeps its orientation (no origin_v)")
    ev = evaluate("grid", "black", "join", 60.0, 40.0, 90, {"size": 14.0}, (3, 2), rule="old")
    note = next((t for t in ev["notes"] if t.startswith("origin ")), None)
    check("an old rectangular board at 90: kept as laid out (3 x 2, not turned), an info note",
          ev["turn"] == 0 and (ev["spec"].cols, ev["spec"].rows) == (3, 2) and note
          and F.is_info(note), (ev["turn"], ev["notes"]))
    ev = evaluate("grid", "black", "join", 40.0, 40.0, 90, {"size": 14.0}, (2, 2), rule="old")
    check("an old square board at 90: turned as before", ev["turn"] == 90)
    ev = evaluate("single", "black", "join", 40.0, 40.0, 270, {"size": 28.0}, rule="old")
    check("an old single marker at 270: turned as before", ev["turn"] == 270)
    check("create() stamps the new rule; the defaults carry none (old nodes keep theirs)",
          "origin_v" not in F.default_settings() and OR.ORIGIN_RULE == 2)

    print("== the removed Frame: settings and records that still carry one")
    s_old = dict(F.default_settings(), frame={"corner": "top_left", "z": "out"}, chamfer=True)
    s = F.normalise(s_old)
    check("normalise drops the Frame, the old checkbox becomes 'cut'",
          "frame" not in s and "chamfer" not in s and s["corner_mark"] == "cut")
    check("normalise keeps an explicit corner mark over the old checkbox",
          F.normalise({"chamfer": True, "corner_mark": "triangle"})["corner_mark"] == "triangle"
          and F.normalise({"chamfer": False})["corner_mark"] == "none")
    check("an edit's changes: only what they say", F.normalise({"chamfer": True}, partial=True)
          == {"corner_mark": "cut"} and F.normalise({"turn": 90}, partial=True) == {"turn": 90})
    ev = evaluate("charuco", "black", "join", 110.0, 60.0, 0, {"size": 18.75, "square": 25.0},
                  (4, 2), extra={"frame": {"corner": "top_left", "z": "out"}})
    tag = AM.TagDef(key="k", component="", spec=ev["spec"], origin=list(ev["tag"][0]),
                    x_axis=list(ev["tag"][1]), y_axis=list(ev["tag"][2]),
                    z_axis=list(ev["tag"][3]))
    check("a node whose settings still hold a Frame evaluates to OpenCV's frame (no 'frame')",
          "frame" not in ev and PV.triad(ev) == T.optical_frame_cm(tag))
    rec = dict(tag.to_dict(), frame={"corner": "top_left", "z": "out"},
               frame_offset=[1.0, 0, 0, 0, 0, -1.0, 0, 0, 0, 0, -1.0, 0, 0, 0, 0, 1.0])
    check("aruco_tags.has_old_frame spots such a record (refresh_one / record_drift rewrite it)",
          T.has_old_frame(rec) and not T.has_old_frame(tag.to_dict()))
    w = []
    back = AM.TagDef.from_dict(rec, w.append)
    check("markers reads it as OpenCV's frame, warns, keeps its offset for the Joint Origin",
          back.record_optical() == tag.record_optical() and back.old_frame_offset == rec[
              "frame_offset"] and w and "Frame chosen on 2026-10-08" in w[0]
          and "frame" not in back.to_dict(), w)
    check("a frame_offset that is no rigid transform is not used",
          AM.TagDef.from_dict(dict(rec, frame_offset=[0.0] * 16), lambda m: None)
          .old_frame_offset is None)

    print("== the dialog: Origin words, Rotate, the info line")

    class Item:
        def __init__(self, dd, name):
            self._dd, self.name, self._sel = dd, name, False

        @property
        def isSelected(self):
            return self._sel

        @isSelected.setter
        def isSelected(self, v):
            if v:
                for it in self._dd.items:
                    it._sel = False
            self._sel = bool(v)

        @property
        def index(self):
            return self._dd.items.index(self)

    class Items:
        def __init__(self, dd):
            self.dd = dd

        @property
        def count(self):
            return len(self.dd.items)

        def item(self, i):
            return self.dd.items[i]

        def add(self, name, sel):
            it = Item(self.dd, name)
            self.dd.items.append(it)
            if sel:
                it.isSelected = True
            return it

        def clear(self):
            self.dd.items = []

    class DD:
        def __init__(self, names, sel):
            self.items = []
            self.listItems = Items(self)
            for nm in names:
                self.listItems.add(nm, nm == sel)

        @property
        def selectedItem(self):
            return next((it for it in self.items if it.isSelected), None)

    class Inputs:
        def __init__(self, **dds):
            self.dds = dds

        def itemById(self, cid):
            return self.dds.get(cid)
    layout_names = [nm for nm, _v in X.LAYOUT_LABELS]
    inp = Inputs(origin=DD([lbl for lbl, _t in OR.items("single")], "Up: top"),
                 layout=DD(layout_names, "Grid board"),
                 corner_mark=DD(["None", "Cut white corner", "Black triangle"], "None"))
    X._X.turn = 0
    X._origin_items(inp, "grid")
    check("the Origin list in a board's words", [it.name for it in inp.dds["origin"].items]
          == ["Top left", "Top right", "Bottom right", "Bottom left"]
          and inp.dds["origin"].selectedItem.name == "Top left")
    shown = []
    for _ in range(4):
        X._take_origin(inp, "rotate")
        X._origin_items(inp, "grid")
        shown.append((X._X.turn, inp.dds["origin"].selectedItem.name))
    check("Rotate: Top right, Bottom right, Bottom left, Top left",
          shown == [(270, "Top right"), (180, "Bottom right"), (90, "Bottom left"),
                    (0, "Top left")], shown)
    inp.dds["origin"].listItems.item(2).isSelected = True        # jump to Bottom right
    X._take_origin(inp, "origin")
    check("the dropdown jumps straight to one", X._X.turn == 180)
    X._origin_items(inp, "single")
    check("a single marker: the same turn in its words",
          inp.dds["origin"].selectedItem.name == "Up: bottom" and X._X.turn == 180)
    sp42 = AM.TagSpec(layout="charuco", rows=4, cols=2, marker_length_m=0.01875,
                      square_length_m=0.025)
    line = X._origin_line(sp42, 90)
    check("info: the corner, OpenCV's axes, and the board on its side",
          "origin on the face's bottom left corner" in line and "z into the face" in line
          and "OpenCV's board is 2 × 4: the 4 × 2 layout on its side" in line, line)
    line = X._origin_line(AM.TagSpec(marker_length_m=0.03), 270)
    check("info, single: its up to the face's right, z out",
          "its up to the face's right" in line and "z out of the face" in line, line)

    print("== the dialog: Corner mark -- only what the tag can have, the old checkbox")
    X._X.mark_pref = "cut"
    X._show_mark(inp, CH.valid_marks("single", "both", "new"), X._X.mark_pref)
    names = [it.name for it in inp.dds["corner_mark"].items]
    check("a tile of its own: None, Cut white corner, Black triangle; 'cut' shown",
          names == ["None", "Cut white corner", "Black triangle"]
          and inp.dds["corner_mark"].selectedItem.name == "Cut white corner", names)
    X._show_mark(inp, CH.valid_marks("single", "both", "join"), X._X.mark_pref)
    check("joined: no cut offered; the triangle shown, the pick kept for later",
          [it.name for it in inp.dds["corner_mark"].items] == ["None", "Black triangle"]
          and inp.dds["corner_mark"].selectedItem.name == "Black triangle"
          and X._X.mark_pref == "cut")
    X._take_mark(inp, "corner_mark")                 # our own re-selection's echo
    check("... its echo is not a pick", X._X.mark_pref == "cut")
    X._show_mark(inp, CH.valid_marks("charuco", "black", None, rows=2), "triangle",
                 board_cut=CH.charuco_cut("charuco", 2, False))
    check("ChArUco: None and the black square's cut (no white corner for a triangle)",
          [it.name for it in inp.dds["corner_mark"].items]
          == ["None", "Cut black square's corner"]
          and inp.dds["corner_mark"].selectedItem.name == "Cut black square's corner")
    check("every label the list can show maps to its choice",
          dict(X.MARK_TABLE)["Cut black square's corner"] == "cut"
          and dict(X.MARK_TABLE)["Black triangle"] == "triangle")
    check("the old remembered checkbox ('chamfer': true) restores as 'cut'",
          CH.choice_of({"chamfer": True}) == "cut" and CH.choice_of({}) == "none"
          and CH.choice_of({"chamfer": True, "corner_mark": "triangle"}) == "triangle")
    check("the dialog no longer remembers a Frame or the checkbox",
          not {"frame", "frame_z", "chamfer"} & set(X.KEEP) and "corner_mark" in X.defaults())


def test_urdf(M):
    import numpy as np
    import yourdfpy
    sys.path.insert(0, str(ROOT / "tools"))
    import verify_aruco_urdf as V

    print("== the URDF: today's output is the goldens', byte for byte")
    text0, warns0, doc0, cd0 = build_outputs(frame_payload(False), "bam_cad_core")
    gu, gm = GOLDEN / "aruco_frame_default.urdf", GOLDEN / "aruco_frame_default_markers.json"
    check("golden URDF (made by f9bba97's code) == today's",
          gu.is_file() and gu.read_text(encoding="utf-8") == text0)
    check("golden markers.json (made by f9bba97's code) == today's",
          gm.is_file() and gm.read_text(encoding="utf-8") == doc0)
    check("no tag frame link, no frame keys",
          "_frame\"" not in text0 and '"frame' not in doc0)

    print("== 2026-10-08's records (a chosen Frame, the Joint Origin there): OpenCV's frame")
    text1, warns1, doc1, cd1 = build_outputs(frame_payload(True), "bam_cad_core")
    check("the URDF is the golden's, byte for byte", text1 == text0)

    def close(a, b):
        if isinstance(a, dict):
            return isinstance(b, dict) and a.keys() == b.keys() and all(
                close(a[k], b[k]) for k in a)
        if isinstance(a, list):
            return isinstance(b, list) and len(a) == len(b) and all(
                close(p, q) for p, q in zip(a, b))
        if isinstance(a, float) and isinstance(b, (int, float)):
            return abs(a - b) <= 1e-12
        return a == b
    check("markers.json is the golden's (to 1e-12: the offset composed back in)",
          close(json.loads(doc1), json.loads(doc0)))
    every = list(cd1.warnings) + list(warns1)
    olds = [w for w in every if "Frame chosen on 2026-10-08" in w]
    check("each old record says so (3), and no Joint Origin disagrees with its tag",
          len(olds) == 3 and not [w for w in every if "from where the pattern was built" in w],
          olds)

    print("== every target is OpenCV's frame, worked out by hand (yourdfpy FK)")
    with tempfile.TemporaryDirectory() as td:
        p0 = Path(td) / "default.urdf"
        p0.write_text(text0, encoding="utf-8")
        robot0 = yourdfpy.URDF.load(str(p0), load_meshes=False, build_scene_graph=True,
                                    build_collision_scene_graph=False)
    base = robot0.base_link
    occ_xf = {path: np.array(xf, float).reshape(4, 4) for path, _c, xf, _g in OCCS}
    to_m = np.diag([0.01, 0.01, 0.01, 1.0])
    names = []
    for inst in cd0.aruco.instances:
        _k, comp, spec_d, o, x, y, z, _frame = next(t for t in TAGS if t[0] == inst.tag.key)
        base_name = (f"aruco_{inst.spec.marker_id}" if inst.spec.layout == "single"
                     else f"{inst.spec.layout}_{inst.spec.marker_id}")
        target = f"{base_name}_target"
        names.append(target)
        W = occ_xf[inst.path]
        truth = to_m @ W @ np.array(frame4(*hand_opencv(o, x, y, z, _Spec(spec_d))),
                                    float).reshape(4, 4) @ np.linalg.inv(to_m)
        G = robot0.get_transform(target, base)
        dt = float(np.linalg.norm(G[:3, 3] - truth[:3, 3]))
        dr = math.acos(max(-1.0, min(1.0, (np.trace(G[:3, :3].T @ truth[:3, :3]) - 1) / 2)))
        check(f"{inst.name}: {target} is OpenCV's frame ({dt:.1e} m, {dr:.1e} rad), on its host",
              dt < 1e-9 and dr < 1e-8 and f"{base_name}_frame" not in robot0.link_map,
              (dt, dr))
    check("both copies of the box, per-copy ids 7/8 and 20/26",
          sorted(names) == ["aruco_40_target", "aruco_7_target", "aruco_8_target",
                            "charuco_30_target", "grid_20_target", "grid_26_target"], names)
    out = []
    _rows, bad = V.check(robot0, json.loads(doc0), 0.01, out=out.append)
    check("tools/verify_aruco_urdf.check: PASS", not bad, out)
    broken = json.loads(doc0)
    broken["instances"][0]["optical_world"][3] += 0.01               # 0.1 mm off
    _rows, bad1 = V.check(robot0, broken, 0.01, out=lambda m: None)
    check("tools/verify_aruco_urdf.check: a target 0.1 mm off FAILS", bad1)


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--write-goldens":
        sys.path.insert(0, str(ROOT / "CADExporter"))
        write_goldens(sys.argv[2])
        return 0
    sys.path.insert(0, str(ROOT / "CADExporter"))
    from bam_cad_core import markers as M
    test_origin_math(M)
    Profile = install_adsk()
    test_addin(M, Profile)
    try:                       # the URDF half needs the CAD Exporter's own
        import bam_cad_core.cad_description  # noqa: F401   modules
    except ImportError:
        print("  skip  URDF checks: the CAD Exporter core is not here "
              "(standalone fusion-aruco checkout)")
    else:
        test_urdf(M)
    print()
    print(f"{PASS} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
