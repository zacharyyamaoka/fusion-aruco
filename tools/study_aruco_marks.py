#!/usr/bin/env python3
"""Where to put a human-readable ID and an orientation mark on a printed ArUco
marker so the detector does not notice -- measured with OpenCV, not guessed.

ORACLE-SIDE ONLY -- needs cv2 (with cv2.aruco), numpy, pypdfium2, matplotlib:

    PYTHONPATH=<dir with cv2+numpy+pypdfium2> python tools/study_aruco_marks.py   # ~10 min on 11 workers
    python tools/study_aruco_marks.py --quick          # smoke run, ~1/10 trials
    python tools/study_aruco_marks.py --media-only     # redraw figures into an existing results.json
    python tools/study_aruco_marks.py --only label     # rerun just the "more text in the black" experiment

What happens
  1. DESIGNS. Each candidate (ID text in the white quiet zone, in the black
     border, in a label strip below the quiet zone, corner triangles, arrows,
     a chamfered cut, white dots in the border, and combinations) is a list of
     primitives in MODULE units (origin = the black square's top-left, y down)
     plus a cut outline. Nothing else about the marker changes: same pattern,
     same black square, same marker_length.
  2. PRINT. Each design becomes a one-page vector PDF -- the same Helvetica,
     the same `g` grey operator and the same hand-written PDF the print sheet
     (bam_cad_core.print_sheet) emits -- and pdfium rasterises it at 64 px per
     module: the "printed sticker". A printer model then adds dot gain (ink
     spreading 0-0.12 module plus mid-tone gain), optionally a clustered-dot
     halftone (what a laser printer does to grey), paper white 0.88 and toner
     black 0.05 reflectance, and the cut: outside the cut outline is the part
     the sticker sits on (white paper, mid grey or dark).
  3. CAMERA. A pinhole camera looks at the sticker: px per module at the
     marker centre, tilt up to 75 deg about a random in-plane axis, random
     roll and sub-pixel phase, rendered 4x supersampled and box-filtered (the
     sensor), then optics blur, a lighting gradient and exposure, Gaussian
     noise, JPEG; the crop is embedded in a >= 320 x 240 frame so OpenCV's
     size-relative defaults (minMarkerPerimeterRate) act as in a real image.
  4. DETECT. cv2.aruco.ArucoDetector with DEFAULT DetectorParameters, and the
     same with CORNER_REFINE_SUBPIX, on the same image. Per trial: was the
     true id found, any other id reported (wrong id), corner order right
     (wrong pose), and corner error vs the projected ground truth.
     ChArUco boards go through cv2.aruco.CharucoDetector (defaults).
  5. Every design sees the SAME list of random conditions (paired design), so
     "variant lost k trials the plain marker passed" is exact, not a
     difference of two noisy rates.

Outputs -> reports/media/aruco/marks/
  results.json             every number the proposal / report quotes
  variants/<key>.svg|png   each design as drawn (svg = prims, png = the pdfium raster)
  sweep_ppm.png, stress_forest.png, dose_white_black.png, charuco.png   plots
  camera_examples.png      what the detector saw, one fixed condition per row
  mechanism.png            one trial: camera image, adaptive threshold, outer outline

Experiments (all paired, seeded): the stress mix (every curated design, also
split by print condition), a white-vs-black grid at matched contrast, a
clearance sweep (ink towards an edge / a corner), one-factor sweeps
(resolution at two ink spreads, tilt, blur, noise, JPEG, light, print), a
clean corner-bias check, ChArUco boards, and a repeat on DICT_6X6_250 and
DICT_APRILTAG_36h11. docs/build_aruco_marks_report.py turns results.json
into reports/aruco/marks-proposals.html.
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
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
import pypdfium2 as pdfium

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "CADExporter"))
from bam_cad_core import aruco_board, aruco_dict  # noqa: E402
from bam_cad_core.print_sheet import MM_TO_PT, pdf_string, text_width_mm  # noqa: E402

OUT = ROOT / "reports" / "media" / "aruco" / "marks"

D4, D6, A36 = "DICT_4X4_50", "DICT_6X6_250", "DICT_APRILTAG_36h11"
IDS = {D4: [0, 3, 7, 17, 23, 31, 42, 49],
       D6: [0, 23, 77, 123, 200, 249],
       A36: [0, 5, 77, 300, 586]}
MODULE_MM = 5.0          # 30 mm 4x4 marker; text sizes below are in modules, so this only sets the PDF scale
S_SINGLE = 64            # master raster, px per module (single markers)
S_BOARD = 32             # master raster, px per module (ChArUco)
BGM = 1.5                # part surface rasterised around the cut outline, modules
FRAME_PAD = 4.0          # camera frame: half-side = half the pattern + this many modules
SS = 4                   # camera supersampling
SENSOR = (320, 240)      # smallest camera frame (w, h) the marker crop is embedded in
CAP = 0.718              # Helvetica cap height / em
PAPER, INK = 0.88, 0.05  # reflectance of paper white and toner black
SPREAD = (0.0, 0.04, 0.08, 0.12)      # dot gain levels: ink spread, modules
TONE = (0.0, 0.06, 0.12, 0.18)        # dot gain levels: mid-tone gain (contone)
SCREEN = (np.array([[12, 5, 6, 13], [4, 0, 1, 7], [11, 3, 2, 8], [15, 10, 9, 14]],
                   np.float32) + 0.5) / 16.0     # 4x4 clustered-dot halftone threshold

# ===================================================================== designs


def T(s, cx, cy, cap, g, rot=0.0, maxw=None):
    """Text, centred on (cx, cy) = the middle of its cap-height box."""
    return {"k": "text", "s": s, "c": (cx, cy), "cap": cap, "g": g, "rot": rot, "maxw": maxw}


def P(pts, g):
    return {"k": "poly", "pts": [tuple(p) for p in pts], "g": g}


def C(cx, cy, r, g):
    return {"k": "circle", "c": (cx, cy), "r": r, "g": g}


@dataclass
class Design:
    key: str
    family: str             # base | white | black | outside | orient | combo | dose-white | dose-black | charuco
    title: str
    quiet: float = 1.0
    marks: list = field(default_factory=list)
    chamfer: float = 0.0    # cut the top-left corner of the outline at 45 deg, legs in modules
    strip: float = 0.0      # extra label strip below the quiet zone, inside the cut, modules
    sheet: bool = False     # uncut sheet: paper everywhere, grey cut line drawn
    labels_below: list = field(default_factory=list)   # sheet: text under the cut line
    baseline: str = "base_q1"
    note: str = ""
    board: Optional[dict] = None   # charuco: cols, rows, sq, mk (modules)
    human: str = ""         # what a person reads off it


def single_designs(dictionary: str) -> Dict[str, Design]:
    """The curated candidates, for any dictionary (positions scale with M)."""
    M = aruco_dict.dictionary_info(dictionary)["modules"]
    c = M / 2.0
    ID = "#{id}"
    LONG = "#{id}  BASE:1 TOP"
    inner = M - 2.4                     # text span available inside the bottom border (corner cells kept clear)
    tri_tl = P([(-1, -1), (-0.4, -1), (-1, -0.4)], 0.5)
    d = [
        Design("base_q1", "base", "Plain marker, quiet zone 1 module (today)", 1.0, human="nothing"),
        Design("base_q2", "base", "Plain marker, quiet zone 2 modules", 2.0, baseline="base_q2", human="nothing"),
        Design("sheet_plain", "base", "Uncut sheet, no label (paper all round, grey cut line)", 1.0, sheet=True,
               baseline="sheet_plain", human="nothing"),
        Design("sheet_label", "base", "Today's sheet before cutting: label under the cut line", 1.0, sheet=True,
               labels_below=[("%s %s" % (dictionary, ID), 0.0)], human="id + dictionary, lost when cut",
               baseline="sheet_plain",
               note="black 6 pt-equivalent text 0.16 module below the cut line, grey 0.55 cut line; white paper all round"),
        # ---- ID text in the WHITE quiet zone
        Design("white_q1_id", "white", "ID in the quiet zone, grey 0.6, cap 0.5 module", 1.0,
               [T(ID, c, M + 0.5, 0.5, 0.6, maxw=M + 1.6)], human="id; upright text = which way is up"),
        Design("white_q1_black", "white", "ID in the quiet zone, BLACK, cap 0.5", 1.0,
               [T(ID, c, M + 0.5, 0.5, 0.0, maxw=M + 1.6)], human="id"),
        Design("white_q1_light", "white", "ID in the quiet zone, light grey 0.8, cap 0.5", 1.0,
               [T(ID, c, M + 0.5, 0.5, 0.8, maxw=M + 1.6)], human="id (faint)"),
        Design("white_q1_long", "white", "Long label in the quiet zone (id + part), grey 0.6, cap 0.5", 1.0,
               [T(LONG, c, M + 0.5, 0.5, 0.6, maxw=M + 1.0)], human="id + where it goes"),
        Design("white_q1_big", "white", "ID in the quiet zone, cap 0.7 (0.15 gaps)", 1.0,
               [T(ID, c, M + 0.5, 0.7, 0.6, maxw=M + 1.6)], human="id, larger"),
        Design("white_q1_touch", "white", "ID in the quiet zone, cap 0.8, 0.1 module from the border", 1.0,
               [T(ID, c, M + 0.5, 0.8, 0.6, maxw=M + 1.6)], human="id, largest", note="anti-example"),
        Design("white_q1_corner", "white", "ID in the bottom-right corner square of the quiet zone", 1.0,
               [T("{id}", M + 0.5, M + 0.5, 0.45, 0.6, maxw=0.85)], human="id (tiny)"),
        Design("white_q15_id", "white", "Quiet zone 1.5: ID cap 0.5, 0.75 module below the border", 1.5,
               [T(ID, c, M + 1.0, 0.5, 0.6, maxw=M + 2.6)], baseline="base_q1",
               human="id; sticker 1 module larger", note="baseline is the q=1 sticker: what the extra white buys"),
        Design("white_q2_id", "white", "Quiet zone 2: ID cap 0.8, centred (0.6 gaps)", 2.0,
               [T(ID, c, M + 1.0, 0.8, 0.6, maxw=M + 3.0)], baseline="base_q2", human="id, large"),
        Design("white_q2_out", "white", "Quiet zone 2: ID cap 0.8 near the cut (1.0 from the border)", 2.0,
               [T(ID, c, M + 1.4, 0.8, 0.6, maxw=M + 3.0)], baseline="base_q2", human="id, large"),
        # ---- ID text inside the BLACK border (bottom border row, corner cells clear)
        Design("black_id_white", "black", "ID knocked out WHITE in the bottom border, cap 0.5", 1.0,
               [T(ID, c, M - 0.5, 0.5, 1.0, maxw=inner)], human="id"),
        Design("black_id_grey", "black", "ID in the bottom border, grey 0.4 (same contrast as white_q1_id)", 1.0,
               [T(ID, c, M - 0.5, 0.5, 0.4, maxw=inner)], human="id"),
        Design("black_id_faint", "black", "ID in the bottom border, dark grey 0.2", 1.0,
               [T(ID, c, M - 0.5, 0.5, 0.2, maxw=inner)], human="id (faint)"),
        Design("black_id_inner", "black", "ID knocked out white, cap 0.5, nudged 0.1 module towards the inside", 1.0,
               [T(ID, c, M - 0.6, 0.5, 1.0, maxw=inner)], human="id",
               note="0.35 module of black kept along the outer edge, 0.15 along the inner"),
        Design("black_id_big", "black", "ID knocked out white in the bottom border, cap 0.7", 1.0,
               [T(ID, c, M - 0.5, 0.7, 1.0, maxw=inner)], human="id, larger"),
        # ---- outside the quiet zone, inside the cut
        Design("strip_q1", "outside", "Label strip: 1 module below the quiet zone, inside the cut", 1.0,
               [T(LONG, c, M + 1.5, 0.55, 0.35, maxw=M + 1.6)], strip=1.0,
               human="id + where it goes; the strip side is the bottom"),
        # ---- orientation marks
        Design("tri_tl", "orient", "Triangle in the top-left quiet-zone corner, grey 0.5, legs 0.6", 1.0,
               [tri_tl], human="corner 0 / board origin"),
        Design("tri_tl_black", "orient", "Same triangle, black", 1.0,
               [P([(-1, -1), (-0.4, -1), (-1, -0.4)], 0.0)], human="corner 0"),
        Design("tri_tl_near", "orient", "Triangle pointing INTO corner 0, 0.15 module away, black", 1.0,
               [P([(-0.15, -0.15), (-0.75, -0.15), (-0.15, -0.75)], 0.0)], human="corner 0", note="anti-example"),
        Design("arrow_top", "orient", "Up arrow in the top quiet strip, grey 0.5", 1.0,
               [P([(c, -0.8), (c - 0.35, -0.25), (c + 0.35, -0.25)], 0.5)], human="which way is up"),
        Design("chamfer_tl", "orient", "Cut outline chamfered at the top-left (no ink)", 1.0,
               chamfer=0.7, human="corner 0 (by the sticker's shape)"),
        Design("dot_tl", "orient", "White dot (d 0.4) in the top-left border cell", 1.0,
               [C(0.5, 0.5, 0.2, 1.0)], human="corner 0"),
        Design("dot_tl_big", "orient", "White dot (d 0.6) in the top-left border cell", 1.0,
               [C(0.5, 0.5, 0.3, 1.0)], human="corner 0"),
        # ---- combinations (the candidates for a default)
        Design("combo_white", "combo", "ID in the quiet zone + top-left triangle", 1.0,
               [T(ID, c, M + 0.5, 0.5, 0.6, maxw=M + 1.6), tri_tl], human="id + corner 0"),
        Design("combo_black", "combo", "White ID in the border + top-left triangle", 1.0,
               [T(ID, c, M - 0.5, 0.5, 1.0, maxw=inner), tri_tl], human="id + corner 0"),
        Design("combo_black_inner", "combo", "Nudged border ID + top-left triangle", 1.0,
               [T(ID, c, M - 0.6, 0.5, 1.0, maxw=inner), tri_tl], human="id + corner 0"),
        Design("combo_strip", "combo", "Label strip + chamfered top-left corner", 1.0,
               [T(LONG, c, M + 1.5, 0.55, 0.35, maxw=M + 1.6)], strip=1.0, chamfer=0.7,
               human="id + part + corner 0, zero ink in the quiet zone"),
        Design("combo_q2", "combo", "Quiet zone 2: ID cap 0.8 + triangle (legs 1.0)", 2.0,
               [T(ID, c, M + 1.0, 0.8, 0.6, maxw=M + 3.0), P([(-2, -2), (-1, -2), (-2, -1)], 0.5)],
               baseline="base_q2", human="id + corner 0, large"),
    ]
    return {x.key: x for x in d}


DOSE_WHITE_G = (0.0, 0.3, 0.5, 0.65, 0.8, 0.9)        # contrast 1 - g against white
DOSE_BLACK_G = (1.0, 0.7, 0.5, 0.35, 0.2, 0.1)        # contrast g against black
DOSE_CAPS = (0.35, 0.5, 0.65, 0.8)


def dose_designs(dictionary: str = D4) -> Dict[str, Design]:
    """White vs black at matched human contrast, over text size: the direct
    answer to "is it best to put something in the white or the black?"."""
    M = aruco_dict.dictionary_info(dictionary)["modules"]
    c = M / 2.0
    out = {}
    for cap in DOSE_CAPS:
        for g in DOSE_WHITE_G:
            k = "dw_g%02d_c%02d" % (round(g * 100), round(cap * 100))
            out[k] = Design(k, "dose-white", "white zone, grey %.2f, cap %.2f" % (g, cap), 1.0,
                            [T("#{id}", c, M + 0.5, cap, g, maxw=M + 1.6)])
        for g in DOSE_BLACK_G:
            k = "db_g%02d_c%02d" % (round(g * 100), round(cap * 100))
            out[k] = Design(k, "dose-black", "black border, grey %.2f, cap %.2f" % (g, cap), 1.0,
                            [T("#{id}", c, M - 0.5, cap, g, maxw=M - 2.4)])
    return out


GAPS = (0.1, 0.25, 0.4, 0.55, 0.7, 0.85, 1.0, 1.25)


def gap_designs(dictionary: str = D4) -> Dict[str, Design]:
    """How much CLEAN white must stay next to the border? Quiet zone 2, so
    there is room; ink moves closer to (a) an edge, (b) a corner."""
    M = aruco_dict.dictionary_info(dictionary)["modules"]
    c = M / 2.0
    out = {}
    for g in GAPS:
        k = "ge_%03d" % round(g * 100)
        out[k] = Design(k, "gap-edge", "ID text (cap 0.5, grey 0.6) %.2f module below the border" % g, 2.0,
                        [T("#{id}", c, M + g + 0.25, 0.5, 0.6, maxw=M + 3.0)], baseline="base_q2")
        k = "gc_%03d" % round(g * 100)
        out[k] = Design(k, "gap-corner", "0.5-module square, grey 0.5, %.2f module diagonally off corner 0" % g, 2.0,
                        [P([(-g - 0.5, -g - 0.5), (-g, -g - 0.5), (-g, -g), (-g - 0.5, -g)], 0.5)],
                        baseline="base_q2")
    return out


CH = dict(cols=5, rows=4, sq=8.0, mk=6.0)   # 4x4: marker = 0.75 square -> 1 module of white inside each square


def charuco_designs() -> Dict[str, Design]:
    spec = _ch_spec()
    under = [T("#%d" % mid, x + 3.0, y + 6.5, 0.5, 0.6, maxw=7.0) for mid, x, y in aruco_board._placements(spec)]
    inside = [T("#%d" % mid, x + 3.0, y + 5.5, 0.5, 1.0, maxw=3.6) for mid, x, y in aruco_board._placements(spec)]
    nudged = [T("#%d" % mid, x + 3.0, y + 5.4, 0.5, 1.0, maxw=3.6) for mid, x, y in aruco_board._placements(spec)]
    d = [
        Design("ch_base", "charuco", "ChArUco 5x4, plain, quiet zone 1", 1.0, board=CH, baseline="ch_base"),
        Design("ch_ids_white", "charuco", "Every marker's id under it, in its white square (grey 0.6, cap 0.5)", 1.0,
               under, board=CH, baseline="ch_base", human="every id"),
        Design("ch_ids_black", "charuco", "Every marker's id knocked out white in its own bottom border", 1.0,
               inside, board=CH, baseline="ch_base", human="every id"),
        Design("ch_ids_black_inner", "charuco", "Every marker's id knocked out white in its border, nudged inward 0.1",
               1.0, nudged, board=CH, baseline="ch_base", human="every id"),
        Design("ch_label_black", "charuco",
               "Board label + origin triangle INSIDE the top-left black square (light grey)", 1.0,
               [T("#0-9", 4.0, 3.9, 1.2, 0.75, maxw=6.0), T("5x4", 4.0, 5.9, 0.9, 0.75, maxw=6.0),
                P([(0.4, 0.4), (2.2, 0.4), (0.4, 2.2)], 0.75)],
               board=CH, baseline="ch_base", human="board ids + origin corner"),
        Design("ch_label_margin", "charuco", "Quiet zone 2: label under the board + origin triangle in the margin", 2.0,
               [T("CHARUCO 5x4  #0-9", 20.0, 33.0, 0.8, 0.6, maxw=36.0), P([(-2, -2), (-1, -2), (-2, -1)], 0.5)],
               board=CH, baseline="ch_base_q2", human="board ids + origin corner"),
        Design("ch_base_q2", "charuco", "ChArUco 5x4, plain, quiet zone 2", 2.0, board=CH, baseline="ch_base_q2"),
    ]
    return {x.key: x for x in d}


LABEL = "Arm:2/Act3"          # a representative part label (10 characters)
LABEL_ID = "#{id} " + LABEL     # id + label on one line (14-15 characters)


def label_designs(dictionary: str) -> Dict[str, Design]:
    """Zach, 2026-10-07: "couldn't you put more text [inside the black]? or does
    that lower performance?" Everything stays inside the black border; white
    knock-out text, cap height <= 0.5 module unless it has to shrink."""
    M = aruco_dict.dictionary_info(dictionary)["modules"]
    c = M / 2.0
    inner = M - 2.4                 # row length with the corner cells kept clear
    full = M - 0.3                  # whole row, 0.15 module short of each end
    b = M - 0.5                     # bottom row centre line
    d = [
        Design("lb_long_inner", "label", "(a) id + label in the bottom row, shrink-to-fit, corner cells clear", 1.0,
               [T(LABEL_ID, c, b, 0.5, 1.0, maxw=inner)], human="id + part"),
        Design("lb_long_full", "label", "(a') id + label in the bottom row, cap 0.5, whole row incl. corner cells", 1.0,
               [T(LABEL_ID, c, b, 0.5, 1.0, maxw=full)], human="id + part"),
        Design("lb_fill_full", "label", "(a'') a 20-character line filling the whole bottom row (shrinks to fit)", 1.0,
               [T(LABEL_ID + " front", c, b, 0.5, 1.0, maxw=full)], human="id + part + where",
               note="reaches into both corner cells in every dictionary"),
        Design("lb_id_top", "label", "(b) id in the bottom row + label in the top row", 1.0,
               [T("#{id}", c, b, 0.5, 1.0, maxw=inner), T(LABEL, c, 0.5, 0.5, 1.0, maxw=inner)], human="id + part"),
        Design("lb_id_sides", "label", "(c) id in the bottom row + label split over the left / right columns (rotated)",
               1.0, [T("#{id}", c, b, 0.5, 1.0, maxw=inner), T("Arm:2", 0.5, c, 0.5, 1.0, rot=90.0, maxw=inner),
                     T("Act3", M - 0.5, c, 0.5, 1.0, rot=-90.0, maxw=inner)], human="id + part"),
        Design("lb_four", "label", "(d) all four border rows: id bottom, label top, two more words left / right", 1.0,
               [T("#{id}", c, b, 0.5, 1.0, maxw=inner), T(LABEL, c, 0.5, 0.5, 1.0, maxw=inner),
                T("Base:1", 0.5, c, 0.5, 1.0, rot=90.0, maxw=inner),
                T("front", M - 0.5, c, 0.5, 1.0, rot=-90.0, maxw=inner)], human="id + part + where"),
        Design("lb_two_04", "label", "(e) two lines of cap 0.4 in the bottom row (0.05 module from each edge)", 1.0,
               [T("#{id}", c, M - 0.75, 0.4, 1.0, maxw=inner), T(LABEL, c, M - 0.25, 0.4, 1.0, maxw=inner)],
               human="id + part", note="leaves 0.05 module of black along the outer edge"),
        Design("lb_two_03", "label", "(e') two lines of cap 0.3 in the bottom row (0.15 module from each edge)", 1.0,
               [T("#{id}", c, M - 0.7, 0.3, 1.0, maxw=inner), T(LABEL, c, M - 0.3, 0.3, 1.0, maxw=inner)],
               human="id + part"),
    ]
    return {x.key: x for x in d}


LABEL_KEYS = ["lb_long_inner", "lb_long_full", "lb_fill_full", "lb_id_top", "lb_id_sides", "lb_four", "lb_two_04",
              "lb_two_03"]


def charuco_label_designs() -> Dict[str, Design]:
    spec = _ch_spec()
    pl = aruco_board._placements(spec)
    per_id = [T("#%d" % mid, x + 3.0, y + 5.5, 0.5, 1.0, maxw=3.6) for mid, x, y in pl]
    per_label = [T("Arm:2", x + 3.0, y + 0.5, 0.5, 1.0, maxw=3.6) for mid, x, y in pl]
    tri = P([(0.4, 0.4), (2.2, 0.4), (0.4, 2.2)], 0.75)
    d = [
        Design("chl_label_more", "label-charuco",
               "Top-left black square: three label lines (part, ids + size, square / marker) + origin triangle", 1.0,
               [T("Arm:2/Act3", 4.0, 3.0, 0.9, 0.75, maxw=6.4), T("#0-9  5x4", 4.0, 4.5, 0.9, 0.75, maxw=6.4),
                T("sq 8 / mk 6", 4.0, 5.9, 0.7, 0.75, maxw=6.4), tri],
               board=CH, baseline="ch_base", human="part + ids + sizes + origin"),
        Design("chl_ids_label", "label-charuco",
               "Every marker: its id in the bottom border row + the part label in the top row", 1.0,
               per_id + per_label, board=CH, baseline="ch_base", human="every id + part"),
        Design("chl_all", "label-charuco",
               "Top-left label square (3 lines) + every marker's id and label in its border", 1.0,
               [T("Arm:2/Act3", 4.0, 3.0, 0.9, 0.75, maxw=6.4), T("#0-9  5x4", 4.0, 4.5, 0.9, 0.75, maxw=6.4),
                T("sq 8 / mk 6", 4.0, 5.9, 0.7, 0.75, maxw=6.4), tri] + per_id + per_label,
               board=CH, baseline="ch_base", human="everything"),
    ]
    return {x.key: x for x in d}


CH_LABEL_KEYS = ["ch_label_black", "ch_ids_black", "chl_label_more", "chl_ids_label", "chl_all"]


def _ch_spec():
    return aruco_board.BoardSpec(layout="charuco", dictionary=D4, first_id=0, marker_length_m=CH["mk"],
                                 rows=CH["rows"], cols=CH["cols"], square_length_m=CH["sq"])


def all_designs(dictionary: str = D4) -> Dict[str, Design]:
    out = dict(single_designs(dictionary))
    out.update(label_designs(dictionary))
    if dictionary == D4:
        out.update(dose_designs(dictionary))
        out.update(gap_designs(dictionary))
        out.update(charuco_designs())
        out.update(charuco_label_designs())
    return out


# ===================================================================== geometry / PDF


def extent_modules(d: Design, dictionary: str) -> Tuple[float, float]:
    if d.board:
        return d.board["cols"] * d.board["sq"], d.board["rows"] * d.board["sq"]
    M = aruco_dict.dictionary_info(dictionary)["modules"]
    return float(M), float(M)


def outline(d: Design, dictionary: str) -> List[Tuple[float, float]]:
    """The cut, in modules (origin black extent top-left, y down), clockwise on the page."""
    W, H = extent_modules(d, dictionary)
    q, s, ch = d.quiet, d.strip, d.chamfer
    x0, y0, x1, y1 = -q, -q, W + q, H + q + s
    if ch > 0:
        return [(x0 + ch, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0 + ch)]
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def ink_rects(d: Design, dictionary: str, mid: int) -> List[Tuple[float, float, float, float]]:
    """Black ink as (x, y, w, h) in modules, origin black extent top-left, y down."""
    if d.board:
        spec = _ch_spec()
        W, H = extent_modules(d, dictionary)
        return [(x0 + W / 2, H / 2 - y1, x1 - x0, y1 - y0) for x0, y0, x1, y1 in aruco_board.black_rects_m(spec)]
    return [tuple(float(v) for v in r) for r in aruco_dict.black_rects(dictionary, mid)]


def text_layout(m: dict, mid: int) -> Tuple[str, float, float]:
    """-> (string, cap height actually used, width) in modules; shrinks to maxw."""
    s = m["s"].format(id=mid)
    cap = m["cap"]
    font_pt = cap * MODULE_MM / CAP * MM_TO_PT
    w = text_width_mm(s, font_pt) / MODULE_MM
    if m.get("maxw") and w > m["maxw"]:
        cap *= m["maxw"] / w
        w = m["maxw"]
    return s, cap, w


def _n(v: float) -> str:
    t = ("%.4f" % v).rstrip("0").rstrip(".")
    return "0" if t in ("", "-0") else t


class Page:
    def __init__(self, x0, y0, x1, y1):
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1
        self.k = MODULE_MM * MM_TO_PT
        self.w_pt, self.h_pt = (x1 - x0) * self.k, (y1 - y0) * self.k
        self.ops: List[str] = []

    def pt(self, u, v):
        return (u - self.x0) * self.k, (self.y1 - v) * self.k

    def poly(self, pts, g, op="f"):
        a = [self.pt(*p) for p in pts]
        self.ops.append("%s g %s %s m %s h %s" % (_n(g), _n(a[0][0]), _n(a[0][1]),
                                                  " ".join("%s %s l" % (_n(x), _n(y)) for x, y in a[1:]), op))

    def rects(self, rects, g):
        if rects:
            self.ops.append("%s g" % _n(g))
            for x, y, w, h in rects:
                X, Y = self.pt(x, y + h)
                self.ops.append("%s %s %s %s re" % (_n(X), _n(Y), _n(w * self.k), _n(h * self.k)))
            self.ops.append("f")

    def circle(self, cx, cy, r, g):
        k = 0.5523 * r
        X, Y = self.pt(cx, cy)
        R, K = r * self.k, k * self.k
        self.ops.append("%s g %s %s m %s %s %s %s %s %s c %s %s %s %s %s %s c %s %s %s %s %s %s c "
                        "%s %s %s %s %s %s c h f" % tuple(_n(v) for v in (
                            g, X + R, Y, X + R, Y + K, X + K, Y + R, X, Y + R,
                            X - K, Y + R, X - R, Y + K, X - R, Y,
                            X - R, Y - K, X - K, Y - R, X, Y - R,
                            X + K, Y - R, X + R, Y - K, X + R, Y)))

    def text(self, s, cx, cy, cap, w, g, rot):
        em_pt = cap / CAP * self.k
        X, Y = self.pt(cx, cy)
        r = math.radians(rot)
        d, u = (math.cos(r), math.sin(r)), (-math.sin(r), math.cos(r))
        ax = X - w * self.k / 2 * d[0] - cap * self.k / 2 * u[0]
        ay = Y - w * self.k / 2 * d[1] - cap * self.k / 2 * u[1]
        self.ops.append("BT /F1 %s Tf %s g %s %s %s %s %s %s Tm %s Tj ET" % (
            _n(em_pt), _n(g), _n(d[0]), _n(d[1]), _n(u[0]), _n(u[1]), _n(ax), _n(ay), pdf_string(s)))

    def stroke_poly(self, pts, g, width_mod):
        self.ops.append("%s G %s w" % (_n(g), _n(width_mod * self.k)))
        self.poly(pts, 0, op="S")


def draw_marks(pg: Page, marks: list, mid: int):
    for m in marks:
        if m["k"] == "text":
            s, cap, w = text_layout(m, mid)
            pg.text(s, m["c"][0], m["c"][1], cap, w, m["g"], m["rot"])
        elif m["k"] == "poly":
            pg.poly(m["pts"], m["g"])
        elif m["k"] == "circle":
            pg.circle(m["c"][0], m["c"][1], m["r"], m["g"])


def page_box(d: Design, dictionary: str, S: int):
    ol = outline(d, dictionary)
    xs, ys = [p[0] for p in ol], [p[1] for p in ol]
    x0, y0 = min(xs) - BGM, min(ys) - BGM
    x1 = x0 + math.ceil((max(xs) + BGM - x0) * S) / S
    y1 = y0 + math.ceil((max(ys) + BGM - y0) * S) / S
    if d.sheet and d.labels_below:
        y1 += 1.0
    return x0, y0, x1, y1


def design_pdf(d: Design, dictionary: str, mid: int, S: int) -> Tuple[bytes, tuple]:
    """Two pages: the printed sticker, and the cut (filled outline) as a mask."""
    box = page_box(d, dictionary, S)
    pg = Page(*box)
    pg.rects(ink_rects(d, dictionary, mid), 0.0)
    draw_marks(pg, d.marks, mid)
    ol = outline(d, dictionary)
    if d.sheet:
        pg.stroke_poly(ol, 0.55, 0.1 / MODULE_MM)            # print_sheet: CUT_GREY, 0.1 mm
        W, H = extent_modules(d, dictionary)
        for k, (s, g) in enumerate(d.labels_below):
            cap = 6.0 / MM_TO_PT * CAP / MODULE_MM          # 6 pt label, as print_sheet
            text = s.format(id=mid)
            w = text_width_mm(text, 6.0) / MODULE_MM
            pg.text(text, W / 2, H + d.quiet + 0.8 / MODULE_MM + cap / 2 + k * 1.2 * cap / CAP, cap, w, g, 0)
    mask = Page(*box)
    if d.sheet:
        mask.rects([(box[0], box[1], box[2] - box[0], box[3] - box[1])], 0.0)
    else:
        mask.poly(ol, 0.0)
    return _pdf([pg.ops, mask.ops], pg.w_pt, pg.h_pt), box


def _pdf(pages: List[List[str]], w_pt: float, h_pt: float) -> bytes:
    objs: List[bytes] = []

    def add(b: bytes) -> int:
        objs.append(b)
        return len(objs)

    add(b"<< /Type /Catalog /Pages 2 0 R >>")
    add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    kids = []
    for ops in pages:
        st = zlib.compress("\n".join(ops).encode("ascii"), 6)
        c = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(st) + st + b"\nendstream")
        kids.append(add(("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %s %s] /Resources << /Font << /F1 %d 0 R "
                         ">> >> /Contents %d 0 R >>" % (_n(w_pt), _n(h_pt), font, c)).encode("ascii")))
    objs[1] = ("<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join("%d 0 R" % k for k in kids),
                                                            len(kids))).encode("ascii")
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offs = []
    for i, b in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + b + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for o in offs:
        out += b"%010d 00000 n \n" % o
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)
    return bytes(out)


def design_svg(d: Design, dictionary: str, mid: int, bg: str = "#8d8d8d") -> str:
    """The same primitives as SVG, viewBox in modules (for the report)."""
    x0, y0, x1, y1 = page_box(d, dictionary, 64)
    pad = BGM - 0.35
    vx, vy, vw, vh = x0 + pad, y0 + pad, (x1 - x0) - 2 * pad, (y1 - y0) - 2 * pad
    o = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="%s %s %s %s" role="img" aria-label="%s">'
         % (_n(vx), _n(vy), _n(vw), _n(vh), d.title.replace('"', "'"))]
    ol = outline(d, dictionary)
    o.append('<rect x="%s" y="%s" width="%s" height="%s" fill="%s"/>'
             % (_n(vx), _n(vy), _n(vw), _n(vh), "#ffffff" if d.sheet else bg))
    o.append('<path d="M%sZ" fill="#fff"%s/>' % ("L".join("%s %s" % (_n(x), _n(y)) for x, y in ol),
                                                 ' stroke="#8c8c8c" stroke-width="0.04"' if d.sheet else ""))
    o.append('<path d="%s" fill="#000" shape-rendering="crispEdges"/>' % "".join(
        "M%s %sh%sv%sh%sz" % (_n(x), _n(y), _n(w), _n(h), _n(-w)) for x, y, w, h in ink_rects(d, dictionary, mid)))

    def grey(g):
        v = int(round(g * 255))
        return "#%02x%02x%02x" % (v, v, v)

    def text(s, cx, cy, cap, g, rot):
        return ('<text transform="translate(%s %s) rotate(%s)" y="%s" font-size="%s" text-anchor="middle" '
                'font-family="Helvetica, Arial, sans-serif" fill="%s">%s</text>'
                % (_n(cx), _n(cy), _n(-rot), _n(cap / 2), _n(cap / CAP), grey(g),
                   s.replace("&", "&amp;").replace("<", "&lt;")))
    for m in d.marks:
        if m["k"] == "text":
            s, cap, _ = text_layout(m, mid)
            o.append(text(s, m["c"][0], m["c"][1], cap, m["g"], m["rot"]))
        elif m["k"] == "poly":
            o.append('<path d="M%sZ" fill="%s"/>' % ("L".join("%s %s" % (_n(x), _n(y)) for x, y in m["pts"]),
                                                     grey(m["g"])))
        elif m["k"] == "circle":
            o.append('<circle cx="%s" cy="%s" r="%s" fill="%s"/>' % (_n(m["c"][0]), _n(m["c"][1]), _n(m["r"]),
                                                                     grey(m["g"])))
    if d.sheet:
        W, H = extent_modules(d, dictionary)
        cap = 6.0 / MM_TO_PT * CAP / MODULE_MM
        for k, (s, g) in enumerate(d.labels_below):
            o.append(text(s.format(id=mid), W / 2, H + d.quiet + 0.8 / MODULE_MM + cap / 2, cap, g, 0))
    o.append("</svg>")
    return "".join(o)


# ===================================================================== print + camera

_MASTER: Dict[tuple, dict] = {}
_PRINTED: Dict[tuple, np.ndarray] = {}
_DESIGNS: Dict[str, Dict[str, Design]] = {}


def designs_for(dictionary: str) -> Dict[str, Design]:
    if dictionary not in _DESIGNS:
        _DESIGNS[dictionary] = all_designs(dictionary)
    return _DESIGNS[dictionary]


def master(key: str, dictionary: str, mid: int) -> dict:
    """pdfium raster of the sticker (ink coverage 0..1) + the cut mask + geometry."""
    ck = (key, dictionary, mid)
    if ck in _MASTER:
        return _MASTER[ck]
    d = designs_for(dictionary)[key]
    S = S_BOARD if d.board else S_SINGLE
    pdf, box = design_pdf(d, dictionary, mid, S)
    doc = pdfium.PdfDocument(pdf)
    scale = S / (MODULE_MM * MM_TO_PT)
    want = (int(round((box[3] - box[1]) * S)), int(round((box[2] - box[0]) * S)))
    imgs = []
    for i in range(2):
        a = doc[i].render(scale=scale, grayscale=True).to_numpy()
        if a.ndim == 3:
            a = a[:, :, 0]
        a = a[:want[0], :want[1]]
        if a.shape != want:
            raise RuntimeError("pdfium raster %s, wanted %s" % (a.shape, want))
        imgs.append(1.0 - a.astype(np.float32) / 255.0)
    doc.close()
    cov, mask = imgs
    W, H = extent_modules(d, dictionary)
    ox, oy = -box[0] * S, -box[1] * S         # master px (continuous) of the black extent's top-left
    # The camera frames a fixed square around the MARKER (not around the
    # sticker), so every design of one dictionary lands in the same pixels,
    # under the same noise and lighting field: the comparison is paired.
    geom = {"S": S, "ox": ox, "oy": oy, "W": W, "H": H, "sheet": d.sheet,
            "centre": (ox + W * S / 2, oy + H * S / 2),
            "extent_mod": max(W, H) + 2.0,
            "frame_mod": max(W, H) / 2.0 + FRAME_PAD}
    if d.board:
        geom["corners"] = np.array([[ox + x * S, oy + y * S] for x, y, _ in aruco_board.charuco_corners(_ch_spec())],
                                   np.float64)
        M = 6
        geom["marker_corners"] = {mid_: np.array([[ox + x * S, oy + y * S], [ox + (x + M) * S, oy + y * S],
                                                  [ox + (x + M) * S, oy + (y + M) * S], [ox + x * S, oy + (y + M) * S]])
                                  for mid_, x, y in aruco_board._placements(_ch_spec())}
    else:
        M = W
        geom["corners"] = np.array([[ox, oy], [ox + M * S, oy], [ox + M * S, oy + M * S], [ox, oy + M * S]],
                                   np.float64)
    out = {"cov": cov, "mask": mask, "geom": geom, "S": S}
    if len(_MASTER) > 64:
        _MASTER.clear()
    _MASTER[ck] = out
    return out


def _disc(r_px: float):
    k = int(math.ceil(r_px))
    yy, xx = np.mgrid[-k:k + 1, -k:k + 1]
    return ((xx * xx + yy * yy) <= r_px * r_px + 1e-9).astype(np.uint8)


def printed(m: dict, dg: int, ht: bool, bg: float, key) -> np.ndarray:
    """Reflectance 0..1 of the sticker on its part, after the printer."""
    if key in _PRINTED:
        return _PRINTED[key]
    c = m["cov"]
    if ht:
        h, w = c.shape
        scr = np.tile(SCREEN, (h // 4 + 1, w // 4 + 1))[:h, :w]
        c = (c > scr).astype(np.float32)
    elif TONE[dg]:
        c = c + TONE[dg] * 4.0 * c * (1.0 - c)
    r = SPREAD[dg] * m["S"]
    if r >= 0.5:
        c = cv2.dilate(c, _disc(r))
    refl = PAPER - (PAPER - INK) * c
    out = (m["mask"] * refl + (1.0 - m["mask"]) * bg).astype(np.float32)
    if len(_PRINTED) > 24:
        _PRINTED.clear()
    _PRINTED[key] = out
    return out


def _rot(axis, ang):
    axis = np.asarray(axis, float)
    axis = axis / np.linalg.norm(axis)
    K = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    return np.eye(3) + math.sin(ang) * K + (1 - math.cos(ang)) * K @ K


def camera(refl: np.ndarray, geom: dict, cond: dict) -> Tuple[np.ndarray, np.ndarray]:
    """-> (uint8 camera image, 3x3 H mapping master continuous px -> OpenCV image coords)."""
    S = geom["S"]
    ppm = cond["ppm"]
    D = 5.0 * geom["extent_mod"]
    f = ppm * D * SS
    R = _rot((0, 0, 1), math.radians(cond["roll"])) @ _rot(
        (math.cos(math.radians(cond["axis"])), math.sin(math.radians(cond["axis"])), 0), math.radians(cond["tilt"]))
    cx, cy = geom["centre"]
    A = np.array([[1.0 / S, 0, -cx / S], [0, 1.0 / S, -cy / S], [0, 0, 1]])
    Pm = np.column_stack([R[:, 0], R[:, 1], np.array([0, 0, D])])
    H = np.diag([f, f, 1.0]) @ Pm @ A
    r = geom["frame_mod"] * S
    frame = np.array([[[cx - r, cy - r], [cx + r, cy - r], [cx + r, cy + r], [cx - r, cy + r]]], np.float64)
    box = cv2.perspectiveTransform(frame, H)[0]
    mn = box.min(axis=0)
    shift = np.array([[1, 0, -mn[0] + SS * cond["dx"] + SS], [0, 1, -mn[1] + SS * cond["dy"] + SS], [0, 0, 1]])
    H = shift @ H
    box = cv2.perspectiveTransform(frame, H)[0]
    W2 = int(math.ceil((box[:, 0].max() + 2 * SS) / SS)) * SS
    H2 = int(math.ceil((box[:, 1].max() + 2 * SS) / SS)) * SS
    k = S / (SS * ppm)
    src = refl
    if k > 1.0:
        src = cv2.GaussianBlur(refl, (0, 0), (k - 1.0) / 2.0 + 1e-3)
    Tm, Tp = np.array([[1, 0, -0.5], [0, 1, -0.5], [0, 0, 1]]), np.array([[1, 0, 0.5], [0, 1, 0.5], [0, 0, 1]])
    Hidx = Tm @ H @ Tp
    ss = cv2.warpPerspective(src, Hidx, (W2, H2), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                             borderValue=float(PAPER if geom["sheet"] else cond["bg"]))
    img = cv2.resize(ss, (W2 // SS, H2 // SS), interpolation=cv2.INTER_AREA)
    if cond["blur"] > 0.05:
        img = cv2.GaussianBlur(img, (0, 0), cond["blur"])
    hh, ww = img.shape
    yy, xx = np.mgrid[0:hh, 0:ww].astype(np.float32)
    a = math.radians(cond["gdir"])
    t = xx * math.cos(a) + yy * math.sin(a)
    t = (t - t.min()) / max(1e-6, float(t.max() - t.min()))
    img = img * (cond["expo"] * (1.0 - cond["grad"] * t))
    rng = np.random.default_rng(cond["seed"])
    img = img * 255.0 + rng.normal(0.0, cond["noise"], img.shape)
    img = np.clip(np.round(img), 0, 255).astype(np.uint8)
    if cond["jpeg"]:
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(cond["jpeg"])])
        img = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)
    # The sensor: at least 320 x 240 (edge-replicated). OpenCV's default
    # minMarkerPerimeterRate is relative to the image size, so a tiny crop
    # would let thousands of noise specks through as candidates; any real
    # camera frame is bigger than this, and none of these markers is near
    # the size that rate rejects (perimeter >= 60 px at 2.5 px/module).
    hh, ww = img.shape
    top, left = max(0, (SENSOR[1] - hh) // 2), max(0, (SENSOR[0] - ww) // 2)
    if top or left:
        img = cv2.copyMakeBorder(img, top, max(0, SENSOR[1] - hh - top), left, max(0, SENSOR[0] - ww - left),
                                 cv2.BORDER_REPLICATE)
    Hcam = np.array([[1.0 / SS, 0, left - 0.5], [0, 1.0 / SS, top - 0.5], [0, 0, 1]]) @ H
    return img, Hcam


def project(Hcam, pts):
    return cv2.perspectiveTransform(np.asarray(pts, np.float64).reshape(1, -1, 2), Hcam)[0]


# ===================================================================== detection

_DET: Dict[tuple, object] = {}


def detectors(dictionary: str):
    if dictionary not in _DET:
        dd = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, dictionary))
        p0 = cv2.aruco.DetectorParameters()
        p1 = cv2.aruco.DetectorParameters()
        p1.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
        _DET[dictionary] = (cv2.aruco.ArucoDetector(dd, p0), cv2.aruco.ArucoDetector(dd, p1))
    return _DET[dictionary]


_CHDET = None


def charuco_detector():
    global _CHDET
    if _CHDET is None:
        dd = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        board = cv2.aruco.CharucoBoard((CH["cols"], CH["rows"]), CH["sq"], CH["mk"], dd)
        _CHDET = cv2.aruco.CharucoDetector(board)
    return _CHDET


NCOL = 12   # det0 det1 wrong0 wrong1 e0[4] e1[4]   (wrong = any other id, a duplicate, or corner order off)


def score(det, img, true_id, gt):
    corners, ids, _ = det.detectMarkers(img)
    ids = [] if ids is None else [int(v) for v in np.asarray(ids).reshape(-1)]
    hits = [np.asarray(c).reshape(4, 2) for c, i in zip(corners, ids) if i == true_id]
    wrong = sum(1 for i in ids if i != true_id) + max(0, len(hits) - 1)
    if not hits:
        return 0, wrong, [np.nan] * 4
    q = min(hits, key=lambda c: float(np.linalg.norm(c - gt, axis=1).mean()))
    errs = [float(np.linalg.norm(np.roll(q, -k, axis=0) - gt, axis=1).mean()) for k in range(4)]
    if int(np.argmin(errs)) != 0:
        wrong += 1                                   # right id, wrong corner order: a wrong pose
    return 1, wrong, [float(v) for v in np.linalg.norm(q - gt, axis=1)]


def run_single(args) -> np.ndarray:
    key, dictionary, conds = args
    cv2.setNumThreads(1)
    out = np.full((len(conds), NCOL), np.nan)
    order = sorted(range(len(conds)), key=lambda i: (conds[i]["id"], conds[i]["dg"], conds[i]["ht"], conds[i]["bg"]))
    for i in order:
        cd = conds[i]
        m = master(key, dictionary, cd["id"])
        refl = printed(m, cd["dg"], cd["ht"], cd["bg"], (key, dictionary, cd["id"], cd["dg"], cd["ht"], cd["bg"]))
        img, Hc = camera(refl, m["geom"], cd)
        gt = project(Hc, m["geom"]["corners"])
        d0, d1 = detectors(dictionary)
        a = score(d0, img, cd["id"], gt)
        b = score(d1, img, cd["id"], gt)
        out[i, 0], out[i, 1], out[i, 2], out[i, 3] = a[0], b[0], a[1], b[1]
        out[i, 4:8], out[i, 8:12] = a[2], b[2]
    return out


CHCOL = 6   # markers_found corners_found rms_err_px max_err_px wrong_markers wrong_corners


def run_charuco(args) -> np.ndarray:
    key, dictionary, conds = args
    cv2.setNumThreads(1)
    det = charuco_detector()
    out = np.full((len(conds), CHCOL), np.nan)
    order = sorted(range(len(conds)), key=lambda i: (conds[i]["dg"], conds[i]["ht"], conds[i]["bg"]))
    for i in order:
        cd = dict(conds[i], ht=False)
        m = master(key, D4, 0)
        refl = printed(m, cd["dg"], False, cd["bg"], (key, D4, 0, cd["dg"], False, cd["bg"]))
        img, Hc = camera(refl, m["geom"], cd)
        gt = project(Hc, m["geom"]["corners"])
        cc, cids, mc, mids = det.detectBoard(img)
        mids = [] if mids is None else [int(v) for v in np.asarray(mids).reshape(-1)]
        n_ids = aruco_board.marker_count(_ch_spec())
        wrong_m = sum(1 for v in mids if v >= n_ids)
        if cids is None or len(cids) == 0:
            out[i] = [len(mids), 0, np.nan, np.nan, wrong_m, 0]
            continue
        cids = np.asarray(cids).reshape(-1)
        cc = np.asarray(cc).reshape(-1, 2)
        e = np.linalg.norm(cc - gt[cids], axis=1)
        wrong_c = int(np.sum(e > cd["ppm"] * 1.0))          # > 1 module off = a wrong corner, not noise
        good = e[e <= cd["ppm"] * 1.0]
        out[i] = [len(mids), len(cids), float(np.sqrt(np.mean(good ** 2))) if len(good) else np.nan,
                  float(good.max()) if len(good) else np.nan, wrong_m, wrong_c]
    return out


# ===================================================================== conditions

NOMINAL = dict(ppm=5.0, tilt=None, blur=0.5, noise=3.0, jpeg=0, grad=0.2, expo=0.9, dg=1, ht=True, bg=0.35)
SWEEPS = {
    "ppm": [1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0, 12.0, 20.0],
    "ppm_dg2": [1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0, 12.0, 20.0],
    "tilt": [0, 20, 40, 50, 55, 60, 65, 70, 75],
    "blur": [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0],
    "noise": [0, 5, 10, 15, 20, 30],
    "jpeg": [0, 90, 70, 50, 30, 15],
    "grad": [0.0, 0.3, 0.5, 0.7, 0.85, 0.95],
    "print": ["c0", "c1", "c2", "c3", "h0", "h1", "h2", "h3"],
}
SWEEP_AT: Dict[str, dict] = {"ppm_dg2": {"dg": 2}}   # per-factor overrides of NOMINAL


def _base_random(rng, ids):
    return dict(id=int(ids[rng.integers(len(ids))]), axis=float(rng.uniform(0, 180)), roll=float(rng.uniform(0, 360)),
                dx=float(rng.uniform(0, 1)), dy=float(rng.uniform(0, 1)), gdir=float(rng.uniform(0, 360)),
                seed=int(rng.integers(2 ** 31 - 1)))


def sweep_conditions(factor, value, n, seed, ids):
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        c = dict(NOMINAL)
        c.update(_base_random(rng, ids))
        c["tilt"] = float(rng.uniform(0, 30))
        c.update(SWEEP_AT.get(factor, {}))
        if factor == "print":
            c["ht"], c["dg"] = value[0] == "h", int(value[1])
        elif factor.startswith("ppm"):
            c["ppm"] = value
        else:
            c[factor] = value
        out.append(c)
    return out


def stress_conditions(n, seed, ids, ppm=(2.8, 10.0)):
    """The hard-but-plausible mix every design is compared on, trial for trial."""
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        c = _base_random(rng, ids)
        c.update(ppm=float(math.exp(rng.uniform(math.log(ppm[0]), math.log(ppm[1])))),
                 tilt=float(rng.uniform(0, 65)), blur=float(rng.uniform(0, 1.6)), noise=float(rng.uniform(0, 14)),
                 jpeg=int(rng.choice([0, 90, 60, 35])), grad=float(rng.uniform(0, 0.8)),
                 expo=float(rng.uniform(0.6, 1.05)), dg=int(rng.integers(4)), ht=bool(rng.random() < 0.5),
                 bg=float(rng.choice([0.85, 0.45, 0.15])))
        out.append(c)
    return out


def clean_conditions(ppm, n, seed, ids):
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        c = dict(NOMINAL)
        c.update(_base_random(rng, ids))
        c.update(ppm=ppm, tilt=0.0, blur=0.4, noise=0.0, grad=0.0, dg=0, bg=0.35)
        out.append(c)
    return out


# ===================================================================== statistics


def binom_two_sided(k, n):
    if n == 0:
        return 1.0
    k = min(k, n - k)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2.0 ** n
    return min(1.0, 2 * p)


def paired(base: np.ndarray, var: np.ndarray, col: int) -> dict:
    """McNemar-style comparison of detection on the same trials."""
    b, v = base[:, col] > 0.5, var[:, col] > 0.5
    n = len(b)
    lost, gained = int(np.sum(b & ~v)), int(np.sum(~b & v))
    delta = (gained - lost) / n
    se = math.sqrt(max(0.0, (lost + gained) - (gained - lost) ** 2 / n)) / n
    return {"n": n, "base_rate": round(float(b.mean()), 4), "rate": round(float(v.mean()), 4),
            "lost": lost, "gained": gained, "delta": round(delta, 4),
            "ci95": [round(delta - 1.96 * se, 4), round(delta + 1.96 * se, 4)],
            "p_mcnemar": round(binom_two_sided(min(lost, gained), lost + gained), 4)}


def corner_stats(base: np.ndarray, var: np.ndarray, conds, refine: int) -> dict:
    """Corner error (px and modules) on the trials BOTH detected; corner 0 vs the rest."""
    e = slice(4, 8) if refine == 0 else slice(8, 12)
    det = (base[:, refine] > 0.5) & (var[:, refine] > 0.5)
    if det.sum() == 0:
        return {}
    ppm = np.array([c["ppm"] for c in conds])[det]
    vb, vv = base[det, e], var[det, e]
    mb, mv = vb.mean(axis=1) / ppm, vv.mean(axis=1) / ppm
    return {"n_both": int(det.sum()),
            "mean_err_mod": round(float(mv.mean()), 4), "base_mean_err_mod": round(float(mb.mean()), 4),
            "p95_err_mod": round(float(np.percentile(mv, 95)), 4),
            "base_p95_err_mod": round(float(np.percentile(mb, 95)), 4),
            "mean_err_px": round(float(vv.mean()), 4), "base_mean_err_px": round(float(vb.mean()), 4),
            "c0_minus_base_mod": round(float((vv[:, 0] / ppm - vb[:, 0] / ppm).mean()), 4),
            "c123_minus_base_mod": round(float((vv[:, 1:].mean(axis=1) / ppm - vb[:, 1:].mean(axis=1) / ppm).mean()), 4),
            "worst_corner_minus_base_mod": round(float(max(
                (vv[:, k] / ppm - vb[:, k] / ppm).mean() for k in range(4))), 4)}


def crossing(xs, rates, level):
    """Smallest x such that the rate is >= level there and at every larger x (linear interp)."""
    xs, rates = list(xs), list(rates)
    if rates[-1] < level:
        return None
    k = len(xs) - 1
    while k > 0 and rates[k - 1] >= level:
        k -= 1
    if k == 0:
        return xs[0]
    x0, x1, r0, r1 = xs[k - 1], xs[k], rates[k - 1], rates[k]
    return round(x0 + (level - r0) * (x1 - x0) / (r1 - r0), 3)


# ===================================================================== experiments


def pool_map(pool, fn, tasks):
    return pool.map(fn, tasks, chunksize=1) if pool else [fn(t) for t in tasks]


TRIALS = [0]


def split(conds, size):
    return [conds[i:i + size] for i in range(0, len(conds), size)]


def run_design_set(pool, keys, dictionary, conds, fn=run_single, size=800):
    """Every design on the SAME condition list; tasks are (design, chunk) so a
    worker renders a design's masters once per chunk."""
    TRIALS[0] += len(keys) * len(conds)
    tasks, owners = [], []
    for key in keys:
        for chunk in split(conds, size):
            tasks.append((key, dictionary, chunk))
            owners.append(key)
    res = pool_map(pool, fn, tasks)
    out = {}
    for key, r in zip(owners, res):
        out.setdefault(key, []).append(r)
    return {k: np.vstack(v) for k, v in out.items()}


