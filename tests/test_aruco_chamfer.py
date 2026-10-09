"""Offline tests for bam_cad_core.aruco_chamfer -- ArUco Extrude's "Chamfer
top-left corner" (Zach 2026-10-08: "Chamfer top left corner. make sure it
works in single marker, board and charuco"), the geometry
docs/proposal_aruco_chamfer.md recommends:

  * single / grid, a tile of its own: the WHITE tile's corner cut, 0.7 module
  * ChArUco: the top-left BLACK square's outer corner cut, 2 modules
  * no tile edge to cut (Black only, White only, a joined tile): a 0.6-module
    triangle in the quiet zone's corner, ~1 module from the black corner

checked at OpenCV's corner 0 / the board origin for every Origin turn, legs
in module units, clear of every black cell, and the locating pins clear of a
cut corner.

Run:  python tests/test_aruco_chamfer.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "CADExporter"))

from bam_cad_core import aruco_board as B  # noqa: E402
from bam_cad_core import aruco_chamfer as C  # noqa: E402
from bam_cad_core import aruco_slabs as S  # noqa: E402
from bam_cad_core import markers as M  # noqa: E402

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


def close(a, b, tol=1e-6):
    if isinstance(a, (tuple, list)):
        return len(a) == len(b) and all(close(x, y, tol) for x, y in zip(a, b))
    return abs(a - b) <= tol


# ------------------------------------------------------------ 2D helpers

def rect_pts(r):
    x0, y0, x1, y1 = r
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def seg_dist(p, a, b):
    ax, ay = b[0] - a[0], b[1] - a[1]
    L = ax * ax + ay * ay
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * ax + (p[1] - a[1]) * ay) / L))
    return math.hypot(p[0] - a[0] - t * ax, p[1] - a[1] - t * ay)


def overlap(P, Q):
    """Convex polygons share area (separating-axis test, touching = no)."""
    for poly in (P, Q):
        for i in range(len(poly)):
            a, b = poly[i - 1], poly[i]
            n = (b[1] - a[1], a[0] - b[0])
            pa = [n[0] * p[0] + n[1] * p[1] for p in P]
            pb = [n[0] * p[0] + n[1] * p[1] for p in Q]
            if max(pa) <= min(pb) + 1e-12 or max(pb) <= min(pa) + 1e-12:
                return False
    return True


def poly_dist(P, Q):
    """Distance between two convex polygons (0 when they overlap or touch)."""
    if overlap(P, Q):
        return 0.0
    d = min(seg_dist(p, Q[i - 1], Q[i]) for p in P for i in range(len(Q)))
    return min(d, min(seg_dist(q, P[i - 1], P[i]) for q in Q for i in range(len(P))))


def point_poly_dist(p, P):
    return min(seg_dist(p, P[i - 1], P[i]) for i in range(len(P)))


def inside(p, P, tol=1e-9):
    """p inside or on a CCW convex polygon."""
    return all((P[i][0] - P[i - 1][0]) * (p[1] - P[i - 1][1])
               - (P[i][1] - P[i - 1][1]) * (p[0] - P[i - 1][0]) >= -tol for i in range(len(P)))


def centroid(P):
    return (sum(p[0] for p in P) / len(P), sum(p[1] for p in P) / len(P))


def cells_mm(spec):
    return [tuple(v * 1000.0 for v in r) for r in M.cell_boxes(spec)]


def turned(theta):
    """The pattern frame's axes after an Origin turn (deg, CCW about +z)."""
    c, s = math.cos(math.radians(theta)), math.sin(math.radians(theta))
    return (c, s), (-s, c)


def world(p, axes, o=(0.0, 0.0)):
    x, y = axes
    return (o[0] + p[0] * x[0] + p[1] * y[0], o[1] + p[0] * x[1] + p[1] * y[1])


def corner0_tag(spec):
    """OpenCV's corner 0 of a single marker / the board's origin, in the TAG
    frame (mm): the detector frame's origin for a board (aruco_board,
    measured against cv2), the first detected corner for a single marker."""
    bs = M.board_spec(spec)
    D = M.detector_frame(spec)
    if spec.layout == "single":
        p = B.object_points(bs)[spec.marker_id][0]
    else:
        p = (0.0, 0.0, 0.0)
    return ((D[0] * p[0] + D[1] * p[1] + D[3]) * 1000.0, (D[4] * p[0] + D[5] * p[1] + D[7]) * 1000.0)


