"""Offline tests for bam_cad_core.aruco_autosize — P1 size first + P3 ranked list.

The expected numbers are docs/proposal_aruco_autosize.md §4 (P1 defaults on
the five test faces) and §6 (P3 ranked lists), which were computed by
docs/build_aruco_autosize_report.py. One deliberate deviation is pinned below:
the 100 x 80 ChArUco default is 26.5 / 19.5 mm (the doc's table says
26.0 / 19.5). The reference rounded the derived marker to the NEAREST step
(26.5 * 0.75 = 19.875 -> 20.0, which leaves < 1 module of white) and then
stepped the SQUARE down; here a marker derived from its square is a maximum
and rounds DOWN (19.5), so the square keeps its nearest value. 3 x 3 @
26.5 / 19.5 is also the doc's own P3 entry for 3 x 3 on that plate.

Run:  python tests/test_aruco_autosize.py
"""
from __future__ import annotations

import math
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "CADExporter"))

from bam_cad_core import aruco_autosize as A  # noqa: E402
from bam_cad_core import aruco_board  # noqa: E402
from bam_cad_core import markers as M  # noqa: E402
from bam_cad_core.markers import TagSpec  # noqa: E402

try:                                    # the notes carry "≥"; a cp1252 console must not crash
    sys.stdout.reconfigure(errors="backslashreplace")
except Exception:  # noqa: BLE001
    pass

PASS = 0
FAIL = []


def check(label, condition, detail=""):
    global PASS
    if condition:
        PASS += 1
        print(f"  ok  {label}")
    else:
        FAIL.append(label)
        print(f"FAIL  {label}  {detail}")


def raises(fn, exc=ValueError):
    try:
        fn()
    except exc:
        return True
    except Exception:  # noqa: BLE001
        return False
    return False


def mm(v):
    return v / 1000.0


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol


def on_grid(x_m):
    k = x_m / A.STEP_M
    return abs(k - round(k)) < 1e-6


def n_of(lay):
    return (M.dictionary_bits(lay.dictionary) or 4) + 2


def desc(lay):
    return "%s ok=%s notes=%s" % (lay.label(), lay.ok, lay.notes)


# ------------------------------------------------------------ invariants

def problems(lay, W, H, *, auto, margins=True):
    """Every hard invariant of the brief, for one layout in a W x H box (m).

    tile <= box; markers never overlap (grid gap >= 1 module, ChArUco marker
    <= S n/(n+2)); a grid has >= 2 markers; ChArUco >= 2 x 2; an ok layout
    passes aruco_board.check_spec; an ok AUTO layout is on the 0.5 mm grid
    and printable (module >= 1.2 mm)."""
    out = []
    if lay.marker_m <= 0:
        if lay.ok:
            out.append("an empty layout claims ok")
        return out
    n = n_of(lay)
    try:
        spec = lay.apply(TagSpec(dictionary=lay.dictionary, marker_id=0))
    except Exception as e:  # noqa: BLE001
        return ["apply raised %r" % e]
    tw, th = spec.tile_size_m
    if tw > W + 1e-9 or th > H + 1e-9:
        out.append("tile %.6f x %.6f > box %.6f x %.6f" % (tw, th, W, H))
    if not (close(tw, lay.tile_m[0], 1e-12) and close(th, lay.tile_m[1], 1e-12)):
        out.append("Layout.tile_m %r != TagSpec.tile_size_m %r" % (lay.tile_m, (tw, th)))
    L, S = lay.marker_m, lay.square_m
    if lay.kind == "single":
        if (lay.cols, lay.rows) != (1, 1):
            out.append("single is %dx%d" % (lay.cols, lay.rows))
    elif lay.kind == "grid":
        if lay.cols * lay.rows < 2:
            out.append("a grid with %d marker(s)" % (lay.cols * lay.rows))
        if not lay.separation_m > 0 or lay.separation_m < L / n - 1e-12:
            out.append("grid gap %.6f < one module %.6f" % (lay.separation_m, L / n))
    elif lay.kind == "charuco":
        if lay.cols < 2 or lay.rows < 2:
            out.append("charuco %dx%d < 2x2" % (lay.cols, lay.rows))
        if L > S * n / (n + 2.0) + 1e-12:
            out.append("charuco marker %.6f > S n/(n+2) = %.6f" % (L, S * n / (n + 2.0)))
        if spec.quiet_modules != 0.0:
            out.append("charuco spec keeps a quiet zone")
    else:
        out.append("unknown kind %r" % lay.kind)
    if margins:
        ew, eh = lay.extent_m
        need = lay.quiet_m if lay.kind != "charuco" else 0.0
        if not (close(lay.margins_m[0], (W - ew) / 2, 1e-6) and close(lay.margins_m[1], (H - eh) / 2, 1e-6)):
            out.append("margins %r are not the leftover" % (lay.margins_m,))
        if min(lay.margins_m) < need - 1e-7:
            out.append("margins %r < quiet zone %.6f" % (lay.margins_m, need))
    # markers never overlap: pairwise, on the board OpenCV is told about
    try:
        corners = aruco_board.marker_corners_m(M.board_spec(spec))
    except ValueError as e:
        corners = None
        if lay.ok:
            out.append("check_spec refuses an ok layout: %s" % e)
    if corners is not None:
        boxes = [(min(p[0] for p in c), min(p[1] for p in c), max(p[0] for p in c), max(p[1] for p in c))
                 for c in corners.values()]
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                if a[0] < b[2] - 1e-12 and b[0] < a[2] - 1e-12 and a[1] < b[3] - 1e-12 and b[1] < a[3] - 1e-12:
                    out.append("markers %d and %d overlap" % (i, j))
                    break
    if lay.ok:
        try:
            aruco_board.check_spec(M.board_spec(spec))
        except ValueError as e:
            out.append("check_spec: %s" % e)
        if auto:
            if lay.module_m < A.MIN_MODULE_M - 1e-12:
                out.append("auto layout module %.6f < 1.2 mm" % lay.module_m)
            if not on_grid(L) or (lay.kind == "charuco" and not on_grid(S)):
                out.append("auto size not on the 0.5 mm grid: %r / %r" % (L, S))
    return out


# ------------------------------------------------------------ the doc's faces

FACES = {"tile": (40.0, 40.0), "plate": (120.0, 70.0), "plate2": (100.0, 80.0),
         "tube15": (168.5, 38.1), "tube20": (224.4, 50.8)}


def face(key):
    w, h = FACES[key]
    return mm(w), mm(h)


