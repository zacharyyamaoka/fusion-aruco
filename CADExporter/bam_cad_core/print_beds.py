"""3D-print beds for exported ArUco tag parts: lay every part flat, pack the
copies onto as few beds as possible, and write ONE PrusaSlicer project with
every bed in it and the MMU colours already assigned.

Input is the exporter's `aruco/print3d/parts.json` (schema
bam_aruco_print3d/1): per part the component name, how many visible
`copies` to print, its `bodies` (a white "tile" and a black "cells" STL, in
the part's OWN component frame) and its `tags` (each a frame: origin = the
pattern's centre on the face, z = the outward face normal, x / y = image
right / up, plus the TagSpec with depth_m / raise_m).

### Units are measured, not trusted

STLs carry no unit and Fusion writes them in the document's unit. The cells
body spans exactly the black pattern (`marker_length_m` square for a single
marker, aruco_board.extent_m for a board), so its extent along the tag's x /
y axes says which unit the mesh is in; the black layer's span along the face
normal ([-depth, +raise] from the origin) says the same for the frame. A
disagreement with the manifest's `stl_units` / `units` is a warning and the
measured unit wins.

### Orientation (`choose_orientation`, the reason is recorded per part)

`orientation="flat"` (default) lays each part flat by its tag:

1. FACE DOWN when the black is inlaid flush (raise_m == 0, recess_m == 0),
   nothing of the part stands proud of the tag face plane and the face
   carries at least FLAT_BACK_MIN_FRACTION of the footprint (so the rest
   does not overhang): the pattern prints on the bed sheet -- flat, crisp,
   and the colour changes all happen in the first `depth` mm of layers. An
   ArUco Extrude tag with locating pins (they hang from the back) lands
   here: pattern down, pins up.
2. FACE UP when the black is raised (raise_m > 0), recessed into an open
   pocket (recess_m > 0) or something else stands proud of the face,
   provided the back is flat (the plane opposite the face carries at least
   FLAT_BACK_MIN_FRACTION of the footprint). Pins on the back make it
   not flat.
3. FALLBACK otherwise: the most stable flat orientation -- the supporting
   plane with the largest contact area (the mesh's coplanar face groups and
   the tag frame's six axes are the candidates), ties to the lower part --
   with a warning.

Then the part turns about Z so the tag's x axis runs along bed +X, and drops
to z = 0, centred on the origin. Nothing is ever scaled.

`orientation="cad"` keeps every part exactly as modelled: no rotation at
all (not even the 90 degree turn when packing), only the drop to z = 0. A
part that does not stand on a flat bottom that way is still placed, with a
warning that it may need supports.

### Packing (`pack_rectangles`)

Oriented footprints (axis-aligned boxes) x copies go onto beds with
`margin_mm` kept clear at every edge and at least `spacing_mm` between any
two parts. Every bed also keeps room for the MMU wipe tower
(`wipe_tower_mm`, brim included): a part may only go where a tower-sized
hole remains, and the tower is then put in the back-most, right-most hole.
MaxRects, 90 degree turns allowed, first bed that fits; several sort orders
x placement rules are tried and the fewest beds win (then the most area on
the earliest beds, then the most compact clusters). Deterministic and
independent of input order. A part whose footprint cannot go on an empty
bed is REJECTED with the reason. Each bed's cluster is centred on the bed.

### The PrusaSlicer project (verified against PrusaSlicer 2.9.6's source)

`aruco_tags.3mf` holds every bed. PrusaSlicer 2.9 keeps no per-object bed
index: an instance belongs to the bed whose translated build volume holds
it, beds sitting on a spiral grid (MultipleBeds.cpp: bed k at grid cell
index2grid_coords(k), stride = bed size + gap, gap = min(100 mm,
0.3 x the bed's diagonal)), at most 9 beds per project (more go to
aruco_tags_2.3mf ...). Two loader rules shape the file:

* a 3MF WITHOUT `Metadata/Slic3r_PE.config` is not a "project": the loader
  re-centres all instances on bed 0, which would scramble the beds. So the
  project carries a config;
* a config is laid over Slic3r's built-in DEFAULTS before it is compared
  with the named presets, so a partial config would replace the user's
  printer settings with defaults. So the config is the COMPLETE preset
  chain (printer + print + filament), resolved from the PrusaSlicer vendor
  bundle installed on this machine (never shipped with this code), plus
  the preset names and the white / black colours.

Each placed COPY is its own object, named "<part> (2/6)", so each can be
turned on its own in PrusaSlicer (`instances=True` makes copies on a bed
instances of one object instead). An object's bodies are its volumes
(`Metadata/Slic3r_PE_model.config`: firstid / lastid = the INCLUSIVE triangle
range of the volume in the object's single mesh) -- the black cells on
extruder 2, everything else on extruder 1 -- so no painting is needed.
`Metadata/Prusa_Slicer_wipe_tower_information.xml` puts each bed's tower in
the hole kept for it (bed-local coordinates). The 3MF's Application is not
"PrusaSlicer-<2.9", which would trigger the legacy linear-bed rearrange.
Open it AS A PROJECT (File > Open Project, double-click, or drop and pick
"Open as project"); "Import geometry only" drops the config, and then
PrusaSlicer re-centres every bed's parts onto one bed.

### Output (`render_beds` -> {relpath: bytes}; `write_beds` writes them)

    aruco_tags.3mf        the project: all beds, MMU colours applied
    beds/bed_01.3mf ...   one project per bed (same objects, bed-local)
    beds/bed_01.svg ...   top view, bed coordinates, part names
    beds/layout.json      what went where and why (schema bam_aruco_print_beds/1)
    parts/<part>__tile.stl / __cells.stl   each part alone, oriented for
                          printing (z >= 0, centred on the origin)
    parts/<part>.3mf      the same part as one two-volume object, no config
                          (imports into whatever is open)

`write_selection_project` is the "print it integral with the rest" path:
any Fusion bodies (STLs in the shared assembly frame), one object per
selected occurrence, kept as modelled by default, packed onto as many beds
as needed and written as the same kind of project.

`write_part_stls` is ArUco Export's "STL files" type: the oriented
parts/ STLs alone, in a folder of their own -- no beds, no 3MF.

All numbers are millimetres in PrusaSlicer's bed frame: origin at the
bed's front-left corner, +x right, +y to the back, +z up. Stdlib only (this
also runs inside Fusion); never imports adsk.
"""
from __future__ import annotations

import io
import json
import math
import os
import re
import shutil
import struct
import zipfile
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

SCHEMA = "bam_aruco_print_beds/1"
MANIFEST_SCHEMA = "bam_aruco_print3d/1"
MK4_BED_MM = (250.0, 210.0)            # Original Prusa MK4 / MK4S (+ MMU3)
CORE_ONE_BED_MM = (250.0, 220.0)       # Prusa CORE One
PROJECT_FILE = "aruco_tags.3mf"
PLANE_TOL_MM = 0.02                    # "on the plane" / "stands proud" threshold
FLAT_BACK_MIN_FRACTION = 0.5           # flat back: contact >= this x footprint box
UNIT_MATCH_TOL = 0.03                  # measured extent vs spec, relative
FRAME_MATCH_TOL_MM = 0.05
# The MMU wipe tower, brim included: wipe_tower_width 60 + 2 x 2 mm brim; a
# two-colour tag bed measured 63.7 x 15.2 mm in PrusaSlicer 2.9.6
# (tools/verify_print_beds.py checks the sliced tower stays inside).
DEFAULT_WIPE_TOWER_MM: Tuple[float, float] = (64.0, 20.0)
WIPE_TOWER_BRIM_MM = 2.0
MAX_BEDS_PER_PROJECT = 9               # PrusaSlicer 2.9 MAX_NUMBER_OF_BEDS
DEFAULT_PRINTER = "Original Prusa MK4 MMU3 0.4 nozzle"
DEFAULT_PRINT = "0.20mm STRUCTURAL @MK4IS 0.4"
DEFAULT_FILAMENT = "Generic PLA @PGIS"
TILE_COLOUR = "#FFFFFF"
CELLS_COLOUR = "#000000"

UNIT_MM: Dict[str, float] = {"mm": 1.0, "millimeter": 1.0, "millimetre": 1.0,
                             "cm": 10.0, "centimeter": 10.0, "centimetre": 10.0,
                             "m": 1000.0, "meter": 1000.0, "metre": 1000.0,
                             "in": 25.4, "inch": 25.4}
_UNIT_ORDER = ("mm", "cm", "m", "in")
_EPS = 1e-9

Vec3 = Tuple[float, float, float]
Tri = Tuple[int, int, int]
Rect = Tuple[float, float, float, float]


# ------------------------------------------------------------------ vectors

