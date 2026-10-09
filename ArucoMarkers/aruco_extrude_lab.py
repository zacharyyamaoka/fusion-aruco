"""Bridge-only checks for ArUco Extrude (2026-10-08), each in a NEW unsaved
document that is closed afterwards (the user's document is put back):

    gim_fixture(root)   Zach's "GIM4305 Aruco Tag" structure, rebuilt: a
                        disc with three screw holes in a CHILD component whose
                        occurrence is moved and tilted, a ROOT sketch drawn on
                        the disc's top face (rectangle a little bigger than
                        the disc, construction diagonals, the holes projected
                        -> a 4-loop profile, like his Sketch3), and a second
                        disc under a root sketch on an offset plane.
    appearances(comp)   what colour every ArUco body really shows.
    cross_case(...)     the live_test steps for that fixture.
    colours(...)        the colour bug, step by step (create, compute, edit).
"""
from __future__ import annotations

import math
import traceback

import adsk.core
import adsk.fusion

import aruco_demo
import aruco_feature
import aruco_tags

P = adsk.core.Point3D.create
V = adsk.core.Vector3D.create
VI = adsk.core.ValueInput


# ----------------------------------------------------------------- fixture

def _mat(rz=0.0, rx=0.0, t=(0.0, 0.0, 0.0)):
    """Rotate rz about Z, then rx about X, then translate (cm)."""
    a, b = math.radians(rz), math.radians(rx)
    x = [math.cos(a), math.sin(a), 0.0]
    y = [-math.sin(a), math.cos(a), 0.0]
    z = [0.0, 0.0, 1.0]

    def rotx(v):
        return [v[0], v[1] * math.cos(b) - v[2] * math.sin(b),
                v[1] * math.sin(b) + v[2] * math.cos(b)]
    x, y, z = rotx(x), rotx(y), rotx(z)
    m = adsk.core.Matrix3D.create()
    m.setWithCoordinateSystem(P(*t), V(*x), V(*y), V(*z))
    return m