def summarise(designs, res, conds, keys) -> dict:
    out = {}
    for k in keys:
        d = designs[k]
        base = res[d.baseline]
        v = res[k]
        out[k] = {"baseline": d.baseline,
                  "default": paired(base, v, 0), "subpix": paired(base, v, 1),
                  "wrong_default": int(np.nansum(v[:, 2])), "wrong_subpix": int(np.nansum(v[:, 3])),
                  "corners_default": corner_stats(base, v, conds, 0),
                  "corners_subpix": corner_stats(base, v, conds, 1)}
    return out


def by_bin(res, conds, keys, field_, edges):
    """Detection rate per bin of one condition field (for the stress mix)."""
    x = np.array([c[field_] for c in conds], float)
    out = {}
    for k in keys:
        rows = []
        for lo, hi in zip(edges[:-1], edges[1:]):
            sel = (x >= lo) & (x < hi)
            rows.append({"lo": lo, "hi": hi, "n": int(sel.sum()),
                         "rate_default": round(float(res[k][sel, 0].mean()), 4) if sel.any() else None,
                         "rate_subpix": round(float(res[k][sel, 1].mean()), 4) if sel.any() else None})
        out[k] = rows
    return out


def exp_stress(pool, designs, keys, n, seed, dictionary=D4):
    conds = stress_conditions(n, seed, IDS[dictionary])
    need = sorted(set(keys) | {designs[k].baseline for k in keys})
    res = run_design_set(pool, need, dictionary, conds)
    summ = summarise(designs, res, conds, keys)
    bins = by_bin(res, conds, need, "ppm", [2.8, 3.5, 4.5, 6.0, 8.0, 10.01])
    bg = {}
    for k in need:
        bg[k] = {str(b): round(float(res[k][np.array([c["bg"] == b for c in conds]), 0].mean()), 4)
                 for b in (0.85, 0.45, 0.15)}
    ht = {}
    for k in need:
        sel = np.array([c["ht"] for c in conds])
        ht[k] = {"halftone": round(float(res[k][sel, 0].mean()), 4), "contone": round(float(res[k][~sel, 0].mean()), 4)}
    dgv = np.array([c["dg"] for c in conds])
    htv = np.array([c["ht"] for c in conds])
    by_dg = {}
    for k in keys:
        b, v = res[designs[k].baseline], res[k]
        by_dg[k] = {}
        for dg in range(4):
            for ht in (False, True):
                sel = (dgv == dg) & (htv == ht)
                by_dg[k]["%s%d" % ("h" if ht else "c", dg)] = paired(b[sel], v[sel], 0)
    cross = {}
    for a, b in (("base_q2", "base_q1"), ("white_q2_id", "base_q1"), ("combo_q2", "base_q1"),
                 ("sheet_plain", "base_q1"), ("white_q15_id", "base_q2")):
        if a in res and b in res:
            cross["%s vs %s" % (a, b)] = {"default": paired(res[b], res[a], 0)}
    return {"n": n, "seed": seed, "dictionary": dictionary, "summary": summ, "cross": cross, "by_print_cell": by_dg,
            "by_ppm": bins, "by_bg": bg,
            "by_print": ht, "conditions": "ppm logU[2.8,10], tilt U[0,65], blur U[0,1.6] px, noise U[0,14], "
            "jpeg {none,90,60,35}, gradient U[0,0.8], exposure U[0.6,1.05], dot gain level {0..3}, "
            "halftone 50%, part surface {0.85,0.45,0.15}"}, res, conds


