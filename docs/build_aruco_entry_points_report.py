#!/usr/bin/env python3
# -*- coding: utf-8 -*-
""""ArUco Tags: five ways in" -- entry-point and ergonomics proposals, one page.

    python docs/build_aruco_entry_points_report.py
    -> reports/aruco/entry-points-proposals.html

Prose comes from docs/proposal_aruco_entry_points.md (rendered with the house
Markdown renderer from build_aruco_reports.py, so a doc edit is one rebuild
away from the page). The mock-ups are drawn here:

  * two real Fusion screenshots (reports/media/aruco/ui/01 and 06) are cleaned
    (the old tooltip and the old ARUCO panel painted out) and inlined ONCE as
    <image> definitions; every mock-up is an SVG that <use>s one of them and
    draws the proposal's toolbar panel, dialog, menu, palette, badges or toast
    on top in Fusion's dark theme;
  * every marker in an icon or a mock-up comes from bam_cad_core.aruco_dict's
    tables (identical to OpenCV's), so the family mark is a real DICT_4X4_50 id 7;
  * the step counts in the comparison table and in each proposal's job table
    are checked against one dictionary (STEPS) at build time; a mismatch prints.

Stdlib + Pillow. No OpenCV. Nothing on the page loads from the network.
"""
from __future__ import annotations

import base64
import io
import re
import sys
from datetime import date
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docs"))
sys.path.insert(0, str(ROOT / "CADExporter"))

import build_aruco_reports as B  # noqa: E402  (house style: CSS, JS, markdown, svg helpers)
from bam_cad_core import aruco_dict  # noqa: E402

SRC = ROOT / "docs" / "proposal_aruco_entry_points.md"
OUT = ROOT / "reports" / "aruco" / "entry-points-proposals.html"
UI = ROOT / "reports" / "media" / "aruco" / "ui"
PAGES = B.PAGES + [("entry", "entry-points-proposals.html", "Entry points")]
BUILT = date.today().isoformat()
D4 = "DICT_4X4_50"
WARN: list = []
esc = B.esc

# Fusion's dark theme, sampled from the screenshots / the how-it-works dialog figure
TB = "#2b2f34"            # toolbar; replaced by the sampled colour in main()
DLG, HD, FLD = "#3b3f46", "#2e3137", "#2a2d32"
INK, MUT = "#e8e8e8", "#a9adb5"
ACC, ACC2 = "#0696d7", "#6fc3ff"
GOOD, WARN_C, BAD = "#3fbf7f", "#e3a93c", "#e5534b"
MENU, MENU_HL = "#2d3136", "#3a5f85"
FF = 'font-family="Segoe UI, Arial, sans-serif"'

# clicks per job, the one place the numbers live: (a) make, (b) number six, (c) print clean / dup, (d) edit
STEPS = {"Today": ("3", "5", "2 / 8", "4"), "A": ("3", "4", "2 / 3", "4 (3*)"), "B": ("2", "2", "1 / 2", "3"),
         "C": ("3", "2", "3 / 4", "4"), "D": ("2", "2", "1 / 1", "4 (3*)"), "E": ("3", "5", "4 / 5", "3*")}
JOB_TABLES = {"4.2": "A", "5.2": "B", "6.2": "C", "7.2": "D", "8.2": "E"}


def warn(m):
    WARN.append(m)
    print("WARN:", m, file=sys.stderr)


# ================================================================ images

_BYTES = [0]


def uri(im: Image.Image, q=76) -> str:
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=q, optimize=True, progressive=True)
    _BYTES[0] += buf.tell()
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


BASE_W, BASE_H = 1228, 735


def clean_base(name: str, old_dialog: bool = False):
    """Screenshot -> (image, toolbar colour). Crops the data panel and the text
    commands, paints out the old tooltip and the old ARUCO panel (and, for the
    rectangle screenshot, the old ArUco Tag dialog) so every mock-up draws its
    own."""
    im = Image.open(UI / name).convert("RGB").crop((228, 0, 1456, BASE_H))
    px = im.load()
    for y in range(74, 134):                       # the tooltip over the viewport
        c = px[878, y]
        for x in range(880, 1086):
            px[x, y] = c
    tb = px[1000, 60]
    for y in range(40, 84):                        # the three old icons + "ARUCO"
        for x in range(874, 970):
            px[x, y] = tb
    if old_dialog:
        # The dialog sat over the top-right corner of a grey box. Above the
        # box's top edge continue the viewport from above the dialog, below it
        # continue each column from below the dialog; the edge row is copied
        # from just left of the dialog.
        ref = [px[1078, y] for y in range(174, 458)]
        edge = next((174 + i for i, c in enumerate(ref) if abs(sum(c) - sum(ref[0])) > 30), None)
        right = next((x for x in range(1082, BASE_W) if abs(sum(px[x, 458]) - sum(px[1078, 458])) > 30), BASE_W)
        for x in range(1082, BASE_W):
            top, bot = px[x, 170], px[x, 458]
            for y in range(174, 458):
                if edge is None or y < edge:
                    px[x, y] = top
                elif y <= edge + 1 or x < right:        # the box's top edge, then its interior row by row
                    px[x, y] = px[1078, y]
                else:
                    px[x, y] = bot
    return im, "#%02x%02x%02x" % tb


def img_tag(path, alt, crop, scale=1.0, q=80):
    im = Image.open(path).convert("RGB").crop(crop)
    if scale != 1.0:
        im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    return '<img src="%s" width="%d" height="%d" alt="%s" loading="lazy">' % (uri(im, q), im.width, im.height, esc(alt))


# ================================================================ svg kit

def t(x, y, s, size=11, fill=INK, weight=None, anchor="start", mono=False, extra=""):
    a = ' font-size="%s" fill="%s"' % (size, fill)
    if weight:
        a += ' font-weight="%s"' % weight
    if anchor != "start":
        a += ' text-anchor="%s"' % anchor
    fam = ' font-family="Consolas, Menlo, monospace"' if mono else ""
    return '<text x="%g" y="%g"%s%s%s>%s</text>' % (x, y, a, fam, extra, esc(s))


def r(x, y, w, h, fill, stroke=None, rx=0, sw=1, extra=""):
    s = ' stroke="%s" stroke-width="%s"' % (stroke, sw) if stroke else ""
    return '<rect x="%g" y="%g" width="%g" height="%g" rx="%g" fill="%s"%s%s/>' % (x, y, w, h, rx, fill, s, extra)


def line(x1, y1, x2, y2, stroke, sw=1, extra=""):
    return '<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="%s" stroke-width="%s"%s/>' % (x1, y1, x2, y2, stroke, sw, extra)


def use(base_id):
    return '<use href="#%s"/>' % base_id


def dsvg(inner, w, h, label, bg="#1e2124"):
    return ('<svg viewBox="0 0 %d %d" role="img" aria-label="%s" %s><title>%s</title>%s%s</svg>'
            % (w, h, esc(label), FF, esc(label), r(0, 0, w, h, bg), inner))


def mock(inner, label, base_id):
    return ('<svg viewBox="0 0 %d %d" role="img" aria-label="%s" %s><title>%s</title>%s%s</svg>'
            % (BASE_W, BASE_H, esc(label), FF, esc(label), use(base_id), inner))


def fig(inner, caption, kind="mock-up", cls="wire mock"):
    return B.fig(inner, caption, kind=kind, cls=cls)


