#!/usr/bin/env python3
# -*- coding: utf-8 -*-
""""ArUco tags in Fusion: how the prototype works" -- one self-contained page.

    python docs/build_aruco_howitworks_report.py
    -> reports/aruco/how-it-works.html

Sits beside the four research pages that docs/build_aruco_reports.py writes
and reuses their CSS, JS and SVG helpers (imported, so the house style has one
source). Unlike that builder it needs no OpenCV: every marker drawn in a
diagram comes from bam_cad_core's own dictionary tables (aruco_dict), which
tools/verify_aruco_output.py checked against cv2.aruco.generateImageMarker
for all 9,596 ids. Stdlib + Pillow.

What is COMPUTED here rather than typed:
  * the two-pass serial walk-through runs markers.plan_serials on the demo's
    occurrence tree and is compared with the ids the demo export recorded;
  * the per-copy resolution figure reads the real export record
    (reports/media/aruco/demo/demo_markers.json);
  * the OpenCV badges under each image are detections.json; the corner quads
    drawn on the URDF renders are the *.frames.json projections;
  * the verification numbers are read from verify_output.json /
    verify_boards.json where those files hold them.
Numbers that live only in other lanes' logs (test counts, evaluator results,
the URDF pose checks) are constants below, each with where it came from.

Screenshots and renders are downscaled with Pillow and inlined as base64, so
the page loads nothing from the network.
"""
from __future__ import annotations

import base64
import html
import io
import json
import sys
from datetime import date
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docs"))
sys.path.insert(0, str(ROOT / "CADExporter"))

import build_aruco_reports as B  # noqa: E402  (house style: CSS, JS, svg helpers)
from bam_cad_core import aruco_board, aruco_dict, markers  # noqa: E402

MEDIA = ROOT / "reports" / "media" / "aruco"
DEMO = MEDIA / "demo"
UI = MEDIA / "ui"
OUT = ROOT / "reports" / "aruco" / "how-it-works.html"
EXPORT_DIR = Path.home() / "CADExporter" / "ArUco_Demo"
WORKTREE = r"%USERPROFILE%\Documents\cad-exporter\.claude\worktrees\aruco-fusion-plugin-0e9bcb"
BUILT = date.today().isoformat()
D4 = "DICT_4X4_50"
COMMITS = ("8fcea9f", "c115463")
WARN: list = []

# Numbers from the lanes that ran them (not re-derivable from files here).
TESTS_NEW = [("test_markers", 55), ("test_aruco_output", 106), ("test_aruco_board", 51)]
TESTS_OLD = [("test_cad_core", 687), ("test_frames", 119), ("test_export_bundle", 127),
             ("test_graph_v2", 397), ("test_dev_bridge", 229)]
EVALS = dict(green=16, skipped=1, red=0, mass_kg=12.46)
FK = dict(pos_m="4.7e-11", rot_rad="0")                  # tools/verify_aruco_urdf.py
POSE = dict(pairs=30, views=7, posed=1, worst_mm=0.295, worst_px=1.8, mm_px=0.167, unread=5)  # verify_aruco_render.py
POSED_READS = [0, 6, 7]                                  # OpenCV on urdf_posed_front.png
POSED_JOINTS = "j1_arm_1 = 0.9, j3_arm_1 = −1.2, j2_arm_2 = 0.6, j4_arm_2 = −0.8 rad"
BOARDS_FUSION = dict(markers=17, corners=12)             # OpenCV on boards_top.png


def warn(m):
    WARN.append(m)
    print("WARN:", m, file=sys.stderr)


esc = B.esc


# ================================================================ images

_BYTES = [0]


def _uri(im: Image.Image, fmt: str, q: int = 84) -> str:
    buf = io.BytesIO()
    if fmt == "JPEG":
        im.convert("RGB").save(buf, "JPEG", quality=q, optimize=True, progressive=True)
        mime = "image/jpeg"
    else:
        im.save(buf, "PNG", optimize=True)
        mime = "image/png"
    _BYTES[0] += buf.tell()
    return "data:%s;base64,%s" % (mime, base64.b64encode(buf.getvalue()).decode("ascii"))


def load(path: Path, crop=None, autocrop=False, pad=28, max_w=1400, fmt="JPEG", q=84):
    """-> (data uri, width, height, (crop x0, y0) in source px)."""
    im = Image.open(path)
    im = im.convert("RGB")
    x0 = y0 = 0
    if crop:
        im = im.crop(crop)
        x0, y0 = crop[0], crop[1]
    if autocrop:
        bg = im.getpixel((2, 2))
        diff = ImageChops.difference(im, Image.new("RGB", im.size, bg)).convert("L")
        bb = diff.point(lambda v: 255 if v > 14 else 0).getbbox()
        w, h = im.size
        if bb:
            box = (max(0, bb[0] - pad), max(0, bb[1] - pad), min(w, bb[2] + pad), min(h, bb[3] + pad))
            im = im.crop(box)
            x0, y0 = x0 + box[0], y0 + box[1]
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
    return _uri(im, fmt, q), im.width, im.height, (x0, y0)


def img(path, alt, crop=None, autocrop=False, max_w=1400, fmt="JPEG", style="", cls=""):
    uri, w, h, _ = load(path, crop, autocrop, max_w=max_w, fmt=fmt)
    st = ' style="%s"' % style if style else ""
    c = ' class="%s"' % cls if cls else ""
    return '<img src="%s" width="%d" height="%d" alt="%s" loading="lazy"%s%s>' % (uri, w, h, esc(alt), st, c)


def reads(ids, label="OpenCV reads", empty="nothing (every tag faces away)"):
    if not ids:
        return '<div class="reads"><span class="rl">%s</span><span class="idb none">%s</span></div>' % (label, empty)
    seen = {}
    for i in ids:
        seen[i] = seen.get(i, 0) + 1
    pills = "".join('<span class="idb%s">%d</span>' % (" dup" if seen[i] > 1 else "", i) for i in ids)
    return '<div class="reads"><span class="rl">%s</span>%s</div>' % (label, pills)


# ================================================================ svg bits

def T(x, y, s, cls="", size=None, anchor="start", weight=None, width=None, lh=1.3):
    return B.svg_text(x, y, s, cls=cls, size=size, anchor=anchor, weight=weight, width=width, lh=lh)


def R(x, y, w, h, cls="box", rx=8, extra=""):
    return '<rect x="%g" y="%g" width="%g" height="%g" rx="%g" class="%s"%s/>' % (x, y, w, h, rx, cls, extra)


def A(x1, y1, x2, y2, pid, cls="edge", mk="ah", dash=False):
    d = ' stroke-dasharray="5 4"' if dash else ""
    return ('<line x1="%g" y1="%g" x2="%g" y2="%g" class="%s"%s marker-end="url(#%s-%s)"/>'
            % (x1, y1, x2, y2, cls, d, pid, mk))


def P(d, cls="edge", pid=None, mk=None, dash=False):
    m = ' marker-end="url(#%s-%s)"' % (pid, mk) if mk else ""
    ds = ' stroke-dasharray="5 4"' if dash else ""
    return '<path d="%s" class="%s"%s%s/>' % (d, cls, ds, m)


def grid(mid, d=D4):
    return aruco_dict.marker_grid(d, int(mid))


def MK(x, y, size, mid, quiet=1.0, d=D4, fg="#000", bg="#fff", outline=True, dashed=False):
    o = B.g_bits(grid(mid, d), x, y, size, quiet, bg=bg, fg=fg)
    if outline:
        o += '<rect x="%g" y="%g" width="%g" height="%g" fill="none" class="mko%s"/>' % (
            x, y, size, size, " dsh" if dashed else "")
    return o


def bulb(x, y, on):
    if on:
        return ('<g><circle cx="%g" cy="%g" r="7" class="bulb-on"/><rect x="%g" y="%g" width="7" height="4" rx="1" '
                'class="bulb-base"/></g>' % (x, y, x - 3.5, y + 6.5))
    return ('<g><circle cx="%g" cy="%g" r="7" class="bulb-off"/><rect x="%g" y="%g" width="7" height="4" rx="1" '
            'class="bulb-base"/><line x1="%g" y1="%g" x2="%g" y2="%g" class="slash"/></g>'
            % (x, y, x - 3.5, y + 6.5, x - 8, y + 9, x + 8, y - 8))


def svg(inner, w, h, label):
    return B.svg(inner, w, h, label)


# ================================================================ data

def load_record():
    rec = json.loads((DEMO / "demo_markers.json").read_text(encoding="utf-8"))
    rows = {}
    for inst in rec["instances"]:
        parts = markers.split_path(inst["path"])
        arm, act = parts[0], parts[1]
        layers = []
        for s in inst["source"]:
            if s["level"] == "model":
                layers.append(("model", inst["modelled"]["marker_id"]))
            else:
                layers.append(("root" if s["owner"] == "" else s["owner"], int(s["values"]["marker_id"])))
        rows[(arm, act)] = dict(id=inst["spec"]["marker_id"], layers=layers, hidden=inst["hidden"])
    overrides = {}
    for inst in rec["instances"]:
        for s in inst["source"]:
            if s["level"] != "model":
                overrides[(s["owner"], s["occurrence"], s["key"])] = int(s["values"]["marker_id"])
    return rec, rows, overrides


def demo_tree():
    tag = markers.TagDef(key="front", component="Tag", spec=markers.TagSpec())
    occs = [markers.OccurrenceRef("Base:1", "Base")]
    for a in (1, 2):
        occs.append(markers.OccurrenceRef("Arm:%d" % a, "Arm"))
        for k in range(2, 8):
            occs.append(markers.OccurrenceRef("Arm:%d+Actuator:%d" % (a, k), "Actuator"))
            occs.append(markers.OccurrenceRef("Arm:%d+Actuator:%d+Tag:2" % (a, k), "Tag"))
    return [tag], occs


def serial_passes():
    """Run plan_serials on the demo tree; split the plan into its passes."""
    tags, occs = demo_tree()
    p1, res1 = markers.plan_serials(tags, [], occs, "", max_rounds=1)
    full, res2 = markers.plan_serials(tags, [], occs, "")
    k1 = {(p.owner, p.occurrence, p.key) for p in p1}
    p2 = [p for p in full if (p.owner, p.occurrence, p.key) not in k1]
    res0 = markers.resolve(tags, [], occs, "")

    def ids(res):
        out = {}
        for i in res.instances:
            parts = markers.split_path(i.path)
            out[(parts[0], parts[1])] = i.spec.marker_id
        return out

    return dict(p1=p1, p2=p2, s0=ids(res0), s1=ids(res1), s2=ids(res2),
                d0=len(res0.duplicates()), d1=len(res1.duplicates()), d2=len(res2.duplicates()))


def board_block_example():
    """Two copies of a 3x2 grid board (ids 1-6) beside a single tag #0."""
    tags = [markers.TagDef(key="top", component="Tag", spec=markers.TagSpec(marker_id=0)),
            markers.TagDef(key="board", component="Plate",
                           spec=markers.TagSpec(marker_id=1, layout="grid", rows=2, cols=3,
                                                separation_m=0.006))]
    occs = [markers.OccurrenceRef("Tag:1", "Tag"), markers.OccurrenceRef("Plate:1", "Plate"),
            markers.OccurrenceRef("Plate:2", "Plate")]
    planned, res = markers.plan_serials(tags, [], occs, "")
    got = {i.path: i.spec.ids for i in res.instances}
    return planned, got


# ================================================================ figures