def exp_sweeps(pool, designs, keys, n_by_factor, seed):
    out = {}
    for factor, values in SWEEPS.items():
        n = n_by_factor.get(factor[:3] if factor.startswith("ppm") else factor, n_by_factor["default"])
        fo = {"x": values, "n_per_point": n, "designs": {}}
        conds, which = [], []
        for vi, val in enumerate(values):
            cs = sweep_conditions(factor, val, n, seed + 1000 * vi, IDS[D4])
            conds += cs
            which += [vi] * len(cs)
        which = np.array(which)
        res = run_design_set(pool, keys, D4, conds, size=700)
        for key in keys:
            rows = []
            for vi, val in enumerate(values):
                a = res[key][which == vi]
                ppm = float(val) if factor.startswith("ppm") else float(NOMINAL["ppm"])
                e1 = a[:, 8:12].mean(axis=1)
                rows.append({"rate_default": round(float(a[:, 0].mean()), 4),
                             "rate_subpix": round(float(a[:, 1].mean()), 4),
                             "wrong": int(np.nansum(a[:, 2]) + np.nansum(a[:, 3])),
                             "median_err_mod_subpix": (round(float(np.nanmedian(e1) / ppm), 4)
                                                       if np.isfinite(e1).any() else None)})
            fo["designs"][key] = rows
        if factor.startswith("ppm"):
            fo["min_ppm"] = {k: {"p90_default": crossing(values, [r["rate_default"] for r in fo["designs"][k]], 0.9),
                                 "p90_subpix": crossing(values, [r["rate_subpix"] for r in fo["designs"][k]], 0.9),
                                 "p50_default": crossing(values, [r["rate_default"] for r in fo["designs"][k]], 0.5)}
                             for k in keys}
        out[factor] = fo
        print("   sweep %-6s done" % factor, flush=True)
    return out