def first_marker_corner0_tag(spec):
    """Corner 0 (top-left as detected) of the board's first marker, tag frame mm."""
    bs = M.board_spec(spec)
    D = M.detector_frame(spec)
    p = B.object_points(bs)[spec.marker_id][0]
    return ((D[0] * p[0] + D[1] * p[1] + D[3]) * 1000.0, (D[4] * p[0] + D[5] * p[1] + D[7]) * 1000.0)


# --------------------------------------------------------------- the tags

SINGLE = M.TagSpec(dictionary="DICT_4X4_50", marker_id=3, marker_length_m=0.03, layout="single")
SINGLE6 = M.TagSpec(dictionary="DICT_6X6_250", marker_id=7, marker_length_m=0.04, layout="single")
GRID = M.TagSpec(dictionary="DICT_4X4_50", marker_id=0, marker_length_m=0.03, layout="grid",
                 rows=1, cols=3, separation_m=0.006)
GRID22 = M.TagSpec(dictionary="DICT_5X5_100", marker_id=4, marker_length_m=0.02, layout="grid",
                   rows=2, cols=2, separation_m=0.004)
# what aruco_autosize makes: 3 x 3, no quiet zone, marker 0.75 of the square
CH33 = M.TagSpec(dictionary="DICT_4X4_50", marker_id=0, marker_length_m=0.02775, layout="charuco",
                 rows=3, cols=3, square_length_m=0.037, quiet_modules=0.0)
CH54 = M.TagSpec(dictionary="DICT_6X6_250", marker_id=10, marker_length_m=0.024, layout="charuco",
                 rows=4, cols=5, square_length_m=0.032, quiet_modules=1.0)
# OpenCV's legacy pattern with an even row count: the top-left square is WHITE
CHLEG = M.TagSpec(dictionary="DICT_4X4_50", marker_id=0, marker_length_m=0.0225, layout="charuco",
                  rows=4, cols=4, square_length_m=0.030, quiet_modules=0.0, legacy=True)
TAGS = {"single 4x4": SINGLE, "single 6x6": SINGLE6, "grid 3x1": GRID, "grid 2x2": GRID22,
        "charuco 3x3": CH33, "charuco 5x4 q1": CH54, "charuco legacy 4x4": CHLEG}
# (mode, effective op): a tile of its own, a joined tile, black only, white only
MODES = (("both", "new"), ("both", "join"), ("black", None), ("white", None))


def turns_of(spec, tile=None):
    w, h = tile or tuple(v * 1000.0 for v in spec.tile_size_m)
    return (0, 90, 180, 270) if abs(w - h) < 1e-9 else (0, 180)


def mark(spec, mode, op, turn=0, tile=None):
    """As evaluate() asks: the tile's sides in the turned frame."""
    t = tile
    if t is None and mode == "both":
        t = tuple(v * 1000.0 for v in spec.tile_size_m)
    if t is not None and turn % 180:
        t = (t[1], t[0])
    return C.mark_for_spec(spec, mode, op, t if mode == "both" else None)


def expected_kind(spec, mode, op):
    if spec.layout == "charuco" and C.charuco_black_tl(spec.rows, spec.legacy):
        return "black_cut"
    return "tile_cut" if (mode == "both" and op == "new") else "triangle"


print("== which mark each layout x mode gets")
for name, spec in TAGS.items():
    for mode, op in MODES:
        mk = mark(spec, mode, op)
        check(f"{name}, {mode}/{op}: {expected_kind(spec, mode, op)}",
              mk.kind == expected_kind(spec, mode, op), (mk.kind, mk.note))
check("the ChArUco rule is OpenCV's (aruco_board._charuco_black(0, 0, ...))",
      all(C.charuco_black_tl(r, lg) == B._charuco_black(0, 0, r, lg)
          for r in range(2, 9) for lg in (False, True)))