def fig_anatomy(rec_spec) -> str:
    pid = "an"
    W, H = 1000, 430
    o = [B.arrow_defs(pid)]
    o.append(T(20, 22, "The demo's “Tag” part, as Fusion's timeline holds it", "t-b", 14))
    items = [("Sketch", "tag outline", "40 × 40 mm rectangle", "box2"),
             ("Extrude", "tag tile", "3 mm, new body", "box2"),
             ("Base feature", "ArUco front cells", "9 boxes, TBM union", "b-acc"),
             ("Combine", "cut, keep tools", "tile − cells", "b-acc"),
             ("Sketch", "mount hole", "drawn after the tag", "box2"),
             ("Extrude", "cut through", "a later feature", "box2")]
    x0, y0, w, h, g = 20, 66, 146, 60, 16
    for i, (k, n, s, c) in enumerate(items):
        x = x0 + i * (w + g)
        o.append(R(x, y0, w, h, c, 8))
        o.append(T(x + 10, y0 + 18, k, "t-mut", 11.5))
        o.append(T(x + 10, y0 + 36, n, "t-b", 13))
        o.append(T(x + 10, y0 + 52, s, "t-mut", 11.5))
        if i:
            o.append(A(x - g + 1, y0 + h / 2, x - 2, y0 + h / 2, pid))
    gx1, gx2 = x0 + 2 * (w + g), x0 + 3 * (w + g) + w
    o.append(P("M%g %g V%g H%g V%g" % (gx1, y0 - 4, y0 - 12, gx2, y0 - 4), "edge e-acc"))
    o.append(T((gx1 + gx2) / 2, y0 - 18, "timeline group “ArUco front: DICT_4X4_50 #0 · 30.0 mm”", "t-acc", 12,
               "middle"))
    # bodies
    by, s = 160, 128
    g0 = grid(0)
    o.append(B.g_bits(g0, 20, by, s, 1, bg="#fff", fg="#c9c6bd"))
    o.append('<rect x="20" y="%g" width="%g" height="%g" fill="none" class="mko"/>' % (by, s, s))
    o.append(T(20 + s + 14, by + s / 2 + 9, "+", "t-b", 26))
    xb = 20 + s + 44
    o.append(B.g_bits(g0, xb, by, s, 1, bg="#e4e1d8", fg="#111"))
    o.append('<rect x="%g" y="%g" width="%g" height="%g" fill="none" class="mko dsh"/>' % (xb, by, s, s))
    o.append(T(xb + s + 12, by + s / 2 + 9, "=", "t-b", 26))
    xc = xb + s + 44
    o.append(MK(xc, by, s, 0))
    o.append('<circle cx="%g" cy="%g" r="%g" class="hole"/>' % (xc + s - 8, by + 8, 3.9))
    for x, l1, l2 in ((20, "“tag tile” body · White", "pockets 0.6 mm deep"),
                      (xb, "“aruco cells front” · Black", "one solid, kept as a tool"),
                      (xc, "the printed part", "+ the mount hole, after the tag")):
        o.append(T(x, by + s + 20, l1, "t-b", 12.5))
        o.append(T(x, by + s + 36, l2, "t-mut", 12))
    o.append(T(20, 362, "Changing the id later swaps the base feature's body (updateBody on its own source body): "
                        "the combine and every later feature, the hole included, recompute on the new pattern. "
                        "The add-in never edits the tile by hand.", "t-ink2", 12.5, width=72))
    # record
    rx, ry, rw, rh = 548, 150, 436, 266
    o.append(R(rx, ry, rw, rh, "b-vio", 10))
    o.append(T(rx + 14, ry + 22, "component “Tag” · attribute bam_aruco / tags", "t-vio t-b", 12.5))
    sp = rec_spec
    lines = ['[{"key": "front",',
             ' "spec": {"dictionary": "%s",' % sp["dictionary"],
             '          "marker_id": %d, "layout": "%s",' % (sp["marker_id"], sp["layout"]),
             '          "marker_length_m": %g,' % sp["marker_length_m"],
             '          "quiet_modules": %g, "depth_m": %g},' % (sp["quiet_modules"], sp["depth_m"]),
             ' "origin": […], "x_axis": […],',
             ' "y_axis": […], "z_axis": […], "units": "cm",',
             ' "fusion": {"base_feature", "combine", "face",',
             '            "tile_body", "cells_body",',
             '            "group", "rotate_deg"}}]']
    for k, ln in enumerate(lines):
        o.append('<text x="%g" y="%g" class="t-mono pre" font-size="11.5">%s</text>' % (rx + 14, ry + 48 + k * 17, esc(ln)))
    o.append(T(rx + 14, ry + 226, "The frame: centre of the black square on the top face, +x image right, +y image up, "
                                  "+z out of the face. Tokens find the features again on the next edit.",
               "t-mut", 11.5, width=66))
    return svg("".join(o), W, H, "Anatomy of a modelled ArUco tag part: timeline, two bodies, record")


def fig_tree(rows, overrides) -> str:
    pid = "tr"
    W, H = 1000, 556
    o = [B.arrow_defs(pid)]

    def box(x, y, w, title, sub, items, panel=None):
        rh = 22
        hh = 42 + len(items) * rh + (len(panel[1]) * 15 + 30 if panel else 0) + 6
        q = [R(x, y, w, hh, "box", 10), T(x + 12, y + 20, title, "t-b", 13.5), T(x + 12, y + 35, sub, "t-mut", 11.5)]
        yy = y + 44
        for text, chip, cls in items:
            q.append(T(x + 22, yy + 14, text, "t-mono", 12))
            if chip:
                cw = len(chip) * 6.9 + 14
                q.append(R(x + w - cw - 10, yy + 1, cw, 19, cls, 9))
                q.append(T(x + w - cw / 2 - 10, yy + 14.5, chip, "t-mono", 11, "middle"))
            yy += rh
        if panel:
            ph = len(panel[1]) * 15 + 24
            q.append(R(x + 12, yy + 2, w - 24, ph, "b-vio", 8))
            q.append(T(x + 22, yy + 18, panel[0], "t-vio t-b", 11.5))
            for k, ln in enumerate(panel[1]):
                q.append(T(x + 22, yy + 34 + k * 15, ln, "t-mono", 11))
        return "".join(q), hh

    root_lines = ["%s → %d" % (k, v) for (own, occ, k), v in sorted(overrides.items(),
                                                                    key=lambda t: t[1]) if own == ""]
    arm_ov = {occ: v for (own, occ, k), v in overrides.items() if own == "Arm"}
    x, y, w = 14, 12, 352
    s1, h1 = box(x, y, w, "Robot · the root design", "what this robot says about its copies",
                 [("Base:1", None, ""), ("Arm:1", None, ""), ("Arm:2", "bam_aruco/overrides", "b-vio")],
                 ("on occurrence Arm:2 (root level):", root_lines))
    o.append(s1)
    y += h1 + 12
    items = [("Actuator:%d" % k, ("Tag:2|front → %d" % arm_ov[("Actuator:%d" % k)]) if "Actuator:%d" % k in arm_ov
              else None, "b-acc") for k in range(2, 8)]
    s2, h2 = box(x, y, w, "component Arm", "ONE definition; Arm:1 and Arm:2 are copies of it", items)
    o.append(s2)
    y += h2 + 12
    s3, h3 = box(x, y, w, "component Actuator → component Tag", "the tag is modelled once, in Tag",
                 [("Actuator ⊃ Tag:2", None, ""), ("Tag: bam_aruco/tags", "front = 4X4_50 #0", "box3")])
    o.append(s3)
    if y + h3 > H - 6:
        warn("tree figure: left column overflows (%d)" % (y + h3))
    # resolution grid
    gx, cw, ch = 470, 86, 140
    o.append(T(400, 22, "What each copy prints · model → Arm → root, the last opinion wins", "t-b", 13.5))
    for k in range(2, 8):
        o.append(T(gx + (k - 2) * cw + cw / 2 - 2, 50, "Actuator:%d" % k, "t-mut t-mono", 11, "middle"))
    for r, arm in enumerate(("Arm:1", "Arm:2")):
        cy = 60 + r * (ch + 10)
        o.append(T(402, cy + ch / 2 + 4, arm, "t-b t-mono", 13))
        for k in range(2, 8):
            cx = gx + (k - 2) * cw
            cell = rows[(arm, "Actuator:%d" % k)]
            o.append(R(cx + 2, cy, cw - 6, ch, "box", 8))
            o.append(MK(cx + 17, cy + 8, 50, cell["id"]))
            o.append(T(cx + cw / 2 - 1, cy + 80, "#%d" % cell["id"], "t-b", 17, "middle"))
            n = len(cell["layers"])
            for j, (lvl, v) in enumerate(cell["layers"]):
                win = j == n - 1
                cls = {"model": "t-mut", "root": "t-vio"}.get(lvl, "t-acc")
                txt = "%s #%d" % (lvl, v)
                extra = "" if win else ' text-decoration="line-through"'
                o.append('<text x="%g" y="%g" text-anchor="middle" font-size="11" class="%s%s"%s>%s</text>'
                         % (cx + cw / 2 - 1, cy + 98 + j * 14, cls if win else "t-mut", " t-b" if win else "",
                            extra, esc(txt)))
    # worked example
    ey = 378
    o.append(T(400, ey, "One copy, step by step: Arm:2 › Actuator:3", "t-b", 13))
    cell = rows[("Arm:2", "Actuator:3")]
    bx = 400
    names = {"model": "the model encodes", "Arm": "written in Arm", "root": "written at the root"}
    for j, (lvl, v) in enumerate(cell["layers"]):
        cls = {"model": "box2", "root": "b-vio"}.get(lvl, "b-acc")
        o.append(R(bx, ey + 14, 128, 52, cls, 8))
        o.append(T(bx + 12, ey + 34, names.get(lvl, lvl), "t-mut", 11.5))
        o.append(T(bx + 12, ey + 55, "#%d" % v, "t-b", 16))
        if j < len(cell["layers"]) - 1:
            o.append(A(bx + 130, ey + 40, bx + 148, ey + 40, pid))
        bx += 152
    o.append(A(bx - 22, ey + 40, bx - 2, ey + 40, pid, "edge e-good", "ahg"))
    o.append(MK(bx + 2, ey + 12, 56, cell["id"]))
    o.append(T(bx + 66, ey + 36, "prints", "t-mut", 11.5))
    o.append(T(bx + 66, ey + 56, "#%d" % cell["id"], "t-good t-b", 17))
    o.append(T(400, ey + 92, "Innermost first, outermost last: the robot can re-serial an arm that already serialised "
                             "its actuators, never the other way round.", "t-ink2", 12, width=96))
    return svg("".join(o), W, H, "Override records in the demo tree and the id each copy resolves to")


