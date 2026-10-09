"""Auto-sizing ArUco tags on a face: the marker size is the control.

Zach, 2026-10-07: "generally what I want to control is the marker size. and
checker size (if its charuco board)... all the other spacing and stuff can
then be autocalculated off those. it should always kinda fit within the face.
markers shouldn't ever be overlapping. Lets have smart good defaults. I prefer
rounding to nearest .5 mm for the defaults on the marker sizes."

He approved Proposal 1 (size first) with Proposal 3's ranked list as a Layout
dropdown (docs/proposal_aruco_autosize.md). This module is that design in
METRES, ported from the executable reference in
docs/build_aruco_autosize_report.py (its "algorithms" section):

    layout_for_size   sizes in -> as many markers as fit, centred, checked
    default_layout    the smart default (his tube rule generalised), nearest 0.5 mm
    clamp_to_fit      a typed size that overhangs -> the largest 0.5 mm step that fits
    candidates        every distinct cols x rows that fits, biggest marker first
    auto_dictionary   keep the family, the smallest capacity with headroom
    face_default      a clicked face's default in its clear rectangle (single: the
                      sticker fills it; boards: default_layout)
    largest_clear_rect  the largest rectangle clear of a face's outline and holes
    fit_on_loops      the same on a clicked face's real outline (holes, L, disc)

### Geometry (n = modules per marker side, q = quiet zone in modules)

    quiet   Q = q L / n                      single, grid
    gap     s = max(gap_ratio L, L / n)      grid: never under one module
    cols    = floor((W - 2Q + s) / (L + s))  grid (rows likewise, with H)
    cols    = floor(W / S)                   charuco (rows likewise)
    margins = (W - extent) / 2 per axis      >= Q (single, grid), >= 0 (charuco)

A ChArUco board needs no outer quiet zone -- each marker sits inside its own
white square -- but the marker must leave >= 1 module of white in it:
L <= S n / (n + 2), which is 0.75 S for 4x4, OpenCV's tutorial ratio derived
rather than assumed. So a ChArUco Layout carries quiet_modules = 0 and its
`apply()` writes that into the TagSpec, which makes `TagSpec.tile_size_m` the
squares' extent, i.e. exactly what was fitted.

### Rounding (0.5 mm, STEP_M)

Defaults -> NEAREST step, then checked; if rounding up broke the fit or lost
a marker, step down. Derived maxima (a ChArUco marker inside its square, a
size from a count) -> DOWN. Typed values are used as typed; only a typed size
that does not fit is clamped (down, with a note). Results are rounded to
1e-7 m so a stored length never carries float noise.

### What "ok" means

ok = the layout is a valid, non-overlapping board inside the box AND (for
anything this module chose) printable: module >= 1.2 mm, three 0.4 mm lines.
A size the user typed below that is honoured with a note. When nothing
printable fits, the answer is the largest size that fits at all, with
ok=False and a note; when nothing fits, marker_m = 0. Nothing here raises.

Every ok layout passes aruco_board.check_spec (the gate that mirrors what
cv2 refuses) with first_id 0 -- so the board's marker count fits the
dictionary's capacity; choosing the first id is the caller's job.

Standard library only (this module is loaded inside Fusion).
"""
from __future__ import annotations

import heapq
import math
import re
from dataclasses import dataclass, field, replace
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import aruco_board
from .fiducials import ARUCO_DICTS
from .markers import (DEFAULT_DICTIONARY, DEFAULT_GRID_SEPARATION_RATIO, LAYOUTS,
                      TagSpec, dictionary_bits)

STEP_M = 0.0005               # Zach: "rounding to nearest .5 mm for the defaults"
GAP_RATIO = DEFAULT_GRID_SEPARATION_RATIO   # 0.2 L, OpenCV's tutorial grid
FILL = 0.7                    # his tube rule: the pattern spans 70 % of the short side
MIN_MODULE_M = 0.0012         # three 0.4 mm lines: the smallest module an FDM nozzle prints solid
STRIP_ASPECT = 3.0            # long / short >= 3 -> a strip (tube face), else a plate
CHARUCO_MIN_CORNERS = 4       # advised for a pose board; OpenCV's floor is 2 x 2 squares (1 corner)
CAPACITIES = (50, 100, 250, 1000)
# a single's auto sticker fills its clear square but never lands exactly ON
# the outline: a white tile flush with the body's walls makes Fusion merge
# them (live test 2026-10-08: a 30.0 mm marker = a 40.0 tile on a 40 mm
# plate, "Edge 1 missing"). 5 um only refuses an exact fit; a sticker that
# touches a curve or a slant at its corners keeps its step (Zach's disc 28.0)
FILL_CLEARANCE_M = 5e-6

EPS_M = 1e-9                  # 1 nm: geometric comparisons
_K_EPS = 1e-6                 # in steps: float noise when snapping to the 0.5 mm grid
_CAP_NOTE = "too many markers for"

__all__ = ["STEP_M", "GAP_RATIO", "FILL", "MIN_MODULE_M", "STRIP_ASPECT", "Layout",
           "layout_for_size", "default_layout", "clamp_to_fit", "candidates",
           "auto_dictionary", "face_default", "largest_clear_rect", "fit_on_loops",
           "CLEAR_EPS_M"]


# ------------------------------------------------------------------ numbers

