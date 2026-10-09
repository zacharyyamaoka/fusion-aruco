"""Offline tests for bam_cad_core.markers — tags, per-copy overrides, serials.

Run:  python tests/test_markers.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "CADExporter"))

from bam_cad_core import markers as M  # noqa: E402
from bam_cad_core.markers import (OccurrenceRef, OverrideRecord, TagDef,  # noqa: E402
                                  TagSpec)

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


def T(x, y, z):
    return [1, 0, 0, x, 0, 1, 0, y, 0, 0, 1, z, 0, 0, 0, 1]


def tag(component="Tag", key="top", marker_id=7, dictionary="DICT_4X4_50"):
    return TagDef(key=key, component=component,
                  spec=TagSpec(dictionary=dictionary, marker_id=marker_id,
                               marker_length_m=0.03),
                  origin=[0.0, 0.0, 0.3])


def arm_tree(n_arms=1, n_act=6):
    """Robot root > Arm:i > Actuator:k > Tag:1 (Tag holds the ArUco feature)."""
    occs = []
    for a in range(1, n_arms + 1):
        arm = f"Arm:{a}"
        occs.append(OccurrenceRef(arm, "Arm", T(100 * a, 0, 0)))
        for k in range(1, n_act + 1):
            act = f"{arm}+Actuator:{k}"
            occs.append(OccurrenceRef(act, "Actuator", T(100 * a, 10 * k, 0)))
            occs.append(OccurrenceRef(f"{act}+Tag:1", "Tag", T(100 * a, 10 * k, 5)))
    return occs


# --------------------------------------------------------------- resolution
print("resolve: inheritance and outermost-wins")
occs = arm_tree(1, 6)
res = M.resolve([tag()], [], occs, "Robot")
check("one instance per copy", len(res.instances) == 6, len(res.instances))
check("all inherit the modelled id", {i.spec.marker_id for i in res.instances} == {7})
dups = res.duplicates()
check("six copies of one tag collide", list(dups) == ["aruco4x4#7"]
      and len(dups["aruco4x4#7"]) == 6, dups)
check("duplicate warning names every copy",
      "6 tags print the same marker aruco4x4#7" in M.describe_duplicates(res)[0])

arm_level = OverrideRecord("Arm", "Actuator:3", {"Tag:1|top": {"marker_id": 17}})
res = M.resolve([tag()], [arm_level], occs, "Robot")
i3 = next(i for i in res.instances if i.path == "Arm:1+Actuator:3+Tag:1")
check("arm-level override reaches its copy", i3.spec.marker_id == 17)
check("...and records where it came from",
      i3.winning.owner == "Arm" and i3.winning.occurrence == "Actuator:3"
      and i3.owner_path == "Arm:1", (i3.winning, i3.owner_path))
check("...and needs a variant part", i3.needs_variant)
others = [i for i in res.instances if i is not i3]
check("other copies untouched", all(i.spec.marker_id == 7 for i in others))
check("unchanged copies need no variant", not any(i.needs_variant for i in others))

root_level = OverrideRecord("Robot", "Arm:1", {"Actuator:3+Tag:1|top": {"marker_id": 23}})
res = M.resolve([tag()], [arm_level, root_level], occs, "Robot")
i3 = next(i for i in res.instances if i.path == "Arm:1+Actuator:3+Tag:1")
check("outermost (root) override wins over the arm's", i3.spec.marker_id == 23)
check("both layers kept, innermost first",
      [l.level for l in i3.layers] == ["model", "Arm:1", ""], [l.level for l in i3.layers])

check("dictionary is overridable", M.resolve(
    [tag()], [OverrideRecord("Arm", "Actuator:1", {"Tag:1|top": {"dictionary": "DICT_5X5_100"}})],
    occs, "Robot").instances[0].spec.dictionary == "DICT_5X5_100")

warns = []
bad = OverrideRecord.from_dict({"owner": "Arm", "occurrence": "Actuator:1",
                                "values": {"Tag:1|top": {"marker_id": 3,
                                                         "marker_length_m": 0.5}}},
                               warns.append)
check("geometry fields are refused per copy",
      bad.values == {"Tag:1|top": {"marker_id": 3}} and any("cannot" in w for w in warns), warns)

res = M.resolve([tag()], [OverrideRecord("Arm", "Actuator:9", {"Tag:1|top": {"marker_id": 4}})],
                occs, "Robot")
check("stale override is reported", any("stale" in w for w in res.warnings), res.warnings)

res = M.resolve([tag()], [OverrideRecord("Arm", "Actuator:1", {"Tag:1|top": {"marker_id": 50}})],
                occs, "Robot")
check("id outside the dictionary is reported",
      any("outside DICT_4X4_50" in w for w in res.warnings), res.warnings)

check("4X4_50 #3 and 4X4_1000 #3 are one pattern",
      M.pattern_key("DICT_4X4_50", 3) == M.pattern_key("DICT_4X4_1000", 3))
check("5X5 #3 is a different pattern",
      M.pattern_key("DICT_5X5_50", 3) != M.pattern_key("DICT_4X4_50", 3))

# hidden copies don't collide
occs_h = arm_tree(1, 2)
occs_h[1].visible = False          # Arm:1+Actuator:1 (the host)
occs_h[2].visible = False          # ...and therefore its tag
res = M.resolve([tag()], [], occs_h, "Robot")
check("a hidden copy does not count as a collision", not res.duplicates())
occs_v = arm_tree(1, 2)
occs_v[2].visible = False          # only the TAG occurrence is off (a variant took over)
res = M.resolve([tag()], [], occs_v, "Robot")
check("a template switched off for a variant still counts", len(res.duplicates()) == 1)

# variant components are not extra tags
variant = tag(component="Tag (4X4_50 #17)", marker_id=17)
variant.variant_of = {"template": "Tag", "slot": "Actuator:3+Tag:1|top"}
res = M.resolve([tag(), variant], [], occs + [OccurrenceRef("Arm:1+Tag (4X4_50 #17):1",
                                                            "Tag (4X4_50 #17)")], "Robot")
check("a variant part is not counted as its own tag", len(res.instances) == 6)

# ------------------------------------------------------------------- poses
print("world poses")
res = M.resolve([tag()], [], arm_tree(1, 1), "Robot")
w = res.instances[0].world
check("world = occurrence transform x tag frame",
      [round(w[3], 6), round(w[7], 6), round(w[11], 6)] == [100, 10, 5.3], w)
res_mm = M.resolve([tag()], [], [OccurrenceRef("Tag:1", "Tag", T(0, 0, 0))], "Robot",
                   design_units="mm")
check("cm tag frame scaled into mm design units", abs(res_mm.instances[0].world[11] - 3.0) < 1e-12)

rot = [0, -1, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]  # yaw +90
r, p, y = M.rpy_from_matrix(rot)
check("rpy of a +90 deg yaw", abs(y - math.pi / 2) < 1e-12 and abs(r) < 1e-12 and abs(p) < 1e-12)
rx = [1, 0, 0, 0, 0, 0, -1, 0, 0, 1, 0, 0, 0, 0, 0, 1]  # roll +90
r, p, y = M.rpy_from_matrix(rx)
check("rpy of a +90 deg roll", abs(r - math.pi / 2) < 1e-12)

# ----------------------------------------------------------------- serials
print("serials: deepest level that works")
occs = arm_tree(1, 6)
planned, res = M.plan_serials([tag()], [], occs, "Robot")
check("one arm: no collisions left", not res.duplicates(), res.duplicates())
check("five serials planned (copy 1 keeps its id)", len(planned) == 5, len(planned))
check("all written in Arm, on the actuator occurrences",
      all(p.owner == "Arm" and p.occurrence.startswith("Actuator:") and p.key == "Tag:1|top"
          for p in planned), planned)
check("new ids are the lowest free ones",
      sorted(p.marker_id for p in planned) == [0, 1, 2, 3, 4], [p.marker_id for p in planned])

occs2 = arm_tree(2, 6)
planned2, res2 = M.plan_serials([tag()], [], occs2, "Robot")
check("two arms: no collisions left", not res2.duplicates(), res2.duplicates())
arm_written = [p for p in planned2 if p.owner == "Arm"]
root_written = [p for p in planned2 if p.owner == ""]
check("arm-level serials written once, in Arm", len(arm_written) == 5, planned2)
check("second arm re-serialled at the root", len(root_written) == 6
      and all(p.occurrence == "Arm:2" for p in root_written), root_written)
ids = [i.spec.marker_id for i in res2.instances]
check("12 distinct ids", len(set(ids)) == 12, ids)

# applying the plan reproduces the planned resolution
ovs = M.apply_planned([], planned2)
again = M.resolve([tag()], ovs, occs2, "Robot")
check("apply_planned + resolve == plan_serials' answer",
      [i.spec.marker_id for i in again.instances] == ids)
check("override records are JSON round-trippable",
      [OverrideRecord.from_dict(json.loads(json.dumps(o.to_dict())), print).to_dict()
       for o in ovs] == [o.to_dict() for o in ovs])

# existing ids elsewhere are respected
occs3 = arm_tree(1, 3) + [OccurrenceRef("Base:1", "Base", T(0, 0, 0))]
planned3, res3 = M.plan_serials([tag(), tag(component="Base", key="front", marker_id=0)],
                                [], occs3, "Robot")
check("a serial never reuses an id a different tag already prints",
      0 not in [p.marker_id for p in planned3] and not res3.duplicates(), planned3)

# ---------------------------------------------------------------- geometry
print("geometry")
spec = TagSpec(marker_length_m=0.03)
grid = [[1] * 6] + [[1, 0, 1, 0, 1, 1]] + [[1, 1, 0, 1, 0, 1]] + [[1] * 6] * 3
boxes = M.cell_boxes(spec, grid=grid, overlap_m=0.0)
check("boxes stay inside the black square",
      all(-0.015 - 1e-12 <= v <= 0.015 + 1e-12 for b in boxes for v in b))
area = sum((b[2] - b[0]) * (b[3] - b[1]) for b in boxes)
black = sum(map(sum, grid))
check("box area == black cells x module^2", abs(area - black * spec.module_m ** 2) < 1e-12)
check("growth is OFF by default: cells are exact",
      M.cell_boxes(spec, grid=grid) == boxes)
grown = M.cell_boxes(spec, grid=grid, overlap_m=M.CELL_OVERLAP_M)
check("grown boxes overlap at diagonal contacts",
      any(g[0] < b[0] or g[1] < b[1] for g, b in zip(grown, boxes)))
check("grow_m on the spec switches it on",
      M.cell_boxes(TagSpec(marker_length_m=0.03, grow_m=M.CELL_OVERLAP_M), grid=grid) == grown)
check("growth never leaves the square",
      min(b[0] for b in grown) >= -0.015 - 1e-12 and max(b[2] for b in grown) <= 0.015 + 1e-12)
check("top row of the grid is +y", abs(boxes[0][3] - 0.015) < 1e-12, boxes[0])
check("tile side = marker + 2 quiet modules",
      abs(spec.tile_side_m - (0.03 + 2 * 0.005)) < 1e-12, spec.tile_side_m)
check("fit_square inverts tile_side", abs(M.fit_square(0.04, 0.05, "DICT_4X4_50") - 0.03) < 1e-12)

# ------------------------------------------------------------------ boards
print("boards: an id block per copy")
grid = TagDef(key="board", component="Tag",
              spec=TagSpec(layout="grid", rows=2, cols=2, marker_length_m=0.02,
                           separation_m=0.004, marker_id=0))
occs_b = arm_tree(1, 3)
res = M.resolve([grid], [], occs_b, "Robot")
check("a 2x2 grid prints 4 ids", res.instances[0].spec.ids == [0, 1, 2, 3])
check("three copies collide on all four", len(res.duplicates()) == 4)
check("...reported once per pair of copies, not per marker",
      len(M.describe_duplicates(res)) == 1, M.describe_duplicates(res))
planned_b, res_b = M.plan_serials([grid], [], occs_b, "Robot")
blocks = sorted(tuple(i.spec.ids) for i in res_b.instances)
check("each copy gets its own contiguous block",
      blocks == [(0, 1, 2, 3), (4, 5, 6, 7), (8, 9, 10, 11)], blocks)
check("a block that would run past the dictionary is reported",
      any("outside" in w for w in M.resolve(
          [grid], [OverrideRecord("Arm", "Actuator:1", {"Tag:1|board": {"marker_id": 48}})],
          occs_b, "Robot").warnings))
ch = TagSpec(layout="charuco", rows=5, cols=7, square_length_m=0.02, marker_length_m=0.015)
check("a 7x5 ChArUco holds 17 markers", ch.marker_count == 17 and ch.ids[-1] == 16)
check("ChArUco extent = squares", ch.extent_m == (0.14, 0.1))
w = []
check("a ChArUco marker larger than its square is refused",
      TagSpec.from_dict({"layout": "charuco", "rows": 3, "cols": 3,
                         "square_length_m": 0.01, "marker_length_m": 0.02}, w.append, "t") is None)
fit = M.fit_board(0.10, 0.06, TagSpec(layout="grid", rows=2, cols=3))
fw, fh = fit.tile_size_m
check("fit_board fills the face without overflowing it",
      fw <= 0.10 + 1e-12 and fh <= 0.06 + 1e-12 and (abs(fw - 0.10) < 1e-9 or abs(fh - 0.06) < 1e-9),
      (fw, fh))
check("board spec round-trips through JSON",
      TagSpec.from_dict(json.loads(json.dumps(ch.to_dict())), print, "t").to_dict() == ch.to_dict())

# ------------------------------------------------------------ black layer
print("black layer: depth into the face, raise above it")
sp = TagSpec(marker_length_m=0.03, depth_m=0.0004, raise_m=0.0008)
check("thickness = depth + raise", abs(sp.black_thickness_m - 0.0012) < 1e-15)
rt = TagSpec.from_dict(json.loads(json.dumps(sp.to_dict())), print, "t")
check("raise_m round-trips", rt.raise_m == 0.0008 and rt.depth_m == 0.0004)
check("an inlay-only spec does not write raise_m", "raise_m" not in TagSpec().to_dict())
w = []
check("zero thickness is refused",
      TagSpec.from_dict({"depth_m": 0, "raise_m": 0}, w.append, "t") is None and w)
check("negative depth is refused",
      TagSpec.from_dict({"depth_m": -0.001, "raise_m": 0.002}, [].append, "t") is None)
f_single = M.detector_frame(TagSpec(raise_m=0.0008))
check("a raised layer lifts the detector frame by raise_m",
      abs(f_single[11] - 0.0008) < 1e-15 and f_single[3] == 0 and f_single[7] == 0)
check("an inlaid layer leaves the frame on the face", M.detector_frame(TagSpec())[11] == 0.0)
check("raise_m is geometry: not overridable per copy", "raise_m" not in M.OVERRIDABLE
      and "depth_m" not in M.OVERRIDABLE)
check("tile size does not depend on the black thickness",
      TagSpec(depth_m=0.002).tile_size_m == TagSpec(depth_m=0.0002).tile_size_m)

# -------------------------------------------------------- joint origins
print("optical frames from Joint Origins")
t1 = tag()                                   # origin (0,0,0.3) cm, axes = identity
jo = {"component": "Tag", "name": "aruco top", "origin": [0.0, 0.0, 3.0],   # mm design units
      "x_axis": [1, 0, 0], "y_axis": [0, 1, 0], "z_axis": [0, 0, 1]}
w = []
check("a matching Joint Origin is attached", M.attach_joint_origins([t1], [jo], "mm", w.append) == 1
      and t1.optical_source == "joint_origin" and not w, w)
res = M.resolve([t1], [], [OccurrenceRef("Tag:1", "Tag", T(10, 0, 0))], "Robot", "mm")
ow = res.instances[0].optical_world
check("optical world = occurrence x Joint Origin (design units)",
      [round(ow[i], 9) for i in (3, 7, 11)] == [10, 0, 3.0], ow)
t2 = tag()
moved = dict(jo, origin=[0.0, 0.0, 5.0])     # the face moved 2 mm after tagging
w = []
M.attach_joint_origins([t2], [moved], "mm", w.append)
check("a Joint Origin away from the pattern warns 'rebuild'",
      w and "rebuild" in w[0] and t2.optical_source == "joint_origin", w)
t3 = tag()
check("no Joint Origin: record frame stays the source",
      M.attach_joint_origins([t3], [dict(jo, name="other")], "mm", print) == 0
      and t3.optical_source == "record")
g = TagDef(key="b", component="Board", spec=TagSpec(layout="grid", rows=2, cols=3,
           marker_length_m=0.02, separation_m=0.004))
op = g.record_optical()
check("a board's optical frame is OpenCV's: z INTO the board",
      abs(op[10] + 1.0) < 1e-12 and abs(op[5] + 1.0) < 1e-12, op)
rt = M.resolve([t1], [], [OccurrenceRef("Tag:1", "Tag", T(0, 0, 0))], "Robot", "mm").to_dict()
check("markers.json carries optical_world + its source",
      rt["instances"][0]["optical_source"] == "joint_origin" and "optical_world" in rt["instances"][0])

# ------------------------------------------------------- face heuristic
print("largest uninterrupted rectangle on a face")
import time as _time
sq = [[(0, 0), (40, 0), (40, 40), (0, 40)]]
r = M.largest_rect(sq)
check("a 40 mm square face -> ~40 mm square at 0 deg",
      r["angle_deg"] == 0 and 39.0 <= r["w"] <= 40.0 and abs(r["cx"] - 20) < 0.5, r)


def circle(cx, cy, rad, n=96):
    return [(cx + rad * math.cos(2 * math.pi * k / n), cy + rad * math.sin(2 * math.pi * k / n))
            for k in range(n)]


# Zach's disc: radius 50 with a flat chord at y = 20 and two holes
_a0 = math.degrees(math.asin(20 / 50))
disc = [(50 * math.cos(math.radians(a)), 50 * math.sin(math.radians(a)))
        for a in [180 - _a0 + k * (180 + 2 * _a0) / 120 for k in range(121)]]  # arc under the chord
holes = [circle(-35, -5, 4), circle(-15, -30, 4)]
t0 = _time.time()
r = M.largest_rect([disc] + holes)
dt = _time.time() - t0


def inside_rect(r, px, py):
    t = math.radians(r["angle_deg"])
    u = (px - r["cx"]) * math.cos(t) + (py - r["cy"]) * math.sin(t)
    v = -(px - r["cx"]) * math.sin(t) + (py - r["cy"]) * math.cos(t)
    return abs(u) <= r["w"] / 2 + 1e-9 and abs(v) <= r["h"] / 2 + 1e-9


def corners(r):
    t = math.radians(r["angle_deg"])
    out = []
    for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        u, v = su * r["w"] / 2, sv * r["h"] / 2
        out.append((r["cx"] + u * math.cos(t) - v * math.sin(t),
                    r["cy"] + u * math.sin(t) + v * math.cos(t)))
    return out


check("the rectangle stays inside the disc",
      all(math.hypot(x, y) <= 50 + 1e-6 and y <= 20 + 1e-6 for x, y in corners(r)), r)
check("...and never covers a hole",
      not any(inside_rect(r, x, y) for hole in holes for x, y in hole), r)
check("...and is big (> 45 mm side on a 100 mm disc)", r["w"] > 45, r)
r0 = M.largest_rect([disc])
check("without the holes it grows (~59 mm: chord + arc bound it; corners verified by hand)",
      57 <= r0["w"] <= 60 and r0["w"] > r["w"], r0)
check(f"...in reasonable time ({dt:.2f} s)", dt < 5.0)
L = [[(0, 0), (60, 0), (60, 20), (20, 20), (20, 60), (0, 60)]]
r = M.largest_rect(L)
check("an L-shaped face -> a 20 mm square in a leg", 19.0 <= r["w"] <= 20.5, r)
r = M.largest_rect([[(0, 0), (120, 0), (120, 70), (0, 70)]], aspect=2.0)
check("aspect 2 on a 120 x 70 face -> ~120 x 60", 115 <= r["w"] <= 120 and abs(r["w"] / r["h"] - 2) < 1e-9, r)
check("tile_aspect: single 1, grid 3x2 wider than tall",
      M.tile_aspect(TagSpec()) == 1.0 and M.tile_aspect(TagSpec(layout="grid", rows=2, cols=3)) > 1.3)

# ------------------------------------------------------------- suggestions
print("suggestions")
s = M.suggest(6)
check("6 tags -> 4x4_50", s["dictionary"] == "DICT_4X4_50", s)
s = M.suggest(60)
check("60 tags -> a 4x4 with headroom", s["dictionary"] == "DICT_4X4_250"
      or s["dictionary"].startswith("DICT_5X5"), s)
s = M.suggest(6, marker_length_m=0.03)
check("range estimate is reported", s["max_distance_m"] > 0.5, s)

print()
print(f"{PASS} passed, {len(FAIL)} failed")
if FAIL:
    sys.exit(1)