check("every board the add-in makes (legacy False) has a black top-left square",
      all(C.charuco_black_tl(r, False) for r in range(2, 12)))

print("== at OpenCV's corner 0 / the board origin, for every Origin turn")
for name, spec in TAGS.items():
    for mode, op in MODES:
        for turn in turns_of(spec):
            mk = mark(spec, mode, op, turn)
            axes = turned(turn)
            poly = mk.cut or mk.solid
            # corner 0 / the board origin in the world, through the turned frame
            k0 = world(corner0_tag(spec), axes)
            # the pattern's four corners in the world; the mark is nearest corner 0's
            W, H = (v * 1000.0 for v in spec.extent_m)
            corners = [world(p, axes) for p in ((-W / 2, H / 2), (W / 2, H / 2), (W / 2, -H / 2),
                                                (-W / 2, -H / 2))]
            near = min(corners, key=lambda q: math.dist(q, world(centroid(poly), axes)))
            check(f"{name}, {mode}/{op}, turn {turn}: the mark is at the corner OpenCV "
                  "calls 0 / the board origin", close(near, k0, 1e-6), (near, k0))
            if spec.layout == "charuco" and not C.charuco_black_tl(spec.rows, spec.legacy):
                kb = first_marker_corner0_tag(spec)
            else:
                kb = corner0_tag(spec)
            check(f"{name}, {mode}/{op}, turn {turn}: its black reference is that corner 0",
                  close(mk.black, kb, 1e-6), (mk.black, kb))

print("== legs, in module units")
for name, spec in TAGS.items():
    m = spec.module_m * 1000.0
    for mode, op in MODES:
        mk = mark(spec, mode, op)
        poly = mk.cut or mk.solid
        want = {"tile_cut": C.TILE_LEG * m, "triangle": C.TRI_LEG * m,
                "black_cut": min(C.CHARUCO_LEG * m,
                                 C.CHARUCO_LEG_SQUARE * spec.square_length_m * 1000.0)}[mk.kind]
        c = mk.corner
        legs = sorted(math.dist(c, p) for p in poly if p != c)
        check(f"{name}, {mode}/{op}: {mk.kind} legs {want / m:.2g} module = {want:.3f} mm",
              close(mk.leg, want) and close(legs, [want, want]), (mk.leg, legs))
        hyp = [p for p in poly if p != c]
        check(f"{name}, {mode}/{op}: cut at 45° (the long side along u = -v)",
              close(abs(hyp[0][0] - hyp[1][0]), abs(hyp[0][1] - hyp[1][1])))
        check(f"{name}, {mode}/{op}: counter-clockwise", C.area2(poly) > 0)
m = CH33.module_m * 1000.0
check("ChArUco 3x3 (marker 0.75 square): 2 modules = 1/4 square",
      close(mark(CH33, "both", "new").leg, 2.0 * m) and close(2.0 * m, 0.25 * 37.0, 1e-9))
check("ChArUco 5x4 6x6: 2 modules (< 1/4 square)", close(mark(CH54, "black", None).leg,
                                                          2.0 * CH54.module_m * 1000.0))
big = M.TagSpec(dictionary="DICT_4X4_50", marker_length_m=0.0285, layout="charuco", rows=3, cols=3,
                square_length_m=0.030, quiet_modules=0.0)
check("a marker of 0.95 square: capped at 1/4 square", close(mark(big, "black", None).leg, 7.5),
      mark(big, "black", None).leg)

print("== single and grid: never touches a black cell")
for name, spec in TAGS.items():
    if spec.layout == "charuco" and C.charuco_black_tl(spec.rows, spec.legacy):
        continue
    m = spec.module_m * 1000.0
    cells = [rect_pts(r) for r in cells_mm(spec)]
    for mode, op in MODES:
        mk = mark(spec, mode, op)
        poly = mk.cut or mk.solid
        d = min(poly_dist(poly, c) for c in cells)
        floor = 0.70 if mk.kind == "tile_cut" else C.TRI_CLEAR
        check(f"{name}, {mode}/{op}: {mk.kind} {d / m:.3f} module from the nearest black "
              f"(>= {floor})", d >= floor * m - 1e-9, d / m)
        check(f"{name}, {mode}/{op}: the clearance it reports is that distance",
              close(mk.clear, d, 1e-6), (mk.clear, d))
        if mk.kind == "triangle":
            check(f"{name}, {mode}/{op}: triangle >= 0.9 module from the black corner",
                  point_poly_dist(mk.black, mk.solid) >= 0.9 * m - 1e-9,
                  point_poly_dist(mk.black, mk.solid) / m)