def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _scale(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def _norm(a):
    return math.sqrt(_dot(a, a))


def _unit(a):
    n = _norm(a)
    if n < 1e-12:
        raise ValueError("zero-length direction")
    return (a[0] / n, a[1] / n, a[2] / n)


# ------------------------------------------------------------------ STL

class Mesh(NamedTuple):
    """Indexed triangle mesh. Vertices are deduplicated by exact coordinate,
    so an STL's triangle soup comes back connected."""
    vertices: List[Vec3]
    triangles: List[Tri]


def mesh_from_soup(points) -> Mesh:
    """Every three points a triangle; triangles that collapse to a repeated
    vertex are dropped."""
    index: Dict[Vec3, int] = {}
    verts: List[Vec3] = []
    tris: List[Tri] = []
    tri: List[int] = []
    for p in points:
        key = (float(p[0]) + 0.0, float(p[1]) + 0.0, float(p[2]) + 0.0)
        i = index.get(key)
        if i is None:
            i = index[key] = len(verts)
            verts.append(key)
        tri.append(i)
        if len(tri) == 3:
            if tri[0] != tri[1] and tri[1] != tri[2] and tri[0] != tri[2]:
                tris.append((tri[0], tri[1], tri[2]))
            tri = []
    return Mesh(verts, tris)


def _looks_ascii(data: bytes) -> bool:
    head = data[:512].lstrip()
    return head[:5].lower() == b"solid" and b"facet" in data[:4096]


def read_stl(data: bytes) -> Mesh:
    """Binary or ASCII STL -> Mesh. A binary file whose 80-byte header
    happens to start with "solid" is still read as binary (its length
    gives it away)."""
    data = bytes(data)
    if len(data) >= 84:
        n = struct.unpack_from("<I", data, 80)[0]
        exact = len(data) == 84 + 50 * n
        if exact or (not _looks_ascii(data) and len(data) >= 84 + 50 * n):
            pts = []
            for rec in struct.iter_unpack("<12fH", data[84:84 + 50 * n]):
                pts.append(rec[3:6])
                pts.append(rec[6:9])
                pts.append(rec[9:12])
            return mesh_from_soup(pts)
    if _looks_ascii(data):
        nums = re.findall(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)", data)
        if len(nums) % 3:
            raise ValueError("ASCII STL with %d vertices (not a multiple of 3)" % len(nums))
        return mesh_from_soup((float(a), float(b), float(c)) for a, b, c in nums)
    raise ValueError("not an STL (%d bytes, neither a binary length match nor "
                     "an ASCII 'solid ... facet' file)" % len(data))


def write_stl(mesh: Mesh, name: str = "") -> bytes:
    """Binary STL bytes. The header never starts with "solid"."""
    header = ("bam_cad_core.print_beds %s" % name).encode("ascii", "replace")[:80]
    out = bytearray(header.ljust(80, b" "))
    out += struct.pack("<I", len(mesh.triangles))
    v = mesh.vertices
    for a, b, c in mesh.triangles:
        pa, pb, pc = v[a], v[b], v[c]
        n = _cross(_sub(pb, pa), _sub(pc, pa))
        ln = _norm(n)
        n = (0.0, 0.0, 0.0) if ln < 1e-20 else _scale(n, 1.0 / ln)
        out += struct.pack("<12fH", n[0], n[1], n[2], pa[0], pa[1], pa[2],
                           pb[0], pb[1], pb[2], pc[0], pc[1], pc[2], 0)
    return bytes(out)


def transform_mesh(mesh: Mesh, rows, t=(0.0, 0.0, 0.0)) -> Mesh:
    """v -> R v + t with R given as its three rows."""
    r0, r1, r2 = rows
    return Mesh([(_dot(r0, p) + t[0], _dot(r1, p) + t[1], _dot(r2, p) + t[2])
                 for p in mesh.vertices], list(mesh.triangles))


def scale_mesh(mesh: Mesh, k: float) -> Mesh:
    if k == 1.0:
        return mesh
    return Mesh([_scale(p, k) for p in mesh.vertices], list(mesh.triangles))


def bbox(meshes) -> Tuple[Vec3, Vec3]:
    pts = [p for m in meshes for p in m.vertices]
    if not pts:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    return (tuple(min(p[k] for p in pts) for k in range(3)),
            tuple(max(p[k] for p in pts) for k in range(3)))


def _tri_normal_area(mesh: Mesh, tri: Tri):
    a, b, c = (mesh.vertices[i] for i in tri)
    n = _cross(_sub(b, a), _sub(c, a))
    ln = _norm(n)
    if ln < 1e-20:
        return None, 0.0
    return _scale(n, 1.0 / ln), ln / 2.0


# ------------------------------------------------------------------ input

class TagFrame(NamedTuple):
    key: str
    origin: Vec3          # mm, in the part's frame
    x: Vec3               # unit vectors, part frame
    y: Vec3
    z: Vec3               # outward face normal
    depth_mm: float       # black INTO the face
    raise_mm: float       # black standing ON the face
    spec: dict
    recess_mm: float = 0.0  # the black's top BELOW the face (an open pocket)


CELLS_PREFIX = "aruco cells"       # the exporter's name for a black cells body


def body_role(role, name) -> str:
    """"cells" (black, extruder 2) for role "cells" or a body named
    "aruco cells ..."; otherwise "tile" or "other" (extruder 1)."""
    r = str(role or "").strip().lower()
    if r == "cells" or str(name).strip().lower().startswith(CELLS_PREFIX):
        return "cells"
    return r if r in ("tile", "other") else "tile"


@dataclass
class Body:
    name: str
    role: str             # "tile" | "cells" | "other"; only "cells" is black
    colour: str
    mesh: Mesh            # mm, the part's own frame
    source: str = ""


@dataclass
class Part:
    name: str
    slug: str
    copies: int
    bodies: List[Body]
    tags: List[TagFrame]
    variant: bool = False
    notes: List[str] = field(default_factory=list)


def slug(name: str) -> str:
    """The exporter's file-name slug (aruco_export._slug)."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(name)).strip("_") or "part"


def _pattern_extent_mm(spec: dict) -> Optional[Tuple[float, float]]:
    try:
        length = float(spec.get("marker_length_m", 0.0))
        layout = spec.get("layout", "single") or "single"
        if layout == "single":
            return (length * 1000.0, length * 1000.0) if length > 0 else None
        from . import aruco_board
        w, h = aruco_board.extent_m(aruco_board.BoardSpec(
            layout=layout, dictionary=spec.get("dictionary", "DICT_4X4_50"),
            first_id=int(spec.get("marker_id", 0)), marker_length_m=length,
            rows=int(spec.get("rows", 1)), cols=int(spec.get("cols", 1)),
            separation_m=float(spec.get("separation_m", 0.0)),
            square_length_m=float(spec.get("square_length_m", 0.0)),
            legacy=bool(spec.get("legacy", False))))
        return w * 1000.0, h * 1000.0
    except (TypeError, ValueError):
        return None


def _extent_along(meshes, axis) -> float:
    vals = [_dot(p, axis) for m in meshes for p in m.vertices]
    return (max(vals) - min(vals)) if vals else 0.0


def _measure_stl_scale(cells: List[Mesh], tag_axes, spec, declared: str, where: str):
    """-> (mm per STL unit, unit name, note or None) from the cells' extent."""
    want = _pattern_extent_mm(spec)
    fallback = UNIT_MM.get(str(declared).lower())
    if not cells or want is None:
        what = "no cells body" if not cells else "no usable marker size in the spec"
        if fallback is None:
            return 1.0, "mm", ("%s: %s and stl_units %r is unknown; assumed mm"
                               % (where, what, declared))
        return fallback, str(declared), ("%s: %s, so the STL unit could not be "
                                         "measured; took stl_units=%s on trust"
                                         % (where, what, declared))
    rx = _extent_along(cells, tag_axes[0])
    ry = _extent_along(cells, tag_axes[1])
    order = [declared.lower()] if declared.lower() in UNIT_MM else []
    order += [u for u in _UNIT_ORDER if u not in order]
    for u in order:
        f = UNIT_MM[u]
        if (abs(rx * f - want[0]) <= UNIT_MATCH_TOL * want[0]
                and abs(ry * f - want[1]) <= UNIT_MATCH_TOL * want[1]):
            note = None
            if fallback is None or abs(f - fallback) > 1e-9:
                note = ("%s: the manifest says stl_units=%s but the black pattern "
                        "measures %.4g x %.4g STL units for a %.2f x %.2f mm "
                        "pattern -- the STL is in %s; used that"
                        % (where, declared, rx, ry, want[0], want[1], u))
            return f, u, note
    f = fallback if fallback is not None else 1.0
    return f, str(declared), ("%s: the black pattern measures %.4g x %.4g STL units, "
                              "which matches no unit for a %.2f x %.2f mm pattern; "
                              "took stl_units=%s on trust"
                              % (where, rx, ry, want[0], want[1], declared))


def _measure_frame_scale(cells_mm: List[Mesh], origin_raw, z, depth_mm, raise_mm,
                         declared: str, where: str, recess_mm: float = 0.0):
    """-> (mm per frame unit, note or None): the black must span
    [-depth, +raise] (a recessed black: [-depth, -recess]) along the face
    normal from the origin."""
    fallback = UNIT_MM.get(str(declared).lower(), 10.0)
    if not cells_mm:
        return fallback, None
    lo_want = -depth_mm if depth_mm > 0 else 0.0
    hi_want = raise_mm if raise_mm > 0 else (-recess_mm if recess_mm > 0 else 0.0)
    order = [declared.lower()] if declared.lower() in UNIT_MM else []
    order += [u for u in ("cm", "mm", "m", "in") if u not in order]
    vals = [_dot(p, z) for m in cells_mm for p in m.vertices]
    lo, hi = min(vals), max(vals)
    for u in order:
        oz = _dot(_scale(origin_raw, UNIT_MM[u]), z)
        if (abs((lo - oz) - lo_want) <= FRAME_MATCH_TOL_MM
                and abs((hi - oz) - hi_want) <= FRAME_MATCH_TOL_MM):
            note = None
            if abs(UNIT_MM[u] - fallback) > 1e-9:
                note = ("%s: the tag frame says units=%s but the black layer only "
                        "sits on the face in %s; used that" % (where, declared, u))
            return UNIT_MM[u], note
    oz = _dot(_scale(origin_raw, fallback), z)
    return fallback, ("%s: the black layer spans %.3f..%.3f mm from the tag origin "
                      "along the face normal, not %.3f..%.3f (depth / raise); the "
                      "frame may be stale -- orientation uses it as given"
                      % (where, lo - oz, hi - oz, lo_want, hi_want))


def load_parts(manifest: dict, read: Callable[[str], bytes]):
    """manifest dict + a reader for its `stl` strings ->
    (parts, failures [(name, reason)], warnings)."""
    warnings: List[str] = []
    failures: List[Tuple[str, str]] = []
    if manifest.get("schema") != MANIFEST_SCHEMA:
        warnings.append("manifest schema is %r, expected %r; reading it anyway"
                        % (manifest.get("schema"), MANIFEST_SCHEMA))
    stl_units = str(manifest.get("stl_units", "mm"))
    frame_units = str(manifest.get("frame_units", "cm"))
    parts: List[Part] = []
    used_slugs: Dict[str, int] = {}
    for entry in manifest.get("parts", []):
        name = str(entry.get("component", "part"))
        copies = entry.get("copies", 1)
        if isinstance(copies, bool) or not isinstance(copies, int) or copies < 0:
            warnings.append("%s: copies=%r is not a count; printing 1" % (name, copies))
            copies = 1
        s = slug(name)
        if s in used_slugs:
            used_slugs[s] += 1
            s = "%s_%d" % (s, used_slugs[s])
        else:
            used_slugs[s] = 1
        notes: List[str] = []
        tags_raw = []
        for t in entry.get("tags", []) or []:
            try:
                z = _unit(tuple(float(v) for v in t["z_axis"]))
                x = tuple(float(v) for v in t["x_axis"])
                x = _unit(_sub(x, _scale(z, _dot(x, z))))
                y = _cross(z, x)
                spec = dict(t.get("spec") or {})
                tags_raw.append((str(t.get("key", "tag")), tuple(float(v) for v in t["origin"]),
                                 x, y, z, max(0.0, float(spec.get("depth_m", 0.0))) * 1000.0,
                                 max(0.0, float(spec.get("raise_m", 0.0))) * 1000.0, spec,
                                 str(t.get("units", frame_units)),
                                 max(0.0, float(spec.get("recess_m", 0.0))) * 1000.0))
            except (KeyError, TypeError, ValueError) as exc:
                notes.append("%s: tag %r has no usable frame (%s); ignored"
                             % (name, t.get("key"), exc))
        raw_bodies = []
        seen_paths: Dict[str, str] = {}
        for b in entry.get("bodies", []) or []:
            path = str(b.get("stl", ""))
            bname = str(b.get("name", "body"))
            role = body_role(b.get("role"), bname)
            if path in seen_paths:
                notes.append("%s: bodies %r and %r share one STL file %s (the export "
                             "wrote one over the other); it is printed once"
                             % (name, seen_paths[path], bname, path))
                continue
            seen_paths[path] = bname
            try:
                mesh = read_stl(read(path))
            except Exception as exc:        # any reader failure is a note, not a crash
                notes.append("%s: body %r (%s) could not be read: %s" % (name, bname, path, exc))
                continue
            if not mesh.triangles:
                notes.append("%s: body %r (%s) has no triangles; skipped" % (name, bname, path))
                continue
            raw_bodies.append((bname, role, str(b.get("colour", "black" if role == "cells"
                                                         else "white")), mesh, path))
        if not raw_bodies:
            failures.append((name, "no STL body could be read" + (
                " (" + "; ".join(notes) + ")" if notes else "")))
            warnings.extend(notes)
            continue
        cells_raw = [m for _, role, _, m, _ in raw_bodies if role == "cells"]
        if tags_raw:
            first = tags_raw[0]
            mm, _, note = _measure_stl_scale(cells_raw, (first[2], first[3]), first[7],
                                             stl_units, name)
        else:
            # No frame: nothing to measure against; "flat" falls back (and
            # says so), "cad" never needed one.
            mm = UNIT_MM.get(stl_units.lower(), 1.0)
            note = None
        if note:
            notes.append(note)
        bodies = [Body(bn, role, colour, scale_mesh(m, mm), path)
                  for bn, role, colour, m, path in raw_bodies]
        cells_mm = [bd.mesh for bd in bodies if bd.role == "cells"]
        tags: List[TagFrame] = []
        for key, origin, x, y, z, depth, rise, spec, units, recess in tags_raw:
            f, fnote = _measure_frame_scale(cells_mm, origin, z, depth, rise, units,
                                            "%s tag %r" % (name, key), recess)
            if fnote:
                notes.append(fnote)
            tags.append(TagFrame(key, _scale(origin, f), x, y, z, depth, rise, spec, recess))
        warnings.extend(notes)
        parts.append(Part(name=name, slug=s, copies=copies, bodies=bodies, tags=tags,
                          variant=bool(entry.get("variant", False)), notes=notes))
    return parts, failures, warnings


# ------------------------------------------------------------------ orientation

class Orientation(NamedTuple):
    rule: str             # "face_down" | "face_up" | "fallback"
    reason: str
    tag: Optional[str]    # the tag the rule was decided on
    down: Vec3            # part-frame direction that ends up pointing at the bed
    x_ref: Vec3           # part-frame direction that ends up along bed +X
    contact_mm2: float    # area resting on the bed
    warning: Optional[str]


def _contact_area(meshes, down) -> Tuple[float, float]:
    """(area on the supporting plane perpendicular to `down`, plane offset)."""
    top = max(_dot(p, down) for m in meshes for p in m.vertices)
    area = 0.0
    for m in meshes:
        for tri in m.triangles:
            if min(_dot(m.vertices[i], down) for i in tri) < top - PLANE_TOL_MM:
                continue
            n, a = _tri_normal_area(m, tri)
            if n is not None and _dot(n, down) > 0.99:
                area += a
    return area, top


def _plane_candidates(meshes, limit=16) -> List[Vec3]:
    groups: Dict[Tuple, List] = {}
    for m in meshes:
        for tri in m.triangles:
            n, a = _tri_normal_area(m, tri)
            if n is None:
                continue
            key = (round(n[0], 3) + 0.0, round(n[1], 3) + 0.0, round(n[2], 3) + 0.0,
                   round(_dot(n, m.vertices[tri[0]]), 2) + 0.0)
            g = groups.setdefault(key, [0.0, (0.0, 0.0, 0.0)])
            g[0] += a
            g[1] = (g[1][0] + n[0] * a, g[1][1] + n[1] * a, g[1][2] + n[2] * a)
    ranked = sorted(groups.items(), key=lambda kv: (-kv[1][0], kv[0]))
    out = []
    for _, (area, nsum) in ranked[:limit]:
        try:
            out.append(_unit(nsum))
        except ValueError:
            pass
    return out


def choose_orientation(meshes: Sequence[Mesh], tags: Sequence[TagFrame]) -> Orientation:
    """Pick how a part lies on the bed; see the module docstring for the rule."""
    meshes = [m for m in meshes if m.vertices]
    pts = [p for m in meshes for p in m.vertices]
    if not pts:
        raise ValueError("a part with no geometry cannot be oriented")

    # 1. pattern face down: inlaid black and nothing proud of the face.
    downs = []
    small_face = {}
    for t in tags:
        proud = max(_dot(_sub(p, t.origin), t.z) for p in pts)
        if t.raise_mm <= 0 and t.recess_mm <= 0 and proud <= PLANE_TOL_MM:
            area, _ = _contact_area(meshes, t.z)
            foot = _extent_along(meshes, t.x) * _extent_along(meshes, t.y)
            if area > 0 and area >= FLAT_BACK_MIN_FRACTION * foot:
                downs.append((-area, len(downs), t, area))
            elif area > 0:
                small_face[t.key] = ("its face carries only %.0f%% of the %.0f mm2 footprint "
                                     "(the rest would overhang)" % (100.0 * area / foot, foot))
    if downs:
        downs.sort(key=lambda c: (c[0], c[1]))
        _, _, t, area = downs[0]
        return Orientation(
            "face_down",
            "black inlaid %.2f mm, raise 0, nothing proud of tag %r's face: pattern face "
            "DOWN on the bed -- it prints flat and crisp in the first %.2f mm, the only "
            "layers with colour changes" % (t.depth_mm, t.key, t.depth_mm),
            t.key, t.z, t.x, area, None)

    # 2. pattern face up on a flat back.
    why = {}
    for t in tags:
        proud = max(_dot(_sub(p, t.origin), t.z) for p in pts)
        if t.raise_mm > 0:
            why[t.key] = "black raised %.2f mm (raise_m > 0)" % t.raise_mm
        elif t.recess_mm > 0:
            why[t.key] = ("black recessed %.2f mm below the face (face down it would "
                          "print over air)" % t.recess_mm)
        elif t.key in small_face:
            why[t.key] = small_face[t.key]
        elif proud > PLANE_TOL_MM:
            why[t.key] = "geometry stands %.2f mm proud of the face" % proud
        else:
            why[t.key] = "no flat contact on the face"
        back = _scale(t.z, -1.0)
        area, _ = _contact_area(meshes, back)
        foot = _extent_along(meshes, t.x) * _extent_along(meshes, t.y)
        if foot > 0 and area >= FLAT_BACK_MIN_FRACTION * foot:
            return Orientation(
                "face_up",
                "%s on tag %r: pattern face UP on its flat back (%.0f mm2 on the bed, "
                "%.0f%% of the %.0f mm2 footprint)"
                % (why[t.key], t.key, area, 100.0 * area / foot, foot),
                t.key, back, t.x, area, None)

    # 3. fallback: the largest flat supporting face, then the lowest part.
    primary = tags[0] if tags else None
    axes = ([primary.x, primary.y, primary.z] if primary else
            [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)])
    cands: List[Vec3] = []
    for d in _plane_candidates(meshes) + [s for a in axes for s in (_scale(a, -1.0), a)]:
        if not any(_dot(d, c) > 1 - 1e-9 for c in cands):
            cands.append(d)
    best = None
    for i, d in enumerate(cands):
        area, _ = _contact_area(meshes, d)
        key = (-round(area, 6), round(_extent_along(meshes, d), 6), i)
        if best is None or key < best[0]:
            best = (key, d, area)
    _, d, area = best
    lead = ("; ".join("tag %r: %s" % (k, v) for k, v in why.items())
            if why else "no tag frame")
    return Orientation(
        "fallback",
        "%s, and no flat back: laid on its largest flat supporting face (%.0f mm2 on "
        "the bed, %.1f mm tall)" % (lead, area, _extent_along(meshes, d)),
        primary.key if primary else None, d, primary.x if primary else (1.0, 0.0, 0.0),
        area, "check its orientation in the slicer" + (
            "" if area > 0 else " -- no flat face at all, it needs supports"))


