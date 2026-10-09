"""Bridge checks for ArUco Extrude's Origin (Zach, 2026-10-09: "100% I always
want the frame to match perfectly OpenCV. If OpenCV doesn't support it I
don't want to either"). Replaces the Frame lab of 2026-10-08.

    python tools/aruco_bridge.py origin_test [--arg out_dir=<dir>] [--arg keep=1]
                                             [--arg parts=create,edit,variant]
    python tools/aruco_bridge.py origin_test --arg parts=dialog       # opt-in, see SAFETY

SAFETY (aruco_chamfer_lab): run this ONLY in a test window the coordinator
opens with Zach's OK. The "dialog" group opens the ArUco Extrude dialog for
real, so it is not in the default parts.

Each group runs in a NEW document of ours (closed after unless keep=1; the
user's document is put back; every step first checks that the test's
document is still the active one and stops if not). Each group ends with
aruco_live_test._jo_check (every Joint Origin on OpenCV's frame, 1 um).

  create   every layout -- a single marker, a square 2 x 2 grid, a
           rectangular 3 x 2 grid, a square 3 x 3 ChArUco, Zach's 4 x 2
           ChArUco (18.75 / 25 mm), a legacy 5 x 4 ChArUco -- x all four
           Origins, each on its own identical plate. Per tag, against the
           un-turned tag of the same layout (its axes ARE the layout frame):
           the record's board as built (a rectangular board's cols / rows
           swapped at 90 / 270, the same ids), the cells body's footprint
           unchanged, the record agreeing with the body (record_drift: the
           built board's cells are where the record says), the Joint Origin
           on the record's OpenCV frame (1 um) AND on the corner / up the
           Origin names, worked out from OpenCV's conventions alone
           (aruco_origin.opencv_in_layout)
  edit     Zach's 4 x 2 ChArUco, White + black New body, Corner mark "cut",
           a triangle pin: "Rotate" four times by the edit route (one by
           APIFeatureEditCommand) -- a compute each time, the same node; the
           record, the Joint Origin, the mark (aruco_chamfer_lab.verify) and
           the pin follow OpenCV's origin corner; back where it started
  variant  a part with the 3 x 2 grid at 90 (on its side, OpenCV's 2 x 3),
           placed twice, copy 2 re-numbered (ArUco IDs -> OK): the copy's
           spec is swapped too and its cells are where that spec says; the
           PDF sheet's item is the board as built
  pnp      with out_dir: one render per tag (aruco_live_test.capture_tags)
           for tools/verify_aruco_jo_pnp.py -- OpenCV's own board object
           with the record's spec against the Joint Origin
  dialog   (opt-in) the Origin dropdown's words per Type, "Rotate" steps
           clockwise through them, the ghost's axes are OpenCV's frame of
           what OK makes, the info line names the corner; Cancel leaves
           nothing

-> {"steps", "groups", "verdict", "failed"}.
"""
from __future__ import annotations

import math
import os
import traceback

import adsk.core
import adsk.fusion

import aruco_core
import aruco_feature as F
import aruco_tags as T

TOL_MM = 1e-3
TOL_DEG = 1e-3
TURNS = (0, 270, 180, 90)        # "Rotate" order: top left, top right, bottom right, bottom left

# key, layout, plate w x d mm, extra make() arguments. A face is fitted at
# the typed size with as many markers as fit (no forcing on a face), so the
# plates are sized for exactly these boards: 2 x 2 and 3 x 2 grids of 14 mm
# (0.2 gaps, 1-module border), 3 x 3 / 4 x 2 / 5 x 4 ChArUco squares
LAYOUTS = (
    ("s", "single", (40.0, 40.0), {"size": 28.0}),
    ("g", "grid", (40.0, 40.0), {"size": 14.0}),
    ("r", "grid", (60.0, 40.0), {"size": 14.0}),
    ("c", "charuco", (60.0, 60.0), {"size": 13.5, "square": 18.0}),
    ("z", "charuco", (110.0, 60.0), {"size": 18.75, "square": 25.0}),
    ("l", "charuco", (100.0, 80.0), {"size": 13.5, "square": 18.0, "legacy": True}),
)
BOARDS = {"g": (2, 2), "r": (3, 2), "c": (3, 3), "z": (4, 2), "l": (5, 4)}


def OR():
    return aruco_core.module("aruco_origin")


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


def _dot(a, b):
    return sum(p * q for p, q in zip(a, b))


def _err(a, b):
    pos, ang = T._frame_error(a, b)
    return round(pos * 10.0, 6), round(math.degrees(ang), 6)


