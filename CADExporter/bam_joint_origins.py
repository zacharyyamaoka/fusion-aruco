"""Fusion **Joint Origins** as dumb, unit-annotated records — the connector
half of the payload (`joining contract` §2).

Zach's ruling #3: *"Frames are authored with the inside view: Fusion Joint
Origins. No standalone frame-authoring tool."* Until now a Joint Origin was
visible to this exporter only when a real `Joint` happened to use one
(`bam_fusion_export._joint_origin` reaches through `.geometry`); the
`Component.jointOrigins` collection was never enumerated at all. So a named
frame that is not part of a kinematic joint — a TCP, a camera mount, a
connector — could not leave Fusion.

This module enumerates them, once per **component definition** (the same rule
joints and decals already follow: the record belongs to the definition, the
world pose belongs to the instance, and the expansion happens offline in
`bam_cad_core.frames` where it is testable).

### What is read, and how sure we are

The Fusion API offers two frames for one Joint Origin and does not say plainly
which one carries the offset/angle/flip a user typed into the dialog:

    jointOrigin.transform                  Matrix3D: origin + X/Y/Z axes
    jointOrigin.primary/secondary/thirdAxisVector    "as shown by the triad"
    jointOrigin.geometry.origin            the underlying geometry's point
    jointOrigin.geometry.primary/...       the underlying geometry's axes

`transform` is tried first because it is the only member that answers origin
AND orientation in one read, and because its docstring says it exists for the
case where the JointGeometry cannot be obtained at all. Whatever path is
taken is recorded in `frame_source` / `axes_source`, and the runner-up reads
(`origin_geometry`, `offset`, `angle_rad`, `is_flipped`) go into the record
beside it — so the first real run in Fusion can CHECK the choice instead of
discovering it as a part snapped on inside out. See
`docs/dev/joint_origin_api_notes.md` for the one-line check per attribute.

Axis naming follows Fusion's own docstrings, which are counter-intuitive:
`primaryAxisVector` is **Z**, `secondaryAxisVector` is **X**, and
`thirdAxisVector` is **Y**. Getting that pair backwards rotates every
connector 90 degrees about nothing in particular, so it is stated here rather
than inferred at the call site.

### The host face

A connector is a named frame the sidecar typed, and what makes a mate
*computable* is the hole pattern around it. `jointOrigin.geometry.entityOne`
is the entity the origin was placed on; when that is a planar `BRepFace` its
loops are harvested:

  * a circular INNER loop becomes a `hole` or a `boss`, decided by WHICH SIDE
    of the host face the adjoining cylinder is on: into the material is a
    hole, proud of it is a boss. That needs only the host plane's outward
    normal, which is `Plane.normal` plus `isParamReversed` — the one
    orientation rule the Fusion documentation states plainly. Not guessed from
    the loop's winding (a kernel convention), and no longer decided by
    `BRepFace.evaluator`, whose sign convention is not verified on this box;
    that reading is still taken and recorded as `kind_evidence`.
  * an INNER loop whose edges are all straight lines becomes a `polygon`
    carrying `"where": "inner"` — a pocket seen from the face it is cut into.
  * the face's own OUTER loop, when its edges are all straight lines, becomes
    a `polygon` carrying `"where": "outer"` (contract §2, amended 2026-09-20).
    That is the profile of a peg end face or of a pocket FLOOR, and §3.3 case
    2 takes a child connector's rotational stops from it — without it a square
    peg would lose its four stops the moment its frame moved to the surface it
    actually mates against. A circular outer boundary is NOT recorded: it is
    not a hole in this face, and the contract's `features` are inner loops.
  * anything else is skipped, silently by design: the contract says "omit
    others", and a half-understood pocket is worse than none.

### When the origin was NOT placed on a planar face

Clicking the flat face is only one of the ways a designer puts a Joint Origin
on a part, and on the first real board it was the minority: two of Zach's
three were placed on something else (a sketch point lifted onto the face by
the dialog's offsetZ), came out as `host: other`, and were then dropped from
every per-body bundle without a word (adversarial round 1, R1-F1). So an
`entityOne` that is not a planar `BRepFace` is RESOLVED to the planar face the
origin actually sits on, by the route its type allows, and the route is
recorded as `host.resolved_by`:

    BRepFace, planar    "entityOne"        the face itself — unchanged
    BRepEdge            "edge.faces"       a circular hole edge borders the
                                           flat face and the hole wall; the
                                           Plane among them is the host
    BRepVertex          "vertex.faces"     the Plane among its faces
    BRepFace, curved    "adjacent_planar"  a hole's wall: the planar faces
                                           across its edges
    anything else       "point_on_face"    a SketchPoint / SketchCircle /
                                           ConstructionPoint has no body at
                                           all, so the component's bodies are
                                           scanned for the one planar face the
                                           origin is ON

Every route asks the same two questions of a candidate: does its PLANE contain
the Joint Origin's own origin, and is its normal along the frame's ±Z. The
second is not a preference, it is the contract: `features` and `loops` are 2D
in the frame's own XY plane with "z = 0 the host face", which is only true of
a face whose normal is the frame's Z. The scan asks a third, because a plane
is infinite and a face is not: is the point inside the face's own boundary
(`evaluator.getParameterAtPoint` + `isParameterOnFace`). A route that finds
nothing falls through to the scan; exactly one hit wins; none or several keep
`host.kind == "other"` and add a warning that says how many — never a silent
pick, and never a silent drop.

The FRAME is never moved by any of this. It is the Joint Origin's own
transform, and the resolved face only decides what is harvested around it,
exactly as if the designer had clicked that face. `host.entity_type` (what
`entityOne` really was) and `host.geometry_type` (the JointGeometry's own
type) are recorded on every record, resolved or not, so a run in Fusion can
answer "what did the designer click" from the payload alone. So is
`host.body_name`, which is how the runner's per-body split attributes a frame.

Known gap, said here rather than discovered: a SKETCH POINT at the centre of
a hole is in the face's plane but not on the face (it is in the hole), so the
scan finds nothing and warns. Clicking the hole's circular edge or its wall
instead resolves.

WHICH loop is the outer one is `BRepLoop.isOuter`, read defensively. It used
to be read with a default of True ("skip it"), which on a build that cannot
answer costs exactly the boundary the amendment needs. It is now read as
True / False / **None**, and when the answer is missing — or when it marks
more than one loop — the fallback is a measurement rather than a guess: the
loop whose enclosed area is largest is the boundary, because on a planar face
the outer loop contains every inner one. Which rule fired is recorded as
`loops_source`, in the same style as `frame_source` / `axes_source`, so a real
run SAYS what it did.

Centres and vertices come out as **2D coordinates in the Joint Origin's own
XY plane** (z = 0 is the host face) — the "pure simple mathematical" record
the solver wants, and the one thing that makes a plate and the board it bolts
to comparable without either of them knowing about the other.

### Nothing here may raise into the export

Every read goes through `bam_fusion_export._safe` or a local `getattr`
guard, and `collect_joint_origins` catches at the top as well. A Joint Origin
on a curved face, on a deleted entity, or on a Fusion build whose API differs
still produces a record — with empty `features` and `loops`, which is exactly
what the contract asks for.
"""
from __future__ import annotations

import math
import traceback

import adsk.core
import adsk.fusion



