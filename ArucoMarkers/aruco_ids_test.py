"""Bridge test of ArUco IDs (per-copy serial ids) on live ArUco Extrude tags.

    python tools/aruco_bridge.py ids_test --arg out_dir=<dir> [--arg legacy=<old aruco_tags.py>]
    python tools/aruco_bridge.py ids_inspect --arg "document=One Dof Test Rack"

Zach, 2026-10-08: "When I do ArUco IDs I want to see a preview. Also when I
actually did it, it didn't put them in the correct location."

run() mirrors his "One Dof Test Rack" in a NEW document of ours: an "Aruco
Marker" part (a 1 mm plate with two holes, a 3 x 1 DICT_4X4_50 grid made
with ArUco Extrude: White + black, New body) and an "Aruco Marker K" part
(his own mode: Black only, inlaid into the plate): twice at the root each
(one tilted 10 deg, one rigidly jointed to its mounting plate) and two
levels down in Rack:1 > Holder:1 / :2 (Holder:2 turned 12 deg about a skew
axis; each holder carries two "Aruco Marker" copies and one "K") -- then
changes both plates upstream (150 x 46 -> 142 x 40 mm: the nodes re-fit,
the records do not), then the IDs flow exactly as the dialog runs it
(Number duplicates at Holder, at Rack, then at the design; ids_write +
apply_variants inside a command), so variants are owned by the root, by
Rack and by Holder (two levels down). With `legacy` (the add-in's aruco_tags.py
before the fix) the variants are built the old way first and measured
("before"), then the fixed apply_variants runs on the same document
("after"), then once more (idempotent), then Plate 2 is moved (does the
variant follow its copy?). Then the plates change again (stale records) and
the dialog itself runs: open (activate evaluates the stale nodes), a spinner
edit, Number duplicates, the ghost measured against the truth and timed,
Cancel (nothing left), OK (ghost gone, records refreshed, variants rebuilt).

Every measurement compares against the TRUTH: each template tag evaluated by
its node at its own place in the timeline (rolled), taken through each
copy's world transform:
  placement_mm   the shown pattern body's footprint + top along the true
                 frame vs the spec's (pattern independent; 0 = in place)
  pattern_bad    black-cell centres outside / white points inside the shown
                 pattern for the RESOLVED id (orientation + id)
  vrec_mm/deg    the variant's record frame through its occurrence vs truth
  export_mm/deg  resolve_live()'s world frame (what the exporter reads) vs truth
  jo_mm/deg      the template's Joint Origin at that copy vs the optical frame
  joint_mm       the variant's as-built joint glyph vs the tag centre (info)
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import time
import traceback

import adsk.core
import adsk.fusion

import aruco_core
import aruco_demo
import aruco_tags

P = adsk.core.Point3D.create
V = adsk.core.Vector3D.create
VI = adsk.core.ValueInput


def M():
    return aruco_core.module("markers")


def F():
    import aruco_feature
    return aruco_feature


# --------------------------------------------------------------- the guard
#
# Zach works in Fusion while this runs (2026-10-08: a pumped request once
# ended up acting under his feet and Fusion crashed). Commands and
# aruco_tags.design() act on the ACTIVE document, so before every step and
# after every event pump the active document must still be the test's; if
# he switched tabs, the run aborts and touches nothing more.

_GUARD = {"doc": None}


class NotMine(RuntimeError):
    pass


def _assert_mine():
    doc = _GUARD.get("doc")
    if doc is None:
        return
    act = adsk.core.Application.get().activeDocument
    if act is None or act != doc:
        raise NotMine(f"the active document is '{act.name if act else None}', not the "
                      "test's -- aborted, nothing more touched")


def _jsonable(v):
    """A step's value as the bridge can write it (str keys, no API objects)."""
    if isinstance(v, dict):
        return {(k if isinstance(k, str) else "|".join(map(str, k)) if isinstance(k, tuple)
                 else str(k)): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    return str(v)


def _clear_selection():
    try:
        adsk.core.Application.get().userInterface.activeSelections.clear()
    except Exception:                              # noqa: BLE001
        pass


# ----------------------------------------------------------------- fixture

def _cs(o, x, y, z):
    m = adsk.core.Matrix3D.create()
    m.setWithCoordinateSystem(P(*o), V(*x), V(*y), V(*z))
    return m


def _rot(axis, deg, t=(0.0, 0.0, 0.0)):
    m = adsk.core.Matrix3D.create()
    m.setToRotation(math.radians(deg), V(*axis), P(0, 0, 0))
    m.translation = V(*t)
    return m


def _then(a, b):
    """a, then b (b * a)."""
    m = a.copy()
    m.transformBy(b)
    return m


# the marker part's +z (its plate's normal) -> world +x: on a vertical plate
R0 = ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))