def note(x, y, text, w=None):
    w = w or len(text) * 6.1 + 18
    return (r(x, y, w, 22, "#3a3320", "#d9b44a", 4, 1.1)
            + t(x + w / 2, y + 15, text, 10.4, "#f3e3a8", None, "middle"))


def cursor(x, y, fill="#ffffff"):
    return ('<path d="M%g %g l0 15 l4 -3.5 l3 6.5 l3 -1.5 l-3 -6.5 l5 -0.5 z" fill="%s" stroke="#111" stroke-width="1"/>'
            % (x, y, fill))


# ------------------------------------------------------------------ icons

def tile(x, y, s, mid, inset, rx=2, outline=None):
    o = r(x, y, s, s, "#ffffff", outline, rx, 1)
    o += B.g_bits(aruco_dict.marker_grid(D4, mid), x + inset, y + inset, s - 2 * inset, 0.0, bg="#ffffff", fg="#101010")
    return o


def badge_glyph(kind, cx, cy):
    W = "#ffffff"
    if kind == "make":
        return ('<path d="M%g %g h11" stroke="%s" stroke-width="2.3" stroke-linecap="round"/>' % (cx - 5.5, cy + 5.4, W)
                + '<path d="M%g %g V%g M%g %g l4.2 -4.4 l4.2 4.4" fill="none" stroke="%s" stroke-width="2.3" '
                  'stroke-linecap="round" stroke-linejoin="round"/>' % (cx, cy + 2.6, cy - 5.6, cx - 4.2, cy - 1.2, W))
    if kind == "edit":
        return ('<path d="M%g %g L%g %g" stroke="%s" stroke-width="3.2" stroke-linecap="round"/>' % (cx - 3.2, cy + 3.2, cx + 4.2, cy - 4.2, W)
                + '<path d="M%g %g l1.2 -3.6 l2.6 2.6 z" fill="%s"/>' % (cx - 6.4, cy + 6.4, W))
    if kind == "ids":
        return t(cx, cy + 4.9, "#", 13.5, W, "800", "middle")
    if kind == "print":
        return (r(cx - 3.2, cy - 7, 6.4, 5, W, None, 0.6) + r(cx - 6.2, cy - 2.6, 12.4, 6.2, W, None, 1.2)
                + r(cx - 3.2, cy + 2.4, 6.4, 4.4, "#dfefff", W, 0.6, 0.8) + r(cx + 3.2, cy - 1, 1.6, 1.6, ACC))
    if kind == "hub":
        return "".join(line(cx - 5, cy + d, cx + 5, cy + d, W, 2.2, ' stroke-linecap="round"') for d in (-4.2, 0, 4.2))
    if kind == "auto":
        pts = [(cx + 1.6, cy - 7.6), (cx - 5.2, cy + 1.2), (cx - 0.6, cy + 1.2), (cx - 1.8, cy + 7.6),
               (cx + 5.2, cy - 1.2), (cx + 0.6, cy - 1.2)]
        return '<polygon points="%s" fill="%s"/>' % (" ".join("%g,%g" % p for p in pts), W)
    return ""


def icon(kind, x, y, s=32, dark=True, ring=None):
    """The family mark (a real DICT_4X4_50 id 7 tile) + a verb badge in Fusion blue."""
    k = s / 32.0
    o = ['<g transform="translate(%g %g) scale(%g)">' % (x, y, k)]
    outline = None if dark else "#9a9a9a"
    if kind == "aruco":
        o.append(tile(2, 2, 28, 7, 3, 3, outline))
    else:
        o.append(tile(0, 0, 22, 7, 2, 2.5, outline))
        o.append('<circle cx="23" cy="23" r="10" fill="%s"/>' % (ring or (TB if dark else "#f0f0f0")))
        o.append('<circle cx="23" cy="23" r="8.6" fill="%s"/>' % ACC)
        o.append(badge_glyph(kind, 23, 23))
    o.append("</g>")
    return "".join(o)


def icon_old(mid, x, y, s=32):
    k = s / 32.0
    return '<g transform="translate(%g %g) scale(%g)">%s</g>' % (x, y, k, tile(0, 0, 32, mid, 3, 1.5))


def any_icon(ic, x, y, s, ring):
    if isinstance(ic, tuple):
        return icon_old(ic[1], x, y, s)
    if ic == "native":
        return r(x + 2, y + 2, s - 4, s - 4, "#8a9099", None, 2)
    if ic == "warn":
        return t(x + s / 2, y + s * 0.78, "⚠", s * 0.72, WARN_C, None, "middle")
    return icon(ic, x, y, s, ring=ring)


# ------------------------------------------------------------- toolbar

def panel(x, y, label, buttons, s=19, gap=7, w=None):
    """The ARUCO panel at the screenshot's scale. buttons: icon kinds or marker ids (today's)."""
    w = w or max(len(buttons) * (s + gap) - gap + 16, 62)
    o = [r(x, y, w, 40, TB)]
    bx = x + 8
    for b in buttons:
        o.append(icon_old(b, bx, y + 5, s) if isinstance(b, int) else icon(b, bx, y + 5, s, ring=TB))
        bx += s + gap
    o.append(t(x + w / 2, y + 34, label + " ▾", 7.6, "#d5d8dd", "600", "middle"))
    return "".join(o)


def tooltip(x, y, w, title, body, clip=None):
    lines = B.wrap_lines(body, int(w / 5.7))
    h = 36 + 13 * len(lines) + (clip[1] + 10 if clip else 0)
    o = [r(x + 2, y + 3, w, h, "#000", None, 5, 0, ' opacity="0.35"'), r(x, y, w, h, "#e3ebf2", "#b9c6d2", 5)]
    o.append(t(x + 10, y + 17, title, 10.2, "#1b2a36", "700"))
    yy = y + 33
    for ln in lines:
        o.append(t(x + 10, yy, ln, 9.6, "#1b2a36", "600"))
        yy += 13
    if clip:
        o.append('<g transform="translate(%g %g)">%s</g>' % (x + 10, yy - 3, clip[0]))
    return "".join(o)


def clip_make(w=236, h=96):
    """The toolclip picture for Make tag: a sketch rectangle becomes a tile with a marker."""
    o = [r(0, 0, w, h, "#2b3036", None, 4)]
    o.append(r(20, 22, 62, 52, "none", "#4f8fdc", 0, 2))
    for px_, py_ in ((20, 22), (82, 22), (20, 74), (82, 74)):
        o.append(r(px_ - 2.5, py_ - 2.5, 5, 5, "#4f8fdc"))
    o.append('<path d="M98 48 h40 m-7 -7 l7 7 l-7 7" stroke="#c8ccd2" stroke-width="2" fill="none" stroke-linecap="round"/>')
    o.append(r(156, 24, 60, 50, "#d9d9d9", None, 2) + r(154, 20, 60, 50, "#f2f2f2", "#bbb", 2))
    o.append(B.g_bits(aruco_dict.marker_grid(D4, 7), 161, 27, 36, 0.0, bg="#fff", fg="#111"))
    o.append(t(51, 90, "rectangle or face", 8.5, "#c8ccd2", None, "middle") + t(184, 90, "tag, one undo step", 8.5, "#c8ccd2", None, "middle"))
    return "".join(o), h


# ------------------------------------------------------------- dialog

def row_h(row):
    k = row[0]
    if k == "info":
        return 8 + 13 * len(row[1])
    if k == "big":
        return 42
    if k == "table":
        return 24 + 18 * len(row[2])
    if k == "note":
        return 18
    if k == "gap":
        return 8
    return 24


