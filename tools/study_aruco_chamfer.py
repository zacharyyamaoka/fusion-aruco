#!/usr/bin/env python3
"""Does a chamfered corner on a 3D-printed ArUco tag hurt OpenCV detection?
And can screws go in a ChArUco board's black squares? Measured, not guessed.

A sibling of tools/study_aruco_marks.py: it imports that study and reuses its
whole pipeline (vector PDF -> pdfium -> printer model -> pinhole camera ->
cv2.aruco), its stress conditions (same seeds, so the first 3000 single-marker
trials are photo for photo the marks study's stress mix) and its paired
statistics. What it adds:

  * candidates
      bch_<leg>    the BLACK square's top-left outer corner (OpenCV corner 0)
                   cut at 45 deg, legs 0.25 / 0.5 / 0.75 / 1.0 module. The cut
                   shows WHITE: in a 3D print the white tile fills it.
      tch_<leg>    the white TILE's top-left corner cut at 45 deg, legs 0.5 /
                   0.7 / 1.0 module; the cut shows the PART. Run on the mix of
                   part colours and again on a dark, a mid and a light part.
                   tch_070 is exactly the marks study's `chamfer_tl`.
      tri_tl, tri_tl_near, chamfer_tl   the marks study's references.
      g_*          a 3 x 1 GridBoard (separation 1.2 modules, quiet zone 1),
                   the chamfer on the board's top-left marker only.
      c5_* / s3_*  ChArUco: the marks study's 5 x 4 board (quiet 1) and a
                   3 x 3 board with no quiet zone (what aruco_autosize makes),
                   top-left element chamfered; and screw heads in the black
                   squares (sizes, placements, dark / bright, off-centre
                   towards an inner corner, one in a white square).
  * a third detector, CORNER_REFINE_CONTOUR, beside the default and SUBPIX;
  * corner 0's error in the MARKER PLANE (modules, signed), not only in px;
  * pose: single markers by solvePnP(SOLVEPNP_IPPE_SQUARE), boards by
    solvePnP (SOLVEPNP_ITERATIVE, what estimatePoseBoard / estimatePose-
    CharucoBoard called) on all detected corners, against the exact pose the
    simulated camera was given.

ORACLE-SIDE ONLY, same environment as the marks study (cv2 + numpy + pypdfium2):

    PYTHONPATH=<cv2 + numpy + pypdfium2> python tools/study_aruco_chamfer.py
    python tools/study_aruco_chamfer.py --quick          # ~1/10 of the trials
    python tools/study_aruco_chamfer.py --media-only     # redraw the images
    python tools/study_aruco_chamfer.py --only single --tag cv410   # cross-check

Outputs -> reports/media/aruco/chamfer/
    results.json            every number docs/proposal_aruco_chamfer.md quotes
    variants/<key>.svg|png  each design as drawn / as printed
    camera_*.png            simulated photos, green = detected, red = missed
    corner0_zoom.png        where each refinement puts corner 0
    outline_*.png           the outline OpenCV traces, and its polygon fit
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import multiprocessing as mp
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import study_aruco_marks as sam  # noqa: E402
from study_aruco_marks import D4, Design, P, C  # noqa: E402
from bam_cad_core import aruco_board, aruco_dict  # noqa: E402  (path set by sam)

OUT = sam.ROOT / "reports" / "media" / "aruco" / "chamfer"
MODULE_MM = sam.MODULE_MM            # 5 mm: a 30 mm DICT_4X4_50 marker
SQ_MM_S3 = 37.0                      # Zach's ChArUco square, mm (3 x 3 board)

# ===================================================================== designs


@dataclass
class CDesign(Design):
    grid: Optional[dict] = None      # GridBoard: cols, rows, L, sep (modules)


def black_cut(L: float, x0: float = 0.0, y0: float = 0.0, e: float = 0.2) -> dict:
    """White fill over a black square's TOP-LEFT corner at (x0, y0): the region
    x >= x0 - e, y >= y0 - e, (x - x0) + (y - y0) <= L. Inside the black it is
    exactly the 45 deg triangle with legs L; the e-overhang only repaints white
    quiet zone white, so no anti-aliased seam is left along the black edges."""
    return P([(x0 - e, y0 - e), (x0 + L + e, y0 - e), (x0 - e, y0 + L + e)], 1.0)


LEGS_BLACK = (0.25, 0.5, 0.75, 1.0)
LEGS_TILE = (0.5, 0.7, 1.0)
PART_BG = {"dark": 0.15, "mid": 0.45, "light": 0.85}     # the marks study's three part colours
GRID = dict(cols=3, rows=1, L=6.0, sep=1.2)              # 4x4, gap 0.2 L (DEFAULT_GRID_SEPARATION_RATIO)
CH5 = dict(sam.CH)                                       # 5 x 4, sq 8, mk 6 (modules), quiet 1
S3 = dict(cols=3, rows=3, sq=8.0, mk=6.0)                # Zach's 3 x 3, marker 0.75 of the square

SCREW_D = (0.15, 0.25, 0.35, 0.5)                        # head diameter, fraction of the square
SCREW_LOOK = {"d": 0.2, "b": 0.9}                        # dark (black screw / hole), bright metal
# off-centre heads, pushed along the diagonal towards inner corner 0: (look, diameter, edge-to-corner gaps), squares
OFF_SETS = (("b", 0.25, (0.06, 0.08, 0.1, 0.15, 0.2, 0.3)), ("d", 0.25, (0.06, 0.1)),
            ("b", 0.15, (0.035, 0.06, 0.1)), ("b", 0.5, (0.11, 0.2)))


def single_designs() -> Dict[str, CDesign]:
    d = [CDesign("bch_%03d" % round(L * 100), "black-chamfer",
                 "Black square's corner 0 cut at 45 deg, legs %.2f module (white tile shows)" % L, 1.0,
                 [black_cut(L)], human="corner 0, by the black square's shape")
         for L in LEGS_BLACK]
    d += [CDesign("tch_%03d" % round(L * 100), "tile-chamfer",
                  "White tile's top-left corner cut at 45 deg, legs %.2f module (the part shows)" % L, 1.0,
                  chamfer=L, human="corner 0, by the tile's shape")
          for L in LEGS_TILE]
    return {x.key: x for x in d}


def grid_designs() -> Dict[str, CDesign]:
    tri = P([(-1, -1), (-0.4, -1), (-1, -0.4)], 0.5)
    d = [CDesign("g_base", "grid", "GridBoard 3 x 1, plain, quiet zone 1", 1.0, grid=GRID, baseline="g_base"),
         CDesign("g_tri", "grid", "GridBoard + grey triangle at the board's top-left (tri_tl)", 1.0, [tri],
                 grid=GRID, baseline="g_base")]
    for L in (0.5, 0.75, 1.0):
        d.append(CDesign("g_bch_%03d" % round(L * 100), "grid",
                         "GridBoard, top-left marker's black corner 0 cut, legs %.2f" % L, 1.0, [black_cut(L)],
                         grid=GRID, baseline="g_base"))
    d.append(CDesign("g_tch_100", "grid", "GridBoard, tile's top-left corner cut, legs 1.0 (part shows)", 1.0,
                     chamfer=1.0, grid=GRID, baseline="g_base"))
    return {x.key: x for x in d}


def _spec(b: dict) -> aruco_board.BoardSpec:
    return aruco_board.BoardSpec(layout="charuco", dictionary=D4, first_id=0, marker_length_m=b["mk"],
                                 rows=b["rows"], cols=b["cols"], square_length_m=b["sq"])


def _black_squares(b: dict):
    return [(x, y) for y in range(b["rows"]) for x in range(b["cols"])
            if aruco_board._charuco_black(x, y, b["rows"], False)]


def charuco_designs() -> Dict[str, CDesign]:
    sq = S3["sq"]
    d = [CDesign("c5_bch_100", "charuco-chamfer", "5 x 4 (quiet 1): top-left BLACK square's outer corner cut, legs 1",
                 1.0, [black_cut(1.0)], board=CH5, baseline="ch_base"),
         CDesign("c5_bch_200", "charuco-chamfer", "5 x 4 (quiet 1): top-left BLACK square's outer corner cut, legs 2",
                 1.0, [black_cut(2.0)], board=CH5, baseline="ch_base"),
         CDesign("c5_tch_100", "charuco-chamfer", "5 x 4 (quiet 1): tile corner cut, legs 1 (part shows)",
                 1.0, chamfer=1.0, board=CH5, baseline="ch_base"),
         CDesign("s3_base", "screw", "3 x 3, no quiet zone (aruco_autosize), plain", 0.0, board=S3, baseline="s3_base"),
         CDesign("s3_bch_100", "charuco-chamfer", "3 x 3: top-left BLACK square's outer corner cut, legs 1 (white)",
                 0.0, [black_cut(1.0)], board=S3, baseline="s3_base"),
         CDesign("s3_bch_200", "charuco-chamfer", "3 x 3: top-left BLACK square's outer corner cut, legs 2 (white)",
                 0.0, [black_cut(2.0)], board=S3, baseline="s3_base"),
         CDesign("s3_tch_200", "charuco-chamfer", "3 x 3: tile corner cut, legs 2 (part shows)",
                 0.0, chamfer=2.0, board=S3, baseline="s3_base")]
    blacks = _black_squares(S3)
    corners = [(x, y) for x, y in blacks if x in (0, S3["cols"] - 1) and y in (0, S3["rows"] - 1)]
    for look, g in SCREW_LOOK.items():
        for place, sqs in (("c", corners), ("a", blacks)):
            for dia in SCREW_D:
                k = "s3_%s%s_%02d" % (look, place, round(dia * 100))
                d.append(CDesign(k, "screw", "%s head d %.2f sq, centred, in %s" % (
                    "dark" if look == "d" else "bright", dia, "the 4 corner squares" if place == "c" else
                    "every black square"), 0.0,
                    [C((x + 0.5) * sq, (y + 0.5) * sq, dia * sq / 2, g) for x, y in sqs],
                    board=S3, baseline="s3_base"))
    # off-centre: in the top-left black square, along its diagonal towards the
    # inner corner (sq, sq) = ChArUco corner id 0; edge `gap` squares from it
    # (the head stays inside its black square: corner gap >= (sqrt 2 - 1) r)
    for look, dia, gaps in OFF_SETS:
        for gp in gaps:
            r = dia / 2
            c = 1.0 - (r + gp) / math.sqrt(2.0)
            k = "s3_off_%s%02d_g%03d" % (look, round(dia * 100), round(gp * 1000))
            d.append(CDesign(k, "screw-off", "%s head d %.2f sq, off-centre: edge %.3f sq from inner corner 0" % (
                "dark" if look == "d" else "bright", dia, gp), 0.0, [C(c * sq, c * sq, r * sq, SCREW_LOOK[look])],
                board=S3, baseline="s3_base",
                note="centre %.3f sq in from the outer corner; %.3f sq from the square's two inner edges"
                     % (c, 1.0 - c - r)))
    # contrast: a dark head in a WHITE square, in the margin under marker 0
    mid, mx, my = aruco_board._placements(_spec(S3))[0]
    mk = S3["mk"]
    d.append(CDesign("s3_white_d10", "screw-white",
                     "dark head d 0.10 sq in a WHITE square: the margin under marker %d, centred" % mid, 0.0,
                     [C(mx + mk / 2, my + mk + (S3["sq"] - mk) / 4, 0.1 * sq / 2, SCREW_LOOK["d"])],
                     board=S3, baseline="s3_base"))
    return {x.key: x for x in d}


SINGLE_KEYS = ["base_q1"] + list(single_designs()) + ["tri_tl", "tri_tl_near"]
TILE_KEYS = ["base_q1", "tch_050", "tch_070", "tch_100"]
GRID_KEYS = list(grid_designs())
CH5_KEYS = ["ch_base", "c5_bch_100", "c5_bch_200", "c5_tch_100"]


def s3_keys() -> List[str]:
    return list(k for k in charuco_designs() if k.startswith("s3_"))


def register():
    """Put the new designs into the marks study's registry, and teach its
    geometry about grid boards and about ChArUco boards other than its 5 x 4."""
    reg = sam.designs_for(D4)
    reg.update(single_designs())
    reg.update(grid_designs())
    reg.update(charuco_designs())
    if getattr(sam, "_chamfer_patched", False):
        return
    orig_extent, orig_ink = sam.extent_modules, sam.ink_rects

    def extent_modules(d, dictionary):
        g = getattr(d, "grid", None)
        if g:
            return (g["cols"] * g["L"] + (g["cols"] - 1) * g["sep"], g["rows"] * g["L"] + (g["rows"] - 1) * g["sep"])
        return orig_extent(d, dictionary)

    def ink_rects(d, dictionary, mid):
        g = getattr(d, "grid", None)
        if g:
            out = []
            for k, (x, y) in enumerate(grid_cells(g)):
                out += [(x + a, y + b, float(w), float(h)) for a, b, w, h in aruco_dict.black_rects(dictionary, mid + k)]
            return out
        if d.board:
            W, H = extent_modules(d, dictionary)
            return [(x0 + W / 2, H / 2 - y1, x1 - x0, y1 - y0)
                    for x0, y0, x1, y1 in aruco_board.black_rects_m(_spec(d.board))]
        return orig_ink(d, dictionary, mid)

    def binom_two_sided(k, n):
        """The marks study's exact McNemar p, in integer arithmetic: its float
        2.0 ** n overflows once more than 1023 trials disagree."""
        if n == 0:
            return 1.0
        k = min(k, n - k)
        return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / (1 << n))

    sam.extent_modules, sam.ink_rects, sam.binom_two_sided = extent_modules, ink_rects, binom_two_sided
    sam._chamfer_patched = True


def grid_cells(g: dict):
    pitch = g["L"] + g["sep"]
    return [(x * pitch, y * pitch) for y in range(g["rows"]) for x in range(g["cols"])]


register()   # module level: spawned workers re-import this file and need it too


def cmaster(key: str, mid: int) -> dict:
    """sam.master, with the ground-truth corners right for grids and any ChArUco size."""
    m = sam.master(key, D4, mid)
    d = sam.designs_for(D4)[key]
    g = m["geom"]
    if g.get("fixed"):
        return m
    S, ox, oy = g["S"], g["ox"], g["oy"]
    if getattr(d, "grid", None):
        L = d.grid["L"]
        pts = []
        for x, y in grid_cells(d.grid):
            pts += [[ox + x * S, oy + y * S], [ox + (x + L) * S, oy + y * S],
                    [ox + (x + L) * S, oy + (y + L) * S], [ox + x * S, oy + (y + L) * S]]
        g["corners"] = np.array(pts, np.float64)
    elif d.board:
        sp = _spec(d.board)
        g["corners"] = np.array([[ox + x * S, oy + y * S] for x, y, _ in aruco_board.charuco_corners(sp)], np.float64)
    g["fixed"] = True
    return m


# ===================================================================== pose truth


def pose_truth(geom: dict, cond: dict, Hc: np.ndarray):
    """(K, R, t) of the simulated camera. sam.camera builds Hc = K @ [r0 r1 t] @ A
    with A: master px -> modules about geom['centre'] (y down), t = (0, 0, D);
    so K = Hc A^-1 [r0 r1 t]^-1, exactly."""
    S = geom["S"]
    cx, cy = geom["centre"]
    D = 5.0 * geom["extent_mod"]
    R = sam._rot((0, 0, 1), math.radians(cond["roll"])) @ sam._rot(
        (math.cos(math.radians(cond["axis"])), math.sin(math.radians(cond["axis"])), 0), math.radians(cond["tilt"]))
    A = np.array([[1.0 / S, 0, -cx / S], [0, 1.0 / S, -cy / S], [0, 0, 1]])
    Pm = np.column_stack([R[:, 0], R[:, 1], np.array([0, 0, D])])
    K = Hc @ np.linalg.inv(A) @ np.linalg.inv(Pm)
    K = K / K[2, 2]
    return K, R, np.array([0.0, 0.0, D])


def rot_err_deg(Ra, Rb) -> float:
    c = (np.trace(Ra.T @ Rb) - 1.0) / 2.0
    return float(math.degrees(math.acos(max(-1.0, min(1.0, c)))))


def plane(Hinv, pts, geom):
    """image points -> marker-plane modules, origin the black extent's top-left, y down."""
    p = cv2.perspectiveTransform(np.asarray(pts, np.float64).reshape(1, -1, 2), Hinv)[0]
    return (p - np.array([geom["ox"], geom["oy"]])) / geom["S"]