def _safe(getter, default, warnings, label):
    """Read through a getter; on any API error, warn and return `default`.
    (The same helper as bam_fusion_export._safe, kept local so this file
    loads on its own -- the ArUco add-in uses it without the CAD Exporter.)"""
    try:
        return getter()
    except Exception:
        warnings.append(f"{label}: {traceback.format_exc(limit=1).splitlines()[-1]}")
        return default

SCHEMA = "bam_joint_origins/1"

#: Two points are the same point when they are closer than this, in Fusion's
#: internal centimetres. 1e-6 cm = 10 nanometres — tighter than any modelling
#: tolerance and looser than float noise on a composed transform.
POINT_TOL = 1e-6

#: How parallel two unit vectors have to be to count as parallel. 1e-4 rad of
#: slack, i.e. about 0.006 degrees.
DIR_TOL = 1e-4


# --------------------------------------------------------------- vector maths
# Deliberately local and deliberately tiny: this module runs inside Fusion,
# where numpy is not guaranteed, and bam_cad_core.geometry is on the other
# side of the waist (it may not import adsk, and this module may not be
# imported by it).

def _sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def _norm(v):
    return math.sqrt(_dot(v, v))


def _unit(v):
    n = _norm(v)
    if n < 1e-12 or not math.isfinite(n):
        return None
    return [v[0] / n, v[1] / n, v[2] / n]


def _as_xyz(obj):
    """A Fusion Point3D / Vector3D -> [x, y, z], or None.

    `asArray()` is the member every geometry type in the API carries; the
    `.x/.y/.z` fallback is for a fake, a proxy or an API rev that does not.
    Anything that answers neither is None rather than an exception — this is
    the deepest read in the module and it runs once per edge.
    """
    if obj is None:
        return None
    try:
        vals = list(obj.asArray())
    except Exception:
        try:
            vals = [obj.x, obj.y, obj.z]
        except Exception:
            return None
    if len(vals) < 3:
        return None
    try:
        out = [float(vals[0]), float(vals[1]), float(vals[2])]
    except (TypeError, ValueError):
        return None
    return out if all(math.isfinite(v) for v in out) else None


def _type_name(obj):
    """The last segment of a Fusion `objectType`, e.g. 'Plane', 'Circle3D'.

    Fusion's own `objectType` string ("adsk::core::Plane") is the only
    reliable way to ask what a piece of geometry IS without importing the
    class and hoping `isinstance` works across a proxy. `bam_fusion_export`
    already uses exactly this idiom for decal faces.
    """
    if obj is None:
        return ""
    try:
        return str(obj.objectType).split("::")[-1]
    except Exception:
        return type(obj).__name__


# ------------------------------------------------------------------ the frame

def _matrix_frame(mat):
    """A Fusion Matrix3D -> (origin, x, y, z) or None.

    `Matrix3D.getAsCoordinateSystem()` (fusion API, core.py) hands back
    exactly that tuple and is tried first, because it is the API SAYING which
    vector is which rather than us reading a layout off a flat array.

    The fallback parses `asArray()`: 16 floats, ROW-major — the same layout
    `occurrence.transform2` is recorded in — so the translation is cells
    3/7/11 and the basis vectors are the COLUMNS. Reading them as rows
    transposes the frame, which is invisible on any axis-aligned test case and
    wrong on every other one, so it is spelled here once and nowhere else.
    """
    try:
        origin, x, y, z = mat.getAsCoordinateSystem()
        frame = (_as_xyz(origin), _unit(_as_xyz(x) or []),
                 _unit(_as_xyz(y) or []), _unit(_as_xyz(z) or []))
        if all(v is not None for v in frame):
            return frame
    except Exception:
        pass
    try:
        cells = [float(v) for v in mat.asArray()]
    except Exception:
        return None
    if len(cells) != 16 or not all(math.isfinite(v) for v in cells):
        return None
    origin = [cells[3], cells[7], cells[11]]
    axes = []
    for i in range(3):
        axis = _unit([cells[0 + i], cells[4 + i], cells[8 + i]])
        if axis is None:
            return None
        axes.append(axis)
    return origin, axes[0], axes[1], axes[2]


def _axes_from(obj):
    """(x, y, z) unit axes off anything carrying Fusion's three axis vectors.

    Fusion's naming, from its own docstrings: `primaryAxisVector` is
    conceptually **Z**, `secondaryAxisVector` is **X**, `thirdAxisVector` is
    **Y**. Both `JointOrigin` and `JointGeometry` expose all three.
    """
    z = _unit(_as_xyz(getattr(obj, "primaryAxisVector", None)) or [])
    x = _unit(_as_xyz(getattr(obj, "secondaryAxisVector", None)) or [])
    y = _unit(_as_xyz(getattr(obj, "thirdAxisVector", None)) or [])
    if x is None or z is None:
        return None
    if y is None:                     # derivable, and a right-handed frame is
        y = _unit(_cross(z, x))       # what every consumer downstream assumes
    return (x, y, z) if y is not None else None


def _parameter_value(param):
    """A ModelParameter's `.value`, in Fusion's INTERNAL units (cm / radians).

    Recorded rather than used: `offsetX/Y/Z` and `angle` are what the user
    typed into the Joint Origin dialog, and whether the frame we read already
    includes them is the open question this module exists to make checkable.
    A non-zero offset beside a `frame_source` that turns out to ignore it is
    the evidence; the first real Fusion run reads both.
    """
    if param is None:
        return None
    try:
        value = float(param.value)
    except Exception:
        return None
    return value if math.isfinite(value) else None


def _read_frame(jo, w, label):
    """The Joint Origin's own frame, in its component definition's coordinates.

    Returns (origin, x, y, z, frame_source, axes_source) in Fusion's internal
    centimetres, or Nones. Three paths, best first, and the one taken is
    recorded rather than assumed — the whole point of `frame_source`.
    """
    frame = _safe(lambda: _matrix_frame(jo.transform), None, [],
                  f"{label}.transform")
    if frame is not None:
        origin, x, y, z = frame
        return origin, x, y, z, "jointOrigin.transform", "jointOrigin.transform"

    # Second best: the triad's own axes ("as shown by the triad representing
    # the joint origin" — so flip and angle are in them) with the geometry's
    # point, which is the only point left to have.
    axes = _safe(lambda: _axes_from(jo), None, [], f"{label}.axisVectors")
    geo = _safe(lambda: jo.geometry, None, [], f"{label}.geometry")
    origin = _safe(lambda: _as_xyz(geo.origin), None, [],
                   f"{label}.geometry.origin") if geo is not None else None
    if axes is not None and origin is not None:
        return (origin, axes[0], axes[1], axes[2],
                "jointOrigin.geometry.origin", "jointOrigin.axisVectors")

    # Last: everything off the JointGeometry. This frame does NOT include the
    # dialog's offset/angle/flip, so it is recorded with a source that says so
    # and a warning, rather than silently standing in for the real one.
    if geo is not None:
        gaxes = _safe(lambda: _axes_from(geo), None, [],
                      f"{label}.geometry.axisVectors")
        if origin is not None and gaxes is not None:
            w.append(f"{label}: neither JointOrigin.transform nor its axis "
                     "vectors were readable — the frame comes from the raw "
                     "JointGeometry and does NOT include this origin's "
                     "offset, angle or flip")
            return (origin, gaxes[0], gaxes[1], gaxes[2],
                    "jointOrigin.geometry.origin", "jointGeometry.axisVectors")
    w.append(f"{label}: no readable frame — the joint origin is exported "
             "without a pose and cannot be mated")
    return None, None, None, None, "none", "none"


