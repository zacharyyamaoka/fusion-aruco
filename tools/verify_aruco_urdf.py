# -*- coding: utf-8 -*-
"""Do the URDF's marker frames sit where the tags are in the CAD? (oracle)

    <clank-viser venv python> tools/verify_aruco_urdf.py <export-dir> [<payload-dir>]

Loads the exported URDF with yourdfpy (an independent URDF reader), takes
every `aruco_*_target` / `grid_*` / `charuco_*` frame at q = 0, and compares
it with the tag's pose straight out of Fusion (markers.json `optical_world`:
OpenCV's frame, what a detector reports; else `world` composed with the
OpenCV detector frame). Prints the worst translation and rotation error;
exits 1 above 1e-6 m / 1e-6 rad, or when a tag has no target of its own.
Dev-only: needs numpy + yourdfpy. `check()` is the same test for a caller
that has the pieces in memory (tests/test_aruco_frame.py).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import yourdfpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "CADExporter"))
from bam_cad_core import markers as M  # noqa: E402

UNIT = {"mm": 0.001, "cm": 0.01, "m": 1.0, "in": 0.0254}
TOL = 1e-6


def _rot_err(A, B):
    R = A[:3, :3].T @ B[:3, :3]
    return math.acos(max(-1.0, min(1.0, (np.trace(R) - 1) / 2)))


def check(robot, doc, to_m, out=print):
    """yourdfpy URDF + markers.json (dict) + design units in metres ->
    (rows, bad). rows: (instance, id, link, |dt| m, |dr| rad, name ok)."""
    base = robot.base_link
    frames = {link: robot.get_transform(link, base)
              for link in robot.link_map if link.endswith("_target")}
    worst_t, worst_r, rows = 0.0, 0.0, []
    seen = set()
    for inst in doc["instances"]:
        if inst["hidden"]:
            continue
        spec = M.TagSpec.from_dict(inst["spec"], out, inst["name"])
        if inst.get("optical_world"):
            expect = np.array(inst["optical_world"], dtype=float).reshape(4, 4)
            expect[:3, 3] *= to_m
        else:
            world = np.array(inst["world"], dtype=float).reshape(4, 4)
            world[:3, 3] *= to_m
            expect = world @ np.array(M.detector_frame(spec), dtype=float).reshape(4, 4)
        # the base link's frame is the world origin at q = 0 for this exporter
        best = None
        for name, T in frames.items():
            if name in seen:
                continue
            d = np.linalg.norm(T[:3, 3] - expect[:3, 3])
            if best is None or d < best[1]:
                best = (name, d, T)
        if best is None:
            rows.append((inst["name"], spec.marker_id, None, math.inf, math.inf, False))
            worst_t = math.inf
            continue
        name, dt, T = best
        seen.add(name)
        dr = _rot_err(T, expect)
        ok_name = name.startswith(("aruco_%d" % spec.marker_id, f"{spec.layout}_{spec.marker_id}"))
        worst_t, worst_r = max(worst_t, dt), max(worst_r, dr)
        rows.append((inst["name"], spec.marker_id, name, dt, dr, ok_name))
    for r in rows:
        out(f"  {r[0]:<36} id {r[1]:>3} -> {str(r[2]):<18} |dt| {r[3]:.2e} m  "
            f"|dr| {r[4]:.2e} rad  {'name ok' if r[5] else 'NAME MISMATCH'}")
    out(f"{len(seen)} tag(s), {len(frames)} frame(s); worst {worst_t:.3e} m, {worst_r:.3e} rad")
    bad = (worst_t > TOL or worst_r > TOL or not all(r[5] for r in rows)
           or len(seen) != len(frames))
    return rows, bad


def main() -> int:
    export = Path(sys.argv[1])
    payload_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else export
    doc = json.loads((export / "aruco" / "markers.json").read_text(encoding="utf-8"))
    units = json.loads((payload_dir / "fusion360_data.json").read_text(
        encoding="utf-8"))["design"]["units"]
    urdf_path = next((export / "urdf").glob("*.urdf"))
    robot = yourdfpy.URDF.load(str(urdf_path), load_meshes=False,
                               build_scene_graph=True, build_collision_scene_graph=False)
    _rows, bad = check(robot, doc, UNIT[units])
    print("FAIL" if bad else "PASS")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