# ===================================================================== detection

REFINE = (("none", 0), ("subpix", 1), ("contour", 2))
_DET3: Dict[str, tuple] = {}


def detectors3(dictionary=D4):
    if dictionary not in _DET3:
        dd = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, dictionary))
        out = []
        for meth in (cv2.aruco.CORNER_REFINE_NONE, cv2.aruco.CORNER_REFINE_SUBPIX, cv2.aruco.CORNER_REFINE_CONTOUR):
            p = cv2.aruco.DetectorParameters()
            p.cornerRefinementMethod = meth
            out.append(cv2.aruco.ArucoDetector(dd, p))
        _DET3[dictionary] = tuple(out)
    return _DET3[dictionary]


def _detect(det, img):
    """detectMarkers -> (corners, ids as a list, 1 if OpenCV raised). cv2 4.10's
    CORNER_REFINE_CONTOUR can assert in _interpolate2Dline on some candidates;
    an application calling it unguarded would get that exception, so it is
    counted, and the photo scored as not detected."""
    try:
        corners, ids, _ = det.detectMarkers(img)
    except cv2.error:
        return [], [], 1
    return corners, ([] if ids is None else [int(v) for v in np.asarray(ids).reshape(-1)]), 0


# single marker, per detector: det wrong_id flip e0 e1 e2 e3(px) c0x c0y(plane mod) t_err(mod) r_err(deg)
NS = 11
OBJ_SQ = None


