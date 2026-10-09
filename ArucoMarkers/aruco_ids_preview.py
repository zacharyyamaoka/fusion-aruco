"""The ArUco IDs ghost: what OK will print on every copy, drawn while the
dialog is open.

Zach (2026-10-08): "When I do ArUco IDs I want to see a preview."

For every copy of every tag, the pattern it WILL print -- the table's
values written as OK would write them (aruco_commands.ids_write), resolved
at the root like apply_variants does -- is drawn at that copy's real place:
custom graphics in the ROOT component, each tag's frame taken through that
copy's world transform. A copy that prints its own id (a variant) gets an
amber backing, blue cells and its ids in amber ("#3-5", plus "was #0-2"
when this dialog changed it); a copy that prints the modelled pattern is
drawn white + black with a grey label. The ghost stands a hair proud of the
real tag (0.5 % of its size, at least 30 um) so it hides the old pattern.

Frames come from aruco_tags.fresh_collect(write=False): a live ArUco
Extrude whose record lags its geometry (the face changed after tagging) is
evaluated at its own place in the timeline. That roll (there and back, then
a repaint; none at all when every record agrees with its node) happens once,
in the dialog's activate (prepare) -- where ArUco Extrude's own edit dialog
rolls -- and is cached for the dialog's life with the occurrence
transforms; executePreview only draws. The group is replaced on every
update and deleted on OK, Cancel and whenever the dialog closes.
"""
from __future__ import annotations

import sys
import time
import types

import adsk.core
import adsk.fusion

import aruco_core
import aruco_tags

STATE_KEY = "_aruco_ids_preview_state"          # outside every module we reload

AMBER = (255, 196, 64)
BLUE = (20, 60, 200)
WHITE = (250, 250, 250)
BLACK = (15, 15, 15)
GREY = (110, 110, 110)


def M():
    return aruco_core.module("markers")


def _st():
    st = sys.modules.get(STATE_KEY)
    if st is None:
        st = types.ModuleType(STATE_KEY)
        st.group = None
        st.cache = None            # {"doc", "raw", "occs", "refreshed", "ms"}
        st.drawn = []
        st.timings = []
        st.last = {}
        st.prepared = None
        sys.modules[STATE_KEY] = st
    return st


def _doc_key(des):
    doc = des.parentDocument
    return getattr(doc, "creationId", None) or id(doc)


def clear():
    """Delete the ghost (idempotent; survives a module reload)."""
    st = _st()
    g, st.group = st.group, None
    st.drawn = []
    if g is None:
        return False
    try:
        if g.isValid:
            g.deleteMe()
            return True
    except Exception:                              # noqa: BLE001
        pass
    return False


def reset():
    """The dialog closed: no ghost, nothing cached."""
    clear()
    st = _st()
    st.cache = None
    st.prepared = None


def groups(des):
    try:
        return des.rootComponent.customGraphicsGroups.count
    except Exception:                              # noqa: BLE001
        return -1


# ---------------------------------------------------------------- planning

def prepare(des):
    """The dialog's activate: fresh records + occurrence transforms, once
    per dialog. Only here may a stale record's node be evaluated (a timeline
    roll there and back, then a repaint) -- the same event ArUco Extrude's
    edit dialog rolls in; executePreview never touches the model.
    -> True when it (re)built the context (a drawn ghost wants redrawing)."""
    st = _st()
    before = st.cache
    ctx = _context(des, evaluate=True)
    st.prepared = {"rebuilt": ctx is not before, "ms": ctx.get("ms"),
                   "stale": len(ctx.get("refreshed", []))}
    return ctx is not before


def _context(des, evaluate=False):
    st = _st()
    key = _doc_key(des)
    if (st.cache is None or st.cache.get("doc") != key
            # an executePreview that came first read the records as they are
            or (evaluate and not st.cache.get("evaluated")
                and any(not r.get("evaluated", True) for r in st.cache.get("refreshed", [])))):
        t0 = time.perf_counter()
        refreshed = []
        raw = aruco_tags.fresh_collect(des, write=False, report=refreshed, evaluate=evaluate)
        occs = aruco_tags.occurrence_refs(des)
        st.cache = {"doc": key, "raw": raw, "occs": occs, "refreshed": refreshed,
                    "evaluated": evaluate,
                    "ms": round((time.perf_counter() - t0) * 1000.0, 1)}
    return st.cache