# ------------------------------------------------------------- the host face

def _plane_normal(face, w, label):
    """A planar BRepFace's OUTWARD normal, or None if it is not planar.

    `face.geometry.normal` is the underlying *surface's* normal; the face's
    outward normal is that, negated when `isParamReversed` — a face is a
    trimmed piece of a surface and it may use it back-to-front. Skipping the
    flag makes exactly half of all pockets read as bosses, which is the kind
    of bug that is invisible on the one test case you tried.
    """
    surface = _safe(lambda: face.geometry, None, w, f"{label}.geometry")
    if surface is None or _type_name(surface) != "Plane":
        return None
    normal = _unit(_as_xyz(_safe(lambda: surface.normal, None, w,
                                 f"{label}.geometry.normal")) or [])
    if normal is None:
        return None
    if _safe(lambda: bool(face.isParamReversed), False, [],
             f"{label}.isParamReversed"):
        normal = [-normal[0], -normal[1], -normal[2]]
    return normal


def _face_normal_at(face, point, w, label):
    """A face's OUTWARD normal at a 3D point on it, or None.

    Same `isParamReversed` correction as `_plane_normal`, ON THE ASSUMPTION
    that `face.evaluator` evaluates the SURFACE and the face may be the other
    side of it. That assumption is NOT verified on this box — Autodesk forum
    guidance suggests a face's own evaluator already answers the face-outward
    normal, in which case the correction below inverts the answer.

    So nothing decides anything on this reading any more. `_cylinder_kind`
    records it as evidence beside a measurement that needs no convention, and
    the first real Fusion run is what settles which of the two is right.
    """
    evaluator = _safe(lambda: face.evaluator, None, w, f"{label}.evaluator")
    if evaluator is None:
        return None

    def _read():
        ok, vec = evaluator.getNormalAtPoint(point)
        return _as_xyz(vec) if ok else None

    normal = _unit(_safe(_read, None, w, f"{label}.getNormalAtPoint") or [])
    if normal is None:
        return None
    if _safe(lambda: bool(face.isParamReversed), False, [],
             f"{label}.isParamReversed"):
        normal = [-normal[0], -normal[1], -normal[2]]
    return normal


def _loop_edges(loop, w, label):
    return _safe(lambda: list(loop.edges), [], w, f"{label}.edges")


def _circle_of(edges, w, label):
    """(centre, radius) when this loop IS a circle, else None.

    Two spellings of the same circle reach here and both are real: one closed
    `Circle3D` edge, and two or more `Arc3D` edges that share a centre and a
    radius (what a hole cut by two features looks like after a boolean). A
    loop that is neither is not a circle, and saying so is how a slot avoids
    being exported as a very eccentric hole.
    """
    centres, radii = [], []
    for i, edge in enumerate(edges):
        curve = _safe(lambda: edge.geometry, None, w, f"{label}.edge[{i}]")
        kind = _type_name(curve)
        if kind not in ("Circle3D", "Arc3D"):
            return None
        centre = _as_xyz(_safe(lambda: curve.center, None, w,
                               f"{label}.edge[{i}].center"))
        radius = _safe(lambda: float(curve.radius), None, w,
                       f"{label}.edge[{i}].radius")
        if centre is None or radius is None or not math.isfinite(radius):
            return None
        centres.append(centre)
        radii.append(radius)
    if not centres:
        return None
    for centre, radius in zip(centres[1:], radii[1:]):
        if (_norm(_sub(centre, centres[0])) > POINT_TOL
                or abs(radius - radii[0]) > POINT_TOL):
            return None
    return centres[0], radii[0]


def _polygon_of(edges, w, label):
    """The ordered vertices when every edge of this loop is a straight line.

    Order matters — a polygon's vertices in the wrong order is a bow tie — so
    the chain is walked rather than collected. The walk stitches the edges by
    their shared endpoints rather than trusting `loop.coEdges` to be in order,
    which costs nothing at these sizes and works on an API rev, a proxy or a
    fake that does not expose coEdges at all. A set of segments that does not
    close into exactly one chain is not a polygon, and returns None.
    """
    segments = []
    for i, edge in enumerate(edges):
        curve = _safe(lambda: edge.geometry, None, w, f"{label}.edge[{i}]")
        if _type_name(curve) != "Line3D":
            return None
        a = _as_xyz(_safe(lambda: curve.startPoint, None, w,
                          f"{label}.edge[{i}].startPoint"))
        b = _as_xyz(_safe(lambda: curve.endPoint, None, w,
                          f"{label}.edge[{i}].endPoint"))
        if a is None or b is None:
            return None
        segments.append((a, b))
    if len(segments) < 3:
        return None

    ordered = [segments[0][0], segments[0][1]]
    remaining = list(segments[1:])
    while remaining:
        tail = ordered[-1]
        for index, (a, b) in enumerate(remaining):
            if _norm(_sub(a, tail)) <= POINT_TOL:
                ordered.append(b)
                remaining.pop(index)
                break
            if _norm(_sub(b, tail)) <= POINT_TOL:
                ordered.append(a)
                remaining.pop(index)
                break
        else:
            return None                      # not a single closed chain
    # the walk comes back to where it started; the closing point is implied
    if _norm(_sub(ordered[-1], ordered[0])) <= POINT_TOL:
        ordered.pop()
    return ordered if len(ordered) >= 3 else None


def _other_faces(edges, exclude, w, label):
    """Every distinct face touching these edges, minus the `exclude` ones.

    Identity by `tempId` when Fusion offers one and by object identity
    otherwise: two proxies of one face are different Python objects, so `is`
    alone would let an excluded face back in — and letting the host face back
    in makes every hole read as its own adjoining cylinder.
    """
    excluded = list(exclude)
    excluded_ids = [i for i in
                    (_safe(lambda f=f: f.tempId, None, [], f"{label}.tempId")
                     for f in excluded) if i is not None]
    out, seen = [], []
    for i, edge in enumerate(edges):
        for face in _safe(lambda: list(edge.faces), [], w,
                          f"{label}.edge[{i}].faces"):
            if any(face is f for f in excluded):
                continue
            face_id = _safe(lambda: face.tempId, None, [], f"{label}.tempId")
            if face_id is not None and face_id in excluded_ids:
                continue
            if face_id is not None:
                if face_id in seen:
                    continue
                seen.append(face_id)
            elif any(face is f for f in out):
                continue
            out.append(face)
    return out


def _bbox_midpoint(face, w, label):
    """The centre of a face's bounding box, or None. A cheap second probe."""
    box = _safe(lambda: face.boundingBox, None, w, f"{label}.boundingBox")
    if box is None:
        return None
    lo = _as_xyz(_safe(lambda: box.minPoint, None, w, f"{label}.bbox.min"))
    hi = _as_xyz(_safe(lambda: box.maxPoint, None, w, f"{label}.bbox.max"))
    if lo is None or hi is None:
        return None
    return [(lo[i] + hi[i]) / 2.0 for i in range(3)]


