"""Bridge checks for ArUco Extrude's corner mark (2026-10-08 "Chamfer top-left
corner"; 2026-10-09 "Corner mark: None / Cut white corner / Black triangle").

    python tools/aruco_bridge.py chamfer_test [--arg parts=single,grid,charuco,edit,variant]
                                              [--arg keep=1]
    python tools/aruco_bridge.py chamfer_test --arg parts=dialog     # opt-in, see SAFETY

SAFETY (2026-10-08, aruco_export_lab): Fusion crashed while a test opened a
dialog programmatically and pumped events under Zach's hands. Run this ONLY
in a test window the coordinator opens with his OK. The "dialog" group opens
the ArUco Extrude dialog for real, so it is not in the default parts; the
dialog's ghost and info line are also covered offline by
tests/test_aruco_chamfer_bodies.py.

Each group runs in a NEW document of ours, closed after (unless keep=1); the
user's document is put back. Every step checks first that the test's
document is still the active one (aruco_extrude_lab.guard) and the run stops
there if not. Each group ends with aruco_live_test._jo_check: every Joint
Origin read back against the optical frame (the record's and the node's now).

    single, grid, charuco   one tag per mode with Corner mark "cut" (the old
                    checkbox's choice: the triangle where there is no tile of
                    its own), on 50 x 50 (grid: 100 x 40) x 5 mm plates and a
                    free sketch:
                      n  White + black, New body, nothing under (a free sketch)
                      p  White + black, New body on the plate, triangle pin
                      j  White + black, Join
                      b  Black only (autosize: the triangle on the face's edge)
                      s  Black only, a smaller marker (the triangle inside the face)
                      w  White only
                      t  White + black, New body, Origin turned (90; grid 180)
                      r  White + black, New body, Corner mark "triangle": the
                         black triangle in the white corner, the tile a plain
                         rectangle (ChArUco: its black square's cut instead)
                      q  as r, grid / single turned 90 (a grid: OpenCV's board
                         with its cols / rows swapped)
                    each measured on its bodies by point containment at the
                    record's fusion.mark (cut corner gone, the white / the part
                    there, the triangle black / a notch), the mark's corner the
                    one nearest OpenCV's corner 0 / the board origin, the record
                    agreeing with the node (record_drift), the node healthy.
    edit            on -> Origin turned -> off by the edit route: a compute, never
                    a rebuild; the tile's volume changes by exactly leg^2 / 2 x
                    its height; the record's mark follows; turned 180 the
                    record's x is the un-turned x reversed (2026-10-09: this
                    plate is SQUARE, so its x is Fusion's first longest edge --
                    -y here, as on live_test's square plates -- not +x as the
                    old check assumed); "triangle" then swaps the cut for the
                    triangle (a compute: the tile whole again).
    dialog          the create dialog: the Corner mark list per mode / layout
                    (only what the tag can have), the info line, the ghost's
                    mark prisms, Cancel leaves nothing; OK makes a marked node;
                    the edit dialog shows its mark.
    variant         three parts (tile cut / triangle / ChArUco black cut), and a
                    recessed black (the variant's pocket opens to the face),
                    each placed twice; copy 2 re-numbered (an override +
                    apply_variants): the copy's bodies carry the same mark; a
                    second apply_variants keeps them.
"""
from __future__ import annotations

import math
import traceback

import adsk.core
import adsk.fusion

import aruco_core
import aruco_demo
import aruco_feature as F
import aruco_tags as T

P = adsk.core.Point3D.create
INSIDE = adsk.fusion.PointContainment.PointInsidePointContainment


def CH():
    return aruco_core.module("aruco_chamfer")


def _lab():
    import aruco_extrude_lab
    return aruco_extrude_lab


def _check(cond, what, info):
    if not cond:
        raise AssertionError(f"{what}: {info}")


def _top_face(body):
    best = None
    for f in body.faces:
        if not isinstance(f.geometry, adsk.core.Plane):
            continue
        ok, n = f.evaluator.getNormalAtPoint(f.pointOnFace)
        if n.z > 0.999 and (best is None or f.pointOnFace.z > best.pointOnFace.z):
            best = f
    return best