def cad_orientation(meshes: Sequence[Mesh]) -> Orientation:
    """orientation="cad": exactly as modelled -- no rotation, the part's own
    -Z on the bed. Warns when it does not stand on a flat bottom."""
    meshes = [m for m in meshes if m.vertices]
    if not meshes:
        raise ValueError("a part with no geometry cannot be placed")
    down = (0.0, 0.0, -1.0)
    area, _ = _contact_area(meshes, down)
    foot = _extent_along(meshes, (1.0, 0.0, 0.0)) * _extent_along(meshes, (0.0, 1.0, 0.0))
    warning = None
    if area <= 0:
        warning = "as modelled it has no flat bottom on the bed -- it needs supports"
    elif area < FLAT_BACK_MIN_FRACTION * foot:
        warning = ("as modelled it stands on %.0f mm2 of its %.0f mm2 footprint -- it may "
                   "need supports" % (area, foot))
    return Orientation("cad", "kept as modelled (orientation='cad'): no rotation, dropped to "
                       "z = 0; %.0f mm2 on the bed" % area, None, down, (1.0, 0.0, 0.0),
                       area, warning)


def orientation_rotation(down: Vec3, x_ref: Vec3):
    """Rows of the rotation taking `down` to -Z and `x_ref` (projected onto
    the bed plane) to +X."""
    zb = _unit(_scale(down, -1.0))
    for ref in (x_ref, (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)):
        xp = _sub(ref, _scale(zb, _dot(ref, zb)))
        if _norm(xp) > 1e-6:
            xb = _unit(xp)
            return (xb, _cross(zb, xb), zb)
    raise ValueError("cannot build a bed frame")  # pragma: no cover


@dataclass
class OrientedPart:
    part: Part
    orientation: Orientation
    rotation: Tuple[Vec3, Vec3, Vec3]   # rows; part frame -> bed frame
    translation: Vec3                   # applied after the rotation
    meshes: List[Mesh]                  # one per part.bodies, oriented
    size_mm: Vec3                       # w (x), d (y), h (z)

    @property
    def cells_z_mm(self) -> Optional[Tuple[float, float]]:
        zs = [p[2] for b, m in zip(self.part.bodies, self.meshes) if b.role == "cells"
              for p in m.vertices]
        return (min(zs), max(zs)) if zs else None


def orient_part(part: Part, orientation: Optional[Orientation] = None) -> OrientedPart:
    meshes = [b.mesh for b in part.bodies]
    o = orientation or choose_orientation(meshes, part.tags)
    rows = orientation_rotation(o.down, o.x_ref)
    turned = [transform_mesh(m, rows) for m in meshes]
    lo, hi = bbox(turned)
    t = (-(lo[0] + hi[0]) / 2.0, -(lo[1] + hi[1]) / 2.0, -lo[2])
    ident = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    placed = [transform_mesh(m, ident, t) for m in turned]
    # Exact zero on the bed: a rotation of float32 input can leave 1e-16.
    placed = [Mesh([(p[0], p[1], 0.0 if abs(p[2]) < 1e-9 else p[2]) for p in m.vertices],
                   m.triangles) for m in placed]
    size = (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])
    return OrientedPart(part, o, rows, t, placed, size)


# ------------------------------------------------------------------ settings

@dataclass
class BedSettings:
    bed_w_mm: float = MK4_BED_MM[0]
    bed_d_mm: float = MK4_BED_MM[1]
    margin_mm: float = 5.0
    spacing_mm: float = 5.0
    bed_h_mm: Optional[float] = None            # max print height; None = unchecked
    allow_rotate: bool = True
    centre: bool = True
    # The MMU wipe tower's footprint, brim included: every bed keeps a hole
    # this big; None = no tower (single colour or a toolchanger).
    wipe_tower_mm: Optional[Tuple[float, float]] = DEFAULT_WIPE_TOWER_MM
    wipe_tower_brim_mm: float = WIPE_TOWER_BRIM_MM
    tile_extruder: int = 1               # every non-cells body
    cells_extruder: int = 2
    orientation: str = "flat"            # "flat" (by the tag) | "cad" (as modelled)
    instances: bool = False              # copies on a bed as instances of one object

    def usable(self) -> Tuple[float, float]:
        return self.bed_w_mm - 2 * self.margin_mm, self.bed_d_mm - 2 * self.margin_mm

    def check(self):
        uw, ud = self.usable()
        if not (self.margin_mm >= 0 and self.spacing_mm >= 0):
            raise ValueError("margin_mm and spacing_mm must be >= 0")
        if self.orientation not in ("flat", "cad"):
            raise ValueError("orientation must be 'flat' or 'cad', not %r" % (self.orientation,))
        if uw <= 0 or ud <= 0:
            raise ValueError("a %.1f x %.1f mm bed with a %.1f mm margin leaves no room"
                             % (self.bed_w_mm, self.bed_d_mm, self.margin_mm))
        if self.wipe_tower_mm:
            tw, td = (float(v) for v in self.wipe_tower_mm)
            if tw <= 0 or td <= 0 or tw > uw + _EPS or td > ud + _EPS:
                raise ValueError("wipe tower %.1f x %.1f mm does not fit the %.1f x %.1f mm "
                                 "usable bed" % (tw, td, uw, ud))


# ------------------------------------------------------------------ packing

class Slot(NamedTuple):
    item: int             # index into the items handed to pack_rectangles
    bed: int              # 0-based
    x: float              # placed footprint's min corner, bed frame, mm
    y: float
    w: float              # footprint as placed (after a turn)
    d: float
    rotated: bool         # turned 90 degrees about Z


def _overlaps(a, b) -> bool:
    return (a[0] < b[0] + b[2] - _EPS and b[0] < a[0] + a[2] - _EPS
            and a[1] < b[1] + b[3] - _EPS and b[1] < a[1] + a[3] - _EPS)


def _contains(outer, inner) -> bool:
    return (inner[0] >= outer[0] - _EPS and inner[1] >= outer[1] - _EPS
            and inner[0] + inner[2] <= outer[0] + outer[2] + _EPS
            and inner[1] + inner[3] <= outer[1] + outer[3] + _EPS)


def _occupy(free: List[Rect], r: Rect) -> List[Rect]:
    """MaxRects split of every free rectangle `r` overlaps, then prune."""
    out = []
    x, y, w, h = r
    for f in free:
        if not _overlaps(f, r):
            out.append(f)
            continue
        fx, fy, fw, fh = f
        if x > fx + _EPS:
            out.append((fx, fy, x - fx, fh))
        if x + w < fx + fw - _EPS:
            out.append((x + w, fy, fx + fw - (x + w), fh))
        if y > fy + _EPS:
            out.append((fx, fy, fw, y - fy))
        if y + h < fy + fh - _EPS:
            out.append((fx, y + h, fw, fy + fh - (y + h)))
    kept = []
    for i, a in enumerate(out):
        if any(j != i and _contains(b, a) and (not _contains(a, b) or j < i)
               for j, b in enumerate(out)):
            continue
        kept.append(a)
    return kept


