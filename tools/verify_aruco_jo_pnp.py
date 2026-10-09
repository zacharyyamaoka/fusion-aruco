# -*- coding: utf-8 -*-
"""Independent check: is each tag's optical frame (and its Joint Origin) the
pose OpenCV solves from a render of the tag?

    python tools/verify_aruco_jo_pnp.py <dir> <label> [<label> ...]

<dir>/<label>.json comes from the ArucoMarkers live test
(`aruco_live_test.capture_tags`, run by `live_test --arg out_dir=<dir>`):
one exact perspective Fusion viewport render per tag, the camera Fusion read
back, the tag's spec, its optical frame and its Joint Origin, both in the
root (cm). Needs OpenCV + numpy only; does not import bam_cad_core.

Per render: detect the target the way a robot would --
    single   ArucoDetector, the marker's 4 corners, solvePnP IPPE_SQUARE on
             OpenCV's standard object points (centre, +y up, +z out)
    grid     GridBoard + matchImagePoints + solvePnP
    charuco  CharucoDetector + CharucoBoard.matchImagePoints + solvePnP
-- then take the pose to the root through the render's camera (Fusion's
perspectiveAngle is the VERTICAL fov; measured in acceptance_aruco_frames)
and compare it with the optical frame and the Joint Origin.

A ChArUco board with too few chessboard corners for a pose (a 4 x 2 has 3)
is solved on its markers' corners instead (marker_points), in the same
board frame. The Joint Origin is OpenCV's frame, always (2026-10-09).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

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


def frame_T(f, scale=0.01):
    """(o, x, y, z) lists in cm -> 4x4 in metres."""
    T = np.eye(4)
    T[:3, 0], T[:3, 1], T[:3, 2] = f[1], f[2], f[3]
    T[:3, 3] = np.asarray(f[0], float) * scale
    return T


def err(A, B):
    D = np.linalg.inv(A) @ B
    t = float(np.linalg.norm(A[:3, 3] - B[:3, 3])) * 1000.0
    ang = math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(D[:3, :3]) - 1) / 2))))
    return t, ang


def detector(spec):
    d = cv2.aruco.getPredefinedDictionary(DICTS[spec["dictionary"]])
    p = cv2.aruco.DetectorParameters()
    p.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    return d, p


def board_of(spec, d):
    layout = spec.get("layout", "single")
    cols, rows, first = int(spec["cols"]), int(spec["rows"]), int(spec["marker_id"])
    if layout == "grid":
        ids = np.arange(first, first + cols * rows, dtype=np.int32)
        return cv2.aruco.GridBoard((cols, rows), spec["marker_length_m"], spec["separation_m"],
                                   d, ids)
    n = (cols * rows) // 2
    ids = np.arange(first, first + n, dtype=np.int32)
    b = cv2.aruco.CharucoBoard((cols, rows), spec["square_length_m"], spec["marker_length_m"],
                               d, ids)
    b.setLegacyPattern(bool(spec.get("legacy")))
    return b


def marker_points(board, corners, ids):
    """(object points, image points) of a board's detected markers: each
    marker's 4 corners, in the board's frame (Board.getObjPoints)."""
    known = [int(i) for i in np.asarray(board.getIds()).ravel()]
    objs = board.getObjPoints()
    obj, imgp = [], []
    for c, i in zip(corners, np.asarray(ids).ravel()):
        if int(i) in known:
            obj.append(np.asarray(objs[known.index(int(i))], np.float64).reshape(4, 3))
            imgp.append(np.asarray(c, np.float64).reshape(4, 2))
    if not obj:
        return None, None
    return np.concatenate(obj), np.concatenate(imgp)


def solve(img, spec, K):
    """-> (T_cam_target 4x4, info) or (None, info)."""
    layout = spec.get("layout", "single")
    d, p = detector(spec)
    info = {"layout": layout}
    if layout == "charuco":
        board = board_of(spec, d)
        cd = cv2.aruco.CharucoDetector(board, cv2.aruco.CharucoParameters(), p)
        ch_c, ch_ids, m_c, m_ids = cd.detectBoard(img)
        info["markers"] = 0 if m_ids is None else len(m_ids)
        info["charuco_corners"] = 0 if ch_ids is None else len(ch_ids)
        if ch_ids is not None and len(ch_ids) >= 4:
            obj, imgp = board.matchImagePoints(ch_c, ch_ids)
        elif m_ids is not None and len(m_ids):
            # a small board -- 4 x 2 squares has 3 chessboard corners, too few
            # for a pose -- solved on its markers' corners instead, which
            # CharucoBoard.getObjPoints gives in the same board frame
            obj, imgp = marker_points(board, m_c, m_ids)
            info["points_from"] = "marker corners"
        else:
            return None, info
    else:
        det = cv2.aruco.ArucoDetector(d, p)
        corners, ids, _ = det.detectMarkers(img)
        info["markers"] = 0 if ids is None else len(ids)
        if ids is None:
            return None, info
        if layout == "grid":
            board = board_of(spec, d)
            obj, imgp = board.matchImagePoints(corners, ids)
        else:
            want = int(spec["marker_id"])
            hit = [c for c, i in zip(corners, ids.ravel()) if int(i) == want]
            if not hit:
                return None, info
            L = spec["marker_length_m"]
            obj = np.array([[-L / 2, L / 2, 0], [L / 2, L / 2, 0], [L / 2, -L / 2, 0],
                            [-L / 2, -L / 2, 0]], dtype=np.float64)
            imgp = hit[0].reshape(4, 2).astype(np.float64)
            n, rvecs, tvecs, errs = cv2.solvePnPGeneric(obj, imgp, K, None,
                                                        flags=cv2.SOLVEPNP_IPPE_SQUARE)
            errs = np.array(errs).ravel()
            k = int(np.argmin(errs))
            info["ippe_errors_px"] = [round(float(e), 3) for e in errs]
            T = np.eye(4)
            T[:3, :3] = cv2.Rodrigues(rvecs[k])[0]
            T[:3, 3] = np.asarray(tvecs[k]).ravel()
            info["points"] = 4
            return T, info
    if obj is None or len(obj) < 4:
        return None, info
    obj = obj.reshape(-1, 3).astype(np.float64)
    imgp = imgp.reshape(-1, 2).astype(np.float64)
    ok, rvec, tvec = cv2.solvePnP(obj, imgp, K, None, flags=cv2.SOLVEPNP_ITERATIVE)
    if not ok:
        return None, info
    proj, _ = cv2.projectPoints(obj, rvec, tvec, K, None)
    info["points"] = len(obj)
    info["reprojection_rms_px"] = round(float(np.sqrt(np.mean(np.sum(
        (proj.reshape(-1, 2) - imgp) ** 2, axis=1)))), 3)
    T = np.eye(4)
    T[:3, :3] = cv2.Rodrigues(rvec)[0]
    T[:3, 3] = tvec.ravel()
    return T, info


def main() -> int:
    work = Path(sys.argv[1])
    rows, bad = [], []
    for label in sys.argv[2:]:
        for cap in json.loads((work / f"{label}.json").read_text(encoding="utf-8")):
            img = cv2.imread(str(work / cap["file"]), cv2.IMREAD_GRAYSCALE)
            h, w = img.shape
            fov = cap["perspective_angle_rad"]
            f = (h / 2.0) / math.tan(fov / 2.0)
            K = np.array([[f, 0, w / 2.0], [0, f, h / 2.0], [0, 0, 1.0]])
            eye = np.array(cap["eye_cm"]) * 0.01
            R = look_at_R(eye, np.array(cap["target_cm"]) * 0.01, cap["up"])
            T_cam_world = np.eye(4)
            T_cam_world[:3, :3], T_cam_world[:3, 3] = R, -R @ eye
            spec = cap["spec"]
            T_ct, info = solve(img, spec, K)
            row = {"label": label, "key": cap["key"], "layout": spec.get("layout"),
                   "mode": cap.get("mode"), "raise_mm": round(spec.get("raise_m", 0) * 1000, 3),
                   "dictionary": spec["dictionary"], "id": spec["marker_id"], **info}
            opt = frame_T(cap["optical_world_cm"])
            jo_cv = frame_T(cap["jo_world_cm"]) if cap.get("jo_world_cm") else None
            if T_ct is None:
                row["detected"] = False
            else:
                row["detected"] = True
                pnp = np.linalg.inv(T_cam_world) @ T_ct
                row["pnp_vs_optical"] = [round(v, 3) for v in err(pnp, opt)]
                # the same error in the optical frame's own axes (mm): +z = OpenCV's z
                d = np.linalg.inv(opt) @ pnp
                row["pnp_in_optical_xyz_mm"] = [round(float(v) * 1000.0, 3) for v in d[:3, 3]]
                if jo_cv is not None:
                    row["pnp_vs_joint_origin"] = [round(v, 3) for v in err(pnp, jo_cv)]
            if jo_cv is not None:
                row["joint_origin_vs_optical"] = [round(v, 6) for v in err(jo_cv, opt)]
            rows.append(row)
    for r in rows:
        print(json.dumps(r))
    (work / "pnp_report.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