def main():
    t_start = time.time()

    # ------------------------------------------------------------- §4 P1
    print("§4 P1 defaults on the five faces (doc table)")
    single = {"tile": 28.0, "plate": 49.0, "plate2": 56.0, "tube15": 26.5, "tube20": 35.5}
    for key, L in single.items():
        lay = A.default_layout("single", *face(key))
        check("single %s -> %.1f mm" % (key, L),
              lay.ok and lay.kind == "single" and close(lay.marker_m, mm(L)), desc(lay))
    # cols, rows, marker, gap, margin x, margin y (doc rounds margins to 0.1)
    grid = {"tile": (2, 2, 12.5, 2.5, 6.25, 6.25), "plate": (4, 2, 22.5, 4.5, 8.25, 10.25),
            "plate2": (3, 2, 25.5, 5.1, 6.65, 11.95), "tube15": (5, 1, 26.5, 5.3, 7.4, 5.8),
            "tube20": (5, 1, 35.5, 7.1, 9.25, 7.65)}
    for key, (c, r, L, gap, mx, my) in grid.items():
        lay = A.default_layout("grid", *face(key))
        check("grid %s -> %d x %d @ %.1f, gap %.1f, margins %.2f / %.2f" % (key, c, r, L, gap, mx, my),
              lay.ok and (lay.cols, lay.rows) == (c, r) and close(lay.marker_m, mm(L))
              and close(lay.separation_m, mm(gap)) and close(lay.margins_m[0], mm(mx), 1e-7)
              and close(lay.margins_m[1], mm(my), 1e-7) and not lay.notes, desc(lay))
    lay = A.default_layout("grid", *face("plate"))
    check("120 x 70 grid: 8 markers (ids 0-7)", lay.count == 8, lay.count)
    charuco = {"tile": (3, 3, 13.0, 9.5), "plate": (5, 3, 23.0, 17.0),
               "plate2": (3, 3, 26.5, 19.5),        # doc: 26.0 / 19.5 -- see the module docstring
               "tube15": (8, 2, 19.0, 14.0), "tube20": (8, 2, 25.0, 18.5)}
    for key, (c, r, S, L) in charuco.items():
        lay = A.default_layout("charuco", *face(key))
        check("charuco %s -> %d x %d @ %.1f / %.1f" % (key, c, r, S, L),
              lay.ok and (lay.cols, lay.rows) == (c, r) and close(lay.square_m, mm(S))
              and close(lay.marker_m, mm(L)), desc(lay))
    lay = A.default_layout("charuco", *face("plate"))
    check("120 x 70 ChArUco: 8 inner corners, no corner warning",
          lay.corners == 8 and not lay.notes, (lay.corners, lay.notes))
    lay = A.default_layout("charuco", *face("tube15"))
    check("38.1 tube ChArUco = his L2 (19 / 14): 7 inner corners, no warning",
          lay.label() == "8 × 2 @ 19.0 / 14.0 mm" and lay.corners == 7 and not lay.notes, desc(lay))
    lay = A.default_layout("charuco", mm(40), mm(25))
    check("a 2 x N board with < 4 inner corners is noted as weak for pose",
          lay.ok and lay.rows == 2 and any("inner corner" in t for t in lay.notes), desc(lay))
    check("labels", (A.default_layout("single", *face("tile")).label(),
                     A.default_layout("grid", *face("plate")).label(),
                     A.default_layout("charuco", *face("plate")).label())
          == ("single 28.0 mm", "4 × 2 @ 22.5 mm", "5 × 3 @ 23.0 / 17.0 mm"))
    for kind in ("single", "grid", "charuco"):
        for key in FACES:
            lay = A.default_layout(kind, *face(key))
            p = problems(lay, *face(key), auto=True)
            if p:
                check("%s %s default holds every invariant" % (kind, key), False, p)
    check("every §4 default holds every invariant", True)

    # ------------------------------------------------------------- §6 P3
    print("§6 P3 ranked lists (doc)")

    def sig(cs):
        return [(c.cols, c.rows, round(c.marker_m * 1000, 3)) for c in cs]

    cs = A.candidates("grid", *face("tube15"))
    check("38.1 tube grid: 4x1@28.5, 5x1@27, 6x1@22.5, 7x1@19.5, 8x1@17, 9x2@15, 10x2@13.5, 11x2@12.5",
          sig(cs) == [(4, 1, 28.5), (5, 1, 27.0), (6, 1, 22.5), (7, 1, 19.5), (8, 1, 17.0),
                      (9, 2, 15.0), (10, 2, 13.5), (11, 2, 12.5)], sig(cs))
    best = [c for c in cs if "best fill" in c.badges]
    check("...best fill 82 % is 9 x 2 @ 15.0, largest marker is the first",
          len(best) == 1 and best[0].label() == "9 × 2 @ 15.0 mm" and round(100 * best[0].fill) == 82
          and cs[0].badges[:1] == ["largest marker"], [(c.label(), c.badges) for c in cs])
    cs = A.candidates("grid", *face("plate"))
    check("120 x 70 grid: 2x1@47, 3x1@32, 3x2@27.5, 4x2@24, 5x2@19.5, 5x3@18.5, 6x3@16, 7x4@14",
          sig(cs) == [(2, 1, 47.0), (3, 1, 32.0), (3, 2, 27.5), (4, 2, 24.0), (5, 2, 19.5),
                      (5, 3, 18.5), (6, 3, 16.0), (7, 4, 14.0)], sig(cs))
    best = [c for c in cs if "best fill" in c.badges]
    check("...best fill 88 % is 7 x 4 @ 14.0",
          len(best) == 1 and best[0].label() == "7 × 4 @ 14.0 mm" and round(100 * best[0].fill) == 88)
    check("each badge given exactly once, within the returned list",
          all(sum(b in c.badges for c in cs) == 1 for b in ("largest marker", "most markers", "best fill")))
    full = A.candidates("grid", *face("tube15"), limit=None)
    check("unlimited list = the page's 14 entries, ending 16 x 3 @ 8.5 (most markers)",
          len(full) == 14 and full[-1].label() == "16 × 3 @ 8.5 mm" and "most markers" in full[-1].badges,
          [c.label() for c in full])
    full = A.candidates("grid", *face("plate"), limit=None)
    # 2026-10-08: past 50 markers a board is capped to what DICT_4X4_50 can
    # number (10 x 5 at 9.5 mm), so the list gains that last entry
    check("unlimited 120 x 70 list = 12 entries, ending 10 x 5 @ 9.5 (capped at 50 ids)",
          len(full) == 12 and full[-2].label() == "9 × 5 @ 10.5 mm"
          and full[-1].label() == "10 × 5 @ 9.5 mm", [c.label() for c in full])
    cs = A.candidates("charuco", *face("tile"))
    check("40 tile ChArUco: 2x2 20/15, 3x3 13/9.5, 4x4 10/7.5",
          [c.label() for c in cs] == ["2 × 2 @ 20.0 / 15.0 mm", "3 × 3 @ 13.0 / 9.5 mm",
                                      "4 × 4 @ 10.0 / 7.5 mm"], [c.label() for c in cs])
    cs = A.candidates("charuco", *face("tube15"))
    check("38.1 tube ChArUco starts 8x2 19/14, 9x2 18.5/13.5, 10x2 16.5/12",
          [c.label() for c in cs[:3]] == ["8 × 2 @ 19.0 / 14.0 mm", "9 × 2 @ 18.5 / 13.5 mm",
                                          "10 × 2 @ 16.5 / 12.0 mm"], [c.label() for c in cs])
    cs = A.candidates("charuco", *face("plate2"))
    check("100 x 80 ChArUco list holds 3 x 3 @ 26.5 / 19.5 (= the P1 default here)",
          "3 × 3 @ 26.5 / 19.5 mm" in [c.label() for c in cs], [c.label() for c in cs])
    cs = A.candidates("single", *face("tile"))
    check("single: one entry, the largest that fits (30.0 on the 40 tile)",
          [c.label() for c in cs] == ["single 30.0 mm"] and cs[0].badges == ["largest marker", "most markers",
                                                                             "best fill"], [c.label() for c in cs])
    check("a 4X4_50 list never offers more than 50 markers",
          all(c.count <= 50 for c in A.candidates("grid", mm(300), mm(300), limit=None)))

    # ------------------------------------------------------------- clamp
    print("clamp_to_fit: typed sizes")
    lay = A.clamp_to_fit("grid", mm(40), mm(40), mm(30))
    check("doc's chip: 'clamped: 30.0 mm does not fit, using 15.5 mm' (2 x 2 @ 15.5)",
          lay.ok and lay.notes[:1] == ["clamped: 30.0 mm does not fit, using 15.5 mm"]
          and lay.label() == "2 × 2 @ 15.5 mm", desc(lay))
    lay = A.clamp_to_fit("grid", *face("plate"), mm(22.5))
    ref = A.default_layout("grid", *face("plate"))
    check("a size that fits is unchanged, no note",
          lay.ok and not lay.notes and lay.label() == ref.label() and lay.margins_m == ref.margins_m, desc(lay))
    lay = A.clamp_to_fit("grid", *face("tube15"), mm(27.1))
    check("his built 27.1 mm board is honoured as typed: 5 x 1 @ 27.1",
          lay.ok and lay.label() == "5 × 1 @ 27.1 mm" and not lay.notes, desc(lay))
    lay = A.clamp_to_fit("grid", *face("tube20"), mm(36.0))
    check("his specified 36.0 mm board: 5 x 1 @ 36.0", lay.ok and lay.label() == "5 × 1 @ 36.0 mm", desc(lay))
    lay = A.clamp_to_fit("grid", *face("tube15"), mm(40.1))
    check("off-grid typed size steps to the grid below first (40.1 -> ... 28.5, 4 x 1)",
          lay.ok and lay.label() == "4 × 1 @ 28.5 mm"
          and lay.notes[0] == "clamped: 40.1 mm does not fit, using 28.5 mm", desc(lay))
    lay = A.clamp_to_fit("single", mm(40), mm(40), mm(35))
    check("single: 35 on a 40 tile -> 30.0 (the quiet zone must fit)",
          lay.ok and lay.label() == "single 30.0 mm" and lay.notes[0].startswith("clamped: 35.0 mm"), desc(lay))
    lay = A.clamp_to_fit("charuco", mm(40), mm(40), mm(22.5), mm(30))
    check("ChArUco: 30 / 22.5 on a 40 tile -> 20 / 15, square and marker in the chip",
          lay.ok and lay.label() == "2 × 2 @ 20.0 / 15.0 mm"
          and lay.notes[0] == "clamped: 30.0 / 22.5 mm does not fit, using 20.0 / 15.0 mm", desc(lay))
    lay = A.clamp_to_fit("charuco", mm(100), mm(100), mm(18), mm(20))
    check("ChArUco: a marker too big for its square shrinks the marker only",
          lay.ok and lay.label() == "5 × 5 @ 20.0 / 15.0 mm"
          and lay.notes[0].startswith("clamped: a 18.0 mm marker leaves < 1 module"), desc(lay))
    lay = A.clamp_to_fit("charuco", *face("plate"), None, mm(23))
    check("ChArUco checker typed, marker auto -> 23 / 17, no note",
          lay.ok and lay.label() == "5 × 3 @ 23.0 / 17.0 mm" and not lay.notes, desc(lay))
    lay = A.clamp_to_fit("charuco", *face("tube15"), mm(14))
    check("ChArUco marker typed, checker auto -> 19 / 14 (his L2)",
          lay.ok and lay.label() == "8 × 2 @ 19.0 / 14.0 mm", desc(lay))
    lay = A.clamp_to_fit("grid", mm(300), mm(300), mm(8))
    # 2026-10-08 (Zach: "no marker fits here ... 54 markers, 50 ids"): more
    # markers than the dictionary numbers -> the largest board it can number,
    # same size, same shape, centred, with a note -- never "nothing fits"
    check("too many markers for DICT_4X4_50: size kept, board capped at 50 ids, noted",
          lay.ok and close(lay.marker_m, mm(8)) and lay.count <= 50 and lay.count >= 48
          and any("has 50 ids" in t for t in lay.notes), desc(lay))
    lay = A.clamp_to_fit("grid", mm(300), mm(300), mm(8), dictionary="DICT_4X4_1000")
    check("...and fits with DICT_4X4_1000", lay.ok and lay.count > 50, desc(lay))
    lay = A.clamp_to_fit("single", mm(10), mm(10), mm(6))
    check("a typed sub-printable size that fits is honoured, with a module chip",
          lay.ok and close(lay.marker_m, mm(6)) and any(t.startswith("module 1.0 mm") for t in lay.notes),
          desc(lay))
    lay = A.clamp_to_fit("single", mm(8), mm(8), mm(20))
    check("nothing printable fits: largest that fits (6.0), ok False, one chip saying so",
          not lay.ok and close(lay.marker_m, mm(6)) and lay.notes[0].startswith("clamped")
          and any(t.startswith("nothing printable fits") for t in lay.notes)
          and not any(t.startswith("module ") for t in lay.notes), desc(lay))

    # ------------------------------------------------------------- core rule
    print("layout_for_size")
    lay = A.layout_for_size("grid", mm(40), mm(40), mm(25))
    check("a 'grid' where one fits comes back as a single, noted",
          lay.ok and lay.kind == "single" and lay.notes and "single, not a grid" in lay.notes[0], desc(lay))
    lay = A.layout_for_size("grid", *face("plate"), mm(22.5), force=(3, 2))
    check("force 3 x 2 keeps the count, wider margins",
          lay.ok and (lay.cols, lay.rows) == (3, 2) and lay.margins_m[0] > mm(8.25), desc(lay))
    lay = A.layout_for_size("grid", *face("plate"), mm(22.5), force=(5, 2))
    check("force past what fits -> ok False", not lay.ok and "does not fit" in lay.notes[-1], desc(lay))
    lay = A.layout_for_size("charuco", mm(100), mm(100), mm(16), mm(20))
    check("ChArUco 16 in 20 (> 0.75 S) refused", not lay.ok and "1 module of white" in lay.notes[-1], desc(lay))
    lay = A.layout_for_size("charuco", mm(30), mm(100), mm(15), mm(20))
    check("ChArUco 1 x N refused (OpenCV's 2 x 2 floor)", not lay.ok and "2 × 2" in lay.notes[-1], desc(lay))
    lay = A.layout_for_size("grid", mm(100), mm(100), mm(20), gap_ratio=0.0)
    check("gap ratio 0 still leaves one module (markers never touch)",
          lay.ok and close(lay.separation_m, mm(20) / 6, 1e-7), desc(lay))
    lay = A.layout_for_size("grid", mm(100), mm(60), mm(20), quiet_modules=0.0)
    check("quiet zone 0: the pattern may reach the edge", lay.ok and lay.quiet_m == 0.0, desc(lay))
    check("unknown layout, zero box, junk numbers: ok False, never raises",
          not A.layout_for_size("hex", 0.1, 0.1, 0.01).ok
          and not A.layout_for_size("single", 0, 0.1, 0.01).ok
          and not A.layout_for_size("single", "x", None, "y").ok
          and A.default_layout("grid", 0, 0).marker_m == 0
          and A.default_layout("grid", None, "x").marker_m == 0
          and A.candidates("grid", 0, 0) == []
          and A.clamp_to_fit("grid", 0.1, 0.1, 0).ok is False)

    # ------------------------------------------------------------- apply
    print("Layout.apply -> TagSpec")
    lay = A.default_layout("grid", *face("plate"))
    spec = lay.apply(TagSpec(marker_id=12, depth_m=0.001, raise_m=0.0002, quiet_modules=1.0))
    check("grid spec: fields set, id / thickness kept",
          (spec.layout, spec.cols, spec.rows, spec.marker_id, spec.depth_m, spec.raise_m)
          == ("grid", 4, 2, 12, 0.001, 0.0002)
          and close(spec.marker_length_m, 0.0225) and close(spec.separation_m, 0.0045)
          and spec.square_length_m == 0.0, spec)
    tw, th = spec.tile_size_m
    check("TagSpec.tile_size_m fits the box", tw <= mm(120) + 1e-12 and th <= mm(70) + 1e-12, (tw, th))
    check("ids 12..19", spec.ids == list(range(12, 20)), spec.ids)
    check("check_spec accepts it", isinstance(aruco_board.check_spec(M.board_spec(spec)), dict))
    lay = A.default_layout("charuco", *face("plate"))
    spec = lay.apply(TagSpec(quiet_modules=1.0))
    check("ChArUco spec: quiet 0, tile = the squares (115 x 69)",
          spec.quiet_modules == 0.0 and close(spec.tile_size_m[0], mm(115)) and close(spec.tile_size_m[1], mm(69))
          and close(spec.square_length_m, mm(23)) and spec.separation_m == 0.0, spec)
    check("apply onto another module count raises (a caller bug)",
          raises(lambda: lay.apply(TagSpec(dictionary="DICT_5X5_100"))))
    check("same module count, other capacity is fine",
          lay.apply(TagSpec(dictionary="DICT_4X4_1000")).dictionary == "DICT_4X4_1000")
    check("apply of 'nothing fits' raises", raises(lambda: A.default_layout("grid", 0, 0).apply(TagSpec())))
    lay = A.layout_for_size("single", mm(40), mm(40), mm(20), quiet_modules=1.5)
    spec = lay.apply(TagSpec(quiet_modules=1.0))
    check("single: quiet zone the layout was fitted with is written",
          spec.layout == "single" and spec.quiet_modules == 1.5 and spec.rows == spec.cols == 1)

    # ------------------------------------------------------------- dictionary
    print("auto_dictionary (§3)")
    check("5 markers, empty design -> DICT_4X4_50", A.auto_dictionary("DICT_4X4_50", 5) == "DICT_4X4_50")
    check("5 markers, 40 in use -> DICT_4X4_100", A.auto_dictionary("4x4", 5, range(40)) == "DICT_4X4_100")
    check("13 markers, 60 in use -> DICT_4X4_250", A.auto_dictionary("DICT_4X4_50", 13, range(60)) == "DICT_4X4_250")
    check("the family is kept: DICT_5X5_250 -> DICT_5X5_50",
          A.auto_dictionary("DICT_5X5_250", 5) == "DICT_5X5_50")
    check("family spellings: DICT_6X6, 6x6, aruco6x6",
          {A.auto_dictionary(f, 3) for f in ("DICT_6X6", "6x6", "aruco6x6")} == {"DICT_6X6_50"})
    check("past every capacity -> the family's largest",
          A.auto_dictionary("4x4", 600, range(500)) == "DICT_4X4_1000")
    check("no 50/100/250/1000 family -> unchanged",
          A.auto_dictionary("DICT_APRILTAG_36h11", 5) == "DICT_APRILTAG_36h11"
          and A.auto_dictionary("DICT_ARUCO_ORIGINAL", 5) == "DICT_ARUCO_ORIGINAL")
    check("junk ids ignored, never raises", A.auto_dictionary("4x4", 2, [None, "x", -3, 1]) == "DICT_4X4_50")

    # ------------------------------------------------------------- sweep
    print("invariant sweep over random boxes")
    rng = random.Random(20261008)
    dicts = ["DICT_4X4_50", "DICT_4X4_1000", "DICT_5X5_100", "DICT_6X6_250", "DICT_7X7_1000"]
    counts = {"default": 0, "candidates": 0, "clamp": 0, "unprintable": 0, "clamped": 0}
    bad = []
    for trial in range(260):
        short = rng.choice([rng.uniform(3, 15), rng.uniform(15, 80), rng.uniform(80, 220)])
        W, H = mm(short), mm(short * rng.choice([1.0, rng.uniform(1, 2.9), rng.uniform(3, 9)]))
        if rng.random() < 0.5:
            W, H = H, W
        kw = dict(dictionary=rng.choice(dicts), quiet_modules=rng.choice([1.0, 1.0, 0.5, 1.5, 2.0]))
        for kind in ("single", "grid", "charuco"):
            where = "%s %.2fx%.2f mm %s" % (kind, W * 1000, H * 1000, kw)
            d = A.default_layout(kind, W, H, **kw)
            counts["default"] += 1
            p = problems(d, W, H, auto=True)
            if d.marker_m > 0 and not d.ok and not any("bigger dictionary" in t for t in d.notes):
                counts["unprintable"] += 1
                cands = A.candidates(kind, W, H, limit=None, **kw)
                if cands:                       # a printable layout existed: the default must find it
                    p.append("default unprintable but %s is printable" % cands[0].label())
                if d.module_m >= A.MIN_MODULE_M:
                    p.append("ok False with a printable module")
            if d.ok and d.kind != kind:
                p.append("default changed kind to %s" % d.kind)
            if p:
                bad.append((where, "default", d.label(), p))
            cs = A.candidates(kind, W, H, **kw)
            for i, c in enumerate(cs):
                counts["candidates"] += 1
                p = problems(c, W, H, auto=True)
                if not c.ok:
                    p.append("candidate not ok")
                if i and (c.square_m if kind == "charuco" else c.marker_m) >= \
                        (cs[i - 1].square_m if kind == "charuco" else cs[i - 1].marker_m):
                    p.append("not biggest first")
                # each entry is at the LARGEST step that gives its count
                if kind == "charuco":           # marker 0: derived from the bigger square
                    up = A.layout_for_size(kind, W, H, 0.0, c.square_m + A.STEP_M, **kw)
                else:
                    up = A.layout_for_size(kind, W, H, c.marker_m + A.STEP_M, **kw)
                if up.ok and up.kind == kind and (up.cols, up.rows) == (c.cols, c.rows) and kind != "single":
                    p.append("a bigger step gives the same count")
                if p:
                    bad.append((where, "candidate", c.label(), p))
            if len({(c.cols, c.rows) for c in cs}) != len(cs):
                bad.append((where, "candidates", "", ["duplicate counts"]))
            if cs and not all(sum(b in c.badges for c in cs) == 1
                              for b in ("largest marker", "most markers", "best fill")):
                bad.append((where, "candidates", "", ["badges"]))
            # a typed size, maybe off the grid, maybe far too big
            L = mm(round(rng.uniform(0.05, 1.3) * min(W, H) * 1000, 1))
            S = 0.0
            if kind == "charuco":
                S = mm(round(rng.uniform(0.1, 0.8) * min(W, H) * 1000, 1))
                L = rng.choice([0.0, mm(round(S * 1000 * rng.uniform(0.4, 0.9), 1))])
            r = A.clamp_to_fit(kind, W, H, L, S, **kw)
            counts["clamp"] += 1
            p = problems(r, W, H, auto=False)
            direct = A.layout_for_size(kind, W, H, L, S, **kw)
            if direct.ok and direct.kind == kind:
                if r.label() != direct.label() or r.notes != direct.notes:
                    p.append("fits, yet changed: %s -> %s" % (direct.label(), r.label()))
            elif r.marker_m > 0 and not any("bigger dictionary" in t for t in r.notes):
                counts["clamped"] += 1
                if not r.notes or not r.notes[0].startswith("clamped"):
                    p.append("no clamp chip: %s" % r.notes)
                L0 = direct.marker_m
                if r.marker_m > L0 + 1e-12 and kind != "charuco":
                    p.append("clamped UP")
                if kind != "charuco" and r.marker_m + A.STEP_M <= L0 + 1e-12:
                    nxt = A.layout_for_size(kind, W, H, r.marker_m + A.STEP_M, **kw)
                    if nxt.kind == kind and (nxt.ok or any("bigger dictionary" in t for t in nxt.notes)):
                        p.append("not the largest step: %s also fits" % nxt.label())
                if kind == "charuco" and r.square_m > direct.square_m + 1e-12:
                    p.append("square clamped UP")
            if p:
                bad.append((where, "clamp", "%s -> %s" % (A._mm(L) if L else "-", r.label()), p))
    for b in bad[:12]:
        print("      ", b)
    check("%d defaults, %d candidates, %d clamps (%d clamped, %d unprintable faces): all invariants hold"
          % (counts["default"], counts["candidates"], counts["clamp"], counts["clamped"], counts["unprintable"]),
          not bad, "%d problems" % len(bad))
    check("the sweep exercised clamping and the unprintable path",
          counts["clamped"] > 50 and counts["unprintable"] > 5, counts)

    # ------------------------------------------------------------- outlines
    print("fit_on_loops: a clicked face's own outline")

    def inside(loops, x, y):
        c = False
        for lp in loops:
            for i in range(len(lp)):
                (x0, y0), (x1, y1) = lp[i - 1], lp[i]
                if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
                    c = not c
        return c

    def tile_on_face(loops, pl, steps=60):
        """Independent check: dense samples of the tile (edges + interior)
        all on the face, and no outline vertex strictly inside the tile."""
        t = math.radians(pl["angle_deg"])
        c, s = math.cos(t), math.sin(t)
        w, h = pl["w"] - 2e-7, pl["h"] - 2e-7
        for i in range(steps + 1):
            for j in range(steps + 1):
                if 0 < i < steps and 0 < j < steps and (i % 6 or j % 6):
                    continue                  # edges densely, the interior on a coarser lattice
                u, v = -w / 2 + w * i / steps, -h / 2 + h * j / steps
                if not inside(loops, pl["cx"] + u * c - v * s, pl["cy"] + u * s + v * c):
                    return False
        for lp in loops:
            for x, y in lp:
                u = (x - pl["cx"]) * c + (y - pl["cy"]) * s
                v = -(x - pl["cx"]) * s + (y - pl["cy"]) * c
                if abs(u) < w / 2 and abs(v) < h / 2:
                    return False
        return True

    def ok_on_face(lay, pl, loops):
        if pl is None:
            return ["no placement"]
        p = problems(lay, pl["w"] + 1e-12, pl["h"] + 1e-12, auto=False, margins=False)
        if not (close(pl["w"], lay.tile_m[0], 1e-6) and close(pl["h"], lay.tile_m[1], 1e-6)):
            p.append("placement %r is not the tile %r" % ((pl["w"], pl["h"]), lay.tile_m))
        if not tile_on_face(loops, pl):
            p.append("tile leaves the face / covers a hole")
        return p

    def rect(x0, y0, w, h):
        return [(mm(x0), mm(y0)), (mm(x0 + w), mm(y0)), (mm(x0 + w), mm(y0 + h)), (mm(x0), mm(y0 + h))]

    def circle(cx, cy, r, n=96, rev=False):
        pts = [(mm(cx + r * math.cos(2 * math.pi * k / n)), mm(cy + r * math.sin(2 * math.pi * k / n)))
               for k in range(n)]
        return pts[::-1] if rev else pts

    # rectangles: exactly the box answer, centred. Auto = face_default (2026-10-08:
    # a single's sticker fills the face's clear square; boards keep default_layout)
    check("face_default: single fills the short side, never flush (29.5 on a 40 x 40 face, "
          "28.0 in a 37.5 square); boards are default_layout",
          A.face_default("single", mm(40), mm(40)).label() == "single 29.5 mm"
          and A.face_default("single", mm(37.5), mm(37.5)).label() == "single 28.0 mm"
          and not A.face_default("single", mm(40), mm(40)).notes
          and A.face_default("grid", *face("plate")).label() == A.default_layout("grid", *face("plate")).label()
          and A.face_default("charuco", *face("plate")).label()
          == A.default_layout("charuco", *face("plate")).label())
    lay = A.face_default("single", mm(8), mm(8))
    check("face_default: nothing printable -> the largest that fits (5.5), ok False, noted",
          not lay.ok and close(lay.marker_m, mm(5.5)) and lay.notes[0].startswith("nothing printable"),
          desc(lay))
    same = True
    detail = []
    for trial in range(40):
        w, h = rng.uniform(8, 200), rng.uniform(8, 200)
        x0, y0 = rng.uniform(-100, 100), rng.uniform(-100, 100)
        loops = [rect(x0, y0, w, h)]
        kind = ("single", "grid", "charuco")[trial % 3]
        for typed in (None, rng.uniform(0.1, 1.2) * min(w, h)):
            if typed is None:
                ref = A.face_default(kind, mm(w), mm(h))
                lay, pl = A.fit_on_loops(kind, loops)
            else:
                ref = A.clamp_to_fit(kind, mm(w), mm(h), mm(typed))
                lay, pl = A.fit_on_loops(kind, loops, marker_m=mm(typed))
            good = (lay.label() == ref.label() and lay.ok == ref.ok and lay.notes == ref.notes
                    and lay.margins_m == ref.margins_m and lay.separation_m == ref.separation_m
                    and (pl is None) == (ref.marker_m <= 0))
            if pl is not None:
                good = good and close(pl["cx"], mm(x0 + w / 2), 1e-7) and close(pl["cy"], mm(y0 + h / 2), 1e-7) \
                    and pl["angle_deg"] == 0.0 and close(pl["w"], ref.tile_m[0], 1e-7) \
                    and close(pl["h"], ref.tile_m[1], 1e-7)
            if not good:
                same = False
                detail.append((kind, w, h, typed, lay.label(), ref.label(), pl))
    check("40 rectangles: fit_on_loops == face_default / clamp_to_fit on the box, centred",
          same, detail[:3])
    noisy = [[(0.0, 0.0), (0.06, 0.0), (0.12, 0.0), (0.12, 0.035), (0.12, 0.07), (0.0, 0.07), (0.0, 0.0)]]
    lay, pl = A.fit_on_loops("grid", noisy)
    check("collinear points and a repeated closing point: still the exact box answer",
          lay.label() == "4 × 2 @ 22.5 mm" and close(pl["cx"], 0.06) and close(pl["cy"], 0.035), (lay.label(), pl))
    th = math.radians(30)
    rot = [(x * math.cos(th) - y * math.sin(th), x * math.sin(th) + y * math.cos(th)) for x, y in rect(-60, -35, 120, 70)]
    lay, pl = A.fit_on_loops("grid", [rot], angles_deg=(0.0, 30.0))
    check("a rectangle at 30 deg, tried at 30: the box answer, angle 30, centred",
          lay.label() == "4 × 2 @ 22.5 mm" and pl["angle_deg"] == 30.0 and close(pl["cx"], 0.0, 1e-7)
          and close(pl["cy"], 0.0, 1e-7), (lay.label(), pl))

    # a plate with a hole in the middle
    plate_hole = [rect(-60, -35, 120, 70), rect(-10, -10, 20, 20)[::-1]]
    t0 = time.time()
    lay, pl = A.fit_on_loops("grid", plate_hole)
    check("120 x 70 with a 20 mm hole, grid: the doc's rule in the largest clear rectangle "
          "(50 x 70 beside the hole) -> 2 x 3 @ 16.0, centred in it",
          lay.ok and lay.label() == "2 × 3 @ 16.0 mm" and close(abs(pl["cx"]), mm(35), 1e-6)
          and close(pl["cy"], 0.0, 1e-6) and lay.notes[0].startswith("not a rectangle")
          and close(pl["clear"]["w"], mm(50), mm(0.05)) and close(pl["clear"]["h"], mm(70), mm(0.05)),
          (desc(lay), pl))
    check("...the tile is on the face and clear of the hole", not ok_on_face(lay, pl, plate_hole),
          ok_on_face(lay, pl, plate_hole))
    check("(the independent checker has teeth: the same tile moved onto the hole, or off the edge, fails)",
          not tile_on_face(plate_hole, dict(pl, cx=0.0, cy=0.0))
          and not tile_on_face(plate_hole, dict(pl, cx=pl["cx"] - mm(15))))
    lay, pl = A.fit_on_loops("single", plate_hole)
    check("single: the sticker fills the 50 mm clear square beside the hole -> 37.0 (37.5 would "
          "be a 50.0 tile, flush), no 'shrunk' note",
          lay.ok and lay.label() == "single 37.0 mm"
          and lay.notes[0].startswith("not a rectangle: the sticker fills")
          and not any("shrunk" in t for t in lay.notes)
          and not ok_on_face(lay, pl, plate_hole), (desc(lay), pl))
    lay, pl = A.fit_on_loops("charuco", plate_hole)
    check("ChArUco: the doc's rule in the 50 x 70 clear rectangle -> 3 x 4 @ 16.5 / 12.0",
          lay.ok and lay.label() == "3 × 4 @ 16.5 / 12.0 mm" and not ok_on_face(lay, pl, plate_hole),
          (desc(lay), pl))
    lay, pl = A.fit_on_loops("grid", plate_hole, marker_m=mm(8))
    check("typed 8 mm: kept, many markers, still clear of the hole",
          lay.ok and close(lay.marker_m, mm(8)) and lay.count >= 12 and not ok_on_face(lay, pl, plate_hole),
          (desc(lay), pl))
    round_hole = [rect(-60, -35, 120, 70), circle(0, 0, 8, 48, rev=True)]
    lay, pl = A.fit_on_loops("single", round_hole)
    check("a round hole works the same: 52 mm of room beside an 8 mm hole -> 38.5 (39.0 = a 52.0 "
          "sticker, flush with the plate's edge)",
          lay.ok and lay.label() == "single 38.5 mm" and not ok_on_face(lay, pl, round_hole), (desc(lay), pl))
    dt_hole = time.time() - t0

    # an L-shape
    L_shape = [[(0, 0), (mm(60), 0), (mm(60), mm(20)), (mm(20), mm(20)), (mm(20), mm(60)), (0, mm(60))]]
    lay, pl = A.fit_on_loops("single", L_shape)
    check("L-shape single: the 20 mm leg -> 14.5 (15.0 would be a 20.0 sticker, flush with both walls)",
          lay.ok and close(lay.marker_m, mm(14.5)) and not any("shrunk" in t for t in lay.notes)
          and not ok_on_face(lay, pl, L_shape), (desc(lay), pl))
    lay, pl = A.fit_on_loops("grid", L_shape)
    check("L-shape grid: >= 2 markers in a leg, inside",
          lay.ok and lay.kind == "grid" and lay.count >= 2 and not ok_on_face(lay, pl, L_shape), (desc(lay), pl))
    lay, pl = A.fit_on_loops("grid", L_shape, marker_m=mm(8))
    check("L-shape typed 8 mm: kept, 6 along the 60 mm leg (tile 58.7 x 10.7; the old sampling "
          "found 5)", lay.ok and lay.label() == "6 × 1 @ 8.0 mm"
          and not ok_on_face(lay, pl, L_shape), (desc(lay), pl))
    lay, pl = A.fit_on_loops("charuco", L_shape)
    check("L-shape ChArUco: a valid board in a leg", lay.ok and lay.cols >= 2 and lay.rows >= 2
          and not ok_on_face(lay, pl, L_shape), (desc(lay), pl))

    # a disc
    disc = [circle(0, 0, 30)]
    t0 = time.time()
    lay, pl = A.fit_on_loops("single", disc)
    check("disc r 30 single: the inscribed square (42.43) holds 31.5 mm (tile 42.0), centred",
          lay.ok and close(lay.marker_m, mm(31.5))
          and close(pl["cx"], 0.0, 1e-4) and close(pl["cy"], 0.0, 1e-4)
          and not any("shrunk" in t for t in lay.notes)
          and not ok_on_face(lay, pl, disc), (desc(lay), pl))
    lay, pl = A.fit_on_loops("grid", disc)
    check("disc grid: the doc's rule in the inscribed square -> 2 x 2 @ 13.5",
          lay.ok and lay.label() == "2 × 2 @ 13.5 mm" and not ok_on_face(lay, pl, disc), (desc(lay), pl))
    lay, pl = A.fit_on_loops("charuco", disc)
    check("disc ChArUco: 3 x 3 @ 14.0 / 10.5 in the inscribed square",
          lay.ok and lay.label() == "3 × 3 @ 14.0 / 10.5 mm" and not ok_on_face(lay, pl, disc),
          (desc(lay), pl))
    lay, pl = A.fit_on_loops("single", disc, marker_m=mm(50))
    check("disc typed 50 mm -> clamped, said so", lay.ok and lay.marker_m <= mm(31.5) + 1e-9
          and lay.notes[1].startswith("clamped: 50.0 mm does not fit") and not ok_on_face(lay, pl, disc),
          (desc(lay), pl))
    lay, pl = A.fit_on_loops("grid", disc, marker_m=mm(10))
    check("disc typed 10 mm: kept, as many as fit (>= 9)", lay.ok and close(lay.marker_m, mm(10))
          and lay.count >= 9 and not ok_on_face(lay, pl, disc), (desc(lay), pl))
    dt_disc = time.time() - t0

    # nothing fits
    lay, pl = A.fit_on_loops("single", [rect(0, 0, 6, 6)])
    check("a 6 mm face: nothing printable -> largest that fits, not flush (4.0), ok False, placed",
          not lay.ok and close(lay.marker_m, mm(4.0)) and pl is not None
          and any(t.startswith("nothing printable fits") for t in lay.notes), (desc(lay), pl))
    lay, pl = A.fit_on_loops("grid", [circle(0, 0, 4, 48)])
    check("an 8 mm disc: ok False, still inside", not lay.ok and lay.marker_m > 0
          and not ok_on_face(lay, pl, [circle(0, 0, 4, 48)]), (desc(lay), pl))
    lay, pl = A.fit_on_loops("charuco", [rect(0, 0, 0.4, 0.4)])
    check("a 0.4 mm face: marker 0, placement None", lay.marker_m == 0 and not lay.ok and pl is None,
          (desc(lay), pl))
    lay, pl = A.fit_on_loops("grid", [])
    check("no loops: marker 0, placement None, a note", lay.marker_m == 0 and pl is None and lay.notes,
          desc(lay))
    lay, pl = A.fit_on_loops("grid", [[(0, 0), (1, 1)], "junk"])
    check("degenerate loops never raise", lay.marker_m == 0 and pl is None)

    # random faces: convex blobs with a hole, both kinds, default and typed
    shapes_bad = []
    for trial in range(9):
        rx, ry = rng.uniform(20, 60), rng.uniform(20, 60)
        npts = rng.randint(5, 14)
        outer = [(mm(rx * math.cos(2 * math.pi * k / npts + 0.3) * rng.uniform(0.85, 1.0)),
                  mm(ry * math.sin(2 * math.pi * k / npts + 0.3) * rng.uniform(0.85, 1.0))) for k in range(npts)]
        loops = [outer]
        if trial % 2:
            loops.append(circle(rng.uniform(-5, 5), rng.uniform(-5, 5), rng.uniform(2, 6), 24, rev=True))
        kind = ("single", "grid", "charuco")[trial % 3]
        for typed in (None, mm(rng.uniform(8, 25))):
            lay, pl = A.fit_on_loops(kind, loops, marker_m=typed)
            if lay.marker_m > 0:
                p = ok_on_face(lay, pl, loops)
                if lay.ok and typed is None and (lay.module_m < A.MIN_MODULE_M - 1e-12 or not on_grid(lay.marker_m)):
                    p.append("auto size not printable / not on the grid")
                if p:
                    shapes_bad.append((trial, kind, typed, lay.label(), p))
    check("9 random faces with holes: every placement inside, every layout valid", not shapes_bad, shapes_bad[:3])
    check("timing: hole plate %.2f s, disc %.2f s (each < 4 s)" % (dt_hole, dt_disc), dt_hole < 4 and dt_disc < 4)

    # ------------------------------------------------------------- gallery
    gallery(tile_on_face, ok_on_face)

    print(f"\n{PASS} passed, {len(FAIL)} failed  ({time.time() - t_start:.1f} s)")
    if FAIL:
        sys.exit(1)