def _tower_hole(free: List[Rect], tower: Optional[Tuple[float, float]]) -> Optional[Rect]:
    """The back-most, then right-most tower-sized spot in `free` (inflated
    units), or None. No tower -> a dummy empty rect."""
    if tower is None:
        return (0.0, 0.0, 0.0, 0.0)
    tw, td = tower
    best = None
    for fx, fy, fw, fh in free:
        if fw + _EPS >= tw and fh + _EPS >= td:
            spot = (fx + fw - tw, fy + fh - td, tw, td)
            key = (-round(spot[1], 6), -round(spot[0], 6))
            if best is None or key < best[0]:
                best = (key, spot)
    return None if best is None else best[1]


def _find(free, w, h, allow_rotate, rule, tower):
    cands = []
    turns = (False, True) if allow_rotate and abs(w - h) > _EPS else (False,)
    for fx, fy, fw, fh in free:
        for rot in turns:
            iw, ih = (h, w) if rot else (w, h)
            if iw > fw + _EPS or ih > fh + _EPS:
                continue
            lw, lh = fw - iw, fh - ih
            if rule == "bssf":
                key = (min(lw, lh), max(lw, lh))
            elif rule == "baf":
                key = (fw * fh - iw * ih, min(lw, lh))
            else:                                   # bottom-left
                key = (fy + ih, fx)
            key = tuple(round(k, 6) for k in key) + (round(fy, 6), round(fx, 6), rot)
            cands.append((key, fx, fy, iw, ih, rot))
    cands.sort()
    for c in cands:
        if tower is None or _tower_hole(_occupy(free, c[1:5]), tower) is not None:
            return c
    return None


_ORDERS = (
    lambda w, d: (-(w * d), -max(w, d)),
    lambda w, d: (-max(w, d), -(w * d)),
    lambda w, d: (-d, -w),
    lambda w, d: (-w, -d),
)
_RULES = ("bl", "bssf", "baf")


def pack_rectangles(items: Sequence[Tuple[float, float]], settings: BedSettings,
                    keys: Optional[Sequence] = None):
    """Place footprints (w, d) in mm on as few beds as possible.

    -> (slots, rejected, towers): `slots[i]` is a Slot or None, `rejected`
    maps an item index to the reason it cannot go on any bed, `towers[b]` is
    bed b's wipe-tower rect (x, y, w, d; brim included) or None. `keys`
    (sortable, one per item) break ties so the result does not depend on
    input order."""
    settings.check()
    keys = list(keys) if keys is not None else list(range(len(items)))
    m, s = settings.margin_mm, settings.spacing_mm
    uw, ud = settings.usable()
    inner = (0.0, 0.0, uw + s, ud + s)
    tower = None
    if settings.wipe_tower_mm:
        tower = (float(settings.wipe_tower_mm[0]) + s, float(settings.wipe_tower_mm[1]) + s)

    rejected: Dict[int, str] = {}
    ok = []
    for i, (w, d) in enumerate(items):
        if not (w > 0 and d > 0 and math.isfinite(w) and math.isfinite(d)):
            rejected[i] = "footprint %r x %r mm is not a size" % (w, d)
            continue
        fits_bed = ((w <= uw + _EPS and d <= ud + _EPS) or
                    (settings.allow_rotate and d <= uw + _EPS and w <= ud + _EPS))
        if not fits_bed:
            rejected[i] = ("its footprint is %.1f x %.1f mm but the bed leaves %.1f x %.1f mm "
                           "inside the %.1f mm margin (%s) -- parts are never scaled; use a "
                           "bigger bed or print it another way"
                           % (w, d, uw, ud, m, "either way round" if settings.allow_rotate
                              else "turning is off"))
            continue
        if _find([inner], w + s, d + s, settings.allow_rotate, "bl", tower) is None:
            rejected[i] = ("its %.1f x %.1f mm footprint fits the bed but leaves no room "
                           "for the %.0f x %.0f mm wipe tower; pass wipe_tower_mm=None to "
                           "print it without the tower"
                           % (w, d, settings.wipe_tower_mm[0], settings.wipe_tower_mm[1]))
            continue
        ok.append(i)

    best = None
    for oi, order in enumerate(_ORDERS):
        seq = sorted(ok, key=lambda i: (order(*items[i]), keys[i]))
        for ri, rule in enumerate(_RULES):
            bins: List[List[Rect]] = []
            slots: Dict[int, Slot] = {}
            for i in seq:
                w, d = items[i]
                spot = None
                for b, free in enumerate(bins):
                    f = _find(free, w + s, d + s, settings.allow_rotate, rule, tower)
                    if f is not None:
                        spot = (b, f)
                        break
                if spot is None:
                    bins.append([inner])
                    spot = (len(bins) - 1, _find(bins[-1], w + s, d + s,
                                                 settings.allow_rotate, rule, tower))
                b, (_, fx, fy, iw, ih, rot) = spot
                bins[b] = _occupy(bins[b], (fx, fy, iw, ih))
                slots[i] = Slot(i, b, m + fx, m + fy, iw - s, ih - s, rot)
            spread = sum(sl.bed * sl.w * sl.d for sl in slots.values())
            compact = 0.0
            for b in range(len(bins)):
                mine = [sl for sl in slots.values() if sl.bed == b]
                compact += ((max(sl.x + sl.w for sl in mine) - min(sl.x for sl in mine))
                            * (max(sl.y + sl.d for sl in mine) - min(sl.y for sl in mine)))
            score = (len(bins), round(spread, 3), round(compact, 3), oi, ri)
            if best is None or score < best[0]:
                best = (score, slots, bins)
    slots = best[1] if best else {}
    out = dict(slots)
    towers: List[Optional[Rect]] = []
    for b in range(len(best[2]) if best else 0):
        mine = [sl for sl in slots.values() if sl.bed == b]
        shifts = [(0.0, 0.0)]
        if settings.centre:
            x0 = min(sl.x for sl in mine)
            y0 = min(sl.y for sl in mine)
            x1 = max(sl.x + sl.w for sl in mine)
            y1 = max(sl.y + sl.d for sl in mine)
            dx = settings.bed_w_mm / 2.0 - (x0 + x1) / 2.0
            dy = settings.bed_d_mm / 2.0 - (y0 + y1) / 2.0
            shifts = [(dx, dy), (dx, 0.0), (0.0, dy), (0.0, 0.0)]
        # Centre the parts, then find the tower its hole among them; the
        # unshifted packing always has one (every placement checked it).
        for dx, dy in shifts:
            free = [inner]
            for sl in mine:
                free = _occupy(free, (sl.x + dx - m, sl.y + dy - m, sl.w + s, sl.d + s))
            hole = _tower_hole(free, tower)
            if hole is not None:
                break
        for sl in mine:
            out[sl.item] = sl._replace(x=sl.x + dx, y=sl.y + dy)
        towers.append(None if tower is None else
                      (m + hole[0], m + hole[1], hole[2] - s, hole[3] - s))
    return [out.get(i) for i in range(len(items))], rejected, towers


# ------------------------------------------------------------------ layout

class Placement(NamedTuple):
    part: int             # index into BedLayout.parts
    copy: int             # 1-based
    bed: int              # 0-based
    x_mm: float           # footprint centre, bed frame
    y_mm: float
    w_mm: float           # footprint as placed
    d_mm: float
    rotated: bool


@dataclass
class BedLayout:
    settings: BedSettings
    parts: List[OrientedPart]
    beds: List[List[Placement]]
    towers: List[Optional[Rect]]          # per bed: wipe tower incl. brim, bed frame
    rejected: List[Tuple[str, str]]       # (part name, reason)
    skipped: List[Tuple[str, str]]        # (part name, reason): nothing to print
    warnings: List[str]
    design: str = ""

    @property
    def bed_count(self) -> int:
        return len(self.beds)

    @property
    def placed(self) -> List[Placement]:
        return [p for bed in self.beds for p in bed]


def layout_parts(parts: Sequence[Part], settings: Optional[BedSettings] = None,
                 failures: Sequence[Tuple[str, str]] = (), warnings: Sequence[str] = (),
                 design: str = "") -> BedLayout:
    settings = settings or BedSettings()
    settings.check()
    warns = list(warnings)
    rejected = list(failures)
    skipped: List[Tuple[str, str]] = []
    oriented: List[OrientedPart] = []
    for part in parts:
        try:
            op = orient_part(part, cad_orientation([b.mesh for b in part.bodies])
                             if settings.orientation == "cad" else None)
        except ValueError as exc:
            rejected.append((part.name, "cannot be oriented: %s" % exc))
            continue
        if op.orientation.warning:
            warns.append("%s: %s orientation (%s) -- %s" % (
                part.name, op.orientation.rule, op.orientation.reason, op.orientation.warning))
        oriented.append(op)

    items, keys, owners = [], [], []
    for pi, op in enumerate(oriented):
        if settings.bed_h_mm is not None and op.size_mm[2] > settings.bed_h_mm + _EPS:
            rejected.append((op.part.name, "it stands %.1f mm tall laid %s, above the %.1f mm "
                             "print height" % (op.size_mm[2], op.orientation.rule.replace(
                                 "_", " "), settings.bed_h_mm)))
            continue
        if op.part.copies == 0:
            skipped.append((op.part.name, "0 visible copies in the design; its oriented "
                            "files are still in parts/"))
            continue
        for c in range(op.part.copies):
            items.append((op.size_mm[0], op.size_mm[1]))
            keys.append((op.part.slug, c))
            owners.append((pi, c + 1))
    # "cad" keeps the modelled rotation, so no 90 degree turn either.
    pack_settings = replace(settings, allow_rotate=False) if settings.orientation == "cad" else settings
    slots, rej, towers = pack_rectangles(items, pack_settings, keys)
    seen = set()
    for i, reason in sorted(rej.items()):
        name = oriented[owners[i][0]].part.name
        if name not in seen:
            seen.add(name)
            n = sum(1 for j in rej if owners[j][0] == owners[i][0])
            rejected.append((name, "%s (%d cop%s not placed)" % (reason, n, "y" if n == 1 else "ies")))
    beds: List[List[Placement]] = [[] for _ in towers]
    for i, sl in enumerate(slots):
        if sl is None:
            continue
        pi, copy = owners[i]
        beds[sl.bed].append(Placement(pi, copy, sl.bed, sl.x + sl.w / 2.0, sl.y + sl.d / 2.0,
                                      sl.w, sl.d, sl.rotated))
    for bed in beds:
        bed.sort(key=lambda p: (round(-p.y_mm, 6), round(p.x_mm, 6), p.part, p.copy))
    for name, reason in rejected:
        warns.append("NOT PRINTED: %s -- %s" % (name, reason))
    return BedLayout(settings, oriented, beds, towers, rejected, skipped, warns, design)


def select_parts(manifest: dict, include: Optional[Sequence[str]] = None,
                 copies_override: Optional[Dict[str, int]] = None):
    """-> (manifest with only the chosen parts and their copy counts,
    skipped [(name, reason)], warnings). `include` = component names to
    print (None = all); `copies_override` = {component: copies}."""
    entries = list(manifest.get("parts", []))
    names = [str(e.get("component", "part")) for e in entries]
    warnings: List[str] = []
    skipped: List[Tuple[str, str]] = []
    if include is not None:
        chosen = set(str(n) for n in include)
        for n in sorted(chosen - set(names)):
            warnings.append("include: no part named %r in the manifest" % n)
        skipped = [(n, "not in the selection (include)") for n in names if n not in chosen]
        entries = [e for e, n in zip(entries, names) if n in chosen]
    if copies_override:
        known = set(str(e.get("component", "part")) for e in entries)
        for n, c in sorted(copies_override.items()):
            if str(n) not in known:
                warnings.append("copies_override: no selected part named %r" % (n,))
        out = []
        for e in entries:
            n = str(e.get("component", "part"))
            if n in copies_override:
                c = copies_override[n]
                if isinstance(c, bool) or not isinstance(c, int) or c < 0:
                    warnings.append("copies_override: %r for %s is not a count; kept %r"
                                    % (c, n, e.get("copies")))
                else:
                    e = dict(e, copies=c)
            out.append(e)
        entries = out
    return dict(manifest, parts=entries), skipped, warnings


def layout_beds(manifest: dict, read: Callable[[str], bytes],
                settings: Optional[BedSettings] = None,
                include: Optional[Sequence[str]] = None,
                copies_override: Optional[Dict[str, int]] = None) -> BedLayout:
    """The whole pipeline short of bytes: select, read, measure, orient, pack."""
    chosen, skipped, sel_warn = select_parts(manifest, include, copies_override)
    parts, failures, warnings = load_parts(chosen, read)
    layout = layout_parts(parts, settings, failures, sel_warn + warnings,
                          design=str(manifest.get("design", "")))
    layout.skipped[:0] = skipped
    return layout


