"""OpenCV's own detector against every Origin of every layout (offline).

Zach, 2026-10-09: "100% I always want the frame to match perfectly OpenCV.
If OpenCV doesn't support it I don't want to either." The Joint Origin is
OpenCV's frame; the Origin choice only lays the pattern on its footprint
(bam_cad_core.aruco_origin) -- a rectangular board at 90 / 270 as OpenCV's
board with its cols / rows swapped, on its side.

For every layout x all four Origins:

  1. the add-in's own aruco_feature.evaluate() runs (on a stand-in adsk: a
     sketch area whose frame is turned 30 deg and moved, so no axis mix-up
     can hide) and its own part_body("pattern") is built -- a CSG tree of
     the black cells as the add-in would hand them to Fusion
  2. that body is rasterized AS IT LIES ON THE FACE (point containment, in
     the frame the tag was laid out in) and warped into a pinhole camera
     25 deg off the face's normal
  3. OpenCV detects it with ITS OWN board object built from the record's
     spec -- cv2.aruco.GridBoard / CharucoBoard((cols, rows), ...) with the
     record's ids and legacy flag (a single marker: IPPE_SQUARE on its
     standard corners) -- and solvePnP gives the pose
     (tools/verify_aruco_jo_pnp.solve: the same code that checks Fusion's
     renders; a 4 x 2 ChArUco, 3 inner corners, is solved on its markers)
  4. the pose is compared with what the add-in records (the record's
     optical frame, through a JSON round trip), the Joint Origin it places
     (ensure_joint_origin's target), the ghost's axes (aruco_preview.triad)
     and OpenCV's frame worked out BY HAND for that Origin from OpenCV's
     conventions alone (aruco_origin.opencv_in_layout): ~0.1 mm / 0.1 deg
  5. the ids OpenCV reads are the record's, every marker found; the board's
     footprint on the face is the same for all four Origins

A second pass repeats it with Corner mark "cut" (a ChArUco board's black
square cut, a triangle elsewhere): OpenCV still finds every marker, the
same pose.

Run:  <a python with opencv-contrib + numpy> tests/test_aruco_frame_pnp.py
      (prints SKIP and exits 0 without cv2)
"""
from __future__ import annotations

import json
import math
import sys
import types
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
try:
    import cv2
    import numpy as np
except ImportError:
    print("SKIP: needs cv2 + numpy")
    sys.exit(0)
sys.path.insert(0, str(ROOT / "ArucoMarkers"))
sys.path.insert(0, str(ROOT / "tools"))
import verify_aruco_jo_pnp as V  # noqa: E402

try:                                    # labels carry × ↻ °: never die on cp1252
    sys.stdout.reconfigure(errors="replace")
except AttributeError:
    pass
PASS = 0
FAIL = []
TOL_MM, TOL_DEG = 0.1, 0.1


def check(label, condition, detail=""):
    global PASS
    if condition:
        PASS += 1
        print(f"  ok  {label}")
    else:
        FAIL.append(label)
        print(f"FAIL  {label}  {detail}")


# ------------------------------------------------ stand-in adsk (CSG bodies)

adsk = types.ModuleType("adsk")
core = types.ModuleType("adsk.core")
fusion = types.ModuleType("adsk.fusion")
adsk.core, adsk.fusion = core, fusion
sys.modules.update({"adsk": adsk, "adsk.core": core, "adsk.fusion": fusion})


def _attr(mod):
    def get(name):
        if name.endswith("Handler"):
            cls = type(name, (), {"__init__": lambda self, *a, **k: None})
            setattr(mod, name, cls)
            return cls
        raise AttributeError(name)
    return get


core.__getattr__ = _attr(core)
fusion.__getattr__ = _attr(fusion)


class _Vec:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = float(x), float(y), float(z)

    @classmethod
    def create(cls, x=0.0, y=0.0, z=0.0):
        return cls(x, y, z)

    def t(self):
        return (self.x, self.y, self.z)


core.Point3D = _Vec
core.Vector3D = _Vec
core.OrientedBoundingBox3D = types.SimpleNamespace(
    create=lambda c, L, W, l, w, h: ("box", c.t(), L.t(), W.t(), float(l), float(w), float(h)))


class Body:
    def __init__(self, tree):
        self.tree = tree


class _TBM:
    def createBox(self, obb):
        return Body(obb)

    def createCylinderOrCone(self, p0, r0, p1, r1):
        return Body(("cyl", p0.t(), p1.t(), float(r0)))

    def booleanOperation(self, target, tool, op):
        target.tree = ({0: "union", 1: "diff", 2: "and"}[op], target.tree, tool.tree)
        return True