# ------------------------------------------------------------------ gallery

class Raster:
    """The brute-force reference: the face rasterised at PX (pixel centres,
    even-odd over every loop, numpy), then every window of every size
    checked with a summed-area table. Shares no code with the solver (no
    distance function, no branch and bound): it can only be off by about a
    pixel, which the checks allow for."""
    PX = 0.0001                                     # 0.1 mm

    def __init__(self, loops):
        import numpy as np
        self.np = np
        pts = [p for lp in loops for p in lp]
        x0, y0 = min(p[0] for p in pts) - self.PX, min(p[1] for p in pts) - self.PX
        x1, y1 = max(p[0] for p in pts) + self.PX, max(p[1] for p in pts) + self.PX
        nx, ny = int(math.ceil((x1 - x0) / self.PX)), int(math.ceil((y1 - y0) / self.PX))
        X = x0 + (np.arange(nx) + 0.5) * self.PX
        Y = y0 + (np.arange(ny) + 0.5) * self.PX
        Xg, Yg = np.meshgrid(X, Y)
        inside = np.zeros((ny, nx), dtype=bool)
        for lp in loops:
            for i in range(len(lp)):
                (ax, ay), (bx, by) = lp[i - 1], lp[i]
                if ay == by:
                    continue
                cross = ((ay > Yg) != (by > Yg)) & (Xg < ax + (Yg - ay) * (bx - ax) / (by - ay))
                inside ^= cross
        self.mask = inside
        sat = np.zeros((ny + 1, nx + 1), dtype=np.int64)
        sat[1:, 1:] = inside.astype(np.int64).cumsum(0).cumsum(1)
        self.sat = sat
        self.x0, self.y0 = x0, y0

    def window(self, kw, kh):
        """Is there a kw x kh pixel window that is all inside?"""
        S = self.sat
        ny, nx = S.shape[0] - 1, S.shape[1] - 1
        if kw < 1 or kh < 1 or kw > nx or kh > ny:
            return kw < 1 or kh < 1
        win = S[kh:, kw:] - S[:-kh, kw:] - S[kh:, :-kw] + S[:-kh, :-kw]
        return bool((win == kw * kh).any())

    def largest(self, aspect):
        """(w, h) m of the largest all-inside window of aspect w / h."""
        lo, hi = 0, max(self.sat.shape)
        while lo < hi:
            k = (lo + hi + 1) // 2
            if self.window(max(1, int(round(aspect * k))), k):
                lo = k
            else:
                hi = k - 1
        return max(1, int(round(aspect * lo))) * self.PX if lo else 0.0, lo * self.PX

    def surely(self, w, h):
        """A w x h rectangle surely fits (a window one pixel bigger all round is inside)."""
        return self.window(int(math.ceil(w / self.PX)) + 2, int(math.ceil(h / self.PX)) + 2)

    def maybe(self, w, h):
        """A w x h rectangle may fit (a window one pixel smaller all round is inside)."""
        return self.window(max(1, int(math.floor(w / self.PX)) - 2), max(1, int(math.floor(h / self.PX)) - 2))


