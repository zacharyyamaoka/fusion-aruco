# fusion-aruco

ArUco Tags is an add-in for Autodesk Fusion that models 3D-printable ArUco
markers and boards. You give a tag an ID on each copy of a part, then export
it for printing.

**Status: prototype.** It works on the author's machine and is covered by the
tests below. Expect rough edges and breaking changes.

## What it does

The add-in puts an **ARUCO** panel on the Design workspace's Solid tab.
ArUco Extrude and ArUco Export show on the panel. ArUco Edit, ArUco IDs and
Send to PrusaSlicer are in its dropdown. ArUco Extrude is also listed under
Solid > Create, after Extrude.

- **ArUco Extrude.** Works like Extrude: pick a closed sketch profile or a
  planar face. You get a single marker, a grid board or a ChArUco board, built
  as one parametric feature in the timeline. Double-click the feature to change
  it. Options:
  - dictionary and ID;
  - auto-size to the face;
  - white + black, black only or white only, each with its own height
    (raised, flush or inlaid);
  - the marker's origin;
  - an optional corner mark that shows which way the tag is turned.
- **ArUco Edit.** Change a tag after it is made: black thickness, inlaid or
  raised, dictionary, ID. Every copy's tag part is rebuilt to match.
- **ArUco IDs.** Pick an assembly level (the whole design, or a sub-assembly)
  and give every copy under it its own marker ID, by hand or with "Number
  duplicates". The model does not have to be duplicated. OK writes the IDs and
  rebuilds the per-copy tag parts.
- **Send to PrusaSlicer.** Takes the selected bodies or components and makes
  one PrusaSlicer project, with the black cells already on extruder 2, then
  opens it in PrusaSlicer.
- **ArUco Export.** Pick one export type per run:
  - **PrusaSlicer project** (`.3mf`): every tag part on as many beds as needed.
    The tile prints on extruder 1 and the black cells on extruder 2, so no
    painting is needed.
  - **STL files**: one per tag part.
  - **PDF print sheet**: true-size markers and boards for paper, with a ruler
    on every page to check the print scale.

  Every export also writes the tag records `aruco.json` and `joint_origins.json`,
  into `~/CADExporter/<design>/`.

Each dialog remembers its last options in `~/CADExporter/_aruco_settings.json`.

## Repository layout

The layout matches the CAD Exporter repo this was taken from, so the add-in's
loader and the tests run unchanged.

| Path | What |
|---|---|
| `ArucoMarkers/` | The Fusion add-in (entry point `ArucoMarkers.py`, icons in `resources/`). |
| `CADExporter/bam_cad_core/` | The pure core: marker dictionaries, boards, sizing, heights, origins, corner marks, labels, print sheet, print beds. It needs no Fusion and no numpy. |
| `CADExporter/bam_joint_origins.py` | Reads Fusion Joint Origins for `joint_origins.json`. |
| `tests/` | Offline tests. They use a stand-in `adsk` module where Fusion is needed. |
| `tools/` | Generators and verifiers for icons, studies, renders and OpenCV checks, plus the sync script. |
| `docs/`, `reports/aruco/` | Research notes, proposals and the HTML reports. |

Some files in `ArucoMarkers/` are in-Fusion test harnesses, not part of the
user-facing commands: `aruco_*_lab.py`, `aruco_live_test.py`,
`aruco_ids_test.py`, `aruco_probe.py`, `aruco_cf_probe.py` and `aruco_demo.py`.
They run through a dev-only file bridge (`aruco_bridge.py`, client
`tools/aruco_bridge.py`). The bridge is **off** unless the file
`%USERPROFILE%\CADExporter\_aruco_bridge\ENABLED` exists, and it can only run
actions from a fixed allow-list.

## Install in Fusion

1. Clone this repo, or download it whole. The add-in loads its core from
   `../CADExporter/bam_cad_core`, so keep `ArucoMarkers/` and `CADExporter/`
   side by side. Copying `ArucoMarkers/` on its own will not work.
