#!/usr/bin/env python3
# -*- coding: utf-8 -*-
""""ID labels and orientation marks on printed ArUco tags" -- one self-contained page.

    python tools/study_aruco_marks.py          # (oracle side, cv2) -> reports/media/aruco/marks/
    python docs/build_aruco_marks_report.py    # -> reports/aruco/marks-proposals.html

Prose comes from docs/proposal_aruco_marks.md (rendered with the house
Markdown renderer of build_aruco_reports.py). Every chart, table and number
in a figure is drawn from reports/media/aruco/marks/results.json, written by
the experiment; every sticker drawing is the experiment's own SVG of the
design (variants/<key>.svg), so the page shows exactly what was measured.
Numbers quoted in the Markdown are checked against the JSON at build time
(FACTS below); a mismatch prints a warning and the build exits 1.

Stdlib only at build time. Nothing on the page loads from the network.
"""
from __future__ import annotations

import base64
import json
import math
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docs"))
sys.path.insert(0, str(ROOT / "CADExporter"))

import build_aruco_reports as B  # noqa: E402  (house style: CSS, JS, markdown, svg helpers)
from bam_cad_core import aruco_dict  # noqa: E402

SRC = ROOT / "docs" / "proposal_aruco_marks.md"
OUT = ROOT / "reports" / "aruco" / "marks-proposals.html"
MEDIA = ROOT / "reports" / "media" / "aruco" / "marks"
PAGES = B.PAGES + [("entry", "entry-points-proposals.html", "Entry points"),
                   ("marks", "marks-proposals.html", "ID & orientation marks")]
BUILT = date.today().isoformat()
D4 = "DICT_4X4_50"
DEFAULT = "combo_black"            # the suggested default design (id in the border + corner triangle)
WARN: list = []
esc = B.esc
R: dict = {}


def warn(m):
    WARN.append(m)
    print("WARN:", m, file=sys.stderr)


# ================================================================ number formats

MINUS = "−"


def pts(d, signed=True, nd=1):
    """A rate difference (fraction) as percentage points, e.g. -0.113 -> '−11.3'."""
    v = round(d * 100, nd)
    if v == 0:
        v = 0.0
    s = ("%+." + str(nd) + "f") % v if signed else ("%." + str(nd) + "f") % v
    return s.replace("-", MINUS)


def pct(r, nd=1):
    return ("%." + str(nd) + "f%%") % (r * 100)


def tick_pts(v):
    """Axis tick in points (v already in points): '0', '−20'."""
    return "0" if abs(v) < 1e-9 else pts(v / 100, nd=0)


def ci(c):
    return "[%s, %s]" % (pts(c[0]), pts(c[1]))


# ================================================================ data access


def S(key):
    return R["stress"]["summary"][key]


def title(key):
    return R["designs"][key]["title"]


def verdict(s) -> tuple:
    """(class, words) for a paired stress result: the 95 % CI is the judge."""
    lo, hi = s["ci95"]
    d = s["delta"]
    if hi < 0:
        return ("no", "costs detections") if d <= -0.01 else ("warn", "small cost (< 1 pt)")
    if lo > 0:
        return "yes", "no cost (slightly better)"
    return ("yes", "no measurable cost") if abs(d) < 0.01 else ("", "unclear")


# ================================================================ svg helpers

def svgfile(key, cls="vt", label=None):
    p = MEDIA / "variants" / ("%s.svg" % key)
    if not p.exists():
        warn("missing variant drawing %s" % p)
        return ""
    s = p.read_text(encoding="utf-8")
    s = s.replace("<svg ", '<svg class="%s" ' % cls, 1)
    if label:
        s = re.sub(r'aria-label="[^"]*"', 'aria-label="%s"' % esc(label), s, count=1)
    return s


def png_uri(path: Path) -> str:
    if not path.exists():
        warn("missing image %s" % path)
        return ""
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def T(x, y, s, cls="", size=None, anchor="start", weight=None):
    return B.svg_text(x, y, s, cls=cls, size=size, anchor=anchor, weight=weight)


def nice_ticks(lo, hi, n=5):
    span = hi - lo
    step = 10 ** math.floor(math.log10(span / n))
    for m in (1, 2, 2.5, 5, 10):
        if span / (step * m) <= n:
            step *= m
            break
    t = math.ceil(lo / step) * step
    out = []
    while t <= hi + 1e-9:
        out.append(round(t, 10))
        t += step
    return out


def panel(x0, y0, w, h, xs, series, ylim, *, xlog=False, xticks=None, yticks=None, xlab="", ylab="",
          yfmt=lambda v: "%g" % v, xfmt=lambda v: "%g" % v, ptitle="", tipx=lambda v: "%g" % v,
          tipy=lambda v: "%g" % v, zero=None):
    """One line-chart panel. series: [{name, ys, cls ('s1'..'s5' | 'base'), dash, labels}]."""
    o = []
    x_lo, x_hi = (min(xs), max(xs))
    tx = (lambda v: math.log(v)) if xlog else (lambda v: v)

    def X(v):
        return x0 + (tx(v) - tx(x_lo)) / (tx(x_hi) - tx(x_lo)) * w

    def Y(v):
        return y0 + h - (v - ylim[0]) / (ylim[1] - ylim[0]) * h

    if ptitle:
        o.append(T(x0, y0 - 12, ptitle, "t-b", 13))
    for t in (yticks or nice_ticks(*ylim)):
        o.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" class="grid"/>' % (x0, Y(t), x0 + w, Y(t)))
        o.append(T(x0 - 6, Y(t) + 4, yfmt(t), "t-mut tn", 11, "end"))
    if zero is not None:
        o.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" class="zero"/>' % (x0, Y(zero), x0 + w, Y(zero)))
    o.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" class="axis"/>' % (x0, y0 + h, x0 + w, y0 + h))
    for t in (xticks or xs):
        o.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" class="axis"/>' % (X(t), y0 + h, X(t), y0 + h + 4))
        o.append(T(X(t), y0 + h + 17, xfmt(t), "t-mut tn", 11, "middle"))
    if xlab:
        o.append(T(x0 + w / 2, y0 + h + 36, xlab, "t-mut", 11.5, "middle"))
    if ylab:
        o.append('<text transform="translate(%.1f %.1f) rotate(-90)" class="t-mut" font-size="11.5" '
                 'text-anchor="middle">%s</text>' % (x0 - 44, y0 + h / 2, esc(ylab)))
    for s in series:
        p = [(X(x), Y(y)) for x, y in zip(xs, s["ys"]) if y is not None]
        if not p:
            continue
        cls = "ln base" if s["cls"] == "base" else "ln st-%s" % s["cls"]
        dash = ' stroke-dasharray="6 4"' if s.get("dash") else ""
        o.append('<polyline points="%s" class="%s"%s/>' % (" ".join("%.1f,%.1f" % q for q in p), cls, dash))
        fcls = "dot base" if s["cls"] == "base" else "dot f-%s" % s["cls"]
        for (px, py), x, y in zip(p, [x for x, y in zip(xs, s["ys"]) if y is not None],
                                  [y for y in s["ys"] if y is not None]):
            o.append('<circle cx="%.1f" cy="%.1f" r="%s" class="%s"/>' % (px, py, 3.2 if s["cls"] == "base" else 4,
                                                                         fcls))
            o.append('<circle cx="%.1f" cy="%.1f" r="11" class="hit" data-tip="%s · %s: %s"/>'
                     % (px, py, esc(s["name"]), esc(tipx(x)), esc(tipy(y))))
    return "".join(o)


