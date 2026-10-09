"""Offline renders of ArUco Extrude's optional label (bam_cad_core.aruco_label).

Every shape comes from the data the Fusion side will build from: the black
cells from markers.cell_boxes, the corner mark from aruco_chamfer, the pins
from aruco_slabs, and the label's boxes and grown tile from aruco_label --
seen from above, in the pattern frame. The dashed orange line is where
white-placement ink may not go (the quiet ring + 1 module); dashed blue
outlines are locating pins under the tile.

    python tools/render_aruco_label.py   # -> reports/media/aruco/label/*.png
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "CADExporter"))

from bam_cad_core import aruco_chamfer as C  # noqa: E402
from bam_cad_core import aruco_label as L  # noqa: E402
from bam_cad_core import aruco_slabs as SL  # noqa: E402
from bam_cad_core import markers as M  # noqa: E402

OUT = ROOT / "reports" / "media" / "aruco" / "label"
SS = 3
WHITE, BLACK = (246, 246, 242), (22, 22, 22)
PART = (128, 136, 148)
WHITE_PART, BLACK_PART = (232, 230, 222), (34, 34, 36)
RING = (255, 140, 0)
PIN = (0, 120, 220)
STRIP = (0, 150, 110)
TEXT = (40, 40, 40)
S = L.LabelSettings


def font(px):
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    return ImageFont.load_default(size=px)


class Panel:
    """World mm (u right, v up) -> pixels, centred on `centre`, `span` mm wide."""

    def __init__(self, w_px, h_px, span, centre=(0.0, 0.0)):
        self.w, self.h = w_px * SS, h_px * SS
        self.s = self.w / span
        self.c = centre
        self.im = Image.new("RGB", (self.w, self.h), (255, 255, 255))
        self.d = ImageDraw.Draw(self.im)

    def px(self, p):
        return (self.w / 2.0 + (p[0] - self.c[0]) * self.s,
                self.h / 2.0 - (p[1] - self.c[1]) * self.s)

    def poly(self, pts, fill):
        self.d.polygon([self.px(p) for p in pts], fill=fill)

    def rect(self, r, fill):
        self.poly(L.rect_poly(r), fill)

    def dashed(self, pts, colour, dash=6, width=1, closed=True):
        q = [self.px(p) for p in pts] + ([self.px(pts[0])] if closed else [])
        for a, b in zip(q, q[1:]):
            n = max(1, int(math.dist(a, b) / (dash * SS)))
            for k in range(0, n, 2):
                t0, t1 = k / n, min(1.0, (k + 1) / n)
                self.d.line((a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0,
                             a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1),
                            fill=colour, width=width * SS)

    def image(self):
        return self.im.resize((self.w // SS, self.h // SS), Image.LANCZOS)


def circle(cx, cy, r, n=64, cw=False):
    pts = [(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n))
           for k in range(n)]
    return pts[::-1] if cw else pts


def draw(spec, lab, *, mode="both", op="new", region=None, tile=None, mark=None, pins=None,
         size=(320, 320), span=None, centre=None, ring=True):
    """One tag and its label from above."""
    tile = tuple(tile) if tile else L.sticker_rect(spec)
    white = lab.white or tile
    boxes = [r for r in (L.ink_rect(lab.boxes()),) if r] + [white]
    if ring and lab.kind == "white":
        boxes.append(L.keepout_rect(spec))
    if region:
        xs = [p[0] for lp in region for p in lp]
        ys = [p[1] for lp in region for p in lp]
        boxes.append((min(xs), min(ys), max(xs), max(ys)))
    u0 = min(b[0] for b in boxes)
    v0 = min(b[1] for b in boxes)
    u1 = max(b[2] for b in boxes)
    v1 = max(b[3] for b in boxes)
    if centre is None:
        centre = ((u0 + u1) / 2.0, (v0 + v1) / 2.0)
    if span is None:
        span = max(u1 - u0, (v1 - v0) * size[0] / size[1]) * 1.12
    pn = Panel(size[0], size[1], span, centre)
    big = L.rect_poly((-1e4, -1e4, 1e4, 1e4))
    black = BLACK if mode != "white" else BLACK_PART
    if region:
        pn.poly(big, PART)
        pn.poly(region[0], WHITE_PART if mode == "black" else PART)
        for h in region[1:]:
            pn.poly(h, (255, 255, 255))
    else:
        pn.poly(big, PART if mode != "white" else BLACK_PART)
    # the white: a tile (cut corner), or White only's white pattern box
    kind = (mark.kind if mark is not None else "none")
    if mode in ("both", "white"):
        if kind == "tile_cut":
            mk = L.mark_on_tile(spec, mode, op, white) if lab.white else mark
            pn.poly(mk.outline, WHITE)
        else:
            pn.rect(white, WHITE)
    if mode == "white":
        pn.rect(white, WHITE)
    for b in M.cell_boxes(spec):
        r = tuple(v * 1000.0 for v in b)
        if kind == "black_cut" and C.is_top_left(r, mark.corner):
            pn.poly(mark.outline, black if mode != "white" else BLACK_PART)
        else:
            pn.rect(r, black)
    if kind == "triangle":
        pn.poly(mark.solid, black)
    ink = {"cut": WHITE, "add": black}.get(lab.role, black)
    if mode == "white" and lab.role == "add":
        ink = BLACK_PART                      # a notch in the white: the black body shows
    for p in lab.polygons():
        pn.poly(p, ink)
    if lab.strip:
        pn.dashed(L.rect_poly(lab.strip), STRIP, dash=5, width=1)
    if pins:
        cut = 0.0
        if kind == "tile_cut":
            cut = (L.mark_on_tile(spec, mode, op, white) if lab.white else mark).leg
        tw, th = white[2] - white[0], white[3] - white[1]
        try:
            outs = SL.pin_outlines(pins["shape"], pins["size"], tw, th, cut=cut)
        except ValueError:
            outs = []
        centre = ((white[0] + white[2]) / 2.0, (white[1] + white[3]) / 2.0)
        for poly in L.pin_keepouts(outs, centre):
            pn.dashed(poly, PIN, dash=3, width=2)
    if ring and lab.kind == "white":
        pn.dashed(L.rect_poly(L.keepout_rect(spec)), RING, dash=5, width=1)
    return pn.image()


def _wrap(text, f, width):
    """Split a caption line at spaces to fit `width` px."""
    out, cur = [], ""
    for word in text.split(" "):
        t = (cur + " " + word).strip()
        if cur and f.getlength(t) > width:
            out.append(cur)
            cur = word
        else:
            cur = t
    return out + [cur]


def caption(im, lines, f):
    lines = [w for t in lines for w in _wrap(t, f, im.width - 10)]
    h = 6 + 20 * len(lines)
    out = Image.new("RGB", (im.width, im.height + h), (255, 255, 255))
    out.paste(im, (0, 0))
    d = ImageDraw.Draw(out)
    for i, t in enumerate(lines):
        d.text((6, im.height + 3 + 20 * i), t, fill=TEXT, font=f)
    return out


def sheet(rows, title, ft):
    w = max(sum(im.width for im in r) + 12 * (len(r) - 1) for r in rows)
    h = sum(max(im.height for im in r) for r in rows) + 14 * (len(rows) - 1) + 46
    out = Image.new("RGB", (w + 24, h + 20), (255, 255, 255))
    ImageDraw.Draw(out).text((12, 10), title, fill=TEXT, font=ft)
    y = 50
    for r in rows:
        x = 12
        for im in r:
            out.paste(im, (x, y))
            x += im.width + 12
        y += max(im.height for im in r) + 14
    return out


def summary(lab):
    if lab.kind == "none":
        return ["no label", (lab.notes[-1] if lab.notes else "").replace("label: ", "")[:150]]
    where = {"black": f"on the black ({lab.side})", "white": f"on the white ({lab.side})"}[lab.kind]
    return [f"{where}: " + "  ".join(repr(t.replace('|', ' ')) for t in lab.texts()[:4])
            + (" ..." if len(lab.texts()) > 4 else ""),
            f"cap {lab.cap:.2f} mm, stroke {lab.stroke:.2f} mm, "
            f"min feature {lab.min_feature:.2f} mm"
            + ("" if not lab.strip else f", strip +{_strip_depth(lab):.1f} mm")]


def _strip_depth(lab):
    """How far the strip grew the tile (its short side)."""
    s = lab.strip
    return min(s[2] - s[0], s[3] - s[1])


def check(spec, lab, what):
    bad = L.check_label(spec, lab)
    if bad:
        raise SystemExit(f"{what}: unsafe label: {bad}")


# ------------------------------------------------------------------ specs

SINGLE = M.TagSpec(marker_id=12, marker_length_m=0.03)
SMALL = M.TagSpec(marker_id=12, marker_length_m=0.02)
GRID = M.TagSpec(marker_id=9, marker_length_m=0.03, layout="grid", rows=2, cols=3,
                 separation_m=0.006)
GRID31 = M.TagSpec(marker_id=9, marker_length_m=0.03, layout="grid", rows=1, cols=3,
                   separation_m=0.006)
CHARUCO = M.TagSpec(marker_id=0, marker_length_m=0.02775, layout="charuco", rows=3, cols=3,
                    square_length_m=0.037, quiet_modules=0.0)


def black_sheet(f, ft):
    rows = [[], []]
    mk = C.mark_for_spec(SINGLE, "both", "new")
    lab = L.plan_label(SINGLE, S(on=True, place="black"), mark=mk)
    check(SINGLE, lab, "single bottom")
    rows[0].append(caption(draw(SINGLE, lab, mark=mk), ["single 4x4 30 mm, #id, bottom row"]
                           + summary(lab), f))
    lab = L.plan_label(SINGLE, S(on=True, place="black", row="top", content="name"),
                       name="Arm:2", mark=mk)
    check(SINGLE, lab, "single top")
    rows[0].append(caption(draw(SINGLE, lab, mark=mk), ["name 'Arm:2', top row"] + summary(lab), f))
    lab = L.plan_label(SMALL, S(on=True, place="black"))
    rows[0].append(caption(draw(SMALL, lab), ["single 4x4 20 mm, on the black: refused"]
                           + summary(lab), f))
    lab = L.plan_label(GRID, S(on=True, place="black"))
    check(GRID, lab, "grid each")
    rows[1].append(caption(draw(GRID, lab, size=(420, 320)), ["grid 3x2, #id in each marker"]
                           + summary(lab), f))
    mk = C.mark_for_spec(CHARUCO, "both", "new")
    lab = L.plan_label(CHARUCO, S(on=True, place="black"), mark=mk)
    check(CHARUCO, lab, "charuco square")
    rows[1].append(caption(draw(CHARUCO, lab, mark=mk), ["ChArUco 3x3 37 mm squares: the square"]
                           + summary(lab), f))
    lab = L.plan_label(CHARUCO, S(on=True, place="black", content="id_name"), name="Plate",
                       mark=mk)
    check(CHARUCO, lab, "charuco square name")
    rows[1].append(caption(draw(CHARUCO, lab, mark=mk), ["#id name in the square (wrapped)"]
                           + summary(lab), f))
    p = OUT / "label_black.png"
    sheet(rows, "Label on the black: white text knocked out of the border row "
          "(cap <= 0.5 module) or a ChArUco black square", ft).save(p)
    return p


def white_sheet(f, ft):
    rows = [[], []]
    mk = C.mark_for_spec(SINGLE, "both", "new")
    pins = {"shape": "round", "size": 3.0}
    lab = L.plan_label(SINGLE, S(on=True, place="white", content="id_name"), name="Actuator:3",
                       grow=True, mark=mk, pins=pins)
    check(SINGLE, lab, "strip below")
    rows[0].append(caption(draw(SINGLE, lab, mark=mk, pins=pins),
                           ["White+black New, strip below, round pins"] + summary(lab), f))
    lab = L.plan_label(SINGLE, S(on=True, place="white", side="right", content="id_name"),
                       name="Actuator:3", grow=True, mark=mk)
    check(SINGLE, lab, "strip right")
    rows[0].append(caption(draw(SINGLE, lab, mark=mk), ["side right (reads downward)"]
                           + summary(lab), f))
    lab = L.plan_label(SMALL, S(on=True), grow=True)
    check(SMALL, lab, "auto small")
    rows[0].append(caption(draw(SMALL, lab), ["20 mm, place auto: falls to the white"]
                           + summary(lab), f))
    lab = L.plan_label(GRID31, S(on=True, place="white", content="id_name"), name="Plate",
                       grow=True)
    check(GRID31, lab, "grid strip")
    rows[1].append(caption(draw(GRID31, lab, size=(440, 260)), ["grid 3x1, one label: #9-11"]
                           + summary(lab), f))
    lab = L.plan_label(SINGLE, S(on=True, place="white", side="above"), mode="white", op=None,
                       grow=True)
    check(SINGLE, lab, "white only")
    rows[1].append(caption(draw(SINGLE, lab, mode="white", op=None),
                           ["White only, above: a notch in the white"] + summary(lab), f))
    lab = L.plan_label(CHARUCO, S(on=True, place="white"), grow=True)
    check(CHARUCO, lab, "charuco strip")
    rows[1].append(caption(draw(CHARUCO, lab), ["ChArUco 3x3, strip below"] + summary(lab), f))
    p = OUT / "label_white.png"
    sheet(rows, "Label on the white: black text beyond the quiet zone + 1 module "
          "(orange dashes); the tile grows by a strip (green dashes)", ft).save(p)
    return p


def region_sheet(f, ft):
    row = []
    disc = [circle(0, 0, 40), circle(0, -26, 4, cw=True), circle(26, 0, 4, cw=True),
            circle(-26, 0, 4, cw=True), circle(0, 26, 4, cw=True)]
    lab = L.plan_label(SINGLE, S(on=True, place="white"), mode="black", op=None, region=disc)
    check(SINGLE, lab, "disc")
    row.append(caption(draw(SINGLE, lab, mode="black", op=None, region=disc, span=90),
                       ["Black only on an 80 mm disc, 4 screw holes", "side auto: slid past a hole"]
                       + summary(lab), f))
    plate = [[(-25, -60), (25, -60), (25, 25), (-25, 25)], circle(0, -30, 5, cw=True)]
    lab = L.plan_label(SINGLE, S(on=True, place="white", content="id_name"), mode="black",
                       op=None, region=plate, name="Arm:2/Actuator:3")
    check(SINGLE, lab, "plate")
    row.append(caption(draw(SINGLE, lab, mode="black", op=None, region=plate),
                       ["Black only on a 50 x 85 plate, a hole below", "auto: the most room"]
                       + summary(lab), f))
    tri = C.mark_for_spec(SINGLE, "black", None)
    plate2 = [[(-40, -24), (40, -24), (40, 24), (-40, 24)]]
    lab = L.plan_label(SINGLE, S(on=True, place="white", side="left", content="dict_id"),
                       mode="black", op=None, region=plate2, mark=tri)
    check(SINGLE, lab, "plate2")
    row.append(caption(draw(SINGLE, lab, mode="black", op=None, region=plate2, mark=tri),
                       ["80 x 48 plate, corner triangle, side left", "dictionary #id"]
                       + summary(lab), f))
    big = (-35.0, -35.0, 35.0, 30.0)
    lab = L.plan_label(SINGLE, S(on=True, place="white"), region=[L.rect_loop(big)], tile=big)
    check(SINGLE, lab, "sketch tile")
    row.append(caption(draw(SINGLE, lab, tile=big), ["White+black on a 70 x 65 sketch area",
                                                     "the tile already has the room"]
                       + summary(lab), f))
    p = OUT / "label_region.png"
    sheet([row], "Label fitted into the white around the tag: never over a hole, never off the "
          "face, clear of the corner mark", ft).save(p)
    return p


def batch_sheet(f, ft):
    """A template + three copies whose ids a batch ArUco IDs set changed:
    each copy's label comes from its own resolved id and its own name."""
    rows = [[], []]
    copies = [(12, "Actuator"), (13, "Actuator:2"), (14, "Actuator:3"), (15, "Actuator:4")]
    mk = C.mark_for_spec(SINGLE, "both", "new")
    tpl = L.plan_label(SINGLE, S(on=True, place="black"), mark=mk, name="Actuator")
    rec = tpl.to_dict()
    for mid, name in copies:
        spec = M.TagSpec(marker_id=mid, marker_length_m=0.03)
        lab = L.relabel(rec, spec, name=name)
        check(spec, lab, f"copy {mid}")
        who = "template" if mid == 12 else f"copy, id set to {mid}"
        rows[0].append(caption(draw(spec, lab, mark=mk, size=(240, 240)), [who] + summary(lab)[:1],
                               f))
    tpl = L.plan_label(SINGLE, S(on=True, place="white", content="id_name"), mark=mk,
                       name="Actuator", grow=True)
    rec = tpl.to_dict()
    for mid, name in copies:
        spec = M.TagSpec(marker_id=mid, marker_length_m=0.03)
        lab = L.relabel(rec, spec, name=name)
        check(spec, lab, f"copy white {mid}")
        rows[1].append(caption(draw(spec, lab, mark=mk, size=(240, 300), ring=False),
                               [name] + [summary(lab)[0][:40]], f))
    p = OUT / "label_batch_ids.png"
    sheet(rows, "Batch ArUco IDs: every copy relabels from its own id and name, in the "
          "template's slots (same cap)", ft).save(p)
    return p


