"""Printable marker sheets: exact-size ArUco stickers as a vector PDF.

### The one rule: a marker is never scaled to fit

`marker_length_m` is the BLACK SQUARE's side, border included — the number
estimatePoseSingleMarkers() multiplies by. Print a 30 mm marker at 29 mm and
nothing fails: every range the robot reports is just 3.3 % short, forever. So
`layout_sheet` places each sticker at exactly its size or REJECTS it with a
reason. There is no shrink-to-fit path, here or in the PDF.

### Geometry

    module   = marker_length / (n + 2)            n = dictionary bits
    extent   = the black pattern: marker_length square for a single marker,
               aruco_board.extent_m for a grid / ChArUco board (non-square)
    sticker  = extent + 2 * quiet_modules * module on each axis  (the cut extent)
    cell     = sticker, plus a label box under it when labels are on

A board item is a PrintItem with layout "grid" or "charuco": marker_id is
then the board's FIRST id, and rows / cols / separation_m / square_length_m /
legacy mean what they mean in aruco_board.BoardSpec.

All layout numbers are MILLIMETRES with the origin at the page's TOP-LEFT
corner, y pointing down (the way a sheet is read). The PDF writer converts to
points (1 mm = 72/25.4 pt, bottom-left origin) at the last moment, and draws
each item's black ink as one path of filled rectangles from
`aruco_board.black_rects_m` — vectors, so the printer, not a resampler,
decides where the edges fall.

Every page carries a scale-check ruler (a 100 mm bar and a 4 in bar) because
the last thing between this file and a mis-scaled marker is a print dialog
defaulting to "fit to page". The PDF is hand-written PDF 1.4 with the base-14
Helvetica, stdlib only, so it can be produced inside Fusion.
"""
from __future__ import annotations

import datetime
import math
import zlib
from dataclasses import dataclass, field
from typing import Dict, List, NamedTuple, Optional, Tuple

from . import aruco_board, aruco_dict

MM_TO_PT = 72.0 / 25.4

PAGE_SIZES_MM: Dict[str, Tuple[float, float]] = {
    "letter": (215.9, 279.4), "a4": (210, 297), "legal": (215.9, 355.6),
    "a3": (297, 420), "tabloid": (279.4, 431.8), "a5": (148, 210),
}

LABEL_FONT_PT = 6.0
LABEL_MIN_FONT_PT = 4.5      # shrink a long label to the sticker width, not below this
LABEL_LEADING = 1.2          # line pitch / font size
LABEL_GAP_MM = 0.8           # sticker bottom -> label box text
FOOTER_FONT_PT = 6.5
FOOTER_H_MM = 4.0
RULER_H_MM = 19.0
RULER_W_MM = 102.0           # the 4 in bar is 101.6 mm; nothing sits beside the bars
BAND_GAP_MM = 3.0            # sticker area -> ruler block
CUT_GREY = 0.55
CUT_LINE_MM = 0.1
SMALL_MODULE_MM = 0.5
_EPS = 1e-9

# Marks (docs/proposal_aruco_marks.md, measured on 430,260 simulated photos;
# Zach 2026-10-07: "lets just do good defaults ... put it in the black at the
# bottom always", nothing that grows the sticker past what was modelled):
#   id_mark "border"   "#id" knocked out WHITE in each marker's bottom border
#                      row, cap height <= 0.5 module, centred clear of the
#                      corner cells, shrunk to fit: -0.3 pp detection (n.s.)
#   orient_mark "triangle"  a 0.5-grey right triangle, legs 0.6 module, in the
#                      quiet zone's top-left corner = OpenCV corner 0 of a
#                      single marker / the board frame origin: +0.2 pp
#   ChArUco            the board label in light grey in the top-left BLACK
#                      square instead of per-marker ids (free in the study)
# Ink in the 1-module WHITE ring is what costs (grey id there: -11 pp, up to
# -40 with dot gain), so nothing is ever written there.
ID_CAP_MODULES = 0.5
HELVETICA_CAP = 0.718           # cap height / em
TRI_LEG_MODULES = 0.6
TRI_GREY = 0.5
CHARUCO_LABEL_GREY = 0.75
MIN_CAP_MM = 1.0                # below this a label is cut short, not shrunk