mk = mark(SINGLE, "both", "new")
check("single, quiet 1, legs 0.7: the cut line 0.92 module from corner 0 (the study's)",
      close(mk.clear / (SINGLE.module_m * 1000.0), 1.3 / math.sqrt(2.0), 1e-9))
mk = mark(SINGLE, "black", None)
check("single, quiet 1: the triangle is the study's tri_tl (0.99 module, the PDF's place)",
      close(mk.clear / (SINGLE.module_m * 1000.0), 1.4 / math.sqrt(2.0), 1e-9))
pdf_corner = (-SINGLE.tile_size_m[0] * 500.0, SINGLE.tile_size_m[1] * 500.0)
check("single: the triangle's right angle is the sticker's corner (print_sheet's)",
      close(mk.corner, pdf_corner))

print("== ChArUco: only the outer corner of the top-left black square")
for name in ("charuco 3x3", "charuco 5x4 q1"):
    spec = TAGS[name]
    rects = cells_mm(spec)
    W, H = (v * 1000.0 for v in spec.extent_m)
    sq = spec.square_length_m * 1000.0
    tl = (-W / 2, H / 2 - sq, -W / 2 + sq, H / 2)
    for mode, op in MODES:
        mk = mark(spec, mode, op)
        hit = [r for r in rects if overlap(mk.cut, rect_pts(r))]
        check(f"{name}, {mode}/{op}: cuts exactly one black region, the top-left square",
              len(hit) == 1 and close(hit[0], tl, 1e-6), hit)
        check(f"{name}, {mode}/{op}: the cut lies inside that square",
              all(inside(p, rect_pts(tl), 1e-9) for p in mk.cut))
        check(f"{name}, {mode}/{op}: ... at its OUTER corner (the board's corner)",
              close(mk.corner, (-W / 2, H / 2)) and all(math.dist(p, mk.corner) <= mk.leg + 1e-9
                                                         for p in mk.cut))
        others = [rect_pts(r) for r in rects if not close(r, tl, 1e-6)]
        check(f"{name}, {mode}/{op}: touches no other cell (markers included)",
              min(poly_dist(mk.cut, o) for o in others) > 0.5 * sq,
              min(poly_dist(mk.cut, o) for o in others) / sq)
        inner = [(x - W / 2, H / 2 - y) for x, y, _z in
                 ((p[0] * 1000.0, p[1] * 1000.0, 0) for p in B.charuco_corners(M.board_spec(spec)))]
        dmin = min(point_poly_dist(p, mk.cut) for p in inner)
        check(f"{name}, {mode}/{op}: {dmin / sq:.2f} square from the nearest ChArUco corner",
              dmin >= 1.0 * sq and close(mk.clear, dmin, 1e-6), (dmin, mk.clear))
        check(f"{name}, {mode}/{op}: the cut square drawn as a pentagon of the right area",
              close(abs(C.area2(mk.outline)) / 2.0, sq * sq - mk.leg ** 2 / 2.0, 1e-6))
mk = mark(CHLEG, "both", "new")
check("legacy, even rows (white top-left square): the TILE's corner, its marker as corner 0",
      mk.kind == "tile_cut" and close(mk.black, first_marker_corner0_tag(CHLEG)), (mk.kind, mk.black))
check("legacy: a 0.7-module cut in a 1-module inset (no quiet zone)",
      close(mk.leg, 0.7 * CHLEG.module_m * 1000.0)
      and min(poly_dist(mk.cut, rect_pts(r)) for r in cells_mm(CHLEG)) > 0.7 * CHLEG.module_m * 1000.0)