def _plate(comp, x0, y0, w_cm, d_cm, name, h_cm=0.5):
    return aruco_demo._box(comp, x0, y0, x0 + w_cm, y0 + d_cm, h_cm, name).bodies.item(0)


def _free_profile(root, cx, cy, w_cm, h_cm, z_mm=20.0, name="free"):
    pl = root.constructionPlanes.createInput()
    pl.setByOffset(root.xYConstructionPlane, adsk.core.ValueInput.createByString(f"{z_mm} mm"))
    sk = root.sketches.add(root.constructionPlanes.add(pl))
    sk.name = name
    sk.sketchCurves.sketchLines.addCenterPointRectangle(P(cx, cy, 0),
                                                        P(cx + w_cm / 2, cy + h_cm / 2, 0))
    return min((sk.profiles.item(i) for i in range(sk.profiles.count)),
               key=lambda p: p.areaProperties().area)


def _rec(des, key):
    """(component, record) of the MODELLED tag `key` -- never a copy's
    variant part (2026-10-09: its record has the template's key and no node;
    found first, it handed None to settings_of)."""
    return T.find_record(des, key)


def _cf(des, key):
    return _lab()._cf(des, key)


def make(target, **kw):
    return _lab().make(target, **kw)


def _at(frame, uv_mm, z_mm):
    """A point of a record's frame: (u, v) mm in the pattern frame, z mm
    along its normal from its origin (the surface)."""
    o, x, y, z = frame
    return [o[i] + (x[i] * uv_mm[0] + y[i] * uv_mm[1] + z[i] * z_mm) / 10.0 for i in range(3)]


def _has(body, p):
    return body is not None and body.pointContainment(P(*p)) == INSIDE


def _centroid(pts):
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def _want_kind(spec, mode, op, choice="cut"):
    """The mark a tag asking for `choice` gets (aruco_chamfer: the other
    form where it cannot have that one)."""
    form, _note = CH().choose(choice, CH().valid_marks(spec.layout, mode, op, spec.rows,
                                                       spec.legacy))
    if form == "cut":
        return "black_cut" if CH().charuco_cut(spec.layout, spec.rows, spec.legacy) else "tile_cut"
    return form


def _corner0_world(tag):
    """OpenCV's corner 0 (single) / the board's origin (boards), world cm."""
    opt = T.optical_frame_cm(tag)
    if tag.spec.layout != "single":
        return opt[0]
    h = tag.spec.marker_length_m * 50.0
    o, x, y = opt[0], opt[1], opt[2]
    return [o[i] - x[i] * h + y[i] * h for i in range(3)]


