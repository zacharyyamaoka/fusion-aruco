"""Bridge checks for ArUco Extrude's Offset / Height model, the locating
pins and the old-parameter migration (2026-10-08). live_test runs them in
its document; `heights_test` runs them alone in a NEW document that is
closed afterwards (the user's document is put back).

    heights_case    every height combination on its own plate, measured on
                    the bodies: flush, raised, recessed, a negative white
                    height, Black only inlaid / on top, White only inlaid /
                    raised
    follow_case     the black offset parameter IS an expression of the white
                    ones while it follows: Change Parameters keeps the black
                    level; a typed offset stops following; a parameter change
                    that adds / removes a part says so and the next edit
                    rebuilds
    pins_case       triangle and round pins on a tag printed on its own, the
                    holes in the plate under it measured against the
                    tolerance; edits that drop, change and bring back the pins
    migration_case  nodes built the way the add-in built them before
                    (parameters height / thickness / above): they compute as
                    before and their next edit rebuilds them with Offset /
                    Height parameters, same geometry
"""
from __future__ import annotations

import json
import math
import os
import traceback

import adsk.core
import adsk.fusion

import aruco_core
import aruco_demo
import aruco_feature as F
import aruco_tags as T

P = adsk.core.Point3D.create
VI = adsk.core.ValueInput
JOIN = adsk.fusion.FeatureOperations.JoinFeatureOperation
CUT = adsk.fusion.FeatureOperations.CutFeatureOperation
TOL_MM = 1e-3


def SL():
    return aruco_core.module("aruco_slabs")


def _lab():
    import aruco_extrude_lab
    return aruco_extrude_lab


def _check(cond, what, info):
    if not cond:
        raise AssertionError(f"{what}: {info}")


def _top_face(body):
    best = None
    for f in body.faces:
        if not isinstance(f.geometry, adsk.core.Plane):
            continue
        ok, n = f.evaluator.getNormalAtPoint(f.pointOnFace)
        if n.z > 0.999 and (best is None or f.pointOnFace.z > best.pointOnFace.z):
            best = f
    return best


def _plate(root, x0, y0, w_cm, d_cm, name, h_cm=0.5):
    ext = aruco_demo._box(root, x0, y0, x0 + w_cm, y0 + d_cm, h_cm, name)
    return ext, ext.bodies.item(0)


def _body(comp, name):
    return comp.bRepBodies.itemByName(name)


def _z_span(body, z0_cm):
    """(low, high) mm of a body along world z, from the plane z0 (the face)."""
    zs = [v.geometry.z for v in body.vertices]
    bb = body.boundingBox
    zs += [bb.minPoint.z, bb.maxPoint.z]
    return [round((min(zs) - z0_cm) * 10.0, 4), round((max(zs) - z0_cm) * 10.0, 4)]


def _inside(body, p):
    return body.pointContainment(P(*p)) == adsk.fusion.PointContainment.PointInsidePointContainment


def _rec(des, key):
    """(component, record) of the MODELLED tag `key` (never a variant part's)."""
    return T.find_record(des, key)


def _cf(des, key):
    return _lab()._cf(des, key)


def make(target, **kw):
    return _lab().make(target, **kw)


def _edit(cf, settings=None, numbers=None):
    """An edit in a hidden command (nothing selected; live_test covers
    Fusion's double-click route)."""
    return F.scripted_edit(cf, settings=settings or {}, numbers=numbers or {}, route=None)


def _cell_point(des, key, z_mm, z0_cm):
    """A point over the centre of one black cell of tag `key`, z_mm over the
    face (world, root tags with z up)."""
    comp, rec = _rec(des, key)
    tag = T.M().TagDef.from_dict(rec, lambda m: None)
    x0, y0, x1, y1 = T.M().cell_boxes(tag.spec)[0]
    cx, cy = (x0 + x1) * 50.0, (y0 + y1) * 50.0
    o, x, y = tag.origin, tag.x_axis, tag.y_axis
    p = [o[i] + x[i] * cx + y[i] * cy for i in range(3)]
    p[2] = z0_cm + z_mm / 10.0
    return p


def _spec_mm(rec):
    sp = rec.get("spec") or {}
    return {k: round(sp.get(k + "_m", 0.0) * 1000.0, 4) for k in ("depth", "raise", "recess")}


# ------------------------------------------------------------------ heights

HEIGHT_CASES = (
    # key, mode, numbers (mm), want: pattern span over the face, white span (or None),
    # record (depth, raise, recess), parts
    ("hf", "both", {}, (1.4, 2.0), (0.0, 2.0), (0.6, 0.0, 0.0), ["tile", "pocket", "pattern"]),
    ("hr", "both", {"black_offset": 2.0, "black_height": 0.6}, (2.0, 2.6), (0.0, 2.0),
     (0.0, 0.6, 0.0), ["tile", "pattern"]),
    ("hc", "both", {"black_offset": 1.6, "black_height": -0.6}, (1.0, 1.6), (0.0, 2.0),
     (1.0, 0.0, 0.4), ["tile", "pocket", "pattern"]),
    ("hn", "both", {"white_offset": 2.0, "white_height": -2.0}, (1.4, 2.0), (0.0, 2.0),
     (0.6, 0.0, 0.0), ["tile", "pocket", "pattern"]),
    ("hb", "black", {}, (-0.6, 0.0), None, (0.6, 0.0, 0.0), ["pattern", "recess"]),
    ("ht", "black", {"black_offset": 0.0, "black_height": 0.6}, (0.0, 0.6), None,
     (0.0, 0.6, 0.0), ["pattern"]),
    ("hw", "white", {}, (-0.6, 0.0), None, (0.6, 0.0, 0.0), ["pattern", "recess"]),
    ("hu", "white", {"white_offset": 0.0, "white_height": 0.5}, (0.0, 0.5), None,
     (0.0, 0.5, 0.0), ["pattern"]),
)


def heights_case(des, step, dy=50.0):
    """One 40 x 40 x 5 mm plate per case in a row at y = dy cm."""
    root = des.rootComponent
    plates = {}
    for i, (key, mode, nums, pat, white, rec_want, parts) in enumerate(HEIGHT_CASES):
        ext, body = _plate(root, -24.0 + i * 6.0, dy, 4.0, 4.0, f"plate {key}")
        plates[key] = body

        def one(key=key, mode=mode, nums=nums, pat=pat, white=white, rec_want=rec_want,
                parts=parts, body=body):
            vol0 = body.volume
            face_z = 0.5
            rec = make(_top_face(body), mode=mode, op="join", key=key, marker_id=0, **nums)
            comp, rec = _rec(des, key)
            cf = _cf(des, key)
            s = F.settings_of(cf)
            pat_body = _body(root, f"{'aruco white' if mode == 'white' else 'aruco cells'} {key}")
            out = {"pattern_mm": _z_span(pat_body, face_z) if pat_body else None,
                   "plate_mm": _z_span(body, face_z), "record": _spec_mm(rec),
                   "parts": s.get("parts"), "numbers": {k: round(v, 4) for k, v in
                                                        F.numbers_of(cf).items()
                                                        if k in F.HEIGHT_PARAMS},
                   "health": int(cf.healthState),
                   "plate_volume_mm3": [round(vol0 * 1000, 2), round(body.volume * 1000, 2)]}
            _check(cf is not None and out["health"] == 0, "not a healthy live node", out)
            _check(out["parts"] == parts, "parts", out)
            _check(out["pattern_mm"] and abs(out["pattern_mm"][0] - pat[0]) < TOL_MM
                   and abs(out["pattern_mm"][1] - pat[1]) < TOL_MM, f"pattern not at {pat}", out)
            r = out["record"]
            _check(all(abs(a - b) < TOL_MM for a, b in zip((r["depth"], r["raise"], r["recess"]),
                                                          rec_want)),
                   f"record depth/raise/recess not {rec_want}", out)
            if white is not None:
                # the joined tile: the plate's top is the white top
                _check(abs(out["plate_mm"][1] - white[1]) < TOL_MM, f"white top not {white[1]}", out)
            if key == "hc":
                # recessed: the pocket is OPEN above the black (not buried)
                above = _cell_point(des, key, 1.8, face_z)
                inside = _cell_point(des, key, 1.3, face_z)
                out["open_above_black"] = not _inside(body, above)
                out["black_at_1.3"] = _inside(pat_body, inside)
                _check(out["open_above_black"] and out["black_at_1.3"],
                       "the recessed black's pocket is not open", out)
            if key == "hf":
                mid = _cell_point(des, key, 1.7, face_z)
                _check(_inside(pat_body, mid) and not _inside(body, mid),
                       "flush: the white is not cut where the black is", out)
            if key == "ht":
                _check(abs(out["plate_volume_mm3"][0] - out["plate_volume_mm3"][1]) < 1e-3,
                       "a black on top must not cut the plate", out)
            if key in ("hb", "hw"):
                _check(out["plate_volume_mm3"][1] < out["plate_volume_mm3"][0] - 1.0,
                       "an inlay must cut the plate", out)
            return out
        step(f"heights {key}: {mode} {nums or 'defaults'}", one)
    lab = _lab()
    step("heights: colours", lambda: lab.assert_colours(des))
    return plates


