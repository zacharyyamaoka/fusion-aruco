"""OpenCV oracle for aruco_board (grid + ChArUco) and boards on print sheets.

ORACLE-SIDE ONLY — needs cv2 (with cv2.aruco), numpy and pypdfium2:

    PYTHONPATH=<dir with cv2+numpy+pypdfium2> python tools/verify_aruco_boards.py

  a. ink: for a spread of grid and ChArUco boards (several dictionaries,
     odd/even rows and cols, legacy on/off, first_id != 0) black_rects_m
     rasterised (numpy, independently) AND board_png must equal
     board.generateImage pixel for pixel, at integer px per module; and
     object_points / charuco_corners / ids must equal getObjPoints /
     getChessboardCorners / getIds;
  b. detection: each board rendered by board_png is detected — grid via
     detectMarkers + matchImagePoints + solvePnP, ChArUco via CharucoDetector —
     with every id (and every ChArUco corner) found;
  c. frame: boards are rendered through a pinhole camera at a KNOWN pose of
     the centre frame; the pose solvePnP recovers must equal
     T_cam_centre @ opencv_frame(spec);
  d. print: a grid and two ChArUco boards (and a single) on a letter sheet,
     rasterised at 600 dpi: every id found where the layout put it, marker
     sides and ChArUco square sides within +-0.15 mm.
  Results -> reports/media/aruco/verify_boards.json, sheet_boards_letter_p1.png,
  sample_boards_letter.pdf.
"""
from __future__ import annotations

import datetime
import json
import math
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pypdfium2 as pdfium

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "CADExporter"))
sys.path.insert(0, str(ROOT / "tools"))
from bam_cad_core import aruco_board, print_sheet  # noqa: E402
from bam_cad_core.aruco_board import BoardSpec  # noqa: E402
from bam_cad_core.print_sheet import PrintItem, SheetSettings  # noqa: E402
from verify_aruco_output import darkness_run, measure_bar  # noqa: E402

OUT_DIR = ROOT / "reports" / "media" / "aruco"
S = 10000.0              # px per metre for part (a): spec lengths = px / S
DPI = 600
SIDE_TOL_MM = 0.15
CORNER_TOL_MM = 0.2
ROT_TOL_DEG = 0.5
TRANS_TOL_MM = 2.0

# (cols, rows, dictionary, first_id, px per module, separation px)
GRIDS = [
    (3, 2, "DICT_4X4_50", 10, 5, 10),
    (2, 3, "DICT_6X6_250", 0, 4, 8),
    (4, 5, "DICT_APRILTAG_36h11", 100, 3, 6),
    (1, 4, "DICT_5X5_100", 90, 4, 4),
    (5, 5, "DICT_7X7_1000", 975, 3, 9),
    (3, 3, "DICT_ARUCO_ORIGINAL", 1000, 3, 5),
]
# (cols, rows, dictionary, first_id, px per module, square px, legacy)
CHARUCOS = [
    (5, 4, "DICT_4X4_50", 0, 5, 40, False),
    (5, 4, "DICT_4X4_50", 0, 5, 40, True),
    (5, 7, "DICT_5X5_100", 20, 4, 40, False),
    (5, 7, "DICT_5X5_100", 20, 4, 40, True),
    (4, 5, "DICT_6X6_250", 7, 4, 48, True),
    (6, 6, "DICT_APRILTAG_36h11", 300, 3, 36, False),
    (6, 6, "DICT_APRILTAG_36h11", 300, 3, 36, True),
    (3, 3, "DICT_ARUCO_ORIGINAL", 1015, 3, 31, False),
    (3, 4, "DICT_4X4_250", 200, 4, 32, False),
    (3, 4, "DICT_4X4_250", 200, 4, 32, True),
    (7, 5, "DICT_7X7_100", 50, 3, 37, False),
]


def cv_dict(name):
    return cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, name))


def flat(ids):
    return [] if ids is None else [int(v) for v in np.asarray(ids).reshape(-1)]


def subpix():
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    return params


