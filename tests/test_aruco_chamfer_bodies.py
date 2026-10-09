"""The Fusion side of the corner mark ("Corner mark: None / Cut white corner /
Black triangle", 2026-10-09; the "Chamfer top-left corner" checkbox before),
run without Fusion.

`adsk` is a stand-in whose TemporaryBRepManager keeps every temporary body
as a CSG tree (oriented boxes, cylinders, union / difference) that answers
point containment. So the add-in's own code runs as it does in Fusion --
aruco_feature.evaluate() on a sketch profile (its frame turned 30 deg and
moved, so axis mix-ups show), part_body() for every part of the node,
aruco_tags._slab() for a copy's variant part, aruco_preview's ghost -- and
the bodies are probed at the mark: the tile's corner gone, the ChArUco
square's corner gone and the white there, the triangle black (or a notch in
the white), the pockets and recesses to match, for every layout x mode x
all four Origins (a rectangular board at 90 / 270 is OpenCV's board with its
cols / rows swapped) x the choice ("cut", which is what the checkbox made;
"triangle", which a tile of its own can now have instead), plus the pins
clear of the cut.

Run:  python tests/test_aruco_chamfer_bodies.py
"""
from __future__ import annotations

import math
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ArucoMarkers"))

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


# ------------------------------------------------ stand-in adsk (CSG bodies)

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


class _OBB:
    @staticmethod
    def create(c, L, W, l, w, h):
        return ("box", c.t(), L.t(), W.t(), float(l), float(w), float(h))


core.OrientedBoundingBox3D = _OBB


class Body:
    def __init__(self, tree):
        self.tree = tree


class _TBM:
    def createBox(self, obb):
        return Body(obb)

    def createCylinderOrCone(self, p0, r0, p1, r1):
        return Body(("cyl", p0.t(), p1.t(), float(r0)))

    def booleanOperation(self, target, tool, op):
        target.tree = ({0: "union", 1: "diff", 2: "and"}[op], target.tree, tool.tree)
        return True

    def copy(self, b):
        return Body(b.tree)


_TBM_ONE = _TBM()
fusion.TemporaryBRepManager = types.SimpleNamespace(get=lambda: _TBM_ONE)
fusion.BooleanTypes = types.SimpleNamespace(UnionBooleanType=0, DifferenceBooleanType=1,
                                            IntersectionBooleanType=2)


class Profile:                      # what evaluate() asks isinstance() about
    assemblyContext = None


fusion.Profile = Profile


def _dot(a, b):
    return sum(p * q for p, q in zip(a, b))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(a):
    n = math.sqrt(_dot(a, a))
    return tuple(c / n for c in a)


def contains(tree, p, eps=1e-9):
    k = tree[0]
    if k == "box":
        c, L, W, l, w, h = tree[1:]
        L, W = _unit(L), _unit(W)
        H = _unit(_cross(L, W))
        d = tuple(p[i] - c[i] for i in range(3))
        return (abs(_dot(d, L)) <= l / 2 + eps and abs(_dot(d, W)) <= w / 2 + eps
                and abs(_dot(d, H)) <= h / 2 + eps)
    if k == "cyl":
        p0, p1, r = tree[1:]
        ax = tuple(p1[i] - p0[i] for i in range(3))
        n = math.sqrt(_dot(ax, ax))
        u = tuple(c / n for c in ax)
        d = tuple(p[i] - p0[i] for i in range(3))
        t = _dot(d, u)
        rad = math.sqrt(max(0.0, _dot(d, d) - t * t))
        return -eps <= t <= n + eps and rad <= r + eps
    if k == "union":
        return contains(tree[1], p) or contains(tree[2], p)
    if k == "diff":
        return contains(tree[1], p) and not contains(tree[2], p)
    if k == "and":
        return contains(tree[1], p) and contains(tree[2], p)
    raise ValueError(k)


import aruco_core  # noqa: E402
import aruco_feature as F  # noqa: E402
import aruco_preview as PV  # noqa: E402
import aruco_tags as T  # noqa: E402
import aruco_extrude_cmd as X  # noqa: E402