# ------------------------------------------------------------ follow / params

def follow_case(des, step, dy=58.0):
    """The black offset parameter follows the white top by expression."""
    root = des.rootComponent
    _ext, body = _plate(root, -24.0, dy, 4.0, 4.0, "plate fx")
    face_z = 0.5

    def made():
        make(_top_face(body), mode="both", op="join", key="fx", marker_id=3)
        cf = _cf(des, "fx")
        p = F._param(cf, "black_offset")
        out = {"expression": p.expression, "want": F.follow_expression(cf),
               "follows": F.black_follows(cf), "value_mm": round(p.value * 10, 4)}
        _check(out["follows"] and abs(out["value_mm"] - 2.0) < TOL_MM,
               "the black offset is not the follow expression", out)
        return out
    step("follow: black offset = white offset + white height (expression)", made)

    def white_param():
        st = F._st()
        n0 = getattr(st, "compute_n", 0)
        cf = _cf(des, "fx")
        F._param(cf, "white_height").expression = "3 mm"
        k, ran = F.computes_since(n0)
        cells = _body(root, "aruco cells fx")
        out = {"computes": k, "errors": [c.get("error", "")[-200:] for c in ran if c.get("error")],
               "black_offset_mm": round(F._param(cf, "black_offset").value * 10, 4),
               "cells_mm": _z_span(cells, face_z), "plate_mm": _z_span(body, face_z),
               "health": int(cf.healthState)}
        _check(k >= 1 and not out["errors"] and out["health"] == 0, "compute did not run clean", out)
        _check(abs(out["black_offset_mm"] - 3.0) < TOL_MM, "the black offset did not follow", out)
        _check(abs(out["cells_mm"][0] - 2.4) < TOL_MM and abs(out["cells_mm"][1] - 3.0) < TOL_MM,
               "the black did not move with the white top", out)
        _check(abs(out["plate_mm"][1] - 3.0) < TOL_MM, "the white top is not 3 mm", out)
        return out
    step("follow: Change Parameters white height 2 -> 3 mm, the black stays level", white_param)

    def typed():
        cf = _cf(des, "fx")
        F._param(cf, "black_offset").expression = "2.5 mm"
        cells = _body(root, "aruco cells fx")
        out = {"follows": F.black_follows(cf), "cells_mm": _z_span(cells, face_z),
               "health": int(cf.healthState)}
        _check(not out["follows"] and out["health"] == 0,
               "a number typed in Change Parameters unlinks the black", out)
        _check(abs(out["cells_mm"][1] - 2.5) < TOL_MM, "the black is not at 2.5 mm", out)
        # the next edit links it again, KEEPING where it is: 0.5 under the top
        _edit(cf, settings={}, numbers={})
        cf = _cf(des, "fx")
        out["after_edit"] = {"follows": F.black_follows(cf),
                             "expression": F._param(cf, "black_offset").expression,
                             "black_offset_mm": round(F._param(cf, "black_offset").value * 10, 4)}
        _check(out["after_edit"]["follows"] and abs(out["after_edit"]["black_offset_mm"] - 2.5)
               < TOL_MM, "the edit should link the black again where it is (white top - 0.5)",
               out)
        # and the white carries it, the step kept
        F._param(cf, "white_height").expression = "4 mm"
        out["white_4"] = {"black_offset_mm": round(F._param(cf, "black_offset").value * 10, 4),
                          "cells_mm": _z_span(_body(root, "aruco cells fx"), face_z),
                          "health": int(cf.healthState)}
        _check(abs(out["white_4"]["black_offset_mm"] - 3.5) < TOL_MM
               and abs(out["white_4"]["cells_mm"][1] - 3.5) < TOL_MM,
               "white 3 -> 4 mm should carry the black, 0.5 under the top", out)
        F._param(cf, "white_height").expression = "3 mm"
        return out
    step("follow: a typed black offset unlinks; the next edit links it again where it is, "
         "and the white carries it (recess 0.5 kept)", typed)

    def structure_by_param():
        cf = _cf(des, "fx")
        st = F._st()
        F._param(cf, "black_offset").expression = "3 mm"
        n0 = getattr(st, "compute_n", 0)
        F._param(cf, "black_height").expression = "0.6 mm"     # stands on the white: no pocket
        _k, ran = F.computes_since(n0)
        # Fusion shows its own "Compute Failed" on the node; the reason is in
        # the compute log (and the edit dialog says it when opened)
        out = {"health": int(cf.healthState), "message": (cf.errorOrWarningMessage or "")[:200],
               "compute_error": next((c.get("error", "")[-300:] for c in ran if c.get("error")), "")}
        _check(out["health"] != 0 and "rebuild" in out["compute_error"],
               "a parameter that removes the pocket should fail compute with 'rebuild'", out)
        out["dialog_says"] = F.rebuild_reason(cf)
        _check("rebuild" in (out["dialog_says"] or ""), "the edit dialog should say why", out)
        res = _edit(cf, settings={}, numbers={})
        cf = _cf(des, "fx")
        cells = _body(root, "aruco cells fx")
        out.update(edit=res, health_after=int(cf.healthState),
                   parts=(F.settings_of(cf) or {}).get("parts"), cells_mm=_z_span(cells, face_z))
        _check(res.get("rebuilt") and out["health_after"] == 0 and out["parts"] == ["tile", "pattern"],
               "the next edit should rebuild it healthy, without the pocket", out)
        _check(abs(out["cells_mm"][0] - 3.0) < TOL_MM and abs(out["cells_mm"][1] - 3.6) < TOL_MM,
               "the black should stand 0.6 on the white", out)
        return out
    step("follow: a parameter change that drops the pocket -> 'rebuild'; the edit rebuilds",
         structure_by_param)


# -------------------------------------------------------------------- pins

def _below(face, face_z, eps=1e-6):
    return all(v.geometry.z <= face_z + eps for v in face.vertices) and \
        min(v.geometry.z for v in face.vertices) < face_z - eps


def _in_rect(face, rect, eps=1e-6):
    cx, cy, w, h = rect
    return all(abs(v.geometry.x - cx) <= w / 2 + eps and abs(v.geometry.y - cy) <= h / 2 + eps
               for v in face.vertices)