_TBM_ONE = _TBM()
fusion.TemporaryBRepManager = types.SimpleNamespace(get=lambda: _TBM_ONE)
fusion.BooleanTypes = types.SimpleNamespace(UnionBooleanType=0, DifferenceBooleanType=1,
                                            IntersectionBooleanType=2)


class Profile:
    assemblyContext = None


fusion.Profile = Profile


def contains(tree, P, eps=1e-9):
    """Point containment of a CSG tree for N points at once (N x 3 cm)."""
    k = tree[0]
    if k == "box":
        c, L, W, l, w, h = tree[1:]
        L, W = np.asarray(L) / np.linalg.norm(L), np.asarray(W) / np.linalg.norm(W)
        H = np.cross(L, W)
        d = P - np.asarray(c)
        return ((np.abs(d @ L) <= l / 2 + eps) & (np.abs(d @ W) <= w / 2 + eps)
                & (np.abs(d @ H) <= h / 2 + eps))
    if k == "cyl":
        p0, p1, r = tree[1:]
        ax = np.asarray(p1) - np.asarray(p0)
        n = np.linalg.norm(ax)
        u = ax / n
        d = P - np.asarray(p0)
        t = d @ u
        rad = np.sqrt(np.maximum(0.0, np.sum(d * d, axis=1) - t * t))
        return (t >= -eps) & (t <= n + eps) & (rad <= r + eps)
    if k == "union":
        return contains(tree[1], P) | contains(tree[2], P)
    if k == "diff":
        return contains(tree[1], P) & ~contains(tree[2], P)
    if k == "and":
        return contains(tree[1], P) & contains(tree[2], P)
    raise ValueError(k)


import aruco_core  # noqa: E402
import aruco_feature as F  # noqa: E402
import aruco_preview as PV  # noqa: E402
import aruco_tags as T  # noqa: E402

M = aruco_core.module("markers")
OR = aruco_core.module("aruco_origin")

# the sketch area: its frame turned 30 deg about z and moved (cm)
A30 = math.radians(30.0)
O = np.array([1.0, 2.0, 0.5])
XB = np.array([math.cos(A30), math.sin(A30), 0.0])
YB = np.array([-math.sin(A30), math.cos(A30), 0.0])
ZB = np.array([0.0, 0.0, 1.0])


def evaluate(layout, w_mm, h_mm, turn, numbers, force=None, legacy=False, mark="none"):
    w, h = w_mm / 10.0, h_mm / 10.0
    F.profile_rect = lambda t, s: (list(O), list(XB), list(YB), list(ZB), w, h)
    F._profile_loops_m = lambda t, o, x, y: [[(-w / 200, -h / 200), (w / 200, -h / 200),
                                               (w / 200, h / 200), (-w / 200, h / 200)]]
    s = F.default_settings()
    s.update(mode="black", op="join", layout=layout, has_body=True, source="profile",
             z_sign=1, x_sketch=[1.0, 0.0], turn=turn, force=list(force) if force else None,
             legacy=legacy, corner_mark=mark, origin_v=OR.ORIGIN_RULE, size_auto=False)
    n = F.default_numbers("black")
    n.update(numbers)
    return s, n, F.evaluate(Profile(), s, n)


def T4(frame, scale=0.01):
    """(o, x, y, z) cm -> 4x4 metres."""
    out = np.eye(4)
    out[:3, 0], out[:3, 1], out[:3, 2] = frame[1], frame[2], frame[3]
    out[:3, 3] = np.asarray(frame[0], float) * scale
    return out


def _box_corners(tree):
    c, L, W, l, w, h = tree[1:]
    L, W = np.asarray(L) / np.linalg.norm(L), np.asarray(W) / np.linalg.norm(W)
    H = np.cross(L, W)
    return [np.asarray(c) + L * a * l / 2 + W * b * w / 2 + H * d * h / 2
            for a in (-1, 1) for b in (-1, 1) for d in (-1, 1)]


