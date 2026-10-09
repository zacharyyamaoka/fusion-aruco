"""Offline tests for bam_cad_core.aruco_label -- ArUco Extrude's optional label
(Zach 2026-10-09: "optionally enable the label ... position it on the black
or white ... choose the size, although by default it will autocompute it ...
put id, put name, etc. Key thing is I want that label to update!
Particularly when we do a batch ArUco id set").

  * content: presets and tokens, per-marker ids, per-copy ids and names
    through markers.resolve / plan_serials, sized for the widest id
  * on the black: the border row's band (cap <= 0.5 module, 0.25 module of
    black kept, corner cells clear), grids each / first, the ChArUco square
  * on the white: beyond the quiet zone + 1 module on every side, auto side,
    auto size, a label strip, a face with holes
  * corner marks and locating pins kept clear; the safety check catches
    every rule broken on purpose
  * strokes and gaps >= the nozzle; deterministic records; relabel

Run:  python tests/test_aruco_label.py
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "CADExporter"))

from bam_cad_core import aruco_chamfer as C  # noqa: E402
from bam_cad_core import aruco_label as L  # noqa: E402
from bam_cad_core import aruco_slabs as SL  # noqa: E402
from bam_cad_core import markers as M  # noqa: E402

PASS = 0
FAIL = []
S = L.LabelSettings


def check(label, condition, detail=""):
    global PASS
    if condition:
        PASS += 1
        print(f"  ok  {label}")
    else:
        FAIL.append(label)
        print(f"FAIL  {label}  {detail}")


def close(a, b, tol=1e-6):
    if isinstance(a, (tuple, list)):
        return len(a) == len(b) and all(close(x, y, tol) for x, y in zip(a, b))
    return abs(a - b) <= tol


def circle(cx, cy, r, n=48, cw=False):
    pts = [(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n))
           for k in range(n)]
    return pts[::-1] if cw else pts


def inside_rect(r, outer, tol=1e-6):
    return (r[0] >= outer[0] - tol and r[1] >= outer[1] - tol and r[2] <= outer[2] + tol
            and r[3] <= outer[3] + tol)


def seg_seg(a, b, c, d):
    """Distance between segments ab and cd."""
    def cross(o, p, q):
        return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])
    d1, d2, d3, d4 = cross(c, d, a), cross(c, d, b), cross(a, b, c), cross(a, b, d)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and d1 * d2 < 0 and d3 * d4 < 0:
        return 0.0
    return min(L._seg_pt(a, c, d), L._seg_pt(b, c, d), L._seg_pt(c, a, b), L._seg_pt(d, a, b))


SINGLE = M.TagSpec(marker_id=12, marker_length_m=0.03)
SMALL = M.TagSpec(marker_id=12, marker_length_m=0.02)
GRID = M.TagSpec(marker_id=9, marker_length_m=0.03, layout="grid", rows=2, cols=3,
                 separation_m=0.006)
GRID31 = M.TagSpec(marker_id=9, marker_length_m=0.03, layout="grid", rows=1, cols=3,
                   separation_m=0.006)
CHARUCO = M.TagSpec(marker_id=0, marker_length_m=0.02775, layout="charuco", rows=3, cols=3,
                    square_length_m=0.037, quiet_modules=0.0)
CHARUCO54 = M.TagSpec(marker_id=0, marker_length_m=0.03, layout="charuco", rows=4, cols=5,
                      square_length_m=0.04, quiet_modules=0.0)
CHLEG = M.TagSpec(marker_id=0, marker_length_m=0.0225, layout="charuco", rows=4, cols=4,
                  square_length_m=0.030, quiet_modules=0.0, legacy=True)
TAGS = {"single": SINGLE, "grid 3x2": GRID, "charuco 3x3": CHARUCO}


def mod(spec):
    return spec.module_m * 1000.0


# ================================================================== the font
print("== the font")
for cap in (L.min_cap(), 3.3, 7.5):
    Mx = L.metrics(cap)
    for ch, (W, polys) in sorted(L.GLYPHS.items()):
        if ch == " ":
            continue
        bx = L.glyph_boxes(ch, Mx, 0.0, 0.0)
        r = L.ink_rect(bx)
        ys = [p[1] for pl in polys for p in pl]
        xs = [p[0] for pl in polys for p in pl]
        ok = r[1] >= -1e-9 and r[3] <= cap + 1e-9
        if min(ys) == 0 and max(ys) == 4:
            ok = ok and close(r[1], 0.0) and close(r[3], cap)
        if min(xs) == 0 and max(xs) == W:
            ok = ok and close(r[0], 0.0) and close(r[2], L.glyph_width(ch, Mx))
        if not ok:
            check(f"cap {cap}: {ch!r} ink inside its cell, exactly the cap", False, r)
            break
        if any(min(b.length, b.width) < Mx.stroke - 1e-9 for b in bx):
            check(f"cap {cap}: {ch!r} strokes >= the stroke width", False)
            break
    else:
        check(f"cap {cap:.2f}: every glyph's ink is its cell, cap tall exactly; strokes >= "
              f"{Mx.stroke:.2f} mm", True)

# the design rule: strokes that do not touch keep >= 2 grid units apart, except
# within 2 units of a joint they share through a third stroke (a wedge)
bad = []
for ch, (_W, polys) in sorted(L.GLYPHS.items()):
    segs = [s for pl in polys for s in (zip(pl, pl[1:]) if len(pl) > 1 else [(pl[0], pl[0])])]
    segs = [tuple(s) for s in segs]
    touch = [[seg_seg(*a, *b) < 1e-9 for b in segs] for a in segs]
    for i, A in enumerate(segs):
        for j, B in enumerate(segs):
            if i == j or touch[i][j]:
                continue
            joints = []
            for k, Cc in enumerate(segs):
                if touch[i][k] and touch[j][k] and k not in (i, j):
                    # where A meets C
                    for p in (Cc[0], Cc[1], A[0], A[1]):
                        if L._seg_pt(p, *A) < 1e-9 and L._seg_pt(p, *Cc) < 1e-9:
                            joints.append(p)
            for t in [k / 20.0 for k in range(21)]:
                p = (A[0][0] + (A[1][0] - A[0][0]) * t, A[0][1] + (A[1][1] - A[0][1]) * t)
                if L._seg_pt(p, *B) < 2.0 - 1e-9 and not any(math.dist(p, q) <= 2.0 + 1e-9
                                                             for q in joints):
                    bad.append((ch, A, B, p))
                    break
check("font: strokes that do not meet are >= 2 units apart (counters >= one stroke)",
      not bad, bad[:3])
check("min_cap: three bars and two gaps of a 0.45 nozzle = 2.25 mm",
      close(L.min_cap(0.45), 2.25) and close(L.min_cap(0.25), 1.25))
Mn = L.metrics(L.min_cap())
check("at the smallest cap: stroke = counters = spacing = the nozzle",
      close(Mn.stroke, 0.45) and close(Mn.gap, 0.45) and close(Mn.space, 0.45))
check("a typed 0.6 stroke: min cap 3 x 0.6 + 2 x 0.45 = 2.7, its counters = the nozzle",
      close(L.min_cap(0.45, 0.6), 2.7) and close(L.metrics(2.7, 0.45, 0.6).gap, 0.45))
check("stroke never under the nozzle (typed 0.3 at a 0.45 nozzle -> 0.45)",
      close(L.stroke_for(5.0, 0.45, 0.3), 0.45) and close(L.stroke_for(3.0, 0.45), 0.45)
      and close(L.stroke_for(10.0, 0.45), 1.3))
worst = min(L.metrics(c / 10.0, n).min_feature - n
            for n in (0.25, 0.4, 0.45, 0.6) for c in range(int(L.min_cap(n) * 10 + 1), 300))
check("every feature >= the nozzle from the smallest cap to 30 mm, nozzles 0.25..0.6",
      worst >= -1e-9, worst)
check("normalize: upper-case, one space, unknown -> '?'",
      L.normalize("  arm  2 é") == ("ARM 2 ?", ["É"]))
b = L.Box(1.0, 2.0, 4.0, 1.0, 30.0)
pts = b.corners()
area = sum(pts[i - 1][0] * pts[i][1] - pts[i][0] * pts[i - 1][1] for i in range(4)) / 2.0
check("Box corners: counter-clockwise, length x width", close(area, 4.0), area)
Mx = L.metrics(3.0)
flat = L.block_boxes(["#12"], Mx)
up = L.block_boxes(["#12"], Mx, (0.0, 0.0), 90)
down = L.block_boxes(["#12"], Mx, (0.0, 0.0), -90)
ru, rd, rf = L.ink_rect(up), L.ink_rect(down), L.ink_rect(flat)
check("turned blocks: sides swapped", close(ru[2] - ru[0], rf[3] - rf[1])
      and close(ru[3] - ru[1], rf[2] - rf[0]) and close(rd[3] - rd[1], rf[2] - rf[0]))
# '-' first: its bar sits at mid-height (u = 0 once turned); +90 puts the first
# character lowest (reads upward), -90 highest (reads downward)
up2 = L.block_boxes(["-1"], Mx, (0.0, 0.0), 90)
down2 = L.block_boxes(["-1"], Mx, (0.0, 0.0), -90)
check("+90 reads upward, -90 reads downward",
      abs(min(up2, key=lambda x: x.cy).cx) < 1e-9 and abs(max(down2, key=lambda x: x.cy).cx) < 1e-9)
check("axis-aligned strokes stay angle 0 after a quarter turn",
      sum(1 for x in up if x.angle == 0.0) == sum(1 for x in flat if x.angle == 0.0))

# ================================================================== content
print("== content")
check("#id", L.resolve_text("#{id}", SINGLE) == "#12")
check("a board's own label: its id block", L.resolve_text("#{id}", GRID) == "#9-14")
check("a per-marker label: that marker's id; {ids} stays the block",
      L.resolve_text("#{id} of {ids}", GRID, marker_id=10) == "#10 of 9-14")
check("dict_id preset", L.resolve_text(L.PRESETS["dict_id"], SINGLE) == "4X4_50 #12")
check("{size} {key}", L.resolve_text("{size}mm {key}", M.TagSpec(marker_length_m=0.0275),
                                     key="top") == "27.5mm top")
check("an empty {name} leaves no trailing space",
      L.resolve_text(L.PRESETS["id_name"], SINGLE, name="") == "#12")
check("'|' breaks lines, spaces collapse",
      L.resolve_text("#{id} | {name}", SINGLE, name="Arm  2") == "#12|Arm 2")
check("sizing: ids padded to the dictionary's widest",
      L.resolve_text("#{id}", M.TagSpec(marker_id=9), sizing=True) == "#09"
      and L.resolve_text("#{id}", M.TagSpec(marker_id=9, dictionary="DICT_4X4_1000"),
                         sizing=True) == "#009"
      and L.resolve_text("#{id}", GRID, sizing=True) == "#09-14")
check("sizing: a component's name gets an occurrence number, an occurrence's does not",
      L.resolve_text("{name}", SINGLE, name="Actuator", sizing=True) == "Actuator:0"
      and L.resolve_text("{name}", SINGLE, name="Arm:2/Actuator:3", sizing=True)
      == "Arm:2/Actuator:3")
check("unknown tokens are kept and listed",
      L.resolve_text("{idd}", SINGLE) == "{idd}" and L.unknown_tokens("{idd} {id}") == ["idd"])
check("part_name: the last two path elements",
      L.part_name("Robot:1+Arm:2+Actuator:3") == "Arm:2/Actuator:3" and L.part_name("") == "")
check("name_variants: the full name, then its last element",
      L.name_variants("Arm:2/Actuator:3") == ["Arm:2/Actuator:3", "Actuator:3"])
check("wraps: one line first, then balanced lines",
      L.wraps("#12 ARM:2 ACT:3", 3) == [["#12 ARM:2 ACT:3"], ["#12 ARM:2", "ACT:3"],
                                         ["#12", "ARM:2", "ACT:3"]])
check("wraps: '|' is a line break where lines fit, a space where they do not",
      L.wraps("#12|ARM", 3)[0] == ["#12", "ARM"] and L.wraps("#12|ARM", 1) == [["#12 ARM"]])
lab = L.plan_label(SINGLE, S(on=True, content="custom", text="#{id} {oops}"), grow=True)
check("plan: an unknown token is a note", any("unknown {oops}" in n for n in lab.notes),
      lab.notes)
lab = L.plan_label(SINGLE, S(on=True, place="white", content="name"), name="Ärm", grow=True)
check("plan: a character the font lacks prints '?', with a note",
      lab.texts() == ["?RM"] and any("no 'Ä'" in n for n in lab.notes), (lab.texts(), lab.notes))
lab = L.plan_label(SINGLE, S(on=True, content="name"), name="")
check("plan: an empty text is no label, with a note",
      lab.kind == "none" and any("empty" in n for n in lab.notes))

print("== per-copy ids and names (markers.resolve / plan_serials)")
tagdef = M.TagDef(key="top", component="Actuator",
                  spec=M.TagSpec(marker_id=7, marker_length_m=0.03))


def T(x, y, z):
    return [1, 0, 0, x, 0, 1, 0, y, 0, 0, 1, z, 0, 0, 0, 1]


occs = []
for a in (1, 2):
    occs.append(M.OccurrenceRef(f"Arm:{a}", "Arm", T(100 * a, 0, 0)))
    for k in range(1, 4):
        occs.append(M.OccurrenceRef(f"Arm:{a}+Actuator:{k}", "Actuator", T(100 * a, 10 * k, 0)))
planned, res = M.plan_serials([tagdef], [], occs, "Robot")
check("batch ids: six copies, six ids", len({i.spec.marker_id for i in res.instances}) == 6,
      [i.spec.marker_id for i in res.instances])
tpl = L.plan_label(tagdef.spec, S(on=True, place="black"), name="Actuator")
tplw = L.plan_label(tagdef.spec, S(on=True, place="white", content="id_name"), name="Actuator",
                    grow=True)
rec, recw = tpl.to_dict(), tplw.to_dict()
for inst in res.instances:
    lab = L.label_for_instance(rec, inst)
    labw = L.label_for_instance(recw, inst)
    want_name = L.instance_name(inst)
    check(f"{inst.path}: #{inst.spec.marker_id} on the black, '{want_name}' on the white",
          lab.texts() == [f"#{inst.spec.marker_id}"]
          and labw.texts() == [f"#{inst.spec.marker_id}|" + L.normalize(want_name)[0]]
          and not L.check_label(inst.spec, lab) and not L.check_label(inst.spec, labw),
          (lab.texts(), labw.texts()))
names = {i.path: L.instance_name(i) for i in res.instances}
check("the template's copy (keeps its id) says the component's name",
      names["Arm:1+Actuator:1"] == "Actuator", names)
check("a copy the arm re-serialled says its occurrence below the arm",
      names["Arm:1+Actuator:2"] == "Actuator:2", names)
check("a copy the robot re-serialled says its path below the root",
      names["Arm:2+Actuator:1"] == "Arm:2/Actuator:1", names)
caps = {round(L.label_for_instance(rec, i).cap, 6) for i in res.instances}
capsw = {round(L.label_for_instance(recw, i).cap, 6) for i in res.instances
         if ":" in L.instance_name(i).split("/")[-1] and "/" not in L.instance_name(i)}
check("every copy prints at the template's cap (ids sized at their widest)",
      caps == {round(tpl.cap, 6)}, caps)
check("... and names sized with an occurrence number ('Actuator:0')",
      capsw == {round(tplw.cap, 6)}, (capsw, tplw.cap))
long_copy = L.relabel(recw, M.TagSpec(marker_id=31, marker_length_m=0.03),
                      name="Robot:1/Arm:2/Actuator:3")
check("a much longer copy name shrinks inside the slot, never out of it",
      long_copy.ok and inside_rect(long_copy.parts[0].rect, tplw.slots[0].rect)
      and not L.check_label(M.TagSpec(marker_id=31, marker_length_m=0.03), long_copy),
      long_copy.notes)

# ================================================================= the black
print("== on the black")
m = mod(SINGLE)
lab = L.plan_label(SINGLE, S(on=True, place="black"))
x0, y0, x1, y1 = -15.0, -15.0, 15.0, 15.0
r = lab.parts[0].rect
check("single: '#12' in the bottom row (row auto = both rows; '#id' has one line)",
      lab.kind == "black" and lab.texts() == ["#12"] and lab.role == "cut"
      and lab.side == "both" and [sl.where for sl in lab.slots] == ["bottom"])
check("... cap 0.5 module (2.5 mm), the largest the row allows", close(lab.cap, 2.5))
check("... 0.25 module of black above and below it",
      r[1] >= y0 + 0.25 * m - 1e-9 and r[3] <= y0 + 0.75 * m + 1e-9, r)
check("... centred, clear of the corner cells by 0.25 module",
      close((r[0] + r[2]) / 2.0, 0.0, 1e-6) and r[0] >= x0 + 1.25 * m - 1e-9
      and r[2] <= x1 - 1.25 * m + 1e-9, r)
check("... strokes = the 0.45 nozzle, min feature 0.45", close(lab.stroke, 0.45)
      and close(lab.min_feature, 0.45))
check("... safe", L.check_label(SINGLE, lab) == [])
lab = L.plan_label(SINGLE, S(on=True, place="black", row="top", content="name"), name="Arm:2")
r = lab.parts[0].rect
check("top row: in the band below the top edge", lab.side == "top"
      and r[3] <= y1 - 0.25 * m + 1e-9 and r[1] >= y1 - 0.75 * m - 1e-9
      and not L.check_label(SINGLE, lab), r)
for size in (25.0, 27.0, 30.0, 40.0, 60.0):
    for d in ("DICT_4X4_50", "DICT_5X5_100", "DICT_6X6_250"):
        sp = M.TagSpec(marker_id=12, marker_length_m=size / 1000.0, dictionary=d)
        lab = L.plan_label(sp, S(on=True, place="black"))
        mm = mod(sp)
        good = (lab.ok and not L.check_label(sp, lab) and lab.min_feature >= 0.45 - 1e-9
                and lab.cap <= 0.5 * mm + 1e-9)
        refused = lab.kind == "none" and any("does not print" in n for n in lab.notes)
        expect = 0.5 * mm >= L.min_cap() - 1e-9
        check(f"{d} {size:g} mm (module {mm:.2f}): "
              + ("prints, inside the rules" if expect else "refused, saying why"),
              good if expect else refused, (lab.kind, lab.cap, lab.notes))
lab = L.plan_label(SMALL, S(on=True, place="black", nozzle_mm=0.25))
check("20 mm with a 0.25 nozzle: prints (cap 1.6 mm, stroke 0.25)",
      lab.ok and close(lab.cap, 1.6) and close(lab.stroke, 0.25)
      and not L.check_label(SMALL, lab), (lab.cap, lab.notes))
row = 30.0 - 2.5 * m
tail = next("8" * k + ":" * j for k in range(1, 12) for j in range(4)
            if L.line_width("#12 " + "8" * k + ":" * j, L.metrics(2.5)) > row
            and L.line_width("#12 " + "8" * k + ":" * j, L.metrics(L.min_cap())) <= row)
lab = L.plan_label(SINGLE, S(on=True, place="black", content="custom", text="#{id} " + tail))
check(f"a longer text ('#12 {tail}': {L.line_width('#12 ' + tail, L.metrics(2.5)):.1f} mm at "
      f"full size, the row {row:.1f}) shrinks to fit, never taller",
      lab.ok and lab.cap < 2.5 and lab.cap >= L.min_cap()
      and lab.parts[0].rect[2] - lab.parts[0].rect[0] <= row + 1e-9
      and not L.check_label(SINGLE, lab), (lab.cap, lab.notes))
check("a text with an id in it that does not fit: refused (an id is never cut)",
      L.plan_label(SINGLE, S(on=True, place="black", row="bottom", content="custom",
                             text="#{id} #{id} #{id} #{id} #{id}")).kind == "none")
lab = L.plan_label(SINGLE, S(on=True, place="black", cap_mm=4.0))
check("typed cap 4 mm: clamped to 0.5 module, with a note",
      close(lab.cap, 2.5) and any("clamped" in n for n in lab.notes), lab.notes)
lab = L.plan_label(SINGLE, S(on=True, place="black", cap_mm=2.3))
check("typed cap 2.3 mm: as typed (not rounded)", close(lab.cap, 2.3), lab.cap)
lab = L.plan_label(SINGLE, S(on=True, place="black", cap_mm=2.0))
check("typed cap 2.0 mm (under 5 nozzles): refused", lab.kind == "none", lab.notes)

lab = L.plan_label(GRID, S(on=True, place="black"))
sq = L.marker_squares(GRID)
check("grid each: one part per marker, its own id",
      lab.texts() == ["#9", "#10", "#11", "#12", "#13", "#14"] and lab.ok, lab.texts())
check("... each in its own marker's bottom row",
      all(inside_rect(p.rect, (sq[i][1][0] + mod(GRID), sq[i][1][1],
                               sq[i][1][2] - mod(GRID), sq[i][1][1] + mod(GRID)))
          for i, p in enumerate(lab.parts)) and not L.check_label(GRID, lab))
lab = L.plan_label(GRID, S(on=True, place="black", scope="first"))
check("grid first: one part, the block, in the top-left marker",
      lab.texts() == ["#9-14"] and lab.parts[0].rect[0] < sq[0][1][2]
      and lab.parts[0].rect[3] < sq[0][1][3] and not L.check_label(GRID, lab), lab.texts())
lab = L.plan_label(GRID, S(on=True, place="black", row="square"))
check("grid 'square' is a ChArUco option: both rows (each marker's id), with a note",
      len(lab.parts) == 6 and lab.side == "both" and any("ChArUco" in n for n in lab.notes))
lab = L.plan_label(GRID, S.from_dict({"on": True, "place": "black", "scope": "square"}))
check("a phase 1 record's scope 'square' reads as row 'square'",
      S.from_dict({"scope": "square"}).row == "square"
      and S.from_dict({"scope": "square"}).scope == "auto")

for name, spec in (("3x3 37 mm", CHARUCO), ("5x4 40 mm", CHARUCO54)):
    mk = C.mark_for_spec(spec, "both", "new")
    lab = L.plan_label(spec, S(on=True, place="black"), mark=mk)
    Sq = spec.square_length_m * 1000.0
    sqr = L.charuco_square(spec)
    corners = L.charuco_corners_mm(spec)
    r = lab.parts[0].rect if lab.parts else None
    check(f"ChArUco {name}: the board label in the top-left black square",
          lab.ok and lab.side == "square" and lab.texts() == [f"#{spec.ids[0]}-{spec.ids[-1]}"]
          and inside_rect(r, sqr), (lab.texts(), r, lab.notes))
    check(f"ChArUco {name}: >= 0.35 square from every ChArUco corner, clear of the cut corner",
          min(L.rect_point_dist(r, c) for c in corners) >= 0.35 * Sq - 1e-6
          and L.poly_dist(L.rect_poly(r), mk.cut) >= 1.0, (min(L.rect_point_dist(r, c)
                                                                for c in corners) / Sq))
    check(f"ChArUco {name}: cap <= 0.22 square, safe", lab.cap <= 0.22 * Sq + 1e-9
          and not L.check_label(spec, lab))
lab = L.plan_label(CHARUCO, S(on=True, place="black", content="id_name"), name="Plate")
check("ChArUco square: '#id name' wraps into lines when that is bigger",
      lab.ok and len(lab.parts[0].lines) == 2 and not L.check_label(CHARUCO, lab),
      lab.parts[0].lines if lab.parts else lab.notes)
lab = L.plan_label(CHARUCO54, S(on=True, place="black", row="bottom", scope="each"))
check("ChArUco each: every marker's row, its own id",
      lab.ok and lab.texts() == [f"#{i}" for i in CHARUCO54.ids]
      and not L.check_label(CHARUCO54, lab), (lab.texts(), lab.notes))
sqr = L.charuco_square(CHLEG)
check("legacy board, white top-left: the square next to it (the first black one)",
      close(sqr[0], -CHLEG.extent_m[0] * 500.0 + CHLEG.square_length_m * 1000.0))
lab = L.plan_label(CHLEG, S(on=True, place="black"))
check("... the label sits in it, safe", lab.ok and inside_rect(lab.parts[0].rect, sqr)
      and not L.check_label(CHLEG, lab), lab.notes)

# ================================================================= the white
print("== on the white")
for name, spec in TAGS.items():
    mm = mod(spec)
    R = L.keepout_rect(spec)
    for side in ("below", "above", "left", "right"):
        lab = L.plan_label(spec, S(on=True, place="white", side=side, content="id_name"),
                           name="Actuator:3", grow=True)
        r = lab.parts[0].rect if lab.parts else None
        beyond = {"below": r and r[3] <= R[1] + 1e-9, "above": r and r[1] >= R[3] - 1e-9,
                  "left": r and r[2] <= R[0] + 1e-9, "right": r and r[0] >= R[2] - 1e-9}[side]
        w = lab.white
        st = lab.strip
        on_side = st is not None and {"below": close(st[3], L.sticker_rect(spec)[1]),
                                      "above": close(st[1], L.sticker_rect(spec)[3]),
                                      "left": close(st[2], L.sticker_rect(spec)[0]),
                                      "right": close(st[0], L.sticker_rect(spec)[2])}[side]
        e = max(L.WHITE_EDGE * mm, L.EDGE_MIN_MM)
        check(f"{name}, {side}: beyond the quiet zone + 1 module, on a strip on that side, "
              f"{e:.2f} mm inside it, rot {L.SIDE_ROT[side]}",
              lab.ok and beyond and on_side and inside_rect(L.grow_rect(r, e), w)
              and lab.slots[0].rot == L.SIDE_ROT[side] and not L.check_label(spec, lab),
              (lab.notes, r, R, w))
lab = L.plan_label(SINGLE, S(on=True, place="white"), grow=True)
check("auto side on a square sticker: below (the tie order)", lab.side == "below")
check("auto cap: the most the white allows, 1.5 modules, on the 0.1 mm grid",
      close(lab.cap, 7.5) and close(lab.cap * 10, round(lab.cap * 10)), lab.cap)
one_line = dict(content="custom", text="#{id} {name}")
lab = L.plan_label(SINGLE, S(on=True, place="white", **one_line), name="Actuator:3",
                   grow=True)
check("a longer one-line text: one line while its cap reaches 0.5 module", lab.ok
      and len(lab.parts[0].lines) == 1 and lab.cap >= 0.5 * mod(SINGLE) - 1e-9
      and close(lab.cap * 10, round(lab.cap * 10)), (lab.parts[0].lines, lab.cap))
lab = L.plan_label(SINGLE, S(on=True, place="white", **one_line),
                   name="Robot:1/Arm:2/Actuator:3", grow=True)
check("... and wraps when one line would be smaller than that (a path breaks after '/')",
      lab.ok and len(lab.parts[0].lines) >= 2 and lab.parts[0].lines[0].endswith("/")
      and lab.cap >= 0.5 * mod(SINGLE) - 1e-9, (lab.parts[0].lines, lab.cap))
lab = L.plan_label(SINGLE, S(on=True, place="white", cap_mm=9.0), grow=True)
check("typed cap 9 mm on the white: honoured (up to 4 modules)", close(lab.cap, 9.0), lab.cap)
lab = L.plan_label(GRID31, S(on=True, place="white", cap_mm=30.0), grow=True)
check("typed cap 30 mm: clamped to 4 modules (20 mm), with a note",
      close(lab.cap, 20.0) and any("clamped" in n for n in lab.notes), (lab.cap, lab.notes))
lab = L.plan_label(SINGLE, S(on=True, place="white", cap_mm=20.0), grow=True)
check("typed cap 20 mm on a 40 mm sticker: shrunk to the strip's width, with a note",
      lab.ok and lab.cap < 20.0 and any("shrunk" in n for n in lab.notes)
      and lab.parts[0].rect[2] - lab.parts[0].rect[0] <= 40.0 + 1e-9, (lab.cap, lab.notes))
lab = L.plan_label(SMALL, S(on=True), grow=True)
check("place auto, 20 mm: not on the black (says why), on the white",
      lab.kind == "white" and any("not on the black" in n for n in lab.notes))
lab = L.plan_label(SINGLE, S(on=True))
check("place auto, 30 mm: on the black", lab.kind == "black")
lab = L.plan_label(SMALL, S(on=True, place="white"))
check("on the white with no white (no region, no strip): none, saying why",
      lab.kind == "none" and any("no white" in n for n in lab.notes))
lab = L.plan_label(GRID31, S(on=True, place="white"), grow=True)
check("grid 3x1: one label for the board, its block, below (the long side)",
      lab.texts() == ["#9-11"] and lab.side == "below")
lab = L.plan_label(SINGLE, S(on=True, place="white", side="above"), mode="white", op=None,
                   grow=True)
check("White only: the white pattern grows by the strip; the text joins the black "
      "footprint (a notch in the white)", lab.ok and lab.role == "add" and lab.white
      and lab.white[3] > L.sticker_rect(SINGLE)[3])
face = [[(-60.0, -60.0), (60.0, -60.0), (60.0, 60.0), (-60.0, 60.0)]]
lab = L.plan_label(SINGLE, S(on=True, place="white"), grow=True,
                   bounds=[[(-25.0, -30.0), (25.0, -30.0), (25.0, 25.0), (-25.0, 25.0)]])
check("a strip inside a tight face: shrunk to stay on it",
      lab.ok and lab.white[1] >= -30.0 - 1e-9 and lab.cap < 7.5, (lab.white, lab.cap))
lab = L.plan_label(SINGLE, S(on=True, place="white", side="below"), grow=True,
                   bounds=[[(-25.0, -25.5), (25.0, -25.5), (25.0, 25.0), (-25.0, 25.0)]])
check("... and none when the face leaves no room on that side",
      lab.kind == "none" and any("no room" in n for n in lab.notes), lab.notes)
for mode, op, hb, big, want in (("both", "new", True, False, (True, None, "face")),
                                ("both", "join", True, True, (False, "tile", None)),
                                ("both", "new", False, False, (True, None, None)),
                                ("black", None, True, False, (False, "face", None)),
                                ("black", None, False, False, (False, None, None)),
                                ("white", None, True, False, (True, None, "face"))):
    w = L.white_room(mode, op, hb, big)
    check(f"white_room {mode}/{op} body={hb} big tile={big}: {want}",
          (w["grow"], w["region"], w["bounds"]) == want, w)

print("== a face with holes (Black only)")
disc = [circle(0, 0, 40), circle(0, -26, 4, cw=True), circle(26, 0, 4, cw=True),
        circle(-26, 0, 4, cw=True), circle(0, 26, 4, cw=True)]
holes = disc[1:]
for side in L.SIDES:
    t0 = time.perf_counter()
    lab = L.plan_label(SINGLE, S(on=True, place="white", side=side), mode="black", op=None,
                       region=disc)
    dt = time.perf_counter() - t0
    e = max(L.WHITE_EDGE * mod(SINGLE), L.EDGE_MIN_MM)
    polys = lab.polygons()
    on_face = all(math.hypot(*p) <= 40.0 - e + 1e-6 for q in polys for p in q)
    off_holes = all(L.poly_dist(q, h) >= e - 1e-6 for q in polys for h in holes)
    check(f"disc, side {side}: on the face, {e:.2f} mm off every hole, safe ({dt * 1000:.0f} ms)",
          lab.ok and on_face and off_holes and not L.check_label(SINGLE, lab) and dt < 1.0,
          (lab.notes, lab.side))
lab = L.plan_label(SINGLE, S(on=True, place="white", side="below"), mode="black", op=None,
                   region=disc)
check("... below: slid along the side past the hole under the tag",
      abs((lab.slots[0].rect[0] + lab.slots[0].rect[2]) / 2.0) > 4.0, lab.slots[0].rect)
plate = [[(-25, -60), (25, -60), (25, 25), (-25, 25)], circle(0, -30, 5, cw=True)]
lab = L.plan_label(SINGLE, S(on=True, place="white"), mode="black", op=None, region=plate)
check("a 50 x 85 plate, a hole right under the tag: auto finds the room below, past the hole",
      lab.ok and lab.side == "below" and lab.parts[0].rect[3] < -35.0 - 1e-9
      and not L.check_label(SINGLE, lab), (lab.side, lab.parts and lab.parts[0].rect))
lab = L.plan_label(SINGLE, S(on=True, place="white"), mode="black", op=None,
                   region=[[(-22, -22), (22, -22), (22, 22), (-22, 22)]])
check("a face just bigger than the sticker: no room, saying why",
      lab.kind == "none" and any("no room on the white around it" in n for n in lab.notes))
wide = [[(-25, -25), (70, -25), (70, 25), (-25, 25)]]
lab = L.plan_label(SINGLE, S(on=True, place="white"), mode="black", op=None, region=wide)
check("auto side: the side with the most room (right, on a plate that runs right)",
      lab.side == "right", lab.side)
big = (-35.0, -35.0, 35.0, 30.0)
lab = L.plan_label(SINGLE, S(on=True, place="white"), region=[L.rect_loop(big)], tile=big)
check("a sketch-area tile bigger than the sticker: fitted inside it, the tile unchanged",
      lab.ok and lab.white is None and inside_rect(lab.parts[0].rect, big))

# ============================================================ marks and pins
print("== corner marks and pins")
mk = C.mark_for_spec(SINGLE, "both", "new")
for side in ("above", "left"):
    lab = L.plan_label(SINGLE, S(on=True, place="white", side=side, content="custom",
                                 text="#{id} ACTUATOR:3 LEFT"), grow=True, mark=mk)
    gm = L.mark_on_tile(SINGLE, "both", "new", lab.white)
    clear = max(L.KEEPOUT_MM, L.KEEPOUT * mod(SINGLE))
    d = min(L.poly_dist(q, gm.cut) for q in lab.polygons())
    check(f"strip {side} + cut corner: the cut moves to the grown tile's corner, the label "
          f"keeps {clear:.2f} mm off it ({d:.2f})",
          gm.kind == "tile_cut" and close(gm.corner, (lab.white[0], lab.white[3]))
          and d >= clear - 1e-6 and close(abs(C.area2(gm.outline)) / 2.0,
                                           (lab.white[2] - lab.white[0])
                                           * (lab.white[3] - lab.white[1]) - gm.leg ** 2 / 2.0),
          (gm.corner, lab.white, d))
pins = {"shape": "round", "size": 3.0}
for side in ("above", "below", "left", "right"):
    lab = L.plan_label(SINGLE, S(on=True, place="white", side=side, content="custom",
                                 text="#{id} ACTUATOR:3 LEFT"), grow=True, mark=mk, pins=pins)
    w = lab.white
    gm = L.mark_on_tile(SINGLE, "both", "new", w)
    outs = SL.pin_outlines("round", 3.0, w[2] - w[0], w[3] - w[1], cut=gm.leg)
    polys = L.pin_keepouts(outs, ((w[0] + w[2]) / 2.0, (w[1] + w[3]) / 2.0))
    d = min(L.poly_dist(q, pp) for q in lab.polygons() for pp in polys)
    check(f"strip {side} + round pins on the grown tile: the label keeps clear ({d:.2f} mm)",
          lab.ok and d >= max(L.KEEPOUT_MM, L.KEEPOUT * mod(SINGLE)) - 1e-6, (lab.notes, d))
# a long word cannot wrap: at full width it would reach into the cut corner
# and over the pins of a strip ABOVE (they move to the grown tile's top)
word = S(on=True, place="white", side="above", content="custom", text="#{id}_ACTUATOR_3_LEFT")
free = L.plan_label(SINGLE, word, grow=True)
e = max(L.WHITE_EDGE * mod(SINGLE), L.EDGE_MIN_MM)
clear = max(L.KEEPOUT_MM, L.KEEPOUT * mod(SINGLE))
check("a long word in a strip above, no mark: as wide as the strip allows",
      free.ok and close(free.parts[0].rect[2] - free.parts[0].rect[0], 40.0 - 2 * e, 0.6),
      free.parts and free.parts[0].rect)
for what, kw in (("the cut corner", dict(mark=mk)), ("round pins", dict(pins=pins)),
                 ("both", dict(mark=mk, pins=pins))):
    lab = L.plan_label(SINGLE, word, grow=True, **kw)
    w = lab.white
    polys = []
    cut = 0.0
    if "mark" in kw:
        gm = L.mark_on_tile(SINGLE, "both", "new", w)
        polys += [gm.cut]
        cut = gm.leg
    if "pins" in kw:
        polys += L.pin_keepouts(SL.pin_outlines("round", 3.0, w[2] - w[0], w[3] - w[1], cut=cut),
                                ((w[0] + w[2]) / 2.0, (w[1] + w[3]) / 2.0))
    d = min(L.poly_dist(L.rect_poly(lab.parts[0].rect), q) for q in polys)
    deeper = w[3] - free.white[3]
    check(f"... with {what}: the strip grows {deeper:.2f} mm deeper so the text keeps its size "
          f"and {clear:.2f} mm off it ({d:.2f})",
          lab.ok and close(lab.cap, free.cap) and deeper > 0 and d >= clear - 1e-6
          and deeper <= L.REACH * mod(SINGLE) + 1e-9, (lab.cap, free.cap, d, deeper))
word2 = S(on=True, place="white", side="above", content="custom", text="#{id}_ACTUATOR_3")
free2 = L.plan_label(SINGLE, word2, grow=True)
top = free2.white[3] + 0.5
lab = L.plan_label(SINGLE, word2, grow=True, mark=mk, pins=pins,
                   bounds=[[(-30.0, -30.0), (30.0, -30.0), (30.0, top), (-30.0, top)]])
w = lab.white or (0, 0, 0, 0)
gm = L.mark_on_tile(SINGLE, "both", "new", w) if lab.ok else None
polys = ([gm.cut] + L.pin_keepouts(SL.pin_outlines("round", 3.0, w[2] - w[0], w[3] - w[1],
                                                   cut=gm.leg),
                                   ((w[0] + w[2]) / 2.0, (w[1] + w[3]) / 2.0))) if lab.ok else []
d = min((L.poly_dist(L.rect_poly(lab.parts[0].rect), q) for q in polys), default=0.0)
check(f"... and when the face stops the strip from growing, the text shrinks instead "
      f"({free2.cap:g} -> {lab.cap:g} mm, {d:.2f} mm clear)",
      lab.ok and lab.cap < free2.cap - 1e-9 and d >= clear - 1e-6 and w[3] <= top + 1e-9,
      (lab.ok, lab.cap, lab.notes))
lab = L.plan_label(SINGLE, word, grow=True, mark=mk, pins=pins,
                   bounds=[[(-30.0, -30.0), (30.0, -30.0), (30.0, free.white[3] + 0.5),
                            (-30.0, free.white[3] + 0.5)]])
check("... or, when even the smallest cap would not clear them, there is no label (says why)",
      lab.kind == "none" and any("no room on a label strip" in n for n in lab.notes), lab.notes)
tri = C.mark_for_spec(SINGLE, "black", None)
plate2 = [[(-40, -24), (40, -24), (40, 24), (-40, 24)]]
for side in ("left", "right", "auto"):
    lab = L.plan_label(SINGLE, S(on=True, place="white", side=side, content="dict_id"),
                       mode="black", op=None, region=plate2, mark=tri)
    d = min(L.poly_dist(q, tri.solid) for q in lab.polygons()) if lab.parts else 0
    check(f"Black only, corner triangle, side {side}: clear of it ({d:.2f} mm, side "
          f"{lab.side})", lab.ok and d >= L.KEEPOUT_MM - 1e-6, (lab.notes, d))
lab = L.plan_label(SINGLE, S(on=True, place="white", side="above"), mode="black", op=None,
                   region=plate2, mark=tri)
check("... above: the plate ends 9 mm over the black, no room (the ring + gap is 10)",
      lab.kind == "none", lab.notes)
block = [[(-30.0, -36.0), (30.0, -36.0), (30.0, -24.0), (-30.0, -24.0)]]
roomy = [[(-80, -80), (80, -80), (80, 80), (-80, 80)]]
lab = L.plan_label(SINGLE, S(on=True, place="white", side="below"), mode="black", op=None,
                   region=roomy, keepouts=block)
d = L.poly_dist(L.rect_poly(lab.parts[0].rect), block[0]) if lab.parts else 0
check(f"a keep-out right below the tag: the label moves off it ({d:.2f} mm clear)",
      lab.ok and d >= max(L.KEEPOUT_MM, L.KEEPOUT * mod(SINGLE)) - 1e-6
      and not L.check_label(SINGLE, lab), (lab.parts and lab.parts[0].rect))
narrow = [[(-25, -80), (25, -80), (25, 25), (-25, 25)]]
lab = L.plan_label(SINGLE, S(on=True, place="white", side="below"), mode="black", op=None,
                   region=narrow, keepouts=block)
check("... where it cannot slide past (a 50 mm plate), it goes out below the keep-out",
      lab.ok and lab.parts[0].rect[3] <= -36.0 - L.KEEPOUT_MM + 1e-6,
      lab.parts and lab.parts[0].rect)
deep = [[(-30.0, -60.0), (30.0, -60.0), (30.0, -24.0), (-30.0, -24.0)]]
lab = L.plan_label(SINGLE, S(on=True, place="white", side="below"), mode="black", op=None,
                   region=narrow, keepouts=deep)
check(f"... but never more than {L.REACH:g} modules further out: none, saying why",
      lab.kind == "none" and any("no room" in n for n in lab.notes), lab.notes)
over_row = [L.rect_poly((-5, -15, 5, -12))]
lab = L.plan_label(SINGLE, S(on=True, place="black"), keepouts=over_row)
check("a keep-out over the bottom row: on the black, that row is skipped, saying why",
      lab.kind == "none" and not lab.parts and any("skipped" in n for n in lab.notes))
lab = L.plan_label(SINGLE, S(on=True), keepouts=over_row, grow=True)
check("... place auto: on the white instead", lab.kind == "white" and lab.ok
      and not L.check_label(SINGLE, lab))
lab = L.plan_label(GRID, S(on=True, place="black", cap_mm=2.4))
check("a board's markers say a note once", len(lab.notes) == len(set(lab.notes)), lab.notes)
lab = L.plan_label(SINGLE, S(on=True, place="black"), pins=pins, mark=mk)
check("pins under a sticker never reach the border row", lab.ok)

# ============================================== one label, position apart
print("== one label, its position apart (any content in any position)")
SINGLE40 = M.TagSpec(marker_id=12, marker_length_m=0.04)
SINGLE60 = M.TagSpec(marker_id=12, marker_length_m=0.06)
check("positions: three rows and the white's sides; ChArUco adds its square",
      L.positions("single") == [("black", "bottom"), ("black", "top"), ("black", "both")]
      + [("white", sd) for sd in L.SIDES]
      and ("black", "square") in L.positions("charuco")
      and ("black", "square") not in L.positions("grid"))
check("row auto: both rows; a ChArUco board's square",
      S().black_row("single") == "both" and S().black_row("grid") == "both"
      and S().black_row("charuco") == "square" and S(row="square").black_row("grid") == "both")
CONTENT = [("id", dict(content="id")), ("name", dict(content="name")),
           ("id_name", dict(content="id_name")),
           ("custom", dict(content="custom", text="#{id} {name}|{dict}"))]
for sname, spec in (("30 mm", SINGLE), ("40 mm", SINGLE40), ("grid 3x1", GRID31),
                    ("ChArUco 3x3", CHARUCO)):
    for place, where in L.positions(spec.layout):
        pos = dict(place=place, row=where) if place == "black" else dict(place=place, side=where)
        bad = []
        for cname, ckw in CONTENT:
            lab = L.plan_label(spec, S(on=True, **pos, **ckw), name="Actuator:2", grow=True)
            if lab.kind == "none":
                if not lab.notes:
                    bad.append((cname, "refused silently"))
                continue
            probs = L.check_label(spec, lab)
            if probs or not lab.ok or lab.min_feature < 0.45 - 1e-9:
                bad.append((cname, probs, lab.notes))
            if place == "black" and lab.kind != "black":
                bad.append((cname, "moved off the black"))
        check(f"{sname}, {place} {where}: every content placed by the rules (or refused with a "
              f"note)", not bad, bad)

print("== the name on the black: both rows")
lab = L.plan_label(SINGLE, S(on=True, place="black", row="both", content="id_name"),
                   name="Actuator")
m = mod(SINGLE)
bot, top = (lab.parts[0].rect, lab.parts[1].rect) if len(lab.parts) == 2 else (None, None)
row30 = 30.0 - 2.5 * mod(SINGLE)
top_cap = max(c / 10.0 for c in range(23, 26)
              if L.line_width("ACTUATOR:0", L.metrics(c / 10.0)) <= row30 + 1e-9)
check(f"30 mm: '#12' in the bottom row (2.5 mm), 'ACTUATOR' in the top row ({top_cap:g} mm: "
      f"sized so 'ACTUATOR:2'..':9' print the same)",
      lab.texts() == ["#12", "ACTUATOR"] and [sl.where for sl in lab.slots] == ["bottom", "top"]
      and [sl.part for sl in lab.slots] == ["first", "rest"] and close(lab.parts[0].cap, 2.5)
      and close(lab.parts[1].cap, top_cap) and not L.check_label(SINGLE, lab),
      (lab.texts(), [p.cap for p in lab.parts]))
check("... each line inside its own row's band",
      bot[1] >= -15.0 + 0.25 * m - 1e-9 and bot[3] <= -15.0 + 0.75 * m + 1e-9
      and top[3] <= 15.0 - 0.25 * m + 1e-9 and top[1] >= 15.0 - 0.75 * m - 1e-9, (bot, top))
lab = L.plan_label(SINGLE, S(on=True, place="black", row="both", content="id"))
check("both rows with a one-line content: the bottom row only",
      [sl.where for sl in lab.slots] == ["bottom"] and lab.texts() == ["#12"])
lab = L.plan_label(SINGLE, S(on=True, place="black", row="top", content="id_name"),
                   name="Arm")
check("one row holds the lines joined: top row '#12 ARM'",
      lab.texts() == ["#12 ARM"] and lab.slots[0].where == "top"
      and lab.parts[0].lines == ["#12 ARM"])
lab = L.plan_label(SINGLE, S(on=True, content="id_name"), name="Actuator")
check("the defaults (place auto, row auto) put '#id name' in both rows of the black",
      lab.kind == "black" and lab.side == "both" and lab.texts() == ["#12", "ACTUATOR"])
lab = L.plan_label(GRID31, S(on=True, place="black", row="both", content="id_name"),
                   name="Actuator")
sq = L.marker_squares(GRID31)
check("grid 3x1, both rows: each marker's id at its bottom, the name once, on the first's top",
      lab.texts() == ["#9", "#10", "#11", "ACTUATOR"]
      and [(sl.where, sl.square) for sl in lab.slots] == [("bottom", 0), ("bottom", 1),
                                                          ("bottom", 2), ("top", 0)]
      and lab.parts[3].rect[3] <= sq[0][1][3] and lab.parts[3].rect[2] <= sq[0][1][2]
      and not L.check_label(GRID31, lab),
      (lab.texts(), [(sl.where, sl.square) for sl in lab.slots]))
lab = L.plan_label(GRID31, S(on=True, place="black", row="both", scope="first",
                             content="id_name"), name="Actuator")
check("grid 3x1, both rows, first marker: its block '#9-11' and the name, both there",
      lab.texts() == ["#9-11", "ACTUATOR"] and all(sl.square == 0 for sl in lab.slots)
      and not L.check_label(GRID31, lab), lab.texts())
lab = L.plan_label(CHARUCO, S(on=True, place="black", row="both", content="id_name"),
                   name="Actuator")
check("ChArUco, both rows: every marker's id, the name once (first marker's top row)",
      lab.texts() == ["#0", "#1", "#2", "#3", "ACTUATOR"] and not L.check_label(CHARUCO, lab),
      lab.texts())
lab = L.plan_label(CHARUCO, S(on=True, place="black", content="id_name"), name="Actuator")
check("ChArUco, row auto: the square, the content's two lines in it",
      lab.side == "square" and lab.parts[0].lines == ["#0-3", "ACTUATOR"]
      and not L.check_label(CHARUCO, lab), lab.parts and lab.parts[0].lines)

print("== a text too long for its place: shrink, then cut short")
lab = L.plan_label(SINGLE, S(on=True, place="black", row="both", content="id_name"),
                   name="Actuator:2")
check("'ACTUATOR:2' in a 30 mm top row: shrunk under 2.5 mm, whole, no cut note",
      lab.parts[1].text == "Actuator:2" and lab.parts[1].cap < 2.5 - 1e-9
      and lab.parts[1].cap >= L.min_cap() and lab.parts[1].short == ""
      and not any("cut short" in n for n in lab.notes), (lab.parts[1].cap, lab.notes))
row30 = 30.0 - 2.5 * mod(SINGLE)
lab = L.plan_label(SINGLE, S(on=True, place="black", row="bottom", content="id_name"),
                   name="Actuator:2")
p0 = lab.parts[0]
cut_name = p0.text.split("|")[-1]
cuts = L.name_cuts("Actuator:2")
nxt = cuts[cuts.index(cut_name) - 1] if cut_name in cuts and cuts.index(cut_name) > 0 else None
check(f"'#12 Actuator:2' in a 30 mm bottom row: cut short to {L.normalize(cut_name)[0]!r}, "
      f"the id whole, its ':2' kept, with a note",
      p0.short == "cut" and p0.lines[0].startswith("#12 ")
      and p0.lines[0].endswith(L.ELLIPSIS + ":2")
      and p0.rect[2] - p0.rect[0] <= row30 + 1e-9 and p0.cap >= L.min_cap() - 1e-9
      and any("cut short" in n and "|" not in n for n in lab.notes)
      and not L.check_label(SINGLE, lab), (p0.lines, lab.notes))
check("... the longest cut that prints: one more letter does not fit at the smallest cap",
      nxt is not None and L.line_width(L.normalize("#12 " + nxt)[0], L.metrics(L.min_cap()))
      > row30, (cut_name, nxt))
lab = L.plan_label(SINGLE60, S(on=True, place="black", row="bottom", content="id_name"),
                   name="Actuator:2")
check("... on a 60 mm marker it fits whole, shrunk under 0.5 module",
      lab.texts() == ["#12 ACTUATOR:2"] and lab.parts[0].short == ""
      and lab.parts[0].cap < 0.5 * mod(SINGLE60), (lab.texts(), lab.parts[0].cap))
lab = L.plan_label(SINGLE, S(on=True, place="black", row="both", content="id_name"),
                   name="Arm:2/Actuator:3")
check("a path that does not fit: its last element first ('ACTUATOR:3'), with a note",
      lab.parts[1].short == "tail" and lab.texts()[1] == "ACTUATOR:3"
      and any("written as" in n for n in lab.notes), (lab.texts(), lab.notes))
lab = L.plan_label(SINGLE, S(on=True, place="black", row="both", content="id_name"),
                   name="Shoulder_Pitch_Actuator:2")
check("a long name: cut short in the top row, keeping ':2'; the id row untouched",
      lab.texts()[0] == "#12" and lab.parts[0].short == "" and lab.parts[1].short == "cut"
      and lab.texts()[1].endswith(L.ELLIPSIS + ":2") and lab.texts()[1].startswith("SHOULD")
      and not L.check_label(SINGLE, lab), lab.texts())
check("name_cuts: longest first, the occurrence number kept",
      L.name_cuts("Arm:2/Pitch:3")[:2] == ["Pitc" + L.ELLIPSIS + ":3", "Pit" + L.ELLIPSIS + ":3"]
      and L.name_cuts("Plate")[0] == "Plat" + L.ELLIPSIS)
lab = L.plan_label(SINGLE, S(on=True, place="black", row="bottom", content="custom",
                             text="ROBOT ARM LEFT SIDE"))
check("a text with no name and no id: cut from its end, with a note",
      lab.ok and lab.parts[0].short == "cut" and lab.texts()[0].startswith("ROBOT")
      and lab.texts()[0].endswith(L.ELLIPSIS), lab.texts())
check("the font has '...' (three dots, two units apart)",
      len(L.glyph_boxes(L.ELLIPSIS, L.metrics(3.0))) == 3)
lab = L.plan_label(SINGLE40, S(on=True, content="id_name"), name="Shoulder_Pitch_Actuator:2",
                   grow=True)
check("place auto, 40 mm: a name too long for the black goes WHOLE on the white first",
      lab.kind == "white" and all(p.short == "" for p in lab.parts)
      and any("not on the black" in n for n in lab.notes), (lab.kind, lab.texts()))
lab = L.plan_label(SINGLE, S(on=True, content="id_name"), name="Shoulder_Pitch_Actuator:2",
                   grow=True)
check("... 30 mm, whole nowhere: cut short on the white (more of it shows than on the black)",
      lab.kind == "white" and lab.parts[0].short == "cut"
      and len(lab.parts[0].lines[-1]) > len("SHOULD" + L.ELLIPSIS + ":2"),
      (lab.kind, lab.texts()))
lab = L.plan_label(SINGLE, S(on=True, content="id_name"), name="Shoulder_Pitch_Actuator:2")
check("... with no white to go to: cut short on the black",
      lab.kind == "black" and lab.parts[1].short == "cut", (lab.kind, lab.texts(), lab.notes))
lab = L.plan_label(SINGLE, S(on=True, place="white", content="name"),
                   name="Shoulder_Pitch_Actuator_Left_Side:2", grow=True)
check("on the white too: one word too long for the strip is cut, keeping ':2'",
      lab.ok and lab.parts[0].short == "cut" and lab.texts()[0].endswith(L.ELLIPSIS + ":2")
      and not L.check_label(SINGLE, lab), (lab.texts(), lab.notes))

print("== batch ids, two rows")
for spec_t, nm in ((SINGLE, "Actuator"), (SINGLE40, "Actuator"), (SINGLE, "Shoulder_Pitch")):
    tpl = L.plan_label(spec_t, S(on=True, place="black", row="both", content="id_name"), name=nm)
    rec = tpl.to_dict()
    copy_specs = [M.TagSpec(marker_id=12 + k, marker_length_m=spec_t.marker_length_m)
                  for k in range(1, 4)]
    copies = [L.relabel(rec, cs, name=f"{nm}:{k + 2}") for k, cs in enumerate(copy_specs)]
    tops = [c.texts()[1] for c in copies]
    check(f"{spec_t.marker_length_m * 1000:g} mm '{nm}': each copy its own id at the bottom and "
          f"its own name at the top ({', '.join(tops)})",
          [c.texts()[0] for c in copies] == ["#13", "#14", "#15"]
          and all(t.endswith(f":{k + 2}") for k, t in enumerate(tops))
          and len(set(tops)) == 3
          and all(not L.check_label(cs, c) for cs, c in zip(copy_specs, copies)),
          [c.texts() for c in copies])
    check(f"... at the template's caps (bottom {tpl.parts[0].cap:g}, top {tpl.parts[1].cap:g})",
          all(close(c.parts[0].cap, tpl.parts[0].cap) and close(c.parts[1].cap, tpl.parts[1].cap)
              for c in copies), [[p.cap for p in c.parts] for c in copies])
planned, res = M.plan_serials([tagdef], [], occs, "Robot")
tpl = L.plan_label(tagdef.spec, S(on=True, place="black", row="both", content="id_name"),
                   name="Actuator")
got = {i.path: L.label_for_instance(tpl.to_dict(), i).texts() for i in res.instances}
check("plan_serials copies, both rows: each its own '#id' and the name below the level that "
      "set it",
      got["Arm:1+Actuator:1"] == ["#7", "ACTUATOR"] and got["Arm:1+Actuator:2"][1] == "ACTUATOR:2"
      and got["Arm:2+Actuator:1"][1] in ("ARM:2/ACTUATOR:1", "ACTUATOR:1"), got)
d = L.plan_label(SINGLE, S(on=True, place="black", row="both", content="id_name"),
                 name="Shoulder_Pitch_Actuator:2").to_dict()
back = L.from_dict(json.loads(json.dumps(d)))
check("records keep each slot's share of the content, its marker, and how a text was cut",
      [sl.part for sl in back.slots] == ["first", "rest"] and back.slots[0].square == 0
      and back.parts[1].short == "cut" and back.parts[1].full == "Shoulder_Pitch_Actuator:2"
      and L.same(d, back.to_dict()))

# ============================================================ the safety check
print("== the safety check catches a broken rule")
good = L.plan_label(SINGLE, S(on=True, place="white"), grow=True)
d = good.to_dict(9)
m = mod(SINGLE)


def moved(d, dv, part=0):
    d2 = json.loads(json.dumps(d))
    for bx in d2["parts"][part]["boxes"]:
        bx[1] += dv
    return d2


R = L.keepout_rect(SINGLE)
top = good.parts[0].rect[3]
check("white: as planned, safe", L.check_label(SINGLE, d) == [])
probs = L.check_label(SINGLE, moved(d, (R[1] + 0.5 * m) - top + m))
check("white: moved into the quiet ring -> 'inside the quiet ring'",
      any("quiet ring" in p for p in probs), probs)
probs = L.check_label(SINGLE, moved(d, 0.5 * m))
check("white: moved within the 1-module gap -> flagged",
      any("within 1 module" in p for p in probs), probs)
blk = L.plan_label(SINGLE, S(on=True, place="black")).to_dict(9)
probs = L.check_label(SINGLE, moved(blk, -0.3 * m))
check("black: moved toward the outer edge -> 'outside the bottom row's band'",
      any("band" in p for p in probs), probs)
d2 = json.loads(json.dumps(blk))
for bx in d2["parts"][0]["boxes"]:
    bx[0] -= 12.0
probs = L.check_label(SINGLE, d2)
check("black: moved into a corner cell -> flagged", any("corner cell" in p for p in probs), probs)
d2 = json.loads(json.dumps(blk))
d2["parts"][0]["boxes"].append([0.0, -12.5, 0.45, 3.2, 0.0])
probs = L.check_label(SINGLE, d2)
check("black: a stroke taller than 0.5 module -> flagged",
      any("cap" in p for p in probs) or any("band" in p for p in probs), probs)
d2 = json.loads(json.dumps(blk))
d2["parts"][0]["stroke_mm"] = 0.3
check("a stroke under the nozzle -> flagged",
      any("under the 0.45 mm nozzle" in p for p in L.check_label(SINGLE, d2)))
ch = L.plan_label(CHARUCO, S(on=True, place="black")).to_dict(9)
c0 = L.charuco_corners_mm(CHARUCO)[0]
d2 = json.loads(json.dumps(ch))
d2["parts"][0]["boxes"].append([c0[0] - 2.0, c0[1] + 2.0, 1.0, 1.0, 0.0])
probs = L.check_label(CHARUCO, d2)
check("ChArUco square: ink near a ChArUco corner -> flagged",
      any("ChArUco corner" in p for p in probs), probs)

# ===================================================== records, determinism
print("== records and determinism")
for name, kw in (("black", dict(place="black")), ("white", dict(place="white")),
                 ("square", dict(place="black"))):
    spec = CHARUCO if name == "square" else SINGLE
    a = L.plan_label(spec, S(on=True, content="id_name", **kw), name="Actuator", grow=True)
    b = L.plan_label(spec, S(on=True, content="id_name", **kw), name="Actuator", grow=True)
    ja, jb = json.dumps(a.to_dict(), sort_keys=True), json.dumps(b.to_dict(), sort_keys=True)
    back = L.from_dict(json.loads(ja))
    again = L.relabel(json.loads(ja), spec, name="Actuator")
    check(f"{name}: two runs give the same record, byte for byte", ja == jb)
    check(f"{name}: the record round-trips (same geometry)",
          L.same(a.to_dict(), back.to_dict()) and back.texts() == a.texts())
    check(f"{name}: relabel with the template's own data rebuilds it exactly",
          L.same(a.to_dict(), again.to_dict()))
other = L.relabel(L.plan_label(SINGLE, S(on=True)).to_dict(), M.TagSpec(marker_id=13,
                                                                         marker_length_m=0.03))
check("same(): another id is another geometry",
      not L.same(L.plan_label(SINGLE, S(on=True)).to_dict(), other.to_dict()))
check("same(): none == none == absent", L.same(None, {"kind": "none"}))
off = L.plan_label(SINGLE, S())
check("off: no label, an empty record", off.kind == "none" and off.to_dict()["kind"] == "none"
      and not off.boxes())
notes = []
s = S.from_dict({"on": True, "place": "grey", "side": "left", "cap_mm": "x", "nozzle_mm": -1},
                notes)
check("settings from a record: bad values fall back to defaults, with notes",
      s.on and s.place == "auto" and s.side == "left" and s.cap_mm == 0.0
      and s.nozzle_mm == L.NOZZLE_MM and len(notes) == 1, (s, notes))
check("settings round-trip", S.from_dict(S(on=True, content="custom", text="{key}",
                                           cap_mm=3.0).to_dict()).to_dict()
      == S(on=True, content="custom", text="{key}", cap_mm=3.0).to_dict())
lab = L.plan_label(SINGLE, {"on": True, "place": "black"})
check("plan_label takes the settings dict a record keeps", lab.kind == "black")
check("footprint_rects: every slot (what a variant refills)",
      L.plan_label(GRID, S(on=True, place="black")).footprint_rects()
      == [sl.rect for sl in L.plan_label(GRID, S(on=True, place="black")).slots])

# =================================================================== frames
print("== frames")
lp = L.loops_to_pattern([[(0.11, 0.2), (0.12, 0.2), (0.11, 0.21)]], cx=0.11, cy=0.2,
                        angle_deg=90.0)
check("loops_to_pattern: the fit's centre is the origin, face +x is pattern -y at 90 deg",
      close(lp[0][0], (0.0, 0.0)) and close(lp[0][1], (0.0, -10.0))
      and close(lp[0][2], (10.0, 0.0)))
lp = L.loops_to_pattern([[(0.01, 0.0)]], angle_deg=0.0, turn_deg=180.0)
check("... the Origin turn turns on top of the fit", close(lp[0][0], (-10.0, 0.0)))

# =================================================================== stdlib
print("== stdlib only")
src = Path(L.__file__).read_text(encoding="utf-8")
imports = {line.split()[1].split(".")[0] for line in src.splitlines()
           if line.startswith(("import ", "from ")) and not line.startswith("from __future__")
           and not line.startswith("from .")}
check("aruco_label imports only the stdlib (and its package)",
      imports <= {"math", "re", "dataclasses", "typing"}, imports)

print()
print(f"{PASS} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
