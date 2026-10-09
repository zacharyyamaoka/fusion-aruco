"""Bridge test of the live ArUco Extrude feature, in a NEW unsaved document.

    python tools/aruco_bridge.py live_test --arg out_dir=<dir> [--arg keep=1]

Builds four plates and a free sketch, makes one ArUco Extrude in every mode
(white + black joined / new body, black only inlaid / raised, white only),
then: a parameter edit, an upstream edit (the plate gets thicker), Fusion's
own edit route with new values (APIFeatureEditCommand -> our edit command),
a structural edit (black only -> white + black: the node is rebuilt), the
export-time refresh, and resolve(). Each step records timeline health and
the bodies' z ranges. Closes its document unless keep=1, and always puts the
user's document back.

Joint Origins (2026-10-08): more tags cover every frame case -- grid and
ChArUco boards (OpenCV z INTO the board: a flipped Joint Origin), raised and
flush patterns, white only, free sketches with no body (G: black only, W:
white only), body faces, and a ROOT sketch over a disc in a CHILD component
placed with a rotated transform (Z, Zach's tag) plus a face of a body in that
component (K). `_jo_check` reads every Joint Origin back against the optical
frame -- the record's and the one the node evaluates to NOW -- after create,
after upstream edits (plates and the disc get thicker; no re-solve), after
the edits (Z goes white + black -> black only), and at the end:
out["jo_verdict"]. With out_dir, `capture_tags` renders every tag for
tools/verify_aruco_jo_pnp.py (OpenCV PnP vs the optical frame).

Extrude (2026-10-08): the colours of every ArUco body after create, and at
the end aruco_extrude_lab's GIM4305 case (a root sketch over a child
component's disc: found through the occurrence, built in the child) and the
colours through compute / edit / rebuild / undo: out["extrude_verdict"].
"""
from __future__ import annotations

import math
import os
import traceback

import adsk.core
import adsk.fusion

import aruco_demo
import aruco_feature
import aruco_tags

P = adsk.core.Point3D.create


def _top_face(body):
    best = None
    for f in body.faces:
        if not isinstance(f.geometry, adsk.core.Plane):
            continue
        ok, n = f.evaluator.getNormalAtPoint(f.pointOnFace)
        if n.z > 0.999 and (best is None or f.pointOnFace.z > best.pointOnFace.z):
            best = f
    return best


def _health(des):
    bad = []
    for i in range(des.timeline.count):
        it = des.timeline.item(i)
        try:
            if it.isGroup:
                continue                 # a group reads 5 (unknown): not a feature's health
            hs = int(it.healthState)
        except Exception:                          # noqa: BLE001
            hs = -1
        if hs != 0:
            bad.append({"i": i, "name": it.name, "health": hs,
                        "message": getattr(it, "errorOrWarningMessage", "")[:160]})
    return bad


def _timeline(des):
    out = []
    for i in range(des.timeline.count):
        it = des.timeline.item(i)
        ent = it.entity
        out.append(f"{it.name} [{ent.objectType.rsplit('::', 1)[-1] if ent else '?'}]")
    return out


def _bodies(comp):
    out = {}
    for b in comp.bRepBodies:
        bb = b.boundingBox
        out[b.name] = {"z_mm": [round(bb.minPoint.z * 10, 3), round(bb.maxPoint.z * 10, 3)],
                       "vol_mm3": round(b.volume * 1000, 3), "solid": bool(b.isSolid),
                       "lumps": b.lumps.count}
    return out


def _records(des):
    out = []
    for comp in des.allComponents:
        for r in aruco_tags.read_tag_records(comp):
            fz = r.get("fusion") or {}
            cf = aruco_feature.find_feature(des, r)
            out.append({"key": r.get("key"), "label": r.get("spec", {}).get("dictionary"),
                        "id": r.get("spec", {}).get("marker_id"),
                        "layout": r.get("spec", {}).get("layout"),
                        "marker_mm": round(r.get("spec", {}).get("marker_length_m", 0) * 1000, 3),
                        "origin_mm": [round(v * 10, 3) for v in r.get("origin", [])],
                        "live": cf is not None, "node": cf.name if cf else None,
                        "jo": fz.get("joint_origin_name"), "jo_err": fz.get("joint_origin_error"),
                        "live_error": fz.get("live_error")})
    return out


def _cf(des, key):
    """The node of the MODELLED tag `key` (never a copy's variant part: its
    record has the template's key and no node -- 2026-10-09)."""
    _comp, r = aruco_tags.find_record(des, key)
    return aruco_feature.find_feature(des, r) if r is not None else None


# ------------------------------------------- Joint Origin vs optical frame

def _occ_of(des, comp):
    """First occurrence of a non-root component (None for the root)."""
    if comp == des.rootComponent:
        return None
    occs = des.rootComponent.allOccurrencesByComponent(comp)
    return occs.item(0) if occs.count else None


