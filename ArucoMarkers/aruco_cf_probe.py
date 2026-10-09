"""Research probe: can an ArUco tag be ONE feature in Fusion's timeline?

Zach, 2026-10-07: "is it possible to make the aruco thing appear as its own
feature inside the history timeline? that would make editing it much
easier..."  The candidate is the Custom Features API (adsk.fusion.Custom*,
still PREVIEW in 2705.1.15). This module is a minimal working custom-feature
tag plus a scripted experiment around it; findings live in
docs/research_aruco_timeline_feature.md.

    custom feature "ArUco <key>"  (one timeline node, our icon)
      base feature    the black cells: ONE TemporaryBRepManager solid
      combine (cut)   pockets the tile, keeps the cells
    custom parameters  marker_id (unitless), black (mm), size (mm)
    named value "spec" dictionary, layout, quiet zone, how the black sits
    dependency "face"  the tile face the tag sits on (upstream geometry)
    compute handler    rebuilds the cells from face + parameters (updateBody)
    edit command       what Fusion runs on double-click / Edit Feature

MEASURED in Fusion 2705.1.15 (2026-10-07; docs/research_aruco_timeline_feature.md):
  * the definition must be created inside an ADD-IN's run(); created from an
    event handler, customFeatures.add fails "InternalValidationError :
    scriptInfoFound". Once an id is bound to an add-in folder it stays bound
    for the session (a second add-in calling create() gets the old binding).
  * that add-in's manifest needs an "id" (GUID); without one add() fails
    "3 : make params invalid". ArucoMarkers.manifest has none today, so this
    probe registers through a throwaway host add-in (host(), ~/CADExporter/
    _aruco_cf_host) whose run() calls register().
  * with both, add() works from the dev bridge too (no command needed).
  * a face in a NON-root component must be given as its root-context proxy.
  * CustomFeatureParameters.itemById RAISES for an unknown id.
  * a compute that reports an error during add() makes add() itself raise.

SAFETY (Zach works in Fusion while this runs): `run()` refuses unless
Fusion is idle (activeCommand == SelectCommand), creates its OWN new
document, never saves, and in a `finally` closes that document and
re-activates the one that was active before. `commandStarting` is only
recorded while a probe run is in flight, so his own commands are never
logged.

Handlers are kept in a sys.modules stash (Fusion holds handlers weakly and
the dev bridge re-imports modules); their notify() looks the worker
function up in this module's globals at call time, so a reload is live.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
import types

import adsk.core
import adsk.fusion

import aruco_tags

HERE = os.path.dirname(os.path.abspath(__file__))
DEF_ID = "bamArucoProbe.ArucoTag"
DEF_NAME = "ArUco Tag"
EDIT_CMD_ID = "bamArucoCfProbeEdit"
CREATE_CMD_ID = "bamArucoCfProbeCreate"
ICON_DIR = os.path.join(HERE, "resources", "bamArucoTag")
STATE_KEY = "_aruco_cf_probe_state"
MEDIA = os.path.normpath(os.path.join(HERE, "..", "reports", "media", "aruco",
                                      "timeline_feature"))
P = adsk.core.Point3D.create
SITS = ("inlaid", "raised", "half")


# ------------------------------------------------------------------ state

def _st():
    st = sys.modules.get(STATE_KEY)
    if st is None:
        st = types.ModuleType(STATE_KEY)
        st.definition = None
        st.compute = None          # CustomFeatureEventHandler
        st.edit_created = None     # CommandCreatedEventHandler on EDIT_CMD_ID
        st.starting = None         # ApplicationCommandEventHandler (commandStarting)
        st.cmd_handlers = []       # per-invocation command handlers
        st.compute_log = []
        st.edit_log = []
        st.started = []            # command ids seen while recording
        st.recording = False
        st.auto = None             # scripted edit: dict of new values, or None
        st.pending_cf = None
        sys.modules[STATE_KEY] = st
    return st


def _app():
    return adsk.core.Application.get()


def _ui():
    return _app().userInterface


def M():
    return aruco_tags.M()


# --------------------------------------------------------------- handlers

class _Compute(adsk.fusion.CustomFeatureEventHandler):
    def notify(self, args):
        _on_compute(args)


class _EditCreated(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        _on_edit_created(args)


class _CreateCreated(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        _on_create_created(args)


class _CmdEvent(adsk.core.CommandEventHandler):
    def __init__(self, kind):
        super().__init__()
        self.kind = kind

    def notify(self, args):
        _on_cmd_event(self.kind, args)


class _Starting(adsk.core.ApplicationCommandEventHandler):
    def notify(self, args):
        _on_starting(args)


def register(def_id=None, host=""):
    """Definition + edit command + handlers, once per Fusion session.

    Measured 2026-10-07: a definition created from the dev bridge's custom
    event (i.e. NOT inside an add-in's run()) makes customFeatures.add fail
    with "InternalValidationError : scriptInfoFound". So the real add-in must
    call this from run(); the probe does it from a throwaway host add-in.
    """
    st = _st()
    def_id = def_id or getattr(st, "def_id", None) or DEF_ID
    st.def_id = def_id
    st.host = host
    ui = _ui()
    out = {}
    cmd_def = ui.commandDefinitions.itemById(EDIT_CMD_ID)
    if cmd_def is None:
        cmd_def = ui.commandDefinitions.addButtonDefinition(
            EDIT_CMD_ID, "Edit ArUco Tag", "Edit an ArUco tag feature (probe)", "")
        st.edit_created = None
        out["edit_command"] = "created"
    if st.edit_created is None:
        h = _EditCreated()
        cmd_def.commandCreated.add(h)
        st.edit_created = h
    if st.starting is None:
        h = _Starting()
        ui.commandStarting.add(h)
        st.starting = h
    # The hidden create command: customFeatures.add only works inside a
    # command's execute (measured: "make params invalid" from an event).
    cdef = ui.commandDefinitions.itemById(CREATE_CMD_ID)
    if cdef is None:
        cdef = ui.commandDefinitions.addButtonDefinition(
            CREATE_CMD_ID, "ArUco Tag (probe create)", "probe", "")
        st.create_created = None
        out["create_command"] = "created"
    if getattr(st, "create_created", None) is None:
        h = _CreateCreated()
        cdef.commandCreated.add(h)
        st.create_created = h
    t0 = time.perf_counter()
    d = adsk.fusion.CustomFeatureDefinition.create(def_id, DEF_NAME, ICON_DIR)
    out["definition_create_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    out["definition_is_existing"] = bool(st.definition is not None and d == st.definition)
    if st.definition is not None and d != st.definition and st.compute is not None:
        try:                                       # moving to a new definition id
            st.definition.customFeatureCompute.remove(st.compute)
        except Exception:                          # noqa: BLE001
            pass
        st.compute = None
    if d is None:
        raise RuntimeError("CustomFeatureDefinition.create returned None")
    d.editCommandId = EDIT_CMD_ID
    if st.compute is None:
        h = _Compute()
        d.customFeatureCompute.add(h)
        st.compute = h
    st.definition = d
    out.update(id=d.id, name=d.defaultName, icon=d.iconFolder,
               editCommandId=d.editCommandId, host=host)
    st.register_log = getattr(st, "register_log", []) + [dict(out, at=round(time.time(), 3))]
    return out


HOST_NAME = "ArucoCfHost"
# Outside the repo, next to the dev bridge's own folder. Written by host().
HOST_DIR = os.path.join(os.path.expanduser("~"), "CADExporter", "_aruco_cf_host", HOST_NAME)
HOST_DEF_ID = "bamArucoProbe.ArucoTagV3"
# The manifest MUST carry an "id": without one, customFeatures.add fails with
# "3 : make params invalid" (measured 2026-10-07; ArucoMarkers.manifest has none).
HOST_GUID = "7d8c3677-e5b2-4094-b759-bee6cc6cbb0a"
HOST_MANIFEST = {
    "autodeskProduct": "Fusion360", "type": "addin", "id": HOST_GUID, "author": "probe",
    "description": {"": "Throwaway host for ArucoMarkers/aruco_cf_probe.py: its run() "
                        "registers the custom feature definition."},
    "version": "0.0.1", "runOnStartup": False, "supportedOS": "windows|mac",
    "editEnabled": True}
HOST_PY = """\"\"\"Throwaway host add-in for ArucoMarkers/aruco_cf_probe.py (research only).

Written by aruco_cf_probe.host(). Fusion ties a CustomFeatureDefinition to the
add-in whose run() created it, and that add-in's manifest needs an "id".
\"\"\"
import importlib
import sys
import traceback

import adsk.core

ARUCO = {aruco!r}


def run(context):
    app = adsk.core.Application.get()
    try:
        if ARUCO not in sys.path:
            sys.path.insert(0, ARUCO)
        import aruco_cf_probe
        importlib.reload(aruco_cf_probe)
        app.log(f"ArucoCfHost: {{aruco_cf_probe.register(def_id={def_id!r}, host='ArucoCfHost')}}")
    except Exception:  # noqa: BLE001
        app.log("ArucoCfHost failed:\\n" + traceback.format_exc())


def stop(context):
    try:
        import aruco_cf_probe
        aruco_cf_probe.unregister()
    except Exception:  # noqa: BLE001
        pass
"""


def write_host(folder=HOST_DIR):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, HOST_NAME + ".manifest"), "w", encoding="utf-8") as f:
        json.dump(HOST_MANIFEST, f, indent=4)
    with open(os.path.join(folder, HOST_NAME + ".py"), "w", encoding="utf-8") as f:
        f.write(HOST_PY.format(aruco=HERE, def_id=HOST_DEF_ID))
    return folder


def host(op="status", folder=""):
    """The throwaway host add-in whose run() calls register() (see register).

    op: status | start | stop. `start` writes the host (if `folder` is not
    given), links it (Scripts.addExisting) and runs it; `stop` stops AND
    unlinks it, so Fusion's Scripts and Add-Ins list ends as it began.
    """
    if not folder:
        folder = HOST_DIR
        if op == "start":
            write_host(folder)
    app = _app()
    scripts = app.scripts
    out = {"op": op}
    known = []
    for i in range(scripts.count):
        sc = scripts.item(i)
        try:
            if "aruco" in sc.name.lower() or "cad-exporter" in sc.folder.lower():
                known.append({"name": sc.name, "running": sc.isRunning, "folder": sc.folder,
                              "addin": sc.isAddIn, "location": int(sc.location)})
        except Exception:                          # noqa: BLE001
            pass
    out["known"] = known
    sc = scripts.itemByPath(folder) if folder else None
    if op == "start":
        if sc is None:
            sc = scripts.addExisting(folder)
            out["linked"] = sc is not None
        if sc is None:
            raise RuntimeError(f"could not link {folder}")
        out["ran"] = bool(sc.run(False)) if not sc.isRunning else "already running"
    elif op == "stop" and sc is not None:
        out["stopped"] = bool(sc.stop()) if sc.isRunning else "not running"
        try:
            out["unlinked"] = bool(sc.unlink())
        except Exception as exc:                   # noqa: BLE001
            out["unlinked"] = f"{type(exc).__name__}: {exc}"
    st = _st()
    out["register_log"] = getattr(st, "register_log", [])[-3:]
    out["def_id"] = getattr(st, "def_id", None)
    out["host_result"] = getattr(st, "host_result", None)
    return out


def unregister():
    """Undo register(): handlers off, edit command definition deleted."""
    st = _st()
    ui = _ui()
    out = {}
    if st.definition is not None and st.compute is not None:
        out["compute_removed"] = bool(st.definition.customFeatureCompute.remove(st.compute))
    st.compute = None
    if st.starting is not None:
        out["starting_removed"] = bool(ui.commandStarting.remove(st.starting))
    st.starting = None
    cmd_def = ui.commandDefinitions.itemById(EDIT_CMD_ID)
    if cmd_def is not None:
        out["edit_command_deleted"] = bool(cmd_def.deleteMe())
    st.edit_created = None
    cdef = ui.commandDefinitions.itemById(CREATE_CMD_ID)
    if cdef is not None:
        out["create_command_deleted"] = bool(cdef.deleteMe())
    st.create_created = None
    st.cmd_handlers = []
    st.recording = False
    st.auto = None
    return out


# ----------------------------------------------------------- the feature

def split_black(thickness_cm, sits):
    """(depth_m, raise_m) for a black layer `thickness_cm` thick."""
    t = thickness_cm / 100.0
    if sits == "raised":
        return 0.0, t
    if sits == "half":
        return t / 2.0, t / 2.0
    return t, 0.0


def _param(cf, pid):
    """CustomFeatureParameters.itemById RAISES ("3 : Invalid argument id") for
    an unknown id in 2705.1.15 -- the docs say it returns null."""
    try:
        return cf.parameters.itemById(pid)
    except RuntimeError:
        return None


def spec_of(cf):
    """TagSpec from the feature: parameters (numbers) + named value (settings)."""
    nv = cf.customNamedValues
    raw = nv.value("spec") if nv.isExistingValue("spec") else ""
    mid, black, size = (_param(cf, "marker_id"), _param(cf, "black"), _param(cf, "size"))
    info = {"named_spec": bool(raw),
            "param_marker_id": mid.value if mid else None,
            "param_black_mm": round(black.value * 10, 4) if black else None,
            "param_size_mm": round(size.value * 10, 4) if size else None}
    if not raw or mid is None or black is None or size is None:
        return None, info
    data = json.loads(raw)
    info["named_dictionary"] = data.get("dictionary")
    # A numeric parameter wins over the named value when both exist: Fusion
    # commits parameters synchronously with compute (apriltag3D issue #17).
    if _param(cf, "dict_index") is not None:
        names = list(aruco_tags.aruco_core.module("fiducials").ARUCO_DICTS)
        k = int(round(_param(cf, "dict_index").value))
        info["param_dict_index"] = k
        if 0 <= k < len(names):
            data["dictionary"] = names[k]
    data["marker_id"] = int(round(mid.value))
    data["depth_m"], data["raise_m"] = split_black(black.value, data.get("sits", "inlaid"))
    data["marker_length_m"] = size.value / 100.0
    warns = []
    spec = M().TagSpec.from_dict(data, warns.append, "custom feature")
    if warns:
        info["warnings"] = warns
    return spec, info


def base_feature_of(cf):
    for f in cf.features:
        bf = adsk.fusion.BaseFeature.cast(f)
        if bf is not None:
            return bf
    return None


def update_cells(bf, temp):
    """Swap the base feature's one source body; ALWAYS leave edit mode."""
    bf.startEdit()
    try:
        src = None
        try:
            src = list(bf.sourceBodies)[0]
        except Exception:                          # noqa: BLE001
            src = None
        if src is None:
            src = bf.bodies.item(0)
        return bool(bf.updateBody(src, temp))
    finally:
        bf.finishEdit()


def rebuild(cf, entry=None):
    """Recompute the cells of one tag feature from its face + parameters."""
    entry = entry if entry is not None else {}
    spec, info = spec_of(cf)
    entry.update(info)
    if spec is None:
        entry["skipped"] = "spec not written yet"
        return None
    dep = cf.dependencies.itemById("face")
    face = dep.entity if dep is not None else None
    if face is None:
        entry["error"] = "face dependency lost"
        return False
    data = json.loads(cf.customNamedValues.value("spec"))
    origin, x, y, z, w, h = aruco_tags.face_frame(face, float(data.get("rotate_deg", 0)))
    entry["face"] = {"vertices": face.vertices.count, "loops": face.loops.count,
                     "origin_mm": [round(v * 10, 3) for v in origin],
                     "w_mm": round(w * 10, 3), "h_mm": round(h * 10, 3)}
    temp = aruco_tags.cells_temp_body(spec, origin, x, y, z)
    bf = base_feature_of(cf)
    if bf is None:
        entry["error"] = "no base feature inside the custom feature"
        return False
    entry["marker_id"] = spec.marker_id
    entry["dictionary"] = spec.dictionary
    ok = update_cells(bf, temp)
    entry["updated"] = ok
    return ok


def _on_compute(args):
    st = _st()
    t0 = time.perf_counter()
    entry = {"seq": len(st.compute_log) + 1, "at": round(time.time(), 3)}
    ea = adsk.fusion.CustomFeatureEventArgs.cast(args)
    try:
        cf = ea.customFeature
        entry["feature"] = cf.name
        ok = rebuild(cf, entry)
        if ok is False:
            ea.computeStatus.statusMessages.addError("API_COMPUTE_ERROR", "")
    except Exception:                              # noqa: BLE001
        entry["error"] = traceback.format_exc()[-900:]
        try:
            ea.computeStatus.statusMessages.addError("API_COMPUTE_ERROR", "")
        except Exception:                          # noqa: BLE001
            pass
    finally:
        entry["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        st.compute_log.append(entry)
        del st.compute_log[:-200]


def make_tag(comp, face, tile, *, key="probe", dictionary="DICT_4X4_50",
             marker_id=3, size_mm=20.0, black_mm=0.6, sits="inlaid",
             quiet_modules=1.0, rotate_deg=0.0, wrap="both", dict_param=False,
             params="all", dep=True, named=True):
    """The tag as ONE custom feature. Returns (custom_feature, info).

    wrap="both": base feature + combine inside the custom feature (the case
    the docs support). wrap="bf": only the base feature; the combine stays a
    sibling after it (apriltag3D's workaround for issue #2).
    """
    st = _st()
    des = adsk.fusion.Design.cast(comp.parentDesign)
    depth_m, raise_m = split_black(black_mm / 10.0, sits)
    spec = M().TagSpec(dictionary=dictionary, marker_id=int(marker_id),
                       marker_length_m=size_mm / 1000.0, quiet_modules=quiet_modules,
                       depth_m=depth_m, raise_m=raise_m)
    origin, x, y, z, w, h = aruco_tags.face_frame(face, rotate_deg)
    info = {"face_before": {"vertices": face.vertices.count, "token_tail": face.entityToken[-12:]}}
    bf = comp.features.baseFeatures.add()
    bf.startEdit()
    try:
        comp.bRepBodies.add(aruco_tags.cells_temp_body(spec, origin, x, y, z), bf)
    finally:
        bf.finishEdit()
    cells = bf.bodies.item(0)
    cells.name = f"aruco cells {key}"
    comb = aruco_tags._pocket(comp, tile, cells) if depth_m > 0 else None
    white, black = (aruco_tags.appearance(des, aruco_tags.WHITE),
                    aruco_tags.appearance(des, aruco_tags.BLACK))
    if white:
        tile.appearance = white
    if black:
        cells.appearance = black
    info["face_valid_after_pocket"] = bool(face.isValid)
    try:
        info["face_after_pocket_vertices"] = face.vertices.count
    except Exception as exc:                       # noqa: BLE001
        info["face_after_pocket_vertices"] = f"{type(exc).__name__}: {exc}"

    inp = comp.features.customFeatures.createInput(st.definition)
    added = {}
    if params in ("all", "unitless"):
        added["marker_id"] = inp.addCustomParameter(
            "marker_id", "Marker ID", adsk.core.ValueInput.createByReal(float(marker_id)),
            "", True)
    if params in ("all", "length"):
        added["black"] = inp.addCustomParameter(
            "black", "Black thickness", adsk.core.ValueInput.createByString(f"{black_mm} mm"),
            "mm", True)
    if params == "all":
        added["size"] = inp.addCustomParameter(
            "size", "Marker size", adsk.core.ValueInput.createByString(f"{size_mm} mm"),
            "mm", True)
    info["addCustomParameter"] = {k: bool(v) for k, v in added.items()}
    if dict_param:
        names = list(aruco_tags.aruco_core.module("fiducials").ARUCO_DICTS)
        inp.addCustomParameter("dict_index", "Dictionary (index)",
                               adsk.core.ValueInput.createByReal(float(names.index(dictionary))),
                               "", True)
    if dep:
        # A face in a non-root component: the native face is refused
        # ("3 : Invalid argument value", measured); try its root-context proxy.
        try:
            info["addDependency"] = bool(inp.addDependency("face", face))
        except RuntimeError as exc:
            info["addDependency_native"] = str(exc)
            occs = des.rootComponent.allOccurrencesByComponent(comp)
            if face.assemblyContext is not None or occs.count == 0:
                raise
            proxy = face.createForAssemblyContext(occs.item(0))
            info["addDependency"] = bool(inp.addDependency("face", proxy))
            info["dependency_via"] = "proxy " + occs.item(0).fullPathName
    end = comb if (comb is not None and wrap == "both") else bf
    info["setStartAndEnd"] = bool(inp.setStartAndEndFeatures(bf, end))
    n0 = len(st.compute_log)
    t0 = time.perf_counter()
    cf = comp.features.customFeatures.add(inp)
    info["add_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    info["computes_during_add"] = len(st.compute_log) - n0
    if not named:
        return cf, info
    cf.customNamedValues.addOrSetValue("spec", json.dumps(
        {"dictionary": dictionary, "layout": "single", "quiet_modules": quiet_modules,
         "sits": sits, "rotate_deg": rotate_deg, "key": key}, sort_keys=True))
    cf.name = f"ArUco {key}: {spec.label()}"
    renamed = {}
    for pid in ("marker_id", "black", "size"):
        prm = _param(cf, pid)
        if prm is None:
            continue
        try:
            prm.name = f"aruco_{key}_{pid}"
            renamed[pid] = prm.name
        except Exception as exc:                   # noqa: BLE001
            renamed[pid] = f"{type(exc).__name__}: {exc}"
    info["param_rename"] = renamed
    dep_ = cf.dependencies.itemById("face")
    try:
        info["dependency_face_vertices"] = dep_.entity.vertices.count
    except Exception as exc:                       # noqa: BLE001
        info["dependency_face_vertices"] = f"{type(exc).__name__}: {exc}"
    return cf, info


# ---------------------------------------------------------- create command

def _on_create_created(args):
    st = _st()
    req = getattr(st, "create_request", None)
    entry = {"event": "create_created", "at": round(time.time(), 3),
             "has_request": req is not None}
    st.edit_log.append(entry)
    cmd = args.command
    cmd.isAutoExecute = True                     # no inputs: runs at once
    if req is None or _app().activeDocument != req.get("doc"):
        entry["skipped"] = "no request for the active document"
        return                                   # nothing to execute
    for kind, ev in (("create_execute", cmd.execute), ("create_destroy", cmd.destroy)):
        ev.add(_remember(_CmdEvent(kind)))


def create_via_command(doc, comp, face, tile, **kw):
    """make_tag() inside our hidden command's execute; pumps events till done."""
    st = _st()
    ui = _ui()
    st.create_request = {"doc": doc, "args": (comp, face, tile), "kw": kw}
    st.create_result = None
    try:
        ui.commandDefinitions.itemById(CREATE_CMD_ID).execute()
        t0 = time.perf_counter()
        while st.create_result is None and time.perf_counter() - t0 < 5.0:
            adsk.doEvents()
            time.sleep(0.005)
        res = st.create_result
    finally:
        st.create_request = None
    if res is None:
        raise RuntimeError("create command did not execute within 5 s")
    if not res["ok"]:
        raise RuntimeError(res["error"])
    return res["cf"], res["info"]


# ------------------------------------------------------------ edit command

def _remember(h):
    st = _st()
    st.cmd_handlers.append(h)
    del st.cmd_handlers[:-40]
    return h


EDIT_ROUTES = ("APIFeatureEditCommand", "FusionEditFeatureCommand", EDIT_CMD_ID)


def _on_starting(args):
    st = _st()
    if args.commandId in EDIT_ROUTES:
        # The double-clicked feature, captured before commandCreated (where
        # activeSelections is sometimes empty -- apriltag3D's lesson).
        try:
            ui = _ui()
            for i in range(ui.activeSelections.count):
                cf = adsk.fusion.CustomFeature.cast(ui.activeSelections.item(i).entity)
                if cf is not None:
                    st.pending_cf = cf
        except Exception:                          # noqa: BLE001
            pass
    if not st.recording:
        return
    try:
        sel = []
        ui = _ui()
        hit = False
        for i in range(ui.activeSelections.count):
            e = ui.activeSelections.item(i).entity
            sel.append(getattr(e, "objectType", "?"))
            cf = adsk.fusion.CustomFeature.cast(e)
            if cf is not None:
                st.pending_cf = cf
            bf = adsk.fusion.BaseFeature.cast(e)
            tok = getattr(st, "intercept", None)
            if bf is not None and tok and bf.entityToken == tok:
                hit = True
        rec = {"id": args.commandId, "selection": sel, "at": round(time.time(), 3)}
        if hit and args.commandId == "BaseFeatureActivate":
            # Fallback without custom features: Edit on OUR base feature opens
            # our dialog instead of Fusion's base-feature edit mode.
            args.isCanceled = True
            rec["canceled"] = True
        st.started.append(rec)
    except Exception:                              # noqa: BLE001
        st.started.append({"id": "?", "error": traceback.format_exc()[-300:]})


def _target_cf():
    st = _st()
    cf = st.pending_cf
    if cf is None or not cf.isValid:
        ui = _ui()
        for i in range(ui.activeSelections.count):
            cf = adsk.fusion.CustomFeature.cast(ui.activeSelections.item(i).entity)
            if cf is not None:
                break
    if cf is not None and cf.nativeObject is not None:
        cf = cf.nativeObject                     # proxy (child component) -> native
    return cf


def _on_edit_created(args):
    st = _st()
    entry = {"event": "created", "at": round(time.time(), 3)}
    st.edit_log.append(entry)
    try:
        cmd = args.command
        cf = _target_cf()
        st.edit_cf = cf
        entry["feature"] = cf.name if cf else None
        if st.auto is not None:
            # Scripted edit: no inputs, so the command executes on its own.
            cmd.isAutoExecute = True
            entry["auto"] = dict(st.auto)
        elif cf is not None:
            spec, _info = spec_of(cf)
            inputs = cmd.commandInputs
            p = cf.parameters
            inputs.addIntegerSpinnerCommandInput("marker_id", "Marker ID", 0, 2319, 1,
                                                 int(round(p.itemById("marker_id").value)))
            dd = inputs.addDropDownCommandInput(
                "dictionary", "Dictionary", adsk.core.DropDownStyles.TextListDropDownStyle)
            for d in aruco_tags.aruco_core.module("fiducials").ARUCO_DICTS:
                dd.listItems.add(d, spec is not None and d == spec.dictionary)
            inputs.addValueInput("size", "Marker size", "mm",
                                 adsk.core.ValueInput.createByString(p.itemById("size").expression))
            inputs.addValueInput("black", "Black thickness", "mm",
                                 adsk.core.ValueInput.createByString(p.itemById("black").expression))
        for kind, ev in (("activate", cmd.activate), ("execute", cmd.execute),
                         ("destroy", cmd.destroy)):
            ev.add(_remember(_CmdEvent(kind)))
    except Exception:                              # noqa: BLE001
        entry["error"] = traceback.format_exc()[-900:]


def _on_cmd_event(kind, args):
    st = _st()
    entry = {"event": kind, "at": round(time.time(), 3)}
    st.edit_log.append(entry)
    if kind.startswith("create_"):
        if kind == "create_execute":
            req = getattr(st, "create_request", None)
            try:
                if req is None:
                    raise RuntimeError("no create request")
                cf, info = make_tag(*req["args"], **req["kw"])
                st.create_result = {"ok": True, "cf": cf, "info": info}
            except Exception as exc:               # noqa: BLE001
                st.create_result = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                                    "where": traceback.format_exc()[-600:]}
                entry["error"] = st.create_result["where"]
                args.executeFailed = True
        return
    try:
        cf = getattr(st, "edit_cf", None)
        if cf is None:
            entry["error"] = "no custom feature to edit"
            return
        des = adsk.fusion.Design.cast(cf.parentComponent.parentDesign)
        if kind == "activate":
            tl = des.timeline
            st.restore_index = tl.markerPosition
            entry["rolled"] = bool(cf.timelineObject.rollTo(True))
            st.rolled = True
            if st.auto is None:
                # keep the roll out of the step each preview aborts (Custom Pocket)
                args.command.beginStep()
        elif kind == "execute":
            if st.auto is not None:
                vals = dict(st.auto)
            else:
                inputs = args.command.commandInputs
                vals = {"marker_id": inputs.itemById("marker_id").value,
                        "dictionary": inputs.itemById("dictionary").selectedItem.name,
                        "size": inputs.itemById("size").expression,
                        "black": inputs.itemById("black").expression}
            n0 = len(st.compute_log)
            p = cf.parameters
            if "dictionary" in vals:
                data = json.loads(cf.customNamedValues.value("spec") or "{}")
                data["dictionary"] = vals["dictionary"]
                cf.customNamedValues.addOrSetValue("spec", json.dumps(data, sort_keys=True))
                if _param(cf, "dict_index") is not None:
                    names = list(aruco_tags.aruco_core.module("fiducials").ARUCO_DICTS)
                    _param(cf, "dict_index").value = float(names.index(vals["dictionary"]))
            if "size" in vals:
                p.itemById("size").expression = str(vals["size"])
            if "black" in vals:
                p.itemById("black").expression = str(vals["black"])
            if "marker_id" in vals:
                p.itemById("marker_id").value = float(vals["marker_id"])
            entry["computes_during_writes"] = len(st.compute_log) - n0
            entry["wrote"] = vals
            spec, _info = spec_of(cf)
            if spec is not None:
                key = json.loads(cf.customNamedValues.value("spec") or "{}").get("key", "")
                cf.name = f"ArUco {key}: {spec.label()}"
            if getattr(st, "rolled", False):
                tl = des.timeline
                tl.moveToEnd() if st.restore_index >= tl.count else \
                    tl.item(st.restore_index - 1).rollTo(False)
                st.rolled = False
            entry["computes_by_end"] = len(st.compute_log) - n0
        elif kind == "destroy":
            st.pending_cf = None
    except Exception:                              # noqa: BLE001
        entry["error"] = traceback.format_exc()[-900:]


# -------------------------------------------------------------- measuring

def _health(des):
    out = []
    tl = des.timeline
    for i in range(tl.count):
        item = tl.item(i)
        ent = item.entity
        out.append({"i": i, "name": item.name,
                    "type": (getattr(ent, "objectType", None) or "").split("::")[-1],
                    "health": int(item.healthState) if hasattr(item, "healthState") else None,
                    "group": bool(item.isGroup),
                    "suppressed": bool(item.isSuppressed)})
    return out


def _bodies(comp):
    out = {}
    for b in comp.bRepBodies:
        bb = b.boundingBox
        out[b.name] = {"z_mm": [round(bb.minPoint.z * 10, 4), round(bb.maxPoint.z * 10, 4)],
                       "x_mm": [round(bb.minPoint.x * 10, 3), round(bb.maxPoint.x * 10, 3)],
                       "y_mm": [round(bb.minPoint.y * 10, 3), round(bb.maxPoint.y * 10, 3)],
                       "volume_mm3": round(b.volume * 1000, 3), "faces": b.faces.count,
                       "lumps": b.lumps.count}
    return out


def _feature_state(cf):
    out = {"name": cf.name, "health": int(cf.healthState),
           "message": cf.errorOrWarningMessage,
           "timeline_index": cf.timelineObject.index,
           "features": [f.name for f in cf.features],
           "params": {cf.parameters.item(i).id: cf.parameters.item(i).expression
                      for i in range(cf.parameters.count)},
           "param_names": {cf.parameters.item(i).id: cf.parameters.item(i).name
                           for i in range(cf.parameters.count)},
           "named": {cf.customNamedValues.idByIndex(i): cf.customNamedValues.value(
               cf.customNamedValues.idByIndex(i)) for i in range(cf.customNamedValues.count)},
           "dependencies": [cf.dependencies.item(i).id for i in range(cf.dependencies.count)]}
    try:
        out["definition"] = cf.definition.id if cf.definition else None
    except Exception:                              # noqa: BLE001
        out["definition"] = "?"
    return out


def _capture(name, view="iso"):
    path = os.path.join(MEDIA, name)
    os.makedirs(MEDIA, exist_ok=True)
    vp = _app().activeViewport
    cam = vp.camera
    cam.viewOrientation = (adsk.core.ViewOrientations.IsoTopRightViewOrientation
                           if view == "iso" else adsk.core.ViewOrientations.TopViewOrientation)
    cam.isFitView = True
    vp.camera = cam
    vp.refresh()
    return {"ok": bool(vp.saveAsImageFile(path, 1200, 800)), "path": path}


# ------------------------------------------------------------------- run

class Busy(RuntimeError):
    pass


def quiet():
    """(is_idle, active command id): only run when Fusion is idle."""
    cmd = _ui().activeCommand
    return cmd == "SelectCommand", cmd


INTENTS = {"hybrid": "HybridDesignIntentType", "part": "PartDesignIntentType",
           "assembly": "AssemblyDesignIntentType"}


def run(intent="", wrap="both", edit="1", undo="1", capture="0", dict_param="0",
        delete="1", edit_via="APIFeatureEditCommand", mode="scenario", only="",
        via="command", where="root", pause_s="0"):
    """The whole experiment, in a new document that is always closed again."""
    if mode == "matrix":
        return matrix(intent=intent, only=only, via=via, where=where)
    if mode == "fallback":
        return fallback()
    if mode == "demo":
        return demo(intent=intent)
    st = _st()
    idle, cmd = quiet()
    if not idle:
        raise Busy(f"Fusion is busy (activeCommand={cmd!r}); not running")
    app = _app()
    ui = _ui()
    prev = app.activeDocument
    out = {"fusion": app.version, "previous_document": prev.name if prev else None,
           "steps": []}
    doc = None
    t_run = time.perf_counter()
    st.compute_log.clear()
    st.edit_log.clear()
    st.started.clear()

    def step(name, fn):
        t0 = time.perf_counter()
        n0 = len(st.compute_log)
        rec = {"step": name}
        try:
            rec["value"] = fn()
            rec["ok"] = True
        except Exception as exc:                   # noqa: BLE001
            rec["ok"] = False
            rec["error"] = f"{type(exc).__name__}: {exc}"
            rec["where"] = traceback.format_exc().splitlines()[-4:]
        rec["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        rec["computes"] = [dict(e) for e in st.compute_log[n0:]]
        out["steps"].append(rec)
        return rec.get("value")

    s = {}
    try:
        step("register", lambda: register() if st.definition is None else
             {"kept": st.def_id, "host": getattr(st, "host", "")})
        doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
        des = adsk.fusion.Design.cast(app.activeProduct)
        des.designType = adsk.fusion.DesignTypes.ParametricDesignType

        def set_intent():
            before = int(des.designIntent)
            if intent:
                des.designIntent = getattr(adsk.fusion.DesignIntentTypes, INTENTS[intent])
            return {"default": before, "now": int(des.designIntent),
                    "names": {int(getattr(adsk.fusion.DesignIntentTypes, v)): k
                              for k, v in INTENTS.items()}}
        step("design_intent", set_intent)
        root = des.rootComponent

        def component():
            if intent == "assembly" or where == "child":
                occ = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
                occ.component.name = "Tag part"
                return occ.component
            return root
        comp = step("component", component) or root
        s["comp"] = comp

        def tile():
            sk = comp.sketches.add(comp.xYConstructionPlane)
            sk.name = "tile outline"
            lines = sk.sketchCurves.sketchLines.addTwoPointRectangle(P(-2, -2, 0), P(2, 2, 0))
            dim = sk.sketchDimensions.addDistanceDimension(
                lines.item(0).startSketchPoint, lines.item(0).endSketchPoint,
                adsk.fusion.DimensionOrientations.HorizontalDimensionOrientation, P(0, -3, 0))
            ext = comp.features.extrudeFeatures.addSimple(
                sk.profiles.item(0), adsk.core.ValueInput.createByString("3 mm"),
                adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
            ext.bodies.item(0).name = "tag tile"
            s.update(ext=ext, dim=dim, tile=ext.bodies.item(0), sketch=sk)
            return {"width_param": dim.parameter.name,
                    "thickness_param": ext.extentOne.distance.name}
        step("tile", tile)

        def tag():
            face = s["ext"].endFaces.item(0)
            make = make_tag if via == "direct" else \
                (lambda *a, **k: create_via_command(doc, *a, **k))
            cf, info = make(comp, face, s["tile"], wrap=wrap,
                            dict_param=str(dict_param) == "1")
            s["cf"] = cf
            info["timeline"] = _health(des)
            info["feature"] = _feature_state(cf)
            info["bodies"] = _bodies(comp)
            return info
        step("custom_feature_tag", tag)

        def hole():
            # A feature AFTER the tag, sketched on the pocketed top face.
            top = max((f for f in s["tile"].faces if isinstance(f.geometry, adsk.core.Plane)
                       and f.evaluator.getNormalAtPoint(f.pointOnFace)[1].z > 0.99),
                      key=lambda f: f.pointOnFace.z)
            sk = comp.sketches.add(top)
            sk.name = "hole after tag"
            c = sk.modelToSketchSpace(P(1.6, 1.6, top.pointOnFace.z))
            sk.sketchCurves.sketchCircles.addByCenterRadius(c, 0.15)
            # A sketch on a face projects that face's edges (the pockets), so
            # profiles.item(0) may be a cell: take the circle's profile.
            import math
            prof = min(sk.profiles, key=lambda pr: abs(
                pr.areaProperties().area - math.pi * 0.15 ** 2))
            ei = comp.features.extrudeFeatures.createInput(
                prof, adsk.fusion.FeatureOperations.CutFeatureOperation)
            ei.setTwoSidesExtent(adsk.fusion.ThroughAllExtentDefinition.create(),
                                 adsk.fusion.ThroughAllExtentDefinition.create())
            h = comp.features.extrudeFeatures.add(ei)
            s["hole"] = h
            return {"health": int(h.healthState), "tile_faces": s["tile"].faces.count}
        step("hole_after_tag", hole)
        if str(capture) == "1":
            step("capture_created", lambda: _capture("01_created_id3.png"))
        if float(pause_s) > 0:
            def pause():
                # Let Fusion repaint (timeline, browser) for an outside capture.
                ui.activeSelections.clear()
                t0 = time.perf_counter()
                while time.perf_counter() - t0 < float(pause_s):
                    adsk.doEvents()
                    time.sleep(0.02)
                return float(pause_s)
            step("pause_for_screenshot", pause)

        def state(label):
            def fn():
                return {"feature": _feature_state(s["cf"]), "bodies": _bodies(comp),
                        "hole": int(s["hole"].healthState) if s.get("hole") else None,
                        "unhealthy": [t for t in _health(des) if t["health"] not in (0, None)],
                        "timeline_count": des.timeline.count}
            return step(label, fn)

        def set_param(pid, expr):
            def fn():
                prm = s["cf"].parameters.itemById(pid)
                prm.expression = expr
                return {pid: prm.expression}
            return fn

        # (a) the user edits the tag's parameters (Parameters dialog / API)
        step("param_marker_id_7", set_param("marker_id", "7"))
        state("after_marker_id_7")
        step("param_black_1.2mm", set_param("black", "1.2 mm"))
        state("after_black_1.2")
        if str(capture) == "1":
            step("capture_id7", lambda: _capture("02_param_id7_black1.2.png"))

        # (b) upstream changes move / resize the face
        def thickness():
            s["ext"].extentOne.distance.expression = "5 mm"
            return s["ext"].extentOne.distance.expression
        step("upstream_thickness_5mm", thickness)
        state("after_thickness_5mm")

        def width():
            s["dim"].parameter.expression = "60 mm"
            return s["dim"].parameter.expression
        step("upstream_width_60mm", width)
        state("after_width_60mm")
        if str(capture) == "1":
            step("capture_upstream", lambda: _capture("03_upstream_5mm_60mm.png"))

        if str(undo) == "1":
            def undo_last():
                ucmd = ui.commandDefinitions.itemById("UndoCommand")
                if ucmd is None:
                    return {"error": "no UndoCommand"}
                before = _bodies(comp)
                ucmd.execute()
                for _ in range(40):
                    adsk.doEvents()
                    if s["dim"].parameter.expression != "60 mm":
                        break
                    time.sleep(0.02)
                return {"width_now": s["dim"].parameter.expression,
                        "cells_before": before.get("aruco cells probe"),
                        "cells_after": _bodies(comp).get("aruco cells probe")}
            step("undo_width", undo_last)
            state("after_undo")

        # recompute churn: does compute fire with nothing changed?
        step("compute_all", lambda: bool(des.computeAll()))

        def roll():
            s["cf"].timelineObject.rollTo(True)
            des.timeline.moveToEnd()
            return des.timeline.markerPosition
        step("roll_before_and_back", roll)

        if str(edit) == "1":
            def edit_feature():
                # Edit Feature on the selected custom feature: first through
                # Fusion's own command ids (what double-click would run), then
                # our edit command directly. Scripted values (st.auto).
                st.recording = True
                st.auto = {"marker_id": 11, "dictionary": "DICT_5X5_50"}
                routes = []
                for cid in [c for c in (edit_via, "APIFeatureEditCommand", EDIT_CMD_ID) if c]:
                    if cid in [r["command"] for r in routes]:
                        continue
                    if ui.commandDefinitions.itemById(cid) is None:
                        routes.append({"command": cid, "missing": True})
                        continue
                    n0, s0 = len(st.edit_log), len(st.started)
                    ui.activeSelections.clear()
                    target = s["cf"]
                    if comp != root:       # a child component's feature: select its proxy
                        target = s["cf"].createForAssemblyContext(
                            root.allOccurrencesByComponent(comp).item(0))
                    ui.activeSelections.add(target)
                    ui.commandDefinitions.itemById(cid).execute()
                    t0 = time.perf_counter()
                    while time.perf_counter() - t0 < 3.0:
                        adsk.doEvents()
                        if any(e["event"] == "destroy" for e in st.edit_log[n0:]):
                            break
                        time.sleep(0.01)
                    r = {"command": cid, "started": st.started[s0:],
                         "edit_log": st.edit_log[n0:], "active_after": ui.activeCommand}
                    if ui.activeCommand != "SelectCommand":
                        r["terminated"] = bool(ui.terminateActiveCommand())
                    routes.append(r)
                    if any(e["event"] == "execute" for e in r["edit_log"]):
                        break
                return routes
            try:
                step("edit_feature_via_fusion", edit_feature)
            finally:
                st.recording = False
                st.auto = None
            state("after_edit")
            if str(capture) == "1":
                step("capture_edit", lambda: _capture("04_edit_5x5_id11.png"))
            if str(undo) == "1":
                def undo_edit():
                    before = _feature_state(s["cf"])
                    ui.commandDefinitions.itemById("UndoCommand").execute()
                    t0 = time.perf_counter()
                    while time.perf_counter() - t0 < 2.0:
                        adsk.doEvents()
                        if s["cf"].parameters.itemById("marker_id").expression != "11":
                            break
                        time.sleep(0.01)
                    return {"params_before": before["params"], "named_before": before["named"],
                            "params_after": _feature_state(s["cf"])["params"],
                            "named_after": _feature_state(s["cf"])["named"],
                            "cells_after": _bodies(comp).get("aruco cells probe")}
                step("undo_edit", undo_edit)
                state("after_undo_edit")

        if str(capture) == "1":
            step("capture_final", lambda: _capture("05_final.png"))
        if str(delete) == "1":
            def delete_cf():
                ok = s["cf"].deleteMe()
                return {"deleted": bool(ok), "bodies": _bodies(comp),
                        "params_left": [des.allParameters.item(i).name
                                        for i in range(des.allParameters.count)],
                        "hole": int(s["hole"].healthState),
                        "timeline": _health(des)}
            step("delete_custom_feature", delete_cf)
    finally:
        st.recording = False
        st.auto = None
        try:
            des_now = adsk.fusion.Design.cast(app.activeProduct)
            if des_now is not None and adsk.fusion.BaseFeature.cast(des_now.activeEditObject):
                adsk.fusion.BaseFeature.cast(des_now.activeEditObject).finishEdit()
        except Exception:                          # noqa: BLE001
            pass
        try:
            if doc is not None and app.activeDocument == doc and \
                    ui.activeCommand not in ("SelectCommand", None, ""):
                out["terminated_leftover_command"] = ui.activeCommand
                ui.terminateActiveCommand()
        except Exception:                          # noqa: BLE001
            pass
        if doc is not None:
            try:
                doc.close(False)
                out["closed"] = True
            except Exception as exc:               # noqa: BLE001
                out["closed"] = f"{type(exc).__name__}: {exc}"
        try:
            if prev is not None and prev.isValid:
                prev.activate()
            out["reactivated"] = prev.name if prev else None
        except Exception as exc:                   # noqa: BLE001
            out["reactivated"] = f"{type(exc).__name__}: {exc}"
        out["total_s"] = round(time.perf_counter() - t_run, 2)
    return out


MATRIX = [
    # name, wrap, params, dep
    ("bf_bare", "bf", "none", False),
    ("bf_unitless", "bf", "unitless", False),
    ("bf_length", "bf", "length", False),
    ("bf_dep", "bf", "none", True),
    ("both_bare", "both", "none", False),
    ("both_all_dep", "both", "all", True),
]


def _cleanup(app, doc, prev, out):
    """Leave no trace: finish any base-feature edit, close OUR doc, re-activate."""
    try:
        des_now = adsk.fusion.Design.cast(app.activeProduct)
        if des_now is not None and adsk.fusion.BaseFeature.cast(des_now.activeEditObject):
            adsk.fusion.BaseFeature.cast(des_now.activeEditObject).finishEdit()
    except Exception:                              # noqa: BLE001
        pass
    if doc is not None:
        try:
            doc.close(False)
            out["closed"] = True
        except Exception as exc:                   # noqa: BLE001
            out["closed"] = f"{type(exc).__name__}: {exc}"
    try:
        if prev is not None and prev.isValid:
            prev.activate()
        out["reactivated"] = prev.name if prev else None
    except Exception as exc:                       # noqa: BLE001
        out["reactivated"] = f"{type(exc).__name__}: {exc}"


def matrix(intent="", only="", via="command", where="root"):
    """One tile per variant in ONE probe document: which wrap survives add()?"""
    st = _st()
    idle, cmd = quiet()
    if not idle:
        raise Busy(f"Fusion is busy (activeCommand={cmd!r}); not running")
    app = _app()
    prev = app.activeDocument
    out = {"previous_document": prev.name if prev else None, "variants": []}
    doc = None
    t_run = time.perf_counter()
    try:
        doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
        des = adsk.fusion.Design.cast(app.activeProduct)
        des.designType = adsk.fusion.DesignTypes.ParametricDesignType
        if intent:
            des.designIntent = getattr(adsk.fusion.DesignIntentTypes, INTENTS[intent])
        out["intent"] = int(des.designIntent)
        root = des.rootComponent
        comp = root
        if intent == "assembly" or where == "child":
            comp = root.occurrences.addNewComponent(adsk.core.Matrix3D.create()).component
            comp.name = "Tag part"
        out["where"] = "root" if comp == root else "child"
        for k, (name, wrap, params, dep) in enumerate(MATRIX):
            if only and name not in only.split(","):
                continue
            rec = {"variant": name, "wrap": wrap, "params": params, "dep": dep}
            n0 = len(st.compute_log)
            t0 = time.perf_counter()
            try:
                sk = comp.sketches.add(comp.xYConstructionPlane)
                x0 = k * 6.0
                sk.sketchCurves.sketchLines.addTwoPointRectangle(P(x0 - 2, -2, 0),
                                                                 P(x0 + 2, 2, 0))
                ext = comp.features.extrudeFeatures.addSimple(
                    sk.profiles.item(0), adsk.core.ValueInput.createByString("3 mm"),
                    adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
                tile = ext.bodies.item(0)
                tile.name = f"tile {name}"
                make = (make_tag if via == "direct" else
                        lambda *a, **k: create_via_command(doc, *a, **k))
                cf, info = make(comp, ext.endFaces.item(0), tile, key=name, wrap=wrap,
                                params=params, dep=dep)
                rec.update(ok=True, info=info, health=int(cf.healthState),
                           features=[f.name for f in cf.features])
            except Exception as exc:               # noqa: BLE001
                rec.update(ok=False, error=f"{type(exc).__name__}: {exc}")
            rec["ms"] = round((time.perf_counter() - t0) * 1000, 1)
            rec["computes"] = [dict(e) for e in st.compute_log[n0:]]
            out["variants"].append(rec)
        out["timeline"] = _health(des)
    finally:
        _cleanup(app, doc, prev, out)
        out["total_s"] = round(time.perf_counter() - t_run, 2)
    return out


def demo(intent=""):
    """For a PERSON to try: a new document with one custom-feature tag and a
    hole after it, left OPEN (unsaved). Double-click the ArUco node in the
    timeline -> the probe's edit dialog. Close the document without saving
    when done. Needs the host add-in running (host(op="start")).
    The probe itself never calls this."""
    st = _st()
    idle, cmd = quiet()
    if not idle:
        raise Busy(f"Fusion is busy (activeCommand={cmd!r}); not running")
    app = _app()
    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    des = adsk.fusion.Design.cast(app.activeProduct)
    des.designType = adsk.fusion.DesignTypes.ParametricDesignType
    if intent:
        des.designIntent = getattr(adsk.fusion.DesignIntentTypes, INTENTS[intent])
    comp = des.rootComponent
    if intent == "assembly":
        comp = comp.occurrences.addNewComponent(adsk.core.Matrix3D.create()).component
        comp.name = "Tag part"
    sk = comp.sketches.add(comp.xYConstructionPlane)
    sk.name = "tile outline"
    sk.sketchCurves.sketchLines.addTwoPointRectangle(P(-2, -2, 0), P(2, 2, 0))
    ext = comp.features.extrudeFeatures.addSimple(
        sk.profiles.item(0), adsk.core.ValueInput.createByString("3 mm"),
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    ext.bodies.item(0).name = "tag tile"
    st.auto = None
    cf, _info = make_tag(comp, ext.endFaces.item(0), ext.bodies.item(0), key="demo")
    return {"document": doc.name, "feature": cf.name, "intent": int(des.designIntent)}


def fallback():
    """Today's tag (base feature + combine + timeline group), no custom feature:
    what does Fusion's base-feature Edit do, and can commandStarting redirect it?"""
    st = _st()
    idle, cmd = quiet()
    if not idle:
        raise Busy(f"Fusion is busy (activeCommand={cmd!r}); not running")
    app = _app()
    ui = _ui()
    prev = app.activeDocument
    out = {"previous_document": prev.name if prev else None}
    doc = None
    t_run = time.perf_counter()
    try:
        doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
        des = adsk.fusion.Design.cast(app.activeProduct)
        des.designType = adsk.fusion.DesignTypes.ParametricDesignType
        root = des.rootComponent
        sk = root.sketches.add(root.xYConstructionPlane)
        sk.sketchCurves.sketchLines.addTwoPointRectangle(P(-2, -2, 0), P(2, 2, 0))
        ext = root.features.extrudeFeatures.addSimple(
            sk.profiles.item(0), adsk.core.ValueInput.createByString("3 mm"),
            adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        rec = aruco_tags.add_tag(ext.endFaces.item(0), tile_body=ext.bodies.item(0),
                                 marker_id=3, key="fb", marker_length_mm=20.0)
        bf = root.features.baseFeatures.item(0)
        out["timeline"] = _health(des)
        out["group"] = rec.get("fusion", {}).get("group") is not None

        def run_edit(intercept):
            st.recording = True
            st.intercept = bf.entityToken if intercept else None
            s0 = len(st.started)
            ui.activeSelections.clear()
            ui.activeSelections.add(bf)
            ui.commandDefinitions.itemById("BaseFeatureActivate").execute()
            t0 = time.perf_counter()
            while time.perf_counter() - t0 < 1.5:
                adsk.doEvents()
                time.sleep(0.01)
            edit_obj = des.activeEditObject
            r = {"started": st.started[s0:], "active_command": ui.activeCommand,
                 "active_edit_object": getattr(edit_obj, "objectType", str(edit_obj)),
                 "in_base_feature_edit": adsk.fusion.BaseFeature.cast(edit_obj) is not None}
            if r["in_base_feature_edit"]:
                bf.finishEdit()
                r["finished_edit"] = True
            if ui.activeCommand != "SelectCommand":
                r["terminated"] = bool(ui.terminateActiveCommand())
            st.recording = False
            st.intercept = None
            return r
        out["edit_plain"] = run_edit(False)
        out["edit_intercepted"] = run_edit(True)
    finally:
        st.recording = False
        st.intercept = None
        _cleanup(app, doc, prev, out)
        out["total_s"] = round(time.perf_counter() - t_run, 2)
    return out


def commands(pattern="edit|configure|undo"):
    """Read-only: command definitions whose id or name matches `pattern`."""
    import re
    rx = re.compile(pattern, re.I)
    ui = _ui()
    hits = []
    for i in range(ui.commandDefinitions.count):
        d = ui.commandDefinitions.item(i)
        try:
            text = f"{d.id} {d.name}"
        except Exception:                          # noqa: BLE001
            continue
        if rx.search(text):
            hits.append({"id": d.id, "name": d.name})
    return {"count": len(hits), "hits": hits, "active": ui.activeCommand}


def record(on="1"):
    """Log every command id that starts (with its selection) until switched
    off -- for a person checking what a double-click fires. Read it with
    status(); off clears nothing, on clears the previous log."""
    st = _st()
    if str(on) in ("1", "true"):
        st.started.clear()
        st.recording = True
    else:
        st.recording = False
    return {"recording": st.recording, "started": list(st.started)}


def status():
    st = _st()
    return {"registered": st.definition is not None, "compute_handler": st.compute is not None,
            "edit_handler": st.edit_created is not None, "recording": st.recording,
            "compute_log_tail": st.compute_log[-5:], "edit_log_tail": st.edit_log[-8:],
            "started_tail": st.started[-12:],
            "active_command": _ui().activeCommand,
            "documents": [{"name": d.name, "saved": bool(d.isSaved),
                           "custom_features": _count_cf(d)}
                          for d in _app().documents]}


def _count_cf(doc):
    """How many of OUR custom features a document holds (leak check)."""
    try:
        des = adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
        n = 0
        for comp in des.allComponents:
            for i in range(comp.features.customFeatures.count):
                d = comp.features.customFeatures.item(i).definition
                if d is not None and d.id.startswith("bamArucoProbe."):
                    n += 1
        return n
    except Exception as exc:                       # noqa: BLE001
        return f"{type(exc).__name__}"