M = aruco_core.module("markers")
CH = aruco_core.module("aruco_chamfer")
SL = aruco_core.module("aruco_slabs")

# a sketch frame turned 30 deg and moved: axis mix-ups cannot hide
A30 = math.radians(30.0)
O = [1.0, 2.0, 0.5]
XB = [math.cos(A30), math.sin(A30), 0.0]
YB = [-math.sin(A30), math.cos(A30), 0.0]
ZB = [0.0, 0.0, 1.0]


def evaluate(layout, mode, op, w_mm, h_mm, turn=0, mark="cut", pin="none", has_body=True,
             numbers=None):
    w, h = w_mm / 10.0, h_mm / 10.0
    F.profile_rect = lambda t, s: (list(O), list(XB), list(YB), list(ZB), w, h)
    F._profile_loops_m = lambda t, o, x, y: [[(-w / 200, -h / 200), (w / 200, -h / 200),
                                               (w / 200, h / 200), (-w / 200, h / 200)]]
    s = F.default_settings()
    s.update(mode=mode, op=op, layout=layout, corner_mark=mark, turn=turn, pin=pin,
             has_body=has_body, source="profile", z_sign=1, x_sketch=[1.0, 0.0],
             origin_v=aruco_core.module("aruco_origin").ORIGIN_RULE)
    n = F.default_numbers(mode)
    n.update(numbers or {})
    return s, n, F.evaluate(Profile(), s, n)


def W(ev, uv_mm, z_mm):
    """World cm: (u, v) mm in the pattern frame, z mm over the face plane."""
    o, x, y, z = ev["tag"]
    lift = ev["lift_cm"]
    return tuple(o[i] - z[i] * lift + (x[i] * uv_mm[0] + y[i] * uv_mm[1] + z[i] * z_mm) / 10.0
                 for i in range(3))


def centroid(pts):
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def mid(sl):
    return (sl[0] + sl[1]) / 2.0


def corner0_world(ev):
    spec = ev["spec"]
    D = M.detector_frame(spec)
    if spec.layout == "single":
        h = spec.marker_length_m * 500.0
        uv = (-h, h)
    else:
        uv = (D[3] * 1000.0, D[7] * 1000.0)
    return W(ev, uv, 0.0)


CASES = [  # layout, profile w x h mm, the turns to try (every Origin, 2026-10-09)
    ("single", 40.0, 40.0, (0, 90, 180, 270)),
    ("single", 60.0, 40.0, (0, 90, 180)),        # a sketch area bigger than the sticker
    ("grid", 100.0, 40.0, (0, 90, 180, 270)),     # 90 / 270: OpenCV's board swapped
    ("charuco", 60.0, 60.0, (0, 90, 180, 270)),
    ("charuco", 110.0, 60.0, (0, 90, 270)),       # Zach's 4 x 2 (2 x 4 on its side)
]
MODES = (("both", "new", "cut"), ("both", "join", "cut"), ("black", "join", "cut"),
         ("white", "join", "cut"),
         ("both", "new", "triangle"))              # Black triangle on a tile of its own


def want_kind(spec, mode, op, mark):
    """What a tag asking for `mark` gets (aruco_chamfer.choose)."""
    form, _note = CH.choose(mark, CH.valid_marks(spec.layout, mode, op, spec.rows, spec.legacy))
    if form == "cut":
        return "black_cut" if CH.charuco_cut(spec.layout, spec.rows, spec.legacy) else "tile_cut"
    return form

