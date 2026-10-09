"""ArUco tags as modelled CAD: definitions, per-copy overrides, resolution.

Zach, 2026-10-07: "I want the markers to now kinda be like first class in
CAD, that is where I can define where they are and what the ids are", and
then, the same morning: "I want to 3D print the tags ... I do actually 100%
need to start modelling them in reality". So a tag here is GEOMETRY that
Fusion owns -- a tile body and an inlaid black-cells body -- plus the small
record that says which marker that geometry is. This module is everything
about that record that does not need Fusion: what it holds, how a copy
inherits or overrides it, whether the result is unique, and the cell boxes
the Fusion side builds.

### The one rule that shapes everything: geometry is shared per component

Fusion stores geometry once per component. Six copies of the actuator are
six occurrences of ONE component, so a tag modelled inside it is the same
tag six times, and that is a fact about the plastic, not about our data.
Zach's ask is a serial number: "on the arm I copy paste that actuator 6
times.. I want at that sub assembly level to be able to override the
component aruco ID ... without making a new model".

So there are two layers, kept apart on purpose:

    TagDef       what a component's geometry ENCODES (dictionary, id, size,
                 where on the part). Read off the ArUco feature.
    Override     what one COPY should be, authored at any assembly level, on
                 the occurrence, as data. Never touches the component.

`resolve()` composes them per occurrence path. When the answer differs from
what the geometry encodes, the instance is `needs_variant`: the add-in
realises it as its own tag part (a copy of the tag component with the new
id) placed on that copy at the level that owns the override, and hides the
template tag for that one path. The record is the source; the variant is a
derived, rebuildable output -- the same split as payload vs meshes.

### Where an override lives, and who wins

An override is stored on a NATIVE occurrence inside the component where it
was authored (the arm), keyed by the path below that occurrence plus the
tag key:

    owner "Arm", occurrence "Actuator:3":  {"Tag:1|top": {"marker_id": 17}}
    owner "",    occurrence "Arm:1":       {"Actuator:3+Tag:1|top": {...}}

Storing it on the native occurrence (not on a proxy) means it travels with
the arm into every design that inserts it, which is what "set those values
again at the sub-assembly level" needs. The OUTERMOST level wins: the robot
can re-serial an arm that already serialised its actuators, never the other
way round -- the same rule USD calls "stronger opinions" and SolidWorks
applies to configurations, because the assembly that places a part is the
one that knows which copy is which.

### Uniqueness is per PATTERN, not per dictionary name

OpenCV's DICT_4X4_50 is the first 50 markers of DICT_4X4_1000, so 4X4_50 #3
and 4X4_1000 #3 are the same printed square. Two tags collide when their
`pattern_key` matches, whatever the dictionary was called. That is the bug
`fiducials.py` was written about (four faces, one texture), one level up.

Standard library only; frames are plain lists so every record stays JSON.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .fiducials import ARUCO_DICTS, Fiducial

SCHEMA = "bam_aruco/1"
ATTR_GROUP = "bam_aruco"          # Fusion attribute group, both sides of the waist
PATH_SEP = "+"                    # occurrence path separator, as fullPathName
KEY_SEP = "|"                     # "<relative path>|<tag key>"
INTERNAL_UNITS = "cm"             # what the Fusion API speaks

DEFAULT_DICTIONARY = "DICT_4X4_50"
DEFAULT_QUIET_MODULES = 1.0
# Three 0.2 mm layers: the shallowest inlay a 0.4 mm-nozzle printer lays down
# as a solid colour rather than a tint over the layer below.
DEFAULT_DEPTH_M = 0.0006
DEFAULT_TILE_THICKNESS_M = 0.002
# OPTIONAL growth of the black cells (clamped to the black square), so two
# cells that touch only at a corner overlap instead of meeting on an edge.
# Zach, 2026-10-07: "Growing cells is an interesting feature. by default
# please keep it off though ... give the user the option." So TagSpec.grow_m
# defaults to 0 and this is only the value the option switches on: 10 um,
# two orders of magnitude below what a printer resolves.
CELL_OVERLAP_M = 0.00001

UNIT_TO_M = {"mm": 0.001, "cm": 0.01, "m": 1.0, "in": 0.0254, "ft": 0.3048}

# Fields a copy may override. Anything that changes the SHAPE of the part
# (size, depth, quiet zone) is the component's, full stop -- Zach: "You
# cannot override the geometry of course.. but you can override like the IDs".
OVERRIDABLE = ("marker_id", "dictionary")


# ------------------------------------------------------------------ helpers

def dictionary_bits(name: str) -> Optional[int]:
    """Inner grid side n for a dictionary name, without loading any table."""
    m = re.match(r"DICT_(\d)X\1_", name or "")
    if m:
        return int(m.group(1))
    if name == "DICT_ARUCO_ORIGINAL":
        return 5
    m = re.match(r"DICT_APRILTAG_(\d\d)h", name or "")
    if m:
        return int(round(math.sqrt(int(m.group(1)))))
    return None


def pattern_key(dictionary: str, marker_id: int) -> str:
    """Two tags with the same key are the same printed square."""
    m = re.match(r"DICT_(\d)X\1_", dictionary or "")
    family = f"aruco{m.group(1)}x{m.group(1)}" if m else (dictionary or "?")
    return f"{family}#{int(marker_id)}"


def split_path(path: str) -> List[str]:
    return [p for p in (path or "").split(PATH_SEP) if p]


def join_path(parts: Sequence[str]) -> str:
    return PATH_SEP.join(parts)


def slot_key(rel_path: Sequence[str] | str, tag_key: str) -> str:
    rel = rel_path if isinstance(rel_path, str) else join_path(rel_path)
    return f"{rel}{KEY_SEP}{tag_key}"


# ---------------------------------------------------------------- 4x4 math

def mat_identity() -> List[float]:
    return [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


def mat_mul(a: Sequence[float], b: Sequence[float]) -> List[float]:
    return [sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4))
            for r in range(4) for c in range(4)]


def mat_from_frame(origin, x_axis, y_axis, z_axis) -> List[float]:
    """Columns are the axes -- the same convention as Fusion's Matrix3D."""
    return [x_axis[0], y_axis[0], z_axis[0], origin[0],
            x_axis[1], y_axis[1], z_axis[1], origin[1],
            x_axis[2], y_axis[2], z_axis[2], origin[2],
            0.0, 0.0, 0.0, 1.0]


def mat_scale_translation(m: Sequence[float], s: float) -> List[float]:
    out = list(m)
    for i in (3, 7, 11):
        out[i] *= s
    return out


def rpy_from_matrix(m: Sequence[float]) -> Tuple[float, float, float]:
    """URDF rpy (R = Rz(yaw) Ry(pitch) Rx(roll)) from a row-major 4x4."""
    r00, r10, r20 = m[0], m[4], m[8]
    r21, r22 = m[9], m[10]
    pitch = math.atan2(-r20, math.hypot(r00, r10))
    if abs(math.cos(pitch)) < 1e-9:          # gimbal lock: fold yaw into roll
        roll = math.atan2(-m[6], m[5])
        yaw = 0.0
    else:
        roll = math.atan2(r21, r22)
        yaw = math.atan2(r10, r00)
    return (roll, pitch, yaw)


# ------------------------------------------------------------------ records

LAYOUTS = ("single", "grid", "charuco")
# Defaults for a board drawn on a face: OpenCV's tutorial boards use a gap of
# about a fifth of the marker for grids and a marker of about 3/4 of the
# square for ChArUco; both are recorded per tag, so these only seed a dialog.
DEFAULT_GRID_SEPARATION_RATIO = 0.2
DEFAULT_CHARUCO_MARKER_RATIO = 0.75


