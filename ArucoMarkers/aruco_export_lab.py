"""Bridge check for ArUco Export's type list (PrusaSlicer project / STL
files / PDF print sheet), in a NEW demo document of ours, closed after; the
user's document is put back.

    python tools/aruco_bridge.py export_types_test --arg out_dir=<folder>
    ... --arg dialog=1     also the real dialog (step 1)

1. dialog=1 only: the dialog, opened for real: each type picked in turn ->
   which inputs are visible, the info line, whether OK is enabled, the args
   OK passes to aruco_export.export; Reset; then OK on the STL type through
   the test hook (writes into <out_dir>/dialog_stl, remembers nothing, opens
   nothing).
2. aruco_export.export(kind=...) for each type into <out_dir>/<kind>: every
   file written, checked against what the type may and must write.

Never opens PrusaSlicer, a PDF viewer or Explorer. Every step first checks
that the active document is still the test's own and that no command but
ours is open, and aborts if not.

SAFETY (2026-10-08): Fusion crashed at 15:46:45 during a dialog=1 run started
while Zach was working (orbit / sketch edits / joint moves): the test made a
new document active under him, opened the Export dialog programmatically and
pumped adsk.doEvents() while it was live (plus, in that run only, added and
deleteMe'd list items on the live dropdown). Run it ONLY in a test window the
coordinator opens with Zach's OK. The dialog step is opt-in; the dialog's
input visibility is covered offline by tests/test_aruco_export_dialog.py.
Cleanup order: terminate our dialog, wait until Fusion is idle, clear the
selection, then close the document -- never close with a command open.
"""
from __future__ import annotations

import os
import shutil
import time

import adsk.core
import adsk.fusion


def _pump_until(cond, timeout_s=10.0):
    t0 = time.perf_counter()
    while not cond() and time.perf_counter() - t0 < timeout_s:
        adsk.doEvents()
        time.sleep(0.005)
    return cond()


def _files(root):
    out = []
    for d, _subs, names in os.walk(root):
        for n in names:
            out.append(os.path.relpath(os.path.join(d, n), root).replace("\\", "/"))
    return sorted(out)


def _visible(inputs):
    """Ids of the inputs a user sees: visible, and so is every group they
    sit in (a hidden group's children still say isVisible)."""
    out = []
    for i in range(inputs.count):
        inp = inputs.item(i)
        node, shown = inp, True
        while node is not None and shown:
            shown = node.isVisible
            node = node.parentCommandInput
        if shown and inp.id not in out:
            out.append(inp.id)
        grp = adsk.core.GroupCommandInput.cast(inp)
        if grp is not None and shown:
            out += [cid for cid in _visible(grp.children) if cid not in out]
    return out


def _pick(dd, label):
    for i in range(dd.listItems.count):
        if dd.listItems.item(i).name == label:
            dd.listItems.item(i).isSelected = True
            return True
    return False