def exp_clean(pool, designs, keys, n, seed):
    out = {}
    for ppm in (6.0, 12.0, 20.0):
        conds = clean_conditions(ppm, n, seed + int(ppm), IDS[D4])
        need = sorted(set(keys) | {designs[k].baseline for k in keys})
        res = run_design_set(pool, need, D4, conds)
        out[str(ppm)] = {}
        for k in keys:
            base, v = res[designs[k].baseline], res[k]
            out[str(ppm)][k] = {"rate": round(float(v[:, 1].mean()), 4),
                                "subpix": corner_stats(base, v, conds, 1),
                                "default": corner_stats(base, v, conds, 0)}
    return out


def exp_charuco(pool, designs, n_sweep, n_stress, seed):
    keys = [k for k, d in designs.items() if d.family == "charuco"]
    out = {"board": dict(CH, dictionary=D4, inner_corners=(CH["cols"] - 1) * (CH["rows"] - 1)),
           "sweep": {"x": [1.75, 2.0, 2.25, 2.5, 3.0, 4.0, 6.0, 10.0], "designs": {}}}
    conds, which = [], []
    for vi, ppm in enumerate(out["sweep"]["x"]):
        cs = sweep_conditions("ppm", ppm, n_sweep, seed + 77 * vi, [0])
        conds += cs
        which += [vi] * len(cs)
    which = np.array(which)
    allres = run_design_set(pool, keys, D4, conds, fn=run_charuco, size=60)
    for key in keys:
        rows = []
        for vi, ppm in enumerate(out["sweep"]["x"]):
            a = allres[key][which == vi]
            rows.append({"corners_mean": round(float(a[:, 1].mean()), 3), "markers_mean": round(float(a[:, 0].mean()), 3),
                         "all12": round(float((a[:, 1] >= 12).mean()), 4),
                         "rms_err_mod": round(float(np.nanmean(a[:, 2]) / ppm), 4) if np.isfinite(a[:, 2]).any() else None,
                         "wrong": int(np.nansum(a[:, 4]) + np.nansum(a[:, 5]))})
        out["sweep"]["designs"][key] = rows
    conds = stress_conditions(n_stress, seed + 5, [0], ppm=(2.5, 8.0))
    res = run_design_set(pool, keys, D4, conds, fn=run_charuco, size=60)
    st = {}
    for key in keys:
        b, v = res[designs[key].baseline], res[key]
        both = np.isfinite(b[:, 2]) & np.isfinite(v[:, 2])
        ppm = np.array([c["ppm"] for c in conds])
        st[key] = {"baseline": designs[key].baseline, "corners_mean": round(float(v[:, 1].mean()), 3),
                   "base_corners_mean": round(float(b[:, 1].mean()), 3),
                   "corners_delta": round(float((v[:, 1] - b[:, 1]).mean()), 3),
                   "trials_fewer": int(np.sum(v[:, 1] < b[:, 1])), "trials_more": int(np.sum(v[:, 1] > b[:, 1])),
                   "markers_mean": round(float(v[:, 0].mean()), 3),
                   "rms_err_mod": round(float(np.mean(v[both, 2] / ppm[both])), 4) if both.any() else None,
                   "base_rms_err_mod": round(float(np.mean(b[both, 2] / ppm[both])), 4) if both.any() else None,
                   "wrong": int(np.nansum(v[:, 4]) + np.nansum(v[:, 5]))}
    out["stress"] = {"n": n_stress, "summary": st,
                     "conditions": "as the single-marker mix but ppm logU[2.5,8] (px per MARKER module), no halftone"}
    return out