def with_table(ovs, owner, rows, values):
    """The overrides after OK writes the table (`rows`: [(rel_path, key,
    original id, spinner id)], `values`: {spinner id: id}) at `owner`
    ("" = root) -- the same rule as aruco_commands.ids_write."""
    markers = M()
    out = [markers.OverrideRecord(o.owner, o.occurrence, {k: dict(v) for k, v in o.values.items()})
           for o in ovs]
    for path, key, original, spin_id in rows:
        new = values.get(spin_id, original)
        if new == original:
            continue
        parts = markers.split_path(path)
        rec = next((o for o in out if (o.owner or "") == owner
                    and o.occurrence == parts[0]), None)
        if rec is None:
            rec = markers.OverrideRecord(owner, parts[0], {})
            out.append(rec)
        rec.values.setdefault(markers.slot_key(parts[1:], key), {})["marker_id"] = int(new)
    return out


def plan(des, level_comp, rows, values):
    """(after OK, as it is now): two root resolutions with world frames."""
    markers = M()
    ctx = _context(des)
    tags, ovs = markers.tags_from_payload(ctx["raw"], lambda m: None)
    owner = "" if level_comp == des.rootComponent else level_comp.name
    root = des.rootComponent.name
    now = markers.resolve(tags, ovs, ctx["occs"], root, "cm")
    after = markers.resolve(tags, with_table(ovs, owner, rows, values), ctx["occs"], root, "cm")
    return after, now


# ----------------------------------------------------------------- drawing

def _cols(m):
    """4x4 row-major -> (origin, x, y, z)."""
    return ([m[3], m[7], m[11]], [m[0], m[4], m[8]], [m[1], m[5], m[9]], [m[2], m[6], m[10]])


_FACES = (  # (axis, sign, the two other axes in CCW order seen from outside)
    (2, 1, 0, 1), (2, -1, 1, 0), (0, 1, 1, 2), (0, -1, 2, 1), (1, 1, 2, 0), (1, -1, 0, 2))


def _add_box(coords, idx, normals, c, axes, half):
    """A box (centre c, unit axes[3], half extents) as 12 flat triangles
    (the same mesher as aruco_preview's ghost)."""
    for a, sgn, u, v in _FACES:
        n = [axes[a][k] * sgn for k in range(3)]
        fc = [c[k] + axes[a][k] * half[a] * sgn for k in range(3)]
        base = len(coords) // 3
        for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            for k in range(3):
                coords.append(fc[k] + axes[u][k] * half[u] * su + axes[v][k] * half[v] * sv)
            normals.extend(n)
        idx.extend((base, base + 1, base + 2, base, base + 2, base + 3))


def _mesh(group, coords, normals, idx, colour, opacity):
    if not idx:
        return None
    cg = adsk.fusion.CustomGraphicsCoordinates.create(coords)
    m = group.addMesh(cg, idx, normals, idx)
    r, g, b = colour
    c = adsk.core.Color.create(r, g, b, 255)
    m.color = adsk.fusion.CustomGraphicsBasicMaterialColorEffect.create(
        c, c, adsk.core.Color.create(40, 40, 40, 255), adsk.core.Color.create(0, 0, 0, 255),
        5.0, opacity)
    return m


def _ids_text(spec):
    ids = spec.ids
    return f"#{ids[0]}" if len(ids) == 1 else f"#{ids[0]}-{ids[-1]}"


