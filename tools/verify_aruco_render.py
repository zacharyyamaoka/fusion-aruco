# -*- coding: utf-8 -*-
"""Independent check: do the URDF's marker FRAMES sit on the marker INK?

    python tools/verify_aruco_render.py <render.png> <frames.json> [...pairs]

Run tools/render_urdf_ortho.py with --frames first (it needs yourdfpy); this
side needs OpenCV only. Two sources that share no code path past Fusion:

    frames.json   each aruco_*_target frame, FK'd from the URDF, its four
                  marker corners projected into the render (records ->
                  markers.resolve -> LinkTree -> URDF)
    render.png    the exported meshes' pixels (Fusion bodies -> STL ->
                  mesh placement -> URDF visuals), read by cv2.aruco

For every frame that faces the camera and whose id OpenCV finds, the four
corners must coincide IN ORDER (OpenCV's TL, TR, BR, BL), which pins the
position and the in-plane rotation of the frame to the printed pattern.
Prints the worst corner error in mm; exits 1 above 0.5 mm or on a wrong id.
This script does not import bam_cad_core.
"""
from __future__ import annotations

import json
import sys

import cv2
import numpy as np

DICTS = {name: getattr(cv2.aruco, name) for name in dir(cv2.aruco) if name.startswith("DICT_")}


def main() -> int:
    args = sys.argv[1:]
    if len(args) < 2 or len(args) % 2:
        print(__doc__)
        return 2
    worst, checked, missing, bad = 0.0, 0, [], []
    for png, fj in zip(args[0::2], args[1::2]):
        frames = json.load(open(fj))
        s = frames["px_per_m"]
        gray = cv2.imread(png, cv2.IMREAD_GRAYSCALE)
        by_dict = {}
        for f in frames["frames"]:
            by_dict.setdefault(f["dictionary"], []).append(f)
        for dname, fs in by_dict.items():
            det = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(DICTS[dname]),
                                          cv2.aruco.DetectorParameters())
            corners, ids, _ = det.detectMarkers(gray)
            found = {} if ids is None else {int(i): c.reshape(4, 2)
                                            for i, c in zip(ids.flatten(), corners)}
            expected = {f["id"] for f in fs}
            for i in found:
                if i not in expected:
                    bad.append(f"{png}: id {i} seen but no frame carries it")
            for f in fs:
                if not f["facing"]:
                    continue
                if f["id"] not in found:
                    missing.append(f"{png.split('/')[-1]}:{f['id']}")
                    continue
                err_px = np.linalg.norm(found[f["id"]] - np.array(f["corners_px"]), axis=1).max()
                err_mm = err_px / s * 1000.0
                worst = max(worst, err_mm)
                checked += 1
                print(f"  {png.split('/')[-1]:<28} {f['frame']:<18} worst corner {err_mm:.3f} mm")
    print(f"{checked} frame/view pair(s) checked; worst corner error {worst:.3f} mm; "
          f"{len(missing)} facing frame(s) not read (occluded or too oblique): {missing}")
    for b in bad:
        print("  BAD", b)
    ok = checked > 0 and worst <= 0.5 and not bad
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