def legend_html(items):
    """items: [(name, cls, dash)] -> HTML legend row with line keys."""
    out = []
    for name, cls, dash in items:
        col = "var(--muted)" if cls == "base" else "var(--%s)" % cls
        style = "background:%s" % col if not dash else "background:repeating-linear-gradient(90deg,%s 0 6px,transparent 6px 9px)" % col
        out.append('<span><i class="lk" style="%s"></i>%s</span>' % (style, esc(name)))
    return '<div class="legend">%s</div>' % "".join(out)


def table_view(head, rows, summary="Table view"):
    th = "".join("<th>%s</th>" % esc(h) for h in head)
    trs = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % c for c in r) for r in rows)
    return ('<details class="src"><summary>%s</summary><div class="tablewrap"><table class="md tn">'
            '<thead><tr>%s</tr></thead><tbody>%s</tbody></table></div></details>' % (summary, th, trs))


# ================================================================ figures


def stat_tiles():
    w, b, cb = S("white_q1_id")["default"], S("black_id_white")["default"], S(DEFAULT)["default"]
    wrong = sum(v["wrong_default"] + v["wrong_subpix"] for v in R["stress"]["summary"].values())
    n = R["stress"]["n"]
    tiles = [
        (pts(w["delta"]) + " pts", "grey ID in the white quiet zone (today's 1-module zone): detections lost vs the "
         "plain marker, %d paired hard trials" % n),
        (pts(b["delta"]) + " pts", "white ID inside the black border: same trials, %s" % ci(b["ci95"])),
        (pts(cb["delta"]) + " pts", "suggested default (border ID + corner triangle), %s" % ci(cb["ci95"])),
        ("%d" % wrong, "wrong ids or flipped corner orders, across %d designs x %d trials x 2 detectors"
         % (len(R["stress"]["summary"]), n)),
    ]
    return '<div class="stats">%s</div>' % "".join('<div class="stat"><b>%s</b><span>%s</span></div>'
                                                   % (esc(a), esc(c)) for a, c in tiles)


def fig_default():
    """The suggested default, big, next to what the sheet would print."""
    cards = []
    for key, cap in (("base_q1", "today: the plain sticker (label under the cut line only)"),
                     (DEFAULT, "suggested default: white ID knocked out of the bottom border, grey triangle in the "
                               "top-left quiet-zone corner (corner 0 / board origin)"),
                     ("combo_strip", "zero-ink-in-the-quiet-zone alternative: a label strip inside the cut "
                                     "+ a chamfered top-left corner")):
        cards.append('<figure>%s<figcaption>%s</figcaption></figure>' % (svgfile(key, "vt lg"), esc(cap)))
    return B.fig('<div class="vrow">%s</div>' % "".join(cards),
                 "Drawn from the experiment's own design files (DICT_4X4_50 id 23 on a grey part). The default "
                 "puts no ink in the white ring the detector needs.", "measured design")


def fig_pipeline():
    steps = [("Design", "marks in modules: text, triangles, dots, cut outline"),
             ("PDF", "same Helvetica + grey operators as print_sheet"),
             ("pdfium", "rasterised at 64 px / module"),
             ("Printer", "dot gain, mid-tone gain, halftone, paper / toner"),
             ("Camera", "px/module, tilt, blur, light, noise, JPEG"),
             ("OpenCV", "ArucoDetector defaults + SUBPIX; CharucoDetector")]
    o, x = [], 10
    bw, gap = 148, 14
    for i, (h, sub) in enumerate(steps):
        o.append('<rect x="%d" y="14" width="%d" height="78" rx="10" class="%s"/>' % (x, bw, "b-acc" if i in (0, 5)
                                                                                        else "box"))
        o.append(T(x + 12, 38, h, "t-b", 13.5))
        o.append(B.svg_text(x + 12, 58, sub, cls="t-mut", size=11, width=21, lh=1.25))
        if i < len(steps) - 1:
            o.append('<line x1="%d" y1="53" x2="%d" y2="53" class="edge" marker-end="url(#pl-ah)"/>'
                     % (x + bw + 1, x + bw + gap - 2))
        x += bw + gap
    return B.svg(B.arrow_defs("pl") + "".join(o), x, 104, "Experiment pipeline")


def fig_examples():
    ex = R["media"].get("camera_examples", {})
    img = png_uri(MEDIA / "camera_examples.png")
    cols = ", ".join(ex.get("cols", []))
    rows = "; ".join("row %d: %s" % (i + 1, r) for i, r in enumerate(ex.get("rows", [])))
    return B.fig('<img src="%s" alt="Simulated camera images of eight designs" style="display:block;'
                 'width:min(100%%,900px);height:auto;margin:0 auto;image-rendering:pixelated">' % img,
                 "What the detector is given: one fixed condition per row, cropped to the sticker and enlarged "
                 "without smoothing. Green frame = detected with the right id, red = missed. Columns: %s. %s. "
                 "Single photos flip either way near the limit; the statistics are in section 5.%s"
                 % (esc(cols), esc(rows), flip_note(ex)), "simulated camera")


def flip_note(ex) -> str:
    """If the default misses in an example row where the plain marker passes, say what the statistics say."""
    det, cols = ex.get("detected") or [], ex.get("cols") or []
    if DEFAULT not in cols or "base_q1" not in cols:
        return ""
    i, j = cols.index(DEFAULT), cols.index("base_q1")
    rows = [r + 1 for r, d in enumerate(det) if d[j] and not d[i]]
    if not rows:
        return ""
    c = R["stress"]["by_print_cell"][DEFAULT]["h3"]
    return (" In row %s the default (%s) misses a photo the plain marker passes; over the %d stress-mix photos printed "
            "the same way (halftone, 0.12-module spread) it lost %d and gained %d against the plain marker."
            % (", ".join(map(str, rows)), DEFAULT, c["n"], c["lost"], c["gained"]))


