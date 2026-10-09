"""Fusion side of ArUco tags: build them, regenerate them, read them back.

What a tag IS in the design (measured by aruco_probe on 2026-10-07):

    <component>
      tile body            the user's body (or our ArUco Extrude), White
      base feature         "ArUco cells <key>" — ONE solid built with the
                           TemporaryBRepManager from the marker's black
                           cells, grown 10 um so corner contacts fuse
                           (probe: 9 boxes -> 1 lump, isSolid)
      combine (cut, keep)  pockets the tile, so the cells sit flush, Black
      ...later features    holes etc. keep working: the probe's hole after
                           the tag stayed healthy through a regeneration
    attribute bam_aruco/tags on the component = [TagDef.to_dict(), ...]

Changing the id swaps the base feature's body (`updateBody` with the
feature's own `sourceBodies` entry) — the combine and every later feature
recompute on the new pattern.

Per-copy ids (markers.resolve) are realised as VARIANT components: a tag
part whose bodies are dumb copies of the template's tile (old pockets filled)
plus new cells. It is placed in the component that owns the winning
override, at the template tag's pose, rigidly joined to the copy's host, and
the template tag is hidden for that one path (probe: per-path light bulbs
are independent, even across two copies of the same arm).
"""
from __future__ import annotations

import json
import math

import adsk.core
import adsk.fusion

import aruco_core

G = "bam_aruco"
TAGS_ATTR = "tags"
OVERRIDES_ATTR = "overrides"
VARIANT_ATTR = "variant"
WHITE = "Plastic - Matte (White)"
BLACK = "Plastic - Matte (Black)"


def M():
    return aruco_core.module("markers")


def _app():
    return adsk.core.Application.get()


def design():
    return adsk.fusion.Design.cast(_app().activeProduct)


# ------------------------------------------------------------- appearances

def appearance(des, name):
    found = des.appearances.itemByName(name)
    if found:
        return found
    libs = _app().materialLibraries
    for i in range(libs.count):
        lib = libs.item(i)
        try:
            item = lib.appearances.itemByName(name)
        except Exception:                          # noqa: BLE001
            item = None
        if item:
            return des.appearances.addByCopy(item, name)
    return None


# ------------------------------------------------------------------ vectors

def _v(p):
    return [p.x, p.y, p.z]


def _sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def _unit(a):
    n = math.sqrt(_dot(a, a)) or 1.0
    return [a[0] / n, a[1] / n, a[2] / n]


def _rot(v, axis, deg):
    """Rodrigues: v rotated about unit `axis` by `deg`."""
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    k = axis
    kxv = _cross(k, v)
    kdv = _dot(k, v)
    return [v[i] * c + kxv[i] * s + k[i] * kdv * (1 - c) for i in range(3)]


# --------------------------------------------------------------- the frame

def face_frame(face, rotate_deg=0.0):
    """A planar face -> (origin, x, y, z, width_cm, height_cm), native coords.

    z is the outward normal at the centroid, x follows the face's longest
    straight edge (so a rectangle's tag is square to its sides), y = z x x.
    Width/height are the face's extent along x/y, centred on the centroid of
    those extents rather than the area centroid, so a hole in the tile does
    not push the marker off centre.
    """
    if face.assemblyContext:
        face = face.nativeObject
    ok, normal = face.evaluator.getNormalAtPoint(face.pointOnFace)
    z = _unit(_v(normal))
    best, best_len = None, -1.0
    for e in face.edges:
        g = e.geometry
        if isinstance(g, adsk.core.Line3D):
            d = _sub(_v(g.endPoint), _v(g.startPoint))
            length = math.sqrt(_dot(d, d))
            if length > best_len:
                best, best_len = d, length
    if best is None:
        plane = face.geometry
        best = _v(plane.uDirection)
    x = _unit(_sub(best, [z[i] * _dot(best, z) for i in range(3)]))
    if rotate_deg:
        x = _unit(_rot(x, z, rotate_deg))
    y = _cross(z, x)
    pts = [_v(v.geometry) for v in face.vertices]
    p0 = _v(face.pointOnFace)
    if not pts:
        pts = [p0]
    xs = [_dot(_sub(p, p0), x) for p in pts]
    ys = [_dot(_sub(p, p0), y) for p in pts]
    cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
    origin = [p0[i] + x[i] * cx + y[i] * cy for i in range(3)]
    return origin, x, y, z, max(xs) - min(xs), max(ys) - min(ys)


def face_loops_2d(face, origin, x, y, tol_cm=0.005):
    """Every loop of a planar face as a 2D polygon (cm) in (origin, x, y)."""
    loops = []
    for loop in face.loops:
        pts = []
        for co in loop.coEdges:
            ev = co.edge.evaluator
            ok, p0, p1 = ev.getParameterExtents()
            ok, strokes = ev.getStrokes(p0, p1, tol_cm)
            seg = [_v(p) for p in strokes] if ok else []
            if co.isOpposedToEdge:
                seg.reverse()
            for p in seg[:-1] if seg else []:
                d = _sub(p, origin)
                pts.append((_dot(d, x), _dot(d, y)))
        if len(pts) >= 3:
            loops.append(pts)
    return loops


def face_best_rect(face, spec, rotate_deg=0.0):
    """face_frame, but centred on the largest uninterrupted rectangle."""
    o, x, y, z, w, h = face_frame(face, 0.0)
    native = face.nativeObject if face.assemblyContext else face
    loops = face_loops_2d(native, o, x, y)
    best = M().largest_rect(loops, aspect=M().tile_aspect(spec))
    if best is None:
        return face_frame(face, rotate_deg)
    t = math.radians(best["angle_deg"])
    xr = [math.cos(t) * x[i] + math.sin(t) * y[i] for i in range(3)]
    origin = [o[i] + x[i] * best["cx"] + y[i] * best["cy"] for i in range(3)]
    w, h = best["w"], best["h"]
    if rotate_deg:
        xr = _unit(_rot(xr, z, rotate_deg))
        if round(rotate_deg) % 180:
            w, h = h, w
    yr = _cross(z, xr)
    return origin, xr, yr, z, w, h


PATTERN_BODY_PREFIXES = ("aruco cells", "aruco white")


def candidate_bodies(comp):
    """The solid bodies a sketch in `comp` can sit on, in comp's space: its
    own, then every body of every occurrence below it (as proxies), visible
    ones first. Zach's GIM4305 case (2026-10-08): a ROOT sketch drawn on the
    disc of Component1:1 -- the body under it is not the root's."""
    out = list(comp.bRepBodies)
    try:
        for occ in comp.allOccurrences:
            out += list(occ.bRepBodies)
    except Exception:                              # noqa: BLE001
        pass
    out = [b for b in out if b.isSolid and not b.name.startswith(PATTERN_BODY_PREFIXES)]
    return sorted(out, key=lambda b: 0 if b.isVisible else 1)


def external_owner(ent):
    """The externally referenced (xref) occurrence an entity lives in, or
    None. Such a body cannot take a combine: it is read-only here."""
    occ = getattr(ent, "assemblyContext", None)
    while occ is not None:
        try:
            if occ.isReferencedComponent:
                return occ
            occ = occ.assemblyContext
        except Exception:                          # noqa: BLE001
            return None
    return None


def profile_frame(profile, rotate_deg=0.0):
    """A closed sketch profile -> (origin, x, y, z, w, h, face, body), model cm.

    x follows the profile's longest straight edge, z is the sketch normal
    turned outward from the body the sketch sits on (when it sits on one),
    w/h are the profile's extents along x/y. face/body are what is UNDER
    the sketch: the face it was drawn on, or None -- in the sketch
    component's space, so a body of an occurrence below that component comes
    back as its PROXY (see candidate_bodies).
    """
    sk = profile.parentSketch
    pts, best, best_len = [], None, -1.0
    for loop in profile.profileLoops:
        for pc in loop.profileCurves:
            # profileCurve.sketchEntity is a generic SketchEntity: cast it
            ent = (adsk.fusion.SketchLine.cast(pc.sketchEntity)
                   or adsk.fusion.SketchArc.cast(pc.sketchEntity)
                   or pc.sketchEntity)
            # sample the curve itself, so arcs, circles and splines count too
            try:
                ev = pc.geometry.evaluator
                ok, p0, p1 = ev.getParameterExtents()
                ok, strokes = ev.getStrokes(p0, p1, 0.01)
                for q in (strokes if ok else []):
                    pts.append(_v(sk.sketchToModelSpace(q)))
            except Exception:                      # noqa: BLE001
                pass
            for sp in (getattr(ent, "startSketchPoint", None), getattr(ent, "endSketchPoint", None)):
                if sp is not None:
                    pts.append(_v(sk.sketchToModelSpace(sp.geometry)))
            if isinstance(ent, adsk.fusion.SketchLine):
                a = _v(sk.sketchToModelSpace(ent.startSketchPoint.geometry))
                b = _v(sk.sketchToModelSpace(ent.endSketchPoint.geometry))
                d = _sub(b, a)
                if math.sqrt(_dot(d, d)) > best_len:
                    best, best_len = d, math.sqrt(_dot(d, d))
    o0 = _v(sk.sketchToModelSpace(adsk.core.Point3D.create(0, 0, 0)))
    ex = _sub(_v(sk.sketchToModelSpace(adsk.core.Point3D.create(1, 0, 0))), o0)
    ey = _sub(_v(sk.sketchToModelSpace(adsk.core.Point3D.create(0, 1, 0))), o0)
    z = _unit(_cross(ex, ey))
    # What is UNDER the rectangle, found by geometry: reading
    # sketch.referencePlane for a sketch on a face needs the timeline rolled
    # back (Fusion: "referencePlane is a BRepFace - need to roll timeline back").
    c = [sum(p[i] for p in pts) / len(pts) for i in range(3)]
    face, body = None, None
    inside = adsk.fusion.PointContainment.PointInsidePointContainment
    for b in candidate_bodies(sk.parentComponent):
        for sgn in (1.0, -1.0):
            probe = [c[i] - sgn * z[i] * 0.01 for i in range(3)]      # 0.1 mm under
            if b.pointContainment(adsk.core.Point3D.create(*probe)) == inside:
                body, z = b, [sgn * v for v in z]
                break
        if body is not None:
            break
    if body is not None:
        for f in body.faces:
            if not isinstance(f.geometry, adsk.core.Plane):
                continue
            ok, n = f.evaluator.getNormalAtPoint(f.pointOnFace)
            if _dot(_unit(_v(n)), z) < 0.999:
                continue
            if abs(_dot(_sub(c, _v(f.pointOnFace)), z)) > 1e-5:
                continue
            ok, prm = f.evaluator.getParameterAtPoint(adsk.core.Point3D.create(*c))
            if ok and f.evaluator.isParameterOnFace(prm):
                face = f
                break
    x = _unit(best if best is not None else ex)
    x = _unit(_sub(x, [z[i] * _dot(x, z) for i in range(3)]))
    if rotate_deg:
        x = _unit(_rot(x, z, rotate_deg))
    y = _cross(z, x)
    p0 = pts[0]
    xs = [_dot(_sub(p, p0), x) for p in pts]
    ys = [_dot(_sub(p, p0), y) for p in pts]
    cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
    origin = [p0[i] + x[i] * cx + y[i] * cy for i in range(3)]
    return origin, x, y, z, max(xs) - min(xs), max(ys) - min(ys), face, body


