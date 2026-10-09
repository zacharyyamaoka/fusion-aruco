# Proposal: ChArUco Studio in Fusion

Status: design proposal only, no code changed. Date: 2026-10-02.
Companion research: `docs/research_aruco_prior_art.md` and
`docs/research_aruco_fusion_architecture.md`.

## 1. What Zach asked for

> "It would be very convenient to have an ArUco generator built into Fusion.
> It could easily create decals, or I could draw rectangle sketches and then
> just convert them into ArUco markers. It would make sure they all had unique
> IDs. I'd set the marker size, dictionary size, etc., but it could even make
> intelligent suggestions there. And when you add things as sub-assemblies, it
> could have helper options to make sure things are still unique even in
> composition."

Seven requirements, used to score every proposal in section 12:

| # | Requirement |
|---|---|
| W1 | The generator lives in Fusion |
| W2 | Make a marker as a decal on a face |
| W3 | Turn a drawn sketch rectangle into a marker |
| W4 | IDs are unique without Zach tracking them |
| W5 | Zach sets size, dictionary, border and layout |
| W6 | The tool suggests good values |
| W7 | IDs stay unique when sub-assemblies are composed, including the same part inserted several times |

## 2. What already exists

### 2.1 ChArUco Studio (Linux, bam_aruco)

The Studio ("ArUco Setup Assistant") is a stdlib HTTP server with a plain
HTML/JS front end. It runs at `http://127.0.0.1:8790/` on the Linux box.

- **Data model** (`marker_sets.py`, schema version 2). A *set* has a UUID,
  a name, **one dictionary**, a `pixels_per_mm` print density (default 20,
  which is 508 DPI) and *boards*. Every board is ChArUco: `squares_x/y`,
  `checker_length_mm`, `marker_length_mm`, `margin_mm`, `thickness_mm`, an
  `object_transform`, and derived outer size, `marker_count =
  floor(sx * sy / 2)` and `ids`.
- **Managed IDs.** `normalize_marker_set` gives each board a contiguous
  range, keeps it across rename, reorder and resize when it still fits, and
  gives new boards the first free range. It is the best allocator in either
  codebase, and every proposal reuses it. **Two catches:** every set starts
  at ID 0, so two sets that share a dictionary collide; and unknown keys are
  dropped on save, so anything Fusion adds to a set is lost when the Studio
  next saves it.
- **Frames.** `aruco_frame` is on the printed top-left corner, with +X
  right, +Y down and +Z into the board. Five `mount_*` frames sit on the back
  face, and the 3D assembler joins boards by them.
- **Store.** One JSON per set in `~/.local/share/bam_aruco/aruco_sets/sets/`,
  shared with Keras Workbench and the 3D assembler. Writes are atomic, with
  `updated_at` as an optimistic lock. *Export set* writes PNGs with a `pHYs`
  density chunk, plus `set.json` and `aruco_objects.yaml`.
- **OpenCV draws pixels and nothing else.** `cv2` and `yaml` are imported
  lazily. The store, the allocator and `suggest_board_grids` (rank ChArUco
  grids that fit an outer envelope, within remaining capacity) are pure
  stdlib, so they can run inside Fusion's Python as they are.

Older formats sit beside it: `aruco_objects.yaml` (the detector config, which
also has single `Marker` and `GridBoard` types) and the robot-description
decal JSONs (`{name, image, width, height, meta: {dictionary, ids, ...}}`).

### 2.2 CAD Exporter fiducial and decal path (this repo)

Much of the export side exists already. The proposals finish it rather than
start again.

- **`fiducials.py`.** The `Fiducial` record (dictionary, ids, layout,
  `marker_length_m` = the black square, gaps, pose, host, and the binding
  keys `decal_name` and `entity_token`). `validate_fiducials` gates duplicate
  IDs across hosts, geometry, ID range, 1:1 aspect and planarity. The file
  also has `assign_id_ranges` and Zach's 70% rule (`board_for_tube`).
- **The library join.** `load_marker_library` reads `bam_aruco_library/1`,
  and `fiducials_from_decals` joins decals to entries by entity token, then
  by name ("Fusion owns WHERE, the library owns WHAT"). **Both are tested
  but never called by the export.** Today only hand-typed `model.fiducials`
  in `cad_config.json` reach the URDF.
- **Decals become textured quads.** `_collect_decals` reads each decal once
  per component. `decals_from_payload` expands it to every occurrence
  (`name#1`, `name#2`), and `resolve_decals` writes an OBJ+MTL quad per
  instance. The matrix convention was settled by measurement: the columns are
  the image axes at full size, the translation is the centre, and the frame
  is the defining component's.
- **Fiducials become frames.** `_add_fiducial_frames` (in
  `link_description.py`, or `link_tree.py` on the Cockpit branch) adds a
  massless `<name>_target` link on a fixed mount. `_emit_fiducials` draws a
  thin box and never a collision or inertial.

Six gaps:

1. The library join is not wired in.
2. **`Fiducial` mis-describes ChArUco.** It has no checker length, the
   count is `rows * cols` instead of `floor(sx * sy / 2)`, and the extent
   uses marker plus gap instead of `squares * checker + 2 * margin`. A Studio
   board would trip two gates.
3. **Instances share IDs.** Every occurrence gets the same image.
4. **Two frame conventions.** The decal is centre-based, and its first
   column points image-left on the visible side. `aruco_frame` is top-left
   with +Y down. One adapter must own the conversion.
5. **Four schemas** for one thing: Studio v2 (mm, ChArUco only),
   `bam_aruco_library/1` (m), decal JSON, and `aruco_objects.yaml`.
6. **Posture.** `sidecar.py` records that writing Fusion attributes "was
   measured and refused on 2026-08-20": the one live write crashed Fusion,
   and a write "creates a second home for a fact that already has one".
   Cockpit ruling D3 later approved user-invoked, design-mutating verbs. Any
   generator writes geometry. A proposal that also keeps *truth* in
   attributes reverses a recorded decision.

**Context.** The **Cockpit** (a React and shadcn palette opened by one
toolbar press: tree on the left, inspector on the right, Problems drawer at
the bottom) lives on branch `claude/cad-exporter-functional-lib-d0938d` and
is not in `main` yet. The Datasheet Editor shows the bridge pattern this
repo already uses: `adsk.fusionSendData` from JS, `sendInfoToHTML` back, and
a watcher thread that fires a custom event. The export folder
`~/CADExporter/` is its own git repo, pushed to GitHub by *Sync*, and Linux
already edits `cad_config.json` through it. From this Windows box only GitHub
is reachable: no SSH host, mapped drive or WSL.