def measure_pins(tile, plate, face_z, rect):
    """Pins (the tile body under the face) vs their holes (the plate inside
    the tile's footprint): wall gaps (mm, along each pin wall's normal /
    radius differences), the pins' and the holes' depth below the face."""
    out = {"walls": [], "radii": [], "pin_bottom_mm": None, "hole_floor_mm": []}
    zs = [v.geometry.z for v in tile.vertices]
    out["pin_bottom_mm"] = round((min(zs) - face_z) * 10.0, 4)
    pin_planes, hole_planes, pin_cyl, hole_cyl = [], [], [], []
    for f in tile.faces:
        if not _below(f, face_z):
            continue
        g = f.geometry
        if isinstance(g, adsk.core.Plane):
            ok, n = f.evaluator.getNormalAtPoint(f.pointOnFace)
            if abs(n.z) < 1e-6:
                pin_planes.append((f, n))
        elif isinstance(g, adsk.core.Cylinder):
            pin_cyl.append(g)
    for f in plate.faces:
        if not _below(f, face_z) or not _in_rect(f, rect):
            continue
        g = f.geometry
        if isinstance(g, adsk.core.Plane):
            ok, n = f.evaluator.getNormalAtPoint(f.pointOnFace)
            if abs(n.z) < 1e-6:
                hole_planes.append((f, n))
            elif n.z > 0.999:
                out["hole_floor_mm"].append(round((f.pointOnFace.z - face_z) * 10.0, 4))
        elif isinstance(g, adsk.core.Cylinder):
            hole_cyl.append(g)
    for f, n in pin_planes:
        best = None
        for h, m in hole_planes:
            if n.x * m.x + n.y * m.y + n.z * m.z > -0.999:
                continue
            p, q = f.pointOnFace, h.pointOnFace
            d = (q.x - p.x) * n.x + (q.y - p.y) * n.y + (q.z - p.z) * n.z
            if d > 0 and (best is None or d < best):
                best = d
        out["walls"].append(round(best * 10.0, 5) if best is not None else None)
    for c in pin_cyl:
        best = None
        for h in hole_cyl:
            d = math.hypot(h.origin.x - c.origin.x, h.origin.y - c.origin.y)
            if d < 1e-4 and (best is None or abs(h.radius - c.radius) < abs(best)):
                best = h.radius - c.radius
        out["radii"].append({"pin_d_mm": round(c.radius * 20.0, 4),
                             "gap_mm": round(best * 10.0, 5) if best is not None else None})
    return out


def pins_case(des, step, dy=66.0, shots=""):
    """Triangle and round pins on tags printed on their own, each pressed
    into a 60 x 50 x 5 mm plate; the edits that change them."""
    root = des.rootComponent
    face_z = 0.5
    plates = {}
    for i, (key, shape) in enumerate((("pt", "triangle"), ("pr", "round"))):
        _ext, body = _plate(root, -24.0 + i * 8.0, dy, 6.0, 5.0, f"plate {key}")
        plates[key] = body

        def one(key=key, shape=shape, body=body):
            vol0 = body.volume
            make(_top_face(body), mode="both", op="new", key=key, marker_id=7 + len(key),
                 pin=shape, size=30.0)
            cf = _cf(des, key)
            s = F.settings_of(cf)
            tile = _body(root, f"tag tile {key}")
            comp, rec = _rec(des, key)
            tw, th = (v * 100.0 for v in T.M().TagDef.from_dict(rec, lambda m: None).spec.tile_size_m)
            o = rec["origin"]
            m = measure_pins(tile, body, face_z, (o[0], o[1], tw, th))
            out = {"parts": s.get("parts"), "pin": s.get("pin"), "health": int(cf.healthState),
                   "measured": m, "tile_mm": _z_span(tile, face_z),
                   "plate_volume_mm3": [round(vol0 * 1000, 2), round(body.volume * 1000, 2)],
                   "tile_appearance": tile.appearance.name if tile.appearance else None}
            _check(out["health"] == 0 and out["parts"] == ["tile", "pin", "pocket", "pattern",
                                                            "recess"], "parts / health", out)
            _check(abs(m["pin_bottom_mm"] + 2.0) < TOL_MM, "the pins should reach 2 mm down", out)
            _check(m["hole_floor_mm"] and all(abs(z + 2.15) < TOL_MM for z in m["hole_floor_mm"]),
                   "the holes should be 2.15 mm deep", out)
            if shape == "triangle":
                _check(len(m["walls"]) == 3 and all(w is not None and abs(w - 0.15) < TOL_MM
                                                    for w in m["walls"]),
                       "each triangle wall should be 0.15 mm off its hole wall", out)
            else:
                _check(len(m["radii"]) == 2 and all(r["gap_mm"] is not None
                                                    and abs(r["gap_mm"] - 0.15) < TOL_MM
                                                    and abs(r["pin_d_mm"] - 3.0) < TOL_MM
                                                    for r in m["radii"]),
                       "two 3 mm pins, holes 0.15 mm bigger all round", out)
            _check(out["tile_appearance"] == T.WHITE, "the pins print white with the tile", out)
            return out
        step(f"pins {key}: {shape} pin on a tag printed on its own, hole in the plate", one)
    if shots:
        os.makedirs(shots, exist_ok=True)
        step("pin shots (triangle, round)", lambda: pin_shots(des, shots, plates))

    def tol_edit():
        _edit(_cf(des, "pr"), settings={}, numbers={"pin_tol": 0.3})
        cf = _cf(des, "pr")
        comp, rec = _rec(des, "pr")
        tw, th = (v * 100.0 for v in T.M().TagDef.from_dict(rec, lambda m: None).spec.tile_size_m)
        m = measure_pins(_body(root, "tag tile pr"), plates["pr"], face_z,
                         (rec["origin"][0], rec["origin"][1], tw, th))
        out = {"measured": m, "health": int(cf.healthState),
               "rebuilt": False, "parts": F.settings_of(cf).get("parts")}
        _check(out["health"] == 0 and all(abs(r["gap_mm"] - 0.3) < TOL_MM for r in m["radii"])
               and all(abs(z + 2.3) < TOL_MM for z in m["hole_floor_mm"]),
               "tolerance 0.3: holes 0.3 bigger and 2.3 deep (compute, no rebuild)", out)
        return out
    step("pins: edit the tolerance 0.15 -> 0.3 mm (compute)", tol_edit)

    def toggle_off():
        vol_full = None
        res = _edit(_cf(des, "pt"), settings={"pin": "none"}, numbers={})
        cf = _cf(des, "pt")
        tile = _body(root, "tag tile pt")
        out = {"result": res, "parts": F.settings_of(cf).get("parts"),
               "tile_mm": _z_span(tile, face_z), "health": int(cf.healthState),
               "plate_volume_mm3": round(plates["pt"].volume * 1000, 2),
               "pin_params": [p for p in F.PIN_PARAMS if F._param(cf, p) is not None]}
        _check(res.get("rebuilt") and out["parts"] == ["tile", "pocket", "pattern"]
               and abs(out["tile_mm"][0]) < TOL_MM and not out["pin_params"],
               "no pin: the tile's underside is flat, no pin parameters", out)
        _check(abs(out["plate_volume_mm3"] - 60 * 50 * 5) < 0.01, "the plate's holes are gone", out)
        return out
    step("pins: edit the triangle tag to no pin (rebuild: pin and hole gone)", toggle_off)

    def toggle_round():
        res = _edit(_cf(des, "pt"), settings={"pin": "round"}, numbers={})
        cf = _cf(des, "pt")
        comp, rec = _rec(des, "pt")
        tw, th = (v * 100.0 for v in T.M().TagDef.from_dict(rec, lambda m: None).spec.tile_size_m)
        m = measure_pins(_body(root, "tag tile pt"), plates["pt"], face_z,
                         (rec["origin"][0], rec["origin"][1], tw, th))
        out = {"result": res, "measured": m, "health": int(cf.healthState),
               "parts": F.settings_of(cf).get("parts")}
        _check(res.get("rebuilt") and out["health"] == 0 and len(m["radii"]) == 2
               and all(abs(r["gap_mm"] - 0.15) < TOL_MM for r in m["radii"]),
               "round pins back, holes 0.15 bigger", out)
        return out
    step("pins: edit it to round pins (rebuild: two pins, two holes)", toggle_round)

    def join_greys():
        res = _edit(_cf(des, "pr"), settings={"op": "join"}, numbers={})
        cf = _cf(des, "pr")
        s = F.settings_of(cf)
        out = {"result": res, "pin": s.get("pin"), "parts": s.get("parts"),
               "health": int(cf.healthState)}
        _check(s.get("pin") == "none" and "pin" not in s["parts"],
               "join: nothing separate to locate, the pins go", out)
        return out
    step("pins: switching to Join drops the pins (rebuild)", join_greys)
    step("pins: colours", lambda: _lab().assert_colours(des))
    return plates