def _disc(comp, name, radius=1.85, height=0.6, holes=((1.35, 0.0), (-0.675, 1.1691),
                                                    (-0.675, -1.1691)), hole_r=0.15):
    sk = comp.sketches.add(comp.xYConstructionPlane)
    sk.name = f"{name} outline"
    sk.sketchCurves.sketchCircles.addByCenterRadius(P(0, 0, 0), radius)
    for (hx, hy) in holes:
        sk.sketchCurves.sketchCircles.addByCenterRadius(P(hx, hy, 0), hole_r)
    prof = max((sk.profiles.item(i) for i in range(sk.profiles.count)),
               key=lambda p: p.areaProperties().area)
    ext = comp.features.extrudeFeatures.addSimple(
        prof, VI.createByReal(height), adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    body = ext.bodies.item(0)
    body.name = name
    return body


def top_face(body, normal):
    best = None
    for f in body.faces:
        if not isinstance(f.geometry, adsk.core.Plane):
            continue
        ok, n = f.evaluator.getNormalAtPoint(f.pointOnFace)
        if n.x * normal[0] + n.y * normal[1] + n.z * normal[2] > 0.999:
            if best is None or f.area > best.area:
                best = f
    return best


def gim_fixture(root, offset=(0.0, 0.0, 0.0)):
    """-> dict of the fixture's pieces (see the module docstring); every
    piece moved by `offset` (cm) so live_test can put it beside its plates."""
    ox, oy, oz = offset
    out = {}
    # disc 1: "GIM disc":1, rotated 30 deg about Z, tilted 20 deg about X, moved
    m1 = _mat(rz=30.0, rx=20.0, t=(1.2 + ox, -0.7 + oy, 0.3 + oz))
    occ1 = root.occurrences.addNewComponent(m1)
    occ1.component.name = "GIM disc"
    disc1 = _disc(occ1.component, "Body1")
    # the occurrence position is captured, not a joint: fine for a fixture
    proxy1 = disc1.createForAssemblyContext(occ1)
    o, x, y, z = m1.getAsCoordinateSystem()
    face1 = top_face(proxy1, (z.x, z.y, z.z))
    sk = root.sketches.add(face1)
    sk.name = "Sketch3 (GIM)"
    # the screw holes are projected (Zach's had them auto-projected)
    for e in face1.edges:
        if isinstance(e.geometry, adsk.core.Circle3D) and e.geometry.radius < 0.2:
            sk.project(e)
    c = sk.modelToSketchSpace(_xf(m1, (0.0, 0.0, 0.6)))
    half = 1.9205                                    # 38.41 mm: bigger than the disc
    lines = sk.sketchCurves.sketchLines
    rect = lines.addCenterPointRectangle(c, P(c.x + half, c.y + half, c.z))
    for a, b in ((0, 2), (1, 3)):
        d = lines.addByTwoPoints(rect.item(a).startSketchPoint, rect.item(b).startSketchPoint)
        d.isConstruction = True
    out.update(occ1=occ1, disc1=disc1, sketch_face=sk, m1=m1)
    # disc 2: "Offset disc":1, moved only; a root sketch on an OFFSET PLANE at
    # the disc's top height
    m2 = _mat(rz=0.0, t=(7.0 + ox, oy, oz))
    occ2 = root.occurrences.addNewComponent(m2)
    occ2.component.name = "Offset disc"
    disc2 = _disc(occ2.component, "Body2", holes=())
    pl = root.constructionPlanes.createInput()
    pl.setByOffset(root.xYConstructionPlane, VI.createByReal(0.6 + oz))
    plane = root.constructionPlanes.add(pl)
    sk2 = root.sketches.add(plane)
    sk2.name = "on offset plane"
    lines2 = sk2.sketchCurves.sketchLines
    r2 = lines2.addCenterPointRectangle(P(7.0 + ox, oy, 0), P(8.2 + ox, 1.2 + oy, 0))
    d2 = lines2.addByTwoPoints(r2.item(0).startSketchPoint, r2.item(2).startSketchPoint)
    d2.isConstruction = True
    out.update(occ2=occ2, disc2=disc2, sketch_plane=sk2, ox=ox, oy=oy, oz=oz)
    return out


def _xf(m, p):
    q = P(*p)
    q.transformBy(m)
    return q


def rect_profile(sk):
    """The profile bounded by the rectangle: the biggest one (holes cut it)."""
    return max((sk.profiles.item(i) for i in range(sk.profiles.count)),
               key=lambda p: p.areaProperties().area)


# -------------------------------------------------------------- appearances

WANT = {"tag tile": aruco_tags.WHITE, "aruco cells": aruco_tags.BLACK,
        "aruco white": aruco_tags.WHITE}


def _is_piece(name):
    """A piece Fusion split off a tile: "tag tile <key> (1)"."""
    return (name.startswith(aruco_feature.TILE_BODY + " ") and name.endswith(")")
            and " (" in name and name[name.rfind(" (") + 2:-1].isdigit())


def appearances(comp, pieces_by_faces=False):
    """Every ArUco body of comp: its appearance, and the expected one; plus
    the faces of a joined tile (face-level appearance). pieces_by_faces: a
    tile PIECE (a pocket through the tile split it, "tag tile <key> (1)")
    passes when every one of its faces carries the colour -- what you see
    after a bare compute, before paint() gives it its body colour."""
    out = {}
    for b in comp.bRepBodies:
        want = next((v for k, v in WANT.items() if b.name.startswith(k)), None)
        try:
            got = b.appearance.name if b.appearance else None
        except Exception as exc:                   # noqa: BLE001
            got = f"? {exc}"
        faces = {}
        n_faces = 0
        for f in b.faces:
            n_faces += 1
            try:
                if f.appearance is not None:
                    faces[f.appearance.name] = faces.get(f.appearance.name, 0) + 1
            except Exception:                      # noqa: BLE001
                pass
        ok = want is None or got == want
        if not ok and pieces_by_faces and _is_piece(b.name):
            ok = n_faces > 0 and faces.get(want, 0) == n_faces
        out[b.name] = {"appearance": got, "want": want, "ok": ok, "faces": faces,
                       "piece": _is_piece(b.name)}
    return out


def colour_errors(comp):
    return {k: v for k, v in appearances(comp).items() if not v["ok"]}


def assert_colours(des, pieces_by_faces=False):
    """Every ArUco body in every component the colour Zach wants (raises).
    -> {component: number of ArUco bodies checked}."""
    bad, seen = {}, {}
    for comp in des.allComponents:
        app = appearances(comp, pieces_by_faces)
        n = sum(1 for v in app.values() if v["want"])
        if n:
            seen[comp.name] = n
        errs = {k: (v["appearance"], v["faces"]) for k, v in app.items() if not v["ok"]}
        if errs:
            bad[comp.name] = errs
    if bad:
        raise AssertionError(f"wrong colours: {bad}")
    return seen


# --------------------------------------------------------------- the cases

def _bbox_root(body, occ):
    bb = body.createForAssemblyContext(occ).boundingBox if occ else body.boundingBox
    return [[round(v * 10, 3) for v in (p.x, p.y, p.z)] for p in (bb.minPoint, bb.maxPoint)]


def _along(body, occ, m, origin_local=(0.0, 0.0, 0.6)):
    """z range (mm) of a body along the disc's normal, from its top face."""
    o = _xf(m, origin_local)
    _o, _x, _y, z = m.getAsCoordinateSystem()
    pb = body.createForAssemblyContext(occ) if occ else body
    ts = [((v.geometry.x - o.x) * z.x + (v.geometry.y - o.y) * z.y
           + (v.geometry.z - o.z) * z.z) * 10 for v in pb.vertices]
    return [round(min(ts), 3), round(max(ts), 3)] if ts else None


def make(target, **kw):
    """A tag the way a script makes one: the mode's own default Offsets /
    Heights, the black offset following the white top unless it is given."""
    s = aruco_feature.default_settings()
    n = aruco_feature.default_numbers(kw.get("mode", "both"))
    s["black_follows"] = "black_offset" not in kw
    for k, v in kw.items():
        (n if k in n else s)[k] = v
    return aruco_feature.in_command(aruco_feature.create, target, s, n)


def _check(cond, what, info):
    if not cond:
        raise AssertionError(f"{what}: {info}")


def cross_case(des, fx, step):
    """live_test steps: Zach's root sketch over a child component's disc
    (2026-10-08, "GIM4305 Aruco Tag"). Each step raises when it is wrong."""
    root = des.rootComponent
    comp1 = fx["occ1"].component
    comp2 = fx["occ2"].component

    def homes(key):
        return [c.name for c in des.allComponents
                if any(r.get("key") == key for r in aruco_tags.template_records(c))]

    def click_on_face():
        """profile_at: a click on the disc's face inside the rectangle is the
        rectangle's profile (what the dialog swaps a face pick for)."""
        on_disc = _xf(fx["m1"], (0.3, -0.4, 0.6))                 # inside, not in a hole
        off_rect = _xf(fx["m1"], (0.0, 0.0, 0.0))                  # the disc's bottom
        on_plane = P(7.0 + fx.get("ox", 0.0) + 0.5, fx.get("oy", 0.0) + 0.2, 0.6 + fx.get("oz", 0.0))
        a = aruco_feature.profile_at(on_disc)
        b = aruco_feature.profile_at(off_rect)
        c = aruco_feature.profile_at(on_plane)
        out = {"on_disc": a.parentSketch.name if a else None,
               "on_disc_loops": a.profileLoops.count if a else None,
               "disc_bottom": b.parentSketch.name if b else None,
               "on_offset_plane": c.parentSketch.name if c else None}
        _check(out["on_disc"] == fx["sketch_face"].name and out["on_disc_loops"] == 4,
               "a click on the disc inside the rectangle is not its profile", out)
        _check(out["disc_bottom"] is None, "a click off every sketch found a profile", out)
        _check(out["on_offset_plane"] == fx["sketch_plane"].name,
               "a click on the disc under the offset-plane rectangle is not its profile", out)
        return out
    step("GIM: a click on the face inside the sketch rectangle means the rectangle "
         "(profile_at, like Extrude)", click_on_face)

    def black_on_disc():
        vol0 = fx["disc1"].volume
        prof = rect_profile(fx["sketch_face"])
        root_bodies0 = root.bRepBodies.count
        rec = make(prof, mode="auto", key="gim", marker_id=5)
        cells = comp1.bRepBodies.itemByName("aruco cells gim")
        out = {"record_in": homes("gim"), "mode": (rec.get("fusion") or {}).get("mode"),
               "live": bool((rec.get("fusion") or {}).get("custom_feature")),
               "live_error": (rec.get("fusion") or {}).get("live_error"),
               "loops": prof.profileLoops.count,
               "marker_mm": round(rec["spec"]["marker_length_m"] * 1000, 2),
               "cells_along_disc_normal_mm": _along(cells, fx["occ1"], fx["m1"]) if cells else None,
               "disc_volume_mm3": [round(vol0 * 1000, 2), round(fx["disc1"].volume * 1000, 2)],
               "jo": (rec.get("fusion") or {}).get("joint_origin_name")}
        _check(out["record_in"] == [comp1.name], "record not in the disc's component", out)
        _check(out["mode"] == "black" and out["live"], "not a live black-only tag", out)
        _check(cells is not None and root.bRepBodies.count == root_bodies0,
               "cells not in the disc's component (or something landed in the root)", out)
        a = out["cells_along_disc_normal_mm"]
        _check(abs(a[0] + 0.6) < 1e-3 and abs(a[1]) < 1e-3, "cells not inlaid 0.6 mm, flush", out)
        _check(out["disc_volume_mm3"][1] < out["disc_volume_mm3"][0], "no pocket in the disc", out)
        _check(out["loops"] == 4, "the profile should keep the 3 holes", out)
        return out
    step("GIM: root sketch on a tilted child disc (holes projected, construction "
         "diagonals), auto -> black inlaid in the child", black_on_disc)

    def both_join_offset():
        prof = rect_profile(fx["sketch_plane"])
        h0 = fx["disc2"].boundingBox.maxPoint.z
        rec = make(prof, mode="both", op="join", key="off", marker_id=6, white_height=1.0)
        out = {"record_in": homes("off"),
               "live": bool((rec.get("fusion") or {}).get("custom_feature")),
               "live_error": (rec.get("fusion") or {}).get("live_error"),
               "comp_bodies": [b.name for b in comp2.bRepBodies],
               "disc_top_mm": [round(h0 * 10, 3), round(fx["disc2"].boundingBox.maxPoint.z * 10, 3)],
               "cells_bbox_root_mm": _bbox_root(comp2.bRepBodies.itemByName("aruco cells off"),
                                                fx["occ2"])
               if comp2.bRepBodies.itemByName("aruco cells off") else None}
        _check(out["record_in"] == [comp2.name] and out["live"], "not live in the disc's part", out)
        _check(sorted(out["comp_bodies"]) == ["Body2", "aruco cells off"],
               "the tile should be joined into Body2", out)
        _check(abs(out["disc_top_mm"][1] - out["disc_top_mm"][0] - 1.0) < 1e-3,
               "the joined tile should stand 1 mm on the disc", out)
        return out
    step("OFF: root sketch on an offset plane over a child disc, white + black joined",
         both_join_offset)

    def param_gim():
        cf = _cf(des, "gim")
        p = aruco_feature._param(cf, "marker_id")
        n_faces0 = comp1.bRepBodies.itemByName("aruco cells gim").faces.count
        p.value = 9.0
        cells = comp1.bRepBodies.itemByName("aruco cells gim")
        out = {"node": cf.name, "health": int(cf.healthState),
               "message": (cf.errorOrWarningMessage or "")[:200],
               "cells_faces": [n_faces0, cells.faces.count],
               "cells_along_disc_normal_mm": _along(cells, fx["occ1"], fx["m1"])}
        _check(out["health"] == 0, "the node is not healthy after the compute", out)
        a = out["cells_along_disc_normal_mm"]
        _check(abs(a[0] + 0.6) < 1e-3 and abs(a[1]) < 1e-3, "cells moved off the disc", out)
        return out
    step("GIM: parameter marker_id 5 -> 9 (compute through the stored transform)", param_gim)

    def edit_gim():
        res = aruco_feature.scripted_edit(_cf(des, "gim"), settings={"mode": "both", "op": "join"},
                                          numbers={"white_height": 1.2})
        out = {"result": res, "record_in": homes("gim"),
               "comp_bodies": sorted(b.name for b in comp1.bRepBodies)}
        _check(out["record_in"] == [comp1.name], "the rebuilt tag left the disc's part", out)
        _check(out["comp_bodies"] == ["Body1", "aruco cells gim"],
               "the rebuilt tile should be joined into Body1", out)
        return out
    step("GIM: structural edit black -> white + black joined (rebuilt in the child)", edit_gim)

    def old_tag_moves():
        """A tag made by the old add-in (it only looked for a body in the
        sketch's own component: a white + black new body in the ROOT, Zach's
        'top2') moves into the disc's part on a structural edit."""
        real = aruco_tags.candidate_bodies
        aruco_tags.candidate_bodies = lambda comp: [b for b in comp.bRepBodies if b.isSolid]
        try:
            make(rect_profile(fx["sketch_plane"]), mode="both", op="new", key="old",
                 marker_id=7, white_height=1.0)
        finally:
            aruco_tags.candidate_bodies = real
        before = {"record_in": homes("old"),
                  "root_jo": root.jointOrigins.itemByName("aruco old") is not None}
        res = aruco_feature.scripted_edit(_cf(des, "old"), settings={"mode": "black"},
                                          numbers={})
        out = {"before": before, "result": res, "record_in": homes("old"),
               "root_jo_left": root.jointOrigins.itemByName("aruco old") is not None,
               "part_jo": comp2.jointOrigins.itemByName("aruco old") is not None}
        _check(before["record_in"] == [root.name], "the old-style tag should start in the root", out)
        _check(out["record_in"] == [comp2.name] and not out["root_jo_left"] and out["part_jo"],
               "the rebuilt tag should move (record + Joint Origin) into the part", out)
        return out
    step("OLD: a root tag made before (no body found) moves into the part on rebuild",
         old_tag_moves)
    step("GIM/OFF: colours", lambda: assert_colours(des))


def _cf(des, key):
    """The node of the MODELLED tag `key`: never a copy's variant part (its
    record carries the template's key and no node -- 2026-10-09, the
    chamfer test's variant group got None here)."""
    _comp, r = aruco_tags.find_record(des, key)
    return aruco_feature.find_feature(des, r) if r is not None else None


# ------------------------------------------------------------ doc wrapper

class Abort(Exception):
    """The test's document is no longer the active one: stop, touch nothing."""


def guard(des):
    """Raise Abort unless `des` (the test's own design) is the ACTIVE one:
    the user may click his own document while a step pumps events, and
    every scripted command then runs there (2026-10-08: Fusion crashed with
    a test's node in Zach's selection)."""
    app = adsk.core.Application.get()
    doc = app.activeDocument
    if des is None or doc is None or doc != des.parentDocument:
        raise Abort(f"the active document is '{doc.name if doc else None}', not the test's "
                    f"'{des.parentDocument.name if des else None}': stopped, nothing touched")


def finish_doc(doc, before, keep=False):
    """Close the test's document (unless keep) and give the user his back --
    only if the test's is still in front: when he already moved on, his
    choice stands. First, in the test's own document only: any ArUco dialog
    the test opened is terminated (never a command in the user's document)
    and the selection is emptied, so nothing selected is closed away."""
    app = adsk.core.Application.get()
    ui = app.userInterface
    in_front = app.activeDocument == doc
    if in_front:
        try:
            if ui.activeCommand in ("bamArucoExtrude", aruco_feature.EDIT_CMD_ID,
                                    aruco_feature.AUTO_CMD_ID):
                ui.terminateActiveCommand()
                _pump_until(lambda: ui.activeCommand in ("SelectCommand", "", None), 5.0)
        except Exception:                          # noqa: BLE001
            pass
        aruco_feature.clear_selections()
    if not keep:
        try:
            doc.close(False)
        except Exception:                          # noqa: BLE001
            pass
    if in_front and before is not None:
        try:
            before.activate()
        except Exception:                          # noqa: BLE001
            pass


def in_new_doc(fn, keep="0"):
    """fn(des) in a NEW document; closed after unless keep=1; the user's
    document is put back. Refuses while Fusion is busy."""
    app = adsk.core.Application.get()
    before = app.activeDocument
    import aruco_actions
    aruco_actions._quiet_or_raise()
    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    aruco_actions._remember_active()
    des = adsk.fusion.Design.cast(app.activeProduct)
    des.designType = adsk.fusion.DesignTypes.ParametricDesignType
    try:
        return fn(des)
    except Exception as exc:                       # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}", "where": traceback.format_exc()[-1500:]}
    finally:
        finish_doc(doc, before, keep=str(keep) in ("1", "true", "yes"))


def colour_stages(des, step, dy=0.0):
    """The colours through every stage, every mode (live_test calls this):
    create, a parameter compute, an upstream compute, Fusion's edit route,
    a structural edit (rebuild), undo, computeAll. Every ArUco body must be
    its colour after each (assert_colours), and the joined tile's top and
    four walls white. dy (cm) moves the fixture."""
    root = des.rootComponent
    pl = root.constructionPlanes.createInput()
    pl.setByOffset(root.xYConstructionPlane, VI.createByString("20 mm"))
    sk = root.sketches.add(root.constructionPlanes.add(pl))
    sk.name = "free rect (colours)"
    sk.sketchCurves.sketchLines.addCenterPointRectangle(P(0, 12 + dy, 0), P(2, 14 + dy, 0))
    j = aruco_demo._box(root, -12, 10 + dy, -8, 14 + dy, 0.5, "plate J")    # white+black joined
    k = aruco_demo._box(root, -6, 10 + dy, -2, 14 + dy, 0.5, "plate K")     # black only
    w = aruco_demo._box(root, 4, 10 + dy, 8, 14 + dy, 0.5, "plate W")       # white only

    def joined_faces():
        """The joined tile's top + four walls on plate J, by colour (from the
        record: evaluate() at the timeline's end would see the pocketed face)."""
        out = {"top": {}, "wall": {}}
        rec = next((r for r in aruco_tags.read_tag_records(root) if r.get("key") == "cj"), None)
        cf = _cf(des, "cj")
        if rec is None or cf is None:
            return out
        tag = aruco_tags.M().TagDef.from_dict(rec, lambda m: None)
        nb = aruco_feature.numbers_of(cf)
        lo = nb["white_offset"] / 10.0
        lift = lo + nb["white_height"] / 10.0             # the white top: the record's plane
        z = list(tag.z_axis)
        o = [tag.origin[i] - z[i] * lift for i in range(3)]
        tw, th = (v * 100.0 for v in tag.spec.tile_size_m)
        tile = (o, list(tag.x_axis), list(tag.y_axis), z, tw, th)
        for f in aruco_feature._tile_faces(j.bodies.item(0), tile, lo, lift):
            ok, nrm = f.evaluator.getNormalAtPoint(f.pointOnFace)
            ts = [sum((a - b) * c for a, b, c in zip(aruco_feature._v(v.geometry), o, z))
                  for v in f.vertices]
            kind = ("top" if nrm.z > 0.99 and ts and min(ts) > lift - 1e-4
                    else "wall" if abs(nrm.z) < 0.01 and ts and min(ts) < lo + 1e-4 else None)
            if kind:
                name = f.appearance.name if f.appearance else None
                out[kind][name] = out[kind].get(name, 0) + 1
        return out

    def check(where, faces=True):
        seen = assert_colours(des)
        out = {"checked": seen}
        if faces:
            jf = joined_faces()
            out["joined_tile"] = jf
            white = aruco_tags.WHITE
            _check(jf["top"] and set(jf["top"]) == {white} and set(jf["wall"]) == {white}
                   and sum(jf["wall"].values()) >= 4,
                   f"{where}: the joined tile's top / walls are not white", jf)
        return out

    def made():
        make(sk.profiles.item(0), mode="both", op="new", key="cn", marker_id=12)
        make(top_face(j.bodies.item(0), (0, 0, 1)), mode="both", op="join", key="cj",
             marker_id=13, white_height=1.5)
        make(top_face(k.bodies.item(0), (0, 0, 1)), mode="black", key="ck", marker_id=14)
        make(top_face(w.bodies.item(0), (0, 0, 1)), mode="white", key="cw", marker_id=15,
             white_offset=0.0, white_height=0.4)
        return check("create")
    step("colours: create (new tile, joined tile, black only, white only)", made)

    def param():
        for key, v in (("cn", 16.0), ("cj", 17.0), ("ck", 18.0), ("cw", 19.0)):
            aruco_feature._param(_cf(des, key), "marker_id").value = v
        return check("param")
    step("colours: after a parameter compute (marker_id)", param)

    def upstream():
        j.extentOne.distance.expression = "7 mm"
        k.extentOne.distance.expression = "7 mm"
        # the record (where the check looks for the tile) follows at export
        aruco_feature.refresh_live(des)
        return check("upstream")
    step("colours: after an upstream compute (plates 5 -> 7 mm) + refresh_live", upstream)

    def edit_route():
        for key in ("cn", "cj", "ck"):
            aruco_feature.scripted_edit(_cf(des, key), settings={}, numbers={"black_height": -0.5})
        aruco_feature.scripted_edit(_cf(des, "cw"), settings={}, numbers={"white_height": 0.5})
        return check("edit")
    step("colours: after Fusion's edit route (black / white height)", edit_route)

    def rebuild():
        """2026-10-09: this edit kept Black only's black offset (0: the face),
        so the black lay UNDER the new tile and the pocket cut the tile in
        two ("tag tile ck (1)", grey). The black now keeps its step to its
        own surface: flush with the white top, one tile body."""
        aruco_feature.scripted_edit(_cf(des, "ck"), settings={"mode": "both", "op": "new"},
                                    numbers={"white_height": 1.0})
        out = check("rebuild")
        top = k.bodies.item(0).boundingBox.maxPoint.z
        cells = root.bRepBodies.itemByName("aruco cells ck")
        tiles = sorted(b.name for b in root.bRepBodies if b.name.startswith("tag tile ck"))
        out["cells_over_plate_mm"] = ([round((cells.boundingBox.minPoint.z - top) * 10, 4),
                                       round((cells.boundingBox.maxPoint.z - top) * 10, 4)]
                                      if cells else None)
        out["tile_bodies"] = tiles
        _check(tiles == ["tag tile ck"], "the rebuilt tile is ONE body (no piece cut off)", out)
        _check(out["cells_over_plate_mm"] is not None
               and abs(out["cells_over_plate_mm"][1] - 1.0) < 1e-3
               and abs(out["cells_over_plate_mm"][0] - 0.5) < 1e-3,
               "the black kept its step: flush with the 1 mm white top (0.5 deep)", out)
        return out
    step("colours: after a structural edit (black only -> white + black new body)", rebuild)

    def undo():
        ui = adsk.core.Application.get().userInterface
        ui.commandDefinitions.itemById("UndoCommand").execute()
        adsk.doEvents()
        return check("undo")
    step("colours: after undo", undo)

    def compute_all():
        des.computeAll()
        return check("computeAll")
    step("colours: after computeAll", compute_all)

    # A black that reaches the tile's underside: the pocket (the black's
    # bottom up to the white top) goes through the tile and cuts it into
    # pieces -- allowed geometry; every piece must be white.
    s_plate = aruco_demo._box(root, 10, 10 + dy, 14, 14 + dy, 0.5, "plate S")

    def pieces():
        return sorted(b.name for b in root.bRepBodies if b.name.startswith("tag tile cs"))

    def split_made():
        make(top_face(s_plate.bodies.item(0), (0, 0, 1)), mode="both", op="new", key="cs",
             marker_id=20, black_offset=0.0, black_height=-0.6)
        out = {"pieces": pieces(), "checked": assert_colours(des)}
        _check(len(out["pieces"]) >= 2, "the pocket should cut this tile into pieces", out)
        return out
    step("colours: a black under the tile (pocket through it): every piece white", split_made)

    def split_compute():
        aruco_feature._param(_cf(des, "cs"), "marker_id").value = 21.0
        return {"pieces": pieces(), "checked": assert_colours(des, pieces_by_faces=True)}
    step("colours: ... after a parameter compute (pieces: white faces)", split_compute)

    def split_compute_all():
        des.computeAll()
        return {"pieces": pieces(), "checked": assert_colours(des, pieces_by_faces=True)}
    step("colours: ... after computeAll (pieces: white faces)", split_compute_all)

    def split_painted():
        aruco_feature.refresh_live(des)                      # export does this; paint_all
        return {"pieces": pieces(), "checked": assert_colours(des)}
    step("colours: ... after refresh_live (every piece's body white)", split_painted)


def colours(keep="0"):
    """colour_stages in a new document."""
    return in_new_doc(lambda des: run_steps(des, lambda step: colour_stages(des, step)), keep)


def colour_probe(keep="0"):
    """Where does a base-feature body's appearance get lost?"""
    def run(des):
        root = des.rootComponent
        out = {}
        tbm = adsk.fusion.TemporaryBRepManager.get()
        black = aruco_tags.appearance(des, aruco_tags.BLACK)
        out["lookup"] = black.name if black else None

        def box(cx):
            return tbm.createBox(adsk.core.OrientedBoundingBox3D.create(
                P(cx, 0, 0.5), V(1, 0, 0), V(0, 1, 0), 1, 1, 1))

        def look(b):
            try:
                return {"body": b.appearance.name if b.appearance else None,
                        "valid": b.isValid}
            except Exception as exc:               # noqa: BLE001
                return f"? {exc}"

        # A: set after finishEdit (what create() does)
        bf, body = aruco_feature._base_body(root, box(0), "A")
        body.appearance = black
        out["A_after_set"] = look(body)
        out["A_bf_body_after_set"] = look(bf.bodies.item(0))
        # B: set on the source body inside the edit
        bf2 = root.features.baseFeatures.add()
        bf2.startEdit()
        b2 = root.bRepBodies.add(box(3), bf2)
        try:
            b2.appearance = black
            out["B_inside_edit"] = look(b2)
        except Exception as exc:                   # noqa: BLE001
            out["B_inside_edit"] = f"refused: {exc}"
        bf2.finishEdit()
        out["B_after_finish"] = look(bf2.bodies.item(0))
        # C: wrap A in a custom feature (does add() reset it?)
        st = aruco_feature._st()
        if st.definition is not None:
            inp = root.features.customFeatures.createInput(st.definition)
            inp.setStartAndEndFeatures(bf, bf)
            try:
                cf = root.features.customFeatures.add(inp)
                out["C_after_wrap"] = look(bf.bodies.item(0))
                body = bf.bodies.item(0)
                body.appearance = black
                out["C_set_after_wrap"] = look(bf.bodies.item(0))
                # D: updateBody (what compute does)
                aruco_feature._update(bf, box(0.2))
                out["D_after_updateBody"] = look(bf.bodies.item(0))
                cf.deleteMe()
            except Exception as exc:               # noqa: BLE001
                out["C_error"] = str(exc)[:300]
        # E: updateBody on a plain base feature
        aruco_feature._update(bf2, box(3.2))
        out["E_after_updateBody_plain"] = look(bf2.bodies.item(0))
        out["appearances_in_design"] = [des.appearances.item(i).name
                                        for i in range(des.appearances.count)]
        return out
    return in_new_doc(run, keep)


def run_steps(des, body):
    """A live_test-like step runner: body(step) -> {"steps": [...]}. Every
    step first checks that the test's document is still the active one
    (guard); if not, the run stops there (out["aborted"])."""
    st = aruco_feature._st()
    out = {"steps": []}

    def step(name, fn):
        n0 = getattr(st, "compute_n", 0)
        guard(des)                                 # Abort ends the whole run
        try:
            entry = {"step": name, "ok": True, "value": fn()}
        except Exception as exc:                   # noqa: BLE001
            entry = {"step": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:600],
                     "where": traceback.format_exc()[-900:]}
        try:
            guard(des)
        except Abort as exc:
            entry.update(ok=False, aborted=str(exc))
            out["steps"].append(entry)
            raise
        entry["computes"], ran = aruco_feature.computes_since(n0)
        entry["compute_errors"] = [c.get("error", "")[-300:] for c in ran
                                   if c.get("error")]
        extra = [{k: c.get(k) for k in ("feature", "paint", "notes", "ms") if c.get(k)}
                 for c in ran if c.get("paint") or c.get("notes")]
        if extra:
            entry["compute_notes"] = extra[:6]
        bad = []
        for i in range(des.timeline.count):
            it = des.timeline.item(i)
            try:
                if it.isGroup:
                    continue             # a group reads 5 (unknown): not a feature's health
                hs = int(it.healthState)
            except Exception:                      # noqa: BLE001
                hs = -1
            if hs != 0:
                bad.append({"i": i, "name": it.name, "health": hs,
                            "message": (getattr(it, "errorOrWarningMessage", "") or "")[:160]})
        entry["unhealthy"] = bad
        out["steps"].append(entry)
        return entry
    try:
        body(step)
    except Abort as exc:
        out["aborted"] = str(exc)
    return out