def verify(des, key, under=None, frame=None, comp=None, rec=None, names=None):
    """One marked tag, measured on its bodies. `under`: the body under it
    (the plate, joined or recessed); frame / comp / rec / names: a variant's
    (its own component, the template's record and body names)."""
    variant = names is not None
    if rec is None:
        comp, rec = _rec(des, key)
    cf = _cf(des, key)                  # the template's node (a variant's too: its heights)
    fz = rec.get("fusion") or {}
    tag = T.M().TagDef.from_dict(rec, lambda m: None)
    spec = tag.spec
    mode = fz.get("mode")
    s = F.settings_of(cf)
    op = F.effective_op(s)
    mk = CH().from_dict(fz.get("mark"))
    choice = CH().choice_of(s)
    out = {"key": key, "mode": mode, "op": op, "layout": spec.layout, "variant": variant,
           "choice": choice, "kind": mk.kind if mk else None, "leg_mm": mk.leg if mk else None,
           "live": bool(fz.get("custom_feature")), "group": fz.get("group"),
           "made": fz.get("made"), "health": int(cf.healthState)}
    want = _want_kind(spec, mode, op, choice)
    _check(mk is not None and mk.kind == want, f"the record's mark should be {want}", out)
    frame = frame or T._frame_of(rec)
    L = F.plan_of(s, F.numbers_of(cf)).layers
    zp = (L.pattern[0] + L.pattern[1]) / 2.0 - L.surface        # mm from the record's origin
    zw = (L.white[0] + L.white[1]) / 2.0 - L.surface if L.white else None
    p_lo = L.pattern[0]
    names = names or {}
    pat = comp.bRepBodies.itemByName(names.get("pattern") or (
        f"aruco {'white' if mode == 'white' else 'cells'} {key}"))
    tile = (comp.bRepBodies.itemByName(names.get("tile") or f"{F.TILE_BODY} {key}")
            if op == "new" else None)
    c = _centroid(mk.cut or mk.solid)
    got = {}
    if mk.kind == "tile_cut":
        a, b = [p for p in mk.cut if p != tuple(mk.corner)][:2]
        keep = ((a[0] + b[0]) / 2.0 + 0.3, (a[1] + b[1]) / 2.0 - 0.3)      # just inside the cut
        got = {"tile_at_cut": _has(tile, _at(frame, c, zw)),
               "tile_past_cut": _has(tile, _at(frame, keep, zw))}
        _check(not got["tile_at_cut"] and got["tile_past_cut"],
               "the tile's corner should be cut on the line (gone at the corner, there past it)",
               dict(out, **got))
    elif mk.kind == "black_cut":
        sq = spec.square_length_m * 1000.0
        centre = (mk.corner[0] + sq / 2.0, mk.corner[1] - sq / 2.0)
        got = {"pattern_at_cut": _has(pat, _at(frame, c, zp)),
               "pattern_in_square": _has(pat, _at(frame, centre, zp))}
        if mode == "white":
            _check(got["pattern_at_cut"] and not got["pattern_in_square"],
                   "White only: the white should fill the cut corner, not the square",
                   dict(out, **got))
        else:
            _check(not got["pattern_at_cut"] and got["pattern_in_square"],
                   "the top-left black square should have its outer corner cut", dict(out, **got))
            if mode == "both" and op == "new":
                got["white_at_cut"] = _has(tile, _at(frame, c, zp))
            elif under is not None and (mode == "both" or p_lo < 0):
                got["white_at_cut"] = _has(under, _at(frame, c, zp))
            if "white_at_cut" in got:
                _check(got["white_at_cut"], "the white (tile / part) should fill the cut corner",
                       dict(out, **got))
    else:
        got = {"pattern_at_triangle": _has(pat, _at(frame, c, zp))}
        if under is not None and (mode == "both" or p_lo < 0):
            # a joined tile / an inlay: the body under is pocketed for the
            # black triangle; White only: it keeps its material in the notch
            got["under_at_triangle"] = _has(under, _at(frame, c, zp))
        if mode == "white":
            _check(not got["pattern_at_triangle"] and got.get("under_at_triangle", True),
                   "White only: a notch in the white, the body under it there", dict(out, **got))
        else:
            _check(got["pattern_at_triangle"] and not got.get("under_at_triangle", False),
                   "the triangle should be black, in a pocket of its own", dict(out, **got))
        if tile is not None:
            # "Black triangle" on a tile of its own: the tile is pocketed for
            # it and stays a plain rectangle (its own corner whole)
            tw, th = (v * 1000.0 for v in spec.tile_size_m)
            corner_in = (-tw / 2.0 + 0.2, th / 2.0 - 0.2)
            got["tile_at_triangle"] = _has(tile, _at(frame, c, zp))
            got["tile_corner_whole"] = _has(tile, _at(frame, corner_in, zw))
            _check(not got["tile_at_triangle"] and got["tile_corner_whole"],
                   "a tile of its own: pocketed for the triangle, its corner whole",
                   dict(out, **got))
    out.update(got)
    # the mark's corner is the one nearest OpenCV's corner 0 / the board origin
    o, x, y, _z = frame
    k0 = _corner0_world(tag)
    k0uv = (sum((k0[i] - o[i]) * x[i] for i in range(3)) * 10.0,
            sum((k0[i] - o[i]) * y[i] for i in range(3)) * 10.0)
    W, H = (v * 1000.0 for v in spec.extent_m)
    corners = [(-W / 2, H / 2), (W / 2, H / 2), (W / 2, -H / 2), (-W / 2, -H / 2)]
    near = min(corners, key=lambda q: math.dist(q, mk.corner))
    out["corner0_uv_mm"] = [round(v, 4) for v in k0uv]
    out["mark_corner_uv_mm"] = [round(v, 4) for v in mk.corner]
    _check(math.dist(near, k0uv) < 1e-3, "the mark should sit at OpenCV's corner 0 / the board "
           "origin", out)
    if not variant:
        out["drift"] = T.record_drift(des, comp, rec)
        _check(out["drift"] == "", "the record should agree with the node", out)
        _check(out["health"] in (0, None), "the node should be healthy", out)
        _check(out["live"] or out["group"], "a live node or one timeline group", out)
    return out