def label_capacity() -> dict:
    """How much text fits in one border row, for real marker sizes: the actual
    limit on "more text". Helvetica advance widths (print_sheet metrics) of a
    representative label alphabet; cap height capped at 0.5 module."""
    sample = "Arm:2/Act3 Base:1 Wrist L2 front Forearm #23 #123 top"
    em_per_char = text_width_mm(sample, MM_TO_PT) / len(sample)        # mm per char at a 1 mm em
    rows = []
    for dn in (D4, D6):
        M = aruco_dict.dictionary_info(dn)["modules"]
        for L in (20.0, 30.0, 40.0, 60.0):
            mod = L / M
            cap_max = 0.5 * mod
            inner, full = (M - 2.4) * mod, (M - 0.3) * mod
            row = {"dictionary": dn, "marker_mm": L, "module_mm": round(mod, 3), "cap_max_mm": round(cap_max, 3)}
            for tag, cap in (("cap_max", cap_max), ("cap_1.0", 1.0), ("cap_0.7", 0.7)):
                ok = cap <= cap_max + 1e-9
                w = em_per_char * cap / CAP
                row[tag] = {"cap_mm": round(cap, 3), "fits": bool(ok),
                            "chars_inner": int(inner // w) if ok else 0, "chars_full": int(full // w) if ok else 0,
                            "font_pt": round(cap / CAP * MM_TO_PT, 1)}
            rows.append(row)
    return {"sample": sample, "em_per_char": round(em_per_char, 4), "rows": rows,
            "inner": "row with the corner cells clear: n + 2 - 2.4 modules",
            "full": "whole row: n + 2 - 0.3 modules (text then reaches into the corner cells)"}


def _charuco_paired(b, v, conds) -> dict:
    ppm = np.array([c["ppm"] for c in conds])
    both = np.isfinite(b[:, 2]) & np.isfinite(v[:, 2])
    diff = v[:, 1] - b[:, 1]
    se = float(np.std(diff, ddof=1) / math.sqrt(len(diff))) if len(diff) > 1 else 0.0
    return {"corners_mean": round(float(v[:, 1].mean()), 3), "base_corners_mean": round(float(b[:, 1].mean()), 3),
            "corners_delta": round(float(diff.mean()), 3),
            "corners_delta_ci95": [round(float(diff.mean()) - 1.96 * se, 3), round(float(diff.mean()) + 1.96 * se, 3)],
            "trials_fewer": int(np.sum(v[:, 1] < b[:, 1])), "trials_more": int(np.sum(v[:, 1] > b[:, 1])),
            "markers_mean": round(float(v[:, 0].mean()), 3), "base_markers_mean": round(float(b[:, 0].mean()), 3),
            "rms_err_mod": round(float(np.mean(v[both, 2] / ppm[both])), 4) if both.any() else None,
            "base_rms_err_mod": round(float(np.mean(b[both, 2] / ppm[both])), 4) if both.any() else None,
            "wrong": int(np.nansum(v[:, 4]) + np.nansum(v[:, 5]))}


def exp_label(pool, n, seed) -> dict:
    """More text inside the black: the SAME n hard conditions as the main
    stress mix (same seed), against the plain marker and today's '#id'."""
    out = {"n": n, "seed": seed, "label": LABEL, "label_id": LABEL_ID, "dicts": {}, "capacity": label_capacity()}
    for dn in (D4, D6):
        designs = designs_for(dn)
        conds = stress_conditions(n, seed, IDS[dn])
        keys = ["base_q1", "black_id_white"] + LABEL_KEYS
        res = run_design_set(pool, keys, dn, conds)
        dgv = np.array([c["dg"] for c in conds])
        htv = np.array([c["ht"] for c in conds])
        crisp = dgv == 0
        rows = {}
        for k in keys:
            v = res[k]
            cells = {}
            for dg in range(4):
                for ht in (False, True):
                    sel = (dgv == dg) & (htv == ht)
                    cells["%s%d" % ("h" if ht else "c", dg)] = paired(res["base_q1"][sel], v[sel], 0)
            shown = 23 if dn == D4 else IDS[dn][3]          # the id the drawings in variants/ use
            texts = [text_layout(m, shown) for m in designs[k].marks if m["k"] == "text"]
            rows[k] = {"title": designs[k].title,
                       "vs_plain": paired(res["base_q1"], v, 0), "vs_id": paired(res["black_id_white"], v, 0),
                       "crisp_vs_plain": paired(res["base_q1"][crisp], v[crisp], 0),
                       "by_print_cell": cells,
                       "wrong_default": int(np.nansum(v[:, 2])), "wrong_subpix": int(np.nansum(v[:, 3])),
                       "corners_subpix": corner_stats(res["base_q1"], v, conds, 1),
                       "texts": [{"text": t, "cap_modules": round(cp, 3), "width_modules": round(wd, 3)}
                                 for t, cp, wd in texts]}
        clean = {}
        for ppm in (6.0, 12.0, 20.0):
            cc = clean_conditions(ppm, 60, 404 + int(ppm), IDS[dn])
            cres = run_design_set(pool, keys, dn, cc)
            clean[str(ppm)] = {k: corner_stats(cres["base_q1"], cres[k], cc, 1) for k in keys}
        out["dicts"][dn] = {"rows": rows, "clean": clean}
        print("   label text, %s done" % dn, flush=True)
    designs = designs_for(D4)
    conds = stress_conditions(n, seed, [0])
    keys = ["ch_base"] + CH_LABEL_KEYS
    res = run_design_set(pool, keys, D4, conds, fn=run_charuco, size=150)
    out["charuco"] = {k: {"title": designs[k].title, "vs_plain": _charuco_paired(res["ch_base"], res[k], conds),
                          "vs_label": _charuco_paired(res["ch_label_black"], res[k], conds)} for k in keys}
    print("   label text, ChArUco done", flush=True)
    return out


# ===================================================================== media


def save_variants(designs, keys, dictionary=D4, mid=23, suffix=""):
    vdir = OUT / "variants"
    vdir.mkdir(parents=True, exist_ok=True)
    info = {}
    for k0 in keys:
        d = designs[k0]
        k = k0 + suffix
        m_id = 0 if d.board else mid
        (vdir / ("%s.svg" % k)).write_text(design_svg(d, dictionary, m_id), encoding="utf-8")
        m = master(k0, dictionary, m_id)
        refl = printed(m, 0, False, 0.55, ("thumb", k, dictionary))
        img = np.clip(refl / PAPER * 255.0, 0, 255).astype(np.uint8)
        h, w = img.shape
        sc = 320.0 / max(h, w)
        cv2.imwrite(str(vdir / ("%s.png" % k)), cv2.resize(img, (int(w * sc), int(h * sc)), interpolation=cv2.INTER_AREA))
        info[k] = {"svg": "variants/%s.svg" % k, "png": "variants/%s.png" % k}
    return info


def legibility(designs, keys, dictionary=D4) -> dict:
    """What the text measures on paper for a few real marker sizes."""
    M = aruco_dict.dictionary_info(dictionary)["modules"]
    out = {}
    for k in keys:
        d = designs[k]
        texts = [m for m in d.marks if m["k"] == "text"]
        if not texts:
            continue
        s, cap, w = text_layout(texts[0], 23)
        row = {"text": s, "cap_modules": round(cap, 3), "grey": texts[0]["g"]}
        for L in (15, 20, 30, 50):
            mod = L / M
            row["cap_mm_at_%dmm" % L] = round(cap * mod, 2)
            row["pt_at_%dmm" % L] = round(cap * mod / CAP * MM_TO_PT, 1)
        out[k] = row
    return out


def camera_examples(designs, keys, path):
    """A contact sheet of what the detector actually saw (one fixed condition per row),
    cropped to the sticker and enlarged without smoothing. Green = detected, red = missed."""
    rows = [("8 px/module, 30 deg tilt, light print", dict(ppm=8.0, tilt=30.0, dg=1, ht=True)),
            ("4.5 px/module, 25 deg, blur 1 px, laser halftone, ink spread 0.08 module",
             dict(ppm=4.5, tilt=25.0, blur=1.0, dg=2, ht=True)),
            ("3.5 px/module, blur 1.2 px, halftone, ink spread 0.12 module, JPEG 60",
             dict(ppm=3.5, tilt=15.0, blur=1.2, dg=3, ht=True, jpeg=60))]
    d0, d1 = detectors(D4)
    tiles, ok_tab = [], []
    for _, over in rows:
        line, oks = [], []
        for k in keys:
            c = dict(NOMINAL, id=23, axis=30.0, roll=12.0, dx=0.3, dy=0.6, gdir=40.0, seed=5, tilt=0.0, bg=0.45)
            c.update(over)
            m = master(k, D4, 23)
            refl = printed(m, c["dg"], c["ht"], c["bg"], ("ex", k, c["dg"], c["ht"], c["bg"]))
            img, Hc = camera(refl, m["geom"], c)
            gt = project(Hc, m["geom"]["corners"])
            ok = score(d0, img, 23, gt)[0]
            oks.append(bool(ok))
            side = float(np.linalg.norm(gt[1] - gt[0]))
            cx, cy = gt.mean(axis=0)
            r = int(math.ceil(side * 0.95))
            x0, y0 = max(0, int(cx) - r), max(0, int(cy) - r)
            crop = img[y0:int(cy) + r, x0:int(cx) + r]
            big = cv2.resize(crop, (160, 160), interpolation=cv2.INTER_NEAREST)
            col = cv2.cvtColor(big, cv2.COLOR_GRAY2BGR)
            col = cv2.copyMakeBorder(col, 4, 4, 4, 4, cv2.BORDER_CONSTANT,
                                     value=(60, 170, 60) if ok else (50, 50, 215))
            line.append(cv2.copyMakeBorder(col, 3, 3, 3, 3, cv2.BORDER_CONSTANT, value=(255, 255, 255)))
        tiles.append(np.hstack(line))
        ok_tab.append(oks)
    cv2.imwrite(str(path), np.vstack(tiles))
    return {"rows": [r[0] for r in rows], "cols": keys, "detected": ok_tab}


def mechanism(path) -> dict:
    """Why white-zone ink hurts and black-border ink does not, on one real trial.

    Searches a fixed list of conditions (deterministic) for one where the
    plain marker and the border id are detected and the white-zone id is not,
    then shows camera image, OpenCV's adaptive threshold (window 13, C 7, as
    the detector's middle pass) and the blob around the marker with its
    polygon approximation (green = a convex quadrilateral, red = not)."""
    keys = ["base_q1", "white_q1_id", "black_id_white"]
    d0, d1 = detectors(D4)
    pick = None
    for c in stress_conditions(1500, 909, [23], ppm=(4.0, 7.0)):
        c.update(ht=False, jpeg=0, noise=min(c["noise"], 4.0))
        ok = []
        for k in keys:
            m = master(k, D4, 23)
            img, Hc = camera(printed(m, c["dg"], False, c["bg"], ("mech", k, c["dg"], c["bg"])), m["geom"], c)
            ok.append(score(d0, img, 23, project(Hc, m["geom"]["corners"]))[0])
        if ok == [1, 0, 1]:
            pick = c
            break
    if pick is None:
        return {}
    tiles, info = [], {"condition": {k: round(pick[k], 3) for k in ("ppm", "tilt", "blur", "noise", "bg", "grad", "dg")},
                       "designs": keys}
    for k in keys:
        m = master(k, D4, 23)
        img, Hc = camera(printed(m, pick["dg"], False, pick["bg"], ("mech", k, pick["dg"], pick["bg"])), m["geom"], pick)
        gt = project(Hc, m["geom"]["corners"])
        cx, cy = gt.mean(axis=0)
        x0, y0 = int(gt[:, 0].min()) - 10, int(gt[:, 1].min()) - 10
        x1, y1 = int(gt[:, 0].max()) + 12, int(gt[:, 1].max()) + 12
        th = cv2.adaptiveThreshold(img, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 13, 7)
        cons, _ = cv2.findContours(th, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
        area = cv2.contourArea(gt.astype(np.float32))
        best = None
        for cn in cons:
            if cv2.pointPolygonTest(cn, (float(cx), float(cy)), False) >= 0:
                a = cv2.contourArea(cn)
                if a > 0.5 * area and (best is None or a < cv2.contourArea(best)):
                    best = cn
        z = 7
        crop = img[y0:y1, x0:x1]
        tcrop = th[y0:y1, x0:x1]
        a = cv2.cvtColor(cv2.resize(crop, None, fx=z, fy=z, interpolation=cv2.INTER_NEAREST), cv2.COLOR_GRAY2BGR)
        b = cv2.cvtColor(cv2.resize(255 - tcrop, None, fx=z, fy=z, interpolation=cv2.INTER_NEAREST),
                         cv2.COLOR_GRAY2BGR)
        quad = False
        if best is not None:
            ap = cv2.approxPolyDP(best, len(best) * 0.03, True)
            quad = len(ap) == 4 and cv2.isContourConvex(ap)
            col = (60, 170, 60) if quad else (50, 50, 215)
            pts_ = ((best.reshape(-1, 2) - [x0, y0]) * z + z // 2).astype(np.int32)
            cv2.polylines(b, [pts_], True, col, 3)
            apx = ((ap.reshape(-1, 2) - [x0, y0]) * z + z // 2).astype(np.int32)
            for q in apx:
                cv2.circle(b, tuple(int(v) for v in q), 7, col, -1)
        info[k] = {"outline_is_quad": bool(quad), "vertices": int(len(ap)) if best is not None else 0}
        pad = lambda im: cv2.copyMakeBorder(im, 6, 6, 6, 6, cv2.BORDER_CONSTANT, value=(255, 255, 255))
        tiles.append(np.vstack([pad(a), pad(b)]))
    h = max(t.shape[0] for t in tiles)
    tiles = [cv2.copyMakeBorder(t, 0, h - t.shape[0], 0, 0, cv2.BORDER_CONSTANT, value=(255, 255, 255)) for t in tiles]
    cv2.imwrite(str(path), np.hstack(tiles))
    return info


def plots(R, designs):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    S1, S2, S3, S4, S5 = "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"
    GREY = "#9b988e"
    plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.edgecolor": "#cbc7bc", "axes.grid": True, "grid.color": "#e2dfd6"})
    sw = R["sweeps"]["ppm"]
    groups = [("ID in the white quiet zone", ["white_q1_id", "white_q1_black", "white_q1_long", "white_q1_touch"]),
              ("ID inside the black border", ["black_id_white", "black_id_grey", "black_id_big", "combo_black"]),
              ("Orientation marks", ["tri_tl", "tri_tl_near", "chamfer_tl", "dot_tl_big"]),
              ("Outside / combos", ["strip_q1", "combo_white", "combo_strip", "white_q2_id"])]
    fig, axs = plt.subplots(1, 4, figsize=(15, 3.6), sharey=True)
    for ax, (title, ks) in zip(axs, groups):
        ax.plot(sw["x"], [r["rate_default"] for r in sw["designs"]["base_q1"]], color=GREY, lw=3, label="plain (q=1)")
        for k, colr in zip(ks, (S1, S2, S3, S4)):
            ax.plot(sw["x"], [r["rate_default"] for r in sw["designs"][k]], color=colr, lw=1.8, marker="o", ms=3, label=k)
        ax.set_xscale("log")
        ax.set_xticks([2.5, 3, 4, 5, 6, 8, 10, 14, 20])
        ax.set_xticklabels(["2.5", "3", "4", "5", "6", "8", "10", "14", "20"])
        ax.set_title(title, fontsize=10)
        ax.set_xlabel("camera px per module")
        ax.legend(fontsize=7, frameon=False, loc="lower right")
    axs[0].set_ylabel("detected (default params)")
    fig.tight_layout()
    fig.savefig(OUT / "sweep_ppm.png", dpi=130)
    plt.close(fig)

    st = R["stress"]["summary"]
    ks = sorted(st, key=lambda k: st[k]["default"]["delta"])
    fig, ax = plt.subplots(figsize=(8, 0.28 * len(ks) + 1))
    for i, k in enumerate(ks):
        s = st[k]["default"]
        ax.plot(s["ci95"], [i, i], color=S1, lw=2)
        ax.plot([s["delta"]], [i], "o", color=S1, ms=5)
    ax.axvline(0, color="#45433e", lw=1)
    ax.set_yticks(range(len(ks)))
    ax.set_yticklabels(ks, fontsize=8)
    ax.set_xlabel("detection rate minus its plain baseline (paired, 95% CI), default params")
    ax.set_title("Stress mix, %d paired trials per design" % R["stress"]["n"], fontsize=10)
    fig.tight_layout()
    fig.savefig(OUT / "stress_forest.png", dpi=130)
    plt.close(fig)

    dose = R["dose"]["summary"]
    fig, axs = plt.subplots(1, 2, figsize=(10, 3.6), sharey=True)
    for ax, ref in zip(axs, ("default", "subpix")):
        for cap, colr in zip(DOSE_CAPS, (S1, S2, S3, S4)):
            xw = [1 - g for g in DOSE_WHITE_G]
            yw = [dose["dw_g%02d_c%02d" % (round(g * 100), round(cap * 100))][ref]["delta"] for g in DOSE_WHITE_G]
            xb = list(DOSE_BLACK_G)
            yb = [dose["db_g%02d_c%02d" % (round(g * 100), round(cap * 100))][ref]["delta"] for g in DOSE_BLACK_G]
            ax.plot(xw, yw, color=colr, lw=1.8, marker="o", ms=3, label="white zone, cap %.2f" % cap)
            ax.plot(xb, yb, color=colr, lw=1.8, ls="--", marker="s", ms=3, label="black border, cap %.2f" % cap)
        ax.axhline(0, color="#45433e", lw=1)
        ax.set_xlabel("text contrast a person sees (1 = black on white, white on black)")
        ax.set_title("%s params" % ref, fontsize=10)
    axs[0].set_ylabel("detection minus plain marker (paired)")
    axs[1].legend(fontsize=6.5, frameon=False, ncol=2)
    fig.tight_layout()
    fig.savefig(OUT / "dose_white_black.png", dpi=130)
    plt.close(fig)

    ch = R["charuco"]["sweep"]
    fig, ax = plt.subplots(figsize=(6, 3.4))
    for k, colr in zip(["ch_base", "ch_ids_white", "ch_ids_black", "ch_label_black", "ch_label_margin"],
                       (GREY, S1, S2, S3, S4)):
        ax.plot(ch["x"], [r["corners_mean"] for r in ch["designs"][k]], color=colr, lw=2 if k == "ch_base" else 1.6,
                marker="o", ms=3, label=k)
    ax.set_xlabel("camera px per marker module")
    ax.set_ylabel("ChArUco corners found (of 12)")
    ax.legend(fontsize=7, frameon=False)
    fig.tight_layout()
    fig.savefig(OUT / "charuco.png", dpi=130)
    plt.close(fig)


# ===================================================================== main


CURATED = ["base_q1", "base_q2", "sheet_plain", "sheet_label",
           "white_q1_id", "white_q1_black", "white_q1_light", "white_q1_long", "white_q1_big", "white_q1_touch",
           "white_q1_corner", "white_q15_id", "white_q2_id", "white_q2_out",
           "black_id_white", "black_id_grey", "black_id_faint", "black_id_inner", "black_id_big",
           "strip_q1", "tri_tl", "tri_tl_black", "tri_tl_near", "arrow_top", "chamfer_tl", "dot_tl", "dot_tl_big",
           "combo_white", "combo_black", "combo_black_inner", "combo_strip", "combo_q2"]
DICT_CHECK = ["base_q1", "white_q1_id", "black_id_white", "black_id_inner", "strip_q1", "tri_tl", "chamfer_tl",
              "combo_white", "combo_black", "combo_black_inner", "combo_strip"]


def media(designs) -> dict:
    out = save_variants(designs, CURATED + [k for k, d in designs.items() if d.family == "charuco"])
    out["camera_examples"] = camera_examples(
        designs, ["base_q1", "white_q1_id", "white_q1_touch", "black_id_white", "tri_tl_near", "combo_white",
                  "combo_black", "combo_strip"], OUT / "camera_examples.png")
    out["mechanism"] = mechanism(OUT / "mechanism.png")
    out.update(save_variants(designs, ["base_q1", "black_id_white"] + LABEL_KEYS + list(charuco_label_designs())))
    save_variants(designs_for(D6), LABEL_KEYS, D6, IDS[D6][3], suffix="_" + D6)
    for dn in (D6, A36):
        save_variants(designs_for(dn), ["combo_white", "combo_black", "combo_black_inner"], dn, IDS[dn][3],
                      suffix="_" + dn)
    return out


def jsonable(o):
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, (np.floating,)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, float):
        return None if not math.isfinite(o) else o
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--quick", action="store_true", help="~1/10 of the trials (smoke run)")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--media-only", action="store_true",
                    help="redraw variants / example sheets into an existing results.json, run no trials")
    ap.add_argument("--only", choices=["label"],
                    help="run one experiment and merge it into an existing results.json")
    args = ap.parse_args()
    if args.only == "label":
        t0 = time.time()
        R = json.loads((OUT / "results.json").read_text(encoding="utf-8"))
        n = 300 if args.quick else 3000
        pool = mp.Pool(args.workers) if args.workers > 1 else None
        try:
            print("label text inside the black: %d paired trials per design" % n, flush=True)
            R["label_text"] = exp_label(pool, n, 101)
        finally:
            if pool:
                pool.close()
                pool.join()
        R["label_text"]["elapsed_s"] = round(time.time() - t0, 1)
        R["label_text"]["generated"] = datetime.datetime.now().isoformat(timespec="seconds")
        R["label_text"]["trials"] = TRIALS[0]
        designs = designs_for(D4)
        R["media"].update(save_variants(designs, ["base_q1", "black_id_white"] + LABEL_KEYS
                                        + list(charuco_label_designs())))
        save_variants(designs_for(D6), LABEL_KEYS, D6, IDS[D6][3], suffix="_" + D6)
        for k in LABEL_KEYS + list(charuco_label_designs()):
            d = designs[k]
            R["designs"][k] = {"family": d.family, "title": d.title, "quiet": d.quiet, "baseline": d.baseline,
                               "chamfer": d.chamfer, "strip": d.strip, "note": d.note, "human": d.human,
                               "marks": d.marks[:4]}
        (OUT / "results.json").write_text(json.dumps(jsonable(R), indent=1) + "\n", encoding="utf-8")
        print("merged label_text into %s (%.0f s, %d trials x 2 detectors)"
              % (OUT / "results.json", time.time() - t0, TRIALS[0]))
        return
    if args.media_only:
        designs = designs_for(D4)
        R = json.loads((OUT / "results.json").read_text(encoding="utf-8"))
        R["media"].update(media(designs))
        (OUT / "results.json").write_text(json.dumps(jsonable(R), indent=1) + "\n", encoding="utf-8")
        plots(R, designs)
        print("media redrawn into %s" % OUT)
        return
    q = 0.1 if args.quick else 1.0

    def N(x):
        return max(8, int(round(x * q)))

    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    designs = designs_for(D4)
    R = {"generated": datetime.datetime.now().isoformat(timespec="seconds"), "quick": bool(args.quick),
         "opencv": cv2.__version__, "numpy": np.__version__, "pdfium": str(pdfium.PDFIUM_INFO),
         "model": {"module_mm_in_pdf": MODULE_MM, "master_px_per_module": S_SINGLE, "board_px_per_module": S_BOARD,
                   "supersample": SS, "paper_white": PAPER, "toner_black": INK, "dot_gain_spread_modules": SPREAD,
                   "dot_gain_tone": TONE, "halftone": "4x4 clustered dot, 1/16 module cell at 64 px/module",
                   "sweeps": SWEEPS, "sweep_overrides": SWEEP_AT,
                   "camera": "pinhole at 5x the target's extent; px/module measured at the marker centre",
                   "detectors": "cv2.aruco.ArucoDetector, DetectorParameters() defaults; and the same with "
                                "cornerRefinementMethod = CORNER_REFINE_SUBPIX",
                   "charuco": "cv2.aruco.CharucoDetector(board) with defaults", "nominal": NOMINAL},
         "ids": IDS}
    R["designs"] = {k: {"family": d.family, "title": d.title, "quiet": d.quiet, "baseline": d.baseline,
                        "chamfer": d.chamfer, "strip": d.strip, "note": d.note, "human": d.human,
                        "marks": d.marks if k not in ("ch_ids_white", "ch_ids_black", "ch_ids_black_inner")
                        else d.marks[:1]}
                    for k, d in designs.items()}
    print("variants -> %s" % (OUT / "variants"), flush=True)
    R["media"] = media(designs)
    R["legibility"] = legibility(designs, CURATED)

    pool = mp.Pool(args.workers) if args.workers > 1 else None
    try:
        print("stress mix: %d designs x %d paired trials" % (len(CURATED), N(3000)), flush=True)
        R["stress"], sres, sconds = exp_stress(pool, designs, CURATED, N(3000), 101)
        print("dose grid (white vs black): %d designs x %d" % (len(dose_designs()), N(1500)), flush=True)
        dkeys = list(dose_designs())
        dose, _, _ = exp_stress(pool, designs, dkeys, N(1500), 202)
        R["dose"] = {"n": dose["n"], "seed": dose["seed"], "summary": dose["summary"],
                     "by_print_cell": {k: v for k, v in dose["by_print_cell"].items() if "_c50" in k},
                     "white_grey": DOSE_WHITE_G, "black_grey": DOSE_BLACK_G, "caps": DOSE_CAPS,
                     "by_ppm": {k: v for k, v in dose["by_ppm"].items() if k.endswith("c50") or k == "base_q1"}}
        gkeys = list(gap_designs())
        print("clearance: %d designs x %d" % (len(gkeys), N(2000)), flush=True)
        gap, _, _ = exp_stress(pool, designs, gkeys, N(2000), 707)
        R["gaps"] = {"n": gap["n"], "seed": gap["seed"], "gaps": GAPS, "summary": gap["summary"],
                     "by_print_cell": gap["by_print_cell"],
                     "by_ppm": gap["by_ppm"]}
        print("one-factor sweeps", flush=True)
        R["sweeps"] = exp_sweeps(pool, designs, CURATED, {"default": N(60), "ppm": N(120)}, 303)
        print("clean corner bias", flush=True)
        R["clean"] = exp_clean(pool, designs, CURATED, N(60), 404)
        print("ChArUco", flush=True)
        R["charuco"] = exp_charuco(pool, designs, N(40), N(600), 505)
        print("other dictionaries", flush=True)
        R["dicts"] = {}
        for dn in (D6, A36):
            dd = designs_for(dn)
            r, _, _ = exp_stress(pool, dd, DICT_CHECK, N(1200), 606, dictionary=dn)
            R["dicts"][dn] = {"n": r["n"], "summary": r["summary"], "by_print_cell": r["by_print_cell"]}
        print("label text inside the black", flush=True)
        R["label_text"] = exp_label(pool, N(3000), 101)
    finally:
        if pool:
            pool.close()
            pool.join()
    R["elapsed_s"] = round(time.time() - t0, 1)
    R["trials"] = TRIALS[0]
    (OUT / "results.json").write_text(json.dumps(jsonable(R), indent=1) + "\n", encoding="utf-8")
    print("plots", flush=True)
    plots(R, designs)
    wrong = sum(v["wrong_default"] + v["wrong_subpix"] for v in R["stress"]["summary"].values())
    print("wrote %s  (%.0f s, %d trials x 2 detectors); wrong ids/poses in the stress mix: %d"
          % (OUT / "results.json", R["elapsed_s"], R["trials"], wrong))


if __name__ == "__main__":
    main()
