"""Offline tests for aruco_board (grid + ChArUco boards) and boards on sheets.

Stdlib only. Every pinned value below was read from OpenCV 5.0.0 —
board.generateImage bitmaps at 1 px per module, board.getObjPoints(),
getChessboardCorners() — not from our own code. tools/verify_aruco_boards.py
is the full OpenCV check (pixel-exact renders, detection, pose).

Run:  python tests/test_aruco_board.py
"""
from __future__ import annotations

import json
import re
import struct
import sys
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "CADExporter"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bam_cad_core import aruco_board, aruco_dict  # noqa: E402
from bam_cad_core.aruco_board import BoardSpec  # noqa: E402
from bam_cad_core.print_sheet import (PrintItem, SheetSettings,  # noqa: E402
                                      layout_sheet, layout_summary, render_pdf)
from test_aruco_output import overlaps, pdf_pages, read_png  # noqa: E402

PASS = 0
FAIL = []


def check(label, condition, detail=""):
    global PASS
    if condition:
        PASS += 1
        print(f"  ok  {label}")
    else:
        FAIL.append(label)
        print(f"FAIL  {label}  {detail}")


def raises(fn, exc=ValueError):
    try:
        fn()
    except exc:
        return True
    except Exception:  # noqa: BLE001
        return False
    return False


# board.generateImage(outSize, marginSize=0, borderBits=1) with lengths in
# PIXELS (1 px per module), "#" = black. Spec lengths below are px / 1000 m.
PINNED_BITMAPS = {
    # GridBoard((2, 2), 6, 2, DICT_4X4_50, ids 3..6)
    "grid_2x2_ids3": (BoardSpec("grid", "DICT_4X4_50", 3, 0.006, rows=2, cols=2,
                                separation_m=0.002), [
        "######..######",
        "#.##.#..##.#.#",
        "#.##.#..##.###",
        "##.###..#.##.#",
        "##..##..#...##",
        "######..######",
        "..............",
        "..............",
        "######..######",
        "##...#..#.##.#",
        "#.##.#..#...##",
        "#..###..###.##",
        "#..#.#..#...##",
        "######..######"]),
    # CharucoBoard((3, 3), 8, 6, DICT_4X4_50, ids 0..3)
    "charuco_3x3": (BoardSpec("charuco", "DICT_4X4_50", 0, 0.006, rows=3, cols=3,
                              square_length_m=0.008), [
        "########........########",
        "########.######.########",
        "########.#.#..#.########",
        "########.##.#.#.########",
        "########.###..#.########",
        "########.###.##.########",
        "########.######.########",
        "########........########",
        "........########........",
        ".######.########.######.",
        ".######.########.###..#.",
        ".#....#.########.###..#.",
        ".#.##.#.########.###.##.",
        ".#.#.##.########.#..#.#.",
        ".######.########.######.",
        "........########........",
        "########........########",
        "########.######.########",
        "########.#.##.#.########",
        "########.#.##.#.########",
        "########.##.###.########",
        "########.##..##.########",
        "########.######.########",
        "########........########"]),
    # CharucoBoard((3, 2), 8, 6, DICT_4X4_50, ids 5..7) — even rows, new pattern
    "charuco_3x2": (BoardSpec("charuco", "DICT_4X4_50", 5, 0.006, rows=2, cols=3,
                              square_length_m=0.008), [
        "########........########",
        "########.######.########",
        "########.##...#.########",
        "########.#.##.#.########",
        "########.#..###.########",
        "########.#..#.#.########",
        "########.######.########",
        "########........########",
        "........########........",
        ".######.########.######.",
        ".#.##.#.########.#..###.",
        ".#...##.########.##.###.",
        ".###.##.########.#....#.",
        ".#...##.########.###.##.",
        ".######.########.######.",
        "........########........"]),
    # ... and the same with setLegacyPattern(True): top-left square turns WHITE
    "charuco_3x2_legacy": (BoardSpec("charuco", "DICT_4X4_50", 5, 0.006, rows=2, cols=3,
                                     square_length_m=0.008, legacy=True), [
        "........########........",
        ".######.########.######.",
        ".##...#.########.#.##.#.",
        ".#.##.#.########.#...##.",
        ".#..###.########.###.##.",
        ".#..#.#.########.#...##.",
        ".######.########.######.",
        "........########........",
        "########........########",
        "########.######.########",
        "########.#..###.########",
        "########.##.###.########",
        "########.#....#.########",
        "########.###.##.########",
        "########.######.########",
        "########........########"]),
}


