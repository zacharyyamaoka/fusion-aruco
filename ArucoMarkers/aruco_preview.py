"""The ArUco Extrude ghost: what OK will make, drawn while the dialog is open.

Zach (2026-10-08): "LIVE PREVIEW with dragging just like extrude!! I want to
see basically like the ghost of what it's going to be".

The ghost is custom graphics, not the real features: one semi-transparent
white mesh (the tile, or the white pattern) and one near-black mesh (the
cells), built straight from evaluate()'s frames and markers.cell_boxes --
no B-Rep, no boolean, no timeline. Measured against building the real
features in executePreview (aruco_extrude_lab.preview_bench, numbers in the
dialog docstring of aruco_extrude_cmd). The group lives in the ROOT
component (root-space coordinates: frames come in host-component space and
go through the occurrence transform), is replaced on every update and
deleted on OK, Cancel and when the dialog closes any other way.
"""
from __future__ import annotations

import math
import sys
import time
import types

import adsk.core
import adsk.fusion

import aruco_core

STATE_KEY = "_aruco_preview_state"           # outside every module we reload
EPS_CM = 0.002                               # ghost cells stand 20 um proud (no z-fighting)


def _st():
    st = sys.modules.get(STATE_KEY)
    if st is None:
        st = types.ModuleType(STATE_KEY)
        st.group = None
        st.timings = []
        sys.modules[STATE_KEY] = st
    return st


def clear():
    """Delete the ghost (idempotent; survives a module reload)."""
    st = _st()
    g, st.group = st.group, None
    if g is None:
        return False
    try:
        if g.isValid:
            g.deleteMe()
            return True
    except Exception:                              # noqa: BLE001
        pass
    return False


def leftover_groups(des):
    """How many custom graphics groups the root holds (cleanup checks)."""
    try:
        return des.rootComponent.customGraphicsGroups.count
    except Exception:                              # noqa: BLE001
        return -1


# ------------------------------------------------------------------ meshes

_FACES = (  # (axis, sign, the two other axes in CCW order seen from outside)
    (2, 1, 0, 1), (2, -1, 1, 0), (0, 1, 1, 2), (0, -1, 2, 1), (1, 1, 2, 0), (1, -1, 0, 2))


def _add_box(coords, normals, idx, c, axes, half):
    """A box (centre c, unit axes[3], half extents) as 12 flat triangles."""
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
    spec = adsk.core.Color.create(40, 40, 40, 255)
    m.color = adsk.fusion.CustomGraphicsBasicMaterialColorEffect.create(
        c, c, spec, adsk.core.Color.create(0, 0, 0, 255), 5.0, opacity)
    return m


def _add_prism(coords, normals, idx, o, x, y, z, pts, lo, hi):
    """A straight prism over a CCW polygon `pts` (cm, in o / x / y), lo..hi
    along z: two caps (fans) and one quad per side."""
    def at(u, v, t):
        return [o[k] + x[k] * u + y[k] * v + z[k] * t for k in range(3)]
    n = len(pts)
    for t, sgn in ((hi, 1.0), (lo, -1.0)):
        base = len(coords) // 3
        for (u, v) in pts:
            coords.extend(at(u, v, t))
            normals.extend([z[k] * sgn for k in range(3)])
        for i in range(1, n - 1):
            idx.extend((base, base + i, base + i + 1) if sgn > 0 else
                       (base, base + i + 1, base + i))
    for i in range(n):
        (u0, v0), (u1, v1) = pts[i], pts[(i + 1) % n]
        du, dv = u1 - u0, v1 - v0
        ln = math.hypot(du, dv) or 1.0
        nrm = [(x[k] * dv - y[k] * du) / ln for k in range(3)]
        base = len(coords) // 3
        for (u, v, t) in ((u0, v0, lo), (u1, v1, lo), (u1, v1, hi), (u0, v0, hi)):
            coords.extend(at(u, v, t))
            normals.extend(nrm)
        idx.extend((base, base + 1, base + 2, base, base + 2, base + 3))


def _mark_kind(ev):
    m = ev.get("mark")
    return m.kind if m is not None else "none"


def _cells_z(ev, mode):
    """The cells' z range in the ghost (cm over the face plane): White only
    draws them a hair past the white layer they sit in."""
    p_lo, p_hi = (v / 10.0 for v in ev["plan"].layers.pattern)
    if mode == "white":
        return p_lo - EPS_CM, p_hi + EPS_CM
    return p_lo, p_hi + EPS_CM


