"""ArUco Extrude's optional LABEL: text printed on the tag (the pure core;
standard library only, it runs inside Fusion).

Zach, 2026-10-09: "Earlier I had said I didn't want a label on the white out
of the quiet space. But actually I can see it can be useful, for example in
this case: I have put it on something that is bigger than just the
rectangle. I would like the option to optionally enable the label, then you
can choose to position it on the black or white. You can choose the size,
although by default it will autocompute it. Then for the label, please have
options to auto-configure it, like put id, put name, etc. Key thing is I
want that label to update! Particularly when we do a batch ArUco id set."

### The rules (docs/proposal_aruco_marks.md, 430,260 simulated photos)

    on the black   white text knocked out of a marker's black border row is
                   free while its cap height is <= 0.5 module, it keeps
                   0.25 module of black to the outer edge and it stays out
                   of the corner cells (`black_id_white` -0.3 pts, n.s.)
    the quiet ring ANY ink in the 1-module white ring costs: -11 pts for a
                   grey id, up to -40 once a printer's dots spread
    on the white   ink 0.7 module or more beyond the black is free
                   (`white_q15_id` +0.8); this module keeps a whole module
                   beyond the quiet zone (WHITE_GAP)
    ChArUco        the top-left BLACK square holds a board label for free
                   (`chl_label_more`); bright marks closer than 0.3 square to
                   an inner chessboard corner pull it (the chamfer study)

### ONE label: content, then position (`LabelSettings`)

Zach, 2026-10-09, later: "I want the option to put the name inside the
black strip. Conceptually it's one label object and then the position is a
separate thing." The label is WHAT it says -- one template, "|" between its
lines -- and, apart from that, WHERE it goes (`positions`); any content goes
in any position the tag has. A place that holds one line (a border row)
joins the lines with a space.

### Content (`resolve_text`)

A template with tokens; presets are templates too:

    "id"       "#{id}"            "#12"; a board's own label "#9-12"
    "id_name"  "#{id}|{name}"     "#12" / "ACTUATOR:3" (one row: "#12 ACTUATOR:3")
    "name"     "{name}"
    "dict_id"  "{dict} #{id}"     "4X4_50 #12"
    "custom"   your text          {id} {ids} {name} {key} {dict} {size}; "|" breaks a line

{id} is the marker's own id in a per-marker label (each marker of a grid)
and the target's id block everywhere else; {ids} is always the block.
{name} is the part the geometry belongs to (`instance_name`): a tag's
geometry is shared by every copy of its component, so the template says the
component's name, and a copy rebuilt as its own tag part (an id override,
aruco_tags variants) says its occurrence path below the level that set its
id ("Actuator:3", or "Arm:2/Actuator:3" when the robot set it). Resolution is
a pure function of (spec, name, key): anything that rebuilds a copy rebuilds
its label from the copy's own data -- that is the "label must update" rule
(`relabel`). Text is drawn upper-case; characters the font lacks print "?".

### Position (`plan_label`)

    black   the marker's border rows: "bottom", "top", or "both" (line 1 in
            the bottom row, the rest in the top row: "#12" + "ACTUATOR",
            as the PDF sheet prints them) -- of each marker of a board
            ("each") or of the first one ("first"); a line that reads the
            same on every marker (no {id}: a name) is written once, on the
            first. ChArUco also: the board label in the top-left black
            square ("square"). Row "auto": both rows; a ChArUco board's
            square. Cap <= 0.5 module, 0.25 module of black above and below,
            0.25 module clear of the corner cells, one line per row.
    white   one label for the whole target, beyond the quiet zone + 1 module
            (WHITE_GAP) on the side asked for, or the side with the most room
            ("auto"; ties go below, above, right, left). Left reads upward,
            right downward: each side reads upright when it is on top.
            Its white comes from one of two places:
              region  the white around the tag (the user's white body under a
                      Black only tag, or a tile bigger than the sticker), as
                      loops in the pattern frame like fit_on_loops' -- the
                      label is FITTED into it: never over a hole, never off
                      the face, WHITE_EDGE from every edge. It slides along
                      its side to the free spot nearest the tag's axis (an
                      exact scan of the band it needs), and moves out at
                      most REACH modules further;
              grow    the tile (or White only's white pattern) grows by a
                      label strip on that side (inside `bounds` when given:
                      the face it sits on).
            Neither collides with a corner mark (aruco_chamfer: the cut
            tile corner, the triangle, the cut ChArUco square) nor with a
            locating pin, KEEPOUT_MM clear. A strip's cut corner and pins
            sit at the GROWN tile's corners, level with the text: the strip
            grows deeper (up to REACH modules) to clear them before the
            text shrinks.
    auto    the whole text on the black; else the whole text on the white;
            else cut short, on the white (more room), else on the black.

### Size, and a text too long for its place

Auto: the largest cap that fits, rounded down to 0.1 mm, at most 0.5 module
on the black, 1.5 modules on the white (0.22 square in a ChArUco square).
On the white one line is kept while it reaches 0.5 module; a longer text
wraps (at spaces and after "/", up to 3 lines) where that is bigger. The
ids are sized at the dictionary's widest ("#9" is fitted as "#09" for
DICT_4X4_50) and a component's name with an occurrence number ("Actuator"
as "Actuator:0"), so every copy of a batch prints at the template's size.
A typed cap is clamped to the rule (on the white: 4 modules) with a note,
and shrunk with a note if it does not fit. A text too long for its place
shrinks, down to the smallest printable cap (5 nozzles: three bars and two
gaps -- never thinner than the nozzle); then a name path drops to its last
element; then it is CUT SHORT with "..." (`name_cuts`: keeping its
occurrence number, "Shoulder_Pi...:2", which tells a batch's copies apart),
with a note. An id is never cut: a text whose id does not fit is refused.

### Geometry

A single-stroke block font (GLYPHS) on a 4-unit cap grid, every stroke a box
of width `stroke` >= the nozzle (auto: max(nozzle, 0.13 cap)): axis-aligned
strokes with square ends, slanted strokes butt-ended with a stroke-square on
each free end or joint, so the ink's extent is exactly the cap height and
every piece is a box the add-in builds with TemporaryBRepManager.createBox
(Box: centre, length along `angle`, width across it). Strokes that do not
meet are at least two grid units apart, so counters and the gaps between
glyphs are >= one stroke too; `min_feature` reports the smallest of all of
them (and the black left around knocked-out text).

### Colour (`role`, relative to the black footprint)

    black placement  "cut": the text comes OUT of the black cells; the white
                     under (tile or body) shows through. White only: the
                     text is added to the white pattern (same rule: white =
                     box - footprint).
    white placement  "add": the text is part of the black cells (inlaid into
                     the tile / the body, like the cells). White only: a
                     notch in the white pattern, the black body shows.

Coordinates: mm in the PATTERN frame -- origin the pattern's centre, u the
pattern's x (image right), v image up -- after the Origin turn, the frame
aruco_chamfer uses (markers.cell_boxes' frame, in mm).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import aruco_board
from .aruco_autosize import _clean, _inside, _rect_inside
from .fiducials import ARUCO_DICTS
from .markers import TagSpec, board_spec, split_path

Pt = Tuple[float, float]
Rect = Tuple[float, float, float, float]          # (u0, v0, u1, v1), mm

# ------------------------------------------------------------------ options

# the label's CONTENT: a template, "|" between its lines
PRESETS = {"id": "#{id}", "id_name": "#{id}|{name}", "name": "{name}",
           "dict_id": "{dict} #{id}"}
CONTENTS = tuple(PRESETS) + ("custom",)
# its POSITION, apart from what it says
PLACES = ("auto", "black", "white")
ROWS = ("auto", "bottom", "top", "both", "square")   # on the black
SIDES = ("auto", "below", "above", "left", "right")  # on the white
SCOPES = ("auto", "each", "first")                    # a board's markers, on the black
TOKENS = ("id", "ids", "name", "key", "dict", "size")
LINE_BREAK = "|"
ELLIPSIS = "…"
SIDE_ORDER = ("below", "above", "right", "left")      # "auto" ties go to the first
SIDE_ROT = {"below": 0, "above": 0, "left": 90, "right": -90}

# --------------------------------------------------------------- the rules
# module units unless the name says mm

BLACK_CAP = 0.5            # cap in a border row (0.7 costs on crisp prints)
BLACK_EDGE = 0.25          # black kept above and below the text in its row
CORNER_CLEAR = 0.25        # black kept between the text and each corner cell
WHITE_GAP = 1.0            # clean white beyond the quiet zone before the label
WHITE_CAP = 1.5            # the auto cap's ceiling on the white
ONE_LINE = 0.5             # on the white one line is kept while its cap reaches this
                           # (the border row's size); a longer text wraps (<= MAX_LINES)
WHITE_LIMIT = 4.0          # a typed cap's ceiling on the white
WHITE_EDGE = 0.25          # the label to the edge of its white (and >= EDGE_MIN_MM)
EDGE_MIN_MM = 1.0
KEEPOUT_MM = 1.0           # the label to a corner mark or a pin, at least ...
KEEPOUT = 0.25             # ... and this many modules
SQUARE_CAP = 0.22          # ChArUco square label: cap <= 0.22 square (print_sheet's)
SQUARE_EDGE = 0.1          # ... >= 0.1 square (and 1 module) inside the square
SQUARE_CORNER = 0.35       # ... >= 0.35 square from every ChArUco corner
SQUARE_CORNER_HARD = 0.3   # the safety check's line (the chamfer study's)
MAX_LINES = 3

NOZZLE_MM = 0.45
STROKE_RATIO = 0.13        # auto stroke: max(nozzle, 0.13 cap)
CAP_STEP_MM = 0.1          # auto caps round down to this
LINE_GAP = 0.5             # between lines, x cap (and >= the stroke)
REACH = 3.0                # on the white a label moves out (a strip grows deeper) at
                           # most this much further to clear a hole, a mark or a pin
EPS = 1e-9
TOL = 1e-6                 # mm: geometric checks


# --------------------------------------------------------------------- font
#
# (width in grid units, polylines). Grid: x 0..width to the right, y 0..4 up
# (0 = baseline, 4 = cap). One unit = (cap - stroke) / 4, the same along x,
# so a 2-unit glyph is 3 strokes wide at the smallest cap. Strokes that do
# not touch stay >= 2 units apart (tests/test_aruco_label.py checks it): a
# counter or a notch is then >= one stroke at the smallest printable cap.

GLYPHS: Dict[str, Tuple[float, Tuple[Tuple[Pt, ...], ...]]] = {
    " ": (0, ()),
    "0": (2, (((1, 0), (2, 1), (2, 3), (1, 4), (0, 3), (0, 1), (1, 0)),)),
    "1": (2, (((0, 3), (1, 4), (1, 0)), ((0, 0), (2, 0)))),
    "2": (2, (((0, 4), (2, 4), (2, 2), (0, 2), (0, 0), (2, 0)),)),
    "3": (2, (((0, 4), (2, 4), (2, 0), (0, 0)), ((0, 2), (2, 2)))),
    "4": (2, (((0, 4), (0, 2), (2, 2)), ((2, 4), (2, 0)))),
    "5": (2, (((2, 4), (0, 4), (0, 2), (2, 2), (2, 0), (0, 0)),)),
    "6": (2, (((2, 4), (0, 4), (0, 0), (2, 0), (2, 2), (0, 2)),)),
    "7": (2, (((0, 4), (2, 4), (1, 0)),)),
    "8": (2, (((0, 0), (0, 4), (2, 4), (2, 0), (0, 0)), ((0, 2), (2, 2)))),
    "9": (2, (((0, 0), (2, 0), (2, 4), (0, 4), (0, 2), (2, 2)),)),
    "A": (2, (((0, 0), (0, 4), (2, 4), (2, 0)), ((0, 2), (2, 2)))),
    "B": (3, (((0, 0), (0, 4), (2, 4), (3, 3), (2, 2), (0, 2)),
              ((2, 2), (3, 1), (2, 0), (0, 0)))),
    "C": (2, (((2, 4), (0, 4), (0, 0), (2, 0)),)),
    "D": (2, (((0, 0), (0, 4), (1, 4), (2, 3), (2, 1), (1, 0), (0, 0)),)),
    "E": (2, (((2, 4), (0, 4), (0, 0), (2, 0)), ((0, 2), (1.5, 2)))),
    "F": (2, (((2, 4), (0, 4), (0, 0)), ((0, 2), (1.5, 2)))),
    "G": (2, (((2, 4), (0, 4), (0, 0), (2, 0), (2, 2)),)),
    "H": (2, (((0, 0), (0, 4)), ((2, 0), (2, 4)), ((0, 2), (2, 2)))),
    "I": (2, (((0, 4), (2, 4)), ((1, 4), (1, 0)), ((0, 0), (2, 0)))),
    "J": (2, (((2, 4), (2, 0), (0, 0), (0, 1)),)),
    "K": (2, (((0, 0), (0, 4)), ((2, 4), (0, 2), (2, 0)))),
    "L": (2, (((0, 4), (0, 0), (2, 0)),)),
    "M": (4, (((0, 0), (0, 4), (2, 2), (4, 4), (4, 0)),)),
    "N": (2, (((0, 0), (0, 4), (2, 0), (2, 4)),)),
    "O": (2, (((0, 0), (0, 4), (2, 4), (2, 0), (0, 0)),)),
    "P": (2, (((0, 0), (0, 4), (2, 4), (2, 2), (0, 2)),)),
    "Q": (3, (((3, 0), (0, 0), (0, 4), (2, 4), (2, 0)),)),
    "R": (3, (((0, 0), (0, 4), (2, 4), (2, 2), (0, 2)), ((2, 2), (3, 0)))),
    "S": (2, (((2, 4), (0, 4), (0, 3), (2, 1), (2, 0), (0, 0)),)),
    "T": (2, (((0, 4), (2, 4)), ((1, 4), (1, 0)))),
    "U": (2, (((0, 4), (0, 0), (2, 0), (2, 4)),)),
    "V": (2, (((0, 4), (1, 0), (2, 4)),)),
    "W": (4, (((0, 4), (0, 0), (2, 2), (4, 0), (4, 4)),)),
    "X": (2, (((0, 0), (2, 4)), ((0, 4), (2, 0)))),
    "Y": (2, (((0, 4), (1, 2), (2, 4)), ((1, 2), (1, 0)))),
    "Z": (2, (((0, 4), (2, 4), (0, 0), (2, 0)),)),
    "#": (4, (((1, 0), (1, 4)), ((3, 0), (3, 4)), ((0, 1), (4, 1)), ((0, 3), (4, 3)))),
    "-": (2, (((0, 2), (2, 2)),)),
    "_": (2, (((0, 0), (2, 0)),)),
    ":": (0, (((0, 1),), ((0, 3),))),
    ".": (0, (((0, 0),),)),
    "/": (2, (((0, 0), (2, 4)),)),
    "+": (2, (((0, 2), (2, 2)), ((1, 1), (1, 3)))),
    "=": (2, (((0, 1), (2, 1)), ((0, 3), (2, 3)))),
    "(": (1, (((1, 4), (0, 3), (0, 1), (1, 0)),)),
    ")": (1, (((0, 4), (1, 3), (1, 1), (0, 0)),)),
    "?": (2, (((0, 4), (2, 4), (2, 2), (1, 2)), ((1, 0),))),
    "…": (4, (((0, 0),), ((2, 0),), ((4, 0),))),       # a text cut short
}
UNKNOWN = "?"


def normalize(text: str) -> Tuple[str, List[str]]:
    """The text as the font draws it: upper-case, one space between words,
    a character it lacks -> "?" (and the list of them)."""
    out, missing = [], []
    for ch in " ".join(str(text or "").upper().split()):
        if ch in GLYPHS:
            out.append(ch)
        else:
            out.append(UNKNOWN)
            if ch not in missing:
                missing.append(ch)
    return "".join(out), missing


@dataclass(frozen=True)
class Metrics:
    """The font at one cap height (mm)."""
    cap: float
    stroke: float

    @property
    def unit(self) -> float:            # one grid unit
        return (self.cap - self.stroke) / 4.0

    @property
    def space(self) -> float:           # between two glyphs' ink
        return max(self.stroke, self.unit)

    @property
    def gap(self) -> float:             # the narrowest counter / notch: 2 units - stroke
        return 2.0 * self.unit - self.stroke

    @property
    def line_gap(self) -> float:
        return max(self.stroke, LINE_GAP * self.cap)

    @property
    def min_feature(self) -> float:
        return min(self.stroke, self.gap, self.space)


def stroke_for(cap: float, nozzle: float = NOZZLE_MM, stroke: float = 0.0) -> float:
    """The stroke at a cap: typed (never under the nozzle), else max(nozzle,
    0.13 cap)."""
    if stroke and stroke > 0:
        return max(float(stroke), float(nozzle))
    return max(float(nozzle), STROKE_RATIO * float(cap))


def metrics(cap: float, nozzle: float = NOZZLE_MM, stroke: float = 0.0) -> Metrics:
    return Metrics(float(cap), stroke_for(cap, nozzle, stroke))


def min_cap(nozzle: float = NOZZLE_MM, stroke: float = 0.0) -> float:
    """The smallest cap whose strokes AND gaps are >= the nozzle: three bars
    and two gaps, (cap - 3 stroke) / 2 >= nozzle."""
    w = stroke_for(0.0, nozzle, stroke)
    return 3.0 * w + 2.0 * float(nozzle)


def glyph_width(ch: str, M: Metrics) -> float:
    """A glyph's ink width (a space: its advance)."""
    return GLYPHS.get(ch, GLYPHS[UNKNOWN])[0] * M.unit + M.stroke