def fig_variant() -> str:
    pid = "va"
    W, H = 1000, 676
    o = [B.arrow_defs(pid)]
    o.append(T(20, 24, "1 · Geometry lives once per component", "t-b", 14))
    o.append(R(20, 40, 170, 116, "box", 10))
    o.append(MK(34, 56, 68, 0))
    o.append(T(112, 72, "component", "t-mut", 11.5))
    o.append(T(112, 88, "Tag", "t-b", 14))
    o.append(T(112, 106, "pattern #0,", "t-mut", 11.5))
    o.append(T(112, 120, "stored once", "t-mut", 11.5))
    o.append(T(34, 146, "one body set, every copy", "t-mut", 11.5))
    for r in range(2):
        for c in range(6):
            x, y = 262 + c * 40, 52 + r * 50
            o.append(MK(x, y, 30, 0))
    o.append(A(192, 98, 254, 98, pid))
    o.append(T(262, 150, "12 occurrences in the robot → 12 × #0 in plastic", "t-mut", 11.5))
    o.append(T(540, 66, "A camera cannot tell twelve #0s apart, and Fusion cannot give one copy different bodies.",
               "t-ink2", 12.5, width=60))
    o.append(T(540, 108, "So an id that differs per copy becomes its own small part: a variant. The demo ends with "
                         "11 of them: 5 written once in Arm (#1–#5), 6 at the root (#6–#11).", "t-ink2", 12.5, width=60))
    # construction
    y2 = 196
    o.append(T(20, y2, "2 · Building one variant (#7 for Arm:2 › Actuator:3), all with the TemporaryBRepManager", "t-b", 14))
    sz, gap = 96, 202
    ty = y2 + 22
    steps = [("copy of the template's", "tile body (pockets of #0)"),
             ("∪ the pattern square:", "old pockets filled"),
             ("− the cells of #7:", "new pockets"),
             ("+ a cells body of #7", "(the black body)"),
             ("= part “Tag · 4X4_50 #7”", "base feature, 2 bodies")]
    g0, g7 = grid(0), grid(7)
    for i, (l1, l2) in enumerate(steps):
        x = 20 + i * gap
        if i == 0:
            o.append(B.g_bits(g0, x, ty, sz, 1, bg="#fff", fg="#c9c6bd"))
        elif i == 1:
            o.append('<rect x="%g" y="%g" width="%g" height="%g" fill="#fff"/>' % (x, ty, sz, sz))
            m = sz / 8
            o.append('<rect x="%g" y="%g" width="%g" height="%g" fill="none" class="mko dsh"/>'
                     % (x + m, ty + m, sz - 2 * m, sz - 2 * m))
        elif i == 2:
            o.append(B.g_bits(g7, x, ty, sz, 1, bg="#fff", fg="#c9c6bd"))
        elif i == 3:
            o.append(B.g_bits(g7, x, ty, sz, 1, bg="#e4e1d8", fg="#111"))
        else:
            o.append(B.g_bits(g7, x, ty, sz, 1))
            o.append('<circle cx="%g" cy="%g" r="3" class="hole"/>' % (x + sz - 6, ty + 6))
        o.append('<rect x="%g" y="%g" width="%g" height="%g" fill="none" class="mko%s"/>'
                 % (x, ty, sz, sz, " dsh" if i == 3 else ""))
        o.append(T(x, ty + sz + 18, l1, "t-b" if i == 4 else "t-ink2", 12))
        o.append(T(x, ty + sz + 33, l2, "t-mut", 11.5))
        if i < 4:
            o.append(A(x + sz + 10, ty + sz / 2, x + gap - 12, ty + sz / 2, pid))
    o.append(T(20, ty + sz + 58, "A dumb copy: the hole survives because it was in the tile body; the variant has no "
                                 "timeline of its own.", "t-mut", 12))
    # placement and light bulbs
    y3 = 418
    o.append(T(20, y3, "3 · Where it goes, and what is switched off (per path)", "t-b", 14))
    py = y3 + 14
    o.append(R(20, py, 452, 170, "b-acc", 10))
    o.append(T(34, py + 22, "inside component Arm: written once, both arms see it", "t-acc t-b", 12.5))
    rows_l = [(0, "Actuator:3 › Tag:2  (template)", "hidden for the path Actuator:3+Tag:2", False),
              (1, "Tag · 4X4_50 #1  (variant)", "rigid as-built joint to Actuator:3", True)]
    for k, (mid, a, b, on) in enumerate(rows_l):
        yy = py + 38 + k * 62
        o.append(MK(34, yy, 40, mid))
        o.append(T(86, yy + 16, a, "t-mono", 12))
        o.append(T(86, yy + 34, b, "t-mut", 11.5))
        o.append(bulb(446, yy + 18, on))
    o.append(R(492, py, 492, 210, "b-vio", 10))
    o.append(T(506, py + 22, "in the root design: each path decides for itself", "t-vio t-b", 12.5))
    rows_r = [(1, "Arm:1 › Tag · 4X4_50 #1", "Arm's answer stands: this copy prints #1", True),
              (0, "Arm:2 › Actuator:3 › Tag:2  (template)", "hidden for this path", False),
              (1, "Arm:2 › Tag · 4X4_50 #1  (Arm's variant)", "a deeper realisation: hidden for this path only", False),
              (7, "Tag · 4X4_50 #7  (variant, in the root)", "rigid joint to Arm:2 › Actuator:3: prints #7", True)]
    for k, (mid, a, b, on) in enumerate(rows_r):
        yy = py + 34 + k * 43
        o.append(MK(506, yy, 32, mid))
        o.append(T(548, yy + 13, a, "t-mono", 11.5))
        o.append(T(548, yy + 29, b, "t-mut", 11))
        o.append(bulb(958, yy + 15, on))
    o.append(bulb(32, py + 196, True))
    o.append(T(46, py + 200, "on", "t-mut", 11.5))
    o.append(bulb(84, py + 196, False))
    o.append(T(98, py + 200, "off for that path only", "t-mut", 11.5))
    o.append(T(20, py + 228, "Probe-verified in Fusion: light bulbs set per occurrence path are independent, even across "
                             "two copies of the same arm, so hiding Arm:2's view of a tag leaves Arm:1's alone.",
               "t-ink2", 12, width=150))
    return svg("".join(o), W, H, "Why each differing copy gets a variant tag part, how it is built, and what is hidden")


def fig_section() -> str:
    pid = "gs"
    W, H = 1000, 340
    o = [B.arrow_defs(pid)]
    s = 200       # 40 mm tile -> 5 px/mm
    x0, y0 = 30, 50
    g0 = grid(0)
    o.append(T(x0, 24, "Top view, DICT_4X4_50 #0 at 30 mm", "t-b", 13.5))
    o.append(MK(x0, y0, s, 0))
    ppm = s / 40.0
    row = 2
    ya = y0 + (5 + row * 5 + 2.5) * ppm
    o.append('<line x1="%g" y1="%g" x2="%g" y2="%g" class="secline"/>' % (x0 - 12, ya, x0 + s + 12, ya))
    o.append(T(x0 - 22, ya + 4, "A", "t-bad t-b", 12))
    o.append(T(x0 + s + 16, ya + 4, "A", "t-bad t-b", 12))
    # dims
    yd = y0 + s + 18
    o.append(P("M%g %g H%g" % (x0 + 5 * ppm, yd, x0 + 35 * ppm), "edge"))
    for xx in (x0 + 5 * ppm, x0 + 35 * ppm):
        o.append('<line x1="%g" y1="%g" x2="%g" y2="%g" class="edge"/>' % (xx, yd - 5, xx, yd + 5))
    o.append(T(x0, yd + 18, "L = 30 mm (black square incl. border)", "t-ink2", 11.5))
    o.append(T(x0, yd + 34, "module 5 mm · quiet zone 1 module", "t-mut", 11.5))
    o.append(T(x0, yd + 50, "tile 40 mm", "t-mut", 11.5))
    # section
    sx, sy, k = 300, 100, 15.0   # 15 px/mm, true scale
    o.append(T(sx, 24, "Section A–A through module row 2, true scale (15 px per mm)", "t-b", 13.5))
    tw, th, dep = 40 * k, 3 * k, 0.6 * k
    o.append('<rect x="%g" y="%g" width="%g" height="%g" class="tilebody"/>' % (sx, sy, tw, th))
    cells = g0[row]
    for c, v in enumerate(cells):
        if v:
            o.append('<rect x="%g" y="%g" width="%g" height="%g" fill="#111"/>' % (sx + (5 + c * 5) * k, sy, 5 * k, dep))
    o.append('<line x1="%g" y1="%g" x2="%g" y2="%g" class="edge e-mut"/>' % (sx - 20, sy, sx + tw + 20, sy))
    # labels
    o.append(A(sx + tw + 50, sy - 30, sx + tw + 50, sy - 6, pid, "edge e-acc"))
    o.append(T(sx + tw + 50, sy - 36, "+z, to the camera", "t-acc", 11.5, "middle"))
    o.append(P("M%g %g H%g" % (sx + tw + 8, sy, sx + tw + 22), "edge"))
    o.append(P("M%g %g H%g" % (sx + tw + 8, sy + dep, sx + tw + 22), "edge"))
    o.append(T(sx + tw + 26, sy + 9, "0.6 mm inlay", "t-ink2", 11.5))
    o.append(P("M%g %g V%g" % (sx - 14, sy, sy + th), "edge"))
    o.append(T(sx - 18, sy + th / 2 + 4, "3 mm", "t-ink2", 11.5, "end"))
    o.append(P("M%g %g H%g" % (sx, sy + th + 16, sx + 5 * k), "edge"))
    o.append(T(sx + 2.5 * k, sy + th + 32, "quiet 5 mm", "t-mut", 11, "middle"))
    o.append(P("M%g %g H%g" % (sx + 5 * k, sy + th + 16, sx + 10 * k), "edge e-acc"))
    o.append(T(sx + 7.5 * k, sy + th + 32, "module", "t-acc", 11, "middle"))
    notes = ["Black cells: their own body, flush with the top face (z from −depth to 0), so the surface prints flat.",
             "0.6 mm = three 0.2 mm layers: the shallowest inlay a 0.4 mm nozzle lays down as a solid colour.",
             "Tile thickness is the ArUco Extrude's (3 mm here); on a face you modelled, it is your body."]
    for i, n in enumerate(notes):
        o.append(T(sx, sy + th + 70 + i * 22, "· " + n, "t-ink2", 12))
    return svg("".join(o), W, H, "Tag geometry: top view with dimensions and a true-scale section")


def corner_contacts(g):
    n = len(g)
    out = []
    for r in range(n - 1):
        for c in range(n - 1):
            a, b, cc, d = g[r][c], g[r][c + 1], g[r + 1][c], g[r + 1][c + 1]
            if a and d and not b and not cc:
                out.append((r + 1, c + 1))
            if b and cc and not a and not d:
                out.append((r + 1, c + 1))
    return out


def fig_growth() -> str:
    W, H = 1000, 250
    g0 = grid(0)
    contacts = corner_contacts(g0)
    boxes = markers.cell_boxes(markers.TagSpec(marker_id=0))
    o = []
    x0, y0, s = 24, 34, 168
    o.append(T(x0, 22, "#0: %d corner-only contacts between black cells" % len(contacts), "t-b", 13))
    o.append(MK(x0, y0, s, 0))
    m = s / 8.0
    for (r, c) in contacts:
        o.append('<circle cx="%g" cy="%g" r="9" class="hot"/>' % (x0 + (c + 1) * m, y0 + (r + 1) * m))
    # zoom: before / after
    zx, zy, z = 400, 50, 70
    for k, (title, grow) in enumerate((("as modelled: they meet at a point", 0), ("grown 10 µm each (drawn ×600)", 6))):
        bx = zx + k * 220
        o.append(T(bx, 34, title, "t-b" if k else "t-ink2", 12.5))
        o.append('<rect x="%g" y="%g" width="%g" height="%g" fill="#fff"/>' % (bx, zy, 2 * z, 2 * z))
        o.append('<rect x="%g" y="%g" width="%g" height="%g" fill="#111"/>' % (bx - grow, zy - grow, z + 2 * grow, z + 2 * grow))
        o.append('<rect x="%g" y="%g" width="%g" height="%g" fill="#111"/>' % (bx + z - grow, zy + z - grow, z + 2 * grow, z + 2 * grow))
        o.append('<rect x="%g" y="%g" width="%g" height="%g" fill="none" class="mko"/>' % (bx, zy, 2 * z, 2 * z))
        if grow:
            o.append('<rect x="%g" y="%g" width="%g" height="%g" class="ovl"/>' % (bx + z - grow, zy + z - grow, 2 * grow, 2 * grow))
        else:
            o.append('<circle cx="%g" cy="%g" r="6" class="hot"/>' % (bx + z, zy + z))
    o.append(T(zx, zy + 2 * z + 22, "a point or edge contact is non-manifold: slicers choke on it", "t-mut", 11.5, width=33))
    o.append(T(zx + 220, zy + 2 * z + 22, "the boxes share area, so the union is one solid", "t-good", 11.5, width=30))
    o.append(T(820, 60, "cell_boxes(#0) → %d boxes" % len(boxes), "t-mono", 12))
    o.append(T(820, 82, "union in Fusion → 1 lump,", "t-mono", 12))
    o.append(T(820, 100, "isSolid (aruco_probe)", "t-mono", 12))
    o.append(T(820, 130, "Growth is clamped to the black square, so the marker side stays exactly L. "
                         "10 µm is two orders of magnitude below what a printer resolves.", "t-mut", 11.5, width=26))
    return svg("".join(o), W, H, "Why black cells are grown by 10 micrometres")


def _board_svg_rects(spec, ox, oy, scale, quiet_modules=1.0):
    bs = markers.board_spec(spec)
    w, h = spec.extent_m
    q = quiet_modules * spec.module_m
    o = ['<rect x="%g" y="%g" width="%g" height="%g" fill="#fff" class="mko"/>'
         % (ox, oy, (w + 2 * q) * scale, (h + 2 * q) * scale)]
    rects = []
    for (x0, y0, x1, y1) in aruco_board.black_rects_m(bs):
        rects.append('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f"/>'
                     % (ox + (q + x0 + w / 2) * scale, oy + (q + h / 2 - y1) * scale, (x1 - x0) * scale, (y1 - y0) * scale))
    o.append('<g fill="#000" shape-rendering="crispEdges">%s</g>' % "".join(rects))
    return "".join(o), (w + 2 * q) * scale, (h + 2 * q) * scale, q * scale