def dialog(x, y, w, title, rows, tabs=None, active=0, buttons=("OK", "Cancel")):
    lab_w = int(w * 0.38)
    H = 32 + (26 if tabs else 0) + 6 + sum(row_h(rw) for rw in rows) + 42
    o = [r(x + 3, y + 4, w, H, "#000", None, 5, 0, ' opacity="0.35"'),
         r(x, y, w, H, DLG, "#1b1e22", 4), r(x, y, w, 26, HD, None, 4), r(x, y + 14, w, 12, HD),
         t(x + 10, y + 17.5, title, 11, INK, "700"), t(x + w - 14, y + 18, "⋯", 11, MUT, None, "middle")]
    cy = y + 32
    if tabs:
        tx = x + 10
        for i, name in enumerate(tabs):
            tw = len(name) * 6.6 + 10
            on = i == active
            o.append(t(tx + tw / 2, cy + 13, name, 10.5, INK if on else MUT, "700" if on else None, "middle"))
            if on:
                o.append(r(tx + 2, cy + 18, tw - 4, 2, ACC))
            tx += tw + 2
        o.append(line(x, cy + 21, x + w, cy + 21, "#262a2f"))
        cy += 26
    cy += 6
    lx, fx = x + 10, x + lab_w
    fw = w - lab_w - 10
    for row in rows:
        k = row[0]
        h = row_h(row)
        if k == "sel":
            o.append(t(lx, cy + 15, row[1], 10.5))
            pw = len(row[2]) * 6.3 + 14
            o.append(r(fx, cy + 3, pw, 18, ACC, None, 2) + t(fx + 7, cy + 15.5, row[2], 10, "#fff", "600"))
            o.append(t(fx + pw + 8, cy + 15.5, "×", 11, MUT))
        elif k in ("drop", "spin", "val", "text"):
            o.append(t(lx, cy + 15, row[1], 10.5))
            o.append(r(fx, cy + 3, fw, 18, FLD, None, 2) + t(fx + 6, cy + 15.5, row[2], 10.5, INK if row[2] else MUT))
            if k == "drop":
                o.append(t(fx + fw - 9, cy + 15.5, "▾", 9, MUT, None, "middle"))
            if k == "spin":
                o.append(t(fx + fw - 8, cy + 15, "⇅", 9, MUT, None, "middle"))
        elif k == "chk":
            o.append(t(lx, cy + 15, row[1], 10.5))
            o.append(r(fx, cy + 5, 13, 13, INK if row[2] else FLD, MUT, 2))
            if row[2]:
                o.append('<path d="M%g %g l3 3 l6 -6" stroke="%s" stroke-width="2" fill="none"/>' % (fx + 2.5, cy + 11, HD))
        elif k == "info":
            yy = cy + 13
            for j, ln in enumerate(row[1]):
                col, txt = ln if isinstance(ln, tuple) else (INK, ln)
                o.append(t(lx + 4, yy, txt, 9.6, col, "700" if j == 0 else None))
                yy += 13
        elif k == "sec":
            o.append(line(x + 8, cy + 11, x + w - 8, cy + 11, "#262a2f"))
            o.append(r(x + 12, cy + 4, len(row[1]) * 6 + 10, 14, DLG) + t(x + 17, cy + 14.5, row[1], 9.5, MUT, "700"))
        elif k == "status":
            col = {"ok": GOOD, "warn": WARN_C, "bad": BAD, "info": ACC}[row[2]]
            o.append('<circle cx="%g" cy="%g" r="4" fill="%s"/>' % (lx + 5, cy + 12, col))
            o.append(t(lx + 15, cy + 15.5, row[1], 10))
            if len(row) > 3 and row[3]:
                bw = len(row[3]) * 6.3 + 16
                o.append(r(x + w - 10 - bw, cy + 3, bw, 18, FLD, MUT, 3)
                         + t(x + w - 10 - bw / 2, cy + 15.5, row[3], 10, INK, "600", "middle"))
        elif k == "big":
            kind, label, sub = row[1], row[2], row[3]
            enabled = row[4] if len(row) > 4 else True
            fill = "#2f4a63" if enabled else FLD
            o.append(r(x + 10, cy + 3, w - 20, 36, fill, ACC if enabled else "#3a3e44", 4))
            o.append(icon(kind, x + 18, cy + 9, 24, ring=fill))
            o.append(t(x + 50, cy + 18, label, 11, INK if enabled else MUT, "700"))
            o.append(t(x + 50, cy + 32, sub, 9.5, MUT))
        elif k == "btnrow":
            bx = lx
            for lab in row[1]:
                bw = len(lab) * 6.3 + 16
                o.append(r(bx, cy + 3, bw, 18, FLD, MUT, 3) + t(bx + bw / 2, cy + 15.5, lab, 10, INK, "600", "middle"))
                bx += bw + 6
        elif k == "table":
            head, body, cols = row[1], row[2], row[3]
            tw = w - 20
            xs = [x + 10]
            for c in cols[:-1]:
                xs.append(xs[-1] + tw * c)
            o.append(r(x + 10, cy + 2, tw, 18, HD, None, 2))
            for j, hd in enumerate(head):
                o.append(t(xs[j] + 5, cy + 15, hd, 9.5, MUT, "700"))
            yy = cy + 20
            for ri, cells in enumerate(body):
                if ri % 2:
                    o.append(r(x + 10, yy, tw, 18, "#363a41"))
                for j, c in enumerate(cells):
                    col, txt = c if isinstance(c, tuple) else (INK, c)
                    if j == 1:
                        o.append(r(xs[j] + 3, yy + 2, tw * cols[j] - 8, 14, FLD, None, 2))
                        o.append(t(xs[j] + 8, yy + 13, txt, 9.8, col) + t(xs[j] + tw * cols[j] - 11, yy + 12.5, "⇅", 8, MUT))
                    else:
                        o.append(t(xs[j] + 5, yy + 13, txt, 9.6, col))
                yy += 18
        elif k == "note":
            o.append(t(lx + 4, cy + 13, row[1], 9.4, MUT))
        cy += h
    bx = x + w - 10
    for b in reversed(buttons):
        bw = max(50, len(b) * 7 + 18)
        bx -= bw
        o.append(r(bx, cy + 10, bw, 22, DLG, MUT, 3) + t(bx + bw / 2, cy + 25, b, 10.5, INK, "600", "middle"))
        bx -= 8
    return "".join(o), H


# ------------------------------------------------------------- menu, palette, badges, toast

def menu(x, y, items, w=236):
    """items: "-" or (icon kind | ("old", id) | "native" | "warn" | None, label, flags)."""
    H = 6 + sum(8 if it == "-" else 22 for it in items)
    o = [r(x + 2, y + 3, w, H, "#000", None, 3, 0, ' opacity="0.4"'), r(x, y, w, H, MENU, "#1b1e22", 3)]
    yy = y + 3
    for it in items:
        if it == "-":
            o.append(line(x + 6, yy + 4, x + w - 6, yy + 4, "#44484f"))
            yy += 8
            continue
        ic, lab = it[0], it[1]
        flags = it[2] if len(it) > 2 else set()
        hl = "hl" in flags
        if hl:
            o.append(r(x + 2, yy, w - 4, 22, MENU_HL, None, 2))
        if ic:
            o.append(any_icon(ic, x + 7, yy + 3, 16, MENU_HL if hl else MENU))
        col = "#80858d" if "grey" in flags else (WARN_C if "warn" in flags else INK)
        o.append(t(x + 30, yy + 15, lab, 10.5, col, "600" if hl else None))
        if "sub" in flags:
            o.append(t(x + w - 9, yy + 15, "▸", 10, MUT, None, "middle"))
        yy += 22
    return "".join(o)