def fig_mechanism():
    mech = R["media"].get("mechanism") or {}
    if not mech:
        warn("results.json has no mechanism figure; run tools/study_aruco_marks.py --media-only")
        return ""
    c = mech["condition"]
    verdicts = ", ".join("%s: %s" % (k, "4-sided, convex" if mech[k]["outline_is_quad"] else
                                     "%d vertices, rejected" % mech[k]["vertices"]) for k in mech["designs"])
    return B.fig('<img src="%s" alt="Camera image and adaptive threshold of three designs" style="display:block;'
                 'width:min(100%%,760px);height:auto;margin:0 auto;image-rendering:pixelated">'
                 % png_uri(MEDIA / "mechanism.png"),
                 "One trial from the stress mix (%.1f px/module, tilt %.0f°, blur %.2f px, dot gain level %d, white part), "
                 "three designs left to right: plain, grey id in the white zone, white id in the border. Top: the "
                 "camera image. Bottom: OpenCV's adaptive threshold (window 13, C = 7), and the outline of the blob "
                 "around the marker with its polygon approximation. %s. Ink from the id has joined the outline (the extra vertex), "
                 "so the white-zone sticker never reaches bit decoding; the border id is a few holes inside the blob."
                 % (c["ppm"], c["tilt"], c["blur"], c["dg"], esc(verdicts)), "simulated camera", cls="")


FAMILIES = [("base", "Baselines"), ("white", "ID in the white quiet zone"), ("black", "ID inside the black border"),
            ("outside", "Outside the quiet zone, inside the cut"), ("orient", "Orientation marks"),
            ("combo", "Combinations")]


def fig_gallery():
    out = []
    for fam, label in FAMILIES:
        keys = [k for k in R["stress"]["summary"] if R["designs"][k]["family"] == fam]
        cards = []
        for k in keys:
            s = S(k)["default"]
            if k == R["designs"][k]["baseline"]:
                chip = '<span class="qtag">baseline</span>'
                num = "detected %s" % pct(s["rate"])
            else:
                cls, words = verdict(s)
                chip = '<span class="qtag %s">%s</span>' % (cls, words)
                num = "%s pts %s · lost %d / gained %d" % (pts(s["delta"]), ci(s["ci95"]), s["lost"], s["gained"])
            note = R["designs"][k].get("note") or ""
            cards.append('<div class="vcard">%s<div class="vb"><div class="vk"><code>%s</code></div><div class="vt2">%s</div>'
                         '<div class="vn tn">%s</div>%s%s</div></div>'
                         % (svgfile(k), esc(k), esc(title(k)), esc(num), chip,
                            '<div class="vnote">%s</div>' % esc(note) if note else ""))
        out.append('<div class="vfam">%s</div><div class="vgrid">%s</div>' % (esc(label), "".join(cards)))
    return B.fig("".join(out), "Every single-marker design in the study (DICT_4X4_50 id 23 shown; the trials use eight "
                 "ids). Numbers: detection rate minus the design's plain baseline over %d paired hard trials, default "
                 "detector parameters, 95%% CI; lost = trials the plain marker passed and this one failed."
                 % R["stress"]["n"], "measured design")


def fig_forest():
    order = []
    for fam, label in FAMILIES:
        ks = [k for k in R["stress"]["summary"] if R["designs"][k]["family"] == fam
              and R["designs"][k]["baseline"] != k]
        ks.sort(key=lambda k: S(k)["default"]["delta"])
        if ks:
            order.append(("h", label))
            order += [("k", k) for k in ks]
    lo = min(min(S(k)["default"]["ci95"][0] for t, k in order if t == "k"), -0.05)
    hi = max(max(S(k)["default"]["ci95"][1] for t, k in order if t == "k"), 0.03)
    lo, hi = math.floor(lo * 20) / 20, math.ceil(hi * 50) / 50
    x0, w, row = 250, 560, 21
    H = 20 + row * len(order) + 40

    def X(v):
        return x0 + (v - lo) / (hi - lo) * w

    o = []
    for t in nice_ticks(lo * 100, hi * 100, 8):
        o.append('<line x1="%.1f" y1="14" x2="%.1f" y2="%d" class="grid"/>' % (X(t / 100), X(t / 100), H - 30))
        o.append(T(X(t / 100), H - 14, tick_pts(t), "t-mut tn", 11, "middle"))
    o.append('<line x1="%.1f" y1="14" x2="%.1f" y2="%d" class="zero"/>' % (X(0), X(0), H - 30))
    y = 24
    for t, k in order:
        if t == "h":
            o.append(T(8, y + 4, k, "t-b", 12.5))
            y += row
            continue
        s = S(k)["default"]
        a, b = X(s["ci95"][0]), X(s["ci95"][1])
        o.append(T(22, y + 4, k, "t-mono", 11.5))
        o.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" class="ln st-s1" />' % (a, y, b, y))
        o.append('<circle cx="%.1f" cy="%d" r="4.5" class="dot f-s1"/>' % (X(s["delta"]), y))
        o.append(T(x0 + w + 12, y + 4, "%s  (%d / %d)" % (pts(s["delta"]), s["lost"], s["gained"]), "t-mut tn", 11))
        o.append('<rect x="0" y="%d" width="%d" height="%d" class="hit" data-tip="%s: %s pts, 95%% CI %s; lost %d, '
                 'gained %d of %d"/>' % (y - row / 2, x0 + w + 120, row, esc(k), pts(s["delta"]), ci(s["ci95"]),
                                         s["lost"], s["gained"], s["n"]))
        y += row
    o.append(T(X(0), 12, "← loses detections   ·   gains →", "t-mut", 11, "middle"))
    svg = B.svg("".join(o), x0 + w + 130, H, "Paired change in detection rate per design")
    rows = [[esc(k), pct(S(k)["default"]["base_rate"]), pct(S(k)["default"]["rate"]), pts(S(k)["default"]["delta"]),
             ci(S(k)["default"]["ci95"]), str(S(k)["default"]["lost"]), str(S(k)["default"]["gained"]),
             "%.3g" % S(k)["default"]["p_mcnemar"], str(S(k)["wrong_default"] + S(k)["wrong_subpix"])]
            for t, k in order if t == "k"]
    tv = table_view(["design", "baseline", "design rate", "Δ pts", "95% CI", "lost", "gained", "McNemar p", "wrong"],
                    rows)
    return B.fig(svg + tv, "Detection rate minus the plain baseline, paired over the same %d hard trials (dot = "
                 "estimate, bar = 95%% CI; in brackets: trials lost / gained). Default detector parameters; "
                 "CORNER_REFINE_SUBPIX detects exactly the same trials (refinement only moves corners)."
                 % R["stress"]["n"], "measured", cls="wide")