def obj_square(M: float) -> np.ndarray:
    h = M / 2.0
    return np.array([[-h, h, 0], [h, h, 0], [h, -h, 0], [-h, -h, 0]], np.float64)


def run_single(args) -> np.ndarray:
    key, dictionary, conds = args
    cv2.setNumThreads(1)
    dets = detectors3(dictionary)
    out = np.full((len(conds), NS * 3 + 3), np.nan)   # + one 'detector raised' flag per refinement
    order = sorted(range(len(conds)), key=lambda i: (conds[i]["id"], conds[i]["dg"], conds[i]["ht"], conds[i]["bg"]))
    M = aruco_dict.dictionary_info(dictionary)["modules"]
    obj = obj_square(M)
    flipyz = np.diag([1.0, -1.0, -1.0])
    for i in order:
        cd = conds[i]
        m = sam.master(key, dictionary, cd["id"])
        geom = m["geom"]
        refl = sam.printed(m, cd["dg"], cd["ht"], cd["bg"], (key, dictionary, cd["id"], cd["dg"], cd["ht"], cd["bg"]))
        img, Hc = sam.camera(refl, geom, cd)
        gt = sam.project(Hc, geom["corners"])
        K, R, t = pose_truth(geom, cd, Hc)
        Rcv = R @ flipyz
        Hinv = np.linalg.inv(Hc)
        for j, det in enumerate(dets):
            corners, ids, raised = _detect(det, img)
            out[i, NS * 3 + j] = raised
            hits = [np.asarray(c, np.float64).reshape(4, 2) for c, v in zip(corners, ids) if v == cd["id"]]
            wrong = sum(1 for v in ids if v != cd["id"]) + max(0, len(hits) - 1)
            base = j * NS
            out[i, base:base + 3] = [0, wrong, 0]
            if not hits:
                continue
            q = min(hits, key=lambda c: float(np.linalg.norm(c - gt, axis=1).mean()))
            errs = [float(np.linalg.norm(np.roll(q, -k, axis=0) - gt, axis=1).mean()) for k in range(4)]
            flip = int(int(np.argmin(errs)) != 0)
            pl = plane(Hinv, q, geom)
            ok, rv, tv = cv2.solvePnP(obj, q, K, None, flags=cv2.SOLVEPNP_IPPE_SQUARE)
            te = re = np.nan
            if ok:
                Re, _ = cv2.Rodrigues(rv)
                te = float(np.linalg.norm(tv.reshape(3) - t))
                re = rot_err_deg(Re, Rcv)
            out[i, base:base + NS] = [1, wrong, flip, *np.linalg.norm(q - gt, axis=1), pl[0, 0], pl[0, 1], te, re]
    return out


# grid, per detector: n_found m0_found wrong flip c0x c0y t_err r_err
NG = 8


def run_grid(args) -> np.ndarray:
    key, dictionary, conds = args
    cv2.setNumThreads(1)
    dets = detectors3(dictionary)
    out = np.full((len(conds), NG * 3 + 3), np.nan)   # + one 'detector raised' flag per refinement
    order = sorted(range(len(conds)), key=lambda i: (conds[i]["id"], conds[i]["dg"], conds[i]["ht"], conds[i]["bg"]))
    d = sam.designs_for(dictionary)[key]
    ncell = d.grid["cols"] * d.grid["rows"]
    for i in order:
        cd = conds[i]
        first = min(cd["id"], 49 - ncell + 1)
        m = cmaster(key, first)
        geom = m["geom"]
        refl = sam.printed(m, cd["dg"], cd["ht"], cd["bg"], (key, dictionary, first, cd["dg"], cd["ht"], cd["bg"]))
        img, Hc = sam.camera(refl, geom, cd)
        gt = sam.project(Hc, geom["corners"]).reshape(ncell, 4, 2)
        K, R, t = pose_truth(geom, cd, Hc)
        Hinv = np.linalg.inv(Hc)
        S, (cx, cy) = geom["S"], geom["centre"]
        objall = ((geom["corners"] - [cx, cy]) / S).reshape(ncell, 4, 2)
        for j, det in enumerate(dets):
            corners, ids, raised = _detect(det, img)
            out[i, NG * 3 + j] = raised
            seen, wrong, flip = {}, 0, 0
            for c, v in zip(corners, ids):
                k = v - first
                if not 0 <= k < ncell:
                    wrong += 1
                    continue
                q = np.asarray(c, np.float64).reshape(4, 2)
                if k in seen:
                    wrong += 1
                    continue
                errs = [float(np.linalg.norm(np.roll(q, -r, axis=0) - gt[k], axis=1).mean()) for r in range(4)]
                if int(np.argmin(errs)) != 0:
                    flip += 1
                seen[k] = q
            base = j * NG
            out[i, base:base + 4] = [len(seen), int(0 in seen), wrong, flip]
            if 0 in seen:
                out[i, base + 4:base + 6] = plane(Hinv, seen[0], geom)[0]
            if seen:
                ks = sorted(seen)
                ip = np.vstack([seen[k] for k in ks])
                op = np.hstack([np.vstack([objall[k] for k in ks]), np.zeros((4 * len(ks), 1))])
                ok, rv, tv = _pnp(op, ip, K)
                if ok:
                    Re, _ = cv2.Rodrigues(rv)
                    out[i, base + 6] = float(np.linalg.norm(tv.reshape(3) - t))
                    out[i, base + 7] = rot_err_deg(Re, R)
    return out


def _pnp(obj, img, K):
    try:
        return cv2.solvePnP(obj, img, K, None, flags=cv2.SOLVEPNP_ITERATIVE)
    except cv2.error:
        return False, None, None


# ChArUco: markers corners rms_px max_px wrong_m wrong_c e_id0_px t_err r_err phantom_id
# (cols 0-5 = sam.run_charuco's; wrong_m = marker ids not on the board, phantom_id = the largest of them)
NC = 10
_CHD: Dict[tuple, object] = {}


def charuco_detector(b: dict):
    k = (b["cols"], b["rows"], b["sq"], b["mk"])
    if k not in _CHD:
        dd = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        board = cv2.aruco.CharucoBoard((b["cols"], b["rows"]), b["sq"], b["mk"], dd)
        _CHD[k] = cv2.aruco.CharucoDetector(board)
    return _CHD[k]


