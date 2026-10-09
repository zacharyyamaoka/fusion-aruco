# ArUco Tags: five ways in

Five structurally different proposals for how you *enter* the ArUco Tags add-in and move between its jobs, a comparison, one recommendation. The HTML twin of this doc (`reports/aruco/entry-points-proposals.html`) carries the mock-ups; the prose is the same.

Proposal, 2026-10-07, branch `claude/aruco-fusion-plugin-0e9bcb`, for Zach. Sister material: [how the prototype works](how-it-works.html) (what the four commands do today, with screenshots), the auto-sizing proposals (another lane, in progress; every proposal here leaves a slot for its rule), and the timeline-feature investigation (another lane, in progress; section 9 says where it would fit).

## 1. What Zach asked for

> "Please make 5 proposals for how this can be made easier and more ergonomic. 1. issue is from the outside all those icons look the same I don't know what any of them do. Also do we need so many entry points? I think 1 option would be a single entry point with more info inside."

> "Generally just try to make it so I have to do as little work as possible!"

> (later) Can the ArUco tag appear as its own feature in the timeline, so editing is a double-click?

Two complaints (the icons are identical; four entry points), one wish (as little work as possible), one question (a timeline feature). The yardstick for every proposal is the same four jobs, counted in clicks:

- **(a)** turn a rectangle or a face into a tag;
- **(b)** give the six copies of an actuator inside an arm their own ids;
- **(c)** print everything: the paper sheet and the 3D-print beds;
- **(d)** change a tag after the fact (the job the timeline question is about).

How steps are counted: a click or a keypress is 1; selecting something in the viewport or the browser is 1; typing a value is not counted (it is the same in every design). A **lookup** is hovering icons until the tooltip says which one you wanted; it is counted separately because it is the complaint. Start state: the design is open on the Solid tab and the rectangle, face or assembly is on screen.

## 2. What exists today, and why the icons look the same

The add-in puts an **ARUCO** panel on the Solid tab with four promoted buttons (`ArucoMarkers/aruco_commands.py`, `register()`): every one of them `isPromoted`, every one with the same kind of icon, a different DICT_4X4_50 marker on a white tile (`resources/bamAruco*/{16,32,64}x{16,32,64}.png`). The panel's dropdown lists the four names with the same four icons. The only explanation is the tooltip, and only on hover (`CommandDefinition.tooltip`; no `toolClipFilename` image).

| Command | What it does | How you get in | Code |
|---|---|---|---|
| ArUco Tag | a closed sketch profile → tile + tag (ArUco Extrude); a planar face → tag on that face; single, grid board or ChArUco | button, then pick the target in the dialog | `_TagCreated`, `aruco_tags.aruco_extrude` / `add_tag` |
| ArUco Edit | change dictionary, id, black thickness and where it sits; rebuild every copy | button, then pick any body of a tag part | `_EditCreated`, `aruco_tags.set_tag_spec`, `apply_variants` |
| ArUco IDs | per-copy ids at an assembly level; table of every copy; Number duplicates | button; choose the level in a dropdown | `_IdsCreated`, `markers.plan_serials`, `aruco_tags.write_override` |
| ArUco Export | true-size PDF sheet, images, record, STLs (bed auto-layout being added) | button | `_ExportCreated`, `aruco_export.export` |

Today's step counts, by the rules above:

| Job | Steps today |
|---|---|
| (a) rectangle → tag | ArUco Tag · click the rectangle · OK = **3**, plus 1 lookup |
| (b) six copies in Arm:1 get their own ids | ArUco IDs · Level ▾ · Arm:1 · Number duplicates · OK = **5**, plus 1 lookup, plus knowing that the level matters |
| (c) print everything | ArUco Export · OK = **2**; when copies share a marker the dialog says "run ArUco IDs first": Cancel · the five of (b) · ArUco Export · OK = **8** |
| (d) change a tag after the fact | ArUco Edit · click the tag · change the value · OK = **4**, plus 1 lookup |

