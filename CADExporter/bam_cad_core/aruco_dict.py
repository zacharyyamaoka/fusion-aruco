"""OpenCV's predefined ArUco / AprilTag dictionaries, without OpenCV.

Fusion's Python has no numpy and no cv2, so it cannot ask OpenCV what marker
17 of DICT_4X4_50 looks like. It does not need to: the patterns are a fixed
table, exported once by `tools/gen_aruco_dictionaries.py` into
`aruco_dictionaries.json` beside this file and checked against cv2 for every
id by `tools/verify_aruco_output.py`. This module only reads that table.

### The conventions (everything downstream depends on these)

    marker_grid(name, id)[row][col]      1 = BLACK, 0 = white
    row 0                                the TOP row of the image, exactly as
                                         cv2.aruco.generateImageMarker draws it
    size                                 (n+2) x (n+2): n data bits plus the
                                         1-module black border on each side

OpenCV's own bit arrays use the opposite polarity (1 = white). Ink is what a
printer, a PDF and a CAD body all draw, so the runtime speaks in ink.

A module is one cell; the black square is n+2 modules wide, and its side is
`marker_length_m` — the number pose estimation multiplies by. Nothing here
scales a marker non-uniformly or snaps it to a size: every renderer takes a
whole number of pixels or an exact length per module, so the drawn square is
the square the detector will be told about.

The table is loaded lazily, once, and its sha256 is re-checked on load: a
corrupt table would not crash, it would print stickers that decode to the
wrong ids, which is the worst kind of wrong.
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import zlib
from typing import Dict, List, Optional, Tuple, Union

from .fiducials import ARUCO_DICTS

TABLE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "aruco_dictionaries.json")
TABLE_SCHEMA = "bam_aruco_dictionaries/1"

# Same names, same order as fiducials.ARUCO_DICTS — the one list of what a
# record may name. tests/test_aruco_output.py asserts the table agrees.
DICTIONARY_NAMES: Tuple[str, ...] = tuple(ARUCO_DICTS)

_TABLE: Optional[dict] = None
_BY_FOLDED = {name.upper(): name for name in DICTIONARY_NAMES}


def _canonical(payload) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _load() -> dict:
    global _TABLE
    if _TABLE is None:
        with open(TABLE_FILE, "rb") as fh:
            doc = json.loads(fh.read().decode("utf-8"))
        if doc.get("schema") != TABLE_SCHEMA:
            raise RuntimeError("%s: schema %r, expected %r"
                               % (TABLE_FILE, doc.get("schema"), TABLE_SCHEMA))
        digest = hashlib.sha256(_canonical(doc["payload"])).hexdigest()
        if digest != doc.get("payload_sha256"):
            raise RuntimeError(
                "%s is corrupt: payload sha256 %s does not match its header %s "
                "— regenerate it with tools/gen_aruco_dictionaries.py"
                % (TABLE_FILE, digest, doc.get("payload_sha256")))
        _TABLE = doc
    return _TABLE


def _resolve(name) -> str:
    """Exact name, or the same name in another case (OpenCV itself spells
    DICT_APRILTAG_36h11 both ways). Anything else is an error, never a guess."""
    if isinstance(name, str):
        if name in ARUCO_DICTS:
            return name
        folded = _BY_FOLDED.get(name.upper())
        if folded is not None:
            return folded
    raise ValueError("unknown ArUco dictionary %r — expected one of: %s"
                     % (name, ", ".join(DICTIONARY_NAMES)))


def _entry(name) -> Tuple[str, dict]:
    resolved = _resolve(name)
    entry = _load()["payload"]["dictionaries"].get(resolved)
    if entry is None:
        raise ValueError("dictionary %s is known to fiducials.ARUCO_DICTS but "
                         "missing from %s — regenerate the table"
                         % (resolved, os.path.basename(TABLE_FILE)))
    return resolved, entry


def dictionary_info(name) -> dict:
    """{'name', 'bits' (n, the inner grid side), 'capacity', 'family'
    ('aruco' | 'apriltag' | 'aruco_original'), 'max_correction_bits',
    'modules' (n+2, the black square's side in modules)}."""
    resolved, entry = _entry(name)
    return {
        "name": resolved,
        "bits": entry["bits"],
        "capacity": entry["capacity"],
        "family": entry["family"],
        "max_correction_bits": entry["max_correction_bits"],
        "modules": entry["bits"] + 2,
    }


def _inner_value(name, marker_id) -> Tuple[int, int]:
    resolved, entry = _entry(name)
    capacity = entry["capacity"]
    if isinstance(marker_id, bool) or not isinstance(marker_id, int):
        raise ValueError("marker id must be an integer, got %r" % (marker_id,))
    if not 0 <= marker_id < capacity:
        raise ValueError("%s has ids 0..%d; id %d does not exist in it"
                         % (resolved, capacity - 1, marker_id))
    hex_value = _load()["payload"]["tables"][entry["table"]]["markers"][marker_id]
    return entry["bits"], int(hex_value, 16)


def marker_grid(name, marker_id: int) -> List[List[int]]:
    """The full (n+2) x (n+2) marker INCLUDING its 1-module black border.
    1 = black, 0 = white; row 0 = top row of the image. A fresh list each call."""
    n, value = _inner_value(name, marker_id)
    side = n + 2
    grid = [[1] * side for _ in range(side)]
    shift = n * n - 1
    for r in range(n):
        row = grid[r + 1]
        for c in range(n):
            row[c + 1] = (value >> shift) & 1
            shift -= 1
    return grid


def black_rects(name, marker_id: int) -> List[Tuple[int, int, int, int]]:
    """Black cells merged into rectangles, (x, y, w, h) in MODULES, origin the
    top-left corner of the black border, y down.

    Horizontal runs first, then identical runs on consecutive rows stacked
    into one rectangle. The rectangles are disjoint and cover exactly the
    black cells, so they can be filled as one path without seams."""
    grid = marker_grid(name, marker_id)
    side = len(grid)
    open_rects: Dict[Tuple[int, int], List[int]] = {}
    done: List[List[int]] = []
    for y, row in enumerate(grid):
        runs = []
        x = 0
        while x < side:
            if row[x]:
                x0 = x
                while x < side and row[x]:
                    x += 1
                runs.append((x0, x - x0))
            else:
                x += 1
        still_open = {}
        for run in runs:
            rect = open_rects.pop(run, None)
            if rect is None:
                rect = [run[0], y, run[1], 0]
            rect[3] += 1
            still_open[run] = rect
        done.extend(open_rects.values())
        open_rects = still_open
    done.extend(open_rects.values())
    return sorted((r[0], r[1], r[2], r[3]) for r in done)


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))