def _pins_clear(des, key, face_z_cm):
    """The pins (the tile's vertices below the face) keep 1 mm from the cut."""
    comp, rec = _rec(des, key)
    mk = CH().from_dict((rec.get("fusion") or {}).get("mark"))
    tile = comp.bRepBodies.itemByName(f"{F.TILE_BODY} {key}")
    o, x, y, _z = T._frame_of(rec)
    worst = None
    for v in tile.vertices:
        g = v.geometry
        if g.z > face_z_cm - 1e-4:
            continue
        d = [g.x - o[0], g.y - o[1], g.z - o[2]]
        u = sum(d[i] * x[i] for i in range(3)) * 10.0
        w = sum(d[i] * y[i] for i in range(3)) * 10.0
        gap = ((u - mk.corner[0]) + (mk.corner[1] - w) - mk.leg) / math.sqrt(2.0)
        worst = gap if worst is None else min(worst, gap)
    out = {"pin_vertices_min_gap_to_cut_mm": round(worst, 4) if worst is not None else None}
    _check(worst is not None and worst >= 1.0 - 1e-3, "the pin should keep 1 mm from the cut", out)
    return out


# ------------------------------------------------------------- the layouts

def _cases(layout):
    turn = 180 if layout == "grid" else 90
    return (("n", "both", "new", "free", {}),
            ("p", "both", "new", "plate", {"pin": "triangle"}),
            ("j", "both", "join", "plate", {}),
            ("b", "black", "join", "plate", {}),
            ("s", "black", "join", "plate", {"small": True}),
            ("w", "white", "join", "plate", {}),
            ("t", "both", "new", "plate", {"turn": turn}),
            # Corner mark "Black triangle" on a tile of its own (2026-10-09)
            ("r", "both", "new", "plate", {"corner_mark": "triangle"}),
            ("q", "both", "new", "plate", {"corner_mark": "triangle", "turn": 90}))


def layout_case(des, step, layout):
    root = des.rootComponent
    w, d = (10.0, 4.0) if layout == "grid" else (5.0, 5.0)
    small = {"single": 30.0, "grid": 20.0, "charuco": 0.0}[layout]
    dict_ = "DICT_4X4_50"
    for i, (k, mode, op, where, extra) in enumerate(_cases(layout)):
        key = f"{layout[0]}{k}"
        x0 = -30.0 + i * (w + 2.0)

        def one(key=key, mode=mode, op=op, where=where, extra=extra, x0=x0):
            plate = None
            if where == "free":
                target = _free_profile(root, x0 + w / 2.0, 0.0, w * 0.8, d * 0.8, name=f"free {key}")
            else:
                plate = _plate(root, x0, -d / 2.0, w, d, f"plate {key}")
                target = _top_face(plate)
            kw = dict(mode=mode, op=op, layout=layout, key=key,
                      corner_mark=extra.get("corner_mark", "cut"), marker_id=0,
                      dictionary=dict_)
            if extra.get("pin"):
                kw["pin"] = extra["pin"]
            if extra.get("turn"):
                kw["turn"] = extra["turn"]
            if extra.get("small") and small:
                kw["size"] = small
            make(target, **kw)
            out = verify(des, key, under=plate)
            if extra.get("pin") and out["kind"] == "tile_cut":
                out["pins"] = _pins_clear(des, key, 0.5)
            return out
        step(f"{layout} {key}: {mode}/{op} on {where}{' ' + str(extra) if extra else ''}", one)

    def health():
        bad = [i for i in range(des.timeline.count)
               if not des.timeline.item(i).isGroup and int(des.timeline.item(i).healthState) != 0]
        _check(not bad, "every timeline item should be healthy",
               [des.timeline.item(i).name for i in bad])
        return {"timeline": des.timeline.count}
    step(f"{layout}: the timeline is healthy", health)