@dataclass
class PrintItem:
    key: str
    dictionary: str
    marker_id: int
    marker_length_m: float          # BLACK SQUARE side incl. its black border
    quiet_modules: float = 1.0
    label_lines: List[str] = field(default_factory=list)   # [] -> identity line
    # Boards (aruco_board.BoardSpec); a single marker leaves these alone.
    layout: str = "single"          # "single" | "grid" | "charuco"
    rows: int = 1
    cols: int = 1
    separation_m: float = 0.0       # grid: gap between markers
    square_length_m: float = 0.0    # charuco: square side
    legacy: bool = False            # charuco: OpenCV setLegacyPattern
    # Short part name for the marker's TOP border row (study section 11:
    # "#id" bottom + part name top costs -0.2 pp vs "#id" alone, within
    # noise). "" = nothing in the top row.
    top_label: str = ""


@dataclass
class SheetSettings:
    page: str = "letter"
    landscape: bool = False
    margin_mm: float = 10
    gap_mm: float = 4
    cut_marks: bool = True
    labels: bool = True
    ruler: bool = True
    title: str = ""
    custom_page_mm: Optional[Tuple[float, float]] = None   # (w, h), overrides page
    id_mark: str = "border"        # "border" | "off"
    top_label: bool = True         # part name in the top border row
    orient_mark: str = "triangle"  # "triangle" | "off"


class RejectedItem(NamedTuple):
    item: PrintItem
    reason: str


@dataclass
class PlacedItem:
    item: PrintItem
    page: int                       # 0-based page index
    x_mm: float                     # sticker (cut extent) top-left, page coords
    y_mm: float
    sticker_mm: float               # sticker WIDTH (== height for a single marker)
    marker_mm: float                # == item.marker_length_m * 1000, always
    module_mm: float
    modules: int                    # n + 2
    marker_x_mm: float              # black pattern (extent) top-left
    marker_y_mm: float
    cell: Tuple[float, float, float, float]               # x, y, w, h reserved
    label_box: Optional[Tuple[float, float, float, float]]
    label_lines: List[str]
    label_font_pt: float
    sticker_w_mm: float = 0.0
    sticker_h_mm: float = 0.0
    extent_w_mm: float = 0.0        # black pattern; == marker_mm for a single
    extent_h_mm: float = 0.0


@dataclass
class SheetLayout:
    settings: SheetSettings
    page_mm: Tuple[float, float]                          # w, h
    printable_mm: Tuple[float, float, float, float]       # sticker area x, y, w, h
    pages: List[List[PlacedItem]]
    rejected: List[RejectedItem]
    ruler: Optional[dict]          # {"mm_bar": (x,y,w,h), "in_bar": (x,y,w,h), "top": y}
    warnings: List[str]

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def placed(self) -> List[PlacedItem]:
        return [p for page in self.pages for p in page]


# ------------------------------------------------------------------ text metrics

# Helvetica advance widths (AFM, 1/1000 em) for WinAnsi 32..126.
_ASCII_W = (
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
    1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
    333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
    556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584)
_WIDTHS = {32 + i: w for i, w in enumerate(_ASCII_W)}
_WIDTHS.update({0x85: 1000, 0x91: 222, 0x92: 222, 0x93: 333, 0x94: 333,
                0x95: 350, 0x96: 556, 0x97: 1000, 0xA0: 278, 0xB0: 400,
                0xB1: 584, 0xB5: 556, 0xB7: 278, 0xD7: 584})


def _encode(text: str) -> bytes:
    return str(text).encode("cp1252", errors="replace")


def text_width_mm(text: str, font_pt: float) -> float:
    """Advance width of `text` in Helvetica at `font_pt`, in mm."""
    units = sum(_WIDTHS.get(b, 556) for b in _encode(text))
    return units / 1000.0 * font_pt / MM_TO_PT


def _fit_text(text: str, font_pt: float, max_mm: float) -> str:
    if text_width_mm(text, font_pt) <= max_mm:
        return text
    while len(text) > 1 and text_width_mm(text + "…", font_pt) > max_mm:
        text = text[:-1]
    return text + "…"


def _fmt_mm(v: float) -> str:
    s = "%.2f" % v
    return s[:-1] if s.endswith("0") else s


