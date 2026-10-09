"""The dev bridge's allow-list. One name -> one function; nothing else runs.

Every action works on the ACTIVE document; `build_demo` creates a new
unsaved one. None of them saves, closes or edits a document the add-in did
not create, except `close_active`, which refuses a saved document.
"""
from __future__ import annotations

import importlib
import math
import sys

import adsk.core

# The bridge re-imports this module on every request; re-import the workers
# it reaches too, so a bridge armed by an older copy still runs today's code.
for _name in ("aruco_core", "aruco_tags", "aruco_demo", "aruco_export", "aruco_probe",
              "aruco_feature", "aruco_extrude_cmd", "aruco_live_test", "aruco_extrude_lab", "aruco_preview",
              "aruco_heights_lab", "aruco_ids_preview", "aruco_ids_test", "aruco_chamfer_lab",
              "aruco_origin_lab",
              # the bridge itself (its _dispatch busy guard); its state lives
              # in a separate module, so the running worker is unaffected
              "aruco_bridge"):
    if _name in sys.modules:
        importlib.reload(sys.modules[_name])


def _ping():
    app = adsk.core.Application.get()
    doc = app.activeDocument
    return {"fusion": app.version, "active_document": doc.name if doc else None,
            "saved": bool(doc and doc.isSaved),
            "active_command": app.userInterface.activeCommand}


def _probe(close="1"):
    import aruco_probe
    return aruco_probe.run(close=str(close) not in ("0", "false", "no"))


def _mine():
    """Documents this add-in created (closing anything else is refused)."""
    st = sys.modules.setdefault("_aruco_created_docs", type(sys)("_aruco_created_docs"))
    if not hasattr(st, "ids"):
        st.ids = set()
    return st.ids


def _require_mine():
    """Refuse to CHANGE a document this add-in did not create.

    The bridge acts on the ACTIVE document, and Zach works in Fusion while
    agents run: on 2026-10-07 a set_black call landed on a new document he
    had just opened (it failed before writing anything). Read-only actions
    stay unguarded; anything that edits geometry or attributes calls this.
    """
    doc = adsk.core.Application.get().activeDocument
    key = doc.creationId if doc is not None and hasattr(doc, "creationId") else id(doc)
    if doc is None or key not in _mine():
        raise PermissionError(
            f"active document '{doc.name if doc else None}' was not created by this "
            "add-in in this session -- refusing to modify it (activate the demo first)")


def _remember_active():
    doc = adsk.core.Application.get().activeDocument
    _mine().add(doc.creationId if hasattr(doc, "creationId") else id(doc))


def _build_demo(n_actuators="6", n_arms="2", dictionary="DICT_4X4_50"):
    import aruco_demo
    out = aruco_demo.build(int(n_actuators), int(n_arms), dictionary)
    _remember_active()
    return out


def _summary():
    import aruco_tags
    return aruco_tags.summary()


def _serials():
    """Plan + WRITE serials for every colliding copy (deepest level first)."""
    _require_mine()
    import aruco_core
    import aruco_tags
    markers = aruco_core.module("markers")
    des = aruco_tags.design()
    raw = aruco_tags.collect(des)
    tags, ovs = markers.tags_from_payload(raw, lambda m: None)
    planned, res = markers.plan_serials(tags, ovs, aruco_tags.occurrence_refs(des),
                                        des.rootComponent.name)
    for p in planned:
        aruco_tags.write_override(p.owner, p.occurrence, p.key, {"marker_id": p.marker_id})
    return {"planned": [vars(p) for p in planned],
            "remaining_duplicates": {k: [i.name for i in v]
                                     for k, v in res.duplicates().items()}}


