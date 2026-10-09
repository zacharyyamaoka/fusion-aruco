"""Export: the sidecar (record, images, print sheet) + the 3D-print parts.

Writes into ~/CADExporter/<design>/ beside the CAD Exporter's own output:

    aruco.json                     the raw block (payload["aruco"]) so an
                                   older exporter's payload can be completed
    aruco/markers.json, images/, print_<page>.pdf    markers.sidecar_files
    aruco/print3d/<part>__tile.stl / __cells.stl     one pair per distinct
                                   physical tag part, in the part's own frame,
                                   ready to load as one two-colour object
    aruco/stl/<part>__<role>.stl   the "STL files" type: one per body,
                                   oriented for printing, millimetres

ArUco Export picks ONE type (`export(kind=...)`, see KINDS); every type
writes aruco.json + joint_origins.json.
"""
from __future__ import annotations

import datetime
import json
import os
import re

import adsk.core
import adsk.fusion

import aruco_core
import aruco_tags


def _slug(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_") or "design"


def export_dir(des):
    name = des.parentDocument.name if des.parentDocument else "Design"
    return os.path.join(os.path.expanduser("~"), "CADExporter", _slug(name))


def _copies_by_component(des):
    """How many VISIBLE occurrences each component has (what to print)."""
    out = {}
    for o in des.rootComponent.allOccurrences:
        if o.isVisible:
            out[o.component.name] = out.get(o.component.name, 0) + 1
    return out


def _black_bases(comp):
    """Body names in `comp` that print BLACK: the body under a White-only
    ArUco Extrude (its white pattern is the extra body; the body is the black)."""
    out = set()
    for r in aruco_tags.read_tag_records(comp):
        fz = r.get("fusion") or {}
        if fz.get("mode") == "white" and fz.get("tile_body"):
            out.add(fz["tile_body"])
    return out


def body_role(body, black_bases=()):
    """'cells' (black, extruder 2) or 'tile' (white, extruder 1)."""
    n = body.name
    if n.startswith("aruco cells"):
        return "cells"
    if n.startswith("aruco white"):
        return "tile"
    return "cells" if n in black_bases else "tile"


def _bodies_of(entity):
    """Selection -> (object name, [body proxies]) for one PrusaSlicer object.

    An occurrence brings every solid body in its subtree. A body brings
    itself AND the pattern bodies ('aruco cells' black, 'aruco white' white)
    of its own component, so picking the tile (or the part a tag is inlaid
    into) is enough."""
    occ = adsk.fusion.Occurrence.cast(entity)
    if occ is not None:
        bodies, stack = [], [occ]
        while stack:
            o = stack.pop()
            bodies += [b for b in o.bRepBodies if b.isSolid and b.isVisible]
            stack += list(o.childOccurrences)
        return occ.name, bodies
    body = adsk.fusion.BRepBody.cast(entity)
    if body is None:
        return None, []
    ctx = body.assemblyContext
    comp = body.parentComponent
    native = body.nativeObject if ctx else body
    bodies = [body]
    for b in comp.bRepBodies:
        if (b.name.startswith(("aruco cells", "aruco white")) and b != native
                and b.isSolid):
            bodies.append(b.createForAssemblyContext(ctx) if ctx else b)
    return (f"{ctx.name}/{body.name}" if ctx else body.name), bodies


def send_selection(entities, out_path=None, orientation="cad", bed_w_mm=None,
                   bed_d_mm=None, open_slicer=True):
    """Selected bodies/occurrences -> one PrusaSlicer project, black cells on
    extruder 2, each selection its own object; optionally open PrusaSlicer.

    STLs are exported in the ASSEMBLY frame (body proxies), so parts keep
    their CAD orientation relative to each other."""
    import datetime
    import subprocess
    import tempfile
    des = aruco_tags.design()
    base = export_dir(des)
    tmp = tempfile.mkdtemp(prefix="aruco_send_")
    em = des.exportManager
    objects, seen = [], set()
    for ent in entities:
        name, bodies = _bodies_of(ent)
        if not bodies:
            continue
        obj = {"name": name, "bodies": []}
        for k, b in enumerate(bodies):
            key = b.entityToken
            if key in seen:
                continue
            seen.add(key)
            role = body_role(b, _black_bases(b.parentComponent))
            path = os.path.join(tmp, f"{_slug(name)}__{k}_{_slug(b.name)}.stl")
            opts = em.createSTLExportOptions(b, path)
            opts.meshRefinement = adsk.fusion.MeshRefinementSettings.MeshRefinementHigh
            opts.sendToPrintUtility = False
            em.execute(opts)
            obj["bodies"].append({"stl": path, "role": role, "name": b.name})
        if obj["bodies"]:
            objects.append(obj)
    if not objects:
        raise ValueError("nothing printable in the selection (pick bodies or components)")
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_path or os.path.join(base, "aruco", "print3d", f"selection_{stamp}.3mf")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    summary = aruco_core.module("print_beds").write_selection_project(
        objects, out_path, bed_w_mm=bed_w_mm, bed_d_mm=bed_d_mm, orientation=orientation)
    exe = summary.get("prusaslicer_exe") if isinstance(summary, dict) else None
    if open_slicer:
        if exe and os.path.exists(exe):
            subprocess.Popen([exe, out_path], close_fds=True)
        else:
            os.startfile(out_path)
    return {"path": out_path, "objects": [o["name"] for o in objects], "summary": summary}


def _harvest_joint_origins(des, warnings):
    import importlib.util
    import sys
    path = os.path.normpath(os.path.join(aruco_core.HERE, "..", "CADExporter",
                                         "bam_joint_origins.py"))
    name = "aruco_bam_joint_origins"
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules[name] = mod
    units = des.unitsManager.defaultLengthUnits
    scale = float(des.unitsManager.convert(1.0, "cm", units))
    return mod.collect_joint_origins(des, des.rootComponent, scale, warnings)


def tag_parts(des=None):
    """[(component name, visible copies, tag keys)] -- what batch export prints."""
    des = des or aruco_tags.design()
    counts = _copies_by_component(des)
    out = []
    for comp in des.allComponents:
        recs = aruco_tags.live_tag_records(des, comp)
        if recs:
            out.append((comp.name, counts.get(comp.name, 0), [r.get("key") for r in recs]))
    return out


def _project_file(summary):
    """print_beds' all-beds project (aruco_tags.3mf), or None."""
    if not isinstance(summary, dict) or summary.get("error"):
        return None
    files = summary.get("project")
    files = [files] if isinstance(files, str) else list(files or ())
    files = [f for f in files if isinstance(f, str) and f.lower().endswith(".3mf")]
    return next((f for f in files if os.path.basename(f) == "aruco_tags.3mf"),
                files[0] if files else None)


def _prusaslicer_exe(summary):
    import aruco_settings
    hint = summary.get("prusaslicer_exe") if isinstance(summary, dict) else None
    return aruco_settings.find_prusaslicer(hint)


# ArUco Export's types (Zach 2026-10-08: one type per export, chosen at the
# top of the dialog like Fusion's Save As Mesh). What each writes besides
# aruco.json + joint_origins.json, which every export writes:
#   prusaslicer  aruco/print3d/: one STL per body + parts.json, the multi-bed
#                project aruco_tags.3mf (MMU colours), beds/, parts/
#   stl          aruco/stl/<part>__<role>.stl: one STL per body of the chosen
#                parts, oriented for printing (print_beds.write_part_stls)
#   pdf          aruco/markers.json, aruco/images/, aruco/print_<page>.pdf
KINDS = ("prusaslicer", "stl", "pdf")
STL_DIR = "aruco/stl"


def _write_body_stls(des, pdir, rel_root, only=None, guard=None):
    """Every tag part's solid bodies as STLs (Fusion's exporter, the part's
    own frame) into `pdir`, one file per BODY -> (manifest dict, [paths
    relative to rel_root]). `only`: component names to write (None = all)."""
    em = des.exportManager
    os.makedirs(pdir, exist_ok=True)
    counts = _copies_by_component(des)
    manifest = {"schema": "bam_aruco_print3d/1", "stl_units": "mm",
                "frame_units": "cm", "design": des.parentDocument.name
                if des.parentDocument else "Design", "parts": []}
    parts = []
    for comp in des.allComponents:
        if only is not None and comp.name not in only:
            continue
        recs = aruco_tags.read_tag_records(comp)
        if not recs:
            continue
        entry = {"component": comp.name, "variant": aruco_tags.is_variant(comp),
                 "copies": counts.get(comp.name, 0), "bodies": [],
                 "tags": [{k: r.get(k) for k in ("key", "spec", "origin", "x_axis",
                                                 "y_axis", "z_axis", "units")}
                          for r in recs]}
        black = _black_bases(comp)
        for body in comp.bRepBodies:
            if not body.isSolid:
                continue
            if guard is not None:
                guard()
            role = body_role(body, black)
            # one file per BODY: a component can hold several white bodies
            # (Zach's GIM4305 part has two), and a per-role name let the
            # second overwrite the first
            fname = os.path.join(pdir, f"{_slug(comp.name)}__{_slug(body.name)}.stl")
            opts = em.createSTLExportOptions(body, fname)
            opts.meshRefinement = adsk.fusion.MeshRefinementSettings.MeshRefinementHigh
            opts.sendToPrintUtility = False
            em.execute(opts)
            rel = os.path.relpath(fname, rel_root).replace("\\", "/")
            parts.append(rel)
            entry["bodies"].append({"name": body.name, "role": role,
                                    "colour": "black" if role == "cells" else "white",
                                    "stl": rel})
        manifest["parts"].append(entry)
    with open(os.path.join(pdir, "parts.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")
    return manifest, parts


def export(out_dir=None, page="letter", landscape=False, stl=True, beds=True,
           bed_w_mm=None, bed_d_mm=None, include=None, orientation="flat", margin_mm=10.0,
           sheet=True, kind=None, guard=None):
    """ArUco export of the active design into `out_dir` (default
    ~/CADExporter/<design>/). Always aruco.json + joint_origins.json (what
    the URDF rebuild reads); the rest by `kind`:

      None           the flags decide -- `sheet` (markers.json, images,
                     print PDF), `stl` (aruco/print3d/ STLs + parts.json),
                     `beds` (with stl: the PrusaSlicer project). The
                     defaults write all of it, as before the export types.
      "prusaslicer"  the `include`d parts' STLs + the multi-bed PrusaSlicer
                     project; no sheet
      "stl"          one oriented STL per body of the `include`d parts into
                     aruco/stl/ (`orientation`: "flat" | "cad"); no 3MF, no
                     sheet, nothing in print3d/
      "pdf"          the sheet only; no STL

    `guard`: called before every long step (each body's STL); raise in it
    to abort. Nothing here opens a file or an application."""
    if kind is not None:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS} or None, not {kind!r}")
        sheet, stl, beds = kind == "pdf", kind == "prusaslicer", kind == "prusaslicer"
    des = aruco_tags.design()
    markers = aruco_core.module("markers")
    out_dir = out_dir or export_dir(des)
    os.makedirs(out_dir, exist_ok=True)
    # Live ArUco Extrude nodes follow upstream edits in compute, which may
    # not write records or move Joint Origins -- bring those up to date first.
    try:
        import aruco_feature
        live = aruco_feature.refresh_live(des)
    except Exception as exc:                       # noqa: BLE001
        live = [{"error": f"live tags not refreshed: {type(exc).__name__}: {exc}"}]
    raw = aruco_tags.collect(des)
    with open(os.path.join(out_dir, "aruco.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(raw, f, indent=2, sort_keys=True)
        f.write("\n")
    # Every Joint Origin, harvested by THIS tree's bam_joint_origins (the
    # exporter Fusion has loaded may predate it); rebuild_urdf merges the file
    # into payload["joint_origins"], which is where the tag frames come from.
    jo_warn = []
    try:
        jos = _harvest_joint_origins(des, jo_warn)
        with open(os.path.join(out_dir, "joint_origins.json"), "w", encoding="utf-8",
                  newline="\n") as f:
            json.dump(jos, f, indent=2, sort_keys=True)
            f.write("\n")
    except Exception as exc:                       # noqa: BLE001
        jo_warn.append(f"joint origins not harvested: {exc}")
    res = aruco_tags.resolve_live(des)
    written = []
    if sheet:
        files = markers.sidecar_files(
            res, design_name=des.parentDocument.name if des.parentDocument else "Design",
            page=page, landscape=bool(landscape), margin_mm=margin_mm,
            exported_at=datetime.datetime.now().isoformat(timespec="seconds"))
        for rel, data in files.items():
            path = os.path.join(out_dir, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
            written.append(rel)
    parts = []
    bed_summary = None
    stl_summary = None
    if stl:
        pdir = os.path.join(out_dir, "aruco", "print3d")
        # the PrusaSlicer type writes only what its project is made of; the
        # flags (kind=None) keep writing every tag part, as before
        only = set(include) if kind == "prusaslicer" and include is not None else None
        _manifest, parts = _write_body_stls(des, pdir, out_dir, only=only, guard=guard)
        # one PrusaSlicer project: every part on as many beds as needed, tile
        # on extruder 1 and black cells on extruder 2 -- no painting
        if beds:
            try:
                kw = {}
                if include is not None:
                    kw["include"] = list(include)
                if orientation != "flat":
                    kw["orientation"] = orientation
                bed_summary = aruco_core.module("print_beds").write_beds(
                    os.path.join(pdir, "parts.json"), out_dir=pdir,
                    bed_w_mm=bed_w_mm, bed_d_mm=bed_d_mm, **kw)
            except Exception as exc:               # noqa: BLE001
                bed_summary = {"error": f"{type(exc).__name__}: {exc}"}
    elif kind == "stl":
        # Fusion's STLs (the part's own frame, the document's unit) go to a
        # scratch folder; print_beds measures, orients and writes the loose
        # millimetre STLs -- so aruco/stl/ holds exactly one file per body
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp(prefix="aruco_stl_")
        try:
            # a guard's abort propagates; a print_beds failure is reported
            _write_body_stls(des, tmp, tmp, only=None if include is None else set(include),
                             guard=guard)
            try:
                stl_summary = aruco_core.module("print_beds").write_part_stls(
                    os.path.join(tmp, "parts.json"), os.path.join(out_dir, *STL_DIR.split("/")),
                    include=None if include is None else list(include), orientation=orientation)
            except Exception as exc:               # noqa: BLE001
                stl_summary = {"error": f"{type(exc).__name__}: {exc}", "files": []}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    project = _project_file(bed_summary)
    stl_files = sorted(os.path.relpath(f, out_dir).replace("\\", "/")
                       for f in (stl_summary or {}).get("files", ()))
    return {"out_dir": out_dir, "kind": kind, "files": sorted(written), "print3d": sorted(parts),
            "beds": bed_summary,
            # the all-beds PrusaSlicer project, and what opens it (Export's
            # "Open in PrusaSlicer when done")
            "project": project,
            "prusaslicer_exe": _prusaslicer_exe(bed_summary) if project else None,
            # kind="stl": the oriented STLs (relative to out_dir) and their folder
            "stl": stl_summary, "stl_files": stl_files,
            "stl_dir": os.path.join(out_dir, *STL_DIR.split("/")) if kind == "stl" else None,
            "pdf": next((f for f in written if f.endswith(".pdf")), None),
            "records": ["aruco.json", "joint_origins.json"],
            "instances": len(res.instances),
            "duplicates": {k: [i.name for i in v] for k, v in res.duplicates().items()},
            "live_refresh": live,
            "warnings": res.warnings + jo_warn
            + [r["error"] for r in live if isinstance(r, dict) and r.get("error")]
            + ([stl_summary["error"]] if stl_summary and stl_summary.get("error") else [])
            + list((stl_summary or {}).get("warnings") or ())}