def chip(x, y, text, kind):
    col = {"ok": GOOD, "dup": BAD, "small": WARN_C, "stale": MUT, "hidden": MUT, "src": ACC2}[kind]
    w = len(text) * 5.8 + 10
    dash = ' stroke-dasharray="3 2"' if kind in ("stale", "hidden") else ""
    return (r(x, y, w, 15, "none" if kind in ("stale", "hidden", "src") else col + "33", col, 7, 1, dash)
            + t(x + w / 2, y + 11, text, 9, col, "700", "middle")), w


def palette(x, y, w, h):
    o = [r(x, y, w, h, "#2a2e33", "#1b1e22"), r(x, y, w, 22, "#3a3f46"),
         t(x + 10, y + 15, "ARUCO HUB", 9.5, "#d5d8dd", "700"), t(x + w - 10, y + 15, "—   ×", 9, MUT, None, "end")]
    yy = y + 30
    o.append(r(x + 8, yy, 172, 22, "#2f4a63", ACC, 3) + icon("make", x + 12, yy + 3, 16, ring="#2f4a63")
             + t(x + 34, yy + 15, "Make tag from selection", 10, INK, "600"))
    o.append(r(x + 188, yy, w - 196, 22, FLD, None, 3) + t(x + 194, yy + 15, "filter…", 10, MUT))
    yy += 30
    o.append(t(x + 8, yy + 10, "enabled: a rectangle is selected", 8.8, GOOD))
    yy += 18
    rows = [
        ("lvl", 0, "Design", "12 tags · 2 problems", None),
        ("lvl", 1, "Arm:1", "6 copies", "Number copies"),
        ("tag", 2, "Actuator:1 · top", "#1", "Arm:1", "ok"),
        ("tag", 2, "Actuator:2 · top", "#2", "Arm:1", "ok"),
        ("tag", 2, "Actuator:3 · top", "#3", "Arm:1", "ok"),
        ("more", 2, "… 3 more, #4 to #6", "", "", ""),
        ("lvl", 1, "Arm:2", "6 copies", "Number copies"),
        ("tag", 2, "Actuator:1 · top", "#0", "model", "dup"),
        ("tag", 2, "Actuator:2 · top", "#0", "model", "dup"),
        ("more", 2, "… 4 more, all #0", "", "", ""),
        ("lvl", 1, "Gripper:1", "1 tag", None),
        ("tag", 2, "Gripper · top", "#8", "model", "small"),
        ("tag", 1, "Base · top", "#9", "model", "ok"),
    ]
    for row in rows:
        kind, depth = row[0], row[1]
        ix = x + 10 + depth * 14
        if kind == "lvl":
            o.append(t(ix, yy + 13, "▾", 9, MUT) + t(ix + 12, yy + 13, row[2], 10.5, INK, "700")
                     + t(ix + 14 + len(row[2]) * 6.6, yy + 13, row[3], 9.2, MUT))
            if row[4]:
                bw = len(row[4]) * 6 + 14
                o.append(r(x + w - 10 - bw, yy, bw, 17, FLD, MUT, 3)
                         + t(x + w - 10 - bw / 2, yy + 12, row[4], 9.3, INK, "600", "middle"))
        elif kind == "tag":
            o.append(tile(ix, yy + 2, 13, 7, 1, 1))
            o.append(t(ix + 18, yy + 12, row[2], 10, INK))
            c, _cw = chip(ix + 22 + len(row[2]) * 6.2, yy + 1, row[3], "src")
            o.append(c)
            lab = {"ok": "✓ ok", "dup": "⚠ shares #0 with 5 other copies",
                   "small": "⚠ 1.1 mm per module: too fine to print", "stale": "⟳ per-copy part stale"}[row[5]]
            col = {"ok": GOOD, "dup": BAD, "small": WARN_C, "stale": MUT}[row[5]]
            src = "from %s · " % row[4]
            o.append(t(ix + 18, yy + 25, src, 8.8, MUT) + t(ix + 18 + len(src) * 5.3, yy + 25, lab, 8.8, col, "600"))
            o.append(icon("edit", x + w - 48, yy + 1, 16, ring="#2a2e33") + t(x + w - 20, yy + 14, "⌖", 12, MUT, None, "middle"))
            yy += 9
        else:
            o.append(t(ix + 18, yy + 13, row[2], 9.5, MUT))
        yy += 21
    yy += 6
    o.append(line(x, yy, x + w, yy, "#1b1e22"))
    yy += 8
    o.append(r(x + 8, yy, 60, 24, "#2f4a63", ACC, 3) + icon("print", x + 12, yy + 4, 16, ring="#2f4a63")
             + t(x + 34, yy + 16, "Print", 10.5, INK, "700"))
    o.append(t(x + 76, yy + 11, "1 Letter sheet · 2 beds 220 × 220 mm", 9.4, INK))
    o.append(t(x + 76, yy + 23, "6 copies still share #0: numbered first, or fix above", 8.8, WARN_C))
    yy += 34
    o.append(line(x, yy, x + w, yy, "#1b1e22"))
    o.append(r(x, yy, w, y + h - yy, "#24272c"))
    o.append(t(x + 10, yy + 15, "PROBLEMS  2", 9, "#d5d8dd", "700"))
    o.append(t(x + 10, yy + 32, "⚠ 6 copies share #0 under Arm:2 — Number copies", 9.4, WARN_C))
    o.append(t(x + 10, yy + 47, "⚠ Gripper · top: 1.1 mm per module, 0.4 mm nozzle", 9.4, WARN_C))
    return "".join(o)


def vbadge(x, y, text, kind="ok", lead=None):
    styles = {"ok": ("#1d2024", ACC, "#fff"), "dup": (BAD, "#ffd9d6", "#fff"), "small": (WARN_C, "#fff1d0", "#1d2024"),
              "stale": ("#3a3e44", MUT, "#fff"), "hidden": ("#1d2024", MUT, MUT)}
    fill, stroke, ink = styles[kind]
    w = len(text) * 6.6 + 14
    dash = ' stroke-dasharray="3 2"' if kind in ("stale", "hidden") else ""
    o = line(x, y, lead[0], lead[1], stroke, 1.2) if lead else ""
    o += r(x - w / 2, y - 9, w, 18, fill, stroke, 9, 1.4, dash) + t(x, y + 4, text, 10.5, ink, "700", "middle", True)
    return o


def toast(x, y, w, text, actions):
    h = 34
    o = [r(x + 2, y + 3, w, h, "#000", None, 6, 0, ' opacity="0.4"'), r(x, y, w, h, "#23272c", ACC, 6, 1.2)]
    o.append(icon("auto", x + 9, y + 5, 24, ring="#23272c"))
    o.append(t(x + 42, y + 21, text, 10.6, INK))
    ax = x + w - 10
    for a in reversed(actions):
        aw = len(a) * 6.6 + 10
        ax -= aw
        o.append(t(ax + aw / 2, y + 21, a, 10.6, ACC2, "700", "middle"))
        ax -= 10
    return "".join(o)


# ================================================================ figures

