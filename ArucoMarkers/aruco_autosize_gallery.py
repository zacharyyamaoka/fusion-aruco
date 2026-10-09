"""The auto-size verification gallery: ten faces that are not plain boxes.

Zach, 2026-10-08: "please set up a couple of examples with holes,
non-rectangular shapes, etc. to verify that the algorithm works properly."

FACES describes each face with primitives in MILLIMETRES (circles, arcs,
polygons), so the same face can be
  * tessellated offline the way Fusion strokes an edge (loops_m: polygons in
    METRES, chord tolerance 0.05 mm like aruco_tags.face_loops_2d), for
    tests/test_aruco_autosize.py and tools/render_autosize_gallery.py;
  * drawn and extruded in Fusion (build_face), then given an ArUco Extrude
    by the face-click path (run_fusion, bridge action "autosize_gallery").

The pure part (FACES, TYPED, loops_mm, loops_m) is standard library only;
everything that touches Fusion imports adsk inside the function.
"""
from __future__ import annotations

import math

KINDS = ("single", "grid", "charuco")
CHORD_TOL_MM = 0.05                 # aruco_tags.face_loops_2d strokes edges at 0.005 cm


def _rect(x0, y0, w, h):
    return ("poly", [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)])


# outer shape, holes; mm, face frame = sketch XY
FACES = [
    {"key": "disc4", "title": "Zach's disc: Ø53, four Ø2.85 holes",
     "outer": ("circle", 0.0, 0.0, 26.5),
     "holes": [("circle", 23.75 * c, 23.75 * s, 1.425)
               for c, s in ((1, 0), (0, 1), (-1, 0), (0, -1))]},
    {"key": "dshape", "title": "D-shape: Ø50 disc, flat 11 mm off centre",
     "outer": ("dshape", 0.0, 0.0, 25.0, 11.0), "holes": []},
    {"key": "lshape", "title": "L-shape: 60 × 60, 20 mm legs",
     "outer": ("poly", [(0, 0), (60, 0), (60, 20), (20, 20), (20, 60), (0, 60)]), "holes": []},
    {"key": "plate_hole", "title": "Plate 80 × 50, Ø18 hole in the middle",
     "outer": _rect(0, 0, 80, 50), "holes": [("circle", 40.0, 25.0, 9.0)]},
    {"key": "ring", "title": "Ring: Ø60 outside, Ø30 inside",
     "outer": ("circle", 0.0, 0.0, 30.0), "holes": [("circle", 0.0, 0.0, 15.0)]},
    {"key": "hexagon", "title": "Hexagon: 60 across corners",
     "outer": ("ngon", 0.0, 0.0, 30.0, 6, 0.0), "holes": []},
    {"key": "stadium", "title": "Stadium slot: 74 × 24",
     "outer": ("stadium", 0.0, 0.0, 50.0, 12.0), "holes": []},
    {"key": "notch", "title": "Plate 70 × 45 with a 20 × 18 edge notch",
     "outer": ("poly", [(0, 0), (70, 0), (70, 45), (45, 45), (45, 27), (25, 27), (25, 45), (0, 45)]),
     "holes": []},
    {"key": "strip", "title": "Long thin strip: 150 × 14",
     "outer": _rect(0, 0, 150, 14), "holes": []},
    {"key": "triangle", "title": "Equilateral triangle: 70 mm sides",
     "outer": ("ngon", 0.0, 0.0, 70.0 / math.sqrt(3.0), 3, 90.0), "holes": []},
]

# typed sizes tried on every face: (layout, marker mm, square mm)
TYPED = [("single", 30.0, 0.0), ("grid", 8.0, 0.0)]
TYPED_EXTRA = {"disc4": [("single", 37.0, 0.0)]}      # what Zach typed on his disc


def face(key):
    return next(f for f in FACES if f["key"] == key)


def typed_for(key):
    return TYPED_EXTRA.get(key, []) + TYPED


# ------------------------------------------------------------- tessellation

def _arc_pts(cx, cy, r, a0, a1, tol=CHORD_TOL_MM, closed=False):
    """Points on an arc from angle a0 to a1 (radians, a1 > a0), chord error
    <= tol: both ends included unless closed (a full circle)."""
    span = a1 - a0
    step = 2.0 * math.acos(max(-1.0, 1.0 - tol / r)) if r > tol else math.pi / 2.0
    n = max(8 if closed else 1, int(math.ceil(span / step - 1e-9)))
    k = range(n) if closed else range(n + 1)
    return [(cx + r * math.cos(a0 + span * i / n), cy + r * math.sin(a0 + span * i / n)) for i in k]


