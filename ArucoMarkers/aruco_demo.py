"""Demo design for the prototype, built in a NEW unsaved document.

Zach's example made literal: one Tag part (a tile made with ArUco Extrude,
plus a hole added AFTER the tag), mounted on an Actuator; six Actuators in an
Arm chained by revolute joints; two Arms on a grounded Base. Every one of the
12 tags starts as the same marker — the defect this prototype exists to fix.
All lengths cm (Fusion API units).
"""
from __future__ import annotations

import math
import os

import adsk.core
import adsk.fusion

import aruco_tags

P = adsk.core.Point3D.create
V = adsk.core.Vector3D.create


def _mat(rz_deg=0.0, t=(0, 0, 0), cols=None):
    m = adsk.core.Matrix3D.create()
    if cols is not None:
        x, y, z = cols
        m.setWithCoordinateSystem(P(*t), V(*x), V(*y), V(*z))
        return m
    a = math.radians(rz_deg)
    m.setWithCoordinateSystem(P(*t), V(math.cos(a), math.sin(a), 0),
                              V(-math.sin(a), math.cos(a), 0), V(0, 0, 1))
    return m


def _box(comp, x0, y0, x1, y1, height, name, z_plane=None):
    sk = comp.sketches.add(z_plane or comp.xYConstructionPlane)
    sk.sketchCurves.sketchLines.addTwoPointRectangle(P(x0, y0, 0), P(x1, y1, 0))
    ext = comp.features.extrudeFeatures.addSimple(
        sk.profiles.item(0), adsk.core.ValueInput.createByReal(height),
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    ext.bodies.item(0).name = name
    return ext


def build(n_actuators=6, n_arms=2, dictionary="DICT_4X4_50"):
    app = adsk.core.Application.get()
    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    des = adsk.fusion.Design.cast(app.activeProduct)
    des.designType = adsk.fusion.DesignTypes.ParametricDesignType
    root = des.rootComponent
    grey = aruco_tags.appearance(des, "Plastic - Matte (Grey)") or \
        aruco_tags.appearance(des, "Paint - Enamel Glossy (Grey)")
    blue = aruco_tags.appearance(des, "Plastic - Matte (Blue)")

    # --- Tag part: a rectangle, ArUco-extruded, then a hole after the tag
    tag_occ = root.occurrences.addNewComponent(_mat())
    tag = tag_occ.component
    tag.name = "Tag"
    sk = tag.sketches.add(tag.xYConstructionPlane)
    sk.name = "tag outline"
    sk.sketchCurves.sketchLines.addCenterPointRectangle(P(0, 0, 0), P(2.0, 2.0, 0))
    rec = aruco_tags.aruco_extrude(sk.profiles.item(0), thickness_mm=3.0,
                                   dictionary=dictionary, marker_id=0, key="front")
    hole_sk = tag.sketches.add(tag.xYConstructionPlane)
    hole_sk.name = "mount hole"
    hole_sk.sketchCurves.sketchCircles.addByCenterRadius(P(-1.75, -1.75, 0), 0.12)
    tag.features.extrudeFeatures.addSimple(
        hole_sk.profiles.item(0), adsk.core.ValueInput.createByReal(0.3),
        adsk.fusion.FeatureOperations.CutFeatureOperation)

    # --- Actuator: housing + shaft, the tag on its +Y face, pinned to parent
    act_occ = root.occurrences.addNewComponent(_mat())
    act = act_occ.component
    act.name = "Actuator"
    housing = _box(act, -2.5, -2.5, 2.5, 2.5, 4.0, "housing")
    plane_in = act.constructionPlanes.createInput()
    plane_in.setByOffset(act.xYConstructionPlane, adsk.core.ValueInput.createByReal(4.0))
    top = act.constructionPlanes.add(plane_in)
    sk2 = act.sketches.add(top)
    sk2.sketchCurves.sketchCircles.addByCenterRadius(P(0, 0, 0), 1.0)
    shaft = act.features.extrudeFeatures.addSimple(
        sk2.profiles.item(0), adsk.core.ValueInput.createByReal(1.0),
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    shaft.bodies.item(0).name = "shaft"
    for b in act.bRepBodies:
        if grey:
            b.appearance = grey
    if blue:
        shaft.bodies.item(0).appearance = blue
    # tag frame: tag +Z -> actuator +Y, tag +X -> +X, centred on the face
    t_in = act.occurrences.addExistingComponent(
        tag, _mat(cols=((1, 0, 0), (0, 0, -1), (0, 1, 0)), t=(0, 2.5, 2.0)))
    t_in.isGroundToParent = True
    tag_occ.deleteMe()

    shaft_top = None
    for e in shaft.bodies.item(0).edges:
        g = e.geometry
        if isinstance(g, adsk.core.Circle3D) and abs(g.center.z - 5.0) < 1e-6:
            shaft_top = e
            break

    # --- Arm: actuators stacked, each revolute on the shaft below it
    # Built at the origin and never moved: a component created with an offset
    # placed the children added to it later at the wrong spot, and a later
    # transform2 write is an uncaptured move a recompute reverts. The base is
    # drawn under the arms instead.
    arm_occ = root.occurrences.addNewComponent(_mat())
    arm = arm_occ.component
    arm.name = "Arm"
    acts = []
    for k in range(n_actuators):
        acts.append(arm.occurrences.addExistingComponent(
            act, _mat(rz_deg=35.0 * k, t=(0, 0, 5.0 * k))))
    for k in range(1, n_actuators):
        edge = shaft_top.createForAssemblyContext(acts[k - 1])
        geo = adsk.fusion.JointGeometry.createByCurve(
            edge, adsk.fusion.JointKeyPointTypes.CenterKeyPoint)
        ji = arm.asBuiltJoints.createInput(acts[k], acts[k - 1], geo)
        ji.setAsRevoluteJointMotion(adsk.fusion.JointDirections.ZAxisJointDirection)
        j = arm.asBuiltJoints.add(ji)
        j.name = f"J{k}"
    act_occ.deleteMe()

    # --- Base + arms
    base_occ = root.occurrences.addNewComponent(_mat())
    base = base_occ.component
    base.name = "Base"
    under = base.constructionPlanes.createInput()
    under.setByOffset(base.xYConstructionPlane, adsk.core.ValueInput.createByReal(-1.0))
    _box(base, -5.0, -6, 5.0 + 10.0 * (n_arms - 1), 6, 1.0, "plate",
         z_plane=base.constructionPlanes.add(under))
    if grey:
        base.bRepBodies.item(0).appearance = grey
    base_occ.isGrounded = True
    arms = [arm_occ]
    for i in range(1, n_arms):
        arms.append(root.occurrences.addExistingComponent(arm, _mat(t=(10.0 * i, 0, 0))))
    # Each arm is grounded and its first actuator pinned to it (Ground to
    # Parent). The first build jointed the nested first actuator straight to
    # the base instead, and Fusion kept those two actuators where they were
    # when the arms moved -- the exporter then faithfully exported a broken
    # arm. A pin to the parent moves with the parent.
    for a in arms:
        a.isGrounded = True
    acts[0].isGroundToParent = True
    app.activeViewport.fit()
    poses = {o.fullPathName: [round(v, 3) for v in o.transform2.translation.asArray()]
             for o in arms}
    return {"document": doc.name, "tag_record": rec, "arm_poses_cm": poses,
            "occurrences": [o.fullPathName for o in root.allOccurrences],
            "summary": aruco_tags.summary(des)}


def capture(path, width=1600, height=1000, view=None):
    app = adsk.core.Application.get()
    vp = app.activeViewport
    if view == "iso":
        cam = vp.camera
        cam.viewOrientation = adsk.core.ViewOrientations.IsoTopRightViewOrientation
        cam.isFitView = True
        vp.camera = cam
    elif view == "front":
        cam = vp.camera
        cam.viewOrientation = adsk.core.ViewOrientations.FrontViewOrientation
        cam.isFitView = True
        vp.camera = cam
    vp.refresh()
    adsk.doEvents()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ok = vp.saveAsImageFile(path, width, height)
    return {"ok": bool(ok), "path": path}


def build_ui_fixture():
    """A new document to drive ArUco Tag by hand: a 40 mm square sketch
    (-> ArUco Extrude), a 120 x 70 x 8 mm plate (-> grid board on its top
    face) and a 100 x 80 x 4 mm plate (-> ChArUco board), side by side."""
    app = adsk.core.Application.get()
    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    des = adsk.fusion.Design.cast(app.activeProduct)
    des.designType = adsk.fusion.DesignTypes.ParametricDesignType
    root = des.rootComponent
    sk = root.sketches.add(root.xYConstructionPlane)
    sk.name = "draw a rectangle"
    sk.sketchCurves.sketchLines.addCenterPointRectangle(P(-9, 0, 0), P(-7, 2, 0))
    _box(root, -4, -3.5, 8, 3.5, 0.8, "grid plate")
    _box(root, 10, -4, 20, 4, 0.4, "charuco plate")
    vp = app.activeViewport
    cam = vp.camera
    cam.viewOrientation = adsk.core.ViewOrientations.TopViewOrientation
    cam.isFitView = True
    vp.camera = cam
    return {"document": doc.name}


def modes_fixture(out_dir):
    """Zach's GIM4305 case, three times over: a D-shaped disc (r 50 mm, flat
    chord, two holes). (1) tag its top face by clicking it -> the largest clear
    rectangle; (2) a rectangle sketched on the face -> INLAY into the disc;
    (3) the same rectangle -> a raised tile JOINED to the disc. Builds in a new
    document, captures each, returns measurements."""
    app = adsk.core.Application.get()
    results = {}
    for mode in ("face", "inlay", "join"):
        doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
        try:
            results[mode] = _one_mode(app, doc, mode, out_dir)
        except Exception as exc:                   # noqa: BLE001
            results[mode] = {"error": f"{type(exc).__name__}: {exc}"}
        finally:
            doc.close(False)
    return results


def _one_mode(app, doc, mode, out_dir):
    if True:
        des = adsk.fusion.Design.cast(app.activeProduct)
        des.designType = adsk.fusion.DesignTypes.ParametricDesignType
        root = des.rootComponent
        sk = root.sketches.add(root.xYConstructionPlane)
        arcs = sk.sketchCurves.sketchArcs
        lines = sk.sketchCurves.sketchLines
        a0 = math.asin(2.0 / 5.0)
        pl = P(-5.0 * math.cos(a0), 2.0, 0)
        pr = P(5.0 * math.cos(a0), 2.0, 0)
        arc = arcs.addByThreePoints(pl, P(0, -5.0, 0), pr)
        lines.addByTwoPoints(arc.endSketchPoint, arc.startSketchPoint)
        disc_prof = max(sk.profiles, key=lambda p: p.areaProperties().area)
        ext = root.features.extrudeFeatures.addSimple(
            disc_prof, adsk.core.ValueInput.createByReal(1.0),
            adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        disc = ext.bodies.item(0)
        disc.name = "disc"
        top = ext.endFaces.item(0)
        hs = root.sketches.add(top)
        for (hx, hy) in ((-3.5, -0.5), (-1.5, -3.0)):
            hs.sketchCurves.sketchCircles.addByCenterRadius(
                hs.modelToSketchSpace(P(hx, hy, 1.0)), 0.4)
        for prof in [p for p in hs.profiles if p.areaProperties().area < 1.0]:  # the holes only
            root.features.extrudeFeatures.addSimple(
                prof, adsk.core.ValueInput.createByReal(-1.0),
                adsk.fusion.FeatureOperations.CutFeatureOperation)
        disc = root.bRepBodies.itemByName("disc")   # the cuts replaced the body object
        top = max((f for f in disc.faces if isinstance(f.geometry, adsk.core.Plane)),
                  key=lambda f: f.pointOnFace.z)
        if mode == "face":
            rec = aruco_tags.add_tag(top, marker_id=0, key="top")
        else:
            rs = root.sketches.add(top)
            c = rs.modelToSketchSpace(P(1.0, -1.0, 1.0))
            rs.sketchCurves.sketchLines.addCenterPointRectangle(
                c, adsk.core.Point3D.create(c.x + 1.8, c.y + 1.8, 0))
            # the sketch on the face auto-projects its edges (holes included):
            # take the profile that is our 36 x 36 mm rectangle
            prof = min(rs.profiles, key=lambda p: abs(p.areaProperties().area - 12.96))
            rec = aruco_tags.aruco_extrude(prof, thickness_mm=2.0, tile_mode=mode,
                                           marker_id=0, key="top")
        vp = app.activeViewport
        cam = vp.camera
        cam.viewOrientation = adsk.core.ViewOrientations.TopViewOrientation
        cam.isFitView = True
        vp.camera = cam
        vp.refresh()
        adsk.doEvents()
        top_png = os.path.join(out_dir, f"mode_{mode}_top.png")
        vp.saveAsImageFile(top_png, 1400, 1000)
        cam = vp.camera
        cam.viewOrientation = adsk.core.ViewOrientations.IsoTopRightViewOrientation
        cam.isFitView = True
        vp.camera = cam
        vp.refresh()
        adsk.doEvents()
        vp.saveAsImageFile(os.path.join(out_dir, f"mode_{mode}_iso.png"), 1400, 1000)
        bodies = {b.name: {"volume_mm3": round(b.volume * 1000, 2)} for b in root.bRepBodies}
        bad = []
        for i in range(des.timeline.count):
            ent = des.timeline.item(i).entity
            h = getattr(ent, "healthState", None) if ent is not None else None
            if h not in (None, 0):
                bad.append(des.timeline.item(i).name)
        return {"spec": rec.get("spec"), "origin_cm": rec.get("origin"),
                "joint_origin_error": (rec.get("fusion") or {}).get("joint_origin_error"),
                "bodies": bodies, "unhealthy": bad, "top_png": top_png}