print("== the cut tool")
for name, spec in TAGS.items():
    for mode, op in (("both", "new"), ("black", None)):
        mk = mark(spec, mode, op)
        if not mk.is_cut:
            continue
        cu, cv = mk.corner
        s = [(p[0] - cu) + (cv - p[1]) for p in mk.tool]
        check(f"{name}, {mode}: the tool's long side IS the cut line",
              sorted(round(v / mk.leg, 9) for v in s)[1:] == [1.0, 1.0], s)
        check(f"{name}, {mode}: the tool covers the cut and pokes past both edges",
              all(inside(p, mk.tool) for p in mk.cut)
              and min(p[0] for p in mk.tool) < cu - 0.5 * mk.leg
              and max(p[1] for p in mk.tool) > cv + 0.5 * mk.leg)
        check(f"{name}, {mode}: two of its sides are axis-aligned (one slanted cut in Fusion)",
              sum(1 for i in range(3) if close(mk.tool[i][0], mk.tool[i - 1][0])
                  or close(mk.tool[i][1], mk.tool[i - 1][1])) == 2)

print("== tiles bigger than the sticker, rectangles, turns")
mk = C.plan_mark("single", "both", "new", (30.0, 30.0), 5.0, 1.0, tile_mm=(60.0, 40.0))
check("a 60 x 40 sketch-area tile: its own corner, 0.7 module", mk.kind == "tile_cut"
      and close(mk.corner, (-30.0, 20.0)) and close(mk.leg, 3.5), (mk.corner, mk.leg))
check("... farther from the black: more clearance",
      mk.clear > mark(SINGLE, "both", "new").clear)
check("... the tile drawn cut: a pentagon of the right area",
      close(abs(C.area2(mk.outline)) / 2.0, 60.0 * 40.0 - 3.5 ** 2 / 2.0))
for turn, want in ((0, (-30.0, 20.0)), (90, (-30.0, -20.0)), (180, (30.0, -20.0)),
                   (270, (30.0, 20.0))):
    t = (60.0, 40.0) if turn % 180 == 0 else (40.0, 60.0)
    mk = C.plan_mark("single", "both", "new", (30.0, 30.0), 5.0, 1.0, tile_mm=t)
    axes = turned(turn)
    got = world(mk.corner, axes)
    k0 = world((-15.0, 15.0), axes)
    check(f"square marker on a 60 x 40 tile, turn {turn}: the tile's world corner {want} "
          "nearest corner 0", close(got, want, 1e-9)
          and min(((x, y) for x in (-30.0, 30.0) for y in (-20.0, 20.0)),
                  key=lambda q: math.dist(q, k0)) == want, (got, k0))
for turn in (0, 180):
    mk = mark(GRID, "both", "new", turn)
    axes = turned(turn)
    W, H = (v * 1000.0 for v in GRID.tile_size_m)
    got = world(mk.corner, axes)
    check(f"3 x 1 grid turned {turn}: the cut follows the board origin to the "
          f"{'top-left' if turn == 0 else 'bottom-right'} tile corner",
          close(got, (-W / 2, H / 2) if turn == 0 else (W / 2, -H / 2), 1e-9), got)

print("== too narrow a white border")
for q in (0.0, 0.5):
    spec = M.TagSpec(marker_length_m=0.03, quiet_modules=q)
    for mode, op in MODES:
        mk = mark(spec, mode, op)
        check(f"single, quiet {q}, {mode}/{op}: no mark, and it says why",
              mk.kind == "none" and "no room" in mk.note, (mk.kind, mk.note))
for q in (1.0, 1.5, 2.0, 4.0):
    spec = M.TagSpec(marker_length_m=0.03, quiet_modules=q)
    m = spec.module_m * 1000.0
    mk = mark(spec, "both", "new")
    tri = mark(spec, "black", None)
    check(f"quiet {q}: tile cut 0.7 module, triangle 0.6, both clear",
          close(mk.leg, 0.7 * m) and close(tri.leg, 0.6 * m)
          and mk.clear >= 0.707 * m and tri.clear >= 0.9 * m)
mk = C.plan_mark("single", "both", "new", (30.0, 30.0), 5.0, 0.8)
check("quiet 0.8: the cut shrinks to 0.6 module to keep 0.707 module from the black",
      mk.kind == "tile_cut" and close(mk.leg, 0.6 * 5.0) and close(mk.clear, 5.0 / math.sqrt(2.0)),
      (mk.leg, mk.clear))