def fig_dose():
    d = R["dose"]["summary"]
    caps = R["dose"]["caps"]
    W_, H_, pw, ph = 990, 300, 178, 180
    o = []
    allv = []
    for cap in caps:
        for g in R["dose"]["white_grey"]:
            allv.append(d["dw_g%02d_c%02d" % (round(g * 100), round(cap * 100))]["default"]["delta"])
    lo = math.floor(min(allv + [-0.05]) * 10) / 10
    for i, cap in enumerate(caps):
        xs = [round(1 - g, 2) for g in R["dose"]["white_grey"]]
        yw = [d["dw_g%02d_c%02d" % (round(g * 100), round(cap * 100))]["default"]["delta"] * 100
              for g in R["dose"]["white_grey"]]
        yb = [d["db_g%02d_c%02d" % (round(g * 100), round(cap * 100))]["default"]["delta"] * 100
              for g in R["dose"]["black_grey"]]
        xb = [round(g, 2) for g in R["dose"]["black_grey"]]
        sw = sorted(zip(xs, yw))
        sb = sorted(zip(xb, yb))
        xx = [a for a, _ in sw]
        o.append(panel(66 + i * (pw + 56), 40, pw, ph, xx,
                       [{"name": "black border", "ys": [b for _, b in sb], "cls": "s1"},
                        {"name": "white zone", "ys": [b for _, b in sw], "cls": "s2"}],
                       (lo * 100, 3), xticks=[0.1, 0.5, 1.0], xfmt=lambda v: "%g" % v,
                       yfmt=tick_pts, xlab="contrast" if i else "contrast a person sees",
                       ylab="Δ detection, pts" if i == 0 else "", ptitle="text cap %.2f module" % cap,
                       tipx=lambda v: "contrast %.2f" % v, tipy=lambda v: "%s pts" % pts(v / 100), zero=0))
    svg = B.svg("".join(o), W_, H_, "White zone versus black border text at matched contrast")
    rows = []
    for cap in caps:
        for gw, gb in zip(R["dose"]["white_grey"], R["dose"]["black_grey"]):
            a = d["dw_g%02d_c%02d" % (round(gw * 100), round(cap * 100))]["default"]
            b = d["db_g%02d_c%02d" % (round(gb * 100), round(cap * 100))]["default"]
            rows.append(["%.2f" % cap, "%.2f" % (1 - gw), "grey %.2f: %s %s" % (gw, pts(a["delta"]), ci(a["ci95"])),
                         "grey %.2f: %s %s" % (gb, pts(b["delta"]), ci(b["ci95"]))])
    tv = table_view(["cap (modules)", "contrast", "white quiet zone (q = 1)", "black border"], rows)
    return B.fig(legend_html([("ID inside the black border", "s1", False), ("ID in the white quiet zone", "s2", False)])
                 + svg + tv,
                 "The same \"#id\" text, at the same contrast against its surroundings (dark grey on white = light "
                 "grey on black), in the two places. %d paired hard trials per point; default parameters. Quiet "
                 "zone 1 module, so a white-zone text of cap c leaves (1 − c)/2 module of clean white each side."
                 % R["dose"]["n"], "measured", cls="wide")


PRINT_CELLS = [("c0", "tone 0"), ("c1", "tone .04"), ("c2", "tone .08"), ("c3", "tone .12"),
               ("h0", "dots 0"), ("h1", "dots .04"), ("h2", "dots .08"), ("h3", "dots .12")]


def fig_print_cells():
    cells = R["stress"].get("by_print_cell")
    if not cells:
        warn("results.json has no by_print_cell breakdown")
        return ""
    keys = ["white_q1_light", "white_q1_id", "white_q1_black", "white_q15_id", "white_q2_id", "black_id_faint",
            "black_id_white", "black_id_inner", "black_id_big", "tri_tl", "chamfer_tl", "strip_q1", "combo_black",
            "combo_black_inner", "combo_white", "combo_strip"]
    head = "".join("<th>%s</th>" % esc(lab) for _, lab in PRINT_CELLS)
    trs = []
    for k in keys:
        tds = []
        for c, lab in PRINT_CELLS:
            v = cells[k][c]
            d = v["delta"]
            a = min(48, 8 + int(round(abs(d) * 100 * 1.2))) if d < 0 and v["ci95"][1] < 0 else 0
            style = (' style="background:color-mix(in srgb, var(--bad) %d%%, transparent)"' % a) if a else ""
            tds.append('<td%s data-tip="%s · %s: %s pts %s, n %d, lost %d / gained %d">%s</td>'
                       % (style, esc(k), esc(lab), pts(d), ci(v["ci95"]), v["n"], v["lost"], v["gained"], pts(d)))
        trs.append("<tr><td><code>%s</code></td>%s</tr>" % (esc(k), "".join(tds)))
    tb = ('<div class="tablewrap"><table class="md tn heat"><thead><tr><th>design</th>%s</tr></thead><tbody>%s'
          '</tbody></table></div>' % (head, "".join(trs)))
    ncell = sum(cells["base_q1"][c]["n"] for c, _ in PRINT_CELLS) // len(PRINT_CELLS) if "base_q1" in cells else 0
    return B.fig(tb, "The stress mix split by how the sticker was printed. tone = grey printed as continuous tone, "
                 "dots = grey printed as laser-style halftone dots; the number is the ink spread per edge in modules "
                 "(0.04 module = 0.1 mm on a 15 mm 4x4 marker, 0.2 mm on a 30 mm one). Δ detection in points against "
                 "the plain marker, about %d paired trials per cell; shaded = a loss whose 95%% CI excludes zero, "
                 "darker = bigger. Hover a cell for the interval." % ncell, "measured", cls="")


def fig_gaps():
    g = R["gaps"]["summary"]
    xs = R["gaps"]["gaps"]
    ye = [g["ge_%03d" % round(v * 100)]["default"]["delta"] * 100 for v in xs]
    yc = [g["gc_%03d" % round(v * 100)]["default"]["delta"] * 100 for v in xs]
    lo = math.floor(min(ye + yc + [-5]) / 5) * 5
    svg = B.svg(panel(80, 30, 600, 220, xs,
                      [{"name": "ID text below an edge", "ys": ye, "cls": "s2"},
                       {"name": "ink square off corner 0", "ys": yc, "cls": "s1"}],
                      (lo, 3), xticks=xs, xfmt=lambda v: "%g" % v, yfmt=tick_pts,
                      xlab="clean white between the black border and the nearest ink (modules)",
                      ylab="Δ detection, pts", tipx=lambda v: "%g module" % v, tipy=lambda v: "%s pts" % pts(v / 100),
                      zero=0), 760, 300, "Detection loss against clearance from the border")
    rows = [["%g" % v, "%s %s" % (pts(g["ge_%03d" % round(v * 100)]["default"]["delta"]),
                                  ci(g["ge_%03d" % round(v * 100)]["default"]["ci95"])),
             "%s %s" % (pts(g["gc_%03d" % round(v * 100)]["default"]["delta"]),
                        ci(g["gc_%03d" % round(v * 100)]["default"]["ci95"]))] for v in xs]
    tv = table_view(["clearance (modules)", "text below an edge", "square off a corner"], rows)
    return B.fig(legend_html([("ink square diagonally off corner 0", "s1", False),
                              ("ID text below the bottom edge", "s2", False)]) + svg + tv,
                 "Quiet zone 2 modules (so there is room), ink moved towards the black border. %d paired hard trials "
                 "per point, against the plain q = 2 marker." % R["gaps"]["n"], "measured", cls="mid")