def cv_board(spec, unit=1.0):
    """The OpenCV board for a spec, lengths multiplied by `unit`."""
    d = cv_dict(spec.dictionary)
    ids = np.array(aruco_board.ids(spec), np.int32)
    if spec.layout == "grid":
        return cv2.aruco.GridBoard((spec.cols, spec.rows), spec.marker_length_m * unit,
                                   spec.separation_m * unit, d, ids)
    board = cv2.aruco.CharucoBoard((spec.cols, spec.rows), spec.square_length_m * unit,
                                   spec.marker_length_m * unit, d, ids)
    board.setLegacyPattern(bool(spec.legacy))
    return board


def spread():
    out = []
    for cols, rows, name, first, k, sep in GRIDS:
        n = cv_dict(name).markerSize
        out.append((BoardSpec("grid", name, first, (n + 2) * k / S, rows=rows, cols=cols,
                              separation_m=sep / S), k))
    for cols, rows, name, first, k, sq, legacy in CHARUCOS:
        n = cv_dict(name).markerSize
        out.append((BoardSpec("charuco", name, first, (n + 2) * k / S, rows=rows, cols=cols,
                              square_length_m=sq / S, legacy=legacy), k))
    return out


def tag(spec):
    extra = " legacy" if spec.legacy else ""
    return "%s %s %dx%d #%d%s" % (spec.layout, spec.dictionary, spec.cols, spec.rows,
                                  spec.first_id, extra)


def numpy_raster(spec, px_per_m):
    w, h = aruco_board.extent_m(spec)
    W, H = int(round(w * px_per_m)), int(round(h * px_per_m))
    xs = (np.arange(W) + 0.5) / px_per_m - w / 2.0
    ys = h / 2.0 - (np.arange(H) + 0.5) / px_per_m
    ink = np.zeros((H, W), bool)
    for x0, y0, x1, y1 in aruco_board.black_rects_m(spec):
        cols = (xs >= x0) & (xs < x1)
        rows = (ys > y0) & (ys <= y1)
        ink |= rows[:, None] & cols[None, :]
    return ink


def decode(png):
    return cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)


# ------------------------------------------------------------------ a + b


def check_ink_and_points(specs):
    rows = []
    for spec, k in specs:
        board = cv_board(spec, S)
        w, h = aruco_board.extent_m(spec)
        W, H = int(round(w * S)), int(round(h * S))
        ref = board.generateImage((W, H), marginSize=0, borderBits=1) < 128
        mine = numpy_raster(spec, S)
        png = decode(aruco_board.board_png(spec, px_per_m=S)) < 128
        # object points in metres
        bm = cv_board(spec, 1.0)
        cv_ids = flat(bm.getIds())
        ours = aruco_board.object_points(spec)
        cv_obj = [np.asarray(o, np.float64).reshape(4, 3) for o in bm.getObjPoints()]
        obj_err = max(float(np.max(np.abs(cv_obj[i] - np.array(ours[mid]))))
                      for i, mid in enumerate(cv_ids))
        rec = {"board": tag(spec), "px_per_module": k, "size_px": [W, H],
               "raster_mismatch_px": int(np.count_nonzero(ref != mine)) if ref.shape == mine.shape else -1,
               "png_mismatch_px": int(np.count_nonzero(ref != png)) if ref.shape == png.shape else -1,
               "ids_match": cv_ids == aruco_board.ids(spec),
               "objpoints_max_err_m": obj_err}
        if spec.layout == "charuco":
            cc = np.asarray(bm.getChessboardCorners(), np.float64).reshape(-1, 3)
            rec["chess_corners_max_err_m"] = float(np.max(np.abs(cc - np.array(
                aruco_board.charuco_corners(spec)))))
        rec["pass"] = (rec["raster_mismatch_px"] == 0 and rec["png_mismatch_px"] == 0
                       and rec["ids_match"] and obj_err < 1e-6
                       and rec.get("chess_corners_max_err_m", 0) < 1e-6)
        rows.append(rec)
    return rows