@dataclass
class TagSpec:
    """Which marker(s), and how big. Lengths in METRES.

    layout "single"   one marker; `marker_id` is its id.
           "grid"     an OpenCV GridBoard of cols x rows markers, `separation_m`
                      apart; ids are `marker_id`, `marker_id + 1`, ... in
                      OpenCV's own order (aruco_board.ids).
           "charuco"  a CharucoBoard of cols x rows SQUARES of
                      `square_length_m`; markers sit in the white squares.

    `marker_length_m` is a marker's black square side INCLUDING its
    one-module black border -- the number `estimatePoseSingleMarkers`
    multiplies by. The tile (what you cut or print) is the pattern's extent
    plus a white quiet zone of `quiet_modules` modules on every side.

    A per-copy override of `marker_id` therefore moves a board's whole id
    BLOCK, which is the only thing a serial number can mean for a board.
    """
    dictionary: str = DEFAULT_DICTIONARY
    marker_id: int = 0
    marker_length_m: float = 0.03
    quiet_modules: float = DEFAULT_QUIET_MODULES
    depth_m: float = DEFAULT_DEPTH_M
    layout: str = "single"
    # How the black sits (Zach, 2026-10-07: control over the thickness of
    # the black part). `depth_m` goes INTO the face (an inlay, flush with
    # it); `raise_m` stands ON the face. Both are geometry, so they belong
    # to the component and are never overridden per copy.
    raise_m: float = 0.0
    # The black's top this far BELOW the face (2026-10-08, ArUco Extrude's
    # Offset / Height: a black extruded below the white top sits at the
    # bottom of an open pocket). The black then spans [-depth, -recess];
    # raise_m is 0. Absent / 0 for every tag made before.
    recess_m: float = 0.0
    # Grow each black cell by this much (0 = exact cells, the default).
    grow_m: float = 0.0
    rows: int = 1
    cols: int = 1
    separation_m: float = 0.0
    square_length_m: float = 0.0
    legacy: bool = False

    @property
    def bits(self) -> int:
        return dictionary_bits(self.dictionary) or 4

    @property
    def module_m(self) -> float:
        return self.marker_length_m / (self.bits + 2)

    @property
    def black_thickness_m(self) -> float:
        return self.depth_m + self.raise_m - self.recess_m

    @property
    def black_top_m(self) -> float:
        """The black's top over the face: +raise, or -recess."""
        return self.raise_m - self.recess_m

    @property
    def marker_count(self) -> int:
        if self.layout == "grid":
            return max(1, self.rows) * max(1, self.cols)
        if self.layout == "charuco":
            return (max(1, self.rows) * max(1, self.cols)) // 2
        return 1

    @property
    def ids(self) -> List[int]:
        return [int(self.marker_id) + k for k in range(self.marker_count)]

    @property
    def patterns(self) -> List[str]:
        return [pattern_key(self.dictionary, i) for i in self.ids]

    @property
    def extent_m(self) -> Tuple[float, float]:
        """The black pattern's width x height (no quiet zone)."""
        L = self.marker_length_m
        if self.layout == "grid":
            return (self.cols * L + (self.cols - 1) * self.separation_m,
                    self.rows * L + (self.rows - 1) * self.separation_m)
        if self.layout == "charuco":
            return (self.cols * self.square_length_m, self.rows * self.square_length_m)
        return (L, L)

    @property
    def tile_size_m(self) -> Tuple[float, float]:
        w, h = self.extent_m
        q = 2.0 * self.quiet_modules * self.module_m
        return (w + q, h + q)

    @property
    def tile_side_m(self) -> float:
        return max(self.tile_size_m)

    @property
    def pattern(self) -> str:
        """The first pattern -- an id for the whole target in messages."""
        return pattern_key(self.dictionary, self.marker_id)

    def label(self) -> str:
        mm = self.marker_length_m * 1000
        if self.layout == "grid":
            return (f"Grid {self.cols}x{self.rows} {self.dictionary} "
                    f"#{self.ids[0]}-{self.ids[-1]} · {mm:.1f} mm")
        if self.layout == "charuco":
            return (f"ChArUco {self.cols}x{self.rows} {self.dictionary} "
                    f"#{self.ids[0]}-{self.ids[-1]} · sq "
                    f"{self.square_length_m * 1000:.1f} mm")
        return f"{self.dictionary} #{self.marker_id} · {mm:.1f} mm"

    def to_dict(self) -> dict:
        out = {"dictionary": self.dictionary, "marker_id": int(self.marker_id),
               "marker_length_m": self.marker_length_m,
               "quiet_modules": self.quiet_modules, "depth_m": self.depth_m,
               "layout": self.layout}
        if self.raise_m:
            out["raise_m"] = self.raise_m
        if self.recess_m:
            out["recess_m"] = self.recess_m
        if self.grow_m:
            out["grow_m"] = self.grow_m
        if self.layout != "single":
            out.update(rows=int(self.rows), cols=int(self.cols), ids=self.ids)
        if self.layout == "grid":
            out["separation_m"] = self.separation_m
        if self.layout == "charuco":
            out["square_length_m"] = self.square_length_m
            out["legacy"] = bool(self.legacy)
        return out

    @classmethod
    def from_dict(cls, data: dict, warn: Callable[[str], None],
                  where: str) -> Optional["TagSpec"]:
        if not isinstance(data, dict):
            warn(f"{where}: tag spec is not an object — ignored")
            return None
        spec = cls()
        d = data.get("dictionary", spec.dictionary)
        if d not in ARUCO_DICTS:
            warn(f"{where}: unknown dictionary {d!r} — tag ignored "
                 f"(known: {', '.join(sorted(ARUCO_DICTS))})")
            return None
        spec.dictionary = d
        layout = data.get("layout", "single")
        if layout not in LAYOUTS:
            warn(f"{where}: unknown layout {layout!r} — tag ignored "
                 f"(known: {', '.join(LAYOUTS)})")
            return None
        spec.layout = layout
        try:
            spec.marker_id = int(data.get("marker_id", 0))
            spec.marker_length_m = float(data.get("marker_length_m",
                                                  spec.marker_length_m))
            spec.quiet_modules = float(data.get("quiet_modules",
                                                spec.quiet_modules))
            spec.depth_m = float(data.get("depth_m", spec.depth_m))
            spec.raise_m = float(data.get("raise_m", 0.0))
            spec.recess_m = float(data.get("recess_m", 0.0))
            spec.grow_m = float(data.get("grow_m", 0.0))
            spec.rows = int(data.get("rows", 1))
            spec.cols = int(data.get("cols", 1))
            spec.separation_m = float(data.get("separation_m", 0.0))
            spec.square_length_m = float(data.get("square_length_m", 0.0))
            spec.legacy = bool(data.get("legacy", False))
        except (TypeError, ValueError):
            warn(f"{where}: tag spec has a non-numeric field — ignored")
            return None
        if spec.marker_length_m <= 0:
            warn(f"{where}: marker_length_m must be > 0 — tag ignored")
            return None
        if (spec.depth_m < 0 or spec.raise_m < 0 or spec.recess_m < 0
                or spec.black_thickness_m <= 0 or (spec.raise_m and spec.recess_m)):
            warn(f"{where}: the black layer needs depth_m >= 0, raise_m >= 0, "
                 "recess_m >= 0 (not with raise_m) and some thickness — tag ignored")
            return None
        if layout == "charuco" and not (0 < spec.marker_length_m < spec.square_length_m):
            warn(f"{where}: a ChArUco marker must be smaller than its square "
                 f"({spec.marker_length_m} vs {spec.square_length_m} m) — tag ignored")
            return None
        if layout != "single" and (spec.rows < 1 or spec.cols < 1
                                   or spec.marker_count < 1):
            warn(f"{where}: a {layout} board needs rows and cols >= 1 — tag ignored")
            return None
        return spec


