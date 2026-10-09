"""ArUco Extrude heights and locating pins: the pure model (stdlib only).

Zach, 2026-10-08: "please use same language as extrude... there is only the
height and then an offset from that surface I need to control. For both of
those support positive and negative values."

Every number here is in mm along the normal of the picked face / sketch
plane, 0 = that plane, + = out of it (towards the camera). Each colour is
one extrude, like Fusion's Extrude with "Start: Offset":

    white   Offset, Height   the white tile (White + black) or the white
                             pattern (White only)
    black   Offset, Height   the black cells (White + black, Black only)

The slab of a colour runs from Offset to Offset + Height; a negative Height
extrudes the other way (into what is under). The defaults are a 2 mm white
tile and the black extruded 0.6 mm DOWN from the white top (a flush inlay);
Black only: 0.6 mm down from the face.

What the node is made of follows from where the slabs are (`plan`):

    cut the white where the black overlaps it -- from the black's bottom up
        to the white top, so a black whose top is below the white top sits
        at the bottom of an open pocket ("recessed") instead of buried;
    cut the body under where a slab goes below the face;
    join the tile to the body under when asked (Operation: Join).

The tag frame (TagSpec / the record) keeps describing the pattern layer
around ONE surface: the white top (White + black), else the face (or the
pattern's own top when there is no body under). depth = how far the
pattern reaches below that surface, raise = how far it stands above it,
recess = how far its top stays below it. OpenCV sees the higher of the two
tops: surface + raise.

Locating pins (`pin_outlines`): a tag printed on its own (White + black, New
body) gets pins hanging from the tile's underside, inside the tile's
footprint, and the body under gets matching holes, the pin grown by the
tolerance all round and deeper by it. Triangle: one right triangle at the
tag's top-left corner (OpenCV corner 0, where the PDF puts its corner mark)
-- it keys the rotation on its own. Round: two round pins at the two top
corners (one round pin would let the tag turn).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

MODES = ("both", "black", "white")
OPS = ("join", "new")

WHITE_OFFSET = 0.0          # mm
WHITE_HEIGHT = 2.0          # the white tile
BLACK_HEIGHT = -0.6         # the black: 0.6 mm down from where it starts
PATTERN_HEIGHT = -0.6       # Black only / White only: a flush 0.6 mm inlay
WHITE_MIN_MM = 0.1          # |Height| of the white
BLACK_MIN_MM = 0.05         # |Height| of the black
LIMIT_MM = 50.0             # |Offset|, |Height|
EPS_MM = 1e-4               # "level with": 0.1 um

PIN_SHAPES = ("none", "triangle", "round")
PIN_SIZE_MM = 3.0           # triangle leg / circle diameter
PIN_LENGTH_MM = 2.0
PIN_TOL_MM = 0.15
PIN_MARGIN_MM = 1.0         # from the tile's edge to the pin
PIN_GAP_MM = 1.0            # between the two round pins at least
PIN_LIMITS = {"pin_size": (0.5, 20.0), "pin_length": (0.2, 20.0), "pin_tol": (0.0, 1.0)}
HOLE_OVERSHOOT_MM = 0.01    # the hole tool pokes this far out of the face

NUMBER_KEYS = ("white_offset", "white_height", "black_offset", "black_height")
PIN_KEYS = ("pin_size", "pin_length", "pin_tol")

Slab = Tuple[float, float]


def slab(offset: float, height: float) -> Slab:
    """(low, high) of an extrude from `offset`, `height` further."""
    a, b = float(offset), float(offset) + float(height)
    return (a, b) if a <= b else (b, a)


def snap_height(h: float, minimum: float, prev: Optional[float] = None) -> float:
    """A Height kept out of the dead zone around 0 and inside the limits,
    keeping its sign (0 itself takes the sign of `prev`, else +). What the
    dialog writes back into the field, so field, arrow and geometry agree."""
    h = float(h)
    if abs(h) < minimum:
        sign = -1.0 if (h < 0 or (h == 0 and prev is not None and prev < 0)) else 1.0
        h = sign * minimum
    return max(-LIMIT_MM, min(LIMIT_MM, h))


def clamp_offset(v: float) -> float:
    return max(-LIMIT_MM, min(LIMIT_MM, float(v)))


def white_top(mode: str, n: Dict[str, float]) -> float:
    """Where the black offset sits until it is set by hand: the white top
    (White + black), the face (Black only)."""
    if mode == "both":
        return slab(n["white_offset"], n["white_height"])[1]
    return 0.0


def default_numbers(mode: str = "both") -> Dict[str, float]:
    """Offsets and heights (mm) for a fresh tag of `mode`."""
    n = {"white_offset": WHITE_OFFSET, "white_height": WHITE_HEIGHT,
         "black_offset": 0.0, "black_height": BLACK_HEIGHT}
    if mode == "white":
        n["white_height"] = PATTERN_HEIGHT
    n["black_offset"] = white_top(mode, n)
    return n


def pin_defaults() -> Dict[str, float]:
    return {"pin_size": PIN_SIZE_MM, "pin_length": PIN_LENGTH_MM, "pin_tol": PIN_TOL_MM}


def from_legacy(mode: str, height: float, thickness: float, above: float) -> Dict[str, float]:
    """The numbers of a node made before 2026-10-08 (parameters height,
    thickness, above) as Offset / Height pairs: the same geometry. The old
    pattern layer ran `thickness` down from its top, `above` over its
    surface (the white top, or the face), stretched down to touch."""
    a = max(0.0, float(above))
    t = max(float(thickness), a)
    depth, rise = t - a, a
    surface = float(height) if mode == "both" else 0.0
    if rise <= 0.0:
        off, h = surface, -depth                  # inlaid from the surface
    elif depth <= 0.0:
        off, h = surface, rise                    # stands on the surface
    else:
        off, h = surface - depth, depth + rise    # straddles it: up from the bottom
    n = default_numbers(mode)
    if mode == "white":
        n["white_offset"], n["white_height"] = off, h
    else:
        n["black_offset"], n["black_height"] = off, h
    if mode == "both":
        n["white_offset"], n["white_height"] = 0.0, float(height)
    return n


# ------------------------------------------------------------------ layers

@dataclass
class Layers:
    """Where each colour is (mm from the face) and what the record says."""
    mode: str
    white: Optional[Slab]        # the white extrude: tile (both) / pattern (white)
    black: Optional[Slab]        # the black cells (both, black)
    pattern: Slab                # the layer with the marker's pattern in it
    surface: float               # the tag frame's plane (TagSpec / record origin)
    depth: float                 # the pattern below the surface
    raise_: float                # the pattern above the surface
    recess: float                # the pattern's top below the surface
    gap: float                   # the pattern's bottom ABOVE the surface (floats)

    @property
    def detector(self) -> float:
        """The plane OpenCV measures: the higher of the two tops."""
        return self.surface + self.raise_

    @property
    def thickness(self) -> float:
        return self.pattern[1] - self.pattern[0]


def layers(mode: str, n: Dict[str, float], has_body: bool = True) -> Layers:
    if mode not in MODES:
        raise ValueError(f"mode {mode!r}")
    white = slab(n["white_offset"], n["white_height"]) if mode in ("both", "white") else None
    black = slab(n["black_offset"], n["black_height"]) if mode in ("both", "black") else None
    pattern = white if mode == "white" else black
    if pattern[1] - pattern[0] <= 0.0:
        raise ValueError("a colour needs some height")
    if mode == "both" and white[1] - white[0] <= 0.0:
        raise ValueError("the white needs some height")
    if mode == "both":
        surface = white[1]
    elif has_body:
        surface = 0.0
    else:
        surface = pattern[1]         # nothing under it: the pattern's own top
    lo, hi = pattern
    return Layers(mode=mode, white=white, black=black, pattern=pattern, surface=surface,
                  depth=max(0.0, surface - lo), raise_=max(0.0, hi - surface),
                  recess=max(0.0, surface - hi), gap=max(0.0, lo - surface))


# -------------------------------------------------------------------- plan

@dataclass
class Plan:
    """What one ArUco Extrude node is made of, in timeline order.

    parts: the base-feature roles, in order --
        "tile"     the white tile (both)          -> joined to the body (join)
        "pin"      the locating pins               -> joined to the tile
        "pocket"   cut tool: the white's pocket    -> cut from the tile / joined body
        "pattern"  the black cells (white pattern in White only)
        "recess"   cut tool: into the body under  -> cut from the body under
    """
    mode: str
    op: Optional[str]                         # "join" / "new" (both), else None
    layers: Layers
    parts: List[str]
    pocket: Optional[Slab] = None             # the pattern's footprint, cut into the white
    recess: List[Tuple[str, Slab]] = field(default_factory=list)   # (what, slab) into the body
    pin: Optional[Slab] = None                # the pins' slab (under the tile)
    notes: List[str] = field(default_factory=list)                 # one line each

    @property
    def structure(self) -> Tuple:
        return (self.mode, self.op, tuple(self.parts))


def effective_op(mode: str, op: str, has_body: bool) -> Optional[str]:
    if mode != "both":
        return None
    return "join" if (op == "join" and has_body) else "new"


def pin_applies(mode: str, op: str, has_body: bool) -> bool:
    """Pins only on a tag printed on its own: White + black as a new body."""
    return effective_op(mode, op, has_body) == "new"


def _mm(v: float) -> str:
    return f"{abs(v):.2f} mm"


def plan(mode: str, op: str, has_body: bool, n: Dict[str, float],
         pin_shape: str = "none") -> Plan:
    """The node's parts and the one-line notes the dialog shows (all of
    them allowed: recessed, floating, poking out, a gap)."""
    L = layers(mode, n, has_body)
    eop = effective_op(mode, op, has_body)
    parts: List[str] = []
    notes: List[str] = []
    out = Plan(mode=mode, op=eop, layers=L, parts=parts, notes=notes)
    eps = EPS_MM
    if mode == "both":
        w0, w1 = L.white
        b0, b1 = L.black
        parts.append("tile")
        pins = pin_shape in ("triangle", "round") and eop == "new"
        if pins:
            length = float(n.get("pin_length", PIN_LENGTH_MM))
            out.pin = (w0 - length, w0)
            parts.append("pin")
        if b0 < w1 - eps:
            out.pocket = (b0, w1)
            parts.append("pocket")
        parts.append("pattern")
        if eop == "new" and has_body:
            if w0 < -eps:
                out.recess.append(("tile", (w0, 0.0)))
            if b0 < -eps:
                out.recess.append(("pattern", (b0, 0.0)))
            if pins:
                tol = float(n.get("pin_tol", PIN_TOL_MM))
                bottom = out.pin[0] - tol
                if bottom < -eps:
                    out.recess.append(("hole", (bottom, HOLE_OVERSHOOT_MM)))
                else:
                    notes.append(f"the pin stops {_mm(bottom)} above the face: no hole")
            if out.recess:
                parts.append("recess")
        # one line per thing out of the ordinary
        if w0 > eps and has_body:
            notes.append(f"the white starts {_mm(w0)} above the face: a gap under the tile")
        elif w0 < -eps and has_body:
            notes.append(f"the white goes {_mm(w0)} into the body"
                         + (" (joined)" if eop == "join" else ": the body is cut to fit"))
        if b0 > w1 + eps:
            notes.append(f"the black floats {_mm(b0 - w1)} above the white top")
        elif b1 < w1 - eps:
            notes.append(f"the black is recessed {_mm(w1 - b1)} below the white top")
        if b0 < w0 - eps and not (eop == "join" or has_body):
            notes.append(f"the black pokes {_mm(w0 - b0)} out under the tile")
        elif b0 < -eps and eop == "join":
            notes.append(f"the black goes {_mm(b0)} into the body under the tile")
        if pins and (b1 > w1 + eps or b1 < w1 - eps):
            notes.append("a pinned tag prints pattern-down, pins up: end the black "
                         "level with the white top")
    else:
        p0, p1 = L.pattern
        colour = "black" if mode == "black" else "white"
        if has_body and p0 < -eps:
            out.recess.append(("pattern", (p0, 0.0)))
        parts.append("pattern")
        if out.recess:
            parts.append("recess")
        if has_body:
            if p0 > eps:
                notes.append(f"the {colour} floats {_mm(p0)} above the face")
            elif p1 < -eps:
                notes.append(f"the {colour} is recessed {_mm(p1)} below the face")
    return out


def describe(p: Plan) -> List[str]:
    """Plain lines for the dialog: each colour from .. to, in Extrude's words."""
    def span(s):
        return f"{s[0]:+.2f} to {s[1]:+.2f} mm"
    out = []
    L = p.layers
    if L.white is not None:
        what = "white tile" if p.mode == "both" else "white"
        out.append(f"{what} {span(L.white)}")
    if L.black is not None:
        out.append(f"black {span(L.black)}")
    if L.raise_ > EPS_MM:
        out.append(f"the camera sees the pattern's top, {L.raise_:.2f} mm over the "
                   + ("white" if p.mode == "both" else "face"))
    return out