def _to_world(occ, frame):
    """(o, x, y, z) in a component -> root, through one occurrence."""
    if occ is None:
        return [list(v) for v in frame]
    O, X, Y, Z = (aruco_tags._v(v) for v in occ.transform2.getAsCoordinateSystem())

    def vec(v):
        return [X[i] * v[0] + Y[i] * v[1] + Z[i] * v[2] for i in range(3)]
    o = vec(frame[0])
    return [[O[i] + o[i] for i in range(3)]] + [vec(v) for v in frame[1:]]


def _fresh_optical(des, comp, rec):
    """The optical frame the tag's node gives NOW (evaluated at its place in
    the timeline, nothing written), or None for a tag without a live node."""
    F, T = aruco_feature, aruco_tags
    cf = F.find_feature(des, rec)
    if cf is None:
        return None
    how = F._roll_to(cf)
    try:
        ev = F.evaluate(F.target_of(cf), F.settings_of(cf), F.numbers_of(cf))
    finally:
        F._roll_back(des, how)
    o, x, y, z = ev["tag"]
    tag = T.M().TagDef(key=rec.get("key"), component=rec.get("component", ""), spec=ev["spec"],
                       origin=list(o), x_axis=list(x), y_axis=list(y), z_axis=list(z))
    return T.optical_frame_cm(tag)


def _fresh_all(des):
    """_fresh_optical for every live tag in ONE sweep: the nodes from the
    last to the first (rolling the marker back is cheap), then one move to
    the end. Rolling to each tag and back recomputes everything after it
    every time -- quadratic in the number of tags (2026-10-08: minutes of a
    busy Fusion with ~50 tags). -> {(component name, key): optical frame}."""
    F, T = aruco_feature, aruco_tags
    nodes = []
    for comp in des.allComponents:
        for rec in T.template_records(comp):
            cf = F.find_feature(des, rec)
            if cf is not None:
                nodes.append((cf.timelineObject.index, comp.name, rec, cf))
    out = {}
    if not nodes:
        return out
    how = None
    try:
        for _i, cname, rec, cf in sorted(nodes, key=lambda t: -t[0]):
            r = F._roll_to(cf)
            how = how or r
            ev = F.evaluate(F.target_of(cf), F.settings_of(cf), F.numbers_of(cf))
            o, x, y, z = ev["tag"]
            tag = T.M().TagDef(key=rec.get("key"), component=rec.get("component", ""),
                               spec=ev["spec"], origin=list(o), x_axis=list(x), y_axis=list(y),
                               z_axis=list(z))
            out[(cname, rec.get("key"))] = T.optical_frame_cm(tag)
    finally:
        if how is not None:
            F._roll_back(des, how)
    return out


def _jo_check(des, fresh=True):
    """Every tag: its Joint Origin's read-back against its optical frame --
    the record's, and (fresh=True) the one its node evaluates to NOW, so a
    Joint Origin that did not follow an upstream change shows up before any
    re-solve -- in the component (native) and in the root (occurrence
    proxy). Nothing is re-solved: this is what an export would read now.
    A copy's variant part has no Joint Origin of its own (its OpenCV frame
    is its template's, through the copy): counted in "variants", not
    checked (2026-10-09: read as tags without one, they made worst = inf)."""
    T = aruco_tags
    # worst: Joint Origin vs the frame the node gives NOW (the record when
    # there is no node); worst_rec: vs the stored record, which only an edit
    # or refresh_live rewrites, so it may lag an upstream change
    rows, worst, worst_rec = [], [0.0, 0.0], [0.0, 0.0]
    variants = 0
    fresh_frames = _fresh_all(des) if fresh else {}
    for comp in des.allComponents:
        occ = _occ_of(des, comp)
        variants += len(T.read_tag_records(comp)) - len(T.template_records(comp))
        for rec in T.template_records(comp):
            fz = rec.get("fusion") or {}
            row = {"key": rec.get("key"), "component": comp.name,
                   "layout": (rec.get("spec") or {}).get("layout"), "mode": fz.get("mode"),
                   "raise_mm": round((rec.get("spec") or {}).get("raise_m", 0.0) * 1000, 3),
                   "recorded": fz.get("joint_origin_error"),
                   "anchor": fz.get("joint_origin_anchor")}
            try:
                tag = T.M().TagDef.from_dict(rec, lambda m: None)
                target = T.optical_frame_cm(tag)
                jo = T.joint_origin_of(des, comp, rec)
                row["exists"] = jo is not None
                if jo is None:
                    worst[0] = float("inf")
                    rows.append(row)
                    continue
                try:
                    row["health"] = int(jo.timelineObject.healthState)
                    row["timeline_index"] = jo.timelineObject.index
                except Exception:                  # noqa: BLE001
                    row["health"] = None
                pos, ang = T._frame_error(T._jo_frame(jo), target)
                row["err_mm"], row["err_deg"] = round(pos * 10.0, 6), round(math.degrees(ang), 6)
                d = T._sub(T._jo_frame(jo)[0], target[0])
                row["delta_along_optical_xyz_mm"] = [round(T._dot(d, target[i]) * 10.0, 4)
                                                     for i in (1, 2, 3)]
                worst_rec = [max(worst_rec[0], row["err_mm"]), max(worst_rec[1], row["err_deg"])]
                now = fresh_frames.get((comp.name, rec.get("key")))
                if now is not None:
                    fpos, fang = T._frame_error(T._jo_frame(jo), now)
                    row["fresh_err_mm"] = round(fpos * 10.0, 6)
                    row["fresh_err_deg"] = round(math.degrees(fang), 6)
                    d = T._sub(T._jo_frame(jo)[0], now[0])
                    row["fresh_delta_xyz_mm"] = [round(T._dot(d, now[i]) * 10.0, 4)
                                                 for i in (1, 2, 3)]
                    worst = [max(worst[0], row["fresh_err_mm"]),
                             max(worst[1], row["fresh_err_deg"])]
                else:
                    worst = [max(worst[0], row["err_mm"]), max(worst[1], row["err_deg"])]
                if occ is not None:
                    proxy = jo.createForAssemblyContext(occ)
                    wpos, wang = T._frame_error(T._jo_frame(proxy), _to_world(occ, target))
                    row["world_err_mm"] = round(wpos * 10.0, 6)
                    row["world_err_deg"] = round(math.degrees(wang), 6)
                    worst = [max(worst[0], row["world_err_mm"]),
                             max(worst[1], row["world_err_deg"])]
            except Exception as exc:               # noqa: BLE001
                row["error"] = f"{type(exc).__name__}: {exc}"[:300]
                worst[0] = float("inf")
            rows.append(row)
    return {"worst_mm": worst[0], "worst_deg": worst[1],
            "worst_record_mm": worst_rec[0], "worst_record_deg": worst_rec[1], "tags": rows,
            "variants": variants,
            "ok": bool(rows) and worst[0] < 1e-3 and worst[1] < 1e-3,
            "record_ok": bool(rows) and worst_rec[0] < 1e-3 and worst_rec[1] < 1e-3}


