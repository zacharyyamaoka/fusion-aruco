"""What the ArUco dialogs remember between runs, and where PrusaSlicer is.

Zach (2026-10-07): "I want it to remember my configuration options. I don't
like how when I export again it forgets what I did last time."

One small JSON file per user, never a Fusion attribute (writing one would
dirty the design):

    %USERPROFILE%/CADExporter/_aruco_settings.json
    {
      "schema": "bam_aruco_settings/1",
      "commands": {"bamArucoExport": {"page": "a4", "bed_w": 25.0, ...}},
      "designs": {
        "file:urn:adsk.wipprod:dm.lineage:...": {
          "name": "GIM4305 Aruco Tag v12", "updated": 1791000000.0,
          "commands": {"bamArucoExport": {"parts": {"GIM4305": true}, ...}}}
      },
      "prusaslicer_exe": "D:/PrusaSlicer/prusa-slicer.exe"     (optional)
    }

`commands` is the last OK of each dialog; a design's own section (parts to
print, paper, bed, orientation, assembly level) wins over it for that
design. Values are what the dialog inputs hold: dropdowns by their value
code (or item name), bools, numbers in Fusion's internal units (cm), strings.

No adsk import: `snapshot` / `apply` talk to command inputs through their
`objectType`, so tests drive them with stand-ins.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import sys
import time

SCHEMA = "bam_aruco_settings/1"
FILE_NAME = "_aruco_settings.json"
MAX_DESIGNS = 100            # oldest design sections are dropped past this


def settings_path():
    return os.path.join(os.path.expanduser("~"), "CADExporter", FILE_NAME)


def _empty():
    return {"schema": SCHEMA, "commands": {}, "designs": {}}


def _dict_of_dicts(value):
    if not isinstance(value, dict):
        return {}
    return {str(k): dict(v) for k, v in value.items() if isinstance(v, dict)}


def _normalise(data):
    """Whatever was on disk -> the expected shape; bad parts are dropped."""
    out = _empty()
    if not isinstance(data, dict):
        return out
    out["commands"] = _dict_of_dicts(data.get("commands"))
    designs = data.get("designs")
    for key, entry in (designs.items() if isinstance(designs, dict) else ()):
        if not isinstance(entry, dict):
            continue
        out["designs"][str(key)] = {
            "name": entry.get("name") if isinstance(entry.get("name"), str) else None,
            "updated": entry.get("updated") if isinstance(entry.get("updated"),
                                                          (int, float)) else 0.0,
            "commands": _dict_of_dicts(entry.get("commands"))}
    exe = data.get("prusaslicer_exe")
    if isinstance(exe, str) and exe:
        out["prusaslicer_exe"] = exe
    return out


def load(path=None):
    """The settings, or an empty set when the file is missing or unreadable
    (a corrupt file is ignored here and replaced by the next save)."""
    path = path or settings_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _normalise(json.load(f))
    except (OSError, ValueError):
        return _empty()


def save(data, path=None):
    """Atomic: write a temp file beside it, then os.replace."""
    path = path or settings_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=1, sort_keys=True, allow_nan=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(20):        # Windows: a reader may hold it a moment
            try:
                os.replace(tmp, path)
                return path
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return path


def design_key(name, file_id=None):
    """A saved design is keyed by its file id (stable across versions); an
    unsaved one by its document name without the ' v12' version suffix."""
    if file_id:
        return "file:%s" % file_id
    return "doc:%s" % re.sub(r"\s+v\d+$", "", (name or "").strip() or "Design")


def _merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def recall(command, design=None, path=None, data=None):
    """The remembered values of one dialog: its last OK, with the design's
    own section (if `design` is given and known) on top."""
    data = _normalise(data) if data is not None else load(path)
    out = json.loads(json.dumps(data["commands"].get(command, {})))      # a copy
    if design:
        entry = data["designs"].get(design) or {}
        _merge(out, json.loads(json.dumps(entry.get("commands", {}).get(command, {}))))
    return out


def remember(command, values, design=None, design_values=None, design_name=None,
             path=None):
    """Merge `values` into the dialog's remembered set (and `design_values`
    into the design's own section), then save. -> the saved data."""
    data = load(path)
    _merge(data["commands"].setdefault(command, {}), dict(values or {}))
    if design and design_values:
        entry = data["designs"].setdefault(design, {"name": None, "updated": 0.0,
                                                    "commands": {}})
        if design_name:
            entry["name"] = design_name
        entry["updated"] = time.time()
        _merge(entry["commands"].setdefault(command, {}), dict(design_values))
        if len(data["designs"]) > MAX_DESIGNS:
            keep = sorted(data["designs"], key=lambda k: data["designs"][k]["updated"],
                          reverse=True)[:MAX_DESIGNS]
            data["designs"] = {k: data["designs"][k] for k in keep}
    save(data, path)
    return data


# ------------------------------------------------- Fusion command inputs

