# -*- coding: utf-8 -*-
"""Acceptance: marker-to-marker transforms agree in Fusion, viser and the CAD.

    python tools/acceptance_aruco_frames.py <work_dir> <export_dir>

Zach, 2026-10-07: "take a screen shot of fusion and a screen shot of viser.
we can check that the relative transforms between the frames is identical in
both of them and also identical to just directly calculating it using the
actual optical frame transform."

<work_dir> holds, per camera NAME:
    fusion_<name>.png + fusion_cameras.json   (ArucoMarkers bridge camera_capture)
    viser_<name>.png  + viser_cameras.json    (tools/acceptance_viser_render.py)
<export_dir>/aruco/markers.json gives the truth: every tag's optical frame in
the world (from its Fusion Joint Origin), and the design units.

For each image every marker is detected and its pose solved (solvePnP,
IPPE_SQUARE, the marker's real side). Relative transforms T_i^-1 T_j are
independent of where the camera is, so the three sources can be compared
without trusting either renderer's extrinsics:

    fusion  vs  truth      (Fusion's own viewport pixels)
    viser   vs  truth      (the exported URDF's pixels)
    fusion  vs  viser

viser's intrinsics are exact (vertical fov, chosen by us). Fusion does not
document whether perspectiveAngle is the vertical or horizontal fov, so both
are tried and the one whose projection of the TRUE corners lands on the
detected corners is used -- reported, not assumed. Needs OpenCV + numpy.
"""
from __future__ import annotations

import itertools
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

UNIT = {"mm": 0.001, "cm": 0.01, "m": 1.0, "in": 0.0254}
DICTS = {n: getattr(cv2.aruco, n) for n in dir(cv2.aruco) if n.startswith("DICT_")}


def look_at_R(eye, target, up):
    """World->camera rotation, OpenCV camera (+x right, +y down, +z forward)."""
    z = np.asarray(target, float) - np.asarray(eye, float)
    z /= np.linalg.norm(z)
    u = np.asarray(up, float)
    y = -(u - (u @ z) * z)
    y /= np.linalg.norm(y)
    x = np.cross(y, z)
    return np.stack([x, y, z], axis=0)


def K_of(w, h, fov, vertical=True):
    f = (h / 2.0 if vertical else w / 2.0) / math.tan(fov / 2.0)
    return np.array([[f, 0, w / 2.0], [0, f, h / 2.0], [0, 0, 1.0]])


def obj_points(L):
    return np.array([[-L / 2, L / 2, 0], [L / 2, L / 2, 0], [L / 2, -L / 2, 0],
                     [-L / 2, -L / 2, 0]], dtype=np.float64)


def detect(img, dictionary):
    det = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(DICTS[dictionary]),
                                  cv2.aruco.DetectorParameters())
    det.getDetectorParameters().cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    corners, ids, _ = det.detectMarkers(img)
    return {} if ids is None else {int(i): c.reshape(4, 2) for i, c in zip(ids.flatten(), corners)}


AMBIGUITY_RATIO = 4.0


def pnp(corners, L, K):
    """Pose of one square marker, or None when it is AMBIGUOUS.

    A small planar square seen at a slant has two IPPE solutions that fit
    the corners almost equally well (the classic flip); picking the lower
    reprojection error then picks the wrong one now and then -- seen here at
    0.48 vs 0.77 px. A pose is kept only when the runner-up is at least
    AMBIGUITY_RATIO times worse. Decided from the image alone, never from
    the truth or the other renderer.
    """
    n, rvecs, tvecs, errs = cv2.solvePnPGeneric(obj_points(L), corners.astype(np.float64),
                                                K, None, flags=cv2.SOLVEPNP_IPPE_SQUARE)
    errs = np.array(errs).ravel()
    order = np.argsort(errs)
    if n > 1 and errs[order[1]] < AMBIGUITY_RATIO * max(errs[order[0]], 1e-6):
        return None
    k = order[0]
    T = np.eye(4)
    T[:3, :3] = cv2.Rodrigues(rvecs[k])[0]
    T[:3, 3] = np.asarray(tvecs[k]).ravel()
    return T


def rel_err(A, B):
    D = np.linalg.inv(A) @ B
    t = np.linalg.norm(D[:3, 3]) * 1000.0
    ang = math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(D[:3, :3]) - 1) / 2))))
    return t, ang