_OVERLAYS = ("isJointsFolderLightBulbOn", "isJointOriginsFolderLightBulbOn",
             "isSketchFolderLightBulbOn", "isConstructionFolderLightBulbOn",
             "isOriginFolderLightBulbOn")


def capture_tags(des, out_dir, label, fov_deg=30.0, width=1600, height=1200, tilt_deg=25.0):
    """One exact perspective render per tag, for the OpenCV PnP check
    (tools/verify_aruco_jo_pnp.py). The camera looks at the pattern centre
    from `tilt_deg` off its normal, the pattern filling ~70 % of the height.
    Joint / Joint Origin glyphs, sketches, construction and origin folders
    are hidden for the shot (a glyph on a marker moved corners 13 px) and
    the visual style is plain shaded (no edge lines); all put back after.
    -> list of capture records (also written to <out_dir>/<label>.json)."""
    import json
    T = aruco_tags
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
    style = vp.visualStyle
    out = []
    try:
        vp.visualStyle = adsk.core.VisualStyles.ShadedVisualStyle
        for comp in des.allComponents:
            occ = _occ_of(des, comp)
            for rec in T.read_tag_records(comp):
                tag = T.M().TagDef.from_dict(rec, lambda m: None)
                spec = tag.spec
                o, x, y, z = _to_world(occ, (tag.origin, tag.x_axis, tag.y_axis, tag.z_axis))
                opt = _to_world(occ, T.optical_frame_cm(tag))
                fz = rec.get("fusion") or {}
                jo = T.joint_origin_of(des, comp, rec)
                jo_w = None
                if jo is not None:
                    jo_w = T._jo_frame(jo.createForAssemblyContext(occ) if occ else jo)
                ext = max(spec.extent_m) * 100.0
                dist = ext / (0.7 * 2.0 * math.tan(math.radians(fov_deg) / 2.0))
                t = math.radians(tilt_deg)
                c = [o[i] + z[i] * spec.raise_m * 100.0 for i in range(3)]
                eye = [c[i] + dist * (math.cos(t) * z[i] - math.sin(t) * y[i]) for i in range(3)]
                # set twice: right after a fit-view shot the first set is
                # overridden (seen: the first render came out zoomed out)
                for _ in range(2):
                    cam = vp.camera
                    cam.cameraType = adsk.core.CameraTypes.PerspectiveCameraType
                    cam.isFitView = False
                    cam.isSmoothTransition = False
                    cam.eye = adsk.core.Point3D.create(*eye)
                    cam.target = adsk.core.Point3D.create(*c)
                    cam.upVector = adsk.core.Vector3D.create(*y)
                    cam.perspectiveAngle = math.radians(fov_deg)
                    vp.camera = cam
                    vp.refresh()
                    adsk.doEvents()
                name = f"{label}_{rec.get('key')}.png"
                ok = vp.saveAsImageFile(os.path.join(out_dir, name), int(width), int(height))
                got = vp.camera
                out.append({"key": rec.get("key"), "file": name, "ok": bool(ok),
                            "width": int(width), "height": int(height),
                            "eye_cm": T._v(got.eye), "target_cm": T._v(got.target),
                            "up": T._v(got.upVector),
                            "perspective_angle_rad": got.perspectiveAngle,
                            "spec": spec.to_dict(), "mode": fz.get("mode"),
                            "optical_world_cm": opt, "jo_world_cm": jo_w,
                            "component": comp.name})
    finally:
        try:
            vp.visualStyle = style
        except Exception:                          # noqa: BLE001
            pass
        for comp, attr, val in saved:
            try:
                setattr(comp, attr, val)
            except Exception:                      # noqa: BLE001
                pass
    with open(os.path.join(out_dir, f"{label}.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    return out


def run(out_dir="", keep="0", capture="1", extended="1"):
    """The scenario below, then (extended=1) aruco_heights_lab.heights_test
    in a SECOND new document -- Offset / Height combinations, the follow
    expression, pins and holes, the old-parameter migration, the group
    fallback, Origin turns -- merged into one report. Two documents, not
    one: _jo_check rolls the timeline to every tag and back, and with all
    the tags in one timeline that is quadratic (2026-10-08: one combined
    document kept Fusion busy for many minutes)."""
    out = _run_main(out_dir, keep, capture)
    if str(extended) in ("0", "false", "no") or out.get("aborted") or out.get("error"):
        return out
    import importlib
    import aruco_heights_lab as hl
    importlib.reload(hl)
    media = os.path.join(out_dir, "heights_pins") if (out_dir and str(capture) not in
                                                       ("0", "false", "no")) else ""
    ext = hl.heights_test(out_dir=media, keep=keep)
    if ext.get("error"):
        out["extended_error"] = ext.get("error")
        out["extrude_verdict"] = "FAIL"
        return out
    out["steps"] += [dict(s_, step="[heights/pins] " + s_["step"]) for s_ in ext.get("steps", [])]
    out["jo_heights_pins"] = ext.get("jo")
    out["extended_aborted"] = ext.get("aborted")
    jo = ext.get("jo") or {}
    out["jo_verdict"] = ("PASS" if out.get("jo_verdict") == "PASS" and jo.get("ok")
                         and jo.get("record_ok") else "FAIL")
    out["extrude_verdict"] = ("PASS" if out.get("extrude_verdict") == "PASS"
                              and ext.get("verdict") == "PASS" else "FAIL")
    return out


def _run_main(out_dir="", keep="0", capture="1"):
    app = adsk.core.Application.get()
    before = app.activeDocument
    import aruco_actions
    aruco_actions._quiet_or_raise()
    out = {"steps": [], "registered": aruco_feature.is_registered()}
    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    aruco_actions._remember_active()
    des = adsk.fusion.Design.cast(app.activeProduct)
    des.designType = adsk.fusion.DesignTypes.ParametricDesignType
    root = des.rootComponent
    keep = str(keep) in ("1", "true", "yes")
    do_capture = str(capture) not in ("0", "false", "no")
    st = aruco_feature._st()

    def shot(name):
        if do_capture and out_dir:
            aruco_demo.capture(os.path.join(out_dir, f"{name}.png"), view="iso")

    import aruco_extrude_lab as _lab

    def step(name, fn):
        n0 = getattr(st, "compute_n", 0)
        # the test's document must still be the active one (the user may
        # click his own while a step pumps events): stop there, touch nothing
        _lab.guard(des)
        try:
            val = fn()
            entry = {"step": name, "ok": True, "value": val}
        except Exception as exc:                   # noqa: BLE001
            entry = {"step": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:600],
                     "where": traceback.format_exc()[-700:]}
        try:
            _lab.guard(des)
        except _lab.Abort as exc:
            entry.update(ok=False, aborted=str(exc))
            out["steps"].append(entry)
            raise
        entry["computes"], ran = aruco_feature.computes_since(n0)
        entry["compute_errors"] = [c.get("error", "")[-300:] for c in ran
                                   if c.get("error")]
        entry["unhealthy"] = _health(des)
        out["steps"].append(entry)
        return entry

    S = aruco_feature.default_settings
    N = aruco_feature.default_numbers
    try:
        a = aruco_demo._box(root, -12, -3, -4, 3, 0.8, "plate A")       # 80 x 60 x 8
        b = aruco_demo._box(root, -2, -2, 4, 2, 0.6, "plate B")         # 60 x 40 x 6
        c = aruco_demo._box(root, 6, -2.5, 11, 2.5, 0.5, "plate C")     # 50 x 50 x 5
        d = aruco_demo._box(root, 13, -2, 17, 2, 0.5, "plate D")        # 40 x 40 x 5
        # a 30 x 20 mm rectangle sketched ON plate B's top face
        skb = root.sketches.add(_top_face(b.bodies.item(0)))
        skb.name = "rect on B"
        cb = skb.modelToSketchSpace(P(1.0, 0.0, 0.6))
        skb.sketchCurves.sketchLines.addCenterPointRectangle(cb, P(cb.x + 1.5, cb.y + 1.0, cb.z))
        # a free 40 x 40 mm rectangle above everything (no body under it)
        pl = root.constructionPlanes.createInput()
        pl.setByOffset(root.xYConstructionPlane, adsk.core.ValueInput.createByString("20 mm"))
        plane = root.constructionPlanes.add(pl)
        skf = root.sketches.add(plane)
        skf.name = "free rect"
        skf.sketchCurves.sketchLines.addCenterPointRectangle(P(0, 8, 0), P(2, 10, 0))
        # two more free rectangles on that plane: 50 x 30 and 30 x 30 mm
        skg = root.sketches.add(plane)
        skg.name = "free rect g"
        skg.sketchCurves.sketchLines.addCenterPointRectangle(P(8, 8, 0), P(10.5, 9.5, 0))
        skw = root.sketches.add(plane)
        skw.name = "free rect w"
        skw.sketchCurves.sketchLines.addCenterPointRectangle(P(16, 8, 0), P(17.5, 9.5, 0))
        e = aruco_demo._box(root, -12, 5, -4, 12, 0.4, "plate E")       # 80 x 70 x 4
        white = aruco_tags.appearance(des, aruco_tags.WHITE)
        black = aruco_tags.appearance(des, aruco_tags.BLACK)
        for ext, colour in ((a, white), (b, white), (c, black), (d, white), (e, white)):
            if colour:
                ext.bodies.item(0).appearance = colour       # contrast for the PnP renders
        # Zach's case (2026-10-08): a disc in a CHILD component, placed with a
        # non-identity transform, and a ROOT sketch on its top face; plus a
        # box in that component tagged on its face (record in the child's space)
        m = adsk.core.Matrix3D.create()
        m.setToRotation(math.radians(30.0), adsk.core.Vector3D.create(0, 0, 1), P(0, 0, 0))
        m.translation = adsk.core.Vector3D.create(0.0, -12.0, 0.2)
        occ = root.occurrences.addNewComponent(m)
        c1 = occ.component
        c1.name = "Component1"
        skd = c1.sketches.add(c1.xYConstructionPlane)
        circles = skd.sketchCurves.sketchCircles
        for cx, cy, r in ((0, 0, 2.65), (2.0, 0, 0.15), (-1.0, 1.7, 0.125)):
            circles.addByCenterRadius(P(cx, cy, 0), r)
        disc = c1.features.extrudeFeatures.addSimple(
            max((skd.profiles.item(i) for i in range(skd.profiles.count)),
                key=lambda p: p.areaProperties().area),
            adsk.core.ValueInput.createByReal(0.6),
            adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        disc.bodies.item(0).name = "disc"
        kbox = aruco_demo._box(c1, 3.5, -1, 5.5, 1, 0.5, "box k")
        if white:
            kbox.bodies.item(0).appearance = white

        def proxy_top(body_name):
            for i in range(occ.bRepBodies.count):
                bp = occ.bRepBodies.item(i)
                if bp.name == body_name:
                    return _top_face(bp)
            return None
        skz = root.sketches.add(proxy_top("disc"))
        skz.name = "on the disc (root)"
        for cx, cy, r in ((0, 0, 2.65), (2.0, 0, 0.15), (-1.0, 1.7, 0.125)):
            pw = P(cx, cy, 0.6)
            pw.transformBy(m)
            skz.sketchCurves.sketchCircles.addByCenterRadius(skz.modelToSketchSpace(pw), r)

        def smallest_profile(sk):
            return min((sk.profiles.item(i) for i in range(sk.profiles.count)),
                       key=lambda p: p.areaProperties().area)

        def make(target, **kw):
            s = S()
            n = N(kw.get("mode", "both"))
            # like the dialog: the black offset follows the white top unless given
            s["black_follows"] = "black_offset" not in kw
            for k, v in kw.items():
                (n if k in n else s)[k] = v
            rec = aruco_feature.in_command(aruco_feature.create, target, s, n)
            return {"key": rec["key"], "spec": rec["spec"].get("dictionary") + " #"
                    + str(rec["spec"].get("marker_id")), "layout": rec["spec"].get("layout"),
                    "live": bool((rec.get("fusion") or {}).get("custom_feature")),
                    "live_error": (rec.get("fusion") or {}).get("live_error")}

        step("A: face, white + black joined, grid auto",
             lambda: make(_top_face(a.bodies.item(0)), mode="both", op="join",
                          layout="grid", key="a", white_height=2.0))
        step("B: sketch on B, black only inlaid",
             lambda: make(smallest_profile(skb), mode="black", key="b", marker_id=10))
        step("C: face, white only raised 0.4",
             lambda: make(_top_face(c.bodies.item(0)), mode="white", key="c",
                          white_offset=0.0, white_height=0.4, marker_id=20))
        step("D: face, black raised 0.6 (sits on top)",
             lambda: make(_top_face(d.bodies.item(0)), mode="black", key="d",
                          black_offset=0.0, black_height=0.6, marker_id=30))
        step("F: free sketch, white + black new body, charuco",
             lambda: make(smallest_profile(skf), mode="both", op="new", layout="charuco",
                          key="f", white_height=3.0, marker_id=40))
        step("G: free sketch, black only, grid (no body, no tile)",
             lambda: make(smallest_profile(skg), mode="black", layout="grid", key="g",
                          marker_id=0, dictionary="DICT_5X5_100"))
        step("H: face, charuco, black raised 0.6",
             lambda: make(_top_face(e.bodies.item(0)), mode="black", layout="charuco", key="h",
                          black_offset=0.0, black_height=0.6, marker_id=0,
                          dictionary="DICT_6X6_250"))
        step("W: free sketch, white only (no body)",
             lambda: make(smallest_profile(skw), mode="white", key="w", marker_id=45))
        step("Z: root sketch on a child component's disc, white + black new body "
             "(Zach's tag: single #1 31.5 mm, white 2 mm, flush)",
             lambda: make(max((skz.profiles.item(i) for i in range(skz.profiles.count)),
                              key=lambda p: p.areaProperties().area),
                          mode="both", op="new", key="z", marker_id=1, size=31.5,
                          white_height=2.0))
        step("K: face of a body in the child component, black only",
             lambda: make(proxy_top("box k"), mode="black", key="k", marker_id=5))
        import aruco_extrude_lab
        step("colours after create: every ArUco body (A..K) black / white",
             lambda: aruco_extrude_lab.assert_colours(des))
        out["after_create"] = {"timeline": _timeline(des), "records": _records(des),
                               "bodies": _bodies(root)}
        out["jo_after_create"] = _jo_check(des)
        shot("01_created")
        if do_capture and out_dir:
            step("PnP renders after create", lambda: len(capture_tags(des, out_dir, "pnp_created")))

        def param_edit():
            cf = _cf(des, "b")
            p = aruco_feature._param(cf, "marker_id")
            v0 = _bodies(root).get("aruco cells b", {}).get("vol_mm3")
            p.value = 11.0
            return {"vol_before": v0, "vol_after": _bodies(root).get("aruco cells b", {}).get("vol_mm3"),
                    "node": cf.name}
        step("parameter: b marker_id 10 -> 11", param_edit)

        def upstream():
            z0 = _bodies(root).get("aruco cells a", {}).get("z_mm")
            a.extentOne.distance.expression = "12 mm"
            b.extentOne.distance.expression = "9 mm"
            return {"a_cells_z_before": z0, "a_cells_z_after": _bodies(root).get("aruco cells a", {}).get("z_mm"),
                    "b_cells_z_after": _bodies(root).get("aruco cells b", {}).get("z_mm")}
        step("upstream: plate A 8 -> 12 mm, plate B 6 -> 9 mm", upstream)
        shot("02_param_upstream")
        # no re-solve yet: does each Joint Origin FOLLOW its tag by itself?
        out["jo_after_param_upstream"] = _jo_check(des)

        def disc_thicker():
            disc.extentOne.distance.expression = "8 mm"
            return _jo_check(des)
        step("upstream: the child component's disc 6 -> 8 mm (no re-solve)", disc_thicker)

        step("edit route: f -> DICT_5X5_100 #7 (APIFeatureEditCommand)",
             lambda: aruco_feature.scripted_edit(_cf(des, "f"),
                                                 settings={"dictionary": "DICT_5X5_100"},
                                                 numbers={"marker_id": 7}))
        step("structural edit: b black only -> white + black (rebuild)",
             lambda: aruco_feature.scripted_edit(_cf(des, "b"),
                                                 settings={"mode": "both", "op": "join"},
                                                 numbers={"white_height": 1.5}))
        step("structural edit: z white + black -> black only (rebuild, no tile left)",
             lambda: aruco_feature.scripted_edit(_cf(des, "z"), settings={"mode": "black"}))
        out["jo_after_edits"] = _jo_check(des)

        def as_old_addin():
            # a Joint Origin made by the add-in before 2026-10-08 has no
            # anchor in its record: refresh_live must re-solve it once
            recs = aruco_tags.read_tag_records(root)
            for r in recs:
                if r.get("key") == "d":
                    (r.get("fusion") or {}).pop("joint_origin_anchor", None)
            aruco_tags.write_tag_records(root, recs)
            return "d's anchor removed"
        step("migration: d looks made by the old add-in", as_old_addin)
        step("refresh_live (export does this)", lambda: aruco_feature.refresh_live(des))

        def migrated():
            rec = next(r for r in aruco_tags.read_tag_records(root) if r.get("key") == "d")
            anchor = (rec.get("fusion") or {}).get("joint_origin_anchor")
            if not anchor:
                raise AssertionError("refresh_live left d's old Joint Origin alone")
            return anchor
        step("migration: refresh_live re-solved d", migrated)

        def resolve():
            res = aruco_tags.resolve_live(des)
            return {"instances": len(res.instances),
                    "ids": sorted(i for inst in res.instances for i in inst.spec.ids),
                    "warnings": res.warnings[:6]}
        step("resolve", resolve)
        out["final"] = {"timeline": _timeline(des), "records": _records(des),
                        "bodies": _bodies(root)}
        out["jo_final"] = _jo_check(des)
        checks = [out[k] for k in ("jo_after_create", "jo_after_param_upstream",
                                    "jo_after_edits", "jo_final")]
        out["jo_verdict"] = "PASS" if (all(c["ok"] for c in checks)
                                       and out["jo_after_create"]["record_ok"]
                                       and out["jo_final"]["record_ok"]) else "FAIL"
        shot("03_final")
        if do_capture and out_dir:
            step("PnP renders final", lambda: len(capture_tags(des, out_dir, "pnp_final")))
            # near-frontal: a raised layer's side walls barely show, so the
            # detected corners sit on the TOP of the relief (raise_m)
            step("PnP renders final, 8 deg tilt",
                 lambda: len(capture_tags(des, out_dir, "pnp_final_t8", tilt_deg=8.0)))
        # Extrude (2026-10-08), after the Joint Origin verdict so it judges
        # only the tags above: Zach's GIM4305 structure (a ROOT sketch, holes
        # projected, construction diagonals, on a tilted disc in a CHILD
        # component; and one on an offset plane), then the colours through
        # every stage. Each step raises when wrong.
        import aruco_extrude_lab as lab
        fx = lab.gim_fixture(root, offset=(0.0, -30.0, 0.0))
        lab.cross_case(des, fx, step)
        lab.colour_stages(des, step, dy=25.0)
        step("colours: every ArUco body at the end", lambda: lab.assert_colours(des))
        out["extrude_verdict"] = ("PASS" if all(s_["ok"] for s_ in out["steps"]
                                                if not s_["step"].startswith("PnP"))
                                  else "FAIL")
    except Exception as exc:                       # noqa: BLE001
        # an Abort (the user switched documents) or a crash in the set-up
        out["aborted" if type(exc).__name__ == "Abort" else "error"] = (
            f"{type(exc).__name__}: {exc}"[:400])
        out["where"] = traceback.format_exc()[-900:]
        out["jo_verdict"] = out["extrude_verdict"] = "FAIL"
    finally:
        out["compute_tail"] = st.compute_log[-4:]
        import aruco_extrude_lab as _lab2
        _lab2.finish_doc(doc, before, keep=keep)
    return out


def jo_probe(keep="0"):
    """How Fusion composes a Joint Origin's flip, angle and offsets: one box,
    one planar-face geometry, every combination read back. New document;
    closed after unless keep=1; the user's document is put back."""
    app = adsk.core.Application.get()
    before = app.activeDocument
    import aruco_actions
    aruco_actions._quiet_or_raise()
    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    aruco_actions._remember_active()
    des = adsk.fusion.Design.cast(app.activeProduct)
    des.designType = adsk.fusion.DesignTypes.ParametricDesignType
    root = des.rootComponent
    out = {"rows": []}
    R = lambda v: [round(c, 5) for c in (v.x, v.y, v.z)]
    try:
        ext = aruco_demo._box(root, -2, -1.5, 2, 1.5, 0.5, "box")          # 40 x 30 x 5 mm
        face = _top_face(ext.bodies.item(0))
        edges = sorted((e for e in face.edges if isinstance(e.geometry, adsk.core.Line3D)),
                       key=lambda e: -e.length)
        kp = adsk.fusion.JointKeyPointTypes
        geo = adsk.fusion.JointGeometry.createByPlanarFace(face, edges[0], kp.MiddleKeyPoint)
        out["edge"] = [R(edges[0].startVertex.geometry), R(edges[0].endVertex.geometry)]
        VI = adsk.core.ValueInput.createByReal
        import math as _m
        for flip in (False, True):
            for angle, ox, oy, oz in ((0, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1),
                                      (90, 0, 0, 0), (90, 1, 0, 0), (90, 0, 1, 0), (90, 0, 0, 1),
                                      (30, 1, 2, 0.5)):
                joi = root.jointOrigins.createInput(geo)
                joi.angle, joi.offsetX, joi.offsetY, joi.offsetZ = (VI(_m.radians(angle)), VI(ox),
                                                                    VI(oy), VI(oz))
                joi.isFlipped = flip
                jo = root.jointOrigins.add(joi)
                o, x, y, z = jo.transform.getAsCoordinateSystem()
                row = {"flip": flip, "angle": angle, "off": [ox, oy, oz],
                       "o": R(o), "x": R(x), "y": R(y), "z": R(z)}
                try:
                    g = jo.geometry
                    row["geo_o"] = R(g.origin)
                    row["geo_axes"] = [R(g.primaryAxisVector), R(g.secondaryAxisVector),
                                       R(g.thirdAxisVector)]
                except Exception as exc:           # noqa: BLE001
                    row["geo_error"] = str(exc)[:100]
                out["rows"].append(row)
                jo.deleteMe()
    finally:
        if str(keep) not in ("1", "true", "yes"):
            try:
                doc.close(False)
            except Exception:                      # noqa: BLE001
                pass
        if before is not None:
            try:
                before.activate()
            except Exception:                      # noqa: BLE001
                pass
    return out


def jo_probe_profile(keep="0"):
    """Joint Origins on a sketch PROFILE (centre keypoint / a line's
    midpoint), and one made with the timeline rolled back before a later
    feature: read back before and after that feature moves geometry."""
    app = adsk.core.Application.get()
    before = app.activeDocument
    import aruco_actions
    aruco_actions._quiet_or_raise()
    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    aruco_actions._remember_active()
    des = adsk.fusion.Design.cast(app.activeProduct)
    des.designType = adsk.fusion.DesignTypes.ParametricDesignType
    root = des.rootComponent
    R = lambda v: [round(c, 5) for c in (v.x, v.y, v.z)]
    kp = adsk.fusion.JointKeyPointTypes
    out = {}
    try:
        sk = root.sketches.add(root.xZConstructionPlane)
        sk.sketchCurves.sketchLines.addTwoPointRectangle(P(1, 1, 0), P(5, 3, 0))
        prof = sk.profiles.item(0)
        out["sketch_x"] = R(sk.xDirection)
        out["sketch_y"] = R(sk.yDirection)
        line = sk.sketchCurves.sketchLines.item(0)
        for label, fn in (("centre", lambda: adsk.fusion.JointGeometry.createByProfile(
                              prof, None, kp.CenterKeyPoint)),
                          ("line_mid", lambda: adsk.fusion.JointGeometry.createByProfile(
                              prof, line, kp.MiddleKeyPoint))):
            try:
                geo = fn()
                jo = root.jointOrigins.add(root.jointOrigins.createInput(geo))
                o, x, y, z = jo.transform.getAsCoordinateSystem()
                out[label] = {"o": R(o), "x": R(x), "y": R(y), "z": R(z)}
                jo.deleteMe()
            except Exception as exc:               # noqa: BLE001
                out[label] = f"{type(exc).__name__}: {exc}"[:200]
        # a JO on the extruded top face, made BEFORE a later combine that
        # merges a box onto that face (rolled timeline), vs one made after
        ext = root.features.extrudeFeatures.addSimple(
            prof, adsk.core.ValueInput.createByReal(0.5),
            adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        body = ext.bodies.item(0)
        top = max((f for f in body.faces if isinstance(f.geometry, adsk.core.Plane)),
                  key=lambda f: f.pointOnFace.y)
        tbm = adsk.fusion.TemporaryBRepManager.get()
        bf = root.features.baseFeatures.add()
        bf.startEdit()
        box = tbm.createBox(adsk.core.OrientedBoundingBox3D.create(
            P(3, -0.1 - 0.5, -2), adsk.core.Vector3D.create(1, 0, 0),
            adsk.core.Vector3D.create(0, 0, 1), 2, 1, 0.2))
        root.bRepBodies.add(box, bf)
        bf.finishEdit()
        tl = des.timeline
        out["timeline_before"] = [tl.item(i).name for i in range(tl.count)]
        bf.timelineObject.rollTo(True)
        jo_b = root.jointOrigins.add(root.jointOrigins.createInput(
            adsk.fusion.JointGeometry.createByPlanarFace(top, None, kp.CenterKeyPoint)))
        o = jo_b.transform.getAsCoordinateSystem()[0]
        out["jo_before_rolled"] = R(o)
        tl.moveToEnd()
        out["timeline_after"] = [tl.item(i).name for i in range(tl.count)]
        out["jo_before_at_end"] = R(jo_b.transform.getAsCoordinateSystem()[0])
        ext.extentOne.distance.expression = "8 mm"
        out["jo_before_after_upstream"] = R(jo_b.transform.getAsCoordinateSystem()[0])
        out["jo_before_health"] = int(jo_b.timelineObject.healthState)
    finally:
        if str(keep) not in ("1", "true", "yes"):
            try:
                doc.close(False)
            except Exception:                      # noqa: BLE001
                pass
        if before is not None:
            try:
                before.activate()
            except Exception:                      # noqa: BLE001
                pass
    return out