def run_charuco(args) -> np.ndarray:
    key, dictionary, conds = args
    cv2.setNumThreads(1)
    d = sam.designs_for(D4)[key]
    det = charuco_detector(d.board)
    n_ids = aruco_board.marker_count(_spec(d.board))
    out = np.full((len(conds), NC), np.nan)
    order = sorted(range(len(conds)), key=lambda i: (conds[i]["dg"], conds[i]["bg"]))
    for i in order:
        cd = dict(conds[i], ht=False)
        m = cmaster(key, 0)
        geom = m["geom"]
        refl = sam.printed(m, cd["dg"], False, cd["bg"], (key, D4, 0, cd["dg"], False, cd["bg"]))
        img, Hc = sam.camera(refl, geom, cd)
        gt = sam.project(Hc, geom["corners"])
        K, R, t = pose_truth(geom, cd, Hc)
        S, (cx, cy) = geom["S"], geom["centre"]
        obj = np.hstack([(geom["corners"] - [cx, cy]) / S, np.zeros((len(geom["corners"]), 1))])
        cc, cids, mc, mids = det.detectBoard(img)
        mids = [] if mids is None else [int(v) for v in np.asarray(mids).reshape(-1)]
        wrong_m = sum(1 for v in mids if v >= n_ids)
        if wrong_m:
            out[i, 9] = max(v for v in mids if v >= n_ids)
        if cids is None or len(cids) == 0:
            out[i, :6] = [len(mids), 0, np.nan, np.nan, wrong_m, 0]
            continue
        cids = np.asarray(cids).reshape(-1)
        cc = np.asarray(cc, np.float64).reshape(-1, 2)
        e = np.linalg.norm(cc - gt[cids], axis=1)
        bad = e > cd["ppm"] * 1.0
        good = e[~bad]
        out[i, :6] = [len(mids), len(cids), float(np.sqrt(np.mean(good ** 2))) if len(good) else np.nan,
                      float(good.max()) if len(good) else np.nan, wrong_m, int(bad.sum())]
        if 0 in set(cids.tolist()):
            out[i, 6] = float(e[list(cids).index(0)])
        # as estimatePoseCharucoBoard: >= 4 corners, and not all on one line
        if len(cids) >= 4 and np.linalg.matrix_rank(obj[cids, :2] - obj[cids, :2].mean(axis=0), tol=1e-6) == 2:
            ok, rv, tv = _pnp(obj[cids], cc, K)
            if ok:
                Re, _ = cv2.Rodrigues(rv)
                out[i, 7] = float(np.linalg.norm(tv.reshape(3) - t))
                out[i, 8] = rot_err_deg(Re, R)
    return out


# ===================================================================== statistics


def pc(b: np.ndarray, v: np.ndarray, scale: float = 1.0, nd: int = 4) -> dict:
    """Paired continuous comparison on the trials where both have a value."""
    sel = np.isfinite(b) & np.isfinite(v)
    n = int(sel.sum())
    if n < 2:
        return {"n": n}
    bb, vv = b[sel] * scale, v[sel] * scale
    dd = vv - bb
    m, se = float(dd.mean()), float(dd.std(ddof=1) / math.sqrt(n))
    r = lambda x: round(float(x), nd)
    return {"n": n, "base_mean": r(bb.mean()), "mean": r(vv.mean()), "base_median": r(np.median(bb)),
            "median": r(np.median(vv)), "base_p95": r(np.percentile(bb, 95)), "p95": r(np.percentile(vv, 95)),
            "diff_mean": r(m), "ci95": [r(m - 1.96 * se), r(m + 1.96 * se)]}


def cells_of(conds):
    dgv = np.array([c["dg"] for c in conds])
    htv = np.array([c["ht"] for c in conds])
    return {"%s%d" % ("h" if ht else "c", dg): (dgv == dg) & (htv == ht) for dg in range(4) for ht in (False, True)}


def summ_single(res, conds, keys, baseline="base_q1") -> dict:
    out = {}
    cells = cells_of(conds)
    bgv = np.array([c["bg"] for c in conds])
    ppm = np.array([c["ppm"] for c in conds])
    b = res[baseline]
    for k in keys:
        v = res[k]
        row = {"baseline": baseline, "det": {}, "corner0": {}, "pose": {}}
        for name, j in REFINE:
            o = j * NS
            row["det"][name] = sam.paired(b, v, o)
            both = (b[:, o] > 0.5) & (v[:, o] > 0.5)
            # marker-plane corner-0 error, modules (|e|, and its component along the diagonal into the marker)
            eb = np.where(both, np.hypot(b[:, o + 7], b[:, o + 8]), np.nan)
            ev = np.where(both, np.hypot(v[:, o + 7], v[:, o + 8]), np.nan)
            diag_b = np.where(both, (b[:, o + 7] + b[:, o + 8]) / math.sqrt(2), np.nan)
            diag_v = np.where(both, (v[:, o + 7] + v[:, o + 8]) / math.sqrt(2), np.nan)
            c123b = np.where(both, b[:, o + 4:o + 7].mean(axis=1) / ppm, np.nan)
            c123v = np.where(both, v[:, o + 4:o + 7].mean(axis=1) / ppm, np.nan)
            row["corner0"][name] = {"abs_mod": pc(eb, ev), "diag_mod": pc(diag_b, diag_v),
                                    "px_per_ppm_c0": pc(np.where(both, b[:, o + 3] / ppm, np.nan),
                                                        np.where(both, v[:, o + 3] / ppm, np.nan)),
                                    "px_per_ppm_c123": pc(c123b, c123v)}
            tb = np.where(both, b[:, o + 9], np.nan)
            tv = np.where(both, v[:, o + 9], np.nan)
            rb = np.where(both, b[:, o + 10], np.nan)
            rv = np.where(both, v[:, o + 10], np.nan)
            row["pose"][name] = {"t_mod": pc(tb, tv), "r_deg": pc(rb, rv, nd=3),
                                 "r_over5_base": int(np.nansum(rb > 5)), "r_over5": int(np.nansum(rv > 5))}
        row["wrong_id"] = {n_: int(np.nansum(v[:, j * NS + 1])) for n_, j in REFINE}
        row["flipped"] = {n_: int(np.nansum(v[:, j * NS + 2])) for n_, j in REFINE}
        row["detector_raised"] = {n_: int(np.nansum(v[:, NS * 3 + j])) for n_, j in REFINE}
        row["base_detector_raised"] = {n_: int(np.nansum(b[:, NS * 3 + j])) for n_, j in REFINE}
        row["by_print_cell"] = {c: sam.paired(b[s], v[s], 0) for c, s in cells.items()}
        worst = min(row["by_print_cell"].items(), key=lambda kv: kv[1]["delta"])
        row["worst_cell"] = {"cell": worst[0], **worst[1]}
        row["by_bg"] = {str(g): sam.paired(b[bgv == g], v[bgv == g], 0) for g in sorted(set(bgv.tolist()))}
        out[k] = row
    return out


def summ_grid(res, conds, keys, baseline="g_base") -> dict:
    out = {}
    cells = cells_of(conds)
    b = res[baseline]
    for k in keys:
        v = res[k]
        row = {"baseline": baseline}
        for name, j in REFINE:
            o = j * NG
            m0b, m0v = b[:, o + 1:o + 2] > 0.5, v[:, o + 1:o + 2] > 0.5
            anyb, anyv = (b[:, o:o + 1] > 0.5), (v[:, o:o + 1] > 0.5)
            all3b, all3v = (b[:, o:o + 1] > 2.5), (v[:, o:o + 1] > 2.5)
            both0 = m0b[:, 0] & m0v[:, 0]
            eb = np.where(both0, np.hypot(b[:, o + 4], b[:, o + 5]), np.nan)
            ev = np.where(both0, np.hypot(v[:, o + 4], v[:, o + 5]), np.nan)
            row[name] = {
                "board": sam.paired(anyb.astype(float), anyv.astype(float), 0),
                "marker0": sam.paired(m0b.astype(float), m0v.astype(float), 0),
                "all3": sam.paired(all3b.astype(float), all3v.astype(float), 0),
                "markers_mean": round(float(v[:, o].mean()), 4), "base_markers_mean": round(float(b[:, o].mean()), 4),
                "corner0_abs_mod": pc(eb, ev),
                "t_mod": pc(b[:, o + 6], v[:, o + 6]), "r_deg": pc(b[:, o + 7], v[:, o + 7], nd=3),
                "t_mod_m0both": pc(np.where(both0, b[:, o + 6], np.nan), np.where(both0, v[:, o + 6], np.nan)),
                "r_deg_m0both": pc(np.where(both0, b[:, o + 7], np.nan), np.where(both0, v[:, o + 7], np.nan), nd=3),
                "wrong": int(np.nansum(v[:, o + 2])), "flipped": int(np.nansum(v[:, o + 3])),
                "detector_raised": int(np.nansum(v[:, NG * 3 + j])), "base_detector_raised": int(np.nansum(b[:, NG * 3 + j]))}
        bm0, vm0 = (b[:, 1:2] > 0.5).astype(float), (v[:, 1:2] > 0.5).astype(float)
        row["marker0_by_print_cell"] = {c: sam.paired(bm0[s], vm0[s], 0) for c, s in cells.items()}
        out[k] = row
    return out