def _paint(tree, mask, grid, op_or=True):
    """mask |= tree (or &= ~tree when not op_or), evaluated only inside each
    box's own footprint on the grid (a union of ~100 boxes stays cheap)."""
    k = tree[0]
    if k == "union":
        _paint(tree[1], mask, grid, op_or)
        _paint(tree[2], mask, grid, op_or)
        return
    if k == "diff":
        if not op_or:
            raise ValueError("a difference inside a cut tool")
        part = np.zeros_like(mask)
        _paint(tree[1], part, grid, True)
        tool = np.zeros_like(mask)
        _paint(tree[2], tool, grid, True)
        mask |= part & ~tool
        return
    f0, s, half, n, zmid = grid
    if k == "box":
        pts = _box_corners(tree)
    else:                                                    # a cylinder: its box
        p0, p1, r = tree[1:]
        pts = [np.asarray(p0) + d for d in (np.array([sx, sy, 0.0]) * r
                                            for sx in (-1, 1) for sy in (-1, 1))]
        pts += [np.asarray(p1) + d for d in (np.array([sx, sy, 0.0]) * r
                                             for sx in (-1, 1) for sy in (-1, 1))]
    us = [float((p - f0) @ XB) for p in pts]
    vs = [float((p - f0) @ YB) for p in pts]
    c0 = max(0, int(math.floor((min(us) + half) / s)) - 1)
    c1 = min(n, int(math.ceil((max(us) + half) / s)) + 1)
    r0 = max(0, int(math.floor((half - max(vs)) / s)) - 1)
    r1 = min(n, int(math.ceil((half - min(vs)) / s)) + 1)
    if c1 <= c0 or r1 <= r0:
        return
    u = (np.arange(c0, c1) + 0.5) * s - half
    v = half - (np.arange(r0, r1) + 0.5) * s
    U, Vv = np.meshgrid(u, v)
    P = (f0[None, :] + U.reshape(-1, 1) * XB[None, :] + Vv.reshape(-1, 1) * YB[None, :]
         + zmid * ZB[None, :])
    hit = contains(tree, P).reshape(r1 - r0, c1 - c0)
    mask[r0:r1, c0:c1] |= hit


def rasterize(ev, n, px_per_mm, ss=4):
    """The add-in's pattern body as it lies on the face: a white image with
    the body's cross-section (mid-slab) black, anti-aliased (ss x ss
    samples a pixel), in the frame the tag was laid out in (XB, YB:
    un-turned), and A: its pixel (u, v, 1) -> plane (X, Y, 1) cm."""
    body = F.part_body("pattern", ev, n)
    L = ev["plan"].layers
    zmid = (L.pattern[0] + L.pattern[1]) / 20.0              # cm over the face plane
    f0 = np.asarray(F.face_origin(ev))
    tw, th = (v * 100.0 for v in ev["spec"].tile_size_m)
    s = 0.1 / px_per_mm                                      # cm per pixel
    npx = int(math.ceil((max(tw, th) + 0.8) / s))            # 4 mm margin all round
    half = npx * s / 2.0
    fine = np.zeros((npx * ss, npx * ss), dtype=bool)
    _paint(body.tree, fine, (f0, s / ss, half, npx * ss, zmid))
    ink = fine.reshape(npx, ss, npx, ss).mean(axis=(1, 3))
    img = np.round(255.0 * (1.0 - ink)).astype(np.uint8)
    A = np.array([[s, 0.0, -half + 0.5 * s], [0.0, -s, half - 0.5 * s], [0.0, 0.0, 1.0]])
    return img, A, f0


def render(ev, n, size=(2000, 1500), fov_deg=30.0, tilt_deg=25.0, azimuth_deg=35.0,
           roll_deg=10.0, px_per_mm=None):
    """The camera image and what the tool needs (K, the camera pose). A
    generic view: 25 deg off the face's normal, from 35 deg round from the
    layout's -y, rolled 10 deg -- a perfectly symmetric trapezoid trips
    IPPE_SQUARE on a single marker (3.7 px, 1.6 deg at one orientation of
    four; no real photo is that symmetric)."""
    spec = ev["spec"]
    if px_per_mm is None:
        # about the image's own scale (the target fills 70 % of its height)
        px_per_mm = max(10.0, 0.7 * size[1] / (max(spec.extent_m) * 1000.0))
    tex, A, f0 = rasterize(ev, n, px_per_mm)
    w, h = size
    fov = math.radians(fov_deg)
    f = (h / 2.0) / math.tan(fov / 2.0)
    K = np.array([[f, 0, w / 2.0], [0, f, h / 2.0], [0, 0, 1.0]])
    ext = max(spec.extent_m) * 100.0                               # cm
    dist = ext / (0.7 * 2.0 * math.tan(fov / 2.0))
    t, a, r = (math.radians(v) for v in (tilt_deg, azimuth_deg, roll_deg))
    c = f0
    side = math.cos(a) * YB + math.sin(a) * XB
    eye = c + dist * (math.cos(t) * ZB - math.sin(t) * side)
    R = V.look_at_R(eye, c, math.cos(r) * YB + math.sin(r) * XB)
    # the face plane (origin f0, axes XB / YB) into the camera, cm
    Hp = K @ np.column_stack([R @ XB, R @ YB, R @ (f0 - eye)])
    img = cv2.warpPerspective(tex, Hp @ A, (w, h), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=255)
    T_cw = np.eye(4)
    T_cw[:3, :3], T_cw[:3, 3] = R, -R @ (eye * 0.01)
    return img, K, T_cw