def shape_pts(shape, tol=CHORD_TOL_MM):
    """One shape -> a closed polygon (mm, last point != first)."""
    kind = shape[0]
    if kind == "circle":
        _k, cx, cy, r = shape
        return _arc_pts(cx, cy, r, 0.0, 2.0 * math.pi, tol, closed=True)
    if kind == "poly":
        return [(float(x), float(y)) for x, y in shape[1]]
    if kind == "ngon":
        _k, cx, cy, r, n, rot = shape
        return [(cx + r * math.cos(math.radians(rot) + 2 * math.pi * i / n),
                 cy + r * math.sin(math.radians(rot) + 2 * math.pi * i / n)) for i in range(n)]
    if kind == "stadium":
        _k, cx, cy, length, r = shape
        h = length / 2.0
        right = _arc_pts(cx + h, cy, r, -math.pi / 2.0, math.pi / 2.0, tol)
        left = _arc_pts(cx - h, cy, r, math.pi / 2.0, 1.5 * math.pi, tol)
        return right + left
    if kind == "dshape":                 # the disc below the chord y = cy + flat
        _k, cx, cy, r, flat = shape
        t = math.asin(flat / r)          # the chord meets the circle at angles t and pi - t
        return _arc_pts(cx, cy, r, math.pi - t, 2.0 * math.pi + t, tol)
    raise ValueError(f"unknown shape {kind!r}")


def loops_mm(f, tol=CHORD_TOL_MM):
    return [shape_pts(f["outer"], tol)] + [shape_pts(h, tol)[::-1] for h in f.get("holes", [])]


def loops_m(f, tol=CHORD_TOL_MM):
    return [[(x / 1000.0, y / 1000.0) for x, y in lp] for lp in loops_mm(f, tol)]


def bbox_mm(f):
    pts = [p for lp in loops_mm(f) for p in lp]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def summary(lay, pl):
    """A Layout + placement as plain numbers (mm) for the gallery's JSON."""
    out = {"label": lay.label(), "ok": bool(lay.ok), "kind": lay.kind, "notes": list(lay.notes),
           "marker_mm": round(lay.marker_m * 1000.0, 4), "square_mm": round(lay.square_m * 1000.0, 4),
           "cols": lay.cols, "rows": lay.rows, "count": lay.count,
           "separation_mm": round(lay.separation_m * 1000.0, 4),
           "quiet_mm": round(lay.quiet_m * 1000.0, 4),
           "tile_mm": [round(v * 1000.0, 4) for v in lay.tile_m] if lay.marker_m > 0 else None,
           "margins_mm": [round(v * 1000.0, 4) for v in lay.margins_m], "fill": round(lay.fill, 4)}
    if pl:
        out["placement_mm"] = {k: round(pl[k] * 1000.0, 4) for k in ("cx", "cy", "w", "h")}
        out["placement_mm"]["angle_deg"] = pl.get("angle_deg", 0.0)
        if pl.get("clear"):
            out["clear_mm"] = {k: round(v * 1000.0, 4) for k, v in pl["clear"].items()}
    return out


# ------------------------------------------------------------------ Fusion

def _p(x_mm, y_mm):
    import adsk.core
    return adsk.core.Point3D.create(x_mm / 10.0, y_mm / 10.0, 0.0)


def _draw(sk, shape, dx, dy):
    """Draw one shape (mm, shifted by dx, dy) as real sketch curves."""
    import adsk.core
    curves = sk.sketchCurves
    kind = shape[0]
    if kind == "circle":
        _k, cx, cy, r = shape
        curves.sketchCircles.addByCenterRadius(_p(cx + dx, cy + dy), r / 10.0)
        return
    if kind in ("poly", "ngon"):
        pts = [_p(x + dx, y + dy) for x, y in shape_pts(shape)]
        lines = curves.sketchLines
        ln = lines.addByTwoPoints(pts[0], pts[1])
        start, prev = ln.startSketchPoint, ln.endSketchPoint
        for pt in pts[2:]:
            prev = lines.addByTwoPoints(prev, pt).endSketchPoint
        lines.addByTwoPoints(prev, start)
        return
    if kind == "stadium":
        _k, cx, cy, length, r = shape
        h = length / 2.0
        a1 = curves.sketchArcs.addByCenterStartSweep(_p(cx + h + dx, cy + dy),
                                                     _p(cx + h + dx, cy - r + dy), math.pi)
        a2 = curves.sketchArcs.addByCenterStartSweep(_p(cx - h + dx, cy + dy),
                                                     _p(cx - h + dx, cy + r + dy), math.pi)
        curves.sketchLines.addByTwoPoints(a1.endSketchPoint, a2.startSketchPoint)
        curves.sketchLines.addByTwoPoints(a2.endSketchPoint, a1.startSketchPoint)
        return
    if kind == "dshape":
        _k, cx, cy, r, flat = shape
        t = math.asin(flat / r)
        start = _p(cx + r * math.cos(math.pi - t) + dx, cy + r * math.sin(math.pi - t) + dy)
        arc = curves.sketchArcs.addByCenterStartSweep(_p(cx + dx, cy + dy), start, math.pi + 2.0 * t)
        curves.sketchLines.addByTwoPoints(arc.endSketchPoint, arc.startSketchPoint)
        return
    raise ValueError(f"unknown shape {kind!r}")


