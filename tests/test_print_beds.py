"""Offline tests for bam_cad_core.print_beds -- STL I/O, the lay-flat rule,
bed packing, the PrusaSlicer 3MF / project, and the files write_beds writes.

Run:  python tests/test_print_beds.py
"""
from __future__ import annotations

import io
import json
import math
import os
import random
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "CADExporter"))

from bam_cad_core import print_beds as P  # noqa: E402

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


# ------------------------------------------------------------------ fixtures

def box(x0, y0, z0, x1, y1, z1):
    """Closed axis-aligned box, outward-facing triangles."""
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    tris = []
    for a, b, c, d in quads:
        tris += [(a, b, c), (a, c, d)]
    return P.Mesh([tuple(float(c) for c in p) for p in v], tris)


def merge(*meshes):
    verts, tris = [], []
    for m in meshes:
        base = len(verts)
        verts += m.vertices
        tris += [(a + base, b + base, c + base) for a, b, c in m.triangles]
    return P.Mesh(verts, tris)


def tag_frame(depth=0.6, rise=0.0, origin=(0.0, 0.0, 3.0), x=(0.0, 1.0, 0.0), z=(0.0, 0.0, 1.0)):
    y = P._cross(z, x)
    return P.TagFrame("front", origin, x, y, z, depth, rise,
                      {"marker_length_m": 0.03, "layout": "single", "depth_m": depth / 1000.0,
                       "raise_m": rise / 1000.0})


TILE = box(-20, -20, 0, 20, 20, 3)
CELLS_INLAID = box(-15, -15, 2.4, 15, 15, 3.0)
CELLS_RAISED = box(-15, -15, 3.0, 15, 15, 3.6)


def manifest_for(parts):
    """parts: [(name, copies, tile_mesh, cells_mesh, spec_extra, origin_cm)] ->
    (manifest, reader) with STLs in memory."""
    files = {}
    entries = []
    for name, copies, tile, cells, extra, origin in parts:
        slug = P.slug(name)
        files["aruco/print3d/%s__tile.stl" % slug] = P.write_stl(tile)
        files["aruco/print3d/%s__cells.stl" % slug] = P.write_stl(cells)
        spec = {"depth_m": 0.0006, "dictionary": "DICT_4X4_50", "layout": "single",
                "marker_id": 1, "marker_length_m": 0.03, "quiet_modules": 1.0}
        spec.update(extra)
        entries.append({
            "component": name, "copies": copies, "variant": False,
            "bodies": [{"name": "tag tile", "role": "tile", "colour": "white",
                        "stl": "aruco/print3d/%s__tile.stl" % slug},
                       {"name": "aruco cells front", "role": "cells", "colour": "black",
                        "stl": "aruco/print3d/%s__cells.stl" % slug}],
            "tags": [{"key": "front", "origin": list(origin), "spec": spec, "units": "cm",
                      "x_axis": [0.0, 1.0, 0.0], "y_axis": [-1.0, 0.0, 0.0],
                      "z_axis": [0.0, 0.0, 1.0]}]})
    manifest = {"schema": P.MANIFEST_SCHEMA, "stl_units": "mm", "frame_units": "cm",
                "design": "Test", "parts": entries}
    return manifest, files.__getitem__


BUNDLE = """# test vendor bundle
[vendor]
name = Test Vendor
config_version = 9.9.9

[printer:*common*]
printer_technology = FFF
bed_shape = 0x0,250x0,250x210,0x210
max_print_height = 220
nozzle_diameter = 0.4
extruder_colour = ""
start_gcode = G28 ; home\\nG1 Z5

[printer:Test MMU 0.4 nozzle]
inherits = *common*
nozzle_diameter = 0.4,0.4,0.4,0.4,0.4
extruder_colour = #FF8000;#DB5182;#3EC0FF;#FF4F4F;#FBEB7D
single_extruder_multi_material = 1

[print:*base*]
layer_height = 0.15
perimeters = 2
compatible_printers_condition = printer_notes=~/.*PG.*/

[print:0.20mm TEST]
inherits = *base*
layer_height = 0.2
wipe_tower = 1

[filament:Test PLA]
filament_type = PLA
filament_colour = #FF8000
filament_diameter = 1.75
"""


def read_pe_config(text):
    """PrusaSlicer's ConfigBase::load_from_gcode_string_legacy: walk the
    lines bottom-up, stop at the first one that is not "; key = value"."""
    out = {}
    for line in reversed([ln for ln in text.splitlines() if ln.strip()]):
        if len(line) < 10 or not line.startswith("; "):
            break
        key, sep, value = line[2:].partition(" = ")
        if not sep or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{2,}", key):
            break
        out[key] = value
    return out


def parse_3mf(data):
    z = zipfile.ZipFile(io.BytesIO(data))
    ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    model = ET.fromstring(z.read("3D/3dmodel.model"))
    objects = {}
    for obj in model.find("m:resources", ns).findall("m:object", ns):
        mesh = obj.find("m:mesh", ns)
        verts = [(float(v.get("x")), float(v.get("y")), float(v.get("z")))
                 for v in mesh.find("m:vertices", ns)]
        tris = [(int(t.get("v1")), int(t.get("v2")), int(t.get("v3")))
                for t in mesh.find("m:triangles", ns)]
        objects[obj.get("id")] = {"verts": verts, "tris": tris}
    items = [(i.get("objectid"), [float(v) for v in i.get("transform").split()])
             for i in model.find("m:build", ns)]
    cfg = ET.fromstring(z.read("Metadata/Slic3r_PE_model.config"))
    for o in cfg.findall("object"):
        obj = objects[o.get("id")]
        obj["instances_count"] = int(o.get("instances_count"))
        obj["meta"] = {m.get("key"): m.get("value") for m in o.findall("metadata")}
        obj["volumes"] = [{"first": int(v.get("firstid")), "last": int(v.get("lastid")),
                           **{m.get("key"): m.get("value") for m in v.findall("metadata")}}
                          for v in o.findall("volume")]
    return z, objects, items


