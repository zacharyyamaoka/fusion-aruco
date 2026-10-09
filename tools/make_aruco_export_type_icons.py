"""Icons for the ArUco Export dialog's "Export" type list (Zach 2026-10-08:
"at the top you select what type it's going to be", like Fusion's Save As
Mesh, whose type list shows an icon per entry).

    python tools/make_aruco_export_type_icons.py            # write both folders
    python tools/make_aruco_export_type_icons.py --out DIR  # ... under DIR instead

Needs Pillow and numpy (e.g. the clank-viser venv). Draws with
make_aruco_icons' rasteriser and palettes, so the entries match the toolbar
family; never touches the toolbar icons themselves.

* bamArucoExportStl -- the family marker tile + a blue triangle-mesh badge:
  loose STL files.
* bamArucoExportPdf -- the print sheet with a marker on it (bamArucoExport
  without its export arrow): the true-size PDF.

The PrusaSlicer entry reuses bamArucoSendPrusa (a printer printing a marker).
Same file set per folder as make_aruco_icons.write_folder (16/32/64, @2x,
light + dark).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import make_aruco_icons as M  # noqa: E402


def draw_stl(cv: M.Canvas, pal, n: int):
    """Marker tile + a blue badge with a white triangulated quad (a mesh
    face: a square and its diagonal), bottom right. Whole pixels and a 45
    degree diagonal, so it stays crisp at 16 px."""
    u = n // 16
    M.draw_tile(cv, pal, 0, 0, n)
    g = M.Canvas(n)
    bx0, by0 = 7 * u, 7 * u
    g.fill(g.rrect(bx0, by0, n, n, 1.5 * u), pal["accent"])
    w = u                                         # stroke: 1 / 2 / 4 px
    s0, s1 = 9 * u, 14 * u                        # the square's outer edges, centred
    for x0, y0, x1, y1 in ((s0, s0, s1, s0 + w), (s0, s1 - w, s1, s1),
                           (s0, s0, s0 + w, s1), (s1 - w, s0, s1, s1)):
        g.fill(g.rect(x0, y0, x1, y1), pal["white"])
    # the diagonal, bottom-left to top-right, one w x w step per w px
    k = s0
    while k < s1:
        g.fill(g.rect(k, s1 - (k - s0) - w, k + w, s1 - (k - s0)), pal["white"])
        k += w
    M.overlay(cv, g, M.gap_for(n))


def draw_pdf(cv: M.Canvas, pal, n: int):
    """The print sheet (dog-eared, a marker and text lines), centred."""
    u = n // 16
    px0, py0, px1, py1 = 2 * u, 0, 14 * u, 16 * u
    ear = 3 * u
    e = 1 if n <= 32 else 2                      # outline width
    cv.fill(cv.poly([(px0, py0), (px1 - ear, py0), (px1, py0 + ear), (px1, py1), (px0, py1)]),
            pal["paper_edge"])
    cv.fill(cv.poly([(px0 + e, py0 + e), (px1 - ear - e * 0.41, py0 + e),
                     (px1 - e, py0 + ear + e * 0.41), (px1 - e, py1 - e), (px0 + e, py1 - e)]),
            pal["paper"])
    cv.fill(cv.poly([(px1 - ear, py0), (px1 - ear, py0 + ear), (px1, py0 + ear)]),
            pal["paper_edge"])
    cv.fill(cv.poly([(px1 - ear + e, py0 + e * 2.4), (px1 - ear + e, py0 + ear - e),
                     (px1 - e * 2.4, py0 + ear - e)]), pal["fold"])
    grid, _cw, _rh = M.marker_spec(n)
    if n <= 16:
        cw = rh = [1, 2, 2, 2, 1]
    else:
        m = 2 * (n // 32)                        # 2 px / module at 32
        cw = rh = [m] * 6
    mx, my = px0 + 2 * u, 2 * u
    y = my
    for r, h in enumerate(rh):
        x = mx
        for col, w in enumerate(cw):
            if grid[r][col]:
                cv.fill(cv.rect(x, y, x + w, y + h), pal["black"])
            x += w
        y += h
    ly = my + sum(rh) + (2 if n <= 16 else 2 * u)
    for i in range(2):
        y0 = ly + i * 2 * u
        if y0 + 1 > py1 - e:
            break
        cv.fill(cv.rect(px0 + 2 * u, y0, px1 - 2 * u - (3 * u if i else 0),
                        y0 + max(1, u // 2 + (u > 1))), pal["line"])


ICONS = {"bamArucoExportStl": draw_stl, "bamArucoExportPdf": draw_pdf}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=M.RESOURCES)
    args = ap.parse_args()
    M.ICONS.update(ICONS)                         # write_folder renders by name
    for name in ICONS:
        M.write_folder(args.out / name, name)
        print("wrote", args.out / name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