def summ_charuco(res, conds, keys, sq_mm: float, mod_per_sq: float) -> dict:
    out = {}
    ppm = np.array([c["ppm"] for c in conds])
    mm_per_mod = sq_mm / mod_per_sq
    for k in keys:
        d = sam.designs_for(D4)[k]
        b, v = res[d.baseline], res[k]
        row = sam._charuco_paired(b, v, conds)
        ph = v[:, 9][np.isfinite(v[:, 9])].astype(int)
        row.update({"wrong_markers": int(np.nansum(v[:, 4])), "base_wrong_markers": int(np.nansum(b[:, 4])),
                    "wrong_corners": int(np.nansum(v[:, 5])), "base_wrong_corners": int(np.nansum(b[:, 5])),
                    "phantom_ids": {str(k): int(n) for k, n in zip(*np.unique(ph, return_counts=True))}})
        row.update({"title": d.title, "baseline": d.baseline,
                    "corners_max": (d.board["cols"] - 1) * (d.board["rows"] - 1),
                    "rms_err_mod": pc(b[:, 2] / ppm, v[:, 2] / ppm),
                    "id0_err_mod": pc(b[:, 6] / ppm, v[:, 6] / ppm),
                    "pose_rate": round(float(np.isfinite(v[:, 7]).mean()), 4),
                    "base_pose_rate": round(float(np.isfinite(b[:, 7]).mean()), 4),
                    "t_mod": pc(b[:, 7], v[:, 7]), "r_deg": pc(b[:, 8], v[:, 8], nd=3),
                    "mm_per_module": round(mm_per_mod, 4)})
        all_b = b[:, 1] >= row["corners_max"]
        all_v = v[:, 1] >= row["corners_max"]
        row["all_corners"] = sam.paired(all_b[:, None].astype(float), all_v[:, None].astype(float), 0)
        out[k] = row
    return out


# ===================================================================== experiments


def exp_single(pool, n, seed=101) -> dict:
    conds = sam.stress_conditions(n, seed, sam.IDS[D4])
    res = sam.run_design_set(pool, SINGLE_KEYS, D4, conds, fn=run_single, size=500)
    out = {"n": n, "seed": seed, "summary": summ_single(res, conds, SINGLE_KEYS[1:])}
    # the marks study's own 3000 trials are the first 3000 of these: its numbers, reproduced
    if n >= 3000:
        sub = [c for c in conds[:3000]]
        r3 = {k: v[:3000] for k, v in res.items()}
        out["repro_first3000"] = {k: sam.paired(r3["base_q1"], r3[k], 0) for k in ("tri_tl", "tri_tl_near", "tch_070")}
        out["repro_first3000"]["base_rate"] = round(float(r3["base_q1"][:, 0].mean()), 4)
        del sub
    return out, res, conds


def exp_tile_on_part(pool, n, seed=101) -> dict:
    out = {}
    base = sam.stress_conditions(n, seed, sam.IDS[D4])
    for name, g in PART_BG.items():
        conds = [dict(c, bg=g) for c in base]
        res = sam.run_design_set(pool, TILE_KEYS, D4, conds, fn=run_single, size=500)
        out[name] = {"bg": g, "n": n, "summary": summ_single(res, conds, TILE_KEYS[1:]),
                     "base_rate": round(float(res["base_q1"][:, 0].mean()), 4)}
        print("   tile chamfer on a %s part done" % name, flush=True)
    return out


CLEAN = (("6 px/mod, flat", dict(ppm=6.0)), ("12 px/mod, flat", dict(ppm=12.0)), ("20 px/mod, flat", dict(ppm=20.0)),
         ("8 px/mod, 35 deg", dict(ppm=8.0, tilt=35.0)))


def exp_clean(pool, n, seed=404) -> dict:
    """Systematic corner-0 bias, no noise / spread (the marks study's clean check), plus a tilted set."""
    out = {}
    keys = ["base_q1"] + [k for k in single_designs()] + ["tri_tl", "tri_tl_near"]
    for vi, (label, over) in enumerate(CLEAN):
        conds = sam.clean_conditions(over["ppm"], n, seed + 31 * vi, sam.IDS[D4])
        for c in conds:
            c.update(over)
        res = sam.run_design_set(pool, keys, D4, conds, fn=run_single, size=200)
        rows = {}
        for k in keys:
            v = res[k]
            r = {"rate": round(float(v[:, 0].mean()), 4)}
            for name, j in REFINE:
                o = j * NS
                ok = v[:, o] > 0.5
                if ok.any():
                    ex, ey = v[ok, o + 7], v[ok, o + 8]
                    r[name] = {"c0_abs_mod": round(float(np.hypot(ex, ey).mean()), 4),
                               "c0_dx_mod": round(float(ex.mean()), 4), "c0_dy_mod": round(float(ey.mean()), 4),
                               "t_mod_median": round(float(np.nanmedian(v[ok, o + 9])), 4),
                               "r_deg_median": round(float(np.nanmedian(v[ok, o + 10])), 3)}
            rows[k] = r
        out[label] = {"n": n, "designs": rows}
    return out


WHY_KEYS = ["base_q1", "bch_025", "bch_050", "bch_075", "bch_100"]


def run_why(args) -> np.ndarray:
    """Per trial: 0 = detected, 1 = a 4-sided candidate on the marker was found but
    rejected when its bits were read, 2 = no 4-sided candidate (the outline OpenCV
    traced did not reduce to a convex quadrilateral). Default detector."""
    key, dictionary, conds = args
    cv2.setNumThreads(1)
    det = detectors3(dictionary)[0]
    out = np.zeros(len(conds))
    for i, cd in enumerate(conds):
        m = sam.master(key, dictionary, cd["id"])
        refl = sam.printed(m, cd["dg"], cd["ht"], cd["bg"], (key, dictionary, cd["id"], cd["dg"], cd["ht"], cd["bg"]))
        img, Hc = sam.camera(refl, m["geom"], cd)
        gt = sam.project(Hc, m["geom"]["corners"])
        _, ids, rej = det.detectMarkers(img)
        if ids is not None and cd["id"] in np.asarray(ids).reshape(-1).tolist():
            continue
        side = float(np.linalg.norm(gt[1] - gt[0]))
        near = any(np.linalg.norm(np.asarray(r).reshape(4, 2).mean(axis=0) - gt.mean(axis=0)) < 0.5 * side
                   for r in rej)
        out[i] = 1 if near else 2
    return out[:, None]


def exp_why(pool, n, seed=101) -> dict:
    """Why a black-corner chamfer is missed: the outline, or the bits?"""
    conds = sam.stress_conditions(n, seed, sam.IDS[D4])
    res = sam.run_design_set(pool, WHY_KEYS, D4, conds, fn=run_why, size=500)
    return {"n": n, "seed": seed, "designs": {k: {"detected": int(np.sum(v == 0)), "quad_rejected_at_bits": int(np.sum(v == 1)),
                                                  "no_quad": int(np.sum(v == 2))} for k, v in res.items()}}


def exp_grid(pool, n, seed=801) -> dict:
    conds = sam.stress_conditions(n, seed, sam.IDS[D4])
    res = sam.run_design_set(pool, GRID_KEYS, D4, conds, fn=run_grid, size=250)
    return {"n": n, "seed": seed, "board": GRID, "summary": summ_grid(res, conds, GRID_KEYS[1:])}


def exp_charuco(pool, n5, n3, seed=510) -> dict:
    out = {}
    conds = sam.stress_conditions(n5, seed, [0], ppm=(2.5, 8.0))
    res = sam.run_design_set(pool, CH5_KEYS, D4, conds, fn=run_charuco, size=100)
    out["c5"] = {"n": n5, "board": CH5, "quiet": 1.0,
                 "summary": summ_charuco(res, conds, CH5_KEYS, sq_mm=8 * MODULE_MM, mod_per_sq=8.0)}
    print("   ChArUco 5 x 4 done", flush=True)
    keys = s3_keys()
    conds = sam.stress_conditions(n3, seed + 1, [0], ppm=(2.5, 8.0))
    res = sam.run_design_set(pool, keys, D4, conds, fn=run_charuco, size=150)
    out["s3"] = {"n": n3, "board": S3, "quiet": 0.0, "square_mm": SQ_MM_S3,
                 "summary": summ_charuco(res, conds, keys, sq_mm=SQ_MM_S3, mod_per_sq=S3["sq"])}
    # where on the 3 x 3 board corner losses happen: by part colour for the plain board
    bgv = np.array([c["bg"] for c in conds])
    out["s3"]["base_corners_by_bg"] = {str(g): round(float(res["s3_base"][bgv == g, 1].mean()), 3)
                                       for g in sorted(set(bgv.tolist()))}
    print("   ChArUco 3 x 3 (screws) done", flush=True)
    return out


# ===================================================================== media


def _cond(**over):
    c = dict(sam.NOMINAL, id=23, axis=30.0, roll=12.0, dx=0.3, dy=0.6, gdir=40.0, seed=5, tilt=0.0, bg=0.45)
    c.update(over)
    return c