def fig_today_real():
    a = img_tag(UI / "01_aruco_panel_tooltip.jpg", "Today's ARUCO panel: three identical marker icons and the tooltip "
                "that is the only explanation", (1040, 40, 1320, 135), 2.0)
    b = img_tag(UI / "06_tag_dialog_rectangle_selected.jpg", "Today's ArUco Tag dialog with a rectangle selected",
                (1306, 176, 1456, 455), 1.6)
    return ('<div class="pair2">%s%s</div>'
            % (B.fig(a, "<b>Today's panel</b>, a real screenshot (2× zoom): the icons are three different markers, "
                        "which is the same picture three times; a fourth, ArUco Edit, has since joined them.",
                     kind="screenshot", cls="dark"),
               B.fig(b, "<b>Today's ArUco Tag dialog</b> (1.6× zoom), the inputs every proposal keeps.",
                     kind="screenshot", cls="dark")))


def fig_today_mock():
    o = [panel(876, 42, "ARUCO", [0, 1, 2, 3])]
    o.append(menu(876, 84, [(("old", 0), "ArUco Tag"), (("old", 1), "ArUco Edit"), (("old", 2), "ArUco IDs", {"hl"}),
                            (("old", 3), "ArUco Export")], 150))
    o.append(tooltip(1032, 118, 190, "ArUco IDs", "Give every copy its own marker id at any assembly level — like a "
                                                  "serial number — without making a new model."))
    o.append(cursor(948, 138))
    o.append(note(560, 110, "four buttons, four markers: which one makes a tag?"))
    return fig(mock("".join(o), "Today: the ARUCO panel with four marker icons, its dropdown and a tooltip", "base01"),
               "<b>Today, drawn:</b> four promoted buttons, the dropdown with the same four icons, and the tooltip you "
               "have to hover for. The viewport is the demo arm from the how-it-works page.")


def icon_svg(kind, s, dark):
    ring = "#2b2f34" if dark else "#f0f0f0"
    inner = icon_old(kind[1], 0, 0, s) if isinstance(kind, tuple) else icon(kind, 0, 0, s, dark, ring)
    return '<svg width="%d" height="%d" viewBox="0 0 %d %d" aria-hidden="true">%s</svg>' % (s, s, s, s, inner)


def fig_icons():
    old = [(("old", i), "today %d" % (i + 1)) for i in range(4)]
    new = [("make", "Make tag"), ("edit", "Edit tag"), ("ids", "Number copies"), ("print", "Print"),
           ("hub", "ArUco Hub (B)"), ("auto", "Tag it (D)"), ("aruco", "ArUco (A)")]

    def row(dark):
        cls = "dark" if dark else "light"
        cells = ['<div class="lab">%s theme</div>' % cls]
        for group, label in ((old, "today"), (new, "proposed: family mark + verb badge")):
            cells.append('<div class="grp"><div class="glab">%s</div>' % label)
            for kind, name in group:
                cells.append('<figure><div class="pair">%s%s</div><figcaption>%s<br>32 · 16 px</figcaption></figure>'
                             % (icon_svg(kind, 32, dark), icon_svg(kind, 16, dark), esc(name)))
            cells.append("</div>")
        return '<div class="iconrow %s">%s</div>' % (cls, "".join(cells))

    strip = dsvg(panel(10, 4, "ARUCO", [0, 1, 2, 3]) + t(76, 56, "today", 9, MUT, None, "middle")
                 + panel(190, 4, "ARUCO", ["make", "edit", "ids", "print"]) + t(252, 56, "the same four, badged", 9, MUT, None, "middle")
                 + panel(360, 4, "ARUCO", ["aruco"]) + t(392, 56, "A: one button", 9, MUT, None, "middle")
                 + panel(460, 4, "ARUCO", ["auto", "print"]) + t(500, 56, "D: two verbs", 9, MUT, None, "middle"),
                 600, 62, "The ARUCO panel at the toolbar's real scale: today, badged, A, D", TB)
    clipimg, _ = clip_make()
    tip = dsvg(tooltip(10, 8, 262, "Make tag", "Turn the selected rectangle or face into a 3D-printable ArUco tag. "
                                               "Size and id are chosen for you; change them in the dialog.", (clipimg, 96)),
               290, 200, "The expanded tooltip with a toolclip image for Make tag")
    return (row(True) + row(False)
            + B.fig(strip, "<b>At the toolbar's own scale</b> (the screenshot's), left to right: today; the same four "
                           "commands with verb badges; proposal A's single button; proposal D's two verbs. The badge "
                           "is what survives at 19 px, a second marker does not.", kind="mock-up", cls="wire mock")
            + B.fig(tip, "<b>The expanded tooltip</b> with a <code>toolClipFilename</code> picture: a hover explains "
                         "the command without reading.", kind="mock-up", cls="mock small"))


def fig_a():
    one = panel(876, 42, "ARUCO", ["aruco"])
    # A1: rectangle selected -> Make tab (base06 has the blue rectangle)
    d, _ = dialog(972, 112, 246, "ARUCO", [
        ("sel", "Rectangle or face", "1 profile selected"), ("drop", "Type", "Single marker"),
        ("drop", "Dictionary", "DICT_4X4_50"), ("spin", "ID", "7  (next free)"), ("val", "Tile thickness", "3 mm"),
        ("val", "Black", "0.6 mm"), ("drop", "Black sits", "Inlaid (flush)"), ("sec", "Fitted for you"),
        ("info", [("#ffffff", "4X4_50 #7 · 30 mm marker on 40 × 40 mm"), "module 5.0 mm · readable to ~1.4 m at 1080p",
                  "black 0.60 mm inlaid · quiet zone 1 module", (ACC2, "the auto-sizing lane's rule fills this group")])],
        tabs=["Overview", "Make", "Edit", "Number", "Print"], active=1)
    a1 = one + d + note(690, 96, "one button") + note(700, 150, "opened on Make: a rectangle was selected", 250)
    f1 = fig(mock(a1, "Proposal A: the ArUco dialog opened on the Make tab because a rectangle was selected", "base06"),
             "<b>A, a rectangle selected:</b> the one button opens its dialog on <i>Make</i> with the rectangle as the "
             "target. The inputs are today's ArUco Tag inputs; <i>Fitted for you</i> is the slot for the auto-sizing "
             "lane's rule.")
    # A3: nothing selected -> Overview (base01, the arm)
    d3, _ = dialog(926, 112, 296, "ARUCO", [
        ("status", "12 tags in 3 parts (Actuator, Base, Gripper)", "info"),
        ("status", "6 copies in Arm:2 all print #0", "bad", "Number them"),
        ("status", "Gripper · top: 1.1 mm/module, too fine", "warn", "Show"),
        ("status", "per-copy parts up to date", "ok"), ("gap",),
        ("big", "print", "Print everything", "1 Letter sheet · 2 beds 220 × 220 mm"),
        ("big", "make", "Make a tag", "select a rectangle or a face first", False),
        ("note", "Select something before pressing ArUco: it opens on that job.")],
        tabs=["Overview", "Make", "Edit", "Number", "Print"], active=0)
    a3 = one + d3 + note(556, 150, "nothing selected: the Overview is the status + the big buttons", 352)
    f3 = fig(mock(a3, "Proposal A: the ArUco dialog opened on the Overview tab with nothing selected", "base01"),
             "<b>A, nothing selected:</b> the Overview tab is the design's tag status, one button per problem, and "
             "<i>Print everything</i>. This is also where problems live when nothing else is open.")
    # A2: Arm:1 selected -> Number tab (dialog only)
    d2, H2 = dialog(10, 10, 400, "ARUCO", [
        ("drop", "Level", "Arm:1 — 6 copies of Actuator"),
        ("status", "6 tags; all 6 printed #0 — numbered 1–6 below", "warn", "Number duplicates"),
        ("table", ["Copy · tag", "ID", "Source"],
         [["Actuator:%d · top" % i, str(i), "this level"] for i in range(1, 7)], [0.55, 0.15, 0.30]),
        ("chk", "Rebuild tag parts on OK", True)],
        tabs=["Overview", "Make", "Edit", "Number", "Print"], active=3)
    a2 = dsvg(d2 + note(430, 14, "opened on Number, level preset:", 200) + note(430, 40, "Arm:1 was selected in the browser", 200),
              650, H2 + 24, "Proposal A: the Number tab with the level preset to Arm:1")
    f2 = B.fig(a2, "<b>A, Arm:1 selected in the browser:</b> the dialog opens on <i>Number</i> with the level preset; "
                   "<i>Number duplicates</i> has been pressed, the table shows the result before OK writes it. The "
                   "table is today's ArUco IDs table.", kind="mock-up", cls="wire mock")
    return f1 + f3 + f2