# ------------------------------------------------------------------ STL
print("STL round trip")
m = merge(TILE, CELLS_INLAID)
f32 = lambda v: tuple(__import__("struct").unpack("<3f", __import__("struct").pack("<3f", *v)))
m = P.Mesh([f32(v) for v in m.vertices], m.triangles)       # what float32 STL can hold
back = P.read_stl(P.write_stl(m, "x"))
check("binary STL: same triangle count", len(back.triangles) == len(m.triangles))
check("binary STL: same vertex set", set(back.vertices) == set(m.vertices))
check("binary STL: same triangles (as vertex triples)",
      sorted(tuple(back.vertices[i] for i in t) for t in back.triangles)
      == sorted(tuple(m.vertices[i] for i in t) for t in m.triangles))
check("written header never starts with 'solid'", not P.write_stl(m, "solid")[:5] == b"solid")
solid_header = b"solid but binary".ljust(80, b" ") + P.write_stl(TILE)[80:]
check("a binary STL whose header says 'solid' is still binary",
      len(P.read_stl(solid_header).triangles) == 12)
ascii_stl = "solid t\n" + "".join(
    "facet normal 0 0 0\n outer loop\n" + "".join(
        "  vertex %r %r %r\n" % TILE.vertices[i] for i in t) + " endloop\nendfacet\n"
    for t in TILE.triangles) + "endsolid t\n"
a = P.read_stl(ascii_stl.encode())
check("ASCII STL reads", len(a.triangles) == 12 and len(a.vertices) == 8, (len(a.triangles), len(a.vertices)))
try:
    P.read_stl(b"not an stl at all")
    check("garbage is rejected", False)
except ValueError:
    check("garbage is rejected", True)

# ------------------------------------------------------------------ orientation
print("orientation rule")
o = P.choose_orientation([TILE, CELLS_INLAID], [tag_frame()])
check("inlaid black, nothing proud -> face down", o.rule == "face_down", o)
check("...face down means the face normal points at the bed", o.down == (0.0, 0.0, 1.0))
part = P.Part("T", "T", 1, [P.Body("tile", "tile", "white", TILE),
                            P.Body("cells", "cells", "black", CELLS_INLAID)], [tag_frame()])
op = P.orient_part(part)
cz = op.cells_z_mm
check("...the cells sit ON the bed (z min = 0)", cz is not None and abs(cz[0]) < 1e-9, cz)
check("...the part rests on z = 0", min(p[2] for mm in op.meshes for p in mm.vertices) == 0.0)
check("...tag x runs along bed +X",
      all(abs(a - b) < 1e-12 for a, b in zip(tuple(P._dot(r, (0.0, 1.0, 0.0)) for r in op.rotation),
                                              (1.0, 0.0, 0.0))))
lo, hi = P.bbox(op.meshes)
check("...centred on the origin in XY", abs(lo[0] + hi[0]) < 1e-9 and abs(lo[1] + hi[1]) < 1e-9)
check("...nothing scaled (40 x 40 x 3)", tuple(round(v, 6) for v in op.size_mm) == (40.0, 40.0, 3.0))

o = P.choose_orientation([TILE, CELLS_RAISED], [tag_frame(depth=0.0, rise=0.6)])
check("raised black -> face up", o.rule == "face_up", o)
check("...the back is on the bed", o.down == (0.0, 0.0, -1.0))
op = P.orient_part(P.Part("R", "R", 1, [P.Body("tile", "tile", "white", TILE),
                                        P.Body("cells", "cells", "black", CELLS_RAISED)],
                          [tag_frame(depth=0.0, rise=0.6)]))
check("...black on top: z 3.0..3.6", tuple(round(v, 6) for v in op.cells_z_mm) == (3.0, 3.6),
      op.cells_z_mm)
check("...tag x still runs along bed +X",
      all(abs(a - b) < 1e-12 for a, b in zip(tuple(P._dot(r, (0.0, 1.0, 0.0)) for r in op.rotation),
                                              (1.0, 0.0, 0.0))))

boss = box(-5, -5, 3, 5, 5, 8)
o = P.choose_orientation([merge(TILE, boss), CELLS_INLAID], [tag_frame()])
check("inlaid but a boss stands proud of the face -> face up", o.rule == "face_up", o)
check("...and the reason says why", "proud" in o.reason, o.reason)

back_boss = box(-5, -5, -5, 5, 5, 0)
o = P.choose_orientation([merge(TILE, boss, back_boss), CELLS_INLAID], [tag_frame()])
check("proud face AND no flat back -> fallback", o.rule == "fallback", o)
check("...with a warning", bool(o.warning))
check("...on its largest flat supporting face (a 40 x 3 side)",
      abs(o.contact_mm2 - 120.0) < 1e-6 and abs(o.down[2]) < 1e-9, (o.contact_mm2, o.down))

o = P.choose_orientation([TILE], [])
check("no tag frame at all -> fallback on the biggest face",
      o.rule == "fallback" and abs(o.contact_mm2 - 1600.0) < 1e-6, o)

# ArUco Extrude, 2026-10-08: a tag printed on its own with locating pins
# hanging from its back (White + black, New body), and a recessed black
PIN = box(-19, 16, -2, -16, 19, 0)                  # a 3 x 3 x 2 mm pin under the tile
o = P.choose_orientation([merge(TILE, PIN), CELLS_INLAID], [tag_frame()])
check("pinned tag, flush black -> face down (pattern on the bed, pins up)",
      o.rule == "face_down" and o.down == (0.0, 0.0, 1.0), o)