def _num(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0


def _r7(x: float) -> float:
    return round(float(x), 7) + 0.0          # + 0.0 turns -0.0 into 0.0


def _from_k(k: int) -> float:
    return _r7(k * STEP_M) if k > 0 else 0.0


def _snap_down(x: float) -> float:
    return _from_k(int(math.floor(x / STEP_M + _K_EPS)))


def _snap_nearest(x: float) -> float:
    return _from_k(int(math.floor(x / STEP_M + 0.5 + _K_EPS)))


def _snap_up(x: float) -> float:
    return _from_k(int(math.ceil(x / STEP_M - _K_EPS)))


def _next_down(x: float) -> float:
    """The next 0.5 mm step below x: the step under it when x is on the
    grid, else the grid value just below x (27.1 -> 27.0 -> 26.5)."""
    k = int(math.floor(x / STEP_M + _K_EPS))
    if abs(x - k * STEP_M) <= EPS_M:
        k -= 1
    return _from_k(k)


def _fit_count(avail: float, pitch: float) -> int:
    if pitch <= 0 or avail <= 0:
        return 0
    return max(0, int(math.floor(avail / pitch + 1e-9)))


def _modules(dictionary: str) -> int:
    return (dictionary_bits(dictionary) or 4) + 2


def _lmax(square_m: float, n: int) -> float:
    """Largest ChArUco marker that leaves one module of white in its square."""
    return square_m * n / (n + 2.0)


def _mm(x_m: float) -> str:
    """28.0, 27.25, 27.1 -- at least one decimal, at most two."""
    s = "%.2f" % (x_m * 1000.0)
    return s[:-1] if s.endswith("0") else s


# ------------------------------------------------------------------ records

@dataclass
class Layout:
    """One sized target in one box. Lengths in METRES.

    margins_m is the leftover per side between the PATTERN and the box
    (so it includes the quiet zone for single / grid); for fit_on_loops on a
    face that is not a rectangle, the distance from the pattern to the
    face's outline along the tile's x / y at the placement. fill = pattern
    area / box area (the face's own area for fit_on_loops). notes are chips
    for the dialog; badges are P3's ("largest marker", "most markers",
    "best fill").
    """
    kind: str                     # "single" | "grid" | "charuco"
    marker_m: float
    square_m: float = 0.0         # charuco only
    cols: int = 1
    rows: int = 1
    separation_m: float = 0.0     # grid gap
    margins_m: tuple = (0.0, 0.0)
    ok: bool = True
    notes: list = field(default_factory=list)
    badges: list = field(default_factory=list)
    fill: float = 0.0
    dictionary: str = DEFAULT_DICTIONARY
    quiet_modules: float = 1.0    # what the layout was fitted with; 0 for charuco

    @property
    def n(self) -> int:
        """Modules per marker side (bits + 2)."""
        return _modules(self.dictionary)

    @property
    def module_m(self) -> float:
        return self.marker_m / self.n

    @property
    def quiet_m(self) -> float:
        return self.quiet_modules * self.module_m

    @property
    def count(self) -> int:
        """Markers on the board = the id block a TagSpec needs."""
        if self.kind == "grid":
            return max(0, self.cols) * max(0, self.rows)
        if self.kind == "charuco":
            return (max(0, self.cols) * max(0, self.rows)) // 2
        return 1 if self.marker_m > 0 else 0

    @property
    def corners(self) -> int:
        if self.kind != "charuco":
            return 0
        return max(0, self.cols - 1) * max(0, self.rows - 1)

    @property
    def extent_m(self) -> Tuple[float, float]:
        """The pattern (black markers / chessboard), no quiet zone."""
        L = self.marker_m
        if self.kind == "grid":
            return (self.cols * L + (self.cols - 1) * self.separation_m,
                    self.rows * L + (self.rows - 1) * self.separation_m)
        if self.kind == "charuco":
            return (self.cols * self.square_m, self.rows * self.square_m)
        return (L, L)

    @property
    def tile_m(self) -> Tuple[float, float]:
        """Pattern + quiet zone: what must lie on the face. = TagSpec.tile_size_m after apply()."""
        w, h = self.extent_m
        q = 2.0 * self.quiet_m
        return (w + q, h + q)

    def label(self) -> str:
        if self.marker_m <= 0:
            return "nothing fits"
        if self.kind == "grid":
            return "%d × %d @ %s mm" % (self.cols, self.rows, _mm(self.marker_m))
        if self.kind == "charuco":
            return "%d × %d @ %s / %s mm" % (self.cols, self.rows, _mm(self.square_m),
                                              _mm(self.marker_m))
        return "single %s mm" % _mm(self.marker_m)

    def apply(self, spec: TagSpec) -> TagSpec:
        """A COPY of `spec` with this layout: layout, cols, rows,
        marker_length_m, square_length_m, separation_m and quiet_modules
        (0 for ChArUco, see the module docstring). The dictionary, id,
        thickness and everything else stay the spec's. Raises ValueError for
        a layout with nothing in it, or a spec whose dictionary has another
        module count than the one this layout was sized for (a caller bug:
        the marker would be a different size than fitted)."""
        if self.marker_m <= 0 or self.cols < 1 or self.rows < 1:
            raise ValueError("nothing to apply: %s" % ("; ".join(self.notes) or "empty layout"))
        if _modules(spec.dictionary) != self.n:
            raise ValueError("this layout was sized for %s (%d modules per side); the spec's "
                             "%s has %d" % (self.dictionary, self.n, spec.dictionary,
                                            _modules(spec.dictionary)))
        return replace(spec, layout=self.kind, cols=int(self.cols), rows=int(self.rows),
                       marker_length_m=self.marker_m,
                       square_length_m=self.square_m if self.kind == "charuco" else 0.0,
                       separation_m=self.separation_m if self.kind == "grid" else 0.0,
                       quiet_modules=0.0 if self.kind == "charuco" else self.quiet_modules)


def _fail(lay: Layout, why: str) -> Layout:
    lay.ok = False
    lay.notes.append(why)
    return lay


def _nothing(kind: str, kw: dict, why: str) -> Layout:
    return Layout(kind, 0.0, cols=0, rows=0, ok=False, notes=[why],
                  dictionary=kw["dictionary"],
                  quiet_modules=0.0 if kind == "charuco" else max(0.0, _num(kw["quiet_modules"])))


def _kw(dictionary, quiet_modules, gap_ratio) -> dict:
    return dict(dictionary=dictionary, quiet_modules=max(0.0, _num(quiet_modules)),
                gap_ratio=max(0.0, _num(gap_ratio)))


def _typed(kind: str, marker_m, square_m, n: int) -> Tuple[float, float]:
    """Typed sizes, rounded to 1e-7 m. ChArUco with only one of the two:
    marker only -> the smallest 0.5 mm square that leaves it one module of
    white; square only -> the largest 0.5 mm marker that fits in it."""
    L = _r7(max(0.0, _num(marker_m)))
    S = _r7(max(0.0, _num(square_m)))
    if kind == "charuco":
        if L <= 0 < S:
            L = _snap_down(_lmax(S, n))
        elif S <= 0 < L:
            S = _snap_up(L * (n + 2.0) / n)
    return L, S


# ------------------------------------------------------------- the core rule

def _cap_count(kind: str, cols: int, rows: int, cap: int) -> Optional[Tuple[int, int]]:
    """The largest cols' x rows' (each <= the fitted count) whose marker count
    fits `cap` ids; ties keep the fitted board's shape. None if none does."""
    best, best_key = None, None
    lo = 2 if kind == "charuco" else 1
    target = cols / float(rows)
    for c in range(lo, cols + 1):
        for r in range(lo, rows + 1):
            n = (c * r) // 2 if kind == "charuco" else c * r
            if n > cap or (kind == "grid" and n < 2):
                continue
            key = (n, -abs(math.log((c / float(r)) / target)))
            if best_key is None or key > best_key:
                best, best_key = (c, r), key
    return best


def _layout(kind: str, W: float, H: float, L: float, S: float, *, dictionary: str,
            quiet_modules: float, gap_ratio: float, force=None, final: bool = True) -> Layout:
    """Sizes in -> as many as fit, centred, checked. `final` adds the
    dictionary checks (capacity + aruco_board.check_spec); the sizing walks
    run without them so a size is never shrunk for a dictionary's sake."""
    n = _modules(dictionary)
    q = quiet_modules
    lay = Layout(kind, L, S if kind == "charuco" else 0.0, dictionary=dictionary,
                 quiet_modules=0.0 if kind == "charuco" else q)
    if kind not in LAYOUTS:
        return _fail(lay, "unknown layout %r (one of %s)" % (kind, ", ".join(LAYOUTS)))
    if not L > 0:
        return _fail(lay, "marker size must be > 0")
    if not (W > 0 and H > 0):
        return _fail(lay, "the face has no area")
    Q = q * L / n
    if kind == "single":
        if force and tuple(force) != (1, 1):
            return _fail(lay, "a single marker is 1 × 1")
        if L + 2 * Q > min(W, H) + EPS_M:
            return _fail(lay, "%s mm + a %s mm quiet zone per side does not fit the %s mm side"
                         % (_mm(L), _mm(Q), _mm(min(W, H))))
    elif kind == "grid":
        s = _r7(max(gap_ratio * L, L / n))       # never below one module
        lay.separation_m = s
        cols = _fit_count(W - 2 * Q + s, L + s)
        rows = _fit_count(H - 2 * Q + s, L + s)
        if force:
            fc, fr = int(force[0]), int(force[1])
            if fc < 1 or fr < 1 or fc > cols or fr > rows:
                lay.cols, lay.rows = fc, fr
                return _fail(lay, "%d × %d of %s mm does not fit (at most %d × %d)"
                             % (fc, fr, _mm(L), cols, rows))
            cols, rows = fc, fr
        lay.cols, lay.rows = cols, rows
        if cols < 1 or rows < 1:
            return _fail(lay, "no %s mm marker fits with its quiet zone" % _mm(L))
        if cols * rows == 1:                      # a grid has >= 2 markers
            lay.kind, lay.separation_m = "single", 0.0
            lay.notes.append("only one %s mm marker fits: a single, not a grid" % _mm(L))
    else:                                         # charuco
        if not S > 0:
            return _fail(lay, "checker size must be > 0")
        if L > _lmax(S, n) + EPS_M:
            return _fail(lay, "a %s mm marker leaves < 1 module of white in a %s mm square "
                         "(at most %s mm)" % (_mm(L), _mm(S), _mm(_snap_down(_lmax(S, n)))))
        cols, rows = _fit_count(W, S), _fit_count(H, S)
        if force:
            fc, fr = int(force[0]), int(force[1])
            if fc > cols or fr > rows:
                lay.cols, lay.rows = fc, fr
                return _fail(lay, "%d × %d squares of %s mm do not fit (at most %d × %d)"
                             % (fc, fr, _mm(S), cols, rows))
            cols, rows = fc, fr
        lay.cols, lay.rows = cols, rows
        if cols < 2 or rows < 2:
            return _fail(lay, "ChArUco needs ≥ 2 × 2 squares; %s mm squares give %d × %d"
                         % (_mm(S), cols, rows))
        if lay.corners < CHARUCO_MIN_CORNERS:
            lay.notes.append("%d inner corner(s): fine for a strip, weak for a pose board "
                             "(≥ %d advised)" % (lay.corners, CHARUCO_MIN_CORNERS))
    if final and not force and lay.kind != "single":
        # "As many as fit" can be more markers than the dictionary has ids
        # (Zach 2026-10-08: a 30 mm grid on a big plate = 54 markers on
        # DICT_4X4_50 -> "no marker fits here"). Use the largest board of
        # about the same shape that the dictionary can number, and say so.
        cap = ARUCO_DICTS.get(dictionary)
        if cap is not None and lay.count > cap:
            fit = _cap_count(lay.kind, lay.cols, lay.rows, cap)
            if fit is not None:
                was = lay.count
                lay.cols, lay.rows = fit
                lay.notes.append("%d fit, but %s has %d ids: using %d × %d (%d markers); "
                                 "a bigger dictionary gives more"
                                 % (was, dictionary, cap, lay.cols, lay.rows, lay.count))
    ew, eh = lay.extent_m
    mx, my = (W - ew) / 2.0, (H - eh) / 2.0
    lay.margins_m = (_r7(mx), _r7(my))
    lay.fill = (ew * eh) / (W * H)
    if min(mx, my) < (lay.quiet_m if lay.kind != "charuco" else 0.0) - EPS_M:
        return _fail(lay, "the pattern overhangs the face")
    if lay.module_m < MIN_MODULE_M - 1e-12:
        lay.notes.append("module %s mm < 1.2 mm: hard to print with a 0.4 mm nozzle"
                         % _mm(lay.module_m))
    if final:
        cap = ARUCO_DICTS.get(dictionary)
        if cap is not None and lay.count > cap:
            return _fail(lay, "%s %s: %d markers, %d ids (use a bigger dictionary)"
                         % (_CAP_NOTE, dictionary, lay.count, cap))
        try:                                      # the OpenCV-validity gate, verbatim
            aruco_board.check_spec(aruco_board.BoardSpec(
                layout=lay.kind, dictionary=dictionary, first_id=0, marker_length_m=L,
                rows=lay.rows, cols=lay.cols, separation_m=lay.separation_m,
                square_length_m=lay.square_m))
        except ValueError as e:
            return _fail(lay, "OpenCV would refuse it: %s" % e)
    return lay


def layout_for_size(kind: str, w_m: float, h_m: float, marker_m: float, square_m: float = 0.0, *,
                    dictionary: str = DEFAULT_DICTIONARY, quiet_modules: float = 1.0,
                    gap_ratio: float = GAP_RATIO, force: Optional[Tuple[int, int]] = None) -> Layout:
    """THE rule: given the sizes, lay out as many markers as fit the w x h
    box (x = w), centred, and say whether that is valid.

    single   the marker plus `quiet_modules` of white on every side fits.
    grid     gap = max(gap_ratio L, L/n); cols / rows = as many as fit inside
             the quiet zone; a grid where only one fits comes back as a
             SINGLE (with a note) -- a grid has >= 2 markers.
    charuco  cols / rows = floor(W / S), floor(H / S); >= 2 x 2 (OpenCV's
             floor); marker <= S n/(n+2). No outer quiet zone. A ChArUco
             given only one of marker / square gets the other derived (see
             clamp_to_fit).
    force    (cols, rows) instead of the maximum; must fit.
    Sizes are used as given (rounded to 1e-7 m); a module under 1.2 mm is
    noted, not refused. Never raises."""
    kw = _kw(dictionary, quiet_modules, gap_ratio)
    L, S = _typed(kind, marker_m, square_m, _modules(dictionary))
    return _layout(kind, _num(w_m), _num(h_m), L, S, force=force, **kw)


# ------------------------------------------------------------ walking down

def _walk(kind, W, H, L, S, accept, floor_L, kw) -> Optional[Layout]:
    """Step (L, S) down 0.5 mm at a time from the given sizes until `accept`
    (sizing pass: no dictionary checks). ChArUco steps the square; the
    marker follows it down only when it no longer leaves a module of white."""
    n = _modules(kw["dictionary"])
    while L > 0 and L >= floor_L - 1e-12:
        lay = _layout(kind, W, H, L, S, final=False, **kw)
        if accept(lay):
            return lay
        if kind == "charuco":
            S = _next_down(S)
            if S <= 0:
                return None
            L = min(L, _snap_down(_lmax(S, n)))
        else:
            L = _next_down(L)
    return None


def _clamp(kind, W, H, L, S, floor_L, kw) -> Optional[Layout]:
    """The largest 0.5 mm step at or below (L, S) that is still a board
    (single, grid >= 2 markers, charuco >= 2 x 2) and fits."""
    n = _modules(kw["dictionary"])
    q = kw["quiet_modules"]
    if kind == "charuco":                         # nothing above these can fit: jump
        top = min(W, H) / 2.0
        if S > top + EPS_M:
            S = _snap_down(top)
        if S <= 0:
            return None
        if L > _lmax(S, n) + EPS_M:
            L = _snap_down(_lmax(S, n))
    else:
        top = min(W, H) * n / (n + 2.0 * q)
        if L > top + EPS_M:
            L = _snap_down(top)
    return _walk(kind, W, H, L, S, lambda lay: lay.ok and lay.kind == kind, floor_L, kw)


def _finish(lay: Layout, W: float, H: float, kw: dict, notes=()) -> Layout:
    """Re-run the chosen sizes with the dictionary checks (same geometry)."""
    force = None if lay.kind == "single" else (lay.cols, lay.rows)
    out = _layout(lay.kind, W, H, lay.marker_m, lay.square_m, force=force, final=True, **kw)
    out.notes[:0] = list(notes)
    return out


def _size_text(kind, L, S) -> str:
    return "%s / %s" % (_mm(S), _mm(L)) if kind == "charuco" else _mm(L)


def _changed_note(prefix: str, kind, L0, S0, L, S) -> str:
    only_marker = kind == "charuco" and abs(S - S0) <= EPS_M and abs(L - L0) > EPS_M
    if only_marker:
        return ("%s: a %s mm marker leaves < 1 module of white in a %s mm square, using %s mm"
                % (prefix, _mm(L0), _mm(S0), _mm(L)))
    return "%s: %s mm does not fit, using %s mm" % (prefix, _size_text(kind, L0, S0),
                                                   _size_text(kind, L, S))


def _unprintable(lay: Layout) -> Layout:
    """Mark a layout that is only there because nothing printable fits."""
    lay.notes = [t for t in lay.notes if not t.startswith("module ")]
    lay.notes.append("nothing printable fits (a module ≥ 1.2 mm needs a ≥ %s mm marker): "
                     "%s is the largest that fits, module %s mm"
                     % (_mm(MIN_MODULE_M * lay.n), lay.label(), _mm(lay.module_m)))
    lay.ok = False
    return lay


# ---------------------------------------------------------------- P1 + P3

def default_layout(kind: str, w_m: float, h_m: float, *, dictionary: str = DEFAULT_DICTIONARY,
                   quiet_modules: float = 1.0, gap_ratio: float = GAP_RATIO) -> Layout:
    """The smart default: Zach's tube rule generalised, rounded to the
    NEAREST 0.5 mm, checked; if rounding up broke the fit or lost a marker,
    step down 0.5 mm.

    single   L = 0.7 x short side
    grid     k markers span 70 % of the short side, k = 1 on a strip
             (long/short >= 3), 2 on a plate: L = 0.7 short / (k + 0.2 (k-1))
    charuco  k squares fill the short side, k = 2 on a strip (his L1/L2),
             3 on a plate; marker = the largest 0.5 mm step <= S n/(n+2)
             (0.75 S for 4x4)

    A rule size under the printable minimum (module 1.2 mm, a small face) is
    raised to the smallest printable step first (noted). If the rule's k
    across never fits printably, the largest board below the rule's size
    that fits is used (noted); if nothing printable fits, the largest that
    fits at all, ok=False; if nothing fits, marker_m = 0."""
    kw = _kw(dictionary, quiet_modules, gap_ratio)
    W, H = _num(w_m), _num(h_m)
    if kind not in LAYOUTS:
        return _layout(kind, W, H, 0.0, 0.0, **kw)
    short, long_ = min(W, H), max(W, H)
    if not short > EPS_M:
        return _nothing(kind, kw, "the face has no area")
    n = _modules(dictionary)
    strip = long_ >= STRIP_ASPECT * short * (1.0 - 1e-12)
    g = kw["gap_ratio"]
    if kind == "single":
        k, S, L = 1, 0.0, _snap_nearest(FILL * short)
    elif kind == "grid":
        k, S = (1 if strip else 2), 0.0
        L = _snap_nearest(FILL * short / (k + (k - 1) * g))
    else:
        k = 2 if strip else 3
        S = _snap_nearest(short / k)
        L = _snap_down(_lmax(S, n))
    printable = MIN_MODULE_M * n
    notes = []
    if L < printable - 1e-12:                   # a small face: no smaller than printable
        L = _snap_up(printable)
        if kind == "charuco":
            S = _snap_up(L * (n + 2.0) / n)
            L = _snap_down(_lmax(S, n))
        notes.append("default raised to %s mm: the smallest printable marker (module ≥ 1.2 mm)"
                     % _mm(L))

    def intended(lay):
        return lay.ok and lay.kind == kind and (kind == "single" or min(lay.cols, lay.rows) >= k)

    lay = _walk(kind, W, H, L, S, intended, printable, kw)
    if lay is not None:
        return _finish(lay, W, H, kw, notes)
    lay = _clamp(kind, W, H, L, S, printable, kw)
    if lay is not None:
        return _finish(lay, W, H, kw, notes + ["default: %d across the short side does not fit, "
                                               "using %s" % (k, lay.label())])
    big = min(W, H)                             # nothing printable: the largest that fits at all
    lay = _clamp(kind, W, H, big, big, STEP_M, kw)
    if lay is None:
        return _nothing(kind, kw, "nothing fits this %s × %s mm face" % (_mm(W), _mm(H)))
    return _unprintable(_finish(lay, W, H, kw))


def clamp_to_fit(kind: str, w_m: float, h_m: float, marker_m: float, square_m: float = 0.0, *,
                 dictionary: str = DEFAULT_DICTIONARY, quiet_modules: float = 1.0,
                 gap_ratio: float = GAP_RATIO) -> Layout:
    """P1's answer to a typed size. Fits (as a board: a grid keeps >= 2
    markers) -> exactly layout_for_size, no note. Overhangs -> the largest
    0.5 mm step at or below it that fits, noted ("clamped: 30.0 mm does not
    fit, using 15.5 mm"); ChArUco steps the square, and the marker only when
    it no longer leaves a module of white. ChArUco with only one of the two
    typed (the other 0 / None): the other is derived first (see
    layout_for_size). A typed size that fits but whose board has more markers
    than the dictionary holds is NOT shrunk (smaller means more markers):
    ok=False with a "use a bigger dictionary" note."""
    kw = _kw(dictionary, quiet_modules, gap_ratio)
    W, H = _num(w_m), _num(h_m)
    n = _modules(dictionary)
    L0, S0 = _typed(kind, marker_m, square_m, n)
    lay = _layout(kind, W, H, L0, S0, **kw)
    if lay.ok and lay.kind == kind:
        return lay
    if kind not in LAYOUTS or L0 <= 0 or not (W > 0 and H > 0):
        return lay
    geo = _layout(kind, W, H, L0, S0, final=False, **kw)
    if geo.ok and geo.kind == kind:               # fits; only the dictionary objects
        return lay
    printable = MIN_MODULE_M * n
    typed_printable = L0 >= printable - 1e-12
    got = _clamp(kind, W, H, L0, S0, printable if typed_printable else STEP_M, kw)
    below = False
    if got is None and typed_printable:
        got = _clamp(kind, W, H, L0, S0, STEP_M, kw)
        below = got is not None
    if got is None:
        return _nothing(kind, kw, "nothing fits this %s × %s mm face" % (_mm(W), _mm(H)))
    out = _finish(got, W, H, kw, [_changed_note("clamped", kind, L0, S0, got.marker_m,
                                                got.square_m)])
    return _unprintable(out) if below else out


def face_default(kind: str, w_m: float, h_m: float, *, dictionary: str = DEFAULT_DICTIONARY,
                 quiet_modules: float = 1.0, gap_ratio: float = GAP_RATIO) -> Layout:
    """The default for a clicked face (or sketch area) whose clear rectangle
    is w x h -- the face itself when it is a plain rectangle, else its
    largest clear rectangle (fit_on_loops).

    single   the largest 0.5 mm marker whose tile (marker + quiet zone) fits
             the short side less FILL_CLEARANCE_M a side: the sticker fills
             the clear square, margins ~0 but never exactly flush (Zach, 2026-10-08,
             on a 53 mm disc: "it should detect the largest rectangle and
             use that to intelligently set the default"). 29.5 on a 40 x 40
             face (sticker 39.3), 28.0 in a 37.5 mm square.
    grid,    default_layout -- the doc's 70 % rule, so a board keeps its
    charuco  sensible fill.
    Nothing printable fits -> the largest that fits, ok False, noted;
    nothing fits -> marker_m 0. Never raises."""
    if kind != "single":
        return default_layout(kind, w_m, h_m, dictionary=dictionary, quiet_modules=quiet_modules,
                              gap_ratio=gap_ratio)
    kw = _kw(dictionary, quiet_modules, gap_ratio)
    W, H = _num(w_m), _num(h_m)
    if not min(W, H) > EPS_M:
        return _nothing(kind, kw, "the face has no area")
    printable = MIN_MODULE_M * _modules(dictionary)
    big = min(W, H)
    c2 = 2.0 * FILL_CLEARANCE_M
    lay = _clamp(kind, W - c2, H - c2, big, 0.0, printable, kw)
    if lay is not None:
        return _finish(lay, W, H, kw)
    lay = _clamp(kind, W - c2, H - c2, big, 0.0, STEP_M, kw)
    if lay is None:
        return _nothing(kind, kw, "nothing fits this %s × %s mm face" % (_mm(W), _mm(H)))
    return _unprintable(_finish(lay, W, H, kw))


def _badge(out: List[Layout]) -> List[Layout]:
    if out:
        out[0].badges.append("largest marker")
        max(out, key=lambda l: l.count).badges.append("most markers")
        max(out, key=lambda l: l.fill).badges.append("best fill")
    return out


def candidates(kind: str, w_m: float, h_m: float, *, dictionary: str = DEFAULT_DICTIONARY,
               quiet_modules: float = 1.0, gap_ratio: float = GAP_RATIO,
               limit: Optional[int] = 8) -> List[Layout]:
    """P3's ranked list: every distinct cols x rows that fits, each at the
    LARGEST 0.5 mm size that gives it, biggest marker first, printable only
    (module >= 1.2 mm), dictionary-valid only. Stepping the size down from
    the largest that fits and keeping the first size that yields each new
    count -- ChArUco Studio's suggest_board_grids, on a face.

    Badges ("largest marker", "most markers", "best fill") are given within
    the RETURNED list, so every badge is visible in an 8-entry dropdown.
    The P1 default is usually NOT an entry as such (an entry is the largest
    size for its count: 4 × 2 @ 24.0 here vs the default 4 × 2 @ 22.5)."""
    kw = _kw(dictionary, quiet_modules, gap_ratio)
    W, H = _num(w_m), _num(h_m)
    out: List[Layout] = []
    if kind not in LAYOUTS or not min(W, H) > EPS_M:
        return out
    n = _modules(dictionary)
    floor_L = MIN_MODULE_M * n - 1e-12
    seen = set()
    cap = int(limit) if limit else 0
    if kind == "charuco":
        kS = int(math.floor(min(W, H) / 2.0 / STEP_M + _K_EPS))
        while kS > 0:
            S = _from_k(kS)
            L = _snap_down(_lmax(S, n))
            if L < floor_L:
                break
            lay = _layout(kind, W, H, L, S, **kw)
            if lay.ok and (lay.cols, lay.rows) not in seen:
                seen.add((lay.cols, lay.rows))
                out.append(lay)
                if cap and len(out) >= cap:
                    break
            kS -= 1
    else:
        kL = int(math.floor(min(W, H) * n / (n + 2.0 * kw["quiet_modules"]) / STEP_M + _K_EPS))
        while kL > 0:
            L = _from_k(kL)
            if L < floor_L:
                break
            lay = _layout(kind, W, H, L, 0.0, **kw)
            if lay.ok and lay.kind == kind and (lay.cols, lay.rows) not in seen:
                seen.add((lay.cols, lay.rows))
                out.append(lay)
                if kind == "single" or (cap and len(out) >= cap):
                    break
            kL -= 1
    return _badge(out)


def candidates_on_loops(kind: str, loops_m: Sequence[Sequence[Tuple[float, float]]], *,
                        dictionary: str = DEFAULT_DICTIONARY, quiet_modules: float = 1.0,
                        gap_ratio: float = GAP_RATIO, angles_deg: Sequence[float] = (0.0, 90.0),
                        limit: Optional[int] = 8) -> List[Layout]:
    """candidates() for a clicked FACE: the Layout list from what fit_on_loops
    really builds at each 0.5 mm size -- holes, notches and round outlines
    count -- every distinct cols x rows at the LARGEST size that gives it,
    biggest marker first, badged. Picking an entry and typing its size give
    the same board (both go through fit_on_loops).

    Zach, 2026-10-08: the list offered "4 × 1 @ 30.5" on a plate whose end
    holes leave room for 3 -- candidates() had sized the face's box."""
    kw = dict(dictionary=dictionary, quiet_modules=quiet_modules, gap_ratio=gap_ratio,
              angles_deg=angles_deg)
    out: List[Layout] = []
    if kind not in LAYOUTS:
        return out
    n = _modules(dictionary)
    floor_L = MIN_MODULE_M * n - 1e-12
    cap = int(limit) if limit else 0
    huge = 10.0                                   # m: a typed size this big clamps to the largest
    if kind == "charuco":
        first, _pl = fit_on_loops(kind, loops_m, marker_m=None, square_m=huge, **kw)
        k = int(math.floor(first.square_m / STEP_M + _K_EPS)) if first.square_m > 0 else 0
    else:
        first, _pl = fit_on_loops(kind, loops_m, marker_m=huge, **kw)
        k = int(math.floor(first.marker_m / STEP_M + _K_EPS)) if first.marker_m > 0 else 0
    k0 = k
    def at(kk):
        size = _from_k(kk)
        if kind == "charuco":
            if _snap_down(_lmax(size, n)) < floor_L:
                return None
            lay, _pl = fit_on_loops(kind, loops_m, marker_m=None, square_m=size, **kw)
        else:
            if size < floor_L:
                return None
            lay, _pl = fit_on_loops(kind, loops_m, marker_m=size, **kw)
        # a clamp means this size did not fit as asked: its board belongs to
        # the smaller size it was clamped to, which the walk reaches anyway
        if not (lay.ok and lay.kind == kind) or any(t.startswith("clamped") for t in lay.notes):
            return False
        return lay

    # 1 mm strides (each fit is 10-40 ms), then the 0.5 mm step above a new
    # board is tried, so every entry is still the LARGEST 0.5 mm size for it
    seen = set()
    while k > 0:
        lay = at(k)
        if lay is None:
            break
        if lay and (lay.cols, lay.rows) not in seen:
            up = at(k + 1) if k + 1 <= k0 else False
            if up and (up.cols, up.rows) == (lay.cols, lay.rows):
                lay = up
            seen.add((lay.cols, lay.rows))
            out.append(lay)
            if kind == "single" or (cap and len(out) >= cap):
                break
        k -= 2
    out.sort(key=lambda l: (-(l.square_m if kind == "charuco" else l.marker_m), l.count))
    return _badge(out)


def auto_dictionary(dictionary_or_family: str, need: int, in_use_ids: Iterable[int] = ()) -> str:
    """Keep the family (4x4 unless chosen), pick the capacity from the design:
    the smallest of 50 / 100 / 250 / 1000 that holds the ids in use + this
    board + headroom, headroom = max(2 x total, total + 10) as markers.suggest
    does, and still has a free block of `need` consecutive ids. 5 markers in
    an empty design -> DICT_4X4_50; with 40 in use -> DICT_4X4_100; 13 with 60
    in use -> DICT_4X4_250. Growing capacity never reprints: 4X4_50 is the
    first 50 patterns of 4X4_1000.

    `dictionary_or_family`: "DICT_5X5_100", "DICT_5X5", "5x5" or "aruco5x5".
    A dictionary without a 50/100/250/1000 family (ARUCO_ORIGINAL, AprilTag)
    comes back unchanged. `in_use_ids`: ids already printed in this family
    (Resolution.used_ids(dictionary)), excluding the board being edited."""
    name = str(dictionary_or_family or "").strip()
    m = re.match(r"^(?:DICT_|ARUCO)?(\d)X(\d)(?:_\d+)?$", name.upper())
    if not m or m.group(1) != m.group(2):
        return name if name in ARUCO_DICTS else DEFAULT_DICTIONARY
    bits = int(m.group(1))
    used = set()
    for i in in_use_ids or ():
        try:
            v = int(i)
        except (TypeError, ValueError):
            continue
        if v >= 0:
            used.add(v)
    block = max(1, int(_num(need)))
    total = len(used) + block
    headroom = max(2 * total, total + 10)
    family = ["DICT_%dX%d_%d" % (bits, bits, c) for c in CAPACITIES
              if "DICT_%dX%d_%d" % (bits, bits, c) in ARUCO_DICTS]
    if not family:
        return name if name in ARUCO_DICTS else DEFAULT_DICTIONARY
    for d in family:
        cap = ARUCO_DICTS[d]
        if cap >= headroom and any(not any(s + k in used for k in range(block))
                                   for s in range(cap - block + 1)):
            return d
    return family[-1]


# ------------------------------------------------------- a face's outline

def _clean(loop) -> List[Tuple[float, float]]:
    """Floats, no repeated points, no collinear middle points."""
    pts: List[Tuple[float, float]] = []
    for p in loop or ():
        try:
            x, y = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError):
            continue
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        if pts and abs(x - pts[-1][0]) <= EPS_M and abs(y - pts[-1][1]) <= EPS_M:
            continue
        pts.append((x, y))
    while len(pts) > 1 and abs(pts[0][0] - pts[-1][0]) <= EPS_M and abs(pts[0][1] - pts[-1][1]) <= EPS_M:
        pts.pop()
    changed = True
    while changed and len(pts) > 3:
        changed = False
        for i in range(len(pts)):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
            ux, uy, vx, vy = b[0] - a[0], b[1] - a[1], c[0] - b[0], c[1] - b[1]
            lu, lv = math.hypot(ux, uy), math.hypot(vx, vy)
            if lu * lv == 0 or (abs(ux * vy - uy * vx) <= 1e-7 * lu * lv and ux * vx + uy * vy > 0):
                del pts[i]
                changed = True
                break
    return pts