def axes(x, y, pid, down=True, into=True, L=46):
    o = [A(x, y, x + L, y, pid, "edge e-bad thick", "ahb"), T(x + L + 4, y + 4, "+x", "t-bad t-b halo", 12)]
    if down:
        o += [A(x, y, x, y + L, pid, "edge e-good thick", "ahg"), T(x + 5, y + L + 12, "+y", "t-good t-b halo", 12)]
    else:
        o += [A(x, y, x, y - L, pid, "edge e-good thick", "ahg"), T(x + 5, y - L - 2, "+y", "t-good t-b halo", 12)]
    if into:
        o += ['<circle cx="%g" cy="%g" r="7" class="zax"/>' % (x, y),
              '<path d="M%g %g L%g %g M%g %g L%g %g" class="edge e-acc"/>' % (x - 4, y - 4, x + 4, y + 4, x - 4, y + 4, x + 4, y - 4)]
    else:
        o += ['<circle cx="%g" cy="%g" r="7" class="zax"/>' % (x, y), '<circle cx="%g" cy="%g" r="2.2" class="zdot"/>' % (x, y)]
    return "".join(o)


def fig_boards() -> str:
    pid = "bo"
    W, H = 1000, 370
    o = [B.arrow_defs(pid)]
    single = markers.TagSpec(marker_id=0, marker_length_m=0.03)
    gridb = markers.fit_board(0.10, 0.07, markers.TagSpec(marker_id=1, layout="grid", rows=2, cols=3))
    ch = markers.fit_board(0.10, 0.08, markers.TagSpec(marker_id=7, layout="charuco", rows=4, cols=5))
    sc = 2400.0   # px per metre
    # single
    x, y = 30, 70
    s, w1, h1, q = _board_svg_rects(single, x, y, sc)
    o.append(s)
    o.append(axes(x + w1 / 2, y + h1 / 2, pid, down=False, into=False, L=40))
    o.append(T(x, 40, "single #0", "t-b", 13))
    o.append(T(x, 56, "frame at the centre, +y up, +z out", "t-mut", 11.5))
    # grid
    x = 230
    s, w2, h2, q = _board_svg_rects(gridb, x, y, sc)
    o.append(s)
    o.append(axes(x + q, y + q, pid, down=True, into=True, L=40))
    o.append(T(x, 40, "grid 3×2 #%d–%d" % (gridb.ids[0], gridb.ids[-1]), "t-b", 13))
    o.append(T(x, 56, "gap = 0.2 · L (fit to a 100 × 70 mm face)", "t-mut", 11.5))
    # charuco
    x = 560
    s, w3, h3, q = _board_svg_rects(ch, x, y, sc)
    o.append(s)
    o.append(axes(x + q, y + q, pid, down=True, into=True, L=40))
    o.append(T(x, 40, "ChArUco 5×4 #%d–%d" % (ch.ids[0], ch.ids[-1]), "t-b", 13))
    o.append(T(x, 56, "marker = 0.75 · square (fit to 100 × 80 mm)", "t-mut", 11.5))
    yb = y + max(h1, h2, h3) + 30
    o.append(T(30, yb, "Boards use the frame OpenCV reports, measured against cv2 5.0 (aruco_board.opencv_frame): origin "
                       "at the board's top-left OUTER corner, +x right, +y down the image, +z into the board. A single "
                       "marker's frame is its centre with +y up and +z out. The URDF marker frame is exactly this "
                       "detector frame, so a pose from solvePnP drops straight onto the link.", "t-ink2", 12, width=150))
    o.append('<circle cx="%g" cy="%g" r="7" class="zax"/><circle cx="%g" cy="%g" r="2.2" class="zdot"/>'
             % (880, 50, 880, 50) + T(892, 54, "+z out", "t-mut", 11.5))
    o.append('<circle cx="%g" cy="%g" r="7" class="zax"/>' % (880, 76) +
             '<path d="M876 72 L884 80 M876 80 L884 72" class="edge e-acc"/>' + T(892, 80, "+z into", "t-mut", 11.5))
    if yb + 60 > H:
        warn("boards figure overflows")
    return svg("".join(o), W, H, "Single marker, grid board and ChArUco board with the frames the URDF records")


def fig_serials(sp) -> str:
    pid = "se"
    W, H = 1000, 330
    o = [B.arrow_defs(pid)]
    states = [("start: every copy is the model's #0", sp["s0"], sp["d0"]),
              ("after pass 1", sp["s1"], sp["d1"]),
              ("after pass 2", sp["s2"], sp["d2"])]
    cw, gx = 40, [20, 360, 700]
    for i, (title, ids, nd) in enumerate(states):
        x = gx[i]
        o.append(T(x, 22, title, "t-b", 13))
        o.append(T(x, 38, "%d duplicated pattern%s" % (nd, "" if nd == 1 else "s") if nd else "no duplicates",
                   "t-bad" if nd else "t-good", 11.5))
        counts = {}
        for v in ids.values():
            counts[v] = counts.get(v, 0) + 1
        for r, arm in enumerate(("Arm:1", "Arm:2")):
            yy = 56 + r * 64
            o.append(T(x, yy + 22, arm, "t-mono t-mut", 11))
            for k in range(2, 8):
                v = ids[(arm, "Actuator:%d" % k)]
                xx = x + 46 + (k - 2) * cw
                o.append(MK(xx, yy, 30, v))
                if counts[v] > 1:
                    o.append('<rect x="%g" y="%g" width="34" height="34" rx="3" class="dupring"/>' % (xx - 2, yy - 2))
                o.append(T(xx + 15, yy + 46, "#%d" % v, "t-mono", 11, "middle"))
        if i < 2:
            o.append(A(x + 300, 110, gx[i + 1] - 14, 110, pid))

    def plan_text(x, y, plist, head):
        o2 = [T(x, y, head, "t-b", 12.5)]
        for j, p in enumerate(plist):
            where = "in %s" % p.owner if p.owner else "at the root"
            o2.append(T(x, y + 18 + j * 15, "%s on %s [%s] = %d" % (where, p.occurrence, p.key, p.marker_id),
                        "t-mono", 10.5))
        return "".join(o2)

    o.append(plan_text(20, 200, sp["p1"], "pass 1 writes %d override%s" % (len(sp["p1"]), "s" if len(sp["p1"]) != 1 else "")))
    o.append(plan_text(500, 200, sp["p2"], "pass 2 writes %d override%s" % (len(sp["p2"]), "s" if len(sp["p2"]) != 1 else "")))
    return svg("".join(o), W, H, "Serial numbering of the demo's twelve tags, two passes, computed by plan_serials")


def fig_pipeline(files) -> str:
    pid = "pp"
    W, H = 1000, 400
    o = [B.arrow_defs(pid)]

    def box(x, y, w, h, title, lines, cls="box"):
        q = [R(x, y, w, h, cls, 10), T(x + 12, y + 22, title, "t-b", 13)]
        for k, ln in enumerate(lines):
            q.append(T(x + 12, y + 42 + k * 16, ln, "t-mono" if ln.startswith(("payload", "_", "aruco_", "markers.",
                                                                              "cd.", "<")) else "t-mut", 11.5))
        return "".join(q)

    y1, h1 = 40, 118
    o.append(T(10, 24, "The exporter path (this branch)", "t-b", 13))
    o.append(box(10, y1, 180, h1, "Fusion design", ["bam_aruco/tags on", "components; overrides", "on native occurrences;", "variant tag parts"], "b-acc"))
    o.append(box(210, y1, 180, h1, "bam_fusion_export", ['payload["aruco"]', "_collect_aruco, a new", "hook, guarded: a failure", "is a warning"]))
    o.append(box(410, y1, 180, h1, "CadDescription", ["cd.aruco =", "markers.resolve: spec", "per copy + world pose,", "duplicates warned"]))
    o.append(box(610, y1, 180, h1, "LinkTree", ["_add_tag_frames: one", "fixed, massless", "aruco_<id>_target link", "at OpenCV's frame"]))
    o.append(box(810, y1, 180, h1, "URDF writer", ["untitled.urdf", "%s links, %s joints" % (files.get("links", "23"), files.get("joints", "22")),
                                                  "meshes keep both bodies:", "white + black materials"], "b-good"))
    for x in (190, 390, 590, 790):
        o.append(A(x + 2, y1 + h1 / 2, x + 18, y1 + h1 / 2, pid))
    # bridge
    y2 = 220
    o.append(T(10, y2 - 14, "The bridge used today (Fusion still loads the exporter from another worktree)", "t-warn t-b", 12.5))
    o.append(box(10, y2, 180, 104, "ArUco Export (add-in)", ["writes aruco.json,", "the same block, beside", "the export"], "b-warn"))
    o.append(box(210, y2, 180, 104, "tools/rebuild_urdf.py", ['merges aruco.json when', 'the payload has no', '"aruco" key'], "b-warn"))
    o.append(A(192, y2 + 52, 208, y2 + 52, pid, "edge", "ah"))
    o.append(P("M%g %g C%g %g %g %g %g %g" % (390, y2 + 30, 440, y2 + 10, 470, y1 + h1 + 40, 490, y1 + h1 + 4),
               "edge", pid, "ah", True))
    # sidecar
    o.append(R(610, y2 - 6, 380, 172, "b-vio", 10))
    o.append(T(622, y2 + 16, "aruco/ sidecar: markers.sidecar_files", "t-vio t-b", 13))
    lines = ["markers.json   every tag: id, pattern, source layers",
             "images/        %s PNG + SVG (1 module = 20 px)" % files.get("images", "12"),
             "print_letter.pdf   true size, cut marks, ruler",
             "print3d/       %s STLs: <part>__tile + __cells" % files.get("stls", "?")]
    for k, ln in enumerate(lines):
        o.append(T(622, y2 + 40 + k * 18, ln, "t-mono", 11.5))
    o.append(T(622, y2 + 122, "STLs come from Fusion's bodies (holes and all);", "t-mut", 11.5))
    o.append(T(622, y2 + 138, "the rest is computed from the resolution.", "t-mut", 11.5))
    o.append(P("M%g %g C%g %g %g %g %g %g" % (500, y1 + h1 + 2, 520, y2 - 40, 560, y2 + 40, 606, y2 + 40), "edge", pid, "ah"))
    return svg("".join(o), W, H, "Export chain from Fusion attributes to URDF frames and the aruco sidecar")


def fig_export_dialog() -> str:
    W, H = 340, 236
    bg, hd, ink, mut, fld = "#3b3f46", "#2e3137", "#e8e8e8", "#a9adb5", "#2a2d32"
    o = ['<rect width="%d" height="%d" rx="6" fill="%s"/>' % (W, H, bg),
         '<rect width="%d" height="26" rx="6" fill="%s"/><rect y="14" width="%d" height="12" fill="%s"/>' % (W, hd, W, hd),
         '<text x="12" y="18" fill="%s" font-size="11.5" font-weight="700">ARUCO EXPORT</text>' % ink]
    rows = [("Paper", "drop", "US Letter"), ("Landscape", "chk", False), ("STLs for the 3D printer", "chk", True),
            ("Open the PDF when done", "chk", True)]
    y = 40
    for lab, kind, val in rows:
        o.append('<text x="14" y="%d" fill="%s" font-size="12">%s</text>' % (y + 14, ink, esc(lab)))
        if kind == "drop":
            o.append('<rect x="170" y="%d" width="156" height="20" rx="2" fill="%s"/><text x="178" y="%d" fill="%s" '
                     'font-size="12">%s</text><text x="314" y="%d" fill="%s" font-size="10">▾</text>'
                     % (y, fld, y + 14, ink, esc(val), y + 14, mut))
        else:
            o.append('<rect x="170" y="%d" width="14" height="14" rx="2" fill="%s" stroke="%s"/>' % (y + 3, "#e8e8e8" if val else fld, mut))
            if val:
                o.append('<path d="M173 %g l3.5 3.5 l6 -7" stroke="#2e3137" stroke-width="2" fill="none"/>' % (y + 10))
        y += 30
    o.append('<text x="14" y="%d" fill="%s" font-size="12">12 tag(s).</text>' % (y + 12, ink))
    o.append('<rect x="198" y="%d" width="56" height="24" rx="3" fill="none" stroke="%s"/><text x="226" y="%d" fill="%s" '
             'font-size="12" text-anchor="middle">OK</text>' % (H - 36, mut, H - 20, ink))
    o.append('<rect x="262" y="%d" width="66" height="24" rx="3" fill="none" stroke="%s"/><text x="295" y="%d" fill="%s" '
             'font-size="12" text-anchor="middle">Cancel</text>' % (H - 36, mut, H - 20, ink))
    return ('<svg viewBox="0 0 %d %d" role="img" aria-label="ArUco Export dialog, drawn from the code" '
            'style="max-width:380px;display:block;margin:0 auto;font-family:Segoe UI,Arial,sans-serif">%s</svg>'
            % (W, H, "".join(o)))