def by_hand(ev0, ev, layout, turn):
    """OpenCV's frame for this Origin from OpenCV's conventions alone
    (aruco_origin.opencv_in_layout), in the world, cm."""
    W, H = (v * 1000.0 for v in ev0["spec"].extent_m)              # as laid out, mm
    (ou, ov), xd, yd, zs = OR.opencv_in_layout(layout, turn, W, H)
    o = np.asarray(ev["tag"][0]) + (XB * ou + YB * ov) / 10.0
    return (list(o), list(XB * xd[0] + YB * xd[1]), list(XB * yd[0] + YB * yd[1]),
            list(ZB * zs))


def detected_ids(img, spec):
    d = cv2.aruco.getPredefinedDictionary(V.DICTS[spec.dictionary])
    det = cv2.aruco.ArucoDetector(d, cv2.aruco.DetectorParameters())
    _c, ids, _r = det.detectMarkers(img)
    return sorted(int(i) for i in np.asarray(ids).ravel()) if ids is not None else []


# key, layout, sketch area w x h mm, numbers, force (cols, rows), legacy
CASES = (
    ("single", "single", 40.0, 40.0, {"size": 28.0, "marker_id": 7}, None, False),
    ("grid 2x2", "grid", 40.0, 40.0, {"size": 14.0, "marker_id": 10}, (2, 2), False),
    ("grid 3x2", "grid", 60.0, 40.0, {"size": 14.0, "marker_id": 20}, (3, 2), False),
    ("grid 4x1", "grid", 80.0, 25.0, {"size": 14.0, "marker_id": 30}, (4, 1), False),
    ("charuco 3x3", "charuco", 60.0, 60.0, {"size": 13.5, "square": 18.0, "marker_id": 0},
     (3, 3), False),
    ("charuco 4x2 (Zach's)", "charuco", 110.0, 60.0,
     {"size": 18.75, "square": 25.0, "marker_id": 0}, (4, 2), False),
    ("charuco 5x4 legacy", "charuco", 100.0, 80.0,
     {"size": 13.5, "square": 18.0, "marker_id": 20}, (5, 4), True),
)