# ----------------------------------------------------------------- geometry

def cells_temp_body(spec, origin, x, y, z, z_range_cm=None):
    """The black cells as ONE temporary solid, in the frame's coordinates (cm).

    The solid spans z in [-depth, +raise] (or [-depth, -recess]): `depth_m`
    inlaid into the face, `raise_m` standing proud of it -- or exactly
    `z_range_cm` (low, high) along z from `origin` when given (ArUco
    Extrude builds every slab from its own Offset / Height).
    """
    tbm = adsk.fusion.TemporaryBRepManager.get()
    if z_range_cm is not None:
        lo, hi = float(z_range_cm[0]), float(z_range_cm[1])
    else:
        lo, hi = -spec.depth_m * 100.0, spec.black_top_m * 100.0
    depth = hi - lo
    mid = (lo + hi) / 2.0
    body = None
    for (x0, y0, x1, y1) in M().cell_boxes(spec):
        cx, cy = (x0 + x1) * 50.0, (y0 + y1) * 50.0
        w, h = (x1 - x0) * 100.0, (y1 - y0) * 100.0
        c = [origin[i] + x[i] * cx + y[i] * cy + z[i] * mid for i in range(3)]
        obb = adsk.core.OrientedBoundingBox3D.create(
            adsk.core.Point3D.create(*c), adsk.core.Vector3D.create(*x),
            adsk.core.Vector3D.create(*y), w, h, depth)
        box = tbm.createBox(obb)
        if body is None:
            body = box
        else:
            tbm.booleanOperation(body, box, adsk.fusion.BooleanTypes.UnionBooleanType)
    return body


def poly_prism_temp_body(origin, x, y, z, pts_mm, lo_cm, hi_cm):
    """A straight prism over a CONVEX polygon (mm, in the frame origin / x /
    y), lo..hi cm along z: the polygon's box with each slanted side cut off
    by a big box (a side lying on the box gets no cut, so no coplanar one).
    The same construction as aruco_feature._prism (the locating pins)."""
    tbm = adsk.fusion.TemporaryBRepManager.get()
    P, V = adsk.core.Point3D.create, adsk.core.Vector3D.create

    def box(c2, ax, ay, w, h, zlo, zhi):
        c = [origin[i] + x[i] * c2[0] + y[i] * c2[1] + z[i] * (zlo + zhi) / 2.0 for i in range(3)]
        return tbm.createBox(adsk.core.OrientedBoundingBox3D.create(
            P(*c), V(*ax), V(*ay), w, h, zhi - zlo))
    pts = [(u / 10.0, v / 10.0) for (u, v) in pts_mm]
    if _area2(pts) < 0:
        pts = list(reversed(pts))
    us, vs = [p[0] for p in pts], [p[1] for p in pts]
    u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
    body = box(((u0 + u1) / 2.0, (v0 + v1) / 2.0), x, y, u1 - u0, v1 - v0, lo_cm, hi_cm)
    big = 4.0 * max(u1 - u0, v1 - v0)
    eps = 1e-9
    for i in range(len(pts)):
        (a, b), (p, q) = pts[i], pts[(i + 1) % len(pts)]
        if (abs(a - p) < eps and (abs(a - u0) < eps or abs(a - u1) < eps)) or \
                (abs(b - q) < eps and (abs(b - v0) < eps or abs(b - v1) < eps)):
            continue
        du, dv = p - a, q - b
        ln = math.hypot(du, dv)
        tu, tv = du / ln, dv / ln
        nu, nv = tv, -tu                                  # outward (CCW polygon)
        mu, mv = (a + p) / 2.0 + nu * big / 2.0, (b + q) / 2.0 + nv * big / 2.0
        ax = [x[i_] * tu + y[i_] * tv for i_ in range(3)]
        ay = [x[i_] * nu + y[i_] * nv for i_ in range(3)]
        cut = box((mu, mv), ax, ay, big * 2.0, big, lo_cm - 0.1, hi_cm + 0.1)
        tbm.booleanOperation(body, cut, adsk.fusion.BooleanTypes.DifferenceBooleanType)
    return body


def _area2(pts):
    return sum(pts[i - 1][0] * pts[i][1] - pts[i][0] * pts[i - 1][1] for i in range(len(pts)))


# "Chamfer top-left corner" (bam_cad_core.aruco_chamfer): a mark is the Mark
# evaluate() made, or the dict a tag record keeps (a copy's variant part).
MARK_PAD_CM = 0.01           # a cut tool / a hole stands this much past the slab


def mark_parts(mark):
    """(kind, cut-tool polygon mm, triangle polygon mm) of a Mark or a
    record's mark dict; ("none", [], []) for none."""
    if not mark:
        return "none", [], []
    if isinstance(mark, dict):
        return (mark.get("kind") or "none", [tuple(p) for p in mark.get("tool") or []],
                [tuple(p) for p in mark.get("solid") or []])
    return mark.kind, list(mark.tool), list(mark.solid)


def mark_cells(cells, frame, mark, lo_cm, hi_cm, triangle=True):
    """The black cells (one temporary body, lo..hi cm over the frame's
    origin) with the corner mark: a ChArUco board's top-left black square
    cut ("black_cut"), or the corner triangle joined ("triangle", unless
    `triangle` is False). -> the body (changed in place)."""
    kind, tool, solid = mark_parts(mark)
    o, x, y, z = frame
    tbm = adsk.fusion.TemporaryBRepManager.get()
    if kind == "black_cut" and tool and cells is not None:
        t = poly_prism_temp_body(o, x, y, z, tool, lo_cm - MARK_PAD_CM, hi_cm + MARK_PAD_CM)
        tbm.booleanOperation(cells, t, adsk.fusion.BooleanTypes.DifferenceBooleanType)
    elif kind == "triangle" and solid and triangle:
        t = poly_prism_temp_body(o, x, y, z, solid, lo_cm, hi_cm)
        if cells is None:
            return t
        tbm.booleanOperation(cells, t, adsk.fusion.BooleanTypes.UnionBooleanType)
    return cells


def mark_white(white, frame, mark, lo_cm, hi_cm):
    """The white pattern (White only) with the corner triangle taken out of
    it -- a notch the black body under shows through. -> the body."""
    kind, _tool, solid = mark_parts(mark)
    if kind == "triangle" and solid and white is not None:
        o, x, y, z = frame
        t = poly_prism_temp_body(o, x, y, z, solid, lo_cm - MARK_PAD_CM, hi_cm + MARK_PAD_CM)
        adsk.fusion.TemporaryBRepManager.get().booleanOperation(
            white, t, adsk.fusion.BooleanTypes.DifferenceBooleanType)
    return white


def mark_tile(tile, frame, mark, lo_cm, hi_cm):
    """A white tile (or the cut tool that recesses the body for it) with its
    top-left corner cut ("tile_cut"). -> the body."""
    kind, tool, _solid = mark_parts(mark)
    if kind == "tile_cut" and tool and tile is not None:
        o, x, y, z = frame
        t = poly_prism_temp_body(o, x, y, z, tool, lo_cm - MARK_PAD_CM, hi_cm + MARK_PAD_CM)
        adsk.fusion.TemporaryBRepManager.get().booleanOperation(
            tile, t, adsk.fusion.BooleanTypes.DifferenceBooleanType)
    return tile


def square_temp_body(spec, origin, x, y, z):
    """The whole pattern extent x depth — fills old pockets in a variant."""
    tbm = adsk.fusion.TemporaryBRepManager.get()
    depth = spec.depth_m * 100.0
    w, h = (v * 100.0 for v in spec.extent_m)
    c = [origin[i] - z[i] * depth / 2.0 for i in range(3)]
    return tbm.createBox(adsk.core.OrientedBoundingBox3D.create(
        adsk.core.Point3D.create(*c), adsk.core.Vector3D.create(*x),
        adsk.core.Vector3D.create(*y), w, h, depth))


# ------------------------------------------------------------ the records

def read_tag_records(comp):
    a = comp.attributes.itemByName(G, TAGS_ATTR)
    if not a:
        return []
    try:
        data = json.loads(a.value)
        return data if isinstance(data, list) else []
    except ValueError:
        return []


def write_tag_records(comp, records):
    comp.attributes.add(G, TAGS_ATTR, json.dumps(records, sort_keys=True))


def is_variant_record(rec):
    """A copy's variant part's record: its template's key, no node, no Joint
    Origin -- it answers the template's slot (_build_variant)."""
    return bool((rec or {}).get("variant_of"))


def template_records(comp):
    """The records of the tags MODELLED in `comp`: none for a variant part.
    2026-10-09: a variant part carries a record with its template's key, so
    a lookup by key over des.allComponents could hit it first (Fusion's
    component order is not creation order) and hand None where the
    template's ArUco Extrude node was wanted."""
    if is_variant(comp):
        return []
    return [r for r in read_tag_records(comp) if not is_variant_record(r)]


def find_record(des, key, component=None):
    """(component, record) of the MODELLED tag `key` (in the component
    named `component`, when given), or (None, None)."""
    for comp in des.allComponents:
        if component is not None and comp.name != component:
            continue
        for r in template_records(comp):
            if r.get("key") == key:
                return comp, r
    return None, None


def record_is_live(des, comp, rec):
    """Does this record's GEOMETRY still exist? (2026-10-07: Zach's PDF printed
    a marker whose base feature had been undone/deleted -- the attribute
    outlived the features.) Variant parts carry no feature tokens and are
    judged by their cells body."""
    fz = rec.get("fusion") or {}
    token = fz.get("base_feature")
    if token:
        bf = _find_by_token(des, token)
        if bf is None:
            return False
        try:
            if bf.timelineObject.isSuppressed:
                return False
        except Exception:                          # noqa: BLE001
            pass
        return True
    return any(b.name.startswith("aruco cells") for b in comp.bRepBodies)


def live_tag_records(des, comp, warn=None):
    """read_tag_records minus orphans (reported through `warn`)."""
    out = []
    for rec in read_tag_records(comp):
        if record_is_live(des, comp, rec):
            out.append(rec)
        elif warn is not None:
            spec = rec.get("spec") or {}
            warn(f"tag '{rec.get('key')}' on '{comp.name}' ({spec.get('dictionary')} "
                 f"#{spec.get('marker_id')}): its geometry was undone or deleted — "
                 "ignored (the record is pruned the next time that component is tagged)")
    return out


def _component_label(des, comp):
    return "" if comp == des.rootComponent else comp.name


def _find_by_token(des, token):
    if not token:
        return None
    try:
        found = des.findEntityByToken(token)
        return found[0] if found else None
    except Exception:                              # noqa: BLE001
        return None


# --------------------------------------------------------------- creating

def next_free_id(dictionary, count=1):
    """Lowest id (block of `count`) no tag in the active design prints."""
    res = resolve_live()
    used = res.used_ids(dictionary)
    cap = aruco_core.module("fiducials").ARUCO_DICTS.get(dictionary, 50)
    return next((i for i in range(cap - count + 1)
                 if not any(i + k in used for k in range(count))), 0)


