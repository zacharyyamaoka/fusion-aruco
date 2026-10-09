#!/usr/bin/env python3
# -*- coding: utf-8 -*-
""""Auto-sizing ArUco tags on a face: three proposals" -- one self-contained page.

    python docs/build_aruco_autosize_report.py
    -> reports/aruco/autosize-proposals.html

Companion of docs/proposal_aruco_autosize.md. Reuses the house style of
docs/build_aruco_reports.py (CSS, JS, dialog wireframe helpers) without
needing OpenCV: every marker and board drawn here comes from
bam_cad_core.aruco_dict / aruco_board, the same tables the add-in prints.

Everything numeric on the page is COMPUTED by the functions in the
"algorithms" section below (the three proposals as small pure functions over
a face's width x height in mm) and by markers.plan_serials for the id-block
example. Nothing is typed in. Stdlib only at build time.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docs"))
sys.path.insert(0, str(ROOT / "CADExporter"))

import build_aruco_reports as B  # noqa: E402  (house style only; cv2 is never touched)
from bam_cad_core import aruco_board, aruco_dict, markers  # noqa: E402
from bam_cad_core.fiducials import ARUCO_DICTS, board_for_tube  # noqa: E402

OUT = ROOT / "reports" / "aruco" / "autosize-proposals.html"
PAGE_FILE = "autosize-proposals.html"
BUILT = date.today().isoformat()
D4 = "DICT_4X4_50"
WARN: list = []
esc = B.esc


def warn(m):
    WARN.append(m)
    print("WARN:", m, file=sys.stderr)


# ================================================================ algorithms
# All lengths in MILLIMETRES here (the add-in's TagSpec is in metres; the
# reference implementation in the design doc is this code, divided by 1000).

STEP_MM = 0.5                 # Zach: "rounding to nearest .5 mm for the defaults"
GAP_RATIO = markers.DEFAULT_GRID_SEPARATION_RATIO      # 0.2 L, OpenCV's tutorial grid
FILL = 0.7                    # Zach's tube rule: the pattern spans 70 % of the short side
MIN_MODULE_MM = 1.2           # three 0.4 mm lines: the smallest module an FDM nozzle prints solid
STRIP_ASPECT = 3.0            # long/short >= 3 -> a strip (tube face), else a plate
CHARUCO_MIN_CORNERS = 4       # our practical floor for a pose board; OpenCV's is 1 (2 x 2 squares)


def modules_of(dictionary: str) -> int:
    return (markers.dictionary_bits(dictionary) or 4) + 2


def rnd_nearest(x: float) -> float:
    return round(x / STEP_MM) * STEP_MM


def rnd_down(x: float) -> float:
    return math.floor(x / STEP_MM + 1e-9) * STEP_MM


@dataclass
class Layout:
    """One sized target on one face. `ok` False = it does not fit / is invalid."""
    kind: str                     # single | grid | charuco
    dictionary: str
    W: float                      # face extent along x (the longest straight edge)
    H: float
    L: float                      # marker length (black square incl. border)
    S: float = 0.0                # charuco square
    cols: int = 1
    rows: int = 1
    sep: float = 0.0              # grid separation
    quiet_modules: float = 1.0
    notes: List[str] = field(default_factory=list)
    ok: bool = True

    @property
    def n(self) -> int:
        return modules_of(self.dictionary)

    @property
    def module(self) -> float:
        return self.L / self.n

    @property
    def quiet(self) -> float:
        return self.quiet_modules * self.module

    @property
    def count(self) -> int:
        if self.kind == "grid":
            return self.cols * self.rows
        if self.kind == "charuco":
            return (self.cols * self.rows) // 2
        return 1

    @property
    def corners(self) -> int:
        return (self.cols - 1) * (self.rows - 1) if self.kind == "charuco" else 0

    @property
    def extent(self) -> Tuple[float, float]:
        if self.kind == "grid":
            return (self.cols * self.L + (self.cols - 1) * self.sep,
                    self.rows * self.L + (self.rows - 1) * self.sep)
        if self.kind == "charuco":
            return (self.cols * self.S, self.rows * self.S)
        return (self.L, self.L)

    @property
    def margins(self) -> Tuple[float, float]:
        w, h = self.extent
        return ((self.W - w) / 2.0, (self.H - h) / 2.0)

    @property
    def coverage(self) -> float:
        w, h = self.extent
        return (w * h) / (self.W * self.H) if self.W and self.H else 0.0

    @property
    def inset(self) -> float:
        """charuco: white between a marker and its square's edge."""
        return (self.S - self.L) / 2.0 if self.kind == "charuco" else self.sep

    def label(self) -> str:
        if self.kind == "single":
            return "single · %.1f mm" % self.L
        if self.kind == "grid":
            return "%d × %d · %.1f mm · gap %.1f" % (self.cols, self.rows, self.L, self.sep)
        return "%d × %d sq · %.1f / %.1f mm" % (self.cols, self.rows, self.S, self.L)

    def board_spec(self, first_id: int = 0) -> aruco_board.BoardSpec:
        return aruco_board.BoardSpec(
            layout=self.kind, dictionary=self.dictionary, first_id=first_id,
            marker_length_m=self.L / 1000.0, rows=self.rows, cols=self.cols,
            separation_m=self.sep / 1000.0, square_length_m=self.S / 1000.0)


def _fail(lay: Layout, why: str) -> Layout:
    lay.ok = False
    lay.notes.append(why)
    return lay


def layout_for_size(kind: str, W: float, H: float, L: float, S: float = 0.0,
                    dictionary: str = D4, quiet_modules: float = 1.0,
                    gap_ratio: float = GAP_RATIO, force: Optional[Tuple[int, int]] = None) -> Layout:
    """THE core rule, shared by all three proposals: given the sizes, lay out
    as many markers as fit, centred, and say whether the result is valid.

    single   the black square plus a quiet zone of `quiet_modules` on every
             side must fit in W x H.
    grid     separation = gap_ratio * L (>= 1 module so each marker keeps its
             own quiet zone); cols/rows = how many fit in W - 2 quiet (resp. H);
             leftover is split into the two outer margins.
    charuco  cols/rows = floor(W / S), floor(H / S); the board needs no outer
             quiet zone (each marker sits inside its white square), but the
             marker must leave >= 1 module of white inside the square:
             L <= S * n / (n + 2), which is 0.75 S for 4x4 -- OpenCV's
             tutorial ratio, derived rather than assumed.
    `force` = (cols, rows) keeps a user-chosen count instead of the maximum.
    """
    n = modules_of(dictionary)
    lay = Layout(kind, dictionary, W, H, L, S, quiet_modules=quiet_modules)
    if L <= 0:
        return _fail(lay, "marker size must be > 0")
    Q = quiet_modules * L / n
    if kind == "single":
        lay.cols = lay.rows = 1
        if L + 2 * Q > min(W, H) + 1e-9:
            return _fail(lay, "%.1f mm + quiet zone %.1f mm per side does not fit the %.1f mm short side"
                         % (L, Q, min(W, H)))
    elif kind == "grid":
        s = max(gap_ratio * L, L / n)          # never below one module
        lay.sep = s
        cols = int(math.floor((W - 2 * Q + s) / (L + s) + 1e-9))
        rows = int(math.floor((H - 2 * Q + s) / (L + s) + 1e-9))
        if force:
            fc, fr = force
            if fc > cols or fr > rows:
                return _fail(lay, "%d × %d of %.1f mm does not fit (at most %d × %d)" % (fc, fr, L, cols, rows))
            cols, rows = fc, fr
        lay.cols, lay.rows = cols, rows
        if cols < 1 or rows < 1:
            return _fail(lay, "no marker of %.1f mm fits with its quiet zone" % L)
        if cols * rows < 2:
            lay.notes.append("only one marker fits: this is a single, not a grid")
    elif kind == "charuco":
        if S <= 0:
            return _fail(lay, "checker size must be > 0")
        if L > S * n / (n + 2) + 1e-9:
            return _fail(lay, "marker %.1f mm leaves < 1 module of white inside a %.1f mm square (max %.2f)"
                         % (L, S, S * n / (n + 2)))
        cols = int(math.floor(W / S + 1e-9))
        rows = int(math.floor(H / S + 1e-9))
        if force:
            fc, fr = force
            if fc > cols or fr > rows:
                return _fail(lay, "%d × %d squares of %.1f mm do not fit (at most %d × %d)" % (fc, fr, S, cols, rows))
            cols, rows = fc, fr
        lay.cols, lay.rows = cols, rows
        if cols < 2 or rows < 2:
            return _fail(lay, "ChArUco needs >= 2 × 2 squares; %.1f mm squares give %d × %d" % (S, cols, rows))
        if lay.corners < CHARUCO_MIN_CORNERS:
            lay.notes.append("%d inner corner(s): fine for a strip, weak for a pose board (we suggest >= %d)"
                             % (lay.corners, CHARUCO_MIN_CORNERS))
    else:
        return _fail(lay, "unknown layout %r" % kind)
    if lay.module < MIN_MODULE_MM:
        lay.notes.append("module %.2f mm < %.1f mm: fine on paper, hard for a 0.4 mm nozzle"
                         % (lay.module, MIN_MODULE_MM))
    mx, my = lay.margins
    if mx < -1e-9 or my < -1e-9:
        return _fail(lay, "pattern overhangs the face")
    try:
        aruco_board.check_spec(lay.board_spec())
    except ValueError as e:                      # the OpenCV-validity gate, verbatim
        return _fail(lay, "OpenCV would refuse it: %s" % e)
    return lay