### 2.3 Prior art in Fusion

- **arnoldad/apriltag3D** (MIT). A Custom Feature builds an AprilTag as
  white and black bodies (for multi-material printing), adds a Joint Origin
  at the face centre and a socket cutter, and can deboss the ID. It is pure
  stdlib. It has no ArUco and no ID logic.
- **wglockner/ArUco-Generator** (MIT). A dialog for single, grid or ChArUco
  markers that pip-installs OpenCV at first run and inserts the PNG as a
  **Canvas**. It uses OpenCV calls that have since been removed. One ID at a
  time.
- **Code Generator** (marketplace, $24.99, closed source). ArUco and
  AprilTag as sketch or bodies, with one hand-typed ID.

The prior-art research reaches a verdict: drawing a marker is solved many
times over, but **no tool in any CAD package keeps track of IDs across a
design or an assembly, and none suggests a dictionary or a minimum size**.
W4, W6 and W7 are new ground. An open apriltag3D issue (#13) asks for the
range estimate in section 10.

### 2.4 Fusion API facts these proposals lean on

Sources: *repo* (this repo's probe of Zach's Fusion), *ref* (Autodesk API
reference, looked up 2026-10-02), *research* (the architecture research
file). Anything else carries (verify).

| Fact | Source |
|---|---|
| Decals are released API (Sept 2024): `comp.decals.createInput(png, [face], point)` then `add`. Size, rotation and flip ride on `transform`; there is no width field | ref, research |
| `Decal.imageFilename`, `name` and `opacity` are settable later; faces and transform change only through `redefine()` | ref |
| So a placed decal **can be re-rendered in place** by writing a new PNG and setting `imageFilename` | follows |
| `Decal` exposes `transform`, `faces`, `entityToken`, `saveImage`, `createForAssemblyContext` | repo |
| Decal creation fails in direct-modeling designs and sometimes ignores `transform` | research, one forum report (verify) |
| Decals on a child's proxy face, made from the parent: undocumented | (verify) |
| Custom Features are still **preview** ("never deliver any programs that use any preview capabilities"). Recompute works only for sketches, base features and combines. `addDependency` accepts profiles | ref, research |
| Occurrence attributes are stored **per assembly path**: written on the proxy `root > A:1 > B:1`, they live in the root. Component attributes are shared by all instances | research |
| Paste New copies attributes, so expect duplicate IDs after a paste | research (inferred) |
| **No insert event.** Rescan on open, save and `ui.commandTerminated`; ParametricText regenerates this way, without Custom Features. The insert command's id is unknown | research (verify the id) |
| Design intent: **Assembly designs allow no sketches or features**; Hybrid allows both | research |
| Palettes are Qt WebEngine. Messages sent at creation are lost (handshake needed); palettes die on a workspace switch | research |
| A localhost HTTP server on a worker thread is fine (FusionMCPSample). A palette `fetch` to localhost may hit CORS | research (verify CORS) |
| Fusion's Python is 3.14 since January 2026 | research |
| Custom-graphics groups for viewport overlays exist; text labels specifically | research (verify text) |

## 3. Shared vocabulary, record and sync

**Words.**
- **Marker:** an n x n grid of data **bits** inside a black **border** `b`
  cells thick (default 1). The black square spans `M = n + 2b` **modules**.
- **Quiet zone:** white margin outside the border. The detector needs it.
- **Dictionary:** OpenCV's fixed table of valid patterns (`DICT_5X5_100` is
  5 x 5 bits, 100 IDs).
- **Grid board:** markers with gaps between them. **ChArUco board:**
  markers in the white squares of a chessboard, whose corners refine to
  sub-pixel accuracy.
- **Slot:** where a marker goes, defined once in a component.
- **Instance:** that slot in one occurrence. A part inserted three times
  is one slot and three instances.
- **xref:** a component stored in another document, read-only from the
  parent.
- **Materialize:** produce the marker's pixels or geometry.

**One record for all five proposals.** They differ in UI, truth and timing,
not in what a marker *is*. `bam_marker/1` is a superset of the four schemas:

```
slot (per component definition)          instance (per occurrence path)
  key            "wrist_L2_outer"          slot_key     "wrist_L2_outer"
  kind           single | grid | charuco   occurrence   "Arm:1+Wrist:2"
  dictionary     "DICT_4X4_100"            ids          [40..52]      <- resolved
  squares/rows/cols, marker_length_m       id_source    auto | pinned | class
  checker_length_m (charuco)               binding      {decal entity_token, name}
  separation_m (grid), margin_m            printed      {date, px_per_mm} | null
  border_bits, thickness_m
  local_count    13                        (pose and host always come from
  studio         {set_id, board_id} | null  Fusion, never stored here)
```

**IDs belong to instances, not slots.** A slot says "I need 13 IDs". An
instance says "these 13". Adapters write Studio v2 (ChArUco slots, mm),
`bam_aruco_library/1` (one entry per instance, keyed by decal name) and
`aruco_objects.yaml` (one object per instance). `Fiducial` gains
`checker_length_m` and ChArUco-aware count and extent, which closes gap 2.

**Cross-machine building blocks.**
- **S1, git, which works today.** `git init` the Studio store on Linux,
  ignore `exports/` and `.trash/`, push to a private GitHub repo, and clone it
  on Windows. One file per set means git merges per set. No Studio, Keras or
  assembler code changes.
- **S2, the Studio on Windows.** The stdlib server plus OpenCV from a pixi
  env (verify that bam_aruco runs on Windows), with `--data-dir` set to the
  S1 clone. The Studio is then local to Fusion.