def _user_serials(document="", confirm="", dry_run="1"):
    """ArUco IDs "Number duplicates" + OK on one of ZACH's documents, at his
    explicit request (2026-10-09, relayed by the CAD2Robot session: "the
    aruco marker IDs are all duplicates, please run the aruco ID thing").

    Refuses unless `document` is the ACTIVE document's name and `confirm`
    repeats it, and only while Fusion is idle. dry_run=1 (the default)
    writes nothing: the current table, the duplicates and the plan.
    dry_run=0 writes the planned per-copy ids (the deepest level that works,
    as plan_serials decides) and rebuilds the tag parts (apply_variants,
    which refreshes stale records first) inside ONE hidden command, i.e.
    one undo step, then reports the final table."""
    import aruco_core
    import aruco_feature
    import aruco_tags
    _quiet_or_raise()
    app = adsk.core.Application.get()
    doc = app.activeDocument
    if doc is None or not document or doc.name != document or confirm != document:
        raise PermissionError(f"refusing: the active document is {doc.name if doc else None!r}; "
                              "pass document=<its exact name> and confirm=<the same name>")
    markers = aruco_core.module("markers")
    des = aruco_tags.design()

    def table(res):
        rows = []
        for inst in sorted(res.instances, key=lambda i: i.name):
            rows.append({"tag": inst.name, "dictionary": inst.spec.dictionary,
                         "ids": inst.spec.ids, "hidden": bool(inst.hidden)})
        return rows

    def plan():
        raw = aruco_tags.collect(des)
        tags, ovs = markers.tags_from_payload(raw, lambda m: None)
        res0 = markers.resolve(tags, ovs, aruco_tags.occurrence_refs(des), des.rootComponent.name, "cm")
        planned, after = markers.plan_serials(tags, ovs, aruco_tags.occurrence_refs(des),
                                              des.rootComponent.name)
        return res0, planned, after

    res0, planned, after = plan()
    out = {"document": doc.name, "dry_run": str(dry_run) not in ("0", "false", "no"),
           "before": table(res0),
           "duplicates_before": {k: [i.name for i in v] for k, v in res0.duplicates().items()},
           "planned": [{"owner": p.owner, "occurrence": p.occurrence, "key": p.key,
                        "marker_id": p.marker_id} for p in planned],
           "duplicates_after_plan": {k: [i.name for i in v] for k, v in after.duplicates().items()}}
    if out["dry_run"]:
        return out

    def write():
        try:
            app.userInterface.activeSelections.clear()
        except Exception:                          # noqa: BLE001
            pass
        for p in planned:
            aruco_tags.write_override(p.owner, p.occurrence, p.key, {"marker_id": p.marker_id})
        return aruco_tags.apply_variants(des)

    report = aruco_feature.in_command(write)
    if app.activeDocument != doc:
        out["warning"] = "the active document changed during the run"
    res1 = aruco_tags.resolve_live(des)
    out["variants"] = {k: report.get(k) for k in ("created", "kept", "removed", "hidden",
                                                  "refreshed", "warnings")}
    out["after"] = table(res1)
    out["duplicates_after"] = {k: [i.name for i in v] for k, v in res1.duplicates().items()}
    return out


def _set_override(owner, occurrence, key, marker_id=""):
    _require_mine()
    import aruco_tags
    vals = {"marker_id": int(marker_id)} if str(marker_id) != "" else None
    return aruco_tags.write_override(owner, occurrence, key, vals)


def _set_tag_id(component, key, marker_id):
    _require_mine()
    import aruco_tags
    des = aruco_tags.design()
    comp = next(c for c in des.allComponents if c.name == component)
    return aruco_tags.set_tag_spec(comp, key, marker_id=int(marker_id))


def _set_black(component, key, depth_mm="0.6", raise_mm="0", rebuild="1"):
    """Bridge twin of ArUco Edit's black-layer fields."""
    _require_mine()
    import aruco_tags
    des = aruco_tags.design()
    comp = next(c for c in des.allComponents if c.name == component)
    out = aruco_tags.set_tag_spec(comp, key, depth_m=float(depth_mm) / 1000.0,
                                  raise_m=float(raise_mm) / 1000.0)
    if str(rebuild) in ("1", "true"):
        out["variants"] = {k: len(v) for k, v in aruco_tags.apply_variants(des).items()}
    return out


def _measure_tag(component, key=""):
    """Thickness of the black and tile bodies along the tag normal (mm)."""
    import aruco_tags
    des = aruco_tags.design()
    comp = next(c for c in des.allComponents if c.name == component)
    rec = next(r for r in aruco_tags.read_tag_records(comp) if not key or r["key"] == key)
    z, o = rec["z_axis"], rec["origin"]
    out = {}
    for b in comp.bRepBodies:
        ts = []
        for v in b.vertices:
            p = v.geometry
            ts.append((p.x - o[0]) * z[0] + (p.y - o[1]) * z[1] + (p.z - o[2]) * z[2])
        out[b.name] = {"from_mm": round(min(ts) * 10, 4), "to_mm": round(max(ts) * 10, 4)}
    cf = aruco_tags._find_by_token(des, (rec.get("fusion") or {}).get("combine"))
    out["_pocket"] = None if cf is None else ("suppressed" if cf.timelineObject.isSuppressed
                                              else "active")
    out["_spec"] = rec["spec"]
    return out


def _finish_edit():
    """Recovery: leave a base-feature edit a failed call stranded."""
    import adsk.fusion
    import aruco_tags
    des = aruco_tags.design()
    obj = des.activeEditObject
    bf = adsk.fusion.BaseFeature.cast(obj)
    if bf is not None:
        bf.finishEdit()
        return {"finished": bf.name}
    info = {"finished": None, "active_edit": getattr(obj, "objectType", str(obj)),
            "active_edit_name": getattr(obj, "name", None),
            "design_type": int(des.designType),
            "root": des.rootComponent.name,
            "occurrences": des.rootComponent.allOccurrences.count}
    for comp in des.allComponents:
        for f in comp.features.baseFeatures:
            try:
                f.finishEdit()
                info.setdefault("closed", []).append(f"{comp.name}/{f.name}")
            except Exception:                      # noqa: BLE001
                pass
    info["design_type_after"] = int(des.designType)
    return info


