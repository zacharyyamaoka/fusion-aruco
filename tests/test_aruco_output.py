"""Offline tests for the marker-output lane — aruco_dict + print_sheet.

Stdlib only. The pinned grids below were read from cv2.aruco.generateImageMarker
(OpenCV 5.0.0), not from our own table; tools/verify_aruco_output.py is the
full every-id OpenCV check.

Run:  python tests/test_aruco_output.py
"""
from __future__ import annotations

import hashlib
import json
import re
import struct
import sys
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "CADExporter"))

from bam_cad_core import aruco_dict, print_sheet  # noqa: E402
from bam_cad_core.aruco_dict import (DICTIONARY_NAMES, black_rects,  # noqa: E402
                                     dictionary_info, marker_grid, marker_png,
                                     marker_svg)
from bam_cad_core.fiducials import ARUCO_DICTS  # noqa: E402
from bam_cad_core.print_sheet import (PAGE_SIZES_MM, PrintItem,  # noqa: E402
                                      SheetSettings, layout_sheet,
                                      layout_summary, render_pdf)

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
    except Exception:  # noqa: BLE001 - wrong exception type is a failure
        return False
    return False


# From cv2.aruco.generateImageMarker(dict, id, (n+2)*10, borderBits=1), sampled
# at module centres; "1" = black. Row 0 is the top of the image.
PINNED = {
    ("DICT_4X4_50", 0): ["111111", "101001", "110101", "111001", "111011", "111111"],
    ("DICT_4X4_50", 17): ["111111", "110011", "110011", "111111", "111111", "111111"],
    ("DICT_6X6_250", 23): ["11111111", "10110011", "10110101", "11100001",
                           "11001101", "10011001", "10011001", "11111111"],
    ("DICT_APRILTAG_36h11", 0): ["11111111", "11101111", "11001011", "11110101",
                                 "11110011", "10100011", "10101001", "11111111"],
    ("DICT_APRILTAG_36h11", 586): ["11111111", "10011001", "10100101", "11101101",
                                   "10000101", "10011111", "11010001", "11111111"],
    ("DICT_ARUCO_ORIGINAL", 1023): ["1111111", "1100011", "1100011", "1100011",
                                    "1100011", "1100011", "1111111"],
    ("DICT_APRILTAG_16h5", 29): ["111111", "110001", "110111", "100001", "110101",
                                 "111111"],
    ("DICT_7X7_1000", 999): ["111111111", "100011001", "100001101", "101011011",
                             "100100011", "111011111", "111001011", "110000001",
                             "111111111"],
}


def all_markers():
    for name in DICTIONARY_NAMES:
        for i in range(dictionary_info(name)["capacity"]):
            yield name, i


# ------------------------------------------------------------------ PNG reader