def cross(keep="0"):
    """Zach's GIM4305 structure in a new document, then cross_case."""
    def run(des):
        fx = gim_fixture(des.rootComponent)
        return run_steps(des, lambda step: cross_case(des, fx, step))
    return in_new_doc(run, keep)


# ---------------------------------------------------------- preview bench

def _pump_until(cond, timeout_s=10.0):
    import time as _t
    t0 = _t.perf_counter()
    while not cond() and _t.perf_counter() - t0 < timeout_s:
        adsk.doEvents()
        _t.sleep(0.005)
    return cond()


_BENCH = {"des": None}        # the bench's own design: every dialog step checks it


def _bench_guard():
    if _BENCH["des"] is not None:
        guard(_BENCH["des"])


def _open_dialog(route=None, select=None):
    """Open the ArUco Extrude dialog (or, with select=<node>, Fusion's edit
    route to it) for real, without auto-execute. -> the dialog's inputs."""
    import aruco_extrude_cmd as X
    _bench_guard()
    ui = adsk.core.Application.get().userInterface
    X._X.cmd = None
    X._X.no_remember = True          # a test's OK must not become the user's next defaults
    if select is not None:
        ui.activeSelections.clear()
        ui.activeSelections.add(select)
        ui.commandDefinitions.itemById(route or "APIFeatureEditCommand").execute()
    else:
        ui.commandDefinitions.itemById(X.CMD_ID).execute()
    if not _pump_until(lambda: X._X.cmd is not None):
        raise RuntimeError("the dialog did not open")
    _pump_until(lambda: False, 0.3)               # activate (edit: the roll)
    return X._X.cmd.commandInputs


