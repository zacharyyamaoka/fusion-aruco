"""ArUco grid boards and ChArUco boards, drawn and framed without OpenCV.

A board is several markers whose relative placement is known, so a detector
can solve ONE pose from all of them. This module produces the same ink
OpenCV's `board.generateImage` does, in metres, and says exactly which frame
OpenCV will report a pose in — the piece that is otherwise learned by
debugging a robot that is 180 degrees wrong.

### What a BoardSpec means (mirrors OpenCV)

    single    one marker, id = first_id, side = marker_length_m
    grid      GridBoard(size=(cols, rows), markerLength, markerSeparation)
              cols x rows markers, ids first_id.. row-major from the TOP-LEFT
    charuco   CharucoBoard(size=(squaresX=cols, squaresY=rows), squareLength,
              markerLength); cols x rows SQUARES, a marker in every white
              square, ids first_id.. row-major from the top-left. The
              top-left square is black — except with `legacy` (OpenCV's
              setLegacyPattern) AND an even row count, where it is white.

### Coordinates

`black_rects_m` and `marker_corners_m` use the CENTRE frame: origin at the
centre of the black-pattern extent, +x right, +y UP (towards the top row of
the image as generateImage draws it), +z out of the board towards the viewer.

### The frame OpenCV reports (`opencv_frame`), measured on cv2 5.0.0

    grid / charuco   origin at the board's TOP-LEFT outer corner, +x right,
                     +y DOWN the image, +z INTO the board (x cross y).
                     In the centre frame: rotation diag(1, -1, -1),
                     origin (-w/2, +h/2, 0).
    single           origin at the marker centre, +x right, +y up, +z out
                     of the marker: the identity.

Evidence (cv2 5.0.0; tools/verify_aruco_boards.py re-checks all of it):
  * GridBoard((3, 2), 0.04, 0.01, DICT_4X4_50, ids 10..15).getObjPoints():
    id 10 = (0,0,0) (.04,0,0) (.04,.04,0) (0,.04,0); id 13, the first of the
    second row, starts at (0, .05, 0). generateImage draws id 10 top-left,
    id 12 top-right, id 13 bottom-left, so objPoints y grows DOWN the image.
    (OpenCV <= 4.6 put the origin bottom-left with y up; 4.7 flipped it.)
  * CharucoBoard((5, 4), 0.04, 0.03): marker 0 at (.045, .005) — in square
    (col 1, row 0), because (0, 0) is black; getChessboardCorners() starts at
    (.04, .04), row-major; getRightBottomCorner() = (.2, .16). The origin is
    the board's OUTER corner, not the first inner chessboard corner.
  * Single: cv2 5.0 has no estimatePoseSingleMarkers; the standard object
    points (OpenCV's tutorial, and the order SOLVEPNP_IPPE_SQUARE requires)
    are (-L/2, L/2, 0) (L/2, L/2, 0) (L/2, -L/2, 0) (-L/2, -L/2, 0) for the
    detected corners TL, TR, BR, BL.
  * Synthetic camera renders of these boards at a known pose, detected and
    solved with solvePnP, recover T_cam_centre @ opencv_frame(spec).

Boards are never scaled: every length here is the physical length.
"""
from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass
from typing import Dict, List, Tuple

from . import aruco_dict
from .aruco_dict import _png_chunk

LAYOUTS = ("single", "grid", "charuco")

Rect = Tuple[float, float, float, float]


@dataclass
class BoardSpec:
    layout: str                      # "single" | "grid" | "charuco"
    dictionary: str
    first_id: int
    marker_length_m: float           # black square side incl. its border
    rows: int = 1
    cols: int = 1
    separation_m: float = 0.0        # grid: gap between markers
    square_length_m: float = 0.0     # charuco: chessboard square side
    legacy: bool = False             # charuco: OpenCV setLegacyPattern(True)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _num(v, what) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError("%s must be a number, got %r" % (what, v))
    if not math.isfinite(f):
        raise ValueError("%s must be finite, got %r" % (what, v))
    return f