def board_spec(item: PrintItem) -> "aruco_board.BoardSpec":
    """The aruco_board.BoardSpec a PrintItem describes (marker_id = first id)."""
    return aruco_board.BoardSpec(
        layout=item.layout, dictionary=item.dictionary, first_id=item.marker_id,
        marker_length_m=item.marker_length_m, rows=item.rows, cols=item.cols,
        separation_m=item.separation_m, square_length_m=item.square_length_m,
        legacy=item.legacy)


def identity_label(item: PrintItem) -> str:
    """The default label line: what a person needs to match sticker to CAD."""
    size = _fmt_mm(item.marker_length_m * 1000.0)
    if item.layout == "single":
        return "%s #%s · %s mm" % (item.dictionary, item.marker_id, size)
    try:
        last = item.marker_id + aruco_board.marker_count(board_spec(item)) - 1
    except (ValueError, TypeError):
        last = "?"
    if item.layout == "grid":
        return "%s grid %dx%d #%s–%s · %s mm, gap %s mm" % (
            item.dictionary, item.cols, item.rows, item.marker_id, last, size,
            _fmt_mm(item.separation_m * 1000.0))
    return "%s ChArUco %dx%d #%s–%s · square %s / marker %s mm%s" % (
        item.dictionary, item.cols, item.rows, item.marker_id, last,
        _fmt_mm(item.square_length_m * 1000.0), size,
        " (legacy)" if item.legacy else "")


# ------------------------------------------------------------------ layout


def page_size_mm(settings: SheetSettings) -> Tuple[float, float]:
    if settings.custom_page_mm is not None:
        w, h = (float(v) for v in settings.custom_page_mm)
        if not (w > 0 and h > 0):
            raise ValueError("custom_page_mm must be two positive lengths, got %r"
                             % (settings.custom_page_mm,))
    else:
        key = str(settings.page).lower()
        if key not in PAGE_SIZES_MM:
            raise ValueError("unknown page %r — expected one of %s or custom_page_mm"
                             % (settings.page, ", ".join(PAGE_SIZES_MM)))
        w, h = (float(v) for v in PAGE_SIZES_MM[key])
    if settings.landscape and w < h:
        w, h = h, w
    return w, h


class _Prepared(NamedTuple):
    index: int
    item: PrintItem
    modules: int
    marker_mm: float
    module_mm: float
    sticker_w: float
    sticker_h: float
    extent_w: float
    extent_h: float
    lines: List[str]
    font_pt: float
    label_h: float
    cell_w: float
    cell_h: float


def _prepare(index, item, settings, area_w, area_h):
    """-> (_Prepared, None) or (None, reason)."""
    try:
        info = aruco_dict.dictionary_info(item.dictionary)
    except ValueError as exc:
        return None, str(exc)
    mid = item.marker_id
    if isinstance(mid, bool) or not isinstance(mid, int) or not 0 <= mid < info["capacity"]:
        return None, ("id %r is not in %s (0..%d)"
                      % (mid, info["name"], info["capacity"] - 1))
    try:
        length = float(item.marker_length_m)
        quiet = float(item.quiet_modules)
    except (TypeError, ValueError):
        return None, "marker_length_m and quiet_modules must be numbers"
    if not (math.isfinite(length) and length > 0):
        return None, "marker_length_m must be > 0 (got %r)" % (item.marker_length_m,)
    if not (math.isfinite(quiet) and quiet >= 0):
        return None, "quiet_modules must be >= 0 (got %r)" % (item.quiet_modules,)
    try:
        extent_w, extent_h = (v * 1000.0 for v in aruco_board.extent_m(board_spec(item)))
    except (ValueError, TypeError) as exc:
        return None, "%s board: %s" % (item.layout, exc)
    modules = info["modules"]
    marker_mm = length * 1000.0
    module_mm = marker_mm / modules
    sticker_w = extent_w + 2.0 * quiet * module_mm
    sticker_h = extent_h + 2.0 * quiet * module_mm

    lines: List[str] = []
    font_pt = LABEL_FONT_PT
    label_h = 0.0
    label_w = 0.0
    if settings.labels:
        lines = [str(s) for s in item.label_lines] or [identity_label(item)]
        widest = max(text_width_mm(s, font_pt) for s in lines)
        if widest > sticker_w:
            font_pt = max(LABEL_MIN_FONT_PT, LABEL_FONT_PT * sticker_w / widest)
        lines = [_fit_text(s, font_pt, area_w) for s in lines]
        label_w = max(text_width_mm(s, font_pt) for s in lines)
        label_h = LABEL_GAP_MM + len(lines) * font_pt * LABEL_LEADING / MM_TO_PT
    cell_w = max(sticker_w, label_w)
    cell_h = sticker_h + label_h
    if sticker_w > area_w + _EPS or cell_h > area_h + _EPS:
        what = ("%.1f mm marker" % marker_mm if item.layout == "single" else
                "%.1f x %.1f mm %s board" % (extent_w, extent_h, item.layout))
        return None, (
            "sticker is %.1f x %.1f mm (%s + quiet zone%s) but the "
            "printable area is %.1f x %.1f mm — markers are never scaled to "
            "fit; use a larger page, landscape or smaller margins"
            % (sticker_w, cell_h, what, " + label" if label_h else "",
               area_w, area_h))
    return _Prepared(index, item, modules, marker_mm, module_mm, sticker_w, sticker_h,
                     extent_w, extent_h, lines, font_pt, label_h, cell_w, cell_h), None