op = P.orient_part(P.Part("Pin", "Pin", 1, [P.Body("tile", "tile", "white", merge(TILE, PIN)),
                                            P.Body("cells", "cells", "black", CELLS_INLAID)],
                          [tag_frame()]))
pin_z = [p[2] for p in op.meshes[0].vertices if p[2] > 3.0 + 1e-9]
check("...the pins point up: 2 mm over the 3 mm tile", pin_z and abs(max(pin_z) - 5.0) < 1e-9,
      max(pin_z) if pin_z else None)
o = P.choose_orientation([merge(TILE, PIN), CELLS_RAISED], [tag_frame(depth=0.0, rise=0.6)])
check("pinned tag with a raised black: no flat side -> fallback with a warning",
      o.rule == "fallback" and bool(o.warning), o)
CELLS_RECESSED = box(-15, -15, 2.0, 15, 15, 2.6)     # top 0.4 under the face, pocket open
rec = P.TagFrame("front", (0.0, 0.0, 3.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0),
                 1.0, 0.0, {"marker_length_m": 0.03, "layout": "single", "depth_m": 0.001,
                            "recess_m": 0.0004}, 0.4)
o = P.choose_orientation([TILE, CELLS_RECESSED], [rec])
check("recessed black -> face up (face down it would print over air)",
      o.rule == "face_up" and "recessed 0.40" in o.reason, o)
man, rd = manifest_for([("Tag R", 1, TILE, CELLS_RECESSED,
                         {"depth_m": 0.001, "recess_m": 0.0004}, (0, 0, 0.3))])
parts, fails, warns = P.load_parts(man, rd)
check("a recessed black's frame measures right (spans -depth..-recess)",
      parts and not warns and abs(parts[0].tags[0].recess_mm - 0.4) < 1e-9, warns)

# ------------------------------------------------------------------ units
print("units are measured")
cm_tile = P.scale_mesh(TILE, 0.1)
cm_cells = P.scale_mesh(CELLS_INLAID, 0.1)
man, rd = manifest_for([("Tag A", 1, cm_tile, cm_cells, {}, (0, 0, 0.3))])
parts, fails, warns = P.load_parts(man, rd)
check("an STL in cm (manifest says mm) is measured as cm",
      parts and abs(P.bbox([b.mesh for b in parts[0].bodies])[1][0] - 20.0) < 1e-4,
      P.bbox([b.mesh for b in parts[0].bodies]) if parts else fails)
check("...and says so", any("STL is in cm" in w for w in warns), warns)
man, rd = manifest_for([("Tag B", 1, TILE, CELLS_INLAID, {}, (0, 0, 3.0))])
man["parts"][0]["tags"][0]["units"] = "cm"          # origin is really mm
parts, fails, warns = P.load_parts(man, rd)
check("a frame in mm labelled cm is measured as mm",
      parts and abs(parts[0].tags[0].origin[2] - 3.0) < 1e-9, parts[0].tags[0] if parts else fails)
man, rd = manifest_for([("Tag C", 1, TILE, CELLS_INLAID, {}, (0, 0, 0.3))])
parts, fails, warns = P.load_parts(man, rd)
check("a correct manifest raises no unit warning", not warns, warns)
man["parts"][0]["bodies"][1]["stl"] = "aruco/print3d/missing.stl"
parts, fails, warns = P.load_parts(man, rd)
check("a missing STL is reported, the tile still loads",
      parts and len(parts[0].bodies) == 1 and any("could not be read" in w for w in warns))

# ------------------------------------------------------------------ packing
print("packing")


def check_packing(items, settings, slots, towers, label):
    m, s = settings.margin_mm, settings.spacing_mm
    ok_inside = ok_gap = ok_size = ok_tower = True
    placed = [(i, sl) for i, sl in enumerate(slots) if sl is not None]
    for i, sl in placed:
        w, d = items[i]
        if (round(sl.w, 9), round(sl.d, 9)) not in ((round(w, 9), round(d, 9)), (round(d, 9), round(w, 9))):
            ok_size = False
        if (sl.x < m - 1e-9 or sl.y < m - 1e-9 or sl.x + sl.w > settings.bed_w_mm - m + 1e-9
                or sl.y + sl.d > settings.bed_d_mm - m + 1e-9):
            ok_inside = False
    for a in range(len(placed)):
        for b in range(a + 1, len(placed)):
            p, q = placed[a][1], placed[b][1]
            if p.bed != q.bed:
                continue
            gx = max(q.x - (p.x + p.w), p.x - (q.x + q.w))
            gy = max(q.y - (p.y + p.d), p.y - (q.y + q.d))
            if max(gx, gy) < s - 1e-9:
                ok_gap = False
    for b, t in enumerate(towers):
        if settings.wipe_tower_mm and t is None:
            ok_tower = False
            continue
        if t is None:
            continue
        if t[0] < m - 1e-9 or t[1] < m - 1e-9 or t[0] + t[2] > settings.bed_w_mm - m + 1e-9 \
                or t[1] + t[3] > settings.bed_d_mm - m + 1e-9:
            ok_tower = False
        for _, sl in placed:
            if sl.bed != b:
                continue
            gx = max(sl.x - (t[0] + t[2]), t[0] - (sl.x + sl.w))
            gy = max(sl.y - (t[1] + t[3]), t[1] - (sl.y + sl.d))
            if max(gx, gy) < s - 1e-9:
                ok_tower = False
    check(label + ": footprints kept their size", ok_size)
    check(label + ": every part inside the bed's margin", ok_inside)
    check(label + ": at least spacing_mm between parts", ok_gap)
    check(label + ": every bed keeps its wipe tower clear of parts", ok_tower)


st = P.BedSettings()
squares = [(40.0, 40.0)] * 41
slots, rej, towers = P.pack_rectangles(squares, st)
nbeds = len(towers)
check("41 tags of 40 mm on an MK4 bed: 20 per bed -> 3 beds", nbeds == 3,
      (nbeds, [sum(1 for s in slots if s and s.bed == b) for b in range(nbeds)]))