def _cylinder_kind(face, plane_point, host_normal, w, label):
    """('hole' | 'boss', radius, axis, axis_origin, evidence), or None.

    WHICH SIDE OF THE HOST FACE THE CYLINDER IS ON. That is the whole
    measurement, and it needs no convention from the Fusion API at all:

        q       a point known to lie on the cylindrical face
        p0      a point on the HOST plane (the face the Joint Origin sits on)
        N       the host plane's OUTWARD normal

        dot(q - p0, N) < 0   the cylinder runs INTO the material, away from
                             the face you would mate against -> a HOLE
        dot(q - p0, N) > 0   the cylinder stands PROUD of that face -> a BOSS

    `N` comes from `Plane.normal` plus `isParamReversed`, which is the one
    orientation rule Fusion's own documentation states plainly and which
    `_plane_normal` already applies.

    The older measurement — the cylindrical face's outward normal at `q`
    against the radial vector from the axis to `q` — is still taken, and still
    recorded, as `evidence`. It is no longer the decision, because it rests on
    an UNVERIFIED reading of `BRepFace.evaluator`: if the evaluator already
    answers the face-outward normal, `_face_normal_at`'s `isParamReversed`
    correction double-negates it and every hole reads as a boss. Boss never
    pairs with boss (contract §3.2), so that inversion would silently cost
    every pattern mate in the fixture set. `evidence.agrees` is how the first
    real Fusion run settles the convention: a run where it is `false` on every
    hole is the proof that the evaluator needs no correction.
    """
    surface = _safe(lambda: face.geometry, None, w, f"{label}.geometry")
    if _type_name(surface) != "Cylinder":
        return None
    axis = _unit(_as_xyz(_safe(lambda: surface.axis, None, w,
                               f"{label}.geometry.axis")) or [])
    axis_origin = _as_xyz(_safe(lambda: surface.origin, None, w,
                                f"{label}.geometry.origin"))
    radius = _safe(lambda: float(surface.radius), None, w,
                   f"{label}.geometry.radius")
    if axis is None or axis_origin is None or radius is None:
        return None
    if (not isinstance(plane_point, (list, tuple)) or len(plane_point) != 3
            or not isinstance(host_normal, (list, tuple))
            or len(host_normal) != 3):
        return None
    probe = _safe(lambda: face.pointOnFace, None, w, f"{label}.pointOnFace")
    point = _as_xyz(probe)
    if point is None:
        return None

    # --- the decision
    side = _dot(_sub(point, plane_point), host_normal)
    decided_by = "pointOnFace"
    if abs(side) <= POINT_TOL:
        # `pointOnFace` landed in the host plane itself — a zero-height face,
        # or a probe the kernel put on the rim. The bounding box's centre is
        # the second opinion, and it is one the geometry cannot refuse.
        middle = _bbox_midpoint(face, w, label)
        if middle is None:
            return None
        side = _dot(_sub(middle, plane_point), host_normal)
        decided_by = "boundingBox"
    if abs(side) <= POINT_TOL:
        return None                   # in the plane, both ways: not a decision
    kind = "hole" if side < 0.0 else "boss"

    # --- the evidence, which decides nothing
    reversed_ = _safe(lambda: bool(face.isParamReversed), None, [],
                      f"{label}.isParamReversed")
    normal = _face_normal_at(face, probe, w, label)
    sense = None
    if normal is not None:
        offset = _sub(point, axis_origin)
        radial = _unit([offset[i] - _dot(offset, axis) * axis[i]
                        for i in range(3)])
        if radial is not None:
            sense = _dot(normal, radial)
    evaluator_kind = None
    if sense is not None and abs(sense) >= DIR_TOL:
        evaluator_kind = "boss" if sense > 0 else "hole"
    evidence = {
        "decided_by": decided_by,
        "offset_along_host_normal": side,
        "evaluator_kind": evaluator_kind,
        "evaluator_radial_sense": sense,
        "is_param_reversed": reversed_,
        "agrees": (None if evaluator_kind is None
                   else evaluator_kind == kind),
    }
    return kind, radius, axis, axis_origin, evidence


def _depth_and_through(cyl_face, host, host_normal, kind, w, label):
    """(depth, through) of a hole or boss, in internal cm, either may be None.

    Depth is the cylindrical face's extent along the host's normal, taken from
    its own bounding box — cheap, exact for a hole square to the face, and an
    over-estimate for a slanted one (recorded rather than corrected: the
    contract makes depth optional precisely because it is not always knowable).

    `through` is decided from the faces that CAP the cylinder, not from the
    depth, because a depth equal to the plate thickness proves nothing about
    where the hole went. A planar cap whose outward normal opposes the host's
    is the far side of the part — the hole came out. A planar cap pointing the
    SAME way as the host is the floor of a blind pocket, looking back up at
    the opening. Anything else leaves it None.
    """
    depth = None
    box = _safe(lambda: cyl_face.boundingBox, None, w, f"{label}.boundingBox")
    if box is not None:
        lo = _as_xyz(_safe(lambda: box.minPoint, None, w, f"{label}.bbox.min"))
        hi = _as_xyz(_safe(lambda: box.maxPoint, None, w, f"{label}.bbox.max"))
        if lo is not None and hi is not None:
            span = 0.0
            for i in range(3):
                span += abs((hi[i] - lo[i]) * host_normal[i])
            if math.isfinite(span) and span > 0.0:
                depth = span

    if kind == "boss":
        # A boss stands on the face; nothing about it is "through".
        return depth, False

    through = None
    edges = _safe(lambda: list(cyl_face.edges), [], w, f"{label}.edges")
    # The host face is the OPENING, and its normal points the same way as
    # itself — counting it as a cap would read every through hole as blind.
    for cap in _other_faces(edges, (host, cyl_face), w, label):
        cap_normal = _plane_normal(cap, [], f"{label}.cap")
        if cap_normal is None:
            continue
        sense = _dot(cap_normal, host_normal)
        if sense < -1.0 + 1e-3:
            return depth, True         # the far side of the part: it came out
        if sense > 1.0 - 1e-3:
            through = False            # a floor looking back at the opening
    return depth, through


def _to_plane(point, origin, x_axis, y_axis):
    """A 3D point -> its (x, y) in the Joint Origin's own XY plane."""
    rel = _sub(point, origin)
    return [_dot(rel, x_axis), _dot(rel, y_axis)]


def _ccw(ring):
    """The same ring, wound COUNTER-CLOCKWISE seen from the frame's +Z.

    The winding a B-Rep loop arrives with is a kernel convention — this module
    says so twice already, which is why hole-vs-boss refuses to read it — and
    the contract's consumer needs the other thing: `check_fixture_bundle.py`
    on the clank-viser side FAILS a loop whose signed area is negative, so
    that "a consumer can trust the sign". Trusting a sign and inheriting one
    are different jobs, and this is where the first becomes true: the ring is
    measured and reversed if it has to be, so what leaves here is the
    contract's convention whichever way Fusion happened to chain the edges.

    Reversing a ring changes nothing else about it: the same vertices, the
    same polygon, the same rotational symmetry §3.3 case 2 counts.
    """
    total = 0.0
    for i, a in enumerate(ring):
        b = ring[(i + 1) % len(ring)]
        total += a[0] * b[1] - b[0] * a[1]
    return list(ring) if total >= 0.0 else list(reversed(ring))