# ------------------------------------------------------------------ PrusaSlicer beds

def bed_grid_coords(index: int) -> Tuple[int, int]:
    """PrusaSlicer 2.9 BedsGrid::index2grid_coords for the first quadrant:
    bed 0 (0,0), 1 (1,0), 2 (0,1), 3 (1,1), 4 (2,0), 5 (2,1), 6 (0,2) ..."""
    if index < 0:
        raise ValueError("negative bed index")
    if index == 0:
        return 0, 0
    ident = index + 1
    a = 1
    while (a + 1) * (a + 1) < ident:
        a += 1
    ident -= a * a
    x = y = a
    if ident <= a:
        y = ident - 1
    else:
        x = ident - a - 1
    return x, y


def bed_gap_mm(bed_w_mm: float, bed_d_mm: float) -> float:
    """MultipleBeds::bed_gap: min(100, 0.3 x the bed's diagonal), both axes."""
    return min(100.0, math.hypot(bed_w_mm, bed_d_mm) * 0.3)


def bed_translation(index: int, bed_w_mm: float, bed_d_mm: float) -> Tuple[float, float]:
    """Where bed `index` sits in a PrusaSlicer 2.9 project (world mm)."""
    gx, gy = bed_grid_coords(index)
    gap = bed_gap_mm(bed_w_mm, bed_d_mm)
    return gx * (bed_w_mm + gap), gy * (bed_d_mm + gap)


# ------------------------------------------------------------------ PrusaSlicer presets

_META_KEYS = {"inherits", "compatible_printers", "compatible_printers_condition",
              "compatible_prints", "compatible_prints_condition", "renamed_from", "alias",
              "instantiation"}


def parse_ini_sections(text: str) -> Dict[str, Dict[str, str]]:
    """A PrusaSlicer config bundle (vendor .ini) -> {"printer:Name": {key: raw value}}."""
    secs: Dict[str, Dict[str, str]] = {}
    cur = None
    for line in text.splitlines():
        m = re.match(r"^\[(.+)\]\s*$", line)
        if m:
            cur = secs.setdefault(m.group(1), {})
            continue
        if cur is None or not line.strip() or line.lstrip().startswith(("#", ";")):
            continue
        k, sep, v = line.partition("=")
        if sep:
            cur[k.strip()] = v.strip()
    return secs


def resolve_preset(secs: Dict[str, Dict[str, str]], kind: str, name: str,
                   _seen: Tuple[str, ...] = ()) -> Dict[str, str]:
    """One preset with its `inherits` chain applied (later parents win, the
    preset's own keys win over all), the way PrusaSlicer loads a bundle."""
    key = "%s:%s" % (kind, name)
    if key in _seen:
        raise ValueError("inheritance loop at %s" % key)
    if key not in secs:
        raise ValueError("no [%s] in the vendor bundle" % key)
    own = secs[key]
    out: Dict[str, str] = {}
    for parent in [p.strip().strip('"') for p in own.get("inherits", "").split(";") if p.strip()]:
        out.update(resolve_preset(secs, kind, parent, _seen + (key,)))
    out.update(own)
    return out


@dataclass
class PrusaProfile:
    """A printer + print + filament chain resolved from the vendor bundle
    installed with PrusaSlicer, ready to become a project's config."""
    printer: str
    print: str
    filament: str
    vendor: str                         # e.g. "Prusa Research 2.5.11"
    values: Dict[str, str]              # every key of the three chains
    source: str = ""                    # the bundle file

    @property
    def extruders(self) -> int:
        return len([v for v in self.values.get("nozzle_diameter", "0.4").split(",") if v.strip()])

    @property
    def bed_mm(self) -> Tuple[float, float]:
        pts = [tuple(float(c) for c in p.split("x"))
               for p in self.values.get("bed_shape", "0x0,250x0,250x210,0x210").split(",")]
        return (max(p[0] for p in pts) - min(p[0] for p in pts),
                max(p[1] for p in pts) - min(p[1] for p in pts))

    @property
    def bed_origin_mm(self) -> Tuple[float, float]:
        pts = [tuple(float(c) for c in p.split("x"))
               for p in self.values.get("bed_shape", "0x0").split(",")]
        return min(p[0] for p in pts), min(p[1] for p in pts)

    @property
    def max_height_mm(self) -> Optional[float]:
        try:
            return float(self.values["max_print_height"])
        except (KeyError, ValueError):
            return None

    @property
    def wipe_tower_width_mm(self) -> float:
        try:
            return float(self.values.get("wipe_tower_width", "60"))
        except ValueError:
            return 60.0


def load_prusa_profile(bundle_text: str, printer: str = DEFAULT_PRINTER,
                       print_: str = DEFAULT_PRINT, filament: str = DEFAULT_FILAMENT,
                       source: str = "") -> PrusaProfile:
    """Resolve the three presets from a vendor bundle's text. Raises
    ValueError naming the missing preset."""
    secs = parse_ini_sections(bundle_text)
    vendor = secs.get("vendor", {})
    values: Dict[str, str] = {}
    for kind, name in (("printer", printer), ("print", print_), ("filament", filament)):
        for k, v in resolve_preset(secs, kind, name).items():
            if k not in _META_KEYS:
                values[k] = v
    return PrusaProfile(printer, print_, filament,
                        "%s %s" % (vendor.get("name", "?"), vendor.get("config_version", "?")),
                        values, source)


def find_vendor_bundle(vendor: str = "PrusaResearch") -> Optional[str]:
    """The vendor bundle PrusaSlicer itself uses on this machine: the data
    directory's (kept current by its updater) before the installed copy."""
    home = os.path.expanduser("~")
    cands = []
    if os.environ.get("APPDATA"):
        cands.append(os.path.join(os.environ["APPDATA"], "PrusaSlicer", "vendor", vendor + ".ini"))
    cands += [os.path.join(home, "Library", "Application Support", "PrusaSlicer", "vendor",
                           vendor + ".ini"),
              os.path.join(home, ".config", "PrusaSlicer", "vendor", vendor + ".ini"),
              os.path.join(home, ".PrusaSlicer", "vendor", vendor + ".ini")]
    for pf in (os.environ.get("ProgramW6432"), os.environ.get("ProgramFiles")):
        if pf:
            cands.append(os.path.join(pf, "Prusa3D", "PrusaSlicer", "resources", "profiles",
                                      vendor + ".ini"))
    cands.append(os.path.join("/Applications", "PrusaSlicer.app", "Contents", "Resources",
                              "profiles", vendor + ".ini"))
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def _split_strings(value: str) -> List[str]:
    out = []
    for tok in re.findall(r'"(?:[^"\\]|\\.)*"|[^;]+', value):
        tok = tok.strip()
        out.append(tok[1:-1] if tok.startswith('"') and tok.endswith('"') else tok)
    return out


def _join_strings(values: Sequence[str]) -> str:
    return ";".join('"%s"' % v if (not v or re.search(r'[\s;"]', v)) else v for v in values)


def project_config_text(profile: PrusaProfile, settings: BedSettings,
                        tile_colour: str = TILE_COLOUR, cells_colour: str = CELLS_COLOUR) -> str:
    """Metadata/Slic3r_PE.config: "; key = value" lines (PrusaSlicer reads
    them bottom-up until the first line that is not one), the full preset
    chain, the preset names, and the white / black colour of the tile and
    cells extruders (extruder_colour wins in PrusaSlicer's views, so both
    it and filament_colour are set). A bed that differs from the printer
    preset's replaces its bed_shape."""
    v = dict(profile.values)
    n = profile.extruders
    ec = _split_strings(v.get("extruder_colour", "")) or [""]
    ec = (ec + [""] * n)[:n]
    fc = _split_strings(v.get("filament_colour", "#FF8000")) or ["#FF8000"]
    fc = (fc + [fc[0]] * n)[:n]
    for slot, colour in ((settings.tile_extruder, tile_colour),
                         (settings.cells_extruder, cells_colour)):
        if 1 <= slot <= n:
            ec[slot - 1] = colour
            fc[slot - 1] = colour
    v["extruder_colour"] = _join_strings(ec)
    v["filament_colour"] = _join_strings(fc)
    v["print_settings_id"] = profile.print
    v["printer_settings_id"] = profile.printer
    v["filament_settings_id"] = _join_strings([profile.filament] * n)
    w, d = profile.bed_mm
    if abs(w - settings.bed_w_mm) > 1e-6 or abs(d - settings.bed_d_mm) > 1e-6:
        ox, oy = profile.bed_origin_mm
        v["bed_shape"] = "%sx%s,%sx%s,%sx%s,%sx%s" % tuple(_f(c) for c in (
            ox, oy, ox + settings.bed_w_mm, oy, ox + settings.bed_w_mm,
            oy + settings.bed_d_mm, ox, oy + settings.bed_d_mm))
    lines = ["; generated by bam_cad_core.print_beds from %s: %s / %s / %s"
             % (profile.vendor, profile.printer, profile.print, profile.filament), ""]
    for k in sorted(v):
        val = v[k].replace("\r", "").replace("\n", "\\n")
        line = "; %s = %s" % (k, val)
        if len(line) < 10:            # PrusaSlicer's reader stops at a line this short
            line = line + " " * (10 - len(line))
        lines.append(line)
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ 3MF

class Volume3mf(NamedTuple):
    name: str
    mesh: Mesh
    extruder: int


class Object3mf(NamedTuple):
    name: str
    volumes: List[Volume3mf]
    instances: List[Tuple[float, float, float, float]]   # (rot_z_deg, x, y, z)
    extruder: int = 1


def _f(v: float) -> str:
    s = ("%.6f" % v).rstrip("0").rstrip(".")
    return "0" if s in ("", "-0", "-") else s


def _xml_attr(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _transform_3mf(rot_deg: float, x: float, y: float, z: float) -> str:
    """3MF item transform: row-vector convention, p' = p . M, 12 numbers."""
    c = round(math.cos(math.radians(rot_deg)), 12)
    s = round(math.sin(math.radians(rot_deg)), 12)
    return " ".join(_f(v) for v in (c, s, 0.0, -s, c, 0.0, 0.0, 0.0, 1.0, x, y, z))


_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
    ' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
    ' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
    ' <Default Extension="png" ContentType="image/png"/>\n'
    '</Types>\n')
_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
    ' <Relationship Target="/3D/3dmodel.model" Id="rel-1" '
    'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
    '</Relationships>\n')
_IDENTITY_4X4 = "1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"