def _make(face, key, layout, turn, kw):
    args = dict(mode="black", key=key, layout=layout, turn=turn, dictionary="DICT_4X4_50",
                size_auto=False)
    args.update(kw)
    if "legacy" in args:
        args["legacy"] = bool(args["legacy"])
    return _lab().make(face, **args)


def origin_check(des, key, turn, ref):
    """One tag against `ref` (the un-turned tag of the same layout on an
    identical plate: {"x", "y", "z", "spec", "box"}). Raises when wrong."""
    comp, rec = T.find_record(des, key)
    _check(rec is not None, "the tag has a record", key)
    tag = T.M().TagDef.from_dict(rec, lambda m: None)
    sp, sp0 = tag.spec, ref["spec"]
    layout = sp.layout
    x0, y0, z0 = ref["x"], ref["y"], ref["z"]
    out = {"origin": OR().label(layout, turn),
           "board": f"{layout} {sp.cols}x{sp.rows}", "unturned": f"{sp0.cols}x{sp0.rows}"}
    # the board as built: OpenCV's board, cols / rows swapped at a quarter turn
    want_cr = OR().built(layout, turn, sp0.cols, sp0.rows)
    _check((sp.cols, sp.rows) == want_cr and sp.ids == sp0.ids
           and abs(sp.marker_length_m - sp0.marker_length_m) < 1e-12,
           "the board as built: cols / rows (swapped at 90 / 270), ids and size", out)
    # the cells body's footprint, measured in the layout frame: unchanged
    body = T.pattern_body(comp, rec)
    _check(body is not None, "the tag has its pattern body", out)
    (u0, u1), (v0, v1), _zz = T._extents(body, (tag.origin, x0, y0, z0))
    out["footprint_mm"] = [round(v * 10, 4) for v in (u0, u1, v0, v1)]
    _check(max(abs(a - b) for a, b in zip((u0, u1, v0, v1), ref["box"])) * 10 < TOL_MM,
           "the same footprint as laid out", dict(out, unturned_mm=[round(v * 10, 4)
                                                                     for v in ref["box"]]))
    drift = T.record_drift(des, comp, rec)
    out["drift"] = drift
    _check(drift == "", "the record agrees with the body (its cells where the spec says)", out)
    # the Joint Origin: on the record's OpenCV frame ...
    jo = T.joint_origin_of(des, comp, rec)
    _check(jo is not None, "the tag has its Joint Origin", rec.get("fusion"))
    f = T._jo_frame(jo)
    opt = T.optical_frame_cm(tag)
    out["jo_vs_record"] = _err(f, opt)
    _check(out["jo_vs_record"][0] < TOL_MM and out["jo_vs_record"][1] < TOL_DEG,
           "the Joint Origin is the record's OpenCV frame", out)
    # ... and where OpenCV's conventions put it for this Origin, worked out
    # in the layout frame from the un-turned footprint alone
    W, H = (v * 1000.0 for v in sp0.extent_m)
    (ou, ov), xd, yd, zs = OR().opencv_in_layout(layout, turn, W, H)
    lift = sp.raise_m * 100.0
    want_o = [tag.origin[i] + (x0[i] * ou + y0[i] * ov) / 10.0 + z0[i] * lift for i in range(3)]
    want_x = [x0[i] * xd[0] + y0[i] * xd[1] for i in range(3)]
    want_y = [x0[i] * yd[0] + y0[i] * yd[1] for i in range(3)]
    want_z = [c * zs for c in z0]
    out["jo_vs_opencv_by_hand"] = _err(f, (want_o, want_x, want_y, want_z))
    d = T._sub(f[0], tag.origin)
    out["jo_in_layout_mm"] = [round(_dot(d, x0) * 10, 4), round(_dot(d, y0) * 10, 4)]
    out["want_in_layout_mm"] = [round(ou, 4), round(ov, 4)]
    _check(out["jo_vs_opencv_by_hand"][0] < TOL_MM and out["jo_vs_opencv_by_hand"][1] < TOL_DEG,
           f"the Joint Origin is OpenCV's frame for '{out['origin']}' (worked out by hand)", out)
    return out


