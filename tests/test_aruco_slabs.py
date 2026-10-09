"""Offline tests for bam_cad_core.aruco_slabs -- ArUco Extrude's Offset / Height
model (Zach 2026-10-08: "same language as extrude ... the height and then an
offset from that surface ... positive and negative values"), the parts a
node is made of, the record's depth / raise / recess, the old-parameter
migration and the locating pins.

Run:  python tests/test_aruco_slabs.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "CADExporter"))

from bam_cad_core import aruco_slabs as S  # noqa: E402
from bam_cad_core import markers as M  # noqa: E402

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


def close(a, b, tol=1e-9):
    if isinstance(a, (tuple, list)):
        return len(a) == len(b) and all(close(x, y, tol) for x, y in zip(a, b))
    return abs(a - b) <= tol


def N(wo=0.0, wh=2.0, bo=2.0, bh=-0.6, **kw):
    n = {"white_offset": wo, "white_height": wh, "black_offset": bo, "black_height": bh}
    n.update(kw)
    return n


print("== slabs: an extrude from Offset, Height further (either sign)")
check("positive height", S.slab(0, 2) == (0, 2))
check("negative height extrudes the other way", close(S.slab(2, -0.6), (1.4, 2.0)))
check("negative offset starts below", close(S.slab(-1, 0.4), (-1.0, -0.6)))
check("snap: 0 -> the minimum, + by default", S.snap_height(0, 0.05) == 0.05)
check("snap: 0 keeps the sign it came from", S.snap_height(0, 0.05, prev=-0.3) == -0.05)
check("snap: inside the dead zone keeps its sign", S.snap_height(-0.01, 0.05) == -0.05)
check("snap: limits", S.snap_height(80, 0.05) == S.LIMIT_MM and S.snap_height(-80, 0.05) == -S.LIMIT_MM)
check("snap: a normal value is left alone", S.snap_height(-0.6, 0.05) == -0.6)

print("== defaults")
d = S.default_numbers("both")
check("white + black: white 0 / +2, black at the white top / -0.6",
      d == N(0.0, 2.0, 2.0, -0.6), d)
d = S.default_numbers("black")
check("black only: black at the face, -0.6 (a flush inlay)",
      d["black_offset"] == 0.0 and d["black_height"] == -0.6, d)
d = S.default_numbers("white")
check("white only: white at the face, -0.6", d["white_offset"] == 0.0 and d["white_height"] == -0.6, d)
check("the black follows the white top", S.white_top("both", N(0.5, 1.5)) == 2.0
      and S.white_top("both", N(1.0, -0.5)) == 1.0 and S.white_top("black", N()) == 0.0)

print("== layers: the record's surface, depth / raise / recess, detector plane")
L = S.layers("both", N())
check("default: flush inlay, depth 0.6, raise 0", close((L.surface, L.depth, L.raise_, L.recess),
                                                        (2.0, 0.6, 0.0, 0.0)), L)
check("default: detector at the white top", close(L.detector, 2.0))
L = S.layers("both", N(bo=2.0, bh=0.6))
check("black standing 0.6 on the white top: raise 0.6, depth 0",
      close((L.depth, L.raise_, L.recess, L.detector), (0.0, 0.6, 0.0, 2.6)), L)
L = S.layers("both", N(bo=1.8, bh=0.6))
check("straddling: depth 0.2, raise 0.4", close((L.depth, L.raise_), (0.2, 0.4)), L)
L = S.layers("both", N(bo=1.6, bh=-0.6))
check("recessed 0.4: depth 1.0, recess 0.4, detector = the white top",
      close((L.depth, L.raise_, L.recess, L.detector), (1.0, 0.0, 0.4, 2.0)), L)
check("recessed: thickness 0.6 = depth - recess", close(L.thickness, 0.6)
      and close(L.depth - L.recess, 0.6))
L = S.layers("black", N(bo=0.0, bh=-0.6))
check("black only inlaid: surface = the face", close((L.surface, L.depth, L.raise_), (0, 0.6, 0)))
L = S.layers("black", N(bo=0.0, bh=0.6))
check("black only on top: raise 0.6", close((L.surface, L.depth, L.raise_, L.detector), (0, 0, 0.6, 0.6)))
L = S.layers("black", N(bo=0.0, bh=0.6), has_body=False)
check("black only, nothing under: the black's own top", close((L.surface, L.depth, L.raise_),
                                                              (0.6, 0.6, 0.0)))
L = S.layers("white", N(wo=0.0, wh=-0.6))
check("white only inlaid: the white is the pattern", L.pattern == (-0.6, 0.0) and L.black is None
      and close((L.depth, L.raise_), (0.6, 0.0)))
L = S.layers("white", N(wo=0.0, wh=0.4))
check("white only raised 0.4", close((L.depth, L.raise_, L.detector), (0.0, 0.4, 0.4)))
L = S.layers("both", N(bo=2.3, bh=0.5))
check("a floating black reports its gap", close(L.gap, 0.3))
check("a height of 0 is refused", raises(lambda: S.layers("both", N(bh=0.0))))

print("== the record (TagSpec) agrees with the slabs")
for n, mode in ((N(), "both"), (N(bo=2.0, bh=0.6), "both"), (N(bo=1.6, bh=-0.6), "both"),
                (N(bo=1.8, bh=0.6), "both"), (N(bo=0.0, bh=-0.6), "black"),
                (N(bo=-1.0, bh=0.4), "black"), (N(wo=0.0, wh=0.4), "white")):
    L = S.layers(mode, n)
    spec = M.TagSpec(depth_m=L.depth / 1000, raise_m=L.raise_ / 1000, recess_m=L.recess / 1000)
    lo, hi = L.pattern
    check(f"{mode} {lo:+.1f}..{hi:+.1f}: [-depth, top] around the surface is the pattern",
          close(L.surface - spec.depth_m * 1000, lo, 1e-9)
          and close(L.surface + spec.black_top_m * 1000, hi, 1e-9)
          and close(spec.black_thickness_m * 1000, hi - lo, 1e-9))
    rt = M.TagSpec.from_dict(spec.to_dict(), lambda m: None, "t")
    check(f"{mode} {lo:+.1f}..{hi:+.1f}: round-trips", rt is not None
          and close((rt.depth_m, rt.raise_m, rt.recess_m), (spec.depth_m, spec.raise_m, spec.recess_m)))
    D = M.detector_frame(spec)
    check(f"{mode} {lo:+.1f}..{hi:+.1f}: OpenCV at the higher top",
          close(D[11] * 1000 + L.surface, L.detector, 1e-9))
check("recess_m is written only when set", "recess_m" not in M.TagSpec().to_dict())
check("raise and recess together are refused",
      M.TagSpec.from_dict({"depth_m": 0.001, "raise_m": 0.0002, "recess_m": 0.0002},
                          lambda m: None, "t") is None)

print("== migration: height / thickness / above -> Offset / Height (same geometry)")


def old_pattern(mode, height, thickness, above):
    a = max(0.0, above)
    t = max(thickness, a)
    surf = height if mode == "both" else 0.0
    return (surf - (t - a), surf + a)


for mode, h, t, a in (("both", 2.0, 0.6, 0.0), ("both", 1.5, 0.6, 0.4), ("both", 3.0, 0.4, 0.4),
                      ("black", 0.0, 0.6, 0.0), ("black", 0.0, 0.6, 0.6), ("black", 0.0, 0.3, 0.6),
                      ("white", 0.0, 0.6, 0.0), ("white", 0.0, 0.4, 0.4)):
    n = S.from_legacy(mode, h, t, a)
    L = S.layers(mode, n)
    check(f"{mode} h{h} t{t} a{a}: same pattern slab", close(L.pattern, old_pattern(mode, h, t, a)),
          (L.pattern, old_pattern(mode, h, t, a)))
    if mode == "both":
        check(f"{mode} h{h}: white 0..{h}", close(L.white, (0.0, h)))
n = S.from_legacy("black", 0.0, 0.6, 0.0)
check("Zach's 'top' (black only, inlaid 0.6) = the new default: offset 0, height -0.6",
      n["black_offset"] == 0.0 and n["black_height"] == -0.6, n)
n = S.from_legacy("both", 2.0, 0.6, 0.0)
check("an old flush tag: black at the white top, -0.6", n["black_offset"] == 2.0
      and n["black_height"] == -0.6, n)
n = S.from_legacy("black", 0.0, 0.6, 0.6)
check("an old raised black: from the face, +0.6", n["black_offset"] == 0.0
      and n["black_height"] == 0.6, n)

print("== plan: the parts follow from where the slabs are")
P = S.plan("both", "join", True, N())
check("join, flush: tile, pocket, pattern", P.parts == ["tile", "pocket", "pattern"] and P.op == "join",
      P.parts)
check("join, flush: the pocket is the black's slab", close(P.pocket, (1.4, 2.0)))
check("join, flush: nothing unusual to say", P.notes == [], P.notes)
P = S.plan("both", "join", True, N(bo=2.0, bh=0.6))
check("black standing on the white: no pocket", P.parts == ["tile", "pattern"], P.parts)
P = S.plan("both", "join", True, N(bo=1.6, bh=-0.6))
check("recessed: the pocket opens up to the white top", close(P.pocket, (1.0, 2.0)))
check("recessed: one line", len(P.notes) == 1 and "recessed 0.40 mm" in P.notes[0], P.notes)
P = S.plan("both", "join", True, N(bo=2.5, bh=0.5))
check("floating black: no pocket, one line", P.parts == ["tile", "pattern"]
      and any("floats 0.50 mm" in t for t in P.notes), P.notes)
P = S.plan("both", "join", True, N(wo=0.5, wh=2.0, bo=2.5))
check("a gap under a joined tile is said", any("gap" in t for t in P.notes), P.notes)
P = S.plan("both", "join", False, N())
check("no body under: a new tile", P.op == "new" and P.parts == ["tile", "pocket", "pattern"])
P = S.plan("both", "new", True, N(wo=-1.0, wh=3.0, bo=2.0))
check("new tile sunk 1 mm into a body: the body gets a recess for it",
      P.parts == ["tile", "pocket", "pattern", "recess"] and P.recess == [("tile", (-1.0, 0.0))],
      (P.parts, P.recess))
P = S.plan("both", "new", True, N(wo=0.0, wh=2.0, bo=2.0, bh=-2.5))
check("new tile, the black poking 0.5 into the body: recessed into it too",
      ("pattern", (-0.5, 0.0)) in P.recess, P.recess)
P = S.plan("both", "new", False, N(wo=0.0, wh=2.0, bo=2.0, bh=-2.5))
check("new tile, nothing under, the black pokes out: one line",
      any("pokes 0.50 mm out" in t for t in P.notes), P.notes)
P = S.plan("black", "join", True, N(bo=0.0, bh=-0.6))
check("black only inlaid: pattern + recess (cut into the body)",
      P.parts == ["pattern", "recess"] and P.recess == [("pattern", (-0.6, 0.0))], P.parts)
P = S.plan("black", "join", True, N(bo=0.0, bh=0.6))
check("black only on top: just the pattern", P.parts == ["pattern"] and P.notes == [])
P = S.plan("black", "join", True, N(bo=-1.0, bh=0.4))
check("black only recessed: the recess opens to the face", P.recess == [("pattern", (-1.0, 0.0))]
      and any("recessed 0.60 mm below the face" in t for t in P.notes), (P.recess, P.notes))
P = S.plan("black", "join", True, N(bo=0.3, bh=0.4))
check("black only floating: one line", any("floats 0.30 mm" in t for t in P.notes), P.notes)
P = S.plan("black", "join", False, N(bo=0.0, bh=-0.6))
check("black only, nothing under: no cut", P.parts == ["pattern"])
P = S.plan("white", "join", True, N(wo=0.0, wh=-0.6))
check("white only inlaid: pattern + recess", P.parts == ["pattern", "recess"])

print("== plan: locating pins")
pn = N(pin_length=2.0, pin_tol=0.15)
P = S.plan("both", "new", True, pn, "triangle")
check("new body + a body under: tile, pin, pocket, pattern, recess (the hole)",
      P.parts == ["tile", "pin", "pocket", "pattern", "recess"], P.parts)
check("the pin hangs 2 mm from the tile's underside", close(P.pin, (-2.0, 0.0)))
check("the hole: 2.15 mm deep, poking out of the face", P.recess == [("hole", (-2.15, S.HOLE_OVERSHOOT_MM))],
      P.recess)
P = S.plan("both", "new", False, pn, "round")
check("nothing under: a pin, no hole", P.parts == ["tile", "pin", "pocket", "pattern"], P.parts)
P = S.plan("both", "join", True, pn, "triangle")
check("join: no pin (nothing separate to locate)", "pin" not in P.parts)
P = S.plan("black", "new", True, pn, "triangle")
check("black only: no pin", "pin" not in P.parts)
check("pin_applies: only white + black as a new body",
      S.pin_applies("both", "new", True) and S.pin_applies("both", "join", False)
      and not S.pin_applies("both", "join", True) and not S.pin_applies("black", "new", True)
      and not S.pin_applies("white", "new", True))
P = S.plan("both", "new", True, N(wo=3.0, wh=2.0, bo=5.0, pin_length=2.0, pin_tol=0.15), "triangle")
check("a tile 3 mm up: the 2 mm pin does not reach, no hole, one line",
      "recess" not in P.parts and any("no hole" in t for t in P.notes), (P.parts, P.notes))

print("== pin outlines")
tri = S.pin_outlines("triangle", 3.0, 40.0, 30.0)
pts = tri[0]["pts"]
check("one triangle", len(tri) == 1 and tri[0]["kind"] == "poly" and len(pts) == 3)
check("counter-clockwise", S._area2(pts) > 0)
check("in the top-left corner, 1 mm in", min(p[0] for p in pts) == -19.0 and max(p[1] for p in pts) == 14.0)
check("legs 3 mm along the top and left edges", close(max(p[0] for p in pts) + 19.0, 3.0)
      and close(14.0 - min(p[1] for p in pts), 3.0))
rnd = S.pin_outlines("round", 3.0, 40.0, 30.0)
check("two round pins", len(rnd) == 2 and all(o["kind"] == "circle" and o["r"] == 1.5 for o in rnd))
check("at the two top corners", close(rnd[0]["c"], (-17.5, 12.5)) and close(rnd[1]["c"], (17.5, 12.5)))
check("none: no outline", S.pin_outlines("none", 3.0, 40.0, 30.0) == [])
check("a pin bigger than the corner is refused", raises(lambda: S.pin_outlines("triangle", 9.0, 16, 16)))
check("two round pins on a narrow tile are refused", raises(lambda: S.pin_outlines("round", 3.0, 8, 30)))
for tol in (0.1, 0.15, 0.3):
    g = S.grow(tri[0], tol)
    check(f"triangle grown {tol}: every edge {tol} out", close(S.edge_gap(tri[0], g), tol, 1e-9)
          and S._area2(g["pts"]) > S._area2(pts))
    gr = S.grow(rnd[0], tol)
    check(f"round grown {tol}: radius + {tol}", close(S.edge_gap(rnd[0], gr), tol))
g = S.grow(tri[0], 0.15)
legs = sorted(math.hypot(g["pts"][i][0] - g["pts"][i - 1][0], g["pts"][i][1] - g["pts"][i - 1][1])
              for i in range(3))
check("grown right triangle: legs 3 + 0.15 (2 + sqrt 2)", close(legs[0], 3.0 + 0.15 * (2 + math.sqrt(2)), 1e-9)
      and close(legs[1], legs[0], 1e-9), legs)
check("outline_points: a circle as a polygon", len(S.outline_points(rnd[0], 24)) == 24)

print("== stdlib only")
src = Path(S.__file__).read_text(encoding="utf-8")
imports = {line.split()[1].split(".")[0] for line in src.splitlines()
           if line.startswith(("import ", "from ")) and not line.startswith("from __future__")}
check("aruco_slabs imports only the stdlib", imports <= {"math", "dataclasses", "typing"}, imports)

print()
print(f"{PASS} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