check("...the first beds are full", [sum(1 for s in slots if s and s.bed == b)
                                      for b in range(nbeds)] == [20, 20, 1])
check_packing(squares, st, slots, towers, "41 squares")

rng = random.Random(7)
items = [(rng.uniform(8, 90), rng.uniform(8, 70)) for _ in range(70)]
keys = ["r%02d" % i for i in range(70)]
slots, rej, towers = P.pack_rectangles(items, st, keys)
check("random parts: none rejected", not rej, rej)
check_packing(items, st, slots, towers, "70 random parts")
uw, ud = st.usable()
inner_area = (uw + st.spacing_mm) * (ud + st.spacing_mm)
bound = math.ceil(sum((w + st.spacing_mm) * (d + st.spacing_mm) for w, d in items) / inner_area)
check("...beds within the area bound + 1 (%d vs bound %d)" % (len(towers), bound),
      bound <= len(towers) <= bound + 1)
again = P.pack_rectangles(items, st, keys)
check("deterministic: the same input packs the same", again[0] == slots and again[2] == towers)
order = list(range(70))
rng.shuffle(order)
shuffled = P.pack_rectangles([items[i] for i in order], st, [keys[i] for i in order])
by_key = {keys[i]: shuffled[0][j] for j, i in enumerate(order)}
check("deterministic: input order does not matter",
      all(by_key[keys[i]][1:] == slots[i][1:] for i in range(70)))

slots, rej, towers = P.pack_rectangles([(300.0, 10.0)], st)
check("a part longer than the bed is rejected loudly", 0 in rej and "never scaled" in rej[0], rej)
slots, rej, towers = P.pack_rectangles([(150.0, 235.0)], st)
check("a part that only fits turned is turned", slots[0] is not None and slots[0].rotated, slots)
slots, rej, towers = P.pack_rectangles([(235.0, 195.0)], st)
check("a part that leaves no room for the wipe tower is rejected with that reason",
      0 in rej and "wipe tower" in rej[0], rej)
st_nt = P.BedSettings(wipe_tower_mm=None)
slots, rej, towers = P.pack_rectangles([(235.0, 195.0)], st_nt)
check("...and placed when there is no tower", slots[0] is not None and towers == [None])
slots, rej, towers = P.pack_rectangles([(40.0, 40.0)] * 2, P.BedSettings(bed_w_mm=250, bed_d_mm=220))
check("bed size is a setting (Core One 250 x 220)", len(towers) == 1 and not rej)

# ------------------------------------------------------------------ PrusaSlicer beds
print("PrusaSlicer 2.9 multi-bed geometry")
check("bed grid = PrusaSlicer's spiral: 0..8",
      [P.bed_grid_coords(k) for k in range(9)]
      == [(0, 0), (1, 0), (0, 1), (1, 1), (2, 0), (2, 1), (0, 2), (1, 2), (2, 2)])
check("MK4 bed gap = 0.3 x diagonal = 97.95 mm", abs(P.bed_gap_mm(250, 210) - 97.9488) < 1e-3)
check("gap is capped at 100 mm", P.bed_gap_mm(400, 400) == 100.0)
check("bed 3 sits one bed + gap right and back",
      all(abs(a - b) < 1e-9 for a, b in zip(P.bed_translation(3, 250, 210),
                                             (250 + P.bed_gap_mm(250, 210), 210 + P.bed_gap_mm(250, 210)))))

# ------------------------------------------------------------------ presets + config
print("project config from a vendor bundle")
prof = P.load_prusa_profile(BUNDLE, "Test MMU 0.4 nozzle", "0.20mm TEST", "Test PLA", "test")
check("inherits chain resolved (own keys win)", prof.values["layer_height"] == "0.2"
      and prof.values["perimeters"] == "2" and prof.values["max_print_height"] == "220")
check("meta keys are not copied", "inherits" not in prof.values
      and "compatible_printers_condition" not in prof.values)
check("extruders / bed / height read from the preset",
      prof.extruders == 5 and prof.bed_mm == (250.0, 210.0) and prof.max_height_mm == 220.0)
try:
    P.load_prusa_profile(BUNDLE, "No Such Printer", "0.20mm TEST", "Test PLA")
    check("a missing preset is an error naming it", False)
except ValueError as exc:
    check("a missing preset is an error naming it", "No Such Printer" in str(exc))
cfg_text = P.project_config_text(prof, P.BedSettings())
cfg = read_pe_config(cfg_text)
check("PrusaSlicer's bottom-up reader gets every key", set(prof.values) <= set(cfg),
      sorted(set(prof.values) - set(cfg)))
check("...and the preset names", cfg.get("printer_settings_id") == "Test MMU 0.4 nozzle"
      and cfg.get("print_settings_id") == "0.20mm TEST"
      and cfg.get("filament_settings_id") == ";".join(['"Test PLA"'] * 5), cfg.get("filament_settings_id"))
check("extruder 1 white, 2 black, the rest as the printer had them",
      cfg.get("extruder_colour") == "#FFFFFF;#000000;#3EC0FF;#FF4F4F;#FBEB7D", cfg.get("extruder_colour"))
check("filament colours per slot", cfg.get("filament_colour") == "#FFFFFF;#000000;#FF8000;#FF8000;#FF8000",
      cfg.get("filament_colour"))
check("escaped newlines stay one line", cfg.get("start_gcode") == "G28 ; home\\nG1 Z5")
cfg2 = read_pe_config(P.project_config_text(prof, P.BedSettings(bed_w_mm=250, bed_d_mm=220)))
check("a bed that differs from the preset overrides bed_shape",
      cfg2.get("bed_shape") == "0x0,250x0,250x220,0x220", cfg2.get("bed_shape"))