def _finish_base_feature_cmd(run="0"):
    """Find Fusion's own Finish Base Feature command; run it when run=1."""
    ui = adsk.core.Application.get().userInterface
    hits = []
    for i in range(ui.commandDefinitions.count):
        d = ui.commandDefinitions.item(i)
        text = f"{d.id} {d.name}".lower()
        if "finish" in text and "base" in text:
            hits.append({"id": d.id, "name": d.name})
    if str(run) == "1" and hits:
        ui.commandDefinitions.itemById(hits[0]["id"]).execute()
    return {"candidates": hits, "ran": str(run) == "1" and bool(hits)}


def _health():
    """Every timeline item that is not healthy (warning or error)."""
    import aruco_tags
    des = aruco_tags.design()
    bad, n = [], des.timeline.count
    for i in range(n):
        item = des.timeline.item(i)
        ent = item.entity
        h = getattr(ent, "healthState", None) if ent is not None else None
        if h not in (None, 0):
            bad.append({"index": i, "name": item.name, "health": int(h),
                        "message": getattr(ent, "errorOrWarningMessage", "")})
    try:
        des.activateRootComponent()
    except Exception:                              # noqa: BLE001
        pass
    return {"timeline_items": n, "unhealthy": bad}


def _cells_info(component):
    """Topology of every 'aruco cells' body in a component (lumps, solid)."""
    import aruco_tags
    des = aruco_tags.design()
    comp = next(c for c in des.allComponents if c.name == component)
    out = []
    for b in comp.bRepBodies:
        if b.name.startswith("aruco cells"):
            out.append({"name": b.name, "lumps": b.lumps.count, "solid": b.isSolid,
                        "faces": b.faces.count, "volume_mm3": round(b.volume * 1000, 4)})
    return out


def _quiet_or_raise():
    """Zach works in Fusion while agents run: never switch documents under an
    open command (it would cancel his dialog)."""
    ui = adsk.core.Application.get().userInterface
    cmd = ui.activeCommand
    if cmd not in ("SelectCommand", "", None):
        raise RuntimeError(f"Fusion is busy ({cmd}) -- try again when it is idle")


def _on_demo(fn):
    """Run fn() with the add-in's demo active, then put the user's document back."""
    app = adsk.core.Application.get()
    _quiet_or_raise()
    before = app.activeDocument
    _activate_demo()
    try:
        _require_mine()
        return fn()
    finally:
        if before is not None and before != app.activeDocument:
            try:
                before.activate()
            except Exception:                      # noqa: BLE001
                pass


def _frames():
    """Joint Origin at every tag's optical frame, on the demo."""
    import aruco_tags
    return _on_demo(lambda: aruco_tags.ensure_frames())


def _demo_export(out_dir="", page="letter"):
    """ArUco export of the demo (sidecar, STLs, joint_origins.json)."""
    import aruco_export
    return _on_demo(lambda: aruco_export.export(out_dir or None, page))


def _camera_capture(path, eye, target, up="0,0,1", fov_deg="30", width="1600",
                    height="1200"):
    """Render the demo from an exact perspective camera (cm), for PnP checks.

    Returns the camera Fusion actually used (read back) so the analysis uses
    measured, not requested, numbers.
    """
    import os

    def vec(t):
        return [float(v) for v in str(t).split(",")]

    def run():
        app = adsk.core.Application.get()
        vp = app.activeViewport
        cam = vp.camera
        cam.cameraType = adsk.core.CameraTypes.PerspectiveCameraType
        cam.isFitView = False
        cam.isSmoothTransition = False
        cam.eye = adsk.core.Point3D.create(*vec(eye))
        cam.target = adsk.core.Point3D.create(*vec(target))
        cam.upVector = adsk.core.Vector3D.create(*vec(up))
        cam.perspectiveAngle = math.radians(float(fov_deg))
        vp.camera = cam
        # Fusion draws joint and Joint Origin glyphs OVER the model; one sat
        # on a marker and moved its detected corners 13 px. Hide them for the
        # capture, put them back after.
        from adsk import fusion as _fusion
        des = _fusion.Design.cast(app.activeProduct)
        saved = []
        for comp in des.allComponents:
            for attr in ("isJointsFolderLightBulbOn", "isJointOriginsFolderLightBulbOn"):
                if hasattr(comp, attr):
                    saved.append((comp, attr, getattr(comp, attr)))
                    try:
                        setattr(comp, attr, False)
                    except Exception:              # noqa: BLE001
                        pass
        vp.refresh()
        adsk.doEvents()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            ok = vp.saveAsImageFile(path, int(width), int(height))
        finally:
            for comp, attr, val in saved:
                try:
                    setattr(comp, attr, val)
                except Exception:                  # noqa: BLE001
                    pass
        got = vp.camera
        return {"ok": bool(ok), "path": path, "width": int(width), "height": int(height),
                "eye_cm": [got.eye.x, got.eye.y, got.eye.z],
                "target_cm": [got.target.x, got.target.y, got.target.z],
                "up": [got.upVector.x, got.upVector.y, got.upVector.z],
                "perspective_angle_rad": got.perspectiveAngle,
                "viewport_px": [vp.width, vp.height]}
    return _on_demo(run)