def check_detection(specs):
    rows = []
    for spec, k in specs:
        n = cv_dict(spec.dictionary).markerSize
        module = spec.marker_length_m / (n + 2)
        px_per_m = 10.0 / module                      # 10 px per module
        img = decode(aruco_board.board_png(spec, px_per_m=px_per_m, quiet_m=2 * module))
        board = cv_board(spec, 1.0)
        K = np.array([[2000.0, 0, img.shape[1] / 2.0], [0, 2000.0, img.shape[0] / 2.0], [0, 0, 1]])
        rec = {"board": tag(spec)}
        if spec.layout == "grid":
            det = cv2.aruco.ArucoDetector(cv_dict(spec.dictionary), subpix())
            corners, ids, _ = det.detectMarkers(img)
            found = flat(ids)
            obj, imgp = board.matchImagePoints(corners, ids)
            rec["markers_found"] = len(found)
        else:
            det = cv2.aruco.CharucoDetector(board)
            ch_corners, ch_ids, m_corners, m_ids = det.detectBoard(img)
            found = flat(m_ids)
            rec["markers_found"] = len(found)
            rec["charuco_corners_found"] = len(flat(ch_ids))
            rec["charuco_corners_expected"] = (spec.cols - 1) * (spec.rows - 1)
            obj, imgp = board.matchImagePoints(ch_corners, ch_ids)
        ok, rvec, tvec = cv2.solvePnP(obj, imgp, K, None)
        proj, _ = cv2.projectPoints(obj, rvec, tvec, K, None)
        rec["reproj_rms_px"] = float(np.sqrt(np.mean(np.sum(
            (proj.reshape(-1, 2) - imgp.reshape(-1, 2)) ** 2, axis=1))))
        rec["markers_expected"] = len(aruco_board.ids(spec))
        rec["pass"] = (sorted(found) == aruco_board.ids(spec) and bool(ok)
                       and rec.get("charuco_corners_found", 0) == rec.get("charuco_corners_expected", 0)
                       and rec["reproj_rms_px"] < 0.5)
        rows.append(rec)
    return rows


# ------------------------------------------------------------------ c. pose


def to_mat(rvec, tvec):
    T = np.eye(4)
    T[:3, :3] = cv2.Rodrigues(np.asarray(rvec, np.float64).reshape(3))[0]
    T[:3, 3] = np.asarray(tvec, np.float64).reshape(3)
    return T


def rot_err_deg(Ra, Rb):
    c = (np.trace(Ra.T @ Rb) - 1.0) / 2.0
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def render_camera(spec, T_cam_centre, K, size, tex_module_px=12, ss=3):
    """Pinhole render of board_png with the CENTRE frame at T_cam_centre."""
    n = cv_dict(spec.dictionary).markerSize
    module = spec.marker_length_m / (n + 2)
    s = tex_module_px / module
    q = 2 * module
    tex = decode(aruco_board.board_png(spec, px_per_m=s, quiet_m=q))
    qpx = int(round(q * s))
    w, h = aruco_board.extent_m(spec)
    # texture pixel (OpenCV coords: centres at integers) -> centre frame (X, Y, 1)
    A = np.array([[1.0 / s, 0.0, (0.5 - qpx) / s - w / 2.0],
                  [0.0, -1.0 / s, h / 2.0 - (0.5 - qpx) / s],
                  [0.0, 0.0, 1.0]])
    R, t = T_cam_centre[:3, :3], T_cam_centre[:3, 3]
    K_hi = np.array([[ss, 0, (ss - 1) / 2.0], [0, ss, (ss - 1) / 2.0], [0, 0, 1]]) @ K
    H = K_hi @ np.column_stack([R[:, 0], R[:, 1], t]) @ A
    hi = cv2.warpPerspective(tex, H, (size[0] * ss, size[1] * ss), flags=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_CONSTANT, borderValue=70)
    return cv2.resize(hi, size, interpolation=cv2.INTER_AREA)