def _dialog(C, guard, out_dir):
    app = adsk.core.Application.get()
    ui = app.userInterface
    st = C._ExportState
    st.cmd = None
    guard()
    ui.commandDefinitions.itemById(C.EXPORT_CMD).execute()
    if not _pump_until(lambda: st.cmd is not None):
        if ui.activeCommand == C.EXPORT_CMD:
            guard()
            ui.terminateActiveCommand()
            raise RuntimeError("the Export dialog that opened is the one of before the "
                               "types (Fusion still holds its old handler): run "
                               "register_commands, then this again")
        raise RuntimeError(f"the Export dialog did not open (active: {ui.activeCommand})")
    guard()
    _pump_until(lambda: False, 0.3)
    guard()
    cmd = st.cmd
    inputs = cmd.commandInputs
    dd = inputs.itemById("export_type")
    out = {"opened_on": C._export_type(inputs),
           "entries": [{"name": dd.listItems.item(i).name,
                        "icon": os.path.basename(dd.listItems.item(i).icon or "")}
                       for i in range(dd.listItems.count)],
           "parts": [c for c, _n, _k in st.parts], "types": {}}
    for i in range(len(st.parts)):           # the user ticks every part
        inputs.itemById(f"part_{i}").value = True
    for label, code in C.EXPORT_TYPES:
        guard()
        _pick(dd, label)
        C.export_changed(inputs, "export_type")
        out["types"][code] = {"visible": _visible(inputs),
                              "labels": {cid: inputs.itemById(cid).name
                                         for cid in _visible(inputs)
                                         if not cid.startswith("part_")},
                              "info": inputs.itemById("info").formattedText,
                              "ok_enabled": C.export_valid(inputs),
                              "args": C._export_args(inputs)}
    # Reset: options back, the type kept
    _pick(dd, "PDF print sheet")
    page = inputs.itemById("page")
    page.listItems.item(page.listItems.count - 1).isSelected = True
    C.export_changed(inputs, "reset")
    out["reset"] = {"type_after": C._export_type(inputs),
                    "page_after": C._export_args(inputs)["page"],
                    "label": inputs.itemById("reset").name}
    # OK on the STL type, through the test hook
    _pick(dd, "STL files")
    for i in range(len(st.parts)):
        inputs.itemById(f"part_{i}").value = True
    C.export_changed(inputs, "export_type")
    target = os.path.join(out_dir, "dialog_stl")
    shutil.rmtree(target, ignore_errors=True)
    st.test = {"out_dir": target}
    st.last = None
    guard()
    t0 = time.perf_counter()
    cmd.doExecute(True)
    _pump_until(lambda: st.cmd is None, 60.0)
    guard()
    last = st.last or {}
    out["ok_stl"] = {"seconds": round(time.perf_counter() - t0, 2),
                     "stl_files": last.get("stl_files"),
                     "all_files": _files(target) if os.path.isdir(target) else [],
                     "warnings": last.get("warnings")}
    if ui.activeCommand == C.EXPORT_CMD:          # doExecute did not close it
        guard()
        ui.terminateActiveCommand()
        _pump_until(lambda: st.cmd is None, 5.0)
    st.test = None
    return out


def _rules(kind, files, n_bodies, page):
    def has(pred):
        return any(pred(f) for f in files)
    checks = {"records (aruco.json, joint_origins.json)":
              "aruco.json" in files and "joint_origins.json" in files}
    if kind == "pdf":
        checks.update({
            "the PDF": f"aruco/print_{page}.pdf" in files,
            "marker images": has(lambda f: f.startswith("aruco/images/") and f.endswith(".png")),
            "markers.json": "aruco/markers.json" in files,
            "no STL": not has(lambda f: f.endswith(".stl")),
            "no 3MF": not has(lambda f: f.endswith(".3mf"))})
    elif kind == "stl":
        stls = [f for f in files if f.endswith(".stl")]
        checks.update({
            "one STL per body, all in aruco/stl/":
                len(stls) == n_bodies and all(f.startswith("aruco/stl/") for f in stls),
            "no PDF": not has(lambda f: f.endswith(".pdf")),
            "no 3MF": not has(lambda f: f.endswith(".3mf")),
            "no images": not has(lambda f: f.startswith("aruco/images/")),
            "no print3d/": not has(lambda f: f.startswith("aruco/print3d/"))})
    else:
        checks.update({
            "the project": "aruco/print3d/aruco_tags.3mf" in files,
            "the STLs": has(lambda f: f.startswith("aruco/print3d/") and f.endswith(".stl")),
            "parts.json": "aruco/print3d/parts.json" in files,
            "no PDF": not has(lambda f: f.endswith(".pdf")),
            "no images": not has(lambda f: f.startswith("aruco/images/")),
            "no markers.json": "aruco/markers.json" not in files})
    return checks