def _inspect_records():
    """READ-ONLY: every tag record in the active design and whether its
    geometry still exists (base feature token resolves, not suppressed)."""
    import aruco_tags
    des = aruco_tags.design()
    out = []
    for comp in des.allComponents:
        for rec in aruco_tags.read_tag_records(comp):
            fz = rec.get("fusion") or {}
            bf = aruco_tags._find_by_token(des, fz.get("base_feature"))
            sup = None
            if bf is not None:
                try:
                    sup = bool(bf.timelineObject.isSuppressed)
                except Exception:                  # noqa: BLE001
                    sup = None
            cells = fz.get("cells_body") or ""
            try:
                cells_found = bool(cells) and comp.bRepBodies.itemByName(cells) is not None
            except RuntimeError:                   # itemByName("") raises in 2705
                cells_found = False
            out.append({"component": comp.name, "key": rec.get("key"),
                        "spec": {k: rec.get("spec", {}).get(k) for k in
                                 ("dictionary", "marker_id", "marker_length_m", "layout")},
                        "base_feature_found": bf is not None, "suppressed": sup,
                        "cells_body_found": cells_found,
                        "live": bool(fz.get("custom_feature")),
                        "live_error": fz.get("live_error"), "group": bool(fz.get("group")),
                        "variant_of": rec.get("variant_of")})
    return {"document": des.parentDocument.name, "records": out}


def _modes_fixture(out_dir):
    """Run aruco_demo.modes_fixture in fresh documents; put the user's back."""
    import aruco_demo
    app = adsk.core.Application.get()
    _quiet_or_raise()
    before = app.activeDocument
    try:
        return aruco_demo.modes_fixture(out_dir)
    finally:
        if before is not None:
            try:
                before.activate()
            except Exception:                      # noqa: BLE001
                pass


def _close_stray_fixture():
    """Close unsaved, untouched 'Untitled' documents whose design has exactly
    the fixture's 'disc' body -- the one a failed modes_fixture left open."""
    import adsk.fusion
    app = adsk.core.Application.get()
    closed = []
    for i in range(app.documents.count - 1, -1, -1):
        doc = app.documents.item(i)
        if doc.isSaved or doc.dataFile is not None or doc == app.activeDocument:
            continue
        des = adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
        names = [b.name for b in des.rootComponent.bRepBodies] if des else []
        if names and all(n.startswith("disc") for n in names)                 and des.rootComponent.occurrences.count == 0:
            closed.append(doc.name)
            doc.close(False)
    return {"closed": closed}


def _list_documents():
    """READ-ONLY: open documents, saved or not, with their root bodies."""
    import adsk.fusion
    app = adsk.core.Application.get()
    out = []
    for i in range(app.documents.count):
        doc = app.documents.item(i)
        info = {"i": i, "name": doc.name, "saved": doc.isSaved,
                "cloud": doc.dataFile is not None, "active": doc == app.activeDocument}
        des = adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
        if des:
            info["root_bodies"] = [b.name for b in des.rootComponent.bRepBodies][:6]
            info["occurrences"] = des.rootComponent.occurrences.count
            info["timeline"] = des.timeline.count if des.designType else None
        out.append(info)
    return out


def _demo_send(paths="", open_slicer="0"):
    """Send to PrusaSlicer on the demo: occurrences by root path ('+'-joined),
    comma-separated; default = two tag parts and one actuator copy."""
    import aruco_export

    def run():
        des = adsk.core.Application.get().activeProduct
        want = [p for p in (paths or "").split(",") if p] or None
        occs = list(des.rootComponent.allOccurrences)
        if want is None:
            tags = [o for o in occs if o.component.name.startswith("Tag ")][:2]
            act = [o for o in occs if o.component.name == "Actuator"][:1]
            ents = tags + act
        else:
            ents = [o for o in occs if o.fullPathName in want]
        return aruco_export.send_selection(ents, open_slicer=str(open_slicer) == "1")
    return _on_demo(run)


def _apply():
    _require_mine()
    import aruco_tags
    return aruco_tags.apply_variants()


def _capture(path, view="", width="1600", height="1000"):
    import aruco_demo
    return aruco_demo.capture(path, int(width), int(height), view or None)


def _export(out_dir="", page="letter", landscape="0"):
    import aruco_export
    return aruco_export.export(out_dir or None, page, landscape in ("1", "true"))


def _collect():
    import aruco_tags
    return aruco_tags.collect()


def _close_active():
    app = adsk.core.Application.get()
    doc = app.activeDocument
    if doc is None:
        return {"closed": None}
    if doc.isSaved or doc.dataFile is not None:
        raise ValueError(f"refusing to close saved document '{doc.name}'")
    key = doc.creationId if hasattr(doc, "creationId") else id(doc)
    if key not in _mine():
        raise ValueError(f"refusing to close '{doc.name}': this add-in did not "
                         "create it in this session")
    name = doc.name
    doc.close(False)
    return {"closed": name}


def _build_ui_fixture():
    import aruco_demo
    out = aruco_demo.build_ui_fixture()
    _remember_active()
    return out