def _area(loop) -> float:
    return 0.5 * sum(loop[i - 1][0] * loop[i][1] - loop[i][0] * loop[i - 1][1]
                     for i in range(len(loop)))


def _inside(loops, x: float, y: float) -> bool:
    """Even-odd over every loop (outer and holes alike)."""
    c = False
    for loop in loops:
        for i in range(len(loop)):
            (x0, y0), (x1, y1) = loop[i - 1], loop[i]
            if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
                c = not c
    return c


def _seg_hits_box(ax, ay, bx, by, x0, y0, x1, y1) -> bool:
    """Does segment a-b touch the closed box? (Liang-Barsky)"""
    t0, t1 = 0.0, 1.0
    dx, dy = bx - ax, by - ay
    for p, qv in ((-dx, ax - x0), (dx, x1 - ax), (-dy, ay - y0), (dy, y1 - ay)):
        if p == 0:
            if qv < 0:
                return False
        else:
            t = qv / p
            if p < 0:
                if t > t1:
                    return False
                t0 = max(t0, t)
            else:
                if t < t0:
                    return False
                t1 = min(t1, t)
    return True


TOUCH_M = 5e-8                # 50 nm: a tile may touch the outline (or a hole) this closely


def _rect_inside(loops, cx: float, cy: float, w: float, h: float, tol: float = TOUCH_M) -> bool:
    """Exact: the axis-aligned w x h box centred at (cx, cy) lies in the
    region (may touch its boundary -- to within tol --, never crosses it,
    holds no hole)."""
    x0, x1 = cx - w / 2.0 + tol, cx + w / 2.0 - tol
    y0, y1 = cy - h / 2.0 + tol, cy + h / 2.0 - tol
    if x0 > x1 or y0 > y1 or not _inside(loops, cx, cy):
        return False
    for loop in loops:
        for i in range(len(loop)):
            (ax, ay), (bx, by) = loop[i - 1], loop[i]
            if _seg_hits_box(ax, ay, bx, by, x0, y0, x1, y1):
                return False
    return True