def _photo(key, c, mid=23):
    m = cmaster(key, mid)
    refl = sam.printed(m, c["dg"], c["ht"], c["bg"], ("media", key, mid, c["dg"], c["ht"], c["bg"]))
    img, Hc = sam.camera(refl, m["geom"], c)
    return img, Hc, m["geom"]


def _label(img, text, h=22):
    bar = np.full((h, img.shape[1], 3), 255, np.uint8)
    cv2.putText(bar, text, (4, h - 7), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (40, 40, 40), 1, cv2.LINE_AA)
    return np.vstack([bar, img])


def _frame(img, ok, pad=4):
    col = (60, 170, 60) if ok else (50, 50, 215)
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    img = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=col)
    return cv2.copyMakeBorder(img, 3, 3, 3, 3, cv2.BORDER_CONSTANT, value=(255, 255, 255))


def _hstack(tiles):
    h = max(t.shape[0] for t in tiles)
    return np.hstack([cv2.copyMakeBorder(t, 0, h - t.shape[0], 0, 0, cv2.BORDER_CONSTANT, value=(255, 255, 255))
                      for t in tiles])


def _vstack(rows):
    w = max(r.shape[1] for r in rows)
    return np.vstack([cv2.copyMakeBorder(r, 0, 0, 0, w - r.shape[1], cv2.BORDER_CONSTANT, value=(255, 255, 255))
                      for r in rows])


def save_variants(keys_bg) -> dict:
    vdir = OUT / "variants"
    vdir.mkdir(parents=True, exist_ok=True)
    info = {}
    for key, bg in keys_bg:
        d = sam.designs_for(D4)[key]
        mid = 0 if (d.board or getattr(d, "grid", None)) else 23
        name = key + ("" if bg is None else "_" + bg)
        g = PART_BG.get(bg, 0.55)
        v = int(round(g / sam.PAPER * 255))
        (vdir / ("%s.svg" % name)).write_text(sam.design_svg(d, D4, mid, bg="#%02x%02x%02x" % (v, v, v)),
                                               encoding="utf-8")
        m = cmaster(key, mid)
        refl = sam.printed(m, 0, False, g, ("thumb", name))
        img = np.clip(refl / sam.PAPER * 255.0, 0, 255).astype(np.uint8)
        h, w = img.shape
        sc = 360.0 / max(h, w)
        cv2.imwrite(str(vdir / ("%s.png" % name)), cv2.resize(img, (int(w * sc), int(h * sc)),
                                                               interpolation=cv2.INTER_AREA))
        info[name] = {"svg": "variants/%s.svg" % name, "png": "variants/%s.png" % name}
    return info


SINGLE_COLS = [("plain", "base_q1", None), ("black 0.25", "bch_025", None), ("black 0.5", "bch_050", None),
               ("black 0.75", "bch_075", None), ("black 1.0", "bch_100", None),
               ("tile 0.7 dark", "tch_070", "dark"), ("tile 1.0 dark", "tch_100", "dark"),
               ("tile 1.0 light", "tch_100", "light"), ("grey tri", "tri_tl", None)]
PHOTO_ROWS = [("8 px/module, 30 deg tilt, light print", dict(ppm=8.0, tilt=30.0, dg=1, ht=True)),
              ("4.5 px/module, 25 deg, blur 1 px, spread 0.08", dict(ppm=4.5, tilt=25.0, blur=1.0, dg=2, ht=True))]


def _seen(key, c, bg=None) -> bool:
    c = dict(c)
    if bg:
        c["bg"] = PART_BG[bg]
    img, _, _ = _photo(key, c)
    _, ids, _ = detectors3()[0].detectMarkers(img)
    return ids is not None and 23 in np.asarray(ids).reshape(-1).tolist()


def _typical_hard_row():
    """A hard condition (deterministic search, the camera roll kept so corner 0
    stays top-left) whose outcome matches the measured pattern: the plain marker,
    the 0.25 black cut, the tile cuts and the triangle seen; the 1.0 black cut missed.
    Single photos near the detection limit flip either way; this row is picked to
    show the typical case, not a lucky or an unlucky one."""
    for cand in sam.stress_conditions(3000, 1234, [23], ppm=(3.0, 5.0)):
        c = _cond(**{k: cand[k] for k in ("ppm", "tilt", "blur", "noise", "jpeg", "grad", "expo", "dg", "ht", "seed")})
        must = [("base_q1", None), ("bch_025", None), ("tch_070", "dark"), ("tch_100", "dark"), ("tch_100", "light"),
                ("tri_tl", None)]
        if not _seen("bch_100", c) and all(_seen(k, c, b) for k, b in must):
            label = "%.1f px/module, %d deg, blur %.1f, spread %.2f%s%s" % (
                c["ppm"], round(c["tilt"]), c["blur"], sam.SPREAD[c["dg"]], ", halftone" if c["ht"] else "",
                ", JPEG %d" % c["jpeg"] if c["jpeg"] else "")
            return label, c
    return None


def camera_single(path) -> dict:
    rows, table = [], []
    plan = [(lbl, _cond(**over)) for lbl, over in PHOTO_ROWS]
    hard = _typical_hard_row()
    if hard:
        plan.append(hard)
    for rlabel, base_c in plan:
        line, oks = [], []
        for clabel, key, bg in SINGLE_COLS:
            c = dict(base_c)
            if bg:
                c["bg"] = PART_BG[bg]
            img, Hc, geom = _photo(key, c)
            gt = sam.project(Hc, geom["corners"])
            ok = _seen(key, c)
            oks.append(bool(ok))
            side = float(np.linalg.norm(gt[1] - gt[0]))
            cx, cy = gt.mean(axis=0)
            r = int(math.ceil(side * 0.95))
            x0, y0 = max(0, int(cx) - r), max(0, int(cy) - r)
            crop = img[y0:int(cy) + r, x0:int(cx) + r]
            t = _frame(cv2.resize(crop, (150, 150), interpolation=cv2.INTER_NEAREST), ok)
            line.append(_label(t, clabel) if not rows else t)
        rows.append(_hstack(line))
        table.append(oks)
    cv2.imwrite(str(path), _vstack(rows))
    return {"rows": [r[0] for r in plan], "cols": [c[0] for c in SINGLE_COLS], "detected": table}