def _drive(inputs, changed, setter=None):
    """One user change: set an input, then the events Fusion fires for it
    (inputChanged -> executePreview), timed."""
    import time as _t
    import aruco_extrude_cmd as X
    _bench_guard()
    t0 = _t.perf_counter()
    if setter is not None:
        setter()
    X.refresh(inputs, changed)
    t1 = _t.perf_counter()
    X._X.cmd.doExecutePreview()
    _pump_until(lambda: False, 0.05)
    t2 = _t.perf_counter()
    return {"change": changed, "refresh_ms": round((t1 - t0) * 1000, 1),
            "evaluate_ms": X._X.last.get("evaluate_ms"),
            "preview_ms": X._X.last.get("preview_ms"),
            "round_trip_ms": round((t2 - t0) * 1000, 1)}


def _close_dialog():
    import aruco_extrude_cmd as X
    _bench_guard()                   # never cancel a command in the user's document
    ui = adsk.core.Application.get().userInterface
    if ui.activeCommand not in (X.CMD_ID, aruco_feature.EDIT_CMD_ID):
        raise RuntimeError(f"not our dialog ({ui.activeCommand}): not cancelling it")
    ui.terminateActiveCommand()
    _pump_until(lambda: X._X.cmd is None, 5.0)
    return ui.activeCommand