def fig_b():
    o = [panel(876, 42, "ARUCO", ["hub"]), palette(900, 84, 328, 606), note(700, 96, "one toggle button")]
    o.append(note(560, 640, "the viewport shrinks beside the docked palette, as with the Cockpit", 330))
    return fig(mock("".join(o), "Proposal B: the ArUco Hub palette docked on the right, listing every tag with status", "base01"),
               "<b>B:</b> the hub docked right (<code>PaletteDockStateRight</code>). Every tag copy is a row with its "
               "id, where the id comes from, and a status chip; <i>Number copies</i> sits on each assembly row; "
               "<i>Print</i> in the footer; the problems strip at the bottom is the Cockpit's drawer idea. Rows "
               "and numbers are illustrative.")


def fig_c():
    W, H = 1000, 540
    o = []
    heads = [(10, 10, "right-click a rectangle or a face"), (510, 10, "right-click a tag (its tile or black cells)"),
             (10, 280, "right-click Arm:1, in the viewport or the browser"), (510, 280, "right-click the empty canvas")]
    for x, y, h in heads:
        o.append(t(x, y + 12, h, 11, MUT, "700"))
    o.append(menu(10, 30, [("native", "Repeat Extrude", {"grey"}), ("native", "Press Pull"), ("native", "Move/Copy"),
                           ("native", "Physical Material"), "-", ("make", "Make ArUco tag", {"hl", "sub"})], 230))
    o.append(menu(246, 134, [(None, "with defaults   (no dialog)", {"hl"}), (None, "Single marker…"),
                             (None, "Grid board…"), (None, "ChArUco board…")], 196))
    o.append(menu(510, 30, [("native", "Press Pull"), ("native", "Move/Copy"), ("native", "Appearance"), "-",
                            ("edit", "Edit tag…   4X4_50 #0"), ("ids", "Give this copy its own id"),
                            ("warn", "duplicate of Arm:2 › Actuator:1: number both", {"warn"}),
                            ("print", "Print this part")], 300))
    o.append(menu(10, 300, [("native", "Activate"), ("native", "Ground"), ("native", "Rigid Group"), ("native", "Isolate"), "-",
                            ("ids", "Number the 6 copies under Arm:1", {"hl"}), ("ids", "ArUco IDs for Arm:1…")], 260))
    o.append(menu(510, 300, [("native", "Repeat ArUco Tag", {"grey"}), ("native", "Create Sketch"), ("native", "Measure"), "-",
                             ("print", "Print all ArUco tags…"), ("warn", "6 copies share a marker: number them", {"warn"}),
                             ("aruco", "ArUco overview…")], 270))
    o.append(t(250, 250, "Fusion's own items first; ours appended at display time with the state in the label", 10, MUT, None, "middle"))
    f1 = B.fig(dsvg("".join(o), W, H, "Proposal C: the four context menus, one per kind of selection"),
               "<b>C, the four menus.</b> Built in <code>markingMenuDisplaying</code> from the selection, so the labels "
               "carry the id, the copy count and the collision. <i>with defaults</i> is proposal D's no-dialog path.",
               kind="mock-up", cls="wire mock")
    full = [panel(876, 42, "ARUCO", ["aruco"]), note(690, 96, "one fallback button")]
    full.append(cursor(588, 246, "#ffd34d"))
    full.append(menu(598, 254, [("native", "Press Pull"), ("native", "Move/Copy"), ("native", "Appearance"), "-",
                                ("edit", "Edit tag…   4X4_50 #0", {"hl"}), ("ids", "Give this copy its own id"),
                                ("warn", "duplicate of Arm:2 › Actuator:1: number both", {"warn"}),
                                ("print", "Print this part")], 300))
    full.append(note(300, 640, "the menu knows the tag's id and that another copy prints it", 330))
    f2 = fig(mock("".join(full), "Proposal C: right-click on a tag face in the viewport", "base01"),
             "<b>C in the viewport:</b> right-click the top actuator's tag; the add-in's items follow Fusion's.")
    return f1 + f2


def fig_d():
    o = [panel(876, 42, "ARUCO", ["auto", "print"]), note(640, 96, "Tag it · Print all (+ a dropdown for the rest)", 260)]
    # the rectangle (30..181 x 316..468 in the cleaned base) has become a tile
    o.append(r(30, 316, 151, 152, "#ececec", "#9a9a9a", 1))
    o.append(B.g_bits(aruco_dict.marker_grid(D4, 7), 49, 335, 114, 0.0, bg="#fff", fg="#111"))
    o.append(note(40, 480, "the rectangle became a tile with a tag, one undo step", 290))
    o.append(toast(300, 650, 620, "Tagged the rectangle: 4X4_50 #7 · 30 mm on 40 × 40 mm · readable to ~1.4 m", ["Edit", "Undo"]))
    f1 = fig(mock("".join(o), "Proposal D: Tag it pressed with a rectangle selected; the toast says what it did", "base06"),
             "<b>D:</b> select, press <i>Tag it</i>, done. The toast is a six-second palette with <i>Edit</i> and "
             "<i>Undo</i>; no dialog unless something needs deciding.")
    d, H = dialog(10, 10, 330, "ARUCO", [
        ("sel", "Rectangle or face", "1 face selected"), ("drop", "Dictionary", "DICT_5X5_100"), ("spin", "ID", "12"),
        ("sec", "Fitted for you"),
        ("info", [(WARN_C, "5X5_100 #12 · 9 mm marker on a 12 × 12 mm face"),
                  (WARN_C, "module 0.9 mm: too fine for a 0.4 mm nozzle"),
                  "4X4_50 fits the same face at 1.25 mm per module"]),
        ("btnrow", ["Use 4X4_50 instead", "Paper only"])],
        tabs=["Overview", "Make", "Edit", "Number", "Print"], active=1)
    f2 = B.fig(dsvg(d + note(360, 14, "the exception: the rule hit a limit,", 210) + note(360, 40, "so the dialog opens with the fix", 210),
                    590, H + 24, "Proposal D: the dialog appears only when the sizing rule crosses a limit"),
               "<b>D's only dialog:</b> the rule found the face too small for the dictionary, so <i>Tag it</i> opened "
               "the Make tab with the warning and the fix as a button.", kind="mock-up", cls="wire mock")
    return f1 + f2