def boxes(ev, mode):
    """(white boxes, black boxes) in ev's frames: (centre, axes, half). The
    slabs come from evaluate()'s plan: each colour from its Offset, Height
    further, over the face plane. A corner mark's cut tile / cut ChArUco
    square is left out here and drawn by mark_prisms."""
    markers = aruco_core.module("markers")
    spec = ev["spec"]
    o, x, y, z = ev["tag"]
    lift = ev.get("lift_cm", 0.0)
    f0 = [o[k] - z[k] * lift for k in range(3)]          # the face / sketch plane
    axes = (x, y, z)
    L = ev["plan"].layers
    kind = _mark_kind(ev)
    white, black = [], []
    if ev.get("tile") is not None and L.white is not None and kind != "tile_cut":
        to, tx, ty, tz, tw, th = ev["tile"]
        lo, hi = (v / 10.0 for v in L.white)
        c = [to[k] + tz[k] * (lo + hi) / 2.0 for k in range(3)]
        white.append((c, (tx, ty, tz), (tw / 2.0, th / 2.0, (hi - lo) / 2.0)))
    p_lo, p_hi = (v / 10.0 for v in L.pattern)
    if mode == "white":
        # the white layer itself (the quiet zone + white modules); the cells
        # drawn dark inside it read as the black the body under will print
        tw, th = (v * 100.0 for v in spec.tile_size_m)
        c = [f0[k] + z[k] * (p_lo + p_hi) / 2.0 for k in range(3)]
        white.append((c, axes, (tw / 2.0, th / 2.0, (p_hi - p_lo) / 2.0)))
    c_lo, c_hi = _cells_z(ev, mode)
    mid, hz = (c_lo + c_hi) / 2.0, (c_hi - c_lo) / 2.0
    ch = aruco_core.module("aruco_chamfer")
    for (x0, y0, x1, y1) in markers.cell_boxes(spec):
        if kind == "black_cut" and ch.is_top_left((x0 * 1000.0, y0 * 1000.0, x1 * 1000.0,
                                                    y1 * 1000.0), ev["mark"].corner):
            continue                                     # drawn cut, by mark_prisms
        cx, cy = (x0 + x1) * 50.0, (y0 + y1) * 50.0
        c = [f0[k] + x[k] * cx + y[k] * cy + z[k] * mid for k in range(3)]
        black.append((c, axes, ((x1 - x0) * 50.0, (y1 - y0) * 50.0, hz)))
    return white, black


def mark_prisms(ev, mode):
    """(white prisms, black prisms) of the corner mark ("Chamfer top-left
    corner"): [(frame, polygon cm, lo cm, hi cm)] -- the tile with its corner
    cut (white), a ChArUco board's top-left square with its outer corner cut
    (black), or the corner triangle (black; White only: the black body
    under, seen through the white's notch)."""
    m = ev.get("mark")
    if m is None:
        return [], []
    o, x, y, z = ev["tag"]
    lift = ev.get("lift_cm", 0.0)
    frame = ([o[k] - z[k] * lift for k in range(3)], x, y, z)
    cm = lambda pts: [(u / 10.0, v / 10.0) for u, v in pts]      # noqa: E731
    L = ev["plan"].layers
    if m.kind == "tile_cut" and L.white is not None and ev.get("tile") is not None:
        lo, hi = (v / 10.0 for v in L.white)
        return [(frame, cm(m.outline), lo, hi)], []
    c_lo, c_hi = _cells_z(ev, mode)
    if m.kind == "black_cut":
        return [], [(frame, cm(m.outline), c_lo, c_hi)]
    if m.kind == "triangle":
        return [], [(frame, cm(m.solid), c_lo, c_hi)]
    return [], []


