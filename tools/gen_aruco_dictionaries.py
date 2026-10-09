"""Write CADExporter/bam_cad_core/aruco_dictionaries.json from OpenCV.

ORACLE-SIDE ONLY. Needs cv2 (with cv2.aruco) and numpy; the file it writes is
what lets the stdlib-only runtime (bam_cad_core.aruco_dict, inside Fusion's
Python, which has neither) draw every predefined marker byte-exactly.

    PYTHONPATH=<dir with cv2+numpy> python tools/gen_aruco_dictionaries.py

How the bits are read: each marker is drawn with
`cv2.aruco.generateImageMarker(dict, id, (n+2)*K, borderBits=1)` and sampled
at module centres — i.e. the table is what OpenCV *renders*, not a decoding of
its byte list, so orientation (row 0 = top of the image) cannot drift. The
byte list is then decoded independently (`getBitsFromByteList`, rotation 0)
and must agree, and the border must be all black.

Storage, per marker: the n*n INNER bits (border excluded), row-major from the
top-left, 1 = BLACK (OpenCV's own bit convention is the opposite: 1 = white),
packed MSB-first into one integer and written as fixed-width lowercase hex of
ceil(n*n/4) digits. Bit k of the row-major sequence is
(value >> (n*n - 1 - k)) & 1.

A dictionary whose markers are exactly the first `capacity` markers of a
larger one in the same family (OpenCV's _50/_100/_250 are prefixes of _1000)
points at that table instead of repeating it — but only after the prefix
property is VERIFIED here, for that name, against cv2. A name that fails the
check gets its own table; nothing is assumed.

`payload_sha256` is sha256 over json.dumps(payload, sort_keys=True,
separators=(",", ":")) encoded UTF-8; the runtime re-checks it on load.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "CADExporter"))
from bam_cad_core.fiducials import ARUCO_DICTS  # noqa: E402  (stdlib-only)

OUT = ROOT / "CADExporter" / "bam_cad_core" / "aruco_dictionaries.json"
SCHEMA = "bam_aruco_dictionaries/1"
K = 4  # px per module when sampling the rendered marker


def family_of(name):
    if name.startswith("DICT_APRILTAG_"):
        return "apriltag"
    if name == "DICT_ARUCO_ORIGINAL":
        return "aruco_original"
    return "aruco"


def cv_dict(name):
    return cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, name))


def black_bits(d, marker_id):
    """Inner n x n bits of one marker, 1 = black, row 0 = top, via rendering."""
    n = d.markerSize
    side = (n + 2) * K
    img = cv2.aruco.generateImageMarker(d, marker_id, side, borderBits=1)
    centres = img[K // 2::K, K // 2::K]
    if centres.shape != (n + 2, n + 2):
        raise SystemExit(f"unexpected render shape {centres.shape}")
    grid = (centres < 128).astype(np.uint8)
    if not (grid[0].all() and grid[-1].all() and grid[:, 0].all() and grid[:, -1].all()):
        raise SystemExit(f"border of id {marker_id} is not all black")
    inner = grid[1:-1, 1:-1]
    # Independent decode of the byte list (rotation 0, OpenCV 1 = white).
    decoded = cv2.aruco.Dictionary.getBitsFromByteList(
        d.bytesList[marker_id:marker_id + 1], n)
    if not np.array_equal(inner, 1 - decoded):
        raise SystemExit(f"render and byte list disagree for id {marker_id}")
    return inner


def to_hex(inner):
    n = inner.shape[0]
    value = 0
    for bit in inner.flatten():
        value = (value << 1) | int(bit)
    return format(value, "0%dx" % ((n * n + 3) // 4))


def canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def build():
    names = list(ARUCO_DICTS)
    tables = {}        # table name -> {"bits": n, "markers": [hex]}
    dictionaries = {}  # dict name -> {...}
    prefix_report = {}

    # The largest dictionary of each family carries the table.
    groups = {}
    for name in names:
        d = cv_dict(name)
        key = (family_of(name), d.markerSize) if family_of(name) == "aruco" else name
        groups.setdefault(key, []).append(name)

    for key, members in groups.items():
        cap = {m: len(cv_dict(m).bytesList) for m in members}
        owner = max(members, key=lambda m: cap[m])
        d_owner = cv_dict(owner)
        owner_hex = [to_hex(black_bits(d_owner, i)) for i in range(cap[owner])]
        tables[owner] = {"bits": int(d_owner.markerSize), "markers": owner_hex}
        for m in members:
            d = cv_dict(m)
            table = owner
            if m != owner:
                # Verify the prefix property for THIS name before relying on it:
                # identical byte lists (all four rotations) AND identical renders.
                same_bytes = (d.markerSize == d_owner.markerSize and np.array_equal(
                    d.bytesList, d_owner.bytesList[:cap[m]]))
                own_hex = [to_hex(black_bits(d, i)) for i in range(cap[m])]
                prefix = same_bytes and own_hex == owner_hex[:cap[m]]
                prefix_report[m] = {"prefix_of": owner, "holds": bool(prefix)}
                if not prefix:
                    tables[m] = {"bits": int(d.markerSize), "markers": own_hex}
                    table = m
            dictionaries[m] = {
                "table": table,
                "bits": int(d.markerSize),
                "capacity": cap[m],
                "family": family_of(m),
                "max_correction_bits": int(d.maxCorrectionBits),
            }

    for name in names:
        if dictionaries[name]["capacity"] != ARUCO_DICTS[name]:
            print(f"WARNING: fiducials.ARUCO_DICTS says {name} has "
                  f"{ARUCO_DICTS[name]} ids, OpenCV has "
                  f"{dictionaries[name]['capacity']}", file=sys.stderr)

    payload = {"names": names, "dictionaries": dictionaries, "tables": tables}
    return payload, prefix_report


def write(payload, prefix_report, date):
    digest = hashlib.sha256(canonical(payload)).hexdigest()
    header = {
        "schema": SCHEMA,
        "generator": {
            "tool": "tools/gen_aruco_dictionaries.py",
            "opencv": cv2.__version__,
            "numpy": np.__version__,
            "date": date,
        },
        "encoding": ("per marker: the n*n inner bits (border excluded), "
                     "row-major from the TOP-LEFT as generateImageMarker "
                     "renders it, 1 = BLACK, packed MSB-first, fixed-width "
                     "lowercase hex of ceil(n*n/4) digits. The full marker "
                     "adds a 1-module black border."),
        "prefix_check": prefix_report,
        "payload_sha256": digest,
    }
    # Hand-assembled so each table is one line: readable header, compact body.
    lines = ["{"]
    for k, v in header.items():
        lines.append("  %s: %s," % (json.dumps(k), json.dumps(v, sort_keys=True)))
    lines.append('  "payload": {')
    lines.append('    "names": %s,' % json.dumps(payload["names"]))
    lines.append('    "dictionaries": {')
    dict_items = list(payload["dictionaries"].items())
    for i, (k, v) in enumerate(dict_items):
        comma = "," if i < len(dict_items) - 1 else ""
        lines.append("      %s: %s%s" % (json.dumps(k), json.dumps(v, sort_keys=True), comma))
    lines.append("    },")
    lines.append('    "tables": {')
    table_items = list(payload["tables"].items())
    for i, (k, v) in enumerate(table_items):
        comma = "," if i < len(table_items) - 1 else ""
        lines.append("      %s: %s%s" % (json.dumps(k), json.dumps(v, sort_keys=True,
                                                               separators=(",", ":")), comma))
    lines.append("    }")
    lines.append("  }")
    lines.append("}")
    text = "\n".join(lines) + "\n"
    # Round-trip: what we wrote must parse back to the payload we hashed.
    back = json.loads(text)
    assert hashlib.sha256(canonical(back["payload"])).hexdigest() == digest
    OUT.write_text(text, encoding="utf-8", newline="\n")
    return digest, len(text.encode("utf-8"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    args = ap.parse_args()
    payload, prefix_report = build()
    digest, size = write(payload, prefix_report, args.date)
    held = sum(1 for r in prefix_report.values() if r["holds"])
    print(f"wrote {OUT} ({size} bytes), sha256 {digest}")
    print(f"opencv {cv2.__version__}: {len(payload['names'])} dictionaries, "
          f"{len(payload['tables'])} tables; prefix property held for "
          f"{held}/{len(prefix_report)} names")
    for name, r in prefix_report.items():
        if not r["holds"]:
            print(f"  prefix FAILED: {name} is stored as its own table")


if __name__ == "__main__":
    main()
