# ArUco IDs: acceptance gates

Status: the spec for the IDs hardening (2026-10-09). Every gate is a test
someone can run, with a pass/fail answer. A feature is "done" only when every
rung up to G9 passes, and G10 passes on Zach's real rack.

Run the whole ladder with `python tools/aruco_acceptance.py` (offline rungs
always; the Fusion rungs only inside a test window the coordinator opened).
It writes `reports/aruco/acceptance.html` + `acceptance.json`: one row per
gate, PASS / FAIL / SKIPPED (no window), with the evidence (numbers, images).

## What Zach asked for (2026-10-09)

- Not having to think about it: at any level, every marker in the design is
  unique, because a camera could otherwise see one id in two places.
- Auto-numbered by default; any tag can be overridden; clearing an override
  returns it to auto (an empty field means auto, never "Invalid expression").
- No browsing assembly levels to do it.
- Clicking a row highlights that tag in the viewport.
- Tags inside linked (external) designs count like any other.
- What the preview shows is exactly what OK builds; no stray or floating parts;
  ChArUco boards are rebuilt correctly.
- ArUco IDs is ONE timeline feature with its own icon; deleting it removes
  everything it did (per-copy parts, hidden originals, stored ids).

## The fixture matrix (every gate runs over all of it)

| # | Fixture | Why |
|---|---|---|
| F1 | one component, one tag, 1 copy | baseline |
| F2 | 3 copies in the root | plain duplicates |
| F3 | copies nested 2 and 3 levels deep, rotated and translated | composed transforms |
| F4 | a sub-assembly copied twice, each holding 2 tagged copies | the same tag at several levels |
| F5 | tags in a LINKED design (xref), placed 1 and 2 levels deep, twice | Zach's actuators |
| F6 | a linked design that itself contains a linked tagged part | xref inside xref |
| F7 | every layout: single, grid 3x1, ChArUco square, ChArUco 4x2 / 2x8 strip | the broken ChArUco |
| F8 | every mode: white + black (join / new), black only, white only, raised, recessed, pins, corner marks | variant geometry |
| F9 | every Origin: 4 turns incl. transposed rectangular boards | variant orientation |
| F10 | mixed dictionaries (4X4_50, 4X4_100, 5X5_50) | per-family uniqueness |
| F11 | hidden / suppressed occurrences | excluded from numbering, flagged |
| F12 | Zach's "One Dof Test Rack" (read-only until G10) | real data |

## The ladder

### G0 offline core (pure, no Fusion)
- G0.1 Planner: on every fixture model, every VISIBLE instance gets a marker
  no other visible instance prints (per dictionary family: 4X4_50 and
  4X4_1000 share ids; different bit sizes do not collide).
- G0.2 Board blocks are contiguous and never overlap another tag's ids.
- G0.3 Ids stay inside the dictionary; when they cannot, a clear refusal names the tag.
- G0.4 Stable: re-planning an already-unique design changes nothing; adding one
  copy renumbers only that copy; overrides are never moved by auto.
- G0.5 Overrides: set, clear-to-auto, conflicting override flagged (never silently changed).
- G0.6 Owners: an id for an instance inside a linked design is stored in the
  nearest EDITABLE ancestor, never inside the linked design.

### G1 collection (Fusion, read-only)
- G1.1 Every tag instance in the design is listed exactly once, with its path,
  including tags inside linked designs at any depth (F5, F6).
- G1.2 Hidden / suppressed occurrences are listed as excluded, not numbered.
- G1.3 Collection on the rack (F12) completes in under 10 s and lists the
  boards on the plates, the round single markers on the actuators, the
  ChArUco corner and the arm's ChArUco strip.

### G2 numbering (Fusion)
- G2.1 After OK with no overrides, no two visible instances print the same
  marker anywhere in the design (checked from the BUILT geometry, not the plan).
- G2.2 Re-running OK changes nothing (idempotent); geometry and timeline unchanged.

### G3 overrides UI
- G3.1 Typing an id overrides; deleting the text returns to auto (shows the
  auto value), never an error.
- G3.2 Overrides persist through save / close / reopen.

### G4 geometry (Fusion)
- G4.1 Every instance's printed pattern matches its resolved id (sampled cells, all fixtures).
- G4.2 Every per-copy part sits exactly on its copy: placement error < 1e-6 m and < 1e-4 deg,
  for F3-F9, including ChArUco boards and transposed origins.
- G4.3 No stray bodies, no floating parts, no orphan joints or components
  (the document's body / component / joint inventory before vs after is
  exactly the expected delta).
- G4.4 The original tag is hidden ONLY on overridden paths; everywhere else visible.

### G5 frames and export
- G5.1 Every instance's Joint Origin = its OpenCV frame (0.000 mm / 0.000 deg, `jo_report`).
- G5.2 Export (`aruco.json`, URDF via `rebuild_urdf.py`): unique ids, one target
  frame per instance, matching G5.1 (`verify_aruco_urdf.py`).
- G5.3 OpenCV PnP on rendered views of the built parts: <= 0.1 mm / 0.1 deg.

### G6 timeline feature
- G6.1 ArUco IDs creates ONE timeline node with the ArUco IDs icon.
- G6.2 Double-click opens the dialog with the current state; OK updates in place.
- G6.3 Deleting the node removes EVERY per-copy part, re-shows every hidden
  original, clears every stored id it wrote; the inventory equals the
  pre-IDs inventory.
- G6.4 Undo / redo of create, edit and delete are each one step and exact.

### G7 dialog
- G7.1 Clicking a row highlights that instance in the viewport.
- G7.2 The preview equals the built result (ghost vs build: 0.000 mm).
- G7.3 Cancel leaves no geometry, no graphics, no stored ids.
- G7.4 No level picker; the table covers the whole design.

### G8 robustness
- G8.1 Upstream edits (move a plate, resize a tagged face, change an Origin)
  then reopen IDs: everything still passes G2-G5 after OK.
- G8.2 Deep Refresh of a linked design: no crash; passes G1-G5.
- G8.3 Adding / removing a copy: only that copy's id appears / disappears.
- G8.4 No Fusion crash or hang across the whole ladder; every test document
  closed; the user's documents untouched (inventory hash before / after).

### G9 performance
- G9.1 On the rack-sized fixture (~600 bodies, ~20 tag instances): open the
  dialog < 5 s, preview update < 0.5 s, OK < 60 s.

### G10 Zach's rack (only with his OK, document saved first)
- G10.1 Dry run lists every tag (G1.3) and the plan.
- G10.2 OK: G2, G4, G5 pass on the rack with no manual fixes; the bad
  per-copy parts from the 2026-10-09 run (the wrong ChArUco, the floating board)
  are replaced or removed.
- G10.3 The tag -> id table is sent to the CAD2Robot session for the exporter / sim.