def run(mark="none"):
    worst = {}
    for name, layout, w, h, nums, force, legacy in CASES:
        _s0, n0, ev0 = evaluate(layout, w, h, 0, nums, force, legacy, mark)
        sp0 = ev0["spec"]
        if force:
            check(f"{name}: laid out as {force[0]} x {force[1]}",
                  (sp0.cols, sp0.rows) == tuple(force), (sp0.cols, sp0.rows))
        foot0 = None
        for turn in OR.TURNS_CW:
            label = f"{name} [{OR.label(layout, turn)}]" + (f" mark {mark}" if mark != "none" else "")
            s, n, ev = evaluate(layout, w, h, turn, nums, force, legacy, mark)
            spec = ev["spec"]
            check(f"{label}: the board as built "
                  f"({spec.cols} x {spec.rows}, ids {spec.ids[0]}..{spec.ids[-1]})",
                  (spec.cols, spec.rows) == OR.built(layout, turn, sp0.cols, sp0.rows)
                  and spec.ids == sp0.ids and ev["turn"] == turn)
            # what the add-in records, the Joint Origin it places, the ghost
            o, x, y, z = ev["tag"]
            rec = M.TagDef(key="k", component="", spec=spec, origin=list(o), x_axis=list(x),
                           y_axis=list(y), z_axis=list(z)).to_dict()
            tag = M.TagDef.from_dict(json.loads(json.dumps(rec)), print)
            record = T4(T.optical_frame_cm(tag))
            ghost = T4(PV.triad(ev))
            hand = T4(by_hand(ev0, ev, layout, turn))
            check(f"{label}: record = ghost = OpenCV by hand (exact)",
                  np.abs(record - ghost).max() < 1e-12 and np.abs(record - hand).max() < 1e-9,
                  (np.abs(record - ghost).max(), np.abs(record - hand).max()))
            img, K, T_cw = render(ev, n)
            # the footprint on the face: the ink's extent, as laid out
            ink = np.argwhere(rasterize(ev, n, 6.0, ss=1)[0] == 0)
            foot = (ink.min(axis=0).tolist(), ink.max(axis=0).tolist())
            if foot0 is None:
                foot0 = foot
            elif mark == "none":
                check(f"{label}: the same footprint on the face (±1 px at 6 px/mm)",
                      max(abs(a - b) for p, q in zip(foot, foot0) for a, b in zip(p, q)) <= 1,
                      (foot, foot0))
            if mark != "none":
                # the mark is in the picture: ink at the triangle, none in the
                # cut corner of a ChArUco board's black square
                mk = ev["mark"]
                tex, A, f0 = rasterize(ev, n, 20.0, ss=1)
                cu, cv_ = (sum(p[i] for p in (mk.cut or mk.solid)) / 3.0 for i in (0, 1))
                o_, x_, y_, _z = F.mark_frame(ev)
                P = np.asarray(o_) + (np.asarray(x_) * cu + np.asarray(y_) * cv_) / 10.0
                px = np.linalg.inv(A) @ np.array([(P - f0) @ XB, (P - f0) @ YB, 1.0])
                ink_there = tex[int(round(px[1])), int(round(px[0]))] == 0
                want_ink = mk.kind == "triangle"
                # OpenCV's corner 0 / board origin by hand, and the pattern
                # extent's corner nearest the mark: the same one
                hf = by_hand(ev0, ev, layout, turn)
                k0 = np.asarray(hf[0])
                if layout == "single":
                    Lh = spec.marker_length_m * 50.0
                    k0 = k0 - np.asarray(hf[1]) * Lh + np.asarray(hf[2]) * Lh
                Wd, Hd = (v * 50.0 for v in spec.extent_m)
                cen = np.asarray(F.face_origin(ev))
                xs, ys = np.asarray(ev["tag"][1]), np.asarray(ev["tag"][2])
                corners = [cen + xs * a * Wd + ys * b * Hd for a in (-1, 1) for b in (-1, 1)]
                near_mark = min(range(4), key=lambda i: np.linalg.norm(corners[i] - P))
                near_k0 = min(range(4), key=lambda i: np.linalg.norm(corners[i] - k0))
                check(f"{label}: the {mk.kind} is in the picture at OpenCV's origin corner",
                      mk is not None and ink_there == want_ink and near_mark == near_k0,
                      (mk.kind, ink_there, near_mark, near_k0))
            ids = detected_ids(img, spec)
            check(f"{label}: OpenCV reads the record's ids, every marker", ids == sorted(spec.ids),
                  (ids, spec.ids))
            T_ct, info = V.solve(img, spec.to_dict(), K)
            if T_ct is None:
                check(f"{label}: OpenCV solves a pose", False, info)
                continue
            pnp = np.linalg.inv(T_cw) @ T_ct
            for what, ref in (("the record's OpenCV frame (= the Joint Origin's target)", record),
                              ("OpenCV's frame by hand", hand)):
                t_mm, deg = V.err(pnp, ref)
                cell = worst.setdefault((name, mark), {}).setdefault(turn, (0.0, 0.0))
                worst[(name, mark)][turn] = (max(cell[0], t_mm), max(cell[1], deg))
                check(f"{label}: PnP on {what} ({t_mm:.3f} mm, {deg:.3f} deg"
                      f"{', ' + info.get('points_from', '') if info.get('points_from') else ''})",
                      t_mm < TOL_MM and deg < TOL_DEG, info)
    return worst


def main():
    print("== every layout x every Origin: the add-in's body, OpenCV's own board")
    worst = run("none")
    print("== the same with Corner mark 'cut' (black square cut / triangle)")
    worst.update(run("cut"))
    print("== the Origin's words and order (aruco_origin)")
    check("boards: Top left, Top right, Bottom right, Bottom left (Rotate's order)",
          OR.corners_of("grid") == ["Top left", "Top right", "Bottom right", "Bottom left"])
    check("singles: Up: top, right, bottom, left",
          OR.corners_of("single") == ["Up: top", "Up: right", "Up: bottom", "Up: left"])
    check("Rotate: clockwise, back after four",
          [OR.rotate_cw(t) for t in OR.TURNS_CW] == [270, 180, 90, 0])
    print()
    print("PnP vs the record / the Joint Origin's target (mm / deg), per Origin:")
    print(f"  {'layout':<22} {'mark':<5} " + "  ".join(f"{OR.label('grid', t):>15}"
                                                       for t in OR.TURNS_CW))
    for (name, mark), row in sorted(worst.items()):
        print(f"  {name:<22} {mark:<5} " + "  ".join(
            f"{row[t][0]:6.3f} / {row[t][1]:5.3f}" if t in row else f"{'-':>15}"
            for t in OR.TURNS_CW))
    print()
    print(f"{PASS} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