# ------------------------------------------- the largest clear rectangle
#
# Zach, 2026-10-08, on a 53 mm disc with four screw holes: "When I click the
# face I want it to also detect the largest rectangle and then use that to
# intelligently set the default". The rectangle is found to a proven bound,
# not sampled. An axis-aligned w x h box centred at p lies on the region iff
# p is on it and no boundary segment comes nearer than h / 2 in the norm
# max(|dx| / a, |dy|), a = w / h (a box that held a hole would hold the
# hole's edges). So the largest clear box of aspect a is twice the largest
# such distance -- a "pole of inaccessibility" in that norm -- which a branch
# and bound over square cells finds: a cell can do no better than its
# centre's distance + its half-size, so whole cells are dropped unseen
# (Mapbox's polylabel, in the box norm). A grid of cells first, then each
# promising cell bisected into four: the grid search and its refinement.

CLEAR_EPS_M = 2e-5        # the search's proven optimality gap on the half-height (0.02 mm)
SCAN_EPS_M = 1e-4         # ... while scanning aspects for the largest-area rectangle (0.1 mm)
_CLEAR_CAP = 30000        # cell evaluations per search: a safety net
_REACH_CAP = 6000         # ... per "does it fit?" (a capped one says no: conservative)
_QUAD = ((-1.0, -1.0), (1.0, -1.0), (-1.0, 1.0), (1.0, 1.0))
# trial aspects (w / h) for "the largest clear rectangle": 1, then 2^(k/4)
# wider-first, out to 16 : 1 -- the best of them is refined in between
ASPECTS = (1.0,) + tuple(a for k in range(1, 17) for a in (2.0 ** (k / 4.0), 2.0 ** (-k / 4.0)))