def fig_e():
    o = []
    o.append(menu(124, 84, [("native", "New Component"), ("native", "Create Sketch"), ("native", "Extrude          E"),
                            ("native", "Revolve"), ("native", "Sweep"), ("native", "Loft"), ("native", "Hole             H"),
                            ("native", "Thread"), ("native", "Box"), ("native", "Cylinder"), "-",
                            ("make", "ArUco Tag", {"hl"})], 196))
    o.append(note(330, 96, "no ARUCO panel: Make tag lives in CREATE ▾", 250))
    o.append(note(330, 122, "Number copies in ASSEMBLE ▾ · Print in UTILITIES › MAKE ▾", 330))
    o.append(vbadge(585, 206, "#0  ×2", "dup", lead=(585, 232)))
    o.append(vbadge(690, 268, "#0  ×2", "dup", lead=(690, 292)))
    o.append(vbadge(820, 420, "#8", "small") + t(840, 424, "too small (shown here for the legend)", 9, MUT))
    o.append(vbadge(820, 450, "#9", "ok") + t(840, 454, "ok", 9, MUT))
    o.append(vbadge(820, 480, "#3", "stale") + t(840, 484, "per-copy part stale", 9, MUT))
    o.append(vbadge(820, 510, "#5", "hidden") + t(840, 514, "hidden template", 9, MUT))
    o.append(note(740, 390, "badge colours", 110))
    # the timeline: one more feature with the family mark
    o.append(r(548, 709, 18, 18, "#dfe3e8", ACC, 2, 1.2) + tile(550.5, 711.5, 13, 0, 1, 1))
    o.append(line(557, 708, 557, 675, ACC2, 1))
    o.append(note(470, 652, "ArUco top · #0 — double-click to edit (pending, section 9)", 310))
    return fig(mock("".join(o), "Proposal E: Make tag in the CREATE menu, live badges on every tag, the tag's timeline feature", "base01"),
               "<b>E:</b> the CREATE menu with one more line; every tag wears a badge (red: two copies print #0); the "
               "timeline shows the tag's own feature. The legend badges at the right are not on the model, they show "
               "the other states.")


def native_feat(kind, x, y):
    o = r(x, y, 24, 24, "#454a52", None, 3)
    if kind == "sk":
        o += line(x + 6, y + 18, x + 18, y + 6, "#e8e8e8", 2) + r(x + 5, y + 5, 14, 14, "none", "#9aa0a8", 0, 1)
    elif kind == "ext":
        o += r(x + 6, y + 9, 12, 10, "#9fc3e8", "#e8e8e8", 1, 1) + '<path d="M%g %g l6 -4 l6 4 l-6 4 z" fill="#d6e8f8" stroke="#e8e8e8"/>' % (x + 6, y + 9)
    elif kind == "hole":
        o += '<circle cx="%g" cy="%g" r="6" fill="none" stroke="#e8e8e8" stroke-width="2"/>' % (x + 12, y + 12)
    elif kind == "joint":
        o += '<path d="M%g %g h12 M%g %g v8" stroke="#e8e8e8" stroke-width="2"/><circle cx="%g" cy="%g" r="3" fill="#e8e8e8"/>' % (
            x + 6, y + 12, x + 12, y + 8, x + 12, y + 12)
    return o


def fig_timeline():
    W, H = 1000, 480
    o = [r(0, 20, W, 42, "#24282d"), t(14, 46, "⏮  ◀  ▶  ⏭", 12, "#9aa0a8")]
    feats = [("sk", "Sketch1"), ("ext", "Extrude1"), ("ar", "ArUco top"), ("hole", "Hole1"), ("sk", "Sketch2"),
             ("ext", "Extrude2"), ("joint", "Rigid1")]
    x = 110
    fx = None
    for kind, name in feats:
        if kind == "ar":
            fx = x
            o.append(r(x - 3, 26, 30, 30, "#2f4a63", ACC2, 4, 1.4))
            o.append(r(x, 29, 24, 24, "#dfe3e8", ACC, 3, 1.2) + tile(x + 3, 32, 18, 7, 1.5, 1))
        else:
            o.append(native_feat(kind, x, 29))
        x += 32
    o.append(r(x + 6, 24, 2, 34, "#6fc3ff"))
    o.append(tooltip(fx - 10, 70, 236, "ArUco top · 4X4_50 #7", "Double-click to edit. Right-click → Edit Feature. "
                                                                 "Roll back and the tag goes with the tile."))
    o.append(cursor(fx + 14, 40, "#ffd34d") + t(x + 24, 46, "◀  double-click the ArUco feature", 11, "#ffd34d", "700"))
    d, Hd = dialog(90, 160, 330, "ARUCO", [
        ("sel", "Tag", "ArUco top (Actuator)"), ("drop", "Dictionary", "DICT_4X4_50"), ("spin", "ID (model value)", "7"),
        ("val", "Black", "0.6 mm"), ("drop", "Black sits", "Inlaid (flush)"), ("chk", "Rebuild all copies", True),
        ("info", ["4X4_50 #7 · black 0.60 mm (0.60 in, 0.00 up)", "Size and position are the face's; edit the tile to change them."])],
        tabs=["Overview", "Make", "Edit", "Number", "Print"], active=2)
    o.append('<path d="M%g %g C %g %g, %g %g, %g %g" fill="none" stroke="%s" stroke-width="1.6" stroke-dasharray="5 4"/>'
             % (fx + 12, 58, fx + 12, 120, 240, 120, 240, 158, ACC2))
    o.append(d)
    o.append(t(560, 182, "or right-click the feature", 11, MUT, "700"))
    o.append(menu(560, 190, [(None, "Edit Feature", {"hl"}), (None, "Edit Sketch", {"grey"}), (None, "Rename"),
                             (None, "Suppress Features"), (None, "Delete")], 200))
    o.append(r(560, 410, 420, 40, "#3a3320", "#d9b44a", 4, 1.1))
    o.append(t(570, 427, "Feasibility pending: another lane is checking adsk.fusion.CustomFeatures", 10, "#f3e3a8", "700"))
    o.append(t(570, 441, "(wrap the cells base feature + the pocket combine; updateBody inside compute).", 10, "#f3e3a8"))
    return B.fig(dsvg("".join(o), W, H, "The timeline feature: the tag as its own feature, double-click opens the Edit tab"),
                 "<b>The timeline feature, if it is feasible:</b> the tag's own item in the timeline with the family mark, "
                 "named for the tag; a double-click (or <i>Edit Feature</i>) opens the Edit dialog. It is an edit "
                 "entry only; make, number and print still need one of A to E.", kind="mock-up", cls="wire mock")


# ================================================================ checks

def check_job_table(prop, head, body, idx):
    """Each proposal's job table: the bold numbers in its column must match STEPS."""
    if idx != 0:
        return
    want = STEPS[prop]
    for row, w, job in zip(body, want, "abcd"):
        nums = re.findall(r"\*\*(\d+)\*\*", row[-1])
        got = " / ".join(nums[:2]) if job == "c" else (nums[0] if nums else "")
        w0 = w.split(" (")[0].rstrip("*")
        if got != w0:
            warn("proposal %s job (%s): table says %r, STEPS says %r" % (prop, job, got, w0))


def cmp_table(head, body, idx):
    if idx != 0:
        return None
    trs = []
    for row in body:
        key = row[0].split(":")[0].strip()
        want = STEPS.get(key)
        if want is None:
            warn("comparison row %r is not in STEPS" % key)
        elif tuple(row[1:5]) != want:
            warn("comparison row %s: %r != STEPS %r" % (key, tuple(row[1:5]), want))
        cls = ' class="rec"' if key == "A" else (' class="base"' if key == "Today" else "")
        trs.append("<tr%s>%s</tr>" % (cls, "".join("<td>%s</td>" % B.inline(c) for c in row)))
    th = "".join("<th>%s</th>" % B.inline(c) for c in head)
    return ('<div class="tablewrap"><table class="md cmp"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>'
            % (th, "".join(trs)))