def _mm3(p):
    return [round(p.x * 10, 2), round(p.y * 10, 2), round(p.z * 10, 2)]


def _pick(dd, name):
    for i in range(dd.listItems.count):
        if dd.listItems.item(i).name == name:
            dd.listItems.item(i).isSelected = True
            return True
    return False


def fresh_defaults(face):
    """A NEW dialog on `face`: in every mode the slabs start flush (Zach,
    2026-10-08: "by default at the start white and black should be the same
    height") -- the black's top at the white top, Black only's at the face,
    White only's white top at the face."""
    import aruco_extrude_cmd as X
    inputs = _open_dialog()
    inputs.itemById("target").addSelection(face)
    _drive(inputs, "target")
    rel = {}
    for mode in ("both", "black", "white"):
        X._select(inputs, "mode", X.MODE_LABELS, mode)
        _drive(inputs, "mode")
        f = dialog_numbers(inputs)
        wo, wh = f["white_offset"]["mm"], f["white"]["mm"]
        bo, bh = f["black_offset"]["mm"], f["black"]["mm"]
        w_top, b_top = max(wo, wo + wh), max(bo, bo + bh)
        rel[mode] = {"white": [wo, wh], "black": [bo, bh],
                     "flush": (abs(b_top - w_top) < 1e-6 if mode == "both" else
                               abs(b_top) < 1e-6 if mode == "black" else abs(w_top) < 1e-6)}
    _close_dialog()
    return {"modes": rel, "ok": all(r["flush"] for r in rel.values())}