@dataclass
class TagDef:
    """One ArUco feature as it is modelled in one component.

    `frame` is the marker frame in the COMPONENT's coordinates, Fusion
    internal units (cm), as four lists: origin = centre of the black square
    on the tile's top surface, x = image right, y = image up (towards row 0),
    z = out of the face, towards the camera. The black cells occupy
    z in [-depth, 0], flush with the surface.
    """
    key: str                         # stable within its component
    component: str                   # component name; "" = root component
    spec: TagSpec
    origin: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    x_axis: List[float] = field(default_factory=lambda: [1.0, 0.0, 0.0])
    y_axis: List[float] = field(default_factory=lambda: [0.0, 1.0, 0.0])
    z_axis: List[float] = field(default_factory=lambda: [0.0, 0.0, 1.0])
    units: str = INTERNAL_UNITS
    name: str = ""                   # the timeline group / feature name
    entity_token: Optional[str] = None
    # Set on a VARIANT component the add-in generated: which slot of which
    # template it realises. Its own feature is then not an extra physical
    # tag, it is that slot's answer, and resolve() skips it as a source.
    variant_of: Optional[dict] = None
    # The optical frame as Fusion holds it: the tag's Joint Origin, in this
    # component's frame and `units`. Filled by attach_joint_origins(); when
    # absent the frame is derived from the record (frame x detector_frame).
    optical: Optional[List[float]] = None
    optical_source: str = "record"
    # A record written on 2026-10-08 with a chosen "Frame" (removed the next
    # day: the Joint Origin is OpenCV's frame, always) until the add-in
    # rewrites it: its Joint Origin still sits at that frame, and this --
    # its stored "frame_offset" (chosen -> OpenCV's, metres) -- takes it back
    # to OpenCV's. Never written.
    old_frame_offset: Optional[List[float]] = None

    def frame_matrix(self) -> List[float]:
        return mat_from_frame(self.origin, self.x_axis, self.y_axis, self.z_axis)

    def record_optical(self) -> List[float]:
        """frame x detector_frame, in this record's units."""
        D = list(detector_frame(self.spec))
        k = 1.0 / UNIT_TO_M.get(self.units, 0.01)          # metres -> units
        for i in (3, 7, 11):
            D[i] *= k
        return mat_mul(self.frame_matrix(), D)

    def optical_matrix(self) -> List[float]:
        return self.optical if self.optical is not None else self.record_optical()

    def to_dict(self) -> dict:
        out = {"key": self.key, "component": self.component,
               "spec": self.spec.to_dict(), "origin": list(self.origin),
               "x_axis": list(self.x_axis), "y_axis": list(self.y_axis),
               "z_axis": list(self.z_axis), "units": self.units}
        if self.name:
            out["name"] = self.name
        if self.entity_token:
            out["entity_token"] = self.entity_token
        if self.variant_of:
            out["variant_of"] = dict(self.variant_of)
        return out

    @classmethod
    def from_dict(cls, data: dict, warn: Callable[[str], None],
                  where: str = "tag") -> Optional["TagDef"]:
        if not isinstance(data, dict) or not data.get("key"):
            warn(f"{where}: tag record without a key — ignored")
            return None
        where = f"{where} '{data.get('component', '')}{KEY_SEP}{data['key']}'"
        spec = TagSpec.from_dict(data.get("spec") or {}, warn, where)
        if spec is None:
            return None
        rec = cls(key=str(data["key"]), component=str(data.get("component", "")),
                  spec=spec, units=str(data.get("units", INTERNAL_UNITS)),
                  name=str(data.get("name", "")),
                  entity_token=data.get("entity_token"),
                  variant_of=data.get("variant_of") or None)
        for attr in ("origin", "x_axis", "y_axis", "z_axis"):
            v = data.get(attr)
            if v is None:
                continue
            if (not isinstance(v, (list, tuple)) or len(v) != 3
                    or not all(isinstance(c, (int, float)) for c in v)):
                warn(f"{where}: {attr} is not three numbers — tag ignored")
                return None
            setattr(rec, attr, [float(c) for c in v])
        if rec.units not in UNIT_TO_M:
            warn(f"{where}: unknown units {rec.units!r} — tag ignored")
            return None
        if data.get("frame") is not None or data.get("frame_offset") is not None:
            rec.old_frame_offset = _rigid_or_none(data.get("frame_offset"))
            warn(f"{where}: has a Frame chosen on 2026-10-08 (removed) — read as OpenCV's "
                 "frame; edit the tag (or export from the ArUco add-in) to drop it")
        return rec


def _rigid_or_none(m) -> Optional[List[float]]:
    """16 numbers that are a rigid row-major 4x4, else None."""
    try:
        v = [float(c) for c in m]
    except (TypeError, ValueError):
        return None
    if len(v) != 16 or any(not math.isfinite(c) for c in v):
        return None
    if max(abs(a - b) for a, b in zip(v[12:], (0.0, 0.0, 0.0, 1.0))) > 1e-9:
        return None
    cols = [(v[0], v[4], v[8]), (v[1], v[5], v[9]), (v[2], v[6], v[10])]
    for i in range(3):
        for j in range(3):
            d = sum(a * b for a, b in zip(cols[i], cols[j]))
            if abs(d - (1.0 if i == j else 0.0)) > 1e-6:
                return None
    return v


@dataclass
class OverrideRecord:
    """Per-copy values authored on one native occurrence.

    owner       component the occurrence lives in ("" = root component)
    occurrence  the occurrence's own name there, e.g. "Actuator:3"
    values      {"<path below occurrence>|<tag key>": {"marker_id": 17, ...}}
    """
    owner: str
    occurrence: str
    values: Dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"owner": self.owner, "occurrence": self.occurrence,
                "values": {k: dict(v) for k, v in sorted(self.values.items())}}

    @classmethod
    def from_dict(cls, data: dict, warn: Callable[[str], None]) -> Optional["OverrideRecord"]:
        if not isinstance(data, dict) or not data.get("occurrence"):
            warn("override record without an occurrence — ignored")
            return None
        values = data.get("values") or {}
        if not isinstance(values, dict):
            warn(f"override on '{data.get('occurrence')}': values is not an "
                 "object — ignored")
            return None
        clean: Dict[str, dict] = {}
        for k, v in values.items():
            if KEY_SEP not in str(k) or not isinstance(v, dict):
                warn(f"override on '{data['occurrence']}': key {k!r} is not "
                     "'<path>|<tag key>' -> {...} — ignored")
                continue
            bad = sorted(set(v) - set(OVERRIDABLE))
            if bad:
                warn(f"override on '{data['occurrence']}' [{k}]: {bad} cannot "
                     "be overridden per copy (geometry belongs to the "
                     "component) — those fields ignored")
            kept = {f: v[f] for f in OVERRIDABLE if f in v}
            if kept:
                clean[str(k)] = kept
        return cls(owner=str(data.get("owner", "")),
                   occurrence=str(data["occurrence"]), values=clean)


@dataclass
class Layer:
    """One opinion applied while resolving one instance."""
    level: str          # "model", or the owner path the override was read at
    owner: str          # component that holds it ("" root; "" for model too)
    occurrence: str     # native occurrence it is stored on ("" for model)
    key: str            # the slot key it was stored under ("" for model)
    values: dict