def _flat_ring(polygon, origin, x_axis, y_axis, scale):
    """A 3D vertex chain -> the contract's 2D ring: the frame's own XY plane,
    design units, counter-clockwise."""
    return _ccw([[v * scale for v in _to_plane(p, origin, x_axis, y_axis)]
                 for p in polygon])


# ------------------------------------------------- which loop is the boundary

def _loop_is_outer(loop, label):
    """`BRepLoop.isOuter` as True, False or **None** when it cannot be read.

    None rather than a default, and that is the whole of this function. The
    read used to default to True — "when in doubt, skip it" — which on an API
    rev that does not answer silently drops the face's outer boundary, the one
    loop contract §3.3 case 2 needs. A missing answer is now a fact the caller
    handles with `_loop_area`, and it is recorded.

    `bool` is checked before `int` on purpose: `isinstance(True, int)` is True
    in Python, so the order is what keeps a real flag out of the numeric path.
    """
    value = _safe(lambda: loop.isOuter, None, [], f"{label}.isOuter")
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    return None


def _loop_area(edges, origin, x_axis, y_axis, label):
    """The area of this loop's bounding box in the frame's XY plane, or None.

    A PROXY for the enclosed area, and a sound one for the only question it is
    asked. On a planar face the outer loop contains every inner loop, so its
    bounding box contains theirs and is strictly larger; ranking by box area
    therefore ranks the same way ranking by true area would.

    It is a box and not a shoelace because a shoelace needs chained vertices,
    which only an all-straight loop has — and the loop this has to identify is
    just as often a circle (a round pocket's floor) or a stadium (a slot's).
    Every curve type answers SOMETHING here: a line its endpoints, a circle or
    arc its centre inflated by its radius, anything else its own bounding box.
    """
    lo = [None, None]
    hi = [None, None]

    def add(point, radius=0.0):
        flat = _to_plane(point, origin, x_axis, y_axis)
        for axis in (0, 1):
            low, high = flat[axis] - radius, flat[axis] + radius
            if lo[axis] is None or low < lo[axis]:
                lo[axis] = low
            if hi[axis] is None or high > hi[axis]:
                hi[axis] = high

    for i, edge in enumerate(edges):
        elabel = f"{label}.edge[{i}]"
        curve = _safe(lambda: edge.geometry, None, [], elabel)
        kind = _type_name(curve)
        if kind == "Line3D":
            for attr in ("startPoint", "endPoint"):
                point = _as_xyz(_safe(lambda a=attr: getattr(curve, a), None,
                                      [], f"{elabel}.{attr}"))
                if point is not None:
                    add(point)
            continue
        if kind in ("Circle3D", "Arc3D", "Ellipse3D", "EllipticalArc3D"):
            centre = _as_xyz(_safe(lambda: curve.center, None, [],
                                   f"{elabel}.center"))
            radius = _safe(lambda: float(curve.radius), None, [],
                           f"{elabel}.radius")
            if radius is None or not math.isfinite(radius):
                radius = _safe(lambda: float(curve.majorRadius), None, [],
                               f"{elabel}.majorRadius")
            if centre is not None:
                add(centre, abs(radius) if radius is not None
                    and math.isfinite(radius) else 0.0)
            continue
        # A spline, a helix, anything this module has never met: its own
        # bounding box is still a box, and a box is all this needs.
        box = _safe(lambda: edge.boundingBox, None, [], f"{elabel}.boundingBox")
        for attr in ("minPoint", "maxPoint"):
            point = _as_xyz(_safe(lambda a=attr: getattr(box, a), None, [],
                                  f"{elabel}.bbox.{attr}"))
            if point is not None:
                add(point)

    if any(v is None for v in lo + hi):
        return None
    area = (hi[0] - lo[0]) * (hi[1] - lo[1])
    return area if math.isfinite(area) else None


def _pick_outer(flags, areas, w, label):
    """(index of the face's outer loop or None, which rule said so).

    Three answers, and the caller treats them differently, which is why the
    rule comes back beside the index:

      * `"BRepLoop.isOuter"` with an index — the API answered plainly.
      * `"BRepLoop.isOuter"` with None — the API answered that this face has
        NO outer loop. Believed: every loop is then an inner one.
      * `"largest_enclosed_area"` — the flag was missing or marked several
        loops, so the measurement decided. Warned about, because a build where
        this fires on every face is a build whose `isOuter` needs looking at.
      * `"none"` — neither could answer. The caller harvests NOTHING from the
        face rather than risk recording its silhouette as a pocket.
    """
    if flags and all(flag is not None for flag in flags):
        marked = [i for i, flag in enumerate(flags) if flag]
        if len(marked) == 1:
            return marked[0], "BRepLoop.isOuter"
        if not marked:
            return None, "BRepLoop.isOuter"
        w.append(f"{label}: BRepLoop.isOuter marks {len(marked)} of "
                 f"{len(flags)} loops as outer, and a face has one — the loop "
                 "with the largest enclosed area was taken as the boundary")
    elif flags:
        w.append(f"{label}: BRepLoop.isOuter is not readable on this build — "
                 "the loop with the largest enclosed area was taken as the "
                 "face's outer boundary (contract §2's `where: \"outer\"`)")
    sized = [(area, i) for i, area in enumerate(areas) if area is not None]
    if sized:
        return max(sized, key=lambda pair: pair[0])[1], "largest_enclosed_area"
    return None, "none"