def default_sizes(kind: str, W: float, H: float, dictionary: str = D4,
                  quiet_modules: float = 1.0, gap_ratio: float = GAP_RATIO) -> Layout:
    """Smart default = Zach's rule generalised, rounded to the nearest 0.5 mm,
    then checked; if rounding up broke the fit, step down half a millimetre.

    single    L = 0.7 x short side
    grid      k across the short side (1 on a strip, 2 on a plate):
              k L + (k - 1) gap = 0.7 x short
    charuco   k squares across the short side (2 on a strip -- Zach's L1/L2 --
              3 on a plate, so the board has >= 4 inner corners); the squares
              fill the short side; marker = S n/(n + 2) (0.75 S for 4x4)
    """
    n = modules_of(dictionary)
    short, long_ = min(W, H), max(W, H)
    strip = long_ / short >= STRIP_ASPECT if short else True
    if kind == "single":
        want = (rnd_nearest(FILL * short), 0.0, 1)
    elif kind == "grid":
        k = 1 if strip else 2
        want = (rnd_nearest(FILL * short / (k + (k - 1) * gap_ratio)), 0.0, k)
    else:
        k = 2 if strip else 3
        S = rnd_nearest(short / k)
        want = (rnd_nearest(S * n / (n + 2)), S, k)
    L, S, k = want
    for _ in range(40):                        # step down until it fits as intended
        lay = layout_for_size(kind, W, H, L, S, dictionary, quiet_modules, gap_ratio)
        across = min(lay.cols, lay.rows)          # how many sit across the short side
        if lay.ok and (kind == "single" or across >= k):
            lay.notes.insert(0, "default: %s" % ("0.7 × short side" if kind != "charuco"
                                                  else "%d squares across the short side" % k))
            return lay
        if kind == "charuco":
            S -= STEP_MM
            L = rnd_down(S * n / (n + 2))
        else:
            L -= STEP_MM
        if L < MIN_MODULE_MM * n:
            break
    return _fail(Layout(kind, dictionary, W, H, L, S), "no default fits this face")


def clamp_to_fit(kind: str, W: float, H: float, L: float, S: float = 0.0,
                 dictionary: str = D4, quiet_modules: float = 1.0) -> Layout:
    """Proposal 1's answer to 'too big': the largest 0.5 mm step at or below
    the typed size that still fits (and still is a board for grid/charuco)."""
    n = modules_of(dictionary)
    L0, S0 = L, S
    for _ in range(400):
        lay = layout_for_size(kind, W, H, L, S, dictionary, quiet_modules)
        if lay.ok and (kind != "grid" or lay.count >= 2):
            if (L, S) != (L0, S0):
                lay.notes.insert(0, "clamped: %.1f%s mm does not fit, using %.1f%s mm"
                                 % (L0, "/%.1f" % S0 if S0 else "", L, "/%.1f" % S if S else ""))
            return lay
        if kind == "charuco":
            S = rnd_down(S - STEP_MM) if S == rnd_down(S) else rnd_down(S)
            L = rnd_down(min(L, S * n / (n + 2)))
        else:
            L = rnd_down(L - STEP_MM) if L == rnd_down(L) else rnd_down(L)
        if L < MIN_MODULE_MM * n:
            break
    return _fail(Layout(kind, dictionary, W, H, L, S), "nothing printable fits")


def layout_for_count(kind: str, W: float, H: float, cols: int, rows: int,
                     dictionary: str = D4, quiet_modules: float = 1.0,
                     gap_ratio: float = GAP_RATIO) -> Layout:
    """Proposal 2 (today's model, finished): the count is given, the size is
    the largest that fills the face, rounded DOWN to 0.5 mm so it still fits;
    leftover goes to the margins."""
    n = modules_of(dictionary)
    q = 2.0 * quiet_modules / n
    if kind == "single":
        L = rnd_down(min(W, H) / (1 + q))
        return layout_for_size(kind, W, H, L, 0.0, dictionary, quiet_modules)
    if kind == "grid":
        L = rnd_down(min(W / (cols + (cols - 1) * gap_ratio + q), H / (rows + (rows - 1) * gap_ratio + q)))
        return layout_for_size(kind, W, H, L, 0.0, dictionary, quiet_modules, gap_ratio, force=(cols, rows))
    S = rnd_down(min(W / cols, H / rows))
    L = rnd_down(S * n / (n + 2))
    return layout_for_size(kind, W, H, L, S, dictionary, quiet_modules, gap_ratio, force=(cols, rows))


def default_count(W: float, H: float, kind: str) -> Tuple[int, int]:
    """Proposal 2's default count from the aspect ratio alone."""
    strip = max(W, H) / min(W, H) >= STRIP_ASPECT
    if kind == "single":
        return 1, 1
    if kind == "grid":
        rows = 1 if strip else 2
    else:
        rows = 2 if strip else 3
    cols = max(rows, int(round(rows * W / H)))
    if W < H:
        cols, rows = rows, max(rows, int(round(rows * H / W)))
    return cols, rows


def candidates(kind: str, W: float, H: float, dictionary: str = D4,
               quiet_modules: float = 1.0, limit: int = 8) -> List[Layout]:
    """Proposal 3: every distinct (cols, rows) that fits, each at the LARGEST
    0.5 mm size that gives it, biggest marker first. The port of ChArUco
    Studio's suggest_board_grids to a face."""
    n = modules_of(dictionary)
    out, seen = [], set()
    if kind == "charuco":
        S = rnd_down(min(W, H) / 2)
        while S >= MIN_MODULE_MM * n * (n + 2) / n:
            L = rnd_down(S * n / (n + 2))
            lay = layout_for_size(kind, W, H, L, S, dictionary, quiet_modules)
            if lay.ok and (lay.cols, lay.rows) not in seen:
                seen.add((lay.cols, lay.rows))
                out.append(lay)
            S -= STEP_MM
    else:
        L = rnd_down(min(W, H) * n / (n + 2 * quiet_modules))
        while L >= MIN_MODULE_MM * n:
            lay = layout_for_size(kind, W, H, L, 0.0, dictionary, quiet_modules)
            if lay.ok and (kind == "single" or lay.count >= 2) and (lay.cols, lay.rows) not in seen:
                seen.add((lay.cols, lay.rows))
                out.append(lay)
            if kind == "single":
                break
            L -= STEP_MM
    if out:
        out[0].notes.append("largest marker")
        most = max(out, key=lambda l: l.count)
        most.notes.append("most markers")
        best = max(out, key=lambda l: l.coverage)
        best.notes.append("best fill %.0f %%" % (100 * best.coverage))
    return out[:limit]


def auto_dictionary(count: int, used: int = 0, bits: int = 4) -> str:
    """Smallest capacity of the chosen family that holds the design's tags
    plus this board plus headroom for serials (same headroom as markers.suggest)."""
    need = used + max(1, count)
    headroom = max(need * 2, need + 10)
    for cap in (50, 100, 250, 1000):
        name = "DICT_%dX%d_%d" % (bits, bits, cap)
        if name in ARUCO_DICTS and ARUCO_DICTS[name] >= headroom:
            return name
    return "DICT_%dX%d_1000" % (bits, bits)


def range_m(L_mm: float, dictionary: str = D4) -> float:
    adv = markers.suggest(1, marker_length_m=L_mm / 1000.0)
    bits = markers.dictionary_bits(dictionary) or 4
    # suggest() picks its own dictionary; recompute for ours (5 px/module, 1080p, 70 deg)
    f_px = (1920 / 2.0) / math.tan(math.radians(70) / 2.0)
    return round((L_mm / 1000.0 / (bits + 2)) * f_px / 5.0, 2) if adv else 0.0


# ------------------------------------------------------------ the test faces

FACES = [
    ("tile", "40 × 40 tile", 40.0, 40.0, "the demo's tag tile (ArUco Extrude)"),
    ("plate", "120 × 70 plate", 120.0, 70.0, "a flat plate"),
    ("plate2", "100 × 80 plate", 100.0, 80.0, "a squarer plate (ChArUco case)"),
    ("tube15", "38.1 × 168.5 tube face", 168.5, 38.1, "1.5 in tube, Zach's 1x5 board (built, x4)"),
    ("tube20", "50.8 × 224.4 tube face", 224.4, 50.8, "2.0 in tube, Zach's 1x5 board (specified)"),
]


def zach_boards() -> list:
    """His two grid boards through fiducials.board_for_tube, the calibrated rule."""
    out = []
    for tube, L, count, outer in ((38.1, 27.1, 5, (38.1, 168.5)), (50.8, 36.0, 5, (50.8, 224.4))):
        gap, w, h = board_for_tube(tube / 1000.0, L / 1000.0, count)
        out.append(dict(tube=tube, L=L, count=count, gap=gap * 1000, w=w * 1000, h=h * 1000, outer=outer))
    return out