def dialog_checks(inputs, shots=""):
    """The create dialog on a face, driven like a user: the Layout list (the
    built layout first and selected; every pick builds what it says), Type
    turning Auto size back on, the Origin corners and the ghost's Joint
    Origin triad following them, the pin group greyed out in Join."""
    import os
    import aruco_extrude_cmd as X
    import aruco_preview
    out = {}
    # --- Layout list on the face (candidates_on_loops)
    X._select(inputs, "layout", X.LAYOUT_LABELS, "grid")
    _drive(inputs, "layout")
    fit = inputs.itemById("fit")
    items = [fit.listItems.item(i).name for i in range(fit.listItems.count)]
    picks = []
    for k in range(1, min(4, len(items))):
        want = items[k].split("  (")[0]
        _drive(inputs, "fit", lambda k=k: setattr(fit.listItems.item(k), "isSelected", True))
        built = X._X.ev["layout"].label() if X._X.ev else None
        picks.append({"picked": want, "built": built, "first": fit.listItems.item(0).name,
                      "selected": fit.selectedItem.name if fit.selectedItem else None,
                      "ok": built == want and fit.selectedItem is not None
                      and fit.selectedItem.name == X.NOW + built})
    out["layout_list"] = {"items": items, "picks": picks,
                          "ms": X._X.last.get("candidates_ms")}
    # --- Type turns Auto size back on, unless a size was typed since
    inputs.itemById("auto").value = True
    X._select(inputs, "layout", X.LAYOUT_LABELS, "single")
    _drive(inputs, "layout")
    _drive(inputs, "size", lambda: setattr(inputs.itemById("size"), "value", 3.7))
    typed_auto = inputs.itemById("auto").value
    X._select(inputs, "layout", X.LAYOUT_LABELS, "grid")
    _drive(inputs, "layout")
    after_typed = inputs.itemById("auto").value
    X._select(inputs, "layout", X.LAYOUT_LABELS, "single")
    _drive(inputs, "layout")
    after_untyped = inputs.itemById("auto").value
    out["type_resets_auto"] = {"after_typing_size": typed_auto,
                               "type_change_after_typing": after_typed,
                               "type_change_again": after_untyped,
                               "ok": (not typed_auto) and (not after_typed) and after_untyped}
    # --- Zach, 2026-10-08: a white drag carries the black (flush stays
    # flush, a raise is kept); the black arrow changes only the black. (The
    # defaults, flush in every mode, are checked on a fresh dialog:
    # fresh_defaults.)
    rel = {}
    X._select(inputs, "mode", X.MODE_LABELS, "both")
    _drive(inputs, "mode")
    _drive(inputs, "white", lambda: setattr(inputs.itemById("white"), "value", 0.3))
    a = dialog_numbers(inputs)
    flush_after_drag = abs(a["black_offset"]["mm"] - 3.0) < 1e-6
    _drive(inputs, "black_offset", lambda: setattr(inputs.itemById("black_offset"), "value", 0.35))
    _drive(inputs, "white", lambda: setattr(inputs.itemById("white"), "value", 0.4))
    b = dialog_numbers(inputs)
    raise_kept = abs(b["black_offset"]["mm"] - 4.5) < 1e-6
    _drive(inputs, "black", lambda: setattr(inputs.itemById("black"), "value", -0.1))
    c = dialog_numbers(inputs)
    black_only = (abs(c["white"]["mm"] - 4.0) < 1e-6 and abs(c["black_offset"]["mm"] - 4.5) < 1e-6
                  and abs(c["black"]["mm"] + 1.0) < 1e-6)
    out["white_carries_black"] = {
        "white_2_to_3_black_offset": a["black_offset"]["mm"],
        "black_typed_3_5_then_white_4_black_offset": b["black_offset"]["mm"],
        "black_arrow_only": [c["white"]["mm"], c["black_offset"]["mm"], c["black"]["mm"]],
        "ok": flush_after_drag and raise_kept and black_only}
    _drive(inputs, "black_offset", lambda: setattr(inputs.itemById("black_offset"), "value", 0.4))
    _drive(inputs, "black", lambda: setattr(inputs.itemById("black"), "value", -0.06))
    # --- Origin: four for every Type (a board's corners, a marker's up); the triad follows
    og = inputs.itemById("origin")
    cyc = {}
    for kind in ("single", "grid"):
        X._select(inputs, "layout", X.LAYOUT_LABELS, kind)
        _drive(inputs, "layout")
        names = [og.listItems.item(i).name for i in range(og.listItems.count)]
        rows = []
        for nm in names:
            _drive(inputs, "origin", lambda nm=nm: _pick(og, nm))
            tri = aruco_preview._st().triad
            rows.append({"origin": nm, "turn": X._X.turn,
                         "triad_o_mm": [round(c * 10, 3) for c in tri[0]] if tri else None,
                         "triad_x": [round(c, 4) for c in tri[1]] if tri else None,
                         "triad_z": [round(c, 4) for c in tri[3]] if tri else None})
            if shots and kind == "single":
                aruco_demo.capture(os.path.join(shots, f"origin_{nm.replace(' ', '_').lower()}.png"),
                                   view="iso")
        cyc[kind] = {"items": names, "rows": rows}
        _drive(inputs, "origin", lambda kind=kind: _pick(og, X.OR().label(kind, 0)))
    out["origin"] = cyc
    # --- pins greyed out in Join, live in New body
    X._select(inputs, "mode", X.MODE_LABELS, "both")
    _drive(inputs, "mode")
    X._select(inputs, "op", X.OP_LABELS, "join")
    _drive(inputs, "op")
    join = dialog_numbers(inputs)
    X._select(inputs, "op", X.OP_LABELS, "new")
    _drive(inputs, "op")
    new = dialog_numbers(inputs)
    out["pins_enabled"] = {"join": {"shape": join["pin"]["enabled"], "note": join["pin_note_visible"]},
                           "new": {"shape": new["pin"]["enabled"], "note": new["pin_note_visible"],
                                   "size": new["pin_size"]["enabled"]},
                           "info_new": inputs.itemById("info").text[:700]}
    if shots:
        aruco_demo.capture(os.path.join(shots, "ghost_pin_new_body.png"), view="iso")
    X._select(inputs, "op", X.OP_LABELS, "join")
    _drive(inputs, "op")
    return out