_FOLDERS = ("isJointsFolderLightBulbOn", "isJointOriginsFolderLightBulbOn",
            "isSketchFolderLightBulbOn", "isConstructionFolderLightBulbOn",
            "isOriginFolderLightBulbOn")


class _Isolate:
    """Only `keep` (root bodies) shown, no Joint Origin / sketch / origin
    glyphs, while a shot is taken; everything put back after."""

    def __init__(self, des, keep):
        self.des, self.keep, self.saved = des, list(keep), []

    def __enter__(self):
        root = self.des.rootComponent
        for b in root.bRepBodies:
            if b.isLightBulbOn and not any(b == k for k in self.keep):
                b.isLightBulbOn = False
                self.saved.append((b, "isLightBulbOn", True))
        for comp in self.des.allComponents:
            for attr in _FOLDERS:
                try:
                    if getattr(comp, attr):
                        setattr(comp, attr, False)
                        self.saved.append((comp, attr, True))
                except Exception:                  # noqa: BLE001
                    pass
        return self

    def __exit__(self, *exc):
        for ent, attr, val in reversed(self.saved):
            try:
                setattr(ent, attr, val)
            except Exception:                      # noqa: BLE001
                pass
        return False


def pin_shots(des, out_dir, plates):
    """Each pinned plate: the plate see-through from below (the pins in their
    holes), and the plate alone from above (the holes)."""
    root = des.rootComponent
    shots = []
    for key, label in (("pt", "triangle"), ("pr", "round")):
        body = plates.get(key)
        tile = _body(root, f"tag tile {key}")
        cells = _body(root, f"aruco cells {key}")
        if body is None or tile is None:
            continue
        with _Isolate(des, [body, tile] + ([cells] if cells else [])):
            try:
                body.opacity = 0.3
                shots.append(frame_shot(os.path.join(out_dir, f"pins_{label}_xray_below.png"),
                                        [body], direction=(-0.8, -1.2, -0.9), fill=0.9))
                body.opacity = 1.0
                tile.isLightBulbOn = False
                if cells:
                    cells.isLightBulbOn = False
                shots.append(frame_shot(os.path.join(out_dir, f"pins_{label}_holes.png"),
                                        [body], direction=(-0.5, -0.9, 1.4), fill=0.9))
            finally:
                body.opacity = 1.0
                tile.isLightBulbOn = True
                if cells:
                    cells.isLightBulbOn = True
    return shots


def frame_shot(path, bodies, direction=(-1.0, -1.6, 1.1), fov_deg=28.0, fill=0.8,
               width=1600, height=1000):
    """A perspective capture looking at `bodies` from `direction` (world),
    framed to fill about `fill` of the view."""
    app = adsk.core.Application.get()
    vp = app.activeViewport
    lo = [min(getattr(b.boundingBox.minPoint, a) for b in bodies) for a in "xyz"]
    hi = [max(getattr(b.boundingBox.maxPoint, a) for b in bodies) for a in "xyz"]
    c = [(lo[i] + hi[i]) / 2.0 for i in range(3)]
    r = 0.5 * math.sqrt(sum((hi[i] - lo[i]) ** 2 for i in range(3)))
    d = r / (fill * math.tan(math.radians(fov_deg) / 2.0))
    n = math.sqrt(sum(v * v for v in direction))
    eye = [c[i] + direction[i] / n * d for i in range(3)]
    for _ in range(2):
        cam = vp.camera
        cam.cameraType = adsk.core.CameraTypes.PerspectiveCameraType
        cam.isFitView = False
        cam.isSmoothTransition = False
        cam.eye = P(*eye)
        cam.target = P(*c)
        cam.upVector = adsk.core.Vector3D.create(0, 0, 1)
        cam.perspectiveAngle = math.radians(fov_deg)
        vp.camera = cam
        vp.refresh()
        adsk.doEvents()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return {"file": path, "ok": bool(vp.saveAsImageFile(path, int(width), int(height)))}


HEIGHT_NAMES = {"hf": "flush", "hr": "black_raised", "hc": "black_recessed",
                "hn": "negative_white_height", "hb": "black_only_inlaid",
                "ht": "black_only_on_top", "hw": "white_only_inlaid", "hu": "white_only_raised"}


def heights_shots(des, out_dir, plates):
    """Every height combination close up (its plate and its bodies only, no
    glyphs), low so the steps show, and the row of them."""
    root = des.rootComponent
    out = []
    every = []
    for key, plate in plates.items():
        mine = [plate] + [b for b in root.bRepBodies
                          if b.name.endswith(f" {key}") and b != plate]
        every += mine
        with _Isolate(des, mine):
            out.append(frame_shot(os.path.join(out_dir, f"heights_{HEIGHT_NAMES.get(key, key)}.png"),
                                  mine, direction=(-0.5, -1.0, 0.55), fill=1.0))
    if every:
        with _Isolate(des, every):
            out.append(frame_shot(os.path.join(out_dir, "heights_row.png"), every,
                                  direction=(-0.35, -1.0, 0.8), fill=0.95))
    return out


# ------------------------------------------------------------- migration

def legacy_tag(des, target, key, mode, height=2.0, thickness=0.6, above=0.0, marker_id=0,
               op="join"):
    """A node the way the add-in built it before 2026-10-08: parameters
    height (White + black) / thickness / above, the tile [0, height], the
    pattern as the keep-tools cut's tool, no "parts" in its spec."""
    S = SL()
    s = F.default_settings()
    s.update(mode=mode, op=op, key=key, v=1)
    s.pop("pin", None)
    s.pop("black_follows", None)
    s, body_under = F.creation_settings(target, s)
    nat = F.native_of(target)
    comp = F.host_of(nat, body_under, s)
    body_under = F.native_of(body_under) if body_under is not None else None
    n = dict(F.default_numbers(mode), marker_id=marker_id)
    n.update(S.from_legacy(mode, height, thickness, above))
    ev = F.evaluate(nat, s, n)
    spec = ev["spec"]
    feats, white_target = [], body_under
    bf_tile = None
    if mode == "both":
        bf_tile, tile = F._base_body(comp, F.tile_temp_body(ev["tile"], 0.0, height / 10.0),
                                     f"tag tile {key}")
        feats.append(bf_tile)
        if F.effective_op(s) == "join":
            feats.append(F._combine(comp, body_under, tile, JOIN, False))
        else:
            white_target = tile
    pat_name = f"{'aruco white' if mode == 'white' else 'aruco cells'} {key}"
    bf_pat, pat = F._base_body(comp, F.pattern_temp_body(ev), pat_name)
    feats.append(bf_pat)
    if spec.depth_m > 0 and white_target is not None:
        feats.append(F._combine(comp, white_target, pat, CUT, True))
    inp = comp.features.customFeatures.createInput(F._st().definition)
    inp.addCustomParameter("marker_id", "Marker ID (first)", VI.createByReal(float(marker_id)),
                           "", True)
    old = [("size", "Marker size", spec.marker_length_m * 1000.0)]
    if mode == "both":
        old.append(("height", "White height", height))
    old += [("thickness", "Pattern thickness", thickness), ("above", "Pattern above surface", above)]
    for pid, label, v in old:
        inp.addCustomParameter(pid, label, VI.createByString(f"{round(v, 6):g} mm"), "mm", True)
    F._dependency(inp, des, target)
    inp.setStartAndEndFeatures(feats[0], feats[-1])
    cf = comp.features.customFeatures.add(inp)
    import json
    cf.customNamedValues.addOrSetValue("spec", json.dumps(s, sort_keys=True))
    cf.name = F._label(key, spec, mode)
    for pid in ("marker_id",) + tuple(p for p, _l, _v in old):
        try:
            F._param(cf, pid).name = f"aruco_{key}_{pid}"
        except Exception:                          # noqa: BLE001
            pass
    F.paint(des, s, bf_tile, bf_pat, body_under if F.effective_op(s) == "join" else None, ev)
    fz = {"custom_feature": cf.entityToken, "base_feature": bf_pat.entityToken,
          "face": None if isinstance(nat, adsk.fusion.Profile) else nat.entityToken,
          "tile_body": (f"tag tile {key}" if mode == "both" and F.effective_op(s) == "new"
                        else (body_under.name if body_under else None)),
          "cells_body": pat.name, "mode": mode, "rotate_deg": 0}
    return F._write_record(des, comp, key, ev, fz)


