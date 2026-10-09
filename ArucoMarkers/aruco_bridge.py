"""Dev-only file bridge for the ArUco Tags add-in. Off unless a flag exists.

Same shape as CADExporter's `bam_dev_bridge` (which Zach approved on
2026-09-20): a daemon thread watches a folder and touches no Fusion API but
`fireCustomEvent`; the handler runs on Fusion's main thread, re-imports this
add-in's `aruco_*` modules so an edit is live without a restart, and runs ONE
action from a fixed allow-list. There is no "exec this file" action.

    %USERPROFILE%/CADExporter/_aruco_bridge/ENABLED      the switch
                                           requests/<id>.json   {"action", "args"}
                                           responses/<id>.json  {"ok", "result"|"error"}
                                           heartbeat.json

Client: `python tools/aruco_bridge.py <action> [--arg k=v ...]`.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import threading
import time
import traceback
import types
from pathlib import Path

import adsk.core

EVENT_ID = "bamArucoBridgeEvent"
ROOT = Path(os.path.expanduser("~")) / "CADExporter" / "_aruco_bridge"
STATE_KEY = "_aruco_bridge_state"           # outside every module we reload
RELOADABLE = ("aruco_core", "aruco_tags", "aruco_probe", "aruco_demo",
              "aruco_export", "aruco_feature", "aruco_extrude_cmd", "aruco_extrude_lab",
              "aruco_preview", "aruco_heights_lab",
              "aruco_ids_preview", "aruco_ids_test",
              "aruco_commands", "aruco_actions")


def _state():
    st = sys.modules.get(STATE_KEY)
    if st is None:
        st = types.ModuleType(STATE_KEY)
        st.thread = None
        st.stop = None
        st.event = None
        st.handler = None
        st.generation = 0
        st.busy = False
        sys.modules[STATE_KEY] = st
    return st


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
    for _ in range(20):
        try:
            os.replace(str(tmp), str(path))
            return
        except OSError:
            time.sleep(0.05)


def _reload():
    for name in RELOADABLE:
        mod = sys.modules.get(name)
        if mod is not None:
            importlib.reload(mod)
    import aruco_core
    aruco_core.load(fresh=True)        # the pure core too, under its alias


def _user_dialog():
    """The id of ANY command the USER has open, or ''. A dialog open in a
    document this add-in created (lab_ui_open's hand-check document) is an
    agent's, not his.

    2026-10-08, Fusion crashed (CER stack: our custom event -> Python ->
    processEvents -> SelectionCommandInput::onEnd -> DcAPIFeature::faces):
    an agent's long request pumped Fusion's event loop while a dialog's
    selection ended on an ArUco Extrude node. Requests now wait while ANY
    command is open in a document that is not the add-in's own."""
    try:
        app = adsk.core.Application.get()
        cmd = app.userInterface.activeCommand or ""
        if cmd in ("", "SelectCommand"):
            return ""
        doc = app.activeDocument
        mine = getattr(sys.modules.get("_aruco_created_docs"), "ids", set())
        if doc is not None and getattr(doc, "creationId", id(doc)) in mine:
            return ""
        return cmd
    except Exception:                              # noqa: BLE001
        return ""


def _active_doc_name():
    try:
        doc = adsk.core.Application.get().activeDocument
        return doc.name if doc is not None else None
    except Exception:                              # noqa: BLE001
        return None


def _dispatch(request_id: str) -> None:
    st = _state()
    req_path = ROOT / "requests" / f"{request_id}.json"
    resp_path = ROOT / "responses" / f"{request_id}.json"
    started = time.time()
    try:
        req = json.loads(req_path.read_text(encoding="utf-8"))
    except Exception as exc:                       # noqa: BLE001
        _write_json(resp_path, {"ok": False, "error": f"unreadable request: {exc}"})
        return
    finally:
        try:
            os.replace(str(req_path), str(ROOT / "done" / req_path.name))
        except OSError:
            pass
    if st.busy:
        # A running action pumped Fusion's event loop (adsk.doEvents, e.g. a
        # scripted command waiting to execute) and this request's event got
        # dispatched INSIDE it. Running it now would reload modules and maybe
        # switch documents under the running action: refuse, the client
        # retries (several agents share this bridge).
        _write_json(resp_path, {"ok": False, "busy": True,
                                "error": "bridge busy: another request is running -- retry"})
        return
    held = _user_dialog() if req.get("action") != "ping" else ""
    if held:
        # Zach has one of OUR dialogs open (2026-10-08: he works in Fusion
        # while agents edit this add-in). Every request reloads the dialog's
        # modules, so a request now would swap half-edited code under his
        # open dialog: answer without reloading or running anything.
        _write_json(resp_path, {"ok": False, "user_dialog": held, "busy": True,
                                "error": f"the user has the '{held}' dialog open -- not "
                                         "reloading code under it; retry when Fusion is idle",
                                "elapsed_s": round(time.time() - started, 3)})
        return
    st.busy = True
    doc0 = _active_doc_name()
    try:
        _reload()
        import aruco_actions
        action = aruco_actions.ACTIONS.get(req.get("action"))
        if action is None:
            raise ValueError(f"unknown action {req.get('action')!r}; known: "
                             f"{sorted(aruco_actions.ACTIONS)}")
        result = action(**(req.get("args") or {}))
        out = {"ok": True, "result": result}
        doc1 = _active_doc_name()
        if doc1 != doc0:
            # a long action pumps events; the user can switch tabs meanwhile
            # and anything after that acted on whatever was in front
            out["warning"] = (f"the active document changed during the request: "
                              f"{doc0!r} -> {doc1!r}")
    except Exception as exc:                       # noqa: BLE001
        out = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
               "traceback": traceback.format_exc()}
    finally:
        st.busy = False
    out["elapsed_s"] = round(time.time() - started, 3)
    _write_json(resp_path, out)