def preview_bench(keep="0", shots=""):
    """Ghost timings, cleanup on Cancel, the edit dialog's ghost, and (for
    comparison) what building the real features costs."""
    import os
    import time as _t
    import aruco_extrude_cmd as X
    import aruco_preview

    def setv(inputs, cid, v):
        return lambda: setattr(inputs.itemById(cid), "value", v)

    def run(des):
        root = des.rootComponent
        out = {}
        _BENCH["des"] = des
        plate = aruco_demo._box(root, -4, -3, 4, 3, 0.5, "plate").bodies.item(0)    # 80 x 60
        face = top_face(plate, (0, 0, 1))
        out["fresh_defaults"] = fresh_defaults(face)
        fx = gim_fixture(root)
        tl0 = des.timeline.count
        # --- create dialog on the plate's face
        inputs = _open_dialog()
        steps = []
        inputs.itemById("target").addSelection(face)
        steps.append(_drive(inputs, "target"))
        groups_after_first = aruco_preview.leftover_groups(des)
        for v in (0.25, 0.3, 0.35):
            steps.append(_drive(inputs, "white", setv(inputs, "white", v)))
        steps.append(_drive(inputs, "black", setv(inputs, "black", 0.045)))
        steps.append(_drive(inputs, "marker_id", setv(inputs, "marker_id", 7)))
        steps.append(_drive(inputs, "mode",
                            lambda: X._select(inputs, "mode", X.MODE_LABELS, "both")))
        steps.append(_drive(inputs, "white", setv(inputs, "white", 0.4)))
        state = dialog_numbers(inputs)
        state.update({"white_manip": _mm3(inputs.itemById("white").manipulatorOrigin),
                      "black_manip": _mm3(inputs.itemById("black").manipulatorOrigin),
                      "info": inputs.itemById("info").text[:400]})
        steps.append(_drive(inputs, "layout",
                            lambda: X._select(inputs, "layout", X.LAYOUT_LABELS, "grid")))
        steps.append(_drive(inputs, "white", setv(inputs, "white", 0.3)))
        steps.append(_drive(inputs, "layout",
                            lambda: X._select(inputs, "layout", X.LAYOUT_LABELS, "charuco")))
        steps.append(_drive(inputs, "white", setv(inputs, "white", 0.25)))
        if shots:
            aruco_demo.capture(os.path.join(shots, "bench_ghost_charuco.png"), view="iso")
        out["dialog_checks"] = dialog_checks(inputs, shots)
        groups_open = aruco_preview.leftover_groups(des)
        after_close = _close_dialog()
        out["create_dialog"] = {"steps": steps, "state_after_drags": state,
                                "groups_after_first_preview": groups_after_first,
                                "groups_while_open": groups_open,
                                "groups_after_cancel": aruco_preview.leftover_groups(des),
                                "timeline_unchanged": des.timeline.count == tl0,
                                "active_after_cancel": after_close}
        # --- the GIM case through the dialog: root sketch over Component1's disc
        inputs = _open_dialog()
        prof = rect_profile(fx["sketch_face"])
        inputs.itemById("target").addSelection(prof)
        st1 = _drive(inputs, "target")
        st2 = _drive(inputs, "black", setv(inputs, "black", 0.05))
        out["gim_dialog"] = {"steps": [st1, st2], "mode": X._X.mode,
                             "info": inputs.itemById("info").text[:500],
                             "black_manip_root_mm": _mm3(inputs.itemById("black").manipulatorOrigin),
                             "black_dir": _mm3(inputs.itemById("black").manipulatorDirection),
                             "disc_top_centre_root_mm": _mm3(_xf(fx["m1"], (0, 0, 0.6)))}
        if shots:
            aruco_demo.capture(os.path.join(shots, "bench_ghost_gim.png"), view="iso")
        _close_dialog()
        out["gim_dialog"]["groups_after_cancel"] = aruco_preview.leftover_groups(des)
        # --- real features, for comparison (what an executePreview building
        # them would cost per update, before Fusion's own roll-back)
        real = []
        for kind in ("single", "grid"):
            t0 = _t.perf_counter()
            make(face, mode="both", op="join", layout=kind, key=f"r{kind[0]}", marker_id=20)
            real.append({"layout": kind, "create_ms": round((_t.perf_counter() - t0) * 1000, 1)})
        out["real_features"] = real
        # --- the edit dialog: ghost with the timeline rolled to the node
        cf = _cf(des, "rs")
        marker0 = des.timeline.markerPosition
        inputs = _open_dialog(select=cf)
        ed = aruco_feature._st().edit or {}
        edit_state = {"how": ed.get("how"), "has_cf": ed.get("cf") is not None,
                      "error": ed.get("activate_error"),
                      "marker_at_open": des.timeline.markerPosition,
                      "active": adsk.core.Application.get().userInterface.activeCommand}
        _edit_log_mark = aruco_feature._edit_log("bench read", cf, how=ed.get("how"),
                                                 same=ed is aruco_feature._st().edit)
        e1 = _drive(inputs, "activate")
        e2 = _drive(inputs, "white", setv(inputs, "white", 0.5))
        aruco_feature._edit_log("bench after drives", cf,
                                how=(aruco_feature._st().edit or {}).get("how"),
                                result=(aruco_feature._st().edit or {}).get("result"))
        rolled = des.timeline.markerPosition
        if shots:
            aruco_demo.capture(os.path.join(shots, "bench_ghost_edit.png"), view="iso")
        g_open = aruco_preview.leftover_groups(des)
        _close_dialog()
        edit_state["log"] = list(getattr(aruco_feature._st(), "edit_log", []))[-10:]
        out["edit_dialog"] = {"steps": [e1, e2], "marker_before": marker0, "state": edit_state,
                              "marker_while_open": rolled,
                              "marker_after_cancel": des.timeline.markerPosition,
                              "groups_while_open": g_open,
                              "groups_after_cancel": aruco_preview.leftover_groups(des),
                              "white_height_param_after_cancel_mm":
                                  aruco_feature.numbers_of(cf)["white_height"]}
        # --- OK in the edit dialog: white 2 -> 3 mm, black standing 1 mm on it
        inputs = _open_dialog(select=cf)
        _drive(inputs, "activate")
        _drive(inputs, "white", setv(inputs, "white", 0.3))
        _drive(inputs, "black", setv(inputs, "black", 0.1))
        X._X.cmd.doExecute(True)
        _pump_until(lambda: X._X.cmd is None, 10.0)
        cf = _cf(des, "rs")            # the black stands on the white: no pocket -> rebuilt
        n_after = aruco_feature.numbers_of(cf)
        out["edit_ok"] = {"numbers": {k: n_after[k] for k in aruco_feature.HEIGHT_PARAMS},
                          "parts": (aruco_feature.settings_of(cf) or {}).get("parts"),
                          "marker": des.timeline.markerPosition, "count": des.timeline.count,
                          "groups": aruco_preview.leftover_groups(des),
                          "colours": colour_errors(root),
                          "log": list(getattr(aruco_feature._st(), "edit_log", []))[-4:]}
        # --- OK in the create dialog, on plate 2
        plate2 = aruco_demo._box(root, -4, 4, 4, 9, 0.5, "plate 2").bodies.item(0)
        inputs = _open_dialog()
        inputs.itemById("target").addSelection(top_face(plate2, (0, 0, 1)))
        _drive(inputs, "target")
        X._select(inputs, "mode", X.MODE_LABELS, "both")
        _drive(inputs, "mode")
        _drive(inputs, "white", setv(inputs, "white", 0.15))
        _drive(inputs, "black", setv(inputs, "black", 0.05))
        X._X.cmd.doExecute(True)
        _pump_until(lambda: X._X.cmd is None, 10.0)
        recs = [r for r in aruco_tags.read_tag_records(root)
                if r.get("key") not in ("rs", "rg")]
        made = recs[-1] if recs else None
        cfm = aruco_feature.find_feature(des, made) if made else None
        out["create_ok"] = {"key": made.get("key") if made else None,
                            "numbers": aruco_feature.numbers_of(cfm) if cfm else None,
                            "mode": (aruco_feature.settings_of(cfm) or {}).get("mode") if cfm else None,
                            "groups": aruco_preview.leftover_groups(des),
                            "colours": colour_errors(root)}
        return out
    try:
        return in_new_doc(run, keep)
    finally:
        _BENCH["des"] = None
        import aruco_extrude_cmd as X
        X._X.no_remember = False


# ------------------------------------------------- interactive session aids

def _ui_state_mod():
    import sys
    import types
    st = sys.modules.get("_aruco_lab_ui")
    if st is None:
        st = types.ModuleType("_aruco_lab_ui")
        st.doc = None
        st.before = None
        sys.modules["_aruco_lab_ui"] = st
    return st


def ui_fixture():
    """A NEW document for the hand checks, left open and active: Zach's GIM
    structure (root sketch on a child disc) and a plate. ui_close() closes
    it and puts the user's document back."""
    import aruco_actions
    app = adsk.core.Application.get()
    aruco_actions._quiet_or_raise()
    st = _ui_state_mod()
    st.before = app.activeDocument
    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    aruco_actions._remember_active()
    st.doc = doc
    des = adsk.fusion.Design.cast(app.activeProduct)
    des.designType = adsk.fusion.DesignTypes.ParametricDesignType
    root = des.rootComponent
    gim_fixture(root)
    aruco_demo._box(root, -9, -3, -3, 3, 0.5, "plate")          # 60 x 60 x 5 mm
    vp = app.activeViewport
    cam = vp.camera
    cam.viewOrientation = adsk.core.ViewOrientations.IsoTopRightViewOrientation
    cam.isFitView = True
    vp.camera = cam
    vp.refresh()
    return {"document": doc.name, "before": st.before.name if st.before else None}


def _ui_mine():
    app = adsk.core.Application.get()
    st = _ui_state_mod()
    if st.doc is None or not st.doc.isValid or app.activeDocument != st.doc:
        raise PermissionError("the hand-check document is not the active one")
    return st