def serial_block_example():
    """Three copies of a 1 x 5 strip, serialled; then the template grows to 1 x 6."""
    def run(cols):
        spec = markers.TagSpec(marker_id=0, layout="grid", rows=1, cols=cols, separation_m=0.0053,
                               marker_length_m=0.0265)
        tags = [markers.TagDef(key="strip", component="Tube", spec=spec)]
        occs = [markers.OccurrenceRef("Tube:%d" % k, "Tube") for k in (1, 2, 3)]
        return tags, occs

    tags, occs = run(5)
    planned, res5 = markers.plan_serials(tags, [], occs, "")
    ovs = markers.apply_planned([], planned)
    before = {i.path: list(i.spec.ids) for i in res5.instances}
    tags6, _ = run(6)
    res6 = markers.resolve(tags6, ovs, occs, "")          # same overrides, longer block
    after = {i.path: list(i.spec.ids) for i in res6.instances}
    dups = sorted(res6.duplicates())
    replanned, res6b = markers.plan_serials(tags6, ovs, occs, "")
    fixed = {i.path: list(i.spec.ids) for i in res6b.instances}
    return dict(before=before, after=after, dups=dups, fixed=fixed, replanned=replanned)


# ================================================================ svg helpers

SCALE = 2.3          # px per mm in the layout drawings (one scale for every face)


def T(x, y, s, cls="", size=None, anchor="start", weight=None, width=None, lh=1.3):
    return B.svg_text(x, y, s, cls=cls, size=size, anchor=anchor, weight=weight, width=width, lh=lh)


def svg(inner, w, h, label, style=""):
    return B.svg(inner, w, h, label, style=style)


def ink(lay: Layout, x, y, k, first_id=0, fg="#000"):
    """The target's black ink at (x, y) = the PATTERN's top-left, k px/mm."""
    spec = lay.board_spec(first_id)
    w, h = aruco_board.extent_m(spec)
    parts = []
    for x0, y0, x1, y1 in aruco_board.black_rects_m(spec):
        px = x + (x0 + w / 2.0) * 1000 * k
        py = y + (h / 2.0 - y1) * 1000 * k
        parts.append('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f"/>'
                     % (px, py, (x1 - x0) * 1000 * k, (y1 - y0) * 1000 * k))
    return '<g fill="%s" shape-rendering="crispEdges">%s</g>' % (fg, "".join(parts))


def draw_face(lay: Layout, x, y, k=SCALE, first_id=0, dims=True, title=None, sub=None):
    """A face W x H drawn to scale with the laid-out target centred in it."""
    o = []
    W, H = lay.W * k, lay.H * k
    if title:
        o.append(T(x, y - 8, title, "t-b", 12.5))
    o.append('<rect x="%g" y="%g" width="%g" height="%g" class="face"/>' % (x, y, W, H))
    if not lay.ok:
        o.append(T(x + W / 2, y + H / 2 + 4, "does not fit", "t-bad t-b", 12, "middle"))
        if sub:
            o.append(T(x, y + H + 30, sub, "t-mut", 11, width=int(max(20, W / 6.2))))
        return "".join(o), H + 90
    ew, eh = lay.extent
    mx, my = lay.margins
    px, py = x + mx * k, y + my * k
    if lay.kind != "charuco":                   # the white that must stay white
        q = lay.quiet * k
        o.append('<rect x="%g" y="%g" width="%g" height="%g" class="quiet"/>'
                 % (px - q, py - q, ew * k + 2 * q, eh * k + 2 * q))
    o.append('<rect x="%g" y="%g" width="%g" height="%g" class="extent"/>' % (px, py, ew * k, eh * k))
    o.append(ink(lay, px, py, k, first_id))
    if dims:
        o.append('<line x1="%g" y1="%g" x2="%g" y2="%g" class="dim"/>' % (px, py - 5, px + lay.L * k, py - 5))
        o.append(T(px + lay.L * k / 2, py - 8, "%g" % lay.L, "t-dim", 9.5, "middle"))
        o.append('<line x1="%g" y1="%g" x2="%g" y2="%g" class="dim"/>' % (x - 5, y, x - 5, py))
        o.append(T(x - 7, (py + y) / 2 + 3, "%.1f" % my, "t-dim", 9, "end"))
        if lay.kind == "grid" and lay.cols > 1:
            gx = px + lay.L * k
            o.append('<line x1="%g" y1="%g" x2="%g" y2="%g" class="dim"/>'
                     % (gx, py + eh * k + 5, gx + lay.sep * k, py + eh * k + 5))
            o.append(T(gx + lay.sep * k / 2, py + eh * k + 16, "%.1f" % lay.sep, "t-dim", 9, "middle"))
        if lay.kind == "charuco":
            o.append('<line x1="%g" y1="%g" x2="%g" y2="%g" class="dim"/>'
                     % (px, py + eh * k + 5, px + lay.S * k, py + eh * k + 5))
            o.append(T(px + lay.S * k / 2, py + eh * k + 16, "sq %g" % lay.S, "t-dim", 9, "middle"))
    lines = 0
    if sub:
        width = int(max(20, W / 6.2))
        lines = len(B.wrap_lines(sub, width))
        o.append(T(x, y + H + 30, sub, "t-mut", 11, width=width))
    return "".join(o), H + 30 + lines * 14.3


def numbers(lay: Layout, first_id=0) -> str:
    if not lay.ok:
        return "no fit: " + "; ".join(lay.notes)
    ids = "id %d" % first_id if lay.count == 1 else "ids %d–%d" % (first_id, first_id + lay.count - 1)
    mx, my = lay.margins
    base = "%s · margins %.1f / %.1f · module %.2f mm · %s · ~%.1f m" % (
        lay.label(), mx, my, lay.module, ids, range_m(lay.L, lay.dictionary))
    if lay.kind == "charuco":
        base += " · %d corners" % lay.corners
    return base


def fig_faces(layouts: dict, label: str, first_ids: Optional[dict] = None) -> str:
    """The five test faces in two rows, each with its layout drawn to scale."""
    first_ids = first_ids or {}
    o = []
    y = 30
    for row in (["tile", "plate", "plate2"], ["tube15", "tube20"]):
        x = 46
        hmax = 0
        for key in row:
            name, Wmm = next((n, w) for k, n, w, h, d in FACES if k == key)
            lay = layouts[key]
            s, h = draw_face(lay, x, y, SCALE, first_ids.get(key, 0), title=name,
                             sub=numbers(lay, first_ids.get(key, 0)))
            o.append(s)
            x += Wmm * SCALE + 44
            hmax = max(hmax, h)
        y += hmax + 40
    return svg("".join(o), 1000, int(y), label)


# ---------------------------------------------------------------- dialogs

def dlg_defs(pid):
    return ('<defs><filter id="%s-sh" x="-10%%" y="-10%%" width="120%%" height="130%%">'
            '<feDropShadow dx="0" dy="2" stdDeviation="3" flood-opacity="0.25"/></filter></defs>' % pid)


def dialog_svg(pid, title, rows, w=372, buttons=("OK", "Cancel")):
    """A Fusion command dialog mock, drawn with the house wireframe helper."""
    body = B.dialog(pid, 8, 8, w - 16, title, rows, buttons)
    h = 30 + sum(27 if r[0] != "chip" else 22 for r in rows) + 44 + 20
    return ('<svg viewBox="0 0 %d %d" role="img" aria-label="%s" %s style="max-width:%dpx;margin:0 auto">'
            '<title>%s</title>%s%s</svg>' % (w, h, esc(title), B.FF, w, esc(title), dlg_defs(pid), body))


def dialog_today(pid="d0"):
    tf = today_fit("grid", 120, 70)            # the real numbers today's dialog would show
    rows = [("sel", "Rectangle or face", "1 face selected"),
            ("drop", "Type", "Grid board"), ("drop", "Dictionary", "DICT_4X4_50"),
            ("input", "ID (first id of a board)", "12"), ("input", "Columns", "3"), ("input", "Rows", "2"),
            ("input", "Tile thickness (rectangle)", "3 mm"), ("input", "Black thickness", "0.6 mm"),
            ("drop", "Black sits", "Inlaid (flush with the face)"), ("input", "Quiet zone (modules)", "1.0"),
            ("drop", "Rotate pattern", "0°"), ("input", "Tag name (optional)", ""),
            ("chip", "Grid 3x2 DICT_4X4_50 #12-17 · %.1f mm" % (tf.marker_length_m * 1000), True),
            ("chip", "module %.2f mm · tile 120.0 x 70.0 mm · readable to ~%.1f m"
             % (tf.module_m * 1000, range_m(tf.marker_length_m * 1000)), True)]
    return dialog_svg(pid, "ARUCO TAG  (today)", rows)