print("== evaluate() -> every part's body, probed at the mark")
for layout, wmm, hmm, turns in CASES:
    for mode, op, mark in MODES:
        for turn in turns:
            tag = f"{layout} {wmm:g}x{hmm:g} {mode}/{op} {mark} turn {turn}"
            try:
                s, n, ev = evaluate(layout, mode, op, wmm, hmm, turn, mark=mark)
            except Exception as exc:               # noqa: BLE001
                check(f"{tag}: evaluates", False, f"{type(exc).__name__}: {exc}")
                continue
            mk = ev["mark"]
            spec = ev["spec"]
            plan = ev["plan"]
            L = plan.layers
            want = want_kind(spec, mode, plan.op, mark)
            check(f"{tag}: {want}", mk is not None and mk.kind == want,
                  (mk.kind if mk else None, ev["notes"]))
            if mk is None:
                continue
            c = centroid(mk.cut or mk.solid)
            # the mark sits at the corner nearest OpenCV's corner 0 / the board origin
            Wd, Hd = (v * 1000.0 for v in spec.extent_m)
            corners = [W(ev, q, 0.0) for q in ((-Wd / 2, Hd / 2), (Wd / 2, Hd / 2),
                                               (Wd / 2, -Hd / 2), (-Wd / 2, -Hd / 2))]
            near = min(corners, key=lambda q: math.dist(q, W(ev, mk.corner, 0.0)))
            check(f"{tag}: at corner 0 / the board origin (world)",
                  math.dist(near, corner0_world(ev)) < 1e-9)
            bodies = {r: F.part_body(r, ev, n) for r in plan.parts}
            zp = mid(L.pattern)
            if "tile" in bodies:
                tb, zw = bodies["tile"].tree, mid(L.white)
                if mk.kind == "tile_cut":
                    a, b = [p for p in mk.cut if p != mk.corner]
                    keep = ((a[0] + b[0]) / 2 + 0.3, (a[1] + b[1]) / 2 - 0.3)
                    far = (-mk.corner[0] - 0.5, -mk.corner[1] + 0.5)      # the opposite corner
                    check(f"{tag}: tile -- corner gone, there past the cut, opposite corner whole",
                          not contains(tb, W(ev, c, zw)) and contains(tb, W(ev, keep, zw))
                          and contains(tb, W(ev, far, zw)))
                else:
                    check(f"{tag}: tile whole at the corner (the mark is not a tile cut)",
                          contains(tb, W(ev, c, zw)))
            pat = bodies["pattern"].tree
            if mk.kind == "black_cut":
                sq = spec.square_length_m * 1000.0
                sqc = (mk.corner[0] + sq / 2, mk.corner[1] - sq / 2)
                if mode == "white":
                    check(f"{tag}: White only -- the white fills the cut corner, not the square",
                          contains(pat, W(ev, c, zp)) and not contains(pat, W(ev, sqc, zp)))
                else:
                    check(f"{tag}: the top-left square's outer corner cut, the square kept",
                          not contains(pat, W(ev, c, zp)) and contains(pat, W(ev, sqc, zp)))
            elif mk.kind == "triangle":
                if mode == "white":
                    qz = (Wd / 2 + spec.module_m * 500.0, -Hd / 2 - spec.module_m * 500.0)
                    check(f"{tag}: White only -- a notch at the triangle, white in the quiet zone",
                          not contains(pat, W(ev, c, zp)) and contains(pat, W(ev, qz, zp)))
                else:
                    check(f"{tag}: the triangle is black (part of the pattern)",
                          contains(pat, W(ev, c, zp)))
            else:
                x0, y0, x1, y1 = (v * 1000.0 for v in M.cell_boxes(spec)[0])
                check(f"{tag}: the pattern is untouched by a tile cut",
                      contains(pat, W(ev, ((x0 + x1) / 2, (y0 + y1) / 2), zp)))
            if "pocket" in bodies:
                zk = mid(plan.pocket)
                inside = contains(bodies["pocket"].tree, W(ev, c, zk))
                check(f"{tag}: the pocket follows the pattern (cut corner kept white / "
                      f"the triangle's pocket)",
                      inside == (mk.kind == "triangle"), inside)
            if "recess" in bodies:
                for what, sl in plan.recess:
                    if what == "hole":
                        continue
                    inside = contains(bodies["recess"].tree, W(ev, c, mid(sl)))
                    wants = (mk.kind == "triangle" and mode != "white") or \
                            (mk.kind == "black_cut" and mode == "white")
                    check(f"{tag}: the recess ({what}) follows: "
                          f"{'cut' if wants else 'kept'} at the mark", inside == wants, inside)
            # a copy's variant part: aruco_tags._slab with the record's mark
            rec_mark = F.mark_record(ev)
            frame = ev["tag"]
            lo, hi = ((L.pattern[0] - L.surface) / 10.0, (L.pattern[1] - L.surface) / 10.0)
            white = mode == "white"
            vs = T._slab(spec, frame, lo, hi, white, rec_mark).tree
            zr = mid(L.pattern) - L.surface

            def at_rec(uv, zz):
                o, x, y, z = frame
                return tuple(o[i] + (x[i] * uv[0] + y[i] * uv[1] + z[i] * zz) / 10.0
                             for i in range(3))
            same = contains(vs, at_rec(c, zr)) == contains(pat, W(ev, c, zp))
            check(f"{tag}: a copy's pattern carries the same mark", same)
            cut = T._slab(spec, frame, lo, max(0.0, hi), white, rec_mark, triangle=False).tree
            if mk.kind == "triangle":
                check(f"{tag}: a copy's body cut leaves the triangle's pocket as it was",
                      not contains(cut, at_rec(c, zr)))
            elif mk.kind == "black_cut":
                check(f"{tag}: a copy's body cut keeps the corner {'(white cuts it)' if white else ''}",
                      contains(cut, at_rec(c, zr)) == white)
            # the ghost
            wb, bb = PV.boxes(ev, mode)
            wp, bp = PV.mark_prisms(ev, mode)
            ncell = len(M.cell_boxes(spec))
            if mk.kind == "tile_cut":
                ok = len(wp) == 1 and len(bb) == ncell and len(wb) == 0
            elif mk.kind == "black_cut":
                ok = len(bp) == 1 and len(bb) == ncell - 1
            else:
                ok = len(bp) == 1 and len(bb) == ncell
            check(f"{tag}: the ghost draws the mark ({len(wp)} white / {len(bp)} black prism)", ok,
                  (len(wb), len(bb), len(wp), len(bp), ncell))
            frame_g, pts, glo, ghi = (wp or bp)[0]
            gc = centroid(pts)
            gw = tuple(frame_g[0][i] + frame_g[1][i] * gc[0] + frame_g[2][i] * gc[1]
                       for i in range(3))
            want_c = centroid(mk.outline if mk.is_cut else mk.solid)
            check(f"{tag}: ... where the body has it",
                  math.dist(gw, W(ev, want_c, 0.0)) < 1e-9 and CH.area2(pts) > 0)