def build_face(root, f, dx, dy, thickness_mm=3.0, name=None):
    """Sketch face f at (dx, dy) mm on root's XY plane, extrude the area
    with its holes by thickness_mm as a new body; -> (body, top face)."""
    import adsk.core
    import adsk.fusion
    sk = root.sketches.add(root.xYConstructionPlane)
    sk.name = name or f["key"]
    _draw(sk, f["outer"], dx, dy)
    for h in f.get("holes", []):
        _draw(sk, h, dx, dy)
    # the face's area = the largest profile (every hole here is smaller than
    # what is left around it)
    best = max((sk.profiles.item(i) for i in range(sk.profiles.count)),
               key=lambda pr: pr.areaProperties().area, default=None)
    if best is None:
        raise RuntimeError(f"{f['key']}: the sketch made no profile")
    ext = root.features.extrudeFeatures.addSimple(
        best, adsk.core.ValueInput.createByReal(thickness_mm / 10.0),
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    body = ext.bodies.item(0)
    body.name = name or f["key"]
    top = None
    for fc in body.faces:
        pl = adsk.core.Plane.cast(fc.geometry)
        if pl is None:
            continue
        n = pl.normal
        if n.z > 0.999 and abs(fc.pointOnFace.z - thickness_mm / 10.0) < 1e-6:
            if top is None or fc.area > top.area:
                top = fc
    sk.isLightBulbOn = False
    return body, top


def _overlays_off(des):
    saved = []
    for comp in des.allComponents:
        for attr in ("isJointsFolderLightBulbOn", "isJointOriginsFolderLightBulbOn",
                     "isSketchFolderLightBulbOn", "isConstructionFolderLightBulbOn",
                     "isOriginFolderLightBulbOn"):
            if hasattr(comp, attr):
                saved.append((comp, attr, getattr(comp, attr)))
                try:
                    setattr(comp, attr, False)
                except Exception:                  # noqa: BLE001
                    pass
    return saved


def _restore(saved):
    for comp, attr, val in saved:
        try:
            setattr(comp, attr, val)
        except Exception:                          # noqa: BLE001
            pass


def _top_shot(vp, path, cx_mm, cy_mm, extent_mm, px=900):
    """An orthographic top view centred on (cx, cy), `extent_mm` across."""
    import adsk.core
    for _ in range(2):              # set twice: the first set after a fit can be overridden
        cam = vp.camera
        cam.cameraType = adsk.core.CameraTypes.OrthographicCameraType
        cam.isFitView = False
        cam.isSmoothTransition = False
        cam.target = adsk.core.Point3D.create(cx_mm / 10.0, cy_mm / 10.0, 0.0)
        cam.eye = adsk.core.Point3D.create(cx_mm / 10.0, cy_mm / 10.0, 100.0)
        cam.upVector = adsk.core.Vector3D.create(0.0, 1.0, 0.0)
        cam.setExtents(extent_mm / 10.0, extent_mm / 10.0)
        vp.camera = cam
        vp.refresh()
        adsk.doEvents()
    return bool(vp.saveAsImageFile(path, px, px))


def run_fusion(out_dir, keep="0", kinds="single,grid,charuco", only=""):
    """In a NEW document of this add-in's own: build every gallery face once
    per layout, put an ArUco Extrude (black only, auto size) on each top
    face by the face-click path (aruco_feature.create inside in_command),
    read back what was built, capture an orthographic top view of each with
    the joint / Joint Origin / sketch glyphs hidden, close the document (keep=0)
    and put the user's document back. -> {faces: {key: {kind: numbers}}}."""
    import json
    import os
    import time
    import traceback
    import adsk.core
    import adsk.fusion
    import aruco_actions
    import aruco_feature
    app = adsk.core.Application.get()
    aruco_actions._quiet_or_raise()
    before = app.activeDocument
    kinds = [k for k in str(kinds).split(",") if k in KINDS]
    faces = [f for f in FACES if not only or f["key"] in str(only).split(",")]
    os.makedirs(out_dir, exist_ok=True)
    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    aruco_actions._remember_active()
    out = {"document": doc.name, "faces": {}, "registered": aruco_feature.is_registered()}
    try:
        des = adsk.fusion.Design.cast(app.activeProduct)
        des.designType = adsk.fusion.DesignTypes.ParametricDesignType
        root = des.rootComponent
        import aruco_tags
        white = aruco_tags.appearance(des, aruco_tags.WHITE)
        built = []
        for i, f in enumerate(faces):
            x0, y0, x1, y1 = bbox_mm(f)
            for j, kind in enumerate(kinds):
                dx = j * 180.0 - (x0 + x1) / 2.0
                dy = -i * 100.0 - (y0 + y1) / 2.0
                row = {"centre_mm": [j * 180.0, -i * 100.0]}
                try:
                    body, top = build_face(root, f, dx, dy, name=f"{f['key']} {kind}")
                    if white is not None:
                        body.appearance = white            # the body is the white (black only)
                    row["face_area_mm2"] = round(top.area * 100.0, 3)
                    s = aruco_feature.default_settings()
                    s.update(layout=kind, mode="black", key=f"{f['key'][:6]}{kind[0]}")
                    n = aruco_feature.default_numbers("black")
                    # what the dialog shows for this click (the face is cut afterwards)
                    t0 = time.perf_counter()
                    ev = aruco_feature.evaluate(top, dict(s, has_body=True), n)
                    row["evaluate_s"] = round(time.perf_counter() - t0, 3)
                    row["notes"] = list(ev.get("notes") or [])
                    lay = ev["layout"]
                    row["label"] = lay.label()
                    row["tile_mm"] = [round(v * 1000.0, 4) for v in lay.tile_m]
                    t0 = time.perf_counter()
                    rec = aruco_feature.in_command(aruco_feature.create, top, s, n)
                    row["create_s"] = round(time.perf_counter() - t0, 3)
                    spec = rec.get("spec", {})
                    row.update({k: spec.get(k) for k in ("layout", "cols", "rows", "dictionary",
                                                         "marker_id", "quiet_modules")})
                    row["marker_mm"] = round(spec.get("marker_length_m", 0) * 1000.0, 4)
                    row["square_mm"] = round(spec.get("square_length_m", 0) * 1000.0, 4)
                    row["live"] = bool((rec.get("fusion") or {}).get("custom_feature"))
                    if (rec.get("fusion") or {}).get("live_error"):
                        row["live_error"] = rec["fusion"]["live_error"]
                    org = rec.get("origin")            # the pattern centre (cm, root)
                    if org:
                        row["tile_centre_mm"] = [round(org[0] * 10.0 - dx, 3),
                                                 round(org[1] * 10.0 - dy, 3)]
                except Exception as exc:           # noqa: BLE001
                    row["error"] = f"{type(exc).__name__}: {exc}"[:400]
                    row["where"] = traceback.format_exc()[-600:]
                out["faces"].setdefault(f["key"], {})[kind] = row
                built.append((f, kind, row))
        vp = app.activeViewport
        saved = _overlays_off(des)
        style = vp.visualStyle
        try:
            vp.visualStyle = adsk.core.VisualStyles.ShadedVisualStyle
            for f, kind, row in built:
                x0, y0, x1, y1 = bbox_mm(f)
                ext = max(x1 - x0, y1 - y0) * 1.12
                name = f"fusion_{f['key']}_{kind}.png"
                cx, cy = row["centre_mm"]
                row["shot"] = name if _top_shot(vp, os.path.join(out_dir, name), cx, cy, ext) else None
                row["shot_extent_mm"] = round(ext, 3)
        finally:
            try:
                vp.visualStyle = style
            except Exception:                      # noqa: BLE001
                pass
            _restore(saved)
        out["unhealthy"] = []
        for i in range(des.timeline.count):
            it = des.timeline.item(i)
            try:
                if it.healthState != adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState:
                    out["unhealthy"].append({"i": i, "name": it.entity.name if it.entity else None,
                                             "msg": it.errorOrWarningMessage[:200]})
            except Exception:                      # noqa: BLE001
                pass
        with open(os.path.join(out_dir, "fusion_gallery.json"), "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1)
    finally:
        if str(keep) not in ("1", "true", "yes"):
            try:
                if app.activeDocument == doc:
                    doc.close(False)
                    out["closed"] = True
            except Exception as exc:               # noqa: BLE001
                out["close_error"] = str(exc)[:200]
        if before is not None:
            try:
                before.activate()
                out["reactivated"] = before.name
            except Exception as exc:               # noqa: BLE001
                out["reactivate_error"] = str(exc)[:200]
    return out