def _sdist(segs, px: float, py: float) -> float:
    """Signed box-norm distance from (px, py) to the region's boundary
    (> 0 inside, even-odd over every loop). segs are _Clear._segs rows."""
    inside = False
    best = math.inf
    for ax, ay, bx, by, ux, uy, lx, hx, ly, hy in segs:
        if (ay > py) != (by > py) and px < ax + (py - ay) * ux / uy:
            inside = not inside
        if lx - px >= best or px - hx >= best or ly - py >= best or py - hy >= best:
            continue                                # its bounding box is already farther
        X, Y = ax - px, ay - py
        # max(|X + t ux|, |Y + t uy|) is convex and piecewise linear in t:
        # its least value on [0, 1] is at an end or where |X| = |Y|
        d = abs(X) if abs(X) > abs(Y) else abs(Y)
        e = abs(X + ux) if abs(X + ux) > abs(Y + uy) else abs(Y + uy)
        if e < d:
            d = e
        k = ux - uy
        if k:
            t = (Y - X) / k
            if 0.0 < t < 1.0:
                v = abs(X + t * ux)
                if v < d:
                    d = v
        k = ux + uy
        if k:
            t = -(X + Y) / k
            if 0.0 < t < 1.0:
                v = abs(X + t * ux)
                if v < d:
                    d = v
        if d < best:
            best = d
    return best if inside else -best


def _pole(segs, x0, y0, x1, y1, eps, need=None, cap=None):
    """Branch and bound for the point farthest from the boundary (box norm).

    need None: the farthest, to within eps -> (d, x, y, ub), ub >= the true
    optimum (best potential first). need given: does any point reach it?
    Best CENTRE first -- a dive that homes in on a touching optimum (a 50 mm
    tile in a 50 mm gap lies on a line no cell centre hits) -- stopping at
    the first point within TOUCH_M of `need` (-> d >= need - TOUCH_M) or once
    no cell can reach it (-> d < need - TOUCH_M)."""
    if cap is None:
        cap = _CLEAR_CAP if need is None else _REACH_CAP
    tol = TOUCH_M
    W, H = x1 - x0, y1 - y0
    cell = min(W, H)
    if not cell > 0:
        return -1.0, x0, y0, -1.0
    h = cell / 2.0
    heap = []
    best, bx, by = -math.inf, (x0 + x1) / 2.0, (y0 + y1) / 2.0
    for i in range(max(1, int(math.ceil(W / cell - 1e-9)))):
        for j in range(max(1, int(math.ceil(H / cell - 1e-9)))):
            cx, cy = x0 + (i + 0.5) * cell, y0 + (j + 0.5) * cell
            d = _sdist(segs, cx, cy)
            if d > best:
                best, bx, by = d, cx, cy
            heapq.heappush(heap, (-(d + h) if need is None else -d, d, cx, cy, h))
    if need is not None and best >= need - tol:
        return best, bx, by, best
    floor_h = 1e-10
    evals = 0
    while heap and evals < cap:
        if need is None and -heap[0][0] - best <= eps:
            break                               # nothing left can beat best by more than eps
        _k, _d, cx, cy, h = heapq.heappop(heap)
        h2 = h / 2.0
        for sx, sy in _QUAD:
            qx, qy = cx + sx * h2, cy + sy * h2
            d = _sdist(segs, qx, qy)
            evals += 1
            if d > best:
                best, bx, by = d, qx, qy
                if need is not None and best >= need - tol:
                    return best, bx, by, best
            if h2 > floor_h:
                if need is None:
                    if d + h2 > best + eps:
                        heapq.heappush(heap, (-(d + h2), d, qx, qy, h2))
                elif d + h2 >= need - tol:
                    heapq.heappush(heap, (-d, d, qx, qy, h2))
    ub = best + (eps if need is None else 0.0)
    if heap and need is None:
        ub = max(ub, -heap[0][0])
    return best, bx, by, ub