def layout_sheet(items: List[PrintItem], settings: Optional[SheetSettings] = None) -> SheetLayout:
    """Place every item at its exact size, or reject it with a reason.

    First-fit-decreasing shelf packing: items sorted by sticker height, then
    width (largest first, ties in input order), each goes on the first open shelf on any page
    with room, else opens a shelf on the first page with height left, else a
    new page. Deterministic for a given input."""
    settings = settings or SheetSettings()
    page_w, page_h = page_size_mm(settings)
    m = float(settings.margin_mm)
    gap = float(settings.gap_mm)
    if m < 0 or gap < 0:
        raise ValueError("margin_mm and gap_mm must be >= 0")
    warnings: List[str] = []
    x0, y0 = m, m
    area_w = page_w - 2 * m
    footer_top = page_h - m - FOOTER_H_MM
    ruler = None
    area_bottom = footer_top - BAND_GAP_MM
    if settings.ruler:
        if area_w + _EPS >= RULER_W_MM:
            top = footer_top - RULER_H_MM
            ruler = {"top": top,
                     "mm_bar": (x0, top + 10.5, 100.0, 1.2),
                     "in_bar": (x0, top + 13.3, 101.6, 1.2)}
            area_bottom = top - BAND_GAP_MM
        else:
            warnings.append("page is %.1f mm wide inside the margins; the scale "
                            "ruler needs %.0f mm and was left off — check the "
                            "print scale another way" % (area_w, RULER_W_MM))
    area_h = area_bottom - y0
    if area_w <= 0 or area_h <= 0:
        raise ValueError("margins and ruler leave no printable area on a "
                         "%.1f x %.1f mm page" % (page_w, page_h))

    prepared: List[_Prepared] = []
    rejected: List[RejectedItem] = []
    for index, item in enumerate(items):
        prep, reason = _prepare(index, item, settings, area_w, area_h)
        if prep is None:
            rejected.append(RejectedItem(item, reason))
        else:
            prepared.append(prep)
            if prep.module_mm < SMALL_MODULE_MM:
                warnings.append("%s: module is %.2f mm — below ~%.1f mm many "
                                "printers blur the cells" % (item.key, prep.module_mm,
                                                             SMALL_MODULE_MM))

    prepared.sort(key=lambda p: (-p.sticker_h, -p.sticker_w, -p.cell_h, p.index))
    x1, y1 = x0 + area_w, y0 + area_h
    shelves: List[List[float]] = []          # [page, y, h, next_x]
    next_shelf_y: List[float] = []           # per page
    pages: List[List[PlacedItem]] = []
    for p in prepared:
        spot = None
        for shelf in shelves:
            if p.cell_h <= shelf[2] + _EPS and shelf[3] + p.cell_w <= x1 + _EPS:
                spot = (int(shelf[0]), shelf[3], shelf[1])
                shelf[3] += p.cell_w + gap
                break
        if spot is None:
            for page_index, y in enumerate(next_shelf_y):
                if y + p.cell_h <= y1 + _EPS:
                    spot = (page_index, x0, y)
                    break
            if spot is None:
                pages.append([])
                next_shelf_y.append(y0)
                spot = (len(pages) - 1, x0, y0)
            shelves.append([spot[0], spot[2], p.cell_h, x0 + p.cell_w + gap])
            next_shelf_y[spot[0]] = spot[2] + p.cell_h + gap
        page_index, cx, cy = spot
        sx = cx + (p.cell_w - p.sticker_w) / 2.0
        quiet_mm = (p.sticker_w - p.extent_w) / 2.0
        label_box = ((cx, cy + p.sticker_h, p.cell_w, p.label_h)
                     if p.label_h else None)
        pages[page_index].append(PlacedItem(
            item=p.item, page=page_index, x_mm=sx, y_mm=cy,
            sticker_mm=p.sticker_w, marker_mm=p.marker_mm, module_mm=p.module_mm,
            modules=p.modules, marker_x_mm=sx + quiet_mm, marker_y_mm=cy + quiet_mm,
            cell=(cx, cy, p.cell_w, p.cell_h), label_box=label_box,
            label_lines=list(p.lines), label_font_pt=p.font_pt,
            sticker_w_mm=p.sticker_w, sticker_h_mm=p.sticker_h,
            extent_w_mm=p.extent_w, extent_h_mm=p.extent_h))
    for page in pages:
        page.sort(key=lambda q: (q.y_mm, q.x_mm))
    return SheetLayout(settings=settings, page_mm=(page_w, page_h),
                       printable_mm=(x0, y0, area_w, area_h), pages=pages,
                       rejected=rejected, ruler=ruler, warnings=warnings)