def read_png(data):
    """Minimal decoder for what marker_png writes (8-bit grey, filter 0)."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "signature"
    pos, chunks = 8, []
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        (crc,) = struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])
        assert crc == zlib.crc32(kind + body) & 0xFFFFFFFF, "crc"
        chunks.append((kind, body))
        pos += 12 + length
    assert chunks[0][0] == b"IHDR" and chunks[-1][0] == b"IEND"
    w, h, depth, ctype, _, _, _ = struct.unpack(">IIBBBBB", chunks[0][1])
    raw = zlib.decompress(b"".join(b for k, b in chunks if k == b"IDAT"))
    stride = w + 1
    rows = []
    for y in range(h):
        line = raw[y * stride:(y + 1) * stride]
        assert line[0] == 0, "filter"
        rows.append(line[1:])
    return w, h, depth, ctype, rows


# ------------------------------------------------------------------ geometry


def overlaps(a, b, eps=1e-9):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw - eps and bx < ax + aw - eps and ay < by + bh - eps and by < ay + ah - eps


def inside(a, area, eps=1e-6):
    ax, ay, aw, ah = a
    x, y, w, h = area
    return ax >= x - eps and ay >= y - eps and ax + aw <= x + w + eps and ay + ah <= y + h + eps


def sample_items(count=30):
    names = ["DICT_4X4_50", "DICT_5X5_100", "DICT_6X6_250", "DICT_APRILTAG_36h11",
             "DICT_ARUCO_ORIGINAL", "DICT_7X7_1000"]
    sizes = [15.0, 20.0, 27.1, 36.0, 45.0, 60.0]
    items = []
    for k in range(count):
        name = names[k % len(names)]
        size = sizes[(k * 5) % len(sizes)]
        labels = [] if k % 3 else [f"{name} #{k} · {size} mm", f"Arm/Actuator:{k} (front)"]
        items.append(PrintItem(key=f"m{k}", dictionary=name, marker_id=k,
                               marker_length_m=size / 1000.0,
                               quiet_modules=1.0 if k % 4 else 2.0,
                               label_lines=labels))
    return items


def pdf_pages(pdf):
    """[(mediabox numbers, decompressed content)] in /Kids order."""
    objs = {int(m.group(1)): m.group(2) for m in
            re.finditer(rb"(\d+) 0 obj\n(.*?)\nendobj\n", pdf, re.S)}
    root = objs[1]
    pages_ref = int(re.search(rb"/Pages (\d+) 0 R", root).group(1))
    kids = [int(k) for k in re.findall(rb"(\d+) 0 R", re.search(
        rb"/Kids \[(.*?)\]", objs[pages_ref]).group(1))]
    count = int(re.search(rb"/Count (\d+)", objs[pages_ref]).group(1))
    out = []
    for kid in kids:
        page = objs[kid]
        box = [float(v) for v in re.search(rb"/MediaBox \[(.*?)\]", page).group(1).split()]
        content_ref = int(re.search(rb"/Contents (\d+) 0 R", page).group(1))
        body = objs[content_ref]
        stream = body.split(b"stream\n", 1)[1].rsplit(b"\nendstream", 1)[0]
        length = int(re.search(rb"/Length (\d+)", body).group(1))
        assert len(stream) == length, "stream length"
        out.append((box, zlib.decompress(stream).decode("ascii")))
    return count, out, objs


def main():
    print("== the table ==")
    doc = json.loads(Path(aruco_dict.TABLE_FILE).read_text(encoding="utf-8"))
    digest = hashlib.sha256(json.dumps(doc["payload"], sort_keys=True,
                                       separators=(",", ":")).encode("utf-8")).hexdigest()
    check("payload sha256 matches the header", digest == doc["payload_sha256"],
          (digest, doc["payload_sha256"]))
    check("header names the generator and OpenCV version",
          doc["generator"].get("opencv") and doc["generator"].get("date"))
    check("table names == fiducials.ARUCO_DICTS (same order)",
          doc["payload"]["names"] == list(ARUCO_DICTS))
    check("DICTIONARY_NAMES == fiducials.ARUCO_DICTS (same order)",
          DICTIONARY_NAMES == tuple(ARUCO_DICTS))
    bad_caps = [n for n in DICTIONARY_NAMES
                if dictionary_info(n)["capacity"] != ARUCO_DICTS[n]]
    check("capacities match fiducials.ARUCO_DICTS", not bad_caps, bad_caps)
    for name in DICTIONARY_NAMES:
        entry = doc["payload"]["dictionaries"][name]
        table = doc["payload"]["tables"][entry["table"]]
        width = (entry["bits"] ** 2 + 3) // 4
        if not (len(table["markers"]) >= entry["capacity"] and table["bits"] == entry["bits"]
                and all(len(h) == width for h in table["markers"])):
            check(f"{name}: table shape", False)
            break
    else:
        check("every table holds capacity markers of ceil(n*n/4) hex digits", True)
    fams = {n: dictionary_info(n)["family"] for n in DICTIONARY_NAMES}
    check("families", fams["DICT_4X4_50"] == "aruco" and fams["DICT_7X7_1000"] == "aruco"
          and fams["DICT_APRILTAG_36h11"] == "apriltag"
          and fams["DICT_ARUCO_ORIGINAL"] == "aruco_original", fams)
    info = dictionary_info("DICT_5X5_100")
    check("dictionary_info keys", {"bits", "capacity", "family"} <= set(info)
          and info["bits"] == 5 and info["capacity"] == 100 and info["modules"] == 7, info)
    check("case-insensitive name (OpenCV spells 36H11 both ways)",
          dictionary_info("DICT_APRILTAG_36H11")["name"] == "DICT_APRILTAG_36h11")

    print("== marker_grid ==")
    for (name, i), rows in PINNED.items():
        got = ["".join(str(v) for v in row) for row in marker_grid(name, i)]
        check(f"pinned {name} #{i}", got == rows, got)
    border_bad, dup_dicts, count = [], [], 0
    for name in DICTIONARY_NAMES:
        seen = set()
        for i in range(dictionary_info(name)["capacity"]):
            g = marker_grid(name, i)
            count += 1
            side = len(g)
            if side != dictionary_info(name)["bits"] + 2 or any(len(r) != side for r in g):
                border_bad.append((name, i, "shape"))
            if not (all(g[0]) and all(g[-1]) and all(r[0] for r in g) and all(r[-1] for r in g)):
                border_bad.append((name, i))
            seen.add(tuple(map(tuple, g)))
        if len(seen) != dictionary_info(name)["capacity"]:
            dup_dicts.append(name)
    check(f"all {count} markers: (n+2)^2 and border all black", not border_bad, border_bad[:5])
    check("markers within each dictionary are distinct", not dup_dicts, dup_dicts)
    g1 = marker_grid("DICT_4X4_50", 3)
    g1[1][1] = 7
    check("marker_grid returns a fresh list", marker_grid("DICT_4X4_50", 3)[1][1] in (0, 1))
    check("unknown dictionary raises ValueError", raises(lambda: marker_grid("DICT_9X9_50", 0)))
    check("id == capacity raises ValueError", raises(lambda: marker_grid("DICT_4X4_50", 50)))
    check("negative id raises ValueError", raises(lambda: marker_grid("DICT_4X4_50", -1)))
    check("bool id raises ValueError", raises(lambda: marker_grid("DICT_4X4_50", True)))
    check("last id of APRILTAG_36h11 (586) works, 587 raises",
          marker_grid("DICT_APRILTAG_36h11", 586) and raises(
              lambda: marker_grid("DICT_APRILTAG_36h11", 587)))
    try:
        marker_grid("DICT_4X4_50", 50)
    except ValueError as exc:
        check("out-of-range message names the valid range", "0..49" in str(exc), str(exc))

    print("== black_rects ==")
    bad = []
    for name, i in all_markers():
        g = marker_grid(name, i)
        side = len(g)
        cover = [[0] * side for _ in range(side)]
        for x, y, w, h in black_rects(name, i):
            for yy in range(y, y + h):
                for xx in range(x, x + w):
                    cover[yy][xx] += 1
        if cover != g:
            bad.append((name, i))
    check(f"black_rects cover exactly the black cells, no overlap ({count} markers)",
          not bad, bad[:5])
    rects = black_rects("DICT_4X4_50", 17)
    check("black_rects merges: DICT_4X4_50 #17 is 4 rects",
          rects == [(0, 0, 6, 1), (0, 1, 2, 2), (0, 3, 6, 3), (4, 1, 2, 2)], rects)

    print("== marker_png ==")
    png = marker_png("DICT_6X6_250", 23, module_px=7, quiet_modules=2)
    w, h, depth, ctype, rows = read_png(png)
    check("PNG size = (n+2+2q) * module_px", (w, h) == (8 * 7 + 28, 8 * 7 + 28), (w, h))
    check("PNG is 8-bit greyscale", (depth, ctype) == (8, 0))
    grid = marker_grid("DICT_6X6_250", 23)
    ok = all(rows[14 + r * 7 + 3][14 + c * 7 + 3] == (0 if grid[r][c] else 255)
             for r in range(8) for c in range(8))
    check("PNG module centres match marker_grid (1 = black pixel 0)", ok)
    quiet_ok = all(v == 255 for v in rows[0]) and all(rows[y][0] == 255 for y in range(h)) \
        and all(rows[y][13] == 255 for y in range(h)) and rows[14][14] == 0
    check("PNG quiet zone white, border starts after it", quiet_ok)
    w0, _, _, _, _ = read_png(marker_png("DICT_4X4_50", 0))
    check("PNG defaults: 20 px/module, 1-module quiet zone", w0 == 6 * 20 + 40, w0)
    check("marker_png rejects module_px=0", raises(lambda: marker_png("DICT_4X4_50", 0, 0)))

    print("== marker_svg ==")
    svg = marker_svg("DICT_4X4_50", 17, size="36mm", quiet_modules=1.5)
    root = ET.fromstring(svg)
    check("SVG viewBox in modules incl. quiet zone",
          root.get("viewBox") == "-1.5 -1.5 9 9", root.get("viewBox"))
    check("SVG width/height verbatim", root.get("width") == "36mm")
    path = [e for e in root if e.tag.endswith("path")][0].get("d")
    check("SVG path = one subpath per merged rect", path.count("M") == len(rects), path)
    bare = ET.fromstring(marker_svg("DICT_4X4_50", 0))
    check("SVG without size has no width/height (scales to container)",
          bare.get("width") is None and bare.get("viewBox") == "-1 -1 8 8",
          bare.attrib)

    print("== layout ==")
    items = sample_items(30)
    for page in ("letter", "a4"):
        settings = SheetSettings(page=page)
        lay = layout_sheet(items, settings)
        placed = lay.placed
        check(f"{page}: every item placed exactly once",
              sorted(p.item.key for p in placed) == sorted(i.key for i in items)
              and not lay.rejected, [r.reason for r in lay.rejected])
        check(f"{page}: spills onto >1 page", lay.page_count > 1, lay.page_count)
        check(f"{page}: never scales (marker_mm == marker_length_m*1000)",
              all(p.marker_mm == p.item.marker_length_m * 1000.0 for p in placed))
        geo_ok = all(abs(p.module_mm * p.modules - p.marker_mm) < 1e-9
                     and abs(p.sticker_mm - (p.marker_mm + 2 * p.item.quiet_modules
                                             * p.module_mm)) < 1e-9
                     and abs(p.marker_x_mm - p.x_mm - p.item.quiet_modules * p.module_mm) < 1e-9
                     for p in placed)
        check(f"{page}: sticker = marker + 2*quiet*module, marker centred in it", geo_ok)
        check(f"{page}: every cell inside the printable area",
              all(inside(p.cell, lay.printable_mm) for p in placed))
        clash = []
        for pg in lay.pages:
            for a in range(len(pg)):
                for b in range(a + 1, len(pg)):
                    if overlaps(pg[a].cell, pg[b].cell):
                        clash.append((pg[a].item.key, pg[b].item.key))
                stick = (pg[a].x_mm, pg[a].y_mm, pg[a].sticker_mm, pg[a].sticker_mm)
                for other in pg:
                    if other.label_box and overlaps(stick, other.label_box):
                        clash.append((pg[a].item.key, "label of " + other.item.key))
        check(f"{page}: no two cells (sticker + label) overlap; no label on a sticker",
              not clash, clash[:5])
        def separation(a, b):
            dx = max(b[0] - (a[0] + a[2]), a[0] - (b[0] + b[2]))
            dy = max(b[1] - (a[1] + a[3]), a[1] - (b[1] + b[3]))
            return max(dx, dy)
        gaps_ok = all(separation(a.cell, b.cell) >= settings.gap_mm - 1e-6
                      for pg in lay.pages for a in pg for b in pg if a is not b)
        check(f"{page}: gap_mm kept between cells", gaps_ok)
        check(f"{page}: largest sticker goes first",
              lay.pages[0][0].sticker_mm == max(p.sticker_mm for p in placed))
        label_ok = all(p.label_box and print_sheet.text_width_mm(line, p.label_font_pt)
                       <= p.label_box[2] + 1e-9 for p in placed for line in p.label_lines)
        check(f"{page}: every label line fits its label box", label_ok)
        check(f"{page}: deterministic",
              layout_summary(layout_sheet(items, settings)) == layout_summary(lay))
        rx, ry, rw, rh = lay.ruler["mm_bar"]
        check(f"{page}: ruler bar is 100 mm and below the sticker area",
              rw == 100.0 and ry > lay.printable_mm[1] + lay.printable_mm[3])
    summary = layout_summary(layout_sheet(items, SheetSettings()))
    check("layout_summary is JSON-able", json.loads(json.dumps(summary)) == summary)
    plain = PrintItem("plain", "DICT_5X5_100", 1, 0.0271)
    check("default label is the identity line",
          layout_sheet([plain]).pages[0][0].label_lines
          == ["DICT_5X5_100 #1 · 27.1 mm"],
          layout_sheet([plain]).pages[0][0].label_lines)

    huge = PrintItem("huge", "DICT_4X4_50", 1, 0.250)
    bad_id = PrintItem("bad_id", "DICT_4X4_50", 50, 0.03)
    bad_dict = PrintItem("bad_dict", "DICT_9X9_50", 1, 0.03)
    zero = PrintItem("zero", "DICT_4X4_50", 2, 0.0)
    lay = layout_sheet([huge, bad_id, bad_dict, zero, items[0]], SheetSettings())
    rej = {r.item.key: r.reason for r in lay.rejected}
    check("oversize marker rejected, not scaled", "huge" in rej
          and "never scaled" in rej["huge"], rej.get("huge"))
    check("out-of-range id / unknown dict / zero length rejected",
          {"bad_id", "bad_dict", "zero"} <= set(rej), rej)
    check("good item still placed beside rejections",
          [p.item.key for p in lay.placed] == ["m0"])
    big = [PrintItem("big", "DICT_4X4_50", 1, 0.140)]   # sticker 186.7 mm
    lay_p = layout_sheet(big, SheetSettings(page="letter"))
    lay_ls = layout_sheet(big, SheetSettings(page="letter", landscape=True))
    check("140 mm marker fits letter portrait at exactly 140 mm",
          lay_p.page_count == 1 and lay_p.placed[0].marker_mm == 140.0,
          [r.reason for r in lay_p.rejected])
    check("... and is rejected (not shrunk) on letter landscape",
          not lay_ls.placed and len(lay_ls.rejected) == 1
          and "never scaled" in lay_ls.rejected[0].reason)
    check("landscape swaps the page", lay_ls.page_mm == (279.4, 215.9), lay_ls.page_mm)
    check("custom_page_mm", layout_sheet([], SheetSettings(custom_page_mm=(100, 150)))
          .page_mm == (100.0, 150.0))
    check("narrow custom page drops the ruler with a warning",
          layout_sheet([], SheetSettings(custom_page_mm=(100, 150))).ruler is None
          and layout_sheet([], SheetSettings(custom_page_mm=(100, 150))).warnings)
    check("unknown page raises", raises(lambda: layout_sheet([], SheetSettings(page="b5"))))

    print("== PDF ==")
    expect = {("letter", False): [0, 0, 612, 792], ("a4", False): [0, 0, 595.2756, 841.8898],
              ("letter", True): [0, 0, 792, 612], ("a4", True): [0, 0, 841.8898, 595.2756]}
    for (page, landscape), box in expect.items():
        settings = SheetSettings(page=page, landscape=landscape, title="Test (sheet) \\ 1")
        lay = layout_sheet(items, settings)
        pdf = render_pdf(items, settings)
        tag = f"{page}{' landscape' if landscape else ''}"
        check(f"{tag}: starts with %PDF-1.4, ends with %%EOF",
              pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF"))
        count, pages, objs = pdf_pages(pdf)
        check(f"{tag}: /Count and page objects == layout pages ({lay.page_count})",
              count == len(pages) == lay.page_count, (count, len(pages)))
        check(f"{tag}: MediaBox {box}", all(b == box for b, _ in pages),
              [b for b, _ in pages][:1])
        xref = int(re.search(rb"startxref\n(\d+)", pdf).group(1))
        offs = [int(m) for m in re.findall(rb"(\d{10}) 00000 n", pdf[xref:])]
        check(f"{tag}: xref offsets point at their objects",
              all(pdf[o:].startswith(b"%d 0 obj" % (k + 1)) for k, o in enumerate(offs)))
        text = pages[0][1]
        check(f"{tag}: ruler text on page 1",
              "(Print at 100% / Actual size \\227 this bar must measure 100 mm) Tj" in text)
        check(f"{tag}: footer page i/N on every page",
              all(f"(page {i + 1}/{len(pages)}) Tj" in c for i, (_, c) in enumerate(pages)))
        check(f"{tag}: title escaped in footer",
              "(Test \\(sheet\\) \\\\ 1 \\267 " in text, re.findall(r"\(Test.*?Tj", text))
        n_fill = sum(c.count("\nf\n") + c.endswith("\nf") for _, c in pages)
        check(f"{tag}: one filled path per marker + ruler per page",
              n_fill == len(lay.placed) + len(pages), (n_fill, len(lay.placed)))
    empty = render_pdf([], SheetSettings())
    check("no items still yields a one-page PDF", pdf_pages(empty)[0] == 1)
    check("pdf_string escapes ( ) \\ and non-ASCII",
          print_sheet.pdf_string("a(b)c\\d ·—") == "(a\\(b\\)c\\\\d \\267\\227)")
    check("PAGE_SIZES_MM as specified",
          PAGE_SIZES_MM["letter"] == (215.9, 279.4) and PAGE_SIZES_MM["a4"] == (210, 297))

    print("== page margin (printer-safe border) ==")
    # Zach: "a lot of printers cut off the edges" -- nothing may be laid out
    # closer to the paper edge than margin_mm: stickers, their labels, ruler.
    items = [PrintItem(key=f"m{i}", dictionary="DICT_4X4_50", marker_id=i,
                       marker_length_m=0.03) for i in range(12)]
    for margin in (5.0, 20.0, 35.0):
        lay = layout_sheet(items, SheetSettings(page="letter", margin_mm=margin))
        pw, ph = lay.page_mm
        boxes = []
        for p in lay.placed:
            boxes.append((p.x_mm, p.y_mm, p.sticker_w_mm, p.sticker_h_mm))
            if p.label_box:
                boxes.append(p.label_box)
        if lay.ruler:
            for key in ("mm_bar", "in_bar"):
                boxes.append(tuple(lay.ruler[key]))
        all_in = all(x >= margin - 1e-6 and y >= margin - 1e-6 and x + w <= pw - margin + 1e-6
                     and y + h <= ph - margin + 1e-6 for x, y, w, h in boxes)
        check(f"margin {margin:g} mm: every sticker, label and ruler bar inside it",
              all_in and not lay.rejected, (margin, lay.rejected))

    print(f"\n{PASS} passed, {len(FAIL)} failed")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()