# ================================================================ overlays

def urdf_overlay(png: Path, frames_json: Path | None, read_ids, label):
    uri, w, h, _ = load(png, fmt="PNG", max_w=4000)
    o = ['<image href="%s" x="0" y="0" width="%d" height="%d"/>' % (uri, w, h)]
    if frames_json and frames_json.is_file():
        fr = json.loads(frames_json.read_text(encoding="utf-8"))
        fs = max(34, w / 20)
        for f in fr["frames"]:
            if not f["facing"]:
                continue
            c = f["corners_px"]
            ok = f["id"] in read_ids
            pts = " ".join("%.1f,%.1f" % (p[0], p[1]) for p in c)
            o.append('<polygon points="%s" class="%s" vector-effect="non-scaling-stroke"/>' % (pts, "q-ok" if ok else "q-no"))
            (ax, ay), (bx, by) = c[0], c[1]
            o.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" class="q-x" vector-effect="non-scaling-stroke"/>'
                     % (ax, ay, ax + (bx - ax) * 0.55, ay + (by - ay) * 0.55))
            o.append('<circle cx="%.1f" cy="%.1f" r="%g" class="q-c0"/>' % (ax, ay, fs * 0.28))
            cx = sum(p[0] for p in c) / 4
            cy = min(p[1] for p in c) - fs * 0.35
            o.append('<g class="q-lab"><rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="%.1f" class="%s"/>'
                     '<text x="%.1f" y="%.1f" font-size="%.1f" text-anchor="middle">%d</text></g>'
                     % (cx - fs * 0.75, cy - fs * 0.95, fs * 1.5, fs * 1.15, fs * 0.3, "ql-ok" if ok else "ql-no",
                        cx, cy - fs * 0.05, fs * 0.85, f["id"]))
    return ('<svg class="ov" viewBox="0 0 %d %d" role="img" aria-label="%s"><title>%s</title>%s</svg>'
            % (w, h, esc(label), esc(label), "".join(o)))


# ================================================================ css

EXTRA_CSS = r"""
.figbody img{display:block; max-width:100%; height:auto; margin:0 auto; border-radius:8px}
.figbody.tight{padding:10px}
.figbody.dark{background:#232323; border-color:#333}
.pair{display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr)); gap:14px; margin:22px 0}
.pair figure.fig{margin:0}
.trio{display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr)); gap:14px; margin:22px 0}
.trio figure.fig{margin:0}
.reads{display:flex; flex-wrap:wrap; gap:5px; align-items:center; margin-top:10px; font-size:13px; color:var(--muted)}
.reads .rl{margin-right:4px}
.idb{display:inline-grid; place-items:center; min-width:26px; height:24px; padding:0 6px; border-radius:7px;
  font:700 13px/1 var(--mono); background:var(--good-weak); color:var(--good); border:1px solid var(--good)}
.idb.dup{background:var(--warn-weak); color:var(--warn); border-color:var(--warn)}
.idb.none{background:var(--surface-2); color:var(--muted); border-color:var(--line-2); font:600 12px/1 var(--sans)}
.gallery{display:grid; grid-template-columns:repeat(auto-fill,minmax(min(100%,200px),1fr)); gap:12px; margin:18px 0}
.gallery figure{margin:0; background:var(--surface); border:1px solid var(--line); border-radius:12px; padding:8px; min-width:0}
.gallery figcaption{margin:6px 2px 0; font-size:12.5px; line-height:1.35}
.gallery .reads{margin-top:6px}
.gallery .idb{min-width:22px; height:21px; font-size:12px; padding:0 4px}
@media (max-width:640px){ .gallery{grid-template-columns:1fr 1fr; gap:8px} .gallery figure{padding:5px} }
svg.ov{display:block; width:100%; height:auto; border-radius:6px; background:#fff}
svg.ov .q-ok{fill:none; stroke:#0ca678; stroke-width:3}
svg.ov .q-no{fill:none; stroke:#868e96; stroke-width:2.5; stroke-dasharray:6 4}
svg.ov .q-x{stroke:#e8590c; stroke-width:3}
svg.ov .q-c0{fill:#e8590c}
svg.ov .ql-ok{fill:#0ca678} svg.ov .ql-no{fill:#868e96}
svg.ov .q-lab text{fill:#fff; font-family:var(--mono); font-weight:700}
.dg .mko{stroke:var(--line-2); stroke-width:1}
.dg .mko.dsh{stroke-dasharray:4 3; stroke:var(--muted)}
.dg .hole{fill:var(--surface-3); stroke:var(--line-2)}
.dg .hot{fill:none; stroke:var(--bad); stroke-width:2.5}
.dg .ovl{fill:var(--bad); opacity:.85}
.dg .secline{stroke:var(--bad); stroke-width:1.6; stroke-dasharray:7 3 2 3}
.dg .tilebody{fill:#fff; stroke:var(--line-2); stroke-width:1.2}
.dg .zax{fill:var(--surface); stroke:var(--acc); stroke-width:1.6}
.dg .zdot{fill:var(--acc)}
.dg .bulb-on{fill:var(--s4); stroke:var(--warn); stroke-width:1.2}
.dg .bulb-off{fill:var(--surface); stroke:var(--muted); stroke-width:1.2}
.dg .bulb-base{fill:var(--muted)}
.dg .slash{stroke:var(--bad); stroke-width:1.8}
.dg .dupring{fill:none; stroke:var(--warn); stroke-width:2.2}
.dg .pre{white-space:pre}
.dg .halo{paint-order:stroke; stroke:var(--surface); stroke-width:3.5px; stroke-linejoin:round}
.dg .thick{stroke-width:2.4}
.short img{max-height:560px; width:auto}
.vstack{display:grid; gap:10px}
.st{display:inline-block; font:650 12px/1.4 var(--sans); padding:2px 9px; border-radius:999px; white-space:nowrap}
.st.done{background:var(--good-weak); color:var(--good)}
.st.part{background:var(--warn-weak); color:var(--warn)}
.ek{display:inline-block; font:650 11.5px/1.4 var(--sans); padding:1px 8px; border-radius:999px; white-space:nowrap}
.ek.ind{background:var(--good-weak); color:var(--good)}
.ek.con{background:var(--vio-weak); color:var(--violet)}
.ek.unit{background:var(--acc-weak); color:var(--acc)}
.ek.reg{background:var(--surface-3); color:var(--ink-2)}
table.md td.num{font-family:var(--mono); font-size:13.5px}
table.ev td:first-child{white-space:nowrap}
table.ev tr.pend td{background:var(--warn-weak)}
.k-screenshot{background:var(--acc-weak); color:var(--acc)}
.k-fusion-capture{background:var(--vio-weak); color:var(--violet)}
.k-urdf-render{background:var(--good-weak); color:var(--good)}
.k-computed-from-the-code{background:var(--good-weak); color:var(--good)}
.k-from-the-export{background:var(--good-weak); color:var(--good)}
.k-viser{background:var(--acc-weak); color:var(--acc)}
.asklist{list-style:none; padding:0; margin:16px 0; display:grid; gap:10px}
.asklist li{background:var(--surface); border:1px solid var(--line); border-left:4px solid var(--acc); border-radius:10px;
  padding:9px 14px; margin:0; font-size:15.5px}
.asklist li b{color:var(--ink)}
.cmdhead{display:flex; gap:12px; align-items:center; margin:40px 0 8px}
.cmdhead svg{width:34px; height:34px; border-radius:3px; flex:none; box-shadow:0 0 0 1px var(--line)}
.cmdhead h3{margin:0}
.zoomwrap{display:flex; flex-wrap:wrap; gap:14px; align-items:flex-start; justify-content:center}
.zoomwrap img{max-width:100%}
.limits > li{margin:10px 0}
.filetree{font:13px/1.55 var(--mono); background:var(--surface); border:1px solid var(--line); border-radius:10px;
  padding:12px 16px; white-space:pre; overflow-x:auto}
"""


# ================================================================ page