def font_sheet(f, ft):
    chars = "".join(sorted(L.GLYPHS, key=lambda c: (not c.isdigit(), not c.isalpha(), c))).strip()
    lines = [chars[i:i + 16] for i in range(0, len(chars), 16)]
    out = []
    for cap, note in ((L.min_cap(), "smallest printable: cap 2.25 mm, stroke 0.45 mm"),
                      (7.5, "cap 7.5 mm (1.5 modules on a 30 mm marker), stroke 0.98 mm")):
        Mx = L.metrics(cap)
        boxes = L.block_boxes(lines, Mx)
        r = L.ink_rect(boxes)
        span = (r[2] - r[0]) * 1.06
        h = int(560 * (r[3] - r[1]) / span * 1.12) + 10
        pn = Panel(560, h, span, ((r[0] + r[2]) / 2.0, (r[1] + r[3]) / 2.0))
        pn.poly(L.rect_poly((-1e4, -1e4, 1e4, 1e4)), WHITE)
        for b in boxes:
            pn.poly(b.corners(), BLACK)
        out.append(caption(pn.image(), [note, f"min feature {Mx.min_feature:.2f} mm "
                                        f"(stroke {Mx.stroke:.2f}, counters {Mx.gap:.2f}, "
                                        f"spacing {Mx.space:.2f})"], f))
    p = OUT / "label_font.png"
    sheet([out], "The label font: single strokes as boxes, every stroke and gap >= the nozzle",
          ft).save(p)
    return p