# -------------------------------------------------------------------- pins

def _area2(pts: Sequence[Tuple[float, float]]) -> float:
    return sum(pts[i - 1][0] * pts[i][1] - pts[i][0] * pts[i - 1][1] for i in range(len(pts)))


def _corner_inset(cut: float, margin: float, half: float = 0.0) -> float:
    """How far in from the tile's top-left corner, along both edges, a pin's
    corner point (half = 0) or centre (half = a round pin's radius) goes: it
    keeps `margin` from the edges and, when that corner is cut at 45 deg
    with legs `cut` ("Chamfer top-left corner"), from the cut line too.
    (= aruco_chamfer.pin_inset; this module stays stdlib only.)"""
    d = margin + half
    if cut > 0:
        d = max(d, (cut + math.sqrt(2.0) * (margin + half)) / 2.0)
    return d


def pin_outlines(shape: str, size: float, tile_w: float, tile_h: float,
                 margin: float = PIN_MARGIN_MM, cut: float = 0.0) -> List[dict]:
    """The pins' outlines (mm) in the tag frame: origin the tile centre, x =
    image right, y = image up. {"kind": "poly", "pts": [...] CCW} or
    {"kind": "circle", "c": (u, v), "r": r}. Raises ValueError when they do
    not fit inside the tile with `margin` all round. `cut`: the tile's
    top-left corner is cut at 45 deg with these legs (mm) -- the pin there
    moves in along the diagonal to keep `margin` from the cut."""
    s = float(size)
    cut = max(0.0, float(cut or 0.0))
    if shape not in ("triangle", "round"):
        return []
    if s <= 0:
        raise ValueError("the pin needs a size")
    if shape == "triangle":
        d = _corner_inset(cut, margin)
        cx, cy = -tile_w / 2.0 + d, tile_h / 2.0 - d          # top-left, inset
        if s + d > min(tile_w, tile_h) / 2.0:
            raise ValueError(f"a {s:g} mm triangle pin does not fit in the corner of a "
                             f"{tile_w:.1f} x {tile_h:.1f} mm tile"
                             + (" with its corner cut" if cut > 0 else ""))
        # the right angle in the corner, legs along the top and left edges
        pts = [(cx, cy), (cx, cy - s), (cx + s, cy)]
        if _area2(pts) < 0:
            pts.reverse()
        return [{"kind": "poly", "pts": pts}]
    r = s / 2.0
    d = _corner_inset(cut, margin, r)                         # the left pin's centre
    if tile_w - margin - d - 3.0 * r < PIN_GAP_MM or d + r > tile_h / 2.0:
        raise ValueError(f"two {s:g} mm round pins do not fit along the top of a "
                         f"{tile_w:.1f} x {tile_h:.1f} mm tile"
                         + (" with its corner cut" if cut > 0 else ""))
    return [{"kind": "circle", "c": (-tile_w / 2.0 + d, tile_h / 2.0 - d), "r": r},
            {"kind": "circle", "c": (tile_w / 2.0 - margin - r, tile_h / 2.0 - margin - r),
             "r": r}]


