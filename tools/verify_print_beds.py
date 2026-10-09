# -*- coding: utf-8 -*-
"""Oracle for bam_cad_core.print_beds on a REAL ArUco export, with
PrusaSlicer itself as the judge.

    python tools/verify_print_beds.py [--manifest parts.json] [--scratch DIR]
                                      [--no-demo-write] [--stress N]

1. Runs write_beds on the demo export (default
   ~/CADExporter/ArUco_Demo/aruco/print3d/parts.json) with the defaults --
   MK4 MMU3 presets from the PrusaSlicer vendor bundle on this machine,
   their bed (250 x 210 mm) -- into a scratch folder AND into the demo's
   print3d/ folder (aruco_tags.3mf, beds/, parts/).
2. Reads every 3MF back WITHOUT print_beds (zipfile + ElementTree): each
   object's volume ranges tile its mesh exactly, every volume is a closed
   two-manifold, tile = extruder 1 and cells = extruder 2, a face-down
   part's cells reach z = 0, and in the all-beds project every copy lies
   wholly on exactly one PrusaSlicer 2.9 bed (the bed grid is re-derived
   here from MultipleBeds.cpp, not imported).
3. Re-resolves the printer / print / filament chain from the vendor bundle
   with this file's own reader and diffs it against the project's
   Metadata/Slic3r_PE.config.
4. Runs prusa-slicer-console --info on every 3MF and --export-gcode on the
   project and on every bed file, using the config embedded in the 3MF
   (only binary_gcode is overridden, to read the G-code). The CLI slices
   bed 1 of a multi-bed project only, so every bed is also sliced from its
   own file. Per slice: objects, tool changes and the layers they happen
   on, filament per extruder, the wipe tower's extent vs the hole kept for
   it, and every extrusion inside the bed's margin.
5. `--stress N` repeats 2-4 in the scratch folder with every part's copies
   multiplied by N, to exercise several beds in one project.
6. Draws each bed from the 3MF (Pillow) to reports/media/aruco/
   print_bed_NN.png, the stress project's beds as PrusaSlicer lays them out
   to print_beds_stress_overview.png, and writes verify_print_beds.json.

PrusaSlicer always runs with --datadir in the scratch folder, so the
user's own PrusaSlicer configuration is never read or written by it.
Exits 1 on any failed check.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "CADExporter"))

from bam_cad_core import print_beds  # noqa: E402  (the code under test)

DEMO = Path.home() / "CADExporter" / "ArUco_Demo" / "aruco" / "print3d" / "parts.json"
MEDIA = ROOT / "reports" / "media" / "aruco"
NS = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}

FAILS: list = []
CHECKS: list = []


def check(label, ok, detail=""):
    CHECKS.append({"check": label, "ok": bool(ok), "detail": str(detail)[:400]})
    print(("  ok  " if ok else "FAIL  ") + label + ("" if ok or not detail else "  " + str(detail)[:400]))
    if not ok:
        FAILS.append(label)


def find_prusa() -> str:
    for c in (os.path.join(os.environ.get("ProgramW6432", r"C:\Program Files"), "Prusa3D",
                           "PrusaSlicer", "prusa-slicer-console.exe"),
              shutil.which("prusa-slicer-console"), shutil.which("prusa-slicer")):
        if c and os.path.isfile(c):
            return c
    raise SystemExit("prusa-slicer-console not found")


# ------------------------------------------------------------------ independent readers

def bed_grid(index):
    """BedsGrid::index2grid_coords, first quadrant (MultipleBeds.cpp, 2.9.x)."""
    if index == 0:
        return 0, 0
    n = index + 1
    a = 1
    while (a + 1) ** 2 < n:
        a += 1
    n -= a * a
    return (a, n - 1) if n <= a else (n - a - 1, a)


def bed_offset(index, w, d):
    gap = min(100.0, 0.3 * math.hypot(w, d))          # MultipleBeds::bed_gap
    gx, gy = bed_grid(index)
    return gx * (w + gap), gy * (d + gap)


def read_3mf(path):
    z = zipfile.ZipFile(path)
    model = ET.fromstring(z.read("3D/3dmodel.model"))
    objects = {}
    for obj in model.find("m:resources", NS).findall("m:object", NS):
        mesh = obj.find("m:mesh", NS)
        objects[obj.get("id")] = {
            "verts": [(float(v.get("x")), float(v.get("y")), float(v.get("z")))
                      for v in mesh.find("m:vertices", NS)],
            "tris": [(int(t.get("v1")), int(t.get("v2")), int(t.get("v3")))
                     for t in mesh.find("m:triangles", NS)]}
    items = []
    for it in model.find("m:build", NS):
        t = [float(v) for v in it.get("transform").split()]
        items.append((it.get("objectid"), t))
    cfg = ET.fromstring(z.read("Metadata/Slic3r_PE_model.config"))
    for o in cfg.findall("object"):
        obj = objects[o.get("id")]
        obj["name"] = {m.get("key"): m.get("value") for m in o.findall("metadata")}.get("name")
        obj["instances_count"] = int(o.get("instances_count"))
        obj["volumes"] = [{"first": int(v.get("firstid")), "last": int(v.get("lastid")),
                           **{m.get("key"): m.get("value") for m in v.findall("metadata")}}
                          for v in o.findall("volume")]
    towers = []
    if "Metadata/Prusa_Slicer_wipe_tower_information.xml" in z.namelist():
        txt = z.read("Metadata/Prusa_Slicer_wipe_tower_information.xml").decode()
        towers = [(int(b), float(x), float(y)) for b, x, y in re.findall(
            r'bed_idx="(\d+)" position_x="([-\d.]+)" position_y="([-\d.]+)"', txt)]
    config = {}
    if "Metadata/Slic3r_PE.config" in z.namelist():
        for line in reversed(z.read("Metadata/Slic3r_PE.config").decode().splitlines()):
            if not line.strip():
                continue
            mm = re.match(r"^; ([A-Za-z][A-Za-z0-9_]{2,}) = (.*)$", line)
            if not mm or len(line) < 10:
                break
            config[mm.group(1)] = mm.group(2)
    return {"objects": objects, "items": items, "towers": towers, "config": config,
            "members": z.namelist()}


def apply(t, p):
    """3MF transform (row-vector, 12 numbers) applied to point p."""
    x, y, z = p
    return (x * t[0] + y * t[3] + z * t[6] + t[9],
            x * t[1] + y * t[4] + z * t[7] + t[10],
            x * t[2] + y * t[5] + z * t[8] + t[11])


def closed_volume(verts, tris):
    """Every directed edge appears once and its reverse once: closed,
    consistently oriented two-manifold."""
    edges = {}
    for a, b, c in tris:
        for e in ((a, b), (b, c), (c, a)):
            edges[e] = edges.get(e, 0) + 1
    return all(n == 1 and edges.get((e[1], e[0]), 0) == 1 for e, n in edges.items())


def object_parts(layout):
    """{3MF object name: its part's layout.json entry}: a copy is its own
    object "<part> (k/n)" (or, with instances, the part's own name)."""
    by_part = {p["part"]: p for p in layout["parts"]}
    out = {name: p for name, p in by_part.items()}
    for b in layout["beds"]:
        for q in b["placed"]:
            out[q.get("object", q["part"])] = by_part[q["part"]]
    return out


def audit_3mf(path, layout, multi_bed):
    """Structural checks on one 3MF; returns a summary."""
    f = read_3mf(path)
    name = Path(path).name
    parts = object_parts(layout)
    rules = {n: p["orientation"]["rule"] for n, p in parts.items()}
    ranges_ok = closed_ok = ext_ok = True
    face_down_ok = True
    vols = 0
    for oid, obj in f["objects"].items():
        v = obj["volumes"]
        vols += len(v)
        if v[0]["first"] != 0 or v[-1]["last"] != len(obj["tris"]) - 1 or any(
                v[i + 1]["first"] != v[i]["last"] + 1 for i in range(len(v) - 1)):
            ranges_ok = False
        for vol in v:
            tris = obj["tris"][vol["first"]:vol["last"] + 1]
            if not closed_volume(obj["verts"], tris):
                closed_ok = False
            body = {b["name"]: b for b in parts[obj["name"]]["bodies"]}.get(vol["name"])
            want = "2" if body is not None and body["role"] == "cells" else "1"
            if body is None or vol.get("extruder") != str(body["extruder"]) or want != str(body["extruder"]):
                ext_ok = False
            if want == "2" and rules.get(obj["name"]) == "face_down":
                zmin = min(obj["verts"][i][2] for t in tris for i in t)
                if abs(zmin) > 1e-6:
                    face_down_ok = False
    check("%s: volume triangle ranges tile each object's mesh exactly" % name, ranges_ok)
    check("%s: every volume is a closed, consistently oriented mesh" % name, closed_ok)
    check("%s: cells volumes on extruder 2, every other body on extruder 1" % name, ext_ok)
    check("%s: face-down parts' cells sit on the bed (z min = 0)" % name, face_down_ok)
    check("%s: instances_count matches the build items" % name,
          sum(o["instances_count"] for o in f["objects"].values()) == len(f["items"]))
    W, D = layout["bed_mm"]
    m = layout["margin_mm"]
    per_bed = {}
    outside = []
    for oid, t in f["items"]:
        pts = [apply(t, p) for p in f["objects"][oid]["verts"]]
        x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
        y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
        hits = []
        for k in range(9 if multi_bed else 1):
            ox, oy = bed_offset(k, W, D)
            if (x0 >= ox + m - 1e-6 and x1 <= ox + W - m + 1e-6
                    and y0 >= oy + m - 1e-6 and y1 <= oy + D - m + 1e-6):
                hits.append(k)
        if len(hits) != 1:
            outside.append((f["objects"][oid]["name"], round(x0, 2), round(y0, 2)))
        else:
            per_bed[hits[0]] = per_bed.get(hits[0], 0) + 1
    check("%s: every copy lies wholly on exactly one bed, inside the margin" % name,
          not outside, outside[:5])
    check("%s: every 3MF object is a placed copy of a known part" % name,
          all(o["name"] in parts for o in f["objects"].values()))
    return {"file": name, "objects": len(f["objects"]), "instances": len(f["items"]),
            "volumes": vols, "per_bed": {str(k + 1): n for k, n in sorted(per_bed.items())},
            "towers": f["towers"], "has_config": bool(f["config"]), "parsed": f}


# ------------------------------------------------------------------ vendor bundle, independently

def own_resolve(bundle_text, kind, name):
    secs, cur = {}, None
    for line in bundle_text.splitlines():
        mm = re.match(r"^\[(.+)\]\s*$", line)
        if mm:
            cur = secs.setdefault(mm.group(1), {})
        elif cur is not None and "=" in line and not line.lstrip().startswith(("#", ";")):
            k, _, v = line.partition("=")
            cur[k.strip()] = v.strip()

    def chain(key):
        own = secs[key]
        out = {}
        for parent in [p.strip().strip('"') for p in own.get("inherits", "").split(";") if p.strip()]:
            out.update(chain("%s:%s" % (kind, parent)))
        out.update(own)
        return out
    return chain("%s:%s" % (kind, name))


def check_config(config, presets):
    text = Path(presets["source"]).read_text(encoding="utf-8")
    want = {}
    for kind, key in (("printer", "printer"), ("print", "print"), ("filament", "filament")):
        want.update(own_resolve(text, kind, presets[key]))
    skip = {"inherits", "compatible_printers", "compatible_printers_condition", "compatible_prints",
            "compatible_prints_condition", "renamed_from", "alias", "instantiation",
            "extruder_colour", "filament_colour",
            # set by the project on purpose; PrusaSlicer's preset comparison
            # (profile_print_params_same) ignores the *_settings_id keys
            "print_settings_id", "printer_settings_id", "filament_settings_id"}
    diff = [k for k in want if k not in skip and config.get(k) != want[k]]
    check("project config = the vendor bundle's %s / %s / %s chain, key for key (%d keys)"
          % (presets["printer"], presets["print"], presets["filament"], len(want) - len(skip & set(want))),
          not diff, diff[:8])
    check("project config names the presets",
          config.get("printer_settings_id") == presets["printer"]
          and config.get("print_settings_id") == presets["print"]
          and config.get("filament_settings_id", "").count(presets["filament"]) == presets["extruders"])
    ec = config.get("extruder_colour", "").split(";")
    fc = config.get("filament_colour", "").split(";")
    check("extruder 1 shows white, extruder 2 black (extruder_colour and filament_colour)",
          ec[:2] == ["#FFFFFF", "#000000"] and fc[:2] == ["#FFFFFF", "#000000"], (ec, fc))


# ------------------------------------------------------------------ PrusaSlicer CLI

def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def cli_info(prusa, datadir, path):
    code, out = run([prusa, "--datadir", str(datadir), "--info", str(path)])
    blocks = []
    for chunk in re.split(r"^\[[^\]]*\]\s*$", out, flags=re.M)[1:]:
        kv = dict(re.findall(r"^(\w+) = +(.*)$", chunk, flags=re.M))
        blocks.append(kv)
    return code, blocks


def parse_gcode(path):
    x = y = None
    z = 0.0
    role = None
    boxes, toolchanges, objects = {}, [], 0
    head = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith(";TYPE:"):
                role = line[6:].strip()
                continue
            if line.startswith(";Z:"):
                z = float(line[3:])
                continue
            if line.startswith("M486 A"):
                objects += 1
                continue
            mt = re.match(r"^T(\d+)\s*$", line)
            if mt:
                toolchanges.append((round(z, 3), int(mt.group(1))))
                continue
            if line.startswith("; ") and " = " in line:
                k, _, v = line[2:].partition(" = ")
                if k in ("filament used [mm]", "filament used [g]", "total toolchanges",
                         "estimated printing time (normal mode)", "printer_settings_id",
                         "printer_model", "total filament used for wipe tower [g]"):
                    head[k] = v.strip()
                continue
            if not (line.startswith("G1 ") or line.startswith("G0 ")):
                continue
            code = line.split(";")[0]
            nx = re.search(r"X([-\d.]+)", code)
            ny = re.search(r"Y([-\d.]+)", code)
            ne = re.search(r"E([-\d.]+)", code)
            px, py = x, y
            if nx:
                x = float(nx.group(1))
            if ny:
                y = float(ny.group(1))
            if ne and float(ne.group(1)) > 0 and (nx or ny) and x is not None and y is not None:
                b = boxes.setdefault(role, [1e9, 1e9, -1e9, -1e9])
                for qx, qy in ((px, py), (x, y)):
                    if qx is None or qy is None:
                        continue
                    b[0], b[1] = min(b[0], qx), min(b[1], qy)
                    b[2], b[3] = max(b[2], qx), max(b[3], qy)
    return {"boxes": boxes, "toolchanges": toolchanges, "objects": objects, "header": head}


def slice_one(prusa, datadir, ascii_ini, path, out, expect, layout, bed_index):
    code, log = run([prusa, "--datadir", str(datadir), "--load", str(ascii_ini),
                     "--export-gcode", "--output", str(out), str(path)])
    label = Path(path).name
    if code != 0 or not Path(out).is_file():
        check("%s: PrusaSlicer slices it" % label, False, log[-400:])
        return None
    g = parse_gcode(out)
    h = g["header"]
    used_mm = [float(v) for v in h.get("filament used [mm]", "0").split(",")]
    used_g = [float(v) for v in h.get("filament used [g]", "0").split(",")]
    tc = int(h.get("total toolchanges", "0"))
    check("%s: sliced with %s" % (label, h.get("printer_settings_id")), code == 0)
    check("%s: %d objects in the G-code (expected %d)" % (label, g["objects"], expect),
          g["objects"] == expect)
    check("%s: both filaments used (white %.2f g, black %.2f g) with %d tool changes"
          % (label, used_g[0], used_g[1] if len(used_g) > 1 else 0, tc),
          len(used_g) > 1 and used_g[0] > 0 and used_g[1] > 0 and tc > 0)
    parts = object_parts(layout)
    spans = sorted({tuple(parts[q.get("object", q["part"])]["black_z_mm"])
                    for q in layout["beds"][bed_index]["placed"]
                    if parts[q.get("object", q["part"])]["black_z_mm"]})
    if spans:
        stray = [zz for zz, tool in g["toolchanges"] if tool == 1 and not any(
            z0 - 1e-6 < zz <= z1 + 1e-6 for z0, z1 in spans)]
        check("%s: black (T1) only in layers that hold black cells %s"
              % (label, [[round(v, 2) for v in sp] for sp in spans]), not stray, stray[:5])
    W, D = layout["bed_mm"]
    m = layout["margin_mm"]
    bad = {r: b for r, b in g["boxes"].items() if r not in ("Wipe tower", "Custom") and
           (b[0] < m - 0.5 or b[1] < m - 0.5 or b[2] > W - m + 0.5 or b[3] > D - m + 0.5)}
    check("%s: every part's extrusion inside the %.0f mm margin" % (label, m), not bad, bad)
    tower = layout["beds"][bed_index]["wipe_tower_mm"]
    wt = g["boxes"].get("Wipe tower")
    if tower and wt:
        inside = (wt[0] >= tower[0] - 0.5 and wt[1] >= tower[1] - 0.5
                  and wt[2] <= tower[0] + tower[2] + 0.5 and wt[3] <= tower[1] + tower[3] + 0.5)
        check("%s: wipe tower %.1f x %.1f mm at (%.1f, %.1f) inside its kept hole %s"
              % (label, wt[2] - wt[0], wt[3] - wt[1], wt[0], wt[1], [round(v, 1) for v in tower]),
              inside)
    return {"file": label, "objects": g["objects"], "toolchanges": tc,
            "toolchange_layers": g["toolchanges"], "filament_mm": used_mm, "filament_g": used_g,
            "wipe_tower_g": h.get("total filament used for wipe tower [g]"),
            "time": h.get("estimated printing time (normal mode)"),
            "printer": h.get("printer_settings_id"), "printer_model": h.get("printer_model"),
            "wipe_tower_extent": None if not wt else [round(v, 2) for v in wt],
            "extrusion_extent": {r: [round(v, 2) for v in b] for r, b in g["boxes"].items()}}


# ------------------------------------------------------------------ pictures

def _font(size):
    from PIL import ImageFont
    for name in ("arial.ttf", "DejaVuSans.ttf", "segoeui.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def draw_bed(parsed, layout, bed_index, out_png, world=(0.0, 0.0), title=""):
    """Top view of one bed straight from the 3MF: tile volumes white, cells
    black (dimmed when the pattern faces down), tower hole, names."""
    from PIL import Image, ImageDraw
    W, D = layout["bed_mm"]
    m = layout["margin_mm"]
    s = 4.0
    pad, top = 30, 70
    img = Image.new("RGB", (int(W * s) + 2 * pad, int(D * s) + pad + top), "#f4f4f1")
    dr = ImageDraw.Draw(img, "RGBA")
    ox, oy = world

    def P(x, y):
        return (pad + (x - ox) * s, top + (D - (y - oy)) * s)
    dr.rectangle([P(0 + ox, D + oy), P(W + ox, 0 + oy)], fill="#dcdcd6", outline="#888888", width=2)
    dr.rectangle([P(m + ox, D - m + oy), P(W - m + ox, m + oy)], outline="#9a9a9a", width=1)
    rules = {n: p["orientation"]["rule"] for n, p in object_parts(layout).items()}
    tower = layout["beds"][bed_index]["wipe_tower_mm"]
    if tower:
        x, y, w, d = tower
        dr.rectangle([P(x + ox, y + d + oy), P(x + w + ox, y + oy)], fill="#f2d7a8", outline="#c08a30")
        dr.text(((P(x + ox, 0)[0] + P(x + w + ox, 0)[0]) / 2, P(0, y + d / 2 + oy)[1]),
                "wipe tower", fill="#8a5a10", font=_font(16), anchor="mm")
    labels = []
    # One mask per kind, painted once: overlapping triangles would leave seams.
    masks = {k: Image.new("L", img.size, 0) for k in ("tile", "down", "up")}
    mdraw = {k: ImageDraw.Draw(v) for k, v in masks.items()}
    for oid, t in parsed["items"]:
        obj = parsed["objects"][oid]
        pts = [apply(t, p) for p in obj["verts"]]
        cx = (min(p[0] for p in pts) + max(p[0] for p in pts)) / 2
        cy = (min(p[1] for p in pts) + max(p[1] for p in pts)) / 2
        if not (ox <= cx <= ox + W and oy <= cy <= oy + D):
            continue
        for vol in obj["volumes"]:
            kind = "tile"
            if vol.get("extruder") == "2":
                kind = "down" if rules.get(obj["name"]) == "face_down" else "up"
            for tri in obj["tris"][vol["first"]:vol["last"] + 1]:
                a, b, c = (pts[i] for i in tri)
                nz = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
                if nz > 1e-9:                      # upward-facing in the top view
                    mdraw[kind].polygon([P(*a[:2]), P(*b[:2]), P(*c[:2])], fill=255)
        labels.append((P(cx, cy), obj["name"], rules.get(obj["name"], "")))
    img.paste((255, 255, 255), mask=masks["tile"])
    img.paste((17, 17, 17), mask=masks["down"].point(lambda v: v * 110 // 255))
    img.paste((17, 17, 17), mask=masks["up"])
    dr = ImageDraw.Draw(img, "RGBA")
    for (x, y), name, rule in labels:
        f = _font(13 if len(name) < 22 else 11)
        half = 3.6 * len(name) * (13 if len(name) < 22 else 11) / 13.0
        dr.rectangle([x - half - 4, y - 10, x + half + 4, y + 8], fill=(255, 255, 255, 215))
        dr.text((x, y - 1), name, fill="#000000", font=f, anchor="mm")
        dr.text((x, y + 16), rule.replace("_", " "), fill="#444444", font=_font(11), anchor="mm")
    dr.text((pad, 14), title, fill="#222222", font=_font(20))
    dr.text((pad, 42), "top view from the 3MF, front edge at the bottom; dimmed black = pattern "
            "face down", fill="#666666", font=_font(14))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png)


def draw_overview(parsed, layout, beds, out_png, title):
    """All beds of a project where PrusaSlicer 2.9 puts them."""
    from PIL import Image, ImageDraw
    W, D = layout["bed_mm"]
    offs = [bed_offset(k, W, D) for k in range(beds)]
    X1 = max(o[0] for o in offs) + W
    Y1 = max(o[1] for o in offs) + D
    s = 1.6
    pad, top = 20, 50
    img = Image.new("RGB", (int(X1 * s) + 2 * pad, int(Y1 * s) + pad + top), "#f4f4f1")
    dr = ImageDraw.Draw(img, "RGBA")

    def P(x, y):
        return (pad + x * s, top + (Y1 - y) * s)
    for k, (bx, by) in enumerate(offs):
        dr.rectangle([P(bx, by + D), P(bx + W, by)], fill="#dcdcd6", outline="#888888", width=2)
        dr.text(P(bx + 4, by + D - 4), "bed %d" % (k + 1), fill="#333333", font=_font(16))
    masks = {k: Image.new("L", img.size, 0) for k in ("1", "2")}
    mdraw = {k: ImageDraw.Draw(v) for k, v in masks.items()}
    for oid, t in parsed["items"]:
        obj = parsed["objects"][oid]
        pts = [apply(t, p) for p in obj["verts"]]
        for vol in obj["volumes"]:
            kind = "2" if vol.get("extruder") == "2" else "1"
            for tri in obj["tris"][vol["first"]:vol["last"] + 1]:
                a, b, c = (pts[i] for i in tri)
                if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) > 1e-9:
                    mdraw[kind].polygon([P(*a[:2]), P(*b[:2]), P(*c[:2])], fill=255)
    img.paste((255, 255, 255), mask=masks["1"])
    img.paste((17, 17, 17), mask=masks["2"].point(lambda v: v * 120 // 255))
    dr = ImageDraw.Draw(img, "RGBA")
    for bed_k, x, y in parsed["towers"]:
        bx, by = offs[bed_k]
        tw, td = layout["wipe_tower_mm"]
        b = 2.0
        dr.rectangle([P(bx + x - b, by + y - b + td), P(bx + x - b + tw, by + y - b)],
                     fill="#f2d7a8", outline="#c08a30")
    dr.text((pad, 12), title, fill="#222222", font=_font(20))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png)


# ------------------------------------------------------------------ one run

def verify_run(tag, out_dir, layout_json, prusa, datadir, ascii_ini, pngs):
    layout = json.loads(Path(layout_json).read_text(encoding="utf-8"))
    report = {"run": tag, "beds": layout["totals"]["beds"], "placed": layout["totals"]["placed"],
              "utilisation": [b["utilisation"] for b in layout["beds"]],
              "presets": layout["project"]["presets"], "files": {}, "info": {}, "slices": {}}
    print("\n== %s: %d beds, %d copies, utilisation %s" % (
        tag, report["beds"], report["placed"], report["utilisation"]))
    projects = [out_dir / f for f in layout["project"]["files"]]
    for proj in projects:
        check("%s exists" % proj.name, proj.is_file())
    bed_files = [out_dir / b["file"] for b in layout["beds"]]
    audits = {}
    for path in projects + bed_files:
        if not path.is_file():
            continue
        a = audit_3mf(path, layout, multi_bed=path in projects)
        audits[path] = a
        report["files"][path.name] = {k: v for k, v in a.items() if k != "parsed"}
        code, blocks = cli_info(prusa, datadir, path)
        report["info"][path.name] = {"exit": code, "objects": len(blocks),
                                     "manifold": sorted({b.get("manifold") for b in blocks}),
                                     "parts_per_object": sorted({b.get("number_of_parts") for b in blocks})}
        parts = object_parts(layout)
        want_parts = [str(len(parts[a["parsed"]["objects"][oid]["name"]]["bodies"]))
                      for oid in sorted(a["parsed"]["objects"], key=int)]
        got_parts = [b.get("number_of_parts", "").strip() for b in blocks]
        check("%s: --info reads %d objects, all manifold, one shell per body"
              % (path.name, len(blocks)),
              code == 0 and len(blocks) == a["objects"]
              and {b.get("manifold") for b in blocks} == {"yes"} and got_parts == want_parts,
              (report["info"][path.name], got_parts, want_parts))
    for proj in projects:
        if proj in audits:
            per_bed = audits[proj]["per_bed"]
            group = [b for b in layout["beds"] if b["project"] == proj.name]
            check("%s: copies per PrusaSlicer bed match the layout %s"
                  % (proj.name, [len(b["placed"]) for b in group]),
                  [per_bed.get(str(b["project_bed"]), 0) for b in group]
                  == [len(b["placed"]) for b in group], per_bed)
            check("%s: one wipe tower per bed, each inside its hole" % proj.name,
                  sorted(t[0] for t in audits[proj]["towers"]) == list(range(len(group))))
            if layout["project"]["presets"]:
                check_config(audits[proj]["parsed"]["config"], layout["project"]["presets"])
    gdir = out_dir / "_gcode"
    gdir.mkdir(exist_ok=True)
    for proj in projects:
        first = next(b for b in layout["beds"] if b["project"] == proj.name and b["project_bed"] == 1)
        r = slice_one(prusa, datadir, ascii_ini, proj, gdir / (proj.stem + ".gcode"),
                      len(first["placed"]), layout, first["bed"] - 1)
        report["slices"][proj.name + " (CLI: bed 1 only)"] = r
    for b, path in zip(layout["beds"], bed_files):
        if not path.is_file():
            continue
        r = slice_one(prusa, datadir, ascii_ini, path, gdir / (path.stem + ".gcode"),
                      len(b["placed"]), layout, b["bed"] - 1)
        report["slices"][path.name] = r
    for name, png in pngs.items():
        if name == "overview" and projects and projects[0] in audits:
            draw_overview(audits[projects[0]]["parsed"], layout,
                          len([b for b in layout["beds"] if b["project"] == projects[0].name]),
                          png, "%s: %s as PrusaSlicer 2.9 lays out its beds" % (tag, projects[0].name))
            report.setdefault("pngs", []).append(str(png))
        elif isinstance(name, int):
            b = layout["beds"][name]
            if bed_files[name] in audits:
                parsed, world = audits[bed_files[name]]["parsed"], (0.0, 0.0)
            elif out_dir / b["project"] in audits:
                parsed, world = audits[out_dir / b["project"]]["parsed"], tuple(b["project_offset_mm"])
            else:
                continue
            draw_bed(parsed, layout, name, png, world=world,
                     title="%s \u00b7 bed %d of %d \u00b7 %d objects \u00b7 %.0f%% of the usable area"
                     % (tag, name + 1, len(layout["beds"]), len(b["placed"]), 100 * b["utilisation"]))
            report.setdefault("pngs", []).append(str(png))
    return report


def box_stl(x0, y0, z0, x1, y1, z1):
    """A closed box as binary STL, written here (not by print_beds)."""
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    out = bytearray(b"verify_print_beds box".ljust(80, b" ")) + struct.pack("<I", 12)
    for a, b, c, d in quads:
        for t in ((a, b, c), (a, c, d)):
            out += struct.pack("<3f", 0, 0, 0)
            for i in t:
                out += struct.pack("<3f", *v[i])
            out += struct.pack("<H", 0)
    return bytes(out)


def moved_stl(data, dx, dy, dz):
    """Translate a binary STL (the demo's tag bodies into an 'assembly' frame)."""
    n = struct.unpack_from("<I", data, 80)[0]
    out = bytearray(data[:84])
    for i in range(n):
        rec = list(struct.unpack_from("<12fH", data, 84 + 50 * i))
        for k in range(3, 12, 3):
            rec[k] += dx
            rec[k + 1] += dy
            rec[k + 2] += dz
        out += struct.pack("<12fH", *rec)
    return bytes(out)


def selection_run(manifest, scratch, prusa, datadir, ascii_ini):
    """write_selection_project on the demo's STLs placed in one assembly
    frame: a loose tag, a tag printed integral on a mount, and a spacer."""
    data = json.loads(manifest.read_text(encoding="utf-8"))
    read = print_beds.manifest_reader(str(manifest))
    by_id = {p["tags"][0]["spec"]["marker_id"]: p for p in data["parts"] if p.get("tags")}
    out = scratch / "selection"
    out.mkdir(exist_ok=True)

    def body(part, b, dx=0.0, dy=0.0, dz=0.0):
        path = out / ("%s__%s.stl" % (print_beds.slug(part["component"]), print_beds.slug(b["name"])))
        path.write_bytes(moved_stl(read(b["stl"]), dx, dy, dz))
        return {"stl": str(path), "role": b["role"], "name": b["name"]}
    t1, t2 = by_id[1], by_id[2]
    (out / "mount.stl").write_bytes(box_stl(70, 20, 0, 130, 80, 10))
    (out / "spacer.stl").write_bytes(box_stl(-50, -80, 5, -20, -65, 13))
    objects = [
        {"name": t1["component"], "bodies": [body(t1, b) for b in t1["bodies"]]},
        {"name": "Mount with " + t2["component"],
         "bodies": [{"stl": str(out / "mount.stl"), "role": "other", "name": "mount"}]
         + [body(t2, b, 100.0, 50.0, 10.0) for b in t2["bodies"]]},
        {"name": "Spacer", "bodies": [{"stl": str(out / "spacer.stl"), "role": "other",
                                       "name": "spacer"}]},
    ]
    s = print_beds.write_selection_project(objects, str(out / "aruco_selection.3mf"))
    check("selection: %d objects placed on %d bed(s), nothing rejected"
          % (s["placed"], s["beds"]), s["placed"] == 3 and not s["rejected"], s["rejected"])
    check("selection: every object kept as modelled (orientation 'cad')",
          set(s["orientation"].values()) == {"cad"}, s["orientation"])
    check("selection: PrusaSlicer GUI found for the add-in to open the file (%s)"
          % s["prusaslicer_exe"], bool(s["prusaslicer_exe"]) and os.path.isfile(s["prusaslicer_exe"]))
    (out / "layout.json").write_text(json.dumps(s["layout"], indent=2, ensure_ascii=False),
                                     encoding="utf-8")
    r = verify_run("selection (cad)", out, out / "layout.json", prusa, datadir, ascii_ini,
                   {0: MEDIA / "print_selection_bed_01.png"})
    r["summary"] = {k: v for k, v in s.items() if k != "layout"}
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=str(DEMO))
    ap.add_argument("--scratch", default=None)
    ap.add_argument("--no-demo-write", action="store_true")
    ap.add_argument("--stress", type=int, default=4, help="copies multiplier for the multi-bed run")
    args = ap.parse_args()
    prusa = find_prusa()
    manifest = Path(args.manifest)
    scratch = Path(args.scratch or tempfile.mkdtemp(prefix="verify_print_beds_"))
    scratch.mkdir(parents=True, exist_ok=True)
    datadir = scratch / "psdata"
    datadir.mkdir(exist_ok=True)
    ascii_ini = scratch / "ascii_gcode.ini"
    ascii_ini.write_text("binary_gcode = 0\n", encoding="ascii")
    print("PrusaSlicer:", prusa)
    print("scratch:", scratch)
    reports = []

    demo_out = scratch / "demo"
    s = print_beds.write_beds(str(manifest), str(demo_out))
    check("demo: write_beds placed every copy (%d of %d), nothing rejected"
          % (s["placed"], s["copies_requested"]),
          s["placed"] == s["copies_requested"] and not s["rejected"], s["rejected"])
    check("demo: the all-beds project was written", bool(s["project"]), s["warnings"])
    pngs = {b: MEDIA / ("print_bed_%02d.png" % (b + 1)) for b in range(s["beds"])}
    reports.append(verify_run("demo", demo_out, Path(s["layout"]), prusa, datadir, ascii_ini, pngs))
    reports[-1]["warnings"] = s["warnings"]
    if not args.no_demo_write:
        s2 = print_beds.write_beds(str(manifest))
        same = all(Path(a).read_bytes() == (demo_out / Path(a).relative_to(s2["out_dir"])).read_bytes()
                   for a in s2["project"] + s2["bed_files"])
        check("demo folder written (%s) with the same bytes as the scratch run" % s2["out_dir"], same)
        reports[-1]["written_to"] = s2["out_dir"]

    if args.stress and args.stress > 1:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data = copy.deepcopy(data)
        for p in data["parts"]:
            p["copies"] = max(1, int(p.get("copies", 1))) * args.stress
        bundle = print_beds.find_vendor_bundle()
        profile = print_beds.load_prusa_profile(Path(bundle).read_text(encoding="utf-8"),
                                                source=bundle)
        st = print_beds.BedSettings(bed_w_mm=profile.bed_mm[0], bed_d_mm=profile.bed_mm[1],
                                    bed_h_mm=profile.max_height_mm)
        lay = print_beds.layout_beds(data, print_beds.manifest_reader(str(manifest)), st)
        out = scratch / "stress"
        print_beds.write_files(str(out), print_beds.render_beds(lay, profile))
        pngs = {"overview": MEDIA / "print_beds_stress_overview.png"}
        r = verify_run("stress x%d" % args.stress, out, out / "beds" / "layout.json", prusa,
                       datadir, ascii_ini, pngs)
        reports.append(r)

    reports.append(selection_run(manifest, scratch, prusa, datadir, ascii_ini))

    result = {"tool": "tools/verify_print_beds.py", "prusaslicer": prusa, "scratch": str(scratch),
              "checks": len(CHECKS), "failed": FAILS, "runs": reports, "all": CHECKS}
    MEDIA.mkdir(parents=True, exist_ok=True)
    (MEDIA / "verify_print_beds.json").write_text(json.dumps(result, indent=2, ensure_ascii=False)
                                                  + "\n", encoding="utf-8")
    print("\n%d checks, %d failed -> %s" % (len(CHECKS), len(FAILS), MEDIA / "verify_print_beds.json"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