@dataclass
class TagInstance:
    """One physical tag in the design: one tag on one occurrence path."""
    path: str                        # occurrence path of the tag's component; "" = root
    tag: TagDef
    spec: TagSpec                    # resolved
    layers: List[Layer] = field(default_factory=list)
    world: Optional[List[float]] = None   # 4x4 row-major, design units
    # the frame a detector reports (OpenCV's), composed through the copy
    optical_world: Optional[List[float]] = None
    hidden: bool = False             # the host occurrence is not drawn

    @property
    def name(self) -> str:
        return f"{self.path or '(root)'}{KEY_SEP}{self.tag.key}"

    @property
    def winning(self) -> Layer:
        return self.layers[-1]

    @property
    def needs_variant(self) -> bool:
        """The geometry at this path encodes something else -> a variant part."""
        return (self.spec.marker_id != self.tag.spec.marker_id
                or self.spec.dictionary != self.tag.spec.dictionary)

    @property
    def owner_path(self) -> str:
        """Root-path of the component that owns the winning override.

        "" means the root design; "Arm:1" means "inside the component Arm,
        as reached through Arm:1". A variant tag part lives there."""
        return self.winning.level if self.winning.level != "model" else self.path

    def to_dict(self) -> dict:
        out = {"name": self.name, "path": self.path, "tag_key": self.tag.key,
               "component": self.tag.component, "spec": self.spec.to_dict(),
               "pattern": self.spec.pattern, "modelled": self.tag.spec.to_dict(),
               "needs_variant": self.needs_variant, "hidden": self.hidden,
               "source": [{"level": l.level, "owner": l.owner,
                           "occurrence": l.occurrence, "key": l.key,
                           "values": l.values} for l in self.layers]}
        if self.world is not None:
            out["world"] = list(self.world)
        if self.optical_world is not None:
            out["optical_world"] = list(self.optical_world)
            out["optical_source"] = self.tag.optical_source
        return out


@dataclass
class Resolution:
    instances: List[TagInstance] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def duplicates(self, include_hidden: bool = False) -> Dict[str, List[TagInstance]]:
        """pattern -> instances sharing it (only patterns seen twice or more)."""
        groups: Dict[str, List[TagInstance]] = {}
        for inst in self.instances:
            if inst.hidden and not include_hidden:
                continue
            for pat in inst.spec.patterns:
                groups.setdefault(pat, []).append(inst)
        return {k: v for k, v in groups.items() if len(v) > 1}

    def used_ids(self, dictionary: str) -> set:
        fam = pattern_key(dictionary, 0).split("#")[0]
        return {mid for i in self.instances
                if i.spec.pattern.split("#")[0] == fam for mid in i.spec.ids}

    def to_dict(self) -> dict:
        dups = self.duplicates()
        return {"schema": SCHEMA,
                "instances": [i.to_dict() for i in self.instances],
                "duplicates": {k: [i.name for i in v] for k, v in sorted(dups.items())},
                "warnings": list(self.warnings)}


# ---------------------------------------------------------------- resolving

@dataclass
class OccurrenceRef:
    """The minimum of the occurrence tree resolve() needs."""
    path: str
    component: str
    transform: Optional[List[float]] = None     # WORLD, design units
    visible: bool = True


def validate_spec(spec: TagSpec, where: str, warn: Callable[[str], None]) -> bool:
    cap = ARUCO_DICTS.get(spec.dictionary)
    if cap is None:
        warn(f"{where}: unknown dictionary {spec.dictionary!r}")
        return False
    last = spec.ids[-1]
    if spec.marker_id < 0 or last >= cap:
        what = (f"id {spec.marker_id} is" if spec.layout == "single"
                else f"ids {spec.marker_id}..{last} are")
        warn(f"{where}: {what} outside {spec.dictionary} (0..{cap - 1}) — "
             "that marker does not exist")
        return False
    return True


def resolve(tags: Iterable[TagDef], overrides: Iterable[OverrideRecord],
            occurrences: Iterable[OccurrenceRef], root_component: str = "",
            design_units: str = "cm") -> Resolution:
    """Every tag on every occurrence path, with its effective spec.

    Pure. `occurrences` is the whole tree in root context (every level).
    Layers are applied innermost first, so the last layer -- the outermost
    assembly that said anything -- wins.
    """
    res = Resolution()
    warn = res.warnings.append
    occs = list(occurrences)
    comp_of = {o.path: o.component for o in occs}
    # A tag is hidden when its HOST is: the tag occurrence's own light bulb
    # belongs to the add-in, which switches a template off for one path
    # when a variant part takes its place -- that copy still has a tag.
    visible = {o.path: o.visible for o in occs}
    by_comp: Dict[str, List[TagDef]] = {}
    for t in tags:
        if t.variant_of:
            continue
        comp = "" if t.component in ("", root_component) else t.component
        by_comp.setdefault(comp, []).append(t)
    table: Dict[Tuple[str, str], Dict[str, dict]] = {}
    for o in overrides:
        owner = "" if o.owner in ("", root_component) else o.owner
        table.setdefault((owner, o.occurrence), {}).update(o.values)
    used_keys = set()

    hosts: List[Tuple[str, Optional[OccurrenceRef]]] = [("", None)]
    hosts += [(o.path, o) for o in occs]
    for path, occ in hosts:
        comp = "" if occ is None else occ.component
        for tag in by_comp.get(comp, []):
            parts = split_path(path)
            spec = replace(tag.spec)
            layers = [Layer("model", "", "", "", tag.spec.to_dict())]
            for k in range(len(parts) - 1, -1, -1):
                owner = "" if k == 0 else comp_of.get(join_path(parts[:k]), "?")
                key = slot_key(parts[k + 1:], tag.key)
                vals = table.get((owner, parts[k]), {}).get(key)
                if not vals:
                    continue
                used_keys.add((owner, parts[k], key))
                spec = replace(spec, **{f: (int(v) if f == "marker_id" else v)
                                        for f, v in vals.items()})
                layers.append(Layer(join_path(parts[:k]), owner, parts[k],
                                    key, dict(vals)))
            host = join_path(parts[:-1])
            inst = TagInstance(path=path, tag=tag, spec=spec, layers=layers,
                               hidden=bool(host) and not visible.get(host, True))
            validate_spec(spec, f"tag {inst.name}", warn)
            scale = UNIT_TO_M.get(tag.units, 0.01) / UNIT_TO_M.get(design_units, 0.01)
            local = mat_scale_translation(tag.frame_matrix(), scale)
            optical = mat_scale_translation(tag.optical_matrix(), scale)
            if occ is None:
                inst.world, inst.optical_world = local, optical
            elif occ.transform and len(occ.transform) == 16:
                inst.world = mat_mul(occ.transform, local)
                inst.optical_world = mat_mul(occ.transform, optical)
            res.instances.append(inst)

    for (owner, occ_name), values in sorted(table.items()):
        for key in sorted(values):
            if (owner, occ_name, key) not in used_keys:
                warn(f"override on '{owner or '(root)'}' / '{occ_name}' "
                     f"[{key}] matches no tag below that occurrence — stale "
                     "(renamed occurrence or deleted tag?)")
    res.instances.sort(key=lambda i: (i.path, i.tag.key))
    return res