def _r(v: float) -> float:
    return round(v, 4)


def _board_summary(item: PrintItem) -> dict:
    if item.layout == "single":
        return {}
    spec = board_spec(item)
    return {"rows": item.rows, "cols": item.cols, "ids": aruco_board.ids(spec),
            "separation_mm": _r(item.separation_m * 1000.0),
            "square_length_mm": _r(item.square_length_m * 1000.0),
            "legacy": bool(item.legacy),
            "opencv_frame": aruco_board.opencv_frame(spec)}


def layout_summary(layout: SheetLayout) -> dict:
    """JSON-able account of a layout, for reports. page_number is 1-based."""
    s = layout.settings
    x, y, w, h = layout.printable_mm
    return {
        "page": s.page if s.custom_page_mm is None else "custom",
        "page_mm": [_r(layout.page_mm[0]), _r(layout.page_mm[1])],
        "landscape": bool(s.landscape),
        "printable_mm": {"x": _r(x), "y": _r(y), "w": _r(w), "h": _r(h)},
        "origin": "page top-left, y down, millimetres",
        "scale": 1.0,
        "pages": layout.page_count,
        "placed": [{
            "key": p.item.key, "dictionary": p.item.dictionary,
            "marker_id": p.item.marker_id,
            "marker_length_mm": _r(p.marker_mm), "module_mm": _r(p.module_mm),
            "quiet_modules": p.item.quiet_modules, "sticker_mm": _r(p.sticker_mm),
            "sticker_w_mm": _r(p.sticker_w_mm), "sticker_h_mm": _r(p.sticker_h_mm),
            "layout": p.item.layout,
            "extent_mm": [_r(p.extent_w_mm), _r(p.extent_h_mm)],
            "page_number": p.page + 1, "x_mm": _r(p.x_mm), "y_mm": _r(p.y_mm),
            "marker_x_mm": _r(p.marker_x_mm), "marker_y_mm": _r(p.marker_y_mm),
            "label_lines": list(p.label_lines),
            **_board_summary(p.item),
        } for p in layout.placed],
        "rejected": [{
            "key": r.item.key, "dictionary": r.item.dictionary,
            "marker_id": r.item.marker_id, "marker_length_m": r.item.marker_length_m,
            "layout": r.item.layout, "reason": r.reason,
        } for r in layout.rejected],
        "ruler": None if layout.ruler is None else {
            k: ([_r(v) for v in val] if isinstance(val, tuple) else _r(val))
            for k, val in layout.ruler.items()},
        "warnings": list(layout.warnings),
    }