def line_width(text: str, M: Metrics) -> float:
    if not text:
        return 0.0
    return sum(glyph_width(c, M) for c in text) + (len(text) - 1) * M.space


def block_size(lines: Sequence[str], M: Metrics) -> Tuple[float, float]:
    """(width, height) of lines stacked, centred."""
    if not lines:
        return 0.0, 0.0
    return (max(line_width(t, M) for t in lines),
            len(lines) * M.cap + (len(lines) - 1) * M.line_gap)


# ---------------------------------------------------------------- boxes

@dataclass(frozen=True)
class Box:
    """One stroke: centre (cx, cy), `length` along the direction `angle`
    (degrees from +u, in (-90, 90]), `width` across it. mm, pattern frame.
    angle 0: an axis-aligned box, length along u, width along v.

    The add-in: TemporaryBRepManager.createBox(OrientedBoundingBox3D(centre,
    x cos a + y sin a, -x sin a + y cos a, length, width, height))."""
    cx: float
    cy: float
    length: float
    width: float
    angle: float = 0.0

    def corners(self) -> List[Pt]:
        """CCW."""
        a = math.radians(self.angle)
        c, s = math.cos(a), math.sin(a)
        hl, hw = self.length / 2.0, self.width / 2.0
        return [(self.cx + du * c - dv * s, self.cy + du * s + dv * c)
                for du, dv in ((-hl, -hw), (hl, -hw), (hl, hw), (-hl, hw))]

    def bounds(self) -> Rect:
        pts = self.corners()
        return (min(p[0] for p in pts), min(p[1] for p in pts),
                max(p[0] for p in pts), max(p[1] for p in pts))

    def to_list(self, nd: int = 4) -> List[float]:
        return [round(v, nd) + 0.0 for v in (self.cx, self.cy, self.length, self.width,
                                             self.angle)]


def _box_norm(cx, cy, length, width, angle) -> Box:
    """A box with its angle in (-90, 90]; a quarter turn becomes angle 0
    with its sides swapped."""
    a = math.fmod(float(angle), 180.0)
    if a <= -90.0 + 1e-9:
        a += 180.0
    elif a > 90.0 + 1e-9:
        a -= 180.0
    if abs(abs(a) - 90.0) < 1e-9:
        return Box(cx, cy, width, length, 0.0)
    if abs(a) < 1e-9:
        a = 0.0
    return Box(cx, cy, length, width, a)


def _on_segment(p: Pt, a: Pt, b: Pt) -> bool:
    return (min(a[0], b[0]) - EPS <= p[0] <= max(a[0], b[0]) + EPS
            and min(a[1], b[1]) - EPS <= p[1] <= max(a[1], b[1]) + EPS)


def glyph_boxes(ch: str, M: Metrics, x0: float = 0.0, y0: float = 0.0) -> List[Box]:
    """A glyph's strokes as boxes, its ink box's bottom-left at (x0, y0)."""
    _w, polys = GLYPHS.get(ch, GLYPHS[UNKNOWN])
    w, s = M.stroke, M.unit

    def P(p):
        return (x0 + w / 2.0 + p[0] * s, y0 + w / 2.0 + p[1] * s)
    segs, dots = [], []
    for pl in polys:
        if len(pl) == 1:
            dots.append(pl[0])
        segs += list(zip(pl, pl[1:]))
    axis = [(a, b) for a, b in segs if a[0] == b[0] or a[1] == b[1]]
    out: List[Box] = []
    squared = set()
    for a, b in segs:
        A, B = P(a), P(b)
        if a[1] == b[1]:
            out.append(Box((A[0] + B[0]) / 2.0, A[1], abs(B[0] - A[0]) + w, w))
        elif a[0] == b[0]:
            out.append(Box(A[0], (A[1] + B[1]) / 2.0, w, abs(B[1] - A[1]) + w))
        else:
            out.append(_box_norm((A[0] + B[0]) / 2.0, (A[1] + B[1]) / 2.0,
                                 math.hypot(B[0] - A[0], B[1] - A[1]), w,
                                 math.degrees(math.atan2(B[1] - A[1], B[0] - A[0]))))
            # a slanted stroke is butt-ended: a stroke-square on each end
            # that no straight stroke covers keeps the ink inside the cap
            for v in (a, b):
                if v in squared or any(_on_segment(v, p, q) for p, q in axis):
                    continue
                squared.add(v)
                c = P(v)
                out.append(Box(c[0], c[1], w, w))
    for d in dots:
        c = P(d)
        out.append(Box(c[0], c[1], w, w))
    return out