def describe_duplicates(res: Resolution) -> List[str]:
    out = []
    seen: Dict[Tuple[str, ...], List[str]] = {}
    for pattern, group in sorted(res.duplicates().items()):
        seen.setdefault(tuple(i.name for i in group), []).append(pattern)
    for names, patterns in seen.items():
        what = (f"the same marker {patterns[0]}" if len(patterns) == 1
                else f"{len(patterns)} of the same markers ({patterns[0]} ...)")
        out.append(f"{len(names)} tags print {what}: {', '.join(names)} "
                   "— a camera cannot tell them apart")
    return out


# --------------------------------------------------------- serial numbering

@dataclass
class PlannedOverride:
    owner: str            # component that will hold it ("" root)
    owner_path: str       # one root-path that reaches that component (for UI)
    occurrence: str
    key: str
    marker_id: int
    instance: str         # the instance this was planned for (first one seen)


def _common_depth(a: List[str], b: List[str]) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def plan_serials(tags: List[TagDef], overrides: List[OverrideRecord],
                 occurrences: List[OccurrenceRef], root_component: str = "",
                 max_rounds: int = 16) -> Tuple[List[PlannedOverride], Resolution]:
    """Give every colliding copy its own id, at the deepest level that works.

    For six actuators in Arm:1 the copies first differ inside Arm, so the
    serials are written in Arm, on Actuator:2..6 -- they then travel with the
    arm. If the robot holds two arms, the second pass finds Arm:1's and
    Arm:2's copies still colliding (one component, one set of serials) and
    writes the second arm's at the root. Each pass re-resolves, because an
    override written into a shared component lands on every copy of it.

    The first instance of each colliding group (path order) keeps its id;
    new ids are the lowest free ones in that pattern family.
    """
    overrides = [OverrideRecord(o.owner, o.occurrence, dict(o.values))
                 for o in overrides]
    planned: List[PlannedOverride] = []
    comp_of = {o.path: o.component for o in occurrences}
    res = resolve(tags, overrides, occurrences, root_component)
    for _ in range(max_rounds):
        dups = res.duplicates()
        if not dups:
            break
        progressed = False
        # ids taken, per pattern family, shared by every group in this pass:
        # two groups both short of an id must not both be handed the same one
        used_by_family: Dict[str, set] = {}
        handled: set = set()
        for pattern, group in sorted(dups.items()):
            # one board collides on many patterns at once; plan it once
            group = sorted({id(i): i for i in group}.values(), key=lambda i: i.path)
            sig = tuple(i.name for i in group)
            if sig in handled:
                continue
            handled.add(sig)
            paths = [split_path(i.path) for i in group]
            # deepest level at which two members still share a prefix
            depth = max(_common_depth(paths[a], paths[b])
                        for a in range(len(paths)) for b in range(a + 1, len(paths)))
            prefix = None
            cluster = []
            for inst, p in zip(group, paths):
                if len(p) <= depth:
                    continue
                if prefix is None:
                    for other in paths:
                        if other is not p and _common_depth(p, other) == depth:
                            prefix = p[:depth]
                            break
                if prefix is not None and p[:depth] == prefix:
                    cluster.append((inst, p))
            if len(cluster) < 2:
                continue
            family = pattern.split("#")[0]
            if family not in used_by_family:
                used_by_family[family] = res.used_ids(group[0].spec.dictionary)
            used = used_by_family[family]
            cap = ARUCO_DICTS.get(group[0].spec.dictionary, 0)
            owner = "" if depth == 0 else comp_of.get(join_path(prefix), "?")
            for inst, p in cluster[1:]:
                n = inst.spec.marker_count
                new_id = next((s0 for s0 in range(cap - n + 1)
                               if not any(s0 + k in used for k in range(n))), None)
                if new_id is None:
                    res.warnings.append(
                        f"{inst.spec.dictionary} is full ({cap} ids) — "
                        f"{inst.name} cannot get its own block of {n} ids; use "
                        "a bigger dictionary")
                    continue
                used.update(range(new_id, new_id + n))
                key = slot_key(p[depth + 1:], inst.tag.key)
                occ_name = p[depth]
                rec = next((o for o in overrides
                            if o.owner == owner and o.occurrence == occ_name), None)
                if rec is None:
                    rec = OverrideRecord(owner, occ_name, {})
                    overrides.append(rec)
                rec.values.setdefault(key, {})["marker_id"] = new_id
                planned = [x for x in planned
                           if not (x.owner == owner and x.occurrence == occ_name
                                   and x.key == key)]
                planned.append(PlannedOverride(owner, join_path(prefix), occ_name,
                                               key, new_id, inst.name))
                progressed = True
        if not progressed:
            break
        res = resolve(tags, overrides, occurrences, root_component)
    return planned, res


def apply_planned(overrides: List[OverrideRecord],
                  planned: List[PlannedOverride]) -> List[OverrideRecord]:
    out = [OverrideRecord(o.owner, o.occurrence, dict(o.values)) for o in overrides]
    for p in planned:
        rec = next((o for o in out if o.owner == p.owner
                    and o.occurrence == p.occurrence), None)
        if rec is None:
            rec = OverrideRecord(p.owner, p.occurrence, {})
            out.append(rec)
        rec.values.setdefault(p.key, {})["marker_id"] = p.marker_id
    return out


# -------------------------------------------------------------- suggestions

def suggest(n_tags: int, *, marker_length_m: Optional[float] = None,
            max_distance_m: float = 1.5, image_width_px: int = 1920,
            hfov_deg: float = 70.0, min_module_px: float = 5.0,
            min_print_module_m: float = 0.0012) -> dict:
    """A dictionary and a size, with the arithmetic shown.

    Smaller dictionaries have more bits of distance between their markers,
    so the advice is the smallest family that holds the tags with headroom
    for serials; then the size that keeps one module at least
    `min_module_px` pixels wide at `max_distance_m` for a pinhole camera,
    and at least `min_print_module_m` (three 0.4 mm lines) to print.
    """
    need = max(1, int(n_tags))
    headroom = max(need * 2, need + 10)
    order = ["DICT_4X4_50", "DICT_4X4_100", "DICT_5X5_100", "DICT_5X5_250",
             "DICT_6X6_250", "DICT_6X6_1000", "DICT_7X7_1000"]
    pick = next((d for d in order if ARUCO_DICTS[d] >= headroom), order[-1])
    bits = dictionary_bits(pick) or 4
    f_px = (image_width_px / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)
    module_for_camera = min_module_px * max_distance_m / f_px
    module = max(module_for_camera, min_print_module_m)
    length = module * (bits + 2)
    out = {"dictionary": pick, "capacity": ARUCO_DICTS[pick], "tags": need,
           "min_marker_length_m": round(length, 4),
           "why": (f"{need} tag(s), headroom {headroom} -> {pick}; a module "
                   f"must be >= {module_for_camera * 1000:.2f} mm to span "
                   f"{min_module_px:g} px at {max_distance_m:g} m "
                   f"({image_width_px} px, {hfov_deg:g} deg HFOV) and >= "
                   f"{min_print_module_m * 1000:.1f} mm to print -> marker "
                   f">= {length * 1000:.1f} mm")}
    if marker_length_m:
        m = marker_length_m / (bits + 2)
        out["max_distance_m"] = round(m * f_px / min_module_px, 2)
        out["module_m"] = m
    return out


def fit_square(face_w_m: float, face_h_m: float, dictionary: str,
               quiet_modules: float = DEFAULT_QUIET_MODULES) -> float:
    """The marker_length that fills a w x h face with its quiet zone."""
    n = (dictionary_bits(dictionary) or 4) + 2
    side = min(face_w_m, face_h_m)
    return side * n / (n + 2.0 * quiet_modules)