def run(out_dir, dialog="0", n_actuators="2", n_arms="1", keep="0"):
    import aruco_actions
    import aruco_commands as C
    import aruco_demo
    import aruco_export
    app = adsk.core.Application.get()
    ui = app.userInterface
    aruco_actions._quiet_or_raise()
    if not out_dir:
        raise ValueError("pass out_dir=<a scratch folder>")
    before = app.activeDocument
    doc = None
    result = {}
    try:
        aruco_demo.build(int(n_actuators), int(n_arms))
        aruco_actions._remember_active()
        doc = app.activeDocument

        def guard():
            if (doc is None or not doc.isValid or app.activeDocument != doc
                    or doc.creationId not in aruco_actions._mine()):
                raise RuntimeError("the active document is no longer the test's own -- aborted")
            busy = ui.activeCommand
            if busy not in ("SelectCommand", "", None, C.EXPORT_CMD):
                raise RuntimeError(f"a command that is not the test's is open ({busy}) -- aborted")
        guard()
        result["document"] = doc.name
        args = None
        if str(dialog) not in ("0", "false"):
            result["dialog"] = _dialog(C, guard, out_dir)
            args = {k: v for k, v in result["dialog"]["types"]["stl"]["args"].items()
                    if k != "kind"}
            args.update({k: result["dialog"]["types"]["pdf"]["args"][k]
                         for k in ("page", "landscape", "margin_mm")})
            args.update({k: result["dialog"]["types"]["prusaslicer"]["args"][k]
                         for k in ("bed_w_mm", "bed_d_mm")})
        if args is None:
            args = {"include": [c for c, _n, _k in aruco_export.tag_parts()]}
        des = adsk.fusion.Design.cast(app.activeProduct)
        n_bodies = sum(1 for c in des.allComponents if c.name in set(args["include"])
                       for b in c.bRepBodies if b.isSolid)
        result["export_args"] = args
        result["exports"] = {}
        for kind in aruco_export.KINDS:
            guard()
            target = os.path.join(out_dir, kind)
            shutil.rmtree(target, ignore_errors=True)
            t0 = time.perf_counter()
            out = aruco_export.export(out_dir=target, kind=kind, guard=guard, **args)
            files = _files(target)
            checks = _rules(kind, files, n_bodies, args.get("page", "letter")
                            + ("_landscape" if args.get("landscape") else ""))
            beds = out.get("beds") if isinstance(out.get("beds"), dict) else {}
            result["exports"][kind] = {
                "seconds": round(time.perf_counter() - t0, 2), "files": files,
                "checks": checks, "all_ok": all(checks.values()),
                "project": out.get("project"), "pdf": out.get("pdf"),
                "stl_orientation": (out.get("stl") or {}).get("orientation"),
                "beds": beds.get("beds"), "bed_orientation": beds.get("orientation"),
                "warnings": out.get("warnings")}
        result["all_ok"] = all(e["all_ok"] for e in result["exports"].values())
        return result
    finally:
        try:
            if (doc is not None and doc.isValid and app.activeDocument == doc
                    and str(keep) not in ("1", "true")):
                # 1. our dialog off, 2. idle, 3. no selection, 4. close
                if ui.activeCommand == C.EXPORT_CMD:
                    ui.terminateActiveCommand()
                    _pump_until(lambda: ui.activeCommand == "SelectCommand", 5.0)
                if ui.activeCommand in ("SelectCommand", "", None):
                    ui.activeSelections.clear()
                    result["closed"] = doc.name
                    doc.close(False)
                else:
                    result["not_closed"] = f"Fusion is busy ({ui.activeCommand})"
        except Exception as exc:                   # noqa: BLE001
            result["close_error"] = str(exc)[:200]
        if before is not None and before.isValid and before != app.activeDocument:
            try:
                before.activate()
                result["reactivated"] = before.name
            except Exception as exc:               # noqa: BLE001
                result["reactivate_error"] = str(exc)[:200]