def _harvest_face(face, origin, x_axis, y_axis, host_normal, scale, w, label):
    """(features, loops, loops_source) off one planar host face. Never raises.

    Inner loops become `features` (circles) and `where: "inner"` polygons; the
    face's OWN outer loop becomes a `where: "outer"` polygon when its edges are
    all straight — contract §2 as amended on 2026-09-20. That outer boundary is
    the profile of a peg end or a pocket floor, and it is what §3.3 case 2 takes
    a child's rotational stops from; before the amendment it was thrown away as
    "the part's silhouette", which was true of a face you mate ON and false of
    a face you mate INTO.

    A circular outer boundary records nothing: it is not a hole in this face,
    and a round profile has no rotational stops to lose. Loops that are neither
    a circle nor an all-straight polygon are skipped in silence, which is the
    contract's "omit others" — a filleted pocket has no honest 2D record and an
    approximate one would be mated against.

    `loops_source` says which rule decided WHICH loop was the boundary, so a
    first real run in Fusion reads it back instead of trusting it.
    """
    features, loops = [], []
    # A point on the HOST PLANE, for the side-of-the-face measurement that
    # decides hole vs boss. `Plane.origin` is on the plane by definition; the
    # Joint Origin's own origin is the fallback and is only as good as the
    # absence of an `offsetZ` (which this fixture set never sets, but a user
    # design may).
    plane_point = _as_xyz(_safe(lambda: face.geometry.origin, None, [],
                                f"{label}.geometry.origin"))
    if plane_point is None:
        plane_point = _as_xyz(_safe(lambda: face.pointOnFace, None, [],
                                    f"{label}.pointOnFace"))
    if plane_point is None:
        plane_point = list(origin)

    # ---- every loop is read ONCE, then classified, then harvested. The three
    # passes are separate because the second needs all of the first: "which of
    # these is the biggest" is not a question you can answer one loop at a time.
    raw_loops = _safe(lambda: list(face.loops), [], w, f"{label}.loops")
    if not raw_loops:
        return [], [], "none"
    edges_of = [_loop_edges(loop, w, f"{label}.loop[{i}]")
                for i, loop in enumerate(raw_loops)]
    flags = [_loop_is_outer(loop, f"{label}.loop[{i}]")
             for i, loop in enumerate(raw_loops)]
    areas = [_loop_area(edges, origin, x_axis, y_axis, f"{label}.loop[{i}]")
             if edges else None for i, edges in enumerate(edges_of)]
    outer_index, loops_source = _pick_outer(flags, areas, w, label)
    if outer_index is None and loops_source == "none":
        w.append(f"{label}: no loop of this face could be identified as its "
                 "outer boundary — neither BRepLoop.isOuter nor any loop's "
                 "enclosed area was readable — so nothing was harvested from "
                 "it rather than risk recording the silhouette as a pocket")
        return [], [], loops_source

    disagreed = 0
    for i, loop in enumerate(raw_loops):
        llabel = f"{label}.loop[{i}]"
        edges = edges_of[i]
        if not edges:
            continue
        if i == outer_index:
            # The face's own boundary: the pocket's profile seen from its
            # floor, the peg's profile seen from its end face. Only the
            # all-straight spelling is recorded — see this function's docstring
            # for why a circular one records nothing at all.
            polygon = _polygon_of(edges, w, llabel)
            if polygon is not None:
                loops.append({
                    "kind": "polygon",
                    "where": "outer",
                    "vertices": _flat_ring(polygon, origin, x_axis, y_axis,
                                           scale),
                })
            continue

        circle = _circle_of(edges, w, llabel)
        if circle is not None:
            centre, radius = circle
            kind, depth, through, evidence = None, None, None, None
            for cyl in _other_faces(edges, (face,), w, llabel):
                found = _cylinder_kind(cyl, plane_point, host_normal, w,
                                       f"{llabel}.cyl")
                if found is None:
                    continue
                kind, evidence = found[0], found[4]
                if evidence.get("agrees") is False:
                    disagreed += 1
                depth, through = _depth_and_through(
                    cyl, face, host_normal, kind, w, f"{llabel}.cyl")
                break
            if kind is None:
                # A circular inner loop with no readable cylinder beside it is
                # still an interface feature — it is just one whose sense we
                # could not measure. `hole` is the honest default (a circle in
                # a face is a hole far more often than it is the foot of a
                # boss) and the warning says the sense was assumed.
                kind = "hole"
                w.append(f"{llabel}: circular loop with no readable adjoining "
                         "cylinder — recorded as a hole; if it is a boss, the "
                         "sidecar's `features` list can exclude it")
            feature = {
                "kind": kind,
                "center": [v * scale for v in
                           _to_plane(centre, origin, x_axis, y_axis)],
                "diameter": 2.0 * radius * scale,
                "depth": depth * scale if depth is not None else None,
                "through": through,
            }
            if evidence is not None:
                # Not a contract field: `bam_cad_core.frames` copies the five
                # keys above into frames.json and drops this. It rides in the
                # PAYLOAD, where the run that is meant to settle the evaluator
                # convention can read it.
                feature["kind_evidence"] = evidence
            features.append(feature)
            continue

        polygon = _polygon_of(edges, w, llabel)
        if polygon is not None:
            loops.append({
                "kind": "polygon",
                "where": "inner",
                "vertices": _flat_ring(polygon, origin, x_axis, y_axis, scale),
            })
    if disagreed:
        # ONE line per host face, not one per hole: on a board with seven
        # holes this is evidence, and seven copies of it is noise.
        w.append(f"{label}: on {disagreed} of {len(features)} circular "
                 "feature(s) the face evaluator's normal disagreed with which "
                 "side of this face the cylinder is on. The side decided the "
                 "kind; see `kind_evidence` — a run where this is EVERY hole "
                 "is the evidence that BRepFace.evaluator already returns the "
                 "face-outward normal and needs no isParamReversed correction")
    return features, loops, loops_source


# ------------------------------------------------------ resolving the host face

def _geometry_type(geo):
    """`JointGeometry.geometryType` as its enum NAME when this build can say
    it, else the raw value, else None.

    Recorded, never branched on: the enum's members are not something this
    module wants to spell (they differ between API revisions), and the name is
    only there so a person reading `result.json` does not have to look up what
    `3` meant. The reverse lookup walks `adsk.fusion.JointGeometryTypes` and
    takes a name only when exactly one member carries the value.
    """
    if geo is None:
        return None
    value = _safe(lambda: geo.geometryType, None, [], "geometry.geometryType")
    if value is None or isinstance(value, bool):
        return None
    enum = getattr(adsk.fusion, "JointGeometryTypes", None)
    if enum is not None:
        names = []
        for attr in _safe(lambda: dir(enum), [], [], "JointGeometryTypes"):
            if attr.startswith("_"):
                continue
            member = _safe(lambda a=attr: getattr(enum, a), None, [], attr)
            if isinstance(member, int) and not isinstance(member, bool) \
                    and member == value:
                names.append(attr)
        if len(names) == 1:
            return names[0]
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)


def _plane_contains(face, point, normal):
    """Does `point` lie in this planar face's PLANE? True / False / None.

    The plane, not the face: a hole's centre is in the plane of the face the
    hole is cut into and is not on that face (it is in the hole), and that is
    exactly the point a circular-edge Joint Origin sits at. Whether the point
    is inside the face's boundary is `_point_on_face`, asked only by the scan.
    """
    anchor = _as_xyz(_safe(lambda: face.geometry.origin, None, [],
                           "plane.origin"))
    if anchor is None:
        anchor = _as_xyz(_safe(lambda: face.pointOnFace, None, [],
                               "face.pointOnFace"))
    if anchor is None:
        return None
    return abs(_dot(_sub(point, anchor), normal)) <= POINT_TOL


def _point_on_face(face, point, label):
    """Is `point` inside this face's own boundary? True / False / None.

    `BRepFace.evaluator.getParameterAtPoint` projects the point into the
    surface's (u, v) and `isParameterOnFace` asks whether that parameter is
    inside the face's trimming loops — the kernel's answer to "is it ON the
    face or merely in its plane", which no arithmetic here can give for a face
    with holes in it. None means the face could not answer; the caller counts
    those and says so rather than guessing either way.
    """
    evaluator = _safe(lambda: face.evaluator, None, [], f"{label}.evaluator")
    if evaluator is None:
        return None
    probe = _safe(lambda: adsk.core.Point3D.create(point[0], point[1],
                                                   point[2]),
                  None, [], f"{label}.Point3D")
    if probe is None:
        return None

    def _ask():
        ok, param = evaluator.getParameterAtPoint(probe)
        if not ok or param is None:
            return None
        return bool(evaluator.isParameterOnFace(param))

    return _safe(_ask, None, [], f"{label}.isParameterOnFace")