def add_tag(face, *, dictionary="DICT_4X4_50", marker_id=None, key="",
            quiet_modules=1.0, depth_mm=0.6, raise_mm=0.0, rotate_deg=0.0,
            marker_length_mm=None, tile_body=None, layout="single",
            rows=1, cols=1, legacy=False, frame=None, recolour_tile=False,
            best_rect=True, grow_m=0.0):
    """Turn a planar face of a body into an ArUco tag. Returns the record.

    The tag lives in the face's NATIVE component, so every copy of that
    component carries it (copies get their own ids through overrides).
    """
    des = design()
    native = face.nativeObject if face.assemblyContext else face
    body = tile_body or native.body
    comp = body.parentComponent
    probe = M().TagSpec(layout=layout, rows=int(rows), cols=int(cols),
                        dictionary=dictionary, quiet_modules=quiet_modules)
    if frame is not None:                          # a rectangle the user drew
        origin, x, y, z, w, h = frame
    elif best_rect:                                # a clicked face: best spot on it
        origin, x, y, z, w, h = face_best_rect(native, probe, rotate_deg)
    else:
        origin, x, y, z, w, h = face_frame(native, rotate_deg)
    markers = M()
    if marker_id is None:
        marker_id = next_free_id(dictionary)
    spec = markers.TagSpec(dictionary=dictionary, marker_id=int(marker_id),
                           quiet_modules=quiet_modules, depth_m=depth_mm / 1000.0,
                           raise_m=raise_mm / 1000.0,
                           layout=layout, rows=int(rows), cols=int(cols),
                           legacy=bool(legacy), grow_m=float(grow_m))
    if marker_length_mm and layout == "single":
        spec.marker_length_m = marker_length_mm / 1000.0
    else:
        spec = markers.fit_board(w / 100.0, h / 100.0, spec)
    spec.marker_length_m = round(spec.marker_length_m, 7)
    records = live_tag_records(des, comp)          # prunes orphaned records
    if not key:
        taken = {r.get("key") for r in records}
        base = {"grid": "board", "charuco": "charuco"}.get(layout, "top")
        key = base if base not in taken else f"{base}{len(records) + 1}"
    face_token = native.entityToken

    tl = des.timeline
    start = tl.markerPosition
    bf = comp.features.baseFeatures.add()
    bf.startEdit()
    comp.bRepBodies.add(cells_temp_body(spec, origin, x, y, z), bf)
    bf.finishEdit()
    cells = bf.bodies.item(0)
    cells.name = f"aruco cells {key}"
    bf.name = f"ArUco {key} cells"
    # The pocket only exists for an inlay; a raised layer just sits on the face.
    cf = _pocket(comp, body, cells) if spec.depth_m > 0 else None
    white, black = appearance(des, WHITE), appearance(des, BLACK)
    if white and recolour_tile:                    # never repaint the user's own body
        body.appearance = white
    if black:
        cells.appearance = black
    try:
        group = tl.timelineGroups.add(start, tl.markerPosition - 1)
        group.name = f"ArUco {key}: {spec.label()}"
        group_token = group.entityToken if hasattr(group, "entityToken") else None
    except Exception:                              # noqa: BLE001
        group_token = None

    tag = markers.TagDef(key=key, component=_component_label(des, comp), spec=spec,
                         origin=origin, x_axis=x, y_axis=y, z_axis=z,
                         name=f"ArUco {key}")
    rec = tag.to_dict()
    rec["fusion"] = {"base_feature": bf.entityToken,
                     "combine": cf.entityToken if cf else None,
                     "face": face_token, "tile_body": body.name,
                     "cells_body": cells.name, "group": group_token,
                     "rotate_deg": rotate_deg}
    try:
        ensure_joint_origin(comp, rec)
    except Exception as exc:                       # noqa: BLE001
        rec.setdefault("fusion", {})["joint_origin_error"] = f"not created: {exc}"
    records = [r for r in records if r.get("key") != key] + [rec]
    write_tag_records(comp, records)
    return rec


def _pocket(comp, tile, cells):
    """Combine: cut the cells out of the tile, keeping the cells (the inlay)."""
    tools = adsk.core.ObjectCollection.create()
    tools.add(cells)
    ci = comp.features.combineFeatures.createInput(tile, tools)
    ci.operation = adsk.fusion.FeatureOperations.CutFeatureOperation
    ci.isKeepToolBodies = True
    return comp.features.combineFeatures.add(ci)


TILE_MODES = ("auto", "inlay", "join", "separate")