def _line_meet(p, d, q, e):
    """Where p + t d meets q + s e (2D lines), or None when parallel."""
    den = d[0] * e[1] - d[1] * e[0]
    if abs(den) < 1e-15:
        return None
    t = ((q[0] - p[0]) * e[1] - (q[1] - p[1]) * e[0]) / den
    return (p[0] + t * d[0], p[1] + t * d[1])


def grow(outline: dict, d: float) -> dict:
    """The outline grown by `d` all round: a circle's radius + d; a convex
    polygon's every edge moved d outwards (mitred corners), so the gap to
    the original is exactly d along every edge's normal."""
    if outline["kind"] == "circle":
        return {"kind": "circle", "c": tuple(outline["c"]), "r": outline["r"] + d}
    pts = list(outline["pts"])
    if _area2(pts) < 0:
        pts.reverse()
    lines = []
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(dx, dy)
        nx, ny = dy / ln, -dx / ln                   # outward for a CCW polygon
        lines.append(((a[0] + nx * d, a[1] + ny * d), (dx, dy)))
    out = []
    for i in range(len(lines)):
        p, dv = lines[i - 1]
        q, ev = lines[i]
        hit = _line_meet(p, dv, q, ev)
        out.append(hit if hit is not None else q)
    return {"kind": "poly", "pts": out}


def outline_points(outline: dict, segments: int = 32) -> List[Tuple[float, float]]:
    """A polygon for drawing (a circle as `segments` points), CCW."""
    if outline["kind"] == "poly":
        return list(outline["pts"])
    (u, v), r = outline["c"], outline["r"]
    return [(u + r * math.cos(2 * math.pi * k / segments),
             v + r * math.sin(2 * math.pi * k / segments)) for k in range(segments)]


def edge_gap(a: dict, b: dict) -> float:
    """The smallest distance from `a`'s edges to `b`'s, for b = grow(a, d):
    the tolerance measured back (polygons: along each edge's normal)."""
    if a["kind"] == "circle":
        return b["r"] - a["r"]
    gaps = []
    pa, pb = a["pts"], b["pts"]
    if _area2(pa) < 0:
        pa = list(reversed(pa))
    for i in range(len(pa)):
        p0, p1 = pa[i], pa[(i + 1) % len(pa)]
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        ln = math.hypot(dx, dy)
        nx, ny = dy / ln, -dx / ln
        # how far the grown outline reaches past this edge (its own edge)
        gaps.append(max((q[0] - p0[0]) * nx + (q[1] - p0[1]) * ny for q in pb))
    return min(gaps)