class _Clear:
    """The largest clear axis-aligned rectangles of one region (loops in one
    frame): largest(a) = the biggest of aspect a, with a proven upper bound;
    reach(a, half_h) = somewhere one of half-height half_h fits, or None."""

    def __init__(self, loops):
        self.segs = [(lp[i - 1][0], lp[i - 1][1], lp[i][0], lp[i][1])
                     for lp in loops for i in range(len(lp))]
        xs = [p[0] for lp in loops for p in lp]
        ys = [p[1] for lp in loops for p in lp]
        self.box = (min(xs), min(ys), max(xs), max(ys))
        self._sc: Dict[float, list] = {}
        self._best: Dict[float, tuple] = {}
        self._reach: Dict[tuple, Optional[Tuple[float, float]]] = {}

    def _segs(self, a: float):
        key = round(a, 12)
        if key not in self._sc:
            rows = []
            for ax, ay, bx, by in self.segs:
                ax, bx = ax / a, bx / a
                rows.append((ax, ay, bx, by, bx - ax, by - ay, min(ax, bx), max(ax, bx),
                             min(ay, by), max(ay, by)))
            self._sc[key] = rows
        return self._sc[key]

    def known(self, a: float):
        got = self._best.get(round(a, 12))
        return got[:4] if got is not None else None

    def largest(self, a: float, eps: float = CLEAR_EPS_M):
        """(half_h, cx, cy, ub) of the largest clear a : 1 rectangle: half
        its height, its centre, and a bound the true half-height is under
        (ub - half_h <= eps)."""
        key = round(a, 12)
        got = self._best.get(key)
        if got is None or got[4] > eps:
            x0, y0, x1, y1 = self.box
            d, x, y, ub = _pole(self._segs(a), x0 / a, y0, x1 / a, y1, eps)
            got = self._best[key] = (d, x * a, y, ub, eps)
        return got[:4]

    def reach(self, a: float, half_h: float):
        key = (round(a, 12), round(half_h, 12))
        if key not in self._reach:
            got = self.known(a)
            if got is not None and got[0] >= half_h:
                at = (got[1], got[2])
            elif got is not None and half_h > got[3] + TOUCH_M:
                at = None
            else:
                x0, y0, x1, y1 = self.box
                d, x, y, _ub = _pole(self._segs(a), x0 / a, y0, x1 / a, y1, 0.0, need=half_h)
                at = (x * a, y) if d >= half_h - TOUCH_M else None
            self._reach[key] = at
        return self._reach[key]


def _reach(loops, cx, cy, w, h, span, axis) -> Tuple[float, float]:
    """How far the placed box can slide along x (axis 0) or y each way."""
    out = []
    for sign in (-1.0, 1.0):
        lo, hi = 0.0, span                      # lo: still inside; hi: outside
        for _ in range(32):
            mid = (lo + hi) / 2.0
            dx, dy = (sign * mid, 0.0) if axis == 0 else (0.0, sign * mid)
            if _rect_inside(loops, cx + dx, cy + dy, w, h):
                lo = mid
            else:
                hi = mid
        out.append(lo)
    return out[0], out[1]


def _centre(loops, cx: float, cy: float, w: float, h: float, span: float):
    """Slide a placed box to the middle of the room it has along x, then y
    (between a hole and an edge, say), so leftover is split evenly the way
    a rectangle's margins are. Every move is re-checked; a move that would
    not hold keeps the old position. Returns (cx, cy, room_x, room_y), the
    room = the free slide on each side at the final position."""
    for axis in (0, 1):
        a, b = _reach(loops, cx, cy, w, h, span, axis)
        shift = (b - a) / 2.0
        nx, ny = (cx + shift, cy) if axis == 0 else (cx, cy + shift)
        if abs(shift) > EPS_M and _rect_inside(loops, nx, ny, w, h):
            cx, cy = nx, ny
    rooms = [min(_reach(loops, cx, cy, w, h, span, axis)) for axis in (0, 1)]
    return cx, cy, rooms[0], rooms[1]


class _Frame:
    """The region seen at one trial angle: loops rotated so the tile's x
    axis is +x, their bounding box, and its clear rectangles."""

    def __init__(self, angle_deg: float, loops):
        self.angle = float(angle_deg)
        t = math.radians(self.angle)
        self.c, self.s = math.cos(t), math.sin(t)
        c, s = self.c, self.s
        self.rot = [[(x * c + y * s, -x * s + y * c) for x, y in loop] for loop in loops]
        xs = [p[0] for loop in self.rot for p in loop]
        ys = [p[1] for loop in self.rot for p in loop]
        self.x0, self.x1, self.y0, self.y1 = min(xs), max(xs), min(ys), max(ys)
        self.W, self.H = self.x1 - self.x0, self.y1 - self.y0
        self.rect = None                       # an exact axis-aligned rectangle (one loop)?
        if len(self.rot) == 1 and len(self.rot[0]) == 4 and self.W > 0 and self.H > 0:
            tol = max(EPS_M, 1e-6 * max(self.W, self.H))
            lp = self.rot[0]
            if all(abs(lp[i][0] - lp[i - 1][0]) <= tol or abs(lp[i][1] - lp[i - 1][1]) <= tol
                   for i in range(4)):
                self.rect = (self.x0, self.y0, self.x1, self.y1)
        self.clear = _Clear(self.rot)

    def place(self, w: float, h: float) -> Optional[Tuple[float, float]]:
        """A centre where a w x h tile lies clear (exactly re-checked), or None."""
        if not (w > 0 and h > 0):
            return None
        at = self.clear.reach(w / h, h / 2.0)
        if at is None or not _rect_inside(self.rot, at[0], at[1], w, h):
            return None
        return at

    def to_input(self, x: float, y: float) -> Tuple[float, float]:
        return (x * self.c - y * self.s, x * self.s + y * self.c)

    def rects(self) -> list:
        """(area, aspect, half_h, cx, cy) of the largest clear rectangle at
        every trial aspect, plus the best of them refined between its
        neighbours (golden section on log aspect)."""
        out = []
        for a in ASPECTS:
            d, cx, cy, _ub = self.clear.largest(a, SCAN_EPS_M)
            if d > 0:
                out.append((4.0 * a * d * d, a, d, cx, cy))
        if out:
            a0 = max(out, key=lambda t: t[0])[1]
            lo, hi = math.log(a0) - math.log(2.0) / 4.0, math.log(a0) + math.log(2.0) / 4.0

            def area(u):
                a = math.exp(u)
                d = self.clear.largest(a, SCAN_EPS_M)[0]
                return 4.0 * a * d * d if d > 0 else 0.0
            g = (math.sqrt(5.0) - 1.0) / 2.0
            u1, u2 = hi - g * (hi - lo), lo + g * (hi - lo)
            f1, f2 = area(u1), area(u2)
            for _ in range(10):
                if f1 >= f2:
                    hi, u2, f2 = u2, u1, f1
                    u1 = hi - g * (hi - lo)
                    f1 = area(u1)
                else:
                    lo, u1, f1 = u1, u2, f2
                    u2 = lo + g * (hi - lo)
                    f2 = area(u2)
            a = math.exp(u1 if f1 >= f2 else u2)
            d, cx, cy, _ub = self.clear.largest(a, SCAN_EPS_M)
            if d > 0:
                out.append((4.0 * a * d * d, a, d, cx, cy))
        return out


def _grid_factor(k: int, n: int, q: float, g: float) -> float:
    """Tile length / L for k markers in a row (gaps + both quiet zones)."""
    return k + (k - 1) * max(g, 1.0 / n) + 2.0 * q / n


