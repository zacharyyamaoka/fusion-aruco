"""Offline renders of ArUco Extrude's "Chamfer top-left corner" footprints.

Every shape comes from the same data the Fusion side builds from: the black
cells from markers.cell_boxes, the mark (cut tile, cut ChArUco square,
corner triangle) from bam_cad_core.aruco_chamfer -- seen from above, turned
by the Origin turn. The orange ring is OpenCV's corner 0 (a single marker)
or the board's origin corner, from markers.detector_frame / aruco_board.

    python tools/render_aruco_chamfer_impl.py   # -> reports/media/aruco/chamfer/impl_*.png
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "CADExporter"))

from bam_cad_core import aruco_board as B  # noqa: E402
from bam_cad_core import aruco_chamfer as C  # noqa: E402
from bam_cad_core import aruco_slabs as S  # noqa: E402
from bam_cad_core import markers as M  # noqa: E402

OUT = ROOT / "reports" / "media" / "aruco" / "chamfer"
SS = 3                                  # supersampling
WHITE, BLACK = (246, 246, 242), (22, 22, 22)
PART = (128, 136, 148)                  # a part of some other colour
WHITE_PART, BLACK_PART = (232, 230, 222), (34, 34, 36)
RING = (255, 140, 0)
TEXT = (40, 40, 40)
MODES = [("both", "new", "White + black, New body"), ("both", "join", "White + black, Join"),
         ("black", None, "Black only"), ("white", None, "White only")]


def font(px):
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    return ImageFont.load_default(size=px)


def turned(theta):
    c, s = math.cos(math.radians(theta)), math.sin(math.radians(theta))
    return (c, s), (-s, c)


def corner0(spec):
    """OpenCV's corner 0 / the board origin in the tag frame (mm)."""
    D = M.detector_frame(spec)
    if spec.layout == "single":
        p = B.object_points(M.board_spec(spec))[spec.marker_id][0]
    else:
        p = (0.0, 0.0)
    return ((D[0] * p[0] + D[1] * p[1] + D[3]) * 1000.0, (D[4] * p[0] + D[5] * p[1] + D[7]) * 1000.0)