2. In Fusion, open **Utilities > Add-Ins > Scripts and Add-Ins** (Shift+S).
3. On the **Add-Ins** tab, click **+** and choose the `ArucoMarkers` folder.
4. Select **ArucoMarkers** and click **Run**. Tick **Run on Startup** if you
   want it every time.
5. The **ARUCO** panel appears on the Solid tab.

The add-in can run next to the CAD Exporter add-in. Fusion runs all add-ins in
one Python, so this add-in loads its core under a private module name and the
two copies never meet.

## Run the tests

Run each test as a plain script from the repo root. Each one prints
`N passed, M failed` and exits non-zero on a failure:

```
python tests/test_markers.py
python tests/test_aruco_board.py
python tests/test_print_beds.py
...
```

To run them all (bash):

```
for t in tests/test_*.py; do python "$t" || echo "FAILED: $t"; done
```

Requirements:

- Python 3.11 or newer.
- Most tests need only the standard library. `test_aruco_autosize.py` also
  needs numpy.
- `test_aruco_frame_pnp.py` needs OpenCV (`opencv-python`, 4.10 tested) and
  numpy. Without them it prints `SKIP` and passes.
- `test_aruco_frame.py` prints a note and skips its URDF half here. That half
  needs the CAD Exporter's URDF pipeline (see below).

The tools need more packages (OpenCV, numpy, Pillow, pypdfium2, matplotlib,
yourdfpy). Each tool's docstring says which ones.

## Key docs

The HTML reports are plain files. Open them in a browser from a clone, since
GitHub shows HTML as source.

- [reports/aruco/index.html](reports/aruco/index.html): the start page, with
  the decisions so far.
- [reports/aruco/how-it-works.html](reports/aruco/how-it-works.html): the
  prototype end to end, from tag to IDs, print sheet, URDF frames and camera
  check.
- [docs/research_aruco_timeline_feature.md](docs/research_aruco_timeline_feature.md):
  how ArUco Extrude lives in the Fusion timeline.
- [docs/research_aruco_fusion_architecture.md](docs/research_aruco_fusion_architecture.md)
  and [docs/research_aruco_prior_art.md](docs/research_aruco_prior_art.md).
- Proposals:
  - [auto-size](docs/proposal_aruco_autosize.md)
  - [corner mark](docs/proposal_aruco_chamfer.md)
  - [labels](docs/proposal_aruco_label.md)
  - [ID marks](docs/proposal_aruco_marks.md)
  - [entry points](docs/proposal_aruco_entry_points.md)
  - [ChArUco studio](docs/proposal_charuco_studio_in_fusion.md)
- [docs/aruco_ids_acceptance.md](docs/aruco_ids_acceptance.md): the acceptance
  gates for ArUco IDs.

The scripts `docs/build_aruco_*.py` rebuild the HTML reports. Two of them need
full-window Fusion screenshots that this repo leaves out:
`build_aruco_entry_points_report.py` and `build_aruco_howitworks_report.py`.

## What lives in the CAD Exporter

The tag frames reach a robot description through the CAD Exporter, not
through this repo. These pieces stay in the CAD Exporter:

- `tools/rebuild_urdf.py`: rebuilds a URDF with the tag frames from
  `aruco.json` and `joint_origins.json`.
- `tests/test_aruco_collect.py`: tests the exporter's collection of tag
  records.
- The URDF half of `tests/test_aruco_frame.py`, and its golden files.

## Keeping this repo in sync

The add-in is developed inside the CAD Exporter repo. To refresh this copy
from a commit there:

```
python tools/sync_from_cad_exporter.py <path to a CAD Exporter checkout> --commit <rev> --dry-run
python tools/sync_from_cad_exporter.py <path to a CAD Exporter checkout> --commit <rev>
```

- **What it copies:** the same file set, taken from `git archive` of that
  commit and never from the working files.
- **What it reports:** what was added, changed and removed, plus any new
  media or embedded images to review before committing.
- **What it never touches:** files outside that set.
- **Provenance:** `tools/sync_manifest.json` records the source commit.
- **Requirements:** git and Pillow.

## License

No license has been chosen yet. All rights reserved until a license is
chosen.