def line_boxes(text: str, M: Metrics, x0: float = 0.0, y0: float = 0.0) -> List[Box]:
    """A line's boxes, its baseline-left at (x0, y0)."""
    out, x = [], x0
    for ch in text:
        out += glyph_boxes(ch, M, x, y0)
        x += glyph_width(ch, M) + M.space
    return out


def block_boxes(lines: Sequence[str], M: Metrics, centre: Pt = (0.0, 0.0),
                rot: int = 0) -> List[Box]:
    """Lines stacked, each centred, the block centred on `centre`, turned by
    `rot` degrees (0, 90 = reads upward, -90 = reads downward)."""
    W, H = block_size(lines, M)
    boxes = []
    for k, t in enumerate(lines):
        base = H / 2.0 - (k + 1) * M.cap - k * M.line_gap
        boxes += line_boxes(t, M, -line_width(t, M) / 2.0, base)
    r = math.radians(rot)
    c, s = round(math.cos(r), 12), round(math.sin(r), 12)
    return [_box_norm(centre[0] + b.cx * c - b.cy * s, centre[1] + b.cx * s + b.cy * c,
                      b.length, b.width, b.angle + rot) for b in boxes]


def ink_rect(boxes: Iterable[Box]) -> Optional[Rect]:
    rs = [b.bounds() for b in boxes]
    if not rs:
        return None
    return (min(r[0] for r in rs), min(r[1] for r in rs),
            max(r[2] for r in rs), max(r[3] for r in rs))


# ------------------------------------------------------------------ content

def dict_short(dictionary: str) -> str:
    return str(dictionary).replace("DICT_", "")


def _fmt_mm(v: float) -> str:
    t = ("%.1f" % v).rstrip("0").rstrip(".")
    return t or "0"


def part_name(path: str, depth: int = 2) -> str:
    """The last `depth` elements of an occurrence path, "/"-joined:
    "Robot:1+Arm:2+Actuator:3" -> "Arm:2/Actuator:3"."""
    parts = split_path(path or "")
    return "/".join(parts[-depth:]) if parts else ""


def name_variants(name: str) -> List[str]:
    """{name} in order of preference: as given, then its last element only
    (a long path that does not fit falls back to the part itself)."""
    out = [str(name or "")]
    tail = out[0].split("/")[-1]
    if tail != out[0]:
        out.append(tail)
    return out


def instance_name(inst) -> str:
    """{name} for the geometry that shows a markers.TagInstance. Geometry is
    shared per component, so: a copy built as its own tag part (its id or
    dictionary overridden -> an aruco_tags variant at the level that set it)
    says its occurrence path below that level, the last two elements; every
    other copy shows its template, which says the component's name."""
    if inst.path and inst.needs_variant:
        parts = split_path(inst.path)
        lvl = inst.winning.level
        depth = len(split_path(lvl)) if lvl else 0
        return "/".join(parts[depth:][-2:])
    return inst.tag.component or ""


_TOKEN = re.compile(r"\{(\w+)\}")
_OCC = re.compile(r":\d+$")              # an occurrence number, "Actuator:3"
_ID_TOKEN = re.compile(r"\{ids?\}")


def name_cuts(name: str) -> List[str]:
    """A name cut short, longest first, for a place it does not fit even at
    the smallest printable cap: its last element, cut with "..." but keeping
    its occurrence number -- the part that tells a batch's copies apart:
    "Shoulder_Actuator:3" -> "Shoulder_Actuat...:3", ..., "S...:3"."""
    tail = str(name or "").split("/")[-1]
    mo = _OCC.search(tail)
    base, occ = (tail[:mo.start()], mo.group()) if mo else (tail, "")
    out = []
    for k in range(len(base) - 1, 0, -1):
        t = base[:k].rstrip() + ELLIPSIS + occ
        if t not in out:
            out.append(t)
    return out


def text_cuts(text: str) -> List[str]:
    """A text cut short from its end, longest first ("ROBOT ARM" -> "ROBOT
    AR...", ...). Only for a text with no id in it: an id is never cut."""
    t = " ".join(str(text or "").replace(LINE_BREAK, " ").split())
    out = []
    for k in range(len(t) - 1, 0, -1):
        c = t[:k].rstrip() + ELLIPSIS
        if c not in out:
            out.append(c)
    return out


def template_lines(template: str) -> List[str]:
    """The content's lines (templates), as written."""
    return str(template or "").split(LINE_BREAK)


def part_template(template: str, part: str) -> str:
    """The share of the content a place shows: "all" of it (a place that
    holds one line joins its lines with a space), its "first" line (the
    bottom row of "both rows") or the "rest" (the top row)."""
    lines = template_lines(template)
    if part == "first":
        return lines[0]
    if part == "rest":
        return LINE_BREAK.join(lines[1:])
    return str(template or "")


def resolve_text(template: str, spec: TagSpec, *, marker_id: Optional[int] = None,
                 name: str = "", key: str = "", sizing: bool = False) -> str:
    """A template's text for one tag: pure (spec, marker, name, key).

    marker_id  a per-marker label's marker ({id} = its id); None = the
               target's own label ({id} = the id block, "9-12" on a board)
    sizing     every id padded with zeros to the dictionary's widest
               ("#9" -> "#09" in DICT_4X4_50), and a component's name
               given an occurrence number ("Actuator" -> "Actuator:0"):
               the text a label is SIZED with, so the copies a batch of ids
               rebuilds ("#10 Actuator:2") print at the template's size
    Unknown tokens are kept as written ({x} prints "?X?")."""
    ids = spec.ids
    cap = ARUCO_DICTS.get(spec.dictionary, 0)
    digits = len(str(max(0, cap - 1))) if sizing else 0

    def num(i):
        return str(int(i)).zfill(digits)
    block = num(ids[0]) if len(ids) == 1 else f"{num(ids[0])}-{num(ids[-1])}"
    nm = str(name or "").replace(LINE_BREAK, "/")
    if sizing and nm and not _OCC.search(nm.split("/")[-1]):
        nm += ":0"
    vals = {"id": block if marker_id is None else num(marker_id), "ids": block,
            "name": nm, "key": str(key or "").replace(LINE_BREAK, "/"),
            "dict": dict_short(spec.dictionary), "size": _fmt_mm(spec.marker_length_m * 1000.0)}
    out = _TOKEN.sub(lambda mo: vals.get(mo.group(1), mo.group(0)), str(template or ""))
    return LINE_BREAK.join(" ".join(p.split()) for p in out.split(LINE_BREAK)).strip(
        LINE_BREAK + " ")


def unknown_tokens(template: str) -> List[str]:
    return [t for t in _TOKEN.findall(str(template or "")) if t not in TOKENS]


_CHUNK = re.compile(r"[^ /]+/?|/")


def _join(chunks: Sequence[str]) -> str:
    """Chunks back into a line: a space between words, none after a "/"."""
    out = ""
    for c in chunks:
        out += c if (not out or out.endswith("/")) else " " + c
    return out


def wraps(text: str, max_lines: int = 1) -> List[List[str]]:
    """Line arrangements of a drawn text, fewest lines first: as written
    ("|" breaks; one line where only one fits), then broken at spaces and
    after each "/" (a path: "ARM:2/" + "ACTUATOR:3") into 2 .. max_lines
    lines of balanced length."""
    parts = [p.strip() for p in text.split(LINE_BREAK) if p.strip()]
    if max_lines <= 1 or not parts:
        return [[" ".join(parts)]] if parts else []
    if len(parts) > max_lines:
        parts = parts[:max_lines - 1] + [" ".join(parts[max_lines - 1:])]
    out = [parts]
    if len(parts) == 1:
        words = _CHUNK.findall(parts[0])
        for k in range(2, min(max_lines, len(words)) + 1):
            best = None
            for cut in _cuts(len(words), k):
                lines = [_join(words[a:b]) for a, b in zip((0,) + cut, cut + (len(words),))]
                score = max(len(t) for t in lines)
                if best is None or score < best[0]:
                    best = (score, lines)
            out.append(best[1])
    return out


def _cuts(n: int, k: int):
    """Every way to cut n words into k non-empty runs (k - 1 cut points)."""
    def rec(start, left):
        if left == 0:
            yield ()
            return
        for c in range(start + 1, n - left + 1):
            for rest in rec(c, left - 1):
                yield (c,) + rest
    return rec(0, k - 1)


# ----------------------------------------------------------------- settings

@dataclass
class LabelSettings:
    """ONE label (stored in the node's spec as "label"): what it says, and,
    apart from that, where it goes and how big. Any content goes in any
    position the tag has (`positions`); a place that holds one line joins
    the content's lines with a space.

    content  what:   content (a preset) / text (custom), "|" between lines
    position where:  place (auto / black / white), then on the black the
                     row (bottom / top / both rows / the ChArUco square) and,
                     on a board, scope (each marker / the first one); on the
                     white the side
    size             cap_mm (0 = auto), nozzle_mm, stroke_mm (0 = auto)"""
    on: bool = False
    content: str = "id"            # a PRESETS key or "custom"
    text: str = ""                 # the custom template
    place: str = "auto"            # auto / black / white
    row: str = "auto"              # black: auto (both rows; ChArUco: the square) / bottom /
                                   # top / both (line 1 bottom, the rest top) / square
    side: str = "auto"             # white: auto / below / above / left / right
    scope: str = "auto"            # a board on the black: auto (each) / each / first
    cap_mm: float = 0.0            # 0 = auto
    nozzle_mm: float = NOZZLE_MM
    stroke_mm: float = 0.0         # 0 = auto: max(nozzle, 0.13 cap)

    def template(self) -> str:
        if self.content == "custom":
            return self.text or ""
        return PRESETS.get(self.content, PRESETS["id"])

    def black_row(self, layout: str) -> str:
        """The row on the black, "auto" resolved: a ChArUco board's label
        goes in its black square, every other tag's in both rows (line 1
        at the bottom: "#12", line 2 at the top: "ACTUATOR")."""
        if self.row == "auto":
            return "square" if layout == "charuco" else "both"
        if self.row == "square" and layout != "charuco":
            return "both"
        return self.row

    def to_dict(self) -> dict:
        return {"on": bool(self.on), "content": self.content, "text": self.text,
                "place": self.place, "row": self.row, "side": self.side,
                "scope": self.scope, "cap_mm": float(self.cap_mm),
                "nozzle_mm": float(self.nozzle_mm), "stroke_mm": float(self.stroke_mm)}

    @classmethod
    def from_dict(cls, d: Optional[dict], notes: Optional[List[str]] = None) -> "LabelSettings":
        """Tolerant: an unknown value falls back to the default, with a note."""
        s = cls()
        if not isinstance(d, dict):
            return s
        say = notes.append if notes is not None else (lambda _m: None)
        s.on = bool(d.get("on", False))
        d = dict(d)
        if d.get("scope") == "square":            # phase 1 kept the square under scope
            d["scope"], d["row"] = "auto", "square"
        for key, allowed in (("content", CONTENTS), ("place", PLACES), ("row", ROWS),
                             ("side", SIDES), ("scope", SCOPES)):
            v = d.get(key, getattr(s, key))
            if v in allowed:
                setattr(s, key, v)
            else:
                say(f"label: {key} {v!r} is not one of {', '.join(allowed)} -- "
                    f"{getattr(s, key)} used")
        s.text = str(d.get("text") or "")
        for key in ("cap_mm", "nozzle_mm", "stroke_mm"):
            try:
                v = float(d.get(key, getattr(s, key)))
            except (TypeError, ValueError):
                v = getattr(s, key)
            if not math.isfinite(v) or v < 0:
                v = getattr(s, key)
            setattr(s, key, v)
        if s.nozzle_mm <= 0:
            s.nozzle_mm = NOZZLE_MM
        return s