def aruco_extrude(profile, thickness_mm=3.0, tile_mode="auto", rotate_deg=0.0, **kw):
    """Draw a rectangle, get a tag. Where the white comes from is `tile_mode`:

    inlay     the rectangle sits on a body's face: cut ONLY the black cells
              into that body, nothing new sticks out (Zach: "just extrude
              the black parts into the body")
    join      extrude the rectangle `thickness_mm` out of that face, JOINED
              to the body, and inlay the cells into its top
    separate  a new tile body of its own (the original ArUco Extrude)
    auto      inlay when the sketch is on a body's face, else separate
    """
    comp = profile.parentSketch.parentComponent
    o, x, y, z, w, h, face, body = profile_frame(profile, rotate_deg)
    mode = tile_mode
    if mode == "auto":
        mode = "inlay" if body is not None else "separate"
    if mode in ("inlay", "join") and body is None:
        raise ValueError("this rectangle is not drawn on a body's face — use a "
                         "separate tile, or sketch on the face you want to tag")
    if mode == "inlay":
        return add_tag(face, tile_body=body, frame=(o, x, y, z, w, h),
                       recolour_tile=False, **kw)
    op = (adsk.fusion.FeatureOperations.JoinFeatureOperation if mode == "join"
          else adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    ei = comp.features.extrudeFeatures.createInput(profile, op)
    ei.setDistanceExtent(False, adsk.core.ValueInput.createByString(f"{thickness_mm} mm"))
    if mode == "join":
        ei.participantBodies = [body]
    ext = comp.features.extrudeFeatures.add(ei)
    end = ext.endFaces.item(0)
    lift = thickness_mm / 10.0                     # mm -> cm
    if _dot(_v(end.evaluator.getNormalAtPoint(end.pointOnFace)[1]), z) < 0:
        lift = -lift                               # extruded the other way
    top = [o[i] + z[i] * lift for i in range(3)]
    if mode == "join":
        return add_tag(end, tile_body=body, frame=(top, x, y, z, w, h),
                       recolour_tile=False, **kw)
    tile = ext.bodies.item(0)
    tile.name = "tag tile"
    # the only body the add-in paints white is a tile it just made itself
    return add_tag(end, tile_body=tile, frame=(top, x, y, z, w, h), recolour_tile=True, **kw)


def set_tag_spec(comp, key, **changes):
    """Re-encode a modelled tag (id / dictionary) in place: swap the cells body."""
    des = design()
    records = read_tag_records(comp)
    rec = next((r for r in records if r.get("key") == key), None)
    if rec is None:
        raise ValueError(f"no tag '{key}' on component '{comp.name}'")
    markers = M()
    tag = markers.TagDef.from_dict(rec, lambda m: None)
    spec = tag.spec
    for k, v in changes.items():
        setattr(spec, k, int(v) if k == "marker_id" else v)
    if spec.depth_m < 0 or spec.raise_m < 0 or spec.black_thickness_m <= 0:
        raise ValueError("the black layer needs some thickness (depth or raise > 0)")
    bf = _find_by_token(des, (rec.get("fusion") or {}).get("base_feature"))
    if bf is None:
        raise ValueError(f"tag '{key}': its cells base feature is gone")
    new = cells_temp_body(spec, tag.origin, tag.x_axis, tag.y_axis, tag.z_axis)
    bf.startEdit()
    try:
        # sourceBodies is a plain vector in this Fusion build (no .item()), and
        # an exception between startEdit and finishEdit would strand the design
        # in base-feature edit mode -- hence the finally.
        ok = bf.updateBody(list(bf.sourceBodies)[0], new)
    finally:
        bf.finishEdit()
    # The pocket follows the depth: suppressed when the black only sits on
    # the face, (re)made when it goes back in. Suppressing rather than
    # deleting keeps any later feature that referenced the pocket's faces.
    fz = rec.setdefault("fusion", {})
    cf = _find_by_token(des, fz.get("combine"))
    if spec.depth_m > 0:
        if cf is not None and cf.timelineObject.isSuppressed:
            cf.timelineObject.isSuppressed = False
        elif cf is None:
            tile = comp.bRepBodies.itemByName(fz.get("tile_body", ""))
            cells = bf.bodies.item(0)
            if tile is not None:
                fz["combine"] = _pocket(comp, tile, cells).entityToken
    elif cf is not None and not cf.timelineObject.isSuppressed:
        cf.timelineObject.isSuppressed = True
    rec["spec"] = spec.to_dict()
    rec.pop("frame", None)                         # the Frame (2026-10-08) is gone
    rec.pop("frame_offset", None)
    if (rec.get("fusion") or {}).get("joint_origin"):
        try:
            ensure_joint_origin(comp, rec)
        except Exception as exc:                   # noqa: BLE001
            rec["fusion"]["joint_origin_error"] = f"not updated: {exc}"
    write_tag_records(comp, records)
    try:
        grp = _find_by_token(des, rec["fusion"].get("group"))
        if grp:
            grp.name = f"ArUco {key}: {spec.label()}"
    except Exception:                              # noqa: BLE001
        pass
    return {"updated": bool(ok), "spec": spec.to_dict()}


# ----------------------------------------------------- the optical frame

JO_PREFIX = "aruco"


def optical_frame_cm(tag):
    """The frame OpenCV reports for this tag, in its component (cm).

    Tag frame (pattern centre on the face) composed with
    `markers.detector_frame` -- identity for a single marker, OpenCV's board
    origin for grid/ChArUco, lifted by raise_m for a raised black layer.
    """
    markers = M()
    D = list(markers.detector_frame(tag.spec))
    for i in (3, 7, 11):
        D[i] *= 100.0                              # m -> cm
    Wm = markers.mat_mul(tag.frame_matrix(), D)
    return ([Wm[3], Wm[7], Wm[11]], [Wm[0], Wm[4], Wm[8]],
            [Wm[1], Wm[5], Wm[9]], [Wm[2], Wm[6], Wm[10]])


def has_old_frame(rec):
    """Does a record still carry the Frame chosen on 2026-10-08 (removed:
    the Joint Origin is OpenCV's frame, always)? It is read as OpenCV's
    frame; the next write drops it and moves the Joint Origin there."""
    return bool(rec) and ("frame" in rec or "frame_offset" in rec)


def _jo_frame(jo):
    o, x, y, z = jo.transform.getAsCoordinateSystem()
    return _v(o), _unit(_v(x)), _unit(_v(y)), _unit(_v(z))


def _frame_error(a, b):
    """(position error cm, worst axis angle rad) between two frames."""
    d = _sub(a[0], b[0])
    pos = math.sqrt(_dot(d, d))
    ang = 0.0
    for i in (1, 2, 3):
        c = max(-1.0, min(1.0, _dot(a[i], b[i])))
        ang = max(ang, math.acos(c))
    return pos, ang


def _planar_normal(face):
    """Unit outward normal of a planar face, else None."""
    if not isinstance(face.geometry, adsk.core.Plane):
        return None
    ok, n = face.evaluator.getNormalAtPoint(face.pointOnFace)
    return _unit(_v(n)) if ok else None


def _bbox_has(bb, p, tol_cm=1e-3):
    lo, hi = _v(bb.minPoint), _v(bb.maxPoint)
    return all(lo[i] - tol_cm <= p[i] <= hi[i] + tol_cm for i in range(3))


def _tag_face(des, comp, rec, tag):
    """The planar face (native, in `comp`) to build the tag's Joint Origin on.

    Any face parallel to the pattern would do -- the offsets put the Joint
    Origin on the optical frame -- so this picks the one most likely to
    follow the tag when the design changes, in this order:

      1. the tile / target body's face the pattern sits in (outward normal
         along the tag's +z, in the tag plane, over the tag centre);
      2. such a face of any other body in the component;
      3. the top of this tag's OWN pattern body (black or white only with
         nothing under it: a free sketch, or a body in another component --
         2026-10-08 that case had no tile body and crashed the lookup);
      4. the face the tag was made on (by token).
    """
    fz = rec.get("fusion") or {}
    z, o = tag.z_axis, tag.origin
    tile_name, pat_name = fz.get("tile_body"), fz.get("cells_body")
    tile = comp.bRepBodies.itemByName(tile_name) if tile_name else None
    pattern = comp.bRepBodies.itemByName(pat_name) if pat_name else None

    def is_pattern(b):
        return b == pattern or b.name.startswith(("aruco cells", "aruco white"))

    def pick(bodies, own_pattern=False):
        """In the tag plane and over the tag centre; for the tag's own
        pattern, any top face (a board's centre is in a gap between cells)."""
        best, best_key = None, None
        for b in bodies:
            if b is None:
                continue
            for f in b.faces:
                n = _planar_normal(f)
                if n is None or _dot(n, z) < 0.999:
                    continue
                gap = _dot(_sub(_v(f.pointOnFace), o), z)
                if not own_pattern and (abs(gap) > 1e-5 or not _bbox_has(
                        f.boundingBox, [o[i] + z[i] * gap for i in range(3)])):
                    continue
                key = (round(gap, 6), f.area)          # highest, then biggest
                if best is None or key > best_key:
                    best, best_key = f, key
        return best

    face = (pick([tile] if tile is not None and not is_pattern(tile) else [])
            or pick([b for b in comp.bRepBodies if b != tile and not is_pattern(b)])
            or pick([pattern], own_pattern=True))
    if face is None:
        tok = _find_by_token(des, fz.get("face"))
        if tok is not None:
            face = tok.nativeObject if tok.assemblyContext else tok
    return face


def _set_param(param, value):
    if param is not None:
        param.value = value


def _jo_add(comp, geo, angle=0.0, off=(0.0, 0.0, 0.0), flip=False):
    """One Joint Origin. Offsets and angle go in through the INPUT: editing
    a Joint Origin's parameters afterwards needs the timeline rolled to it."""
    joi = comp.jointOrigins.createInput(geo)
    VI = adsk.core.ValueInput.createByReal
    joi.angle = VI(angle)
    joi.offsetX, joi.offsetY, joi.offsetZ = (VI(float(v)) for v in off)
    joi.isFlipped = bool(flip)
    return comp.jointOrigins.add(joi)


def _solve3(cols, b):
    """x with x0*c0 + x1*c1 + x2*c2 = b (Cramer), None when singular."""
    def det(a, b_, c):
        return _dot(a, _cross(b_, c))
    d = det(*cols)
    if abs(d) < 1e-12:
        return None
    return [det(b, cols[1], cols[2]) / d, det(cols[0], b, cols[2]) / d,
            det(cols[0], cols[1], b) / d]


def _place_joint_origin(comp, geo, target, tol_cm, tol_rad):
    """A Joint Origin on `geo` whose read-back is `target` (component cm).

    How Fusion composes the parameters, measured with `jo_probe` on
    2026-10-08 (Fusion 2705.1.15; geometry frame o, x, y, z):

      x' = x turned by `angle` about the geometry's own (UNFLIPPED) z
      isFlipped: the frame turns half a turn about x' (y and z negate)
      origin = o + offsetX x' + offsetY y' + offsetZ z'  (the FINAL axes)

    The old solve assumed the angle turned about the flipped z, so every
    flipped frame -- grid and ChArUco boards, whose OpenCV z points INTO
    the board -- came out up to 180 deg / tens of mm off (62.3 mm on a
    ChArUco board on a free sketch). If the read-back disagrees anyway (a
    Fusion update), the angle and flip are found by read-back and the
    offsets from three unit probes, which holds for any linear convention.
    -> (jo, (pos_err_cm, ang_err_rad), (angle, offsets, flip))
    """
    to, tx, _ty, tz = target
    base = _jo_add(comp, geo)
    bo, bx, by, bz = _jo_frame(base)
    base.deleteMe()
    flip = _dot(bz, tz) < 0
    a = math.atan2(_dot(tx, by), _dot(tx, bx))
    s = -1.0 if flip else 1.0
    fx = [math.cos(a) * bx[i] + math.sin(a) * by[i] for i in range(3)]
    fy = [s * (-math.sin(a) * bx[i] + math.cos(a) * by[i]) for i in range(3)]
    fzv = [s * c for c in bz]
    d = _sub(to, bo)
    params = (a, (_dot(d, fx), _dot(d, fy), _dot(d, fzv)), flip)
    jo = _jo_add(comp, geo, *params)
    err = _frame_error(_jo_frame(jo), target)
    if err[0] < tol_cm and err[1] < tol_rad:
        return jo, err, params
    jo.deleteMe()
    best = None
    for fl in (flip, not flip):
        for ang in (a, -a, a + math.pi, math.pi - a):
            j = _jo_add(comp, geo, ang, (0.0, 0.0, 0.0), fl)
            f0 = _jo_frame(j)
            j.deleteMe()
            e = _frame_error(f0, target)[1]
            if best is None or e < best[0]:
                best = (e, ang, fl, f0[0])
    _e, ang, fl, o0 = best
    cols = []
    for k in range(3):
        off = [0.0, 0.0, 0.0]
        off[k] = 1.0
        j = _jo_add(comp, geo, ang, off, fl)
        cols.append(_sub(_jo_frame(j)[0], o0))
        j.deleteMe()
    off = _solve3(cols, _sub(to, o0)) or [0.0, 0.0, 0.0]
    params = (ang, tuple(off), fl)
    jo = _jo_add(comp, geo, *params)
    return jo, _frame_error(_jo_frame(jo), target), params


def _native(ent):
    try:
        if ent is not None and ent.assemblyContext is not None:
            return ent.nativeObject
    except Exception:                              # noqa: BLE001
        pass
    return ent


def _feature_of(des, rec):
    """The tag's ArUco Extrude node (CustomFeature), or None."""
    ent = _find_by_token(des, (rec.get("fusion") or {}).get("custom_feature"))
    try:
        return adsk.fusion.CustomFeature.cast(ent) if ent is not None else None
    except Exception:                              # noqa: BLE001
        return None


def _roll_before(des, cf):
    """Timeline marker just before `cf` -> how to put it back."""
    tl = des.timeline
    pos = tl.markerPosition
    at_end = pos >= tl.count
    keep = tl.item(pos - 1) if (not at_end and pos > 0) else None
    cf.timelineObject.rollTo(True)
    return at_end, keep


def _roll_restore(des, how):
    at_end, keep = how
    if not at_end and keep is not None:
        try:
            if keep.isValid:
                keep.rollTo(False)
                return
        except Exception:                          # noqa: BLE001
            pass
    des.timeline.moveToEnd()


def _anchors(src, comp, centre_first):
    """[(label, JointGeometry maker)] on a sketch profile or a planar face of
    `comp`, in the order to try. The offsets put the Joint Origin on the
    optical frame whichever one sticks; the centre keypoint is tried first
    on a target the ArUco feature has not cut yet, because a rectangle's
    centre is where the tag's centre goes when it is resized."""
    kp = adsk.fusion.JointKeyPointTypes
    JG = adsk.fusion.JointGeometry
    if isinstance(src, adsk.fusion.Profile):
        if src.parentSketch.parentComponent != comp:
            return []
        lines = [pc.sketchEntity for loop in src.profileLoops for pc in loop.profileCurves
                 if isinstance(pc.geometry, adsk.core.Line3D)]
        lines.sort(key=lambda c: -c.length)
        out = [("profile centre", lambda: JG.createByProfile(src, None, kp.CenterKeyPoint))]
        out += [("profile line midpoint",
                 lambda c=c: JG.createByProfile(src, c, kp.MiddleKeyPoint)) for c in lines[:2]]
        return out
    face = adsk.fusion.BRepFace.cast(src)
    if face is None or face.body.parentComponent != comp \
            or not isinstance(face.geometry, adsk.core.Plane):
        return []
    edges = sorted((e for e in face.edges if isinstance(e.geometry, adsk.core.Line3D)),
                   key=lambda e: -e.length)
    centre = [("face centre", lambda: JG.createByPlanarFace(face, None, kp.CenterKeyPoint))]
    # A face with holes and pockets can make the face-CENTRE keypoint fail
    # validation (seen on Zach's D-shaped disc): edge midpoints first there.
    mids = [("edge midpoint", lambda e=e: JG.createByPlanarFace(face, e, kp.MiddleKeyPoint))
            for e in edges[:3]]
    if edges:
        mids.append(("face centre, edge x",
                     lambda: JG.createByPlanarFace(face, edges[0], kp.CenterKeyPoint)))
    return centre + mids if centre_first else mids + centre


def _face_under(comp, tag):
    """The planar face of `comp` the tag stands on: parallel to it (outward
    normal along +z), at or just below the tag plane (the nearest one), over
    the tag centre. Called with the timeline rolled before the tag's node,
    so the bodies the node makes do not exist yet. Covers a ROOT sketch over
    a body in a child component when the tag lives in that component (the
    profile cannot anchor a Joint Origin of another component)."""
    z, o = tag.z_axis, tag.origin
    best, best_key = None, None
    for b in comp.bRepBodies:
        if b.name.startswith(("aruco cells", "aruco white")):
            continue
        for f in b.faces:
            n = _planar_normal(f)
            if n is None or _dot(n, z) < 0.999:
                continue
            gap = _dot(_sub(_v(f.pointOnFace), o), z)
            if gap > 1e-5 or not _bbox_has(f.boundingBox, [o[i] + z[i] * gap for i in range(3)]):
                continue
            key = (round(gap, 6), f.area)              # nearest below, then biggest
            if best is None or key > best_key:
                best, best_key = f, key
    return best


def _solve_on(comp, anchors, target, tol_cm, tol_rad, errors):
    """Best (jo, err, params, label) over the anchors, or None."""
    best = None
    for label, make_geo in anchors:
        try:
            jo, err, params = _place_joint_origin(comp, make_geo(), target, tol_cm, tol_rad)
        except Exception as exc:                   # noqa: BLE001
            errors.append(f"{label}: {str(exc).splitlines()[0][:80]}")
            continue
        if best is None or err < best[1]:
            if best is not None:
                best[0].deleteMe()
            best = (jo, err, params, label)
        else:
            jo.deleteMe()
        if best[1][0] < tol_cm and best[1][1] < tol_rad:
            break
    return best


def ensure_joint_origin(comp, rec, tol_cm=1e-5, tol_rad=1e-6):
    """Create/update the tag's Joint Origin so it IS OpenCV's frame
    (optical_frame_cm: what a detector reports, the Origin included).

    Anchored on what the tag was MADE ON -- the sketch profile or the face
    its ArUco Extrude depends on -- and placed in the timeline just BEFORE
    that feature, so it follows every upstream change and nothing the
    feature rebuilds can pull it off. (2026-10-08: built on the tile's top
    face it lost its reference whenever the feature recomputed -- the tile
    is a base-feature body that is replaced on every compute -- and stayed
    where it was: "floating above the marker".) When the target belongs to
    another component (a root sketch over a body in a child component, the
    tag living with the body), the face the tag stands on is used, still
    before the feature (`_face_under`). Tags without a live node fall back
    to a planar face found by geometry at the end of the timeline
    (`_tag_face`).

    Then rotated / flipped / offset (`_place_joint_origin`) until Fusion's
    own `JointOrigin.transform` reads back as that frame; the record keeps
    how close the read-back got (with the timeline forward again).
    """
    # the component's own design, not the active one (2026-10-08: the user
    # can switch documents while a scripted step pumps events)
    des = adsk.fusion.Design.cast(comp.parentDesign) or design()
    markers = M()
    tag = markers.TagDef.from_dict(rec, lambda m: None)
    target = optical_frame_cm(tag)
    fz = rec.setdefault("fusion", {})
    name = f"{JO_PREFIX} {tag.key}"
    old = _find_by_token(des, fz.get("joint_origin")) or comp.jointOrigins.itemByName(name)
    best, errors, where = None, [], ""
    cf = _feature_of(des, rec)
    if cf is not None:
        how = None
        try:
            dep = cf.dependencies.itemById("target")
            how = _roll_before(des, cf)
            src = _native(dep.entity) if dep is not None else None
            anchors = _anchors(src, comp, True) if src is not None else []
            under = _face_under(comp, tag)
            if under is not None:
                anchors += [(f"{label} of '{under.body.name}'", mk)
                            for label, mk in _anchors(under, comp, True)]
            if anchors:
                best = _solve_on(comp, anchors, target, tol_cm, tol_rad, errors)
                where = "before the ArUco feature"
        except Exception as exc:                   # noqa: BLE001
            errors.append(f"target: {str(exc).splitlines()[0][:80]}")
        finally:
            if how is not None:
                _roll_restore(des, how)
        if best is not None:                       # read back with the timeline forward
            best = (best[0], _frame_error(_jo_frame(best[0]), target), best[2], best[3])
    if best is None or best[1][0] >= tol_cm or best[1][1] >= tol_rad:
        face = _tag_face(des, comp, rec, tag)
        alt = _solve_on(comp, _anchors(face, comp, False), target, tol_cm, tol_rad, errors) \
            if face is not None else None
        if alt is not None and (best is None or alt[1] < best[1]):
            if best is not None:
                best[0].deleteMe()
            best, where = alt, f"on '{face.body.name}' at the end of the timeline"
        elif alt is not None:
            alt[0].deleteMe()
    if best is None:
        raise ValueError(f"tag '{tag.key}': no Joint Origin could be made "
                         "(" + " | ".join(errors[-4:]) + ")")
    jo, (pos, ang), params, label = best
    if old is not None:
        try:
            old.deleteMe()
        except Exception:                          # noqa: BLE001
            pass
    jo.name = name
    fz["joint_origin"] = jo.entityToken
    fz["joint_origin_name"] = name
    fz["joint_origin_anchor"] = f"{label}, {where}"
    fz["joint_origin_error"] = {"mm": round(pos * 10.0, 7), "deg": round(math.degrees(ang), 7)}
    return {"name": name, "error_mm": pos * 10.0, "error_deg": math.degrees(ang),
            "flipped": bool(jo.isFlipped), "anchor": fz["joint_origin_anchor"]}


def joint_origin_of(des, comp, rec):
    """The tag's Joint Origin (by token, else by name), or None."""
    fz = rec.get("fusion") or {}
    jo = _find_by_token(des, fz.get("joint_origin"))
    if jo is None and fz.get("joint_origin_name"):
        jo = comp.jointOrigins.itemByName(fz["joint_origin_name"])
    return jo


def joint_origin_matches(des, comp, rec, tol_mm=1e-3, tol_deg=1e-3):
    """Does the tag's Joint Origin exist, read back as its record's OpenCV
    frame (optical_frame_cm), and carry this code's anchor? refresh_one
    (edits, export) re-solves when it does not -- which also moves a Joint
    Origin made by an older add-in (on the tile face, no
    "joint_origin_anchor") onto its target, once, and one left at a Frame
    chosen on 2026-10-08 back to OpenCV's frame."""
    if not (rec.get("fusion") or {}).get("joint_origin_anchor"):
        return False
    jo = joint_origin_of(des, comp, rec)
    if jo is None:
        return False
    try:
        tag = M().TagDef.from_dict(rec, lambda m: None)
        pos, ang = _frame_error(_jo_frame(jo), optical_frame_cm(tag))
    except Exception:                              # noqa: BLE001
        return False
    return pos * 10.0 <= tol_mm and math.degrees(ang) <= tol_deg


def joint_origin_report(des=None):
    """READ-ONLY: every tag's Joint Origin read back against its optical
    frame (OpenCV's, component cm), with what it was built on. Variant parts
    (a copy's re-numbered tag) have no Joint Origin of their own: skipped.
    Changes nothing."""
    des = des or design()
    markers = M()
    R = lambda v: [round(c * 10.0, 4) for c in v]                 # cm -> mm
    U = lambda v: [round(c, 5) for c in v]
    out = []
    for comp in des.allComponents:
        for rec in template_records(comp):
            fz = rec.get("fusion") or {}
            spec = rec.get("spec") or {}
            ent = {"component": comp.name, "key": rec.get("key"),
                   "layout": spec.get("layout"),
                   "raise_mm": round(spec.get("raise_m", 0.0) * 1000, 4),
                   "depth_mm": round(spec.get("depth_m", 0.0) * 1000, 4),
                   "recorded_error": fz.get("joint_origin_error"),
                   "anchor": fz.get("joint_origin_anchor"),
                   "tile_body": fz.get("tile_body"), "mode": fz.get("mode")}
            try:
                tag = markers.TagDef.from_dict(rec, lambda m: None)
                t = optical_frame_cm(tag)
                ent["tag_origin_mm"] = R(tag.origin)
                ent["tag_z"] = U(tag.z_axis)
                ent["optical_mm"] = R(t[0])
                ent["optical_xyz"] = [U(t[1]), U(t[2]), U(t[3])]
                if has_old_frame(rec):
                    ent["old_frame"] = rec.get("frame")     # 2026-10-08's: re-solved on refresh
                jo = joint_origin_of(des, comp, rec)
                ent["jo_found"] = jo is not None
                if jo is not None:
                    f = _jo_frame(jo)
                    pos, ang = _frame_error(f, t)
                    d = _sub(f[0], t[0])
                    ent["jo_name"] = jo.name
                    ent["jo_mm"] = R(f[0])
                    ent["jo_xyz"] = [U(f[1]), U(f[2]), U(f[3])]
                    ent["error_mm"] = round(pos * 10.0, 4)
                    ent["error_deg"] = round(math.degrees(ang), 4)
                    # the error in the optical frame's own axes (+z = OpenCV's z)
                    ent["delta_in_optical_mm"] = [round(_dot(d, t[i]) * 10.0, 4) for i in (1, 2, 3)]
                    ent["flipped"] = bool(jo.isFlipped)
                    for k in ("offsetX", "offsetY", "offsetZ", "angle"):
                        try:
                            ent[k] = getattr(jo, k).expression
                        except Exception:           # noqa: BLE001
                            pass
                    try:
                        g = jo.geometry
                        ent["geo_origin_mm"] = R(_v(g.origin))
                        ent["geo_entity"] = (g.entityOne.objectType.rsplit("::", 1)[-1]
                                             if g.entityOne else None)
                        ent["geo_keypoint"] = int(g.keyPointType)
                        ent["geo_type"] = int(g.geometryType)
                    except Exception as exc:        # noqa: BLE001
                        ent["geo_error"] = str(exc)[:120]
                face = _tag_face(des, comp, rec, tag)
                if face is not None:
                    ok, n = face.evaluator.getNormalAtPoint(face.pointOnFace)
                    ent["face_body"] = face.body.name
                    ent["face_normal"] = U(_unit(_v(n)))
                    ent["face_gap_mm"] = round(_dot(_sub(_v(face.pointOnFace), tag.origin),
                                                    tag.z_axis) * 10.0, 4)
                else:
                    ent["face_body"] = None

                def along(p):
                    return round(_dot(_sub(_v(p), tag.origin), tag.z_axis) * 10.0, 3)
                ent["bodies_along_z_mm"] = {b.name: [along(b.boundingBox.minPoint),
                                                     along(b.boundingBox.maxPoint)]
                                            for b in comp.bRepBodies}
            except Exception as exc:               # noqa: BLE001
                ent["error"] = f"{type(exc).__name__}: {exc}"[:300]
            out.append(ent)
    return out


def ensure_frames(des=None):
    """A Joint Origin at every modelled tag (variants answer slots, no frames)."""
    des = des or design()
    out = []
    for comp in des.allComponents:
        if is_variant(comp):
            continue
        records = read_tag_records(comp)
        if not records:
            continue
        for rec in records:
            r = ensure_joint_origin(comp, rec)
            r["component"] = _component_label(des, comp)
            out.append(r)
        write_tag_records(comp, records)
    return out


# -------------------------------------------------------------- overrides

def read_overrides(des):
    out = []
    comps = [des.rootComponent] + [c for c in des.allComponents if c != des.rootComponent]
    for comp in comps:
        for occ in comp.occurrences:
            a = occ.attributes.itemByName(G, OVERRIDES_ATTR)
            if not a:
                continue
            try:
                values = json.loads(a.value)
            except ValueError:
                continue
            out.append({"owner": _component_label(des, comp), "occurrence": occ.name,
                        "values": values})
    return out


def _owner_component(des, owner):
    if not owner:
        return des.rootComponent
    for c in des.allComponents:
        if c.name == owner:
            return c
    raise ValueError(f"no component named '{owner}'")


def write_override(owner, occurrence, key, values):
    """Set (values) or clear (None) one per-copy override."""
    des = design()
    comp = _owner_component(des, owner)
    occ = comp.occurrences.itemByName(occurrence)
    if occ is None:
        raise ValueError(f"'{owner or '(root)'}' has no occurrence '{occurrence}'")
    a = occ.attributes.itemByName(G, OVERRIDES_ATTR)
    data = json.loads(a.value) if a else {}
    if values:
        data[key] = dict(values)
    else:
        data.pop(key, None)
    if data:
        occ.attributes.add(G, OVERRIDES_ATTR, json.dumps(data, sort_keys=True))
    elif a:
        a.deleteMe()
    return data


# ---------------------------------------------------------------- reading

def is_variant(comp):
    return bool(comp.attributes.itemByName(G, VARIANT_ATTR))


def collect(des=None):
    """The raw `aruco` block: what CADExporter puts into payload["aruco"]."""
    des = des or design()
    tags, warnings = [], []
    for comp in des.allComponents:
        if is_variant(comp):
            continue
        for rec in live_tag_records(des, comp, warnings.append):
            rec = dict(rec)
            rec["component"] = _component_label(des, comp)
            tags.append(rec)
    variants = []
    for comp in des.allComponents:
        a = comp.attributes.itemByName(G, VARIANT_ATTR)
        if a:
            try:
                variants.append(dict(json.loads(a.value), component=comp.name))
            except ValueError:
                pass
    return {"schema": M().SCHEMA, "units": "cm", "tags": tags,
            "overrides": read_overrides(des), "variants": variants,
            "warnings": warnings}


def occurrence_refs(des=None):
    des = des or design()
    markers = M()
    out = []
    for o in des.rootComponent.allOccurrences:
        try:
            t = list(o.transform2.asArray())
        except Exception:                          # noqa: BLE001
            t = None
        out.append(markers.OccurrenceRef(o.fullPathName, o.component.name, t,
                                         bool(o.isVisible)))
    return out


def resolve_live(des=None, raw=None):
    """resolve() on the active design; `raw` (a collect() / fresh_collect()
    block) instead of the records as they stand."""
    des = des or design()
    markers = M()
    raw = collect(des) if raw is None else raw
    warns = []
    tags, ovs = markers.tags_from_payload(raw, warns.append)
    res = markers.resolve(tags, ovs, occurrence_refs(des),
                          des.rootComponent.name, "cm")
    res.warnings[:0] = warns
    return res


# ------------------------------------------------------- fresh tag records
#
# Zach, 2026-10-08 ("when I actually did it, it didn't put them in the
# correct location"): a variant was built from its template's RECORD (the
# bam_aruco/tags attribute), and a live ArUco Extrude's record is written
# only by create, edit and refresh_live -- compute re-fits the tag on every
# upstream change but may not write attributes. His plate changed after it
# was tagged; the node re-fitted to a 30.0 mm grid centred 7.0 mm / 16.9 mm
# away from the record's 30.5 mm one, and both variants were built at the
# record. Every frame a variant is built from, kept against or previewed
# with now comes from fresh_collect().

FRAME_TOL_CM = 1e-4          # 1 um: a record agrees with its node's geometry


def _frame_of(rec):
    return ([float(v) for v in rec.get("origin") or (0.0, 0.0, 0.0)],
            [float(v) for v in rec.get("x_axis") or (1.0, 0.0, 0.0)],
            [float(v) for v in rec.get("y_axis") or (0.0, 1.0, 0.0)],
            [float(v) for v in rec.get("z_axis") or (0.0, 0.0, 1.0)])


def _extents(body, frame):
    """[(min, max)] along the frame's x, y, z of a body's vertices, from its
    origin (cm). A proxy body answers in its assembly context."""
    o, x, y, z = frame
    lo, hi = [9e9] * 3, [-9e9] * 3
    for v in body.vertices:
        d = _sub(_v(v.geometry), o)
        for i, a in enumerate((x, y, z)):
            t = _dot(d, a)
            lo[i], hi[i] = min(lo[i], t), max(hi[i], t)
    return list(zip(lo, hi))


def _is_white(rec, body=None):
    fz = rec.get("fusion") or {}
    if fz.get("mode") == "white":
        return True
    return body is not None and body.name.startswith("aruco white")


def _footprint_cm(spec, white, mark=None):
    """(x0, x1, y0, y1) of a pattern body in its tag frame (cm): the black
    cells' extent (and the corner triangle, when the record has one), or the
    white tile (pattern + quiet zone) in mode white."""
    if white:
        tw, th = (v * 50.0 for v in spec.tile_size_m)
        return (-tw, tw, -th, th)
    boxes = M().cell_boxes(spec)
    us = [b[0] * 100.0 for b in boxes] + [b[2] * 100.0 for b in boxes]
    vs = [b[1] * 100.0 for b in boxes] + [b[3] * 100.0 for b in boxes]
    kind, _tool, solid = mark_parts(mark)
    if kind == "triangle":
        us += [u / 10.0 for u, _v in solid]
        vs += [v / 10.0 for _u, v in solid]
    return (min(us), max(us), min(vs), max(vs))


def _node(des, rec):
    """(aruco_feature, its live node) for a record, or (module, None)."""
    try:
        import aruco_feature as F
    except Exception:                              # noqa: BLE001
        return None, None
    try:
        cf = F.find_feature(des, rec)
        if cf is None or not F.is_ours(cf):
            return F, None
        tl = cf.timelineObject
        if tl.isSuppressed or tl.isRolledBack:
            return F, None
        return F, cf
    except Exception:                              # noqa: BLE001
        return F, None


def pattern_body(comp, rec, cf=None):
    """The body a record's pattern is (cells, or the white layer): by the
    record's name, else the node's pattern base feature."""
    fz = rec.get("fusion") or {}
    key = rec.get("key", "")
    for name in (fz.get("cells_body"), f"aruco cells {key}", f"aruco white {key}"):
        b = comp.bRepBodies.itemByName(name) if name else None
        if b is not None:
            return b
    if cf is not None:
        try:
            import aruco_feature as F
            bf = F.base_features(cf)[1]
            if bf is not None and bf.bodies.count:
                return bf.bodies.item(0)
        except Exception:                          # noqa: BLE001
            pass
    return None


def record_drift(des, comp, rec):
    """'' when a record still describes what its live ArUco Extrude makes
    (or it has no live node: then the record IS the tag), else why not.
    Cheap -- no timeline roll: the node's id / dictionary / layout against
    the record's, then its pattern body against the record's frame + spec
    (footprint and top to 1 um, and the black cells' centres inside it, so
    a turned pattern with the same outline is caught too)."""
    F, cf = _node(des, rec)
    if cf is None:
        return ""
    try:
        s, n = F.settings_of(cf) or {}, F.numbers_of(cf)
        sp = rec.get("spec") or {}
        if s.get("dictionary") and s.get("dictionary") != sp.get("dictionary"):
            return f"dictionary {sp.get('dictionary')} -> {s.get('dictionary')}"
        if s.get("layout") and s.get("layout") != sp.get("layout", "single"):
            return f"layout {sp.get('layout')} -> {s.get('layout')}"
        if "marker_id" in n and int(round(n["marker_id"])) != int(sp.get("marker_id", -1)):
            return f"id {sp.get('marker_id')} -> {int(round(n['marker_id']))}"
        if has_old_frame(rec):
            return "a Frame chosen on 2026-10-08 (removed) -> OpenCV's frame"
        tag = M().TagDef.from_dict(rec, lambda m: None)
        if tag is None:
            return "unreadable record"
        body = pattern_body(comp, rec, cf)
        if body is None:
            return "no pattern body"
        frame = _frame_of(rec)
        ext = _extents(body, frame)
        want = _footprint_cm(tag.spec, _is_white(rec, body), (rec.get("fusion") or {}).get("mark"))
        got = (ext[0][0], ext[0][1], ext[1][0], ext[1][1])
        err = max(abs(a - b) for a, b in zip(got, want))
        top = getattr(tag.spec, "black_top_m", tag.spec.raise_m) * 100.0
        if _is_white(rec, body):
            top = tag.spec.raise_m * 100.0
        err = max(err, abs(ext[2][1] - top))
        if err > FRAME_TOL_CM:
            return (f"pattern {err * 10.0:.3f} mm from the record (footprint "
                    f"{[round(v * 10, 3) for v in got]} mm, record "
                    f"{[round(v * 10, 3) for v in want]} mm)")
        if not _is_white(rec, body):
            mid = (ext[2][0] + ext[2][1]) / 2.0
            o, x, y, z = frame
            inside = adsk.fusion.PointContainment.PointInsidePointContainment
            for (x0, y0, x1, y1) in M().cell_boxes(tag.spec)[:12]:
                u, v = (x0 + x1) * 50.0, (y0 + y1) * 50.0
                p = [o[i] + x[i] * u + y[i] * v + z[i] * mid for i in range(3)]
                if body.pointContainment(adsk.core.Point3D.create(*p)) != inside:
                    return "the pattern differs from the record's (turned or renumbered)"
        return ""
    except Exception as exc:                       # noqa: BLE001
        return f"not checked: {type(exc).__name__}: {exc}"[:200]


def evaluated_record(des, rec, cf=None):
    """A record as its node evaluates NOW at its own place in the timeline
    (rolled there and back; nothing written -- the IDs dialog's preview).
    The caller repaints (a roll replays the nodes' bodies) or, in a
    command's executePreview, lets Fusion undo the whole thing."""
    F, node = _node(des, rec)
    cf = cf or node
    if cf is None:
        return rec
    how = F._roll_to(cf)
    try:
        ev = F.evaluate(F.target_of(cf), F.settings_of(cf), F.numbers_of(cf))
    finally:
        F._roll_back(des, how)
    o, x, y, z = ev["tag"]
    out = dict(rec)
    out.update(origin=list(o), x_axis=list(x), y_axis=list(y), z_axis=list(z),
               spec=ev["spec"].to_dict())
    out.pop("frame", None)                     # 2026-10-08's Frame: OpenCV's frame now
    out.pop("frame_offset", None)
    return out


def _comp_by_label(des, label):
    if not label or label == des.rootComponent.name:
        return des.rootComponent
    for c in des.allComponents:
        if c.name == label:
            return c
    return None


def fresh_collect(des=None, write=False, report=None, evaluate=True):
    """collect() with every live tag as its node makes it NOW.

    A record that disagrees with its node (record_drift) is re-evaluated at
    the node: write=True rewrites it (aruco_feature.refresh_one: record +
    Joint Origin, what export does) -- the IDs dialog's OK; write=False only
    reads (the preview). Records that agree cost no timeline roll.
    evaluate=False rolls nothing at all: a stale record is only reported.
    `report` (a list) gets one entry per stale tag."""
    des = des or design()
    raw = collect(des)
    out, rolled = [], False
    for rec in raw["tags"]:
        comp = _comp_by_label(des, rec.get("component"))
        why = record_drift(des, comp, rec) if comp is not None else ""
        if not why:
            out.append(rec)
            continue
        F, cf = _node(des, rec)
        new = rec
        if not evaluate:
            if report is not None:
                report.append({"component": rec.get("component") or "(root)",
                               "key": rec.get("key"), "why": why, "evaluated": False})
            out.append(rec)
            continue
        try:
            if write:
                new = dict(F.refresh_one(cf), component=rec.get("component", ""))
            else:
                new = evaluated_record(des, rec, cf)
            rolled = True
        except Exception as exc:                   # noqa: BLE001
            raw["warnings"].append(f"tag '{rec.get('key')}' on '{rec.get('component') or '(root)'}':"
                                   f" its record is stale ({why}) and re-evaluating its "
                                   f"ArUco Extrude failed: {type(exc).__name__}: {exc}"[:400])
        if report is not None:
            d = [round((a - b) * 10.0, 4) for a, b in zip(new.get("origin") or [0] * 3,
                                                          rec.get("origin") or [0] * 3)]
            report.append({"component": rec.get("component") or "(root)",
                           "key": rec.get("key"), "why": why, "written": bool(write),
                           "moved_mm": d,
                           "size_mm": [round((rec.get("spec") or {}).get("marker_length_m", 0) * 1000, 4),
                                       round((new.get("spec") or {}).get("marker_length_m", 0) * 1000, 4)]})
        out.append(new)
    if rolled:
        try:
            import aruco_feature as F
            F.paint_all(des)          # a roll replays the nodes' bodies (colours)
        except Exception:                          # noqa: BLE001
            pass
    raw["tags"] = out
    return raw


# --------------------------------------------------------------- variants

VARIANT_BUILD = 2       # variants built before this (no "build") are rebuilt once


def _occ_at(owner_comp, rel_path):
    """The occurrence at `rel_path` (a+b+c) in `owner_comp`'s context: the
    native occurrence for one level, a proxy down the chain below it."""
    occ, comp = None, owner_comp
    for name in M().split_path(rel_path):
        nat = comp.occurrences.itemByName(name)
        if nat is None:
            return None
        occ = nat if occ is None else nat.createForAssemblyContext(occ)
        comp = nat.component
    return occ


_proxy_in = _occ_at          # the old name (bridge actions, tests)


def _rel_transform(owner_comp, rel_path):
    """Where the occurrence at `rel_path` puts its component in
    `owner_comp`'s space: the NATIVE occurrences' transforms composed down
    the path (each relative to its parent), or None."""
    m, comp = None, owner_comp
    for name in M().split_path(rel_path):
        nat = comp.occurrences.itemByName(name)
        if nat is None:
            return None
        t = nat.transform2.copy()
        if m is not None:
            t.transformBy(m)
        m = t
        comp = nat.component
    return m


def place(des, owner_comp, occ, transform):
    """Make sure `occ` (native in `owner_comp`) sits at `transform` (owner
    space). Measured 2026-10-08: an occurrence added to a component two
    levels down landed at L * P instead of P * L, and Fusion sets a nested
    occurrence's transform only through a ROOT proxy, in root space ("3 :
    transform overrides can only be set on Occurrence proxy from root
    component"). -> True when it is there."""
    if _close(occ.transform2.asArray(), transform.asArray()):
        return True
    try:
        if owner_comp == des.rootComponent:
            occ.transform2 = transform
        else:
            hosts = des.rootComponent.allOccurrencesByComponent(owner_comp)
            if hosts.count:
                host = hosts.item(0)
                world = transform.copy()
                world.transformBy(host.transform2)
                occ.createForAssemblyContext(host).transform2 = world
    except Exception:                              # noqa: BLE001
        pass
    return _close(occ.transform2.asArray(), transform.asArray())


def _variant_name(template, spec):
    short = spec.dictionary.replace("DICT_", "")
    return f"{template} · {short} #{spec.marker_id}"


def _slab(spec, frame, lo, hi, white, mark=None, triangle=True):
    """A pattern footprint as one temporary solid over z in [lo, hi] (cm):
    the black cells, or (white) the quiet zone + white modules -- with the
    template's corner mark (its record's "mark"): a ChArUco board's cut
    top-left square, the corner triangle (black: joined, unless `triangle`
    is False; white: a notch)."""
    tbm = adsk.fusion.TemporaryBRepManager.get()
    o, x, y, z = frame
    P, V = adsk.core.Point3D.create, adsk.core.Vector3D.create

    def box(cx, cy, w, h, zlo, zhi):
        c = [o[i] + x[i] * cx + y[i] * cy + z[i] * (zlo + zhi) / 2.0 for i in range(3)]
        return tbm.createBox(adsk.core.OrientedBoundingBox3D.create(
            P(*c), V(*x), V(*y), w, h, zhi - zlo))
    cells = None
    pad = 0.001 if white else 0.0          # cut cells a hair taller: no coplanar faces
    for (x0, y0, x1, y1) in M().cell_boxes(spec):
        b = box((x0 + x1) * 50.0, (y0 + y1) * 50.0, (x1 - x0) * 100.0, (y1 - y0) * 100.0,
                lo - pad, hi + pad)
        if cells is None:
            cells = b
        else:
            tbm.booleanOperation(cells, b, adsk.fusion.BooleanTypes.UnionBooleanType)
    if not white:
        return mark_cells(cells, frame, mark, lo, hi, triangle=triangle) if mark else cells
    if mark and mark_parts(mark)[0] == "black_cut":
        # the cut cells come out of the white: the corner stays white
        cells = mark_cells(cells, frame, mark, lo - pad, hi + pad)
    tw, th = (v * 100.0 for v in spec.tile_size_m)
    out = box(0.0, 0.0, tw, th, lo, hi)
    if cells is not None:
        tbm.booleanOperation(out, cells, adsk.fusion.BooleanTypes.DifferenceBooleanType)
    return mark_white(out, frame, mark, lo, hi) if mark else out


def _fill_box(spec, frame, lo, white):
    """Whatever the old pattern pocketed, solid again: its footprint's box
    from `lo` up to the face (z = 0). (A corner triangle's pocket lies
    outside it and is kept: the copy's triangle goes back into it.)"""
    o, x, y, z = frame
    x0, x1, y0, y1 = _footprint_cm(spec, white)
    c = [o[i] + x[i] * (x0 + x1) / 2.0 + y[i] * (y0 + y1) / 2.0 + z[i] * lo / 2.0
         for i in range(3)]
    return adsk.fusion.TemporaryBRepManager.get().createBox(
        adsk.core.OrientedBoundingBox3D.create(
            adsk.core.Point3D.create(*c), adsk.core.Vector3D.create(*x),
            adsk.core.Vector3D.create(*y), x1 - x0, y1 - y0, -lo))


def _body_sig(b):
    bb = b.boundingBox
    return [b.name, round(b.volume, 7),
            [round(c, 5) for c in (bb.minPoint.x, bb.minPoint.y, bb.minPoint.z,
                                   bb.maxPoint.x, bb.maxPoint.y, bb.maxPoint.z)]]


def _sources_sig(template_comp):
    """What a variant copies, as numbers: changes when the template's
    bodies do (an upstream edit), so a kept variant is never a stale copy."""
    return [_body_sig(b) for b in template_comp.bRepBodies if b.isSolid]


def _close(a, b, tol=1e-6):
    try:
        return all(abs(float(p) - float(q)) <= tol for p, q in zip(a, b)) and len(a) == len(b)
    except (TypeError, ValueError):
        return False


def _frame_flat(frame):
    return [round(float(c), 9) for v in frame for c in v]


def _copy_face_colours(src, dst):
    """Face-level appearances (a joined tile's white faces) onto the copy:
    a copied face that lies on a painted source face takes its colour."""
    painted = []
    try:
        for f in src.faces:
            if f.appearance is not None and (src.appearance is None
                                             or f.appearance.name != src.appearance.name):
                painted.append(f)
    except Exception:                              # noqa: BLE001
        return 0
    n = 0
    for nf in dst.faces if painted else []:
        p = nf.pointOnFace
        for f in painted[:200]:
            try:
                ok, prm = f.evaluator.getParameterAtPoint(p)
                if not (ok and f.evaluator.isParameterOnFace(prm)):
                    continue
                ok, q = f.evaluator.getPointAtParameter(prm)
                if ok and q.distanceTo(p) < 1e-5:
                    nf.appearance = f.appearance
                    n += 1
                    break
            except Exception:                      # noqa: BLE001
                continue
    return n


def _joint_geometry(occ, comp, frame, top_cm=0.0):
    """Where a variant's as-built joint is drawn: the centre of the face the
    tag is on (the old joints sat at the component's origin, which for
    Zach's rack is 30 cm below the plates). None when no such face."""
    o, x, y, z = frame
    best, best_d = None, 9e9
    for plane_z in (0.0, top_cm):
        for b in comp.bRepBodies:
            for f in b.faces:
                g = f.geometry
                if not isinstance(g, adsk.core.Plane):
                    continue
                if abs(_dot(_unit(_v(g.normal)), z)) < 0.9999:
                    continue
                if abs(_dot(_sub(_v(f.pointOnFace), o), z) - plane_z) > 1e-4:
                    continue
                bb = f.boundingBox
                c = [(bb.minPoint.x + bb.maxPoint.x) / 2.0, (bb.minPoint.y + bb.maxPoint.y) / 2.0,
                     (bb.minPoint.z + bb.maxPoint.z) / 2.0]
                d = _sub(c, o)
                dist = math.sqrt(_dot(d, d))
                if dist < best_d:
                    best, best_d = f, dist
        if best is not None:
            break
    if best is None:
        return None
    return adsk.fusion.JointGeometry.createByPlanarFace(
        best.createForAssemblyContext(occ), None,
        adsk.fusion.JointKeyPointTypes.CenterKeyPoint)


def _build_variant(des, owner_comp, template_comp, tag, spec, slot, transform, rec=None):
    """A new tag part in `owner_comp`, placed by `transform` (the template
    occurrence's, in owner space): every body of the template copied, the
    body the pattern pockets with its old pockets filled and this copy's
    cut, and this copy's pattern -- all from `tag`, the template tag as its
    node makes it NOW (fresh_collect), and `rec` its record (the names)."""
    tbm = adsk.fusion.TemporaryBRepManager.get()
    if rec is None:
        rec = next((r for r in template_records(template_comp) if r.get("key") == tag.key),
                   None)
    if rec is None:
        raise ValueError(f"template '{template_comp.name}' has no tag '{tag.key}'")
    fz = rec.get("fusion") or {}
    frame = (tag.origin, tag.x_axis, tag.y_axis, tag.z_axis)
    pat_src = pattern_body(template_comp, rec)
    white = _is_white(rec, pat_src)
    # "Chamfer top-left corner": the copy carries the template's mark. A cut
    # tile comes along in the tile's copy; a cut ChArUco square and the
    # corner triangle are rebuilt with the copy's pattern
    mark = fz.get("mark") if mark_parts(fz.get("mark"))[0] != "none" else None
    # the pattern's slab as built, measured along the fresh frame; a record
    # made before ArUco Extrude: [-depth, raise]
    if pat_src is not None:
        lo, hi = _extents(pat_src, frame)[2]
    else:
        lo, hi = -tag.spec.depth_m * 100.0, tag.spec.raise_m * 100.0
    tile_name = fz.get("tile_body")
    tile_src = template_comp.bRepBodies.itemByName(tile_name) if tile_name else None
    if tile_name and tile_src is None:
        raise ValueError(f"template '{template_comp.name}' lost its body '{tile_name}'")
    temps = []                     # (temp body, source body or None, role)
    if tile_src is not None:
        tile = tbm.copy(tile_src)
        if lo < -1e-6:             # the pattern was inlaid: refill, then cut this one
            tbm.booleanOperation(tile, _fill_box(tag.spec, frame, lo, white),
                                 adsk.fusion.BooleanTypes.UnionBooleanType)
            # from the pattern's bottom up to the face (z = 0) even when its
            # top stays below it (a recessed black: the pocket is open);
            # the triangle's pocket was never filled (outside the fill box)
            tbm.booleanOperation(tile, _slab(spec, frame, lo, max(0.0, hi), white, mark,
                                             triangle=False),
                                 adsk.fusion.BooleanTypes.DifferenceBooleanType)
        temps.append((tile, tile_src, "tile"))
    temps.append((_slab(spec, frame, lo, hi, white, mark), pat_src, "pattern"))
    # Every OTHER body comes along unchanged (the template occurrence is
    # hidden for this copy): a housing's shaft, another tag's cells.
    for b in template_comp.bRepBodies:
        if b.isSolid and b != tile_src and b != pat_src:
            temps.append((tbm.copy(b), b, "other"))
    occ = owner_comp.occurrences.addNewComponent(transform)
    if not place(des, owner_comp, occ, transform):
        occ.deleteMe()
        raise RuntimeError(f"the tag part for '{slot}' could not be placed at its copy")
    comp = occ.component
    comp.name = _variant_name(template_comp.name, spec)
    bf = comp.features.baseFeatures.add()
    bf.startEdit()
    try:
        for t, _src, _role in temps:
            comp.bRepBodies.add(t, bf)
    finally:
        bf.finishEdit()
    bf.name = f"ArUco {spec.dictionary} #{spec.marker_id}"
    colour = appearance(des, WHITE if white else BLACK)
    for i, (_t, src, role) in enumerate(temps):
        nb = bf.bodies.item(i)
        if role == "pattern":
            nb.name = (pat_src.name if pat_src is not None
                       else f"aruco {'white' if white else 'cells'} {tag.key}")
            if colour:
                nb.appearance = colour
            continue
        nb.name = src.name
        try:
            if src.appearance is not None:
                nb.appearance = src.appearance
            if role == "tile":
                _copy_face_colours(src, nb)
            if not src.isLightBulbOn:
                nb.isLightBulbOn = False
        except Exception:                          # noqa: BLE001
            pass
    info = {"template": template_comp.name, "slot": slot, "spec": spec.to_dict(),
            "owner": _component_label(des, owner_comp), "base_feature": bf.entityToken,
            "build": VARIANT_BUILD, "frame": _frame_flat(frame),
            "template_spec": tag.spec.to_dict(), "sources": _sources_sig(template_comp)}
    comp.attributes.add(G, VARIANT_ATTR, json.dumps(info, sort_keys=True))
    # A variant answers its template's slot and is never a source (resolve /
    # collect skip it): no Joint Origin of its own -- the copy's OpenCV frame
    # is the template's, through the copy. Its record keeps the template's
    # key (variant_of says whose): lookups by key use template_records.
    vrec = M().TagDef(key=tag.key, component=comp.name, spec=spec, origin=list(frame[0]),
                      x_axis=list(frame[1]), y_axis=list(frame[2]), z_axis=list(frame[3]),
                      variant_of={"template": template_comp.name, "slot": slot}).to_dict()
    write_tag_records(comp, [vrec])
    return occ


def _existing_variants(owner_comp):
    out = {}
    for occ in owner_comp.occurrences:
        a = occ.component.attributes.itemByName(G, VARIANT_ATTR)
        if a:
            try:
                info = json.loads(a.value)
            except ValueError:
                continue
            out.setdefault(info.get("slot"), []).append((occ, info))
    return out


def _variant_current(occ, info, want):
    """'' when an existing variant still is what `want` would build, else
    why not: its id, the template frame / spec / bodies it was copied from,
    and where it sits (a variant from before 2026-10-08 has none of these
    and is rebuilt once)."""
    if info.get("spec") != want["inst"].spec.to_dict():
        return "id"
    if info.get("build") != VARIANT_BUILD:
        return "built by an older add-in"
    tag = want["inst"].tag
    if not _close(info.get("frame") or [],
                  _frame_flat((tag.origin, tag.x_axis, tag.y_axis, tag.z_axis))):
        return "the template tag moved"
    if info.get("template_spec") != tag.spec.to_dict():
        return "the template tag changed"
    if info.get("sources") != _sources_sig(want["template"]):
        return "the template's bodies changed"
    t = want["transform"]
    if t is not None and not _close(occ.transform2.asArray(), t.asArray()):
        return "the template copy moved"
    return ""


def apply_variants(des=None, fresh=True):
    """Make every copy's tag GEOMETRY match its resolved id.

    fresh=True (the IDs dialog's OK): live tags whose record lags their node
    get their record rewritten first (fresh_collect), and a variant whose
    template moved, changed or was built by an older add-in is rebuilt.
    Idempotent: re-running with nothing changed touches nothing. Returns what
    it created / kept / removed / hid / refreshed, for the dialog and the
    report.
    """
    des = des or design()
    markers = M()
    # nothing selected may point at what is deleted or rebuilt below (a
    # selection holding a deleted ArUco Extrude node crashed Fusion, 2026-10-08)
    try:
        _app().userInterface.activeSelections.clear()
    except Exception:                              # noqa: BLE001
        pass
    refreshed = []
    raw = fresh_collect(des, write=True, report=refreshed) if fresh else collect(des)
    res = resolve_live(des, raw)
    recs = {(r.get("component") or "", r.get("key")): r for r in raw["tags"]}
    report = {"created": [], "kept": [], "removed": [], "hidden": [], "shown": [],
              "refreshed": refreshed, "rebuilt_because": {}, "joints": {},
              "warnings": list(res.warnings)}
    comp_of = {o.fullPathName: o.component for o in des.rootComponent.allOccurrences}
    wanted = {}       # (owner comp name, slot) -> what to build there
    for inst in res.instances:
        if not inst.path or not inst.needs_variant:
            continue
        parts = markers.split_path(inst.path)
        lvl = inst.winning.level
        depth = len(markers.split_path(lvl)) if lvl else 0
        owner_comp = (des.rootComponent if depth == 0
                      else comp_of[markers.join_path(parts[:depth])])
        slot = markers.slot_key(parts[depth:], inst.tag.key)
        rel = markers.join_path(parts[depth:])
        tmpl_occ = _occ_at(owner_comp, rel)
        wanted[(owner_comp.name, slot)] = {
            "owner": owner_comp, "inst": inst, "depth": depth, "rel": rel,
            "occ": tmpl_occ, "template": tmpl_occ.component if tmpl_occ else None,
            "transform": _rel_transform(owner_comp, rel)}

    # every owner that could hold variants: root + all components
    owners = [des.rootComponent] + [c for c in des.allComponents
                                    if c != des.rootComponent and not is_variant(c)]
    kept = set()
    for owner_comp in owners:
        existing = _existing_variants(owner_comp)
        for slot, items in existing.items():
            k = (owner_comp.name, slot)
            want = wanted.get(k)
            for occ, info in items:
                why = "no longer needed"
                if want and k not in kept and want["template"] is not None:
                    why = _variant_current(occ, info, want)
                    if not why:
                        kept.add(k)
                        report["kept"].append(f"{owner_comp.name}/{occ.name}")
                        continue
                comp_name = occ.component.name
                occ.deleteMe()
                report["removed"].append(f"{owner_comp.name}/{comp_name}")
                report["rebuilt_because"][comp_name] = why
    for (owner_name, slot), w in wanted.items():
        owner_comp, inst, rel, template_occ = w["owner"], w["inst"], w["rel"], w["occ"]
        if (owner_name, slot) not in kept:
            if template_occ is None or w["transform"] is None:
                report["warnings"].append(f"{inst.name}: cannot see '{rel}' from "
                                          f"'{owner_comp.name}' — no variant built")
                continue
            tmpl = w["template"]
            rec = recs.get((_component_label(des, tmpl), inst.tag.key))
            try:
                occ = _build_variant(des, owner_comp, tmpl, inst.tag, inst.spec, slot,
                                     w["transform"], rec)
            except Exception as exc:               # noqa: BLE001
                report["warnings"].append(f"{inst.name}: no tag part built: "
                                          f"{type(exc).__name__}: {exc}"[:300])
                continue
            host = template_occ
            parent_rel = markers.join_path(markers.split_path(rel)[:-1])
            if parent_rel:
                host = _occ_at(owner_comp, parent_rel) or template_occ
            tag = inst.tag
            frame = (tag.origin, tag.x_axis, tag.y_axis, tag.z_axis)
            geo, how = None, "no face on the tag's plane"
            try:
                geo = _joint_geometry(occ, occ.component, frame)
            except Exception as exc:               # noqa: BLE001
                geo, how = None, f"no joint geometry: {type(exc).__name__}: {exc}"[:200]
            for g in ((geo, None) if geo is not None else (None,)):
                try:
                    ji = owner_comp.asBuiltJoints.createInput(occ, host, g)
                    ji.setAsRigidJointMotion()
                    owner_comp.asBuiltJoints.add(ji)
                    report["joints"][occ.component.name] = ("at the tag face's centre"
                                                            if g is not None else how)
                    break
                except Exception as exc:           # noqa: BLE001
                    how = f"with geometry refused: {type(exc).__name__}: {exc}"[:200]
                    if g is None:
                        report["warnings"].append(f"{inst.name}: rigid joint failed: {exc}")
            report["created"].append(f"{owner_comp.name or '(root)'}/{occ.component.name}")
        if template_occ is not None and template_occ.isLightBulbOn:
            template_occ.isLightBulbOn = False
            report["hidden"].append(f"{owner_comp.name}:{rel}")
    # An outer override beats an inner one, so along that path every DEEPER
    # realisation of the same slot (a variant the arm built for its own
    # copies) must go dark too -- seen from the outer owner, for that path
    # only. Without this, Arm:2's actuators would wear the arm's tag AND the
    # root's, one inside the other.
    for (owner_name, slot), w in wanted.items():
        owner_comp, inst, depth = w["owner"], w["inst"], w["depth"]
        parts = markers.split_path(inst.path)
        for d2 in range(depth + 1, len(parts)):
            inner = comp_of.get(markers.join_path(parts[:d2]))
            if inner is None:
                continue
            inner_slot = markers.slot_key(parts[d2:], inst.tag.key)
            for vocc, _info in _existing_variants(inner).get(inner_slot, []):
                rel = markers.join_path(parts[depth:d2] + [vocc.name])
                proxy = _occ_at(owner_comp, rel)
                if proxy is not None and proxy.isLightBulbOn:
                    proxy.isLightBulbOn = False
                    report["hidden"].append(f"{owner_comp.name}:{rel}")
    # template tags that no longer need hiding: any hidden template proxy
    # whose instance resolves back to the modelled id
    for inst in res.instances:
        if inst.needs_variant or not inst.path:
            continue
        o = _occ_at(des.rootComponent, inst.path)
        if o is not None and not o.isLightBulbOn:
            o.isLightBulbOn = True
            report["shown"].append(inst.path)
    return report


def summary(des=None):
    des = des or design()
    res = resolve_live(des)
    return {"instances": [{"path": i.path, "key": i.tag.key, "id": i.spec.marker_id,
                           "dictionary": i.spec.dictionary,
                           "modelled_id": i.tag.spec.marker_id,
                           "source": i.winning.level if i.winning.level != "model" else "model",
                           "owner": i.winning.owner, "needs_variant": i.needs_variant,
                           "hidden": i.hidden} for i in res.instances],
            "duplicates": {k: [i.name for i in v] for k, v in res.duplicates().items()},
            "warnings": res.warnings}
