# -*- coding: utf-8 -*-
"""Refresh this repo from the CAD Exporter repo it was extracted from.

    python tools/sync_from_cad_exporter.py <cad-exporter checkout> [--commit REV]
                                           [--dry-run] [--verbose]

The ArUco add-in is developed inside the CAD Exporter repo. This script copies
the same file set out of a COMMIT there (`git archive REV`, default HEAD), never
out of its working files, into this repo with the same relative layout, then
prints what was added, changed and removed.

Rules:
  * Only the managed set is ever written or deleted: the files that INCLUDE
    selects and EXCLUDE does not, plus the files the previous sync wrote
    (recorded in tools/sync_manifest.json). A managed file that is gone
    upstream is deleted; nothing else in this repo is touched.
  * The files this repo owns (OWNED) are never written, even if a pattern
    matches them: README.md, .gitignore, .gitattributes, the subset package's
    own CADExporter/bam_cad_core/__init__.py, this script and its manifest.
  * Media that shows the owner's own designs or his Fusion workspace is
    excluded by name (EXCLUDE), and three full-window Fusion captures embedded
    in two HTML reports are painted over (SCRUB_EMBEDDED). An embedded image
    in a report that the last sync did not see is listed under REVIEW, and so
    is every added media file: look at them before you commit.
  * In text files the owner's Windows profile path becomes %USERPROFILE%
    (REDACT_TEXT); it only appears in recorded outputs and doc examples.
  * After copying, the imports of the copied Python files are checked: a core
    module, a `bam_*` module, or a sibling script that is imported but not in
    the set is printed as a WARNING (add it to CORE_MODULES / INCLUDE only if
    it is not one of the CAD Exporter's own modules).

Needs git on PATH and Pillow (for the scrub). Exit 0, or 2 on a usage error.
"""
from __future__ import annotations

import argparse
import ast
import base64
import fnmatch
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "tools" / "sync_manifest.json"

# Top-level folders of the source that hold anything we take (git archive
# pathspec: keeps the tarball small).
ROOTS = ["ArucoMarkers", "CADExporter", "tests", "tools", "docs", "reports"]

# The pure core the add-in and its tests use, and nothing else from
# CADExporter/bam_cad_core. These import only each other.
CORE_MODULES = [
    "markers", "fiducials", "aruco_board", "aruco_dict", "print_sheet",
    "print_beds", "aruco_autosize", "aruco_slabs", "aruco_chamfer",
    "aruco_origin", "aruco_label",
]
CORE_DATA = ["aruco_dictionaries.json"]

# Core modules that may be named by an included file without being needed:
# tests/test_aruco_frame.py's URDF half imports them, and skips itself (with
# a printed note) when they cannot be imported -- as here.
CORE_OPTIONAL = {"cad_description", "cad_config", "urdf_writer"}

INCLUDE = [
    "ArucoMarkers/*",
    "CADExporter/bam_joint_origins.py",
    *[f"CADExporter/bam_cad_core/{m}.py" for m in CORE_MODULES],
    *[f"CADExporter/bam_cad_core/{f}" for f in CORE_DATA],
    "tests/test_aruco_*.py",
    "tests/test_markers.py",
    "tests/test_print_beds.py",
    "tests/golden/aruco_*",
    "tools/*aruco*",
    "tools/make_aruco_*",
    "tools/study_aruco_*",
    "tools/render_*aruco*",
    "tools/render_autosize_gallery.py",     # makes reports/media/aruco/autosize
    "tools/verify_print_beds.py",
    "docs/*aruco*",
    "docs/research_aruco_*",
    "docs/proposal_aruco_*",
    "docs/proposal_charuco_studio_in_fusion.md",
    "docs/build_aruco_*",
    "reports/aruco/*.html",
    "reports/media/aruco/*",
]