def solve_pose(spec, img, K):
    if spec.layout == "single":
        det = cv2.aruco.ArucoDetector(cv_dict(spec.dictionary), subpix())
        corners, ids, _ = det.detectMarkers(img)
        if flat(ids) != [spec.first_id]:
            return None, "ids %s" % flat(ids)
        obj = np.array(aruco_board.object_points(spec)[spec.first_id], np.float64)
        ok, rvec, tvec = cv2.solvePnP(obj, np.asarray(corners[0], np.float64).reshape(4, 2),
                                      K, None, flags=cv2.SOLVEPNP_IPPE_SQUARE)
        return (to_mat(rvec, tvec) if ok else None), "IPPE_SQUARE, standard object points"
    board = cv_board(spec, 1.0)
    if spec.layout == "grid":
        det = cv2.aruco.ArucoDetector(cv_dict(spec.dictionary), subpix())
        corners, ids, _ = det.detectMarkers(img)
        if sorted(flat(ids)) != aruco_board.ids(spec):
            return None, "ids %s" % flat(ids)
        obj, imgp = board.matchImagePoints(corners, ids)
        how = "detectMarkers + GridBoard.matchImagePoints + solvePnP"
    else:
        ch_corners, ch_ids, _, _ = cv2.aruco.CharucoDetector(board).detectBoard(img)
        if len(flat(ch_ids)) != (spec.cols - 1) * (spec.rows - 1):
            return None, "charuco corners %d" % len(flat(ch_ids))
        obj, imgp = board.matchImagePoints(ch_corners, ch_ids)
        how = "CharucoDetector + CharucoBoard.matchImagePoints + solvePnP"
    ok, rvec, tvec = cv2.solvePnP(obj, imgp, K, None)
    return (to_mat(rvec, tvec) if ok else None), how


def check_frames():
    size = (1600, 1200)
    K = np.array([[1500.0, 0, (size[0] - 1) / 2.0], [0, 1500.0, (size[1] - 1) / 2.0], [0, 0, 1]])
    specs = [
        BoardSpec("single", "DICT_4X4_50", 7, 0.06),
        BoardSpec("grid", "DICT_4X4_50", 10, 0.04, rows=2, cols=3, separation_m=0.01),
        BoardSpec("grid", "DICT_ARUCO_ORIGINAL", 1000, 0.035, rows=3, cols=3, separation_m=0.007),
        BoardSpec("charuco", "DICT_4X4_50", 0, 0.03, rows=4, cols=5, square_length_m=0.04),
        BoardSpec("charuco", "DICT_4X4_50", 0, 0.03, rows=4, cols=5, square_length_m=0.04,
                  legacy=True),
        BoardSpec("charuco", "DICT_5X5_100", 20, 0.021, rows=7, cols=5, square_length_m=0.03),
    ]
    # The centre frame's +z faces the viewer and a camera looks down its own
    # +z, so a board seen from the FRONT has R = tilt @ diag(1, -1, -1).
    # (Without the flip the camera sees the board from behind: mirrored ids.)
    poses = [((0.35, -0.45, 0.2), (0.015, -0.01, 0.42)),
             ((-0.5, 0.3, 2.6), (-0.02, 0.012, 0.5))]
    flip = np.diag([1.0, -1.0, -1.0, 1.0])
    rows = []
    for spec in specs:
        F = np.array(aruco_board.opencv_frame(spec), np.float64).reshape(4, 4)
        for rvec, tvec in poses:
            T_cc = to_mat(rvec, tvec) @ flip
            img = render_camera(spec, T_cc, K, size)
            T_est, how = solve_pose(spec, img, K)
            rec = {"board": tag(spec), "pose_rvec": rvec, "pose_t_m": tvec, "method": how}
            if T_est is None:
                rec["pass"] = False
                rows.append(rec)
                continue
            T_exp = T_cc @ F
            rec["rot_err_deg"] = round(rot_err_deg(T_est[:3, :3], T_exp[:3, :3]), 4)
            rec["trans_err_mm"] = round(float(np.linalg.norm(T_est[:3, 3] - T_exp[:3, 3])) * 1000, 3)
            # The naive guess "OpenCV reports the centre frame" — must be far off
            # for boards, which is the point of opencv_frame.
            rec["identity_hypothesis_rot_err_deg"] = round(rot_err_deg(T_est[:3, :3], T_cc[:3, :3]), 2)
            rec["identity_hypothesis_trans_err_mm"] = round(
                float(np.linalg.norm(T_est[:3, 3] - T_cc[:3, 3])) * 1000, 1)
            rec["pass"] = rec["rot_err_deg"] <= ROT_TOL_DEG and rec["trans_err_mm"] <= TRANS_TOL_MM
            rows.append(rec)
    return rows