def page(body: str, toc) -> str:
    nav = "".join('<a href="%s"%s>%s</a>' % (fn, ' class="on" aria-current="page"' if k == "how" else "", lab)
                  for k, fn, lab in B.PAGES)
    brand = B.marker_svg(D4, 7, quiet=0.6, cls="", label="ArUco DICT_4X4_50 id 7", grid=grid(7))
    title = "ArUco tags in Fusion — how the prototype works"
    desc = ("How the ArUco tag prototype works: three Fusion commands, tags as two-colour geometry, per-copy ids at any "
            "assembly level, the export chain to URDF, and the evidence.")
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
%s
<div class="foot">Generated %s by <code>docs/build_aruco_howitworks_report.py</code>. Markers in the diagrams are drawn from
<code>bam_cad_core.aruco_dict</code>'s tables (identical to OpenCV's for all 9,596 ids); screenshots and renders are inlined;
nothing on this page loads from the network.</div>
</main></div></div>
<script>%s</script>
</body></html>""" % (esc(title), esc(desc), B.CSS, EXTRA_CSS, brand, nav, B.toc_html(toc), body, BUILT, B.JS)


def h2(sid, text, toc):
    toc.append((2, sid, text))
    return '<h2 id="%s">%s</h2>' % (sid, esc(text))


def h3(sid, text, toc):
    toc.append((3, sid, text))
    return '<h3 id="%s">%s</h3>' % (sid, esc(text))


def table(head, rows, cls="md"):
    th = "".join("<th>%s</th>" % h for h in head)
    tb = "".join("<tr%s>%s</tr>" % (r[0] if isinstance(r, tuple) else "", "".join(
        "<td%s>%s</td>" % (c[0], c[1]) if isinstance(c, tuple) else "<td>%s</td>" % c
        for c in (r[1] if isinstance(r, tuple) else r))) for r in rows)
    return '<div class="tablewrap"><table class="%s"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>' % (cls, th, tb)


def export_facts():
    f = {}
    try:
        urdf = (EXPORT_DIR / "urdf" / "untitled.urdf").read_text(encoding="utf-8")
        f["links"] = str(urdf.count("<link "))
        f["joints"] = str(urdf.count("<joint "))
        f["fixed"] = str(urdf.count('type="fixed"'))
        f["moving"] = str(urdf.count('type="continuous"') + urdf.count('type="revolute"'))
        f["black"] = str(urdf.count('material name="plastic_matte_black"'))
        f["white"] = str(urdf.count('material name="plastic_matte_white"'))
        i = urdf.index('<link name="aruco_0_target">')
        j = urdf.index("</joint>", i) + len("</joint>")
        f["snippet"] = urdf[i:j]
        f["images"] = str(len(list((EXPORT_DIR / "aruco" / "images").glob("*.png"))))
        f["stls"] = str(len(list((EXPORT_DIR / "aruco" / "print3d").glob("*.stl"))))
        f["stl_names"] = sorted(p.name for p in (EXPORT_DIR / "aruco" / "print3d").glob("*.stl"))
    except (OSError, ValueError) as e:
        warn("demo export not readable (%s); using the numbers from the brief" % e)
        f = {"links": "23", "joints": "22", "fixed": "12", "moving": "10"}
    return f


def build() -> str:
    toc: list = []
    rec, rows, overrides = load_record()
    vo = json.loads((MEDIA / "verify_output.json").read_text(encoding="utf-8"))
    vb = json.loads((MEDIA / "verify_boards.json").read_text(encoding="utf-8"))
    det = json.loads((DEMO / "detections.json").read_text(encoding="utf-8"))
    sp = serial_passes()
    ef = export_facts()

    # cross-checks between the computed walk-through and the real export
    for key, cell in rows.items():
        if sp["s2"].get(key) != cell["id"]:
            warn("plan_serials gives %s for %s but the export recorded %s" % (sp["s2"].get(key), key, cell["id"]))
    union = sorted({i for k, v in det.items() if k.startswith("urdf_view") for i in v})
    if union != list(range(12)):
        warn("URDF view union is %s, not 0..11" % union)
    n_markers = vo["grids"]["markers_compared"]
    n_dicts = len(vo["grids"]["per_dictionary"])
    rt_ok = sum(d["detected_ok"] for d in vo["roundtrip"]["per_dictionary"].values())
    let, a4 = vo["sheets"]["letter"], vo["sheets"]["a4"]
    sizes = sorted({it["marker_mm"] for it in let["items"]})
    ruler = let["rulers"][0]["mm_bar_mm"]
    n_boards = len(vb["ink"])
    n_grid = sum(1 for x in vb["ink"] if x["board"].startswith("grid"))
    n_ch = sum(1 for x in vb["ink"] if x["board"].startswith("charuco"))
    px_diff = max(max(x["raster_mismatch_px"], x["png_mismatch_px"]) for x in vb["ink"])
    rot = max(f["rot_err_deg"] for f in vb["frames"])
    trans = max(f["trans_err_mm"] for f in vb["frames"])
    chs = next(it for it in vb["sheet"]["items"] if it["key"] == "charuco")
    bb_planned, bb_got = board_block_example()

    b = []
    # ---------------------------------------------------------------- hero
    b.append('<div class="hero"><div class="eyebrow">Prototype · how it works · %s</div>'
             '<h1>ArUco tags in Fusion — how the prototype works</h1>'
             '<p class="lede">Tags are now modelled parts in Fusion: a white tile and an inlaid black body you can 3D print '
             'in two colours. Each copy of a part can carry its own id, set at any assembly level like a serial number, '
             'without a new model. The ids, frames, images and a true-size print sheet travel out with the URDF. This page '
             'shows how each piece works, with screenshots from a real Fusion session and the ids OpenCV reads back from '
             'Fusion and from the exported robot.</p>'
             '<p class="meta">Branch <code>%s</code> · commits <code>%s</code>, <code>%s</code> · demo export in '
             '<code>%USERPROFILE%\\CADExporter\\ArUco_Demo\\</code></p></div>' % (BUILT, B.BRANCH, *COMMITS))
    b.append('<div class="stats">'
             '<div class="stat"><b>3</b><span>commands in an ARUCO panel on the Solid tab</span></div>'
             '<div class="stat"><b>12 → 12</b><span>demo tags: all #0 at first, then 12 distinct ids from two presses of '
             'Number duplicates</span></div>'
             '<div class="stat"><b>%d/12</b><span>ids OpenCV reads back from renders of the exported URDF</span></div>'
             '<div class="stat"><b>%.3f mm</b><span>worst gap between a URDF marker frame and its printed ink '
             '(independent check)</span></div>'
             '<div class="stat"><b>%.4f mm</b><span>worst printed marker-side error, PDF rasterised at 600 dpi</span></div>'
             '</div>' % (len(union), POSE["worst_mm"], max(let["worst_side_err_mm"], a4["worst_side_err_mm"])))
    bf = load(DEMO / "chain_01_before_front.png", autocrop=True, fmt="PNG")
    b.append('<div class="pair">%s%s</div>' % (
        B.fig('<img src="%s" width="%d" height="%d" alt="Fusion front view before serials: twelve copies of marker 0">'
              % bf[:3] + reads(det["chain_01_before_front.png"]),
              "<b>Before.</b> Fusion's own viewport, front view of the demo robot: one Tag part, copied twelve times "
              "through Actuator and Arm. OpenCV finds marker 0 six times: a camera cannot tell the copies apart.",
              "Fusion capture", "short"),
        B.fig(img(DEMO / "chain_02_after_front.png", "Fusion front view after serials", autocrop=True, fmt="PNG")
              + reads(det["chain_02_after_front.png"]),
              "<b>After</b> ArUco IDs → Number duplicates → OK, at the Arm level and then the design level. Same view: "
              "every visible tag is a different marker (the other faces point away from this camera).",
              "Fusion capture", "short")))

    # ---------------------------------------------------------------- 1 asked
    b.append(h2("asked", "What you asked, and what exists now", toc))
    b.append('<p>Your asks from today, paraphrased:</p><ul class="asklist">'
             '<li><b>Markers are first class in CAD.</b> You define in Fusion where each tag is and which id it carries.</li>'
             '<li><b>Tags are 3D printed, so they are real geometry,</b> not decals or stickers.</li>'
             '<li><b>A copy can override its id at any sub-assembly level, like a serial number.</b> Six actuators pasted '
             'into an arm get their own ids there, without a new model. Geometry cannot be overridden; ids can.</li>'
             '<li><b>The information travels with the robot description.</b></li>'
             '<li><b>The sidecar keeps the images and an ergonomic PDF export</b> (Letter, A4 and the rest, chosen in '
             'settings), so a tag goes straight to a paper printer or a 3D printer.</li>'
             '<li><b>Single markers, ChArUco boards and ArUco grid boards.</b></li>'
             '<li><b>The test of success: the pattern is visible after the URDF export.</b></li></ul>')
    D, Pt = '<span class="st done">done</span>', '<span class="st part">partial</span>'
    b.append(table(["Ask", "Status", "What does it", "Evidence"], [
        ["Markers defined in CAD (where + which id)", D,
         "<b>ArUco Tag</b> turns a rectangle or a planar face into a tag; the record sits on the component as the "
         "attribute <code>bam_aruco/tags</code>.", '<a href="#cmd-tag">ArUco Tag</a> · <a href="#model">the model</a>'],
        ["Real, printable geometry", D,
         "A white tile body plus a black cells body inlaid 0.6 mm, flush with the face. ArUco Export writes one "
         "STL pair per tag part.", '<a href="#geometry">geometry</a>'],
        ["Per-copy id at any level, like a serial number", D,
         "Overrides live on the native occurrence in the component where they were authored; the outermost level "
         "wins. Only <code>marker_id</code> and <code>dictionary</code> can be overridden.",
         '<a href="#model">model</a> · <a href="#variants">variants</a> · <a href="#serials">serials</a>'],
        ["Info travels with the robot description", Pt,
         'On this branch the exporter reads <code>payload["aruco"]</code> and the URDF gets one frame per tag. Fusion '
         "still loads the exporter from another worktree, so today the add-in's <code>aruco.json</code> plus "
         "<code>tools/rebuild_urdf.py</code> bridge it.", '<a href="#export">export chain</a>'],
        ["Sidecar: images + ergonomic PDF, page size in settings", Pt,
         "<code>aruco/</code> holds markers.json, a PNG and an SVG per marker, a true-size PDF and the STLs. The page "
         "size (Letter, A4, Legal, A3, Tabloid, A5, landscape) is chosen in the ArUco Export dialog; the exporter's own "
         "path writes Letter and has no setting for it yet.", '<a href="#export">export</a> · <a href="#cmd-export">ArUco Export</a>'],
        ["Single, grid board, ChArUco board", D,
         "One command, three layouts, fitted to the face. %d boards match OpenCV pixel for pixel." % n_boards,
         '<a href="#geo-boards">boards</a> · <a href="#evidence">evidence</a>'],
        ["Pattern visible after the URDF export", D,
         "OpenCV reads all 12 ids from renders of the exported URDF meshes, and every URDF marker frame sits on its "
         "ink within %.3f mm, also after the joints move." % POSE["worst_mm"], '<a href="#ev-urdf">URDF evidence</a>'],
    ]))

    # ---------------------------------------------------------------- 2 model
    b.append(h2("model", "The model in one picture", toc))
    b.append("<p>Two things are stored, and they are kept apart on purpose. What a component's geometry <b>encodes</b> "
             "(dictionary, id, size, where on the part) is a <code>TagDef</code> on the component. What one <b>copy</b> "
             "should print is an override, authored at any assembly level, stored as data on an occurrence; it never "
             "touches the component. <code>markers.resolve()</code> composes the two for every occurrence path.</p>")
    b.append(B.fig(fig_anatomy(rec["instances"][0]["modelled"]),
                   "A tag is a <b>base feature</b> holding the black cells as one solid, built with the "
                   "TemporaryBRepManager from boxes, and a <b>Combine</b> that cuts them out of the tile while keeping "
                   "them. Features added after the tag, like the demo's mount hole, keep working: the probe's hole "
                   "stayed healthy through a regeneration. The spec shown is the demo's, read from the export record.",
                   "computed from the code", "wide"))
    b.append(B.fig(fig_tree(rows, overrides),
                   "The demo's records, read back from the export (<code>demo_markers.json</code>). Arm-level "
                   "overrides sit on Actuator:3 to Actuator:7 <b>inside the Arm component</b>, keyed "
                   "<code>&lt;path below&gt;|&lt;tag key&gt;</code>, so they travel into every design that inserts the "
                   "arm, and both arms get 0 to 5. The root then re-serials Arm:2 (6 to 11) with keys like "
                   "<code>Actuator:3+Tag:2|front</code> on its own occurrence Arm:2. Struck-through values were "
                   "overridden by an outer level.", "from the export", "wide"))
    b.append(B.callout("<p>Storing an override on a <b>native</b> occurrence, not on an assembly-context proxy, is what "
                       "makes “set it once at the sub-assembly level” work: the arm carries its actuators' serials "
                       "into any robot. The outermost level wins because the assembly that places a part is the one "
                       "that knows which copy is which; it is the rule USD calls stronger opinions.</p>",
                       "note", "Why the record lives where it does"))

    # ---------------------------------------------------------------- 3 variants
    b.append(h2("variants", "Why a variant part per copy", toc))
    b.append("<p>An override is only data. The plastic still has to change, and Fusion keeps geometry once per "
             "component, so a copy whose id differs from what its component encodes gets its own small tag part. "
             "<code>apply_variants()</code> makes the geometry match the resolution and is idempotent: run it twice "
             "with nothing changed and it touches nothing.</p>")
    b.append(B.fig(fig_variant(),
                   "A variant goes into the component that owns the <b>winning</b> override (Arm for #1 to #5, the root "
                   "for #6 to #11), at the template tag's pose, with a rigid as-built joint to the copy's host. The "
                   "template is hidden for that one path. Along a path an outer level overrode, the deeper variant is "
                   "hidden too; otherwise Arm:2's actuators would wear Arm's tag and the root's, one inside the other.",
                   "schematic", "wide"))
    b.append(B.fig('<div class="zoomwrap">%s%s</div>' % (
        img(UI / "05_after_ok_root_variants_browser.jpg", "Fusion browser after OK at the design level, enlarged",
            crop=(138, 47, 250, 192), style="width:min(100%,300px)"),
        img(UI / "05_after_ok_root_variants_browser.jpg", "Fusion viewport after OK at the design level",
            crop=(380, 95, 640, 395), style="width:min(100%,340px)")),
                   "After OK at the design level: the browser's root now lists Arm:1, Base:1, Arm:2 and six tag parts "
                   "named <code>Tag · 4X4_50 #6</code> to <code>#11</code>, the root-level variants for Arm:2 (the "
                   "names are small in this capture; they follow <code>_variant_name</code>).", "screenshot"))

    # ---------------------------------------------------------------- 4 commands
    b.append(h2("commands", "The three commands", toc))
    b.append("<p>The add-in puts an <b>ARUCO</b> panel on the Solid tab with three buttons. Each command does its model "
             "changes inside one execute, so each is a single undo step.</p>")
    b.append(B.fig('<div class="zoomwrap">%s%s</div>' % (
        img(UI / "01_aruco_panel_tooltip.jpg", "Fusion with the ARUCO panel and the demo robot", max_w=1400,
            style="max-width:min(100%,760px)"),
        img(UI / "01_aruco_panel_tooltip.jpg", "ARUCO panel and ArUco IDs tooltip, zoomed", crop=(1060, 28, 1310, 128),
            style="width:min(100%,500px)")),
        "The demo design in Fusion with the panel: <b>ArUco Tag</b>, <b>ArUco IDs</b>, <b>ArUco Export</b>. The tooltip "
        "is ArUco IDs': give every copy its own marker id at any assembly level, like a serial number, without making a "
        "new model. Below: the panel enlarged.", "screenshot"))

    def cmdhead(sid, mid, text):
        toc.append((3, sid, text))
        return ('<div class="cmdhead">%s<h3 id="%s">%s</h3></div>'
                % (B.marker_svg(D4, mid, quiet=0.6, cls="", label="", grid=grid(mid)), sid, esc(text)))

    b.append(cmdhead("cmd-tag", 0, "ArUco Tag: a rectangle or a face becomes a tag"))
    b.append("<p>Pick a <b>closed sketch profile</b> and the command extrudes it into a tile and tags its top face "
             "(“ArUco Extrude”), or pick a <b>planar face</b> of a body you modelled and that face becomes the tag. "
             "Type is single marker, grid board or ChArUco board. The id defaults to the lowest one no tag in the "
             "design prints. As soon as something is selected the dialog shows the fitted size, the module, the tile "
             "and how far a 1080p, 70° camera can read it at 5 px per module, and warns when a module drops under "
             "1.2 mm (fine on paper, hard for a 0.4 mm nozzle).</p>")
    b.append('<div class="pair">%s%s</div>' % (
        B.fig('<div class="zoomwrap">%s</div>' % img(UI / "06_tag_dialog_rectangle_selected.jpg", "ArUco Tag dialog",
                                                     crop=(1311, 178, 1453, 450), style="width:min(100%,230px)"),
              "A 40 × 40 mm rectangle selected. Live info: <code>DICT_4X4_50 #0 · 30.0 mm</code>, module 5.00 mm, "
              "tile 40.0 × 40.0 mm, readable to about 1.37 m.", "screenshot"),
        B.fig(img(UI / "07_aruco_extrude_result.jpg", "ArUco Extrude result", max_w=1100),
              "OK: the tile is extruded 3 mm and marker #0 is inlaid in it (top view; the grey rectangle is another "
              "sketch).", "screenshot")))
    b.append('<div class="pair">%s%s</div>' % (
        B.fig('<div class="zoomwrap">%s</div>' % img(UI / "08_tag_dialog_grid_board.jpg", "ArUco Tag dialog, grid board",
                                                     style="width:min(100%,230px)"),
              "Type <b>Grid board</b>, 3 columns × 2 rows on a rectangle: <code>Grid 3x2 DICT_4X4_50 #1-6 · 27.6 mm"
              "</code>, module 4.61 mm, tile 103.2 × 70.0 mm. The first id moved to 1 because #0 is taken.",
              "screenshot"),
        B.fig(img(UI / "09_grid_board_result.jpg", "Grid board result", crop=(140, 140, 870, 340), max_w=1000),
              "The result beside the single tag: one tile, six markers #1 to #6 in OpenCV's order.", "screenshot")))

    b.append(cmdhead("cmd-ids", 5, "ArUco IDs: serial numbers at an assembly level"))
    b.append("<p>Choose the <b>assembly level</b>: the design, or any component that has occurrences (here Arm). The "
             "table lists every tag under that level with its id and where the id comes from; copies that print the "
             "same marker are flagged. <b>Number duplicates</b> fills in serials (the next section shows how). "
             "<b>OK</b> writes every changed row as an override at that level, then rebuilds the tag parts.</p>")
    b.append(B.fig('<div class="zoomwrap">%s</div>' % img(UI / "02_ids_dialog_design_all_duplicate.jpg",
                                                          "ArUco IDs dialog at design level", crop=(1135, 178, 1452, 540),
                                                          style="width:min(100%,430px)"),
                   "1 · Design level, before anything is set: 12 tags, every one id 0 from the model, all flagged "
                   "<b>⚠ duplicate</b>.", "screenshot"))
    b.append('<div class="pair">%s%s</div>' % (
        B.fig(img(UI / "03_ids_dialog_arm_number_duplicates.jpg", "ArUco IDs at Arm level after Number duplicates"),
              "2 · Level <b>Arm</b>, Number duplicates: Actuator:2 to 7 get 0 to 5. The flags still say duplicate "
              "until OK (a refresh nit). OK writes five overrides in Arm.", "screenshot"),
        B.fig(img(UI / "04_ids_dialog_design_level_serials.jpg", "ArUco IDs at design level after Number duplicates"),
              "3 · Back at the <b>design</b> level: the source column now says <b>Arm</b> for the arm's serials. "
              "Number duplicates gives Arm:2 6 to 11; OK writes them on Arm:2 at the root.", "screenshot")))

    b.append(cmdhead("cmd-export", 11, "ArUco Export: sheet, images, record, STLs"))
    b.append('<div class="twocol"><div>%s</div><div><p>Paper is US Letter, A4, US Legal, A3, Tabloid or A5, '
             'optionally landscape. It writes into <code>~/CADExporter/&lt;design&gt;/</code>: '
             '<code>aruco.json</code> (the raw block, for an older exporter), the <code>aruco/</code> sidecar, and with '
             '<b>STLs for the 3D printer</b> one <code>__tile</code> + <code>__cells</code> pair per tag part, each in '
             'the part\'s own frame, ready to load as one two-colour object. It opens the PDF when done. If any '
             'marker is printed by more than one copy, the dialog says so in red and asks you to run ArUco IDs first.'
             '</p></div></div>' % B.fig(fig_export_dialog(), "Drawn from <code>aruco_commands.py</code>; no "
                                                             "screenshot of this dialog was taken.", "wireframe"))

    # ---------------------------------------------------------------- 5 geometry
    b.append(h2("geometry", "Geometry details", toc))
    b.append(h3("geo-bodies", "Two bodies, so the pattern survives every export", toc))
    b.append("<p>Black and white are separate <b>bodies</b>, not appearances on one body. That is what carries the "
             "pattern through the URDF export, which writes one coloured mesh per body: the demo URDF references "
             "<code>plastic_matte_black</code> %s times and <code>plastic_matte_white</code> %s times. A slicer gets the "
             "same two bodies as two colours.</p>" % (ef.get("black", "13"), ef.get("white", "13")))
    b.append(B.fig(fig_section(), "Dimensions of the demo tag. The marker length L is the black square including "
                   "its one-module border, the number a pose estimator multiplies by.", "computed from the code", "wide"))
    b.append(h3("geo-growth", "Cells grown by 10 µm so corner contacts fuse", toc))
    b.append(B.fig(fig_growth(), "Two black cells that touch only at a corner make a non-manifold solid. Each box is "
                   "grown by <code>CELL_OVERLAP_M</code> = 10 µm, clamped to the black square, so neighbours share a "
                   "sliver of area and the union is one solid.", "computed from the code", "wide"))
    b.append(h3("geo-boards", "Boards: fitted to the face, in OpenCV's frame", toc))
    b.append("<p>On a face, a board is sized to fill it with its quiet zone, using OpenCV's tutorial ratios: a grid's "
             "gap is 0.2 of the marker length, a ChArUco marker is 0.75 of the square. Both are recorded per tag. "
             "A per-copy override moves a board's whole id block.</p>")
    b.append(B.fig(fig_boards(), "Drawn from <code>aruco_board.black_rects_m</code>, the same ink OpenCV's "
                   "<code>generateImage</code> produces (%d boards, %d pixels differ)." % (n_boards, px_diff),
                   "computed from the code", "wide"))
    b.append(B.fig(img(DEMO / "boards_top.png", "Fusion capture of single, grid and ChArUco tags", autocrop=True, fmt="PNG")
                   + reads([0] + list(range(1, 7)) + list(range(7, 17)), "OpenCV reads (%d/%d markers, %d/%d ChArUco "
                           "corners)" % (BOARDS_FUSION["markers"], BOARDS_FUSION["markers"], BOARDS_FUSION["corners"],
                                         BOARDS_FUSION["corners"])),
                   "Fusion's viewport, top view of the boards fixture: single #0, grid #1 to #6, ChArUco #7 to #16 (5 × 4 "
                   "squares, 12 inner corners).", "Fusion capture"))

    # ---------------------------------------------------------------- 6 serials
    b.append(h2("serials", "Serial numbering: how Number duplicates decides", toc))
    b.append('<ol class="steps">'
             '<li><b>Find collisions by pattern</b>4X4_50 #3 and 4X4_1000 #3 are the same printed square, so groups are '
             'keyed by pattern, not by dictionary name.</li>'
             '<li><b>Write at the deepest level that works</b>The copies in a group first differ inside some component; '
             'the serials are written there, so they travel with it.</li>'
             '<li><b>Re-resolve, repeat</b>An override written in a shared component lands on every copy of it, so '
             'each pass re-resolves and looks again.</li>'
             '<li><b>Lowest free ids, whole blocks</b>The first copy keeps its id; the others get the lowest free id, '
             'and a board gets a contiguous block.</li></ol>')
    b.append(B.fig(fig_serials(sp), "Computed at build time by running <code>markers.plan_serials</code> on the "
                   "demo's occurrence tree; it reproduces the ids the real export recorded for all 12 copies. In the "
                   "Fusion session the same two steps were taken by hand: Number duplicates at the Arm level, OK, then at "
                   "the design level, OK.", "computed from the code", "wide"))
    bb = ", ".join("%s → ids %d–%d" % (p, v[0], v[-1]) for p, v in sorted(bb_got.items()) if len(v) > 1)
    b.append("<p>Boards take blocks. With a single tag #0 and two copies of a 3 × 2 grid board that starts at #1, the "
             "same function gives <b>%s</b>: the second board's block starts at the lowest id where six in a row are "
             "free.</p>" % esc(bb))

    # ---------------------------------------------------------------- 7 export
    b.append(h2("export", "The export chain", toc))
    b.append(B.fig(fig_pipeline(ef), "The exporter reads the attributes as one block, resolves them against the "
                   "occurrence tree the CAD Exporter already builds, and lowers each visible tag to the same frame a "
                   "hand-declared fiducial gets: an empty link under a fixed mount. Hidden copies are left out of the "
                   "frames, the sheet and the images.", "schematic", "wide"))
    snippet = ef.get("snippet")
    if snippet:
        b.append('<div class="twocol"><div><p>The first marker frame in the demo URDF, verbatim: a massless link and a '
                 'fixed joint whose origin is the OpenCV frame of marker #0, relative to the link the tag rides on.</p>'
                 '<pre class="code"><code>%s</code></pre><p>%s links and %s joints: the robot\'s own links plus one '
                 '<code>aruco_&lt;id&gt;_target</code> per tag; %s joints move, %s are fixed.</p></div>'
                 '<div><div class="filetree">%s</div></div></div>'
                 % (esc(snippet), ef["links"], ef["joints"], ef["moving"], ef["fixed"], esc(
                     "ArUco_Demo/\n  urdf/untitled.urdf\n  urdf/meshes/...\n  aruco/\n    markers.json\n"
                     "    images/4x4_50_0.png .svg ... 4x4_50_11\n    print_letter.pdf\n    print3d/\n"
                     + "".join("      %s\n" % n for n in ef.get("stl_names", [])[:4]) + "      ...")))
    b.append('<div class="pair">%s%s</div>' % (
        B.fig('<div class="zoomwrap">%s</div>' % img(DEMO / "demo_print_letter_p1.png", "Print sheet, US Letter",
                                                     fmt="PNG", max_w=900, style="width:min(100%,460px);box-shadow:0 0 0 1px var(--line)"),
              'The demo\'s print sheet: twelve 30 mm markers, each labelled with its id and the copy it belongs on, '
              'plus a 100 mm and a 4 in ruler. <a href="../media/aruco/demo/demo_print_letter.pdf">The PDF</a>.',
              "from the export"),
        B.fig('<div class="zoomwrap">%s</div>' % img(MEDIA / "sheet_boards_letter_p1.png", "Board print sheet", fmt="PNG",
                                                     max_w=900, style="width:min(100%,460px);box-shadow:0 0 0 1px var(--line)"),
              "Boards on paper: the verification sheet with a ChArUco, a grid, a legacy-pattern ChArUco and a single "
              "marker.", "from the export")))

    # ---------------------------------------------------------------- 8 evidence
    b.append(h2("evidence", "Evidence", toc))
    b.append("<p>The strongest checks read <b>pixels</b>: OpenCV, which shares no code with the add-in, decodes ids from "
             "Fusion's own viewport and from renders of the exported URDF meshes. Labels in the table say which checks "
             "are independent and which only show that two parts of our own code agree.</p>")
    b.append(h3("ev-fusion", "In Fusion", toc))
    b.append('<div class="pair">%s%s</div>' % (
        B.fig(img(DEMO / "chain_01_before_iso.png", "Fusion iso view before", autocrop=True, fmt="PNG")
              + reads(det["chain_01_before_iso.png"]), "Iso view before: one readable tag, #0.", "Fusion capture", "short"),
        B.fig(img(DEMO / "chain_02_after_iso.png", "Fusion iso view after", autocrop=True, fmt="PNG")
              + reads(det["chain_02_after_iso.png"]), "Iso view after: the readable tag is now #5, Arm:1's top actuator.",
              "Fusion capture", "short")))
    b.append(h3("ev-urdf", "After the URDF export", toc))
    def vdir(fn):
        toks = fn[len("urdf_view"):-len(".png")].split("_")
        return "(%s)" % ", ".join(("−" if t.startswith("m") else "") + t.lstrip("m").replace("m", ".") for t in toks)

    views = [("urdf_view0_1_0.png", "the side Fusion's front view sees"), ("urdf_view0_m1_0.png", "the opposite side"),
             ("urdf_view1_0_0.png", "side"), ("urdf_viewm1_0_0.png", "side"),
             ("urdf_view1_m0m6_0.png", "oblique"), ("urdf_viewm0m6_m1_0.png", "oblique"),
             ("urdf_view0m6_1_0.png", "oblique"), ("urdf_viewm1_0m6_0.png", "oblique")]
    cells = []
    for fn, lab in views:
        p = DEMO / fn
        ids = det.get(fn, [])
        fj = DEMO / fn.replace(".png", ".frames.json")
        cells.append('<figure>%s<figcaption><b>%s</b> %s</figcaption>%s</figure>'
                     % (urdf_overlay(p, fj, ids, "URDF render %s" % fn), esc(vdir(fn)), esc(lab),
                        reads(ids, "reads")))
    b.append('<div class="gallery">%s</div>' % "".join(cells))
    b.append('<p style="font-size:14.5px;color:var(--muted)">Each tile is a z-buffered render of the exported URDF\'s '
             'meshes at q = 0, labelled with the view direction from its file name; the first reads exactly the ids Fusion\'s '
             'own front view read (3, 4, 5, 9, 10, 11). Badges: the ids OpenCV decodes from the pixels; '
             'across the eight views they cover <b>%s</b>, %d of 12. Overlay, from the independent pose check: each '
             'facing <code>aruco_&lt;id&gt;_target</code> frame, FK\'d from the URDF, with its four marker corners '
             'projected (green: OpenCV read it; grey dashed: hidden behind the other arm). The orange dot is OpenCV\'s '
             'corner 0 (top-left) and the orange tick runs toward corner 1, so position and in-plane rotation are both '
             'on show.</p>' % (", ".join(map(str, union)), len(union)))
    posed = urdf_overlay(DEMO / "urdf_posed_front.png", DEMO / "urdf_posed_front.frames.json", POSED_READS,
                         "URDF render with joints moved")
    b.append('<div class="pair">%s%s</div>' % (
        B.fig('<div style="max-width:340px;margin:0 auto">%s</div>' % posed + reads(POSED_READS),
              "Joints moved (%s): the frames follow the links and the ids still read." % POSED_JOINTS, "URDF render"),
        B.fig('<div class="vstack">%s%s</div>' % (img(DEMO / "viser_q0.jpg", "viser viewer at q = 0"),
                                                  img(DEMO / "viser_posed.jpg", "viser viewer posed")),
              "The exported URDF in the viser viewer: at q = 0 (top) the inlaid patterns arrive as geometry; with "
              "j1_arm_1 = 0.9 and j3_arm_1 = −1.2 (bottom) the tags ride their links.", "viser")))

    b.append(h3("ev-table", "The numbers", toc))
    I, C, U, G = ('<span class="ek ind">independent</span>', '<span class="ek con">consistency</span>',
                  '<span class="ek unit">unit tests</span>', '<span class="ek reg">regression</span>')
    ev = [
        ["Dictionary tables", "every id of every dictionary vs <code>cv2.aruco.generateImageMarker</code>; then decode",
         "", I],
        ["Print sheet (Letter + A4)", "%d markers %g–%g mm, PDF rasterised at %d dpi and measured with OpenCV"
         % (len(let["items"]), sizes[0], sizes[-1], vo["dpi"]), "", I],
        ["Board ink", "%d boards (%d grid, %d ChArUco) vs <code>board.generateImage</code>" % (n_boards, n_grid, n_ch),
         "", I],
        ["Board pose frame", "synthetic renders at a known pose, solved with solvePnP (%d frames)" % len(vb["frames"]),
         "", I],
        ["Printed ChArUco", "board on the rasterised sheet", "", I],
        ["Fusion viewport", "OpenCV on Fusion's own render of the demo, front view", "", I],
        ["Fusion boards fixture", "OpenCV on Fusion's render of single + grid + ChArUco", "", I],
        ["URDF mesh renders", "OpenCV on z-buffered renders of the exported meshes, 8 views + 1 posed", "", I],
        ["<b>Independent pose check</b>", "<code>tools/verify_aruco_render.py</code>, no <code>bam_cad_core</code> "
         "import: each URDF marker frame FK'd with yourdfpy, its 4 corners projected into the render and compared in "
         "OpenCV's order (TL, TR, BR, BL) with the corners cv2.aruco detects; %d views + %d posed render "
         "(%s)" % (POSE["views"], POSE["posed"], POSED_JOINTS), "", I],
        ["FK consistency", "<code>tools/verify_aruco_urdf.py</code>: the URDF's kinematic chain carries each tag frame "
         "through unchanged (yourdfpy FK vs markers.json world poses). Both sides come from the same "
         "<code>markers</code> pose math, so this is consistency, not proof", "", C],
        ["CAD Exporter evaluators", "the existing evaluator set on the demo export; none of them judges "
         "<code>aruco/</code> yet", "", G],
        ["Tests, new", ", ".join("%s %d" % t for t in TESTS_NEW), "", U],
        ["Tests, existing", ", ".join("%s %d" % t for t in TESTS_OLD) + ": counts unchanged", "", U],
    ]
    results = [
        "%s/%s ids in %d dictionaries identical; %s/%s decode in round trip" % (f"{n_markers:,}", f"{n_markers:,}", n_dicts,
                                                                                f"{rt_ok:,}", f"{n_markers:,}"),
        "worst marker-side error %.4f mm (Letter) / %.4f mm (A4); 100 mm ruler measures %.3f mm"
        % (let["worst_side_err_mm"], a4["worst_side_err_mm"], ruler),
        "%d pixels differ" % px_diff,
        "within %.2f° / %.2f mm" % (rot, trans),
        "%d/%d ids, %d/%d corners" % (chs["ids_found_once"], chs["ids_expected"], chs["charuco_corners_found"],
                                      chs["charuco_corners_expected"]),
        "before: id 0 ×%d; after: %s (the other faces point away)" % (len(det["chain_01_before_front.png"]),
                                                                     ", ".join(map(str, det["chain_02_after_front.png"]))),
        "%d/%d markers, %d/%d ChArUco corners" % (BOARDS_FUSION["markers"], BOARDS_FUSION["markers"],
                                                 BOARDS_FUSION["corners"], BOARDS_FUSION["corners"]),
        "union of ids over the 8 views = %d–%d (%d/12); posed render reads %s" % (union[0], union[-1], len(union),
                                                                                 ", ".join(map(str, POSED_READS))),
        "<b>%d frame/view pairs, worst corner error %.3f mm</b> (≈%.1f px at %.3f mm/px): position and in-plane "
        "rotation agree with the ink, after the joints move too. %d facing frames not read: occluded by the other arm "
        "in side views" % (POSE["pairs"], POSE["worst_mm"], POSE["worst_px"], POSE["mm_px"], POSE["unread"]),
        "12/12 within %s m / %s rad" % (FK["pos_m"], FK["rot_rad"]),
        "%d green / %d skipped / %d red; total mass %.2f kg; %s links, %s joints (%s actuated, %s fixed)"
        % (EVALS["green"], EVALS["skipped"], EVALS["red"], EVALS["mass_kg"], ef["links"], ef["joints"], ef["moving"],
           ef["fixed"]),
        "%d pass" % sum(n for _, n in TESTS_NEW),
        "%d pass" % sum(n for _, n in TESTS_OLD),
    ]
    rows_ev = []
    for r, res in zip(ev, results):
        rows_ev.append(("", [r[0], r[1], ('', res), r[3]]))
    b.append(table(["Check", "What was measured", "Result", "Kind"], rows_ev, "md ev"))

    # ---------------------------------------------------------------- 9 limits
    b.append(h2("limits", "Known limits and next steps", toc))
    b.append('<ul class="limits">'
             '<li><b>Variant parts are dumb copies.</b> Edit the template tag, then press OK in ArUco IDs to rebuild '
             'them; a variant has no timeline of its own.</li>'
             '<li><b>The tag frame is recorded when the tag is created.</b> If the face moves upstream the frame does not '
             'follow yet; auto-refit on regeneration is next.</li>'
             '<li><b>A hole that crosses the marker area is filled in variants</b> (the pattern square fills the old '
             'pockets). Keep holes in the quiet zone or outside it, as the demo does.</li>'
             '<li><b>Hidden, superseded variants still weigh</b> in Fusion\'s physical properties for their parent: an '
             'earlier demo tripped evaluator e09\'s loudness leg. Suppressing instead of hiding is the follow-up.</li>'
             '<li><b>Fusion still loads the CAD Exporter from another worktree</b>, so <code>payload["aruco"]</code> '
             'arrives once this branch merges; until then <code>aruco.json</code> plus <code>tools/rebuild_urdf.py</code> '
             'bridge it.</li>'
             '<li><b>No CAD Exporter evaluator judges the <code>aruco/</code> output yet.</b> Open question for you. '
             'Per the branch\'s evaluator rules, such an evaluator must not import the markers code it judges, and '
             'needs a committed mutation that turns it red.</li>'
             '<li><b>It writes Fusion attributes.</b> That reverses the 2026-08-20 “sidecar only” decision, because '
             'markers are now defined in CAD.</li>'
             '<li><b>Mirrored-marker trap.</b> A mirrored DICT_ARUCO_ORIGINAL marker decodes as other valid ids, so '
             'keep tags un-mirrored; 4X4_50 is safe.</li>'
             '<li><b>UI nits seen while testing:</b> the Fusion dropdown needed the keyboard; the “duplicate” labels '
             'refresh only after OK; the level label reads “Design ((Unsaved))” for an unsaved design.</li></ul>')

    # ---------------------------------------------------------------- 10 try
    b.append(h2("try", "Try it", toc))
    b.append('<ol class="steps">'
             '<li><b>Load the add-in</b>Fusion → Scripts and Add-ins → + → the <code>ArucoMarkers</code> folder, or '
             'the one-line loader below in Text Commands (Py).</li>'
             '<li><b>Make a tag</b>Sketch a rectangle → ArUco Tag → OK. Or pick a planar face of a body you modelled.</li>'
             '<li><b>Copy it</b>Put the part in an assembly and copy it; every copy shows the same marker.</li>'
             '<li><b>Serialise</b>ArUco IDs → pick the level → Number duplicates → OK. Repeat at the outer level.</li>'
             '<li><b>Export</b>ArUco Export → paper size → OK; the PDF opens.</li></ol>')
    b.append('<pre class="code"><code>%s</code></pre>' % esc(
        "import sys; sys.path.insert(0, r'%s\\ArucoMarkers'); import ArucoMarkers; ArucoMarkers.run(None)" % WORKTREE))
    b.append("<p>The exported demo robot in the viewer (running now at "
             '<a href="http://localhost:8093">http://localhost:8093</a>):</p>')
    b.append('<pre class="code"><code>%s</code></pre>' % esc(
        r"%USERPROFILE%\Documents\clank-viser\.venv\Scripts\python.exe viewer\bam_urdf_viewer.py "
        r"%USERPROFILE%\CADExporter\ArUco_Demo\urdf\untitled.urdf --port 8093"))
    b.append("<p>Files: <code>%USERPROFILE%\\CADExporter\\ArUco_Demo\\</code> (URDF and the aruco sidecar). Code: "
             "<code>CADExporter/bam_cad_core/markers.py</code> (records, resolve, plan_serials, cell boxes, sidecar), "
             "<code>ArucoMarkers/aruco_tags.py</code> (build, re-encode, variants), "
             "<code>ArucoMarkers/aruco_commands.py</code> (the three dialogs), "
             "<code>CADExporter/bam_cad_core/aruco_board.py</code> (board ink and frames). Commits on "
             "<code>%s</code>: <code>%s</code>, <code>%s</code>.</p>" % (B.BRANCH, *COMMITS))
    return page("\n".join(b), toc)


def main() -> int:
    html_ = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html_, encoding="utf-8")
    print("wrote %s (%s; inlined media %s)" % (OUT, B.fmt_bytes(OUT.stat().st_size), B.fmt_bytes(_BYTES[0])))
    if WARN:
        print("%d warning(s)" % len(WARN), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