def pin_prisms(ev, n=None):
    """(pins, holes): [(frame, polygon cm, lo cm, hi cm)] -- the locating
    pins under the tile (white) and the holes they get in the body under."""
    slabs = aruco_core.module("aruco_slabs")
    pl = ev["plan"]
    if pl.pin is None or not ev.get("pins") or ev.get("pin_frame") is None:
        return [], []
    frame = tuple(ev["pin_frame"])
    pins, holes = [], []
    lo, hi = (v / 10.0 for v in pl.pin)
    for p in ev["pins"]:
        pins.append((frame, [(u / 10.0, v / 10.0) for u, v in slabs.outline_points(p, 32)],
                     lo, hi))
    tol = float((n or {}).get("pin_tol", ev.get("pin_tol", slabs.PIN_TOL_MM)))
    for what, (a, b) in pl.recess:
        if what != "hole":
            continue
        for p in ev["pins"]:
            g = slabs.grow(p, tol)
            holes.append((frame, [(u / 10.0, v / 10.0) for u, v in slabs.outline_points(g, 32)],
                          a / 10.0, b / 10.0))
    return pins, holes


def triad(ev):
    """The Joint Origin the tag will get, in ev's frames: OpenCV's frame for
    the Origin picked -- a single marker's centre (y its up), a board's
    origin corner (y down, z into the face), the board as built (ev["spec"]:
    a rectangular board at 90 / 270 has its cols / rows swapped).
    (o, x, y, z) cm."""
    import aruco_tags
    o, x, y, z = ev["tag"]
    tag = aruco_tags.M().TagDef(key="", component="", spec=ev["spec"], origin=list(o),
                                x_axis=list(x), y_axis=list(y), z_axis=list(z))
    return aruco_tags.optical_frame_cm(tag)


def _add_triad(group, frame, length):
    """Three coloured axes (x red, y green, z blue) and a dot at the origin,
    drawn over the geometry."""
    o, x, y, z = frame
    for axis, rgb in ((x, (230, 40, 40)), (y, (40, 190, 40)), (z, (50, 90, 255))):
        tip = [o[k] + axis[k] * length for k in range(3)]
        coords = adsk.fusion.CustomGraphicsCoordinates.create(list(o) + tip)
        ln = group.addLines(coords, [0, 1], False)
        ln.color = adsk.fusion.CustomGraphicsSolidColorEffect.create(
            adsk.core.Color.create(rgb[0], rgb[1], rgb[2], 255))
        for attr, val in (("weight", 4.0), ("depthPriority", 2)):
            try:
                setattr(ln, attr, val)
            except Exception:                      # noqa: BLE001
                pass
    coords, normals, idx = [], [], []
    h = length * 0.06
    _add_box(coords, normals, idx, list(o), (x, y, z), (h, h, h))
    m = _mesh(group, coords, normals, idx, (255, 200, 0), 1.0)
    try:
        if m is not None:
            m.depthPriority = 2
    except Exception:                              # noqa: BLE001
        pass


def show(des, ev_root, mode):
    """Replace the ghost with this evaluation (frames in ROOT space).
    -> {"ms", "cells", "groups"}."""
    t0 = time.perf_counter()
    clear()
    st = _st()
    group = des.rootComponent.customGraphicsGroups.add()
    st.group = group
    white, black = boxes(ev_root, mode)
    pins, holes = pin_prisms(ev_root)
    mark_w, mark_b = mark_prisms(ev_root, mode)
    # opaque black, milky white: legible over Fusion's blue selection
    # highlight; the pins' holes in the body under, see-through orange
    for items, prisms, colour, opacity in ((white, pins + mark_w, (250, 250, 250), 0.7),
                                           (black, mark_b, (10, 10, 10), 1.0),
                                           ([], holes, (255, 120, 0), 0.45)):
        coords, normals, idx = [], [], []
        for c, axes, half in items:
            _add_box(coords, normals, idx, c, axes, half)
        for (po, px, py, pz), pts, lo, hi in prisms:
            _add_prism(coords, normals, idx, po, px, py, pz, pts, lo, hi)
        _mesh(group, coords, normals, idx, colour, opacity)
    jo = None
    try:
        jo = triad(ev_root)
        tw, th = (v * 100.0 for v in ev_root["spec"].tile_size_m)
        _add_triad(group, jo, max(0.3, 0.35 * min(tw, th)))
    except Exception:                              # noqa: BLE001
        jo = None
    st.triad = jo
    try:
        group.isSelectable = False
    except Exception:                              # noqa: BLE001
        pass
    ms = round((time.perf_counter() - t0) * 1000.0, 1)
    st.timings = (st.timings + [ms])[-50:]
    return {"ms": ms, "cells": len(black), "white_boxes": len(white), "pins": len(pins),
            "holes": len(holes), "mark": _mark_kind(ev_root),
            "mark_prisms": len(mark_w) + len(mark_b)}