# ------------------------------------------------------------------ PDF


def _n(v: float) -> str:
    text = ("%.4f" % v).rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def pdf_string(text: str) -> str:
    """A PDF literal string for WinAnsi Helvetica: cp1252 bytes, with ( ) \\
    escaped and everything outside printable ASCII written as octal."""
    out = []
    for b in _encode(text):
        if b in (0x28, 0x29, 0x5C):
            out.append("\\" + chr(b))
        elif 32 <= b < 127:
            out.append(chr(b))
        else:
            out.append("\\%03o" % b)
    return "(" + "".join(out) + ")"


class _Page:
    """Content-stream builder in millimetres, top-left origin."""

    def __init__(self, page_h_mm: float):
        self.h = page_h_mm
        self.ops: List[str] = []

    def rect(self, x, y, w, h) -> str:
        return "%s %s %s %s re" % (_n(x * MM_TO_PT), _n((self.h - y - h) * MM_TO_PT),
                                   _n(w * MM_TO_PT), _n(h * MM_TO_PT))

    def fill_rects(self, rects, grey=0.0):
        if rects:
            self.ops.append("%s g" % _n(grey))
            self.ops.extend(self.rect(*r) for r in rects)
            self.ops.append("f")

    def stroke_rect(self, x, y, w, h, grey, width_mm):
        self.ops.append("%s G %s w %s S" % (_n(grey), _n(width_mm * MM_TO_PT),
                                             self.rect(x, y, w, h)))

    def text(self, x, baseline_y, s, size, align="left", grey=0.0):
        if align != "left":
            width = text_width_mm(s, size)
            x -= width / 2.0 if align == "center" else width
        self.ops.append("BT /F1 %s Tf %s g %s %s Td %s Tj ET"
                        % (_n(size), _n(grey), _n(x * MM_TO_PT),
                           _n((self.h - baseline_y) * MM_TO_PT), pdf_string(s)))

    def triangle(self, x, y, leg, grey):
        """Right triangle, right angle at (x, y) (top-left), legs along +x, +y."""
        yy = self.h - y
        self.ops.append("%s g %s %s m %s %s l %s %s l h f"
                        % (_n(grey), _n(x * MM_TO_PT), _n(yy * MM_TO_PT),
                           _n((x + leg) * MM_TO_PT), _n(yy * MM_TO_PT),
                           _n(x * MM_TO_PT), _n((yy - leg) * MM_TO_PT)))


def _border_text(pg, text, left_mm, edge_mm, length_mm, module_mm, row="bottom",
                 min_cap_mm=0.0):
    """White text centred in a marker's border row (page mm, y down).

    `edge_mm` is the marker's bottom edge (row="bottom") or top edge
    (row="top"). The corner cells are left clear and the cap height never
    exceeds ID_CAP_MODULES -- a longer text gets a smaller font, never a
    taller row; below `min_cap_mm` it is cut short with "..." instead."""
    cap = ID_CAP_MODULES * module_mm
    size = cap / HELVETICA_CAP * MM_TO_PT
    room = (length_mm - 2.0 * module_mm) * 0.9
    width = text_width_mm(text, size)
    if width > room > 0:
        floor_size = min_cap_mm / HELVETICA_CAP * MM_TO_PT
        size = max(size * room / width, min(size, floor_size))
        while text_width_mm(text, size) > room and len(text) > 4:
            text = text[:-4].rstrip() + "..."
        cap = size / MM_TO_PT * HELVETICA_CAP
    if row == "top":
        baseline = edge_mm + module_mm - (module_mm - cap) / 2.0
    else:
        baseline = edge_mm - (module_mm - cap) / 2.0
    pg.text(left_mm + length_mm / 2.0, baseline, text, size, "center", grey=1.0)