# ------------------------------------------------------------------ edits

def edit_case(des, step):
    root = des.rootComponent
    plate = _plate(root, -3.0, -3.0, 6.0, 6.0, "plate e")

    def tile_vol():
        b = root.bRepBodies.itemByName(f"{F.TILE_BODY} e")
        return b.volume * 1000.0                                  # mm^3

    def made():
        make(_top_face(plate), mode="both", op="new", key="e", marker_id=3, size=30.0)
        comp, rec = _rec(des, "e")
        _check("mark" not in (rec.get("fusion") or {}), "off: no mark in the record", rec.get("fusion"))
        return {"tile_mm3": round(tile_vol(), 4)}
    v0 = step("edit: a tag without the mark", made)["value"]["tile_mm3"]

    def on():
        cf = _cf(des, "e")
        parts0 = list(F.settings_of(cf).get("parts") or [])
        res = F.scripted_edit(cf, settings={"corner_mark": "cut"}, route=None)
        cf = _cf(des, "e")
        comp, rec = _rec(des, "e")
        mk = CH().from_dict((rec.get("fusion") or {}).get("mark"))
        h = 2.0
        want = v0 - mk.leg ** 2 / 2.0 * h
        out = {"rebuilt": res.get("rebuilt"), "parts": F.settings_of(cf).get("parts"),
               "tile_mm3": round(tile_vol(), 4), "want_mm3": round(want, 4), "kind": mk.kind}
        _check(not res.get("rebuilt") and out["parts"] == parts0, "a compute, not a rebuild", out)
        _check(abs(out["tile_mm3"] - want) < 1e-2, "the tile loses exactly leg^2 / 2 x 2 mm", out)
        out["verify"] = verify(des, "e", under=plate)
        return out
    step("edit: Corner mark Cut white corner (Fusion's edit, hidden)", on)

    def turned():
        """2026-10-09: this asserted x = -X after the turn. The plate is
        SQUARE: its four edges are equally long, face_frame's x is Fusion's
        first one -- -Y here (live_test's square plates C, D read the same)
        -- so turned 180 the record's x is +Y. What a 180 turn means: the
        un-turned x and y reversed, the same normal and centre."""
        _c, before = _rec(des, "e")
        res = F.scripted_edit(_cf(des, "e"), settings={"turn": 180}, route=None)
        out = {"rebuilt": res.get("rebuilt"), "verify": verify(des, "e", under=plate)}
        comp, rec = _rec(des, "e")
        dot = lambda a, b: sum(p * q for p, q in zip(a, b))     # noqa: E731
        out.update(x_before=[round(v, 6) for v in before["x_axis"]],
                   x_after=[round(v, 6) for v in rec["x_axis"]],
                   centre_moved_mm=round(math.dist(before["origin"], rec["origin"]) * 10.0, 6))
        _check(dot(rec["x_axis"], before["x_axis"]) < -0.999999
               and dot(rec["y_axis"], before["y_axis"]) < -0.999999
               and dot(rec["z_axis"], before["z_axis"]) > 0.999999,
               "turned 180: x and y reversed, z kept", out)
        _check(out["centre_moved_mm"] < 1e-4, "turned 180 about its own centre", out)
        return out
    step("edit: Origin bottom right with the mark on", turned)

    def route():
        res = F.scripted_edit(_cf(des, "e"), settings={"turn": 0})
        return {"rebuilt": res.get("rebuilt"), "verify": verify(des, "e", under=plate)}
    step("edit: Origin back via APIFeatureEditCommand (double-click route)", route)

    def triangle():
        """Corner mark "Black triangle" on the same tile of its own: a
        compute; the tile whole again but for the triangle's pocket."""
        cf = _cf(des, "e")
        res = F.scripted_edit(cf, settings={"corner_mark": "triangle"}, route=None)
        cf = _cf(des, "e")
        s = F.settings_of(cf)
        comp, rec = _rec(des, "e")
        mk = CH().from_dict((rec.get("fusion") or {}).get("mark"))
        pk = F.plan_of(s, F.numbers_of(cf)).pocket
        want = v0 - mk.leg ** 2 / 2.0 * (pk[1] - pk[0])
        out = {"rebuilt": res.get("rebuilt"), "kind": mk.kind if mk else None,
               "tile_mm3": round(tile_vol(), 4), "want_mm3": round(want, 4)}
        _check(not res.get("rebuilt") and mk is not None and mk.kind == "triangle",
               "a compute, the record's mark the triangle", out)
        _check(abs(out["tile_mm3"] - want) < 1e-2,
               "the tile loses only the triangle's pocket (its corner whole)", out)
        out["verify"] = verify(des, "e", under=plate)
        return out
    step("edit: Corner mark Black triangle (the tile a plain rectangle)", triangle)

    def off():
        res = F.scripted_edit(_cf(des, "e"), settings={"corner_mark": "none"}, route=None)
        comp, rec = _rec(des, "e")
        out = {"rebuilt": res.get("rebuilt"), "tile_mm3": round(tile_vol(), 4),
               "mark": (rec.get("fusion") or {}).get("mark")}
        _check(not res.get("rebuilt") and out["mark"] is None, "off: a compute, no mark", out)
        _check(abs(out["tile_mm3"] - v0) < 1e-2, "the tile whole again", out)
        return out
    step("edit: Corner mark None", off)