def _label(group, text, frame, spec, eps, colour):
    o, x, y, z = frame
    tw, th = (v * 100.0 for v in spec.tile_size_m)
    size = max(0.3, 0.16 * th)
    p = [o[i] - x[i] * tw / 2.0 + y[i] * (th / 2.0 + 0.25 * size) + z[i] * eps for i in range(3)]
    m = adsk.core.Matrix3D.create()
    m.setWithCoordinateSystem(adsk.core.Point3D.create(*p), adsk.core.Vector3D.create(*x),
                              adsk.core.Vector3D.create(*y), adsk.core.Vector3D.create(*z))
    try:
        t = group.addText(text, "Arial", size, m)
        r, g, b = colour
        t.color = adsk.fusion.CustomGraphicsSolidColorEffect.create(
            adsk.core.Color.create(r, g, b, 255))
        return t
    except Exception:                              # noqa: BLE001
        return None


def show(des, level_comp, rows, values):
    """Replace the ghost with what OK would print on every copy.
    -> {"ms", "copies", "variants", "changed", "cells", "labels", "context_ms"}."""
    markers = M()
    t0 = time.perf_counter()
    clear()
    st = _st()
    after, now = plan(des, level_comp, rows, values)
    before = {i.name: i.spec for i in now.instances}
    group = des.rootComponent.customGraphicsGroups.add()
    st.group = group
    meshes = {k: ([], [], []) for k in ("white", "black", "amber", "blue")}
    drawn, n_cells, n_labels = [], 0, 0
    for inst in after.instances:
        if inst.hidden or inst.world is None:
            continue
        spec = inst.spec
        frame = _cols(inst.world)
        o, x, y, z = frame
        axes = (x, y, z)
        variant = inst.needs_variant
        was = before.get(inst.name)
        changed = was is not None and (was.marker_id != spec.marker_id
                                       or was.dictionary != spec.dictionary)
        tw, th = (v * 100.0 for v in spec.tile_size_m)
        top = max(0.0, getattr(spec, "black_top_m", spec.raise_m) * 100.0, spec.raise_m * 100.0)
        eps = max(0.003, 0.005 * max(tw, th))
        back, cells = ("amber", "blue") if variant else ("white", "black")
        # the backing hides the pattern underneath; the cells sit on it
        c = [o[i] + z[i] * (top + eps * 0.5) for i in range(3)]
        _add_box(*meshes[back], c, axes, (tw / 2.0, th / 2.0, eps * 0.25))
        hz = eps * 0.25
        zc = top + eps + hz
        for (x0, y0, x1, y1) in markers.cell_boxes(spec):
            cx, cy = (x0 + x1) * 50.0, (y0 + y1) * 50.0
            cc = [o[i] + x[i] * cx + y[i] * cy + z[i] * zc for i in range(3)]
            _add_box(*meshes[cells], cc, axes, ((x1 - x0) * 50.0, (y1 - y0) * 50.0, hz))
            n_cells += 1
        text = _ids_text(spec)
        if changed:
            text += f"  (was {_ids_text(was)})"
        if _label(group, text, frame, spec, top + eps, AMBER if variant else GREY):
            n_labels += 1
        drawn.append({"path": inst.path, "key": inst.tag.key, "ids": list(spec.ids),
                      "dictionary": spec.dictionary, "variant": variant, "changed": changed,
                      "world": list(inst.world)})
    for k, colour, opacity in (("white", WHITE, 0.85), ("amber", AMBER, 0.9),
                               ("black", BLACK, 1.0), ("blue", BLUE, 1.0)):
        coords, idx, normals = meshes[k]
        _mesh(group, coords, normals, idx, colour, opacity)
    try:
        group.isSelectable = False
    except Exception:                              # noqa: BLE001
        pass
    st.drawn = drawn
    ms = round((time.perf_counter() - t0) * 1000.0, 1)
    st.timings = (st.timings + [ms])[-50:]
    st.last = {"ms": ms, "copies": len(drawn),
               "variants": sum(1 for d in drawn if d["variant"]),
               "changed": sum(1 for d in drawn if d["changed"]),
               "cells": n_cells, "labels": n_labels,
               "context_ms": (st.cache or {}).get("ms"),
               "context_evaluated": (st.cache or {}).get("evaluated"),
               "prepared": getattr(st, "prepared", None),
               "refreshed": (st.cache or {}).get("refreshed", [])}
    return dict(st.last)