def _plate_part(comp, w0=15.0, h0=4.6):
    """Zach's marker plate: a 1 mm plate with two 6.5 mm holes 130 mm apart,
    its outline driven by two sketch dimensions (the top-right corner fixed)
    so it can change UPSTREAM of the tag. -> (width dim, height dim)."""
    sk = comp.sketches.add(comp.xYConstructionPlane)
    sk.name = "plate"
    x1, y1 = 7.1, 2.0
    lines = sk.sketchCurves.sketchLines.addTwoPointRectangle(P(x1 - w0, y1 - h0, 0), P(x1, y1, 0))
    pts = []
    for i in range(lines.count):
        ln = lines.item(i)
        pts += [ln.startSketchPoint, ln.endSketchPoint]
    for sp in pts:
        g = sp.geometry
        if abs(g.x - x1) < 1e-6 and abs(g.y - y1) < 1e-6:
            sp.isFixed = True
    bottom = left = None
    for i in range(lines.count):
        ln = lines.item(i)
        a, b = ln.startSketchPoint.geometry, ln.endSketchPoint.geometry
        if abs(a.y - (y1 - h0)) < 1e-6 and abs(b.y - (y1 - h0)) < 1e-6:
            bottom = ln
        if abs(a.x - (x1 - w0)) < 1e-6 and abs(b.x - (x1 - w0)) < 1e-6:
            left = ln
    D = adsk.fusion.DimensionOrientations
    dw = sk.sketchDimensions.addDistanceDimension(
        bottom.startSketchPoint, bottom.endSketchPoint, D.HorizontalDimensionOrientation,
        P(0, y1 - h0 - 1.0, 0))
    dh = sk.sketchDimensions.addDistanceDimension(
        left.startSketchPoint, left.endSketchPoint, D.VerticalDimensionOrientation,
        P(x1 - w0 - 1.0, 0, 0))
    for cx in (-6.5, 6.5):
        sk.sketchCurves.sketchCircles.addByCenterRadius(P(cx, 0, 0), 0.325)
    prof = max((sk.profiles.item(i) for i in range(sk.profiles.count)),
               key=lambda p: p.areaProperties().area)
    ext = comp.features.extrudeFeatures.addSimple(
        prof, VI.createByReal(0.1), adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    ext.bodies.item(0).name = "Body1"
    white = aruco_tags.appearance(comp.parentDesign, "Plastic - Matte (Grey)")
    if white:
        ext.bodies.item(0).appearance = white
    return dw, dh


def _mount_plate(comp, name):
    ext = aruco_demo._box(comp, 14.3, -13.0, 15.3, 13.0, 35.0, name)
    alu = aruco_tags.appearance(comp.parentDesign, "Aluminum - Satin")
    if alu:
        ext.bodies.item(0).appearance = alu
    return ext.bodies.item(0)


def _top_face_proxy(occ, body_name="Body1"):
    """The +z face of a part's body, as seen through `occ` (a root proxy:
    Zach's tag depends on a face picked in 'Aruco Marker:1')."""
    body = occ.component.bRepBodies.itemByName(body_name)
    best = None
    for f in body.faces:
        if not isinstance(f.geometry, adsk.core.Plane):
            continue
        ok, n = f.evaluator.getNormalAtPoint(f.pointOnFace)
        if n.z > 0.999 and (best is None or f.pointOnFace.z > best.pointOnFace.z):
            best = f
    return best.createForAssemblyContext(occ)


def _close_m(a, b, tol=1e-7):
    return all(abs(x - y) < tol for x, y in zip(a.asArray(), b.asArray()))


def build(des):
    """The rack. -> {"dims": [...], "occ": {...}, "comps": {...}}."""
    root = des.rootComponent
    L_m = _cs((15.3, 0.0, 26.0), *R0)
    L_k = _cs((15.3, 0.0, 12.0), *R0)
    # the root stations beside the rack (the rack stays at the origin: an
    # occurrence added two levels down under a MOVED parent lands at L * P,
    # not P * L, and a moved rack did not take a new transform -- measured)
    S1 = _rot((0, 0, 1), 0.0, (0.0, -70.0, 0.0))
    S2 = _rot((1, 0, 0), 10.0, (0.0, -35.0, 0.0))
    ax = [1.0, 0.5, 0.2]
    n = math.sqrt(sum(v * v for v in ax))
    H2 = _rot([v / n for v in ax], 12.0, (2.0, 30.0, 1.5))
    occ_m1 = root.occurrences.addNewComponent(_then(L_m, S1))
    cm = occ_m1.component
    cm.name = "Aruco Marker"
    occ_k1 = root.occurrences.addNewComponent(_then(L_k, S1))
    ck = occ_k1.component
    ck.name = "Aruco Marker K"
    dims = list(_plate_part(cm)) + list(_plate_part(ck))
    p1 = root.occurrences.addNewComponent(S1)
    p1.component.name = "Plate 1"
    _mount_plate(p1.component, "plate 1")
    p2 = root.occurrences.addNewComponent(S2)
    p2.component.name = "Plate 2"
    _mount_plate(p2.component, "plate 2")
    occ_m2 = root.occurrences.addExistingComponent(cm, _then(L_m, S2))
    occ_k2 = root.occurrences.addExistingComponent(ck, _then(L_k, S2))
    rack = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    rack.component.name = "Rack"
    h1 = rack.component.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    hc = h1.component
    hc.name = "Holder"
    _mount_plate(hc, "holder plate")
    # two marker copies in each holder: serials authored AT the Holder level
    # make variants owned by a component two levels down
    L_m2 = _cs((15.3, 0.0, 19.0), *R0)
    nested = []
    for comp, L in ((cm, L_m), (cm, L_m2), (ck, L_k)):
        o = hc.occurrences.addExistingComponent(comp, L)
        ent = {"occ": o.name, "as_given": _close_m(o.transform2, L)}
        if not ent["as_given"]:
            # measured: added to a component two levels down, the occurrence
            # landed at L * P instead of P * L; put it where it belongs
            ent["placed"] = aruco_tags.place(des, hc, o, L)
        nested.append(ent)
    rack.component.occurrences.addExistingComponent(hc, H2)
    # M:2 rigidly jointed to its plate (his copies sit on xref'd plates)
    try:
        ji = root.asBuiltJoints.createInput(occ_m2, p2, None)
        ji.setAsRigidJointMotion()
        root.asBuiltJoints.add(ji)
    except Exception:                              # noqa: BLE001
        pass
    return {"dims": dims, "m1": occ_m1, "k1": occ_k1, "cm": cm, "ck": ck, "nested": nested}


def placements(des):
    """Every occurrence: its root proxy's transform2 origin, the composed
    native transforms (aruco_tags._rel_transform from the root), and where
    its first body really is (bbox centre in root, through the proxy) vs
    where the composed transform puts that body's native bbox centre (mm)."""
    root = des.rootComponent
    out = []
    for o in root.allOccurrences:
        m = aruco_tags._rel_transform(root, o.fullPathName)
        t2 = o.transform2
        ent = {"path": o.fullPathName,
               "transform2_mm": [round(v * 10, 3) for v in t2.translation.asArray()],
               "composed_mm": [round(v * 10, 3) for v in m.translation.asArray()] if m else None,
               "same": bool(m) and all(abs(a - b) < 1e-7 for a, b in
                                       zip(t2.asArray(), m.asArray()))}
        if o.component.bRepBodies.count and m is not None:
            nb = o.component.bRepBodies.item(0)
            bb = nb.boundingBox
            c = P((bb.minPoint.x + bb.maxPoint.x) / 2, (bb.minPoint.y + bb.maxPoint.y) / 2,
                  (bb.minPoint.z + bb.maxPoint.z) / 2)
            pb = nb.createForAssemblyContext(o).boundingBox
            real = [(pb.minPoint.x + pb.maxPoint.x) / 2, (pb.minPoint.y + pb.maxPoint.y) / 2,
                    (pb.minPoint.z + pb.maxPoint.z) / 2]
            c2 = c.copy()
            c2.transformBy(t2)
            c.transformBy(m)
            ent["body"] = nb.name
            ent["body_real_mm"] = [round(v * 10, 2) for v in real]
            ent["body_by_composed_mm"] = [round(v * 10, 2) for v in (c.x, c.y, c.z)]
            ent["body_by_transform2_mm"] = [round(v * 10, 2) for v in (c2.x, c2.y, c2.z)]
        out.append(ent)
    return out


def make_tags(fx):
    """The two ArUco Extrudes, on faces picked through the first copies."""
    Fm = F()
    out = {}
    for occ, mode, mid in ((fx["m1"], "both", 0), (fx["k1"], "black", 10)):
        s = Fm.default_settings()
        s.update(mode=mode, op="new", layout="grid", dictionary="DICT_4X4_50", key="board",
                 size_auto=False)
        n = Fm.default_numbers(mode)
        n.update(marker_id=mid, size=30.5)
        _assert_mine()
        rec = Fm.in_command(Fm.create, _top_face_proxy(occ), s, n)
        _assert_mine()
        out[occ.component.name] = {"spec": rec["spec"].get("layout") + " "
                                   + str(rec["spec"].get("cols")) + "x" + str(rec["spec"].get("rows"))
                                   + " #" + str(rec["spec"].get("marker_id"))
                                   + f" {rec['spec'].get('marker_length_m', 0) * 1000:.2f} mm",
                                   "origin_mm": [round(v * 10, 3) for v in rec["origin"]],
                                   "live": bool((rec.get("fusion") or {}).get("custom_feature")),
                                   "mode": (rec.get("fusion") or {}).get("mode")}
    return out


def upstream(fx, w_mm=142.0, h_mm=40.0):
    """Both plates 150 x 46 -> 142 x 40 mm, the bottom-left corner moving:
    the nodes re-fit (smaller, recentred); the records stay."""
    fx["dims"][0].parameter.expression = f"{w_mm} mm"
    fx["dims"][1].parameter.expression = f"{h_mm} mm"
    fx["dims"][2].parameter.expression = f"{w_mm} mm"
    fx["dims"][3].parameter.expression = f"{h_mm} mm"
    adsk.doEvents()


# ------------------------------------------------------------- measuring

def _comp_label(des, comp):
    return "" if comp == des.rootComponent else comp.name


def drifts(des):
    """record_drift() for every tag: '' = the record matches its node."""
    out = {}
    for comp in des.allComponents:
        if aruco_tags.is_variant(comp):
            continue
        for rec in aruco_tags.read_tag_records(comp):
            out[f"{comp.name}|{rec.get('key')}"] = aruco_tags.record_drift(des, comp, rec)
    return out


def truth(des):
    """{(component label, key): record as its node evaluates NOW} -- always
    rolled (independent of record_drift); repainted after."""
    out = {}
    raw = aruco_tags.collect(des)
    for rec in raw["tags"]:
        out[(rec.get("component") or "", rec.get("key"))] = aruco_tags.evaluated_record(des, rec)
    try:
        F().paint_all(des)
    except Exception:                              # noqa: BLE001
        pass
    return out


def _xf(m, frame):
    """(o, x, y, z) through a Matrix3D."""
    o = P(*frame[0])
    o.transformBy(m)
    out = [[o.x, o.y, o.z]]
    for v in frame[1:]:
        w = V(*v)
        w.transformBy(m)
        out.append([w.x, w.y, w.z])
    return out


def _cols(m):
    return ([m[3], m[7], m[11]], [m[0], m[4], m[8]], [m[1], m[5], m[9]], [m[2], m[6], m[10]])


def _ferr(a, b):
    """(position cm, worst axis angle rad) between two frames."""
    d = aruco_tags._sub(a[0], b[0])
    ang = max(math.acos(max(-1.0, min(1.0, aruco_tags._dot(aruco_tags._unit(a[i]),
                                                            aruco_tags._unit(b[i])))))
              for i in (1, 2, 3))
    return math.sqrt(aruco_tags._dot(d, d)), ang


def _pattern_bad(body, spec, frame, lo, hi, white):
    """How many sample points disagree with `spec` at `frame` (black-cell
    centres must be inside the cells / white points outside; inverted for a
    white pattern). -> (bad, checked)."""
    o, x, y, z = frame
    mod = spec.module_m * 100.0
    boxes = [tuple(v * 100.0 for v in b) for b in M().cell_boxes(spec)]
    fx0, fx1, fy0, fy1 = aruco_tags._footprint_cm(spec, False)
    zc = (lo + hi) / 2.0
    inside = adsk.fusion.PointContainment.PointInsidePointContainment

    def in_black(u, v, margin):
        return any(b[0] - margin <= u <= b[2] + margin and b[1] - margin <= v <= b[3] + margin
                   for b in boxes)
    pts = []
    for b in boxes:
        pts.append(((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0, True))
        cy, cx = (b[1] + b[3]) / 2.0, (b[0] + b[2]) / 2.0
        for u, v in ((b[0] - mod / 2.0, cy), (b[2] + mod / 2.0, cy),
                     (cx, b[1] - mod / 2.0), (cx, b[3] + mod / 2.0)):
            if (fx0 + mod / 4.0 < u < fx1 - mod / 4.0 and fy0 + mod / 4.0 < v < fy1 - mod / 4.0
                    and not in_black(u, v, mod / 4.0)):
                pts.append((u, v, False))
    bad = 0
    for u, v, black in pts:
        p = [o[i] + x[i] * u + y[i] * v + z[i] * zc for i in range(3)]
        got = body.pointContainment(P(*p)) == inside
        if got != (black != white):
            bad += 1
    return bad, len(pts)


def _variants_by_template(des):
    """{(template root path, key): [(variant occurrence (root proxy), info)]}."""
    markers = M()
    out = {}
    for o in des.rootComponent.allOccurrences:
        a = o.component.attributes.itemByName(aruco_tags.G, aruco_tags.VARIANT_ATTR)
        if not a:
            continue
        try:
            info = json.loads(a.value)
        except ValueError:
            continue
        parts = markers.split_path(o.fullPathName)
        rel, key = info["slot"].split(markers.KEY_SEP)
        tpath = markers.join_path(parts[:-1] + markers.split_path(rel))
        out.setdefault((tpath, key), []).append((o, info))
    return out


def _joint_origin_of(des, vocc):
    """World origin of the as-built joint holding a variant occurrence."""
    comps = [des.rootComponent] + list(des.allComponents)
    for c in comps:
        try:
            joints = c.asBuiltJoints
        except Exception:                          # noqa: BLE001
            continue
        for j in joints:
            try:
                one = j.occurrenceOne
                if one is None or one.component != vocc.component:
                    continue
                g = j.geometry
                if g is None:
                    return None
                p = g.origin
                return [p.x, p.y, p.z]
            except Exception:                      # noqa: BLE001
                continue
    return None


def measure(des, T=None):
    """Every copy of every tag against the truth. -> {"rows", "worst"}."""
    markers = M()
    T = T or truth(des)
    root = des.rootComponent
    occs = {o.fullPathName: o for o in root.allOccurrences}
    variants = _variants_by_template(des)
    raw = aruco_tags.collect(des)
    raw["tags"] = [dict(T.get((r.get("component") or "", r.get("key")), r),
                        component=r.get("component") or "") for r in raw["tags"]]
    res = aruco_tags.resolve_live(des, raw)             # resolved ids, true frames
    exported = {i.name: i for i in aruco_tags.resolve_live(des).instances}   # the records
    rows = []
    worst = {"placement_mm": 0.0, "pattern_bad": 0, "vrec_mm": 0.0, "vrec_deg": 0.0,
             "export_mm": 0.0, "export_deg": 0.0, "jo_mm": 0.0, "jo_deg": 0.0,
             "visible_wrong": 0}
    for inst in res.instances:
        if not inst.path:
            continue
        key = inst.tag.key
        comp_label = inst.tag.component
        comp = aruco_tags._comp_by_label(des, comp_label)
        rec = T[(comp_label, key)]
        tag = markers.TagDef.from_dict(rec, lambda m: None)
        occ = occs[inst.path]
        W = occ.transform2
        Fw = _xf(W, (tag.origin, tag.x_axis, tag.y_axis, tag.z_axis))
        row = {"copy": inst.path, "key": key, "id": inst.spec.marker_id,
               "modelled": tag.spec.marker_id, "needs_variant": inst.needs_variant}
        vs = variants.get((inst.path, key), [])
        row["variants"] = [v.fullPathName for v, _i in vs]
        src_rec = rec
        if inst.needs_variant:
            vis = [(v, i) for v, i in vs if v.isVisible]
            row["template_visible"] = bool(occ.isVisible)
            if len(vis) != 1 or occ.isVisible:
                worst["visible_wrong"] += 1
            if not vs:
                row["error"] = "no variant"
                rows.append(row)
                worst["placement_mm"] = float("inf")
                continue
            vocc, info = (vis or vs)[0]
            vcomp = vocc.component
            vrec = aruco_tags.read_tag_records(vcomp)[0]
            body = aruco_tags.pattern_body(vcomp, vrec)
            shown = body.createForAssemblyContext(vocc) if body is not None else None
            vt = markers.TagDef.from_dict(vrec, lambda m: None)
            vw = _xf(vocc.transform2, (vt.origin, vt.x_axis, vt.y_axis, vt.z_axis))
            pos, ang = _ferr(vw, Fw)
            row["vrec_mm"], row["vrec_deg"] = round(pos * 10.0, 5), round(math.degrees(ang), 5)
            worst["vrec_mm"] = max(worst["vrec_mm"], row["vrec_mm"])
            worst["vrec_deg"] = max(worst["vrec_deg"], row["vrec_deg"])
            js = [j for j in joints_of(des, vocc) if j.get("kind") == "as-built"]
            row["joints"] = [{k: j.get(k) for k in ("in", "two", "geometry_origin_mm")} for j in js]
            g = next((j.get("geometry_origin_mm") for j in js if j.get("geometry_origin_mm")), None)
            if g is not None:
                d = aruco_tags._sub([v / 10.0 for v in g], Fw[0])
                row["joint_mm"] = round(math.sqrt(aruco_tags._dot(d, d)) * 10.0, 2)
            else:
                row["joint_mm"] = None
            # the variant's tile vs the template's at this copy (Zach's grew 12 mm)
            tname = (src_rec.get("fusion") or {}).get("tile_body")
            if tname:
                tb = comp.bRepBodies.itemByName(tname)
                vb = vcomp.bRepBodies.itemByName(tname)
                if tb is not None and vb is not None:
                    a = tb.createForAssemblyContext(occ).boundingBox
                    b = vb.createForAssemblyContext(vocc).boundingBox
                    row["tile_bbox_mm"] = round(max(
                        abs(a.minPoint.x - b.minPoint.x), abs(a.minPoint.y - b.minPoint.y),
                        abs(a.minPoint.z - b.minPoint.z), abs(a.maxPoint.x - b.maxPoint.x),
                        abs(a.maxPoint.y - b.maxPoint.y), abs(a.maxPoint.z - b.maxPoint.z)) * 10.0, 4)
        else:
            body = aruco_tags.pattern_body(comp, rec)
            shown = body.createForAssemblyContext(occ) if body is not None else None
            if not occ.isVisible:
                worst["visible_wrong"] += 1
        if shown is None:
            row["error"] = "no pattern body"
            rows.append(row)
            continue
        white = aruco_tags._is_white(rec, shown)
        ext = aruco_tags._extents(shown, Fw)
        want = aruco_tags._footprint_cm(inst.spec, white)
        got = (ext[0][0], ext[0][1], ext[1][0], ext[1][1])
        top = (inst.spec.raise_m if white else getattr(inst.spec, "black_top_m", inst.spec.raise_m)) * 100.0
        errs = [a - b for a, b in zip(got, want)] + [ext[2][1] - top]
        row["placement_mm"] = round(max(abs(e) for e in errs) * 10.0, 5)
        row["offset_mm"] = [round((errs[0] + errs[1]) / 2.0 * 10.0, 4),
                            round((errs[2] + errs[3]) / 2.0 * 10.0, 4), round(errs[4] * 10.0, 4)]
        row["size_err_mm"] = [round((errs[1] - errs[0]) * 10.0, 4), round((errs[3] - errs[2]) * 10.0, 4)]
        bad, n = _pattern_bad(shown, inst.spec, Fw, ext[2][0], ext[2][1], white)
        row["pattern_bad"] = f"{bad}/{n}"
        worst["placement_mm"] = max(worst["placement_mm"], row["placement_mm"])
        worst["pattern_bad"] += bad
        ex = exported.get(inst.name)
        if ex is not None and ex.world is not None:
            pos, ang = _ferr(_cols(ex.world), Fw)
            row["export_mm"], row["export_deg"] = round(pos * 10.0, 5), round(math.degrees(ang), 5)
            worst["export_mm"] = max(worst["export_mm"], row["export_mm"])
            worst["export_deg"] = max(worst["export_deg"], row["export_deg"])
        try:
            jo = aruco_tags.joint_origin_of(des, comp, rec)
            if jo is not None:
                jw = aruco_tags._jo_frame(jo.createForAssemblyContext(occ))
                opt = _xf(W, aruco_tags.optical_frame_cm(tag))
                pos, ang = _ferr(jw, opt)
                row["jo_mm"], row["jo_deg"] = round(pos * 10.0, 5), round(math.degrees(ang), 5)
                worst["jo_mm"] = max(worst["jo_mm"], row["jo_mm"])
                worst["jo_deg"] = max(worst["jo_deg"], row["jo_deg"])
        except Exception as exc:                   # noqa: BLE001
            row["jo_error"] = str(exc)[:120]
        rows.append(row)
    worst = {k: (round(v, 5) if isinstance(v, float) else v) for k, v in worst.items()}
    worst["ok"] = (worst["placement_mm"] < 1e-3 and worst["pattern_bad"] == 0
                   and worst["vrec_mm"] < 1e-3 and worst["vrec_deg"] < 1e-3
                   and worst["visible_wrong"] == 0)
    return {"rows": rows, "worst": worst}


# --------------------------------------------------------------- captures

_OVERLAYS = ("isJointsFolderLightBulbOn", "isJointOriginsFolderLightBulbOn",
             "isSketchFolderLightBulbOn", "isConstructionFolderLightBulbOn",
             "isOriginFolderLightBulbOn")


def shot(des, path, eye=None, target=None, up=None, fov_deg=30.0, width=1600, height=1000):
    """A viewport capture with the joint, Joint Origin, sketch, construction
    and origin folders' bulbs off (put back after). eye/target in cm; no eye
    = iso, fit."""
    app = adsk.core.Application.get()
    vp = app.activeViewport
    saved = []
    for comp in des.allComponents:
        for attr in _OVERLAYS:
            if hasattr(comp, attr):
                saved.append((comp, attr, getattr(comp, attr)))
                try:
                    setattr(comp, attr, False)
                except Exception:                  # noqa: BLE001
                    pass
    try:
        cam = vp.camera
        if eye is None:
            cam.viewOrientation = adsk.core.ViewOrientations.IsoTopRightViewOrientation
            cam.isFitView = True
        else:
            cam.cameraType = adsk.core.CameraTypes.PerspectiveCameraType
            cam.isFitView = False
            cam.isSmoothTransition = False
            cam.eye, cam.target = P(*eye), P(*target)
            cam.upVector = V(*(up or (0, 0, 1)))
            cam.perspectiveAngle = math.radians(fov_deg)
        vp.camera = cam
        vp.refresh()
        adsk.doEvents()
        if eye is not None:                          # set twice (a fit can win the first)
            vp.camera = cam
            vp.refresh()
            adsk.doEvents()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        ok = vp.saveAsImageFile(path, width, height)
    finally:
        for comp, attr, val in saved:
            try:
                setattr(comp, attr, val)
            except Exception:                      # noqa: BLE001
                pass
    return {"ok": bool(ok), "path": path}


def _closeup(des, path, copy_path, T, dist_cm=28.0):
    """A shot square onto one copy's tag (truth frame), a little from above."""
    markers = M()
    occ = next(o for o in des.rootComponent.allOccurrences if o.fullPathName == copy_path)
    rec = T[(_comp_label(des, occ.component), "board")]
    tag = markers.TagDef.from_dict(rec, lambda m: None)
    o, x, y, z = _xf(occ.transform2, (tag.origin, tag.x_axis, tag.y_axis, tag.z_axis))
    eye = [o[i] + z[i] * dist_cm + y[i] * dist_cm * 0.25 for i in range(3)]
    return shot(des, path, eye=eye, target=o, up=y)


def captures(des, out_dir, label, T):
    if not out_dir:
        return []
    out = [shot(des, os.path.join(out_dir, f"{label}_rack_iso.png"))]
    paths = [o.fullPathName for o in des.rootComponent.allOccurrences]

    def find(prefix, comp):
        return next((p for p in paths if p.startswith(prefix) and p.split("+")[-1].startswith(comp + ":")
                     and p.count("+") == prefix.count("+")), None)
    for cp, name in ((find("", "Aruco Marker") and "Aruco Marker:2", "marker2"),
                     (find("", "Aruco Marker K") and "Aruco Marker K:2", "k2"),
                     (find("Rack:1+Holder:2+", "Aruco Marker"), "rack_holder2")):
        try:
            out.append(_closeup(des, os.path.join(out_dir, f"{label}_{name}.png"), cp, T))
        except StopIteration:
            out.append({"ok": False, "path": cp, "error": "no such copy"})
    return out


# ------------------------------------------------------------ the IDs flow

def _level(des, name):
    import aruco_commands as C
    return dict((c.name if c != des.rootComponent else "", c) for _l, c in C._levels(des))[name]


def ids_ok(des, level_comp, auto=True, edits=None, rebuild=True, variants_fn=None):
    """What the dialog does at one level: the table (_level_resolution),
    Number duplicates (plan_serials), `edits` {(rel path, key): id}, then
    OK -- ids_write + apply_variants (or `variants_fn`) in ONE command."""
    import aruco_commands as C
    markers = M()
    res, tags, ovs, occs = C._level_resolution(des, level_comp)
    rows = [(i.path, i.tag.key, i.spec.marker_id, f"r{n}")
            for n, i in enumerate(res.instances) if i.path]
    values = {}
    if auto:
        planned, after = markers.plan_serials(tags, ovs, occs, level_comp.name)
        plan = {(i.path, i.tag.key): i.spec.marker_id for i in after.instances}
        values = {sid: plan.get((p, k), o) for p, k, o, sid in rows}
    for p, k, o, sid in rows:
        if edits and (p, k) in edits:
            values[sid] = edits[(p, k)]

    def ok():
        _assert_mine()
        _clear_selection()
        n = C.ids_write(des, level_comp, rows, values)
        rep = (variants_fn or aruco_tags.apply_variants)(des) if rebuild else None
        return {"written": n, "variants": rep}
    _assert_mine()
    out = F().in_command(ok)
    _assert_mine()
    out["level"] = level_comp.name
    out["table"] = [{"copy": p, "key": k, "was": o, "now": values.get(sid, o)}
                    for p, k, o, sid in rows]
    return out


def _load_legacy(path):
    spec = importlib.util.spec_from_file_location("aruco_tags_legacy", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------- the dialog test

def _pump_until(cond, timeout_s=10.0):
    t0 = time.perf_counter()
    while not cond() and time.perf_counter() - t0 < timeout_s:
        adsk.doEvents()
        _assert_mine()
        time.sleep(0.005)
    return cond()


def _open_ids():
    import aruco_commands as C
    ui = adsk.core.Application.get().userInterface
    C._IdsState.cmd = None
    _assert_mine()
    _clear_selection()
    ui.commandDefinitions.itemById(C.IDS_CMD).execute()
    if not _pump_until(lambda: C._IdsState.cmd is not None):
        raise RuntimeError("the ArUco IDs dialog did not open")
    _pump_until(lambda: False, 0.3)
    return C._IdsState.cmd


def _preview(cmd):
    """One executePreview, timed (Fusion's own round trip included)."""
    import aruco_ids_preview as IP
    t0 = time.perf_counter()
    cmd.doExecutePreview()
    _pump_until(lambda: False, 0.05)
    return {"round_trip_ms": round((time.perf_counter() - t0) * 1000.0, 1),
            **dict(IP._st().last)}


def _ghost_vs_truth(des, T):
    """Every drawn copy's frame against the truth (world)."""
    import aruco_ids_preview as IP
    markers = M()
    occs = {o.fullPathName: o for o in des.rootComponent.allOccurrences}
    worst = [0.0, 0.0]
    for d in IP._st().drawn:
        occ = occs.get(d["path"])
        if occ is None:
            continue
        rec = T[(_comp_label(des, occ.component), d["key"])]
        tag = markers.TagDef.from_dict(rec, lambda m: None)
        Fw = _xf(occ.transform2, (tag.origin, tag.x_axis, tag.y_axis, tag.z_axis))
        pos, ang = _ferr(_cols(d["world"]), Fw)
        worst = [max(worst[0], pos * 10.0), max(worst[1], math.degrees(ang))]
    return {"worst_mm": round(worst[0], 6), "worst_deg": round(worst[1], 6),
            "copies": len(IP._st().drawn)}


def _state(des):
    return {"overrides": json.dumps(aruco_tags.read_overrides(des), sort_keys=True),
            "records": json.dumps([aruco_tags.read_tag_records(c) for c in des.allComponents],
                                  sort_keys=True),
            "timeline": des.timeline.count, "marker": des.timeline.markerPosition}


def dialog_test(des, out_dir, T):
    import aruco_commands as C
    import aruco_ids_preview as IP
    ui = adsk.core.Application.get().userInterface
    out = {}
    s0 = _state(des)
    g0 = IP.groups(des)
    cmd = _open_ids()
    inputs = cmd.commandInputs
    steps = [dict(_preview(cmd), step="open")]
    out["after_open"] = {"groups": IP.groups(des), "ghost": _ghost_vs_truth(des, T),
                         "status": inputs.itemById("status").text[:200]}
    # a spinner: the first copy that prints the modelled ids -> 40
    row = next(r for r in C._IdsState.rows if r[2] in (0, 10))
    inputs.itemById(row[3]).value = 40
    steps.append(dict(_preview(cmd), step=f"spinner {row[0]} -> 40"))
    drawn = [d for d in IP._st().drawn if d["path"].endswith(row[0]) and d["key"] == row[1]]
    out["edited_copy"] = drawn[:1]
    out["after_edit"] = _ghost_vs_truth(des, T)
    if out_dir:
        out["shot_preview"] = shot(des, os.path.join(out_dir, "preview_dialog_iso.png"))
        try:
            occ = next(o for o in des.rootComponent.allOccurrences
                       if o.fullPathName.endswith(row[0]))
            out["shot_preview_close"] = _closeup(
                des, os.path.join(out_dir, "preview_dialog_close.png"), occ.fullPathName, T)
        except StopIteration:
            pass
    C.ids_changed(inputs, "auto")
    steps.append(dict(_preview(cmd), step="Number duplicates"))
    out["groups_open"] = IP.groups(des)
    _assert_mine()
    ui.terminateActiveCommand()
    _pump_until(lambda: C._IdsState.cmd is None, 5.0)
    s1 = _state(des)
    out["cancel"] = {"groups": IP.groups(des), "groups_before": g0,
                     "nothing_written": s0 == s1,
                     "changed": [k for k in s0 if s0[k] != s1[k]],
                     "active": ui.activeCommand}
    # OK with one edit
    cmd = _open_ids()
    inputs = cmd.commandInputs
    _preview(cmd)
    row = next(r for r in C._IdsState.rows if r[2] in (0, 10))
    inputs.itemById(row[3]).value = 40
    steps.append(dict(_preview(cmd), step=f"(OK run) spinner {row[0]} -> 40"))
    t0 = time.perf_counter()
    _assert_mine()
    cmd.doExecute(True)
    _pump_until(lambda: C._IdsState.cmd is None, 30.0)
    out["ok"] = {"ms": round((time.perf_counter() - t0) * 1000.0, 1),
                 "groups": IP.groups(des), "result": C._IdsState.last,
                 "active": ui.activeCommand, "edited": row[0]}
    out["steps"] = steps
    out["preview_ms"] = [s.get("ms") for s in steps]
    out["records_after_ok_drift"] = drifts(des)
    return out


# ------------------------------------------------------------------- run

# steps whose value is kept in `out` under its own name (not repeated)
_QUIET_STEPS = ("build the rack", "truth (every node evaluated, rolled)", "measure before",
                "measure after", "measure after the dialog's OK",
                "the ArUco IDs dialog: preview, Cancel, OK")


def run(out_dir="", keep="0", legacy="", capture="1", dialog="1"):
    app = adsk.core.Application.get()
    before_doc = app.activeDocument
    import aruco_actions
    aruco_actions._quiet_or_raise()
    out = {"steps": []}
    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    aruco_actions._remember_active()
    _GUARD["doc"] = doc
    des = adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
    des.designType = adsk.fusion.DesignTypes.ParametricDesignType
    keep = str(keep) in ("1", "true", "yes")
    shots = out_dir if str(capture) not in ("0", "false", "no") else ""
    aborted = False

    def step(name, fn):
        t0 = time.perf_counter()
        val = None
        try:
            _assert_mine()
            val = fn()
            _assert_mine()
            entry = {"step": name, "ok": True,
                     "value": None if name in _QUIET_STEPS else _jsonable(val)}
        except NotMine:
            raise
        except Exception as exc:                   # noqa: BLE001
            entry = {"step": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:800],
                     "where": traceback.format_exc()[-1500:]}
        entry["s"] = round(time.perf_counter() - t0, 2)
        out["steps"].append(entry)
        return val
    try:
        fx = step("build the rack", lambda: build(des))
        out["placements"] = step("placements (transform2 vs composed vs real)",
                                 lambda: placements(des))
        step("ArUco Extrude on both marker parts", lambda: make_tags(fx))
        out["drift_after_create"] = drifts(des)
        step("upstream: both plates 150 x 46 -> 142 x 40 mm", lambda: upstream(fx))
        out["drift_after_upstream"] = drifts(des)
        out["records_before_ids"] = {f"{r.get('component')}|{r.get('key')}": {
            "origin_mm": [round(v * 10, 3) for v in r["origin"]],
            "x_axis": [round(v, 6) for v in r["x_axis"]],
            "size_mm": round(r["spec"]["marker_length_m"] * 1000, 3)}
            for r in aruco_tags.collect(des)["tags"]}
        legacy_mod = _load_legacy(legacy) if legacy else None
        fn = legacy_mod.apply_variants if legacy_mod else None
        out["nested_placement"] = (fx or {}).get("nested")
        step("IDs at Holder: Number duplicates + OK",
             lambda: ids_ok(des, _level(des, "Holder"), variants_fn=fn))
        step("IDs at Rack: Number duplicates + OK",
             lambda: ids_ok(des, _level(des, "Rack"), variants_fn=fn))
        step("IDs at the design: Number duplicates + OK",
             lambda: ids_ok(des, des.rootComponent, variants_fn=fn))
        T = step("truth (every node evaluated, rolled)", lambda: truth(des))
        out["truth"] = {f"{k[0]}|{k[1]}": {"origin_mm": [round(v * 10, 3) for v in r["origin"]],
                                           "x_axis": [round(v, 6) for v in r["x_axis"]],
                                           "size_mm": round(r["spec"]["marker_length_m"] * 1000, 3)}
                        for k, r in T.items()}
        if legacy_mod:
            out["before"] = step("measure before", lambda: measure(des, T))
            out["shots_before"] = step("captures before", lambda: captures(des, shots, "before", T))
            rep = step("fixed apply_variants (re-run ArUco IDs -> OK)",
                       lambda: F().in_command(aruco_tags.apply_variants, des))
            out["fix_report"] = rep
        out["after"] = step("measure after", lambda: measure(des, T))
        out["shots_after"] = step("captures after", lambda: captures(des, shots, "after", T))
        rep2 = step("apply_variants again (idempotent)",
                    lambda: F().in_command(aruco_tags.apply_variants, des))
        out["idempotent"] = bool(rep2) and not rep2.get("created") and not rep2.get("removed")
        out["drift_after_ids"] = drifts(des)

        # Zach's copy :3 (2026-10-08): its plate was moved after the
        # variants were built and the variant stayed behind. Move Plate 2
        # (Aruco Marker:2 is rigidly jointed to it) and look.
        def move_plate():
            p2 = next(o for o in des.rootComponent.occurrences if o.component.name == "Plate 2")
            m = p2.transform2
            m.translation = V(m.translation.x, m.translation.y - 10.0, m.translation.z)
            p2.transform2 = m
            adsk.doEvents()
            _assert_mine()
            occs = {o.fullPathName: o for o in des.rootComponent.allOccurrences}
            return {k: [round(v * 10, 3) for v in occs[k].transform2.translation.asArray()]
                    for k in occs if k.startswith(("Aruco Marker:2", "Aruco Marker · ", "Plate 2"))}
        out["moved_plate"] = step("move Plate 2 by -100 mm (its marker copy is jointed to it)",
                                  move_plate)
        out["after_move"] = step("measure after the move", lambda: measure(des, truth(des))["worst"])
        rep3 = step("apply_variants after the move (ArUco IDs -> OK)",
                    lambda: F().in_command(aruco_tags.apply_variants, des))
        out["move_report"] = {k: (rep3 or {}).get(k) for k in ("created", "kept", "rebuilt_because")}
        out["after_move_ok"] = step("measure after that OK", lambda: measure(des, truth(des))["worst"])
        if str(dialog) not in ("0", "false", "no"):
            # Zach's case through the dialog: the plates change again, so the
            # records are stale when it opens (activate evaluates the nodes)
            step("upstream again: plates 142 -> 146 mm wide (stale records)",
                 lambda: upstream(fx, w_mm=146.0, h_mm=40.0))
            out["drift_before_dialog"] = drifts(des)
            T2 = step("truth before the dialog", lambda: truth(des))
            out["dialog"] = step("the ArUco IDs dialog: preview, Cancel, OK",
                                 lambda: dialog_test(des, shots, T2))
            out["after_dialog"] = step("measure after the dialog's OK",
                                       lambda: measure(des, truth(des)))
        out["verdict"] = {
            "before_placement_mm": (out.get("before") or {}).get("worst", {}).get("placement_mm"),
            "after": (out.get("after") or {}).get("worst"),
            "after_dialog": (out.get("after_dialog") or {}).get("worst"),
            "idempotent": out["idempotent"],
            "steps_ok": all(s["ok"] for s in out["steps"])}
    except NotMine as exc:
        aborted = True
        out["aborted"] = str(exc)
    finally:
        _GUARD["doc"] = None
        if not aborted:
            _clear_selection()
            if not keep:
                try:
                    doc.close(False)
                except Exception:                  # noqa: BLE001
                    pass
            if before_doc is not None:
                try:
                    before_doc.activate()
                except Exception:                  # noqa: BLE001
                    pass
    return _jsonable(out)


# ---------------------------------------------------------- read-only

def inspect(document=""):
    """READ-ONLY, any open document (by name; not activated, nothing
    rolled, nothing written): every tag record against its live node, and
    every variant against where that copy's template pattern is NOW --
    both pattern bodies measured along the copy's record axes in root space.
    -> where his variants should be vs where they are."""
    import aruco_actions
    aruco_actions._quiet_or_raise()
    markers = M()
    app = adsk.core.Application.get()
    doc = app.activeDocument
    if document:
        doc = next((app.documents.item(i) for i in range(app.documents.count)
                    if app.documents.item(i).name == document), None)
        if doc is None:
            raise ValueError(f"no open document {document!r}")
    des = adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
    out = {"document": doc.name, "records": [], "copies": []}
    recs = {}
    for comp in des.allComponents:
        if aruco_tags.is_variant(comp):
            continue
        for rec in aruco_tags.read_tag_records(comp):
            recs[(comp.name, rec.get("key"))] = (comp, rec)
            body = aruco_tags.pattern_body(comp, rec)
            ent = {"component": comp.name, "key": rec.get("key"),
                   "drift": aruco_tags.record_drift(des, comp, rec),
                   "record_origin_mm": [round(v * 10, 3) for v in rec["origin"]],
                   "record_size_mm": round(rec["spec"]["marker_length_m"] * 1000, 3)}
            if body is not None:
                ext = aruco_tags._extents(body, aruco_tags._frame_of(rec))
                ent["live_pattern_in_record_frame_mm"] = [[round(a * 10, 3), round(b * 10, 3)]
                                                          for a, b in ext]
                ent["live_centre_offset_mm"] = [round((a + b) / 2 * 10, 3) for a, b in ext[:2]]
                want = aruco_tags._footprint_cm(markers.TagDef.from_dict(rec, lambda m: None).spec,
                                                aruco_tags._is_white(rec, body))
                ent["record_footprint_mm"] = [round(v * 10, 3) for v in want]
            out["records"].append(ent)
    occs = {o.fullPathName: o for o in des.rootComponent.allOccurrences}
    for (tpath, key), items in _variants_by_template(des).items():
        tocc = occs.get(tpath)
        if tocc is None:
            out["copies"].append({"copy": tpath, "error": "template copy not found"})
            continue
        comp, rec = recs.get((tocc.component.name, key), (None, None))
        if rec is None:
            continue
        frame = aruco_tags._frame_of(rec)
        Fw = _xf(tocc.transform2, frame)
        tbody = aruco_tags.pattern_body(comp, rec)
        te = aruco_tags._extents(tbody.createForAssemblyContext(tocc), Fw)
        for vocc, info in items:
            vcomp = vocc.component
            vrec = (aruco_tags.read_tag_records(vcomp) or [{}])[0]
            vbody = aruco_tags.pattern_body(vcomp, vrec)
            ve = aruco_tags._extents(vbody.createForAssemblyContext(vocc), Fw)
            ent = {"copy": tpath, "variant": vocc.fullPathName, "ids": info.get("spec", {}).get("ids"),
                   "visible": vocc.isVisible, "template_visible": tocc.isVisible,
                   "template_pattern_mm": [[round(a * 10, 3), round(b * 10, 3)] for a, b in te],
                   "variant_pattern_mm": [[round(a * 10, 3), round(b * 10, 3)] for a, b in ve],
                   "variant_minus_template_centre_mm": [
                       round(((ve[i][0] + ve[i][1]) - (te[i][0] + te[i][1])) / 2 * 10, 3)
                       for i in range(2)] + [round((ve[2][1] - te[2][1]) * 10, 3)],
                   "variant_minus_template_size_mm": [
                       round(((ve[i][1] - ve[i][0]) - (te[i][1] - te[i][0])) * 10, 3)
                       for i in range(2)],
                   "built_by_fixed_addin": info.get("build") == aruco_tags.VARIANT_BUILD}
            tname = (rec.get("fusion") or {}).get("tile_body")
            if tname:
                tb, vb = comp.bRepBodies.itemByName(tname), vcomp.bRepBodies.itemByName(tname)
                if tb is not None and vb is not None:
                    a = aruco_tags._extents(tb.createForAssemblyContext(tocc), Fw)
                    b = aruco_tags._extents(vb.createForAssemblyContext(vocc), Fw)
                    ent["tile_body_extents_diff_mm"] = [[round((b[i][0] - a[i][0]) * 10, 3),
                                                         round((b[i][1] - a[i][1]) * 10, 3)]
                                                        for i in range(3)]
            jo = _joint_origin_of(des, vocc)
            if jo is not None:
                d = aruco_tags._sub(jo, Fw[0])
                ent["joint_glyph_from_tag_mm"] = round(math.sqrt(aruco_tags._dot(d, d)) * 10, 1)
            ent["joints"] = joints_of(des, vocc)
            out["copies"].append(ent)
    tl = des.timeline
    out["timeline"] = {"count": tl.count, "marker": tl.markerPosition, "items": []}
    for i in range(max(0, tl.count - 25), tl.count):
        it = tl.item(i)
        try:
            ent = it.entity
            kind = ent.objectType.rsplit("::", 1)[-1] if ent is not None else None
        except Exception:                          # noqa: BLE001
            kind = "?"
        row = {"i": i, "name": it.name, "type": kind, "suppressed": it.isSuppressed,
               "group": it.isGroup}
        try:
            row["health"] = int(it.healthState)
        except Exception:                          # noqa: BLE001
            pass
        out["timeline"]["items"].append(row)
    return out


def joints_of(des, occ):
    """READ-ONLY: every joint / as-built joint / rigid group naming an
    occurrence's component, with what it holds it to."""
    out = []
    comps = [des.rootComponent] + [c for c in des.allComponents if c != des.rootComponent]
    for c in comps:
        for coll, kind in ((getattr(c, "asBuiltJoints", None), "as-built"),
                           (getattr(c, "joints", None), "joint")):
            if coll is None:
                continue
            for j in coll:
                try:
                    one, two = j.occurrenceOne, j.occurrenceTwo
                    names = [o.component.name if o is not None else None for o in (one, two)]
                    if occ.component.name not in names:
                        continue
                    ent = {"kind": kind, "in": c.name, "name": j.name,
                           "one": one.fullPathName if one else None,
                           "two": two.fullPathName if two else None,
                           "suppressed": bool(getattr(j, "isSuppressed", False))}
                    try:
                        ent["health"] = int(j.healthState)
                        ent["message"] = (j.errorOrWarningMessage or "")[:160]
                    except Exception:              # noqa: BLE001
                        pass
                    try:
                        ent["motion"] = j.jointMotion.objectType.rsplit("::", 1)[-1]
                    except Exception:              # noqa: BLE001
                        pass
                    try:
                        g = j.geometry
                        ent["geometry_origin_mm"] = (None if g is None else
                                                     [round(v * 10, 2) for v in
                                                      (g.origin.x, g.origin.y, g.origin.z)])
                    except Exception:              # noqa: BLE001
                        pass
                    out.append(ent)
                except Exception:                  # noqa: BLE001
                    continue
        try:
            for rg in c.rigidGroups:
                if any(o.component.name == occ.component.name for o in rg.occurrences):
                    out.append({"kind": "rigid group", "in": c.name, "name": rg.name,
                                "members": [o.fullPathName for o in rg.occurrences]})
        except Exception:                          # noqa: BLE001
            pass
    return out
