"""OpenCV oracle for the marker-output lane (aruco_dict + print_sheet).

ORACLE-SIDE ONLY — needs cv2 (with cv2.aruco), numpy and pypdfium2:

    PYTHONPATH=<dir with cv2+numpy+pypdfium2> python tools/verify_aruco_output.py

  a. every dictionary, every id: marker_grid == cv2.aruco.generateImageMarker
     sampled at module centres (exact); then marker_png -> ArucoDetector must
     return exactly that id (every id by default, --detect-sample N to thin);
  b. a mixed ~25-marker, 15-60 mm sheet on letter AND a4 (2+ pages each) is
     rendered by print_sheet, rasterised by pdfium at 600 dpi, and detected
     per dictionary: every (dictionary, id) found exactly once, at the place
     the layout says, upright, with its black-square side within +-0.15 mm
     of marker_length; the 100 mm and 4 in ruler bars are measured too;
  c. results -> reports/media/aruco/verify_output.json, page-1 previews and
     the sample PDFs beside it.
"""
from __future__ import annotations

import argparse
import datetime
import json
import random
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pypdfium2 as pdfium

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "CADExporter"))
from bam_cad_core import aruco_dict, print_sheet  # noqa: E402
from bam_cad_core.print_sheet import PrintItem, SheetSettings  # noqa: E402

OUT_DIR = ROOT / "reports" / "media" / "aruco"
DPI = 600
SIDE_TOL_MM = 0.15
CORNER_TOL_MM = 0.2
RULER_TOL_MM = 0.15

# (dictionary, id, marker_length_mm, extra label line or None, quiet_modules).
# Several families, prefix-sharing names with DISTINCT ids (DICT_4X4_50 #17
# and DICT_4X4_1000 #17 would be the same printed pattern), top-of-range ids.
SHEET = [
    ("DICT_4X4_50", 0, 60.0, "Base:1 · top", 1.0),
    ("DICT_4X4_50", 17, 15.0, None, 1.0),
    ("DICT_4X4_50", 49, 27.1, "Arm/Actuator:3 · front", 1.0),
    ("DICT_4X4_250", 123, 19.0, None, 1.5),
    ("DICT_4X4_1000", 999, 36.0, "Forearm (L2) \\ outer", 1.0),
    ("DICT_4X4_1000", 500, 22.5, None, 1.0),
    ("DICT_5X5_50", 13, 35.0, None, 1.0),
    ("DICT_5X5_100", 7, 45.0, None, 2.0),
    ("DICT_5X5_100", 99, 18.0, None, 1.0),
    ("DICT_5X5_1000", 640, 30.0, None, 1.0),
    ("DICT_6X6_50", 3, 33.0, None, 1.0),
    ("DICT_6X6_250", 23, 50.0, "Wrist:1 · side", 1.0),
    ("DICT_6X6_250", 249, 20.0, None, 1.0),
    ("DICT_6X6_1000", 777, 26.0, None, 1.0),
    ("DICT_7X7_50", 9, 58.0, None, 1.0),
    ("DICT_7X7_100", 42, 25.0, None, 1.0),
    ("DICT_7X7_1000", 999, 55.0, None, 1.0),
    ("DICT_ARUCO_ORIGINAL", 1023, 40.0, None, 1.0),
    ("DICT_ARUCO_ORIGINAL", 0, 16.5, None, 1.0),
    ("DICT_APRILTAG_16h5", 29, 32.0, None, 1.0),
    ("DICT_APRILTAG_25h9", 34, 28.0, None, 1.0),
    ("DICT_APRILTAG_36h10", 2319, 42.0, None, 1.0),
    ("DICT_APRILTAG_36h11", 0, 38.0, None, 1.0),
    ("DICT_APRILTAG_36h11", 586, 24.0, None, 1.0),
    ("DICT_APRILTAG_36h11", 300, 15.0, None, 1.0),
]


def cv_dict(name):
    return cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, name))


def flat_ids(ids):
    return [] if ids is None else [int(v) for v in np.asarray(ids).reshape(-1)]


# ------------------------------------------------------------------ a. tables