# ------------------------------------------------------------------ files
print("3MF and project files")
man, rd = manifest_for([("Tag · A #1", 3, TILE, CELLS_INLAID, {}, (0, 0, 0.3)),
                        ("Tag B", 1, TILE, CELLS_RAISED, {"depth_m": 0.0, "raise_m": 0.0006},
                         (0, 0, 0.3)),
                        ("Big & <odd>", 1, box(-100, -60, 0, 100, 60, 3),
                         box(-15, -15, 2.4, 15, 15, 3), {}, (0, 0, 0.3)),
                        ("Hidden", 0, TILE, CELLS_INLAID, {}, (0, 0, 0.3))])
layout = P.layout_beds(man, rd, P.BedSettings())
check("copies placed, the 0-copy part skipped",
      len(layout.placed) == 5 and [n for n, _ in layout.skipped] == ["Hidden"], layout.skipped)
files = P.render_beds(layout, prof)
check("project + per-bed + parts + layout files",
      "aruco_tags.3mf" in files and "beds/bed_01.3mf" in files and "beds/layout.json" in files
      and "parts/Tag_A_1__tile.stl" in files and "parts/Tag_A_1__cells.stl" in files
      and "parts/Tag_A_1.3mf" in files and "beds/bed_01.svg" in files, sorted(files))
z, objects, items = parse_3mf(files["beds/bed_01.3mf"])
names = set(z.namelist())
check("3MF has the required members", {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model",
                                       "Metadata/Slic3r_PE_model.config",
                                       "Metadata/Slic3r_PE.config"} <= names, names)
ok_ranges = ok_ext = ok_idx = True
for oid, obj in objects.items():
    vols = obj["volumes"]
    if [v["first"] for v in vols][0] != 0 or vols[-1]["last"] != len(obj["tris"]) - 1 or any(
            vols[i + 1]["first"] != vols[i]["last"] + 1 for i in range(len(vols) - 1)):
        ok_ranges = False
    if sorted((v["name"], v["extruder"]) for v in vols) != [("aruco cells front", "2"), ("tag tile", "1")]:
        ok_ext = False
    if any(max(t) >= len(obj["verts"]) for t in obj["tris"]):
        ok_idx = False
check("each object = two volumes whose triangle ranges cover its mesh exactly", ok_ranges)
check("tile on extruder 1, cells on extruder 2", ok_ext,
      [[(v["name"], v["extruder"]) for v in o["volumes"]] for o in objects.values()])
check("triangle indices stay inside the vertex list", ok_idx)
check("one build item per placed copy on the bed",
      len(items) == sum(1 for p in layout.placed if p.bed == 0)
      and sum(o["instances_count"] for o in objects.values()) == len(items))
check("names are XML-escaped and survive", any(o["meta"]["name"] == "Big & <odd>"
                                               for o in objects.values()))
check("the same input renders the same bytes", P.render_beds(layout, prof) == files)

# A multi-bed project: 45 squares -> 3 beds, each copy inside its own bed.
man, rd = manifest_for([("Sq", 45, TILE, CELLS_INLAID, {}, (0, 0, 0.3))])
layout = P.layout_beds(man, rd, P.BedSettings())
files = P.render_beds(layout, prof)
check("45 tags -> 3 beds", layout.bed_count == 3, layout.bed_count)
z, objects, items = parse_3mf(files["aruco_tags.3mf"])
W, D = 250.0, 210.0
inside = {}
for oid, tr in items:
    cx, cy = tr[9], tr[10]
    hit = [k for k in range(9) if (lambda t: t[0] + 20 <= cx <= t[0] + W - 20
                                   and t[1] + 20 <= cy <= t[1] + D - 20)(P.bed_translation(k, W, D))]
    inside[len(hit)] = inside.get(len(hit), 0) + 1
    if hit:
        inside.setdefault("beds", []).append(hit[0])
check("every copy lies wholly on exactly one PrusaSlicer bed", inside.get(1) == 45, inside)
check("...20 + 20 + 5 per bed", sorted(inside["beds"].count(k) for k in range(3)) == [5, 20, 20])
wt = z.read("Metadata/Prusa_Slicer_wipe_tower_information.xml").decode()
check("one wipe tower per bed, bed-local", re.findall(r'bed_idx="(\d)"', wt) == ["0", "1", "2"], wt)
check("project carries the config", "Metadata/Slic3r_PE.config" in z.namelist())
check("Application is not a PrusaSlicer < 2.9 (no legacy rearrange)",
      b"PrusaSlicer-" not in z.read("3D/3dmodel.model"))
files_np = P.render_beds(layout, None)
check("without presets: no all-beds project, bed files have no config",
      "aruco_tags.3mf" not in files_np and "Metadata/Slic3r_PE.config"
      not in zipfile.ZipFile(io.BytesIO(files_np["beds/bed_01.3mf"])).namelist())
man, rd = manifest_for([("Sq", 200, TILE, CELLS_INLAID, {}, (0, 0, 0.3))])
layout = P.layout_beds(man, rd, P.BedSettings())
files = P.render_beds(layout, prof)
check("200 tags -> 10 beds -> two projects (PrusaSlicer holds 9)",
      layout.bed_count == 10 and "aruco_tags.3mf" in files and "aruco_tags_2.3mf" in files,
      (layout.bed_count, [f for f in files if f.startswith("aruco")]))
summary = json.loads(files["beds/layout.json"])
check("layout.json says which project and bed each bed is",
      summary["beds"][9]["project"] == "aruco_tags_2.3mf" and summary["beds"][9]["project_bed"] == 1)