def _point_in_hole_of(face, origin, label):
    """True when `origin` is inside one of this face's CIRCULAR inner loops.

    The round-2 ruling (§5 of `docs/adversarial/round2.md`). A designer who
    wants a connector at a hole's centre has three ways to say so, and Fusion
    gives each a different `entityOne`: click the circular edge (a `BRepEdge`,
    resolved through `edge.faces`), click the hole wall (a cylindrical
    `BRepFace`, resolved through its adjoining planar face), or drop a sketch
    or construction point there (resolved by the scan). The first two land in
    the hole's void too — which is why the adjacency routes are asked with
    `need_on_face=False` — and only the third was then refused, because
    `isParameterOnFace` is False in a void. Same point, same intent, two
    verdicts, decided by which tool was used.

    So the void of a hole counts as the face, for circles only. A non-circular
    inner loop is a pocket MOUTH, and §7 already ruled that a mouth carries no
    frame: `_circle_of` returns None for it and the point stays unresolved.
    """
    loops = _safe(lambda: list(face.loops), [], [], f"{label}.loops")
    for i, loop in enumerate(loops):
        if _loop_is_outer(loop, f"{label}.loop[{i}]") is True:
            continue
        edges = _loop_edges(loop, [], f"{label}.loop[{i}]")
        if not edges:
            continue
        circle = _circle_of(edges, [], f"{label}.loop[{i}]")
        if circle is None:
            continue
        centre, radius = circle
        if _norm(_sub(origin, centre)) < radius:
            return True
    return False


def _planar_candidates(faces, origin, z_axis, need_on_face, label):
    """([(face, outward normal)], n_unanswered) — the faces that could host.

    A candidate is PLANAR, has its normal along the frame's ±Z (the contract's
    "z = 0 is the host face" is false of any other face, and a harvest
    projected into the wrong plane is a pattern that mates wrongly), and has
    the Joint Origin's own origin in its plane. `need_on_face` adds the fourth
    question the scan needs and the adjacency routes must NOT ask (a hole's
    centre is in the hole, not on the face) — and a point in the void of a
    CIRCULAR inner loop passes it anyway, by the round-2 ruling.

    Each entry carries how it qualified, because that is what the record's
    `host.resolved_by` reports.
    """
    out, unanswered = [], 0
    for i, face in enumerate(faces):
        flabel = f"{label}.candidate[{i}]"
        normal = _plane_normal(face, [], flabel)
        if normal is None:
            continue
        if _norm(_cross(normal, z_axis)) > DIR_TOL:
            continue
        if not _plane_contains(face, origin, normal):
            continue
        how = "adjacent"
        if need_on_face:
            on = _point_on_face(face, origin, flabel)
            if on is None:
                unanswered += 1
                continue
            if on:
                how = "point_on_face"
            elif _point_in_hole_of(face, origin, flabel):
                how = "point_in_hole"
            else:
                continue
        out.append((face, normal, how))
    return out, unanswered


def _faces_of_bodies(bodies, label):
    out = []
    for i, body in enumerate(bodies):
        out.extend(_safe(lambda b=body: list(b.faces), [], [],
                         f"{label}.body[{i}].faces"))
    return out


def _resolve_host_face(host, entity_type, origin, z_axis, comp, w, label):
    """(face, outward normal, resolved_by) for an `entityOne` that is not a
    planar BRepFace — or None, with a warning saying how many faces qualified.

    The route follows what the designer clicked (see the module docstring's
    table). A route that finds NO face falls through to the scan, over the
    entity's own body when it has one and over the component's bodies when it
    does not (a sketch point has no body). A route that finds SEVERAL stops
    there: two faces with an equal claim is a question for the designer, and
    picking one would harvest a pattern from a face nobody meant.

    The frame is never touched. This only decides which face's loops are
    harvested around it.
    """
    if origin is None or z_axis is None:
        return None
    what = entity_type or "nothing readable"
    route, adjacent = None, []
    if entity_type == "BRepFace":          # a face, but not a planar one
        route = "adjacent_planar"
        edges = _safe(lambda: list(host.edges), [], [], f"{label}.edges")
        adjacent = _other_faces(edges, (host,), [], label)
    elif entity_type == "BRepEdge":
        route = "edge.faces"
        adjacent = _safe(lambda: list(host.faces), [], [], f"{label}.faces")
    elif entity_type == "BRepVertex":
        route = "vertex.faces"
        adjacent = _safe(lambda: list(host.faces), [], [], f"{label}.faces")
    if route is not None:
        found, _ = _planar_candidates(adjacent, origin, z_axis, False, label)
        if len(found) == 1:
            return found[0][0], found[0][1], route
        if len(found) > 1:
            w.append(f"{label}: the Joint Origin sits on a {what}, and "
                     f"{len(found)} planar faces beside it (via {route}) have "
                     "its origin in their plane with a normal along its Z — "
                     "ambiguous, so none was chosen: host kind stays 'other' "
                     "and nothing is harvested")
            return None

    body = _safe(lambda: host.body, None, [], f"{label}.body") \
        if host is not None else None
    if body is not None:
        bodies, scope = [body], "its own body"
    else:
        bodies = (_safe(lambda: list(comp.bRepBodies), [], [],
                        f"{label}.component.bRepBodies")
                  if comp is not None else [])
        comp_name = _safe(lambda: comp.name, "", [], f"{label}.component") \
            if comp is not None else ""
        scope = (f"component '{comp_name}'" if comp_name
                 else "the component") + f" ({len(bodies)} body/bodies)"
    found, unanswered = _planar_candidates(
        _faces_of_bodies(bodies, label), origin, z_axis, True, label)
    if len(found) == 1:
        return found[0][0], found[0][1], found[0][2]
    tail = (f"; {unanswered} coplanar face(s) could not say whether the point "
            "is on them (evaluator.isParameterOnFace)" if unanswered else "")
    if found:
        w.append(f"{label}: the Joint Origin sits on a {what}, not on a planar "
                 f"face, and {len(found)} planar faces of {scope} have its "
                 "origin ON them with a normal along its Z — ambiguous, so "
                 "none was chosen: host kind stays 'other' and nothing is "
                 f"harvested{tail}")
    else:
        w.append(f"{label}: the Joint Origin sits on a {what}, not on a planar "
                 f"face, and 0 planar faces of {scope} have its origin on them "
                 "with a normal along its Z — host kind stays 'other', so it "
                 f"exports with no features and no loops{tail}")
    return None


# ---------------------------------------------------------------- the record