MIGRATION_CASES = (
    # key, mode, old (height, thickness, above), want pattern span, want numbers
    ("ma", "both", (2.0, 0.6, 0.0), (1.4, 2.0),
     {"white_offset": 0.0, "white_height": 2.0, "black_offset": 2.0, "black_height": -0.6}),
    ("mb", "black", (2.0, 0.6, 0.0), (-0.6, 0.0), {"black_offset": 0.0, "black_height": -0.6}),
    ("mc", "black", (2.0, 0.6, 0.6), (0.0, 0.6), {"black_offset": 0.0, "black_height": 0.6}),
    ("md", "white", (2.0, 0.4, 0.4), (0.0, 0.4), {"white_offset": 0.0, "white_height": 0.4}),
)


def migration_case(des, step, dy=74.0):
    """Old nodes: compute as before; the next edit rebuilds them with the
    Offset / Height parameters, the geometry unchanged."""
    root = des.rootComponent
    face_z = 0.5
    for i, (key, mode, old, pat, want) in enumerate(MIGRATION_CASES):
        _ext, body = _plate(root, -24.0 + i * 6.0, dy, 4.0, 4.0, f"plate {key}")

        def one(key=key, mode=mode, old=old, pat=pat, want=want, body=body):
            F.in_command(legacy_tag, des, _top_face(body), key, mode, *old, marker_id=20 + len(key))
            cf = _cf(des, key)
            s = F.settings_of(cf)
            n = F.numbers_of(cf)
            pat_name = f"{'aruco white' if mode == 'white' else 'aruco cells'} {key}"
            out = {"legacy": F.is_legacy(s),
                   "old_params": sorted(p for p in F.LEGACY_PARAMS if F._param(cf, p) is not None),
                   "numbers": {k: round(n[k], 4) for k in want},
                   "pattern_mm": _z_span(_body(root, pat_name), face_z)}
            _check(out["legacy"] and "thickness" in out["old_params"], "not an old-style node", out)
            _check(all(abs(n[k] - v) < TOL_MM for k, v in want.items()),
                   "numbers_of does not map the old parameters", out)
            _check(abs(out["pattern_mm"][0] - pat[0]) < TOL_MM
                   and abs(out["pattern_mm"][1] - pat[1]) < TOL_MM, "old geometry", out)
            # compute on the old node (a parameter change) keeps it as it was
            F._param(cf, "marker_id").value = F._param(cf, "marker_id").value + 1.0
            out["after_compute"] = {"health": int(cf.healthState),
                                    "pattern_mm": _z_span(_body(root, pat_name), face_z)}
            _check(out["after_compute"]["health"] == 0
                   and abs(out["after_compute"]["pattern_mm"][0] - pat[0]) < TOL_MM
                   and abs(out["after_compute"]["pattern_mm"][1] - pat[1]) < TOL_MM,
                   "the old node's compute moved its geometry", out)
            # the next edit (no change) rewrites it with the new parameters
            res = _edit(cf, settings={}, numbers={})
            cf = _cf(des, key)
            s2 = F.settings_of(cf)
            n2 = F.numbers_of(cf)
            out["after_edit"] = {
                "rebuilt": res.get("rebuilt"), "parts": s2.get("parts"),
                "params": sorted(p for p in F.PARAMS + F.LEGACY_PARAMS if F._param(cf, p) is not None),
                "names": sorted(F._param(cf, p).name for p in F.PARAMS
                                if F._param(cf, p) is not None),
                "numbers": {k: round(n2[k], 4) for k in want},
                "pattern_mm": _z_span(_body(root, pat_name), face_z),
                "health": int(cf.healthState)}
            a = out["after_edit"]
            _check(a["rebuilt"] and a["parts"] and not any(p in a["params"] for p in F.LEGACY_PARAMS),
                   "the edit should rebuild with the new parameters", out)
            _check(all(f"aruco_{key}_{p}" in a["names"] for p in want), "parameter names", out)
            _check(all(abs(n2[k] - v) < TOL_MM for k, v in want.items())
                   and abs(a["pattern_mm"][0] - pat[0]) < TOL_MM
                   and abs(a["pattern_mm"][1] - pat[1]) < TOL_MM and a["health"] == 0,
                   "the rebuilt node changed the geometry", out)
            return out
        step(f"migration {key}: old {mode} node (height/thickness/above {old}) -> "
             "computes as before, the next edit rebuilds it", one)


# ------------------------------------------------------------------ Origin

TURN_CASES = (
    # key, layout, turn wanted, turn expected. 2026-10-09: every Origin for
    # every layout -- a rectangular board at 90 / 270 is OpenCV's board with
    # its cols / rows swapped, on its side (aruco_origin)
    ("o0", "single", 0, 0), ("o90", "single", 90, 90), ("o180", "single", 180, 180),
    ("o270", "single", 270, 270), ("og0", "grid", 0, 0), ("og180", "grid", 180, 180),
    ("og90", "grid", 90, 90), ("og270", "grid", 270, 270),
)