# ------------------------------------------------------------------ write_beds
print("write_beds")
tmp = tempfile.mkdtemp(prefix="print_beds_")
try:
    root = Path(tmp) / "Design"
    pdir = root / "aruco" / "print3d"
    pdir.mkdir(parents=True)
    man, rd = manifest_for([("Tag A", 2, TILE, CELLS_INLAID, {}, (0, 0, 0.3))])
    for rel in ("aruco/print3d/Tag_A__tile.stl", "aruco/print3d/Tag_A__cells.stl"):
        (root / rel).write_bytes(rd(rel))
    (pdir / "parts.json").write_text(json.dumps(man), encoding="utf-8")
    bundle = Path(tmp) / "Test.ini"
    bundle.write_text(BUNDLE, encoding="utf-8")
    (pdir / "beds").mkdir()
    (pdir / "beds" / "bed_07.3mf").write_bytes(b"stale")
    (pdir / "beds" / "notes.txt").write_text("mine")
    s = P.write_beds(str(pdir / "parts.json"), vendor_bundle=str(bundle),
                     printer_preset="Test MMU 0.4 nozzle", print_preset="0.20mm TEST",
                     filament_preset="Test PLA")
    check("write_beds: the project, one bed, both copies", s["beds"] == 1 and s["placed"] == 2
          and Path(s["project"][0]).is_file(), s)
    check("write_beds: bed from the printer preset", s["bed_mm"] == [250.0, 210.0])
    check("write_beds: oriented parts written", (pdir / "parts" / "Tag_A__cells.stl").is_file())
    check("write_beds: a stale bed of ours is removed, other files kept",
          not (pdir / "beds" / "bed_07.3mf").exists() and (pdir / "beds" / "notes.txt").exists())
    s = P.write_beds(str(pdir / "parts.json"), str(Path(tmp) / "out"), 250, 220, vendor_bundle=None)
    check("write_beds: no presets -> no project, bed files still written",
          s["project"] == [] and Path(s["bed_files"][0]).is_file() and s["bed_mm"] == [250.0, 220.0], s)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ------------------------------------------------------------------ objects per copy
print("every copy its own object")
man, rd = manifest_for([("Tag · A #1", 3, TILE, CELLS_INLAID, {}, (0, 0, 0.3)),
                        ("Tag B", 1, TILE, CELLS_INLAID, {}, (0, 0, 0.3))])
layout = P.layout_beds(man, rd, P.BedSettings())
z, objects, items = parse_3mf(P.render_beds(layout, prof)["aruco_tags.3mf"])
names = sorted(o["meta"]["name"] for o in objects.values())
check("default: one object per copy, named '<part> (k/n)'",
      names == ["Tag B", "Tag · A #1 (1/3)", "Tag · A #1 (2/3)", "Tag · A #1 (3/3)"], names)
check("...each with a single instance and both volumes",
      all(o["instances_count"] == 1 and len(o["volumes"]) == 2 for o in objects.values()))
layout_i = P.layout_beds(man, rd, P.BedSettings(instances=True))
z, objects, items = parse_3mf(P.render_beds(layout_i, prof)["aruco_tags.3mf"])
check("instances=True: copies are instances of one object",
      sorted((o["meta"]["name"], o["instances_count"]) for o in objects.values())
      == [("Tag B", 1), ("Tag · A #1", 3)])
summ = P.layout_summary(layout)
check("layout.json names each copy's object",
      sorted(p["object"] for b in summ["beds"] for p in b["placed"])[-1] == "Tag · A #1 (3/3)")

# ------------------------------------------------------------------ selection
print("part selection")
man, rd = manifest_for([("Tag A", 2, TILE, CELLS_INLAID, {}, (0, 0, 0.3)),
                        ("Tag B", 1, TILE, CELLS_INLAID, {}, (0, 0, 0.3)),
                        ("Tag C", 1, TILE, CELLS_INLAID, {}, (0, 0, 0.3))])
lay = P.layout_beds(man, rd, P.BedSettings(), include=["Tag B", "Tag Z"])
check("include: only the named parts are placed",
      [lay.parts[p.part].part.name for p in lay.placed] == ["Tag B"])
check("...the others are skipped with a reason",
      sorted(n for n, r in lay.skipped if "selection" in r) == ["Tag A", "Tag C"], lay.skipped)
check("...and an unknown name is a warning", any("'Tag Z'" in w for w in lay.warnings), lay.warnings)
lay = P.layout_beds(man, rd, P.BedSettings(), copies_override={"Tag B": 4, "Tag A": 0})
check("copies_override: counts replaced (B x4, A x0 skipped, C x1)",
      sorted(lay.parts[p.part].part.name for p in lay.placed) == ["Tag B"] * 4 + ["Tag C"]
      and any(n == "Tag A" for n, _ in lay.skipped), (len(lay.placed), lay.skipped))
lay = P.layout_beds(man, rd, P.BedSettings(), copies_override={"Tag B": -1})
check("...a bad count is a warning, the manifest's count stays",
      any("not a count" in w for w in lay.warnings) and
      sum(1 for p in lay.placed if lay.parts[p.part].part.name == "Tag B") == 1)

# ------------------------------------------------------------------ cad orientation + overhang
print("orientation='cad' and the overhang guard")
op = P.orient_part(P.Part("R", "R", 1, [P.Body("tile", "tile", "white", TILE),
                                        P.Body("cells", "cells", "black", CELLS_INLAID)],
                          [tag_frame()]), P.cad_orientation([TILE, CELLS_INLAID]))
check("cad: no rotation at all", op.rotation == ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))
check("cad: the inlaid black stays on top as modelled",
      tuple(round(v, 6) for v in op.cells_z_mm) == (2.4, 3.0), op.cells_z_mm)
mushroom = merge(box(-2, -2, 0, 2, 2, 10), box(-20, -20, 10, 20, 20, 13))
o = P.cad_orientation([mushroom])
check("cad: a part on a 4 mm stalk is kept, with a supports warning",
      o.rule == "cad" and o.warning and "supports" in o.warning, o)