def _marks(pg, p, settings):
    """Orientation triangle + ids, all inside the sticker the CAD modelled."""
    item = p.item
    module = p.module_mm
    if settings.orient_mark == "triangle":
        pg.triangle(p.x_mm, p.y_mm, TRI_LEG_MODULES * module, TRI_GREY)
    if settings.id_mark != "border":
        return
    spec = board_spec(item)
    cx = p.marker_x_mm + p.extent_w_mm / 2.0
    cy = p.marker_y_mm + p.extent_h_mm / 2.0
    if item.layout == "charuco":
        sq = float(item.square_length_m) * 1000.0
        col = 0 if aruco_board._charuco_black(0, 0, item.rows, item.legacy) else 1
        x0 = p.marker_x_mm + col * sq
        ids = aruco_board.ids(spec)
        lines = ["#%d-%d" % (ids[0], ids[-1]), "%dx%d" % (item.cols, item.rows)]
        if settings.top_label and item.top_label:
            lines.append(item.top_label)
        size = min(0.22 * sq / HELVETICA_CAP * MM_TO_PT,
                   min(0.8 * sq / max(text_width_mm(t, 1.0), 1e-9) for t in lines))
        cap = size / MM_TO_PT * HELVETICA_CAP
        n = len(lines)
        for k, t in enumerate(lines):
            pg.text(x0 + sq / 2.0,
                    p.marker_y_mm + sq / 2.0 + (k - (n - 1) / 2.0) * 1.6 * cap + cap / 2.0,
                    t, size, "center", grey=CHARUCO_LABEL_GREY)
        return
    first = True
    for mid, corners in sorted(aruco_board.marker_corners_m(spec).items()):
        xs = [c[0] for c in corners]
        ys = [c[1] for c in corners]
        left = cx + min(xs) * 1000.0
        length = (max(xs) - min(xs)) * 1000.0
        bottom = cy - min(ys) * 1000.0          # +y up in metres -> page y down
        top = cy - max(ys) * 1000.0
        _border_text(pg, "#%d" % mid, left, bottom, length, module)
        # the part name once per target: the single marker, or the board's
        # first (top-left) marker
        if first and settings.top_label and item.top_label:
            _border_text(pg, item.top_label, left, top, length, module, row="top",
                         min_cap_mm=MIN_CAP_MM)
        first = False