def turns_case(des, step, dy=90.0):
    """Origin: the pattern turned on its footprint. Every tag on an identical
    60 x 40 x 5 mm plate; its frame against the un-turned one; a grid at 90
    / 270 has its cols / rows swapped and the same footprint (the cells'
    extent on the plate); the Joint Origin is checked against OpenCV's
    frame by the run's _jo_check. A node made before 2026-10-09 (no
    "origin_v") keeps a rectangular board's 90 as laid out."""
    root = des.rootComponent
    face_z = 0.5
    ref, ref_cols, ref_box = {}, {}, {}

    def box_mm(key):
        bb = _body(root, f"aruco cells {key}").boundingBox
        return [round((bb.maxPoint.x - bb.minPoint.x) * 10, 4),
                round((bb.maxPoint.y - bb.minPoint.y) * 10, 4)]
    for i, (key, layout, turn, want) in enumerate(TURN_CASES):
        _ext, body = _plate(root, -30.0 + i * 8.0, dy, 6.0, 4.0, f"plate {key}")

        def one(key=key, layout=layout, turn=turn, want=want, body=body):
            make(_top_face(body), mode="black", key=key, layout=layout, turn=turn,
                 marker_id=30 + len(key), size=(14.0 if layout == "grid" else 0.0))
            comp, rec = _rec(des, key)
            cf = _cf(des, key)
            x = rec["x_axis"]
            base = ref.setdefault(layout, x) if turn == 0 else ref.get(layout)
            ang = None
            if base is not None:
                cz = base[0] * x[1] - base[1] * x[0]
                ang = round(math.degrees(math.atan2(cz, base[0] * x[0] + base[1] * x[1])) % 360, 6)
            s = F.settings_of(cf)
            sp = rec.get("spec") or {}
            cr = (sp.get("cols", 1), sp.get("rows", 1))
            c0 = ref_cols.setdefault(layout, cr) if turn == 0 else ref_cols.get(layout)
            b0 = ref_box.setdefault(layout, box_mm(key)) if turn == 0 else ref_box.get(layout)
            out = {"turn_setting": s.get("turn"), "origin_v": s.get("origin_v"), "want": want,
                   "x_axis": [round(v, 6) for v in x], "turned_deg": ang,
                   "health": int(cf.healthState),
                   "layout": f"{sp.get('layout')} {cr[0]}x{cr[1]}",
                   "cells_box_mm": box_mm(key), "cells_box_unturned_mm": b0,
                   "pattern_mm": _z_span(_body(root, f"aruco cells {key}"), face_z)}
            _check(out["health"] == 0, "the node should be healthy", out)
            _check(ang is not None and abs(((ang - want + 180) % 360) - 180) < 1e-4,
                   f"the pattern should be turned {want} deg", out)
            if layout != "single" and c0 is not None:
                swapped = (cr[1], cr[0]) == tuple(c0)
                _check(swapped if want in (90, 270) else tuple(cr) == tuple(c0),
                       "a quarter turn swaps the board's cols / rows, a half turn keeps them",
                       out)
                _check(b0 is not None and max(abs(a - b) for a, b in zip(out["cells_box_mm"], b0))
                       < 1e-3, "the same footprint on the plate", out)
            return out
        step(f"origin {key}: {layout} turned {turn} -> {want}", one)

    def older_node():
        """A node made before 2026-10-09 (its spec without "origin_v") with a
        grid turned 90 kept it as laid out: it still does."""
        _e, plate = _plate(root, -30.0 + (len(TURN_CASES) + 1) * 8.0, dy, 6.0, 4.0, "plate oo")
        make(_top_face(plate), mode="black", key="oo", layout="grid", turn=90, marker_id=40,
             size=14.0)
        cf = _cf(des, "oo")
        s = dict(F.settings_of(cf))
        s.pop("origin_v", None)
        cf.customNamedValues.addOrSetValue("spec", json.dumps(s, sort_keys=True))
        p = F._param(cf, "marker_id")
        p.value = 41.0                                 # recompute under the old rule
        F.refresh_one(cf)
        comp, rec = _rec(des, "oo")
        sp = rec.get("spec") or {}
        x = rec["x_axis"]
        base = ref.get("grid")
        out = {"layout": f"grid {sp.get('cols')}x{sp.get('rows')}", "x_axis": x,
               "grid_ref_layout": list(ref_cols.get("grid") or []),
               "notes": (F._st().compute_log[-1] or {}).get("notes"),
               "health": int(cf.healthState)}
        _check(base is not None and abs(sum(a * b for a, b in zip(x, base)) - 1.0) < 1e-6
               and [sp.get("cols"), sp.get("rows")] == list(ref_cols.get("grid") or []),
               "an older node keeps a rectangular board's 90 as laid out (not swapped)", out)
        return out
    step("origin: a node made before 2026-10-09 keeps its orientation (migration)", older_node)

    def pinned():
        # the pin follows corner 0: turned 90, it sits at the un-turned
        # tile's bottom-left
        _e, plate = _plate(root, -30.0 + len(TURN_CASES) * 8.0, dy, 6.0, 5.0, "plate op")
        make(_top_face(plate), mode="both", op="new", key="op", pin="triangle", turn=90,
             marker_id=39, size=30.0)
        comp, rec = _rec(des, "op")
        tile = _body(root, "tag tile op")
        o, xt = rec["origin"], rec["x_axis"]
        pins = [v.geometry for v in tile.vertices if v.geometry.z < face_z - 1e-4]
        cx = sum(p.x for p in pins) / len(pins) - o[0]
        cy = sum(p.y for p in pins) / len(pins) - o[1]
        # the un-turned frame: x0 = the turned x turned back 90 deg, y0 = the turned x
        x0 = (xt[1], -xt[0])
        u = cx * x0[0] + cy * x0[1]
        v = cx * xt[0] + cy * xt[1]
        out = {"pin_centre_unturned_mm": [round(u * 10, 3), round(v * 10, 3)]}
        _check(u < 0 and v < 0, "turned 90, the pin should be bottom-left of the un-turned tile",
               out)
        return out
    step("origin: a triangle pin follows corner 0 (turned 90)", pinned)


# ------------------------------------------------- a board across the face