man, rd = manifest_for([("Long", 1, box(-75, -117.5, 0, 75, 117.5, 3),
                         box(-15, -15, 2.4, 15, 15, 3), {}, (0, 0, 0.3))])
lay_flat = P.layout_beds(man, rd, P.BedSettings())
lay_cad = P.layout_beds(man, rd, P.BedSettings(orientation="cad"))
check("a 150 x 235 part: flat lays it tag-x along X (235 x 150, fits); cad keeps it and "
      "cannot turn it, so it is rejected with the reason",
      lay_flat.placed and lay_flat.placed[0].w_mm == 235.0 and not lay_cad.placed
      and "turning is off" in lay_cad.rejected[0][1], (lay_flat.placed, lay_cad.rejected))
mount = box(-30, -30, -10, 30, 30, 0)
o = P.choose_orientation([merge(TILE, mount)], [tag_frame()])     # 40 x 40 face, 60 x 60 part
check("a tag on a bigger mount is not put face down (it would overhang): face up",
      o.rule == "face_up" and "overhang" in o.reason, o)
try:
    P.BedSettings(orientation="sideways").check()
    check("an unknown orientation is an error", False)
except ValueError:
    check("an unknown orientation is an error", True)

# ------------------------------------------------------------------ selection project
print("write_selection_project")
tmp = tempfile.mkdtemp(prefix="print_sel_")
try:
    def put(name, mesh):
        path = os.path.join(tmp, name)
        Path(path).write_bytes(P.write_stl(mesh))
        return path

    def moved(mesh, dx, dy, dz):
        return P.transform_mesh(mesh, ((1, 0, 0), (0, 1, 0), (0, 0, 1)), (dx, dy, dz))
    # One assembly frame: a tag on top of a mount (integral), a loose tag, a spacer.
    objs = [
        {"name": "Mount with tag", "bodies": [
            {"stl": put("mount.stl", box(70, 20, 0, 130, 80, 10)), "role": "other", "name": "mount"},
            {"stl": put("tile2.stl", moved(TILE, 100, 50, 10)), "role": "tile", "name": "tag tile"},
            {"stl": put("cells2.stl", moved(CELLS_INLAID, 100, 50, 10)), "role": "other",
             "name": "aruco cells front"}]},
        {"name": "Tag B", "bodies": [
            {"stl": put("tile1.stl", moved(TILE, -60, 0, 0)), "role": "tile", "name": "tag tile"},
            {"stl": put("cells1.stl", moved(CELLS_INLAID, -60, 0, 0)), "role": "cells",
             "name": "cells"}],
         "frame": {"key": "front", "origin": [-6.0, 0.0, 0.3], "units": "cm",
                   "x_axis": [0, 1, 0], "y_axis": [-1, 0, 0], "z_axis": [0, 0, 1],
                   "spec": {"depth_m": 0.0006, "marker_length_m": 0.03, "layout": "single"}}},
        {"name": "Spacer", "bodies": [
            {"stl": put("spacer.stl", box(-50, -80, 5, -20, -65, 13)), "name": "spacer"}]},
    ]
    out = os.path.join(tmp, "sel", "selection.3mf")
    s = P.write_selection_project(objs, out, vendor_bundle=False)
    check("selection without presets: one bed, one file", s["beds"] == 1 and s["files"] == [out]
          and os.path.isfile(out), s)
    bundle = Path(tmp) / "Test.ini"
    bundle.write_text(BUNDLE, encoding="utf-8")
    s = P.write_selection_project(objs, out, vendor_bundle=str(bundle),
                                  printer_preset="Test MMU 0.4 nozzle", print_preset="0.20mm TEST",
                                  filament_preset="Test PLA")
    z, objects, items = parse_3mf(Path(out).read_bytes())
    by_name = {o["meta"]["name"]: o for o in objects.values()}
    check("selection: one object per selected occurrence",
          sorted(by_name) == ["Mount with tag", "Spacer", "Tag B"], sorted(by_name))
    check("...an object's volumes are its bodies, black by role or by name",
          [(v["name"], v["extruder"]) for v in by_name["Mount with tag"]["volumes"]]
          == [("mount", "1"), ("tag tile", "1"), ("aruco cells front", "2")]
          and [v["extruder"] for v in by_name["Tag B"]["volumes"]] == ["1", "2"]
          and [v["extruder"] for v in by_name["Spacer"]["volumes"]] == ["1"])
    check("...the project carries the presets and colours",
          read_pe_config(z.read("Metadata/Slic3r_PE.config").decode()).get("extruder_colour", "")
          .startswith("#FFFFFF;#000000"))
    mw = by_name["Mount with tag"]
    zs = [p[2] for p in mw["verts"]]
    cells = mw["volumes"][2]
    czs = [mw["verts"][i][2] for t in mw["tris"][cells["first"]:cells["last"] + 1] for i in t]
    check("cad (default): kept as modelled, dropped to z = 0 -- tag still on top of its mount",
          min(zs) == 0.0 and abs(max(czs) - 13.0) < 1e-6 and abs(min(czs) - 12.4) < 1e-6,
          (min(zs), min(czs), max(czs)))
    sp = by_name["Spacer"]
    check("...each object dropped on its own (the spacer floated at z 5)",
          min(p[2] for p in sp["verts"]) == 0.0)
    check("...nothing turned", all(abs(t[0] - 1) < 1e-9 and abs(t[1]) < 1e-9 for _, t in items))
    check("summary names the objects and finds no rejects",
          sorted(s["objects"]) == ["Mount with tag", "Spacer", "Tag B"] and not s["rejected"], s)
    check("summary carries the PrusaSlicer GUI path key", "prusaslicer_exe" in s)
    s = P.write_selection_project(objs, out, orientation="flat", vendor_bundle=str(bundle),
                                  printer_preset="Test MMU 0.4 nozzle", print_preset="0.20mm TEST",
                                  filament_preset="Test PLA")
    check("orientation='flat' uses the frame: Tag B face down, frameless objects fall back",
          s["orientation"] == {"Mount with tag": "fallback", "Tag B": "face_down",
                               "Spacer": "fallback"}, s["orientation"])
    many = [dict(objs[1], copies=30)]
    s = P.write_selection_project(many, os.path.join(tmp, "many.3mf"), vendor_bundle=str(bundle),
                                  printer_preset="Test MMU 0.4 nozzle", print_preset="0.20mm TEST",
                                  filament_preset="Test PLA")
    check("selection: as many beds as needed, still one project",
          s["beds"] == 2 and len(s["files"]) == 1 and len(s["objects"]) == 30
          and "Tag B (30/30)" in s["objects"], (s["beds"], s["files"]))