def raster(spec, px_per_m):
    """Independent rasteriser: pixel is ink when its centre is in a rect."""
    w, h = aruco_board.extent_m(spec)
    W, H = round(w * px_per_m), round(h * px_per_m)
    rects = aruco_board.black_rects_m(spec)
    rows = []
    for r in range(H):
        y = h / 2.0 - (r + 0.5) / px_per_m
        row = []
        for c in range(W):
            x = (c + 0.5) / px_per_m - w / 2.0
            row.append("#" if any(x0 <= x < x1 and y0 < y <= y1
                                  for x0, y0, x1, y1 in rects) else ".")
        rows.append("".join(row))
    return rows


def mat_apply(t, p):
    x, y, z = p
    return (t[0] * x + t[1] * y + t[2] * z + t[3],
            t[4] * x + t[5] * y + t[6] * z + t[7],
            t[8] * x + t[9] * y + t[10] * z + t[11])


def close(a, b, tol=1e-9):
    return all(abs(u - v) <= tol for u, v in zip(a, b))


GRID = BoardSpec("grid", "DICT_4X4_50", 10, 0.04, rows=2, cols=3, separation_m=0.01)
CHARUCO = BoardSpec("charuco", "DICT_4X4_50", 0, 0.03, rows=4, cols=5, square_length_m=0.04)
SINGLE = BoardSpec("single", "DICT_4X4_50", 7, 0.03)