def dialog_p1(pid="d1", lay=None):
    lay = lay or default_sizes("grid", 120, 70)
    mx, my = lay.margins
    rows = [("sel", "Face", "1 face selected   120 x 70 mm"),
            ("drop", "Type", "Grid board"),
            ("input", "Marker size", "%g mm" % lay.L, "auto"),
            ("input", "Checker size (ChArUco)", "-", "auto"),
            ("drop", "Dictionary", "Auto -> DICT_4X4_50"),
            ("input", "First id", "12", "auto"),
            ("chip", "%d x %d · gap %.1f · margins %.1f / %.1f mm · ids 12-%d" % (lay.cols, lay.rows, lay.sep, mx, my, 11 + lay.count), True),
            ("chip", "module %.2f mm · readable to ~%.1f m" % (lay.module, range_m(lay.L)), True),
            ("section", "Advanced"),
            ("input", "Quiet zone (modules)", "1.0"), ("input", "Gap (x marker)", "0.2"),
            ("input", "Black thickness", "0.6 mm"), ("drop", "Black sits", "Inlaid"),
            ("drop", "Grow cells", "Off"), ("drop", "Rotate", "0°"), ("input", "Tag name", "")]
    return dialog_svg(pid, "ARUCO TAG  ·  P1 size first", rows)


def dialog_p2(pid="d2", lay=None):
    lay = lay or layout_for_count("grid", 120, 70, 3, 2)
    mx, my = lay.margins
    rows = [("sel", "Face", "1 face selected   120 x 70 mm"),
            ("drop", "Type", "Grid board"),
            ("input", "Columns x rows", "%d x %d" % (lay.cols, lay.rows), "auto"),
            ("ro", "Marker size", "%g mm  (fills the face)" % lay.L),
            ("drop", "Dictionary", "Auto -> DICT_4X4_50"),
            ("input", "First id", "12", "auto"),
            ("chip", "gap %.1f · margins %.1f / %.1f mm · ids 12-%d" % (lay.sep, mx, my, 11 + lay.count), True),
            ("chip", "module %.2f mm · readable to ~%.1f m" % (lay.module, range_m(lay.L)), True),
            ("section", "Advanced"),
            ("input", "Quiet zone (modules)", "1.0"), ("input", "Gap (x marker)", "0.2"),
            ("input", "Black thickness", "0.6 mm"), ("drop", "Black sits", "Inlaid"),
            ("drop", "Grow cells", "Off"), ("drop", "Rotate", "0°"), ("input", "Tag name", "")]
    return dialog_svg(pid, "ARUCO TAG  ·  P2 count first", rows)


def dialog_p3(pid="d3", cands=None, pick=None):
    cands = cands or candidates("grid", 120, 70)
    pick = pick or default_sizes("grid", 120, 70)
    rows = [("sel", "Face", "1 face selected   120 x 70 mm"),
            ("drop", "Type", "Grid board"),
            ("drop", "Layout", "%s   (default)" % pick.label())]
    for c in cands[:6]:
        tag = "; ".join(n for n in c.notes if n in ("largest marker", "most markers") or n.startswith("best"))
        rows.append(("chip", "%s%s" % (c.label(), ("   <- " + tag) if tag else ""), c.label() != pick.label()))
    rows += [("input", "Marker size >=", "", ""), ("input", "Markers >=", "", ""),
             ("drop", "Dictionary", "Auto -> DICT_4X4_50"),
             ("input", "First id", "12", "auto"),
             ("section", "Advanced"),
             ("input", "Quiet zone (modules)", "1.0"), ("input", "Black thickness", "0.6 mm"),
             ("drop", "Black sits", "Inlaid"), ("drop", "Grow cells", "Off"), ("input", "Tag name", "")]
    return dialog_svg(pid, "ARUCO TAG  ·  P3 pick a layout", rows)


# ================================================================ figures

def fig_anatomy() -> str:
    """The shared geometry: one grid and one ChArUco on the 120 x 70 plate, annotated."""
    k = 3.0
    g = default_sizes("grid", 120, 70)
    c = default_sizes("charuco", 120, 70)
    y0 = 86                                   # face top; labels above it never meet the title
    o = [T(60, 20, "Face box W × H → sizes → counts → centred. Every number here is computed by layout_for_size().", "t-b", 13)]
    for i, (lay, x0) in enumerate(((g, 56), (c, 548))):
        o.append(T(x0, 46, "%s on the 120 × 70 plate" % ("Grid" if i == 0 else "ChArUco"), "t-b", 12.5))
        s, _ = draw_face(lay, x0, y0, k, 0, dims=False)
        o.append(s)
        W, H = lay.W * k, lay.H * k
        ew, eh = lay.extent
        mx, my = lay.margins
        px, py = x0 + mx * k, y0 + my * k
        # W / H dimension lines, well clear of the face
        o.append('<line x1="%g" y1="%g" x2="%g" y2="%g" class="dim"/>' % (x0, y0 + H + 30, x0 + W, y0 + H + 30))
        o.append(T(x0 + W / 2, y0 + H + 44, "W = %g (along the longest straight edge)" % lay.W, "t-dim", 10.5, "middle"))
        o.append('<line x1="%g" y1="%g" x2="%g" y2="%g" class="dim"/>' % (x0 + W + 14, y0, x0 + W + 14, y0 + H))
        o.append(T(x0 + W + 20, y0 + H / 2 + 4, "H = %g" % lay.H, "t-dim", 10.5))
        if lay.kind == "grid":
            o.append(T(x0, 66, "L = %g (black square incl. border) · quiet ≥ 1 module (%.1f) · x margin %.1f"
                       % (lay.L, lay.quiet, mx), "t-dim", 10))
            o.append('<line x1="%g" y1="%g" x2="%g" y2="%g" class="dim"/>' % (px, 72, px + lay.L * k, 72))
            o.append(T(px + lay.L * k + lay.sep * k / 2, y0 + H + 14, "gap = 0.2 L = %.1f" % lay.sep, "t-dim", 10, "middle"))
            o.append(T(x0 + W - 4, y0 + H + 14, "y margin = leftover / 2 = %.1f" % my, "t-dim", 10, "end"))
        else:
            o.append(T(x0, 66, "S = %g square · L = %g marker (≤ 0.75 S) · margins %.1f / %.1f"
                       % (lay.S, lay.L, mx, my), "t-dim", 10))
            o.append('<line x1="%g" y1="%g" x2="%g" y2="%g" class="dim"/>' % (px, 72, px + lay.S * k, 72))
            o.append(T(x0 + W / 2, y0 + H + 14, "inset (S − L)/2 = %.2f ≥ 1 module" % lay.inset, "t-dim", 10, "middle"))
            o.append(T(x0 + W / 2, y0 + H + 62, "no outer quiet zone needed: each marker sits in its own white square",
                       "t-mut", 10.5, "middle"))
    return svg("".join(o), 1012, y0 + 70 * k + 78, "Shared geometry of a grid and a ChArUco board on a face")


def fig_serial(ex: dict) -> str:
    """Three copies of a strip: serialled at 1 x 5, then the template grows to 1 x 6."""
    o = []
    rows = [("1 × 5, serialled by ArUco IDs", ex["before"], set()),
            ("template edited to 1 × 6: same first ids, longer blocks", ex["after"], set(ex["dups"])),
            ("ArUco IDs → Number duplicates again", ex["fixed"], set())]
    y = 26
    for title, table, dups in rows:
        o.append(T(16, y, title, "t-b", 13))
        x = 16
        for path in ("Tube:1", "Tube:2", "Tube:3"):
            ids = table[path]
            o.append(T(x, y + 22, path, "t-mono t-mut", 11.5))
            for j, i in enumerate(ids):
                dup = ("aruco4x4#%d" % i) in dups
                o.append('<rect x="%g" y="%g" width="30" height="22" rx="6" class="%s"/>' % (x + j * 34, y + 30, "b-warn" if dup else "b-good"))
                o.append(T(x + j * 34 + 15, y + 45, str(i), "t-mono t-b", 12, "middle"))
            x += 330
        y += 84
    o.append(T(16, y - 10, "resolve() flags #5 and #10 as printed twice; plan_serials hands the colliding copies the next free blocks "
                           "(the first copy keeps its ids). Overrides store only the FIRST id, so the block length always follows the template.",
               "t-ink2", 12, width=118))
    return svg("".join(o), 1000, y + 24, "What happens to serialled copies when a board's marker count changes")


def fig_candidates(key: str, kind: str, n: int = 3) -> str:
    """The first n candidates for one face, drawn side by side."""
    name, W, H = next((nm, w, h) for k, nm, w, h, d in FACES if k == key)
    cands = candidates(kind, W, H)[:n]
    o = []
    x = 46
    for c in cands:
        tag = "; ".join(t for t in c.notes if t in ("largest marker", "most markers") or t.startswith("best"))
        s, h = draw_face(c, x, 30, SCALE, 0, title=c.label(), sub=(tag or "") + " · margins %.1f / %.1f" % c.margins)
        o.append(s)
        x += W * SCALE + 40
    return svg("".join(o), max(1000, x), 30 + H * SCALE + 90, "Ranked candidates for the %s (%s)" % (name, kind))


# ================================================================ page bits