Why the icons came out identical: a marker *is* the add-in's logo, and each icon was drawn as the logo. The fix is the same in every proposal, so it is section 3 rather than part of any of them.

What every proposal reuses unchanged, because it is already the right shape: the pure core (`markers.resolve`, `plan_serials`, `fit_board`, `next_free_id`, `summary`), the Fusion side (`aruco_tags.add_tag` / `aruco_extrude` / `set_tag_spec` / `apply_variants`, `aruco_export.export`), the rule that every model change happens inside a command's `execute` so it is one undo step, and the dev bridge's hot reload. The proposals differ in the *entry* and the *routing*, not in the work underneath.

## 3. Icons first: a family mark plus a verb

Independent of the proposal chosen, and the cheapest fix of all: keep **one** marker tile as the family mark (top-left, DICT_4X4_50 id 7, the same marker the report pages use as their brand) and put the **verb** in a Fusion-blue badge at bottom-right:

| Command | Badge | Name on the button |
|---|---|---|
| make a tag | ↑ on a base line (extrude) | Make tag |
| edit a tag | pencil | Edit tag |
| per-copy ids | # | Number copies |
| print | printer | Print |
| (B) the hub | list | ArUco Hub |
| (D) the verb button | lightning bolt | Tag it |
| (A) the single entry | the tile alone, no badge: it is the only button | ArUco |