EXCLUDE = [
    "*/__pycache__/*", "*.pyc", "*/.vscode/*",
    # lives in the CAD Exporter: tests bam_fusion_export._collect_aruco
    "tests/test_aruco_collect.py",
    # lives in the CAD Exporter: needs its URDF pipeline
    "tools/rebuild_urdf.py",
    # only test_aruco_frame's URDF half (skipped here) reads these goldens
    "tests/golden/aruco_frame_default.urdf",
    "tests/golden/aruco_frame_default_markers.json",
    # captures of the owner's own design (the "rack" plates and holders)
    "reports/media/aruco/ids_variants/*",
    # full Fusion window captures: his project and document names, file
    # thumbnails, account initials and local paths
    "reports/media/aruco/ui/01_aruco_panel_tooltip.jpg",
    "reports/media/aruco/ui/02_ids_dialog_design_all_duplicate.jpg",
    "reports/media/aruco/ui/05_after_ok_root_variants_browser.jpg",
    "reports/media/aruco/ui/06_tag_dialog_rectangle_selected.jpg",
    "reports/media/aruco/ui/09_grid_board_result.jpg",
    # named after a real motor (GIM): left out when unsure
    "reports/media/aruco/heights_pins/bench_ghost_gim.png",
    "reports/media/aruco/live_extrude/bench_ghost_gim.png",
    "reports/media/aruco/live_extrude/04_dialog_default_width_gim_fixture.png",
]

OWNED = {
    "README.md", ".gitignore", ".gitattributes",
    "CADExporter/bam_cad_core/__init__.py",
    "tools/sync_from_cad_exporter.py", "tools/sync_manifest.json",
}

# Embedded (data: URI) images painted over before they are written: the
# sha256 of the decoded image -> boxes as fractions of (width, height).
_TOP = (0.0, 0.0, 1.0, 0.044)            # title bar + document tabs + account
SCRUB_EMBEDDED = {
    "reports/aruco/entry-points-proposals.html": {
        "aef71bc9f71d3da2228e9ea5dc8e06032de2771bc1bf17cde97e713b8e6e5ebf": [_TOP],
        "76c40cc46ffda03de0f5eff0138137c42a0c22f4071cdbf624380508e141ae17": [_TOP],
    },
    "reports/aruco/how-it-works.html": {
        "d1e2a072975a926e162548e13f9951d2ef3dbc69d1371ec45975e2a4c208c1a6": [
            (0.0, 0.0, 1.0, 0.041),          # title bar (project name) + tabs
            (0.0, 0.0, 0.1572, 1.0),         # Data Panel (his files)
            (0.1572, 0.893, 1.0, 1.0),       # Text Commands (local paths)
        ],
    },
}
SCRUB_FILL = (30, 34, 40)

# Text rewrites on every copied text file: the Windows profile folder of whoever
# runs the sync (the owner; --profile-user overrides), in any spelling the
# files use (drive:\Users\<name>, the doubled-backslash form inside JSON and
# Python strings, drive:/Users/<name>), becomes %USERPROFILE%, and the same name
# inside a Claude Code scratch-folder name becomes USER. Only recorded outputs
# and doc examples carry these paths; no code path depends on them.
TEXT_SUFFIXES = {".py", ".md", ".json", ".html", ".txt", ".svg", ".manifest", ".csv"}


def redactions(user: str) -> list[tuple[re.Pattern, bytes]]:
    u = re.escape(user.encode())
    return [
        (re.compile(rb"[A-Za-z]:(\\\\|\\|/)Users\1" + u + rb"(?![A-Za-z0-9_])", re.I),
         b"%USERPROFILE%"),
        (re.compile(rb"C--Users-" + u + rb"-", re.I), b"C--Users-USER-"),
    ]


def redact_text(data: bytes, rules) -> bytes:
    for pat, rep in rules:
        data = pat.sub(rep, data)
    return data

DATA_URI = re.compile(rb"data:image/(png|jpeg|jpg|gif|webp);base64,([A-Za-z0-9+/=]+)")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def selected(path: str) -> bool:
    if path in OWNED:
        return False
    if not any(fnmatch.fnmatchcase(path, p) for p in INCLUDE):
        return False
    return not any(fnmatch.fnmatchcase(path, p) for p in EXCLUDE)


def git(src: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(src), "-c", "core.autocrlf=false", *args],
                          check=True, capture_output=True).stdout