def _zip(members: Sequence[Tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)
    return buf.getvalue()


def render_3mf(objects: Sequence[Object3mf], title: str = "",
               wipe_towers: Optional[Sequence[Tuple[int, float, float]]] = None,
               config_text: Optional[str] = None) -> bytes:
    """A PrusaSlicer-compatible 3MF: each object one mesh, its volumes as
    triangle ranges in Metadata/Slic3r_PE_model.config. `wipe_towers` =
    [(bed index, x, y)] bed-local tower corners; `config_text` = the
    project config (makes the file a PrusaSlicer project)."""
    model = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<model unit="millimeter" xml:lang="en-US" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
             'xmlns:slic3rpe="http://schemas.slic3r.org/3mf/2017/06">',
             ' <metadata name="slic3rpe:Version3mf">1</metadata>',
             ' <metadata name="Title">%s</metadata>' % _xml_attr(title),
             ' <metadata name="Application">bam_cad_core.print_beds</metadata>',
             ' <resources>']
    config = ['<?xml version="1.0" encoding="UTF-8"?>', '<config>']
    for oid, obj in enumerate(objects, start=1):
        model.append('  <object id="%d" type="model">' % oid)
        model.append('   <mesh>')
        model.append('    <vertices>')
        for vol in obj.volumes:
            for p in vol.mesh.vertices:
                model.append('     <vertex x="%s" y="%s" z="%s"/>' % (_f(p[0]), _f(p[1]), _f(p[2])))
        model.append('    </vertices>')
        model.append('    <triangles>')
        ranges = []
        base = 0
        first = 0
        for vol in obj.volumes:
            for a, b, c in vol.mesh.triangles:
                model.append('     <triangle v1="%d" v2="%d" v3="%d"/>' % (a + base, b + base, c + base))
            ranges.append((first, first + len(vol.mesh.triangles) - 1))
            first += len(vol.mesh.triangles)
            base += len(vol.mesh.vertices)
        model.append('    </triangles>')
        model.append('   </mesh>')
        model.append('  </object>')
        config.append(' <object id="%d" instances_count="%d">' % (oid, len(obj.instances)))
        config.append('  <metadata type="object" key="name" value="%s"/>' % _xml_attr(obj.name))
        config.append('  <metadata type="object" key="extruder" value="%d"/>' % obj.extruder)
        for vol, (lo, hi) in zip(obj.volumes, ranges):
            config.append('  <volume firstid="%d" lastid="%d">' % (lo, hi))
            config.append('   <metadata type="volume" key="name" value="%s"/>' % _xml_attr(vol.name))
            config.append('   <metadata type="volume" key="volume_type" value="ModelPart"/>')
            config.append('   <metadata type="volume" key="matrix" value="%s"/>' % _IDENTITY_4X4)
            config.append('   <metadata type="volume" key="extruder" value="%d"/>' % vol.extruder)
            config.append('   <mesh edges_fixed="0" degenerate_facets="0" facets_removed="0" '
                          'facets_reversed="0" backwards_edges="0"/>')
            config.append('  </volume>')
        config.append(' </object>')
    model.append(' </resources>')
    model.append(' <build>')
    for oid, obj in enumerate(objects, start=1):
        for inst in obj.instances:
            model.append('  <item objectid="%d" transform="%s" printable="1"/>'
                         % (oid, _transform_3mf(*inst)))
    model.append(' </build>')
    model.append('</model>')
    config.append('</config>')
    members = [("[Content_Types].xml", _CONTENT_TYPES.encode("utf-8")),
               ("_rels/.rels", _RELS.encode("utf-8")),
               ("3D/3dmodel.model", ("\n".join(model) + "\n").encode("utf-8"))]
    if wipe_towers:
        members.append(("Metadata/Prusa_Slicer_wipe_tower_information.xml", (
            '<?xml version="1.0" encoding="utf-8"?>\n' + "".join(
                '<wipe_tower_information bed_idx="%d" position_x="%s" position_y="%s" '
                'rotation_deg="0"/>\n' % (b, _f(x), _f(y)) for b, x, y in wipe_towers)
        ).encode("utf-8")))
    if config_text:
        members.append(("Metadata/Slic3r_PE.config", config_text.encode("utf-8")))
    members.append(("Metadata/Slic3r_PE_model.config", ("\n".join(config) + "\n").encode("utf-8")))
    return _zip(members)


def _volumes(op: OrientedPart, settings: BedSettings) -> List[Volume3mf]:
    order = sorted(range(len(op.part.bodies)),
                   key=lambda i: (op.part.bodies[i].role == "cells", i))
    return [Volume3mf(op.part.bodies[i].name, op.meshes[i],
                      settings.cells_extruder if op.part.bodies[i].role == "cells"
                      else settings.tile_extruder) for i in order]


def tower_position(layout: BedLayout, bed: int) -> Optional[Tuple[float, float]]:
    """The tower's corner as PrusaSlicer stores it: inside the kept hole,
    clear of its edge by the tower's own brim. Bed-local."""
    t = layout.towers[bed] if bed < len(layout.towers) else None
    if t is None:
        return None
    b = layout.settings.wipe_tower_brim_mm
    return t[0] + b, t[1] + b


def copy_name(op: OrientedPart, copy: int) -> str:
    """The object name of one copy: "<part> (2/6)", or "<part>" alone."""
    n = op.part.copies
    return op.part.name if n <= 1 else "%s (%d/%d)" % (op.part.name, copy, n)


def objects_for_beds(layout: BedLayout, beds: Sequence[int],
                     offsets: Optional[Dict[int, Tuple[float, float]]] = None) -> List[Object3mf]:
    """The objects of these beds, each bed shifted by offsets[bed] (its
    world placement in a multi-bed project). Every copy is its own object
    ("<part> (2/6)") so it can be turned on its own; with
    settings.instances the copies of a part become instances of one."""
    st = layout.settings
    if not st.instances:
        out = []
        for b in beds:
            ox, oy = (offsets or {}).get(b, (0.0, 0.0))
            for p in sorted(layout.beds[b], key=lambda q: (layout.parts[q.part].part.slug,
                                                          q.part, q.copy)):
                op = layout.parts[p.part]
                out.append(Object3mf(copy_name(op, p.copy), _volumes(op, st),
                                     [(90.0 if p.rotated else 0.0, p.x_mm + ox, p.y_mm + oy, 0.0)],
                                     st.tile_extruder))
        return out
    by_part: Dict[int, List[Placement]] = {}
    for b in beds:
        for p in layout.beds[b]:
            by_part.setdefault(p.part, []).append(p)
    out = []
    for pi in sorted(by_part, key=lambda i: (layout.parts[i].part.slug, i)):
        op = layout.parts[pi]
        insts = []
        for p in sorted(by_part[pi], key=lambda q: (q.bed, q.copy)):
            ox, oy = (offsets or {}).get(p.bed, (0.0, 0.0))
            insts.append((90.0 if p.rotated else 0.0, p.x_mm + ox, p.y_mm + oy, 0.0))
        out.append(Object3mf(op.part.name, _volumes(op, layout.settings), insts,
                             layout.settings.tile_extruder))
    return out


def project_groups(layout: BedLayout) -> List[List[int]]:
    """Beds per project file: PrusaSlicer 2.9 holds at most 9."""
    n = layout.bed_count
    return [list(range(i, min(n, i + MAX_BEDS_PER_PROJECT)))
            for i in range(0, n, MAX_BEDS_PER_PROJECT)]


def project_file(group: int) -> str:
    return PROJECT_FILE if group == 0 else PROJECT_FILE.replace(".3mf", "_%d.3mf" % (group + 1))


# ------------------------------------------------------------------ previews

def _hull(points):
    pts = sorted(set((round(p[0], 4), round(p[1], 4)) for p in points))
    if len(pts) <= 2:
        return pts

    def half(seq):
        h = []
        for p in seq:
            while len(h) >= 2 and ((h[-1][0] - h[-2][0]) * (p[1] - h[-2][1])
                                   - (h[-1][1] - h[-2][1]) * (p[0] - h[-2][0])) <= 0:
                h.pop()
            h.append(p)
        return h
    lower, upper = half(pts), half(reversed(pts))
    return lower[:-1] + upper[:-1]


def bed_shapes(layout: BedLayout, bed: int) -> List[dict]:
    """Top-view shapes of one bed in bed mm (+y to the back): per placed
    copy its outline ("hull"), its black cells as triangles seen from
    above, and its label. Shared by the SVG and any raster preview."""
    out = []
    for p in layout.beds[bed]:
        op = layout.parts[p.part]
        c, s = (0.0, 1.0) if p.rotated else (1.0, 0.0)

        def place(v):
            return (c * v[0] - s * v[1] + p.x_mm, s * v[0] + c * v[1] + p.y_mm)
        allv = [place(v) for m in op.meshes for v in m.vertices]
        cells = []
        for b, m in zip(op.part.bodies, op.meshes):
            if b.role != "cells":
                continue
            for tri in m.triangles:
                n, a = _tri_normal_area(m, tri)
                if n is not None and n[2] > 0.5:
                    cells.append([place(m.vertices[i]) for i in tri])
        label = op.part.name + (" (%d/%d)" % (p.copy, op.part.copies) if op.part.copies > 1 else "")
        out.append({"part": op.part.name, "copy": p.copy, "label": label,
                    "rule": op.orientation.rule, "hull": _hull(allv), "cells": cells,
                    "box": (p.x_mm - p.w_mm / 2, p.y_mm - p.d_mm / 2, p.w_mm, p.d_mm),
                    "centre": (p.x_mm, p.y_mm)})
    return out


def render_bed_svg(layout: BedLayout, bed: int) -> bytes:
    st = layout.settings
    W, D = st.bed_w_mm, st.bed_d_mm
    m = st.margin_mm

    def Y(y):
        return D - y

    def pts(seq):
        return " ".join("%.2f,%.2f" % (x, Y(y)) for x, y in seq)
    shapes = bed_shapes(layout, bed)
    util = bed_utilisation(layout, bed)
    o = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="-8 -18 %s %s" width="%dmm" '
         'height="%dmm" font-family="Helvetica, Arial, sans-serif">'
         % (_f(W + 16), _f(D + 30), round(W + 16), round(D + 30)),
         '<rect x="-8" y="-18" width="%s" height="%s" fill="#f4f4f1"/>' % (_f(W + 16), _f(D + 30)),
         '<text x="0" y="-8" font-size="5" fill="#222">%s</text>' % _xml_attr(
             "Bed %d/%d · %s x %s mm · %d part%s · %.0f%% of the usable area"
             % (bed + 1, layout.bed_count, _f(W), _f(D), len(shapes),
                "" if len(shapes) == 1 else "s", 100 * util)),
         '<text x="0" y="-2.5" font-size="3" fill="#666">top view, front edge at the bottom; '
         'faint black = pattern face down (seen through the part)</text>',
         '<rect x="0" y="0" width="%s" height="%s" fill="#dcdcd6" stroke="#888" '
         'stroke-width="0.4"/>' % (_f(W), _f(D)),
         '<rect x="%s" y="%s" width="%s" height="%s" fill="none" stroke="#999" '
         'stroke-width="0.3" stroke-dasharray="2 2"/>' % (_f(m), _f(m), _f(W - 2 * m), _f(D - 2 * m))]
    tower = layout.towers[bed] if bed < len(layout.towers) else None
    if tower:
        zx, zy, zw, zd = tower
        o.append('<rect x="%s" y="%s" width="%s" height="%s" fill="#f2d7a8" stroke="#c08a30" '
                 'stroke-width="0.4" stroke-dasharray="1.5 1"/>' % (_f(zx), _f(Y(zy + zd)), _f(zw), _f(zd)))
        o.append('<text x="%s" y="%s" font-size="3.2" fill="#8a5a10" text-anchor="middle">'
                 'wipe tower</text>' % (_f(zx + zw / 2), _f(Y(zy + zd / 2) + 1.1)))
    for sh in shapes:
        o.append('<polygon points="%s" fill="#ffffff" stroke="#555" stroke-width="0.35"/>'
                 % pts(sh["hull"]))
        if sh["cells"]:
            d = " ".join("M%s Z" % " L".join("%.2f %.2f" % (x, Y(y)) for x, y in t)
                         for t in sh["cells"])
            o.append('<path d="%s" fill="#111" fill-opacity="%s"/>'
                     % (d, "0.3" if sh["rule"] == "face_down" else "1"))
        bx, by, bw, bd = sh["box"]
        size = max(1.6, min(3.0, 0.92 * bw / max(1, 0.56 * len(sh["label"]))))
        cx, cy = sh["centre"]
        o.append('<rect x="%s" y="%s" width="%s" height="%s" rx="0.8" fill="#ffffff" '
                 'fill-opacity="0.85"/>' % (_f(cx - 0.29 * size * len(sh["label"]) - 1),
                                            _f(Y(cy) - size * 0.85),
                                            _f(0.58 * size * len(sh["label"]) + 2),
                                            _f(size * 1.2)))
        o.append('<text x="%s" y="%s" font-size="%s" fill="#000" text-anchor="middle">%s</text>'
                 % (_f(cx), _f(Y(cy) + size * 0.1), _f(size), _xml_attr(sh["label"])))
        o.append('<text x="%s" y="%s" font-size="%s" fill="#555" text-anchor="middle">%s</text>'
                 % (_f(cx), _f(Y(cy) + size * 1.3), _f(size * 0.7),
                    sh["rule"].replace("_", " ")))
    o.append('<text x="%s" y="%s" font-size="3.2" fill="#666" text-anchor="middle">front</text>'
             % (_f(W / 2), _f(D + 6)))
    o.append('</svg>')
    return ("\n".join(o) + "\n").encode("utf-8")


# ------------------------------------------------------------------ summary + files

def bed_utilisation(layout: BedLayout, bed: int) -> float:
    uw, ud = layout.settings.usable()
    return sum(p.w_mm * p.d_mm for p in layout.beds[bed]) / (uw * ud)