def fit_board(face_w_m: float, face_h_m: float, spec: "TagSpec") -> "TagSpec":
    """Size `spec` (layout, rows, cols, dictionary, quiet) to fill a face.

    Returns a copy with marker_length_m (and separation_m / square_length_m)
    set from the default ratios: the largest target that fits with its
    quiet zone.
    """
    n = spec.bits + 2
    q = 2.0 * spec.quiet_modules / n           # quiet zone, in marker lengths
    out = replace(spec)
    if spec.layout == "grid":
        r = DEFAULT_GRID_SEPARATION_RATIO
        L = min(face_w_m / (spec.cols + (spec.cols - 1) * r + q),
                face_h_m / (spec.rows + (spec.rows - 1) * r + q))
        out.marker_length_m, out.separation_m = L, L * r
    elif spec.layout == "charuco":
        m = DEFAULT_CHARUCO_MARKER_RATIO
        sq = min(face_w_m / (spec.cols + q * m), face_h_m / (spec.rows + q * m))
        out.square_length_m, out.marker_length_m = sq, sq * m
    else:
        out.marker_length_m = fit_square(face_w_m, face_h_m, spec.dictionary,
                                         spec.quiet_modules)
    return out


def tile_aspect(spec: "TagSpec") -> float:
    """Width / height of the tile (pattern + quiet zone) for this layout."""
    n = spec.bits + 2
    q = 2.0 * spec.quiet_modules / n               # quiet zone in marker lengths
    if spec.layout == "grid":
        r = DEFAULT_GRID_SEPARATION_RATIO
        return (spec.cols + (spec.cols - 1) * r + q) / (spec.rows + (spec.rows - 1) * r + q)
    if spec.layout == "charuco":
        m = DEFAULT_CHARUCO_MARKER_RATIO
        return (spec.cols + q * m) / (spec.rows + q * m)
    return 1.0


def _row_intervals(loops, y):
    """x-intervals inside the region (even-odd over all loops) on the line y."""
    xs = []
    for loop in loops:
        n = len(loop)
        for i in range(n):
            (x0, y0), (x1, y1) = loop[i], loop[(i + 1) % n]
            if (y0 <= y < y1) or (y1 <= y < y0):
                xs.append(x0 + (y - y0) * (x1 - x0) / (y1 - y0))
    xs.sort()
    return [(xs[i], xs[i + 1]) for i in range(0, len(xs) - 1, 2)]


def _covered(intervals, a, b):
    return any(lo <= a and b <= hi for lo, hi in intervals)


def largest_rect(loops: List[List[Tuple[float, float]]], aspect: float = 1.0,
                 angles_deg: Sequence[float] = tuple(range(0, 90, 5)),
                 cells: int = 120) -> Optional[dict]:
    """The largest rectangle of width/height `aspect` inside a planar region.

    Zach, 2026-10-07: click a face "and it does some heuristics to place the
    mark as best it can. Perhaps it basically tries to find the largest
    rectangle that will fit uninterrupted". `loops` are closed polygons in
    the face's own 2D frame (outer boundary AND holes; even-odd decides
    inside), any unit. For each trial angle the region is rotated, cut into
    rows, and each row's inside-intervals are found by scanline at the row's
    top, centre and bottom; a cell counts only if all three cover it -- so the
    answer never crosses a hole or an edge by more than the sampling allows.
    The largest square of cells (stretched by `aspect`) wins; ties keep the
    first angle, i.e. the one aligned with the face's own x axis.

    Returns {"cx", "cy", "w", "h", "angle_deg"} in the input frame, or None.
    """
    pts = [p for loop in loops for p in loop]
    if len(pts) < 3:
        return None
    best = None
    for a in angles_deg:
        t = math.radians(a)
        c, s = math.cos(t), math.sin(t)
        rot = [[(x * c + y * s, -x * s + y * c) for x, y in loop] for loop in loops]
        xs = [p[0] for loop in rot for p in loop]
        ys = [p[1] for loop in rot for p in loop]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        span = max((x1 - x0) / aspect, y1 - y0)
        dy = span / cells
        dx = dy * aspect
        nx, ny = int((x1 - x0) / dx) + 1, int((y1 - y0) / dy) + 1
        grid = []
        for j in range(ny):
            yb, yt = y0 + j * dy, y0 + (j + 1) * dy
            lines = [_row_intervals(rot, yb + 1e-9 * dy), _row_intervals(rot, (yb + yt) / 2),
                     _row_intervals(rot, yt - 1e-9 * dy)]
            row = []
            for i in range(nx):
                xa, xb = x0 + i * dx, x0 + (i + 1) * dx
                row.append(all(_covered(iv, xa, xb) for iv in lines))
            grid.append(row)
        # largest square of True cells (classic DP)
        dp = [[0] * nx for _ in range(ny)]
        k_best, at = 0, None
        for j in range(ny):
            for i in range(nx):
                if grid[j][i]:
                    dp[j][i] = 1 if i == 0 or j == 0 else 1 + min(
                        dp[j - 1][i], dp[j][i - 1], dp[j - 1][i - 1])
                    if dp[j][i] > k_best:
                        k_best, at = dp[j][i], (i, j)
        if not k_best:
            continue
        i, j = at
        cxr = x0 + (i + 1 - k_best / 2.0) * dx
        cyr = y0 + (j + 1 - k_best / 2.0) * dy
        w, h = k_best * dx, k_best * dy
        if best is None or w * h > best["w"] * best["h"] * (1 + 1e-9):
            best = {"cx": cxr * c - cyr * s, "cy": cxr * s + cyr * c, "w": w, "h": h,
                    "angle_deg": a}
    return best


def board_spec(spec: "TagSpec"):
    """TagSpec -> aruco_board.BoardSpec (the geometry module's own record)."""
    from . import aruco_board
    return aruco_board.BoardSpec(
        layout=spec.layout, dictionary=spec.dictionary, first_id=int(spec.marker_id),
        marker_length_m=spec.marker_length_m, rows=int(spec.rows), cols=int(spec.cols),
        separation_m=spec.separation_m, square_length_m=spec.square_length_m,
        legacy=bool(spec.legacy))


def detector_frame(spec: "TagSpec") -> List[float]:
    """Pose of the frame OpenCV reports for this target, in the tag frame.

    The tag frame is the pattern's centre (+y = image up, +z = out of the
    face). A single marker's OpenCV frame IS that frame; a board's is
    wherever OpenCV puts its board origin, which aruco_board measured
    against cv2 rather than assumed. A raised black layer lifts it by
    `raise_m` along +z.
    """
    if spec.layout == "single":
        m = mat_identity()
    else:
        from . import aruco_board
        m = list(aruco_board.opencv_frame(board_spec(spec)))
    # A raised black layer puts the pattern's corners ON its top, raise_m
    # out of the face: that is the plane a detector measures.
    m[11] += spec.raise_m
    return m


# ----------------------------------------------------------------- geometry