def fig_ppm():
    o = []
    keys = [("base_q1", "base"), ("black_id_white", "s1"), ("white_q1_id", "s2")]
    panels = [("ppm", "laser halftone, ink spread 0.04 module"), ("ppm_dg2", "laser halftone, ink spread 0.08 module")]
    for i, (f, ttl) in enumerate(panels):
        sw = R["sweeps"][f]
        ser = [{"name": k, "ys": [r["rate_default"] * 100 for r in sw["designs"][k]], "cls": c} for k, c in keys]
        o.append(panel(76 + i * 470, 40, 390, 200, sw["x"], ser, (0, 100), xlog=True,
                       xticks=[1.5, 2, 2.5, 3, 4, 5, 8, 12, 20], yfmt=lambda v: "%d%%" % v,
                       xlab="camera px per module (log)", ylab="detected" if i == 0 else "", ptitle=ttl,
                       tipx=lambda v: "%g px/module" % v, tipy=lambda v: "%.1f%%" % v))
    svg = B.svg("".join(o), 960, 300, "Detection rate against camera pixels per module, two print qualities")
    mp = R["sweeps"]["ppm"]["min_ppm"]
    mp2 = R["sweeps"].get("ppm_dg2", {}).get("min_ppm", {})
    rows = [[esc(k), "%s" % mp[k]["p90_default"], "%s" % mp[k]["p50_default"],
             "%s" % mp2.get(k, {}).get("p90_default"), "%s" % mp2.get(k, {}).get("p50_default")]
            for k in R["stress"]["summary"] if k in mp]
    tv = table_view(["design", "≥ 90 % detected (spread 0.04)", "≥ 50 % (spread 0.04)", "≥ 90 % (spread 0.08)",
                     "≥ 50 % (spread 0.08)"], rows, "Table view: smallest px/module that still detects, every design")
    n = R["sweeps"]["ppm"]["n_per_point"]
    cap = ("%d trials per point at the nominal condition (tilt 0–30°, blur 0.5 px, noise 3, mild gradient, mid-grey "
           "part), default parameters, at two ink spreads. Grey line: the plain q = 1 marker." % n)
    return B.fig(legend_html([("plain marker", "base", False), ("black_id_white", "s1", False),
                              ("white_q1_id", "s2", False)]) + svg + tv, cap, "measured", cls="wide")