print("== off: nothing changes")
for layout, wmm, hmm, _t in CASES:
    for mode, op, _mark in MODES[:4]:
        s, n, ev = evaluate(layout, mode, op, wmm, hmm, 0, mark="none")
        tag = f"{layout} {wmm:g}x{hmm:g} {mode}/{op}"
        check(f"{tag}: no mark, no note", ev["mark"] is None and not any(
            "corner mark" in t for t in ev["notes"]) and F.mark_record(ev) is None)
        if mode == "both":
            tb = F.part_body("tile", ev, n).tree
            tw, th = ev["tile"][4] * 10.0, ev["tile"][5] * 10.0
            check(f"{tag}: the tile's corner is there",
                  contains(tb, W(ev, (-tw / 2 + 0.2, th / 2 - 0.2), mid(ev["plan"].layers.white))))

print("== too narrow a white border: a note (the node's warning), no mark")
s, n, ev = evaluate("single", "both", "new", 40.0, 40.0, 0)
s2 = dict(s, quiet_modules=0.5)
ev2 = F.evaluate(Profile(), s2, n)
check("quiet 0.5: no mark", ev2["mark"] is None)
check("quiet 0.5: the node says why", any("corner mark: no room" in t for t in ev2["notes"])
      and not any(F.is_info(t) for t in ev2["notes"] if "corner mark" in t), ev2["notes"])