def create_case(des, step, plates):
    import aruco_demo
    root = des.rootComponent
    refs = {}
    for li, (lk, layout, (w, d), kw) in enumerate(LAYOUTS):
        for ti, turn in enumerate(TURNS):
            key = f"{lk}{turn}"
            x0 = -80.0 + ti * 13.0
            y0 = -60.0 + li * 11.0
            e = aruco_demo._box(root, x0, y0, x0 + w / 10.0, y0 + d / 10.0, 0.5, f"plate {key}")
            plates.append(e)

            def one(key=key, layout=layout, turn=turn, kw=kw, body=e.bodies.item(0), lk=lk):
                _make(_top_face(body), key, layout, turn, kw)
                cf = _lab()._cf(des, key)
                _check(cf is not None and int(cf.healthState) == 0, "a healthy node",
                       cf.errorOrWarningMessage if cf else None)
                if turn == 0:
                    comp, rec = T.find_record(des, key)
                    tag = T.M().TagDef.from_dict(rec, lambda m: None)
                    if lk in BOARDS:
                        _check((tag.spec.cols, tag.spec.rows) == BOARDS[lk],
                               "the plate fits the intended board", (tag.spec.cols, tag.spec.rows))
                    body_ = T.pattern_body(comp, rec)
                    frame = (tag.origin, tag.x_axis, tag.y_axis, tag.z_axis)
                    (u0, u1), (v0, v1), _z = T._extents(body_, frame)
                    refs[lk] = {"x": tag.x_axis, "y": tag.y_axis, "z": tag.z_axis,
                                "spec": tag.spec, "box": (u0, u1, v0, v1)}
                _check(lk in refs, "the un-turned tag of this layout was made first", lk)
                return origin_check(des, key, turn, refs[lk])
            step(f"create {key}: {layout} {OR().label(layout, turn)}", one)
    return refs


def edit_case(des, step):
    """Zach's board, White + black New body, Corner mark cut, a triangle
    pin: Rotate four times by the edit route."""
    import aruco_demo
    import aruco_chamfer_lab as CL
    root = des.rootComponent
    e = aruco_demo._box(root, -80.0, 20.0, -69.0, 26.0, 0.5, "plate ze")
    plate = e.bodies.item(0)
    st = {}

    def made():
        _make(_top_face(plate), "ze", "charuco", 0,
              {"size": 18.75, "square": 25.0, "mode": "both", "op": "new",
               "corner_mark": "cut", "pin": "triangle"})
        comp, rec = T.find_record(des, "ze")
        tag = T.M().TagDef.from_dict(rec, lambda m: None)
        body = T.pattern_body(comp, rec)
        frame = (tag.origin, tag.x_axis, tag.y_axis, tag.z_axis)
        (u0, u1), (v0, v1), _z = T._extents(body, frame)
        st.update(ref={"x": tag.x_axis, "y": tag.y_axis, "z": tag.z_axis, "spec": tag.spec,
                       "box": (u0, u1, v0, v1)}, token=_lab()._cf(des, "ze").entityToken)
        out = origin_check(des, "ze", 0, st["ref"])
        out["mark"] = CL.verify(des, "ze", under=plate)
        out["pin"] = _pin_at_origin(des, "ze")
        return out
    step("edit ze: Zach's 4 x 2 ChArUco, White + black New body, cut, triangle pin", made)
    turn = 0
    for i in range(4):
        nxt = OR().rotate_cw(turn)

        def one(turn=nxt, route=("APIFeatureEditCommand" if i == 1 else None)):
            res = F.scripted_edit(_lab()._cf(des, "ze"), settings={"turn": turn}, numbers={},
                                  route=route)
            cf = _lab()._cf(des, "ze")
            _check(not res.get("rebuilt") and cf is not None and cf.entityToken == st["token"],
                   "Rotate is a compute: the same node", res)
            out = origin_check(des, "ze", turn, st["ref"])
            out["mark"] = CL.verify(des, "ze", under=plate)
            out["pin"] = _pin_at_origin(des, "ze")
            return out
        step(f"edit ze: Rotate -> {OR().label('charuco', nxt)}"
             + (" (APIFeatureEditCommand)" if i == 1 else ""), one)
        turn = nxt