def _r(v: float) -> float:
    return round(float(v), 4) + 0.0


def bed_file(bed: int, ext: str = "3mf") -> str:
    return "beds/bed_%02d.%s" % (bed + 1, ext)


def part_files(op: OrientedPart) -> Dict[str, str]:
    """{body name: parts/<slug>__<role>[_n].stl} plus "3mf"."""
    out: Dict[str, str] = {}
    count: Dict[str, int] = {}
    for b in op.part.bodies:
        count[b.role] = count.get(b.role, 0) + 1
        suffix = "" if count[b.role] == 1 else "_%d" % count[b.role]
        out[b.name] = "parts/%s__%s%s.stl" % (op.part.slug, b.role, suffix)
    out["3mf"] = "parts/%s.3mf" % op.part.slug
    return out


def layout_summary(layout: BedLayout, profile: Optional[PrusaProfile] = None,
                   project_name: Optional[Callable[[int], str]] = None) -> dict:
    """JSON-able account of a layout (beds/layout.json)."""
    project_name = project_name or project_file
    st = layout.settings
    uw, ud = st.usable()
    parts = []
    for op in layout.parts:
        o = op.orientation
        cz = op.cells_z_mm
        files = part_files(op)
        parts.append({
            "part": op.part.name, "slug": op.part.slug, "copies": op.part.copies,
            "variant": op.part.variant,
            "orientation": {"rule": o.rule, "reason": o.reason, "tag": o.tag,
                            "contact_mm2": _r(o.contact_mm2), "warning": o.warning,
                            "part_to_bed_rotation": [[_r(v) for v in row] for row in op.rotation],
                            "then_translate_mm": [_r(v) for v in op.translation]},
            "size_mm": [_r(v) for v in op.size_mm],
            "black_z_mm": None if cz is None else [_r(cz[0]), _r(cz[1])],
            "bodies": [{"name": b.name, "role": b.role, "colour": b.colour, "source": b.source,
                        "extruder": st.cells_extruder if b.role == "cells" else st.tile_extruder,
                        "file": files[b.name]} for b in op.part.bodies],
            "file_3mf": files["3mf"],
            "notes": list(op.part.notes),
        })
    groups = project_groups(layout)
    beds = []
    for g, group in enumerate(groups):
        for k, b in enumerate(group):
            tx, ty = bed_translation(k, st.bed_w_mm, st.bed_d_mm)
            t = layout.towers[b]
            beds.append({
                "bed": b + 1, "file": bed_file(b), "preview": bed_file(b, "svg"),
                "project": project_name(g), "project_bed": k + 1,
                "project_offset_mm": [_r(tx), _r(ty)],
                "utilisation": _r(bed_utilisation(layout, b)),
                "wipe_tower_mm": None if t is None else [_r(c) for c in t],
                "placed": [{"part": layout.parts[p.part].part.name, "copy": p.copy,
                            "object": copy_name(layout.parts[p.part], p.copy),
                            "centre_mm": [_r(p.x_mm), _r(p.y_mm)],
                            "footprint_mm": [_r(p.w_mm), _r(p.d_mm)],
                            "rotation_deg": 90 if p.rotated else 0,
                            "orientation": layout.parts[p.part].orientation.rule}
                           for p in layout.beds[b]]})
    copies = sum(op.part.copies for op in layout.parts)
    return {
        "schema": SCHEMA,
        "design": layout.design,
        "frame": "PrusaSlicer bed: origin at the front-left corner, +x right, +y to the "
                 "back, +z up; millimetres",
        "bed_mm": [_r(st.bed_w_mm), _r(st.bed_d_mm)],
        "bed_h_mm": None if st.bed_h_mm is None else _r(st.bed_h_mm),
        "margin_mm": _r(st.margin_mm), "spacing_mm": _r(st.spacing_mm),
        "usable_mm": [_r(uw), _r(ud)],
        "allow_rotate": st.allow_rotate and st.orientation != "cad",
        "orientation": st.orientation,
        "instances": st.instances,
        "extruders": {"tile": st.tile_extruder, "cells": st.cells_extruder},
        "wipe_tower_mm": None if not st.wipe_tower_mm else [_r(c) for c in st.wipe_tower_mm],
        "project": {
            "files": [project_name(g) for g in range(len(groups))],
            "bed_gap_mm": _r(bed_gap_mm(st.bed_w_mm, st.bed_d_mm)),
            "max_beds_per_file": MAX_BEDS_PER_PROJECT,
            "presets": None if profile is None else {
                "printer": profile.printer, "print": profile.print,
                "filament": profile.filament, "vendor": profile.vendor,
                "source": profile.source, "extruders": profile.extruders},
        },
        "beds": beds,
        "parts": parts,
        "rejected": [{"part": n, "reason": r} for n, r in layout.rejected],
        "skipped": [{"part": n, "reason": r} for n, r in layout.skipped],
        "warnings": list(layout.warnings),
        "totals": {"beds": layout.bed_count, "placed": len(layout.placed),
                   "copies_requested": copies,
                   "utilisation_mean": _r(sum(bed_utilisation(layout, b)
                                              for b in range(layout.bed_count))
                                          / max(1, layout.bed_count))},
    }


def render_projects(layout: BedLayout, config_text: str,
                    project_name: Optional[Callable[[int], str]] = None) -> Dict[str, bytes]:
    """{name: bytes}: the multi-bed project(s), 9 beds per file, every bed
    at its PrusaSlicer 2.9 position, each with its wipe tower."""
    st = layout.settings
    project_name = project_name or project_file
    design = layout.design or "ArUco tags"
    files: Dict[str, bytes] = {}
    for g, group in enumerate(project_groups(layout)):
        offsets = {b: bed_translation(k, st.bed_w_mm, st.bed_d_mm) for k, b in enumerate(group)}
        towers = [(k, *tower_position(layout, b)) for k, b in enumerate(group)
                  if tower_position(layout, b) is not None]
        files[project_name(g)] = render_3mf(
            objects_for_beds(layout, group, offsets),
            "%s: %d bed%s" % (design, len(group), "" if len(group) == 1 else "s"),
            towers, config_text)
    return files