class Panel:
    """One footprint, drawn in world mm (x right, y up) at `s` px / mm."""

    def __init__(self, size_px, span_mm):
        self.n = size_px * SS
        self.s = self.n / span_mm
        self.im = Image.new("RGB", (self.n, self.n), (255, 255, 255))
        self.d = ImageDraw.Draw(self.im)

    def px(self, p):
        return (self.n / 2.0 + p[0] * self.s, self.n / 2.0 - p[1] * self.s)

    def poly(self, pts, fill, outline=None, width=1):
        self.d.polygon([self.px(p) for p in pts], fill=fill, outline=outline,
                       width=width * SS if outline else 0)

    def ring(self, p, r_px=7):
        x, y = self.px(p)
        r = r_px * SS
        self.d.ellipse((x - r, y - r, x + r, y + r), outline=RING, width=3 * SS)

    def dashed(self, pts, colour, dash=6, width=1):
        q = [self.px(p) for p in pts] + [self.px(pts[0])]
        for a, b in zip(q, q[1:]):
            L = math.dist(a, b)
            n = max(1, int(L / (dash * SS)))
            for k in range(0, n, 2):
                t0, t1 = k / n, min(1.0, (k + 1) / n)
                self.d.line((a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0,
                             a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1),
                            fill=colour, width=width * SS)

    def image(self):
        return self.im.resize((self.n // SS, self.n // SS), Image.LANCZOS)


def rect(r):
    x0, y0, x1, y1 = r
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def draw_tag(spec, mode, op, turn=0, tile_mm=None, size_px=300, pins=None, zoom=None):
    """One tag from above. tile_mm: White + black's tile (unturned frame),
    default the sticker. pins: a pin shape under a New tile (dashed).
    zoom: (centre in pattern mm, span mm) to look at one corner."""
    axes = turned(turn)
    tw, th = tile_mm or tuple(v * 1000.0 for v in spec.tile_size_m)
    t_turned = (tw, th) if turn % 180 == 0 else (th, tw)
    mk = C.mark_for_spec(spec, mode, op, t_turned if mode == "both" else None)
    W = lambda p: (p[0] * axes[0][0] + p[1] * axes[1][0], p[0] * axes[0][1] + p[1] * axes[1][1])  # noqa: E731
    span = max(tw, th) * 1.25
    centre = (0.0, 0.0)
    if zoom:
        centre, span = W(zoom[0]), zoom[1]
    pn = Panel(size_px, span)
    shift = lambda p: (W(p)[0] - centre[0], W(p)[1] - centre[1])  # noqa: E731
    big = [(-span, -span), (span, -span), (span, span), (-span, span)]
    spec_tile = tuple(v * 1000.0 for v in spec.tile_size_m)
    st_rect = rect((-spec_tile[0] / 2, -spec_tile[1] / 2, spec_tile[0] / 2, spec_tile[1] / 2))
    tile_rect = rect((-t_turned[0] / 2, -t_turned[1] / 2, t_turned[0] / 2, t_turned[1] / 2))
    kind = mk.kind
    if mode == "both":
        pn.poly(big, PART)
        tile = mk.outline if kind == "tile_cut" else tile_rect
        pn.poly([shift(p) for p in tile], WHITE)
    elif mode == "black":
        pn.poly(big, WHITE_PART)
        pn.dashed([shift(p) for p in st_rect], (170, 170, 170))
    else:
        pn.poly(big, BLACK_PART)
        pn.poly([shift(p) for p in st_rect], WHITE)
    for r in (tuple(v * 1000.0 for v in b) for b in M.cell_boxes(spec)):
        if kind == "black_cut" and C.is_top_left(r, mk.corner):
            pn.poly([shift(p) for p in mk.outline], BLACK if mode != "white" else BLACK_PART)
        else:
            pn.poly([shift(p) for p in rect(r)], BLACK if mode != "white" else BLACK_PART)
    if kind == "triangle":
        pn.poly([shift(p) for p in mk.solid], BLACK if mode != "white" else BLACK_PART)
    if pins and mode == "both" and op == "new":
        cut = mk.leg if kind == "tile_cut" else 0.0
        for o in S.pin_outlines(pins, 3.0, t_turned[0], t_turned[1], cut=cut):
            pn.dashed([shift(p) for p in S.outline_points(o, 40)], (0, 120, 220), dash=4, width=2)
    pn.ring(shift(corner0(spec)))
    return pn.image(), mk


def label(im, lines, f):
    h = 8 + 22 * len(lines)
    out = Image.new("RGB", (im.width, im.height + h), (255, 255, 255))
    out.paste(im, (0, 0))
    d = ImageDraw.Draw(out)
    for i, t in enumerate(lines):
        d.text((8, im.height + 4 + 22 * i), t, fill=TEXT, font=f)
    return out


def grid_image(rows, f, title, ftitle):
    """rows: [[PIL image]]: one sheet, a title on top."""
    w = max(sum(im.width for im in r) + 12 * (len(r) - 1) for r in rows)
    h = sum(max(im.height for im in r) for r in rows) + 14 * (len(rows) - 1) + 50
    sheet = Image.new("RGB", (w + 24, h + 24), (255, 255, 255))
    ImageDraw.Draw(sheet).text((12, 10), title, fill=TEXT, font=ftitle)
    y = 56
    for r in rows:
        x = 12
        for im in r:
            sheet.paste(im, (x, y))
            x += im.width + 12
        y += max(im.height for im in r) + 14
    return sheet


def what(mk):
    """Two short lines: the mark, and its clearance."""
    if mk.kind == "none":
        return ["no mark", mk.note.split("--")[-1].strip()]
    m = mk.module
    first = {"tile_cut": f"tile corner cut, legs {mk.leg / m:.2g} mod",
             "black_cut": f"top-left square cut, legs {mk.leg / m:.2g} mod",
             "triangle": f"triangle, legs {mk.leg / m:.2g} mod"}[mk.kind]
    if mk.kind == "black_cut":
        return [first, f"{mk.clear / m:.1f} mod clear of ChArUco corners"]
    return [first, f"{mk.clear / m:.2f} mod ({mk.clear:.1f} mm) clear of black"]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    f, ft = font(15), font(22)
    tags = [("single 4x4, 30 mm", M.TagSpec(marker_id=3, marker_length_m=0.03)),
            ("grid 3x1", M.TagSpec(marker_length_m=0.03, layout="grid", rows=1, cols=3,
                                   separation_m=0.006)),
            ("ChArUco 3x3, 37 mm squares", M.TagSpec(marker_length_m=0.02775, layout="charuco",
                                                     rows=3, cols=3, square_length_m=0.037,
                                                     quiet_modules=0.0)),
            ("ChArUco 4x4 legacy (white top-left)",
             M.TagSpec(marker_length_m=0.0225, layout="charuco", rows=4, cols=4,
                       square_length_m=0.030, quiet_modules=0.0, legacy=True))]
    made = []
    rows = []
    for name, spec in tags:
        row = []
        for mode, op, mname in MODES:
            im, mk = draw_tag(spec, mode, op, size_px=280)
            row.append(label(im, [f"{name}", mname] + what(mk), f))
        rows.append(row)
    p = OUT / "impl_modes.png"
    grid_image(rows, f, "Chamfer top-left corner: every layout x mode (orange ring = OpenCV "
               "corner 0 / board origin)", ft).save(p)
    made.append(p)

    rows = []
    for name, spec in tags[:3]:
        row = []
        for turn, oname in ((0, "Top left"), (90, "Bottom left"), (180, "Bottom right"),
                            (270, "Top right")):
            tw, th = spec.tile_size_m
            if abs(tw - th) > 1e-9 and turn % 180:
                continue
            im, mk = draw_tag(spec, "both", "new", turn, size_px=250)
            row.append(label(im, [name, f"Origin: {oname} ({turn}°)"] + what(mk), f))
        rows.append(row)
    p = OUT / "impl_turns.png"
    grid_image(rows, f, "White + black, New body: the mark follows the Origin turn", ft).save(p)
    made.append(p)

    single = tags[0][1]
    m = single.module_m * 1000.0
    c0 = (-15.0 - m, 15.0 + m)
    rows = [[], []]
    for mode, op, mname in MODES:
        im, mk = draw_tag(single, mode, op, size_px=300, zoom=((c0[0] + 1.6 * m, c0[1] - 1.6 * m),
                                                                 4.2 * m))
        rows[0].append(label(im, ["single, top-left (1 mod = 5 mm)", mname] + what(mk), f))
    ch = tags[2][1]
    m2 = ch.module_m * 1000.0
    W = ch.extent_m[0] * 500.0
    for mode, op, mname in MODES[:1] + MODES[2:]:
        im, mk = draw_tag(ch, mode, op, size_px=300, zoom=((-W + 2.5 * m2, W - 2.5 * m2), 6.0 * m2))
        rows[1].append(label(im, ["ChArUco 3x3, top-left square", mname] + what(mk), f))
    p = OUT / "impl_corner_zoom.png"
    grid_image(rows, f, "The corner up close: the cut / triangle and corner 0", ft).save(p)
    made.append(p)

    rows = [[]]
    for shape in ("triangle", "round"):
        for tile in (None, (50.0, 40.0)):
            im, mk = draw_tag(single, "both", "new", size_px=280, pins=shape, tile_mm=tile)
            size = "sticker 40 mm" if tile is None else "sketch area 50 x 40 mm"
            rows[0].append(label(im, [f"{shape} pin (dashed: under the tile)", size,
                                      "1 mm clear of the cut"], f))
    p = OUT / "impl_pins.png"
    grid_image(rows, f, "Locating pins move in, clear of the cut corner", ft).save(p)
    made.append(p)
    for p in made:
        print(p.relative_to(ROOT))


if __name__ == "__main__":
    main()