def h2(sid, text, toc):
    toc.append((2, sid, text))
    return '<h2 id="%s">%s</h2>' % (sid, text)


def h3(sid, text, toc):
    toc.append((3, sid, text))
    return '<h3 id="%s">%s</h3>' % (sid, text)


def table(head, rows, cls="md"):
    th = "".join("<th>%s</th>" % h for h in head)
    tb = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % c for c in r) for r in rows)
    return '<div class="tablewrap"><table class="%s"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>' % (cls, th, tb)


def code_of(*fns) -> str:
    import inspect
    return '<pre class="code"><code>%s</code></pre>' % esc("\n\n".join(inspect.getsource(f) for f in fns))


EXTRA_CSS = r"""
.dg .face{fill:#fff; stroke:var(--line-2); stroke-width:1.2}
.dg .quiet{fill:var(--acc-weak); opacity:.55; stroke:none}
.dg .extent{fill:none; stroke:var(--acc); stroke-width:.9; stroke-dasharray:4 3}
.dg .dim{stroke:var(--violet); stroke-width:1}
.dg .t-dim{fill:var(--violet); font-family:var(--mono)}
.k-computed-from-the-code{background:var(--good-weak); color:var(--good)}
.k-mock{background:var(--acc-weak); color:var(--acc)}
.trio{display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr)); gap:14px; margin:22px 0; align-items:start}
.trio figure.fig{margin:0}
.pc{display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr)); gap:14px; margin:14px 0}
.pc div{background:var(--surface); border:1px solid var(--line); border-radius:12px; padding:10px 14px; font-size:15px}
.pc div b{display:block; margin-bottom:4px}
.pc ul{margin:0; padding-left:1.2em}
.req{counter-reset:rq; list-style:none; padding:0}
.req li{counter-increment:rq; position:relative; padding-left:46px; margin:6px 0}
.req li::before{content:"R" counter(rq); position:absolute; left:0; top:0; font-weight:800; color:var(--muted); font-family:var(--mono)}
.badge.ok{color:var(--good); background:var(--good-weak); border:1px solid var(--good)}
.badge.part{color:var(--warn); background:var(--warn-weak); border:1px solid var(--warn)}
.badge.no{color:var(--bad); background:var(--bad-weak); border:1px solid var(--bad)}
.badge.rec{color:#fff; background:var(--good); border:1px solid var(--good)}
table.md td.num{font-family:var(--mono); font-size:13.5px; white-space:nowrap}
.cands span{display:inline-block; font:12.5px/1.4 var(--mono); padding:1px 7px; margin:2px 3px 2px 0; border-radius:6px; background:var(--surface-2); border:1px solid var(--line)}
.cands span.pick{border-color:var(--good); background:var(--good-weak)}
"""