# ----------------------------------------------------------------- dialog

def dialog_case(des, step):
    import aruco_extrude_cmd as X
    import aruco_preview
    lab = _lab()
    root = des.rootComponent
    plate = _plate(root, -3.0, -3.0, 6.0, 6.0, "plate d")
    plate2 = _plate(root, 5.0, -3.0, 6.0, 6.0, "plate d2")
    lab._BENCH["des"] = des
    try:
        def ghost():
            tl0 = des.timeline.count
            inputs = lab._open_dialog()
            inputs.itemById("target").addSelection(_top_face(plate))
            lab._drive(inputs, "target")
            rows = []

            def pick(choice):
                X._select(inputs, "corner_mark", X.MARK_TABLE, choice)
                lab._drive(inputs, "corner_mark")
            for mode, op, layout, choice, want, listed in (
                    ("both", "new", "single", "cut", "tile_cut",
                     ["None", "Cut white corner", "Black triangle"]),
                    ("both", "new", "single", "triangle", "triangle",
                     ["None", "Cut white corner", "Black triangle"]),
                    ("both", "join", "single", "triangle", "triangle", ["None", "Black triangle"]),
                    ("black", "join", "single", "triangle", "triangle", ["None", "Black triangle"]),
                    ("white", "join", "grid", "triangle", "triangle", ["None", "Black triangle"]),
                    ("black", "join", "charuco", "cut", "black_cut",
                     ["None", "Cut black square's corner"])):
                X._select(inputs, "mode", X.MODE_LABELS, mode)
                lab._drive(inputs, "mode")
                X._select(inputs, "op", X.OP_LABELS, op)
                lab._drive(inputs, "op")
                X._select(inputs, "layout", X.LAYOUT_LABELS, layout)
                lab._drive(inputs, "layout")
                pick(choice)
                ev = X._X.ev_root
                mk = ev.get("mark") if ev else None
                wp, bp = aruco_preview.mark_prisms(ev, X._X.mode) if ev else ([], [])
                info = inputs.itemById("info").text
                dd = inputs.itemById("corner_mark")
                names = [dd.listItems.item(i).name for i in range(dd.listItems.count)]
                rows.append({"mode": mode, "op": op, "layout": layout, "choice": choice,
                             "kind": mk.kind if mk else None, "listed": names,
                             "white_prisms": len(wp), "black_prisms": len(bp),
                             "info_has_line": "corner mark" in info,
                             "groups": aruco_preview.leftover_groups(des)})
                _check(mk is not None and mk.kind == want and len(wp) + len(bp) == 1
                       and "corner mark" in info and names == listed,
                       f"the dialog should list {listed} and show a {want}", rows[-1])
            pick("none")
            off = {"mark": X._X.ev_root.get("mark") if X._X.ev_root else "?"}
            _check(off["mark"] is None, "None: no mark", off)
            lab._close_dialog()
            after = {"groups": aruco_preview.leftover_groups(des),
                     "timeline_unchanged": des.timeline.count == tl0}
            _check(after["groups"] == 0 and after["timeline_unchanged"], "Cancel leaves nothing", after)
            return {"rows": rows, "off": off, "after_cancel": after}
        step("dialog: the checkbox, the info line and the ghost, per mode / layout", ghost)

        def ok():
            inputs = lab._open_dialog()
            inputs.itemById("target").addSelection(_top_face(plate2))
            lab._drive(inputs, "target")
            X._select(inputs, "mode", X.MODE_LABELS, "both")
            lab._drive(inputs, "mode")
            X._select(inputs, "op", X.OP_LABELS, "new")
            lab._drive(inputs, "op")
            X._select(inputs, "corner_mark", X.MARK_TABLE, "triangle")
            lab._drive(inputs, "corner_mark")
            X._X.cmd.doExecute(True)
            lab._pump_until(lambda: X._X.cmd is None, 10.0)
            recs = T.read_tag_records(root)
            key = recs[-1].get("key") if recs else None
            out = verify(des, key, under=plate2)
            cf = _cf(des, key)
            inputs = lab._open_dialog(select=cf)
            lab._drive(inputs, "activate")
            sel = inputs.itemById("corner_mark").selectedItem
            out["edit_dialog_shows"] = sel.name if sel else None
            lab._close_dialog()
            _check(out["edit_dialog_shows"] == "Black triangle",
                   "the edit dialog should show its Black triangle", out)
            return out
        step("dialog: OK makes a marked node; its edit dialog shows its mark", ok)
    finally:
        lab._BENCH["des"] = None
        X._X.no_remember = False