def cell_boxes(spec: TagSpec, grid: Optional[List[List[int]]] = None,
               rects: Optional[List[Tuple[int, int, int, int]]] = None,
               overlap_m: Optional[float] = None) -> List[Tuple[float, float, float, float]]:
    """Black regions as (x0, y0, x1, y1) in METRES, marker frame.

    Origin = centre of the black square, +y = image up. `rects` are merged
    black rectangles in module units with a top-left origin (what
    `aruco_dict.black_rects` returns); when absent they are derived from
    `grid` (1 = black) one run per row. Each box is grown by `overlap_m`
    (default: the spec's own `grow_m`, which is 0 unless asked for) and
    clamped to the black square, so the marker's side stays exact and
    corner-touching cells share area instead of an edge.
    """
    if overlap_m is None:
        overlap_m = spec.grow_m
    if spec.layout != "single" and grid is None and rects is None:
        from . import aruco_board  # lazy: board geometry lives with the tables
        half_w, half_h = spec.extent_m[0] / 2.0, spec.extent_m[1] / 2.0
        return [(max(-half_w, x0 - overlap_m), max(-half_h, y0 - overlap_m),
                 min(half_w, x1 + overlap_m), min(half_h, y1 + overlap_m))
                for (x0, y0, x1, y1) in aruco_board.black_rects_m(board_spec(spec))]
    n = spec.bits + 2
    if rects is None:
        if grid is None:
            from . import aruco_dict  # lazy: the table is ~100 kB
            rects = aruco_dict.black_rects(spec.dictionary, spec.marker_id)
        else:
            rects = []
            for r, row in enumerate(grid):
                c = 0
                while c < len(row):
                    if row[c]:
                        start = c
                        while c < len(row) and row[c]:
                            c += 1
                        rects.append((start, r, c - start, 1))
                    else:
                        c += 1
    mod = spec.module_m
    half = spec.marker_length_m / 2.0
    out = []
    for (cx, cy, w, h) in rects:
        x0 = (cx - n / 2.0) * mod
        x1 = (cx + w - n / 2.0) * mod
        y1 = (n / 2.0 - cy) * mod
        y0 = (n / 2.0 - cy - h) * mod
        out.append((max(-half, x0 - overlap_m), max(-half, y0 - overlap_m),
                    min(half, x1 + overlap_m), min(half, y1 + overlap_m)))
    return out


# ------------------------------------------------------- export / fiducials

def tags_from_payload(raw: Optional[dict], warn: Callable[[str], None]
                      ) -> Tuple[List[TagDef], List[OverrideRecord]]:
    """payload["aruco"] -> records. Absent or empty is not an error."""
    if not raw:
        return [], []
    if not isinstance(raw, dict):
        warn("payload 'aruco' is not an object — tags ignored")
        return [], []
    if raw.get("schema") not in (None, SCHEMA):
        warn(f"payload 'aruco' schema {raw.get('schema')!r} is not {SCHEMA} "
             "— read best-effort")
    tags = [t for t in (TagDef.from_dict(d, warn) for d in raw.get("tags") or [])
            if t is not None]
    ovs = [o for o in (OverrideRecord.from_dict(d, warn)
                       for d in raw.get("overrides") or []) if o is not None]
    for w in raw.get("warnings") or []:
        warn(f"[fusion] {w}")
    return tags, ovs


def attach_joint_origins(tags: List[TagDef], joint_origins, design_units: str,
                         warn: Callable[[str], None],
                         tol_m: float = 1e-5, tol_rad: float = 1e-4) -> int:
    """Give each tag the optical frame of its Joint Origin "aruco <key>".

    Zach, 2026-10-07: insert a Joint Origin at each tag "with the correct
    orientation ... cadexporter can then automatically create the frame based
    on that frame". The add-in puts one there (aruco_tags.ensure_joint_origin)
    and the exporter harvests every Joint Origin anyway (bam_joint_origins),
    so the frame a camera is told about is the one the designer can SEE in
    Fusion. It is built on the tag's face, so it follows the face when the
    design changes upstream -- and the black cells (a base feature) do not.
    A disagreement between the two is therefore reported, not averaged: it
    means the pattern is stale and the tag wants rebuilding.
    """
    if not joint_origins:
        return 0
    by_key = {}
    for rec in joint_origins:
        if isinstance(rec, dict) and rec.get("origin") and rec.get("x_axis"):
            by_key[(rec.get("component") or "", rec.get("name") or "")] = rec
    hit = 0
    for tag in tags:
        rec = by_key.get((tag.component, f"aruco {tag.key}"))
        if rec is None:
            continue
        k = UNIT_TO_M.get(design_units, 0.01) / UNIT_TO_M.get(tag.units, 0.01)
        origin = [v * k for v in rec["origin"]]
        x, y, z = rec["x_axis"], rec.get("y_axis"), rec.get("z_axis")
        if y is None or z is None:
            continue
        jo = mat_from_frame(origin, x, y, z)
        if tag.old_frame_offset is not None:
            # a record from 2026-10-08's Frame: its Joint Origin sits there;
            # its frame_offset takes it to OpenCV's (TagDef.old_frame_offset)
            jo = mat_mul(jo, mat_scale_translation(tag.old_frame_offset,
                                                   1.0 / UNIT_TO_M.get(tag.units, 0.01)))
        ref = tag.record_optical()
        d_m = math.sqrt(sum((jo[i] - ref[i]) ** 2 for i in (3, 7, 11))) \
            * UNIT_TO_M.get(tag.units, 0.01)
        ang = max(math.acos(max(-1.0, min(1.0, sum(jo[r * 4 + c] * ref[r * 4 + c]
                                                    for r in range(3)))))
                  for c in range(3))
        if d_m > tol_m or ang > tol_rad:
            warn(f"tag {tag.component or '(root)'}|{tag.key}: its Joint Origin is "
                 f"{d_m * 1000:.3f} mm / {math.degrees(ang):.3f} deg from where the "
                 "pattern was built — the face moved after tagging; rebuild the tag "
                 "(ArUco Edit -> OK) so the ink and the frame agree again")
        tag.optical, tag.optical_source = jo, "joint_origin"
        hit += 1
    return hit


def resolve_for_description(cd, raw: Optional[dict],
                            warn: Callable[[str], None],
                            joint_origins=None) -> Resolution:
    """CadDescription + payload["aruco"] -> per-copy tags with world poses."""
    tags, ovs = tags_from_payload(raw, warn)
    attach_joint_origins(tags, joint_origins, getattr(cd, "units", "cm") or "cm", warn)
    if not tags:
        for o in ovs:
            warn(f"override on '{o.owner or '(root)'}' / '{o.occurrence}' "
                 "but the design has no ArUco tags — stale")
        return Resolution()
    occs = [OccurrenceRef(o.path, o.component, o.transform, o.is_visible)
            for o in getattr(cd, "occurrences", [])]
    res = resolve(tags, ovs, occs, getattr(cd, "root_name", ""),
                  getattr(cd, "units", "cm") or "cm")
    for w in res.warnings:
        warn(w)
    for line in describe_duplicates(res):
        warn(line)
    return res