def page(body: str, toc) -> str:
    pages = list(B.PAGES) + [("auto", PAGE_FILE, "Auto-sizing")]
    nav = "".join('<a href="%s"%s>%s</a>' % (fn, ' class="on" aria-current="page"' if k == "auto" else "", lab)
                  for k, fn, lab in pages)
    brand = B.marker_svg(D4, 7, quiet=0.6, cls="", label="ArUco DICT_4X4_50 id 7", grid=aruco_dict.marker_grid(D4, 7))
    title = "Auto-sizing ArUco tags on a face: three proposals"
    desc = ("Three proposals for computing an ArUco tag's layout from the face it is put on: what Zach sets, "
            "what is computed, worked examples on real faces, and a recommendation.")
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%s</title>
<meta name="description" content="%s">
<style>%s%s</style></head>
<body>
<header class="topbar"><div class="in">
<a class="brand" href="index.html">%s<span>ArUco in Fusion</span></a>
<nav aria-label="Reports">%s</nav><span class="sp"></span>
<button id="themebtn" type="button" aria-label="Switch colour theme">Theme: auto</button>
</div></header>
<div class="wrap"><div class="layout">
%s
<main class="content">
%s
<div class="foot">Generated %s by <code>docs/build_aruco_autosize_report.py</code>; the design doc is
<code>docs/proposal_aruco_autosize.md</code>. Every layout and number on this page is computed by the proposal functions in that
script, and every marker is drawn from <code>bam_cad_core.aruco_board</code> (the ink the add-in prints). Nothing loads from the network.
This page is not yet on the index; reach it from the “Auto-sizing” tab above or <a href="index.html">go back to the overview</a>.
Sibling proposal from the same day: <a href="entry-points-proposals.html">entry points and UI</a>.</div>
</main></div></div>
<script>%s</script>
</body></html>""" % (esc(title), esc(desc), B.CSS, EXTRA_CSS, brand, nav, B.toc_html(toc), body, BUILT, B.JS)


def main() -> int:
    html_ = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html_, encoding="utf-8")
    print("wrote %s (%s)" % (OUT, B.fmt_bytes(OUT.stat().st_size)))
    if WARN:
        print("%d warning(s)" % len(WARN), file=sys.stderr)
    return 0


# ================================================================ the page

def today_fit(kind, W, H, cols=3, rows=2):
    """What the add-in computes TODAY for a face (markers.fit_board, no rounding)."""
    spec = markers.TagSpec(layout=kind, rows=rows if kind != "single" else 1, cols=cols if kind != "single" else 1)
    out = markers.fit_board(W / 1000.0, H / 1000.0, spec)
    return out


def cands_html(kind, W, H, pick: Layout) -> str:
    cs = candidates(kind, W, H)
    out = []
    for c in cs:
        tag = next((t for t in c.notes if t in ("largest marker", "most markers") or t.startswith("best")), "")
        cls = ' class="pick"' if c.label() == pick.label() else ""
        out.append("<span%s>%s%s</span>" % (cls, esc(c.label()), (" · " + tag) if tag else ""))
    return '<div class="cands">%s</div>' % "".join(out)


def build() -> str:
    toc: list = []
    b: list = []
    P1 = {kind: {k: default_sizes(kind, w, h) for k, _, w, h, _ in FACES} for kind in ("single", "grid", "charuco")}
    P2 = {kind: {k: layout_for_count(kind, w, h, *default_count(w, h, kind)) for k, _, w, h, _ in FACES}
          for kind in ("single", "grid", "charuco")}
    zb = zach_boards()
    ex = serial_block_example()
    t15, t20 = P1["grid"]["tube15"], P1["grid"]["tube20"]
    c15, c20 = P1["charuco"]["tube15"], P1["charuco"]["tube20"]

    b.append('<div class="hero"><div class="eyebrow">Design proposal · %s · branch <code>%s</code></div>'
             '<h1>Auto-sizing ArUco tags on a face: three proposals</h1>'
             '<p class="lede">Once you tag a face, nearly every number should be computed. Three ways to get there '
             '(you set the <b>marker size</b>; you set the <b>count</b>; you <b>pick from ranked layouts</b>), each run on '
             'five real faces by real code, and one recommendation.</p>'
             '<p class="meta">Companion doc: <code>docs/proposal_aruco_autosize.md</code>. Nothing here is implemented yet; '
             'the “today” section is what the add-in on this branch does.</p></div>' % (BUILT, B.BRANCH))

    # ---------------------------------------------------------------- tl;dr
    b.append(B.callout(
        '<p><b>Recommendation: Proposal 1 (size first) with Proposal 3\'s ranked list as a “Layout” dropdown under it.</b> '
        'You type one number (two for ChArUco); the add-in fills the face with as many markers as fit at that size, centred, '
        'quiet zone guaranteed, dictionary and first id chosen. The default size is your own tube rule (pattern = 70 %% of the '
        'short side) rounded to the nearest 0.5 mm, and it reproduces both of your boards: '
        '<b>%d × %d at %g mm</b> on the 38.1 mm tube (you built 27.1), <b>%d × %d at %g mm</b> on the 50.8 mm tube (you specified 36.0); '
        'ChArUco on the 38.1 tube comes out <b>%g / %g mm</b> squares / markers, your L2 exactly.</p>'
        '<p><b>Are we doing it already?</b> Partly, and backwards. Today the dialog takes <i>columns × rows</i> and computes the marker '
        'size to fill the face (that is Proposal 2\'s model, minus rounding and minus the checks). The marker size itself is not an '
        'input on a face at all. Nothing is rounded to 0.5 mm, the dictionary is never auto-picked, and a face that is not a rectangle is '
        'measured by its vertex box only. Effort to get to the recommendation: about three days, almost all of it pure Python with tests.</p>'
        % (t15.cols, t15.rows, t15.L, t20.cols, t20.rows, t20.L, c15.S, c15.L), "good", "TL;DR"))

    # ---------------------------------------------------------------- now
    b.append(h2("now", "What we do now", toc))
    b.append('<p>The ArUco Tag dialog on this branch (<code>ArucoMarkers/aruco_commands.py</code>) and the sizing it runs '
             '(<code>markers.fit_board</code>), described exactly:</p>')
    tf = {k: today_fit("grid", w, h) for k, _, w, h, _ in FACES}
    ts = {k: today_fit("single", w, h) for k, _, w, h, _ in FACES}
    tc = {k: today_fit("charuco", w, h) for k, _, w, h, _ in FACES}
    rows = []
    for k, name, w, h, _ in FACES:
        rows.append([name, "%.2f mm" % (ts[k].marker_length_m * 1000),
                     "%.2f mm · gap %.2f" % (tf[k].marker_length_m * 1000, tf[k].separation_m * 1000),
                     "sq %.2f · marker %.2f" % (tc[k].square_length_m * 1000, tc[k].marker_length_m * 1000)])
    b.append('<div class="trio">%s<div>%s</div></div>' % (
        B.fig(dialog_today(), "The dialog today. Columns and rows are inputs; the marker size is derived and only shown in the info text.",
              "mock"),
        '<ul>'
        '<li><b>Inputs:</b> target (sketch profile or planar face), type, dictionary, first id (next free), columns, rows, tile '
        'thickness (profile only), black thickness and whether it is inlaid / raised / half (<code>depth_m</code>, <code>raise_m</code>), '
        'quiet zone in modules, rotate, name. Cell growth (<code>grow_m</code>, off by default) is a separate option.</li>'
        '<li><b>Face measure:</b> <code>aruco_tags.face_frame</code>: x along the longest straight edge, W × H = the extent of the face\'s '
        '<i>vertices</i> along x / y. Holes and notches are invisible to it; a face with no straight edge and no vertices (a circle) '
        'measures 0 × 0.</li>'
        '<li><b>Sizing:</b> <code>fit_board</code> fills the box: single = <code>min(W, H) · n / (n + 2q)</code>; grid = the largest L with gap '
        '0.2 L and quiet zone q modules for the given cols × rows; ChArUco = the largest square with marker 0.75 S. No rounding '
        '(the stored length is rounded to 0.1 µm). The pattern is centred in the box.</li>'
        '<li><b>Checks:</b> the info text warns when a module is under 1.2 mm and when the ids run off the dictionary. Nothing stops an '
        'absurd count (1 × 40 on a tile).</li>'
        '<li><b>Not done:</b> no marker-size input on a face, no 0.5 mm rounding, no dictionary auto-pick, no minimum-count sense '
        '(a “grid” of 1 × 1 is accepted), no fit test against the real face.</li></ul>'
        + table(["Face", "single", "grid 3 × 2 (dialog default)", "ChArUco 3 × 2"], rows)))

    # ---------------------------------------------------------------- asked
    b.append(h2("asked", "What Zach asked for", toc))
    b.append('<blockquote class="quote"><p>“I want to make sure its very ergonomic that once we tag the face that a lot of the '
             'parameters for the size of the marker are being autocomputed. … I think the prior aruco studio we have could be a '
             'reference.. in terms of like spacing around etc... I think generally what I want to control is the marker size. and '
             'checker size (if its charuco board)... all the other spacing and stuff I think can then be autocalculated off those. '
             'it should always kinda fit within the face. markers shouldn\'t ever be overlapping. Lets have smart good defaults. '
             'I prefer rounding to nearest .5 mm for the defaults on the marker sizes.”</p><p>— 2026-10-07; and earlier that day, '
             'on OpenCV-tutorial ratios: “good defaults always a good idea”.</p></blockquote>')
    b.append('<ol class="req">'
             '<li><b>Tag a face, get numbers.</b> After the face is picked, the dialog is already valid.</li>'
             '<li><b>The marker size is the control</b> (plus the checker size for ChArUco). Spacing, counts, margins follow.</li>'
             '<li><b>Always inside the face.</b></li><li><b>Markers never overlap.</b></li>'
             '<li><b>Smart defaults</b>, rounded to the nearest 0.5 mm.</li>'
             '<li><b>The Studio\'s spacing as the reference</b>: its boards are ChArUco with checker, marker and margin in mm; its '
             '<code>suggest_board_grids</code> ranks the grids that fit an envelope. Your tube boards (<code>fiducials.board_for_tube</code>) '
             'are the other reference: marker ≈ 0.7 × tube, gap = margin = (tube − marker)/2.</li>'
             '<li><b>OpenCV-tutorial ratios as defaults</b>: gap = 0.2 × marker, ChArUco marker = 0.75 × square.</li>'
             '<li><i>(implied)</i> <b>Valid for OpenCV</b> every time, and <b>serial ids keep working</b> when a board\'s count changes.</li></ol>')

    # ---------------------------------------------------------------- shared
    b.append(h2("shared", "The geometry every proposal shares", toc))
    b.append('<p>All three proposals compute the same things from the same face box; they differ only in which number you hold. '
             'Lengths in mm; <code>n</code> = modules per marker side (6 for 4×4, 7 for 5×5); <code>q</code> = quiet zone in modules '
             '(default 1).</p>')
    b.append(B.fig(fig_anatomy(), "The named lengths. <b>Grid:</b> the quiet zone (blue) is the white that must surround the pattern; the gap "
                   "between markers is 0.2 L (never below one module, so each marker keeps its own quiet zone). <b>ChArUco:</b> the squares may run "
                   "to the face edge; what matters is the white inside each square around its marker, which is why the marker must be "
                   "≤ S·n/(n+2) — 0.75 S for 4×4, the OpenCV tutorial ratio, here derived rather than assumed.", "computed from the code"))
    b.append('<div class="formula">'
             'quiet Q = q · L / n                      (grid, single)\n'
             'gap   s = max(0.2 L, L / n)              (grid)\n'
             'cols  = ⌊(W − 2Q + s) / (L + s)⌋          rows = ⌊(H − 2Q + s) / (L + s)⌋   (grid)\n'
             'cols  = ⌊W / S⌋                           rows = ⌊H / S⌋                     (ChArUco)\n'
             'extent = cols·L + (cols−1)·s   |   cols·S          margins = (W − extent_w)/2, (H − extent_h)/2\n'
             'invariants:  margins ≥ Q (grid/single) · margins ≥ 0 (ChArUco) · s &gt; 0 · L &lt; S · ids &lt; capacity\n'
             'rounding:    defaults → nearest 0.5 mm, then re-check; if rounding UP broke the fit, step down 0.5\n'
             '             sizes derived from a count → DOWN to 0.5 mm (they are maxima)\n'
             'limits:      module ≥ 1.2 mm (0.4 mm nozzle)   ·   grid ≥ 2 markers   ·   ChArUco ≥ 2×2 squares (OpenCV), ≥ 4 corners advised'
             '</div>')
    b.append('<p><b>Hard invariants, checked by code, not by eye:</b> every candidate is run through <code>aruco_board.check_spec</code> '
             '(the gate that mirrors what cv2 refuses: a grid with separation 0, a ChArUco under 2 × 2 or with marker ≥ square, ids past '
             'the dictionary) and through the margin test above. A layout that fails either is never offered; the dialog says why.</p>')
    b.append('<p><b>Dictionary auto-pick.</b> Keep the family (4×4 unless you choose otherwise, since fewer bits means bigger modules at '
             'the same marker size) and pick the capacity from the design: smallest of 50 / 100 / 250 / 1000 holding <i>ids already used '
             '+ this board + headroom</i>, with headroom = max(2 × need, need + 10) as <code>markers.suggest</code> already does. '
             'Examples: a 5-marker strip in an empty design → <code>%s</code>; the same with 40 ids in use → <code>%s</code>; a 13-marker '
             'ChArUco with 60 in use → <code>%s</code>. Growing capacity never reprints anything: 4X4_50 is the first 50 patterns of 4X4_1000.</p>'
             % (auto_dictionary(5, 0), auto_dictionary(5, 40), auto_dictionary(13, 60)))
    b.append('<p><b>What does not change the footprint.</b> <code>depth_m</code> / <code>raise_m</code> (inlaid vs raised black) and '
             '<code>grow_m</code> (cell growth, clamped to the black square) are thickness and manufacturing choices; none of them moves a '
             'marker\'s side or the layout. Two things they do touch: a raised layer lifts the detector frame by <code>raise_m</code> '
             '(already handled in <code>detector_frame</code>), and an inlay deeper than the body under the face is a hole — a check the '
             'dialog could add (face body thickness vs <code>depth_m</code>), not a sizing input.</p>')
    b.append(h3("shared-shape", "When the face is not a rectangle", toc))
    b.append('<p>Honestly: the face box is all we measure today, and all any proposal <i>sizes</i> from. What is computable beyond it:</p>'
             '<ul><li><b>Detect it.</b> <code>face.area</code> vs box area: a rectangle is 1.0; a plate with a hole 0.9; an L-shape 0.75; '
             'a disc 0.785. Below 0.98 the dialog says “not a rectangle: sized to the %g × %g box, checked against the face”.</li>'
             '<li><b>Test the candidate against the real face.</b> Fusion answers “is this point on the face” '
             '(<code>face.evaluator.getParameterAtPoint</code> + <code>isParameterOnFace</code>). Sample the pattern outline (corners, edge '
             'midpoints, every marker\'s four corners) and the quiet-zone outline; any point off the face means the layout does not fit. '
             'Then shrink: P1 steps the size down 0.5 mm, P2 drops a column or row, P3 filters its list. A hole in the quiet zone '
             'passes (as the demo tile\'s mount hole does); a hole under a marker fails. On a disc this finds the inscribed square (d/√2) '
             'in a few steps.</li>'
             '<li><b>Not computable (cheaply):</b> the best <i>placement</i>. We centre in the box; for an L-shape the right spot is '
             'off-centre. The honest answer is the existing one — sketch a rectangle where you want the tag and use ArUco Extrude, or '
             'split the face.</li></ul>' % (120, 70))

    # ---------------------------------------------------------------- P1
    b.append(h2("p1", "Proposal 1 — Size first: you set the marker, the face fills", toc))
    b.append('<div class="trio">%s<div>'
             '<p><b>You set:</b> Marker size (one number). ChArUco adds Checker size. Both carry an <i>auto</i> button that restores the '
             'default. Dictionary and first id default to <i>auto</i>. Everything else is under <i>Advanced</i>, pre-filled.</p>'
             '<p><b>Computed:</b> cols × rows (as many as fit), gap, quiet zone, margins (leftover split, pattern centred), dictionary, '
             'ids. The info chips show the layout and the readable range.</p>'
             '<p><b>Default size</b> (your rule, generalised): single L = 0.7 × short side. Grid: <i>k</i> markers across the short side '
             'span 70 %% of it, k = 1 on a strip (long/short ≥ 3), 2 on a plate: L = 0.7·short / (k + 0.2(k−1)). ChArUco: k squares fill '
             'the short side, k = 2 on a strip (your L1/L2), 3 on a plate (so ≥ 4 inner corners); marker = 0.75 S. All rounded to the '
             'nearest 0.5 mm, then checked; if rounding up lost a marker or broke the fit, step down.</p>'
             '<p><b>Can\'t fit:</b> a typed size that overhangs (or leaves a “grid” with one marker) is <b>clamped</b> to the largest '
             '0.5 mm step that works, and the chip says so in orange: <i>%s</i>. Nothing is silently accepted and nothing is rejected: the '
             'dialog is always valid.</p>'
             '<p><b>Not a rectangle:</b> the fit test above, shrinking the size. The chip adds “sized to the box, shrunk for the hole”.</p>'
             '</div></div>' % (B.fig(dialog_p1(), "Proposal 1 on the 120 × 70 plate: one input, the rest computed.", "mock"),
                               esc(clamp_to_fit("grid", 40, 40, 30).notes[0])))
    b.append(h3("p1-ex", "Worked examples (defaults, computed)", toc))
    b.append(B.fig(fig_faces(P1["grid"], "Proposal 1 defaults, grid board, five faces"),
                   "<b>Grid board, default size.</b> The two tube faces reproduce your boards (1 × 5 across the long edge; drawn with x "
                   "along the longest edge, so “cols” is the count along the tube). Blue = the quiet zone that must stay white; violet "
                   "numbers = marker, margin and gap in mm.", "computed from the code"))
    b.append(B.fig(fig_faces(P1["charuco"], "Proposal 1 defaults, ChArUco board, five faces"),
                   "<b>ChArUco, default size.</b> Two squares across a strip, three across a plate. The 38.1 tube gives 19 / 14 mm, your L2; "
                   "the 50.8 tube gives 25 / 18.5 (your L1 is 25.4 / 19, the inch not being a 0.5 mm multiple).", "computed from the code"))
    rows = []
    for k, name, w, h, _ in FACES:
        s = P1["single"][k]
        rows.append([name, "%g mm" % s.L, "%.1f / %.1f" % s.margins, "%.2f mm" % s.module, "~%.1f m" % range_m(s.L)])
    b.append(table(["Face", "single marker (0.7 × short side)", "margins", "module", "readable to"], rows))
    b.append(h3("p1-pc", "Pros, cons, effort, ids", toc))
    b.append('<div class="pc"><div><b>Pros</b><ul><li>Exactly the words: one number in, a board out.</li><li>The default is the '
             'calibrated tube rule, so existing boards re-create themselves.</li><li>Always valid; clamping explains itself.</li>'
             '<li>Pure function; 20 unit tests cover it.</li></ul></div>'
             '<div><b>Cons</b><ul><li>You cannot ask for “2 × 3” directly; you move the size until the count is what you want '
             '(P3 fixes that).</li><li>On a plate the 2-across default is a heuristic; some plates want 1 × 2 big markers '
             '(the chips show the alternative).</li></ul></div>'
             '<div><b>Effort ~2 days</b><ul><li><code>markers.autosize()</code> (this page\'s functions in metres) + tests: 1 d</li>'
             '<li>Dialog rewrite, chips, auto buttons: ½ d</li><li>Face area / fit test probe in Fusion: ½ d</li></ul></div>'
             '<div><b>Serial ids</b><ul><li>A size edit changes the count, so the id <i>block</i> changes length. See '
             '<a href="#serial">the id-block example</a>: existing copies keep their first id; blocks that now overlap are flagged by '
             '<code>resolve()</code> and “Number duplicates” fixes them. ArUco Edit should say “count changed 5 → 6: run ArUco IDs”.</li></ul></div></div>')

    # ---------------------------------------------------------------- P2
    b.append(h2("p2", "Proposal 2 — Count first: today's model, finished", toc))
    b.append('<div class="trio">%s<div>'
             '<p><b>You set:</b> Columns × rows (defaulted from the face\'s aspect: 1 row on a strip, 2 on a plate; ChArUco 2 / 3 '
             'rows; columns = rows × W/H). The marker size is <b>read-only</b>: the largest that fills the face, rounded <b>down</b> '
             'to 0.5 mm so it still fits.</p>'
             '<p><b>Computed:</b> L (or S and L), gap, quiet zone, margins, dictionary, ids. Changing the count changes the size.</p>'
             '<p><b>Can\'t fit:</b> it always fits by construction; what can fail is printability — a count that pushes the module under '
             '1.2 mm turns the chip orange, and under 0.6 mm red (the dialog refuses OK). A 1 × 1 “grid” is renamed a single.</p>'
             '<p><b>Not a rectangle:</b> the same fit test, dropping a column or row until it passes.</p>'
             '<p><b>This is 80 %% of what the add-in does today</b>; the missing 20 %% is the rounding, the margins and limits in the chips, '
             'the aspect default and the dictionary auto-pick.</p>'
             '</div></div>' % B.fig(dialog_p2(), "Proposal 2: the count is yours, the size is derived and shown.", "mock"))
    b.append(h3("p2-ex", "Worked examples (aspect defaults, computed)", toc))
    b.append(B.fig(fig_faces(P2["grid"], "Proposal 2 defaults, grid board, five faces"),
                   "<b>Grid, count from the aspect ratio.</b> The tubes get 1 × 4 (4.4 ≈ 4), not your 1 × 5: filling the short side "
                   "with the quiet zone alone gives 28.5 mm markers, and five of those do not fit. The leftover on the long axis "
                   "(18.7 mm) sits in the margins.", "computed from the code"))
    b.append(B.fig(fig_faces(P2["charuco"], "Proposal 2 defaults, ChArUco board, five faces"),
                   "<b>ChArUco, count from the aspect ratio.</b> Squares are the largest that fit the count, rounded down; on the 100 × 80 "
                   "plate 4 × 3 squares of 25 mm run exactly to the x edges (margin 0, allowed for ChArUco).", "computed from the code"))
    b.append(h3("p2-pc", "Pros, cons, effort, ids", toc))
    b.append('<div class="pc"><div><b>Pros</b><ul><li>Smallest change; the dialog already works this way.</li><li>Direct control of the '
             'count, which is what a <i>board</i> designer sometimes wants.</li><li>Never fails to fit.</li></ul></div>'
             '<div><b>Cons</b><ul><li>The opposite of the request: the size is the output.</li><li>Sizes are whatever the face gives '
             '(28.5, 27.5…); boards on different faces never share a marker size unless you fiddle with counts.</li>'
             '<li>No “as many as fit at my size”.</li></ul></div>'
             '<div><b>Effort ~1 day</b><ul><li>Rounding, margins, limits, aspect default, auto dictionary: ½ d</li><li>Tests: ½ d</li></ul></div>'
             '<div><b>Serial ids</b><ul><li>Same as P1: the count is the block length. Here the user changes the count on purpose, so the '
             'Edit command should re-run the serial plan immediately.</li></ul></div></div>')

    # ---------------------------------------------------------------- P3
    b.append(h2("p3", "Proposal 3 — Pick a layout: the Studio's assistant on a face", toc))
    b.append('<div class="trio">%s<div>'
             '<p><b>You set:</b> nothing, or a filter. The <i>Layout</i> dropdown lists every distinct cols × rows that fits, each at the '
             'largest 0.5 mm size that gives it, biggest marker first and badged (<i>largest marker</i>, <i>best fill</i>, <i>most markers</i>). '
             'The default pick is Proposal 1\'s default. Two optional filters: marker size ≥, markers ≥.</p>'
             '<p><b>Computed:</b> the whole list, by stepping the size down 0.5 mm from the largest that fits to the printable minimum and '
             'keeping the first size that yields each new count — the port of ChArUco Studio\'s <code>suggest_board_grids</code> from an '
             'envelope to a face.</p>'
             '<p><b>Can\'t fit:</b> cannot happen; the list only holds layouts that pass the invariants. An empty list (a face under 10 mm) '
             'says “nothing printable fits”.</p>'
             '<p><b>Not a rectangle:</b> the fit test runs on every candidate; failing ones are dropped (or shown greyed with “hole”).</p>'
             '</div></div>' % B.fig(dialog_p3(), "Proposal 3: a ranked list of layouts that fit; one click picks.", "mock"))
    b.append(h3("p3-ex", "Worked examples: the ranked lists (computed)", toc))
    rows = []
    for k, name, w, h, _ in FACES:
        rows.append([name, cands_html("grid", w, h, P1["grid"][k]), cands_html("charuco", w, h, P1["charuco"][k])])
    b.append(table(["Face", "grid candidates (largest first; green = Proposal 1's default)", "ChArUco candidates"], rows))
    b.append(B.fig(fig_candidates("plate", "grid"), "The first three grid candidates for the 120 × 70 plate, to scale. The list makes the "
                   "trade-off visible: 2 × 1 at 47 mm reads from ~2 m; 3 × 2 at 27.5 mm gives six markers at ~1.3 m.", "computed from the code"))
    b.append(B.fig(fig_candidates("tube15", "grid"), "The first three for the 38.1 mm tube. Your 1 × 5 is the second entry (27 mm: half a "
                   "millimetre under your 27.1, because 27.5 does not fit five with a quiet zone).", "computed from the code"))
    b.append(h3("p3-pc", "Pros, cons, effort, ids", toc))
    b.append('<div class="pc"><div><b>Pros</b><ul><li>No heuristic decides for you; every option that fits is one click away.</li>'
             '<li>Trade-offs (range vs count) are visible with numbers.</li><li>Same engine as P1; the list is P1 run in a loop.</li></ul></div>'
             '<div><b>Cons</b><ul><li>A Fusion dropdown with 8 entries is not a Studio web page; badges have to live in the text.</li>'
             '<li>Typing an exact size (“26.5, like the other tube”) is a filter, not a field — unless combined with P1.</li>'
             '<li>Alone, it answers “what fits” better than “make it like the last one”.</li></ul></div>'
             '<div><b>Effort ~3 days</b><ul><li>Candidate enumeration + ranking + tests: 1 d</li><li>Dropdown with live refresh, filters, '
             'badges: 1 d</li><li>Fit test per candidate in Fusion: 1 d</li></ul></div>'
             '<div><b>Serial ids</b><ul><li>Picking another entry changes the count, as in P1. Because the list is visible, the dialog can '
             'show “ids 12–19 → 12–17” next to each entry before you commit.</li></ul></div></div>')

    # ---------------------------------------------------------------- compare
    b.append(h2("compare", "Side by side", toc))
    ok, part, no = ('<span class="badge ok">yes</span>', '<span class="badge part">partly</span>', '<span class="badge no">no</span>')
    b.append(table(["", "P1 size first", "P2 count first", "P3 pick a layout", "today"], [
        ["R1 tag a face, numbers appear", ok, ok, ok, part],
        ["R2 marker (+ checker) size is the control", ok, no + " derived", part + " filter", no],
        ["R3/R4 inside the face, never overlapping", ok, ok, ok, part + " box only"],
        ["R5 smart default, nearest 0.5 mm", ok + " 0.7 rule", part + " down to 0.5", ok + " P1's default", no],
        ["R6 Studio / tube rule as reference", ok + " board_for_tube", no, ok + " suggest_board_grids", no],
        ["R7 OpenCV-tutorial ratios", ok, ok, ok, ok],
        ["R8 valid for OpenCV, serials survive", ok, ok, ok, part],
        ["direct control of the count", no, ok, ok, ok],
        ["sees the alternatives", part + " hint in chip", no, ok, no],
        ["effort", "~2 d", "~1 d", "~3 d", "—"],
        ["re-creates your 38.1 / 50.8 boards", ok + " 1 × 5 @ 26.5 / 35.5", no + " 1 × 4 @ 28.5 / 38", ok + " 2nd entry", no + " 3 × 2"],
    ]))

    # ---------------------------------------------------------------- reco
    b.append(h2("reco", "Recommendation", toc))
    b.append(B.callout(
        '<p><b>Build P1, and put P3\'s list under its size field as a “Layout” dropdown.</b> The size field is the control (R2); the '
        'dropdown is how you see what else fits and how you get a particular count without hunting for the size that gives it. Both '
        'run the same pure function, so the second costs a day on top of the first. P2 falls out for free (pick an entry, the size is '
        'read back into the field).</p>'
        '<ol><li><b><code>markers.autosize</code></b>: <code>layout_for_size</code>, <code>default_sizes</code>, <code>clamp_to_fit</code>, '
        '<code>candidates</code>, <code>auto_dictionary</code> in metres, returning a <code>TagSpec</code> plus margins and notes; '
        'tested on the five faces here (the numbers on this page become the expected values).</li>'
        '<li><b>Dialog</b>: Face → Type → Marker size [auto] → Checker size [auto] → Layout (dropdown, ranked) → Dictionary [auto] → '
        'First id [auto] → chips; Advanced folds the rest (quiet zone, gap ratio, black thickness / sits, grow cells, rotate, name).</li>'
        '<li><b>Fit test</b> against the real face for non-rectangles (evaluator samples), shrinking the size until it passes.</li>'
        '<li><b>ArUco Edit</b> gains the same size field; when the count changes it re-plans serials and says so.</li></ol>'
        '<p>What this does <i>not</i> do: choose where on an L-shaped face the tag goes (sketch a rectangle), or change the black layer\'s '
        'thickness rules (<code>depth_m</code> / <code>raise_m</code> / <code>grow_m</code> stay as they are).</p>', "good", "Recommended"))
    b.append('<details class="src"><summary>The reference implementation (the code that computed this page, in mm)</summary>'
             '<div class="srcbody">%s</div></details>' % code_of(layout_for_size, default_sizes, clamp_to_fit, layout_for_count, candidates,
                                                                  auto_dictionary))
    b.append('<p>Your two boards through <code>fiducials.board_for_tube</code>, the rule the defaults generalise: '
             + "; ".join("%g tube, marker %g, gap %.1f → %g × %g outer" % (z["tube"], z["L"], z["gap"], z["w"], z["h"]) for z in zb)
             + '. The defaults land 0.5–0.6 mm under those markers because 27.1 and 36.0 are not on the 0.5 mm grid and rounding up '
               'would not leave five with a quiet zone.</p>')

    # ---------------------------------------------------------------- serial
    b.append(h2("serial", "Per-copy ids when a board's count changes", toc))
    b.append('<p>A board\'s id block is <code>marker_count</code> long and an override stores only the <i>first</i> id '
             '(<code>TagSpec.ids = marker_id .. marker_id + count − 1</code>). So resizing the template never edits a copy\'s record, but it '
             'can make two copies\' blocks overlap. Computed with <code>markers.plan_serials</code> and <code>resolve</code>:</p>')
    b.append(B.fig(fig_serial(ex), "Three copies of a 1 × 5 strip serialled 0 / 5 / 10. Growing the template to 1 × 6 makes the blocks "
                   "0–5, 5–10, 10–15: #5 and #10 are printed twice (orange). One more “Number duplicates” gives the second and third copies "
                   "fresh blocks (%s). Shrinking a board only leaves gaps, which is harmless."
                   % ", ".join("%s → %d–%d" % (p, ids[0], ids[-1]) for p, ids in sorted(ex["fixed"].items()) if ids[0] != 0),
                   "computed from the code"))
    b.append('<p>Rule for all three proposals: a size or count change on a template that has serialled copies re-runs the plan '
             'inside the same command (one undo step), and the chip reports “block 5 → 6, 2 copies renumbered”. Variant parts rebuild '
             'from the new spec as they do today.</p>')

    # ---------------------------------------------------------------- questions
    b.append(h2("questions", "Open questions for Zach", toc))
    b.append('<ol class="qlist">'
             '<li><b>Default on a plate: 2 across (this page) or 1 across (biggest markers)?</b><div class="qd">On the 120 × 70 plate '
             'that is 4 × 2 at 22.5 mm vs 2 × 1 at 47 mm. Strips are not affected (always 1 across, your rule).</div></li>'
             '<li><b>ChArUco minimum: accept 2 × 2 squares (OpenCV\'s floor, one corner) or insist on ≥ 4 inner corners for a plate?</b>'
             '<div class="qd">This page warns below 4 but allows 2 × N strips, which are your L1/L2.</div></li>'
             '<li><b>Should a count change re-serial copies automatically, or only warn?</b><div class="qd">Automatic is one undo step '
             'and keeps every copy unique; warning keeps printed stickers valid until you decide.</div></li>'
             '<li><b>Is a face-area ratio under 0.98 the right trigger for the slower fit test, and may it shrink the tag without '
             'asking?</b><div class="qd">It always says what it did; the question is whether shrinking silently is acceptable.</div></li></ol>')
    return page("\n".join(b), toc)


if __name__ == "__main__":
    sys.exit(main())