def marker_png(name, marker_id: int, module_px: int = 20,
               quiet_modules: Union[int, float] = 1) -> bytes:
    """8-bit grayscale PNG: the marker at `module_px` px per module inside a
    white quiet zone of `quiet_modules` modules (round(quiet * module_px) px).
    Side = (n + 2) * module_px + 2 * quiet px. Whole pixels per module, so the
    image is never resampled."""
    if isinstance(module_px, bool) or not isinstance(module_px, int) or module_px < 1:
        raise ValueError("module_px must be a positive integer, got %r" % (module_px,))
    if quiet_modules < 0:
        raise ValueError("quiet_modules must be >= 0, got %r" % (quiet_modules,))
    grid = marker_grid(name, marker_id)
    quiet = int(round(quiet_modules * module_px))
    side = len(grid) * module_px + 2 * quiet
    white_row = b"\x00" + b"\xff" * side          # filter byte 0 + pixels
    pad = b"\xff" * quiet
    rows = [white_row] * quiet
    for grid_row in grid:
        line = b"\x00" + pad + b"".join(
            (b"\x00" if cell else b"\xff") * module_px for cell in grid_row) + pad
        rows.extend([line] * module_px)
    rows.extend([white_row] * quiet)
    ihdr = struct.pack(">IIBBBBB", side, side, 8, 0, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr)
            + _png_chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
            + _png_chunk(b"IEND", b""))


def _num(v) -> str:
    text = ("%.6f" % v).rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def marker_svg(name, marker_id: int, size=None,
               quiet_modules: Union[int, float] = 1) -> str:
    """Compact SVG, viewBox in MODULES with the origin at the black border's
    top-left (so the quiet zone sits at negative coordinates).

    `size` sets width and height of the WHOLE image (quiet zone included): a
    number is written as-is (user units / px), a string such as "36mm" is
    used verbatim, None leaves the size to the container. Black cells are one
    path of merged rectangles; the quiet zone is an explicit white rect so the
    marker survives being placed on a dark background."""
    if quiet_modules < 0:
        raise ValueError("quiet_modules must be >= 0, got %r" % (quiet_modules,))
    rects = black_rects(name, marker_id)
    side = dictionary_info(name)["modules"]
    q = _num(quiet_modules)
    total = _num(side + 2 * quiet_modules)
    dims = ""
    if size is not None:
        s = size if isinstance(size, str) else _num(size)
        dims = ' width="%s" height="%s"' % (s, s)
    path = "".join("M%d %dh%dv%dh-%dz" % (x, y, w, h, w) for x, y, w, h in rects)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="-%s -%s %s %s"%s '
            'shape-rendering="crispEdges">'
            '<rect x="-%s" y="-%s" width="%s" height="%s" fill="#fff"/>'
            '<path d="%s" fill="#000"/></svg>'
            % (q, q, total, total, dims, q, q, total, total, path))