def factor_table():
    keys = ["base_q1", "white_q1_id", "white_q1_black", "white_q15_id", "black_id_white", "black_id_inner",
            "black_id_big", "tri_tl", "tri_tl_near", "chamfer_tl", "combo_black_inner", "combo_white", "combo_strip"]
    names = {"tilt": "tilt (°)", "blur": "blur (px)", "noise": "noise σ", "jpeg": "JPEG q",
             "grad": "light gradient", "print": "print"}
    out = []
    for f, lab in names.items():
        fo = R["sweeps"][f]
        head = ["design"] + [("%s" % v) if f != "jpeg" or v else "none" for v in fo["x"]]
        rows = []
        for k in keys:
            rows.append([esc(k)] + ["%.0f" % (r["rate_default"] * 100) for r in fo["designs"][k]])
        out.append('<div class="ft"><div class="fth">%s <span class="t-mut">(%% detected, %d trials per cell)</span>'
                   '</div><div class="tablewrap"><table class="md tn small"><thead><tr>%s</tr></thead><tbody>%s'
                   '</tbody></table></div></div>'
                   % (esc(lab), fo["n_per_point"], "".join("<th>%s</th>" % esc(h) for h in head),
                      "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % c for c in r) for r in rows)))
    note = ("print: c = contone (grey printed as grey), h = halftone (laser-style clustered dots); 0-3 = dot gain level "
            "(ink spread 0, 0.04, 0.08, 0.12 module).")
    return ('<details class="src"><summary>One factor at a time: tilt, blur, noise, JPEG, light, print (tables)'
            '</summary><div class="srcbody">%s<p class="t-mut" style="font-size:14px">%s</p></div></details>'
            % ("".join(out), esc(note)))


def fig_stress_ppm():
    b = R["stress"]["by_ppm"]
    keys = [("base_q1", "base"), ("black_id_white", "s1"), ("white_q1_id", "s2")]
    xs = [round((r["lo"] + r["hi"]) / 2, 2) for r in b["base_q1"]]
    ser = [{"name": k, "ys": [r["rate_default"] * 100 for r in b[k]], "cls": c} for k, c in keys]
    svg = B.svg(panel(80, 30, 560, 200, xs, ser, (0, 100), xticks=xs,
                      xfmt=lambda v: "%g" % v, yfmt=lambda v: "%d%%" % v, xlab="px per module (bin centre)",
                      ylab="detected", tipx=lambda v: "%g px/module" % v, tipy=lambda v: "%.1f%%" % v),
                700, 280, "Stress mix detection by resolution bin")
    return B.fig(legend_html([("plain q=1", "base", False), ("black_id_white", "s1", False),
                              ("white_q1_id", "s2", False)]) + svg,
                 "The stress mix split by resolution: the white-zone text costs detections at every resolution in "
                 "the mix, not only at the low end, because blur and tilt also close the small gap.",
                 "measured", cls="mid")


def fig_corners():
    keys = ["tri_tl", "tri_tl_near", "white_q1_corner", "dot_tl", "dot_tl_big", "black_id_white", "black_id_inner",
            "black_id_big", "combo_black_inner", "combo_white"]
    rows = []
    for k in keys:
        cells = [esc(k)]
        for ppm in ("6.0", "12.0", "20.0"):
            c = R["clean"][ppm][k]["subpix"]
            cells.append("%+.3f / %+.3f" % (c["c0_minus_base_mod"], c["worst_corner_minus_base_mod"]))
        st = S(k)["corners_subpix"]
        cells.append("%.3f vs %.3f" % (st["mean_err_mod"], st["base_mean_err_mod"]))
        rows.append(cells)
    head = ["design", "6 px/mod: corner 0 / worst corner", "12 px/mod", "20 px/mod", "stress mix: mean error vs plain"]
    tb = ('<div class="tablewrap"><table class="md tn"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>'
          % ("".join("<th>%s</th>" % esc(h) for h in head),
             "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % c for c in r) for r in rows)))
    return B.fig(tb, "CORNER_REFINE_SUBPIX corner error, in MODULES, minus the plain marker's on the same images. "
                 "Clean frontal views (no dot gain, noise 0, blur 0.4 px), %d trials per resolution: corner 0 "
                 "(top-left) and the worst of the four. Last column: mean over the stress mix (includes dot gain, "
                 "which grows every black square for every design alike)." % 60, "measured", cls="")


def fig_charuco():
    st = R["charuco"]["stress"]["summary"]
    sw = R["charuco"]["sweep"]
    keys = ["ch_base", "ch_ids_white", "ch_ids_black", "ch_ids_black_inner", "ch_label_black", "ch_base_q2",
            "ch_label_margin"]
    thumbs = "".join('<figure>%s<figcaption><code>%s</code></figcaption></figure>' % (svgfile(k, "vt board"), esc(k))
                     for k in keys if k in st)
    rows = []
    for k in keys:
        s = st[k]
        rows.append([esc(k), esc(R["designs"][k]["title"]), esc(s["baseline"]),
                     "%.2f vs %.2f" % (s["corners_mean"], s["base_corners_mean"]),
                     "%d / %d" % (s["trials_fewer"], s["trials_more"]),
                     "%s vs %s" % (s["rms_err_mod"], s["base_rms_err_mod"]), str(s["wrong"]),
                     " ".join("%.1f" % r["corners_mean"] for r in sw["designs"][k])])
    head = ["design", "what", "baseline", "corners found (of 12), stress", "trials fewer / more corners",
            "corner RMS (modules)", "wrong", "corners vs px/module %s" % "/".join("%g" % v for v in sw["x"])]
    tb = ('<div class="tablewrap"><table class="md tn small"><thead><tr>%s</tr></thead><tbody>%s</tbody></table>'
          '</div>' % ("".join("<th>%s</th>" % esc(h) for h in head),
                      "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % c for c in r) for r in rows)))
    return B.fig('<div class="vrow">%s</div>%s' % (thumbs, tb),
                 "ChArUco 5×4 squares, DICT_4X4_50 ids 0–9, marker = 0.75 square, through cv2.aruco.CharucoDetector "
                 "(defaults). %d paired stress trials (px per marker module log-uniform 2.5–8, tilt to 65°, "
                 "blur, noise, JPEG, light, dot gain) plus a %d-trial-per-point resolution sweep."
                 % (R["charuco"]["stress"]["n"], 40), "measured", cls="")


def fig_dicts():
    rows = []
    for dn, r in R["dicts"].items():
        for k, v in r["summary"].items():
            rows.append([esc(dn), esc(k), pct(v["default"]["base_rate"]), pts(v["default"]["delta"]),
                         ci(v["default"]["ci95"]), "%d / %d" % (v["default"]["lost"], v["default"]["gained"]),
                         str(v["wrong_default"] + v["wrong_subpix"])])
    head = ["dictionary", "design", "baseline rate", "Δ pts", "95% CI", "lost / gained", "wrong"]
    tb = ('<div class="tablewrap"><table class="md tn"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>'
          % ("".join("<th>%s</th>" % esc(h) for h in head),
             "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % c for c in r) for r in rows)))
    thumbs = "".join('<figure>%s<figcaption><code>%s</code><br>%s</figcaption></figure>'
                     % (svgfile("%s_%s" % (k, dn), "vt"), esc(k), esc(dn))
                     for dn in ("DICT_6X6_250", "DICT_APRILTAG_36h11") for k in (DEFAULT, "combo_white"))
    return B.fig('<div class="vrow">%s</div>%s' % (thumbs, tb), "The same positions in module units on 8-module markers, %d paired stress trials each."
                 % next(iter(R["dicts"].values()))["n"], "measured", cls="")


def fig_options():
    """A sheet cell per option value, drawn from the study's designs."""
    items = [("base_q1", "marks: off"), ("black_id_white", "id_mark = border"), ("tri_tl", "orient_mark = triangle"),
             ("chamfer_tl", "orient_mark = chamfer"), ("strip_q1", "id_mark = strip"),
             ("white_q15_id", "id_mark = quiet (needs q ≥ 1.5)")]
    cards = "".join('<figure>%s<figcaption><code>%s</code></figcaption></figure>' % (svgfile(k, "vt"), esc(c))
                    for k, c in items)
    return B.fig('<div class="vrow">%s</div>' % cards, "One drawing per option value (each measured above).",
                 "measured design")


# ================================================================ follow-up: more text in the black

LABEL_KEYS = ["black_id_white", "lb_long_inner", "lb_long_full", "lb_fill_full", "lb_id_top", "lb_id_sides", "lb_four",
              "lb_two_04", "lb_two_03"]
D6 = "DICT_6X6_250"


def L_(dn, k):
    return R["label_text"]["dicts"][dn]["rows"][k]


def fig_label_gallery():
    lt = R.get("label_text")
    if not lt:
        warn("results.json has no label_text; run tools/study_aruco_marks.py --only label")
        return ""
    cards = []
    for k in LABEL_KEYS:
        r = L_(D4, k)
        a, b = r["vs_plain"], r["vs_id"]
        cls, words = verdict(a)
        texts = " · ".join("%s (cap %.2f)" % (t["text"], t["cap_modules"]) for t in r["texts"])
        num = ("vs plain %s pts %s · vs '#id' %s pts · lost %d / gained %d"
               % (pts(a["delta"]), ci(a["ci95"]), pts(b["delta"]), a["lost"], a["gained"]))
        cards.append('<div class="vcard">%s<div class="vb"><div class="vk"><code>%s</code></div><div class="vt2">%s</div>'
                     '<div class="vn tn">%s</div><div class="vn">%s</div><span class="qtag %s">%s</span></div></div>'
                     % (svgfile(k), esc(k), esc(r["title"]), esc(num), esc(texts), cls, words))
    return B.fig('<div class="vgrid">%s</div>' % "".join(cards),
                 "DICT_4X4_50 drawn with id 23 (the trials use eight ids); every mark is white knocked out of the black "
                 "border. %d paired hard trials per design, the same conditions as the main stress mix; default "
                 "detector parameters." % lt["n"], "measured design")


def fig_label_table():
    if not R.get("label_text"):
        return ""
    rows = []
    for dn in (D4, D6):
        for k in LABEL_KEYS:
            r = L_(dn, k)
            a, b, c = r["vs_plain"], r["vs_id"], r["crisp_vs_plain"]
            worst_c = min(r["by_print_cell"], key=lambda q: r["by_print_cell"][q]["delta"])
            wc = r["by_print_cell"][worst_c]
            cl = R["label_text"]["dicts"][dn]["clean"]["12.0"][k]
            texts = " / ".join("%s @%.2f" % (t["text"], t["cap_modules"]) for t in r["texts"])
            rows.append(["%s" % ("4x4_50" if dn == D4 else "6x6_250"), "<code>%s</code>" % esc(k), esc(texts),
                         "%s %s" % (pts(a["delta"]), ci(a["ci95"])), "%d / %d" % (a["lost"], a["gained"]),
                         "%s %s" % (pts(b["delta"]), ci(b["ci95"])),
                         "%s (%d / %d)" % (pts(c["delta"]), c["lost"], c["gained"]),
                         "%s %s" % (pts(wc["delta"]), worst_c),
                         "%+.3f" % cl.get("worst_corner_minus_base_mod", 0.0),
                         str(r["wrong_default"] + r["wrong_subpix"])])
    head = ["dict", "design", "text @ cap (modules)", "vs plain, pts", "lost / gained", "vs today's '#id', pts",
            "crisp print (no spread)", "worst print cell", "SUBPIX corner shift @12 px/mod (modules)", "wrong"]
    tb = ('<div class="tablewrap"><table class="md tn small"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>'
          % ("".join("<th>%s</th>" % esc(h) for h in head),
             "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % x for x in r) for r in rows)))
    return B.fig(tb, "Paired against the plain marker and against today's '#id' default (black_id_white) on the same "
                 "%d trials. Print cells: c = continuous tone, h = halftone dots, digit = ink-spread level 0-3. "
                 "Corner shift: CORNER_REFINE_SUBPIX error minus the plain marker's on clean frontal photos, worst of "
                 "the four corners." % R["label_text"]["n"], "measured", cls="")


def fig_label_6x6():
    if not R.get("label_text"):
        return ""
    cards = "".join('<figure>%s<figcaption><code>%s</code></figcaption></figure>' % (svgfile("%s_%s" % (k, D6), "vt"), esc(k))
                    for k in LABEL_KEYS[1:])
    return B.fig('<div class="vrow">%s</div>' % cards, "The same designs on DICT_6X6_250 (id 123): an 8-module marker "
                 "has a 5.6-module inner row, so the 15-character line already fits at cap 0.5 without the corner cells.",
                 "measured design")


def fig_label_charuco():
    lt = R.get("label_text")
    if not lt:
        return ""
    keys = ["ch_label_black", "ch_ids_black", "chl_label_more", "chl_ids_label", "chl_all"]
    thumbs = "".join('<figure>%s<figcaption><code>%s</code></figcaption></figure>' % (svgfile(k, "vt board"), esc(k))
                     for k in keys)
    rows = []
    for k in keys:
        a, b = lt["charuco"][k]["vs_plain"], lt["charuco"][k]["vs_label"]
        rows.append(["<code>%s</code>" % esc(k), esc(lt["charuco"][k]["title"]),
                     "%.2f vs %.2f" % (a["corners_mean"], a["base_corners_mean"]),
                     "%+.3f [%+.3f, %+.3f]" % (a["corners_delta"], a["corners_delta_ci95"][0], a["corners_delta_ci95"][1]),
                     "%d / %d" % (a["trials_fewer"], a["trials_more"]),
                     "%+.3f (%d / %d)" % (b["corners_delta"], b["trials_fewer"], b["trials_more"]),
                     "%s vs %s" % (a["rms_err_mod"], a["base_rms_err_mod"]), str(a["wrong"])])
    head = ["design", "what", "corners found (of 12)", "Δ corners vs plain [95% CI]", "trials fewer / more",
            "vs ch_label_black (fewer / more)", "corner RMS (modules)", "wrong"]
    tb = ('<div class="tablewrap"><table class="md tn small"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>'
          % ("".join("<th>%s</th>" % esc(h) for h in head),
             "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % x for x in r) for r in rows)))
    return B.fig('<div class="vrow">%s</div>%s' % (thumbs, tb),
                 "ChArUco 5×4 (ids 0–9) through CharucoDetector defaults, %d paired trials (the same hard conditions; "
                 "px/module per marker module). 'wrong' counts reported marker ids outside the board and corners more "
                 "than a module off, in the plain board as well." % lt["n"], "measured", cls="")


def fig_capacity():
    lt = R.get("label_text")
    if not lt:
        return ""
    cap = lt["capacity"]
    rows = []
    for r in cap["rows"]:
        def cell(tag):
            c = r[tag]
            return "%d / %d" % (c["chars_inner"], c["chars_full"]) if c["fits"] else "—"
        rows.append(["%s" % ("4x4" if r["dictionary"] == D4 else "6x6"), "%g mm" % r["marker_mm"],
                     "%.2f mm" % r["module_mm"], "%.2f mm (%.1f pt)" % (r["cap_max_mm"], r["cap_max"]["font_pt"]),
                     cell("cap_max"), cell("cap_1.0"), cell("cap_0.7")])
    head = ["dict", "marker", "module", "largest cap (0.5 module)", "chars per row at that cap",
            "chars per row at cap 1.0 mm (3.9 pt)", "chars per row at cap 0.7 mm (2.8 pt)"]
    tb = ('<div class="tablewrap"><table class="md tn"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>'
          % ("".join("<th>%s</th>" % esc(h) for h in head),
             "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % x for x in r) for r in rows)))
    return B.fig(tb, "Characters per border row, corner cells clear / whole row, in Helvetica at %.3f em per character "
                 "(the average of a typical label, “%s”). A side has one row; four sides give four rows. "
                 "Geometry only: no detection numbers in this table." % (cap["em_per_char"], esc(cap["sample"])),
                 "computed", cls="")


EXTRA_CSS = r"""
.dg .ln{fill:none; stroke-width:2; stroke-linejoin:round; stroke-linecap:round}
.dg .ln.base{stroke:var(--muted); stroke-width:3; opacity:.55}
.dg .dot{stroke:var(--surface); stroke-width:2}
.dg .dot.base{fill:var(--muted); opacity:.7}
.dg .hit{fill:transparent; stroke:none}
.dg .hit:hover{fill:var(--acc-weak); opacity:.35}
.dg .zero{stroke:var(--ink-2); stroke-width:1}
.dg .tn, .tn{font-variant-numeric:tabular-nums}
.legend .lk{display:inline-block; width:18px; height:3px; border-radius:2px; vertical-align:4px; margin-right:7px}
.vgrid{display:grid; grid-template-columns:repeat(auto-fill,minmax(min(100%,290px),1fr)); gap:12px; margin:8px 0 18px}
.vfam{font-weight:700; font-size:13px; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); margin:14px 2px 2px}
.vcard{display:grid; grid-template-columns:92px minmax(0,1fr); gap:12px; align-items:start; background:var(--surface-2);
  border:1px solid var(--line); border-radius:12px; padding:10px}
.vcard svg.vt{width:92px; height:92px}
svg.vt{display:block; border-radius:6px}
svg.vt.lg{width:min(100%,250px); height:auto}
svg.vt.board{width:min(100%,230px); height:auto}
.vb{min-width:0; font-size:13.5px; line-height:1.4}
.vk code{font-size:12px}
.vt2{color:var(--ink-2); margin:3px 0}
.vn{font-size:12.5px; color:var(--muted); margin-bottom:5px}
.vnote{font-size:12px; color:var(--warn); margin-top:4px}
.vrow{display:flex; flex-wrap:wrap; gap:18px 22px; align-items:flex-start; justify-content:center}
.vrow figure{margin:0; max-width:260px; text-align:center}
.vrow figure svg.vt{margin:0 auto; width:150px; height:150px}
.vrow figure svg.vt.lg{width:min(100%,240px); height:auto}
.vrow figure svg.vt.board{width:min(100%,250px); height:auto}
.vrow figcaption{font-size:13px; margin-top:6px}
table.md.small td, table.md.small th{font-size:13px; padding:6px 8px}
table.heat td, table.heat th{font-size:13px; padding:6px 8px; text-align:right; white-space:nowrap}
table.heat td:first-child, table.heat th:first-child{text-align:left}
main.content table.md td:first-child code{white-space:nowrap; overflow-wrap:normal}
main.content table.md td code{overflow-wrap:normal; word-break:normal}
.ft{margin:10px 0 4px} .fth{font-weight:650; font-size:14.5px; margin:10px 2px 0}
.k-measured{background:var(--good-weak); color:var(--good)}
.qtag.warn{border-color:var(--warn); color:var(--warn); background:var(--warn-weak)}
.k-measured-design{background:var(--acc-weak); color:var(--acc)}
.k-simulated-camera{background:var(--vio-weak); color:var(--violet)}
.k-method{background:var(--surface-3); color:var(--ink-2)}
.k-computed{background:var(--surface-3); color:var(--ink-2)}
"""


def page(title_, lede, meta, body, toc):
    nav = "".join('<a href="%s"%s>%s</a>' % (fn, ' class="on" aria-current="page"' if k == "marks" else "", lab)
                  for k, fn, lab in PAGES)
    brand = B.marker_svg(D4, 7, quiet=0.6, cls="", label="ArUco DICT_4X4_50 id 7", grid=aruco_dict.marker_grid(D4, 7))
    desc = ("Where to put a readable ID and an orientation mark on printed ArUco stickers so OpenCV does not notice: "
            "%d designs measured over paired hard camera conditions; recommendation and toggleable options."
            % len(R["designs"]))
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%s</title>
<meta name="description" content="%s">
<style>%s%s</style></head>
<body>
<header class="topbar"><div class="in">
<a class="brand" href="index.html">%s<span>ArUco in Fusion</span></a>
<nav aria-label="Reports">%s</nav><span class="sp"></span>
<button id="themebtn" type="button" aria-label="Switch colour theme">Theme: auto</button>
</div></header>
<div class="wrap"><div class="layout">
%s
<main class="content">
<div class="hero"><div class="eyebrow">Proposal · measured with OpenCV %s · %s</div><h1>%s</h1><p class="lede">%s</p><p class="meta">%s</p></div>
%s
<div class="foot">Generated %s by <code>docs/build_aruco_marks_report.py</code> from <code>docs/proposal_aruco_marks.md</code>
and <code>reports/media/aruco/marks/results.json</code> (written by <code>tools/study_aruco_marks.py</code>, %d trials,
%s). Sticker drawings are the experiment's own design files; nothing on this page loads from the network.
<a href="index.html">Back to the ArUco overview</a>.</div>
</main></div></div>
<script>%s</script>
</body></html>""" % (esc(title_), esc(desc), B.CSS, EXTRA_CSS, brand, nav, B.toc_html(toc), esc(R["opencv"]), BUILT,
                     esc(title_), lede, meta, body, BUILT, R.get("trials", 0), esc(R["generated"]), B.JS)


# ================================================================ facts the prose quotes


def facts() -> list:
    """(what, the exact text the Markdown must contain). Recomputed from the JSON."""
    f = []
    for k in ("white_q1_id", "white_q1_black", "white_q1_light", "white_q1_long", "white_q1_big", "white_q1_touch",
              "black_id_white", "black_id_grey", "black_id_faint", "black_id_inner", "black_id_big", "combo_black",
              "combo_black_inner", "combo_white",
              "combo_strip", "tri_tl", "tri_tl_near", "arrow_top", "chamfer_tl", "dot_tl", "dot_tl_big", "strip_q1",
              "white_q15_id", "white_q2_id", "white_q1_corner", "sheet_label"):
        f.append((k + " delta", "`%s` %s" % (k, pts(S(k)["default"]["delta"]))))
    f.append(("stress n", "%d paired" % R["stress"]["n"]))
    if R.get("label_text"):
        for k in LABEL_KEYS[1:]:
            f.append((k + " vs plain", "`%s` %s" % (k, pts(L_(D4, k)["vs_plain"]["delta"]))))
    f.append(("baseline rate", pct(S("base_q1")["default"]["base_rate"])))
    return f


def check_facts(text):
    flat = re.sub(r"\s+", " ", text)
    for what, s in facts():
        if s not in flat:
            warn("doc does not quote %s as %r" % (what, s))


# ================================================================ build


def build() -> str:
    text = SRC.read_text(encoding="utf-8")
    check_facts(text)
    title_, pre, secs = B.split_md(text)
    paras = [p for p in re.split(r"\n\s*\n", "\n".join(pre).strip()) if p.strip()]
    lede = B.inline(" ".join(paras[0].split())) if paras else ""
    meta = B.inline(" ".join(paras[1].split())) if len(paras) > 1 else ""
    hooks = {
        "2": {"lead": stat_tiles(), "after": fig_mechanism() + fig_default()},
        "3": {"after": B.fig(fig_pipeline(), "", "method") + fig_examples()},
        "4": {"after": fig_gallery()},
        "5.1": {"after": fig_forest()},
        "5.2": {"after": fig_dose() + fig_print_cells()},
        "5.3": {"after": fig_gaps()},
        "5.4": {"after": fig_ppm() + fig_stress_ppm() + factor_table()},
        "5.5": {"after": fig_corners()},
        "5.6": {"after": fig_charuco()},
        "5.7": {"after": fig_dicts()},
        "7": {"lead": fig_options()},
        "11.2": {"after": fig_label_gallery() + fig_label_6x6()},
        "11.3": {"after": fig_label_table()},
        "11.4": {"after": fig_label_charuco()},
        "11.5": {"after": fig_capacity()},
    }
    keys = {s["key"] for s in secs}
    for k in hooks:
        if k not in keys:
            warn("hook for section %r but the doc has no such section" % k)
    body, toc = B.render_sections(secs, hooks)
    return page(title_, lede, meta, body, toc)


def main() -> int:
    global R
    R = json.loads((MEDIA / "results.json").read_text(encoding="utf-8"))
    if R.get("quick"):
        warn("results.json is from a --quick run; rerun tools/study_aruco_marks.py without --quick")
    html_ = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html_, encoding="utf-8")
    print("wrote %s (%s)" % (OUT, B.fmt_bytes(OUT.stat().st_size)))
    if WARN:
        print("%d warning(s)" % len(WARN), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