def _charuco_black(x: int, y: int, rows: int, legacy: bool) -> bool:
    """Is chessboard square (col x, row y from the top) black? OpenCV's rule."""
    if legacy and rows % 2 == 0:
        return (y + 1) % 2 == x % 2
    return y % 2 == x % 2


def check_spec(spec: BoardSpec) -> dict:
    """Validate a spec; returns aruco_dict.dictionary_info. Raises ValueError
    naming the problem — a board OpenCV would refuse, or one whose ids run
    off the end of the dictionary."""
    if spec.layout not in LAYOUTS:
        raise ValueError("layout %r is not one of %s" % (spec.layout, LAYOUTS))
    info = aruco_dict.dictionary_info(spec.dictionary)
    if not _is_int(spec.first_id) or spec.first_id < 0:
        raise ValueError("first_id must be an integer >= 0, got %r" % (spec.first_id,))
    if not (_is_int(spec.rows) and _is_int(spec.cols) and spec.rows >= 1 and spec.cols >= 1):
        raise ValueError("rows and cols must be integers >= 1, got %r x %r"
                         % (spec.rows, spec.cols))
    length = _num(spec.marker_length_m, "marker_length_m")
    if length <= 0:
        raise ValueError("marker_length_m must be > 0, got %r" % (spec.marker_length_m,))
    if spec.layout == "single" and (spec.rows, spec.cols) != (1, 1):
        raise ValueError("a single marker is 1 x 1, got %d x %d — use layout "
                         "'grid'" % (spec.cols, spec.rows))
    if spec.layout == "grid" and _num(spec.separation_m, "separation_m") <= 0:
        # OpenCV's GridBoard asserts markerSeparation > 0 (cv2 5.0.0).
        raise ValueError("separation_m must be > 0 for a grid (OpenCV refuses "
                         "touching markers), got %r" % (spec.separation_m,))
    if spec.layout == "charuco":
        if spec.rows < 2 or spec.cols < 2:
            raise ValueError("a ChArUco board needs at least 2 x 2 squares, got "
                             "%d x %d" % (spec.cols, spec.rows))
        if _num(spec.square_length_m, "square_length_m") <= length:
            raise ValueError("square_length_m (%r) must be larger than "
                             "marker_length_m (%r) — the marker sits inside a "
                             "white square" % (spec.square_length_m, length))
    last = spec.first_id + _count(spec) - 1
    if last >= info["capacity"]:
        raise ValueError("%s has ids 0..%d; this board needs %d..%d"
                         % (info["name"], info["capacity"] - 1, spec.first_id, last))
    return info


def _count(spec: BoardSpec) -> int:
    if spec.layout == "grid":
        return spec.rows * spec.cols
    if spec.layout == "charuco":
        return sum(1 for y in range(spec.rows) for x in range(spec.cols)
                   if not _charuco_black(x, y, spec.rows, spec.legacy))
    return 1


def marker_count(spec: BoardSpec) -> int:
    check_spec(spec)
    return _count(spec)


def ids(spec: BoardSpec) -> List[int]:
    """Marker ids in OpenCV's own order (row-major from the top-left)."""
    return list(range(spec.first_id, spec.first_id + marker_count(spec)))


def extent_m(spec: BoardSpec) -> Tuple[float, float]:
    """(w, h) of the black pattern: the markers' outer borders for single and
    grid, the full chessboard for charuco. Quiet zone not included."""
    check_spec(spec)
    L = float(spec.marker_length_m)
    if spec.layout == "grid":
        s = float(spec.separation_m)
        return spec.cols * L + (spec.cols - 1) * s, spec.rows * L + (spec.rows - 1) * s
    if spec.layout == "charuco":
        sq = float(spec.square_length_m)
        return spec.cols * sq, spec.rows * sq
    return L, L