def positions(layout: str) -> List[Tuple[str, str]]:
    """Every position a tag of this layout has, (place, row or side): what
    the dialog's Position + Row lists offer. Any content goes in any of them
    (a long text shrinks, then is cut short with a note)."""
    rows = ["bottom", "top", "both"] + (["square"] if layout == "charuco" else [])
    return [("black", r) for r in rows] + [("white", sd) for sd in SIDES]


# ------------------------------------------------------------------- result

@dataclass
class Slot:
    """Where one block of label text may go, every rule already checked:
    text laid out inside `rect` (centred, turned by `rot`) is safe. A copy
    (`relabel`) lays its own text out in the same slots."""
    where: str                     # "bottom" / "top" row, "square", or the white side
    rect: Rect
    rot: int = 0                   # 0, 90 (reads upward), -90 (reads downward)
    cap: float = 0.0               # the cap the template chose (copies keep it if they fit)
    lines: int = 1                 # the most lines it takes
    marker: Optional[int] = None   # a per-marker slot: index into spec.ids
    bound: Optional[Rect] = None   # the black it sits in (black rows / square)
    part: str = "all"              # the content's share: all / first line / the rest
    square: Optional[int] = None   # the marker whose border it is in (black rows)

    def to_dict(self, nd: int = 4) -> dict:
        d = {"where": self.where, "rect": [round(v, nd) + 0.0 for v in self.rect],
             "rot": int(self.rot), "cap_mm": round(self.cap, nd), "lines": int(self.lines),
             "marker": self.marker, "part": self.part}
        if self.bound is not None:
            d["bound"] = [round(v, nd) + 0.0 for v in self.bound]
        if self.square is not None:
            d["square"] = int(self.square)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Slot":
        b = d.get("bound")
        return cls(where=d["where"], rect=tuple(map(float, d["rect"])), rot=int(d.get("rot", 0)),
                   cap=float(d.get("cap_mm", 0.0)), lines=int(d.get("lines", 1)),
                   marker=d.get("marker"), bound=tuple(map(float, b)) if b else None,
                   part=d.get("part", "all"), square=d.get("square"))


@dataclass
class Part:
    """One slot's text, laid out."""
    slot: int                      # index into Label.slots
    text: str                      # resolved
    lines: List[str]               # as drawn (normalized, wrapped)
    cap: float
    stroke: float
    min_feature: float
    rect: Rect                     # the ink's box
    boxes: List[Box]
    short: str = ""                # "" | "tail" (a path's last element) | "cut" ("...")
    full: str = ""                 # the text before it was shortened

    def to_dict(self, nd: int = 4) -> dict:
        d = {"slot": self.slot, "text": self.text, "lines": list(self.lines),
             "cap_mm": round(self.cap, nd), "stroke_mm": round(self.stroke, nd),
             "min_feature_mm": round(self.min_feature, nd),
             "rect": [round(v, nd) + 0.0 for v in self.rect],
             "boxes": [b.to_list(nd) for b in self.boxes]}
        if self.short:
            d["short"], d["full"] = self.short, self.full
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Part":
        return cls(slot=int(d["slot"]), text=d.get("text", ""), lines=list(d.get("lines") or []),
                   cap=float(d["cap_mm"]), stroke=float(d["stroke_mm"]),
                   min_feature=float(d.get("min_feature_mm", 0.0)),
                   rect=tuple(map(float, d["rect"])),
                   boxes=[Box(*map(float, b)) for b in d.get("boxes") or []],
                   short=d.get("short", ""), full=d.get("full", ""))


@dataclass
class Label:
    """One tag's label (mm, pattern frame).

    kind   "none" | "black" | "white"
    role   "cut": the text comes out of the black footprint (black placement)
           "add": the text joins the black footprint (white placement)
           (White only builds its white as box - footprint: the same rule)
    white  the white grown by a label strip -- the tile (White + black) or the
           white pattern's box (White only) -- or None (unchanged)
    strip  what that added, or None
    """
    kind: str = "none"
    role: str = ""
    side: str = ""
    settings: LabelSettings = field(default_factory=LabelSettings)
    slots: List[Slot] = field(default_factory=list)
    parts: List[Part] = field(default_factory=list)
    white: Optional[Rect] = None
    strip: Optional[Rect] = None
    notes: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.kind != "none" and bool(self.slots) and len(self.parts) == len(self.slots)

    @property
    def cap(self) -> float:
        return min((p.cap for p in self.parts), default=0.0)

    @property
    def stroke(self) -> float:
        return min((p.stroke for p in self.parts), default=0.0)

    @property
    def min_feature(self) -> float:
        return min((p.min_feature for p in self.parts), default=0.0)

    def boxes(self) -> List[Box]:
        return [b for p in self.parts for b in p.boxes]

    def polygons(self) -> List[List[Pt]]:
        return [b.corners() for b in self.boxes()]

    def texts(self) -> List[str]:
        """What each slot prints (a copy compares these with its variant's)."""
        by = {p.slot: LINE_BREAK.join(p.lines) for p in self.parts}
        return [by.get(i, "") for i in range(len(self.slots))]

    def footprint_rects(self) -> List[Rect]:
        """Every slot's rect: what a variant refills before it cuts its own
        text (aruco_tags._fill_box only covers the pattern's extent)."""
        return [s.rect for s in self.slots]

    def to_dict(self, nd: int = 4) -> dict:
        if self.kind == "none":
            return {"kind": "none", "settings": self.settings.to_dict(),
                    "notes": list(self.notes)}
        r = (lambda x: [round(v, nd) + 0.0 for v in x] if x is not None else None)
        return {"kind": self.kind, "role": self.role, "side": self.side,
                "settings": self.settings.to_dict(),
                "slots": [s.to_dict(nd) for s in self.slots],
                "parts": [p.to_dict(nd) for p in self.parts],
                "white": r(self.white), "strip": r(self.strip),
                "cap_mm": round(self.cap, nd), "stroke_mm": round(self.stroke, nd),
                "min_feature_mm": round(self.min_feature, nd), "notes": list(self.notes)}


def from_dict(d: Optional[dict]) -> Label:
    """A record's label back (kind "none" for none / absent)."""
    if not isinstance(d, dict):
        return Label()
    s = LabelSettings.from_dict(d.get("settings"))
    if d.get("kind") in (None, "none"):
        return Label(settings=s, notes=list(d.get("notes") or []))
    t = (lambda x: tuple(map(float, x)) if x else None)
    return Label(kind=d["kind"], role=d.get("role", ""), side=d.get("side", ""), settings=s,
                 slots=[Slot.from_dict(x) for x in d.get("slots") or []],
                 parts=[Part.from_dict(x) for x in d.get("parts") or []],
                 white=t(d.get("white")), strip=t(d.get("strip")),
                 notes=list(d.get("notes") or []))


def same(a: Optional[dict], b: Optional[dict], tol: float = 1e-3) -> bool:
    """Two records' labels (dicts) make the same geometry (to `tol` mm)."""
    la, lb = from_dict(a), from_dict(b)
    if la.kind != lb.kind or la.role != lb.role:
        return False
    if la.kind == "none":
        return True
    if (la.white is None) != (lb.white is None):
        return False
    if la.white is not None and any(abs(x - y) > tol for x, y in zip(la.white, lb.white)):
        return False
    ba, bb = la.boxes(), lb.boxes()
    if len(ba) != len(bb):
        return False
    return all(abs(x - y) <= tol for p, q in zip(ba, bb)
               for x, y in zip(p.to_list(9), q.to_list(9)))


# ------------------------------------------------------------- 2D helpers

def rect_poly(r: Rect) -> List[Pt]:
    return [(r[0], r[1]), (r[2], r[1]), (r[2], r[3]), (r[0], r[3])]


def grow_rect(r: Rect, d: float) -> Rect:
    return (r[0] - d, r[1] - d, r[2] + d, r[3] + d)


def _overlap(P: Sequence[Pt], Q: Sequence[Pt]) -> bool:
    """Convex polygons share area (separating axes; touching = no)."""
    for poly in (P, Q):
        for i in range(len(poly)):
            a, b = poly[i - 1], poly[i]
            n = (b[1] - a[1], a[0] - b[0])
            pa = [n[0] * p[0] + n[1] * p[1] for p in P]
            pb = [n[0] * p[0] + n[1] * p[1] for p in Q]
            if max(pa) <= min(pb) + 1e-12 or max(pb) <= min(pa) + 1e-12:
                return False
    return True


def _seg_pt(p: Pt, a: Pt, b: Pt) -> float:
    ax, ay = b[0] - a[0], b[1] - a[1]
    L = ax * ax + ay * ay
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * ax + (p[1] - a[1]) * ay) / L))
    return math.hypot(p[0] - a[0] - t * ax, p[1] - a[1] - t * ay)


def poly_dist(P: Sequence[Pt], Q: Sequence[Pt]) -> float:
    """Distance between two convex polygons (0 when they overlap)."""
    if _overlap(P, Q):
        return 0.0
    d = math.inf
    for A, B in ((P, Q), (Q, P)):
        for p in A:
            for i in range(len(B)):
                d = min(d, _seg_pt(p, B[i - 1], B[i]))
    return d