def _search_frame(kind, fr: _Frame, L, S, kw):
    """As many markers as fit at (L, S) in this frame: (count, c, r, (cx, cy), tw, th) or None."""
    n = _modules(kw["dictionary"])
    q = 0.0 if kind == "charuco" else kw["quiet_modules"]
    Q = q * L / n
    s = _r7(max(kw["gap_ratio"] * L, L / n)) if kind == "grid" else 0.0

    def dims(c, r):
        if kind == "charuco":
            return c * S, r * S
        return c * L + (c - 1) * s + 2 * Q, r * L + (r - 1) * s + 2 * Q

    def fits(c, r):
        return fr.place(*dims(c, r))

    if kind == "single":
        at = fits(1, 1)
        return (1, 1, 1, at) + dims(1, 1) if at else None
    if kind == "grid":
        cmax, rmax, lo = _fit_count(fr.W - 2 * Q + s, L + s), _fit_count(fr.H - 2 * Q + s, L + s), 1
    else:
        cmax, rmax, lo = _fit_count(fr.W, S), _fit_count(fr.H, S), 2
    best = None
    for r in range(lo, rmax + 1):
        c_lo = 2 if (kind == "grid" and r == 1) else lo
        if c_lo > cmax:
            continue
        at = fits(c_lo, r)
        if at is None:
            if kind == "grid" and r == 1:       # 2 x 1 failing says nothing about 1 x 2
                continue
            break                               # (c_lo, r) failing: no taller board fits either
        good_c, good_at = c_lo, at
        a, b = c_lo + 1, cmax
        while a <= b:                           # the widest row count that still fits
            m = (a + b) // 2
            am = fits(m, r)
            if am is not None:
                good_c, good_at, a = m, am, m + 1
            else:
                b = m - 1
        cnt = good_c * r if kind == "grid" else (good_c * r) // 2
        if best is None or cnt > best[0]:
            best = (cnt, good_c, r, good_at) + dims(good_c, r)
    return best


def _region_hit(kind, frames, L, S, kw):
    """The most markers at (L, S) over every trial angle (ties: the earlier angle)."""
    best = None
    for fr in frames:
        got = _search_frame(kind, fr, L, S, kw)
        if got is not None and (best is None or got[0] > best[0][0]):
            best = (got, fr)
    return best


def _region_clamp(kind, frames, L, S, floor_L, kw):
    """The largest 0.5 mm step at or below (L, S) whose smallest board fits
    somewhere on the region: (L, S, hit) or None. Starts from each frame's
    proven upper bound and steps down to the first that holds (a step or two)."""
    n = _modules(kw["dictionary"])
    q, g = kw["quiet_modules"], kw["gap_ratio"]
    top = None
    for fr in frames:
        if kind == "charuco":                   # 2 x 2 squares: the tile is 2 S, half S
            ub = fr.clear.largest(1.0)[3]
            s2 = min(S, _snap_down(ub))
            cand = (s2, min(L, _snap_down(_lmax(s2, n))))
        elif kind == "single":
            ub = fr.clear.largest(1.0)[3]
            cand = (min(L, _snap_down(2.0 * ub * n / (n + 2.0 * q))), 0.0)
        else:
            lm = 0.0
            for c, r in ((2, 1), (1, 2)):
                fc, frr = _grid_factor(c, n, q, g), _grid_factor(r, n, q, g)
                lm = max(lm, 2.0 * fr.clear.largest(fc / frr)[3] / frr)
            cand = (min(L, _snap_down(lm)), 0.0)
        if top is None or cand > top:
            top = cand
    if top is None:
        return None
    if kind == "charuco":
        S2, L2 = top
    else:
        L2, S2 = top
    for _ in range(24):
        if L2 <= 0 or L2 < floor_L - 1e-12 or (kind == "charuco" and S2 <= 0):
            return None
        hit = _region_hit(kind, frames, L2, S2, kw)
        if hit is not None:
            return L2, S2, hit
        if kind == "charuco":
            S2 = _next_down(S2)
            L2 = min(L2, _snap_down(_lmax(S2, n)))
        else:
            L2 = _next_down(L2)
    return None


def _placed(lay: Layout, fr: _Frame, cx: float, cy: float, clear=None):
    """The placement dict in the input frame; `clear` = (cx, cy, w, h) in
    the frame: the clear rectangle the size came from."""
    if lay.marker_m <= 0:
        return None
    tw, th = lay.tile_m                         # exact, not rounded: never smaller than the tile
    x, y = fr.to_input(cx, cy)
    out = {"cx": x, "cy": y, "angle_deg": fr.angle, "w": tw, "h": th}
    if clear is not None:
        ccx, ccy = fr.to_input(clear[0], clear[1])
        out["clear"] = {"cx": ccx, "cy": ccy, "w": clear[2], "h": clear[3]}
    return out


def _settle(lay: Layout, fr: _Frame, cx: float, cy: float, region_area: float, clear=None,
            move: bool = True):
    """Centre the tile in its room (move=False: it stays, concentric with
    the clear rectangle it was sized in), set the margins (pattern ->
    nearest boundary along x / y) and the fill (pattern / face area)."""
    tw, th = lay.tile_m
    span = max(fr.W, fr.H)
    if move:
        cx, cy, room_x, room_y = _centre(fr.rot, cx, cy, tw, th, span)
    else:
        room_x, room_y = (min(_reach(fr.rot, cx, cy, tw, th, span, axis)) for axis in (0, 1))
    q = lay.quiet_m
    lay.margins_m = (_r7(q + room_x), _r7(q + room_y))
    ew, eh = lay.extent_m
    lay.fill = (ew * eh) / region_area
    return lay, _placed(lay, fr, cx, cy, clear)


def _single_top(fr: _Frame, n: int, q: float, floor_L: float):
    """The largest 0.5 mm single marker >= floor_L whose tile fits on the
    frame's region: (L, centre, half) or None. From the proven upper bound
    down to the first step that holds (exact: the step above does not)."""
    f = 1.0 + 2.0 * q / n
    c2 = 2.0 * FILL_CLEARANCE_M                    # never flush with the outline
    d, cx, cy, ub = fr.clear.largest(1.0)
    if not ub > 0:
        return None
    L = _snap_down((2.0 * ub - c2) / f)
    while L > 0 and L >= floor_L - 1e-12:
        t = L * f + c2
        at = (cx, cy) if (t / 2.0 <= d and _rect_inside(fr.rot, cx, cy, t, t)) else fr.place(t, t)
        if at is not None:
            return L, at, d
        L = _next_down(L)
    return None


def _auto_single(frames, region_area, kw):
    n = _modules(kw["dictionary"])
    q = kw["quiet_modules"]
    printable = MIN_MODULE_M * n
    below = False
    best = None
    for floor_L in (printable, STEP_M):
        for fr in frames:
            got = _single_top(fr, n, q, floor_L)
            if got is not None and (best is None or got[0] > best[0][0] + 1e-12):
                best = (got, fr)
        if best is not None:
            break
        below = True
    if best is None:
        return _nothing("single", kw, "nothing fits on this face"), None
    (L, (cx, cy), d), fr = best
    side = 2.0 * d
    t = L * (1.0 + 2.0 * q / n)
    lay = _finish(Layout("single", L, dictionary=kw["dictionary"]), t, t, kw,
                  ["not a rectangle: the sticker fills the face's largest clear square, "
                   "%.1f mm (holes and notches avoided)" % (side * 1000.0)])
    if below:
        _unprintable(lay)
    # the clear square slid to the middle of its room (along a leg, say), the
    # tile at its centre: what is drawn is where the size came from
    pd, pcx, pcy, _ub = fr.clear.known(1.0)
    span = max(fr.W, fr.H)
    if t <= side + 1e-12 and _rect_inside(fr.rot, pcx, pcy, side, side):
        ncx, ncy, _rx, _ry = _centre(fr.rot, pcx, pcy, side, side, span)
        if _rect_inside(fr.rot, ncx, ncy, t, t):
            return _settle(lay, fr, ncx, ncy, region_area, (ncx, ncy, side, side), move=False)
    # the tile is the clear square (a touching optimum the search only bounds)
    ncx, ncy, _rx, _ry = _centre(fr.rot, cx, cy, t, t, span)
    return _settle(lay, fr, ncx, ncy, region_area, (ncx, ncy, max(side, t), max(side, t)), move=False)