def _placements(spec: BoardSpec) -> List[Tuple[int, float, float]]:
    """(id, left, top) of each marker in OpenCV's board frame (origin top-left,
    y down) — the same arithmetic as OpenCV's objPoints."""
    L = float(spec.marker_length_m)
    out = []
    if spec.layout == "grid":
        pitch = L + float(spec.separation_m)
        for y in range(spec.rows):
            for x in range(spec.cols):
                out.append((spec.first_id + len(out), x * pitch, y * pitch))
    elif spec.layout == "charuco":
        sq = float(spec.square_length_m)
        inset = (sq - L) / 2.0
        for y in range(spec.rows):
            for x in range(spec.cols):
                if not _charuco_black(x, y, spec.rows, spec.legacy):
                    out.append((spec.first_id + len(out), x * sq + inset, y * sq + inset))
    else:
        out.append((spec.first_id, 0.0, 0.0))
    return out


def object_points(spec: BoardSpec) -> Dict[int, List[Tuple[float, float, float]]]:
    """{id: 4 corners} in OpenCV's REPORTED frame, corner order TL, TR, BR, BL
    as detectMarkers returns them — what board.getObjPoints() holds (and, for
    a single marker, the standard IPPE_SQUARE object points)."""
    check_spec(spec)
    L = float(spec.marker_length_m)
    if spec.layout == "single":
        h = L / 2.0
        return {spec.first_id: [(-h, h, 0.0), (h, h, 0.0), (h, -h, 0.0), (-h, -h, 0.0)]}
    return {mid: [(x, y, 0.0), (x + L, y, 0.0), (x + L, y + L, 0.0), (x, y + L, 0.0)]
            for mid, x, y in _placements(spec)}


def charuco_corners(spec: BoardSpec) -> List[Tuple[float, float, float]]:
    """Inner chessboard corners in OpenCV's frame, in OpenCV's id order —
    board.getChessboardCorners()."""
    check_spec(spec)
    if spec.layout != "charuco":
        raise ValueError("charuco_corners needs a charuco board, not %r" % spec.layout)
    sq = float(spec.square_length_m)
    return [((x + 1) * sq, (y + 1) * sq, 0.0)
            for y in range(spec.rows - 1) for x in range(spec.cols - 1)]


def marker_corners_m(spec: BoardSpec) -> Dict[int, List[Tuple[float, float]]]:
    """{id: 4 corners (x, y)} in the CENTRE frame (y up), order TL, TR, BR, BL
    — for overlays and for checking a detection against the design."""
    w, h = extent_m(spec)
    L = float(spec.marker_length_m)
    out = {}
    for mid, x, y in _placements(spec):
        x0, y0 = x - w / 2.0, h / 2.0 - y
        out[mid] = [(x0, y0), (x0 + L, y0), (x0 + L, y0 - L), (x0, y0 - L)]
    return out


def black_rects_m(spec: BoardSpec) -> List[Rect]:
    """The board's black ink as disjoint rectangles (x0, y0, x1, y1), x0 < x1,
    y0 < y1, metres, CENTRE frame (+y up). Their union is exactly the black of
    OpenCV's board.generateImage: every marker with its 1-module border
    (merged per marker by aruco_dict.black_rects), plus the black chessboard
    squares for charuco (which touch only at corners, so stay separate)."""
    info = check_spec(spec)
    w, h = extent_m(spec)
    hw, hh = w / 2.0, h / 2.0
    out: List[Rect] = []
    if spec.layout == "charuco":
        sq = float(spec.square_length_m)
        for y in range(spec.rows):
            for x in range(spec.cols):
                if _charuco_black(x, y, spec.rows, spec.legacy):
                    out.append((x * sq - hw, hh - (y + 1) * sq, (x + 1) * sq - hw, hh - y * sq))
    module = float(spec.marker_length_m) / info["modules"]
    for mid, left, top in _placements(spec):
        for x, y, rw, rh in aruco_dict.black_rects(spec.dictionary, mid):
            out.append((left + x * module - hw, hh - (top + (y + rh) * module),
                        left + (x + rw) * module - hw, hh - (top + y * module)))
    return out