def gallery(tile_on_face, ok_on_face):
    print("gallery: ten faces that are not boxes, against a brute-force raster (0.1 mm)")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ArucoMarkers"))
    import aruco_autosize_gallery as G
    t0 = time.time()
    step = A.STEP_M
    n, q = 6, 1.0
    f_single = 1.0 + 2.0 * q / n                    # tile / marker, single, 4x4, quiet 1
    bad, exact, times, rows = [], 0, [], []
    for f in G.FACES:
        loops = G.loops_m(f)
        R = Raster(loops)
        # the largest clear rectangle over every aspect, solver vs raster
        best_bf, k0 = 0.0, 0
        for k in range(-32, 33):                    # 1/16 .. 16, then finely around the best
            w, h = R.largest(2.0 ** (k / 8.0))
            if w * h > best_bf:
                best_bf, k0 = w * h, k
        for k in range(8 * k0 - 8, 8 * k0 + 9):
            w, h = R.largest(2.0 ** (k / 64.0))
            best_bf = max(best_bf, w * h)
        lc = A.largest_clear_rect(loops)
        if lc is None or lc["w"] * lc["h"] < best_bf * 0.97 or lc["w"] * lc["h"] > best_bf * 1.03 + 1e-6:
            bad.append((f["key"], "largest clear rect", lc and lc["w"] * lc["h"] * 1e6, best_bf * 1e6))
        for kind in G.KINDS:
            t = time.perf_counter()
            lay, pl = A.fit_on_loops(kind, loops)
            times.append(time.perf_counter() - t)
            p = ok_on_face(lay, pl, loops) if lay.marker_m > 0 else ["nothing placed"]
            if any("shrunk" in t_ for t_ in lay.notes):
                p.append("a 'shrunk' note on an auto default")
            if kind == "single":
                # the optimum = the largest step whose tile fits; the raster brackets it
                hi = max([k for k in range(1, 400) if R.maybe(k * step * f_single, k * step * f_single)] or [0]) * step
                lo = max([k for k in range(1, 400) if R.surely(k * step * f_single, k * step * f_single)] or [0]) * step
                if not (lo - 1e-12 <= lay.marker_m <= hi + 1e-12):
                    p.append("single %.1f outside the brute-force band [%.1f, %.1f]"
                             % (lay.marker_m * 1e3, lo * 1e3, hi * 1e3))
                if abs(lay.marker_m - lo) > step + 1e-12 and abs(lay.marker_m - hi) > step + 1e-12:
                    p.append("single more than one step from the brute force")
                exact += lo == hi and abs(lay.marker_m - lo) < 1e-12
                rows.append((f["key"], kind, lay.label(), "bf %.1f-%.1f" % (lo * 1e3, hi * 1e3)))
            elif pl is not None and pl.get("clear"):
                # the clear rectangle the board was sized in: as big as the raster's at its aspect
                c = pl["clear"]
                wb, hb = R.largest(c["w"] / c["h"])
                if abs(c["h"] - hb) > 3 * Raster.PX + 2 * A.CLEAR_EPS_M:
                    p.append("clear rect h %.2f vs brute force %.2f" % (c["h"] * 1e3, hb * 1e3))
                ref = A.default_layout(kind, wb, hb)
                key = "square_m" if kind == "charuco" else "marker_m"
                if ref.marker_m > 0 and abs(getattr(lay, key) - getattr(ref, key)) > step + 1e-12:
                    p.append("%s more than one step from the rule on the brute-force rectangle (%s)"
                             % (lay.label(), ref.label()))
                rows.append((f["key"], kind, lay.label(), "bf rect %.1f x %.1f -> %s"
                             % (wb * 1e3, hb * 1e3, ref.label())))
            if p:
                bad.append((f["key"], kind, lay.label(), p))
        for kind, L, S in G.typed_for(f["key"]):
            t = time.perf_counter()
            lay, pl = A.fit_on_loops(kind, loops, marker_m=L / 1000.0, square_m=S / 1000.0)
            times.append(time.perf_counter() - t)
            p = ok_on_face(lay, pl, loops) if lay.marker_m > 0 else ["nothing placed"]
            if kind == "single":
                fits = R.maybe(L / 1000.0 * f_single, L / 1000.0 * f_single)
                if lay.marker_m < L / 1000.0 - 1e-12 and not lay.notes[-1].startswith("clamped:"):
                    p.append("clamped without a note")
                if lay.marker_m < L / 1000.0 - 1e-12 and R.surely(L / 1000.0 * f_single, L / 1000.0 * f_single):
                    p.append("clamped though it fits")
                if lay.marker_m >= L / 1000.0 - 1e-12 and not fits:
                    p.append("kept though it cannot fit")
            else:                                   # grid: as many as surely fit, no more than may
                Lm = L / 1000.0
                s_ = max(A.GAP_RATIO * Lm, Lm / n)
                Q = q * Lm / n
                xs = [pt[0] for lp in loops for pt in lp]
                ys = [pt[1] for lp in loops for pt in lp]
                sure = may = 0
                for c in range(1, int((max(xs) - min(xs)) / (Lm + s_)) + 2):
                    for r in range(1, int((max(ys) - min(ys)) / (Lm + s_)) + 2):
                        if c * r < 2:
                            continue
                        tw, th = c * Lm + (c - 1) * s_ + 2 * Q, r * Lm + (r - 1) * s_ + 2 * Q
                        if c * r > may and R.maybe(tw, th):
                            may = c * r
                        if c * r > sure and R.surely(tw, th):
                            sure = c * r
                if not (sure <= lay.count <= may):
                    p.append("typed grid count %d outside the brute-force band [%d, %d]"
                             % (lay.count, sure, may))
            rows.append((f["key"], "typed %s %.1f" % (kind, L), lay.label(), "; ".join(lay.notes[1:])))
            if p:
                bad.append((f["key"], "typed", kind, L, lay.label(), p))
    for r in rows:
        print("      %-10s %-15s %-24s %s" % r)
    for b in bad[:8]:
        print("      BAD", b)
    check("10 gallery faces x (single, grid, ChArUco defaults + typed sizes): every tile inside "
          "the face and clear of its holes, every size within the brute-force band (one step)",
          not bad, "%d problems" % len(bad))
    print("      single defaults where the raster pins the optimum to one step: %d of 10, all equal to it" % exact)
    # the face frame Fusion picks (x along the longest straight edge, either
    # way round) turns these faces by a multiple of 90 deg: same answer
    turned = []
    for key in ("lshape", "plate_hole", "notch", "dshape", "disc4"):
        Lp = G.loops_m(G.face(key))
        bx = G.bbox_mm(G.face(key))
        ox, oy = (bx[0] + bx[2]) / 2000.0, (bx[1] + bx[3]) / 2000.0
        for kind in G.KINDS:
            got = set()
            for rot in (0, 90, 180, 270):
                c_, s_ = round(math.cos(math.radians(rot))), round(math.sin(math.radians(rot)))
                lp = [[((x - ox) * c_ - (y - oy) * s_, (x - ox) * s_ + (y - oy) * c_) for x, y in l] for l in Lp]
                lay = A.fit_on_loops(kind, lp)[0]
                got.add((tuple(sorted((lay.cols, lay.rows))), round(lay.marker_m, 7), round(lay.square_m, 7)))
            if len(got) != 1:
                turned.append((key, kind, got))
    check("the same face turned by 90 / 180 / 270 deg gets the same default (cols x rows up to a "
          "transpose)", not turned, turned)
    check("disc4 (Zach's Ø53, 4 holes): single auto = 28.0 mm, sticker 37.33 in the 37.4 clear square",
          A.fit_on_loops("single", G.loops_m(G.face("disc4")))[0].label() == "single 28.0 mm")
    worst = max(times)
    check("timing: worst fit %.3f s, mean %.3f s over %d gallery fits (each < 1 s)"
          % (worst, sum(times) / len(times), len(times)), worst < 1.0)
    print("      (gallery section %.1f s)" % (time.time() - t0))

    # candidates_on_loops: the Layout list for a clicked face (2026-10-08,
    # Zach: "it says 4 x 1 but only 3 are showing" -- the list had sized the
    # plate's box and ignored the holes at its ends)
    print("candidates_on_loops: the Layout list respects holes")

    def circ(cx, cy, r, n=96):
        return [(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n))
                for k in range(n)]

    def rect(w, h):
        return [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]

    plate = [rect(mm(150), mm(45)), circ(mm(-66), mm(5), mm(3.5)), circ(mm(66), mm(-5), mm(3.5))]
    cl = A.candidates_on_loops("grid", plate)
    labels = [c.label() for c in cl]
    check("plate 150 x 45 with end holes: no '4 × 1 @ 30.0' (the box's answer), 4 × 1 only at "
          "25.0", "4 × 1 @ 30.0 mm" not in labels and "4 × 1 @ 25.0 mm" in labels, labels)
    bad = []
    for kind in ("grid", "charuco"):
        for c in A.candidates_on_loops(kind, plate):
            lay = A.fit_on_loops(kind, plate, marker_m=None if kind == "charuco" else c.marker_m,
                                 square_m=c.square_m)[0]
            if (lay.cols, lay.rows) != (c.cols, c.rows) or not close(lay.marker_m, c.marker_m):
                bad.append((c.label(), lay.label()))
    check("every entry is what typing its size builds (same cols x rows, same marker)", not bad, bad)
    rc = [c.label() for c in A.candidates_on_loops("grid", [rect(mm(120), mm(70))])]
    check("a plain rectangle gets the same list as candidates() on its box",
          rc == [c.label() for c in A.candidates("grid", mm(120), mm(70))], rc)
    t1 = time.time()
    A.candidates_on_loops("grid", plate)
    check("plate list in < 1.5 s (%.2f s)" % (time.time() - t1), time.time() - t1 < 1.5)


if __name__ == "__main__":
    main()