def check_grids():
    k = 10
    report, mismatches, total = {}, [], 0
    for name in aruco_dict.DICTIONARY_NAMES:
        d = cv_dict(name)
        info = aruco_dict.dictionary_info(name)
        n = info["bits"]
        bad = []
        if d.markerSize != n or len(d.bytesList) != info["capacity"]:
            bad.append("shape: cv2 %dx%d/%d vs table %d/%d" % (
                d.markerSize, d.markerSize, len(d.bytesList), n, info["capacity"]))
        for i in range(info["capacity"]):
            img = cv2.aruco.generateImageMarker(d, i, (n + 2) * k, borderBits=1)
            cv_grid = (img[k // 2::k, k // 2::k] < 128).astype(np.uint8)
            if not np.array_equal(cv_grid, np.array(aruco_dict.marker_grid(name, i), np.uint8)):
                bad.append(i)
        total += info["capacity"]
        report[name] = {"ids": info["capacity"], "mismatches": len(bad)}
        mismatches.extend((name, b) for b in bad[:10])
    return {"markers_compared": total, "mismatches": mismatches,
            "per_dictionary": report, "pass": not mismatches}


def check_roundtrip(sample, module_px=8):
    rng = random.Random(20261007)
    report, failures, total = {}, [], 0
    for name in aruco_dict.DICTIONARY_NAMES:
        cap = aruco_dict.dictionary_info(name)["capacity"]
        if sample and cap > sample:
            ids = sorted(set(range(5)) | set(range(cap - 5, cap))
                         | set(rng.sample(range(cap), sample - 10)))
        else:
            ids = list(range(cap))
        det = cv2.aruco.ArucoDetector(cv_dict(name), cv2.aruco.DetectorParameters())
        bad = []
        for i in ids:
            png = aruco_dict.marker_png(name, i, module_px=module_px)
            img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
            _, found, _ = det.detectMarkers(img)
            if flat_ids(found) != [i]:
                bad.append((i, flat_ids(found)))
        total += len(ids)
        report[name] = {"detected_ok": len(ids) - len(bad), "tested": len(ids)}
        failures.extend((name,) + b for b in bad[:10])
    return {"module_px": module_px, "quiet_modules": 1, "tested": total,
            "failures": failures, "per_dictionary": report, "pass": not failures}


# ------------------------------------------------------------------ b. sheets


def sheet_items():
    items = []
    for k, (name, mid, mm, extra, quiet) in enumerate(SHEET):
        lines = []
        if extra:
            lines = [print_sheet.identity_label(
                PrintItem("", name, mid, mm / 1000.0)), extra]
        items.append(PrintItem(key="s%02d" % k, dictionary=name, marker_id=mid,
                               marker_length_m=mm / 1000.0, quiet_modules=quiet,
                               label_lines=lines))
    return items


def darkness_run(arr, scale, fixed, lo_mm, hi_mm, horizontal):
    """Ink along one pixel row/column between lo and hi (mm), in mm. Sums
    (255 - v)/255, so partially covered edge pixels count fractionally."""
    sx, sy = scale
    s_along, s_across = (sx, sy) if horizontal else (sy, sx)
    lo, hi = int(np.floor(lo_mm * s_along)), int(np.ceil(hi_mm * s_along))
    idx = int(fixed * s_across)
    line = arr[idx, lo:hi] if horizontal else arr[lo:hi, idx]
    return float(np.sum(255.0 - line.astype(np.float64)) / 255.0) / s_along


def measure_bar(arr, scale, bar):
    x, y, w, h = bar
    rows = [y + h * f for f in (0.3, 0.4, 0.5, 0.6, 0.7)]
    return float(np.mean([darkness_run(arr, scale, r, x - 2.0, x + w + 2.0, True)
                          for r in rows]))


def verify_sheet(page_name, items):
    settings = SheetSettings(page=page_name, title="verify_aruco_output %s" % page_name)
    layout = print_sheet.layout_sheet(items, settings)
    pdf = print_sheet.render_layout_pdf(layout)
    (OUT_DIR / ("sample_sheet_%s.pdf" % page_name)).write_bytes(pdf)
    doc = pdfium.PdfDocument(pdf)
    out = {"page": page_name, "pages": layout.page_count, "pdf_pages": len(doc),
           "rejected": [r.reason for r in layout.rejected], "items": [],
           "misreads": [], "spurious": [], "aliases": [], "rulers": []}
    names = sorted({it.dictionary for it in items})
    detectors = {}
    for name in names:
        params = cv2.aruco.DetectorParameters()
        params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
        detectors[name] = cv2.aruco.ArucoDetector(cv_dict(name), params)
    hits = {id(p): [] for p in layout.placed}
    for page_index in range(len(doc)):
        arr = doc[page_index].render(scale=DPI / 72.0, grayscale=True).to_numpy()
        if arr.ndim == 3:
            arr = arr[:, :, 0]
        # pdfium rounds the bitmap UP to whole pixels and stretches the page to
        # fill it (A4 at 600 dpi: 4960.6 -> 4961 px), so the true scale is the
        # bitmap size over the page size, per axis — not 600/25.4.
        scale = (arr.shape[1] / layout.page_mm[0], arr.shape[0] / layout.page_mm[1])
        out["raster_px_per_mm"] = [round(scale[0], 6), round(scale[1], 6)]
        if page_index == 0:
            h, w = arr.shape
            small = cv2.resize(arr, (1200, int(round(h * 1200.0 / w))),
                               interpolation=cv2.INTER_AREA)
            cv2.imwrite(str(OUT_DIR / ("sheet_%s_p1.png" % page_name)), small)
        placed = layout.pages[page_index] if page_index < layout.page_count else []
        for name in names:
            corners, ids, _ = detectors[name].detectMarkers(arr)
            for quad, mid in zip(corners, flat_ids(ids)):
                # OpenCV pixel centres sit at integer coords; +0.5 -> pdfium's
                # continuous pixel space, where page mm * scale lands.
                q = (np.asarray(quad).reshape(4, 2) + 0.5) / np.array(scale)
                cx, cy = q.mean(axis=0)
                owner = None
                for p in placed:
                    if p.x_mm <= cx <= p.x_mm + p.sticker_mm and p.y_mm <= cy <= p.y_mm + p.sticker_mm:
                        owner = p
                        break
                where = {"detector": name, "id": mid, "page": page_index + 1,
                         "centre_mm": [round(float(cx), 2), round(float(cy), 2)]}
                if owner is None:
                    out["spurious"].append(where)
                elif owner.item.dictionary == name:
                    if owner.item.marker_id == mid:
                        hits[id(owner)].append(q)
                    else:
                        out["misreads"].append(dict(where, expected=owner.item.marker_id))
                else:
                    where["on"] = "%s #%d" % (owner.item.dictionary, owner.item.marker_id)
                    same = (aruco_dict.marker_grid(name, mid)
                            == aruco_dict.marker_grid(owner.item.dictionary,
                                                      owner.item.marker_id)
                            if len(aruco_dict.marker_grid(name, mid))
                            == owner.modules else False)
                    where["same_pattern"] = bool(same)
                    out["aliases"].append(where)
        if layout.ruler is not None:
            out["rulers"].append({
                "page": page_index + 1,
                "mm_bar_mm": round(measure_bar(arr, scale, layout.ruler["mm_bar"]), 4),
                "in_bar_mm": round(measure_bar(arr, scale, layout.ruler["in_bar"]), 4)})
        for p in placed:
            found = hits[id(p)]
            rec = {"key": p.item.key, "dictionary": p.item.dictionary,
                   "id": p.item.marker_id, "page": page_index + 1,
                   "marker_mm": round(p.marker_mm, 4), "found": len(found)}
            # Scanline cross-check: ink along the middle of the top border row
            # and the left border column == the black square's side.
            half = p.module_mm / 2.0
            win = min(half, p.item.quiet_modules * p.module_mm / 2.0)
            rec["scan_w_mm"] = round(darkness_run(
                arr, scale, p.marker_y_mm + half, p.marker_x_mm - win,
                p.marker_x_mm + p.marker_mm + win, True), 4)
            rec["scan_h_mm"] = round(darkness_run(
                arr, scale, p.marker_x_mm + half, p.marker_y_mm - win,
                p.marker_y_mm + p.marker_mm + win, False), 4)
            if len(found) == 1:
                q = found[0]
                edges = [float(np.linalg.norm(q[(j + 1) % 4] - q[j])) for j in range(4)]
                side = float(np.mean(edges))
                x0, y0, s = p.marker_x_mm, p.marker_y_mm, p.marker_mm
                expect = np.array([[x0, y0], [x0 + s, y0], [x0 + s, y0 + s], [x0, y0 + s]])
                rec.update({
                    "side_mm": round(side, 4),
                    "side_err_mm": round(side - p.marker_mm, 4),
                    "edge_spread_mm": round(max(edges) - min(edges), 4),
                    "corner_err_mm": round(float(np.max(np.linalg.norm(q - expect, axis=1))), 4),
                })
            out["items"].append(rec)
    items_ok = all(r["found"] == 1 and abs(r["side_err_mm"]) <= SIDE_TOL_MM
                   and r["corner_err_mm"] <= CORNER_TOL_MM for r in out["items"])
    scan_ok = all(abs(r["scan_w_mm"] - r["marker_mm"]) <= SIDE_TOL_MM
                  and abs(r["scan_h_mm"] - r["marker_mm"]) <= SIDE_TOL_MM
                  for r in out["items"])
    rulers_ok = bool(out["rulers"]) and all(
        abs(r["mm_bar_mm"] - 100.0) <= RULER_TOL_MM and abs(r["in_bar_mm"] - 101.6) <= RULER_TOL_MM
        for r in out["rulers"])
    sides = [abs(r["side_err_mm"]) for r in out["items"] if "side_err_mm" in r]
    out["worst_side_err_mm"] = round(max(sides), 4) if sides else None
    out["worst_corner_err_mm"] = round(max(r.get("corner_err_mm", 99) for r in out["items"]), 4)
    out["worst_scan_err_mm"] = round(max(max(abs(r["scan_w_mm"] - r["marker_mm"]),
                                             abs(r["scan_h_mm"] - r["marker_mm"]))
                                         for r in out["items"]), 4)
    out["aliases_same_pattern"] = sum(1 for a in out["aliases"] if a["same_pattern"])
    out["aliases_cross_family"] = len(out["aliases"]) - out["aliases_same_pattern"]
    out["checks"] = {
        "two_or_more_pages": layout.page_count >= 2,
        "pdf_pages_match_layout": len(doc) == layout.page_count,
        "nothing_rejected": not layout.rejected,
        "every_item_once_in_place_and_size": items_ok,
        "no_misreads": not out["misreads"],
        "no_spurious": not out["spurious"],
        "scanline_sides": scan_ok,
        "rulers": rulers_ok,
        "all_items_measured": len(out["items"]) == len(items),
    }
    out["pass"] = all(out["checks"].values())
    out["layout"] = print_sheet.layout_summary(layout)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--detect-sample", type=int, default=0,
                    help="round-trip only N ids per dictionary (0 = every id)")
    args = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    results = {"generated": datetime.datetime.now().isoformat(timespec="seconds"),
               "opencv": cv2.__version__, "numpy": np.__version__,
               "pdfium": str(pdfium.PDFIUM_INFO), "dpi": DPI,
               "tolerances_mm": {"side": SIDE_TOL_MM, "corner": CORNER_TOL_MM,
                                 "ruler": RULER_TOL_MM}}
    print("a. marker_grid vs generateImageMarker, every dictionary, every id ...")
    results["grids"] = check_grids()
    g = results["grids"]
    print("   %d markers, %d mismatches" % (g["markers_compared"], len(g["mismatches"])))
    print("a. marker_png -> ArucoDetector round trip ...")
    results["roundtrip"] = check_roundtrip(args.detect_sample)
    r = results["roundtrip"]
    print("   %d detected correctly of %d" % (r["tested"] - len(r["failures"]), r["tested"]))
    items = sheet_items()
    results["sheets"] = {}
    for page_name in ("letter", "a4"):
        print("b. %s sheet, %d markers, %d dpi ..." % (page_name, len(items), DPI))
        s = verify_sheet(page_name, items)
        results["sheets"][page_name] = s
        found = sum(1 for it in s["items"] if it["found"] == 1)
        print("   pages %d, found exactly once %d/%d, worst side err %.4f mm, worst "
              "corner err %.4f mm, worst scanline err %.4f mm"
              % (s["pages"], found, len(items), s["worst_side_err_mm"] or -1,
                 s["worst_corner_err_mm"], s["worst_scan_err_mm"]))
        for rr in s["rulers"]:
            print("   page %d ruler: 100 mm bar = %.3f mm, 4 in bar = %.3f mm (101.6)"
                  % (rr["page"], rr["mm_bar_mm"], rr["in_bar_mm"]))
        print("   misreads %d, spurious %d; other-dictionary detections on a "
              "sticker: %d same pattern (prefix-sharing names), %d cross-family"
              % (len(s["misreads"]), len(s["spurious"]), s["aliases_same_pattern"],
                 s["aliases_cross_family"]))
        for name, ok in s["checks"].items():
            if not ok:
                print("   FAIL check: %s" % name)
    results["elapsed_s"] = round(time.time() - t0, 1)
    results["pass"] = bool(results["grids"]["pass"] and results["roundtrip"]["pass"]
                           and all(s["pass"] for s in results["sheets"].values()))
    (OUT_DIR / "verify_output.json").write_text(json.dumps(results, indent=1) + "\n",
                                                encoding="utf-8")
    print("wrote %s" % (OUT_DIR / "verify_output.json"))
    print("%s  (%.1f s)" % ("PASS" if results["pass"] else "FAIL", results["elapsed_s"]))
    sys.exit(0 if results["pass"] else 1)


if __name__ == "__main__":
    main()