def pt_poly_dist(p: Pt, poly: Sequence[Pt]) -> float:
    """A point to a convex polygon (0 inside)."""
    sgn = [(b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
           for a, b in zip(poly, list(poly[1:]) + [poly[0]])]
    if all(v >= -1e-12 for v in sgn) or all(v <= 1e-12 for v in sgn):
        return 0.0
    return min(_seg_pt(p, poly[i - 1], poly[i]) for i in range(len(poly)))


def rect_point_dist(r: Rect, p: Pt) -> float:
    return math.hypot(max(r[0] - p[0], 0.0, p[0] - r[2]), max(r[1] - p[1], 0.0, p[1] - r[3]))


def rect_loop(r: Rect) -> List[Pt]:
    """A rectangle as a loop (CCW): a tile bigger than the sticker as the
    region a label is fitted into."""
    return rect_poly(r)


def loops_to_pattern(loops_m: Sequence[Sequence[Pt]], cx: float = 0.0, cy: float = 0.0,
                     angle_deg: float = 0.0, turn_deg: float = 0.0) -> List[List[Pt]]:
    """A face's loops (METRES, in the face frame aruco_autosize.fit_on_loops
    was given) -> mm in the PATTERN frame: the fit put the pattern's centre
    at (cx, cy), its x at angle_deg; the Origin turn then turned it by
    turn_deg more (evaluate's `pl` and `turn`)."""
    a = math.radians(float(angle_deg) + float(turn_deg))
    c, s = math.cos(a), math.sin(a)
    out = []
    for loop in loops_m or ():
        pts = []
        for p in loop:
            dx, dy = float(p[0]) - cx, float(p[1]) - cy
            pts.append(((dx * c + dy * s) * 1000.0, (-dx * s + dy * c) * 1000.0))
        out.append(pts)
    return out


# --------------------------------------------------------- the tag in mm

def _mm(spec: TagSpec):
    """(module, extent rect, quiet modules) in mm."""
    m = spec.module_m * 1000.0
    w, h = (v * 1000.0 for v in spec.extent_m)
    return m, (-w / 2.0, -h / 2.0, w / 2.0, h / 2.0), max(0.0, float(spec.quiet_modules))


def sticker_rect(spec: TagSpec) -> Rect:
    """The sticker: the pattern + its quiet zone, centred."""
    w, h = (v * 1000.0 for v in spec.tile_size_m)
    return (-w / 2.0, -h / 2.0, w / 2.0, h / 2.0)


def quiet_ring(spec: TagSpec) -> float:
    """The clean white (modules) every edge of the black keeps: the quiet
    zone, never under 1 module on a single marker or a grid (the study's
    ring); a ChArUco board's markers keep theirs inside their own squares."""
    q = max(0.0, float(spec.quiet_modules))
    return q if spec.layout == "charuco" else max(q, 1.0)


def keepout_rect(spec: TagSpec, gap: float = WHITE_GAP) -> Rect:
    """Where white-placement ink never goes: the black grown by the quiet
    ring and `gap` modules."""
    m, ext, _q = _mm(spec)
    return grow_rect(ext, (quiet_ring(spec) + gap) * m)


def marker_squares(spec: TagSpec) -> List[Tuple[int, Rect]]:
    """(id, black square in mm) of every marker, OpenCV's order."""
    corners = aruco_board.marker_corners_m(board_spec(spec))
    out = []
    for mid in sorted(corners):
        xs = [c[0] * 1000.0 for c in corners[mid]]
        ys = [c[1] * 1000.0 for c in corners[mid]]
        out.append((mid, (min(xs), min(ys), max(xs), max(ys))))
    return out


def charuco_square(spec: TagSpec) -> Rect:
    """The black square a ChArUco board label goes in: the top-left one, or
    the next in the top row when the top-left is white (legacy, even rows)."""
    S = spec.square_length_m * 1000.0
    _m, ext, _q = _mm(spec)
    col = 0 if aruco_board._charuco_black(0, 0, spec.rows, bool(spec.legacy)) else 1
    u0 = ext[0] + col * S
    return (u0, ext[3] - S, u0 + S, ext[3])


def charuco_corners_mm(spec: TagSpec) -> List[Pt]:
    """Inner chessboard corners (the ones the detector refines), mm, centre frame."""
    _m, ext, _q = _mm(spec)
    W, H = ext[2] - ext[0], ext[3] - ext[1]
    return [(x * 1000.0 - W / 2.0, H / 2.0 - y * 1000.0)
            for x, y, _z in aruco_board.charuco_corners(board_spec(spec))]


# ----------------------------------------------------------- keep-outs

def mark_keepouts(mark) -> List[List[Pt]]:
    """The corner mark (aruco_chamfer.Mark or its record dict) as polygons
    a label keeps clear of: the cut corner, or the triangle."""
    if not mark:
        return []
    if isinstance(mark, dict):
        kind, cut, solid = mark.get("kind"), mark.get("cut") or [], mark.get("solid") or []
    else:
        kind, cut, solid = mark.kind, mark.cut, mark.solid
    pts = cut if kind in ("tile_cut", "black_cut") else solid if kind == "triangle" else []
    return [[tuple(map(float, p)) for p in pts]] if len(pts) >= 3 else []


def pin_keepouts(outlines: Sequence[dict], centre: Pt = (0.0, 0.0), segments: int = 24
                 ) -> List[List[Pt]]:
    """aruco_slabs.pin_outlines (tile-centre frame) as polygons in the
    pattern frame, the tile centred at `centre`; a circle as a polygon drawn
    AROUND it (never inside)."""
    out = []
    for o in outlines or ():
        if o.get("kind") == "circle":
            (u, v), r = o["c"], o["r"] / math.cos(math.pi / segments)
            out.append([(centre[0] + u + r * math.cos(2 * math.pi * k / segments),
                         centre[1] + v + r * math.sin(2 * math.pi * k / segments))
                        for k in range(segments)])
        elif o.get("pts"):
            out.append([(centre[0] + p[0], centre[1] + p[1]) for p in o["pts"]])
    return out


def mark_on_tile(spec: TagSpec, mode: str, op: Optional[str], tile: Rect):
    """aruco_chamfer's corner mark for a white tile that is NOT centred on
    the pattern (a tile grown by a label strip): its cut stays at the
    tile's own top-left corner, its legs by the same rule."""
    from . import aruco_chamfer as C
    tw, th = -2.0 * tile[0], 2.0 * tile[3]           # a centred tile with that top-left corner
    mk = C.mark_for_spec(spec, mode, op, (tw, th))
    if mk.kind == "tile_cut":
        mk.outline = C.cut_rect(tile, mk.leg)
    return mk


def _pins_on(tile: Rect, pins: Optional[dict], cut: float, notes: List[str]) -> List[List[Pt]]:
    """The locating pins of a tile (pins: {"shape", "size", "margin"}), as
    keep-out polygons, or [] (with a note when they do not fit)."""
    if not pins or pins.get("shape") not in ("triangle", "round"):
        return []
    from . import aruco_slabs as SL
    tw, th = tile[2] - tile[0], tile[3] - tile[1]
    try:
        outs = SL.pin_outlines(pins["shape"], float(pins.get("size", SL.PIN_SIZE_MM)), tw, th,
                               float(pins.get("margin", SL.PIN_MARGIN_MM)), cut=cut)
    except ValueError as exc:
        notes.append(f"label: {exc}")
        return []
    return pin_keepouts(outs, ((tile[0] + tile[2]) / 2.0, (tile[1] + tile[3]) / 2.0))


def _clear_of(r: Rect, polys: Sequence[Sequence[Pt]], d: float) -> bool:
    P = rect_poly(r)
    return all(poly_dist(P, q) >= d - TOL for q in polys)


# --------------------------------------------------------------- layout

def _caps(hi: float, lo: float, typed: bool) -> List[float]:
    """Candidate caps, biggest first: `hi` when typed, then the 0.1 mm grid
    down to `lo`, and `lo` itself (the smallest printable: an auto cap never
    rounds away the only one that prints)."""
    if hi < lo - EPS:
        return []
    out = [hi] if typed else []
    k = math.floor(hi / CAP_STEP_MM + 1e-6)
    while k * CAP_STEP_MM >= lo - EPS:
        c = round(k * CAP_STEP_MM, 6)
        if not out or c < out[-1] - EPS:
            out.append(c)
        k -= 1
    if not out or out[-1] > lo + EPS:
        out.append(round(lo, 6))
    return out


def _largest(caps: List[float], ok) -> Optional[float]:
    """The largest cap in `caps` (descending) for which ok(cap) holds;
    ok must be monotone (a smaller block fits wherever a bigger one did)."""
    if not caps or not ok(caps[-1]):
        return None
    lo, hi = len(caps) - 1, 0                     # ok at lo; find the smallest index ok
    if ok(caps[0]):
        return caps[0]
    while lo - hi > 1:
        mid = (lo + hi) // 2
        if ok(caps[mid]):
            lo = mid
        else:
            hi = mid
    return caps[lo]


def _dims(lines, M, rot) -> Tuple[float, float]:
    """The block's (u, v) size once turned."""
    w, h = block_size(lines, M)
    return (w, h) if rot % 180 == 0 else (h, w)


def _fit_in(rect: Rect, rot: int, arrangements: List[List[str]], cap_hi: float,
            s: LabelSettings, typed: bool) -> Optional[Tuple[float, List[str]]]:
    """The largest cap (and the arrangement) whose block fits in `rect`."""
    rw, rh = rect[2] - rect[0], rect[3] - rect[1]
    caps = _caps(cap_hi, min_cap(s.nozzle_mm, s.stroke_mm), typed)
    best = None
    for lines in arrangements:
        def ok(c, lines=lines):
            du, dv = _dims(lines, metrics(c, s.nozzle_mm, s.stroke_mm), rot)
            return du <= rw + TOL and dv <= rh + TOL
        c = _largest(caps, ok)
        if c is not None and (best is None or c > best[0] + EPS):
            best = (c, lines)
    return best


def _part(slot_i: int, slot: Slot, cand: "Cand", lines: List[str], cap: float,
          s: LabelSettings) -> Part:
    M = metrics(cap, s.nozzle_mm, s.stroke_mm)
    centre = ((slot.rect[0] + slot.rect[2]) / 2.0, (slot.rect[1] + slot.rect[3]) / 2.0)
    boxes = block_boxes(lines, M, centre, slot.rot)
    rect = ink_rect(boxes) or (centre[0], centre[1], centre[0], centre[1])
    feats = [M.min_feature] + ([M.line_gap] if len(lines) > 1 else [])
    if slot.bound is not None:          # the black left around knocked-out text
        b = slot.bound
        feats.append(min(rect[0] - b[0], b[2] - rect[2], rect[1] - b[1], b[3] - rect[3]))
    return Part(slot=slot_i, text=cand.text, lines=list(lines), cap=cap, stroke=M.stroke,
                min_feature=min(feats), rect=rect, boxes=boxes, short=cand.short,
                full=cand.full if cand.short else "")


@dataclass(frozen=True)
class Cand:
    """One way to write a place's share of the label: the text, the text it
    is SIZED with (resolve_text sizing), and how it was shortened."""
    text: str
    sizing: str
    short: str = ""                # "" | "tail" | "cut"
    full: str = ""                 # the unshortened text


def _cands(s: LabelSettings, spec: TagSpec, slot: Slot, name: str, key: str,
           cut: bool = True) -> List[Cand]:
    """A place's text, best first: as written; a {name} path's last element
    ("Arm:2/Actuator:3" -> "Actuator:3"); then (cut=True) the name cut short
    with "..." keeping its occurrence number -- or, with no {name} and no
    id in it, the text cut from its end. An id is never cut."""
    tpl = part_template(s.template(), slot.part)
    mid = spec.ids[slot.marker] if slot.marker is not None else None
    out: List[Cand] = []
    seen = set()

    def add(n, short, text=None):
        t = text if text is not None else resolve_text(tpl, spec, marker_id=mid, name=n, key=key)
        z = text if text is not None else resolve_text(tpl, spec, marker_id=mid, name=n, key=key,
                                                       sizing=True)
        if t and t not in seen:
            seen.add(t)
            out.append(Cand(t, z, short, full))
    full = resolve_text(tpl, spec, marker_id=mid, name=name, key=key)
    if "{name}" in tpl:
        for i, n in enumerate(name_variants(name)):
            add(n, "" if i == 0 else "tail")
        if cut:
            for n in name_cuts(name):
                add(n, "cut")
    else:
        add(name, "")
        if cut and full and not _ID_TOKEN.search(tpl):
            for t in text_cuts(full):
                add(name, "cut", t)
    return out


def _first_fit(cands: List[Cand], fits):
    """(index, result) of the first candidate `fits` accepts: the whole and
    tail forms in order, then the LONGEST cut form (a bisection: a shorter
    cut fits wherever a longer one did). None when none does."""
    whole = [i for i, c in enumerate(cands) if c.short != "cut"]
    for i in whole:
        r = fits(cands[i])
        if r is not None:
            return i, r
    cut = [i for i, c in enumerate(cands) if c.short == "cut"]
    if not cut:
        return None
    r_last = fits(cands[cut[-1]])
    if r_last is None:
        return None
    lo, hi, best = 0, len(cut) - 1, (cut[-1], r_last)   # cut[hi] fits
    while lo < hi:
        mid = (lo + hi) // 2
        r = fits(cands[cut[mid]])
        if r is not None:
            hi, best = mid, (cut[mid], r)
        else:
            lo = mid + 1
    return best


def _short_note(part: Part, slot: Slot) -> Optional[str]:
    """One line when a place could not hold its text whole."""
    def shown(t):
        return t.replace(LINE_BREAK, " ") if slot.lines == 1 else t
    where = _where(slot)
    if part.short == "tail":
        return f"label: {shown(part.full)!r} written as {shown(part.text)!r} to fit {where}"
    if part.short == "cut":
        return (f"label: {shown(part.full)!r} cut short to {shown(part.text)!r} -- {where} "
                f"does not hold it whole at the smallest printable size")
    return None


def _where(slot: Slot) -> str:
    return {"bottom": "the bottom row", "top": "the top row", "square": "the black square"
            }.get(slot.where, f"the white ({slot.where})")


def _lay(slot_i: int, slot: Slot, cands: List[Cand], s: LabelSettings,
         notes: List[str]) -> Optional[Part]:
    """A copy's (or the template's) text in a slot that is already sized:
    at the slot's cap when it fits, else shrunk, else cut short (with a
    note); never under the printable cap."""
    def fits(c):
        arr = wraps(_drawn(c.text), slot.lines)
        return _fit_in(slot.rect, slot.rot, arr, slot.cap, s, typed=True) if arr else None
    got = _first_fit(cands, fits)
    if got is None:
        if cands:
            notes.append(f"label: {cands[0].text!r} does not fit {_where(slot)} at the "
                         f"smallest printable size ({min_cap(s.nozzle_mm, s.stroke_mm):.2f} mm "
                         f"cap at a {s.nozzle_mm:g} mm nozzle)")
        return None
    i, (cap, lines) = got
    part = _part(slot_i, slot, cands[i], lines, cap, s)
    note = _short_note(part, slot)
    if note:
        notes.append(note)
    return part


def _drawn(text: str) -> str:
    """normalize() each line, keeping the "|" breaks."""
    return LINE_BREAK.join(normalize(p)[0] for p in text.split(LINE_BREAK))


def _missing_note(text: str) -> Optional[str]:
    miss = []
    for p in text.split(LINE_BREAK):
        for ch in normalize(p)[1]:
            if ch not in miss:
                miss.append(ch)
    if miss:
        return ("label: the font has no " + " ".join(repr(c) for c in miss)
                + " -- printed as '?'")
    return None


# ------------------------------------------------------------ black placement

def _cap_rule(rule: float, s: LabelSettings, where: str, notes: List[str]) -> Tuple[float, bool]:
    """(the cap to start from, typed?) -- a typed cap above the rule is
    clamped, with a note."""
    if s.cap_mm > 0:
        if s.cap_mm > rule + TOL:
            notes.append(f"label: cap {s.cap_mm:g} mm is over the {rule:.2f} mm {where} "
                         f"allows -- clamped")
            return rule, True
        return float(s.cap_mm), True
    return rule, False


def _sized_slot(slot: Slot, cands: List[Cand], cap_hi: float, typed: bool,
                s: LabelSettings) -> Optional[float]:
    """The cap a slot gets: the largest its SIZING text fits at (every
    copy's id fits too), for the first candidate whose own text fits at all;
    its own largest when the sizing text does not."""
    def fits(c):
        arr = wraps(_drawn(c.text), slot.lines)
        return _fit_in(slot.rect, slot.rot, arr, cap_hi, s, typed) if arr else None
    got = _first_fit(cands, fits)
    if got is None:
        return None
    i, (cap, _lines) = got
    sz = _fit_in(slot.rect, slot.rot, wraps(_drawn(cands[i].sizing), slot.lines), cap_hi, s,
                 typed)
    return sz[0] if sz is not None else cap


def _scope(spec: TagSpec, s: LabelSettings) -> str:
    if spec.layout == "single":
        return "first"
    return "each" if s.scope == "auto" else s.scope


def _row_rect(sq: Rect, m: float, where: str) -> Tuple[Rect, Rect]:
    """(the text's band, the black around it) in one marker's border row."""
    x0, y0, x1, y1 = sq
    u0, u1 = x0 + (1.0 + CORNER_CLEAR) * m, x1 - (1.0 + CORNER_CLEAR) * m
    if where == "top":
        return ((u0, y1 - (1.0 - BLACK_EDGE) * m, u1, y1 - BLACK_EDGE * m),
                (x0 + m, y1 - m, x1 - m, y1))
    return ((u0, y0 + BLACK_EDGE * m, u1, y0 + (1.0 - BLACK_EDGE) * m),
            (x0 + m, y0, x1 - m, y0 + m))


ROW_PARTS = {"bottom": (("bottom", "all"),), "top": (("top", "all"),),
             "both": (("bottom", "first"), ("top", "rest"))}


def _plan_black(spec: TagSpec, s: LabelSettings, name: str, key: str, keep: List[List[Pt]],
                notes: List[str], cut: bool = True) -> Label:
    """The label on the black: border rows (bottom / top / both: line 1 at
    the bottom, the rest at the top) of each marker or the first, or a
    ChArUco board's black square. On a board a line that reads the same on
    every marker (no {id} in it: a name) is written once, on the first."""
    m, ext, _q = _mm(spec)
    lab = Label(kind="black", role="cut", settings=s)
    row = s.black_row(spec.layout)
    if s.row == "square" and row != "square":
        notes.append("label: a black square is a ChArUco board's -- both rows used")
    clear = max(KEEPOUT_MM, KEEPOUT * m)
    slots: List[Slot] = []
    lab.side = row
    if row == "square":
        S = spec.square_length_m * 1000.0
        sq = charuco_square(spec)
        e = max(SQUARE_EDGE * S, m)
        inner = (sq[0] + e, sq[1] + e, sq[2] - e, sq[3] - e)
        corners = charuco_corners_mm(spec)
        cap_hi, typed = _cap_rule(SQUARE_CAP * S, s, "a ChArUco square", notes)
        c0 = ((sq[0] + sq[2]) / 2.0, (sq[1] + sq[3]) / 2.0)
        caps = _caps(cap_hi, min_cap(s.nozzle_mm, s.stroke_mm), typed)

        def centred(du, dv):
            return (c0[0] - du / 2.0, c0[1] - dv / 2.0, c0[0] + du / 2.0, c0[1] + dv / 2.0)

        def ok_rect(r):
            return (r[0] >= inner[0] - TOL and r[2] <= inner[2] + TOL and r[1] >= inner[1] - TOL
                    and r[3] <= inner[3] + TOL
                    and all(rect_point_dist(r, p) >= SQUARE_CORNER * S - TOL for p in corners)
                    and _clear_of(r, keep, clear))

        def best_for(text):
            best = None
            for lines in wraps(_drawn(text), MAX_LINES):
                c = _largest(caps, lambda c, lines=lines: ok_rect(centred(*_dims(
                    lines, metrics(c, s.nozzle_mm, s.stroke_mm), 0))))
                if c is not None and (best is None or c > best[0] + EPS):
                    best = (c, lines)
            return best
        probe = Slot("square", sq, 0, 0.0, MAX_LINES, None, sq)
        cands = _cands(s, spec, probe, name, key, cut)
        got = _first_fit(cands, lambda c: best_for(c.text))
        if got is None:
            notes.append(f"label: nothing printable fits the ChArUco square ({S:.1f} mm) clear "
                         f"of its corner -- smallest cap "
                         f"{min_cap(s.nozzle_mm, s.stroke_mm):.2f} mm")
            lab.kind = "none"
            return lab
        i, (c, lines) = got
        sized = best_for(cands[i].sizing)        # room for every copy's id, when there is
        if sized is not None:
            c, lines = sized
        du, dv = _dims(lines, metrics(c, s.nozzle_mm, s.stroke_mm), 0)
        lab.slots = [Slot("square", centred(du, dv), 0, c, MAX_LINES, None, sq)]
        return lab
    squares = marker_squares(spec)
    scope = _scope(spec, s)
    cap_hi, typed = _cap_rule(BLACK_CAP * m, s, "a border row", notes)
    for where, part in ROW_PARTS[row]:
        tpl = part_template(s.template(), part)
        if not resolve_text(tpl, spec, name=name, key=key).replace(LINE_BREAK, ""):
            continue                           # "#id" has no second line for the top row
        per_marker = scope == "each" and len(squares) > 1 and "{id}" in tpl
        for i in (range(len(squares)) if per_marker else [0]):
            rect, bound = _row_rect(squares[i][1], m, where)
            if not _clear_of(rect, keep, clear):
                notes.append(f"label: marker #{spec.ids[i]}'s {where} row meets a corner mark "
                             "or a pin -- skipped")
                continue
            slots.append(Slot(where, rect, 0, 0.0, 1, i if per_marker else None, bound, part, i))
    if not slots:
        lab.kind = "none"
        return lab
    for sl in slots:
        cands = _cands(s, spec, sl, name, key, cut)
        c = _sized_slot(sl, cands, cap_hi, typed, s)
        if c is None:
            lab.kind = "none"
            text = cands[0].text if cands else ""
            notes.append(
                f"label: {text!r} does not print in a {m:.2f} mm module's {sl.where} row -- "
                f"its cap can be {cap_hi:.2f} mm at most, a {s.nozzle_mm:g} mm nozzle needs "
                f"{min_cap(s.nozzle_mm, s.stroke_mm):.2f} mm (and the row is "
                f"{sl.rect[2] - sl.rect[0]:.1f} mm long); put it on the white, use a "
                f"bigger marker or a finer nozzle")
            return lab
        sl.cap = c
        if typed and s.cap_mm > 0 and c < min(cap_hi, s.cap_mm) - EPS:
            notes.append(f"label: cap {s.cap_mm:g} mm does not fit the {sl.where} row -- "
                         f"shrunk to {c:.2f} mm")
    lab.slots = slots
    return lab


# ------------------------------------------------------------ white placement

def _side_box(side: str, R: Rect, du: float, dv: float, along: float, out: float,
              centre: float) -> Rect:
    """A du x dv box just outside the keep-out rect R on `side`, `out` mm
    further out and `along` mm along the side from `centre`."""
    if side == "below":
        c = centre + along
        return (c - du / 2.0, R[1] - out - dv, c + du / 2.0, R[1] - out)
    if side == "above":
        c = centre + along
        return (c - du / 2.0, R[3] + out, c + du / 2.0, R[3] + out + dv)
    if side == "left":
        c = centre + along
        return (R[0] - out - du, c - dv / 2.0, R[0] - out, c + dv / 2.0)
    c = centre + along
    return (R[2] + out, c - dv / 2.0, R[2] + out + du, c + dv / 2.0)


def _free_spans(loops, polys, along_u: bool, lo: float, hi: float) -> List[Tuple[float, float]]:
    """Where a band lies wholly on the region: the band is lo..hi across
    (v when the label slides along u, else u); -> the intervals along it
    that no boundary segment crosses and that are inside (even-odd), and
    that miss every keep-out polygon (already grown). Exact for polygons."""
    blocked = []

    def clip(p, q):
        if along_u:
            (pa, pc), (qa, qc) = (p[0], p[1]), (q[0], q[1])
        else:
            (pa, pc), (qa, qc) = (p[1], p[0]), (q[1], q[0])
        if (pc < lo - TOL and qc < lo - TOL) or (pc > hi + TOL and qc > hi + TOL):
            return None
        if abs(qc - pc) < EPS:
            return (min(pa, qa), max(pa, qa))
        t0, t1 = (lo - pc) / (qc - pc), (hi - pc) / (qc - pc)
        t0, t1 = max(0.0, min(t0, t1)), min(1.0, max(t0, t1))
        a0, a1 = pa + (qa - pa) * t0, pa + (qa - pa) * t1
        return (min(a0, a1), max(a0, a1))
    for loop in list(loops) + list(polys):
        for i in range(len(loop)):
            r = clip(loop[i - 1], loop[i])
            if r is not None:
                blocked.append(r)
    for poly in polys:                     # a keep-out spanning the whole band
        cs = [(p[1] if along_u else p[0]) for p in poly]
        if min(cs) <= lo and max(cs) >= hi:
            av = [(p[0] if along_u else p[1]) for p in poly]
            blocked.append((min(av), max(av)))
    blocked.sort()
    merged: List[List[float]] = []
    for a, b in blocked:
        if merged and a <= merged[-1][1] + TOL:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    edges = [-math.inf] + [v for ab in merged for v in ab] + [math.inf]
    out = []
    mid_c = (lo + hi) / 2.0
    for a, b in zip(edges[0::2], edges[1::2]):
        if not (b - a > TOL) or math.isinf(a) or math.isinf(b):
            continue
        m = (a + b) / 2.0
        if _inside(loops, m, mid_c) if along_u else _inside(loops, mid_c, m):
            out.append((a, b))
    return out


def _place_on_region(side: str, R: Rect, du: float, dv: float, centre: float, loops, polys,
                     e: float, clear: float, step: float, reach: float
                     ) -> Optional[Rect]:
    """The du x dv box on `side` of R nearest the tag: for each distance out
    (0, step, ... up to `reach`), the free spans of its band (grown by the
    edge margin), the spot along it nearest the tag's axis."""
    along_u = side in ("below", "above")
    length = du if along_u else dv
    grown = [_grow_poly(p, clear) for p in polys]
    n = int(reach / step + 1e-9)
    for k in range(n + 1):
        out = k * step
        b0 = _side_box(side, R, du, dv, 0.0, out, centre)
        lo, hi = (b0[1] - e, b0[3] + e) if along_u else (b0[0] - e, b0[2] + e)
        best = None
        for a, b in _free_spans(loops, grown, along_u, lo, hi):
            if b - a < length + 2.0 * e - TOL:
                continue
            c = min(max(centre, a + e + length / 2.0), b - e - length / 2.0)
            if best is None or abs(c - centre) < abs(best - centre) - EPS:
                best = c
        if best is not None:
            return _side_box(side, R, du, dv, best - centre, out, centre)
    return None


def _grow_poly(poly: Sequence[Pt], d: float) -> List[Pt]:
    """A convex polygon's box grown by d -- a conservative keep-out for the
    band scan (corner marks and pins are small and near-square)."""
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return rect_poly((min(xs) - d, min(ys) - d, max(xs) + d, max(ys) + d))


def _deeper(r: Rect, side: str, d: float) -> Rect:
    """A rect grown by d on one side."""
    if side == "below":
        return (r[0], r[1] - d, r[2], r[3])
    if side == "above":
        return (r[0], r[1], r[2], r[3] + d)
    if side == "left":
        return (r[0] - d, r[1], r[2], r[3])
    return (r[0], r[1], r[2] + d, r[3])


def _grown(tile: Rect, box: Rect, e: float) -> Rect:
    return (min(tile[0], box[0] - e), min(tile[1], box[1] - e),
            max(tile[2], box[2] + e), max(tile[3], box[3] + e))


def _plan_white(spec: TagSpec, s: LabelSettings, name: str, key: str, *, mode: str,
                op: Optional[str], region, grow: bool, bounds, tile: Rect, mark, pins,
                keep: List[List[Pt]], notes: List[str], cut: bool = True) -> Label:
    m, ext, _q = _mm(spec)
    lab = Label(kind="white", role="add", settings=s)
    R = keepout_rect(spec)
    e = max(WHITE_EDGE * m, EDGE_MIN_MM)
    clear = max(KEEPOUT_MM, KEEPOUT * m)
    step = max(0.5, 0.25 * m)                # how far a label moves at a time
    cap_hi, typed = _cap_rule(WHITE_LIMIT * m if s.cap_mm > 0 else WHITE_CAP * m, s,
                              "the white", notes)
    caps = _caps(cap_hi, min_cap(s.nozzle_mm, s.stroke_mm), typed)
    cands = _cands(s, spec, Slot("white", tile, 0, 0.0, MAX_LINES), name, key, cut)
    sides = [s.side] if s.side in SIDE_ORDER else list(SIDE_ORDER)
    loops = [lp for lp in (_clean(l) for l in (region or ())) if len(lp) >= 3] if region else []
    bnd = [lp for lp in (_clean(l) for l in (bounds or ())) if len(lp) >= 3] if bounds else []
    if region is not None and not loops:
        notes.append("label: the white around the tag has no outline")
    if not loops and not grow:
        lab.kind = "none"
        notes.append("label: no white to put it on -- the tag has no white of its own around "
                     "it (Black only on a sketch with no body; or allow the tile to grow)")
        return lab
    tile_cut = mark is not None and (mark.get("kind") if isinstance(mark, dict)
                                     else mark.kind) == "tile_cut"
    centre_u, centre_v = (ext[0] + ext[2]) / 2.0, (ext[1] + ext[3]) / 2.0
    tries = []          # (candidate, cap, side order index, side, lines, box, white or None)
    for si, side in enumerate(sides):
        rot = SIDE_ROT[side]
        centre = centre_u if side in ("below", "above") else centre_v
        if loops:

            def place(c, lines):
                du, dv = _dims(lines, metrics(c, s.nozzle_mm, s.stroke_mm), rot)
                b = _place_on_region(side, R, du, dv, centre, loops, keep, e, clear, step,
                                     REACH * m)
                # re-checked exactly: inside the region with its margin, clear of
                # every keep-out polygon as drawn
                if b is None or not _rect_inside(
                        loops, (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0,
                        b[2] - b[0] + 2.0 * e, b[3] - b[1] + 2.0 * e) \
                        or not _clear_of(b, keep, clear):
                    return None
                return b, None
        else:
            long_ = (tile[2] - tile[0]) if side in ("below", "above") else (tile[3] - tile[1])
            tcentre = ((tile[0] + tile[2]) / 2.0 if side in ("below", "above")
                       else (tile[1] + tile[3]) / 2.0)

            def place(c, lines):
                du, dv = _dims(lines, metrics(c, s.nozzle_mm, s.stroke_mm), rot)
                if (du if side in ("below", "above") else dv) > long_ - 2.0 * e + TOL:
                    return None
                b = _side_box(side, R, du, dv, tcentre - centre, 0.0, centre)
                base = _grown(tile, b, e)
                # the cut corner and the pins sit at the grown tile's
                # corners, level with the text: a deeper strip (up to
                # REACH more) takes them past it before the text shrinks
                for k in range(int(REACH * m / step + 1e-9) + 1):
                    white = _deeper(base, side, k * step) if base != tile else base
                    if bnd and not _rect_inside(bnd, (white[0] + white[2]) / 2.0,
                                                (white[1] + white[3]) / 2.0,
                                                white[2] - white[0], white[3] - white[1]):
                        return None
                    polys = list(keep)
                    leg = 0.0
                    if tile_cut:
                        mk = mark_on_tile(spec, mode, op, white)
                        polys += mark_keepouts(mk)
                        leg = mk.leg if mk.kind == "tile_cut" else 0.0
                    polys += _pins_on(white, pins, leg, [])
                    if _clear_of(b, polys, clear):
                        return b, white
                    if white == tile:
                        return None
                return None

        def search(text, place=place):
            """(cap, lines, box, white) for a text on this side, or None."""
            best = None
            for k, lines in enumerate(wraps(_drawn(text), MAX_LINES)):
                hit = {}

                def ok(c, lines=lines):
                    r = place(c, lines)
                    if r is not None:
                        hit[c] = r
                    return r is not None
                # the strip's pins move with the strip: scan, do not bisect
                c = (next((c for c in caps if ok(c)), None) if not loops
                     else _largest(caps, ok))
                if c is None:
                    continue
                if c not in hit:
                    ok(c)
                res = (c, lines, hit[c][0], hit[c][1])
                if k == 0 and c >= max(ONE_LINE * m, caps[-1]) - EPS:
                    return res                  # as written (one line) is big enough
                if best is None or c > best[0] + EPS:
                    best = res
            return best

        def fits(cand, search=search):
            # sized for every copy's id when that fits, else for its own text
            r = search(cand.sizing)
            return r if r is not None or cand.sizing == cand.text else search(cand.text)
        got = _first_fit(cands, fits)
        if got is not None:
            i, (c, lines, box, white) = got
            tries.append((i, c, si, side, lines, box, white))
    if not tries:
        lab.kind = "none"
        where = "the white around it" if loops else "a label strip"
        notes.append(f"label: no room on {where} for {cands[0].text!r} at the smallest printable "
                     f"size ({min_cap(s.nozzle_mm, s.stroke_mm):.2f} mm cap), "
                     f"{(quiet_ring(spec) + WHITE_GAP):g} modules clear of the black"
                     if cands else "label: the text is empty")
        return lab
    # the least shortened text first, then the biggest, then the side order
    tries.sort(key=lambda t: (t[0], -round(t[1], 6), t[2]))
    _i, cap, _si, side, lines, box, white = tries[0]
    if typed and s.cap_mm > 0 and cap < min(cap_hi, s.cap_mm) - EPS:
        notes.append(f"label: cap {s.cap_mm:g} mm does not fit {side} -- shrunk to {cap:.2f} mm")
    lab.side = side
    lab.slots = [Slot(side, box, SIDE_ROT[side], cap, MAX_LINES, None, None)]
    if white is not None and white != tile:
        lab.white = white
        lab.strip = _strip(tile, white)
    return lab


def _strip(tile: Rect, white: Rect) -> Rect:
    """The rectangle a grown white added (one side)."""
    if white[1] < tile[1] - EPS:
        return (white[0], white[1], white[2], tile[1])
    if white[3] > tile[3] + EPS:
        return (white[0], tile[3], white[2], white[3])
    if white[0] < tile[0] - EPS:
        return (white[0], white[1], tile[0], white[3])
    return (tile[2], white[1], white[2], white[3])


# -------------------------------------------------------------------- plan

def white_room(mode: str, op: Optional[str], has_body: bool, tile_bigger: bool = False) -> dict:
    """Where a label on the white finds its white, per ArUco Extrude mode:

        White + black, a tile bigger than the sticker (a sketch area)
                                   region = that tile (rect_loop)
        White + black, the sticker grow the tile by a strip (inside the
                                   face when there is a body under)
        Black only                 region = the face: the user's white body
        White only                 grow the white pattern by a strip (inside
                                   the face it sits on)
    -> {"grow": bool, "region": "tile" | "face" | None,
        "bounds": "face" | None, "why": str}"""
    if mode == "both":
        if tile_bigger:
            return {"grow": False, "region": "tile", "bounds": None,
                    "why": "the white tile is bigger than the sticker"}
        return {"grow": True, "region": None, "bounds": "face" if has_body else None,
                "why": "the white tile grows by a label strip"}
    if mode == "black":
        if has_body:
            return {"grow": False, "region": "face", "bounds": None,
                    "why": "the label sits on the body around the tag (it must be white)"}
        return {"grow": False, "region": None, "bounds": None,
                "why": "no body under the tag: no white around it"}
    return {"grow": True, "region": None, "bounds": "face" if has_body else None,
            "why": "the white pattern grows by a label strip"}


def plan_label(spec: TagSpec, settings: Optional[LabelSettings] = None, *, mode: str = "both",
               op: Optional[str] = "new", name: str = "", key: str = "",
               region: Optional[Sequence[Sequence[Pt]]] = None, grow: bool = False,
               bounds: Optional[Sequence[Sequence[Pt]]] = None, tile: Optional[Rect] = None,
               mark=None, pins: Optional[dict] = None,
               keepouts: Sequence[Sequence[Pt]] = ()) -> Label:
    """One tag's label (see the module docstring). Never raises.

    spec      markers.TagSpec, the RESOLVED one (a copy's own ids)
    settings  LabelSettings (or its dict); None / off -> kind "none"
    mode, op  ArUco Extrude's mode ("both" / "black" / "white") and the
              effective op ("join" / "new" / None): the corner mark on a
              grown tile follows them
    name, key {name} and {key}
    region    the white the label may sit on, loops in mm in the pattern
              frame (white_room: the face, or a big tile's rect_loop)
    grow      no region: the tile (White only: the white pattern) may grow
              by a label strip; `bounds` (loops) keep the grown white inside
              the face it sits on
    tile      the white tile's rect before growing (default the sticker)
    mark      the corner mark (aruco_chamfer Mark / dict) of the un-grown
              tile; a tile cut is re-made on a grown tile (mark_on_tile)
    pins      {"shape", "size", "margin"}: the locating pins, on the tile
              the label ends up with (aruco_slabs.pin_outlines)
    keepouts  more polygons (mm) to keep KEEPOUT_MM clear of
    """
    notes: List[str] = []
    if isinstance(settings, dict):
        settings = LabelSettings.from_dict(settings, notes)
    s = settings or LabelSettings()
    if not s.on:
        return Label(settings=s)
    if s.stroke_mm and s.stroke_mm < s.nozzle_mm - EPS:
        notes.append(f"label: stroke {s.stroke_mm:g} mm is under the {s.nozzle_mm:g} mm nozzle "
                     "-- the nozzle used")
    bad = unknown_tokens(s.template())
    if bad:
        notes.append("label: unknown " + ", ".join("{%s}" % t for t in bad)
                     + " (known: " + ", ".join("{%s}" % t for t in TOKENS) + ")")
    try:
        aruco_board.check_spec(board_spec(spec))
    except (ValueError, KeyError) as exc:
        return Label(settings=s, notes=notes + [f"label: {exc}"])
    if not resolve_text(s.template(), spec, name=name, key=key).replace(LINE_BREAK, ""):
        return Label(settings=s, notes=notes + ["label: the text is empty"])
    note = _missing_note(resolve_text(s.template(), spec, name=name, key=key))
    if note:
        notes.append(note)
    tile = tuple(tile) if tile else sticker_rect(spec)
    extra = [list(p) for p in keepouts or ()]
    tile_cut = bool(mark) and (mark.get("kind") if isinstance(mark, dict)
                               else mark.kind) == "tile_cut"
    leg = (mark.get("leg_mm", 0.0) if isinstance(mark, dict) else mark.leg) if tile_cut else 0.0
    # the tile as it is: its corner mark and its pins
    keep = extra + mark_keepouts(mark) + _pins_on(tile, pins, leg, [])
    strip = bool(grow) and not region
    # a strip moves the tile's corner (a cut is re-made there) and its
    # pins; a triangle stays in the sticker's corner
    wkeep = (extra + ([] if tile_cut else mark_keepouts(mark))) if strip else keep

    def black(cut, out):
        return _plan_black(spec, s, name, key, keep, out, cut)

    def white(cut, out):
        return _plan_white(spec, s, name, key, mode=mode, op=op, region=region,
                           grow=bool(grow), bounds=bounds, tile=tile,
                           mark=mark if strip else None, pins=pins if strip else None,
                           keep=wkeep, notes=out, cut=cut)
    if s.place == "black":
        lab = black(True, notes)
    elif s.place == "white":
        lab = white(True, notes)
    else:
        # auto: the whole text on the black, else the whole text on the
        # white, else cut short -- on the white (more room), else the black
        lab, why = None, []
        for fn, cut in ((black, False), (white, False), (white, True), (black, True)):
            said: List[str] = []
            got = fn(cut, said)
            if got.kind != "none":
                lab = got
                if fn is white and why:
                    notes.append("label: not on the black -- " + "; ".join(
                        w.replace("label: ", "") for w in why[:1]) + ": on the white instead")
                notes += said
                break
            if not why:
                why = said
        if lab is None:
            lab = Label(settings=s)
            notes += why
    lab.settings = s
    if lab.kind == "none":
        lab.slots, lab.parts = [], []
    for i, sl in enumerate(lab.slots):
        part = _lay(i, sl, _cands(s, spec, sl, name, key), s, notes)
        if part is not None:
            lab.parts.append(part)
    if lab.parts and lab.min_feature < s.nozzle_mm - TOL:
        notes.append(f"label: smallest feature {lab.min_feature:.2f} mm is under the "
                     f"{s.nozzle_mm:g} mm nozzle")
    lab.notes = list(dict.fromkeys(notes))          # a board's markers say it once
    return lab


def relabel(label, spec: TagSpec, *, name: str = "", key: str = "") -> Label:
    """A copy's label: the template's slots (sized, checked, the strip and
    the tile as built) with the copy's own text -- its resolved ids, its
    name. What a variant builds (aruco_tags._build_variant)."""
    lab = from_dict(label) if isinstance(label, dict) else label
    out = Label(kind=lab.kind, role=lab.role, side=lab.side, settings=lab.settings,
                slots=list(lab.slots), white=lab.white, strip=lab.strip, notes=[])
    if out.kind == "none":
        return out
    for i, sl in enumerate(out.slots):
        part = _lay(i, sl, _cands(lab.settings, spec, sl, name, key), lab.settings,
                    out.notes)
        if part is not None:
            out.parts.append(part)
    out.notes = list(dict.fromkeys(out.notes))
    return out


def label_for_instance(label, inst) -> Label:
    """relabel() for a markers.TagInstance: its resolved spec, the name its
    geometry shows (instance_name), its tag key."""
    return relabel(label, inst.spec, name=instance_name(inst), key=inst.tag.key)


# ------------------------------------------------------------ safety check

def check_label(spec: TagSpec, label, tol: float = 1e-6) -> List[str]:
    """Problems with a label against the detection rules, from the spec
    alone (the slots are not trusted). [] = safe.

    white  no ink inside the quiet ring (the black grown by quiet_ring),
           and none within WHITE_GAP more of it
    black  a row's ink inside its border row's band (BLACK_EDGE of black
           above and below), clear of the corner cells, cap <= 0.5 module;
           a ChArUco square's ink inside that black square, >= 0.3 square
           from every ChArUco corner
    both   strokes >= the nozzle"""
    lab = from_dict(label) if isinstance(label, dict) else label
    if lab.kind == "none":
        return []
    out: List[str] = []
    m, ext, _q = _mm(spec)
    noz = lab.settings.nozzle_mm
    for p in lab.parts:
        polys = [b.corners() for b in p.boxes]
        if p.stroke < noz - tol:
            out.append(f"{p.text!r}: stroke {p.stroke:.3f} mm under the {noz:g} mm nozzle")
        if p.min_feature < noz - tol:
            out.append(f"{p.text!r}: a feature of {p.min_feature:.3f} mm under the nozzle")
        sl = lab.slots[p.slot] if p.slot < len(lab.slots) else None
        if lab.kind == "white":
            ring = grow_rect(ext, quiet_ring(spec) * m)
            far = grow_rect(ext, (quiet_ring(spec) + WHITE_GAP) * m)
            if any(_overlap(q, rect_poly(ring)) for q in polys):
                out.append(f"{p.text!r}: ink inside the quiet ring")
            elif any(_overlap(q, rect_poly(far)) for q in polys):
                out.append(f"{p.text!r}: ink within {WHITE_GAP:g} module of the quiet zone")
            continue
        where = sl.where if sl else ""
        if where == "square":
            S = spec.square_length_m * 1000.0
            sq = charuco_square(spec)
            if any(not (sq[0] - tol <= x <= sq[2] + tol and sq[1] - tol <= y <= sq[3] + tol)
                   for q in polys for x, y in q):
                out.append(f"{p.text!r}: ink outside the black square")
            d = min((pt_poly_dist(c, q) for q in polys for c in charuco_corners_mm(spec)),
                    default=math.inf)
            if d < SQUARE_CORNER_HARD * S - tol:
                out.append(f"{p.text!r}: {d / S:.2f} square from a ChArUco corner "
                           f"(< {SQUARE_CORNER_HARD:g})")
            continue
        squares = marker_squares(spec)
        idx = (sl.square if sl.square is not None else sl.marker or 0) if sl else 0
        if where not in ROWS or idx >= len(squares):
            out.append(f"{p.text!r}: not in a known place ({where!r})")
            continue
        x0, y0, x1, y1 = squares[idx][1]
        if where == "top":
            band = (x0 + m, y1 - (1.0 - BLACK_EDGE) * m, x1 - m, y1 - BLACK_EDGE * m)
        else:
            band = (x0 + m, y0 + BLACK_EDGE * m, x1 - m, y0 + (1.0 - BLACK_EDGE) * m)
        r = ink_rect(p.boxes)
        if r is None:
            continue
        if r[1] < band[1] - tol or r[3] > band[3] + tol:
            out.append(f"{p.text!r}: ink outside the {where} row's band "
                       f"({BLACK_EDGE:g} module of black kept to its edges)")
        if r[0] < band[0] - tol or r[2] > band[2] + tol:
            out.append(f"{p.text!r}: ink in a corner cell")
        if r[3] - r[1] > BLACK_CAP * m + tol:
            out.append(f"{p.text!r}: cap {r[3] - r[1]:.3f} mm over {BLACK_CAP:g} module")
    return out
