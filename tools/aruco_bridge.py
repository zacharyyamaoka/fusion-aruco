# -*- coding: utf-8 -*-
"""Drive the ArUco Tags add-in's dev bridge from a shell. Dev-only; stdlib.

    python tools/aruco_bridge.py ping
    python tools/aruco_bridge.py probe --arg step=all
    python tools/aruco_bridge.py build_demo --json '{"n_actuators": 6}'

Exit 0 when the add-in answered ok, 1 otherwise, 2 on usage errors.
The other end is ArucoMarkers/aruco_bridge.py (armed only when
%USERPROFILE%/CADExporter/_aruco_bridge/ENABLED exists).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(os.path.expanduser("~")) / "CADExporter" / "_aruco_bridge"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action")
    ap.add_argument("--arg", action="append", default=[], help="k=v (string)")
    ap.add_argument("--json", default=None, help="JSON object of args")
    ap.add_argument("--timeout", type=float, default=300.0)
    ns = ap.parse_args()
    args = {}
    if ns.json:
        args.update(json.loads(ns.json))
    for kv in ns.arg:
        if "=" not in kv:
            print(f"--arg needs k=v, got {kv!r}", file=sys.stderr)
            return 2
        k, v = kv.split("=", 1)
        args[k] = v
    # Zach can pause every agent's use of Fusion (2026-10-08: "pause usage of
    # fusion for 1 hour"): PAUSED_UNTIL.json {"until": <epoch s>} refuses
    # every request before it reaches Fusion. Delete the file to resume early.
    pause = ROOT / "PAUSED_UNTIL.json"
    if pause.exists():
        # {"until": epoch, "reason": ...} and/or {"windows": [{"from": epoch,
        # "until": epoch, "until_local": "...", "reason": ...}, ...]} -- a
        # window also reserves Fusion for another session (coordination)
        try:
            info = json.loads(pause.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            info = {"until": float("inf"), "reason": "unreadable pause file"}
        now = time.time()
        wins = list(info.get("windows") or [])
        if info.get("until"):
            wins.append({"from": 0, "until": info["until"], "until_local": info.get("until_local"),
                         "reason": info.get("reason")})
        for w in wins:
            if float(w.get("from", 0)) <= now < float(w.get("until", 0)):
                print(f"Fusion is PAUSED for agents until {w.get('until_local') or w.get('until')} "
                      f"({w.get('reason') or 'paused by the user'}). Do not use Fusion; carry on "
                      "with offline work and retry after that time.", file=sys.stderr)
                return 3
    # CLOSED BY DEFAULT (2026-10-08, after two crashes while Zach worked):
    # agents may drive Fusion only inside a test window the orchestrator
    # opens with Zach's OK -- FUSION_OPEN.json {"from": epoch, "until":
    # epoch, "reason": ...}. Read-only pings are allowed any time.
    gate = ROOT / "FUSION_OPEN.json"
    if ns.action != "ping":
        try:
            g = json.loads(gate.read_text(encoding="utf-8")) if gate.exists() else {}
        except (ValueError, OSError):
            g = {}
        now = time.time()
        if not (float(g.get("from", 0)) <= now < float(g.get("until", 0))):
            print("Fusion is CLOSED to agents: no test window is open (Zach works in "
                  "Fusion; agents test only in a window the orchestrator opens with his "
                  "OK). Do not use Fusion; carry on offline and tell the orchestrator "
                  "what you need to test.", file=sys.stderr)
            return 3
    beat = ROOT / "heartbeat.json"
    if not beat.exists() or time.time() - json.loads(beat.read_text())["beat_at"] > 5:
        print("aruco bridge is not beating — is the ArucoMarkers add-in running "
              "with _aruco_bridge/ENABLED present?", file=sys.stderr)
        return 1
    deadline = time.time() + ns.timeout

    def send():
        rid = time.strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:6]
        (ROOT / "requests").mkdir(parents=True, exist_ok=True)
        tmp = ROOT / "requests" / f"{rid}.tmp"
        tmp.write_text(json.dumps({"action": ns.action, "args": args}), encoding="utf-8")
        os.replace(tmp, ROOT / "requests" / f"{rid}.json")
        return rid, ROOT / "responses" / f"{rid}.json"

    rid, resp = send()
    while time.time() < deadline:
        if resp.exists():
            try:
                data = json.loads(resp.read_text(encoding="utf-8"))
            except ValueError:
                time.sleep(0.1)
                continue
            if data.get("busy"):
                # another agent's request is running in Fusion: try again
                time.sleep(2.0)
                rid, resp = send()
                continue
            print(json.dumps(data, indent=1, ensure_ascii=False))
            return 0 if data.get("ok") else 1
        time.sleep(0.2)
    print(f"no answer within {ns.timeout}s (request {rid} may still be running)",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