def _draw_ruler(pg: _Page, ruler: dict):
    top = ruler["top"]
    x0 = ruler["mm_bar"][0]
    pg.text(x0, top + 2.5, "Print at 100% / Actual size — this bar must "
            "measure 100 mm", 7)
    pg.text(x0, top + 5.0, "Sizes are the black square's side (the length a "
            "detector multiplies by). Cut on the grey lines.", 5.5)
    tick_w = 0.25
    # 100 mm bar, ticks up every 10 mm (long) and 5 mm (short), labels above.
    # Nothing is drawn on the bars' own rows, so a scanline through a bar
    # measures exactly the bar (tools/verify_aruco_output.py does).
    bx, by, bw, bh = ruler["mm_bar"]
    rects = [(bx, by, bw, bh)]
    for k in range(0, 21):
        pos = bx + 5.0 * k
        length = 1.6 if k % 2 == 0 else 0.8
        tx = min(max(pos - tick_w / 2, bx), bx + bw - tick_w)
        rects.append((tx, by - length, tick_w, length))
        if k % 2 == 0:
            label = "100 mm" if k == 20 else str(5 * k)
            align = "left" if k == 0 else ("right" if k == 20 else "center")
            pg.text(pos, by - 2.2, label, 5, align)
    # 4 in bar, ticks down every 1 in / 1/2 in / 1/4 in, labels below.
    ix, iy, iw, ih = ruler["in_bar"]
    rects.append((ix, iy, iw, ih))
    for k in range(0, 17):
        pos = ix + 25.4 * k / 4.0
        length = 1.6 if k % 4 == 0 else (1.1 if k % 2 == 0 else 0.6)
        tx = min(max(pos - tick_w / 2, ix), ix + iw - tick_w)
        rects.append((tx, iy + ih, tick_w, length))
        if k % 4 == 0:
            label = "4 in" if k == 16 else str(k // 4)
            align = "left" if k == 0 else ("right" if k == 16 else "center")
            pg.text(pos, iy + ih + 3.7, label, 5, align)
    pg.fill_rects(rects)


def _page_content(layout: SheetLayout, page_index: int, page_total: int,
                  date: str) -> bytes:
    s = layout.settings
    page_w, page_h = layout.page_mm
    pg = _Page(page_h)
    items = layout.pages[page_index] if page_index < len(layout.pages) else []
    for p in items:
        # Black ink: one path of disjoint rectangles, filled once (no seams).
        # black_rects_m is centre-origin, +y up, metres -> page mm, y down.
        cx = p.marker_x_mm + p.extent_w_mm / 2.0
        cy = p.marker_y_mm + p.extent_h_mm / 2.0
        cells = [(cx + x0 * 1000.0, cy - y1 * 1000.0, (x1 - x0) * 1000.0, (y1 - y0) * 1000.0)
                 for x0, y0, x1, y1 in aruco_board.black_rects_m(board_spec(p.item))]
        pg.fill_rects(cells)
        _marks(pg, p, s)
        if s.cut_marks:
            pg.stroke_rect(p.x_mm, p.y_mm, p.sticker_w_mm, p.sticker_h_mm,
                           CUT_GREY, CUT_LINE_MM)
        if p.label_box is not None:
            bx, by, bw, _ = p.label_box
            line_mm = p.label_font_pt * LABEL_LEADING / MM_TO_PT
            for k, line in enumerate(p.label_lines):
                baseline = (by + LABEL_GAP_MM + k * line_mm
                            + 0.9 * p.label_font_pt / MM_TO_PT)
                pg.text(bx + bw / 2.0, baseline, line, p.label_font_pt, "center")
    if layout.ruler is not None:
        _draw_ruler(pg, layout.ruler)
    m = float(s.margin_mm)
    base = page_h - m - 1.0
    title = s.title or "ArUco marker sheet"
    pg.text(m, base, "%s · %s" % (title, date), FOOTER_FONT_PT)
    pg.text(page_w - m, base, "page %d/%d" % (page_index + 1, page_total),
            FOOTER_FONT_PT, "right")
    if layout.rejected:
        pg.text(page_w / 2.0, base, "%d marker(s) NOT printed — see the "
                "layout report" % len(layout.rejected), FOOTER_FONT_PT, "center")
    if not layout.pages:
        pg.text(m, m + 5, "No markers placed.", 9)
    return "\n".join(pg.ops).encode("ascii")


def _utf16_hex(text: str) -> str:
    return "<FEFF" + text.encode("utf-16-be").hex().upper() + ">"


def render_layout_pdf(layout: SheetLayout, date: Optional[str] = None) -> bytes:
    """PDF 1.4 bytes for an existing layout (one page per layout page, and a
    single page when nothing was placed)."""
    date = date or datetime.date.today().isoformat()
    page_w, page_h = layout.page_mm
    total = max(1, layout.page_count)
    objects: List[bytes] = []          # objects[i] is object number i + 1

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    add(b"<< /Type /Catalog /Pages 2 0 R >>")
    add(b"")                                                    # 2: Pages, below
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
               b"/Encoding /WinAnsiEncoding >>")
    title = layout.settings.title or "ArUco marker sheet"
    info = add(("<< /Title %s /Producer (bam_cad_core.print_sheet) >>"
                % _utf16_hex(title)).encode("ascii"))
    media = "[0 0 %s %s]" % (_n(page_w * MM_TO_PT), _n(page_h * MM_TO_PT))
    kids = []
    for i in range(total):
        stream = zlib.compress(_page_content(layout, i, total, date), 9)
        content = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n"
                      % len(stream) + stream + b"\nendstream")
        kids.append(add(("<< /Type /Page /Parent 2 0 R /MediaBox %s "
                         "/Resources << /Font << /F1 %d 0 R >> >> "
                         "/Contents %d 0 R >>" % (media, font, content)).encode("ascii")))
    objects[1] = ("<< /Type /Pages /Kids [%s] /Count %d >>"
                  % (" ".join("%d 0 R" % k for k in kids), len(kids))).encode("ascii")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objects) + 1, info, xref))
    return bytes(out)


def render_pdf(items: List[PrintItem], settings: Optional[SheetSettings] = None) -> bytes:
    """Lay out `items` (see layout_sheet) and return the sheet as PDF bytes.
    Rejected items are not drawn; the footer says how many, and
    layout_sheet / layout_summary say which and why."""
    return render_layout_pdf(layout_sheet(items, settings or SheetSettings()))