def instance_fiducials(res: Resolution, ld, design_units: str,
                       warn: Callable[[str], None]) -> List[Fiducial]:
    """Each visible tag -> a `Fiducial` whose frame sits on its link.

    The link frames this exporter writes are world-aligned at q = 0 (every
    joint origin carries rpy 0), so the tag's pose in its link is its world
    pose translated by the link's world origin. The board's visual box is
    switched off: the pattern is modelled geometry and already in the
    link's meshes; a sticker box on top of it would z-fight the cells.
    """
    out: List[Fiducial] = []
    to_m = UNIT_TO_M.get(design_units, 0.01)
    taken = set()
    for inst in res.instances:
        if inst.hidden or (inst.world is None and inst.optical_world is None):
            continue
        link = ld.link_for_occurrence(inst.path) if inst.path else None
        if link is None and not inst.path:
            link = ld.links.get(ld.root) if ld.root else None
        if link is None:
            warn(f"tag {inst.name}: its occurrence reached no link — "
                 "no marker frame emitted")
            continue
        origin = getattr(link, "frame_origin", None) or [0.0, 0.0, 0.0]
        # the frame a detector reports, not the pattern's centre -- from the
        # tag's Joint Origin when the export carried it
        w = inst.optical_world or mat_mul(inst.world, detector_frame(inst.spec))
        xyz = ((w[3] - origin[0]) * to_m, (w[7] - origin[1]) * to_m,
               (w[11] - origin[2]) * to_m)
        base_name = (f"aruco_{inst.spec.marker_id}" if inst.spec.layout == "single"
                     else f"{inst.spec.layout}_{inst.spec.marker_id}")
        name = base_name
        k = 2
        while name in taken:
            name = f"{base_name}_{k}"
            k += 1
        taken.add(name)
        sp = inst.spec
        fid = Fiducial(name=name, host=link.name, dictionary=sp.dictionary,
                       layout=sp.layout, ids=list(sp.ids),
                       marker_length_m=sp.marker_length_m,
                       separation_m=(sp.separation_m if sp.layout == "grid" else
                                     max(0.0, sp.square_length_m - sp.marker_length_m)),
                       rows=sp.rows, cols=sp.cols,
                       origin_xyz=xyz, origin_rpy=rpy_from_matrix(w),
                       source="cad_tag")
        fid.visual = False
        out.append(fid)
    return out


def short_part_name(path: str) -> str:
    """What to write on the marker's top border: the part the tag sits on.

    The tag's own occurrence is dropped (it is the tag, not the part), and the
    two nearest owners are kept, so six copies across two arms still read
    apart: "Arm:2/Actuator:3". The full path stays on the label under the cut.
    """
    parts = split_path(path)
    if len(parts) > 1:
        parts = parts[:-1]
    return "/".join(parts[-2:]) if parts else "(root)"


def print_items(res: Resolution, *, include_hidden: bool = False):
    """One PrintItem per distinct physical tag, labelled with where it goes."""
    from .print_sheet import PrintItem  # lazy: only the export path needs it
    items = []
    for inst in res.instances:
        if inst.hidden and not include_hidden:
            continue
        where = inst.path or "(root)"
        sp = inst.spec
        kw = {}
        if sp.layout != "single":
            kw = dict(layout=sp.layout, rows=sp.rows, cols=sp.cols,
                      separation_m=sp.separation_m,
                      square_length_m=sp.square_length_m, legacy=sp.legacy)
        items.append(PrintItem(
            key=inst.name, dictionary=sp.dictionary, marker_id=sp.marker_id,
            marker_length_m=sp.marker_length_m, quiet_modules=sp.quiet_modules,
            label_lines=[sp.label(), f"{where} · {inst.tag.key}"],
            top_label=short_part_name(inst.path), **kw))
    return items


# ------------------------------------------------------------------ sidecar

def marked_svg(spec: "TagSpec") -> str:
    """The marker SVG with the default marks (print_sheet's rules): the grey
    corner-0 triangle in the quiet zone and "#id" knocked out white in the
    bottom border row, cap height 0.5 module, shrunk to fit. Single markers;
    the plain SVG next to it stays exactly OpenCV's marker."""
    from . import aruco_dict
    q = spec.quiet_modules
    n = spec.bits + 2
    svg = aruco_dict.marker_svg(spec.dictionary, spec.marker_id, quiet_modules=q)
    text = "#%d" % spec.marker_id
    size = 0.5 / 0.718                                  # font size for a 0.5-module cap
    room = (n - 2) * 0.9
    est = 0.56 * size * len(text)                       # Helvetica digits ~0.556 em
    if est > room:
        size *= room / est
    cap = size * 0.718
    marks = (f'<path d="M{-q:g} {-q:g}h0.6l-0.6 0.6z" fill="#808080"/>'
             f'<text x="{n / 2:g}" y="{n - (1 - cap) / 2:.4f}" font-family="Helvetica, Arial, '
             f'sans-serif" font-size="{size:.4f}" fill="#fff" text-anchor="middle">{text}</text>')
    return svg.replace("</svg>", marks + "</svg>")


def _image_stem(spec: "TagSpec") -> str:
    d = spec.dictionary.replace("DICT_", "").lower()
    if spec.layout == "single":
        return f"{d}_{spec.marker_id}"
    return f"{spec.layout}_{spec.cols}x{spec.rows}_{d}_{spec.ids[0]}-{spec.ids[-1]}"


def sidecar_files(res: Resolution, *, design_name: str = "Design",
                  page: str = "letter", landscape: bool = False,
                  exported_at: str = "", margin_mm: float = 10.0) -> Dict[str, bytes]:
    """Everything the export writes beside the URDF, as {relpath: bytes}.

    Zach: "in the side car we also want to keep the image as well, with that
    ergonomic PDF export. that allows us to quickly just print it paper
    printer or 3D printer". The 3D-printable parts come out of Fusion (they
    are its bodies, holes and all); this is the paper half and the record:

        aruco/markers.json            every physical tag, its id, its source
        aruco/images/<dict>_<id>.png  1 module = 20 px, one quiet module
        aruco/images/<dict>_<id>.svg  vector, viewBox in modules
        aruco/print_<page>.pdf        true-size sheet, cut marks, ruler

    Pure: a caller writes the bytes. Hidden copies are left out of the sheet
    and the images, because nobody needs to print a tag that is not there.
    """
    from . import aruco_dict, print_sheet  # lazy: tables + PDF only on export
    out: Dict[str, bytes] = {}
    doc = res.to_dict()
    doc["design"] = design_name
    doc["exported_at"] = exported_at
    images = {}
    for inst in res.instances:
        if inst.hidden:
            continue
        stem = _image_stem(inst.spec)
        if stem in images:
            continue
        images[stem] = inst.spec
        sp = inst.spec
        if sp.layout == "single":
            out[f"aruco/images/{stem}.png"] = aruco_dict.marker_png(
                sp.dictionary, sp.marker_id, module_px=20,
                quiet_modules=max(1, int(round(sp.quiet_modules))))
            out[f"aruco/images/{stem}.svg"] = aruco_dict.marker_svg(
                sp.dictionary, sp.marker_id,
                quiet_modules=sp.quiet_modules).encode("utf-8")
            out[f"aruco/images/{stem}_marked.svg"] = marked_svg(sp).encode("utf-8")
        else:
            from . import aruco_board
            quiet = sp.quiet_modules * sp.module_m
            out[f"aruco/images/{stem}.png"] = aruco_board.board_png(
                board_spec(sp), px_per_m=20.0 / sp.module_m, quiet_m=quiet)
            out[f"aruco/images/{stem}.svg"] = aruco_board.board_svg(
                board_spec(sp), quiet_m=quiet).encode("utf-8")
    for row, inst in zip(doc["instances"], res.instances):
        row["image"] = None if inst.hidden else f"images/{_image_stem(inst.spec)}.png"
    # margin_mm: nothing is drawn closer than this to the paper edge -- the
    # printer's unprintable border (Zach: "a lot of printers cut off the edges")
    settings = print_sheet.SheetSettings(page=page, landscape=landscape,
                                         margin_mm=max(0.0, float(margin_mm)),
                                         title=f"{design_name} — ArUco tags")
    items = print_items(res)
    layout = print_sheet.layout_sheet(items, settings)
    doc["print_sheet"] = print_sheet.layout_summary(layout)
    out[f"aruco/print_{page}{'_landscape' if landscape else ''}.pdf"] = \
        print_sheet.render_pdf(items, settings)
    out["aruco/markers.json"] = (json.dumps(doc, indent=2, sort_keys=True,
                                            default=str) + "\n").encode("utf-8")
    return out