# --------------------------------------------------------------- variants

def _part(root, name, w_cm, d_cm, transforms):
    occ = root.occurrences.addNewComponent(transforms[0])
    comp = occ.component
    comp.name = name
    _plate(comp, -w_cm / 2.0, -d_cm / 2.0, w_cm, d_cm, "Body1")
    occs = [occ] + [root.occurrences.addExistingComponent(comp, t) for t in transforms[1:]]
    return comp, occs


def _shift(x_cm, y_cm):
    m = adsk.core.Matrix3D.create()
    m.translation = adsk.core.Vector3D.create(x_cm, y_cm, 0.0)
    return m


def variant_case(des, step):
    root = des.rootComponent
    parts = {}
    specs = (("vn", "Part N", dict(mode="both", op="new", layout="single", size=30.0)),
             ("vb", "Part B", dict(mode="black", layout="single", size=30.0)),
             ("vc", "Part C", dict(mode="black", layout="charuco")),
             ("vr", "Part R", dict(mode="both", op="new", layout="single", size=30.0,
                                   black_offset=1.6)),           # recessed 0.4 mm
             # Black triangle on a tile of its own: the copy's tile keeps the
             # triangle's pocket, its pattern brings the triangle
             ("vt", "Part T", dict(mode="both", op="new", layout="single", size=30.0,
                                   corner_mark="triangle")))
    for i, (key, name, kw) in enumerate(specs):
        def one(key=key, name=name, kw=kw, i=i):
            comp, occs = _part(root, name, 5.0, 5.0, [_shift(-20.0 + i * 8.0, 0.0),
                                                       _shift(-20.0 + i * 8.0, 8.0)])
            body = comp.bRepBodies.itemByName("Body1")
            face = _top_face(body).createForAssemblyContext(occs[0])
            kw = dict({"corner_mark": "cut"}, **kw)
            make(face, key=key, marker_id=10 + i, **kw)
            parts[key] = {"comp": comp, "occs": occs}
            return verify(des, key, under=body)
        step(f"variant: {name} ({kw.get('mode')}/{kw.get('layout')}) marked", one)

    def renumber():
        def write():
            for i, (key, p) in enumerate(parts.items()):
                T.write_override("", p["occs"][1].name, f"|{key}", {"marker_id": 30 + 4 * i})
            return T.apply_variants(des)
        return F.in_command(write)
    rep = step("variant: copy 2 of each part re-numbered (ArUco IDs -> OK)", renumber)["value"] or {}

    def copies():
        out = {}
        existing = T._existing_variants(root)
        for key, p in parts.items():
            comp_t, rec = _rec(des, key)
            fz = rec.get("fusion") or {}
            found = [(o, info) for slot, items in existing.items() for o, info in items
                     if slot and slot.endswith(f"|{key}")]
            _check(found, f"{key}: a variant part should exist", list(existing))
            vocc, info = found[0]
            vcomp = vocc.component
            names = {"pattern": fz.get("cells_body"), "tile": fz.get("tile_body")}
            under = vcomp.bRepBodies.itemByName(fz.get("tile_body") or "Body1")
            res = verify(des, key, under=under, comp=vcomp, rec=rec, names=names)
            if key == "vr":
                # the recessed black: the copy's pocket opens up to the white top
                tag = T.M().TagDef.from_dict(rec, lambda m: None)
                x0, y0, x1, y1 = T.M().cell_boxes(tag.spec)[0]
                uv = ((x0 + x1) * 500.0, (y0 + y1) * 500.0)
                above = _has(vcomp.bRepBodies.itemByName(fz.get("tile_body")),
                             _at(T._frame_of(rec), uv, -0.2))
                res["tile_over_recessed_black"] = above
                _check(not above, "the copy's pocket should be open over a recessed black", res)
            out[key] = res
        return out
    step("variant: every copy carries the template's mark", copies)

    def again():
        rep2 = F.in_command(T.apply_variants, des)
        out = {"kept": rep2.get("kept"), "created": rep2.get("created"),
               "removed": rep2.get("removed")}
        _check(not out["created"] and not out["removed"] and len(out["kept"]) == len(parts),
               "a second ArUco IDs OK keeps every copy", out)
        return out
    step("variant: apply_variants again keeps them (idempotent)", again)
    return rep


