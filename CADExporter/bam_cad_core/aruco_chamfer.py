"""ArUco Extrude's corner mark: the orientation corner marked by SHAPE (the
pure geometry; standard library only).

Zach, 2026-10-08: "implement that as a check box option. Chamfer top left
corner. make sure it works in single marker, board and charuco." A 3D
printer has no grey, so the PDF's grey corner triangle cannot be printed.
2026-10-09 ("the ability to add the black corner to the white"): the
checkbox became a choice, "Corner mark: None / Cut white corner / Black
triangle" (`want` = "none" / "cut" / "triangle"; `valid_marks` says which a
tag can have, `choose` what it gets when asked for one it cannot):

    cut       a tile of its own (White + black, New body): its corner cut;
              a ChArUco board whose top-left square is black: that square's
              outer corner cut, in every mode (the board's "cut" form)
    triangle  any white: a black triangle in the quiet zone's corner -- the
              tile stays a plain rectangle (White only: a notch the black
              body under shows through). Not on a ChArUco board whose
              top-left square is black: there is no white corner.

The old checkbox ("chamfer": true) is "cut": asked where there is no tile of
its own, "cut" gives the triangle -- exactly what the checkbox made.
docs/proposal_aruco_chamfer.md measured what can be (298,000 simulated
photos, OpenCV 5.0 and 4.10):

    the BLACK square's corner cut       never on a marker: corner 0 slides
                                        along the cut (0.07 module even at
                                        legs 0.25), detection drops from 0.5
    the WHITE tile's corner cut         free: -0.2..+0.1 pts, corner 0 kept
                                        (legs 0.5..1.0 module; 0.7 is the
                                        sticker cut `chamfer_tl`)
    a ChArUco board's top-left BLACK    free, not one trial changed: that
    square's outer corner               corner is no ChArUco corner
    a triangle in the quiet zone's      free (`tri_tl`, the PDF's mark;
    top-left corner, legs 0.6 module    black: `tri_tl_black`)

What a tag gets (`plan_mark`). "Top-left" is OpenCV's corner 0 of a single
marker / the board's origin corner, in the pattern frame AFTER the Origin
the user picked. "cut":

    layout        a tile of its own              no tile edge to cut
                  (White + black, New body)      (Black only, White only,
                                                 White + black JOINED: the
                                                 tile top is the user's body)
    single, grid  "tile_cut": the white tile's   "triangle" instead (below)
                  corner at 45 deg, legs 0.7
                  module (the part under it
                  shows through)

"triangle" (any white corner, a tile of its own included): legs 0.6
module, its right angle in the quiet zone's outer corner, ~1 module from
the black corner. A ChArUco board's "cut", in every mode (no "triangle":
its top-left corner is black):

    charuco       "black_cut", in every mode: the top-left black square's
                  outer corner at 45 deg, legs 2 modules (at most 1/4
                  square); the white fills it (White + black: the tile;
                  Black only: the part; White only: the white pattern).
                  A board whose top-left square is WHITE (legacy pattern,
                  even rows) is marked like a single marker, the marker in
                  that square standing in for corner 0.

The triangle is the pattern's colour: part of the black (Black only, White +
black) or a notch in the white pattern showing the black body under it
(White only) -- the 3D twin of the PDF's grey triangle.

Coordinates: mm in the PATTERN frame -- origin the pattern's centre, u along
the pattern's x (image right), v image up -- the frame markers.cell_boxes
uses (in metres), turned with the pattern. Polygons are counter-clockwise.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

KINDS = ("none", "tile_cut", "black_cut", "triangle")
# the user's choice (ArUco Extrude's "Corner mark"), and the dialog's words
CHOICES = ("none", "cut", "triangle")
CHOICE_LABELS = {"none": "None", "cut": "Cut white corner", "triangle": "Black triangle"}
CUT_LABEL_CHARUCO = "Cut black square's corner"     # the board's own "cut" form
# notes that only say which form a tag got instead of the one asked for
# (aruco_feature shows them in the dialog, never as a node warning)
INSTEAD = "corner mark form: "

TILE_LEG = 0.7           # modules: the tile cut (= the sticker cut `chamfer_tl`)
TILE_LEG_MAX = 1.0       # modules: the largest tile cut measured
TILE_LEG_MIN = 0.5       # modules: the smallest measured (and still a visible cut)
# the tile cut's line stays (2 q - leg) / sqrt 2 from the black corner; the
# largest cut measured (legs 1.0, quiet 1) leaves 1/sqrt 2 = 0.707 module, so
# a cut keeps legs <= dx + dy - 1 module (dx, dy: tile corner -> black corner)
TILE_ROOM = 1.0          # modules
CHARUCO_LEG = 2.0        # modules: the top-left black square's outer corner
CHARUCO_LEG_SQUARE = 0.25  # ... at most this much of the square (9 mm on 37 mm)
TRI_LEG = 0.6            # modules: the triangle's legs (the PDF's TRI_LEG_MODULES)
TRI_CLEAR = 0.9          # modules: the triangle's long side from the black corner, at least
EPS = 1e-9

Pt = Tuple[float, float]


def charuco_black_tl(rows: int, legacy: bool) -> bool:
    """Is a ChArUco board's top-left square black? OpenCV's rule
    (aruco_board._charuco_black(0, 0, ...)): yes, unless the legacy pattern
    AND an even row count."""
    return not (legacy and int(rows) % 2 == 0)


@dataclass
class Mark:
    """One tag's corner mark. Lengths in mm, points in the pattern frame.

    kind     "none" | "tile_cut" | "black_cut" | "triangle"
    leg      the 45-degree cut's legs / the triangle's legs
    cut      the corner taken away (tile_cut, black_cut): exact, CCW
    tool     the cut tool: the same long side, poking `leg` past both edges
             (a boolean with no coplanar faces)
    solid    the triangle (triangle)
    outline  what is cut, after the cut: the tile (tile_cut) or the top-left
             black square (black_cut) -- for drawing (the ghost)
    corner   the corner cut / the triangle's right angle
    black    corner 0 of the top-left marker / the board's origin corner
    clear    the cut / triangle's long side from the nearest black (tile_cut,
             triangle) or from the nearest ChArUco corner (black_cut)
    note     one line: what was done, or why nothing was
    instead  one line when the form asked for cannot be had here and this
             is the other one ("" otherwise; not kept in the record)
    """
    kind: str = "none"
    leg: float = 0.0
    module: float = 0.0
    cut: List[Pt] = field(default_factory=list)
    tool: List[Pt] = field(default_factory=list)
    solid: List[Pt] = field(default_factory=list)
    outline: List[Pt] = field(default_factory=list)
    corner: Pt = (0.0, 0.0)
    black: Pt = (0.0, 0.0)
    clear: float = 0.0
    note: str = ""
    instead: str = ""

    @property
    def is_cut(self) -> bool:
        return self.kind in ("tile_cut", "black_cut")

    def to_dict(self, nd: int = 4) -> dict:
        """JSON for the tag record (aruco_tags builds a copy's mark from it)."""
        def r(pts):
            return [[round(u, nd), round(v, nd)] for u, v in pts]
        if self.kind == "none":
            return {"kind": "none", "note": self.note}
        return {"kind": self.kind, "leg_mm": round(self.leg, nd),
                "module_mm": round(self.module, nd), "cut": r(self.cut), "tool": r(self.tool),
                "solid": r(self.solid), "outline": r(self.outline),
                "corner": [round(self.corner[0], nd), round(self.corner[1], nd)],
                "black": [round(self.black[0], nd), round(self.black[1], nd)],
                "clear_mm": round(self.clear, nd), "note": self.note}


def area2(pts: Sequence[Pt]) -> float:
    """Twice the signed area (> 0: counter-clockwise)."""
    return sum(pts[i - 1][0] * pts[i][1] - pts[i][0] * pts[i - 1][1] for i in range(len(pts)))


def corner_triangle(c: Pt, leg: float) -> List[Pt]:
    """The right triangle in a top-left corner `c`: legs along +u and -v (CCW)."""
    return [(c[0], c[1]), (c[0], c[1] - leg), (c[0] + leg, c[1])]


def corner_tool(c: Pt, leg: float, over: float) -> List[Pt]:
    """The cut tool for a top-left corner `c`: its long side on the cut line
    (u - cu) + (cv - v) = leg, its two short sides `over` outside the edges
    through `c`, both axis-aligned (CCW)."""
    return [(c[0] - over, c[1] + over), (c[0] - over, c[1] - leg - over),
            (c[0] + leg + over, c[1] + over)]


def cut_rect(rect: Tuple[float, float, float, float], leg: float) -> List[Pt]:
    """A rectangle (u0, v0, u1, v1) with its top-left corner cut at 45 deg,
    legs `leg`: a pentagon (CCW)."""
    u0, v0, u1, v1 = rect
    return [(u0, v0), (u1, v0), (u1, v1), (u0 + leg, v1), (u0, v1 - leg)]


def is_top_left(rect: Tuple[float, float, float, float], c: Pt, tol: float = 1e-6) -> bool:
    """Does a rectangle (u0, v0, u1, v1) have its top-left corner at `c`?"""
    return abs(rect[0] - c[0]) <= tol and abs(rect[3] - c[1]) <= tol


def charuco_cut(layout: str, rows: int = 1, legacy: bool = False) -> bool:
    """Is this tag's "cut" its ChArUco board's top-left BLACK square's corner?"""
    return layout == "charuco" and charuco_black_tl(rows, legacy)


def valid_marks(layout: str, mode: str, op: Optional[str], rows: int = 1,
                legacy: bool = False) -> List[str]:
    """The corner marks this tag's geometry can have, in the dialog's order
    ("none" is always possible and not listed): "cut" on a white tile of its
    own (White + black, New body) or a ChArUco board's black top-left
    square; "triangle" on any white corner (not that board's)."""
    out = []
    board_cut = charuco_cut(layout, rows, legacy)
    if board_cut or (mode == "both" and op == "new"):
        out.append("cut")
    if not board_cut:
        out.append("triangle")
    return out


def choice_of(settings: dict) -> str:
    """The corner mark a node's settings ask for: "corner_mark", or the old
    checkbox ("chamfer": true is "cut", which reproduces what it made)."""
    want = settings.get("corner_mark")
    if want in CHOICES:
        return want
    return "cut" if settings.get("chamfer") else "none"


def choose(want: str, valid: Sequence[str]) -> Tuple[str, str]:
    """(the form to build, a note when it is not `want`): a form this tag
    cannot have gives the other one -- the corner stays marked."""
    if want not in ("cut", "triangle"):
        return "none", ""
    if want in valid:
        return want, ""
    other = next((v for v in valid if v != want), None)
    if other is None:
        return "none", INSTEAD + "no corner mark fits this tag"
    if want == "cut":
        return other, (INSTEAD + "a cut white corner needs a white tile of its own (White + "
                       "black, New body): a black triangle instead")
    return other, (INSTEAD + "a ChArUco board's top-left square is black, no white corner "
                   "for a triangle: that square's outer corner cut instead")


def plan_mark(layout: str, mode: str, op: Optional[str], extent_mm: Tuple[float, float],
              module_mm: float, quiet_modules: float = 1.0,
              tile_mm: Optional[Tuple[float, float]] = None, square_mm: float = 0.0,
              marker_mm: float = 0.0, rows: int = 1, legacy: bool = False,
              want: str = "cut") -> Mark:
    """The corner mark for one tag (see the module docstring).

    extent_mm  the black pattern's (w, h) (TagSpec.extent_m, in mm)
    module_mm  one module of a marker
    tile_mm    White + black: the white tile's (w, h) IN THE PATTERN FRAME
               (a sketch area's box can be bigger than marker + quiet zone;
               a 90/270-degree Origin turn swaps its sides). Else the
               pattern + quiet zone.
    op         the effective operation: "join" / "new" (White + black), else None
    want       "cut" / "triangle" ("none": no mark); one the tag cannot have
               gives the other (Mark.instead says so)
    """
    W, H = (float(v) for v in extent_mm)
    m = float(module_mm)
    q = max(0.0, float(quiet_modules))
    if m <= 0 or W <= 0 or H <= 0:
        return Mark(note="corner mark: no pattern")
    form, instead = choose(want, valid_marks(layout, mode, op, rows, legacy))
    if form == "none":
        return Mark(note=instead, instead=instead)
    spec_tile = (W + 2.0 * q * m, H + 2.0 * q * m)
    out = Mark(module=m, instead=instead)
    if form == "cut" and charuco_cut(layout, rows, legacy):
        sq = float(square_mm)
        leg = min(CHARUCO_LEG * m, CHARUCO_LEG_SQUARE * sq)
        k = (-W / 2.0, H / 2.0)
        out.kind, out.leg, out.corner, out.black = "black_cut", leg, k, k
        out.cut = corner_triangle(k, leg)
        out.tool = corner_tool(k, leg, leg)
        out.outline = cut_rect((k[0], k[1] - sq, k[0] + sq, k[1]), leg)
        out.clear = (2.0 * sq - leg) / math.sqrt(2.0)     # to the square's inner corner
        out.note = (f"corner mark: the top-left black square's outer corner cut at 45°, "
                    f"legs {leg:.2f} mm ({leg / m:.2g} module): the white shows there")
        return out
    # corner 0 of the top-left marker: the extent's corner, or the marker in
    # a white top-left ChArUco square, (square - marker) / 2 in
    if layout == "charuco":
        inset = max(0.0, (float(square_mm) - float(marker_mm)) / 2.0)
        k = (-W / 2.0 + inset, H / 2.0 - inset)
    else:
        k = (-W / 2.0, H / 2.0)
    out.black = k
    if form == "cut":                       # valid_marks: White + black, a new body
        tw, th = tile_mm if tile_mm else spec_tile
        c = (-float(tw) / 2.0, float(th) / 2.0)
        room = (k[0] - c[0]) + (c[1] - k[1]) - TILE_ROOM * m
        leg = min(TILE_LEG * m, TILE_LEG_MAX * m, room)
        if leg < TILE_LEG_MIN * m - EPS:
            out.note = (f"corner mark: no room for a chamfer -- the white border is "
                        f"{q:g} module; the cut needs about 1 module to stay clear of "
                        "the black")
            return out
        out.kind, out.leg, out.corner = "tile_cut", leg, c
        out.cut = corner_triangle(c, leg)
        out.tool = corner_tool(c, leg, leg)
        out.outline = cut_rect((c[0], -float(th) / 2.0, float(tw) / 2.0, c[1]), leg)
        out.clear = ((k[0] - c[0]) + (c[1] - k[1]) - leg) / math.sqrt(2.0)
        out.note = (f"corner mark: the white tile's top-left corner cut at 45°, legs "
                    f"{leg:.2f} mm ({leg / m:.2g} module): the part shows through")
        return out
    # no tile edge of its own: a triangle in the quiet zone's top-left corner
    c = (-spec_tile[0] / 2.0, spec_tile[1] / 2.0)
    t = TRI_LEG * m
    clear = ((k[0] - c[0]) + (c[1] - k[1]) - t) / math.sqrt(2.0)
    if clear < TRI_CLEAR * m - EPS:
        out.note = (f"corner mark: no room for the corner triangle -- the white border is "
                    f"{q:g} module; it needs about 1 module")
        return out
    out.kind, out.leg, out.corner, out.clear = "triangle", t, c, clear
    out.solid = corner_triangle(c, t)
    colour = "a notch in the white" if mode == "white" else "a black triangle"
    if instead:
        why = {"both": "the tile is joined to the body: a cut corner would cut the body",
               "black": "no tile of its own to cut",
               "white": "no tile of its own to cut: the body under shows"}.get(mode, "")
    else:
        why = {"both": ("the tile stays a plain rectangle" if op == "new" else
                        "the tile is joined to the body"),
               "black": "on the body's white", "white": "the body under shows"}.get(mode, "")
    out.note = (f"corner mark: {colour}, legs {t:.2f} mm, in the quiet zone's top-left "
                f"corner ({why})")
    return out


def mark_for_spec(spec, mode: str, op: Optional[str],
                  tile_mm: Optional[Tuple[float, float]] = None, want: str = "cut") -> Mark:
    """plan_mark for a markers.TagSpec (lengths in metres)."""
    w, h = spec.extent_m
    return plan_mark(spec.layout, mode, op, (w * 1000.0, h * 1000.0), spec.module_m * 1000.0,
                     quiet_modules=spec.quiet_modules, tile_mm=tile_mm,
                     square_mm=spec.square_length_m * 1000.0,
                     marker_mm=spec.marker_length_m * 1000.0, rows=spec.rows,
                     legacy=bool(spec.legacy), want=want)


def pin_inset(cut: float, margin: float, half: float = 0.0) -> float:
    """How far in from a top-left tile corner cut by `cut` (legs, mm) a pin's
    corner point (`half` = 0: a triangle's right angle) or centre (`half` = a
    round pin's radius) goes along both edges, so it keeps `margin` from the
    edges and from the cut line."""
    d = margin + half
    if cut > 0:
        d = max(d, (cut + math.sqrt(2.0) * (margin + half)) / 2.0)
    return d


def from_dict(d: Optional[dict]) -> Optional[Mark]:
    """A record's mark back (None for none / absent)."""
    if not d or d.get("kind") in (None, "none"):
        return None
    def p(key):
        return [tuple(map(float, xy)) for xy in d.get(key) or []]
    return Mark(kind=d["kind"], leg=float(d.get("leg_mm", 0.0)),
                module=float(d.get("module_mm", 0.0)), cut=p("cut"), tool=p("tool"),
                solid=p("solid"), outline=p("outline"),
                corner=tuple(map(float, d.get("corner") or (0.0, 0.0))),
                black=tuple(map(float, d.get("black") or (0.0, 0.0))),
                clear=float(d.get("clear_mm", 0.0)), note=d.get("note", ""))


def same(a: Optional[dict], b: Optional[dict], tol: float = 1e-3) -> bool:
    """Two records' marks (dicts) describe the same geometry (to `tol` mm)."""
    ka = (a or {}).get("kind", "none")
    kb = (b or {}).get("kind", "none")
    if ka != kb:
        return False
    if ka == "none":
        return True
    for key in ("cut", "tool", "solid", "outline"):
        pa, pb = a.get(key) or [], b.get(key) or []
        if len(pa) != len(pb):
            return False
        if any(abs(x - y) > tol for p, q in zip(pa, pb) for x, y in zip(p, q)):
            return False
    return abs(float(a.get("leg_mm", 0)) - float(b.get("leg_mm", 0))) <= tol