- **S3, render without OpenCV in Fusion.** Dictionary bit tables as JSON,
  exported once from OpenCV, and PNG output from `zlib` + `struct` (the
  Studio's `_png_with_physical_resolution` already writes `pHYs`).
- **S4, a live link** (Tailscale or an SSH tunnel). Not available on this
  box, and nothing depends on it.

**One writer per set.** Studio-made sets are read-only in Fusion, which
copies boards out of them. Fusion-made sets (`Fusion · <design>`) are edited
only from Fusion. A Studio save would strip their instance table. Git then
never has to merge two edits to the same file.

| | Where the UI lives | Who owns the truth | When geometry is made |
|---|---|---|---|
| A Marker Feature | native dialog + timeline | the Fusion document | live parametric feature |
| B Studio Dock | the Studio's web UI in a palette | the Studio set store | one-shot decal |
| C Ghost Slots | browser tree and canvas only | a repo-tracked library file | none until export |
| D Studio Remote | the Studio stays external | the Studio store, handed off as a library file | batch decals from Studio PNGs |
| E Cockpit Markers | a Cockpit lens + one small command | the sidecar `cad_config.json` | one-shot decals, re-rendered per instance at export |

## 4. Proposal A: Marker Feature

**Pitch:** a real Fusion feature. Pick a rectangle and get a marker that
lives in the timeline and rebuilds when you edit the rectangle.

**Flows.**
- **(a) Rectangle.** *Solid > Create > Marker Feature*, then select a
  rectangle profile. The feature fits the largest marker or board inside it,
  quiet zone included. It writes the cells into a sketch, can build them as a
  body, and adds a Joint Origin at `aruco_frame`. Bodies use apriltag3D's
  fast path: TemporaryBRep boxes, touching cells merged, one union into a
  base feature. (A 2025 forum thread got stuck extruding pixel profiles one
  by one.) `addDependency` on the profile makes a rectangle drag recompute
  the cells. That is the one shape Custom Feature recompute supports, though
  no donor ships it yet.
- **(b) Decal.** *Output: Decal* places an S3-rendered PNG on a picked face.
  Decals cannot live inside a Custom Feature, so this one sits beside it,
  linked by attribute, and a recompute swaps `imageFilename`. Decals are
  second-class here.
- **(c) Suggestions.** A *Suggest* group (camera, distance, angle) ranks
  (dictionary, border, size) chips. The ID field reads `auto: 17`.
- **(d) Same part three times.** The feature lives in the component, so all
  three copies show the same geometry and ID. *Marker Scan* offers three
  fixes. *Override* writes an ID base on each occurrence proxy (stored per
  assembly path) and applies only at export. *Make Independent* gives
  distinct geometry. *Accept as class* silences the warning. An xref allows
  only override and accept. An Assembly-intent design cannot hold the feature
  at all. **If the marker is embossed into the part, no override changes
  what gets printed.**

```
 SOLID > CREATE                        ADD-INS: [CAD Exporter][Datasheet]
 +---------------------+
 | [#] Marker Feature  |               BROWSER
 | [#] Marker Socket   |               v Wrist v3
 +---------------------+                 v Sketches
                                             Sketch3            (rectangle)
 +- MARKER FEATURE --------------------+ v [#] Marker 17  4X4_50
 | Input       [1 profile selected]    |     Cells sketch
 | Kind    (o)single ( )grid ( )ChArUco|     Body  black_cells
 | Dictionary  [DICT_4X4_50       v]   |     Joint Origin  aruco_frame
 | ID          [auto: 17  ] [lock]     |
 | Border      [1 v]  Quiet zone [1.5] |   TIMELINE
 | Fit         26.0 mm in 35 x 35 mm   |   [sk][ext][sk][#17]  <- double-click
 | Output      [Sketch + emboss v] 0.6 |                          to edit
 | -- Suggest ------------------------ |
 | Camera [D435 1080p v] Z[0.8] ang[45]|   CANVAS
 |  * 4X4_50   26 mm  5.4 px/bit  ok   |   real cells in the sketch, triad at
 |    5X5_100  26 mm  4.6 px/bit  low  |   the top-left corner
 |                      [OK] [Cancel]  |   (+X right, +Y down, +Z in)
 +-------------------------------------+
```

**Truth and sync.** The `.f3d`. Custom parameters hold dictionary, IDs and
size; apriltag3D uses them because attribute writes lag compute by one
transaction. A design-attribute registry laid out like ParametricText's
holds allocator state, and entity tokens catch Paste New copies. *Export
set* and *Import board* exchange Fusion-owned sets through S1.

**Exporter.** Read `findAttributes("bam_marker", ...)` into one `Fiducial`
per instance, with the pose taken straight from the Joint Origin (no decal
matrix). Render the texture from the record. This reverses the 2026-08-20
attribute decision.

**Studio reuse:** allocator logic, `suggest_board_grids`, the L1/L2
templates. **Borrows:** almost all of apriltag3D.

**Effort: L (about 4 weeks).** Custom Feature, edit command and compute,
5 days. Cell geometry, 3 d. Emboss, inlay and socket, 3 d. Registry and
scanner, 3 d. Export, 2 d. Studio exchange, 1 d. Tests and smoke, 3 d.

**Risks:** a preview API; a crash history with attribute writes; slow
compute for a ChArUco board's hundreds of cells; non-manifold solids where
cells touch only at a corner; Part designs offer only *Configure*.
**Worst at:** W7, and at decals, the common case.

## 5. Proposal B: Studio Dock

**Pitch:** the Studio you already use, docked in Fusion. Its set store stays
the truth, and *Place* turns any board into a decal on the face you pick.

**How it runs.** The add-in starts the Studio's own server (`create_server`)
on a worker thread, the way FusionMCPSample runs an HTTP server inside
Fusion. It uses a vendored `marker_sets.py` with S3 rendering swapped in and
the store pointed at the S1 clone. The palette loads that URL, so the
Studio's HTML, CSS and JS are served unchanged. One injected script adds a
**Fusion strip** (selection, *Fit to selection*, *Place*, *Sync*) that talks
through `adsk.fusionSendData`. If that call is unavailable on an
`http://127.0.0.1` page (verify), the fallback is a local page with a
`fetch` shim that routes `/api/*`, including preview `<img>` URLs, through
the bridge. That also avoids CORS. The strip waits for a ready ping, and the
palette is rebuilt after a workspace switch.

**Flows.**
- **(a) Rectangle.** Select it. The strip reads "rect 50.8 x 203.2 on Upper
  Arm:1". *Fit to selection* fills the Size assistant envelope and calls
  `suggest_board_grids`. Pick one, and the Studio adds the board with its
  usual ID allocation. *Place* creates a decal centred on the rectangle,
  aligned to its edges and scaled through `transform`. There is no width
  field, so measure the default size once. The decal is named
  `<set>/<board>`, and the rectangle is hidden.
- **(b) Decal.** Select a board, then *Place on face*.
- **(c) Suggestions.** The Studio's Size assistant and capacity meter, plus
  a new *Camera* card.
- **(d) Same part three times.** A *Design* tab lists decals per
  occurrence, with collisions across instances and across sets shown in red.
  *Own board for this instance* duplicates the board (fresh range) and binds
  the instance to it. That binding needs a `placements` list in the set,
  which is Studio schema v3, because v2 drops unknown keys. An xref keeps its
  own decals; the override lives in the parent's set.

```
+- Fusion ------------------------------------------+- Studio Dock --------------+
| ADD-INS: [CAD Exporter][Datasheet][Studio]        | Fusion: rect 50.8x203.2 on |
|                                                   |  Upper Arm:1               |
| BROWSER                 CANVAS                    | [Fit to selection] [Place] |
| v Robot                                           | [Sync]        set: Wrist v |
|   v Upper Arm:1         +--------+                |----------------------------|
|       Sketch7 (hidden)  |##  ##  |  <- decal      | SETS        | BOARDS       |
|       Decal Wrist/L1    |  ##  ##|     "Wrist/L1" | Wrist Proto | L1 2x8 ids0-7|
|   v Forearm:1           |##  ##  |                | Shoulder Rig| L2 2x13 8-20 |
|       Decal Wrist/L2    +--------+                | Iceland Arm | + Add board  |
|   v Forearm:2  !dup                               | 20 px/mm    |--------------|
|       Decal Wrist/L2    (same image as :1)        | [Export set]| preview PNG  |
|                                                   |             | [Download]   |
|                                                   |             | frames corner|
+---------------------------------------------------+----------------------------+
```

**Truth and sync.** The Studio store (S1 clone), with Fusion as one more
client, like Keras. Linux sees changes after `git pull`. With S2 both
machines share one folder. Fusion stores only named decals.

**Exporter.** Read the clone and its placements into a library in memory,
then use `fiducials_from_decals` (already written). Gap 2 must be fixed
first.

**Studio reuse:** the most of any proposal (UI, handlers, store, allocator,
suggestions). **Borrows:** wglockner's "rendered PNG on a face", as a decal
and without pip.

**Effort: M-L (about 3 weeks).** Server thread and S3 patch, 3 d. Strip and
bridge, 2 d. Place, 2 d. Schema v3 and cross-set allocation in the Studio,
3 d (Linux). Export, 2 d. Sync, 1 d. Smoke, 2 d.

**Risks:** a schema change that Keras and the assembler must accept; the
bridge on an http page; ChArUco-only (single markers need Studio work); two
visual styles in one window. **Worst at:** W3. The rectangle-first flow is
bolted onto a board-first app, and the design owns nothing.

## 6. Proposal C: Ghost Slots

**Pitch:** draw rectangles, tag them as slots, and stop thinking about IDs.
The CAD barely changes, and markers exist only from export onward, unique
per instance.

**Flows.**
- **(a) Rectangle.** Right-click a sketch profile, then *Make marker slot*.
  That is a no-dialog command (`isAutoExecute`, like the Datasheet button).
  It records the slot (component, sketch, profile, key) in the library with
  design defaults: `single` if square-ish, ChArUco if long. Nothing is added
  to the model. A custom-graphics overlay shades the rectangle and labels the
  IDs per instance (verify text). Overlays are redrawn on each start.
- **(b) Decal.** None. *Make marker slot* on a face drops a construction
  rectangle sized by the suggestion, and that rectangle is the slot.
- **(c) Suggestions.** In the export report, as instruction-style warnings:
  dictionary, fitted size, px/bit at range. Edits happen in JSON, or later
  in the Cockpit.
- **(d) Same part three times.** This is C's strength. At export, slots
  expand over occurrences (decals already do this), and instances sorted by
  (slot key, occurrence path) get contiguous ranges. *Print sheet* writes
  **pins** (instance to IDs), so a printed instance never moves and new ones
  take free ranges. Three copies get three ranges with zero clicks. An xref
  ships its own slots, the parent pins them, and the child is never touched.

```
 BROWSER                       CANVAS (custom graphics overlay)
 v Robot                       +--------------+   +--------------+
   v Forearm:1                 |# # # # # # # |   |# # # # # # # |
     v Sketches                | slot L2_outer|   | slot L2_outer|
         Sketch4  [slot]       | ids 8-20     |   | ids 21-33    |
   v Forearm:2                 +--------------+   +--------------+
     v Sketches                   Forearm:1          Forearm:2
         Sketch4  [slot]
                               RIGHT-CLICK ON A SKETCH PROFILE
 ADD-INS: [CAD Exporter]       +--------------------------+
          [Datasheet]          | Make marker slot         |
                               | Remove marker slot       |
 EXPORT REPORT (warnings)      | Show slot IDs            |
 [marker] L2_outer#2: ids      +--------------------------+
  21-33 (auto, unpinned),
  14.0 mm, 5.0 px/bit @0.65 m
```

**Truth and sync.** A repo-tracked `markers.json` (`bam_aruco_library/2`)
holding slots, defaults (camera, density) and pins. It lives in the data
repo, so it reaches Linux through the existing Sync. A converter publishes
one Fusion-owned Studio set per design over S1. A slot may reference a
Studio board.

**Exporter.** The payload gains `marker_slots` (rectangle corners, plane,
component). The core expands, allocates and renders per instance (S3). The
pose is taken **from the rectangle itself**, which is exact and avoids the
decal matrix. `resolve_decals` and `_add_fiducial_frames` emit the quads and
frames. A print folder gets per-instance PNGs and `aruco_objects.yaml`.

**Studio reuse:** the allocator (plus pins), rendering semantics, `pHYs`,
`suggest_board_grids`. **Borrows:** the KiCad image injector's idea, where a
tagged placeholder rectangle stays the truth and is filled with pixels on
output.

**Effort: S-M (about 2 weeks).** Slot command and reader, 2 d. Library v2,
allocator and pins, 2 d. S3 renderer with golden tests, 3 d. Export, 2 d.
Overlay, 2 d (optional). Print sheet, 1 d.

**Risks:** not WYSIWYG; IDs can shift if nobody pins before printing;
rotated or projected rectangles. **Worst at:** W2, and at anything that is
not a sticker.

## 7. Proposal D: Studio Remote

**Pitch:** keep the Studio as the one marker app and teach it about the
robot. Fusion sends faces and gets finished decals back in one batch.

**Flows.**
- **(a) Rectangle.** Select rectangles or faces, then *Send to Studio*. That
  writes `requests/<design>.json` to the S1 repo: envelope, occurrence
  paths, instance count, planarity, xref flag. On Linux, a new **Robot** view
  suggests boards per slot (`suggest_board_grids`), allocates IDs across all
  instances and *exports to Fusion* (`bam_aruco_library/2` plus PNGs).
  Fusion's *Apply from Studio* places every decal in one batch.
- **(b) Decal.** The same batch, or *Insert from library*, which places the
  Studio's byte-exact print PNG on a picked face.
- **(c) Suggestions.** All in the Studio. Fusion contributes measured face
  sizes, the one thing the Studio cannot know.
- **(d) Same part three times.** The request carries instance counts, so
  each instance gets a range from the start. Per-instance decals land in the
  parent if the API allows that (verify), and otherwise at export. Xref
  overrides live in the Studio's placement table.

```
 FUSION (Windows)                          STUDIO (Linux, :8790)
 ADD-INS: [CAD Exporter][Datasheet]        +- Robot: Arm v12 ---------------+
          [Send to Studio][Apply]          | slot        need  inst  board  |
                                           | L1_outer    50x203  1   L1 2x8 |
 +- SEND TO STUDIO -----------------+      | L2_outer    38x247  2   L2 2x13|
 | 3 rectangles, 1 face selected    |      |   Forearm:1  ids 8-20          |
 | design   Arm v12                 |      |   Forearm:2  ids 21-33         |
 | instances found  4 (1 xref)      |      | hand_tag    38x38   1   single |
 | request  requests/Arm_v12.json   |      | [Suggest] [Allocate] [Export   |
 |                 [Send] [Cancel]  |      |  to Fusion]   capacity 35/100  |
 +----------------------------------+      +--------------------------------+
      |  git push            git pull  ^          |  git push
      v                                |          v
      +------- S1 repo: requests/  library/  png/ -+
 BROWSER after Apply:  Decal L2_outer#1, Decal L2_outer#2 ...
```

**Truth and sync.** The Studio store plus a new `robots/<design>.json`
(a schema change). The library file is the hand-off. S1 both ways means one
sync per side per round trip. S2 makes the round trip instant.

**Exporter.** The least new code. Load the library, and
`fiducials_from_decals` matches decals by name, `L2_outer#2` included.

**Studio reuse:** all of it, and the new features help Keras and the
assembler too. **Borrows:** wglockner.

**Effort: M (about 2.5 weeks, over half on Linux).** Fusion: request
writer 1 d, batch apply 2 d, export wiring 2 d. Studio: robot view,
instance allocation and library export, 5 d. Sync, 1 d.

**Risks:** round-trip friction; requests go stale when rectangles change; a
schema change other clients read. **Worst at:** W1 and W3. "Draw a rectangle
and just convert it" becomes a two-machine errand.

## 8. Proposal E: Cockpit Markers

**Pitch:** markers become a fourth lens of the Cockpit you already open with
one press: every marker instance in one table, conflicts flagged, renumber
in one click. A small *Make Marker* command turns rectangles into decals.
The sidecar is the truth, as it is for everything else the exporter
decides.

**Flows.**
- **(a) Rectangle.** Select rectangles, then *Make Marker* (a small dialog,
  also a Cockpit context-menu verb), pre-filled with the suggestion. OK
  renders the PNG (S3), creates a key-named decal on the face under the
  rectangle, aligned to its edges, and stages the slot and instances into
  `model.markers`. Following ParametricText, a `commandTerminated` hook sees
  sketch edits and re-fits the bound decals through a hidden auto-execute
  command, one undo step each. That is parametric enough without the
  preview API. A *Refit* verb covers anything the hook misses.
- **(b) Decal.** The same command on a face, or *Add marker* in the inspector.
- **(c) Suggestions.** The inspector holds the design's camera profile
  (`model.markers.camera`), px/bit per instance, a design-wide capacity
  meter and suggestion chips. Edits are staged like the semantics sidecar.
- **(d) Same part three times.** The Cockpit already walks every occurrence,
  so the lens lists instances. A scan runs on Cockpit open, document open and
  save, and after insert or paste commands. Conflicts appear in Problems as
  `W_MARKER_DUPLICATE`, with the verbs *Renumber instance*, *Same object
  class* and *Make Independent* (Fusion-only, absent elsewhere, per D3).
  Fusion shows the component's decal, with instance #1's IDs, on every copy.
  In Hybrid designs, a per-instance decal from the parent may work (verify).
  In Assembly-intent designs it cannot. The fallback always works: an
  overlay label per copy ("ids 21-33"), per-instance thumbnails in the lens,
  and per-instance re-render at export. Xref slots are read from the child.
  The parent's sidecar holds their IDs.

```
+- Cockpit -------------------------------------------------------------------+
| Export URDF | Export & View | Probe | Folder | Sync         (Fusion only)   |
| Arm v12   33 occ  17/18 joints  12 links     [Search  Ctrl P]  Tree Insp    |
+-------------------+----------------------------------+----------------------+
| ASSEMBLY          |  [SubAssembly|Joint|Link|Markers] | INSPECTOR            |
| v Upper Arm:1  #1 |  instance        slot     ids    | L2_outer  (charuco)  |
| v Forearm:1    #1 |  Upper Arm:1     L1_outer  0-7   | 2 x 13, 19/14 mm     |
| v Forearm:2  ! #1 |  Forearm:1       L2_outer  8-20  | DICT_4X4_100         |
| v Hand:1 (xref)#1 |  Forearm:2    !  L2_outer  8-20  | camera D435 1080p    |
|                   |  Hand:1 (xref)   tag      30     | Z 0.65 m: 5.0 px/bit |
|                   |  ------------------------------- | at 45 deg: 3.5  LOW  |
|                   |  capacity 35/100  [Renumber all] | [Make Marker][Refit] |
+-------------------+----------------------------------+----------------------+
| PROBLEMS (1)  W_MARKER_DUPLICATE  Forearm:2 repeats ids 8-20 of Forearm:1   |
|               -> right-click: Renumber instance | Same object class | ...   |
+-----------------------------------------------------------------------------+
 FUSION CANVAS: decals on faces; overlay label per extra instance "ids 21-33"
```

**Truth and sync.** `model.markers` in `~/CADExporter/<design>/cad_config.json`,
which is already the single source of truth. It reaches Linux through the
data repo, where viser can read it. *Publish to Studio* writes a Fusion-owned
set over S1, and *Import from Studio* copies boards in as slots.

**Exporter.** `model.markers` becomes per-instance `Fiducial`s, with the
pose from `decal_placement.placement` through one `aruco_frame` adapter. The
library join is wired in, fed by the sidecar. `resolve_decals` gains a
per-instance image override.

**Studio reuse:** vendored `marker_sets.py` (store, allocator, suggestions),
the `app_logic.js` frame math, `pHYs`. **Borrows:** apriltag3D's
named-origin idea, as an optional Joint Origin at `aruco_frame`.

**Effort: M (about 3 weeks; 2 without the lens).** Pure core, 4 d. Export,
2 d. *Make Marker*, 2 d. Lens (table, inspector, codes), 4 d. Studio
exchange, 1 d. Smoke, 2 d.

**Risks:** it waits on the Cockpit merge; per-instance WYSIWYG rests on an
undocumented API; decal `transform` quirks and mirroring (section 13). It is
a posture change, but limited to user-invoked, undoable writes. **Worst
at:** true timeline parametrics. It also has no printed-in markers.

## 9. ID uniqueness in composition

**The problem.** A component is a definition and an occurrence is a
placement of it, so a decal drawn in `Forearm` is shown on every copy with
the same IDs. Nesting multiplies it: an `Arm` with two `Forearm`s, inserted
twice, gives four copies of IDs 8-20, at `Arm:1+Forearm:1` through
`Arm:2+Forearm:2`. This is the shipped L2 bug ("four faces share one
texture") produced by composition instead of by hand. The detector does not
fail: it reports a confident pose for the wrong copy, and for ChArUco it
merges two copies into one garbage board solve. A second source is **two
Studio sets**, both starting at ID 0.

| Option | How | Good | Bad |
|---|---|---|---|
| O1 Per-instance override | Slot defined once; each occurrence path gets its own range, held by the top-level design; textures re-rendered per instance | Component untouched; works for xrefs; shared edits survive | Fusion shows one image for all copies unless parent decals work (undocumented in Hybrid, impossible in Assembly intent); useless for printed-in geometry |
| O2 Make Independent | Fusion's own split into a separate component | True WYSIWYG; right for embossed markers | Breaks shared edits; bloats the design; breaks the link for an xref |
| O3 Reserved ranges | Component reserves `k * max_instances`; instance i takes `base + i*k` | Simple, pre-allocatable | Wastes IDs; `:2` can become `:3` after a delete and re-insert; still needs O1 to show |
| O4 Same object class | Repeats are intentional, and something else (tracking, kinematics) tells copies apart | No bookkeeping; right for identical tools or bins | Wrong for robot links; breaks ChArUco when two copies are in view |
| O5 Scanner + auto-renumber | Expand every slot, find collisions (instance vs instance, set vs set), renumber while keeping existing ranges | Catches every source, including cross-set | Needs O1 or O2 to hold the result |

**Recommendation: O1 + O5 by default, O4 as explicit opt-in, O2 as a
manual escape hatch.**
- **The top-level design owns the instance table**, keyed by occurrence
  path (the exporter's existing key) with the decal entity token as a backup.
  Occurrence-proxy attributes, which are stored per assembly path, could
  hold it natively. The sidecar is preferred for the posture reason in gap 6.
- **Allocate like the Studio.** Run `normalize_marker_set`'s rule over
  instances: keep a range that still fits, and give new instances the first
  free range. Freed ranges wait for *Renumber all*. Reusing one at once
  would hand a new copy the IDs of a sticker that may still be on the shelf.
- **Pin what was printed.** A printed instance never moves automatically.
- **Scan everywhere:** Cockpit open, every export (as `validate_fiducials`
  warnings, which already compare by host), document open and save, and
  after insert and Paste New, caught through `commandTerminated` (there is no
  insert event, and Paste New copies attributes and decals). The scan also
  covers set against set, and a design-level *reserved* list for markers
  outside the CAD (bench calibration boards, other robots in view).
- **Opt-in duplicates:** `id_source: class` silences the warning and writes
  `class: <slot>` into the detector config. It is never the default.
- **Printed-in markers are different.** If a marker is embossed, inlaid or
  engraved, only O2 gives real per-instance IDs, and the scanner says so.

**Nesting.** Allocate once, at the top, over fully expanded leaves. A
sub-assembly's own table (from exporting it alone) acts as a suggestion:
kept where it does not collide, so a reused sub-assembly usually keeps its
IDs, and re-based where it does.

**xrefs.** (1) The child's slots are read, never written: from its registry
in the data repo, or from its key-named decals. (2) Instance IDs live in the
parent's table. (3) If the child's own image must change, because it ships
as its own product, the tool says "open Hand v4, renumber, save, update the
reference". That is the only path that edits a child. (4) The parent records
which child version its overrides were made against, and warns when the
reference updates. Decals inside an xref are likely read-only from the
parent (verify), which is one more reason the per-instance truth lives in
the parent and reaches pixels at export.

## 10. Smart suggestions

Plain arithmetic, all runnable in Fusion's Python without OpenCV.
Symbols: `n` bits per side, `b` border bits, `M = n + 2b`, `s` the black
square side, `p` pixels per module, `Z` distance, `theta` worst view angle.

**1. Capacity: the smallest dictionary that fits, plus headroom.** The
tracker loads one dictionary per collection, so the whole robot shares one:

```
N_need = sum over instances of markers + N_reserved
C      = smallest of {50, 100, 250, 1000} with C >= max(1.5 * N_need, N_need + 10)
```

Two arms with L1 + L2 (21 markers each) plus a hand tag is 43, which
suggests `_100`. Within one bit size, smaller is safer: fewer valid patterns
for noise to hit. OpenCV's `_50`, `_100` and `_250` tables are the first N
entries of `_1000` (prior-art research), so Zach's `dict3 = DICT_4X4_1000`
strips (IDs 0-24) can be re-declared `DICT_4X4_50` with **no reprint** and a
stricter detector. The engine should say so whenever usage is far below
capacity.

**2. Bits: the largest n that still reads at range.**

```
f_px  = (W_px / 2) / tan(HFOV / 2)
s_min = M * p * Z / (f_px * cos(theta))      Z_max = f_px * s * cos(theta) / (M * p)
p     = 5 for markers and grids (4 is the floor), 6-8 for ChArUco
```

ChArUco needs more because its sub-pixel corner window must sit well inside
one square. D435 colour at 1920 x 1080, HFOV about 69 deg (verify), gives
`f_px` about 1397. With `b = 1` and `p = 5`:

| Board | s | M | Z_max facing camera | Z_max at 45 deg |
|---|---|---|---|---|
| L2 2 x 13, 4x4 | 14 mm | 6 | 0.65 m | 0.46 m |
| L1 2 x 8, 4x4 | 19 mm | 6 | 0.88 m | 0.63 m |
| L2 as 5x5 | 14 mm | 7 | 0.56 m | 0.40 m |
| L2 as 6x6 or AprilTag 36h11 | 14 mm | 8 | 0.49 m | 0.35 m |
| one tag on a 1.5 in tube (70% rule, 26.7 mm), 4x4 | 26.7 mm | 6 | 1.24 m | 0.88 m |

Compute `Z_max` for each n at the **smallest** slot, keep the ones that
cover the needed range, and suggest the **largest** survivor (more bits,
more error margin). A "range first" toggle picks the smallest. If even 4x4
fails, name the lever and the amount: a bigger face, a closer camera or more
resolution.

**3. Family.**

| Family | M | IDs | Error margin | Use when |
|---|---|---|---|---|
| ArUco 4x4 | 6 | 50 to 1000 | lowest; OpenCV stores 1 correction bit (0 for `_1000`) (verify) | small faces, long range, clean scenes |
| ArUco 5x5 | 7 | 50 to 1000 | medium | the default for 50 to 250 IDs |
| ArUco 6x6 | 8 | 50 to 1000 | high | clutter, many IDs |
| ArUco 7x7 | 9 | 50 to 1000 | highest | rarely; modules get small |
| AprilTag 36h11 | 8 | 587 | minimum Hamming distance 11 (the "h11") | lowest false positives, ROS interop |

Do not trust this table, mine included. The add-in computes the real minimum
distance over all four rotations **among the IDs in use**, from the shipped
tables. Two IDs that are 2 bits apart should never share a robot.

**4. Border.** Keep `b = 1` (OpenCV's default and every board Zach has
printed). Use 2 only for blurry or low-contrast engraving, which costs 2
modules and needs `markerBorderBits` set to match. Never 0.

**5. Quiet zone.** At least 1 module of white, 2 on dark or textured hosts.
A single marker in a rectangle with short side `w` and quiet zone `q`
modules: `s = w * M / (M + 2q)`. Zach's 70% rule gives
`0.15 / (0.7 / 6) = 1.29` modules for 4x4, so it passes. A ChArUco marker
sits in a white square, so `marker <= checker * M / (M + 2)`: 0.75 for 4x4,
0.78 for 5x5, 0.80 for 6x6. His L1 (0.748) and L2 (0.737) pass, and the tool
should say why. Grid gaps are at least 1 module.

**6. Print density.** One module is `s / M * ppmm` print pixels; suggest
at least 8. Optionally snap `s` to multiples of `M / ppmm` (0.3 mm for 4x4
at 20 px/mm) so every module is whole pixels. Zach's 14 mm marker gives
46.67 px modules, a 0.05 mm jitter: harmless on stickers, worth fixing for
engraving. URDF textures can use about 4 px/mm, provided the aspect passes
the existing 1:1 gate. Print at 100%, then measure the **black square** with
calipers, because that is the number pose estimation multiplies by.

**7. Fitting a rectangle.** Single: rule 5. Grid: the 70% preset
(`board_for_tube`), with `k = floor((L - 2m + g) / (s + g))` markers along
the long side `L`. ChArUco: the Studio's `suggest_board_grids`, with the
rectangle as the envelope and the remaining design capacity as
`maximum_marker_count`.

**What runs without OpenCV:** all of the above, plus rendering. A 30-line
Linux script exports the bit tables once (draw each ID at 1 px per module,
read the bits back). arucogen's MIT `dict.json` cross-checks it; mind its
last-partial-byte trap, which broke two tag36h11 IDs for one user. The
tables come to well under 100 KB. Fusion's Python can then draw markers,
grids and ChArUco boards, write PNGs with `pHYs`, compute Hamming distances
and emit merged cell geometry. It cannot *detect*, so "render, detect,
compare" lives in the Linux test suite as golden tests against Studio PNGs.
That test must cover OpenCV 4.6's even-row ChArUco pattern change
(`setLegacyPattern`) (verify), because L1 is 2 x 8.

## 11. Physical realization

- **Printed sticker (the common case).** The Studio PNG at
  `pixels_per_mm`, with `pHYs`, printed at 100% on matte stock (gloss washes
  out). The S3 renderer must match the Studio pixel for pixel (golden test),
  so stickers from either machine are interchangeable. Per-instance IDs cost
  nothing here: one sheet, labelled by occurrence path.
- **Multi-material 3D print.** Black cells and white substrate as separate
  bodies (apriltag3D). A module should be at least 3 nozzle widths (about
  1.2 mm for 0.4 mm), so at least 7 mm for a 4x4 marker. Merge touching cells
  or cut the white cells instead (Onshape's QR feature found cutting much
  faster), or corner-touching cells make non-manifold solids. Per-instance
  IDs need distinct components (O2).
- **Engraving or laser inlay.** Engraving dark metal gives an *inverted*
  marker, which needs `detectInvertedMarker` (verify) and the flag in the
  detector config. Use `b = 2` and a 2-module quiet zone. An inlay cuts black
  and white parts from the same cell profiles.

| | Sticker | 3D print | Engrave / inlay |
|---|---|---|---|
| A | export-time PNG | native (bodies, socket) | native (profiles) |
| B | native (decal from Studio PNG) | none | none |
| C | native (per-instance print folder) | later: *Materialize as bodies* | later: DXF of cell profiles |
| D | native (Studio PNGs) | none | none |
| E | native (decals and print sheet) | later: *Materialize as bodies*, A's geometry code without the Custom Feature | later, as C |

## 12. Comparison matrix

Scores run 1 to 5, higher is better. For effort and risk, 5 means cheap
and safe.

| Criterion | A Marker Feature | B Studio Dock | C Ghost Slots | D Studio Remote | E Cockpit Markers |
|---|---|---|---|---|---|
| Fits the wishlist | 3: W3 strong; W2, W7 weak | 4: W2, W6 strong; W3 bolted on | 3: W4, W7 strong; no W2 | 3: W6, W7 strong; W1 weak | **5**: all seven (W7 shown by overlay, not CAD image) |
| Assembly uniqueness | 2: shared geometry, export-only override | 3: needs Studio v3 | **5**: free by construction | 4: Studio allocates per instance | **5**: scan, renumber, pins |
| Parametric behavior | **5**: live (preview API) | 2: one-shot | 4: nothing to regenerate | 1: requests go stale | 3: event-driven re-fit |
| Studio reuse | 1 | **5** | 2 | **5** | 3 |
| Cross-machine sync | 2: on demand | 3: S1 client | 4: data repo | 2: round trip per change | 4: data repo + S1 |
| Export integration | 3: new reader, clean pose | 3: join + placements | **5**: exact pose from the rectangle | 4: join already written | **5**: sidecar is the existing input |
| Effort | 1: L | 2: M-L | **4**: S-M | 3: M, half Linux | 3: M |
| Risk | 1: preview API, attributes | 2: schema change, bridge | **4**: read-only CAD | 3: friction | 3: Cockpit dependency, decal quirks |
| **Total /40** | 18 | 24 | **31** | 25 | **31** |

C and E tie for opposite reasons. C is the cheapest and safest way to get
uniqueness right but never shows Zach a marker. E shows him markers and
covers the whole wishlist, at a higher cost. Their cores are identical: an
instance table, the allocator, per-instance render at export. That is why
the recommendation joins them.

## 13. Recommendation

**Ship C's engine behind E's front door as v1, grow into full E, and keep
B and A as optional later add-ons.**

The truth is `model.markers` in the sidecar. IDs belong to instances and are
allocated design-wide with Studio-style retention and pins. Markers are
materialized per instance at export (C). Zach meets it through one *Make
Marker* command that turns a rectangle or face into a decal (E), with
conflicts as coded warnings. Why this shape:
- It keeps the project's rules: the sidecar is the single truth, the image
  is derived from the record, and Fusion writes come only from commands the
  user runs (D3). A reverses the attribute decision, and B and D put the
  truth on the other machine.
- W7 is the new ground, and only an instance table handles it in every case,
  xrefs included.
- It finishes code that already exists (the join, the frames, the
  per-occurrence expansion) and syncs over git, which is all the two
  machines share today.

**v1 scope.**
1. `bam_cad_core/markers.py` (pure stdlib): the `bam_marker/1` record and
   adapters (Studio v2, `bam_aruco_library/1`, `aruco_objects.yaml`).
2. Bit tables JSON and an S3 renderer (single, grid, ChArUco, PNG with
   `pHYs`), golden-tested against Studio PNGs.
3. An instance allocator ported from `normalize_marker_set`: retention,
   pins, a reserved list, cross-set checks.
4. The suggestion engine (section 10), including the no-reprint dictionary
   re-declaration.
5. A ChArUco-aware `Fiducial` with updated gates.
6. Export: per-instance fiducials and textures, the library join wired in,
   one `aruco_frame` adapter, and a print folder.
7. A *Make Marker* toolbar command: rectangle or face, suggestion dialog,
   key-named decal, staged sidecar write.
8. Coded warnings `W_MARKER_DUPLICATE`, `W_MARKER_RANGE`,
   `W_MARKER_CAPACITY` and `W_MARKER_UNPINNED_PRINTED`, shown in today's
   warnings tab and later in the Cockpit drawer.
9. S1: the Studio store as a git repo, *Import Studio board as slot*, and
   *Publish to Studio*.

**v2:** the Cockpit Markers lens; the `commandTerminated` auto-refit;
per-instance parent decals, if the probe allows them. **v3, if wanted:** B's
board editor docked for ChArUco editing; a one-shot *Materialize as bodies*
using apriltag3D's cluster method. Custom Features stay out until Autodesk
releases them from preview.

**First three build steps.**
1. **Measure before building (about 1 day).** In Fusion, through the file
   bridge: create a decal from code and record its default size and how
   `transform` scales it; round-trip an *asymmetric* test marker (place,
   export, detect on Linux) to pin down mirroring and rotation; try a decal
   on a child's proxy face from a Hybrid parent; log `commandTerminated` ids
   for Insert and Paste New. On Linux, export the bit tables and golden PNGs
   (L1, L2, one single, one grid) from the Studio's OpenCV.
2. **Pure core with tests (about 4 days).** `markers.py` (record, adapters,
   renderer matching the goldens, allocator, suggestions), tested offline
   with two-instance, nested four-instance, cross-set and xref fixtures.
3. **Export wiring, then the command (about 4 days).**
   `ModelConfig.markers`, the ChArUco `Fiducial`, per-instance textures in
   `resolve_decals`, the join. Then *Make Marker*, smoke-tested on the rig
   through the bridge.

**Top risks.**
1. **Mirrored decals.** `decals.py` found that Fusion's first decal column
   points image-left. A naive transform mirrors the marker, and a mirrored
   ArUco reads as nothing or as another ID. Step 1's round trip becomes a
   standing test.
2. **The CAD image is per component.** Unless parent decals work, Fusion
   shows instance #1's IDs on every copy. Everything downstream is correct,
   but the picture in Fusion is not. Zach must accept this (question 2).
3. **Format and pixel drift between the machines.** The Studio drops
   unknown keys and starts every set at ID 0, and the S3 renderer must match
   OpenCV exactly. The guards are one writer per set, the cross-set scan,
   and golden tests on Linux.

## 14. Open questions for Zach

1. **Where does marker truth live?** *Default:* the sidecar
   (`model.markers`). The Studio gets a published copy. No Fusion
   attributes.
2. **Is it OK that Fusion shows the same marker image on every copy of a
   part,** with true per-instance IDs in overlays, the Cockpit, the print
   sheet and the URDF? *Default:* yes, until a probe proves parent decals
   work.
3. **One dictionary per robot?** *Default:* yes, starting at
   `DICT_4X4_100`. The `dict3` strips are re-declared `DICT_4X4_50` (same
   patterns, no reprint).
4. **May the Studio store become a git repo** pushed to a private GitHub
   repo? *Default:* yes. Keras and the assembler see no change.
5. **Default camera for suggestions?** *Default:* D435 colour
   1920 x 1080, 0.3 to 0.8 m, worst angle 45 deg, 5 px per module (6 for
   ChArUco).
6. **Printed-in or engraved markers soon?** *Default:* no. v1 is stickers
   only.

## Appendix: unverified facts

- A decal made from a parent on a child's proxy face (Hybrid), which
  decides per-instance WYSIWYG.
- `transform` sometimes ignored on decal creation, the default decal size,
  and the mirroring of code-created decals.
- `adsk.fusionSendData` on an `http://127.0.0.1` palette page; CORS for
  localhost `fetch` (B only).
- `commandTerminated` ids for Insert into Design and Paste New.
- Custom-graphics text labels (C and E overlays).
- Decals inside xrefs being read-only from the parent.
- OpenCV's stored correction bits per ArUco dictionary.
- OpenCV 4.6's even-row ChArUco change (`setLegacyPattern`), and which
  pattern the Studio's OpenCV draws.
- `detectInvertedMarker` for engraved markers.
- D435 colour HFOV of about 69 deg.
- bam_aruco running on Windows under pixi (S2).