def _l_plate(root, x0, y0, name):
    """An L-shaped plate, 5 mm: 80 x 20 mm arm + 20 x 60 mm leg."""
    sk = root.sketches.add(root.xYConstructionPlane)
    L = sk.sketchCurves.sketchLines
    pts = [(0, 0), (8, 0), (8, 2), (2, 2), (2, 6), (0, 6)]
    pts = [P(x0 + a, y0 + b, 0) for a, b in pts]
    for i in range(len(pts)):
        L.addByTwoPoints(pts[i], pts[(i + 1) % len(pts)])
    ext = root.features.extrudeFeatures.addSimple(
        sk.profiles.item(0), VI.createByReal(0.5),
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    ext.bodies.item(0).name = name
    return ext


def split_case(des, step, dy=98.0, strict=True):
    """A board whose pocket reaches the face's edge splits the face: does the
    tag stay a live node? (Zach saw boards on an L-shape, a plate beside a
    hole and a notch fall back to a timeline group.)"""
    root = des.rootComponent
    cases = []
    e1, full = _plate(root, -30.0, dy, 8.0, 6.0, "plate full")
    cases.append(("sf", e1, full, {"mode": "black", "layout": "charuco"}))
    # a white + black board across the whole face stays a timeline group (a
    # node stretched over the joined tile kept Fusion computing until it had
    # to be killed, 2026-10-08)
    e2, full2 = _plate(root, -20.0, dy, 8.0, 6.0, "plate full2")
    cases.append(("sj", e2, full2, {"mode": "both", "op": "join", "layout": "charuco"}))
    e3 = _l_plate(root, -10.0, dy, "plate L")
    cases.append(("sl", e3, e3.bodies.item(0), {"mode": "black", "layout": "grid"}))
    for key, ext, body, kw in cases:
        def one(key=key, ext=ext, body=body, kw=kw):
            face = _top_face(body)
            n_faces = body.faces.count
            trace = []
            real = F._wrap

            def traced(des_, comp, target, feats, s, n, spec):
                tl = des_.timeline

                def snap(tag):
                    trace.append({"at": tag, "marker": tl.markerPosition, "count": tl.count,
                                  "feats": [(f.timelineObject.index, int(f.healthState))
                                            if f.isValid else None for f in feats]})
                snap("before " + type(target).__name__)
                try:
                    cf = real(des_, comp, target, feats, s, n, spec)
                    snap("after ok")
                    trace[-1]["cf"] = (cf.timelineObject.index, int(cf.healthState))
                    return cf
                except Exception as exc:
                    snap(f"after {type(exc).__name__}: {str(exc)[:80]}")
                    raise
            F._wrap = traced
            try:
                rec = make(face, key=key, marker_id=0, **kw)
            finally:
                F._wrap = real
            fz = rec.get("fusion") or {}
            out = {"live": bool(fz.get("custom_feature")), "live_error": fz.get("live_error"),
                   "trace": trace, "marker_end": [des.timeline.markerPosition, des.timeline.count],
                   "group": fz.get("group"), "dependency": fz.get("dependency"),
                   "spec": (rec.get("spec") or {}).get("layout"),
                   "board": [(rec.get("spec") or {}).get(k) for k in ("cols", "rows")],
                   "faces_before_after": [n_faces, body.faces.count],
                   "top_faces_after": sum(1 for f in body.faces
                                          if isinstance(f.geometry, adsk.core.Plane)
                                          and abs(f.pointOnFace.z - 0.5) < 1e-6)}
            if strict and kw.get("mode") in ("black", "white"):
                _check(out["live"], "the board should stay a live node", out)
            # never loose features: a live node, or ONE timeline group
            _check(out["live"] or out["group"], "neither a live node nor a group", out)
            # it keeps computing: an id change, then the plate gets thicker
            cf = _cf(des, key)
            if cf is not None:
                F._param(cf, "marker_id").value = 5.0
                out["after_param"] = {"health": int(cf.healthState),
                                      "marker_at_end": des.timeline.markerPosition
                                      == des.timeline.count}
                pat = _body(des.rootComponent, f"aruco cells {key}")
                z0 = _z_span(pat, 0.5) if pat else None
                ext.extentOne.distance.expression = "7 mm"
                F.refresh_one(_cf(des, key))     # its record + Joint Origin follow (export's step)
                cf = _cf(des, key)
                pat = _body(des.rootComponent, f"aruco cells {key}")
                out["after_upstream"] = {"health": int(cf.healthState),
                                         "cells_mm_before": z0,
                                         "cells_mm_after": _z_span(pat, 0.5) if pat else None}
                if True:
                    _check(out["after_param"]["health"] == 0
                           and out["after_upstream"]["health"] == 0, "compute should hold", out)
                    if pat and z0:
                        _check(abs(out["after_upstream"]["cells_mm_after"][1]
                                   - out["after_upstream"]["cells_mm_before"][1] - 2.0) < TOL_MM,
                               "the pattern should follow the 2 mm thicker plate", out)
            return out
        step(f"split {key}: {kw}", one)


# ------------------------------------------- a reversed longest edge (x_ref)

def _quad_plate(root, x0, y0, top_dx, name, top_reversed=False):
    """A 5 mm plate, 60 mm along the bottom, 40 mm deep, its top edge
    60 + top_dx mm: moving the top-right sketch point later makes the top
    edge the longest. Whether that edge runs the other way than the bottom
    one is the B-Rep's business, so callers make both drawings."""
    sk = root.sketches.add(root.xYConstructionPlane)
    sk.name = f"{name} outline"
    pts = sk.sketchPoints
    pa, pb = pts.add(P(x0, y0, 0)), pts.add(P(x0 + 6.0, y0, 0))
    pc, pd = pts.add(P(x0 + 6.0 + top_dx, y0 + 4.0, 0)), pts.add(P(x0, y0 + 4.0, 0))
    L = sk.sketchCurves.sketchLines
    L.addByTwoPoints(pa, pb)
    L.addByTwoPoints(pb, pc)
    if top_reversed:
        L.addByTwoPoints(pd, pc)
    else:
        L.addByTwoPoints(pc, pd)
    L.addByTwoPoints(pd, pa)
    ext = root.features.extrudeFeatures.addSimple(
        sk.profiles.item(0), VI.createByReal(0.5),
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    ext.bodies.item(0).name = name
    return ext, pc


def _raw_x(des, cf):
    """aruco_tags.face_frame's x of the node's face, at the node's place."""
    how = F._roll_to(cf)
    try:
        return aruco_tags_face_x(F.target_of(cf))
    finally:
        F._roll_back(des, how)


def aruco_tags_face_x(face):
    return T.face_frame(F.native_of(face))[1]


def _edges(des, cf):
    """The node's face's straight edges (length mm, direction), at its place."""
    how = F._roll_to(cf)
    try:
        f = F.native_of(F.target_of(cf))
        out = []
        for e in f.edges:
            g = e.geometry
            if isinstance(g, adsk.core.Line3D):
                a, b = g.startPoint, g.endPoint
                out.append((round(e.length * 10, 3),
                            [round(b.x - a.x, 3), round(b.y - a.y, 3), round(b.z - a.z, 3)]))
        return sorted(out, key=lambda t: -t[0])
    finally:
        F._roll_back(des, how)


def _frame_x(des, key):
    """(the node's x evaluated now, the record's x, the Joint Origin's x)."""
    comp, rec = _rec(des, key)
    cf = _cf(des, key)
    how = F._roll_to(cf)
    try:
        ev = F.evaluate(F.target_of(cf), F.settings_of(cf), F.numbers_of(cf))
    finally:
        F._roll_back(des, how)
    jo = T.joint_origin_of(des, comp, rec)
    return ev["tag"][1], rec["x_axis"], (T._jo_frame(jo)[1] if jo is not None else None)


def flip_case(des, step, dy=106.0):
    """An upstream edit that reverses the face's longest edge must not turn
    the tag (2026-10-08: the IDs agent saw a live node rebuild its pattern
    180° turned): a new node (x_base / x_ref at creation) and an older one
    (no x_ref: refresh_live takes it from its record first)."""
    root = des.rootComponent
    flipped = {}
    for key, x0, legacy, rev in (("xf", -30.0, False, False), ("xg", -22.0, False, True),
                                 ("xo", -14.0, True, False), ("xp", -6.0, True, True)):
        ext, corner = _quad_plate(root, x0, dy, -0.1, f"plate {key}", top_reversed=rev)

        def one(key=key, ext=ext, corner=corner, legacy=legacy):
            body = ext.bodies.item(0)
            make(_top_face(body), mode="black", key=key, marker_id=40 + len(key), size=20.0)
            cf = _cf(des, key)
            out = {}
            if legacy:
                s = F.settings_of(cf)
                s.pop("x_ref", None)
                s.pop("x_base", None)
                cf.customNamedValues.addOrSetValue("spec", __import__("json").dumps(s, sort_keys=True))
                F.refresh_one(_cf(des, key))     # what refresh_live does per node
                out["adopted_x_ref"] = (F.settings_of(_cf(des, key)) or {}).get("x_ref")
                _check(out["adopted_x_ref"], "refresh_live should give an old node its x_ref", out)
            cf = _cf(des, key)
            raw0 = _raw_x(des, cf)
            out["edges_before"] = _edges(des, cf)[:2]
            x_eval0, x_rec0, x_jo0 = _frame_x(des, key)
            corner.move(adsk.core.Vector3D.create(0.2, 0.0, 0.0))     # top edge 59 -> 61 mm
            _raw_x(des, _cf(des, key))       # a sketch-point move recomputes lazily: this
                                             # roll makes it happen (not computeAll: the
                                             # whole test design, minutes)
            cf = _cf(des, key)
            raw1 = _raw_x(des, cf)
            # what the node would do WITHOUT its stored x (the bug)
            how = F._roll_to(cf)
            try:
                bare = {k: v for k, v in F.settings_of(cf).items() if k not in ("x_base", "x_ref")}
                x_bare = F.evaluate(F.target_of(cf), bare, F.numbers_of(cf))["tag"][1]
            finally:
                F._roll_back(des, how)
            out["edges_after"] = _edges(des, cf)[:2]
            F.refresh_one(_cf(des, key))
            x_eval1, x_rec1, x_jo1 = _frame_x(des, key)
            d = lambda a, b: round(sum(p * q for p, q in zip(a, b)), 6)
            out.update({"raw_edge_x_dot": d(raw0, raw1), "without_fix_x_dot": d(x_eval0, x_bare),
                        "eval_x_dot": d(x_eval0, x_eval1),
                        "record_x_dot": d(x_rec0, x_rec1),
                        "jo_x_dot": d(x_jo0, x_jo1) if x_jo0 and x_jo1 else None,
                        "health": int(_cf(des, key).healthState)})
            flipped[key] = out["raw_edge_x_dot"] < -0.99 and out["without_fix_x_dot"] < -0.99
            _check(out["eval_x_dot"] > 0.9999 and out["record_x_dot"] > 0.9999
                   and (out["jo_x_dot"] or 0) > 0.9999 and out["health"] == 0,
                   "the pattern / record / Joint Origin turned with the edge", out)
            return out
        step(f"x_ref {key}: an upstream edit moves the longest edge, the tag does not turn"
             + (" (older node: x_ref from its record)" if legacy else ""), one)

    def proved():
        out = dict(flipped)
        _check(any(flipped.get(k) for k in ("xf", "xg")) and any(flipped.get(k) for k in ("xo", "xp")),
               "one drawing of each kind should reverse the face's longest edge (else the "
               "steps above prove nothing)", out)
        return out
    step("x_ref: the edit did reverse face_frame's x (new and older node)", proved)


def wrap_first_probe(des, step, dy=114.0):
    """Feasibility: a full-width board (its pocket splits the face) as ONE
    live node -- base features wrapped FIRST (the face still whole), the
    combines added after the node, then the node's range stretched over
    them (setStartAndEndFeatures). Measures; changes nothing in the add-in."""
    root = des.rootComponent
    e1, body = _plate(root, -30.0, dy, 8.0, 6.0, "plate wf")

    def one():
        face = _top_face(body)
        s = F.default_settings()
        s.update(mode="black", layout="charuco", key="wf", black_follows=True)
        n = F.default_numbers("black")
        s, under = F.creation_settings(face, s)
        nat = F.native_of(face)
        s["x_base"] = list(T.face_frame(nat)[1])
        ev = F.evaluate(nat, s, n)
        s["x_ref"] = list(ev["x_fit"])
        s["parts"] = list(ev["plan"].parts)
        spec = ev["spec"]
        n = dict(n, size=spec.marker_length_m * 1000.0, square=spec.square_length_m * 1000.0)
        comp = root
        tl = des.timeline
        out = {"parts": s["parts"]}
        bfs = {}
        for role in s["parts"]:
            bf, b = F._base_body(comp, F.part_body(role, ev, n), f"{role} wf")
            bfs[role] = (bf, b)
        feats = [bfs[r][0] for r in s["parts"]]
        cf = F._wrap(des, comp, face, feats, s, n, spec)
        out["wrapped"] = {"index": cf.timelineObject.index, "health": int(cf.healthState),
                          "marker": [tl.markerPosition, tl.count]}
        cut = F._combine(comp, body, bfs["recess"][1], CUT, False)
        out["cut"] = {"index": cut.timelineObject.index, "health": int(cut.healthState)}
        tries = {}
        for how in ("before", "after"):
            try:
                cf.timelineObject.rollTo(how == "before")
                ok = cf.setStartAndEndFeatures(feats[0], cut)
                tries[how] = bool(ok)
                tl.moveToEnd()
                break
            except Exception as exc:               # noqa: BLE001
                tries[how] = f"{type(exc).__name__}: {exc}"[:200]
                tl.moveToEnd()
        out["stretched"] = tries
        out["after"] = {"cf_health": int(cf.healthState), "features": [f.name for f in cf.features],
                        "marker": [tl.markerPosition, tl.count], "faces": body.faces.count,
                        "timeline": [tl.item(i).name for i in range(max(0, tl.count - 4), tl.count)]}
        F.paint(des, s, None, bfs["pattern"][0])
        F._param(cf, "marker_id").value = 3.0
        out["after_param"] = {"cf_health": int(cf.healthState), "faces": body.faces.count}
        e1.extentOne.distance.expression = "7 mm"
        pat = _body(root, "pattern wf")
        out["after_upstream"] = {"cf_health": int(cf.healthState),
                                 "pattern_mm": _z_span(pat, 0.5) if pat else None,
                                 "faces": body.faces.count}
        return out
    step("probe: wrap the base features first, then stretch the node over the cut", one)


# ---------------------------------------------------------- group fallback

def fallback_case(des, step, dy=82.0):
    """When the live node cannot be made (anything raised in _wrap -- Zach's
    2026-10-08 ChArUco board hit a KeyError there), the features end up in
    ONE timeline group, never loose."""
    root = des.rootComponent
    _ext, body = _plate(root, -24.0, dy, 6.0, 5.0, "plate fb")

    def one():
        real = F._wrap

        def broken(*a, **kw):
            raise KeyError("thickness")
        F._wrap = broken
        try:
            rec = make(_top_face(body), mode="both", op="join", key="fb", marker_id=4)
        finally:
            F._wrap = real
        fz = rec.get("fusion") or {}
        tl = des.timeline
        groups = []
        for i in range(tl.timelineGroups.count):
            g = tl.timelineGroups.item(i)
            groups.append({"name": g.name, "count": g.count})
        out = {"live": fz.get("custom_feature"), "live_error": fz.get("live_error"),
               "group": fz.get("group"), "group_error": fz.get("group_error"), "groups": groups}
        _check(not out["live"] and "thickness" in (out["live_error"] or ""),
               "the broken wrap should leave no live node", out)
        _check(out["group"] and any(g["name"].startswith("ArUco fb") and g["count"] >= 4
                                    for g in groups),
               "the features should be ONE group named after the tag", out)
        return out
    step("fallback: the live wrap raises -> the features are one timeline group", one)


# ------------------------------------------------------------------ runner

GROUPS = ("heights", "follow", "pins", "migration", "fallback", "turns", "split", "flip")


def heights_test(out_dir="", keep="0", parts=",".join(GROUPS)):
    """The cases above, each group in a NEW document of its own (closed after
    unless keep=1), one after the other. Small documents: every scripted edit
    and every Joint Origin check rolls the timeline, which recomputes what
    comes after -- with all the groups in one timeline Fusion's hang
    detector fired and Fusion had to be killed (2026-10-08). Stops at the
    first group that cannot start (Fusion busy) or that the user interrupts
    by switching documents."""
    lab = _lab()
    want = [g for g in str(parts).split(",") if g]
    import aruco_live_test

    def one_group(name):
        def run(des):
            def body(step):
                if name == "expr":
                    step("expression forms", lambda: expr_probe(des))
                elif name == "heights":
                    hp = heights_case(des, step)
                    if out_dir:
                        os.makedirs(out_dir, exist_ok=True)
                        step("height shots", lambda: heights_shots(des, out_dir, hp))
                elif name == "follow":
                    follow_case(des, step)
                elif name == "pins":
                    pins_case(des, step, shots=out_dir)
                elif name == "migration":
                    migration_case(des, step)
                elif name == "fallback":
                    fallback_case(des, step)
                elif name == "turns":
                    turns_case(des, step)
                elif name == "split":
                    split_case(des, step)
                elif name == "flip":
                    flip_case(des, step)
                elif name == "wrapfirst":
                    wrap_first_probe(des, step)
                else:
                    raise ValueError(f"unknown group {name!r}")
            res = lab.run_steps(des, body)
            if not res.get("aborted"):
                res["jo"] = aruco_live_test._jo_check(des)
            return res
        return lab.in_new_doc(run, keep)

    out = {"steps": [], "groups": {}}
    worst = [0.0, 0.0]
    jo_ok, rec_ok, n_tags = True, True, 0
    for name in want:
        res = one_group(name)
        if res.get("error"):
            out["groups"][name] = {"error": res["error"], "where": res.get("where")}
            out["aborted"] = f"{name}: {res['error']}"
            break
        out["steps"] += [dict(s_, step=f"[{name}] {s_['step']}") for s_ in res.get("steps", [])]
        jo = res.get("jo") or {}
        out["groups"][name] = {"steps": len(res.get("steps", [])),
                               "ok": all(s_["ok"] for s_ in res.get("steps", [])),
                               "jo_ok": jo.get("ok"), "aborted": res.get("aborted")}
        if res.get("aborted"):
            out["aborted"] = f"{name}: {res['aborted']}"
            break
        jo_ok = jo_ok and bool(jo.get("ok") or not jo.get("tags"))
        rec_ok = rec_ok and bool(jo.get("record_ok") or not jo.get("tags"))
        n_tags += len(jo.get("tags") or [])
        worst = [max(worst[0], jo.get("worst_mm", 0.0)), max(worst[1], jo.get("worst_deg", 0.0))]
    out["jo"] = {"ok": jo_ok and not out.get("aborted"), "record_ok": rec_ok,
                 "worst_mm": worst[0], "worst_deg": worst[1], "tags": [None] * n_tags}
    out["verdict"] = ("PASS" if all(s_["ok"] for s_ in out["steps"]) and out["jo"]["ok"]
                      and not out.get("aborted") else "FAIL")
    out["failed"] = [{"step": s_["step"], "error": s_.get("error"), "where": s_.get("where")}
                     for s_ in out["steps"] if not s_["ok"]]
    return out


def expr_probe(des):
    """Which expression forms Fusion takes for "the white top" (max of the
    white extrude's two ends): user parameters a = 2 mm, b = -3 mm."""
    ups = des.userParameters
    ups.add("probe_a", VI.createByString("2 mm"), "mm", "")
    ups.add("probe_b", VI.createByString("-3 mm"), "mm", "")
    out = {}
    for i, e in enumerate(("max(probe_a; probe_a + probe_b)", "max(probe_a, probe_a + probe_b)",
                           "probe_a + (probe_b + abs(probe_b)) / 2",
                           "probe_a + max(probe_b; 0 mm)", "probe_a + max(probe_b, 0 mm)")):
        try:
            p = ups.add(f"probe_e{i}", VI.createByString(e), "mm", "")
            out[e] = {"mm": round(p.value * 10, 6) if p else None,
                      "expression": p.expression if p else None}
        except Exception as exc:                   # noqa: BLE001
            out[e] = f"{type(exc).__name__}: {str(exc)[:120]}"
    try:
        ups.itemByName("probe_b").expression = "1.5 mm"
        out["after b = 1.5"] = {k: round(ups.itemByName(f"probe_e{i}").value * 10, 6)
                                for i, k in enumerate(list(out)[:5])
                                if ups.itemByName(f"probe_e{i}") is not None}
    except Exception as exc:                       # noqa: BLE001
        out["after"] = str(exc)[:200]
    return out