At 16 px the badge is an 8 px solid blue disc with one white stroke in it; that still reads where a second marker never will. Dark and light theme variants go in the same resource folder with the `-dark` suffix (verify: Fusion's documented file-name suffix for the dark-theme icon; Zach runs the dark theme, so the dark set is the one that matters). Each command also gets a `toolClipFilename` image, the before/after picture Fusion shows in the expanded tooltip, so a hover explains the command without words. Rename the commands to verbs (table above); the panel label stays ARUCO so the family name is on the panel once, not in every button.

Effort S (an afternoon), no API risk, and it is "Phase 0" of every proposal below. The mock-ups all use these icons.

## 4. Proposal A: one ArUco button, a dialog that opens on what you selected

**The idea:** one promoted button, **ArUco**; its dialog has the tabs *Overview · Make · Edit · Number · Print*, and the selection at the moment you press decides which tab opens and what is already filled in: nothing selected → Overview; a sketch profile or a planar face → Make with it as the target; a body of a tag part or the part itself → Edit for that tag; any other occurrence (Arm:1) → Number at that level. **Best for:** Zach today: one thing to find, everything else inside, and it is his own suggestion made concrete.

### 4.1 Entry and screens

The toolbar shows one button. The panel's dropdown may keep the four direct commands, unpromoted, for keyboard shortcuts and muscle memory (open question 1); the single button is the only thing you see.

The dialog has five tabs (`TabCommandInput`); each tab's inputs are the inputs of today's dialog for that job, unchanged, so nothing is lost:

- **Overview** (nothing selected): the design's tag status as rows with a button each: "12 tags in 3 parts"; "6 copies print the same marker in Arm:2 → Number them"; "1 tag below 1.2 mm per module → Show"; "per-copy parts up to date". Below: **Print everything** (sheet + beds, with the counts) and a greyed **Make a tag** that says "select a rectangle or a face first".
- **Make** (profile or face selected): today's ArUco Tag inputs, the target already selected, plus a *Fitted for you* group where the auto-sizing lane's rule writes its result (marker size, module, readable distance) and its warning.
- **Edit** (tag selected): today's ArUco Edit inputs, the tag already picked.
- **Number** (an assembly occurrence selected): today's ArUco IDs table with the level preset to that occurrence's component.
- **Print** (from Overview, or the dropdown): today's ArUco Export inputs plus the bed layout.

### 4.2 The four jobs

| Job | Today | A |
|---|---|---|
| (a) rectangle → tag | ArUco Tag · click the rectangle · OK = **3** (+1 lookup) | select the rectangle · ArUco · OK = **3** |
| (b) six copies in Arm:1 | ArUco IDs · Level ▾ · Arm:1 · Number duplicates · OK = **5** (+1 lookup) | select Arm:1 in the browser · ArUco · Number duplicates · OK = **4** |
| (c) print everything | **2**; with duplicates **8** | ArUco · Print everything = **2**; with duplicates: ArUco · Number them (on the Overview) · Print everything = **3** |
| (d) change a tag | **4** (+1 lookup) | select the tag · ArUco · change · OK = **4**; with the timeline feature: double-click · change · OK = **3** |

The lookups go to zero: there is one button, and the tab names are the job names.

### 4.3 Build, reuse, risks, effort

- **Reuses:** all four `_XCreated` input lists become `build_make(inputs)`, `build_edit(...)`, `build_number(...)`, `build_print(...)` that add their inputs as children of a tab (`TabCommandInput.children`); the four `_XExecute` handlers stay and are dispatched on the active tab; `_tag_owner()` already classifies "what did you pick" for Edit and is reused for the routing.
- **New:** one command definition and one `commandCreated` that reads `ui.activeSelections`, classifies the first entity (`adsk.fusion.Profile` / `BRepFace` / `BRepBody` or `Occurrence` of a tag part via `_tag_owner` / any other `Occurrence`), activates the tab and pre-fills its selection input with `SelectionCommandInput.addSelection(entity)`. Fusion also pre-fills the *first* selection input of a command from the active selection when the filter accepts it (verify: automatic pre-selection behaviour; the explicit `addSelection` path is certain and is what the code would do anyway). The Overview rows are a `TextBoxCommandInput` per line plus a `ButtonRowCommandInput`; pressing a button switches the tab (`TabCommandInput.activate()` (verify: whether `isActive` is writable or `activate()` is the call)).
- **API risks:** one `Command` has one `execute`, so OK must do only the active tab's job and `validateInputs` must judge only that tab; the Number table (`TableCommandInput`) is expensive to build (`_level_resolution` walks the design) so it is built on first activation, not on open; only one `SelectionCommandInput` is active at a time, which is fine because only one tab is. All of this is stock `CommandInputs`: the same objects the four dialogs use now.
- **Effort:** M, two to three days including the Overview.

### 4.4 How problems surface

The Overview tab is the problem surface when nothing is selected: every row that is not green has a button that opens the tab with the fix prepared (Number with the duplicate level chosen and *Number duplicates* already pressed; Make with the too-small tag selected). Inside the tabs, today's live texts stay: the Make tab's fit text with its "module under 1.2 mm" warning, the Number table's "⚠ duplicate" column, and the Print tab, which instead of "run ArUco IDs first" offers **Number them first** as a button and then prints.

### 4.5 Where the timeline feature fits

A double-click on the tag's timeline feature opens the same command on the Edit tab with that tag chosen: one more route into the one dialog.

## 5. Proposal B: ArUco Hub, a dockable palette

**The idea:** one button toggles a docked HTML palette, like the CAD Exporter's Cockpit, that lists every tag copy in the design with its id, its source and its live status, and does the jobs from the list: a *Make tag from selection* button that lights up when a rectangle or a face is selected, *Number copies* on every assembly row, *Print* in the footer with the sheet and bed counts, a pencil per tag. The Fusion dialogs remain only for the detailed edit. **Best for:** designs with many tags (the twelve on the demo arm, a calibration rig) and for anyone who already lives in the Cockpit; a variant is a *Markers* tab inside the Cockpit itself, same code, same palette, at the price of coupling the two add-ins' releases.

### 5.1 Entry and screens

The toolbar shows one button, **ArUco Hub**, which shows or hides the palette (a `CheckBoxDefinition` so the button shows its state). The palette docks right (`Palette.dockingState = PaletteDockStateRight`) and the viewport shrinks beside it, as the Cockpit does. Inside, top to bottom:

- a header row: **Make tag from selection** (enabled only while the selection is a profile or a planar face; the palette listens to `UserInterface.activeSelectionChanged`), a filter box;
- the tag tree by level: *Design* › *Arm:1* (6 tags, **Number copies**) › *Actuator:1 · top* · `#21` · *Arm:1* · ✓, ... with a pencil (opens Edit) and a crosshair (selects the part in the viewport through `ui.activeSelections.add`);
- a footer: **Print** with "1 Letter sheet · 2 beds 220×220 mm" and the duplicate count if any;
- a bottom strip, the Cockpit's drawer idea: "⚠ 6 copies share #0 in Arm:2 · 1 tag below 1.2 mm", each line a link to the row.

### 5.2 The four jobs

| Job | Today | B (hub already open; opening it is 1 once per session) |
|---|---|---|
| (a) rectangle → tag | **3** (+1 lookup) | select the rectangle · Make tag from selection = **2** (defaults applied; the row appears with a pencil) |
| (b) six copies in Arm:1 | **5** (+1 lookup) | Arm:1 row · Number copies = **2** |
| (c) print everything | **2**; with duplicates **8** | Print = **1**; with duplicates: Number copies on the flagged row · Print = **2** |
| (d) change a tag | **4** (+1 lookup) | pencil on the row · change · OK = **3** |

### 5.3 Build, reuse, risks, effort

- **Reuses:** `aruco_tags.summary()` is already the list the hub shows (path, key, id, source, needs_variant, hidden, duplicates, warnings); the Cockpit's palette plumbing (`bam_datasheet_action.py`: `sendInfoToHTML`, `incomingFromHTML`, the echo guard for two-way selection) is the pattern to copy; all four execute handlers stay as the hub's workers.
- **New:** the palette HTML/JS (one file, no framework needed, a few hundred lines), the Python bridge, and a way for the palette to run work as an undo step: palettes cannot call `add_tag` directly without leaving the timeline a mess, so each hub action stashes a request in a module global and runs a hidden command (`CommandDefinition.execute()`), whose `execute` reads the request; commands take no arguments in the API, hence the stash. Refresh after every command (`UserInterface.commandTerminated`) and on `Application.documentActivated`.
- **API risks:** palettes die on a workspace switch and have to be re-created on `workspaceActivated`; a docked palette steals keyboard focus from the viewport at times; two UIs (palette and dialogs) must agree; HTML inside Fusion is Qt WebEngine (Chromium 134 in Fusion 2026 per the GUI work order, so modern JS is fine). This is the Cockpit's risk list, already paid once.
- **Effort:** L, one to two weeks.

### 5.4 How problems surface

The list is the problem surface: a chip per row (✓, ⚠ duplicate, ⚠ too small, ⟳ stale per-copy part, ◌ hidden template), counts in the header, the bottom strip with one line per problem, and every line selects the offending copy in the viewport when clicked. Nothing needs opening because the hub is already open.

### 5.5 Where the timeline feature fits

A double-click on the feature opens the Edit dialog; the hub's pencil opens the same dialog. The hub stays the overview, the feature becomes the fastest single edit.

## 6. Proposal C: right-click first

**The idea:** the commands live in the right-click menu of the thing you would use them on, added at display time through `UserInterface.markingMenuDisplaying`: a rectangle or a face offers *Make ArUco tag*, a tag offers *Edit tag*, *Give this copy its own id* and *Print this part*, a sub-assembly offers *Number the 6 copies under Arm:1*, the empty canvas offers *Print all ArUco tags* and the duplicate fix when there is one. The toolbar keeps one button as the discoverable fallback. **Best for:** a daily Fusion user who right-clicks for everything and never wants to look at the toolbar.

### 6.1 Entry and screens

Four menus, built from `MarkingMenuEventArgs.selectedEntities` every time Fusion shows a context menu (the controls are rebuilt on each display, so they are never stale):

- **a profile or a planar face:** *Make ArUco tag* ▸ *with defaults* (no dialog; the sizing rule decides) · *Single marker...* · *Grid board...* · *ChArUco board...*
- **a tag's body or tag part:** *Edit tag... (4X4_50 #0)* · *Give this copy its own id* · *⚠ duplicate of Arm:2 › Actuator:1: number both* · *Print this part*
- **a sub-assembly occurrence (in the viewport or the browser):** *Number the 6 copies under Arm:1* · *ArUco IDs for Arm:1...*
- **nothing (the canvas):** *Print all ArUco tags...* · *⚠ 6 copies share a marker: number them* · *ArUco overview...*

The label carries the state ("#0", "6 copies", "duplicate of Arm:2") because the handler has the selection and one `resolve_live()` in hand when it builds the menu. The radial marking menu (the gesture wheel) can also take *Make ArUco tag* in a free slot (`MarkingMenuEventArgs.radialMarkingMenu`), which turns job (a) into one flick (verify: whether an add-in can choose the slot or only append).

### 6.2 The four jobs

| Job | Today | C |
|---|---|---|
| (a) rectangle → tag | **3** (+1 lookup) | right-click the rectangle · Make ArUco tag · OK = **3** (with defaults: **2**) |
| (b) six copies in Arm:1 | **5** (+1 lookup) | right-click Arm:1 · Number the 6 copies under Arm:1 = **2** |
| (c) print everything | **2**; with duplicates **8** | right-click the canvas · Print all · OK = **3**; with duplicates: the fix item first = **4** |
| (d) change a tag | **4** (+1 lookup) | right-click the tag · Edit tag · change · OK = **4** |

### 6.3 Build, reuse, risks, effort

- **Reuses:** today's four commands unchanged as the menu's targets; `_tag_owner()` for the classification; `plan_serials` for the one-click numbering.
- **New:** one `MarkingMenuDisplayingEventHandler` that classifies `args.selectedEntities`, builds the labels, and adds `args.linearMarkingMenu.controls.addCommand(...)` / `addDropDown(...)` items; a *prefill* module global (which level, which tag, which target, "with defaults") that each command's `commandCreated` reads so the dialog opens prepared; the "with defaults" path sets `Command.isAutoExecute` so no dialog shows.
- **API risks:** `markingMenuDisplaying` fires for the viewport's context menu; for the browser's right-click on an occurrence it should fire with `Occurrence` entities (verify: the browser's context menu raising the same event). Items must be re-added on every display (they are, by design). The event handler runs on every right-click in Fusion, so it must be cheap when the selection has nothing to do with tags: classify first, `resolve_live()` only when a tag or assembly is in hand.
- **Effort:** M, two to three days.

### 6.4 How problems surface

In the labels: a tag's menu says what it is and whether it collides; the canvas menu grows a "⚠ ... number them" item only while there is something to fix. There is no other surface, which is also the limit: you see a problem only when you right-click near it, or press the fallback button.

### 6.5 Where the timeline feature fits

Right-click on the feature → *Edit Feature* is Fusion's own menu item for custom features; it opens the same Edit dialog. C becomes complete: every object a tag touches has a right-click route.

## 7. Proposal D: Tag it, one verb, no dialog, undo

**The idea:** one big button does the obvious thing to whatever is selected, immediately, with computed defaults, and a six-second toast says what it did with *Edit* and *Undo* links; a dialog appears only when something needs deciding. A rectangle or a face → a tag (size, id, dictionary and inlay from the sizing rule and the design's existing tags); a sub-assembly → its copies numbered; a tag → nothing to do, so it opens Edit. A second button, **Print all**, prints with the same attitude: duplicates are numbered first, stale parts rebuilt, and the toast says so. **Best for:** "as little work as possible" taken literally, and a good *default press* even inside A or C (the "with defaults" item).

### 7.1 Entry and screens

The toolbar shows **Tag it** and **Print all**, with a small dropdown for the detailed commands. The screens are the toast ("Tagged the rectangle: 4X4_50 #7 · 30 mm on 40 × 40 mm · readable to ~1.4 m — Edit · Undo") and the exception dialog: when the sizing rule's result crosses a limit (module under 1.2 mm for a 0.4 mm nozzle, or no free id in the dictionary) the Make dialog opens instead, with the warning and the suggestion ("4X4_50 fits at 1.25 mm per module: use it") as a button. Nothing selected → the toast says "Select a rectangle, a face or an assembly first".

### 7.2 The four jobs

| Job | Today | D |
|---|---|---|
| (a) rectangle → tag | **3** (+1 lookup) | select the rectangle · Tag it = **2** |
| (b) six copies in Arm:1 | **5** (+1 lookup) | select Arm:1 · Tag it = **2** |
| (c) print everything | **2**; with duplicates **8** | Print all = **1**; with duplicates still **1** (numbered first, said in the toast, one Undo away) |
| (d) change a tag | **4** (+1 lookup) | Edit tag from the dropdown · click the tag · change · OK = **4**; with the timeline feature: **3** |

### 7.3 Build, reuse, risks, effort

- **Reuses:** `add_tag` / `aruco_extrude` already accept every default as a keyword and `next_free_id` already picks the id; `plan_serials` + `apply_variants` are the numbering; `export` is the print. The timeline group that `add_tag` creates ("ArUco top: ...") is the undo step.
- **New:** a router in `commandCreated` (`ui.activeSelections` → profile / face / tag / occurrence / nothing), `Command.isAutoExecute = True` when the route is unambiguous, the sizing rule (the other lane's), and the toast: a small undocked palette shown for six seconds, hidden by a `CustomEvent` fired from a thread (the pattern `aruco_bridge.py` already uses for the dev bridge), with *Edit* and *Undo* calling `CommandDefinition.execute()` on Edit and on Fusion's own undo command id.
- **API risks:** the toast's lifecycle is the only new UI object; everything else is existing handlers with the dialog skipped. The behaviour depends entirely on the sizing rule being right most of the time; when it is not, every press costs a dialog anyway, which is today.
- **Effort:** S, one to two days once the sizing rule exists.

### 7.4 How problems surface

It never acts silently on a problem: a too-small tag opens the dialog with the warning and the suggestion; printing with duplicates numbers them and says so; stale per-copy parts are rebuilt before printing. Problems you did not just cause (a duplicate created by copying an arm last week) surface at print time, which is the moment they matter, with Undo if the fix is not what you wanted.

### 7.5 Where the timeline feature fits

Tag it never edits, so the feature is D's edit surface: double-click to change what the verb decided.

## 8. Proposal E: live where the thing lives

**The idea:** no ARUCO panel at all. *Make tag* sits in the **CREATE** menu beside Extrude, *Number copies* in **ASSEMBLE**, *Print* in **UTILITIES › MAKE** beside 3D Print, editing is the tag's own timeline feature, and every tag in the viewport wears a small live badge with its id and state (red when a copy shares the marker, amber when too small to print, dashed when its per-copy part is stale). **Best for:** someone who wants the add-in to feel like Fusion rather than a plug-in bolted beside SELECT, and for seeing problems without opening anything.

### 8.1 Entry and screens

The entry points are Fusion's own menus with one more line in each (`ui.allToolbarPanels.itemById("SolidCreatePanel").controls.addCommand(def)`, optionally promoted so the icon shows in the panel). The screen that is new is the viewport itself: a badge per tag (`CustomGraphicsGroup.addText` with `CustomGraphicsBillBoard`, so it faces the camera, and `CustomGraphicsViewScale`, so it stays the same pixel size at any zoom), rebuilt from `aruco_tags.summary()` after every command. The timeline shows the tag's feature with the family icon (section 9).

### 8.2 The four jobs

| Job | Today | E |
|---|---|---|
| (a) rectangle → tag | **3** (+1 lookup) | select the rectangle · Make tag (promoted in CREATE) · OK = **3** (**4** from the CREATE dropdown) |
| (b) six copies in Arm:1 | **5** (+1 lookup) | select Arm:1 · ASSEMBLE ▾ · Number copies · Number duplicates · OK = **5** (the level is preset, the clicks are not fewer) |
| (c) print everything | **2**; with duplicates **8** | UTILITIES · MAKE ▾ · Print · OK = **4**; with duplicates **5** |
| (d) change a tag | **4** (+1 lookup) | double-click the feature · change · OK = **3** (pending the feature lane) |

E does not win on clicks; it wins on *not having to know the add-in exists*: the badges tell you the state and Fusion's menus hold the verbs where Fusion users look for verbs.

### 8.3 Build, reuse, risks, effort

- **Reuses:** the four commands unchanged; `summary()` for the badge content; the browser already shows the per-copy parts as "Actuator · 4X4_50 #21" (the `_variant_name` convention), which E leans on.
- **New:** panel placement (three `addCommand` calls into existing panels; panel ids "SolidCreatePanel" is known, the ASSEMBLE and MAKE panel ids need reading from `ui.allToolbarPanels` (verify: "AssemblePanel" and "SolidMakePanel")); the badge overlay: one `CustomGraphicsGroup` on the root component, one text per tag copy at the tag's frame origin pushed out along its normal, colours by state, rebuilt on `commandTerminated` and `documentActivated`, cleared on `documentDeactivated` (custom graphics are not saved in the file, which is what we want); the custom feature (section 9, pending).
- **API risks:** custom graphics performance with many tags (fine below a hundred); badges are drawn over geometry, so a tag on the far side still shows its badge unless depth-tested (`CustomGraphicsEntity.depthPriority` (verify: the right property to put a badge behind nearer geometry)); whether a badge can be clicked to select its tag (`CustomGraphicsEntity.isSelectable` (verify: that a custom-graphics selection reaches `ui.activeSelections` usefully)); and the custom feature itself, whose feasibility is the other lane's question.
- **Effort:** M for the panels and badges (three to four days); the custom feature on top is L and pending.

### 8.4 How problems surface

The badges are the surface: red *#0* on both copies that share it, amber on the tag that is too small, a dashed grey badge on a copy whose per-copy part is stale, a hollow badge on a hidden template. You see it while modelling, before any ArUco command is opened, which is the earliest any proposal can tell you.

### 8.5 Where the timeline feature fits

It is E's edit entry, and E is the proposal that needs it most: without the feature, Edit would have to live in MODIFY as one more menu line.

## 9. The timeline feature (pending): where a double-click would fit

What Zach asked: can a tag be its own feature in the timeline, so editing is a double-click? The API that would do it is `adsk.fusion.CustomFeatures`: `CustomFeatureDefinition.create(id, name, resourceFolder)` gives the feature its own icon (the family mark) and name ("ArUco top · 4X4_50 #7"); `CustomFeatureDefinition.editCommandId` names the command Fusion runs on a double-click or on *Edit Feature* in the right-click menu; `CustomFeatures.createInput(definition, startFeature, endFeature)` wraps the features a tag already consists of (the cells base feature and the pocket combine that `add_tag` creates) into one timeline item; `customFeatureCompute` is where the feature would rebuild itself when its inputs change. Another lane is checking the hard parts: whether a `BaseFeature` can be wrapped and its body swapped (`BaseFeature.updateBody`) from inside a compute, how the dependency on the face is declared, and whether the per-copy parts (dumb copies today) can be features too.

If it works, it is an *edit* entry: a double-click, change, OK. It does not make, number or print, so every proposal above still needs its entry point; the feature completes them rather than replacing any:

| Proposal | What the double-click opens | What changes |
|---|---|---|
| A | the ArUco dialog on the Edit tab, that tag chosen | one more route in; (d) drops from 4 to 3 |
| B | the Edit dialog | the hub's pencil and the feature open the same thing |
| C | the Edit dialog, via Fusion's own *Edit Feature* item | C becomes complete without a fallback button for edit |
| D | the Edit dialog | D's only edit surface; without it D needs the dropdown |
| E | the Edit dialog | E's edit entry; E needs it most |

Until the lane reports, the mock-ups show the feature in the timeline as it would look, and the step counts mark it with an asterisk.

## 10. Comparison

| | (a) make a tag | (b) number six copies | (c) print, clean / with duplicates | (d) edit a tag | Lookups | Discoverable | Effort | API risk |
|---|---|---|---|---|---|---|---|---|
| Today | 3 | 5 | 2 / 8 | 4 | 1 per job | low: four identical icons | — | — |
| A: one button, routed dialog | 3 | 4 | 2 / 3 | 4 (3*) | 0 | high: one button; the tabs are the job names | M | low: stock command inputs |
| B: hub palette | 2 | 2 | 1 / 2 | 3 | 0 once the hub is open | high once open; medium before | L | high: palette lifecycle, two UIs |
| C: right-click first | 3 | 2 | 3 / 4 | 4 | 0 | low at first, high after; needs the fallback button | M | medium: menu event coverage |
| D: Tag it | 2 | 2 | 1 / 1 | 4 (3*) | 0 | medium: one verb, you must trust it | S (+ the sizing rule) | low |
| E: native homes + badges | 3 | 5 | 4 / 5 | 3* | 0 | high: where Fusion puts things | M (+ L*) | medium; * pending |

(*) with the timeline feature, pending.

How they differ structurally, one line each: A routes by selection into **one dialog**; B puts the state in a **persistent list** and acts from it; C puts the verbs on the **objects' menus**; D removes the dialog and keeps **one verb and undo**; E removes the add-in's own panel and spreads the verbs into **Fusion's panels**, with the state drawn **on the model**.

## 11. Recommendation

**A as the spine, C's menus on top, D's "with defaults" as the first choice inside both, the icons first, the timeline feature as the edit entry when its lane says yes.** B stays on the shelf until a design has more tags than the Overview tab can list; E's badges are an optional later layer.

Why this combination and not one of them: A answers both complaints directly and costs the least per click saved; C costs nothing in discoverability once A's button exists and takes job (b) from 4 to 2; D's no-dialog path is the "as little work as possible" wish and belongs in the menus and as the Make tab's default rather than as a separate button, because the sizing rule will not be right every time and the dialog is where the exceptions go. The timeline feature is the only thing that makes (d) shorter than today, so it is worth the lane. B and E both ask for a second UI surface (a palette, custom graphics) before the first one is good.

Phases:

1. **Icons, names, toolclips** (S, now): section 3. Fixes "they all look the same" this week, whatever else happens.
2. **One button, selection-routed tabs** (M): section 4. The four dialogs become tabs; the Overview tab is the problem surface.
3. **Right-click routes** (M): section 6, using the same prefill mechanism as the routing. The four direct commands stay unpromoted in the dropdown until Zach says otherwise.
4. **With defaults** (S once the sizing lane lands): the first item in the Make menu, and the Make tab opens with the rule's result already applied.
5. **Timeline feature** (pending the lane): the edit entry, in the same dialog.
6. **Badges** (M, optional): E's overlay, if problems still go unnoticed after 2 and 3.

What Zach sees after phase 2: one button labelled ArUco with one icon; select a rectangle, press it, OK; select an arm, press it, Number duplicates, OK; press it with nothing selected and the design's tag status is on screen with a Print button.

## 12. Open questions

1. Keep the four direct commands reachable, unpromoted, in the panel's dropdown (so keyboard shortcuts and "Repeat last command" still work), or hide them entirely behind the one button?
2. Default press: the dialog (A) or the deed (D)? That is a question of how much to trust the auto-sizing rule on day one; the "with defaults" menu item is the hedge.
3. When nothing is open, where should a problem show: on the model (E's badges), in a bottom strip like the Cockpit's drawer, or only on the Overview tab when you press the button?