mk = C.plan_mark("single", "both", "new", (30.0, 30.0), 5.0, 0.9)
check("quiet 0.9: still the full 0.7 module (room 0.8)", close(mk.leg, 0.7 * 5.0), mk.leg)

print("== a copy (another id) carries the same mark")
for name, spec in TAGS.items():
    for mode, op in MODES:
        a = mark(spec, mode, op).to_dict(6)
        other = M.TagSpec(**{**spec.__dict__, "marker_id": spec.marker_id + 5})
        b = mark(other, mode, op).to_dict(6)
        check(f"{name}, {mode}/{op}: id {spec.marker_id} -> {other.marker_id}: same mark",
              C.same(a, b) and a["kind"] == b["kind"])
d = mark(SINGLE, "both", "new").to_dict(6)
back = C.from_dict(d)
check("record round trip", back.kind == "tile_cut" and close(back.tool, mark(SINGLE, "both", "new").tool,
                                                             1e-6))
check("same(): none == none == absent", C.same(None, {"kind": "none"}) and C.same({}, None))
check("same(): on != off", not C.same(d, None))
moved = dict(d, tool=[[u + 0.01, v] for u, v in d["tool"]])
check("same(): a moved cut is a change", not C.same(d, moved))
check("from_dict(none) is None", C.from_dict({"kind": "none"}) is None and C.from_dict(None) is None)

print("== locating pins keep clear of a cut corner")
for shape in ("triangle", "round"):
    a = S.pin_outlines(shape, 3.0, 40.0, 40.0)
    b = S.pin_outlines(shape, 3.0, 40.0, 40.0, cut=0.0)
    check(f"{shape}: no cut = as before", a == b)
for size, tw, th, cut in ((3.0, 40.0, 40.0, 3.5), (3.0, 40.0, 40.0, 5.0), (2.0, 26.7, 26.7, 3.5),
                          (4.0, 60.0, 40.0, 3.5)):
    c = (-tw / 2.0, th / 2.0)
    for shape in ("triangle", "round"):
        pins = S.pin_outlines(shape, size, tw, th, cut=cut)
        first = pins[0]
        if first["kind"] == "poly":
            d_line = min(((p[0] - c[0]) + (c[1] - p[1]) - cut) / math.sqrt(2.0) for p in first["pts"])
            d_edge = min(min(p[0] - c[0], c[1] - p[1]) for p in first["pts"])
        else:
            (u, v), r = first["c"], first["r"]
            d_line = ((u - c[0]) + (c[1] - v) - cut) / math.sqrt(2.0) - r
            d_edge = min(u - c[0], c[1] - v) - r
        check(f"{shape} {size} mm on {tw:g} x {th:g}, corner cut {cut}: >= 1 mm from the cut "
              f"and the edges ({d_line:.3f}, {d_edge:.3f})",
              d_line >= S.PIN_MARGIN_MM - 1e-9 and d_edge >= S.PIN_MARGIN_MM - 1e-9)
        if shape == "round":
            check(f"round on {tw:g}: the right pin stays where it was",
                  pins[1] == S.pin_outlines("round", size, tw, th)[1])
check("pin_inset agrees with aruco_slabs' copy",
      all(close(C.pin_inset(cut, 1.0, h), S._corner_inset(cut, 1.0, h))
          for cut in (0.0, 2.0, 3.5, 7.0) for h in (0.0, 1.5)))
raised = False
try:
    S.pin_outlines("triangle", 3.0, 10.0, 10.0, cut=3.5)
except ValueError as exc:
    raised = "corner cut" in str(exc)
check("a triangle pin with no room beside the cut is refused, saying why", raised)

print("== Corner mark: None / Cut white corner / Black triangle (2026-10-09)")
check("what each tag can have: a tile of its own both; any other white the triangle; a "
      "ChArUco board with a black top-left square its cut only",
      C.valid_marks("single", "both", "new") == ["cut", "triangle"]
      and C.valid_marks("grid", "both", "join") == ["triangle"]
      and C.valid_marks("single", "black", None) == ["triangle"]
      and C.valid_marks("single", "white", None) == ["triangle"]
      and C.valid_marks("charuco", "black", None, rows=3) == ["cut"]
      and C.valid_marks("charuco", "both", "new", rows=4, legacy=True) == ["cut", "triangle"])