def opencv_frame(spec: BoardSpec) -> List[float]:
    """Row-major 4x4: the pose of the frame OpenCV reports for this target,
    expressed in the centre frame (p_centre = T @ p_opencv). See the module
    docstring for the measured conventions."""
    w, h = extent_m(spec)
    if spec.layout == "single":
        return [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
                0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
    return [1.0, 0.0, 0.0, -w / 2.0,
            0.0, -1.0, 0.0, h / 2.0,
            0.0, 0.0, -1.0, 0.0,
            0.0, 0.0, 0.0, 1.0]


# ------------------------------------------------------------------ images


def _edge(v: float) -> int:
    """Pixel index where an edge at continuous coordinate v starts covering:
    pixel i is ink when its centre i + 0.5 lies in [start, end)."""
    return int(math.ceil(v - 0.5))


def board_png(spec: BoardSpec, px_per_m: float = 10000.0, quiet_m: float = 0.0) -> bytes:
    """8-bit greyscale PNG of the board at `px_per_m` (default 10 px/mm), with
    a white margin of `quiet_m` on every side, and a pHYs chunk so the file
    carries its physical size. Pixels are ink when their centre is inside a
    black rectangle; pick px_per_m so a module is a whole number of pixels
    for an image that matches OpenCV's generateImage pixel for pixel."""
    s = _num(px_per_m, "px_per_m")
    if s <= 0:
        raise ValueError("px_per_m must be > 0")
    if _num(quiet_m, "quiet_m") < 0:
        raise ValueError("quiet_m must be >= 0")
    w, h = extent_m(spec)
    q = int(round(quiet_m * s))
    width, height = int(round(w * s)) + 2 * q, int(round(h * s)) + 2 * q
    spans = []
    for x0, y0, x1, y1 in black_rects_m(spec):
        c0, c1 = q + _edge((x0 + w / 2.0) * s), q + _edge((x1 + w / 2.0) * s)
        r0, r1 = q + _edge((h / 2.0 - y1) * s), q + _edge((h / 2.0 - y0) * s)
        c0, c1 = max(c0, 0), min(c1, width)
        r0, r1 = max(r0, 0), min(r1, height)
        if c1 > c0 and r1 > r0:
            spans.append((r0, r1, c0, c1))
    # Rows between consecutive span edges are identical: build each band once.
    cuts = sorted({0, height} | {r for sp in spans for r in sp[:2]})
    out = []
    for top, bottom in zip(cuts, cuts[1:]):
        row = bytearray(b"\xff" * width)
        for r0, r1, c0, c1 in spans:
            if r0 <= top and bottom <= r1:
                row[c0:c1] = b"\x00" * (c1 - c0)
        out.append((b"\x00" + bytes(row)) * (bottom - top))
    ppm = int(round(s))
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + _png_chunk(b"pHYs", struct.pack(">IIB", ppm, ppm, 1))
            + _png_chunk(b"IDAT", zlib.compress(b"".join(out), 9))
            + _png_chunk(b"IEND", b""))


def _mm(v: float) -> str:
    text = ("%.6f" % v).rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def board_svg(spec: BoardSpec, quiet_m: float = 0.0) -> str:
    """Vector SVG. viewBox in MILLIMETRES, origin at the black pattern's
    top-left corner, y down (SVG's convention); the quiet zone sits at
    negative coordinates. width/height are given in mm, so the file imports
    at true size. White background rect included."""
    if _num(quiet_m, "quiet_m") < 0:
        raise ValueError("quiet_m must be >= 0")
    w, h = extent_m(spec)
    q = quiet_m * 1000.0
    tw, th = w * 1000.0 + 2 * q, h * 1000.0 + 2 * q
    parts = []
    for x0, y0, x1, y1 in black_rects_m(spec):
        parts.append("M%s %sh%sv%sh-%sz" % (
            _mm((x0 + w / 2.0) * 1000.0), _mm((h / 2.0 - y1) * 1000.0),
            _mm((x1 - x0) * 1000.0), _mm((y1 - y0) * 1000.0), _mm((x1 - x0) * 1000.0)))
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="%s %s %s %s" '
            'width="%smm" height="%smm" shape-rendering="crispEdges">'
            '<rect x="%s" y="%s" width="%s" height="%s" fill="#fff"/>'
            '<path d="%s" fill="#000"/></svg>'
            % (_mm(-q), _mm(-q), _mm(tw), _mm(th), _mm(tw), _mm(th),
               _mm(-q), _mm(-q), _mm(tw), _mm(th), "".join(parts)))