def _tag_body(body, layout="single", dictionary="DICT_4X4_50", marker_id="",
              rows="1", cols="1", key=""):
    """Tag the +Z-most planar face of a root body (bridge twin of ArUco Tag)."""
    _require_mine()
    import aruco_tags
    des = aruco_tags.design()
    b = des.rootComponent.bRepBodies.itemByName(body)
    faces = [f for f in b.faces if isinstance(f.geometry, adsk.core.Plane)]
    top = max(faces, key=lambda f: f.pointOnFace.z)
    probe = aruco_tags.M().TagSpec(layout=layout, rows=int(rows), cols=int(cols))
    mid = int(marker_id) if str(marker_id) != "" else aruco_tags.next_free_id(
        dictionary, probe.marker_count)
    return aruco_tags.add_tag(top, layout=layout, dictionary=dictionary, marker_id=mid,
                              rows=int(rows), cols=int(cols), key=key)


def _extrude_sketch(sketch, thickness_mm="3", **kw):
    _require_mine()
    import aruco_tags
    des = aruco_tags.design()
    sk = des.rootComponent.sketches.itemByName(sketch)
    return aruco_tags.aruco_extrude(sk.profiles.item(0), thickness_mm=float(thickness_mm),
                                    marker_id=aruco_tags.next_free_id("DICT_4X4_50"))


def _activate_demo():
    """Bring forward the demo this add-in built (the document with an 'Arm')."""
    import adsk.fusion
    app = adsk.core.Application.get()
    docs = [app.documents.item(i) for i in range(app.documents.count)]
    mine = _mine()
    # documents this add-in registered first (an older demo built before the
    # registry existed would otherwise win and then fail _require_mine)
    docs.sort(key=lambda d: 0 if (d.creationId if hasattr(d, "creationId") else id(d)) in mine else 1)
    for doc in docs:
        if doc.isSaved or doc.dataFile is not None:
            continue                       # only unsaved documents are demos
        try:
            des = adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
            if des and any(c.name == "Arm" and c.attributes.itemByName("bam_aruco", "tags") is None
                           and any(cc.attributes.itemByName("bam_aruco", "tags")
                                   for cc in des.allComponents)
                           for c in des.allComponents):
                doc.activate()
                return {"activated": doc.name}
        except Exception:                          # noqa: BLE001
            continue
    raise ValueError("no demo document from this session is open")


def _live_status():
    """READ-ONLY: is the ArUco Extrude feature registered; how is the add-in loaded."""
    import os
    import aruco_feature
    app = adsk.core.Application.get()
    here = os.path.dirname(os.path.abspath(__file__))
    scripts = []
    for i in range(app.scripts.count):
        sc = app.scripts.item(i)
        try:
            if "aruco" in sc.name.lower():
                scripts.append({"name": sc.name, "running": sc.isRunning, "folder": sc.folder,
                                "addin": sc.isAddIn})
        except Exception:                          # noqa: BLE001
            pass
    return dict(aruco_feature.status(), here=here, scripts=scripts)


def _start_addin():
    """Link THIS ArucoMarkers folder in Scripts and Add-Ins and run it, so its
    run() registers the custom feature definition (it must be made inside an
    add-in's run(); the manifest carries the id it needs). Idempotent."""
    import os
    app = adsk.core.Application.get()
    here = os.path.dirname(os.path.abspath(__file__))
    sc = app.scripts.itemByPath(here)
    out = {"folder": here}
    if sc is None:
        sc = app.scripts.addExisting(here)
        out["linked"] = sc is not None
    if sc is None:
        raise RuntimeError(f"could not link {here}")
    out["was_running"] = bool(sc.isRunning)
    if not sc.isRunning:
        out["ran"] = bool(sc.run(False))
    out["status"] = _live_status()
    return out


def _diag_modules():
    """READ-ONLY: which aruco modules are loaded, from where, and the
    feature state module (is there more than one copy?)."""
    out = {}
    for name, mod in sorted(sys.modules.items()):
        if "aruco" in name.lower() and mod is not None:
            out[name] = getattr(mod, "__file__", None) or type(mod).__name__
    st = sys.modules.get("_aruco_feature_state")
    out["_state_definition"] = repr(getattr(st, "definition", None))
    out["_state_register_log"] = getattr(st, "register_log", None)
    return out


def _restart_addin():
    """Stop + run THIS add-in (Scripts and Add-Ins), keeping the bridge that
    serves this request armed. Fusion runs it a moment later."""
    import os
    app = adsk.core.Application.get()
    _quiet_or_raise()
    here = os.path.dirname(os.path.abspath(__file__))
    sc = app.scripts.itemByPath(here)
    if sc is None:
        return _start_addin()
    bst = sys.modules.get("_aruco_bridge_state")
    if bst is not None:
        bst.keep_armed = True
    out = {"was_running": bool(sc.isRunning)}
    try:
        if sc.isRunning:
            out["stopped"] = bool(sc.stop())
        out["ran"] = bool(sc.run(False))
    finally:
        if bst is not None:
            bst.keep_armed = False
    return out


def _live_test(out_dir="", keep="0", capture="1"):
    import aruco_live_test
    importlib.reload(aruco_live_test)
    return aruco_live_test.run(out_dir=out_dir, keep=keep, capture=capture)