check("choose: asked where it cannot be, the other form, with an info note",
      C.choose("cut", ["triangle"])[0] == "triangle"
      and C.choose("cut", ["triangle"])[1].startswith(C.INSTEAD)
      and C.choose("triangle", ["cut"])[0] == "cut"
      and C.choose("triangle", ["cut", "triangle"]) == ("triangle", "")
      and C.choose("none", ["cut", "triangle"]) == ("none", ""))
check("the old checkbox: 'chamfer': true is 'cut'",
      C.choice_of({"chamfer": True}) == "cut" and C.choice_of({"chamfer": False}) == "none"
      and C.choice_of({"corner_mark": "triangle", "chamfer": True}) == "triangle"
      and C.choice_of({"corner_mark": "bogus"}) == "none")
for name, spec in TAGS.items():
    for mode, op in MODES:
        mk_old = mark(spec, mode, op)                      # want="cut", the default
        t = tuple(v * 1000.0 for v in spec.tile_size_m) if mode == "both" else None
        mk_cut = C.mark_for_spec(spec, mode, op, t, want="cut")
        check(f"{name}, {mode}/{op}: 'cut' makes what the checkbox made",
              mk_cut.to_dict(6) == mk_old.to_dict(6))
for name, spec in TAGS.items():
    t = tuple(v * 1000.0 for v in spec.tile_size_m)
    mk = C.mark_for_spec(spec, "both", "new", t, want="triangle")
    if C.charuco_cut(spec.layout, spec.rows, spec.legacy):
        check(f"{name}: Black triangle on its tile -> the board's black square cut instead",
              mk.kind == "black_cut" and mk.instead.startswith(C.INSTEAD), mk.kind)
        continue
    m = spec.module_m * 1000.0
    tri = mk.solid
    blacks = [rect_pts(r) for r in cells_mm(spec)]
    W, H = (v * 1000.0 for v in spec.tile_size_m)
    check(f"{name}: Black triangle on a tile of its own -- a triangle, legs 0.6 module, in "
          "the quiet zone's top-left corner, the tile a plain rectangle (no cut)",
          mk.kind == "triangle" and close(mk.leg, 0.6 * m) and not mk.cut and not mk.instead
          and close(mk.corner, (-W / 2.0, H / 2.0)) and C.area2(tri) > 0
          and "plain rectangle" in mk.note, (mk.kind, mk.note))
    check(f"{name}: ... about a module clear of the black (>= 0.9), never on it",
          min(poly_dist(tri, b) for b in blacks) >= 0.9 * m - 1e-9,
          min(poly_dist(tri, b) for b in blacks) / m)
    for turn in turns_of(spec):
        mk_t = C.mark_for_spec(spec, "both", "new",
                               t if turn % 180 == 0 else (t[1], t[0]), want="triangle")
        k0 = corner0_tag(spec)
        Wd, Hd = (v * 1000.0 for v in spec.extent_m)
        corners = [(-Wd / 2, Hd / 2), (Wd / 2, Hd / 2), (Wd / 2, -Hd / 2), (-Wd / 2, -Hd / 2)]
        near = min(corners, key=lambda q: math.dist(q, mk_t.corner))
        near0 = min(corners, key=lambda q: math.dist(q, k0))
        check(f"{name} turn {turn}: the triangle at corner 0 / the board origin", near == near0)
check("'none' asks for nothing", C.mark_for_spec(SINGLE, "both", "new", want="none").kind == "none")

print("== stdlib only")
src = Path(C.__file__).read_text(encoding="utf-8")
imports = {line.split()[1].split(".")[0] for line in src.splitlines()
           if line.startswith(("import ", "from ")) and not line.startswith("from __future__")}
check("aruco_chamfer imports only the stdlib", imports <= {"math", "dataclasses", "typing"}, imports)

print()
print(f"{PASS} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