class _Handler(adsk.core.CustomEventHandler):
    def notify(self, args):
        try:
            _dispatch(args.additionalInfo)
        except Exception:                          # noqa: BLE001
            pass


def _worker(stop: threading.Event, generation: int) -> None:
    app = adsk.core.Application.get()
    fired = set()
    while not stop.is_set():
        try:
            _write_json(ROOT / "heartbeat.json",
                        {"generation": generation, "beat_at": time.time(),
                         "addin_dir": os.path.dirname(os.path.abspath(__file__)),
                         "busy": _state().busy, "pid": os.getpid()})
            for path in sorted((ROOT / "requests").glob("*.json")):
                if path.stem in fired:
                    continue
                fired.add(path.stem)
                app.fireCustomEvent(EVENT_ID, path.stem)
        except Exception:                          # noqa: BLE001
            pass
        stop.wait(0.25)


def arm() -> str:
    """Start the bridge if ENABLED exists. Returns a one-line status."""
    if not (ROOT / "ENABLED").exists():
        return "aruco bridge off (no ENABLED flag)"
    st0 = _state()
    if st0.thread is not None and st0.thread.is_alive():
        # run() again while armed (e.g. the add-in started for real through
        # the bridge itself): keep the bridge that is serving this request
        return f"aruco bridge already armed (generation {st0.generation})"
    disarm()
    for sub in ("requests", "responses", "done"):
        (ROOT / sub).mkdir(parents=True, exist_ok=True)
    st = _state()
    app = adsk.core.Application.get()
    st.event = app.registerCustomEvent(EVENT_ID)
    st.handler = _Handler()
    st.event.add(st.handler)
    st.generation += 1
    st.stop = threading.Event()
    st.thread = threading.Thread(target=_worker, args=(st.stop, st.generation),
                                 daemon=True, name="aruco-bridge")
    st.thread.start()
    return f"aruco bridge armed (generation {st.generation})"


def disarm() -> None:
    st = _state()
    if st.stop is not None:
        st.stop.set()
    if st.thread is not None:
        st.thread.join(timeout=2)
    app = adsk.core.Application.get()
    if st.event is not None and st.handler is not None:
        try:
            st.event.remove(st.handler)
        except Exception:                          # noqa: BLE001
            pass
    try:
        app.unregisterCustomEvent(EVENT_ID)
    except Exception:                              # noqa: BLE001
        pass
    st.thread = st.stop = st.event = st.handler = None
