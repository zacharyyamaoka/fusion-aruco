"""ArUco boards as exported data — identity, metric size, pose, host link.

### Why this is not "export the decal image"

What a detector needs from CAD is four facts, and a picture is none of them:

    dictionary + ids      which marker this is
    marker_length_m       the BLACK SQUARE's side — the number
                          estimatePoseSingleMarkers() multiplies by
    pose                  6-DOF relative to the link frame
    host link             what it is rigidly attached to

The image is *derivable* from (dictionary, ids, layout): OpenCV regenerates it
byte-exactly, forever. So the image is an output, the record is the source —
the same split as meshes vs the payload, for the same reason.

### The numbers this module was calibrated against

Zach's own boards, from `create_grid_board.ipynb` and the `dummy_bam_fb_macro`
xacro (see [[MOC - ArUco Markers]]). His rule is *marker ~= 0.7 x tube width,
then separation = margin = (tube - marker)/2*, and it closes EXACTLY:

    1.5in tube 38.1mm, marker 27.1, gap 5.5  ->  38.1 x 168.5 outer  (built, x4 faces)
    2.0in tube 50.8mm, marker 36.0, gap 7.4  ->  50.8 x 224.4 outer  (specified)

Both match the recorded outer dimensions to 0.1 mm, which is what makes
`geometry_closes` a real gate rather than a plausible one — it is calibrated on
hardware that exists.

### The defect this module exists to catch

From the same MOC, about the four stickers already on L2:

    "All four faces share one texture, and both boards use IDs 0-4 — so the
     four faces are mutually indistinguishable and a detector cannot tell
     which one it sees."

That is a shipped bug in a real robot, found by reading, not by a test. It is
`ids_unique` below, and it is the reason this module validates rather than just
serialises.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# id capacity per OpenCV predefined dictionary. An id at or above the capacity
# is not "a bit unlucky" — the dictionary simply has no such marker, and what
# happens next is silent nonsense.
ARUCO_DICTS: Dict[str, int] = {
    "DICT_4X4_50": 50, "DICT_4X4_100": 100, "DICT_4X4_250": 250,
    "DICT_4X4_1000": 1000,
    "DICT_5X5_50": 50, "DICT_5X5_100": 100, "DICT_5X5_250": 250,
    "DICT_5X5_1000": 1000,
    "DICT_6X6_50": 50, "DICT_6X6_100": 100, "DICT_6X6_250": 250,
    "DICT_6X6_1000": 1000,
    "DICT_7X7_50": 50, "DICT_7X7_100": 100, "DICT_7X7_250": 250,
    "DICT_7X7_1000": 1000,
    "DICT_ARUCO_ORIGINAL": 1024,
    "DICT_APRILTAG_16h5": 30, "DICT_APRILTAG_25h9": 35,
    "DICT_APRILTAG_36h10": 2320, "DICT_APRILTAG_36h11": 587,
}

# Zach's own `ArucoDictEnum` shorthand, so a board named ...dict3... imports
# without him renaming anything. Extend as more turn up; an unknown alias is a
# warning, never a guess.
DICT_ALIASES = {"dict3": "DICT_4X4_1000"}

# A sticker is not a plate. 0.1 mm is Zach's own number from the xacro
# (thickness="0.0001"), and it is deliberately not zero: zero-extent geometry
# is degenerate in every engine that has to normalise a face.
DEFAULT_THICKNESS_M = 0.0001

LAYOUTS = ("single", "grid", "charuco")


@dataclass
class Fiducial:
    """One printed board, on one planar face, on one link.

    All lengths are METRES. `marker_length_m` is the black square's outer
    side — NOT the sticker's extent, NOT the square plus quiet zone. That
    distinction is the single most common way an ArUco pose estimate goes
    quietly wrong: pass the sticker width to estimatePoseSingleMarkers and
    every range is scaled by the margin ratio, with no error anywhere.
    """
    name: str
    host: str                                    # occurrence path or link name
    dictionary: str = "DICT_4X4_1000"
    layout: str = "grid"                         # single | grid | charuco
    ids: List[int] = field(default_factory=list)
    marker_length_m: float = 0.0
    separation_m: float = 0.0                    # gap BETWEEN markers
    margin_m: Optional[float] = None             # border; defaults to separation
    rows: int = 1
    cols: int = 1
    outer_w_m: Optional[float] = None            # declared sticker extent
    outer_h_m: Optional[float] = None
    thickness_m: float = DEFAULT_THICKNESS_M
    # 6-DOF in the host link's frame. Kept as plain numbers so the record
    # stays JSON — the transform math lives in the writers.
    origin_xyz: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    origin_rpy: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    source: str = "sidecar"                      # sidecar | decal | filename
    image: Optional[str] = None                  # relative path IF one was kept
    # --- how this record finds its decal in the design --------------------
    # Zach, 2026-08-20: "Instead of overloading the marker ... giving the
    # marker a more semantically meaningful name, and then the sidecar can
    # hold the full config. Overloading the name is clearly a bit of a hack."
    #
    # Exactly right, and the distinction is worth stating: the name is now an
    # opaque KEY, not an encoded RECORD. Same string either way, but a
    # different contract — a key can be renamed freely as long as both sides
    # move together, while an encoded name means a rename silently changes the
    # spec. Fusion preserves the decal name on insert (verified in his design:
    # "Artboard 32final"), so the key survives the trip through the CAD.
    # Pixel size of the generated image. With the physical extent this is what
    # makes the 1:1 check possible — a non-uniform scale is otherwise invisible
    # and turns every marker into a rectangle the pose solver still treats as
    # a square.
    image_px: Optional[Tuple[int, int]] = None
    decal_name: Optional[str] = None
    # entityToken is Fusion's own persistent id and outlives a rename, so it
    # is the stronger key of the two when it is available — the binding_survival
    # gate should prefer it and fall back to the name.
    entity_token: Optional[str] = None

    @property
    def effective_margin_m(self) -> float:
        return self.separation_m if self.margin_m is None else self.margin_m

    @property
    def marker_count(self) -> int:
        return max(0, self.rows) * max(0, self.cols)

    def computed_extent_m(self) -> Tuple[float, float]:
        """Width x height the board MUST occupy, from its own layout numbers.

        cols along width, rows along height — n markers, n-1 gaps between
        them, and a margin at each end.
        """
        margin = self.effective_margin_m
        width = (self.cols * self.marker_length_m
                 + max(0, self.cols - 1) * self.separation_m + 2 * margin)
        height = (self.rows * self.marker_length_m
                  + max(0, self.rows - 1) * self.separation_m + 2 * margin)
        return width, height

    def to_dict(self) -> dict:
        out = {
            "name": self.name, "host": self.host,
            "dictionary": self.dictionary, "layout": self.layout,
            "ids": list(self.ids),
            "marker_length_m": self.marker_length_m,
            "separation_m": self.separation_m,
            "rows": self.rows, "cols": self.cols,
            "thickness_m": self.thickness_m,
            "origin_xyz": list(self.origin_xyz),
            "origin_rpy": list(self.origin_rpy),
            "source": self.source,
        }
        if self.margin_m is not None:
            out["margin_m"] = self.margin_m
        for key, value in (("outer_w_m", self.outer_w_m),
                           ("outer_h_m", self.outer_h_m),
                           ("image", self.image),
                           ("decal_name", self.decal_name),
                           ("entity_token", self.entity_token)):
            if value is not None:
                out[key] = value
        if self.image_px is not None:
            out["image_px"] = list(self.image_px)
        return out

    def key(self) -> str:
        """What the sidecar is looked up by. entity_token survives a rename,
        so it wins when present; the decal name is the human-readable
        fallback; the record's own name is the last resort for boards that
        were never decals at all."""
        return self.entity_token or self.decal_name or self.name

    @classmethod
    def from_dict(cls, data: dict, warnings: List[str], where: str) -> Optional["Fiducial"]:
        if not isinstance(data, dict):
            warnings.append(f"{where}: expected an object — ignored")
            return None
        name = str(data.get("name") or where)
        host = data.get("host")
        if not isinstance(host, str) or not host.strip():
            warnings.append(f"{where}: needs a 'host' (occurrence path or link "
                            "name) — ignored")
            return None
        fid = cls(name=name, host=host.strip())

        raw_dict = str(data.get("dictionary", fid.dictionary))
        fid.dictionary = DICT_ALIASES.get(raw_dict, raw_dict)
        if fid.dictionary not in ARUCO_DICTS:
            warnings.append(f"{where}: dictionary '{raw_dict}' is not a known "
                            "OpenCV predefined dictionary — kept as declared, "
                            "id-range checking disabled")

        layout = str(data.get("layout", fid.layout)).lower()
        if layout in LAYOUTS:
            fid.layout = layout
        else:
            warnings.append(f"{where}: layout '{layout}' not one of "
                            f"{list(LAYOUTS)} — using '{fid.layout}'")

        ids = data.get("ids")
        if isinstance(ids, list):
            clean = [i for i in ids if isinstance(i, int) and not isinstance(i, bool)]
            if len(clean) != len(ids):
                warnings.append(f"{where}: non-integer ids dropped")
            fid.ids = clean
        elif ids is not None:
            warnings.append(f"{where}: ids must be a list of integers — ignored")

        for attr in ("marker_length_m", "separation_m", "margin_m",
                     "outer_w_m", "outer_h_m", "thickness_m"):
            if data.get(attr) is None:
                continue
            try:
                setattr(fid, attr, float(data[attr]))
            except (TypeError, ValueError):
                warnings.append(f"{where}: {attr} must be a number — ignored")
        for attr in ("rows", "cols"):
            if data.get(attr) is None:
                continue
            try:
                setattr(fid, attr, int(data[attr]))
            except (TypeError, ValueError):
                warnings.append(f"{where}: {attr} must be an integer — ignored")
        for attr, length in (("origin_xyz", 3), ("origin_rpy", 3)):
            v = data.get(attr)
            if v is None:
                continue
            if isinstance(v, list) and len(v) == length:
                try:
                    setattr(fid, attr, tuple(float(x) for x in v))
                    continue
                except (TypeError, ValueError):
                    pass
            warnings.append(f"{where}: {attr} must be {length} numbers — ignored")
        px = data.get("image_px")
        if px is not None:
            if isinstance(px, (list, tuple)) and len(px) == 2:
                try:
                    fid.image_px = (int(px[0]), int(px[1]))
                except (TypeError, ValueError):
                    warnings.append(f"{where}: image_px must be two integers — ignored")
            else:
                warnings.append(f"{where}: image_px must be [width, height] — ignored")
        for attr in ("image", "source", "decal_name", "entity_token"):
            if isinstance(data.get(attr), str):
                setattr(fid, attr, data[attr])
        if fid.decal_name is None and fid.source == "decal":
            warnings.append(f"{where}: source is 'decal' but no decal_name — "
                            "nothing links this record to a decal in the design")
        return fid


# --------------------------------------------------------------- the 70% rule


def board_for_tube(tube_width_m: float, marker_length_m: float,
                   count: int) -> Tuple[float, float, float]:
    """Zach's rule, as a function: separation = margin = (tube - marker)/2.

    Returns (separation, outer_width, outer_height). The point of the rule is
    that outer_width comes out EQUAL to the tube width, so a 1x5 strip sits
    flush on a square-tube face with no trimming — which is what makes it a
    sticker you can actually apply. Verified against both of his boards.
    """
    gap = (tube_width_m - marker_length_m) / 2.0
    width = marker_length_m + 2 * gap
    height = count * marker_length_m + max(0, count - 1) * gap + 2 * gap
    return gap, width, height


# ------------------------------------------------------------ filename import

_BOARD_RE = re.compile(
    r"grid_board_(?P<dict>[A-Za-z0-9_]+?)"
    r"_marker(?P<marker>[0-9p]+)mm"
    r"_num(?P<rows>\d+)x(?P<cols>\d+)"
    r"_outer(?P<w>[0-9p]+)x(?P<h>[0-9p]+)mm",
    re.IGNORECASE)


def _mm(token: str) -> float:
    return float(token.replace("p", "."))


def parse_board_filename(filename: str, host: str,
                         warnings: Optional[List[str]] = None) -> Optional[Fiducial]:
    """`grid_board_dict3_marker27p1mm_num1x5_outer38p1x168p5mm` -> Fiducial.

    His generator already encodes every number this module needs in the
    filename, so his existing boards import with nothing to re-enter by hand.
    That is the "adapt to the design" contract applied to his own conventions
    instead of to Fusion's.

    Note `num1x5` reads rows x cols in his schema, and the outer dimensions
    confirm it: 38.1 wide (one marker across) by 168.5 tall (five down).
    """
    warnings = warnings if warnings is not None else []
    match = _BOARD_RE.search(filename)
    if not match:
        warnings.append(f"'{filename}' does not match the grid_board filename "
                        "schema — declare this board in the sidecar instead")
        return None
    raw_dict = match.group("dict")
    marker_m = _mm(match.group("marker")) / 1000.0
    outer_w = _mm(match.group("w")) / 1000.0
    outer_h = _mm(match.group("h")) / 1000.0
    rows, cols = int(match.group("rows")), int(match.group("cols"))
    # separation is not in the name, but the rule recovers it exactly: the
    # outer width is one marker plus two margins.
    gap = (outer_w - marker_m) / 2.0
    return Fiducial(
        name=filename, host=host,
        dictionary=DICT_ALIASES.get(raw_dict, raw_dict),
        layout="grid" if rows * cols > 1 else "single",
        ids=list(range(rows * cols)),   # the generator's default — see ids_unique
        marker_length_m=marker_m, separation_m=gap, margin_m=gap,
        rows=max(rows, cols), cols=min(rows, cols),
        outer_w_m=outer_w, outer_h_m=outer_h,
        source="filename",
    )


# ------------------------------------------------------------------- the gates


def validate_fiducials(fiducials: List[Fiducial],
                       planar_hosts: Optional[Dict[str, bool]] = None,
                       known_hosts: Optional[List[str]] = None,
                       tolerance_m: float = 5e-5) -> List[str]:
    """Every way a board can be wrong that arithmetic can see.

    Returns warnings; an empty list means nothing here will silently mislead a
    detector. Ordered by how quietly each failure hurts, worst first.
    """
    warnings: List[str] = []

    # 1. ids_unique — the shipped defect. Two boards sharing an id makes the
    #    faces mutually indistinguishable, and the detector reports a pose for
    #    the WRONG face rather than failing.
    #
    #    Compared by HOST, not by name. The first version of this gate compared
    #    names and stayed silent on the exact case it was written for: his four
    #    L2 faces all carry the same texture FILE, so they share a name while
    #    sitting on four different faces. Sharing a name is normal here — the
    #    board is one printed design applied four times. Sharing a *face* is
    #    what makes ids ambiguous.
    seen: Dict[int, str] = {}
    reported = set()
    for fid in fiducials:
        for marker_id in fid.ids:
            other = seen.get(marker_id)
            if other is not None and other != fid.host:
                key = (marker_id, other, fid.host)
                if key not in reported:
                    reported.add(key)
                    warnings.append(
                        f"id {marker_id} appears on BOTH host '{other}' and host "
                        f"'{fid.host}' — a detector cannot tell these two apart, "
                        "and will report a pose for whichever it matches first. "
                        "Give each board its own id range "
                        "(fiducials.assign_id_ranges).")
            seen.setdefault(marker_id, fid.host)

    by_key = {}
    for fid in fiducials:
        by_key.setdefault((fid.name, fid.host), []).append(fid)
    for (name, host), group in sorted(by_key.items()):
        if len(group) > 1:
            warnings.append(f"{len(group)} fiducials share name '{name}' on host "
                            f"'{host}' — sidecar keys will collide")

    for fid in fiducials:
        where = f"fiducial '{fid.name}'"

        # 2. geometry_closes — declared extent vs what the layout requires.
        computed_w, computed_h = fid.computed_extent_m()
        for axis, declared, computed in (("width", fid.outer_w_m, computed_w),
                                         ("height", fid.outer_h_m, computed_h)):
            if declared is None:
                continue
            if abs(declared - computed) > tolerance_m:
                warnings.append(
                    f"{where}: declared {axis} {declared * 1000:.2f} mm but the "
                    f"layout needs {computed * 1000:.2f} mm "
                    f"({fid.rows}x{fid.cols} of {fid.marker_length_m * 1000:.2f} mm, "
                    f"gap {fid.separation_m * 1000:.2f}, margin "
                    f"{fid.effective_margin_m * 1000:.2f}) — the printed board "
                    "will not sit flush")

        # 3. marker_length must be positive and smaller than the sticker.
        if fid.marker_length_m <= 0:
            warnings.append(f"{where}: marker_length_m must be > 0 — it is the "
                            "number pose estimation multiplies by, so a zero "
                            "here scales every range to zero")
        elif fid.outer_w_m is not None and fid.marker_length_m >= fid.outer_w_m:
            warnings.append(
                f"{where}: marker_length_m ({fid.marker_length_m * 1000:.2f} mm) "
                f"is not smaller than the sticker width "
                f"({fid.outer_w_m * 1000:.2f} mm) — marker_length is the BLACK "
                "SQUARE, not the printed extent; passing the extent to "
                "estimatePoseSingleMarkers scales every range by the margin ratio")

        # 4. id count vs layout, and ids within the dictionary.
        if fid.ids and len(fid.ids) != fid.marker_count:
            warnings.append(f"{where}: {len(fid.ids)} id(s) for a "
                            f"{fid.rows}x{fid.cols} board ({fid.marker_count} "
                            "markers)")
        if len(set(fid.ids)) != len(fid.ids):
            warnings.append(f"{where}: repeats an id within itself")
        capacity = ARUCO_DICTS.get(fid.dictionary)
        if capacity is not None:
            over = [i for i in fid.ids if i < 0 or i >= capacity]
            if over:
                warnings.append(f"{where}: id(s) {over} are outside "
                                f"{fid.dictionary} (0..{capacity - 1})")

        # 5. 1:1 — the image's pixel aspect must equal the physical aspect.
        #    Zach: "for aruco markers likely we want to enforce 1:1 or
        #    something". Enforced as: no NON-UNIFORM scale. A marker stretched
        #    on one axis is no longer square, and neither the corner refinement
        #    nor the pose solve says a word about it — they just return a
        #    confidently wrong pose.
        if (fid.image_px and fid.outer_w_m and fid.outer_h_m
                and fid.image_px[1] and fid.outer_h_m):
            image_aspect = fid.image_px[0] / float(fid.image_px[1])
            physical_aspect = fid.outer_w_m / float(fid.outer_h_m)
            if physical_aspect > 0 and abs(image_aspect - physical_aspect) / physical_aspect > 0.005:
                warnings.append(
                    f"{where}: image is {fid.image_px[0]}x{fid.image_px[1]} px "
                    f"(aspect {image_aspect:.4f}) but the board is "
                    f"{fid.outer_w_m * 1000:.2f}x{fid.outer_h_m * 1000:.2f} mm "
                    f"(aspect {physical_aspect:.4f}) — the decal is scaled "
                    f"non-uniformly by {image_aspect / physical_aspect:.4f}x, "
                    "so the markers are rectangles and every pose estimate is "
                    "biased with no error reported")

        # 6. planarity — an ArUco marker on a curved face is not a marker.
        if planar_hosts is not None and planar_hosts.get(fid.host) is False:
            warnings.append(
                f"{where}: host '{fid.host}' is not planar. ArUco detection "
                "assumes a flat square; wrapping one around a curve degrades "
                "corner localisation and biases the pose. Put one flat board "
                "per face instead — which is what the 4x38.1 mm tube design "
                "already does.")

        if known_hosts is not None and fid.host not in known_hosts:
            warnings.append(f"{where}: host '{fid.host}' is not a link or "
                            "occurrence in this design — the board will not be "
                            "attached to anything")

        if fid.thickness_m <= 0:
            warnings.append(f"{where}: thickness_m must be > 0 (zero-extent "
                            "geometry is degenerate); 0.1 mm is the usual value")
    return warnings


def assign_id_ranges(fiducials: List[Fiducial], start: int = 0,
                     stride: Optional[int] = None) -> List[Fiducial]:
    """Hand each board its own contiguous id block, in order.

    The fix for `ids_unique`, and the thing his other repo already did by hand
    (`front_right_L2_top: [59..67]`) but which never crossed over. Mutates and
    returns the same objects so a caller can apply it in place.
    """
    cursor = start
    for fid in fiducials:
        count = fid.marker_count or len(fid.ids) or 1
        step = stride if stride is not None else count
        fid.ids = list(range(cursor, cursor + count))
        cursor += step
    return fiducials


# ---------------------------------------------------- the marker library

LIBRARY_SCHEMA = "bam_aruco_library/1"


def load_marker_library(data: dict, warnings: Optional[List[str]] = None) -> Dict[str, dict]:
    """The file Zach's marker generator should emit beside every image.

    His workflow, in his words: *"I can generate the markers from my marker
    app, and when it generates it should generate the marker, but also the
    config that was used to generate it. Then I place that marker art into
    Fusion360 ... then perhaps we can id the marker just by its name."*

    That is the right shape and it beats the filename schema, because the name
    becomes an opaque KEY rather than an encoded record. Concretely:

        {
          "schema": "bam_aruco_library/1",
          "generated_by": "<your marker app + version>",
          "markers": {
            "forearm_L2_top": {          <- the decal name in Fusion
              "dictionary": "DICT_4X4_1000",
              "layout": "grid", "rows": 5, "cols": 1,
              "ids": [0, 1, 2, 3, 4],
              "marker_length_m": 0.0271,
              "separation_m": 0.0055,
              "outer_w_m": 0.0381, "outer_h_m": 0.1685,
              "image_px": [450, 1990]
            }
          }
        }

    `image_px` is not decoration: with the physical extent it is what makes
    the 1:1 check possible, and a non-uniform scale is otherwise invisible.

    Returns {key: spec}; every problem lands in `warnings` and the entry is
    skipped, never guessed at.
    """
    warnings = warnings if warnings is not None else []
    if not isinstance(data, dict):
        warnings.append("marker library must be an object — ignored")
        return {}
    schema = data.get("schema")
    if schema != LIBRARY_SCHEMA:
        warnings.append(f"marker library schema '{schema}' is not "
                        f"'{LIBRARY_SCHEMA}' — reading it anyway, but the "
                        "generator and the exporter may disagree")
    markers = data.get("markers")
    if not isinstance(markers, dict):
        warnings.append("marker library has no 'markers' object — nothing loaded")
        return {}
    out = {}
    for key, spec in markers.items():
        if not isinstance(spec, dict):
            warnings.append(f"marker library['{key}']: expected an object — skipped")
            continue
        out[str(key)] = spec
    return out


def fiducials_from_decals(decals: List[dict], library: Dict[str, dict],
                          warnings: Optional[List[str]] = None) -> List[Fiducial]:
    """Join what the CAD knows (pose, host, planarity) to what the generator
    knows (dictionary, ids, metric size).

    Neither side can produce a usable marker alone, and that is the whole
    design: Fusion owns WHERE, the library owns WHAT. A decal with no library
    entry is not an error — it is a decorative decal, and saying so out loud
    is how the two pipelines stay separate.
    """
    warnings = warnings if warnings is not None else []
    out: List[Fiducial] = []
    by_name: List[Tuple[str, str]] = []
    for decal in decals:
        if not isinstance(decal, dict):
            continue
        name = str(decal.get("name") or "")
        token = decal.get("entity_token")
        spec = None
        matched_by = None
        if token and token in library:
            spec, matched_by = library[token], "entity_token"
        elif name and name in library:
            spec, matched_by = library[name], "name"
        if spec is None:
            warnings.append(
                f"decal '{name}' has no entry in the marker library — not "
                "exported as a fiducial. That is correct for a decorative "
                "decal; if it IS a marker, add it to the library under this "
                "exact name.")
            continue
        merged = dict(spec)
        merged["name"] = merged.get("name", name)
        merged["decal_name"] = name
        merged["source"] = "decal"
        if token:
            merged["entity_token"] = token
        for carried in ("host", "origin_xyz", "origin_rpy", "image_px",
                        "outer_w_m", "outer_h_m"):
            # The CAD wins on anything the CAD measures: a physical extent read
            # off the face is ground truth, the library's is what was intended.
            if decal.get(carried) is not None:
                merged.setdefault("_declared_" + carried, merged.get(carried))
                merged[carried] = decal[carried]
        fid = Fiducial.from_dict(merged, warnings, f"decal '{name}'")
        if fid is None:
            continue
        for carried in ("outer_w_m", "outer_h_m"):
            declared = merged.get("_declared_" + carried)
            actual = merged.get(carried)
            if (declared is not None and actual is not None
                    and abs(declared - actual) > 5e-5):
                warnings.append(
                    f"decal '{name}': the design places it at "
                    f"{actual * 1000:.2f} mm {carried[6:7]} but the library "
                    f"says it was generated at {declared * 1000:.2f} mm — the "
                    "marker is scaled in CAD, so every pose estimate using the "
                    "library's marker_length_m will be wrong by that ratio")
        if matched_by == "name" and token:
            by_name.append((name, token))
        out.append(fid)
    if by_name:
        # One line, not one per board: the generator cannot know an entityToken
        # (it does not exist until the decal is inserted), so matching by name
        # is the NORMAL first pass, not a fault. Worth saying once, because
        # writing the tokens back is what makes a later rename harmless.
        listed = ", ".join(f"{n} -> {t}" for n, t in by_name[:6])
        more = f" (+{len(by_name) - 6} more)" if len(by_name) > 6 else ""
        warnings.append(
            f"{len(by_name)} board(s) matched by name. Fusion assigns an "
            f"entityToken only once a decal exists, so this is expected on the "
            f"first pass — write these back into the library and a later "
            f"rename cannot break the link: {listed}{more}")
    return out