def _lab(fn, **kw):
    """aruco_extrude_lab checks (each in a new document, closed after)."""
    import aruco_extrude_lab
    importlib.reload(aruco_extrude_lab)
    if fn not in ("colours", "colour_probe", "cross", "preview_bench",
                  "ui_fixture", "ui_open", "ui_state", "ui_close", "probe_doc"):
        raise ValueError(f"unknown lab check {fn!r}")
    return getattr(aruco_extrude_lab, fn)(**kw)


def _inspect_design(sketch="", document=""):
    """READ-ONLY: what an ArUco Extrude would see in a design -- sketches
    (visibility, profiles shown, curves, profiles with area and centre in root
    space), the bodies under each profile centre (any component, through
    occurrences), occurrences + transforms, appearances of our bodies, and
    every ArUco Extrude node. Rolls nothing, activates nothing. `document`
    reads an open document by name without activating it."""
    import adsk.fusion
    app = adsk.core.Application.get()
    doc = app.activeDocument
    if document:
        doc = next((app.documents.item(i) for i in range(app.documents.count)
                    if app.documents.item(i).name == document), None)
        if doc is None:
            raise ValueError(f"no open document {document!r}")
    des = adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
    root = des.rootComponent
    R = lambda v: [round(c * 10, 3) for c in (v.x, v.y, v.z)]          # cm -> mm
    out = {"document": doc.name, "design_type": int(des.designType),
           "active_component": des.activeComponent.name,
           "timeline_marker": des.timeline.markerPosition if des.designType else None,
           "occurrences": [], "sketches": [], "nodes": [], "bodies": []}
    for occ in root.allOccurrences:
        m = occ.transform2
        o, x, y, z = m.getAsCoordinateSystem()
        out["occurrences"].append({"path": occ.fullPathName, "component": occ.component.name,
                                   "visible": occ.isVisible, "ref": occ.isReferencedComponent,
                                   "origin_mm": R(o), "x": [round(c, 4) for c in (x.x, x.y, x.z)],
                                   "z": [round(c, 4) for c in (z.x, z.y, z.z)]})
    all_bodies = [(b, "root") for b in root.bRepBodies]
    for occ in root.allOccurrences:
        all_bodies += [(b, occ.fullPathName) for b in occ.bRepBodies]
    for b, where in all_bodies:
        bb = b.boundingBox
        try:
            app_name = b.appearance.name if b.appearance else None
        except Exception:                          # noqa: BLE001
            app_name = "?"
        faces_over = set()
        try:
            for f in b.faces:
                if f.appearance is not None and (b.appearance is None
                                                 or f.appearance.name != b.appearance.name):
                    faces_over.add(f.appearance.name)
        except Exception:                          # noqa: BLE001
            pass
        out["bodies"].append({"name": b.name, "in": where, "visible": b.isVisible,
                              "solid": b.isSolid, "min_mm": R(bb.minPoint),
                              "max_mm": R(bb.maxPoint), "appearance": app_name,
                              "face_appearances_other": sorted(faces_over)})
    inside = adsk.fusion.PointContainment.PointInsidePointContainment
    comps = [root] + [c for c in des.allComponents if c != root]
    for comp in comps:
        for sk in comp.sketches:
            if sketch and sk.name != sketch:
                continue
            info = {"name": sk.name, "component": comp.name, "visible": sk.isVisible,
                    "lightbulb": sk.isLightBulbOn, "profiles_shown": sk.areProfilesShown,
                    "is_parametric": getattr(sk, "isParametric", None),
                    "profiles": [], "curves": {}}
            try:
                rp = sk.referencePlane
                info["reference"] = rp.objectType.rsplit("::", 1)[-1] if rp else None
                bf = adsk.fusion.BRepFace.cast(rp)
                if bf is not None:
                    info["reference_body"] = bf.body.name
                    info["reference_ctx"] = (bf.assemblyContext.fullPathName
                                             if bf.assemblyContext else None)
            except Exception as exc:               # noqa: BLE001
                info["reference"] = f"unreadable: {str(exc)[:120]}"
            o0 = sk.sketchToModelSpace(adsk.core.Point3D.create(0, 0, 0))
            p1 = sk.sketchToModelSpace(adsk.core.Point3D.create(0, 0, 1))
            info["plane_origin_mm"] = R(o0)
            info["plane_normal"] = [round(p1.x - o0.x, 4), round(p1.y - o0.y, 4),
                                    round(p1.z - o0.z, 4)]
            cur = {}
            for c in sk.sketchCurves:
                k = c.objectType.rsplit("::", 1)[-1]
                if c.isConstruction:
                    k += "(construction)"
                if getattr(c, "isReference", False):
                    k += "(projected)"
                if getattr(c, "isFixed", False):
                    k += "(fixed)"
                cur[k] = cur.get(k, 0) + 1
            info["curves"] = cur
            for i in range(sk.profiles.count):
                pr = sk.profiles.item(i)
                ap = pr.areaProperties()
                c = sk.sketchToModelSpace(ap.centroid)
                n = adsk.core.Vector3D.create(p1.x - o0.x, p1.y - o0.y, p1.z - o0.z)
                n.normalize()
                hits = []
                for b, where in all_bodies:
                    for sgn in (1.0, -1.0):
                        q = adsk.core.Point3D.create(c.x - sgn * n.x * 0.01, c.y - sgn * n.y * 0.01,
                                                     c.z - sgn * n.z * 0.01)
                        try:
                            if b.pointContainment(q) == inside:
                                hits.append({"body": b.name, "in": where, "side": sgn})
                        except Exception as exc:   # noqa: BLE001
                            hits.append({"body": b.name, "error": str(exc)[:80]})
                bb = pr.boundingBox
                info["profiles"].append({"i": i, "area_mm2": round(ap.area * 100, 2),
                                         "centroid_root_mm": R(c),
                                         "bbox_sketch_mm": [R(bb.minPoint), R(bb.maxPoint)],
                                         "loops": pr.profileLoops.count,
                                         "under_or_over": hits})
            out["sketches"].append(info)
        try:
            import aruco_feature
            for i in range(comp.features.customFeatures.count):
                cf = comp.features.customFeatures.item(i)
                ent = {"name": cf.name, "component": comp.name,
                       "ours": aruco_feature.is_ours(cf), "health": int(cf.healthState),
                       "settings": aruco_feature.settings_of(cf) if aruco_feature.is_ours(cf) else None,
                       "numbers": aruco_feature.numbers_of(cf) if aruco_feature.is_ours(cf) else None,
                       "features": [f.name for f in cf.features], "deps": []}
                for j in range(cf.dependencies.count):
                    d = cf.dependencies.item(j)
                    e = d.entity
                    ent["deps"].append({"id": d.id, "type": e.objectType if e else None,
                                        "ctx": (e.assemblyContext.fullPathName
                                                if e is not None and getattr(e, "assemblyContext", None)
                                                else None)})
                out["nodes"].append(ent)
        except Exception as exc:                   # noqa: BLE001
            out["nodes_error"] = str(exc)[:200]
    return out