def read_archive(src: Path, sha: str) -> dict[str, bytes]:
    """Every file under ROOTS at `sha`, as committed (LF per .gitattributes)."""
    tar = tarfile.open(fileobj=io.BytesIO(git(src, "archive", "--format=tar", sha, "--", *ROOTS)))
    out = {}
    for m in tar.getmembers():
        if m.isfile():
            out[m.name] = tar.extractfile(m).read()
    return out


def embedded_hashes(html: bytes) -> list[str]:
    return [sha256(base64.b64decode(m.group(2))) for m in DATA_URI.finditer(html)]


def scrub(path: str, html: bytes, notes: list[str]) -> bytes:
    """Paint the SCRUB_EMBEDDED boxes onto the matching embedded images."""
    table = SCRUB_EMBEDDED.get(path)
    if not table:
        return html
    from PIL import Image, ImageDraw

    found = set()

    def fix(m):
        raw = base64.b64decode(m.group(2))
        boxes = table.get(sha256(raw))
        if boxes is None:
            return m.group(0)
        found.add(sha256(raw))
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        w, h = im.size
        d = ImageDraw.Draw(im)
        for x0, y0, x1, y1 in boxes:
            d.rectangle([round(x0 * w), round(y0 * h), round(x1 * w) - 1, round(y1 * h) - 1],
                        fill=SCRUB_FILL)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85, optimize=True, progressive=True)
        return b"data:image/jpeg;base64," + base64.b64encode(buf.getvalue())

    html = DATA_URI.sub(fix, html)
    for h in sorted(set(table) - found):
        notes.append(f"{path}: scrub target {h[:12]} not found (the report was rebuilt?) "
                     "-- look at its embedded Fusion captures by hand")
    return html


# ------------------------------------------------------------- import check

def imported_names(path: str, source: bytes) -> tuple[set[str], set[str]]:
    """(core module names, top-level module names) that `source` imports:
    import statements, aruco_core.module("x") and importlib.import_module("x"),
    read from the syntax tree so comments and docstrings never count."""
    core, top = set(), set()
    in_core = path.startswith("CADExporter/bam_cad_core/")
    for node in ast.walk(ast.parse(source, filename=path)):
        if isinstance(node, ast.Import):
            for a in node.names:
                parts = a.name.split(".")
                top.add(parts[0])
                if parts[0] == "bam_cad_core" and len(parts) > 1:
                    core.add(parts[1])
        elif isinstance(node, ast.ImportFrom):
            if node.level and in_core:
                core |= {node.module.split(".")[0]} if node.module else \
                    {a.name for a in node.names}
            elif not node.level and node.module:
                parts = node.module.split(".")
                top.add(parts[0])
                if parts[0] == "bam_cad_core":
                    core |= {parts[1]} if len(parts) > 1 else {a.name for a in node.names}
        elif isinstance(node, ast.Call) and node.args \
                and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str):
            fn = getattr(node.func, "attr", getattr(node.func, "id", ""))
            arg = node.args[0].value
            if fn == "module":                       # aruco_core.module("markers")
                core.add(arg)
            elif fn == "import_module":
                parts = arg.split(".")
                top.add(parts[0])
                if parts[0] in ("bam_cad_core", "arucocore") and len(parts) > 1:
                    core.add(parts[1])
    return core, top


def check_imports(new: dict[str, bytes], archive: dict[str, bytes]) -> list[str]:
    core_have = {Path(p).stem for p in new if p.startswith("CADExporter/bam_cad_core/")}
    core_all = {Path(p).stem for p in archive
                if p.startswith("CADExporter/bam_cad_core/") and p.endswith(".py")}
    local_all = {}                     # module name -> archive paths (scripts, add-in, bam_*)
    for p in archive:
        if p.endswith(".py") and p.count("/") == 1 and p.split("/")[0] in ROOTS:
            local_all.setdefault(Path(p).stem, []).append(p)
    local_have = {Path(p).stem for p in new if p.endswith(".py")}
    warns = []
    for path, data in sorted(new.items()):
        if not path.endswith(".py"):
            continue
        try:
            core, top = imported_names(path, data)
        except SyntaxError as exc:
            warns.append(f"{path} does not parse: {exc}")
            continue
        for n in sorted(n for n in core if n in core_all and n not in core_have
                        and n not in CORE_OPTIONAL):
            warns.append(f"{path} uses core module {n!r}, which is not in CORE_MODULES")
        for n in sorted(n for n in top if n in local_all and n not in local_have):
            warns.append(f"{path} imports {n!r} ({', '.join(local_all[n])}), "
                         "which is not in the set")
    return warns