def main() -> int:
    work, export = Path(sys.argv[1]), Path(sys.argv[2])
    doc = json.loads((export / "aruco" / "markers.json").read_text(encoding="utf-8"))
    payload_units = json.loads((export.parent / "Untitled" / "fusion360_data.json").read_text(
        encoding="utf-8"))["design"]["units"] if (export.parent / "Untitled").is_dir() else "mm"
    to_m = UNIT[payload_units]
    truth, L_of, dict_of = {}, {}, {}
    for inst in doc["instances"]:
        if inst["hidden"] or inst["spec"].get("layout", "single") != "single":
            continue
        T = np.array(inst.get("optical_world") or inst["world"], float).reshape(4, 4)
        T[:3, 3] *= to_m
        mid = inst["spec"]["marker_id"]
        truth[mid], L_of[mid], dict_of[mid] = T, inst["spec"]["marker_length_m"], inst["spec"]["dictionary"]
    fcam = json.loads((work / "fusion_cameras.json").read_text())
    # exact-intrinsics sources: viser's own renders, and/or our z-buffered
    # rasterisation of the same exported URDF (tools/render_urdf_ortho.py
    # --perspective) when the browser could not hold a viser client
    exact = {src: json.loads((work / f"{src}_cameras.json").read_text())
             for src in ("viser", "urdf") if (work / f"{src}_cameras.json").exists()}
    report = {"units": payload_units, "sources": ["fusion"] + list(exact), "views": {},
              "pairs": []}
    keys = [f"{s}_vs_truth" for s in ["fusion"] + list(exact)] + \
           [f"fusion_vs_{s}" for s in exact]
    worst = {k: [0.0, 0.0] for k in keys}
    names = sorted(set(fcam) & set.intersection(*[set(c["cameras"]) for c in exact.values()])) \
        if exact else sorted(fcam)

    def detect_all(img):
        out = {}
        for d in sorted(set(dict_of.values())):
            out.update(detect(img, d))
        return out

    resid = {}                                   # source -> list of corner residuals (px)
    camera_of = {}                               # (source, view) -> (R, eye, K)

    def project(i, R, eye, K):
        P = (truth[i] @ np.c_[obj_points(L_of[i]), np.ones(4)].T)[:3].T
        cam = (R @ (P - eye).T).T
        uv = (K @ cam.T).T
        return uv[:, :2] / uv[:, 2:]

    def reproj(det, R, eye, K, src=None):
        errs = []
        for i, c in det.items():
            if i in truth:
                e = np.linalg.norm(project(i, R, eye, K) - c, axis=1)
                if src:
                    resid.setdefault(src, []).extend(e.tolist())
                errs.append(float(e.max()))
        return max(errs) if errs else None

    for name in names:
        poses, view = {}, {}
        for src, cams in exact.items():
            img = cv2.imread(str(work / cams["cameras"][name]["file"]), cv2.IMREAD_GRAYSCALE)
            K = K_of(cams["width"], cams["height"], cams["fov_vertical_rad"], True)
            det = detect_all(img)
            raw = {i: pnp(c, L_of[i], K) for i, c in det.items() if i in truth}
            poses[src] = {i: T for i, T in raw.items() if T is not None}
            view[f"{src}_ambiguous"] = sorted(i for i, T in raw.items() if T is None)
            vc = cams["cameras"][name]
            R = look_at_R(np.array(vc["eye"]), np.array(vc["target"]), vc["up"])
            view[f"{src}_ids"] = sorted(poses[src])
            view[f"{src}_truth_reprojection_px"] = reproj(det, R, np.array(vc["eye"]), K, src)
            camera_of[(src, name)] = (R, np.array(vc["eye"]), K)
        fc = fcam[name]
        fimg = cv2.imread(str(work / f"fusion_{name}.png"), cv2.IMREAD_GRAYSCALE)
        h, w = fimg.shape
        df = detect_all(fimg)
        eye = np.array(fc["eye_cm"]) * 0.01
        R = look_at_R(eye, np.array(fc["target_cm"]) * 0.01, fc["up"])
        best = None
        for vertical in (True, False):
            K = K_of(w, h, fc["perspective_angle_rad"], vertical)
            e = reproj(df, R, eye, K)
            e = float("inf") if e is None else e
            if best is None or e < best[0]:
                best = (e, vertical, K)
        raw = {i: pnp(c, L_of[i], best[2]) for i, c in df.items() if i in truth}
        poses["fusion"] = {i: T for i, T in raw.items() if T is not None}
        reproj(df, R, eye, best[2], "fusion")
        camera_of[("fusion", name)] = (R, eye, best[2])
        view["fusion_ambiguous"] = sorted(i for i, T in raw.items() if T is None)
        view["fusion_ids"] = sorted(poses["fusion"])
        view["fusion_fov_is"] = "vertical" if best[1] else "horizontal"
        view["fusion_truth_reprojection_px"] = round(best[0], 2)
        report["views"][name] = view
        common = sorted(set.intersection(*[set(p) for p in poses.values()]))
        for i, j in itertools.combinations(common, 2):
            Tt = np.linalg.inv(truth[i]) @ truth[j]
            rel = {src: np.linalg.inv(p[i]) @ p[j] for src, p in poses.items()}
            row = {"view": name, "pair": [i, j],
                   "distance_mm": round(float(np.linalg.norm(Tt[:3, 3])) * 1000, 1)}
            for k in keys:
                a, b = k.split("_vs_")
                A, B = rel[a], (Tt if b == "truth" else rel[b])
                t, ang = rel_err(A, B)
                row[k] = [round(t, 3), round(ang, 3)]
                worst[k] = [max(worst[k][0], t), max(worst[k][1], ang)]
            report["pairs"].append(row)
    report["worst"] = {k: {"mm": round(v[0], 3), "deg": round(v[1], 3)} for k, v in worst.items()}
    # What can this measurement resolve? Corner noise is each source's own RMS
    # residual against the projected truth; a Monte Carlo of the same PnP at
    # that noise, per pair and view, gives the 99th percentile a CORRECT frame
    # would still show. A pair passes when its error is inside that floor AND
    # under a hard ceiling (so a noisy image cannot excuse a real offset).
    rng = np.random.default_rng(0)
    sigma = {src: float(np.sqrt(np.mean(np.square(v)))) for src, v in resid.items()}
    report["corner_noise_rms_px"] = {k: round(v, 3) for k, v in sigma.items()}
    CEIL_MM, CEIL_DEG, TRIALS = 3.0, 3.0, 300

    def floor(src, view, i, j):
        R, eye, K = camera_of[(src, view)]
        ui, uj = project(i, R, eye, K), project(j, R, eye, K)
        Tc = np.eye(4)
        Tc[:3, :3], Tc[:3, 3] = R, -R @ eye
        Tt = np.linalg.inv(truth[i]) @ truth[j]
        ts, angs = [], []
        for _ in range(TRIALS):
            a = pnp(ui + rng.normal(0, sigma[src], ui.shape), L_of[i], K)
            b = pnp(uj + rng.normal(0, sigma[src], uj.shape), L_of[j], K)
            if a is None or b is None:
                continue
            t, g = rel_err(np.linalg.inv(a) @ b, Tt)
            ts.append(t)
            angs.append(g)
        return (float(np.percentile(ts, 99)), float(np.percentile(angs, 99))) if ts else (CEIL_MM, CEIL_DEG)

    failures = []
    for row in report["pairs"]:
        i, j = row["pair"]
        floors = {}
        for src in report["sources"]:
            if (src, row["view"]) in camera_of:
                floors[src] = floor(src, row["view"], i, j)
        row["noise_floor_99"] = {k: [round(v[0], 3), round(v[1], 3)] for k, v in floors.items()}
        for k in keys:
            a, b = k.split("_vs_")
            fa = floors.get(a, (CEIL_MM, CEIL_DEG))
            fb = floors.get(b, (0.0, 0.0)) if b != "truth" else (0.0, 0.0)
            tol = (min(CEIL_MM, math.hypot(fa[0], fb[0])), min(CEIL_DEG, math.hypot(fa[1], fb[1])))
            t, g = row[k]
            row.setdefault("tolerance", {})[k] = [round(tol[0], 3), round(tol[1], 3)]
            if t > max(tol[0], 0.25) or g > max(tol[1], 0.25):
                failures.append(f"{row['view']} {i}-{j} {k}: {t:.2f} mm / {g:.2f} deg > "
                                f"{tol[0]:.2f} mm / {tol[1]:.2f} deg")
    report["failures"] = failures
    report["ceiling"] = {"mm": CEIL_MM, "deg": CEIL_DEG}
    ok = bool(report["pairs"]) and not failures
    report["verdict"] = "PASS" if ok else "FAIL"
    tol_mm, tol_deg = CEIL_MM, CEIL_DEG
    (work / "acceptance.json").write_text(json.dumps(report, indent=1))
    for name, v in report["views"].items():
        print(f"view {name}: " + " | ".join(f"{k}={v[k]}" for k in v))
    print(f"{len(report['pairs'])} marker pairs")
    for k, v in report["worst"].items():
        print(f"  worst {k:<16} {v['mm']:.3f} mm  {v['deg']:.3f} deg")
    print("corner noise (RMS px):", report["corner_noise_rms_px"])
    for f in failures:
        print("  over the noise floor:", f)
    print(report["verdict"], f"(per pair: inside the 99% PnP noise floor at each source's "
                             f"measured corner noise, and under {tol_mm} mm / {tol_deg} deg)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