def _jo_report(document=""):
    """READ-ONLY: every tag's Joint Origin vs its optical frame (OpenCV's;
    aruco_tags.joint_origin_report).
    `document` reads an open document by name without activating it."""
    import adsk.fusion
    import aruco_tags
    _quiet_or_raise()            # read only while Fusion is idle (Zach's open command)
    app = adsk.core.Application.get()
    doc = app.activeDocument
    if document:
        doc = next((app.documents.item(i) for i in range(app.documents.count)
                    if app.documents.item(i).name == document), None)
        if doc is None:
            raise ValueError(f"no open document {document!r}")
    des = adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
    return {"document": doc.name, "tags": aruco_tags.joint_origin_report(des)}


def _face_report():
    """READ-ONLY: for every live ArUco Extrude in the active design, the edges
    of the face it was made on (type, length, radius, centre in the tag's
    face frame, mm) -- what the auto-size fitter has to work with."""
    import adsk.fusion
    import aruco_feature
    import aruco_tags
    _quiet_or_raise()
    des = aruco_tags.design()
    out = []
    for comp in des.allComponents:
        for rec in aruco_tags.read_tag_records(comp):
            cf = aruco_feature.find_feature(des, rec)
            if cf is None:
                continue
            tgt = aruco_feature.target_of(cf)
            entry = {"key": rec.get("key"), "component": comp.name, "node": cf.name,
                     "target": tgt.objectType.rsplit("::", 1)[-1] if tgt else None,
                     "settings": aruco_feature.settings_of(cf),
                     "numbers": aruco_feature.numbers_of(cf)}
            face = adsk.fusion.BRepFace.cast(tgt)
            if face is not None:
                o, x, y, z, w, h = aruco_tags.face_frame(face)
                entry["bbox_mm"] = [round(w * 10, 3), round(h * 10, 3)]
                entry["area_mm2"] = round(face.area * 100, 2)
                loops = []
                for lp in face.loops:
                    edges = []
                    for e in lp.edges:
                        g = e.geometry
                        d = {"type": g.objectType.rsplit("::", 1)[-1],
                             "len_mm": round(e.length * 10, 3)}
                        c = getattr(g, "center", None)
                        if c is not None:
                            v = [c.x - o[0], c.y - o[1], c.z - o[2]]
                            d["centre_mm"] = [round(sum(a * b for a, b in zip(v, x)) * 10, 2),
                                              round(sum(a * b for a, b in zip(v, y)) * 10, 2)]
                            d["r_mm"] = round(g.radius * 10, 3)
                        edges.append(d)
                    loops.append({"outer": lp.isOuter, "edges": edges})
                entry["loops"] = loops
            out.append(entry)
    return out


def _register_commands():
    import aruco_commands
    aruco_commands.unregister()
    aruco_commands.register()
    return {"commands": [c[0] for c in aruco_commands.COMMANDS]}