def ui_open(pick="", mode="", op="", layout="", pin="", origin=""):
    """Open the ArUco Extrude dialog in the hand-check document; with
    pick=<body name>, click its top face for the user (and set mode / op /
    layout / pin / origin) so the dialog shows a tag."""
    import aruco_actions
    import aruco_extrude_cmd as X
    _ui_mine()
    aruco_actions._quiet_or_raise()
    X._X.cmd = None
    adsk.core.Application.get().userInterface.commandDefinitions.itemById(
        "bamArucoExtrude").execute()
    out = {"opened": True}
    if pick:
        if not _pump_until(lambda: X._X.cmd is not None):
            raise RuntimeError("the dialog did not open")
        _pump_until(lambda: False, 0.3)
        _ui_mine()                                  # still the hand-check document
        des = adsk.fusion.Design.cast(adsk.core.Application.get().activeProduct)
        body = des.rootComponent.bRepBodies.itemByName(pick)
        if body is None:
            raise ValueError(f"no body {pick!r} in the hand-check document")
        inputs = X._X.cmd.commandInputs
        inputs.itemById("target").addSelection(top_face(body, (0, 0, 1)))
        X.refresh(inputs, "target")
        for cid, val, table in (("mode", mode, X.MODE_LABELS), ("op", op, X.OP_LABELS),
                                ("layout", layout, X.LAYOUT_LABELS), ("pin", pin, X.PIN_LABELS)):
            if val:
                X._select(inputs, cid, table, val)
                X.refresh(inputs, cid)
        if origin:
            _pick(inputs.itemById("origin"), origin)
            X.refresh(inputs, "origin")
        X._X.cmd.doExecutePreview()
        _pump_until(lambda: False, 0.2)
        out["fields"] = dialog_numbers(inputs)
    return out


def ui_state():
    """READ-ONLY: the open ArUco Extrude dialog as the add-in sees it, and
    where its two arrows' tips are in viewport pixels."""
    import aruco_extrude_cmd as X
    import aruco_preview
    app = adsk.core.Application.get()
    des = adsk.fusion.Design.cast(app.activeProduct)
    out = {"active_command": app.userInterface.activeCommand,
           "document": app.activeDocument.name,
           "groups": aruco_preview.leftover_groups(des),
           "timeline": [des.timeline.markerPosition, des.timeline.count],
           "x": {"mode": X._X.mode, "delta": X._X.delta, "touched": sorted(X._X.touched),
                 "last": dict(X._X.last), "clamp_notes": list(X._X.clamp_notes)},
           "preview_ms_tail": list(aruco_preview._st().timings[-12:])}
    cmd = X._X.cmd
    if cmd is not None:
        try:
            inputs = cmd.commandInputs
            sel = inputs.itemById("target")
            if sel.selectionCount:
                ent = sel.selection(0).entity
                out["selected"] = ent.objectType.rsplit("::", 1)[-1]
                ctx = getattr(ent, "assemblyContext", None)
                out["selected_in"] = ctx.fullPathName if ctx else "root"
            out["fields"] = dialog_numbers(inputs)
            vp = app.activeViewport
            for cid in ("white", "black", "pin_length"):
                inp = inputs.itemById(cid)
                o, d = inp.manipulatorOrigin, inp.manipulatorDirection
                tip = adsk.core.Point3D.create(o.x + d.x * inp.value, o.y + d.y * inp.value,
                                               o.z + d.z * inp.value)
                p2 = vp.modelToViewSpace(tip)
                out[cid] = {"visible": inp.isVisible, "enabled": inp.isEnabled,
                            "mm": round(inp.value * 10, 3),
                            "tip_view_px": [round(p2.x), round(p2.y)]}
            out["viewport_px"] = [vp.width, vp.height]
            out["info"] = inputs.itemById("info").text[:600]
        except Exception as exc:                   # noqa: BLE001
            out["error"] = str(exc)[:200]
    return out


def dialog_numbers(inputs):
    """What the dialog's Offset / Height / pin fields show (mm), which of
    them are visible / enabled, and the Layout list."""
    out = {}
    for cid in ("white_offset", "white", "black_offset", "black", "pin_size", "pin_length",
                "pin_tol"):
        inp = inputs.itemById(cid)
        if inp is not None:
            out[cid] = {"mm": round(inp.value * 10, 4), "visible": inp.isVisible,
                        "enabled": inp.isEnabled, "label": inp.name}
    for cid in ("pin", "mode", "op", "layout", "fit", "origin"):
        inp = inputs.itemById(cid)
        if inp is not None:
            out[cid] = {"selected": inp.selectedItem.name if inp.selectedItem else None,
                        "enabled": inp.isEnabled, "visible": inp.isVisible,
                        "items": [inp.listItems.item(i).name for i in range(inp.listItems.count)]
                        if cid in ("fit", "origin") else None}
    note = inputs.itemById("pin_note")
    out["pin_note_visible"] = bool(note and note.isVisible)
    out["auto"] = inputs.itemById("auto").value
    out["size_mm"] = round(inputs.itemById("size").value * 10, 3)
    return out


def ui_close():
    """Cancel OUR dialog (only ours) in the hand-check document, close that
    document, put the user's document back."""
    app = adsk.core.Application.get()
    ui = app.userInterface
    st = _ui_state_mod()
    out = {}
    if st.doc is not None and st.doc.isValid and app.activeDocument == st.doc:
        if ui.activeCommand in ("bamArucoExtrude", "bamArucoExtrudeEdit"):
            ui.terminateActiveCommand()
            out["terminated"] = True
            _pump_until(lambda: ui.activeCommand == "SelectCommand", 5.0)
        import aruco_preview
        out["groups_left"] = aruco_preview.leftover_groups(
            adsk.fusion.Design.cast(app.activeProduct))
        if ui.activeCommand not in ("SelectCommand", "", None):
            raise RuntimeError(f"Fusion is busy ({ui.activeCommand}): not closing")
        out["closed"] = st.doc.name
        st.doc.close(False)
        st.doc = None
    if st.before is not None:
        try:
            st.before.activate()
            out["reactivated"] = st.before.name
        except Exception as exc:                   # noqa: BLE001
            out["reactivate_error"] = str(exc)[:120]
    return out


def probe_doc(document, sketch="Sketch3", x_mm="0", y_mm="1", z_mm="6"):
    """READ-ONLY, on an open document by name (not activated): what ArUco
    Extrude makes of a click at (x, y, z) mm and of that sketch's biggest
    profile -- profile_at, body_under, creation_settings, evaluate. Writes
    nothing (evaluate is pure geometry on the current timeline state)."""
    app = adsk.core.Application.get()
    doc = next((app.documents.item(i) for i in range(app.documents.count)
                if app.documents.item(i).name == document), None)
    if doc is None:
        raise ValueError(f"no open document {document!r}")
    des = adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
    pt = P(float(x_mm) / 10.0, float(y_mm) / 10.0, float(z_mm) / 10.0)
    out = {"document": doc.name, "active_is_it": app.activeDocument == doc}
    hit = aruco_feature.profile_at(pt, des=des)
    out["click"] = ({"sketch": aruco_feature.native_of(hit).parentSketch.name,
                     "loops": hit.profileLoops.count,
                     "area_mm2": round(hit.areaProperties().area * 100, 2)} if hit else None)
    sk = des.rootComponent.sketches.itemByName(sketch)
    if sk is not None and sk.profiles.count:
        prof = hit or max((sk.profiles.item(i) for i in range(sk.profiles.count)),
                          key=lambda p_: p_.areaProperties().area)
        body, note = aruco_feature.body_under(prof)
        out["body_under"] = (body.name if body else None, getattr(getattr(body, "assemblyContext", None),
                                                                   "fullPathName", None), note)
        s, _b = aruco_feature.creation_settings(prof, aruco_feature.default_settings())
        out["host"] = aruco_feature.host_of(prof, body, s).name
        out["host_xform_set"] = bool(s.get("host_xform"))
        s = dict(s, mode="black" if s.get("has_body") else "both")
        try:
            ev = aruco_feature.evaluate(prof, s, aruco_feature.default_numbers(s["mode"]))
            out["auto_mode"] = s["mode"]
            out["layout"] = ev["layout"].label()
            out["notes"] = ev["notes"]
            out["tag_origin_mm"] = [round(v * 10, 2) for v in ev["tag"][0]]
        except Exception as exc:                   # noqa: BLE001
            out["evaluate_error"] = str(exc)[:300]
    return out