def _joint_origin_record(jo, comp_name, root, comp, length_scale, warnings):
    """One JointOrigin, as dumb numbers in its component definition's frame.

    Lengths are scaled internal-cm -> design units exactly like every other v2
    length in the payload. Axes are unit vectors and are NOT scaled. The 2D
    feature coordinates are design units too, so one number means one thing
    everywhere in this record.
    """
    w: list = []
    name = _safe(lambda: jo.name, "joint origin", w, "jointOrigin.name")
    rec = {
        "name": name,
        "entity_token": _safe(lambda: jo.entityToken, None, w,
                              f"{name}.entityToken"),
        "component": comp_name,
        "is_light_bulb_on": bool(_safe(lambda: jo.isLightBulbOn, True, [],
                                       f"{name}.isLightBulbOn")),
        # the frame, in the DEFINING COMPONENT's coordinates (decision #2:
        # the record is per definition, the world pose is per instance and is
        # composed offline in bam_cad_core.frames)
        "origin": None,
        "x_axis": None,
        "y_axis": None,
        "z_axis": None,
        "frame_source": "none",
        "axes_source": "none",
        # the runner-up reads, kept so the first real Fusion run can CHECK the
        # choice above rather than trust it — see the module docstring
        "origin_geometry": None,
        "offset": None,
        "angle_rad": None,
        "is_flipped": None,
        "host": {"kind": "none", "normal": None},
        "features": [],
        "loops": [],
        # which rule decided WHICH loop was the face's outer boundary —
        # "BRepLoop.isOuter", "largest_enclosed_area" or "none". Recorded in
        # the same style as frame_source/axes_source, and for the same reason:
        # the first real Fusion run reads it back rather than assuming it.
        "loops_source": "none",
        "host_path": None,
        "warnings": w,
    }

    origin, x_axis, y_axis, z_axis, frame_source, axes_source = _read_frame(
        jo, w, name)
    rec["frame_source"], rec["axes_source"] = frame_source, axes_source
    if origin is not None:
        rec["origin"] = [v * length_scale for v in origin]
        rec["x_axis"], rec["y_axis"], rec["z_axis"] = x_axis, y_axis, z_axis

    rec["is_flipped"] = _safe(lambda: bool(jo.isFlipped), None, [],
                              f"{name}.isFlipped")
    offsets = [_parameter_value(_safe(lambda a=attr: getattr(jo, a), None, [],
                                      f"{name}.{attr}"))
               for attr in ("offsetX", "offsetY", "offsetZ")]
    if any(v is not None for v in offsets):
        rec["offset"] = [(v * length_scale) if v is not None else None
                         for v in offsets]
    rec["angle_rad"] = _parameter_value(
        _safe(lambda: jo.angle, None, [], f"{name}.angle"))

    geo = _safe(lambda: jo.geometry, None, [], f"{name}.geometry")
    if geo is not None:
        raw = _as_xyz(_safe(lambda: geo.origin, None, [],
                            f"{name}.geometry.origin"))
        if raw is not None:
            rec["origin_geometry"] = [v * length_scale for v in raw]

    # ---- the host. entityOne is what the origin was placed on. Only a planar
    # face carries an interface we can describe in 2D, so anything else is
    # RESOLVED to the planar face the origin sits on (`_resolve_host_face`,
    # and the module docstring's table), and the route is recorded. What
    # entityOne really was is recorded whatever happens next: it is the one
    # fact round 1 found missing when two of three origins came out "other".
    host = _safe(lambda: geo.entityOne, None, [],
                 f"{name}.geometry.entityOne") if geo is not None else None
    entity_type = _type_name(host) if host is not None else None
    rec["host"]["entity_type"] = entity_type or None
    rec["host"]["geometry_type"] = _geometry_type(geo)
    rec["host"]["resolved_by"] = None

    face, normal, resolved_by = None, None, None
    if entity_type == "BRepFace":
        normal = _plane_normal(host, w, f"{name}.host")
        if normal is not None:
            face, resolved_by = host, "entityOne"
    if face is None and origin is not None:
        scope = comp if comp is not None else _safe(
            lambda: jo.parentComponent, None, [], f"{name}.parentComponent")
        found = _resolve_host_face(host, entity_type, origin, z_axis, scope,
                                   w, f"{name}.host")
        if found is not None:
            face, normal, resolved_by = found

    if face is None:
        rec["host"]["kind"] = "none" if host is None else "other"
    else:
        rec["host"]["kind"] = "planar_face"
        rec["host"]["normal"] = normal
        rec["host"]["resolved_by"] = resolved_by
        rec["host"]["entity_token"] = _safe(
            lambda: face.entityToken, None, [], f"{name}.host.entityToken")
        area = _safe(lambda: float(face.area), None, [], f"{name}.host.area")
        if area is not None and math.isfinite(area):
            rec["host"]["area"] = area * length_scale ** 2
        # The same harvest as a face the designer clicked, on the face the
        # origin was found to sit on. The frame is the Joint Origin's own and
        # is never moved: a hole-centre origin keeps its hole at [0, 0].
        if origin is not None:
            (rec["features"], rec["loops"],
             rec["loops_source"]) = _harvest_face(
                face, origin, x_axis, y_axis, normal, length_scale, w,
                f"{name}.host")

    # Which body, and which occurrence, own the host — the RESOLVED face's
    # when there is one, else the entity's own (an edge, a vertex or a curved
    # face that resolved to nothing still belongs to a body, and a frame on it
    # still belongs to that body's link). `body_name` is how the runner's
    # per-body split attributes the frame; `host_path` is the exact instance,
    # read the way `_decal_record` reads `faces[0].body.assemblyContext`.
    owner_of = face if face is not None else host
    if owner_of is not None:
        body_name = _safe(lambda: owner_of.body.name, None, [],
                          f"{name}.host.body.name")
        if isinstance(body_name, str) and body_name:
            rec["host"]["body_name"] = body_name
        owner = _safe(lambda: owner_of.body.assemblyContext.fullPathName, None,
                      [], f"{name}.host.assemblyContext")
        if owner:
            rec["host_path"] = owner

    # The world pose of the FIRST instance, read exactly the way
    # `_joint_record` and `_decal_record` read a sub-assembly's geometry. It is
    # a cross-check, not the value used: the composition in bam_cad_core has to
    # work for every instance and this only ever describes one.
    if comp_name and root is not None and comp is not None:
        def _proxy():
            occs = root.allOccurrencesByComponent(comp)
            if occs.count == 0:
                return None
            return jo.createForAssemblyContext(occs.item(0))

        proxy = _safe(_proxy, None, [], f"{name}.assemblyContext")
        if proxy is not None:
            pframe = _safe(lambda: _matrix_frame(proxy.transform), None, [],
                           f"{name}.proxy.transform")
            if pframe is not None:
                porigin, px, py, pz = pframe
                rec["origin_world"] = [v * length_scale for v in porigin]
                rec["x_axis_world"], rec["y_axis_world"] = px, py
                rec["z_axis_world"] = pz
    return rec


def collect_joint_origins(design, root, length_scale, warnings) -> list:
    """Every Joint Origin in the design, once per component DEFINITION.

    Per definition and not per occurrence for the reason joints and decals
    already are: a component placed twice carries the same named frame twice,
    and the expansion belongs in `bam_cad_core.frames` where it can be tested
    without Fusion.

    Returns a list, always — the whole body is guarded, so a Fusion build whose
    `jointOrigins` collection behaves differently costs one warning line and
    an empty list rather than the export.
    """
    out: list = []
    root_name = _safe(lambda: root.name, "", warnings, "root.name")
    for comp in _safe(lambda: list(design.allComponents), [], warnings,
                      "allComponents(jointOrigins)"):
        comp_name = _safe(lambda: comp.name, "", warnings, "component.name")
        origins = _safe(lambda: list(comp.jointOrigins), [], warnings,
                        f"{comp_name}.jointOrigins")
        for jo in origins:
            try:
                out.append(_joint_origin_record(
                    jo, "" if comp_name == root_name else comp_name,
                    root, comp, length_scale, warnings))
            except Exception:
                # The belt to _safe's braces. A joint origin is decoration on
                # an export that is otherwise complete; losing one to a read
                # nobody anticipated must cost a line, never the URDF.
                warnings.append(
                    f"{comp_name}.jointOrigins: one joint origin could not be "
                    f"read at all — skipped "
                    f"({traceback.format_exc(limit=1).splitlines()[-1]})")
    return out