def render_beds(layout: BedLayout, profile: Optional[PrusaProfile] = None,
                tile_colour: str = TILE_COLOUR, cells_colour: str = CELLS_COLOUR) -> Dict[str, bytes]:
    """{relpath: bytes} for everything: aruco_tags.3mf (+ _2 ... past 9
    beds), beds/*.3mf, beds/*.svg, beds/layout.json, parts/*.stl,
    parts/*.3mf. Without a `profile` the bed files carry no config, so
    PrusaSlicer keeps its current presets -- and the all-beds project is
    left out: without a config PrusaSlicer re-centres every instance on bed
    0, which would scramble the beds."""
    st = layout.settings
    files: Dict[str, bytes] = {}
    cfg = None if profile is None else project_config_text(profile, st, tile_colour, cells_colour)
    design = layout.design or "ArUco tags"
    if cfg is not None:
        files.update(render_projects(layout, cfg))
    for b in range(layout.bed_count):
        t = tower_position(layout, b)
        files[bed_file(b)] = render_3mf(
            objects_for_beds(layout, [b]),
            "%s bed %d of %d" % (design, b + 1, layout.bed_count),
            None if t is None else [(0, t[0], t[1])], cfg)
        files[bed_file(b, "svg")] = render_bed_svg(layout, b)
    for op in layout.parts:
        names = part_files(op)
        for body, mesh in zip(op.part.bodies, op.meshes):
            files[names[body.name]] = write_stl(mesh, "%s %s" % (op.part.slug, body.role))
        files[names["3mf"]] = render_3mf(
            [Object3mf(op.part.name, _volumes(op, st),
                       [(0.0, st.bed_w_mm / 2.0, st.bed_d_mm / 2.0, 0.0)], st.tile_extruder)],
            op.part.name)
    files["beds/layout.json"] = (json.dumps(layout_summary(layout, profile), indent=2,
                                            sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    return files


def manifest_reader(manifest_path: str) -> Callable[[str], bytes]:
    """Read a manifest's `stl` strings: they are relative to the export root
    (aruco/print3d/x.stl), so try the manifest's folder and its parents,
    then the bare file name beside the manifest."""
    here = os.path.dirname(os.path.abspath(manifest_path))

    def read(rel: str) -> bytes:
        rel_os = os.path.join(*[p for p in re.split(r"[\\/]+", rel) if p]) if rel else ""
        tried = []
        base = here
        for _ in range(4):
            cand = os.path.join(base, rel_os)
            tried.append(cand)
            if rel_os and os.path.isfile(cand):
                with open(cand, "rb") as f:
                    return f.read()
            base = os.path.dirname(base)
        cand = os.path.join(here, os.path.basename(rel_os))
        tried.append(cand)
        if rel_os and os.path.isfile(cand):
            with open(cand, "rb") as f:
                return f.read()
        raise OSError("not found; tried %s" % ", ".join(tried))
    return read


_OWN_FILES = (re.compile(r"^aruco_tags(_\d+)?\.3mf$"),
              re.compile(r"^beds/bed_\d+\.(3mf|svg)$"),
              re.compile(r"^parts/[^/]+__(tile|cells|other)(_\d+)?\.stl$"),
              re.compile(r"^parts/[^/]+\.3mf$"))


def write_files(out_dir: str, files: Dict[str, bytes], clean: bool = True) -> List[str]:
    """Write {relpath: bytes} under out_dir; with `clean`, remove earlier
    files of OUR naming (aruco_tags*.3mf, beds/bed_NN.*, parts/...) that
    this run did not write, so a bed that no longer exists cannot be
    printed by mistake."""
    written = []
    for rel in sorted(files):
        path = os.path.join(out_dir, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(files[rel])
        written.append(path)
    if clean:
        for sub in ("", "beds", "parts"):
            folder = os.path.join(out_dir, sub) if sub else out_dir
            if not os.path.isdir(folder):
                continue
            for name in sorted(os.listdir(folder)):
                rel = "%s/%s" % (sub, name) if sub else name
                if (rel not in files and any(p.match(rel) for p in _OWN_FILES)
                        and os.path.isfile(os.path.join(folder, name))):
                    os.remove(os.path.join(folder, name))
    return written


def find_prusaslicer_gui() -> Optional[str]:
    """The PrusaSlicer GUI executable on this machine, or None -- for an
    add-in that opens a written project (`[exe, path]`)."""
    cands = []
    for env in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        if os.environ.get(env):
            cands.append(os.path.join(os.environ[env], "Prusa3D", "PrusaSlicer", "prusa-slicer.exe"))
    cands.append("/Applications/PrusaSlicer.app/Contents/MacOS/PrusaSlicer")
    for name in ("prusa-slicer", "PrusaSlicer"):
        found = shutil.which(name)
        if found:
            cands.append(found)
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def _project_setup(bed_w_mm, bed_d_mm, margin_mm, spacing_mm, options: dict):
    """Shared by write_beds / write_selection_project: pops the preset
    options, resolves the PrusaSlicer profile, settles the bed. ->
    (settings, profile, (tile_colour, cells_colour), warnings); the rest of
    `options` become BedSettings fields."""
    bundle = options.pop("vendor_bundle", "auto")
    printer = options.pop("printer_preset", DEFAULT_PRINTER)
    print_ = options.pop("print_preset", DEFAULT_PRINT)
    filament = options.pop("filament_preset", DEFAULT_FILAMENT)
    colours = (options.pop("tile_colour", TILE_COLOUR), options.pop("cells_colour", CELLS_COLOUR))
    warn: List[str] = []
    profile: Optional[PrusaProfile] = None
    if bundle:
        path = find_vendor_bundle() if bundle == "auto" else bundle
        if path is None:
            warn.append("no PrusaSlicer vendor bundle found on this machine, so the files carry "
                        "no project config (PrusaSlicer keeps its current presets) and no "
                        "multi-bed project is written")
        else:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    profile = load_prusa_profile(f.read(), printer, print_, filament, path)
            except (OSError, ValueError) as exc:
                warn.append("PrusaSlicer presets unavailable (%s), so the files carry no project "
                            "config and no multi-bed project is written" % exc)
    if bed_w_mm is None or bed_d_mm is None:
        pw, pd = profile.bed_mm if profile is not None else MK4_BED_MM
        bed_w_mm = pw if bed_w_mm is None else bed_w_mm
        bed_d_mm = pd if bed_d_mm is None else bed_d_mm
    if profile is not None:
        if "bed_h_mm" not in options and profile.max_height_mm:
            options["bed_h_mm"] = profile.max_height_mm
        pw, pd = profile.bed_mm
        if abs(pw - float(bed_w_mm)) > 1e-6 or abs(pd - float(bed_d_mm)) > 1e-6:
            warn.append("the bed %s x %s mm differs from %s's %s x %s mm; the project uses "
                        "%s x %s (its bed_shape is overridden)"
                        % (_f(float(bed_w_mm)), _f(float(bed_d_mm)), profile.printer,
                           _f(pw), _f(pd), _f(float(bed_w_mm)), _f(float(bed_d_mm))))
        cells_slot = options.get("cells_extruder", 2)
        if profile.extruders < cells_slot:
            warn.append("%s has %d extruder(s); the black cells are on extruder %d, so "
                        "PrusaSlicer prints them in extruder 1's filament"
                        % (profile.printer, profile.extruders, cells_slot))
    settings = BedSettings(bed_w_mm=float(bed_w_mm), bed_d_mm=float(bed_d_mm),
                           margin_mm=float(margin_mm), spacing_mm=float(spacing_mm), **options)
    settings.check()
    return settings, profile, colours, warn


def write_beds(manifest_path: str, out_dir: Optional[str] = None,
               bed_w_mm: Optional[float] = None, bed_d_mm: Optional[float] = None,
               margin_mm: float = 5.0, spacing_mm: float = 5.0,
               include: Optional[Sequence[str]] = None,
               copies_override: Optional[Dict[str, int]] = None,
               orientation: str = "flat", instances: bool = False, **options) -> dict:
    """Lay out the parts in `manifest_path` (parts.json) and write
    aruco_tags.3mf, beds/ and parts/ under `out_dir` (default: the
    manifest's folder).

    bed_w_mm/_d_mm   default: the printer preset's bed (MK4: 250 x 210);
                     a bed passed here wins and goes into the bed_shape
    include          component names to print (None = all); the others are
                     listed under "skipped"
    copies_override  {component: copies} instead of the manifest's counts
    orientation      "flat" (laid down by the tag, the module rule) or
                     "cad" (as modelled: dropped to z = 0, never turned)
    instances        False: every copy its own object "<part> (2/6)";
                     True: copies on a bed are instances of one object

    options: printer_preset / print_preset / filament_preset (PrusaSlicer
    system preset names; default MK4 MMU3 0.4, 0.20mm STRUCTURAL, Generic
    PLA), vendor_bundle ("auto" = the one PrusaSlicer uses here; a path; or
    None for no project config), tile_colour / cells_colour, bed_h_mm,
    allow_rotate, centre, wipe_tower_mm (None = no tower hole),
    tile_extruder, cells_extruder, clean (default True).
    -> a JSON-able summary: files, beds, utilisation, rejected, skipped,
    warnings, prusaslicer_exe."""
    clean = options.pop("clean", True)
    options.update(orientation=orientation, instances=bool(instances))
    settings, profile, colours, pre_warn = _project_setup(bed_w_mm, bed_d_mm, margin_mm,
                                                          spacing_mm, options)
    with open(manifest_path, "rb") as f:
        manifest = json.loads(f.read().decode("utf-8"))
    out_dir = out_dir or os.path.dirname(os.path.abspath(manifest_path))
    layout = layout_beds(manifest, manifest_reader(manifest_path), settings,
                         include, copies_override)
    layout.warnings[:0] = pre_warn
    files = render_beds(layout, profile, *colours)
    write_files(out_dir, files, clean=clean)
    summary = layout_summary(layout, profile)

    def full(rel):
        return os.path.join(out_dir, *rel.split("/"))
    return {
        "out_dir": out_dir,
        "project": [full(r) for r in summary["project"]["files"] if r in files],
        "beds": layout.bed_count,
        "bed_files": [full(bed_file(b)) for b in range(layout.bed_count)],
        "previews": [full(bed_file(b, "svg")) for b in range(layout.bed_count)],
        "layout": full("beds/layout.json"),
        "parts_dir": full("parts"),
        "bed_mm": [settings.bed_w_mm, settings.bed_d_mm],
        "presets": summary["project"]["presets"],
        "placed": len(layout.placed),
        "copies_requested": summary["totals"]["copies_requested"],
        "utilisation": [b["utilisation"] for b in summary["beds"]],
        "orientation": {op.part.name: op.orientation.rule for op in layout.parts},
        "rejected": summary["rejected"],
        "skipped": summary["skipped"],
        "warnings": summary["warnings"],
        "prusaslicer_exe": find_prusaslicer_gui(),
    }


_OWN_STLS = re.compile(r"^[^/\\]+__(tile|cells|other)(_\d+)?\.stl$")


def write_part_stls(manifest_path: str, out_dir: Optional[str] = None,
                    include: Optional[Sequence[str]] = None, orientation: str = "flat",
                    clean: bool = True) -> dict:
    """ArUco Export's "STL files" type: every chosen part's bodies as loose
    STLs, oriented for printing -- no beds, no 3MF, no copies.

    Writes <out_dir>/<part>__<role>[_n].stl (the names write_beds uses
    under parts/): millimetres, the part resting on z = 0 and centred on the
    origin, laid down by its tag (orientation="flat", the module rule) or
    kept as modelled ("cad"). With `clean`, earlier STLs of that naming in
    out_dir that this run did not write are removed.
    -> {"out_dir", "files" (absolute), "orientation" {part: rule},
    "rejected", "skipped", "warnings"}."""
    if orientation not in ("flat", "cad"):
        raise ValueError("orientation must be 'flat' or 'cad', not %r" % (orientation,))
    with open(manifest_path, "rb") as f:
        manifest = json.loads(f.read().decode("utf-8"))
    out_dir = out_dir or os.path.dirname(os.path.abspath(manifest_path))
    chosen, skipped, warnings = select_parts(manifest, include)
    parts, failures, load_warn = load_parts(chosen, manifest_reader(manifest_path))
    warnings = list(warnings) + list(load_warn)
    rejected = list(failures)
    files: Dict[str, bytes] = {}
    rules: Dict[str, str] = {}
    for part in parts:
        try:
            op = orient_part(part, cad_orientation([b.mesh for b in part.bodies])
                             if orientation == "cad" else None)
        except ValueError as exc:
            rejected.append((part.name, "cannot be oriented: %s" % exc))
            continue
        if op.orientation.warning:
            warnings.append("%s: %s orientation (%s) -- %s" % (
                part.name, op.orientation.rule, op.orientation.reason, op.orientation.warning))
        rules[part.name] = op.orientation.rule
        names = part_files(op)
        for body, mesh in zip(op.part.bodies, op.meshes):
            files[names[body.name].split("/", 1)[1]] = write_stl(
                mesh, "%s %s" % (op.part.slug, body.role))
    for name, reason in rejected:
        warnings.append("NOT WRITTEN: %s -- %s" % (name, reason))
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for rel in sorted(files):
        path = os.path.join(out_dir, rel)
        with open(path, "wb") as f:
            f.write(files[rel])
        written.append(path)
    if clean:
        for name in sorted(os.listdir(out_dir)):
            path = os.path.join(out_dir, name)
            if name not in files and _OWN_STLS.match(name) and os.path.isfile(path):
                os.remove(path)
    return {"out_dir": out_dir, "files": written, "orientation": rules,
            "rejected": [{"part": n, "reason": r} for n, r in rejected],
            "skipped": [{"part": n, "reason": r} for n, r in skipped],
            "warnings": warnings}


def write_selection_project(objects: Sequence[dict], out_path: str,
                            bed_w_mm: Optional[float] = None, bed_d_mm: Optional[float] = None,
                            orientation: str = "cad", **options) -> dict:
    """The "print it integral" path: arbitrary Fusion bodies -> one
    PrusaSlicer project with the black already on extruder 2.

    objects: [{"name": str,
               "bodies": [{"stl": path, "role": "cells" | "tile" | "other",
                           "name": str}, ...],
               "frame": optional tag record (as in parts.json "tags": key,
                        origin, x_axis, y_axis, z_axis, units, spec), used
                        by orientation="flat",
               "copies": optional int (default 1)}]

    Each object becomes ONE PrusaSlicer object whose volumes are its bodies:
    a body with role "cells" or named "aruco cells ..." prints in the black
    filament (extruder 2), every other body in extruder 1. The STLs may
    share one (assembly) coordinate system: each object is kept as modelled
    (orientation="cad", the default: no rotation), dropped to z = 0 and
    packed onto as many beds as needed; "flat" lays it down by its frame.
    Writes `out_path` (out_path's stem + "_2.3mf" ... past 9 beds) with the
    same embedded preset config and white / black colours as
    aruco_tags.3mf. Without PrusaSlicer presets on this machine one bed is
    still written to out_path (no config: PrusaSlicer keeps its presets and
    re-centres it); several beds then go one per file, "<stem>_bed_NN.3mf".

    options: margin_mm, spacing_mm, stl_units (default "mm"), frame_units
    (default "cm"), design, instances, and write_beds' preset / colour /
    tower options.
    -> summary: files, beds, objects, placed, skipped, rejected, warnings,
    prusaslicer_exe, layout (the layout.json dict)."""
    margin = options.pop("margin_mm", 5.0)
    spacing = options.pop("spacing_mm", 5.0)
    stl_units = options.pop("stl_units", "mm")
    frame_units = options.pop("frame_units", "cm")
    design = options.pop("design", os.path.splitext(os.path.basename(out_path))[0])
    options["orientation"] = orientation
    settings, profile, colours, pre_warn = _project_setup(bed_w_mm, bed_d_mm, margin, spacing,
                                                          options)
    manifest = {"schema": MANIFEST_SCHEMA, "stl_units": stl_units, "frame_units": frame_units,
                "design": design, "parts": []}
    for i, o in enumerate(objects):
        frame = o.get("frame")
        manifest["parts"].append({
            "component": str(o.get("name") or "object %d" % (i + 1)),
            "copies": o.get("copies", 1),
            "bodies": [{"name": str(b.get("name") or "body %d" % (j + 1)),
                        "role": b.get("role", "other"), "stl": str(b["stl"])}
                       for j, b in enumerate(o.get("bodies", []))],
            "tags": [frame] if frame else []})

    def read(path: str) -> bytes:
        with open(path, "rb") as f:
            return f.read()
    layout = layout_beds(manifest, read, settings)
    layout.warnings[:0] = pre_warn
    out_path = os.path.abspath(out_path)
    stem, ext = os.path.splitext(out_path)
    ext = ext or ".3mf"

    def name(g: int) -> str:
        return stem + ext if g == 0 else "%s_%d%s" % (stem, g + 1, ext)
    if profile is not None:
        files = render_projects(layout, project_config_text(profile, settings, *colours), name)
    elif layout.bed_count <= 1:
        t = tower_position(layout, 0) if layout.bed_count else None
        files = {name(0): render_3mf(objects_for_beds(layout, range(layout.bed_count)), design,
                                     None if t is None else [(0, t[0], t[1])])}
    else:
        files = {}
        for b in range(layout.bed_count):
            t = tower_position(layout, b)
            files["%s_bed_%02d%s" % (stem, b + 1, ext)] = render_3mf(
                objects_for_beds(layout, [b]), "%s bed %d" % (design, b + 1),
                None if t is None else [(0, t[0], t[1])])
        layout.warnings.append("no project config, so the %d beds are separate files"
                               % layout.bed_count)
    for path in sorted(files):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            f.write(files[path])
    summary = layout_summary(layout, profile, lambda g: os.path.basename(name(g)))
    return {
        "files": sorted(files),
        "beds": layout.bed_count,
        "bed_mm": [settings.bed_w_mm, settings.bed_d_mm],
        "presets": summary["project"]["presets"],
        "placed": len(layout.placed),
        "objects": [copy_name(layout.parts[p.part], p.copy) for p in layout.placed],
        "orientation": {op.part.name: op.orientation.rule for op in layout.parts},
        "rejected": summary["rejected"],
        "skipped": summary["skipped"],
        "warnings": summary["warnings"],
        "prusaslicer_exe": find_prusaslicer_gui(),
        "layout": summary,
    }
