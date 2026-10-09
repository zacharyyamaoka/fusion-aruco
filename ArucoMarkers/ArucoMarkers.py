"""ArUco Tags (prototype) — Fusion add-in entry point. Wiring only.

The commands live in `aruco_commands`, the Fusion-side geometry and records
in `aruco_tags`, and everything that does not need Fusion in the pure core
(`CADExporter/bam_cad_core/markers.py`, loaded via `aruco_core`).
"""
from __future__ import annotations

import os
import sys
import traceback

import adsk.core

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def run(context):
    app = adsk.core.Application.get()
    try:
        # FIRST, and here in run(): Fusion ties a custom feature definition
        # to the add-in whose run() made it (made anywhere else, every
        # customFeatures.add fails "scriptInfoFound"; measured 2026-10-07).
        try:
            import aruco_feature
            live = aruco_feature.register()
            app.log(f"ArUco Extrude feature registered: {live.get('id')}")
        except Exception:                          # noqa: BLE001
            app.log("ArUco Tags: live ArUco Extrude feature unavailable\n"
                    + traceback.format_exc())
        import aruco_bridge
        status = aruco_bridge.arm()
        try:
            import aruco_commands
            aruco_commands.register()
        except Exception:                          # noqa: BLE001
            app.log("ArUco Tags: commands failed to register\n" + traceback.format_exc())
        app.log(f"ArUco Tags loaded from {HERE}; {status}")
    except Exception:                              # noqa: BLE001
        app.userInterface.messageBox("ArUco Tags failed to start:\n" + traceback.format_exc())


def stop(context):
    try:
        import aruco_commands
        aruco_commands.unregister()
    except Exception:                              # noqa: BLE001
        pass
    try:
        import aruco_feature
        aruco_feature.unregister()
    except Exception:                              # noqa: BLE001
        pass
    try:
        import aruco_bridge
        # restart_addin (a bridge action) stops and re-runs this add-in from
        # inside a bridge request: keep that bridge alive across the restart
        if not getattr(sys.modules.get("_aruco_bridge_state"), "keep_armed", False):
            aruco_bridge.disarm()
    except Exception:                              # noqa: BLE001
        pass