# ----------------------------------------------------------------- runner

GROUPS = ("single", "grid", "charuco", "edit", "variant")      # "dialog": opt-in (SAFETY)


def chamfer_test(out_dir="", keep="0", parts=",".join(GROUPS)):
    """The groups above, each in a NEW document of its own (closed after
    unless keep=1). Stops at the first group that cannot start or that the
    user interrupts by switching documents. parts=dialog adds the real
    dialog (opt-in)."""
    lab = _lab()
    import aruco_live_test
    want = [g for g in str(parts).split(",") if g]

    def one_group(name):
        def run(des):
            def body(step):
                if name in ("single", "grid", "charuco"):
                    layout_case(des, step, name)
                elif name == "edit":
                    edit_case(des, step)
                elif name == "dialog":
                    dialog_case(des, step)
                elif name == "variant":
                    variant_case(des, step)
                else:
                    raise ValueError(f"unknown group {name!r}")
            res = lab.run_steps(des, body)
            if not res.get("aborted"):
                try:
                    res["jo"] = aruco_live_test._jo_check(des)
                except Exception as exc:           # noqa: BLE001
                    res["jo"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                                 "where": traceback.format_exc()[-600:]}
            return res
        return lab.in_new_doc(run, keep)

    out = {"steps": [], "groups": {}}
    for name in want:
        res = one_group(name)
        if res.get("error"):
            out["groups"][name] = {"error": res["error"], "where": res.get("where")}
            out["aborted"] = f"{name}: {res['error']}"
            break
        out["steps"] += [dict(s_, step=f"[{name}] {s_['step']}") for s_ in res.get("steps", [])]
        jo = res.get("jo") or {}
        out["groups"][name] = {"steps": len(res.get("steps", [])),
                               "ok": all(s_["ok"] for s_ in res.get("steps", [])),
                               "jo_ok": jo.get("ok"), "jo_record_ok": jo.get("record_ok"),
                               "jo_worst_mm": jo.get("worst_mm"), "aborted": res.get("aborted")}
        if res.get("aborted"):
            out["aborted"] = f"{name}: {res['aborted']}"
            break
    out["verdict"] = ("PASS" if out["steps"] and all(s_["ok"] for s_ in out["steps"])
                      and all(g.get("jo_ok") for g in out["groups"].values())
                      and not out.get("aborted") else "FAIL")
    out["failed"] = [{"step": s_["step"], "error": s_.get("error"), "where": s_.get("where")}
                     for s_ in out["steps"] if not s_["ok"]]
    return out