def _kind(inp):
    return str(getattr(inp, "objectType", "")).rsplit("::", 1)[-1]


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def snapshot(inputs, ids, tables=None):
    """{input id: value} for the inputs in `ids` (missing ones skipped).
    A dropdown with a (label, code) table in `tables` is stored by code."""
    tables = tables or {}
    out = {}
    for cid in ids:
        inp = inputs.itemById(cid)
        if inp is None:
            continue
        kind = _kind(inp)
        if kind == "DropDownCommandInput":
            item = inp.selectedItem
            if item is None:
                continue
            table = tables.get(cid)
            out[cid] = dict(table).get(item.name, item.name) if table else item.name
        elif kind in ("BoolValueCommandInput", "StringValueCommandInput"):
            out[cid] = inp.value
        elif kind == "IntegerSpinnerCommandInput":
            out[cid] = int(inp.value)
        elif kind in ("ValueCommandInput", "FloatSpinnerCommandInput"):
            v = float(inp.value)
            if math.isfinite(v):
                out[cid] = v
    return out


def _select(dd, value, table):
    """Select the item whose code (via `table`) or name is `value`."""
    want = None
    if table:
        for label, code in table:
            if code == value and type(code) is type(value):
                want = label
                break
    if want is None and isinstance(value, str):
        want = value
    if want is None:
        return False
    items = dd.listItems
    for i in range(items.count):
        if items.item(i).name == want:
            items.item(i).isSelected = True
            return True
    return False


def apply(inputs, values, tables=None):
    """Put remembered `values` into the dialog. A value of the wrong type, out
    of a spinner's range, or naming a dropdown item that no longer exists is
    skipped (the input keeps its default). -> the ids that were set."""
    tables = tables or {}
    done = []
    for cid, v in (values or {}).items():
        inp = inputs.itemById(cid)
        if inp is None:
            continue
        kind = _kind(inp)
        try:
            if kind == "DropDownCommandInput":
                ok = _select(inp, v, tables.get(cid))
            elif kind == "BoolValueCommandInput":
                ok = isinstance(v, bool)
                if ok:
                    inp.value = v
            elif kind == "StringValueCommandInput":
                ok = isinstance(v, str)
                if ok:
                    inp.value = v
            elif kind in ("IntegerSpinnerCommandInput", "FloatSpinnerCommandInput"):
                ok = _number(v) and (kind == "FloatSpinnerCommandInput" or float(v).is_integer())
                lo, hi = getattr(inp, "minimumValue", None), getattr(inp, "maximumValue", None)
                ok = ok and (lo is None or v >= lo) and (hi is None or v <= hi)
                if ok:
                    inp.value = int(v) if kind == "IntegerSpinnerCommandInput" else float(v)
            elif kind == "ValueCommandInput":
                ok = _number(v)
                if ok:
                    inp.value = float(v)
            else:
                ok = False
        except Exception:                          # noqa: BLE001
            ok = False
        if ok:
            done.append(cid)
    return done


# ------------------------------------------------------------ PrusaSlicer

def _registry_exe():
    """The .exe Windows opens a .3mf with, if that is PrusaSlicer."""
    try:
        import winreg
    except ImportError:
        return None
    out = []
    try:
        prog = winreg.QueryValue(winreg.HKEY_CLASSES_ROOT, ".3mf")
        cmd = winreg.QueryValue(winreg.HKEY_CLASSES_ROOT, prog + r"\shell\open\command")
        m = re.match(r'\s*"([^"]+)"|\s*(\S+)', cmd or "")
        if m:
            out.append(m.group(1) or m.group(2))
    except OSError:
        pass
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            out.append(winreg.QueryValue(
                hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\prusa-slicer.exe"))
        except OSError:
            pass
    return [p for p in out if p and os.path.basename(p).lower().startswith("prusa-slicer")]


def find_prusaslicer(hint=None, path=None):
    """PrusaSlicer's executable, or None. Order: `prusaslicer_exe` in the
    settings file (a manual override), `hint` (print_beds' summary), the
    standard install folders, the .3mf file association, PATH."""
    cands = [load(path).get("prusaslicer_exe"), hint]
    for pf in (os.environ.get("ProgramW6432"), os.environ.get("ProgramFiles"),
               os.environ.get("ProgramFiles(x86)")):
        if pf:
            cands.append(os.path.join(pf, "Prusa3D", "PrusaSlicer", "prusa-slicer.exe"))
    if os.environ.get("LOCALAPPDATA"):
        cands.append(os.path.join(os.environ["LOCALAPPDATA"], "Programs", "PrusaSlicer",
                                  "prusa-slicer.exe"))
    if sys.platform == "darwin":
        cands.append("/Applications/PrusaSlicer.app/Contents/MacOS/PrusaSlicer")
    cands += _registry_exe() or []
    cands += [shutil.which("prusa-slicer"), shutil.which("PrusaSlicer")]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def open_in_prusaslicer(project, exe=None):
    """Open `project` in PrusaSlicer, detached from Fusion. Falls back to the
    file association when no executable is known. -> how it was opened."""
    import subprocess
    exe = exe if exe and os.path.isfile(exe) else find_prusaslicer()
    if exe:
        flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        subprocess.Popen([exe, project], cwd=os.path.dirname(project) or None,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True, creationflags=flags)
        return exe
    os.startfile(project)                          # noqa: S606 (Windows only)
    return "file association"