def _pin_at_origin(des, key):
    """The triangle pin (the tile's vertices under the face) sits in the
    tile corner nearest OpenCV's origin (a board) / corner 0."""
    comp, rec = T.find_record(des, key)
    tag = T.M().TagDef.from_dict(rec, lambda m: None)
    tile = comp.bRepBodies.itemByName(f"{F.TILE_BODY} {key}")
    opt = T.optical_frame_cm(tag)
    o, x, y, z = tag.origin, tag.x_axis, tag.y_axis, tag.z_axis
    # the record's plane is the white top; the face is white_top below it
    face = -F.SL().white_top("both", F.numbers_of(_lab()._cf(des, key))) / 10.0
    pins = [T._v(v.geometry) for v in tile.vertices
            if _dot(T._sub(T._v(v.geometry), o), z) < face - 1e-4]
    _check(pins, "the tile has its pin", key)
    c = [sum(p[i] for p in pins) / len(pins) for i in range(3)]
    d, k = T._sub(c, o), T._sub(opt[0], o)
    out = {"pin_uv_mm": [round(_dot(d, x) * 10, 3), round(_dot(d, y) * 10, 3)],
           "origin_uv_mm": [round(_dot(k, x) * 10, 3), round(_dot(k, y) * 10, 3)]}
    _check(_dot(d, x) * _dot(k, x) > 0 and _dot(d, y) * _dot(k, y) > 0,
           "the pin is in the quadrant of OpenCV's origin corner", out)
    return out


def variant_case(des, step):
    root = des.rootComponent
    import aruco_chamfer_lab as CL
    comp, occs = CL._part(root, "Part O", 6.0, 4.0, [CL._shift(-20.0, 40.0),
                                                     CL._shift(-20.0, 48.0)])
    body = comp.bRepBodies.itemByName("Body1")

    def made():
        face = _top_face(body).createForAssemblyContext(occs[0])
        _make(face, "vo", "grid", 90, {"size": 14.0, "marker_id": 10})
        _c, rec = T.find_record(des, "vo")
        sp = rec.get("spec") or {}
        _check((sp.get("cols"), sp.get("rows")) == (2, 3),
               "the 3 x 2 grid at 90 is OpenCV's 2 x 3", sp)
        return {"spec": f"{sp.get('cols')}x{sp.get('rows')}"}
    step("variant: Part O, a 3 x 2 grid at 90 (OpenCV's 2 x 3 on its side)", made)

    def renumber():
        return F.in_command(lambda: (T.write_override("", occs[1].name, "|vo", {"marker_id": 20}),
                                     T.apply_variants(des))[1])
    step("variant: copy 2 re-numbered (ArUco IDs -> OK)", renumber)

    def copy():
        _c, rec = T.find_record(des, "vo")
        found = [(o, info) for slot, items in T._existing_variants(root).items()
                 for o, info in items if slot and slot.endswith("|vo")]
        _check(found, "a variant part", list(T._existing_variants(root)))
        vocc, info = found[0]
        vcomp = vocc.component
        vrec = T.read_tag_records(vcomp)[0]
        vspec = T.M().TagSpec.from_dict(vrec["spec"], lambda m: None, "variant")
        out = {"template": f"{rec['spec']['cols']}x{rec['spec']['rows']}",
               "variant": f"{vspec.cols}x{vspec.rows}", "variant_ids": vspec.ids}
        _check((vspec.cols, vspec.rows) == (rec["spec"]["cols"], rec["spec"]["rows"])
               and vspec.ids[0] == 20, "the copy is the same board as built, re-numbered", out)
        cells = T.pattern_body(vcomp, vrec)
        o, x, y, z = T._frame_of(vrec)
        (_a, _b), (_c2, _d), (zlo, zhi) = T._extents(cells, (o, x, y, z))
        mid = (zlo + zhi) / 2.0
        inside = adsk.fusion.PointContainment.PointInsidePointContainment
        miss = 0
        for (x0, y0, x1, y1) in T.M().cell_boxes(vspec):
            u, v = (x0 + x1) * 50.0, (y0 + y1) * 50.0
            p = [o[i] + x[i] * u + y[i] * v + z[i] * mid for i in range(3)]
            if cells.pointContainment(adsk.core.Point3D.create(*p)) != inside:
                miss += 1
        out["cells_missing"] = miss
        _check(miss == 0, "the copy's cells are where its spec says", out)
        res = T.resolve_live(des)
        items = [i for i in T.M().print_items(res) if "|vo" in i.key or i.key.endswith("vo")]
        out["pdf_items"] = [(i.key, i.cols, i.rows, i.marker_id) for i in items]
        _check(items and all((i.cols, i.rows) == (vspec.cols, vspec.rows) for i in items),
               "the PDF sheet prints the board as built", out)
        return out
    step("variant: the copy's board is the one as built (spec, cells, PDF)", copy)