# ------------------------------------------------------------------ d. print


SHEET = [
    PrintItem("grid", "DICT_4X4_50", 10, 0.025, layout="grid", rows=3, cols=2,
              separation_m=0.005),
    PrintItem("charuco", "DICT_5X5_100", 20, 0.015, quiet_modules=1.5, layout="charuco",
              rows=7, cols=5, square_length_m=0.020),
    PrintItem("charuco_legacy", "DICT_6X6_250", 50, 0.011, layout="charuco", rows=4, cols=4,
              square_length_m=0.015, legacy=True,
              label_lines=["DICT_6X6_250 ChArUco 4x4 legacy #50–57", "Base:1 · top"]),
    PrintItem("single", "DICT_APRILTAG_36h11", 5, 0.030),
]


def verify_sheet():
    settings = SheetSettings(page="letter", title="verify_aruco_boards letter")
    layout = print_sheet.layout_sheet(SHEET, settings)
    pdf = print_sheet.render_layout_pdf(layout)
    (OUT_DIR / "sample_boards_letter.pdf").write_bytes(pdf)
    doc = pdfium.PdfDocument(pdf)
    out = {"pages": layout.page_count, "rejected": [r.reason for r in layout.rejected],
           "items": [], "rulers": [], "stray": []}
    for page_index in range(len(doc)):
        arr = doc[page_index].render(scale=DPI / 72.0, grayscale=True).to_numpy()
        if arr.ndim == 3:
            arr = arr[:, :, 0]
        scale = (arr.shape[1] / layout.page_mm[0], arr.shape[0] / layout.page_mm[1])
        sc = np.array(scale)
        if page_index == 0:
            h, w = arr.shape
            cv2.imwrite(str(OUT_DIR / "sheet_boards_letter_p1.png"),
                        cv2.resize(arr, (1200, int(round(h * 1200.0 / w))),
                                   interpolation=cv2.INTER_AREA))
        placed = layout.pages[page_index]
        hits_by = {id(p): [] for p in placed}
        if layout.ruler is not None:
            out["rulers"].append({"page": page_index + 1,
                                  "mm_bar_mm": round(measure_bar(arr, scale, layout.ruler["mm_bar"]), 4),
                                  "in_bar_mm": round(measure_bar(arr, scale, layout.ruler["in_bar"]), 4)})
        for name in sorted({p.item.dictionary for p in placed}):
            params = cv2.aruco.DetectorParameters()
            params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
            corners, ids, _ = cv2.aruco.ArucoDetector(cv_dict(name), params).detectMarkers(arr)
            for quad, mid in zip(corners, flat(ids)):
                q = (np.asarray(quad, np.float64).reshape(4, 2) + 0.5) / sc
                cx, cy = q.mean(axis=0)
                inside = [p for p in placed if p.x_mm <= cx <= p.x_mm + p.sticker_w_mm
                          and p.y_mm <= cy <= p.y_mm + p.sticker_h_mm]
                if not inside or inside[0].item.dictionary != name:
                    out["stray"].append({"detector": name, "id": mid,
                                         "centre_mm": [round(float(cx), 2), round(float(cy), 2)]})
                    continue
                hits_by[id(inside[0])].append((mid, q))
        for p in placed:
            spec = print_sheet.board_spec(p.item)
            ccx = p.marker_x_mm + p.extent_w_mm / 2.0
            ccy = p.marker_y_mm + p.extent_h_mm / 2.0
            expect = {mid: np.array([(ccx + x * 1000, ccy - y * 1000) for x, y in pts])
                      for mid, pts in aruco_board.marker_corners_m(spec).items()}
            hits = hits_by[id(p)]
            got = {}
            for mid, q in hits:
                got.setdefault(mid, []).append(q)
            sides, cerr = [], []
            for mid, qs in got.items():
                if len(qs) == 1 and mid in expect:
                    q = qs[0]
                    sides.append(float(np.mean([np.linalg.norm(q[(j + 1) % 4] - q[j])
                                                for j in range(4)])) - p.marker_mm)
                    cerr.append(float(np.max(np.linalg.norm(q - expect[mid], axis=1))))
            rec = {"key": p.item.key, "layout": p.item.layout, "page": page_index + 1,
                   "dictionary": p.item.dictionary, "ids_expected": len(expect),
                   "ids_found_once": sum(1 for mid, qs in got.items() if len(qs) == 1 and mid in expect),
                   "ids_unexpected": sorted(mid for mid in got if mid not in expect),
                   "worst_side_err_mm": round(max(map(abs, sides)), 4) if sides else None,
                   "worst_corner_err_mm": round(max(cerr), 4) if cerr else None}
            # Scanline across the top border row of the first marker: exact ink.
            first = expect[spec.first_id]
            half = p.module_mm / 2.0
            rec["scan_first_marker_w_mm"] = round(darkness_run(
                arr, scale, first[0][1] + half, first[0][0] - half, first[1][0] + half, True), 4)
            if p.item.layout == "charuco":
                board = cv_board(spec, 1.0)
                ch_corners, ch_ids, _, _ = cv2.aruco.CharucoDetector(board).detectBoard(arr)
                cid = flat(ch_ids)
                rec["charuco_corners_found"] = len(cid)
                rec["charuco_corners_expected"] = (spec.cols - 1) * (spec.rows - 1)
                pts = {i: (np.asarray(c, np.float64).reshape(2) + 0.5) / sc
                       for i, c in zip(cid, ch_corners if ch_corners is not None else [])}
                F = np.array(aruco_board.opencv_frame(spec)).reshape(4, 4)
                cerr2, spacing = [], []
                per_row = spec.cols - 1
                for i, (x, y, z) in enumerate(aruco_board.charuco_corners(spec)):
                    X, Y, _ = (F @ np.array([x, y, z, 1.0]))[:3]
                    if i in pts:
                        cerr2.append(float(np.linalg.norm(pts[i] - (ccx + X * 1000, ccy - Y * 1000))))
                        if i % per_row != per_row - 1 and i + 1 in pts:
                            spacing.append(float(np.linalg.norm(pts[i + 1] - pts[i])))
                        if i + per_row in pts:
                            spacing.append(float(np.linalg.norm(pts[i + per_row] - pts[i])))
                sq = spec.square_length_m * 1000
                rec["square_mm_mean"] = round(float(np.mean(spacing)), 4) if spacing else None
                rec["worst_square_err_mm"] = (round(max(abs(v - sq) for v in spacing), 4)
                                              if spacing else None)
                rec["worst_chess_corner_err_mm"] = round(max(cerr2), 4) if cerr2 else None
            rec["pass"] = (rec["ids_found_once"] == rec["ids_expected"] and not rec["ids_unexpected"]
                           and rec["worst_side_err_mm"] is not None
                           and rec["worst_side_err_mm"] <= SIDE_TOL_MM
                           and rec["worst_corner_err_mm"] <= CORNER_TOL_MM
                           and abs(rec["scan_first_marker_w_mm"] - p.marker_mm) <= SIDE_TOL_MM
                           and (p.item.layout != "charuco" or (
                               rec["charuco_corners_found"] == rec["charuco_corners_expected"]
                               and rec["worst_square_err_mm"] <= SIDE_TOL_MM
                               and rec["worst_chess_corner_err_mm"] <= CORNER_TOL_MM)))
            out["items"].append(rec)
    out["pass"] = (not layout.rejected and not out["stray"]
                   and len(out["items"]) == len(SHEET) and all(r["pass"] for r in out["items"])
                   and all(abs(r["mm_bar_mm"] - 100) <= 0.15 for r in out["rulers"]))
    out["layout"] = print_sheet.layout_summary(layout)
    return out


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    results = {"generated": datetime.datetime.now().isoformat(timespec="seconds"),
               "opencv": cv2.__version__, "numpy": np.__version__,
               "tolerances": {"side_mm": SIDE_TOL_MM, "corner_mm": CORNER_TOL_MM,
                              "rot_deg": ROT_TOL_DEG, "trans_mm": TRANS_TOL_MM}}
    specs = spread()
    print("a. %d boards: black_rects_m / board_png vs generateImage, objPoints, ids ..." % len(specs))
    results["ink"] = check_ink_and_points(specs)
    for r in results["ink"]:
        print("   %-48s %s  raster %d px, png %d px, objpts %.1e m%s"
              % (r["board"], "ok  " if r["pass"] else "FAIL", r["raster_mismatch_px"],
                 r["png_mismatch_px"], r["objpoints_max_err_m"],
                 "" if "chess_corners_max_err_m" not in r
                 else ", chess %.1e m" % r["chess_corners_max_err_m"]))
    print("b. detection from board_png ...")
    results["detection"] = check_detection(specs)
    for r in results["detection"]:
        print("   %-48s %s  markers %d/%d%s, reproj %.3f px"
              % (r["board"], "ok  " if r["pass"] else "FAIL", r["markers_found"],
                 r["markers_expected"],
                 "" if "charuco_corners_found" not in r else ", corners %d/%d" % (
                     r["charuco_corners_found"], r["charuco_corners_expected"]),
                 r["reproj_rms_px"]))
    print("c. opencv_frame vs solvePnP on camera renders at a known pose ...")
    results["frames"] = check_frames()
    for r in results["frames"]:
        if "rot_err_deg" in r:
            print("   %-48s %s  rot %.3f deg, trans %.2f mm  (centre-frame guess: %.1f deg, %.0f mm)"
                  % (r["board"], "ok  " if r["pass"] else "FAIL", r["rot_err_deg"],
                     r["trans_err_mm"], r["identity_hypothesis_rot_err_deg"],
                     r["identity_hypothesis_trans_err_mm"]))
        else:
            print("   %-48s FAIL  %s" % (r["board"], r["method"]))
    print("d. boards on a letter sheet, %d dpi ..." % DPI)
    results["sheet"] = verify_sheet()
    sh = results["sheet"]
    for r in sh["items"]:
        line = ("   %-15s p%d %s  ids %d/%d, side err %.4f mm, corner err %.4f mm, scan %.4f mm"
                % (r["key"], r["page"], "ok  " if r["pass"] else "FAIL", r["ids_found_once"],
                   r["ids_expected"], r["worst_side_err_mm"] or -1, r["worst_corner_err_mm"] or -1,
                   r["scan_first_marker_w_mm"]))
        if r["layout"] == "charuco":
            line += ("; corners %d/%d, square %.4f mm (worst err %.4f), chess corner err %.4f mm"
                     % (r["charuco_corners_found"], r["charuco_corners_expected"],
                        r["square_mm_mean"] or -1, r["worst_square_err_mm"] or -1,
                        r["worst_chess_corner_err_mm"] or -1))
        print(line)
    for r in sh["rulers"]:
        print("   page %d ruler: 100 mm bar = %.3f mm, 4 in bar = %.3f mm"
              % (r["page"], r["mm_bar_mm"], r["in_bar_mm"]))
    print("   stray detections: %d, pages: %d" % (len(sh["stray"]), sh["pages"]))
    results["elapsed_s"] = round(time.time() - t0, 1)
    results["pass"] = bool(all(r["pass"] for r in results["ink"])
                           and all(r["pass"] for r in results["detection"])
                           and all(r["pass"] for r in results["frames"])
                           and sh["pass"])
    (OUT_DIR / "verify_boards.json").write_text(json.dumps(results, indent=1) + "\n",
                                                encoding="utf-8")
    print("wrote %s" % (OUT_DIR / "verify_boards.json"))
    print("%s  (%.1f s)" % ("PASS" if results["pass"] else "FAIL", results["elapsed_s"]))
    sys.exit(0 if results["pass"] else 1)


if __name__ == "__main__":
    main()