ACTIONS = {
    "live_status": _live_status,
    "user_serials": _user_serials,
    "face_report": _face_report,
    # the auto-size verification gallery: every face in a NEW document of ours, closed after
    "autosize_gallery": lambda **kw: importlib.reload(
        importlib.import_module("aruco_autosize_gallery")).run_fusion(**kw),
    "diag_modules": _diag_modules,
    "start_addin": _start_addin,
    "restart_addin": _restart_addin,
    "live_test": _live_test,
    "inspect_design": _inspect_design,
    "lab_colours": lambda **kw: _lab("colours", **kw),
    "lab_colour_probe": lambda **kw: _lab("colour_probe", **kw),
    "lab_cross": lambda **kw: _lab("cross", **kw),
    "lab_preview": lambda **kw: _lab("preview_bench", **kw),
    "lab_ui_fixture": lambda **kw: _lab("ui_fixture", **kw),
    "lab_ui_open": lambda **kw: _lab("ui_open", **kw),
    "lab_ui_state": lambda **kw: _lab("ui_state", **kw),
    "lab_ui_close": lambda **kw: _lab("ui_close", **kw),
    "lab_probe_doc": lambda **kw: _lab("probe_doc", **kw),
    # ArUco Extrude Offset / Height, locating pins, old-parameter migration
    # (a NEW document of ours, closed after unless keep=1)
    "heights_test": lambda **kw: importlib.reload(
        importlib.import_module("aruco_heights_lab")).heights_test(**kw),
    # the corner mark (None / Cut white corner / Black triangle): every layout
    # x mode, edits, the dialog's ghost, the copies' variants (NEW documents
    # of ours, closed after)
    "chamfer_test": lambda **kw: importlib.reload(
        importlib.import_module("aruco_chamfer_lab")).chamfer_test(**kw),
    # the Origin (OpenCV's frame, four for every layout; a rectangular board
    # at 90 / 270 swapped on its side): every layout x Origin, Rotate on a
    # live node, a copy's variant, PnP renders (NEW documents of ours)
    "origin_test": lambda **kw: importlib.reload(
        importlib.import_module("aruco_origin_lab")).origin_test(**kw),
    "jo_report": _jo_report,
    "jo_probe": lambda **kw: importlib.reload(importlib.import_module("aruco_live_test")).jo_probe(**kw),
    "jo_probe_profile": lambda **kw: importlib.reload(
        importlib.import_module("aruco_live_test")).jo_probe_profile(**kw),
    "register_commands": _register_commands,
    "build_ui_fixture": _build_ui_fixture,
    "tag_body": _tag_body,
    "set_black": _set_black,
    "finish_edit": _finish_edit,
    "health": _health,
    "finish_base_feature_cmd": _finish_base_feature_cmd,
    "measure_tag": _measure_tag,
    "inspect_records": _inspect_records,
    "frames": _frames,
    "modes_fixture": _modes_fixture,
    "close_stray_fixture": _close_stray_fixture,
    "list_documents": _list_documents,
    "camera_capture": _camera_capture,
    "demo_export": _demo_export,
    "demo_send": _demo_send,
    "cells_info": _cells_info,
    "activate_demo": _activate_demo,
    "extrude_sketch": _extrude_sketch,
    "ping": _ping,
    "probe": _probe,
    "build_demo": _build_demo,
    "summary": _summary,
    "serials": _serials,
    "set_override": _set_override,
    "set_tag_id": _set_tag_id,
    "apply": _apply,
    "capture": _capture,
    "export": _export,
    "collect": _collect,
    "close_active": _close_active,
    # custom-feature research probe (aruco_cf_probe; re-imported per call)
    "cf_run": lambda **kw: importlib.reload(importlib.import_module("aruco_cf_probe")).run(**kw),
    "cf_commands": lambda **kw: importlib.reload(importlib.import_module("aruco_cf_probe")).commands(**kw),
    "cf_status": lambda **kw: importlib.reload(importlib.import_module("aruco_cf_probe")).status(**kw),
    "cf_unregister": lambda **kw: importlib.reload(importlib.import_module("aruco_cf_probe")).unregister(**kw),
    "cf_host": lambda **kw: importlib.reload(importlib.import_module("aruco_cf_probe")).host(**kw),
    "cf_record": lambda **kw: importlib.reload(importlib.import_module("aruco_cf_probe")).record(**kw),
    # ArUco IDs (per-copy serials): the rack reproduction, before/after the
    # variant fix + the dialog's preview, in a NEW document of ours (closed after)
    "ids_test": lambda **kw: importlib.reload(importlib.import_module("aruco_ids_test")).run(**kw),
    # READ-ONLY: an open document's variants vs where each copy's tag is now
    "ids_inspect": lambda **kw: importlib.reload(importlib.import_module("aruco_ids_test")).inspect(**kw),
    # ArUco Export's types: each type's files (+ the real dialog with dialog=1),
    # in a NEW demo document of ours (closed after); opens no viewer or slicer.
    # Only in a test window the coordinator opens (see aruco_export_lab SAFETY)
    "export_types_test": lambda **kw: importlib.reload(
        importlib.import_module("aruco_export_lab")).run(**kw),
}