def dialog_case(des, step):
    """Opt-in: the real dialog on a 60 x 40 plate."""
    import aruco_demo
    import aruco_extrude_cmd as X
    import aruco_preview
    lab = _lab()
    root = des.rootComponent
    e = aruco_demo._box(root, -3.0, -2.0, 3.0, 2.0, 0.5, "plate dlg")
    lab._BENCH["des"] = des
    try:
        def cycle():
            tl0 = des.timeline.count
            inputs = lab._open_dialog()
            inputs.itemById("target").addSelection(_top_face(e.bodies.item(0)))
            lab._drive(inputs, "target")
            out = {}
            for layout in ("grid", "single", "charuco"):
                X._select(inputs, "layout", X.LAYOUT_LABELS, layout)
                lab._drive(inputs, "layout")
                og = inputs.itemById("origin")
                names = [og.listItems.item(i).name for i in range(og.listItems.count)]
                rows = []
                for _k in range(4):
                    ev = X._X.ev_root
                    tri = aruco_preview._st().triad
                    tag = T.M().TagDef(key="", component="", spec=ev["spec"],
                                       origin=list(ev["tag"][0]), x_axis=list(ev["tag"][1]),
                                       y_axis=list(ev["tag"][2]), z_axis=list(ev["tag"][3]))
                    want = T.optical_frame_cm(tag)
                    rows.append({"shown": og.selectedItem.name if og.selectedItem else None,
                                 "turn": X._X.turn, "board": f"{ev['spec'].cols}x{ev['spec'].rows}",
                                 "ghost_vs_opencv": _err(tri, want) if tri else None,
                                 "info_names_it": (OR().corner(X._X.turn) if layout != "single"
                                                   else OR().up(X._X.turn))
                                 in inputs.itemById("info").text})
                    lab._drive(inputs, "rotate")
                out[layout] = {"items": names, "rows": rows, "back_to": X._X.turn}
                _check(names == [lbl for lbl, _t in OR().items(layout)],
                       f"{layout}: the Origin list in its words, Rotate's order", out[layout])
                _check([r["turn"] for r in rows] == list(TURNS) and X._X.turn == 0
                       and [r["shown"] for r in rows] == names,
                       f"{layout}: Rotate steps clockwise through all four", out[layout])
                _check(all(r["ghost_vs_opencv"] and r["ghost_vs_opencv"][0] < TOL_MM
                           and r["ghost_vs_opencv"][1] < TOL_DEG and r["info_names_it"]
                           for r in rows),
                       f"{layout}: the ghost's axes are OpenCV's frame; the info names it",
                       out[layout])
            lab._close_dialog()
            out["timeline_unchanged"] = des.timeline.count == tl0
            out["groups"] = aruco_preview.leftover_groups(des)
            _check(out["timeline_unchanged"] and out["groups"] == 0, "Cancel leaves nothing", out)
            return out
        step("dialog: Origin words, Rotate, the ghost's OpenCV axes (grid / single / ChArUco)",
             cycle)
    finally:
        lab._BENCH["des"] = None
        X._X.no_remember = False


GROUPS = ("create", "edit", "variant")              # "dialog": opt-in (SAFETY)


def origin_test(out_dir="", keep="0", parts=",".join(GROUPS)):
    """The groups above, each in a NEW document of its own."""
    lab = _lab()
    import aruco_live_test
    want = [g for g in str(parts).split(",") if g]

    def one_group(name):
        def run(des):
            plates = []

            def body(step):
                if name == "create":
                    create_case(des, step, plates)
                    if out_dir:
                        os.makedirs(out_dir, exist_ok=True)
                        step("PnP renders", lambda: len(aruco_live_test.capture_tags(
                            des, out_dir, "pnp_origins")))
                elif name == "edit":
                    edit_case(des, step)
                elif name == "variant":
                    variant_case(des, step)
                elif name == "dialog":
                    dialog_case(des, step)
                else:
                    raise ValueError(f"unknown group {name!r}")
            res = lab.run_steps(des, body)
            if not res.get("aborted") and name != "dialog":
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
                               "jo_ok": jo.get("ok") if name != "dialog" else True,
                               "jo_record_ok": jo.get("record_ok"),
                               "jo_worst_mm": jo.get("worst_mm"),
                               "variants_skipped": jo.get("variants"),
                               "aborted": res.get("aborted")}
        if res.get("aborted"):
            out["aborted"] = f"{name}: {res['aborted']}"
            break
    out["verdict"] = ("PASS" if out["steps"] and all(s_["ok"] for s_ in out["steps"])
                      and all(g.get("jo_ok") for g in out["groups"].values())
                      and not out.get("aborted") else "FAIL")
    out["failed"] = [{"step": s_["step"], "error": s_.get("error"), "where": s_.get("where")}
                     for s_ in out["steps"] if not s_["ok"]]
    return out