def corner0_zoom(path) -> dict:
    """Corner 0 up close at 12 px/module, 20 deg: truth (white ring) and where the
    default (red), SUBPIX (green) and CONTOUR (blue) detectors put it."""
    dets = detectors3()
    cols = [("plain", "base_q1", None), ("black 0.25", "bch_025", None), ("black 0.5", "bch_050", None),
            ("black 0.75", "bch_075", None), ("black 1.0", "bch_100", None), ("tile 1.0 dark", "tch_100", "dark")]
    c = _cond(ppm=12.0, tilt=20.0, blur=0.5, noise=2.0, dg=1, ht=True)
    tiles, info = [], {}
    z = 9
    for clabel, key, bg in cols:
        cc = dict(c)
        if bg:
            cc["bg"] = PART_BG[bg]
        img, Hc, geom = _photo(key, cc)
        gt = sam.project(Hc, geom["corners"])
        g0 = gt[0]
        r = int(round(1.6 * cc["ppm"]))
        x0, y0 = int(round(g0[0])) - r, int(round(g0[1])) - r
        crop = img[max(0, y0):y0 + 2 * r + 1, max(0, x0):x0 + 2 * r + 1]
        big = cv2.cvtColor(cv2.resize(crop, None, fx=z, fy=z, interpolation=cv2.INTER_NEAREST), cv2.COLOR_GRAY2BGR)

        def to(p):
            return int(round((p[0] - x0 + 0.5) * z)), int(round((p[1] - y0 + 0.5) * z))
        cv2.circle(big, to(g0), 9, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.circle(big, to(g0), 9, (255, 255, 255), 2, cv2.LINE_AA)
        got = {}
        for (name, j), col in zip(REFINE, ((40, 40, 230), (40, 170, 40), (220, 120, 30))):
            corners, ids, _ = dets[j].detectMarkers(img)
            ids = [] if ids is None else np.asarray(ids).reshape(-1).tolist()
            if 23 in ids:
                q = np.asarray(corners[ids.index(23)], np.float64).reshape(4, 2)
                p = to(q[0])
                cv2.drawMarker(big, p, col, cv2.MARKER_TILTED_CROSS if j == 0 else
                               (cv2.MARKER_CROSS if j == 1 else cv2.MARKER_DIAMOND), 18, 3, cv2.LINE_AA)
                pl = plane(np.linalg.inv(Hc), q, geom)[0]
                got[name] = [round(float(pl[0]), 3), round(float(pl[1]), 3)]
            else:
                got[name] = None
        info[clabel] = got
        tiles.append(_label(_frame(big, got["none"] is not None), clabel))
    cv2.imwrite(str(path), _hstack(tiles))
    return {"condition": "12 px/module, tilt 20, blur 0.5, noise 2, spread 0.04, halftone",
            "legend": "white ring = true corner 0; red x = default; green + = SUBPIX; blue diamond = CONTOUR",
            "corner0_plane_modules": info}


def outline_fig(path) -> dict:
    """OpenCV's first step on one photo: adaptive threshold (window 13, C 7, the
    detector's middle pass), the outer outline of the blob around the marker, and
    approxPolyDP at 3 % of the outline's length (green = 4 vertices, red = not)."""
    cols = [("plain", "base_q1"), ("black 0.5", "bch_050"), ("black 0.75", "bch_075"), ("black 1.0", "bch_100")]
    c = None
    for cand in sam.stress_conditions(800, 909, [23], ppm=(5.0, 8.0)):   # deterministic search, as sam.mechanism
        cand.update(ht=False, jpeg=0, noise=min(cand["noise"], 3.0), tilt=min(cand["tilt"], 30.0), dg=0)
        if (_outline_vertices("bch_100", cand) not in (0, 4) and _outline_vertices("base_q1", cand) == 4
                and _outline_vertices("bch_050", cand) == 4):
            c = cand
            break
    if c is None:
        c = _cond(ppm=6.0, tilt=10.0, blur=0.6, noise=2.0, dg=0, ht=True)
    tiles, info = [], {}
    z = 6
    for clabel, key in cols:
        img, Hc, geom = _photo(key, c)
        gt = sam.project(Hc, geom["corners"])
        cx, cy = gt.mean(axis=0)
        x0, y0 = int(gt[:, 0].min()) - 8, int(gt[:, 1].min()) - 8
        x1, y1 = int(gt[:, 0].max()) + 9, int(gt[:, 1].max()) + 9
        th = cv2.adaptiveThreshold(img, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 13, 7)
        cons, _ = cv2.findContours(th, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
        area = cv2.contourArea(gt.astype(np.float32))
        best = None
        for cn in cons:
            if cv2.pointPolygonTest(cn, (float(cx), float(cy)), False) >= 0:
                a = cv2.contourArea(cn)
                if a > 0.5 * area and (best is None or a < cv2.contourArea(best)):
                    best = cn
        a_img = cv2.cvtColor(cv2.resize(img[y0:y1, x0:x1], None, fx=z, fy=z, interpolation=cv2.INTER_NEAREST),
                             cv2.COLOR_GRAY2BGR)
        b_img = cv2.cvtColor(cv2.resize(255 - th[y0:y1, x0:x1], None, fx=z, fy=z, interpolation=cv2.INTER_NEAREST),
                             cv2.COLOR_GRAY2BGR)
        nv = 0
        if best is not None:
            ap = cv2.approxPolyDP(best, len(best) * 0.03, True)
            nv = len(ap)
            quad = nv == 4 and cv2.isContourConvex(ap)
            col = (60, 170, 60) if quad else (50, 50, 215)
            pts_ = ((best.reshape(-1, 2) - [x0, y0]) * z + z // 2).astype(np.int32)
            cv2.polylines(b_img, [pts_], True, col, 2)
            for q in ((ap.reshape(-1, 2) - [x0, y0]) * z + z // 2).astype(np.int32):
                cv2.circle(b_img, (int(q[0]), int(q[1])), 7, col, -1)
        _, ids, _ = detectors3()[0].detectMarkers(img)
        ok = ids is not None and 23 in np.asarray(ids).reshape(-1).tolist()
        info[clabel] = {"vertices": int(nv), "detected": bool(ok)}
        tiles.append(_label(_frame(np.vstack([a_img, b_img]), ok), "%s: %d vertices" % (clabel, nv)))
    cv2.imwrite(str(path), _hstack(tiles))
    return {"condition": {k: round(float(c[k]), 3) for k in ("ppm", "tilt", "blur", "noise", "bg", "grad", "dg")},
            "designs": info}


def _outline_vertices(key, c) -> int:
    img, Hc, geom = _photo(key, c)
    gt = sam.project(Hc, geom["corners"])
    cx, cy = gt.mean(axis=0)
    th = cv2.adaptiveThreshold(img, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 13, 7)
    cons, _ = cv2.findContours(th, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    area = cv2.contourArea(gt.astype(np.float32))
    best = None
    for cn in cons:
        if cv2.pointPolygonTest(cn, (float(cx), float(cy)), False) >= 0:
            a = cv2.contourArea(cn)
            if a > 0.5 * area and (best is None or a < cv2.contourArea(best)):
                best = cn
    return 0 if best is None else len(cv2.approxPolyDP(best, len(best) * 0.03, True))


def camera_boards(path_grid, path_s3) -> dict:
    out = {}
    c = _cond(ppm=5.0, tilt=25.0, blur=0.6, dg=1, ht=True, bg=PART_BG["dark"])
    dets = detectors3()
    tiles = []
    for clabel, key in (("grid plain", "g_base"), ("grid, black 0.75", "g_bch_075"), ("grid, tile 1.0", "g_tch_100")):
        img, Hc, geom = _photo(key, c, mid=0)
        gt = sam.project(Hc, geom["corners"])
        _, ids, _ = dets[0].detectMarkers(img)
        n = 0 if ids is None else len(set(np.asarray(ids).reshape(-1).tolist()) & {0, 1, 2})
        x0, y0 = [max(0, int(v) - 14) for v in gt.min(axis=0)]
        x1, y1 = [int(v) + 15 for v in gt.max(axis=0)]
        crop = img[y0:y1, x0:x1]
        big = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
        tiles.append(_label(_frame(big, n == 3), "%s: %d/3 markers" % (clabel, n)))
        out[clabel] = n
    cv2.imwrite(str(path_grid), _vstack(tiles))
    c = _cond(ppm=4.0, tilt=25.0, blur=0.6, dg=1, ht=False, bg=PART_BG["dark"])
    tiles, s3 = [], {}
    for clabel, key in (("3x3 plain", "s3_base"), ("bright d0.25, all black", "s3_ba_25"),
                        ("bright d0.5, all black", "s3_ba_50"), ("dark d0.35, all black", "s3_da_35"),
                        ("bright d0.25, 0.06 sq from corner", "s3_off_b25_g060"),
                        ("dark d0.10 in a WHITE square", "s3_white_d10")):
        d = sam.designs_for(D4)[key]
        det = charuco_detector(d.board)
        img, Hc, geom = _photo(key, dict(c, ht=False), mid=0)
        gt = sam.project(Hc, geom["corners"])
        cc, cids, _, _ = det.detectBoard(img)
        n = 0 if cids is None else len(np.asarray(cids).reshape(-1))
        allp = np.vstack([sam.project(Hc, np.array([[geom["ox"] + u * geom["S"], geom["oy"] + v * geom["S"]]
                                                    for u in (0, geom["W"]) for v in (0, geom["H"])]))])
        x0, y0 = [max(0, int(v) - 10) for v in allp.min(axis=0)]
        x1, y1 = [int(v) + 11 for v in allp.max(axis=0)]
        big = cv2.cvtColor(cv2.resize(img[y0:y1, x0:x1], None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST),
                           cv2.COLOR_GRAY2BGR)
        if cids is not None:
            for p in np.asarray(cc).reshape(-1, 2):
                cv2.circle(big, (int(round((p[0] - x0 + 0.5) * 2)), int(round((p[1] - y0 + 0.5) * 2))), 5,
                           (0, 200, 255), 2, cv2.LINE_AA)
        tiles.append(_label(_frame(big, n == 4), "%s: %d/4" % (clabel, n)))
        s3[clabel] = n
    half = (len(tiles) + 1) // 2
    cv2.imwrite(str(path_s3), _vstack([_hstack(tiles[:half]), _hstack(tiles[half:])]))
    out["s3"] = s3
    return out


def screw_zoom(path) -> dict:
    """ChArUco corner id 0 (the inner corner of the top-left black square) up close,
    10 px/module, 20 deg: truth (white ring) and the corner CharucoDetector reports
    (yellow cross), for screw heads ever closer to it."""
    cols = [("plain", "s3_base"), ("bright d0.35 centred", "s3_ba_35"), ("bright d0.5 centred", "s3_ba_50"),
            ("bright d0.25, 0.3 sq", "s3_off_b25_g300"), ("bright d0.25, 0.2 sq", "s3_off_b25_g200"),
            ("bright d0.25, 0.1 sq", "s3_off_b25_g100"), ("dark d0.25, 0.06 sq", "s3_off_d25_g060")]
    c = _cond(ppm=10.0, tilt=20.0, blur=0.5, noise=2.0, dg=1, ht=False, bg=PART_BG["dark"], roll=8.0)
    tiles, info = [], {}
    z = 5
    for clabel, key in cols:
        d = sam.designs_for(D4)[key]
        img, Hc, geom = _photo(key, c, mid=0)
        gt = sam.project(Hc, geom["corners"])
        g0 = gt[0]
        cc, cids, _, _ = charuco_detector(d.board).detectBoard(img)
        r = int(round(4.2 * c["ppm"]))
        x0, y0 = int(round(g0[0])) - r, int(round(g0[1])) - r
        crop = img[max(0, y0):y0 + 2 * r + 1, max(0, x0):x0 + 2 * r + 1]
        big = cv2.cvtColor(cv2.resize(crop, None, fx=z, fy=z, interpolation=cv2.INTER_NEAREST), cv2.COLOR_GRAY2BGR)

        def to(p_):
            return int(round((p_[0] - x0 + 0.5) * z)), int(round((p_[1] - y0 + 0.5) * z))
        cv2.circle(big, to(g0), 8, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.circle(big, to(g0), 8, (255, 255, 255), 2, cv2.LINE_AA)
        err = None
        if cids is not None and 0 in np.asarray(cids).reshape(-1).tolist():
            q = np.asarray(cc).reshape(-1, 2)[np.asarray(cids).reshape(-1).tolist().index(0)]
            cv2.drawMarker(big, to(q), (0, 200, 255), cv2.MARKER_CROSS, 22, 3, cv2.LINE_AA)
            err = round(float(np.linalg.norm(plane(np.linalg.inv(Hc), q[None], geom)[0] - [8.0, 8.0])), 3)
        info[clabel] = err
        tiles.append(_label(_frame(big, err is not None),
                            "%s: %s" % (clabel, "missed" if err is None else "%.2f mm" % (err * SQ_MM_S3 / S3["sq"]))))
    cv2.imwrite(str(path), _vstack([_hstack(tiles[:4]), _hstack(tiles[4:])]))
    return {"condition": "10 px/module, tilt 20, blur 0.5, dark part", "legend": "white ring = true corner id 0; "
            "yellow + = CharucoDetector's corner", "id0_err_modules": info, "square_mm": SQ_MM_S3}


def media() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    keys_bg = [("base_q1", None)] + [(k, None) for k in single_designs() if k.startswith("bch")] + \
        [(k, b) for k in single_designs() if k.startswith("tch") for b in ("dark", "light")] + \
        [("tri_tl", None), ("g_base", "dark"), ("g_bch_075", "dark"), ("g_tch_100", "dark"),
         ("c5_bch_200", None), ("s3_base", "dark"), ("s3_bch_200", "dark"), ("s3_ba_25", "dark"),
         ("s3_ba_50", "dark"), ("s3_da_35", "dark"), ("s3_off_b25_g060", "dark"), ("s3_off_b25_g100", "dark"),
         ("s3_white_d10", "dark")]
    out = {"variants": save_variants(keys_bg)}
    out["camera_single"] = camera_single(OUT / "camera_single.png")
    out["corner0_zoom"] = corner0_zoom(OUT / "corner0_zoom.png")
    out["outline"] = outline_fig(OUT / "outline_black_chamfer.png")
    out["camera_boards"] = camera_boards(OUT / "camera_grid.png", OUT / "camera_charuco_screws.png")
    out["screw_zoom"] = screw_zoom(OUT / "screw_corner_zoom.png")
    return out


# ===================================================================== main


def jsonable(o):
    return sam.jsonable(o)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--quick", action="store_true", help="~1/10 of the trials (smoke run)")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--media-only", action="store_true", help="redraw the images only")
    ap.add_argument("--only", choices=["single", "tile", "clean", "grid", "charuco", "why"], action="append",
                    help="run only these experiments (repeatable)")
    ap.add_argument("--tag", default="", help="write results_<tag>.json instead of results.json")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    rpath = OUT / ("results%s.json" % ("_" + args.tag if args.tag else ""))
    if args.media_only:
        R = json.loads(rpath.read_text(encoding="utf-8")) if rpath.exists() else {}
        R["media"] = media()
        rpath.write_text(json.dumps(jsonable(R), indent=1) + "\n", encoding="utf-8")
        print("media redrawn into %s" % OUT)
        return
    q = 0.1 if args.quick else 1.0

    def N(x):
        return max(16, int(round(x * q)))
    want = set(args.only or ["single", "tile", "clean", "grid", "charuco", "why"])
    R = json.loads(rpath.read_text(encoding="utf-8")) if (args.only and rpath.exists()) else {}
    t0 = time.time()
    R.update({"generated": datetime.datetime.now().isoformat(timespec="seconds"), "quick": bool(args.quick),
              "opencv": cv2.__version__, "numpy": np.__version__, "module_mm": MODULE_MM,
              "model": "tools/study_aruco_marks.py pipeline (see its results.json 'model'); detectors: default, "
                       "CORNER_REFINE_SUBPIX, CORNER_REFINE_CONTOUR; pose: IPPE_SQUARE (single), ITERATIVE (boards)"})
    R.setdefault("designs", {})
    for k, d in sam.designs_for(D4).items():
        if k in SINGLE_KEYS or k in GRID_KEYS or k in CH5_KEYS or k.startswith("s3_"):
            R["designs"][k] = {"family": d.family, "title": d.title, "quiet": d.quiet, "chamfer": d.chamfer,
                               "baseline": d.baseline, "note": d.note, "marks": d.marks[:6]}

    exps = ("single", "tile_on_part", "clean", "grid", "why", "charuco")
    mark = [0]

    def save(exp=None):                      # checkpoint after every experiment
        if exp:                              # photos this experiment rendered (each through 3 detectors)
            R[exp]["photos"] = sam.TRIALS[0] - mark[0]
            mark[0] = sam.TRIALS[0]
        R["trials"] = sum(R[e].get("photos", 0) for e in exps if e in R)   # photos in the whole file
        rpath.write_text(json.dumps(jsonable(R), indent=1) + "\n", encoding="utf-8")

    pool = mp.Pool(args.workers) if args.workers > 1 else None
    try:
        if "single" in want:
            print("single marker, stress mix: %d designs x %d" % (len(SINGLE_KEYS), N(6000)), flush=True)
            R["single"], _, _ = exp_single(pool, N(6000))
            save("single")
            print("   done (%.0f s)" % (time.time() - t0), flush=True)
        if "tile" in want:
            print("tile chamfer on dark / mid / light parts: 3 x %d designs x %d" % (len(TILE_KEYS), N(6000)),
                  flush=True)
            R["tile_on_part"] = exp_tile_on_part(pool, N(6000))
            save("tile_on_part")
        if "clean" in want:
            print("clean corner bias", flush=True)
            R["clean"] = exp_clean(pool, N(200))
            save("clean")
        if "grid" in want:
            print("GridBoard 3 x 1: %d designs x %d" % (len(GRID_KEYS), N(3000)), flush=True)
            R["grid"] = exp_grid(pool, N(3000))
            save("grid")
            print("   done (%.0f s)" % (time.time() - t0), flush=True)
        if "why" in want:
            print("why a black chamfer is missed: %d designs x %d" % (len(WHY_KEYS), N(6000)), flush=True)
            R["why"] = exp_why(pool, N(6000))
            save("why")
        if "charuco" in want:
            print("ChArUco: 5 x 4 x %d, 3 x 3 (%d designs) x %d" % (N(2000), len(s3_keys()), N(3000)), flush=True)
            R["charuco"] = exp_charuco(pool, N(2000), N(3000))
            save("charuco")
    finally:
        if pool:
            pool.close()
            pool.join()
    R.pop("elapsed_s", None)
    R["elapsed_s_last_run"] = round(time.time() - t0, 1)
    save()
    if not args.tag:
        print("media", flush=True)
        R["media"] = media()
        save()
    print("wrote %s  (%.0f s, %d photos this run, %d in the file)" % (rpath, time.time() - t0, sam.TRIALS[0],
                                                                        R["trials"]))


if __name__ == "__main__":
    main()