def _expand(fr: _Frame, x0: float, y0: float, x1: float, y1: float):
    """Push each side of a clear box out as far as it stays clear
    (bisection to 0.1 nm): a maximal clear rectangle that contains it. The
    branch and bound's box is only as exact as its aspect; a leg of an L
    is exactly 20 x 60, and a 10 mm checker needs every bit of the 20."""
    def ok(b):
        return _rect_inside(fr.rot, (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0, b[2] - b[0], b[3] - b[1])
    box = [x0, y0, x1, y1]
    if not ok(box):
        return tuple(box)
    lim = (fr.x0, fr.y0, fr.x1, fr.y1)
    for side in range(4):
        sign = -1.0 if side < 2 else 1.0
        lo, hi = 0.0, abs(lim[side] - box[side]) + 1e-9
        while hi - lo > 1e-10:
            m = (lo + hi) / 2.0
            trial = list(box)
            trial[side] += sign * m
            if ok(trial):
                lo = m
            else:
                hi = m
        box[side] += sign * lo
    return tuple(box)


def _auto_board(kind, frames, region_area, kw):
    """Grid / ChArUco: the doc's default rule (default_layout) on the
    largest clear rectangle; the next largest when that one holds no valid
    board of this kind (a thin strip and ChArUco's 2 x 2, say)."""
    ranked = []
    for fi, fr in enumerate(frames):
        for ai, (area, a, d, cx, cy) in enumerate(fr.rects()):
            ranked.append([area, fi, ai, a, d, cx, cy, fr])
    if not ranked:
        return _nothing(kind, kw, "nothing fits on this face"), None
    top = max(r[0] for r in ranked)
    # within 0.1 % of each other an earlier angle / aspect wins (1 : 1, then wider)
    ranked.sort(key=lambda r: (-int(r[0] / top * 1000.0 + 1e-9), r[1], r[2]))
    fallback = chosen = None
    for _area, _fi, _ai, a, _d, _cx, _cy, fr in ranked[:12]:
        d, cx, cy, _ub = fr.clear.largest(a)         # this one to the fine bound
        bx0, by0, bx1, by1 = _expand(fr, cx - a * d, cy - d, cx + a * d, cy + d)
        # to 1 um: the 50 nm a touching box may overshoot must not tip a rule
        # threshold (an L's 20 x 60 leg is exactly a 3 : 1 strip)
        W, H = round(bx1 - bx0, 6), round(by1 - by0, 6)
        cx, cy = (bx0 + bx1) / 2.0, (by0 + by1) / 2.0
        lay = default_layout(kind, W, H, **kw)
        if lay.marker_m <= 0:
            continue
        tw, th = lay.tile_m
        at = ((cx, cy) if tw <= W + 1e-12 and th <= H + 1e-12 and _rect_inside(fr.rot, cx, cy, tw, th)
              else fr.place(tw, th))
        if at is None:
            continue
        seen = (cx, cy, W, H)                       # the clear rectangle (shown)
        if lay.ok and lay.kind == kind:
            chosen = (lay, fr, at, seen)
            break
        if fallback is None or lay.marker_m > fallback[0].marker_m + 1e-12:
            fallback = (lay, fr, at, seen)          # nothing printable: the largest that fits
    chosen = chosen or fallback
    if chosen is None:
        return _nothing(kind, kw, "nothing fits on this face"), None
    lay, fr, (cx, cy), seen = chosen
    lay.notes = (["not a rectangle: sized in the face's largest clear rectangle, %.1f × %.1f mm "
                  "(holes and notches avoided)" % (seen[2] * 1000.0, seen[3] * 1000.0)]
                 + [t for t in lay.notes if not t.startswith("default:")])
    # the clear rectangle slid to the middle of its room, the board at its centre
    tw, th = lay.tile_m
    scx, scy, sw, sh = seen
    if _rect_inside(fr.rot, scx, scy, sw, sh):
        ncx, ncy, _rx, _ry = _centre(fr.rot, scx, scy, sw, sh, max(fr.W, fr.H))
        if _rect_inside(fr.rot, ncx, ncy, tw, th):
            return _settle(lay, fr, ncx, ncy, region_area, (ncx, ncy, sw, sh), move=False)
    return _settle(lay, fr, cx, cy, region_area, seen)


def largest_clear_rect(loops_m: Sequence[Sequence[Tuple[float, float]]], aspect: Optional[float] = None,
                       angle_deg: float = 0.0) -> Optional[dict]:
    """The largest axis-aligned rectangle (at angle_deg) clear of the
    outline, holes and notches: aspect w / h given -> the largest of that
    aspect; None -> the largest area over ASPECTS, refined. Within
    CLEAR_EPS_M of the optimum on the half-height (proven bound).
    -> {"cx", "cy", "w", "h", "angle_deg"} in the loops' frame, or None."""
    loops = [lp for lp in (_clean(l) for l in (loops_m or ())) if len(lp) >= 3]
    if not loops:
        return None
    fr = _Frame(angle_deg, loops)
    if aspect is None:
        rs = fr.rects()
        if not rs:
            return None
        _area, a, d, cx, cy = max(rs, key=lambda t: t[0])
    else:
        a = float(aspect)
        if not a > 0:
            return None
        d, cx, cy, _ub = fr.clear.largest(a)
        if not d > 0:
            return None
    x, y = fr.to_input(cx, cy)
    return {"cx": x, "cy": y, "w": 2.0 * a * d, "h": 2.0 * d, "angle_deg": fr.angle}


def fit_on_loops(kind: str, loops_m: Sequence[Sequence[Tuple[float, float]]], *,
                 marker_m: Optional[float] = None, square_m: float = 0.0,
                 dictionary: str = DEFAULT_DICTIONARY, quiet_modules: float = 1.0,
                 gap_ratio: float = GAP_RATIO, angles_deg: Sequence[float] = (0.0, 90.0),
                 cells: int = 120) -> Tuple[Layout, Optional[dict]]:
    """Size and place a tag on a clicked planar face that may not be a rectangle.

    loops_m: the face's loops as 2D polygons in METRES in the face's own frame
    (first = outer boundary, the rest = holes; even-odd decides inside) --
    aruco_tags.face_loops_2d's output times 0.01. Returns (layout, placement),
    placement = {"cx", "cy", "angle_deg", "w", "h", "clear"} in that frame:
    the rectangle the tile gets (pattern + quiet zone; ChArUco: the squares),
    centred at (cx, cy), its w along the direction angle_deg from +x, and
    "clear" = {"cx", "cy", "w", "h"}, the clear rectangle the size came from
    (a typed size: absent). The whole tile lies inside the outer loop and
    outside every hole (re-checked exactly against the polygons).

    marker_m None (and, for ChArUco, square_m 0) = the face default, from the
    face's LARGEST CLEAR RECTANGLE (largest_clear_rect), not its box:
      single   the largest 0.5 mm marker whose tile (marker + quiet zone)
               fits the largest clear square -- the sticker fills it
               (face_default on a plain rectangle)
      grid,    default_layout (the doc's 70 % rule) on the largest clear
      charuco  rectangle, the next largest if that one holds no board
    A plain rectangle is its own largest clear rectangle: face_default on it.

    A given size is kept if any placement holds it, with as many markers as
    fit anywhere on the face (count first, then the earlier angle); if none
    fits, the largest 0.5 mm step below that does, noted ("clamped: ...").
    Exact: the clear rectangles come from a branch and bound with a proven
    bound (CLEAR_EPS_M), then the step above the answer is tried and refused.

    Nothing fits at all -> (Layout with marker_m 0, ok False, a note), None.
    Nothing printable fits -> the largest that fits, ok False, a note, and a
    placement. `cells` is ignored (the old sampling's resolution). Never raises."""
    kw = _kw(dictionary, quiet_modules, gap_ratio)
    if kind not in LAYOUTS:
        return _layout(kind, 1.0, 1.0, 0.0, 0.0, **kw), None
    loops = [lp for lp in (_clean(l) for l in (loops_m or ())) if len(lp) >= 3]
    areas = sorted((abs(_area(lp)) for lp in loops), reverse=True)
    region_area = (areas[0] - sum(areas[1:])) if areas else 0.0
    if not loops or region_area <= EPS_M * EPS_M:
        return _nothing(kind, kw, "the face has no outline"), None
    n = _modules(dictionary)
    angles: List[float] = []
    for a in (_num(a) for a in (angles_deg or ())):
        # a c x r tile at a + 90 has the footprint of r x c at a, which the
        # search at a already tries: one angle per class mod 90 is enough
        if not any(abs(((a - b) / 90.0) - round((a - b) / 90.0)) < 1e-9 for b in angles):
            angles.append(a)
    angles = angles or [0.0]
    frames = [_Frame(a, loops) for a in angles]
    auto = (marker_m is None or _num(marker_m) <= 0) and (kind != "charuco" or _num(square_m) <= 0)
    L, S = _typed(kind, None if auto else marker_m, None if auto else square_m, n)

    rf = next((f for f in frames if f.rect is not None), None)
    if rf is not None:                          # a plain rectangle: the box answer, exactly
        lay = (face_default(kind, rf.W, rf.H, **kw) if auto
               else clamp_to_fit(kind, rf.W, rf.H, L, S, **kw))
        cx, cy = (rf.x0 + rf.x1) / 2.0, (rf.y0 + rf.y1) / 2.0
        return lay, _placed(lay, rf, cx, cy, (cx, cy, rf.W, rf.H) if auto else None)

    if auto:
        if kind == "single":
            return _auto_single(frames, region_area, kw)
        return _auto_board(kind, frames, region_area, kw)
    if L <= 0 or (kind == "charuco" and S <= 0):
        return _layout(kind, 1.0, 1.0, L, S, **kw), None
    printable = MIN_MODULE_M * n
    want_printable = L >= printable - 1e-12

    def attempt(floor_L):
        if L >= floor_L - 1e-12:
            hit = _region_hit(kind, frames, L, S, kw)
            if hit is not None:
                return L, S, hit
        return _region_clamp(kind, frames, L, S, floor_L, kw)

    got = attempt(printable if want_printable else STEP_M)
    below = False
    if got is None and want_printable:
        got = attempt(STEP_M)
        below = got is not None
    if got is None:
        return _nothing(kind, kw, "nothing fits on this face"), None
    L2, S2, ((cnt, c, r, (cx, cy), tw, th), fr) = got
    notes = ["not a rectangle: fitted to the face's own outline (holes and notches avoided)"]
    if abs(L2 - L) > EPS_M or abs(S2 - S) > EPS_M:
        notes.append(_changed_note("clamped", kind, L, S, L2, S2))
    base = Layout(kind, L2, S2, cols=c, rows=r, dictionary=dictionary)
    lay = _finish(base, tw, th, kw, notes)
    if below:
        _unprintable(lay)
    return _settle(lay, fr, cx, cy, region_area)