# ---------------------------------------------------------------------- main

def summarize(label: str, paths: list[str], verbose: bool) -> None:
    print(f"{label}: {len(paths)}")
    if not paths:
        return
    if verbose or len(paths) <= 40:
        for p in paths:
            print("   ", p)
        return
    by_dir = {}
    for p in paths:
        by_dir[os.path.dirname(p)] = by_dir.get(os.path.dirname(p), 0) + 1
    for d, n in sorted(by_dir.items()):
        print(f"    {n:4d}  {d}/")
    print("    (--verbose lists every file)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("source", help="a checkout of the CAD Exporter repo (any worktree)")
    ap.add_argument("--commit", default="HEAD", help="commit to copy from (default HEAD)")
    ap.add_argument("--dry-run", action="store_true", help="print the diff, write nothing")
    ap.add_argument("--verbose", action="store_true", help="list every file")
    ap.add_argument("--profile-user", default=Path.home().name,
                    help="Windows user folder name to replace with %%USERPROFILE%% "
                         "(default: yours)")
    a = ap.parse_args(argv)

    src = Path(a.source).resolve()
    if not (src / ".git").exists():
        print(f"not a git checkout: {src}", file=sys.stderr)
        return 2
    sha = git(src, "rev-parse", "--verify", a.commit + "^{commit}").decode().strip()
    subject = git(src, "log", "-1", "--format=%s", sha).decode().strip()
    print(f"source  {src}\ncommit  {sha}  {subject}\n")

    archive = read_archive(src, sha)
    rules = redactions(a.profile_user)
    notes: list[str] = []
    new = {}
    for path in sorted(archive):
        if selected(path):
            data = archive[path]
            if path.endswith(".html"):
                data = scrub(path, data, notes)
            if Path(path).suffix.lower() in TEXT_SUFFIXES:
                data = redact_text(data, rules)
            new[path] = data

    old = {}
    if MANIFEST.is_file():
        old = json.loads(MANIFEST.read_text(encoding="utf-8"))
    old_files = old.get("files", {})
    old_embedded = {h for hs in old.get("embedded", {}).values() for h in hs}

    added, changed, same = [], [], []
    for path, data in new.items():
        dest = REPO / path
        if not dest.is_file():
            added.append(path)
        elif dest.read_bytes() != data:
            changed.append(path)
        else:
            same.append(path)
    removed = sorted(p for p in old_files if p not in new and p not in OWNED
                     and (REPO / p).is_file())

    embedded = {p: embedded_hashes(d) for p, d in new.items() if p.endswith(".html")}
    review = [f"{p}: embedded image {h[:12]} is new" for p, hs in embedded.items()
              for h in hs if h not in old_embedded]
    review += [f"{p}: new media file" for p in added if p.startswith("reports/media/")]

    summarize("added", added, a.verbose)
    summarize("changed", changed, a.verbose)
    summarize("removed (gone upstream)", removed, a.verbose)
    print(f"unchanged: {len(same)}")
    for n in notes:
        print("NOTE   ", n)
    warns = check_imports(new, archive)
    for w in warns:
        print("WARNING", w)
    if review:
        print(f"\nREVIEW before committing ({len(review)}): media can show your own "
              "designs or your Fusion window")
        for r in review[:60] if not a.verbose else review:
            print("   ", r)
        if len(review) > 60 and not a.verbose:
            print(f"    ... {len(review) - 60} more (--verbose)")

    if a.dry_run:
        print("\n(dry run: nothing written)")
        return 0
    for path in added + changed:
        dest = REPO / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(new[path])
    for path in removed:
        (REPO / path).unlink()
    MANIFEST.write_text(json.dumps({
        "source_commit": sha,
        "source_subject": subject,
        "files": {p: sha256(d) for p, d in sorted(new.items())},
        "embedded": embedded,
    }, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(f"\nwrote {len(added) + len(changed)}, removed {len(removed)}; "
          f"manifest {MANIFEST.relative_to(REPO).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
