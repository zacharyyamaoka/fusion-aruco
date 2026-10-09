"""ArUco Extrude's Origin: where OpenCV's frame lands on the face (pure;
standard library only).

Zach, 2026-10-09: "100% I always want the frame to match perfectly OpenCV.
If OpenCV doesn't support it I don't want to either." A tag's Joint Origin
IS the frame OpenCV reports (markers.detector_frame):

    single marker   its centre, x right, y up, z out of the face
    board           GridBoard / CharucoBoard: its top-left outer corner,
                    x right, y down, z INTO the board

The only freedom OpenCV leaves is how the pattern lies on the face, so the
Origin choice turns it: `turn`, degrees CCW about the face normal, in the
frame the tag was laid out in (x along the face, y up as he sees it):

    single          4 x 90 deg about its centre, named by where the
                    marker's up points (Up: top / right / bottom / left)
    square board    4 x 90 deg, named by the face corner OpenCV's origin
                    lands on (Top left / Top right / Bottom right / Bottom left)
    rect. board     the same 4 corners. 0 and 180 deg are plain turns; 90
                    and 270 deg turn the board AND swap its cols / rows (a
                    4 x 2 is laid as a 2 x 4 on its side): the same
                    footprint, the same marker size and count -- OpenCV's
                    own 2 x 4 board, only which squares hold which ids changes

    turn   a board's origin lands on   a single marker's up points to
       0   top left                    top
     270   top right                   right
     180   bottom right                bottom
      90   bottom left                 left

"Rotate" steps through them clockwise, in that order (TURNS_CW). z never
flips: OpenCV fixes it (out of a single marker, into a board).

Nodes made before 2026-10-09 refused 90 / 270 deg on a rectangular board
(kept as laid out, with a note). A node whose settings lack "origin_v" =
ORIGIN_RULE keeps that rule (`effective`), so it keeps its orientation; the
next edit that sets its Origin moves it to this one.
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

TURNS_CW = (0, 270, 180, 90)          # the "Rotate" order: clockwise, as seen
ORIGIN_RULE = 2                       # settings "origin_v" of a node made since 2026-10-09
_CORNER = {0: "top left", 270: "top right", 180: "bottom right", 90: "bottom left"}
_UP = {0: "top", 270: "right", 180: "bottom", 90: "left"}

Vec2 = Tuple[float, float]


def raw_turn(turn) -> int:
    """A stored turn as whole degrees in [0, 360)."""
    try:
        return int(round(float(turn or 0))) % 360
    except (TypeError, ValueError):
        return 0


def norm(turn) -> int:
    """A quarter turn (0, 90, 180, 270); anything else is 0."""
    t = raw_turn(turn)
    return t if t % 90 == 0 else 0


def effective(turn, layout: str, cols: int = 1, rows: int = 1,
              rule: Optional[int] = ORIGIN_RULE) -> Tuple[int, str]:
    """(the turn a node gets, a note when that is not the one it asked
    for). `rule`: its settings' "origin_v" (None for a node made before
    2026-10-09, which kept a rectangular board's 90 / 270 deg as laid out)."""
    t = raw_turn(turn)
    if t % 90:
        return 0, f"origin turned {t}° is not a quarter turn: kept as laid out"
    if (rule != ORIGIN_RULE and t in (90, 270) and layout != "single"
            and int(cols) != int(rows)):
        return 0, (f"origin turned {t}° needs a square footprint: kept as laid out "
                   "(a tag made before 2026-10-09; pick its Origin again to turn it)")
    return t, ""


def transposes(layout: str, turn) -> bool:
    """Does this Origin lay a board with its cols / rows swapped? (90 / 270
    deg; a square board swaps nothing it would notice.)"""
    return layout != "single" and norm(turn) in (90, 270)


def built(layout: str, turn, cols: int, rows: int) -> Tuple[int, int]:
    """(cols, rows) of OpenCV's board as built, for a layout fitted as
    cols x rows on the face."""
    return (int(rows), int(cols)) if transposes(layout, turn) else (int(cols), int(rows))


def rotate_cw(turn) -> int:
    """The next Origin clockwise ("Rotate"): the pattern turns 90 deg
    clockwise as seen on the face, its top-left to the top-right."""
    return (norm(turn) - 90) % 360


def corner(turn) -> str:
    """The face corner a board's origin lands on: 'top left', ..."""
    return _CORNER[norm(turn)]


def up(turn) -> str:
    """Where a single marker's up points on the face: 'top', ..."""
    return _UP[norm(turn)]


def label(layout: str, turn) -> str:
    """The dialog's name for one Origin."""
    if layout == "single":
        return f"Up: {up(turn)}"
    return corner(turn)[0].upper() + corner(turn)[1:]


def items(layout: str) -> List[Tuple[str, int]]:
    """[(label, turn)] in the "Rotate" order."""
    return [(label(layout, t), t) for t in TURNS_CW]


def turn_of(layout: str, name: str) -> Optional[int]:
    """The turn a dialog label means (either layout's words), or None."""
    for kind in (layout, "grid" if layout == "single" else "single"):
        for lbl, t in items(kind):
            if lbl == name:
                return t
    return None


def _rot(v: Vec2, deg: int) -> Vec2:
    c = round(math.cos(math.radians(deg)))
    s = round(math.sin(math.radians(deg)))
    return (c * v[0] - s * v[1], s * v[0] + c * v[1])


def opencv_in_layout(layout: str, turn, w: float, h: float) -> Tuple[Vec2, Vec2, Vec2, int]:
    """OpenCV's frame for this Origin in the frame the tag was laid out in
    (x along the face, y up, z out; the pattern's centre at 0): (origin,
    x, y, z sign). w x h: the pattern's footprint AS LAID OUT. Worked out
    from OpenCV's conventions alone: a test's yardstick, and the dialog's."""
    t = norm(turn)
    xt, yt = _rot((1.0, 0.0), t), _rot((0.0, 1.0), t)
    if layout == "single":
        return (0.0, 0.0), xt, yt, 1
    wb, hb = (w, h) if t in (0, 180) else (h, w)          # the board's own extent
    o = (-wb / 2.0 * xt[0] + hb / 2.0 * yt[0], -wb / 2.0 * xt[1] + hb / 2.0 * yt[1])
    return o, xt, (-yt[0], -yt[1]), -1


def describe(layout: str, turn, cols: int = 1, rows: int = 1) -> str:
    """The dialog's Joint Origin line. cols x rows: the board AS BUILT."""
    if layout == "single":
        return (f"Joint Origin = OpenCV's frame: the marker's centre, its up to the face's "
                f"{up(turn)} (x right, y up, z out of the face)")
    out = (f"Joint Origin = OpenCV's frame: the board's origin on the face's {corner(turn)} "
           "corner (x right, y down, z into the face)")
    if transposes(layout, turn) and int(cols) != int(rows):
        out += (f"; OpenCV's board is {cols} × {rows}: the {rows} × {cols} layout on its side "
                "(same footprint and markers, ids in OpenCV's order)")
    return out


def corners_of(layout: str) -> Sequence[str]:
    """The four names, in the "Rotate" order (tests, docs)."""
    return [label(layout, t) for t in TURNS_CW]