def check_html(html_):
    ids = set(re.findall(r'\sid="([^"]+)"', html_))
    for href in set(re.findall(r'href="#([^"]+)"', html_)):
        if href not in ids:
            warn("broken anchor #%s" % href)
    for m in set(re.findall(r'(?:src|href)="(https?://[^"]+)"', html_)):
        warn("external reference %s" % m)
    if re.search(r"url\((?!['\"]?data:)", html_):
        warn("css url() that is not a data: URI")
    if "<link" in html_ or "<script src" in html_:
        warn("external stylesheet or script")


# ================================================================ page

EXTRA_CSS = r"""
.fig.mock .figbody{background:#1e2124; border-color:#2c3036; padding:8px}
.fig.mock .figbody > svg{min-width:860px}
.fig.small .figbody > svg{min-width:0; max-width:380px; margin:0 auto}
.fig.dark .figbody{background:#1e2124; border-color:#2c3036}
.fig.dark .figbody img{display:block; max-width:100%; height:auto; margin:0 auto; border-radius:8px}
.pair2{display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,320px),1fr)); gap:14px; margin:22px 0; align-items:start}
.pair2 figure.fig{margin:0}
.iconrow{display:flex; flex-wrap:wrap; gap:14px 26px; align-items:flex-end; padding:14px 18px 12px; border-radius:12px; margin:12px 0}
.iconrow.dark{background:#2b2f34} .iconrow.light{background:#f0f0f0; border:1px solid #ddd}
.iconrow .lab{font:600 11px/1.2 var(--sans); letter-spacing:.08em; text-transform:uppercase; width:100%; margin-bottom:-4px}
.iconrow.dark .lab{color:#9aa0a8} .iconrow.light .lab{color:#666}
.iconrow .grp{display:flex; gap:14px 18px; flex-wrap:wrap; align-items:flex-end; padding:8px 12px 6px; border-radius:10px}
.iconrow.dark .grp{background:#23272c} .iconrow.light .grp{background:#fafafa}
.iconrow .glab{width:100%; font:600 12px/1.2 var(--sans)} .iconrow.dark .glab{color:#c8ccd2} .iconrow.light .glab{color:#444}
.iconrow figure{margin:0; text-align:center} .iconrow .pair{display:flex; gap:8px; align-items:flex-end; justify-content:center}
.iconrow figcaption{font:11.5px/1.3 var(--mono); margin:6px 0 0} .iconrow.dark figcaption{color:#c8ccd2} .iconrow.light figcaption{color:#444}
.iconrow svg{display:block}
table.cmp td, table.cmp th{text-align:center; vertical-align:middle}
table.cmp td:first-child, table.cmp th:first-child{text-align:left}
table.cmp tr.rec td{background:var(--good-weak)}
table.cmp tr.base td{color:var(--muted)}
.k-mock-up{background:var(--acc-weak); color:var(--acc)}
.k-screenshot{background:var(--vio-weak); color:var(--violet)}
main.content table.md td{font-size:14.5px}
"""


def page(title, lede, meta, body, toc, defs):
    nav = "".join('<a href="%s"%s>%s</a>' % (fn, ' class="on" aria-current="page"' if k == "entry" else "", lab)
                  for k, fn, lab in PAGES)
    brand = B.marker_svg(D4, 7, quiet=0.6, cls="", label="ArUco DICT_4X4_50 id 7", grid=aruco_dict.marker_grid(D4, 7))
    desc = ("Five proposals for how you enter the ArUco Tags add-in and move between its jobs: one routed dialog, a hub "
            "palette, right-click first, one verb with undo, native homes with badges; mock-ups, step counts, a recommendation.")
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%s</title>
<meta name="description" content="%s">
<style>%s%s</style></head>
<body>
%s
<header class="topbar"><div class="in">
<a class="brand" href="index.html">%s<span>ArUco in Fusion</span></a>
<nav aria-label="Reports">%s</nav><span class="sp"></span>
<button id="themebtn" type="button" aria-label="Switch colour theme">Theme: auto</button>
</div></header>
<div class="wrap"><div class="layout">
%s
<main class="content">
<div class="hero"><div class="eyebrow">Proposal · entry points and ergonomics · %s</div><h1>%s</h1><p class="lede">%s</p><p class="meta">%s</p></div>
%s
<div class="foot">Generated %s by <code>docs/build_aruco_entry_points_report.py</code> from <code>docs/proposal_aruco_entry_points.md</code>.
Mock-ups are drawn over two cleaned Fusion screenshots; every marker is a real DICT_4X4_50 pattern from <code>bam_cad_core.aruco_dict</code>;
nothing on this page loads from the network.</div>
</main></div></div>
<script>%s</script>
</body></html>""" % (esc(title), esc(desc), B.CSS, EXTRA_CSS, defs, brand, nav, B.toc_html(toc), BUILT, esc(title), lede, meta,
                     body, BUILT, B.JS)


def build() -> str:
    global TB
    text = SRC.read_text(encoding="utf-8")
    title, pre, secs = B.split_md(text)
    paras = [p for p in re.split(r"\n\s*\n", "\n".join(pre).strip()) if p.strip()]
    lede = B.inline(" ".join(paras[0].split())) if paras else ""
    meta = B.inline(" ".join(paras[1].split())) if len(paras) > 1 else ""

    base01, TB = clean_base("01_aruco_panel_tooltip.jpg")
    base06, _ = clean_base("06_tag_dialog_rectangle_selected.jpg", old_dialog=True)
    defs = ('<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>'
            '<image id="base01" href="%s" width="%d" height="%d"/><image id="base06" href="%s" width="%d" height="%d"/>'
            '</defs></svg>' % (uri(base01), BASE_W, BASE_H, uri(base06), BASE_W, BASE_H))

    hooks = {
        "2": {"after": fig_today_real() + fig_today_mock(), "toc": "What exists today"},
        "3": {"after": fig_icons(), "toc": "Icons first"},
        "4": {"toc": "A: one button, routed dialog"},
        "4.1": {"after": fig_a()},
        "5": {"toc": "B: hub palette"},
        "5.1": {"after": fig_b()},
        "6": {"toc": "C: right-click first"},
        "6.1": {"after": fig_c()},
        "7": {"toc": "D: Tag it"},
        "7.1": {"after": fig_d()},
        "8": {"toc": "E: native homes + badges"},
        "8.1": {"after": fig_e()},
        "9": {"after": fig_timeline(), "toc": "The timeline feature (pending)"},
        "10": {"table": cmp_table},
        "12": {"toc": "Open questions"},
    }
    for key, prop in JOB_TABLES.items():
        hooks[key] = {"table": (lambda p: (lambda h, b, i: check_job_table(p, h, b, i)))(prop)}
    keys = {s["key"] for s in secs}
    for k in hooks:
        if k not in keys:
            warn("hook for section %r but the doc has no such section" % k)
    body, toc = B.render_sections(secs, hooks)
    return page(title, lede, meta, body, toc, defs)


def main() -> int:
    html_ = build()
    check_html(html_)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html_, encoding="utf-8")
    print("wrote %s (%s; inlined media %s)" % (OUT, B.fmt_bytes(OUT.stat().st_size), B.fmt_bytes(_BYTES[0])))
    if WARN:
        print("%d warning(s)" % len(WARN), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