finally:
    shutil.rmtree(tmp, ignore_errors=True)
gui = P.find_prusaslicer_gui()
check("find_prusaslicer_gui returns an existing file or None", gui is None or os.path.isfile(gui), gui)

# ------------------------------------------------------------------ STL files (no 3MF)
print("write_part_stls (ArUco Export's STL files type)")
tmp = tempfile.mkdtemp(prefix="print_beds_stl_")
try:
    src_dir = Path(tmp) / "raw"
    src_dir.mkdir()
    # the raw STLs in cm (as Fusion writes them in a cm document), raised
    # off the origin: the writer must measure, lay down and drop them
    man, rd = manifest_for([("Tag A", 2, TILE, CELLS_INLAID, {}, (0, 0, 0.3)),
                            ("Tag B", 0, TILE, CELLS_RAISED, {"depth_m": 0.0, "raise_m": 0.0006},
                             (0, 0, 0.3)),
                            ("Tag C", 1, TILE, CELLS_INLAID, {}, (0, 0, 0.3))])
    for e in man["parts"]:
        for b in e["bodies"]:
            name = b["stl"].rsplit("/", 1)[1]
            (src_dir / name).write_bytes(P.write_stl(P.scale_mesh(
                P.read_stl(rd(b["stl"])), 0.1)))
            b["stl"] = name
    man["stl_units"] = "cm"
    (src_dir / "parts.json").write_text(json.dumps(man), encoding="utf-8")
    out = Path(tmp) / "stl"
    out.mkdir()
    (out / "Old_part__tile.stl").write_bytes(b"stale")
    (out / "readme.txt").write_text("mine")
    s = P.write_part_stls(str(src_dir / "parts.json"), str(out), include=["Tag A", "Tag B"])
    got = sorted(os.listdir(out))
    check("STL files: one per body of the chosen parts, a stale STL of ours removed, "
          "other files kept",
          got == ["Tag_A__cells.stl", "Tag_A__tile.stl", "Tag_B__cells.stl", "Tag_B__tile.stl",
                  "readme.txt"], got)
    check("...nothing but STLs (no 3MF, no beds/)",
          not any(n.endswith(".3mf") for n in got) and not (out / "beds").exists())
    check("...a part with 0 visible copies is still written when chosen",
          (out / "Tag_B__tile.stl").is_file())
    check("...the unchosen part is listed as skipped",
          [k["part"] for k in s["skipped"]] == ["Tag C"], s["skipped"])
    a_tile = P.read_stl((out / "Tag_A__tile.stl").read_bytes())
    a_cells = P.read_stl((out / "Tag_A__cells.stl").read_bytes())
    lo, hi = P.bbox([a_tile, a_cells])
    check("...in millimetres (40 x 40 x 3 from a cm STL), resting on z = 0, centred",
          [round(v, 4) for v in (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])] == [40, 40, 3]
          and lo[2] == 0.0 and abs(lo[0] + hi[0]) < 1e-6 and abs(lo[1] + hi[1]) < 1e-6,
          (lo, hi))
    check("...flat: inlaid black face down (the cells on the bed)",
          min(p[2] for p in a_cells.vertices) == 0.0 and s["orientation"]["Tag A"] == "face_down",
          s["orientation"])
    check("...flat: raised black face up", s["orientation"]["Tag B"] == "face_up", s["orientation"])
    s = P.write_part_stls(str(src_dir / "parts.json"), str(out), include=["Tag A"],
                          orientation="cad")
    a_cells = P.read_stl((out / "Tag_A__cells.stl").read_bytes())
    check("as modelled: no turn, the cells stay on top (z 2.4..3.0)",
          [round(v, 4) for v in (min(p[2] for p in a_cells.vertices),
                                 max(p[2] for p in a_cells.vertices))] == [2.4, 3.0]
          and s["orientation"] == {"Tag A": "cad"}, s["orientation"])
    check("...and Tag B's files from the earlier run are cleaned",
          sorted(os.listdir(out)) == ["Tag_A__cells.stl", "Tag_A__tile.stl", "readme.txt"])
    try:
        P.write_part_stls(str(src_dir / "parts.json"), str(out), orientation="sideways")
        check("an unknown orientation is refused", False)
    except ValueError:
        check("an unknown orientation is refused", True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ------------------------------------------------------------------ hygiene
src = (Path(P.__file__)).read_text(encoding="utf-8")
check("print_beds never imports adsk", not re.search(r"^\s*(import|from)\s+adsk", src, re.M))
check("print_beds imports only the stdlib and its own package",
      set(re.findall(r"^(?:import|from)\s+([A-Za-z_]\w*)", src, re.M))
      <= {"__future__", "io", "json", "math", "os", "re", "shutil", "struct", "zipfile",
          "dataclasses",
          "typing"})

print("\n%d passed, %d failed" % (PASS, len(FAIL)))
if FAIL:
    for f in FAIL:
        print("  FAILED:", f)
    sys.exit(1)
