"""One-shot API probe: what Fusion actually does with tags, copies, overrides.

Runs entirely in a NEW unsaved document it creates. Every step is wrapped so
one failure costs one fact, never the rest; the result is a list of
{step, ok, value|error}. Answers the questions the design hangs on:

  * TBM union of boxes that touch only at a corner -> one solid?
  * base feature add + updateBody -> does a downstream Combine survive?
  * per-path visibility: can Arm:1+Actuator:2+Tag:1 be hidden alone?
  * where do occurrence attributes live (native vs proxy)?
  * addNewComponentCopy / as-built rigid joints available?
"""
from __future__ import annotations

import json
import traceback

import adsk.core
import adsk.fusion

import aruco_core


def run(close: bool = True) -> list:
    app = adsk.core.Application.get()
    log = []

    def step(name, fn):
        try:
            value = fn()
            log.append({"step": name, "ok": True, "value": value})
            return value
        except Exception as exc:                   # noqa: BLE001
            log.append({"step": name, "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                        "where": traceback.format_exc().splitlines()[-3:]})
            return None

    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    design = adsk.fusion.Design.cast(app.activeProduct)
    design.designType = adsk.fusion.DesignTypes.ParametricDesignType
    root = design.rootComponent
    step("design_intent", lambda: str(getattr(design, "designIntent", "n/a")))
    I = adsk.core.Matrix3D.create

    tag_occ = root.occurrences.addNewComponent(I())
    tag = tag_occ.component
    tag.name = "Tag"

    def tile():
        sk = tag.sketches.add(tag.xYConstructionPlane)
        sk.sketchCurves.sketchLines.addCenterPointRectangle(
            adsk.core.Point3D.create(0, 0, 0), adsk.core.Point3D.create(2, 2, 0))
        ext = tag.features.extrudeFeatures.addSimple(
            sk.profiles.item(0), adsk.core.ValueInput.createByString("3 mm"),
            adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        ext.bodies.item(0).name = "tile"
        return {"bodies": tag.bRepBodies.count}
    step("tile_extrude", tile)

    markers = aruco_core.module("markers")
    spec = markers.TagSpec(dictionary="DICT_4X4_50", marker_id=7, marker_length_m=0.03)
    boxes = markers.cell_boxes(spec)
    tbm = adsk.fusion.TemporaryBRepManager.get()

    def cells_body(boxes_m, top_cm=0.3, depth_cm=0.06):
        body = None
        for (x0, y0, x1, y1) in boxes_m:
            cx, cy = (x0 + x1) * 50, (y0 + y1) * 50          # m -> cm
            w, h = (x1 - x0) * 100, (y1 - y0) * 100
            obb = adsk.core.OrientedBoundingBox3D.create(
                adsk.core.Point3D.create(cx, cy, top_cm - depth_cm / 2),
                adsk.core.Vector3D.create(1, 0, 0), adsk.core.Vector3D.create(0, 1, 0),
                w, h, depth_cm)
            box = tbm.createBox(obb)
            if body is None:
                body = box
            else:
                tbm.booleanOperation(body, box, adsk.fusion.BooleanTypes.UnionBooleanType)
        return body

    temp = step("tbm_union", lambda: None)
    temp = cells_body(boxes)
    step("tbm_union_lumps", lambda: {"lumps": temp.lumps.count, "faces": temp.faces.count,
                                     "solid": temp.isSolid, "boxes": len(boxes)})

    state = {}

    def base_feature():
        bf = tag.features.baseFeatures.add()
        bf.startEdit()
        b = tag.bRepBodies.add(temp, bf)
        bf.finishEdit()
        cells = bf.bodies.item(0)
        cells.name = "aruco cells"
        state["bf"], state["cells"] = bf, cells
        return {"bf_bodies": bf.bodies.count, "lumps": cells.lumps.count,
                "comp_bodies": tag.bRepBodies.count}
    step("base_feature_add", base_feature)

    def combine():
        tilebody = tag.bRepBodies.itemByName("tile")
        tools = adsk.core.ObjectCollection.create()
        tools.add(state["cells"])
        ci = tag.features.combineFeatures.createInput(tilebody, tools)
        ci.operation = adsk.fusion.FeatureOperations.CutFeatureOperation
        ci.isKeepToolBodies = True
        cf = tag.features.combineFeatures.add(ci)
        state["combine"] = cf
        return {"health": str(cf.healthState), "comp_bodies": tag.bRepBodies.count}
    step("combine_cut_keep", combine)

    def hole_after():
        # a feature AFTER the tag, like Zach's "print it with a hole"
        sk = tag.sketches.add(tag.xYConstructionPlane)
        sk.sketchCurves.sketchCircles.addByCenterRadius(
            adsk.core.Point3D.create(1.6, 1.6, 0), 0.15)
        prof = sk.profiles.item(0)
        ext = tag.features.extrudeFeatures.addSimple(
            prof, adsk.core.ValueInput.createByString("3 mm"),
            adsk.fusion.FeatureOperations.CutFeatureOperation)
        state["hole"] = ext
        return {"health": str(ext.healthState)}
    step("hole_after_tag", hole_after)

    def update_body():
        spec2 = markers.TagSpec(dictionary="DICT_4X4_50", marker_id=23, marker_length_m=0.03)
        new = cells_body(markers.cell_boxes(spec2))
        bf = state["bf"]
        bf.startEdit()
        ok = bf.updateBody(state["cells"], new)
        bf.finishEdit()
        return {"updateBody": ok, "combine": str(state["combine"].healthState),
                "hole": str(state["hole"].healthState),
                "cells_lumps": bf.bodies.item(0).lumps.count,
                "comp_bodies": [b.name for b in tag.bRepBodies]}
    step("update_body_regen", update_body)

    def appearance():
        lib = None
        for i in range(app.materialLibraries.count):
            l = app.materialLibraries.item(i)
            if "Appearance" in l.name:
                lib = l
                break
        names = []
        for i in range(lib.appearances.count):
            n = lib.appearances.item(i).name
            if "black" in n.lower() or "white" in n.lower():
                names.append(n)
        return {"lib": lib.name, "candidates": names[:40]}
    step("appearance_candidates", appearance)

    # assemblies: Actuator holds Tag; Arm holds 3 Actuators; root holds 2 Arms
    def assemble():
        act_occ = root.occurrences.addNewComponent(I())
        act = act_occ.component
        act.name = "Actuator"
        act.occurrences.addExistingComponent(tag, I())
        tag_occ.deleteMe()
        arm_occ = root.occurrences.addNewComponent(I())
        arm = arm_occ.component
        arm.name = "Arm"
        for k in range(3):
            m = I()
            m.translation = adsk.core.Vector3D.create(6 * k, 0, 0)
            arm.occurrences.addExistingComponent(act, m)
        act_occ.deleteMe()
        m = I()
        m.translation = adsk.core.Vector3D.create(0, 10, 0)
        root.occurrences.addExistingComponent(arm, m)
        state.update(act=act, arm=arm)
        return [o.fullPathName for o in root.allOccurrences]
    step("assemble", assemble)

    def attrs():
        arm = state["arm"]
        native = arm.occurrences.item(1)                 # Actuator:2 inside Arm
        native.attributes.add("bam_aruco", "overrides",
                              json.dumps({"Tag:1|top": {"marker_id": 17}}))
        seen = {}
        for o in root.allOccurrences:
            a = o.attributes.itemByName("bam_aruco", "overrides")
            na = o.nativeObject.attributes.itemByName("bam_aruco", "overrides") \
                if o.nativeObject else None
            seen[o.fullPathName] = {"proxy": a.value if a else None,
                                    "native": na.value if na else None,
                                    "has_native": o.nativeObject is not None}
        # and a root-level one on a proxy, to see where it lands
        proxy = next(o for o in root.allOccurrences
                     if o.fullPathName.endswith("Arm:2+Actuator:3:1")
                     or o.fullPathName == "Arm:2+Actuator:3")
        proxy.attributes.add("bam_aruco", "probe_proxy", "root-level")
        seen["_after_proxy_write_native_sees"] = bool(
            proxy.nativeObject.attributes.itemByName("bam_aruco", "probe_proxy"))
        seen["_find_attributes"] = [a.parent.objectType if a.parent else None
                                    for a in design.findAttributes("bam_aruco", "")]
        return seen
    step("occurrence_attributes", attrs)

    def visibility():
        tags = [o for o in root.allOccurrences if o.component.name == "Tag"]
        target = next(o for o in tags if "Arm:1+Actuator:2" in o.fullPathName)
        target.isLightBulbOn = False
        return {o.fullPathName: {"bulb": o.isLightBulbOn, "visible": o.isVisible}
                for o in tags}
    step("per_path_visibility", visibility)

    def component_copy():
        arm = state["arm"]
        has = hasattr(arm.occurrences, "addNewComponentCopy")
        out = {"has_addNewComponentCopy": has}
        if has:
            occ = arm.occurrences.addNewComponentCopy(tag, I())
            out["copy_component"] = occ.component.name
            out["copy_bodies"] = [b.name for b in occ.component.bRepBodies]
            out["copy_features"] = occ.component.features.count
            out["copy_base_features"] = occ.component.features.baseFeatures.count
            state["copy_occ"] = occ
        return out
    step("component_copy", component_copy)

    def asbuilt():
        arm = state["arm"]
        occ = state.get("copy_occ")
        act2 = arm.occurrences.item(1)
        ji = arm.asBuiltJoints.createInput(occ, act2, None)
        ji.setAsRigidJointMotion()
        j = arm.asBuiltJoints.add(ji)
        return {"joint": j.name, "health": str(j.healthState)}
    step("asbuilt_rigid", asbuilt)

    def export_3mf():
        import os, tempfile
        path = os.path.join(tempfile.gettempdir(), "aruco_probe_tag.3mf")
        opts = design.exportManager.createC3MFExportOptions(tag, path)
        ok = design.exportManager.execute(opts)
        return {"ok": ok, "bytes": os.path.getsize(path) if os.path.exists(path) else 0}
    step("export_3mf", export_3mf)

    if close:
        step("close", lambda: doc.close(False))
    return log
