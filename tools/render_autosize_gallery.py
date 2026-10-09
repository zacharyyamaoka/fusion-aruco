# -*- coding: utf-8 -*-
"""Render the auto-size verification gallery offline (Pillow).

For every face in ArucoMarkers/aruco_autosize_gallery.FACES and every layout
(single, grid, ChArUco) at its AUTO default, and for the typed sizes: the
face outline and holes, the largest clear rectangle the size came from
(dashed blue), the tile / sticker (white, orange edge) and the real black
cells, with the numbers underneath. Writes

    reports/media/aruco/autosize/offline_<face>_<kind>.png      one panel each
    reports/media/aruco/autosize/offline_<face>_typed_<kind><mm>.png
    reports/media/aruco/autosize/gallery_offline.png            all defaults, one sheet
    reports/media/aruco/autosize/offline_gallery.json           every number

Run:  <clank-viser venv>/python tools/render_autosize_gallery.py
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "CADExporter"), str(ROOT / "ArucoMarkers")]

from bam_cad_core import aruco_autosize as A  # noqa: E402
from bam_cad_core import markers as M  # noqa: E402
import aruco_autosize_gallery as G  # noqa: E402

OUT = ROOT / "reports" / "media" / "aruco" / "autosize"
SS = 3                                   # supersampling
PANEL = 440                              # px, the drawing square
CAPTION = 118                            # px of text under it
BG = (247, 246, 242)
FACE = (214, 210, 201)
EDGE = (110, 106, 98)
CLEAR = (36, 89, 184)
TILE = (255, 255, 255)
TILE_EDGE = (235, 104, 52)
INK = (28, 27, 25)
MUTED = (108, 105, 97)
BAD = (179, 48, 45)


def _font(size, bold=False):
    for name in (("segoeuib.ttf" if bold else "segoeui.ttf"), "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _dashed(d, a, b, fill, width, dash):
    (x0, y0), (x1, y1) = a, b
    L = math.hypot(x1 - x0, y1 - y0)
    if L == 0:
        return
    n = max(1, int(L / dash))
    for i in range(0, n, 2):
        t0, t1 = i / n, min(1.0, (i + 1) / n)
        d.line([(x0 + (x1 - x0) * t0, y0 + (y1 - y0) * t0), (x0 + (x1 - x0) * t1, y0 + (y1 - y0) * t1)],
               fill=fill, width=width)


def panel(f, lay, pl, title, lines, ok=True):
    """One face + one layout -> a PIL image (PANEL x PANEL+CAPTION)."""
    x0, y0, x1, y1 = G.bbox_mm(f)
    span = max(x1 - x0, y1 - y0) * 1.08
    cxm, cym = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    S = PANEL * SS
    k = S / span

    def P(x_mm, y_mm):
        return ((x_mm - cxm) * k + S / 2.0, S / 2.0 - (y_mm - cym) * k)

    img = Image.new("RGB", (S, S), BG)
    d = ImageDraw.Draw(img)
    loops = G.loops_mm(f, tol=0.01)
    d.polygon([P(*p) for p in loops[0]], fill=FACE, outline=EDGE, width=2 * SS)
    for h in loops[1:]:
        d.polygon([P(*p) for p in h], fill=BG, outline=EDGE, width=2 * SS)
    if pl is not None and lay.marker_m > 0:
        t = math.radians(pl.get("angle_deg", 0.0))
        c, s = math.cos(t), math.sin(t)

        def R(u_mm, v_mm, ox, oy):
            return P(ox + u_mm * c - v_mm * s, oy + u_mm * s + v_mm * c)
        cl = pl.get("clear")
        if cl:
            ccx, ccy, cw, ch = (cl[k_] * 1000.0 for k_ in ("cx", "cy", "w", "h"))
            pts = [R(-cw / 2, -ch / 2, ccx, ccy), R(cw / 2, -ch / 2, ccx, ccy),
                   R(cw / 2, ch / 2, ccx, ccy), R(-cw / 2, ch / 2, ccx, ccy)]
            for i in range(4):
                _dashed(d, pts[i], pts[(i + 1) % 4], CLEAR, 2 * SS, 7 * SS)
        tcx, tcy, tw, th = pl["cx"] * 1000.0, pl["cy"] * 1000.0, pl["w"] * 1000.0, pl["h"] * 1000.0
        tile = [R(-tw / 2, -th / 2, tcx, tcy), R(tw / 2, -th / 2, tcx, tcy),
                R(tw / 2, th / 2, tcx, tcy), R(-tw / 2, th / 2, tcx, tcy)]
        d.polygon(tile, fill=TILE, outline=TILE_EDGE, width=2 * SS)
        spec = lay.apply(M.TagSpec(dictionary=lay.dictionary, marker_id=0))
        for (bx0, by0, bx1, by1) in M.cell_boxes(spec):
            q = [R(bx0 * 1000, by0 * 1000, tcx, tcy), R(bx1 * 1000, by0 * 1000, tcx, tcy),
                 R(bx1 * 1000, by1 * 1000, tcx, tcy), R(bx0 * 1000, by1 * 1000, tcx, tcy)]
            d.polygon(q, fill=INK)
        for i in range(4):                       # the sticker edge on top of the cells
            d.line([tile[i], tile[(i + 1) % 4]], fill=TILE_EDGE, width=2 * SS)
    img = img.resize((PANEL, PANEL), Image.LANCZOS)
    out = Image.new("RGB", (PANEL, PANEL + CAPTION), BG)
    out.paste(img, (0, 0))
    d = ImageDraw.Draw(out)
    d.text((12, PANEL + 4), title, font=_font(19, True), fill=INK if ok else BAD)
    y = PANEL + 32
    for ln in lines:
        d.text((12, y), ln, font=_font(15), fill=MUTED)
        y += 20
    return out


def _numbers(lay, pl, dt):
    n = G.summary(lay, pl)
    n["fit_ms"] = round(dt * 1000.0, 1)
    if lay.marker_m > 0:
        n["module_mm"] = round(lay.module_m * 1000.0, 4)
    return n


def _lines(lay, pl, dt):
    if lay.marker_m <= 0:
        return ["nothing fits", "; ".join(lay.notes)[:60]]
    tw, th = (v * 1000.0 for v in lay.tile_m)
    box = "%.1f" % tw if abs(tw - th) < 1e-6 else "%.1f × %.1f" % (tw, th)
    if lay.kind == "charuco":
        first = "board %s mm (no border)" % box
    else:
        first = "sticker %s mm (marker + 1 module)" % box
    cl = (pl or {}).get("clear")
    second = ("largest clear %.1f × %.1f mm" % (cl["w"] * 1000.0, cl["h"] * 1000.0)) if cl else \
        "typed size, as many as fit"
    extra = [t for t in lay.notes if t.startswith(("clamped", "nothing printable", "default raised"))]
    third = ("%s · fit %.0f ms" % (extra[0][:44], dt * 1000.0)) if extra else \
        ("fill %.0f %% of the face · fit %.0f ms" % (100.0 * lay.fill, dt * 1000.0))
    return [first, second, third]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    data = {"built": time.strftime("%Y-%m-%d %H:%M"), "faces": []}
    sheet_rows = []
    for f in G.FACES:
        loops = G.loops_m(f)
        entry = {"key": f["key"], "title": f["title"], "bbox_mm": [round(v, 3) for v in G.bbox_mm(f)],
                 "area_mm2": round((abs(A._area(A._clean(loops[0])))
                                    - sum(abs(A._area(A._clean(h))) for h in loops[1:])) * 1e6, 2),
                 "auto": {}, "typed": []}
        row = []
        for kind in G.KINDS:
            t0 = time.perf_counter()
            lay, pl = A.fit_on_loops(kind, loops)
            dt = time.perf_counter() - t0
            img = panel(f, lay, pl, lay.label(), _lines(lay, pl, dt), lay.ok)
            name = "offline_%s_%s.png" % (f["key"], kind)
            img.save(OUT / name)
            entry["auto"][kind] = dict(_numbers(lay, pl, dt), png=name)
            row.append(img)
        for kind, L, S in G.typed_for(f["key"]):
            t0 = time.perf_counter()
            lay, pl = A.fit_on_loops(kind, loops, marker_m=L / 1000.0, square_m=S / 1000.0)
            dt = time.perf_counter() - t0
            img = panel(f, lay, pl, "typed %.1f → %s" % (L, lay.label()), _lines(lay, pl, dt), lay.ok)
            name = "offline_%s_typed_%s%g.png" % (f["key"], kind, L)
            img.save(OUT / name)
            entry["typed"].append(dict(_numbers(lay, pl, dt), typed_mm=L, kind_typed=kind, png=name))
        data["faces"].append(entry)
        sheet_rows.append((f, row))
    # one sheet: a title column + the three defaults per face
    head = 300
    W = head + 3 * PANEL + 4 * 8
    H = len(sheet_rows) * (PANEL + CAPTION + 8) + 60
    sheet = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(sheet)
    d.text((12, 14), "ArUco Extrude: auto size on a clicked face (offline, %s)" % data["built"],
           font=_font(24, True), fill=INK)
    for x, lab in ((head + 8, "single"), (head + 16 + PANEL, "grid"), (head + 24 + 2 * PANEL, "ChArUco")):
        d.text((x + 8, 40 - 4), lab, font=_font(16, True), fill=MUTED)
    y = 60
    for f, row in sheet_rows:
        import textwrap
        yy = y + 12
        for ln in textwrap.wrap(f["title"], 26):
            d.text((12, yy), ln, font=_font(18, True), fill=INK)
            yy += 24
        d.text((12, yy + 6), "face key: %s" % f["key"], font=_font(14), fill=MUTED)
        for j, img in enumerate(row):
            sheet.paste(img, (head + 8 + j * (PANEL + 8), y))
        y += PANEL + CAPTION + 8
    sheet.save(OUT / "gallery_offline.png", optimize=True)
    (OUT / "offline_gallery.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    print("wrote %s and %d panels" % (OUT / "gallery_offline.png", sum(1 for _ in OUT.glob("offline_*.png"))))


if __name__ == "__main__":
    main()