print("== pins under a cut tile")
for shape in ("triangle", "round"):
    for turn in (0, 90):
        s, n, ev = evaluate("single", "both", "new", 40.0, 40.0, turn, pin=shape)
        mk = ev["mark"]
        pins = ev["pins"]
        first = pins[0]
        pts = SL.outline_points(first, 64)
        gap = min(((p[0] - mk.corner[0]) + (mk.corner[1] - p[1]) - mk.leg) / math.sqrt(2.0)
                  for p in pts)
        body = F.part_body("pin", ev, n).tree
        pc = centroid(pts)
        check(f"{shape} pin, turn {turn}: 1 mm clear of the cut ({gap:.3f} mm) and built there",
              gap >= 1.0 - 1e-6 and contains(body, W(ev, pc, mid(ev["plan"].pin))), gap)

print("== pins follow OpenCV's origin on a rectangular board, every Origin (swapped at 90 / 270)")
for turn in (0, 90, 180, 270):
    s, n, ev = evaluate("grid", "both", "new", 100.0, 40.0, turn, mark="cut", pin="triangle")
    spec = ev["spec"]
    body = F.part_body("pin", ev, n).tree
    pc = centroid(SL.outline_points(ev["pins"][0], 64))
    o, x, y, _z = ev["pin_frame"]
    pw = tuple(o[i] + (x[i] * pc[0] + y[i] * pc[1]) / 10.0 for i in range(3))
    k0 = corner0_world(ev)
    cen = W(ev, (0.0, 0.0), 0.0)
    same_quadrant = all(
        (sum((pw[i] - cen[i]) * a[i] for i in range(3)) > 0)
        == (sum((k0[i] - cen[i]) * a[i] for i in range(3)) > 0) for a in (XB, YB))
    check(f"grid 100x40 turn {turn} ({spec.cols} x {spec.rows} as built): the pin at the "
          "board origin's corner, built there",
          same_quadrant and contains(body, W(ev, pc, mid(ev["plan"].pin)))
          and (spec.cols < spec.rows) == (turn % 180 == 90), (spec.cols, spec.rows))

print("== the record and the dialog")
s, n, ev = evaluate("single", "both", "new", 40.0, 40.0, 90)
d = F.mark_record(ev)
check("the record keeps the mark (JSON)", d["kind"] == "tile_cut" and CH.same(d, ev["mark"].to_dict(6)))
check("refresh_one's test: on vs off is a change", not CH.same(None, d) and CH.same(d, dict(d)))
s, n, ev = evaluate("single", "both", "join", 40.0, 40.0, 0)
line = X._mark_line(ev["mark"], "Body1")
check("the dialog says why a joined tile gets a triangle, in the body's name",
      "joined to 'Body1': a cut corner would cut 'Body1'" in line, line)
check("the dialog remembers the corner mark (by hand: the user's pick), none by default",
      X.defaults()["corner_mark"] == "none" and "chamfer" not in X.KEEP)
check("a node made before keeps its geometry (no mark by default)",
      F.default_settings()["corner_mark"] == "none" and F.settings_of(None) is None)
check("a node made with the checkbox on ('chamfer': true) is 'cut': the same mark",
      CH.choice_of(F.normalise({"chamfer": True})) == "cut")
s, n, ev = evaluate("single", "both", "new", 40.0, 40.0, 0, mark="triangle")
line = X._mark_line(ev["mark"], None)
check("Black triangle on a tile of its own: the dialog says the tile stays a rectangle",
      "black triangle" in line and "the tile stays a plain rectangle" in line, line)
s, n, ev = evaluate("single", "both", "join", 40.0, 40.0, 0, mark="cut")
check("Cut asked for on a joined tile: the triangle, and an info note (no node warning)",
      ev["mark"].kind == "triangle"
      and any(t.startswith(CH.INSTEAD) and F.is_info(t) for t in ev["notes"]), ev["notes"])
s, n, ev = evaluate("charuco", "both", "new", 60.0, 60.0, 0, mark="triangle")
check("Black triangle asked for on a ChArUco board (black top-left): its square's cut",
      ev["mark"].kind == "black_cut"
      and any(t.startswith(CH.INSTEAD) for t in ev["notes"]), ev["notes"])

print()
print(f"{PASS} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
