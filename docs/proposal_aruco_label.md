# An optional label on 3D-printed ArUco tags

Zach, 2026-10-09:

> "I would like the option to optionally enable the label, then you can choose to position it on the black or white. You can choose the size, although by default it will autocompute it. Then for the label, please have options to auto-configure it, like put id, put name, etc. Key thing is I want that label to update! Particularly when we do a batch ArUco id set."

Then, after the first renders:

> "I want to see an example with the name on the black bar as well... I want the option to put the name inside the black strip. Conceptually it's one label object and then the position is a separate thing."

**Phases 1 and 1b (this branch) are the pure core and nothing else.** That is `CADExporter/bam_cad_core/aruco_label.py`: stdlib only, 244 checks in `tests/test_aruco_label.py`, renders from `tools/render_aruco_label.py`. No add-in file was touched. Phase 2 wires it into ArUco Extrude (section 5).

## 1. The rules it enforces

These come from `proposal_aruco_marks.md` (430,260 simulated photos) and `proposal_aruco_chamfer.md`.

| Where | Rule | Why |
|---|---|---|
| On the black (border row) | White text, cap ≤ 0.5 module, 0.25 module of black kept above and below, 0.25 module clear of the corner cells, one line, shrunk to fit, never taller | `black_id_white` −0.3 pts (n.s.); 0.7 module costs on crisp prints |
| ChArUco black square | Board label in the top-left black square, cap ≤ 0.22 square, ≥ 0.35 square from every ChArUco corner, clear of the cut corner | `chl_label_more` free; bright marks < 0.3 square pull a corner |
| On the white | Black text beyond the quiet zone **+ 1 module** (the study needs 0.7), never inside the 1-module ring | ink in the ring costs −11 to −40 pts |
| Room on the white | Fitted into the white around the tag (a face's loops, holes and all), or the tile grows by a label strip. Never over a hole, never off the face, 0.25 module (≥ 1 mm) from every edge, 1 mm (≥ 0.25 module) from corner marks and pins | — |
| Printing | Every stroke, counter and gap ≥ the nozzle (0.45 mm). The smallest cap is 5 nozzles (2.25 mm); below that the label is refused with a note | — |

`check_label(spec, label)` re-derives every rule from the spec alone and lists what is broken. The tests break each rule on purpose and check that it is caught.

## 2. One label: its content, then its position (`LabelSettings`)

A label is **one object**: what it says (a template, `|` between its lines), and, separately, where it goes and how big it is. Any content goes in any position the tag has (`positions(layout)`). A place that holds one line, such as a border row, joins the content's lines with a space.

| | Field | Values | Default |
|---|---|---|---|
| | `on` | bool | **off** (opt-in) |
| **Text** | `content` | `id` "#{id}", `id_name` "#{id}\|{name}", `name`, `dict_id` "{dict} #{id}", `custom` | `id` |
| | `text` | template: `{id} {ids} {name} {key} {dict} {size}`, `\|` = new line | "" |
| **Position** | `place` | `auto`, `black`, `white` | `auto` (below) |
| | `row` (black) | `auto`, `bottom`, `top`, `both` (line 1 bottom, the rest top), `square` (ChArUco) | `auto`: both rows; ChArUco → the square |
| | `scope` (black, boards) | `auto`, `each`, `first` | `auto` = each marker |
| | `side` (white) | `auto`, `below`, `above`, `left`, `right` | `auto`: the side with the most room, ties below |
| **Size** | `cap_mm` | 0 = auto, else typed | auto: the largest that fits, rounded down to 0.1 mm, ≤ 0.5 module on the black and ≤ 1.5 modules on the white |
| | `nozzle_mm` | | 0.45 |
| | `stroke_mm` | 0 = auto: max(nozzle, 0.13 cap) | auto |

**The name on the black.** `#{id}|{name}` in **both rows** puts "#12" in the bottom border row and "ACTUATOR" in the top one, as the PDF sheet does. In one row it reads "#12 ACTUATOR".
- On a board, a line that reads the same on every marker (no `{id}` in it: a name) is written once, in the first marker's top row. The ids still go in every marker's bottom row.
- `row` `auto` is both rows, so a plain "#id" still prints in the bottom row alone.

**A text too long for its place** (`name_cuts`, `text_cuts`):
1. It shrinks, down to the smallest printable cap.
2. A path name drops to its last element ("Arm:2/Actuator:3" → "Actuator:3").
3. It is cut short with "…", with a note. It keeps the occurrence number, the part that tells a batch's copies apart: "Shoulder_Pitch_Actuator:2" → "SHOULD…:2".

An id is never cut: a text whose id does not fit is refused. The longest cut that still prints wins.

**`place` `auto`** tries, in order:
1. The whole text on the black.
2. The whole text on the white.
3. Cut short on the white, where more of it shows.
4. Cut short on the black.

Other behaviour:

- On the white, one line is kept while its cap is at least 0.5 module. A longer line wraps (at spaces and after "/", up to 3 lines) when that gives a bigger cap. Written `|` breaks are kept.
- A typed cap above the rule is clamped, with a note: 0.5 module on the black, 4 modules on the white.

What it means in practice, at a 0.45 mm nozzle:

- **On the black:** a 4x4 marker needs ≥ 27 mm, a 5x5 ≥ 31.5 mm, a 6x6 ≥ 36 mm (cap 0.5 module ≥ 2.25 mm). Below that, `auto` goes to the white. With a 0.25 mm nozzle, a 20 mm 4x4 marker prints on the black.
- **On the black, "#12" on a 30 mm 4x4** prints at cap 2.5 mm with 0.45 mm strokes. "#12 ARM:2" fits one row, shrunk to 2.4 mm.
- **"#12" + "ACTUATOR" in both rows** fits whole at 30 mm. "ACTUATOR:2" shrinks to 2.3 mm.
- **"#12 Actuator:2" in one row** fits whole from 42 mm (shrunk to 2.25 mm). Below that it is cut: at 30 mm it reads "#12 AC…:2", at 40 mm "#12 ACTUAT…:2" (`label_name_on_black.png`). Both rows hold it whole.
- **The font** is a single-stroke block font (digits, A–Z, `# - : . _ / + = ( ) ? …`), drawn upper-case. Every stroke is a box (`Box`: centre, length, width, angle), so the add-in builds it with `TemporaryBRepManager.createBox`, as it builds the cells. Slanted strokes are butt-ended with a stroke-square on each end. The ink is therefore exactly the cap height.

## 3. Colour

The label never adds a body. It changes the black footprint the node already builds:

| Placement | `role` | White + black / Black only | White only (white = box − footprint) |
|---|---|---|---|
| black | `cut` | text cut **out of** the cells; the white under shows (the pocket/recess keeps text-shaped islands) | text added to the white pattern |
| white | `add` | text **joined to** the cells (inlaid like them) | a notch in the white; the black body shows |

A strip grows the white tile (White + black) or the white pattern's box (White only): `Label.white`.

## 4. How a batch IDs set updates labels

- **Resolution is pure.** `resolve_text(template, spec, marker_id, name, key)` resolves the text. `relabel(record, spec, name, key)` lays a copy's own text into the template's **slots**: the rectangles the template sized and checked, with the strip and the cut tile as built. Anything that rebuilds a copy rebuilds its label from the copy's data. A per-copy override (the IDs dialog, `plan_serials`) gives the copy's variant part its own "#id".
- **Same size across a batch.** A slot is sized for the widest id of the dictionary ("#9" is fitted as "#09") and for a component name with a one-digit occurrence number ("Actuator" as "Actuator:0"). Copies 2 to 9 of a batch therefore print at the template's cap. In both rows that is "#13" bottom with "ACTUATOR:2" top, all at one size. A longer copy text shrinks inside its slot, or is cut short, and never leaves it.
- **`{name}` follows geometry sharing** (`instance_name`):
  - The template's geometry shows on every plain copy, so it says the **component's name** ("Actuator").
  - A copy rebuilt as a variant says its occurrence path **below the level that set its id**: "Actuator:3" when the arm set it, "Arm:2/Actuator:1" when the robot did.
  - A per-occurrence name on shared geometry is impossible. A label never forces a variant by itself.
- **Tested:** with two arms × three actuators through `plan_serials`, every copy prints its own id and name at one cap, on the white and in both rows of the black (`label_batch_ids.png`, last row of `label_name_on_black.png`).

## 5. Phase 2: integration plan

**`aruco_feature.py`** (the custom feature; every label change is a compute, like the corner mark):

1. `default_settings()["label"] = LabelSettings().to_dict()`. Add `"label: "` to `INFO_NOTES`: a refused label is a dialog line, not a node warning.
2. In `evaluate`, after the mark is planned and before the pins:
   - Take `room = AL.white_room(mode, eop, has_body, tile_bigger)`. `tile_bigger` is a sketch-area tile larger than the sticker.
   - The region is the face loops in the **final** pattern frame. Re-project with `aruco_tags.face_loops_2d(face, face_origin, x_turned, y_turned)`, or use `_profile_loops_m` for a profile. Alternatively use `AL.loops_to_pattern(loops_m, pl.cx, pl.cy, pl.angle, turn)`. The tile region is `AL.rect_loop(tile_rect)`.
   - Call `ev["label"] = AL.plan_label(spec, s["label"], mode=, op=eop, name=comp.name, key=key, region=, grow=room["grow"], bounds=face loops if room["bounds"], tile=(-tw/2, -th/2, tw/2, th/2), mark=mark, pins={"shape": s["pin"], "size": n["pin_size"]} if pins apply)`.
   - Add `label.notes` to `notes`. Cache it as `_FIT_CACHE` does: the dialog re-evaluates on every drag.
3. If `label.white` is set:
   - `ev["tile"]` becomes that rect: centre `face_origin + x·cu + y·cv` on the turned axes, size from the rect.
   - Re-make a tile cut with `AL.mark_on_tile(spec, mode, op, label.white)`.
   - Compute the pins on the grown tile (`tile_mm` and `pin_frame` from it).
4. Geometry: add `label_temp_body(ev, lo_cm, hi_cm)`, the union of `_box` per `Box` in `mark_frame(ev)` (axis `x cos a + y sin a`).
   - In `_footprint`: `role == "cut"` subtracts it from the cells (padded ±`MARK_PAD_CM`); `"add"` unions it in.
   - `white_temp_body` uses the grown white rect for its box.
   - The pocket, the recess and White only all go through `_footprint`, so they follow with no new parts. `structure()` is unchanged, so a toggle never forces a rebuild.
5. `_write_record`: `fz["label"] = ev["label"].to_dict(6)`, the record a variant relabels from.

**`aruco_extrude_cmd.py`** (dialog): a collapsed **Label** group, laid out as the object is (Label → Text / Position / Size):

- **Label** (checkbox).
- **Text** (#id / #id + name / Name / Dictionary #id / Custom). **Custom text** (a string; its tooltip lists the tokens and `|`).
- **Position** (Auto / On the black / On the white), with its **Row** choice:
  - On the black: Auto / Bottom row / Top row / Both rows / Black square (ChArUco).
  - On the white: Auto / Below / Above / Left / Right.
- **Markers** (Each / First; boards on the black only).
- **Size**: **Auto size** (checkbox) + **Cap height** (mm). **Nozzle** (mm) under Advanced.

One result line per row, e.g. "bottom '#12' 2.50 mm · top 'ACTUATOR' 2.30 mm". The cut and refusal notes go under it. Only the fields that apply are visible, following the mode and layout.

**`aruco_preview.py`** (ghost):

- `cut` text: white boxes a hair proud of the cells.
- `add` text: dark boxes with the cells.
- The strip comes through the grown `ev["tile"]`. `_add_box` already takes rotated axes.

**`aruco_tags._build_variant`** (per-copy label):

1. Take `lab = AL.relabel(fz["label"], spec, name=AL.part_name(rel), key=tag.key)`, where `rel` is the slot path below the owner.
2. Pass it to `_slab` through a shared `label_cells` helper, mirroring `mark_cells`. White only uses `lab.white` for its box.
3. `_fill_box` must also refill the template's slot rects (`lab.footprint_rects()`) for `role == "add"`, so the template's old text pocket is solid again before the copy's text is cut. Text on the black lies inside the extent fill already.
4. Store `info["label"] = lab.texts()`. `_variant_current` returns `"label"` when it differs; the template's own label edits already change `sources`.
5. The IDs ghost can show `lab.texts()` beside the amber id.

**`print_sheet.py`** (optional): print the same text the 3D label resolves, in the bottom border, when the label is on the black. Separately, `markers.print_items` passes `short_part_name(inst.path)`, which drops the path's last element. That made sense when a tag was its own `Tag:1` occurrence. An ArUco Extrude tag lives in the part's own component, so the PDF's top border now names the parent ("Arm:1") for every actuator. Worth fixing together with phase 2 (`AL.part_name` keeps it).

## 6. Images

`reports/media/aruco/label/`, from `python tools/render_aruco_label.py`:

- `label_black.png`: single (bottom, top), a refused 20 mm, grid each, ChArUco square.
- `label_white.png`: strips on each side, auto falling to the white, a grid, White only, ChArUco.
- `label_region.png`: Black only on a holed disc and plates, and a sketch-area tile.
- `label_batch_ids.png`: a template and three copies relabelled from their own ids and names.
- `label_font.png`: the font at the smallest printable cap and at 1.5 modules.
- `label_name_on_black.png`: the name on the black, on a 4x4 marker at 30 and 40 mm, a 3x1 grid and a ChArUco board. Three cases each: "#id" bottom + name top; "#id name" in one row; a long name cut short. The last row is a batch of ids in both rows (a template and three copies, each with its own id and name).

## 7. Caveats

- The rules are the paper-print simulation's. A 3D print's "ink" is a colour change, with no halftone. Nobody has photographed a printed label yet.
- **Pins are kept clear in plan view**, conservatively, although they hang under the tile. A strip "above" has the grown tile's cut corner and pins level with its text. The strip grows deeper (up to 3 modules) before the text shrinks.
- **A label on the white moves out at most 3 modules** to dodge a hole or a keep-out. It slides along its side freely: an exact scan of the band it needs.
- **The font is upper-case block only.** Other characters print "?" with a note.