def _rows_line(lab):
    """What each row says: 'bottom #12 / top ACTUATOR', and the caps."""
    out = []
    for p in lab.parts:
        sl = lab.slots[p.slot]
        out.append(f"{sl.where} {' '.join(p.lines)!r} {p.cap:.2f}"
                   + (" (cut)" if p.short == "cut" else ""))
    return out


def name_on_black_sheet(f, ft):
    """Zach, 2026-10-09: "I want to see an example with the name on the
    black bar as well". One label ("#{id}|{name}"), three positions on the
    black, four tags; then a batch of ids with their own names."""
    S40 = M.TagSpec(marker_id=12, marker_length_m=0.04)
    tags = [("single 4x4, 30 mm", SINGLE, (300, 300)), ("single 4x4, 40 mm", S40, (300, 300)),
            ("grid 3x1, 30 mm markers", GRID31, (460, 230)),
            ("ChArUco 3x3, 37 mm squares", CHARUCO, (300, 300))]
    cases = [("both rows: '#id' bottom + name top", dict(row="both"), "Actuator"),
             ("bottom row: '#id name'", dict(row="bottom"), "Actuator:2"),
             ("both rows, a long name", dict(row="both"), "Shoulder_Pitch_Actuator:2")]
    rows = []
    for tname, spec, size in tags:
        mk = C.mark_for_spec(spec, "both", "new")
        row = []
        for cname, pos, nm in cases:
            lab = L.plan_label(spec, S(on=True, place="black", content="id_name", **pos),
                               name=nm, mark=mk)
            check(spec, lab, f"{tname} {cname}")
            cut = [p for p in lab.parts if p.short == "cut"]
            row.append(caption(draw(spec, lab, mark=mk, size=size),
                               [tname, f"{cname}, name '{nm}'"] + _rows_line(lab)[:2]
                               + ([f"... and {len(lab.parts) - 2} more rows"]
                                  if len(lab.parts) > 2 else [])
                               + ([f"cut short: '{cut[0].full.replace('|', ' ')}' does not "
                                   f"fit whole at the smallest printable cap "
                                   f"({L.min_cap():.2f} mm)"] if cut else []), f))
        rows.append(row)
    # the batch: one template, three copies a batch ArUco IDs set re-serialled
    tpl = L.plan_label(SINGLE, S(on=True, place="black", content="id_name"), name="Actuator",
                       mark=C.mark_for_spec(SINGLE, "both", "new"))
    rec = tpl.to_dict()
    row = []
    mk = C.mark_for_spec(SINGLE, "both", "new")
    for mid, nm, who in ((12, "Actuator", "template (Actuator)"),
                         (13, "Actuator:2", "copy Actuator:2, id set to 13"),
                         (14, "Actuator:3", "copy Actuator:3, id set to 14"),
                         (15, "Actuator:4", "copy Actuator:4, id set to 15")):
        spec = M.TagSpec(marker_id=mid, marker_length_m=0.03)
        lab = L.relabel(rec, spec, name=nm)
        check(spec, lab, f"batch {mid}")
        row.append(caption(draw(spec, lab, mark=mk, size=(250, 250)), [who] + _rows_line(lab), f))
    rows.append(row)
    p = OUT / "label_name_on_black.png"
    sheet(rows, "The name on the black: one label '#{id}|{name}' -- line 1 in the bottom row, "
          "line 2 in the top row (or both in one row); too long: shrunk, then cut short with "
          "'...'", ft).save(p)
    return p


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    f, ft = font(14), font(20)
    for p in (black_sheet(f, ft), white_sheet(f, ft), region_sheet(f, ft), batch_sheet(f, ft),
              font_sheet(f, ft), name_on_black_sheet(f, ft)):
        print(p.relative_to(ROOT))


if __name__ == "__main__":
    main()