def main():
    print("== counts, ids, extents ==")
    check("grid 3x2: 6 ids 10..15 row-major", aruco_board.ids(GRID) == list(range(10, 16)))
    check("charuco 5x4: 10 markers", aruco_board.marker_count(CHARUCO) == 10)
    check("charuco 5x7: 17 markers, 3x3: 4 (OpenCV's counts)",
          aruco_board.marker_count(BoardSpec("charuco", "DICT_4X4_50", 0, 0.03, rows=7, cols=5,
                                             square_length_m=0.04)) == 17
          and aruco_board.marker_count(BoardSpec("charuco", "DICT_4X4_50", 0, 0.03, rows=3,
                                                 cols=3, square_length_m=0.04)) == 4)
    check("single: one id", aruco_board.ids(SINGLE) == [7])
    check("extent single", aruco_board.extent_m(SINGLE) == (0.03, 0.03))
    gw, gh = aruco_board.extent_m(GRID)
    check("extent grid = cols*L+(cols-1)*sep x rows*L+(rows-1)*sep",
          close((gw, gh), (0.14, 0.09)), (gw, gh))
    check("extent charuco = cols*sq x rows*sq",
          close(aruco_board.extent_m(CHARUCO), (0.2, 0.16)))

    print("== validation ==")
    check("unknown layout", raises(lambda: aruco_board.ids(BoardSpec("diamond", "DICT_4X4_50", 0, 0.03))))
    check("grid with separation 0 refused (OpenCV asserts > 0)",
          raises(lambda: aruco_board.ids(BoardSpec("grid", "DICT_4X4_50", 0, 0.03, rows=2, cols=2))))
    check("charuco square <= marker refused",
          raises(lambda: aruco_board.ids(BoardSpec("charuco", "DICT_4X4_50", 0, 0.03, rows=3,
                                                   cols=3, square_length_m=0.03))))
    check("charuco needs >= 2x2",
          raises(lambda: aruco_board.ids(BoardSpec("charuco", "DICT_4X4_50", 0, 0.03, rows=1,
                                                   cols=4, square_length_m=0.04))))
    try:
        aruco_board.ids(BoardSpec("grid", "DICT_4X4_50", 45, 0.03, rows=2, cols=3,
                                  separation_m=0.01))
        check("ids past the dictionary refused", False)
    except ValueError as exc:
        check("ids past the dictionary refused, naming the range", "45..50" in str(exc), str(exc))
    check("single with rows=2 refused",
          raises(lambda: aruco_board.ids(BoardSpec("single", "DICT_4X4_50", 0, 0.03, rows=2))))

    print("== ink == generateImage (pinned from cv2) ==")
    for name, (spec, expected) in PINNED_BITMAPS.items():
        got = raster(spec, 1000.0)
        check(f"{name}: black_rects_m raster == generateImage", got == expected,
              "\n" + "\n".join(got))
        w, h, _, _, rows = read_png(aruco_board.board_png(spec, px_per_m=1000.0))
        png_rows = ["".join("#" if v < 128 else "." for v in r) for r in rows]
        check(f"{name}: board_png == generateImage", png_rows == expected)
    bad = []
    for spec in (GRID, CHARUCO, PINNED_BITMAPS["charuco_3x2_legacy"][0]):
        rects = aruco_board.black_rects_m(spec)
        w, h = aruco_board.extent_m(spec)
        for i, a in enumerate(rects):
            if not (a[0] < a[2] and a[1] < a[3] and a[0] >= -w / 2 - 1e-12
                    and a[2] <= w / 2 + 1e-12 and a[1] >= -h / 2 - 1e-12 and a[3] <= h / 2 + 1e-12):
                bad.append((spec.layout, "bounds", a))
            for b in rects[i + 1:]:
                if overlaps((a[0], a[1], a[2] - a[0], a[3] - a[1]),
                            (b[0], b[1], b[2] - b[0], b[3] - b[1]), eps=1e-12):
                    bad.append((spec.layout, "overlap", a, b))
    check("rects are disjoint, ordered (x0<x1, y0<y1), inside the extent", not bad, bad[:3])
    module = 0.03 / 6
    expect = sorted((-0.015 + x * module, 0.015 - (y + h) * module,
                     -0.015 + (x + w) * module, 0.015 - y * module)
                    for x, y, w, h in aruco_dict.black_rects("DICT_4X4_50", 7))
    check("single == aruco_dict.black_rects scaled, centred, y up",
          all(close(a, b) for a, b in zip(sorted(aruco_board.black_rects_m(SINGLE)), expect))
          and len(expect) == len(aruco_board.black_rects_m(SINGLE)))

    print("== OpenCV's frame (pinned from cv2 5.0 objPoints) ==")
    op = aruco_board.object_points(GRID)
    check("grid id 13 objPoints == cv2 (y DOWN from the top-left corner)",
          all(close(a, b) for a, b in zip(op[13], [(0, .05, 0), (.04, .05, 0), (.04, .09, 0), (0, .09, 0)])),
          op[13])
    op = aruco_board.object_points(CHARUCO)
    check("charuco marker 0 objPoints == cv2 (square col 1, row 0)",
          all(close(a, b) for a, b in zip(op[0], [(.045, .005, 0), (.075, .005, 0),
                                                  (.075, .035, 0), (.045, .035, 0)])), op[0])
    cc = aruco_board.charuco_corners(CHARUCO)
    check("chessboard corners == cv2 (12, from (sq, sq), row-major)",
          len(cc) == 12 and all(close(cc[i], v) for i, v in
                                ((0, (.04, .04, 0)), (1, (.08, .04, 0)), (4, (.04, .08, 0)),
                                 (11, (.16, .12, 0)))), cc[:5])
    leg = BoardSpec("charuco", "DICT_4X4_50", 0, 0.03, rows=4, cols=5, square_length_m=0.04,
                    legacy=True)
    check("legacy (even rows): marker 0 moves to square (0, 0) as in cv2",
          close(aruco_board.object_points(leg)[0][0], (.005, .005, 0)))
    check("opencv_frame(single) is the identity",
          aruco_board.opencv_frame(SINGLE) == [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])
    check("opencv_frame(grid 3x2) pinned",
          close(aruco_board.opencv_frame(GRID),
                [1, 0, 0, -0.07, 0, -1, 0, 0.045, 0, 0, -1, 0, 0, 0, 0, 1]),
          aruco_board.opencv_frame(GRID))
    check("opencv_frame(charuco 5x4) pinned",
          close(aruco_board.opencv_frame(CHARUCO),
                [1, 0, 0, -0.1, 0, -1, 0, 0.08, 0, 0, -1, 0, 0, 0, 0, 1]))
    consistent = True
    for spec in (SINGLE, GRID, CHARUCO, leg):
        t = aruco_board.opencv_frame(spec)
        centre = aruco_board.marker_corners_m(spec)
        for mid, pts in aruco_board.object_points(spec).items():
            for p_cv, p_c in zip(pts, centre[mid]):
                q = mat_apply(t, p_cv)
                consistent &= close(q, (p_c[0], p_c[1], 0.0), 1e-12)
    check("opencv_frame maps object_points onto marker_corners_m (all layouts)", consistent)
    t = aruco_board.opencv_frame(GRID)
    check("grid frame is right-handed with z INTO the board (z_cv = -z_centre)",
          (t[2], t[6], t[10]) == (0.0, 0.0, -1.0))

    print("== board_png / board_svg ==")
    png = aruco_board.board_png(GRID, px_per_m=2000.0, quiet_m=0.005)
    w, h, _, _, rows = read_png(png)
    check("board_png size = extent*px_per_m + 2*quiet", (w, h) == (280 + 20, 180 + 20), (w, h))
    phys = png.find(b"pHYs")
    check("board_png carries pHYs (px per metre)",
          phys > 0 and struct.unpack(">IIB", png[phys + 4:phys + 13]) == (2000, 2000, 1))
    check("board_png quiet zone is white", all(v == 255 for v in rows[0]) and rows[10][10] == 0)
    svg = aruco_board.board_svg(CHARUCO, quiet_m=0.005)
    root = ET.fromstring(svg)
    check("board_svg viewBox in mm incl. quiet zone", root.get("viewBox") == "-5 -5 210 170",
          root.get("viewBox"))
    check("board_svg width/height in mm", (root.get("width"), root.get("height")) == ("210mm", "170mm"))
    path = [e for e in root if e.tag.endswith("path")][0].get("d")
    check("board_svg: one subpath per rect",
          path.count("M") == len(aruco_board.black_rects_m(CHARUCO)))

    print("== boards on a print sheet ==")
    grid_item = PrintItem("grid", "DICT_4X4_50", 10, 0.025, quiet_modules=1.0,
                          layout="grid", rows=3, cols=2, separation_m=0.005)
    ch_item = PrintItem("ch", "DICT_5X5_100", 20, 0.015, quiet_modules=1.5,
                        layout="charuco", rows=7, cols=5, square_length_m=0.02)
    single = PrintItem("one", "DICT_4X4_50", 0, 0.03)
    lay = layout_sheet([single, grid_item, ch_item], SheetSettings())
    by = {p.item.key: p for p in lay.placed}
    check("all three placed", set(by) == {"grid", "ch", "one"} and not lay.rejected,
          [r.reason for r in lay.rejected])
    g = by["grid"]
    gmod = 25.0 / 6
    check("grid sticker = extent + 2*quiet*module, non-square",
          abs(g.extent_w_mm - 55.0) < 1e-9 and abs(g.extent_h_mm - 85.0) < 1e-9
          and abs(g.sticker_w_mm - (55 + 2 * gmod)) < 1e-9
          and abs(g.sticker_h_mm - (85 + 2 * gmod)) < 1e-9,
          (g.extent_w_mm, g.extent_h_mm, g.sticker_w_mm, g.sticker_h_mm))
    c = by["ch"]
    check("charuco sticker = 100 x 140 + 2*1.5*module",
          abs(c.sticker_w_mm - (100 + 3 * 15.0 / 7)) < 1e-9
          and abs(c.sticker_h_mm - (140 + 3 * 15.0 / 7)) < 1e-9)
    check("boards never scaled: marker_mm == marker_length_m*1000",
          g.marker_mm == 25.0 and c.marker_mm == 15.0)
    s = by["one"]
    check("single marker unchanged: sticker_mm == w == h == marker + 2*module",
          s.sticker_mm == s.sticker_w_mm == s.sticker_h_mm and abs(s.sticker_mm - 40.0) < 1e-9
          and s.extent_w_mm == s.extent_h_mm == 30.0)
    check("tallest sticker first", lay.pages[0][0].item.key == "ch")
    clash = [(a.item.key, b.item.key) for pg in lay.pages for a in pg for b in pg
             if a is not b and (overlaps(a.cell, b.cell)
                                or (b.label_box and overlaps((a.x_mm, a.y_mm, a.sticker_w_mm,
                                                              a.sticker_h_mm), b.label_box)))]
    check("no overlaps between board/single cells and labels", not clash, clash)
    check("board identity labels",
          g.label_lines == ["DICT_4X4_50 grid 2x3 #10–15 · 25.0 mm, gap 5.0 mm"]
          and c.label_lines == ["DICT_5X5_100 ChArUco 5x7 #20–36 · square 20.0 / "
                                "marker 15.0 mm"], (g.label_lines, c.label_lines))
    summ = layout_summary(lay)
    gs = [r for r in summ["placed"] if r["key"] == "grid"][0]
    check("layout_summary carries ids, size and opencv_frame for boards",
          gs["ids"] == list(range(10, 16)) and gs["layout"] == "grid"
          and len(gs["opencv_frame"]) == 16 and json.loads(json.dumps(summ)) == summ)
    pdf = render_pdf([single, grid_item, ch_item], SheetSettings())
    count, pages, _ = pdf_pages(pdf)
    check("PDF renders boards", pdf.startswith(b"%PDF-1.4") and count == lay.page_count)
    content = "\n".join(c for _, c in pages)
    n_rect = len(re.findall(r" re\n", content + "\n"))
    expected_rects = (sum(len(aruco_board.black_rects_m(
        aruco_board.BoardSpec(i.layout, i.dictionary, i.marker_id, i.marker_length_m, i.rows,
                              i.cols, i.separation_m, i.square_length_m, i.legacy)))
        for i in (single, grid_item, ch_item)))
    ruler_rects = 2 + 21 + 17          # two bars + their ticks, per page
    check("PDF draws exactly black_rects_m for every item (+ ruler)",
          n_rect == expected_rects + ruler_rects * len(pages), (n_rect, expected_rects))
    huge = PrintItem("huge", "DICT_4X4_50", 0, 0.05, layout="charuco", rows=6, cols=5,
                     square_length_m=0.06)
    bad_board = PrintItem("bad", "DICT_4X4_50", 0, 0.03, layout="charuco", rows=4, cols=4,
                          square_length_m=0.02)
    lay2 = layout_sheet([huge, bad_board], SheetSettings())
    rej = {r.item.key: r.reason for r in lay2.rejected}
    check("oversize board rejected, never scaled", "never scaled" in rej.get("huge", ""), rej)
    check("invalid board rejected with OpenCV's reason",
          "square_length_m" in rej.get("bad", ""), rej)

    print(f"\n{PASS} passed, {len(FAIL)} failed")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()
