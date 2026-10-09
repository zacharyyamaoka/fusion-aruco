# ID labels and orientation marks on printed ArUco tags

Once a sticker is cut out you want to see which marker it is and which way is up. We measured where that ink can go without OpenCV noticing. **Inside the black border: nothing measurable, overall or in any print condition. In today's 1-module white ring: from almost nothing on a crisp print to about 40 % of hard detections lost once the printer spreads its halftone dots.** The suggested default prints the id knocked out white in the bottom border and a small grey triangle in the top-left corner of the quiet zone. Both are switches.

Study, 2026-10-07, branch `claude/aruco-fusion-plugin-0e9bcb`, for Zach. Experiment `tools/study_aruco_marks.py` (OpenCV 5.0.0, 430,260 simulated photos, each run through two detector settings); every number below is in `reports/media/aruco/marks/results.json`. Sister pages: [how the prototype works](how-it-works.html), [entry points](entry-points-proposals.html).

## 1. What Zach asked for

> "When I print out and then stick it's helpful to know the orientation.. also once I cut it it would be helpful to know the association between the different things."

> "I thought I could write the id perhaps in faint grey ink in the white? or something. ... the id could also go inside of the black. I guess the goal is: how do you put it in a way where it is the least disturbing as possible to the detection algorithm.. Is it best to put something in the white or the black?"

> "Regardless I am just imagining some features here that can be kinda like turned on or off; these would just apply to the generated images I guess as well to start... with the 3D prints.. I guess we don't have that extra step of having to stick it on in the correct orientation."

So, four questions:

1. **Orientation.** Mark one corner. By default, the corner where the transform is. A small triangle, or something that survives being cut.
2. **Association.** Put the id on the sticker itself, so it still says which marker it is after cutting.
3. **White or black?** Which placement disturbs detection least? Measure it, don't guess.
4. **Switches.** Each mark is an option on the PDF sheet and the PNG / SVG images. Say what makes sense for 3D-printed tags.

## 2. Short answer: in the black, not the white

All numbers come from 3000 paired simulated photos per design. The conditions are hard but plausible: 2.8 to 10 camera pixels per module, tilt up to 65°, blur, noise, JPEG, uneven light, four levels of printer ink spread, grey printed as dots or as tone, and three part colours. The plain marker is detected in 80.0% of them. "Paired" means every design is photographed under exactly the same conditions. So "−10 pts" means the design was detected in 10 % fewer of the same photos, net of the few it passed and the plain one did not.

- **The id inside the black border costs nothing measurable.** We tested white "#23" knocked out of the bottom border row, 0.5 module tall, between the corner cells. Centred in the row: `black_id_white` −0.3 pts [−0.6, +0.0], lost 15 / gained 7. Nudged 0.1 module towards the inside, to keep 0.35 module of black along the outer edge, it measures the same: `black_id_inner` −0.3 pts [−0.6, +0.0], lost 15 / gained 6. Grey or nearly invisible text there is the same: `black_id_grey` −0.1, `black_id_faint` −0.1. The only hint of a cost is on a perfectly crisp print with no ink spread, where the white holes are at their largest and sit closest to the outer edge. There the centred id loses −0.9 pts (lost 14 / gained 7, within noise) and the nudged one −1.1 (lost 14 / gained 6). Taller 0.7-module text does cost there, −3.6 (lost 36 / gained 9). Over the whole mix, `black_id_big` −0.8.
- **The id in today's 1-module white quiet zone costs a lot on average.** Grey 0.6 text, 0.5 module tall, centred under the marker: `white_q1_id` −11.4 pts [−12.7, −10.2]. Black text is worse: `white_q1_black` −23.2. Fainter text helps only a little: `white_q1_light` −9.5. Longer or taller text is worse: `white_q1_long` −22.6, `white_q1_big` −21.5, `white_q1_touch` −24.2.
- **The printer decides how bad the white is.** With little ink spread (up to 0.04 module per edge) the grey id costs −0.6 pts. When grey is printed as halftone dots, as laser and inkjet printers do, and the ink spreads 0.08–0.12 module, the dots fatten into solid black and the cost is −40.0 pts. The same spread on continuous-tone grey costs −3.1. "Faint grey" is only faint until the printer turns it into dots.
- **Why.** OpenCV first finds the marker by thresholding the image and tracing the black border's *outer* edge against the white ring. That edge has to form a clean four-sided shape. Grey ink in the ring is still dark to the threshold, even 20 % grey: the cut-off is only 7 grey levels below the local mean. Once ink spread, blur or tilt closes the 0.25-module gap between the ink and the border, the ink joins the border's outline. The outline stops being a square, and the marker is never looked at. Ink *inside* the border only makes holes in the black blob, so the outer edge stays intact. The bits are then read by majority vote over the middle of each cell, and up to 35 % of border cells may be wrong. Text that covers a fraction of two border cells is absorbed.
- **The white ring works if it is wider.** Ink needs about 0.7 module of clean white next to the border (5.3). A 1.5-module quiet zone with the id 0.75 module below the border gives `white_q15_id` +0.8 pts against today's plain 1-module sticker. The cost is a sticker one module wider each way.
- **Orientation.** A grey triangle in the top-left *corner square* of the quiet zone, about one module from the marker's corner, is free: `tri_tl` +0.2. The same triangle pointing into the corner from 0.15 module away is not: `tri_tl_near` −13.4. It also pulls the refined corner 0 off by 0.084 module at 12 px/module (5.5). An up-arrow in the top strip costs, for the same reason as text: `arrow_top` −7.9. A chamfered cut uses no ink at all: `chamfer_tl` +0.3. White dots in the corner cell cost a little detection (`dot_tl` −0.6, `dot_tl_big` −3.2), and the big one biases the refined corner.
- **Suggested default.** The id in the border plus the corner triangle: `combo_black` −0.1 pts [−0.7, +0.5]; its worst print condition is −0.8 pts (halftone, no spread). With the id nudged inward, `combo_black_inner` −0.1, the same within noise, so the simpler centred placement wins. There were zero wrong ids, and zero flipped corner orders, in any single-marker design and any trial.

## 3. How it was measured

Each candidate is a list of marks in *module* units: text, triangles, dots, and the cut outline. It goes through the same path a printed sticker takes:

1. **Print.** The design is written as a one-page vector PDF. It uses the same base-14 Helvetica, the same `g` grey operator and the same hand-written PDF as `print_sheet.py`. pdfium rasterises it at 64 px per module. A printer model is applied on top. Ink spreads by 0, 0.04, 0.08 or 0.12 module per edge. Grey is either continuous tone with mid-tone gain, or clustered halftone dots (1/16-module cells, as on a laser printer). Paper reflects 0.88 and toner 0.05. Outside the cut is the part: white paper, mid grey or dark grey.
2. **Photograph.** A pinhole camera is set to a given number of pixels per module at the marker's centre. It tilts up to 75° about a random axis, with random roll and sub-pixel position. The image is rendered 4× supersampled and box-filtered like a sensor. Then come lens blur, a lighting gradient and exposure, Gaussian noise and JPEG. The crop is embedded in a frame of at least 320 × 240 px, so OpenCV's size-relative defaults behave as they would in a real image.
3. **Detect.** `cv2.aruco.ArucoDetector` runs with **default** `DetectorParameters`, then again with `CORNER_REFINE_SUBPIX`. Each trial records: was the true id found; was any other id reported, a duplicate, or the corners in the wrong order (a wrong pose); and the corner error against the projected truth. Boards go through `cv2.aruco.CharucoDetector` with defaults.
4. **Pair.** Every design in an experiment sees the *same* list of random conditions, framed on the marker. That includes the same noise field, light field, id (eight ids of DICT_4X4_50) and print. A difference is then counted trial by trial: trials lost and gained against the design's plain baseline, with a McNemar test and a 95 % interval.

The experiments are:

- the **stress mix** above (3000 trials × 32 designs), also split by print condition;
- a **white-against-black grid** at matched contrast and four text sizes (1500 trials per point);
- a **clearance sweep** that moves ink towards an edge and a corner (2000 trials per point);
- **one-factor sweeps** of resolution (at two ink spreads), tilt, blur, noise, JPEG, light and print;
- a **clean corner-bias check**;
- **ChArUco** boards;
- a repeat of the main designs on **DICT_6X6_250** and **DICT_APRILTAG_36h11**.

Each experiment is reproducible from its seed. One stress-mix run of the default detector and SUBPIX gives the same detections, trial for trial: refinement only moves corners, so every detection rate here holds for both.

## 4. The candidates

The candidates are the ones Zach named, plus two the data suggested. One is a *label strip*: an extra module of sticker *below* the quiet zone, inside the cut, holding the id and the part. The other is a *chamfered* cut corner, which marks orientation with no ink at all.

Two vendors say something relevant, and no more. OpenMV's tag generator prints the family name and id under each tag, outside the white border, and says it is worth keeping ([OpenMV docs](https://docs.openmv.io/dev/openmvcam/tutorial/tools/ide/tag-generators.html)). Basler calls the white ring mandatory and one module wide ([Basler rc_cube docs](https://docs.baslerweb.com/stereovisard/rc_cube/en/tagdetect)). We found no guidance either way on writing inside the black border, which is why it was measured.

## 5. Results

### 5.1 The stress mix, paired

One row per design. The plain sticker on today's sheet, before cutting, loses nothing to its label under the cut line: `sheet_label` −0.1 against the same sheet without a label. A 2-module zone with the id centred (0.6-module gaps) costs about a point, `white_q2_id` −1.1. A tiny id in the bottom-right corner square costs about as much, `white_q1_corner` −1.4. The label strip is free, `strip_q1` +0.5, and so is the strip with a chamfer, `combo_strip` +0.9. Everything that leaves ink within about half a module of the border's outer edge, in the white, sits well left of zero. That covers text and arrows in the q = 1 strips and the triangle pointing at the corner; the white-zone combo `combo_white` −11.3 goes with them. Everything inside the black, in a quiet-zone corner square, or outside the quiet zone sits on or within a point of zero.

### 5.2 White or black, at the same visibility

The same "#id" text, at the same contrast a person sees, was printed in both places at four sizes. Dark grey on white was matched with light grey on black. In the white 1-module zone, the loss grows with both size and contrast. At cap 0.5 it runs from −22.3 pts (black text) to −7.4 pts (10 % grey). In the black border it is −2.1 to +0.2 pts across the whole grid; the only cell outside ±0.2 is the largest, white text (cap 0.8, −2.1). Making the white-zone ink faint is not the fix, because the threshold still sees it, and the printer darkens it. Moving it is.

The second table splits the stress mix by how the sticker was printed. Every white-zone design gets worse as the ink spreads, and much worse with halftone grey. Every black-border, corner-square and outside-the-ring design stays near zero in every cell.

### 5.3 How much clean white the border needs

On a 2-module quiet zone, an id (cap 0.5, grey 0.6) was moved towards the bottom edge, and a 0.5-module grey square towards corner 0. Along the edge the loss is −12.5 pts at 0.25 module (today's q = 1 geometry), −7.4 at 0.4 and −1.5 at 0.55. It reaches −0.1 at 0.7 and +0.1 at 1.0. At 0.1 module the edge loss is smaller (−6.3): ink touching the border is sometimes absorbed by the 3 % tolerance of OpenCV's polygon fit, while ink a little further out makes a bump the tolerance cannot absorb. Near a corner the damage is worse when close and falls off sooner: −21.3 pts at 0.1 module, −6.7 at 0.25, −0.5 at 0.4. **Rule: keep 0.7 module of clean white along an edge, and 0.5 diagonally off a corner.**

### 5.4 Resolution and the other factors one at a time

The nominal condition is 5 px/module, tilt up to 30°, a little blur and noise, and laser halftone with 0.04-module spread. There, the plain marker reaches 90 % detection at 2.18 px/module, the border id at 2.18 and the white-zone id at 2.25. At 0.08-module spread the same thresholds are 2.40, 2.40 and 4.90: the white-zone id needs about twice the resolution. At 3 px/module it is detected in 43% of photos, against 92% for the plain marker and 92% for the border id. In the stress mix the white-zone loss shows up at every resolution: spread, blur and tilt close a 0.25-module gap whatever the pixel count. The one-factor tables below the charts show where each design gives out under tilt, blur, noise, JPEG, light and print.

### 5.5 Corner accuracy

Marks change the *corners* only when they fall inside the window cornerSubPix looks at. In OpenCV 5 that window reaches 0.3 module (`relativeCornerRefinmentWinSize`), at most 5 px (`cornerRefinementWinSize`), around each corner. Far marks leave every corner bit-identical: `tri_tl`, the border id (centred or nudged) and both border combos all show 0.000. The triangle pointing into corner 0 shifts it by 0.084 module at 12 px/module (0.42 mm on a 30 mm 4x4 marker). The 0.6-module white dot in the corner cell shifts it by 0.080. Both shifts sit in exactly the band where the window reaches them: at 20 px/module the 5 px cap pulls the window in, and the dot drops out of it. With default (no refinement) corners, marks away from the outline change nothing.

### 5.6 ChArUco boards

The board is 5×4 squares (ids 0–9), marker 0.75 of the square. It shows the same pattern as single markers, with the chessboard corners as the measure. A light-grey board label with an origin triangle in the top-left *black square* is free: `ch_label_black` changes the mean corner count by 0.00 (1 / 0 trials fewer / more). Ids knocked out of each marker's border cost a little: −0.06 corners (20 / 4) centred, −0.04 (14 / 5) nudged inward. Ids in the white square under each marker cost more: −0.14 (75 / 30). Corner error is unchanged in all of them. The top-left black square is the best home for a board label. It sits at the board's frame origin, it is large, and its middle is at least two modules from any inner corner the detector refines.

### 5.7 Other dictionaries

The same positions in module units on 8-module markers (DICT_6X6_250, DICT_APRILTAG_36h11) give the same picture. The white-zone id costs −4.4 and −4.8 pts, and the border id +0.0 and −0.1.

## 6. Recommendations, ranked

*Decided by Zach on 2026-10-07 (section 11.1): the border id and the corner triangle are the defaults, the ChArUco board label goes in the top-left black square, and options 4 (label strip) and 5 (id in the white) are dropped, because every mark must stay inside the modelled tile.*

1. **Id inside the black border, on by default.** White "#id" knocked out of the bottom border row, cap height 0.5 module, centred, clear of the corner cells. Keep it at 0.5 module: taller text starts to cost on crisp prints. It shrinks to fit and stays upright, so it also says which edge is the bottom. Write the number with a prefix ("#69", not "69") so 6 and 9 cannot swap. On boards, put each marker's id in its own border. On ChArUco, put the board label in the top-left black square.
2. **Corner triangle, on by default.** A grey (0.5) right triangle with legs of 0.6 module in the *corner square* of the quiet zone, at the top-left. For a single marker that is OpenCV's corner 0, the first corner `detectMarkers` returns. For a grid or ChArUco board it is the frame origin. It is about one module from the black corner, outside the refinement window.
3. **Keep today's label under the cut line** (dictionary, id, size, part). It stays with the sheet and costs nothing.
4. **Label strip plus chamfer, opt-in.** This puts the part name on the sticker itself and marks orientation by shape, with zero ink in the quiet zone: `combo_strip` +0.9. The sticker grows one module at the bottom, and its corner is cut.
5. **Id in the white, opt-in, only with a quiet zone of 1.5 modules or more.** The text sits 0.75 module or more below the border. It is the most legible option, at the cost of a larger sticker.
6. **Avoid** anything in the quiet-zone *strips* at q = 1 (text, arrows) and anything within half a module of a corner (a triangle pointing at it, a big dot in the corner cell).

## 7. The option set

Every option is a switch on `SheetSettings`, with a per-item override on `PrintItem`. The sidecar image writer takes the same options.

| Option | Values | Default | PDF sheet | PNG / SVG images | 3D-printed tag |
|---|---|---|---|---|---|
| `id_mark` | `off`, `border`, `strip`, `quiet` | `border` | `border`: "#id" in white, centred in the bottom border row, cap 0.5 module. `strip`: an id + part line in a 1-module strip inside the cut. `quiet`: grey id in the white, only if `quiet_modules` ≥ 1.5 (else refused with a reason) | plain `<stem>.png/.svg` stay exactly OpenCV's marker; any mark on writes `<stem>_marked.png/.svg` beside them | not on the face (strokes are thinner than a nozzle); see `emboss_id` |
| `id_text` | template | `#{id}` (boards: `#{id}` per marker) | the string drawn; `{id}`, `{dict}`, `{part}` | same | same |
| `orient_mark` | `off`, `triangle`, `chamfer` | `triangle` | `triangle`: grey 0.5, legs 0.6 module, quiet-zone corner square. `chamfer`: the grey cut line takes a 45° corner (0.7-module legs), no ink | `triangle` drawn; `chamfer` gives a transparent corner in the PNG | `key_corner` instead |
| `orient_corner` | `origin`, `tl`, `tr`, `br`, `bl` | `origin` | `origin` = top-left: OpenCV corner 0 for a single marker, the frame origin for a board | same | same |
| `labels` (exists) | on / off | on | today's lines under the cut line | n/a | n/a |
| `emboss_id` | `off`, `back`, `edge` | `off` | n/a | n/a | "#id" debossed 0.4 mm on the tile's back (tile mode) or a side wall ≥ 3 mm tall; never on the face's quiet zone |
| `key_corner` | on / off | off | n/a | n/a | the tile's (and its pocket's) top-left corner chamfered, so a loose tile fits one way only |

**Default:** the id in the border plus the triangle (`combo_black`). On these numbers it is the least risky pair that still answers both of Zach's questions: which marker, and which way up.

How it lands in `print_sheet._page_content`, after a single marker's black ink. All lengths are in mm; `mod` is the module and `q` the quiet zone. The study's `Page.text` / `Page.poly` are the drawing calls, already in the same PDF dialect.

```
if s.id_mark == "border":
    text = s.id_text.format(id=item.marker_id, dict=item.dictionary, part=...)
    cap  = fit(text, cap=0.5 * mod, max_width=(n + 2 - 2.4) * mod)    # shrink, never overflow
    pg.text_grey(text, cx=marker_x + marker_mm / 2, cy=marker_y + marker_mm - mod / 2,
                 cap=cap, grey=1.0)                                  # white, knocked out of the black
if s.orient_mark == "triangle":
    x, y, a = sticker_x, sticker_y, 0.6 * mod                        # sticker's top-left corner
    pg.fill_poly([(x, y), (x + a, y), (x, y + a)], grey=0.5)         # needs q >= 0.7 mod; else skip + warn
if s.orient_mark == "chamfer":
    cut = chamfered_rect(sticker, corner="tl", leg=0.7 * mod)        # the grey cut line only, no ink
```

On a grid or ChArUco board, the border id is drawn per marker (each marker's own id), and the triangle goes at the board's top-left corner. For ChArUco, the top-left black square can carry the board label in light grey (`ch_label_black`).

## 8. 3D-printed tags

An inlaid or raised tag on the part already has its orientation and its identity: the CAD fixes both, as Zach says. What is left:

- **Reading the id off a printed part** while debugging. The face is the wrong place. A knock-out "#23" in the border has 0.3 mm strokes on a 30 mm marker, thinner than one 0.4 mm extrusion. The 0.7-module white rule would also make the tile bigger. Put the id on the *back* of a separate tile, or on a side wall, as a 0.4 mm deboss.
- **Loose tiles** (printed separately and glued into a pocket) can go in rotated by 90°. A chamfered corner on the tile and the pocket (`key_corner`) makes that impossible. Use the same top-left corner the sheet's triangle marks.
- **On the face itself**, the measured rules carry over directly. A deboss in the white quiet zone is "faint grey ink" made of shadow, so keep it 0.7 module or more from the border, or leave it off.

## 9. Caveats

- This is a **simulation**. It models print and camera physics carefully, but it is not a real printer and camera. The next step is cheap: print a sheet with `id_mark=border` and `orient_mark=triangle`, then photograph it in the existing acceptance flow (`tools/acceptance_aruco_frames.py`).
- **How much ink spreads on Zach's printer is the unknown** that decides how bad the white-zone id would be. Spread is modelled per edge in modules: 0.04 module is 0.1 mm on a 15 mm 4x4 marker and 0.2 mm on a 30 mm one. A good laser printer on a big marker sits at the low end; small markers and inkjet on plain paper sit higher. The border id does not depend on it, which is the point of recommending it.
- **One detector family:** OpenCV's `ArucoDetector` and `CharucoDetector` (defaults, and SUBPIX), which is what this pipeline uses. The AprilTag C library builds its black/white model from samples along the border. White text in the border is **untested** against it, and so is OpenCV's `useAruco3Detection`.
- **Legibility** is estimated from size. A cap height of 0.5 module is 2.5 mm (about 10 pt) on a 30 mm 4x4 marker, but 1.25 mm (5 pt) on a 15 mm one. Small knocked-out text fills in on printers that spread ink. That costs readability, not detection: the model applies the same spread to every design.
- **Corner errors** in the stress mix include ink spread, which grows every black square alike. Only the paired differences say anything about the marks.

## 10. Reproduce

```
PYTHONPATH=<cv2 + numpy + pypdfium2> python tools/study_aruco_marks.py      # ~10 min, 11 worker processes
python docs/build_aruco_marks_report.py                                    # this page
```

`--quick` runs a tenth of the trials as a smoke test, and `--media-only` redraws the figures from an existing `results.json`. The page build checks the numbers quoted in this document against `results.json`, and fails if any has drifted.

## 11. Follow-up: more text inside the black

> "what about putting the label inside the black? like right now we just have the ID.. couldn't you put more text there? or does that lower performance?"

### 11.1 Decisions this builds on

Zach, 2026-10-07, after reading sections 1–10:

- The id in the black bottom border row is always on, and is the default.
- The corner triangle is on by default.
- On a ChArUco board, the board label goes in the top-left black square.
- The id in the white (option 5) and the label strip (option 4) are dropped. The strip makes the sticker bigger than the modelled tile, and every annotation must stay inside the existing bounds.

### 11.2 What was measured

This is the same harness as sections 3–5. The 3000 hard conditions are the same ones as the main stress mix, photo for photo, so today's "#id" scores −0.3 here exactly as in section 2. Every design is compared with the plain marker and with today's "#id" default, on DICT_4X4_50 and DICT_6X6_250. All text is white, knocked out of the black. The test label is "Arm:2/Act3". The designs are:

- **(a)** id + label on one line in the bottom row. `lb_long_inner` keeps the corner cells clear, so the 14 characters shrink to cap 0.38 on 4x4. `lb_long_full` keeps cap 0.5 and uses the whole row, corner cells included. `lb_fill_full` fills the whole row with 20 characters.
- **(b)** `lb_id_top`: id in the bottom row, label in the top row.
- **(c)** `lb_id_sides`: id in the bottom row, the label split over the left and right columns, rotated 90°.
- **(d)** `lb_four`: all four rows.
- **(e)** two lines in the bottom row. Two lines of cap 0.5 cannot fit: 1.0 module of cap height alone fills the 1-module row, with nothing left between the lines or along the edges. So the test is two lines of cap 0.4 (`lb_two_04`, 0.05 module of black left along each edge, 0.1 between the lines) and of cap 0.3 (`lb_two_03`).
- On ChArUco: a three-line label in the top-left black square (`chl_label_more`), every marker's id plus the label in its own border (`chl_ids_label`), and both together (`chl_all`).

### 11.3 Results: single markers

More text in the black costs a little, roughly in proportion to how much of the border it covers. The cost lands almost entirely on crisp prints with no ink spread. On DICT_4X4_50, against the plain marker:

- **One longer line at the 0.5 cap:** `lb_long_full` −0.4 pts [−0.8, +0.0].
- **The same line shrunk to fit the clear row:** `lb_long_inner` +0.0, free, because smaller text leaves more black.
- **Filling the whole row, corner cells included:** `lb_fill_full` −0.2 for detection, but it is the only design here that moves a refined corner (0.009 module at 12 px/module). Ink in the corner cells reaches the corner window.
- **One more row:** top `lb_id_top` −0.5 [−0.9, −0.1]; sides `lb_id_sides` −0.5. Against today's "#id" that is −0.2 and −0.2, within noise. On crisp prints the top row costs −1.8 (lost 26 / gained 13).
- **All four rows:** `lb_four` −0.9 [−1.4, −0.4], and −3.2 (lost 37 / gained 13) on crisp prints. This is the first design that is clearly worse than today's "#id": −0.6 [−1.1, −0.2].
- **Two lines:** cap 0.4 `lb_two_04` −0.4, about the same as one longer line; cap 0.3 `lb_two_03` −0.1, free.

On DICT_6X6_250 nothing is measurable. Every design is within ±0.2 pts: four rows −0.2, top row +0.0, two lines of 0.4 −0.2. The 6x6 border has 28 cells against the 4x4's 20, so the same text covers a smaller share of it. There were no wrong ids and no flipped corner orders in any design.

### 11.4 Results: ChArUco

The three-line label in the top-left black square is free: `chl_label_more` changes the mean corner count by 0.00 (1 / 2 trials fewer / more). Writing in every marker's border is what costs. Each marker's id alone, `ch_ids_black`, gives −0.03 corners [−0.04, −0.01] (68 / 44). The id plus the label in every marker, `chl_ids_label`, gives −0.05 [−0.07, −0.03] (105 / 64). Those are small: 0.3 % and 0.6 % of the corners. But the board label already says everything, so per-marker text buys little on a board.

### 11.5 How much text fits: the real limit

Detection allows about one extra row of text on 4x4, and all four rows on 6x6. What limits "more text" is legibility. At the largest cap the tests support (0.5 module), one row holds 11 characters on any 4x4 marker with the corner cells clear, or 17 using the whole row. On any 6x6 marker it holds 17 or 23. The count does not depend on size, because text and module scale together. Shrinking the text towards the smallest readable cap fits more on bigger markers. At cap 1.0 mm (about 4 pt) a 4x4 row holds 18 characters at 20 mm, 27 at 30 mm, 36 at 40 mm and 55 at 60 mm (corner cells clear). The table has 0.7 mm, 6x6 and whole-row counts. Two lines of cap 0.3 are 1.0 mm tall on a 20 mm 4x4 marker, right at the legibility floor.

### 11.6 Recommendation for the default label content

**"#id" in the bottom row, plus a short part name in the top row, both at cap 0.5, shrinking to fit the clear span (corner cells kept clear).** For example "#23" at the bottom and "Arm:2" at the top. Both read upright from the same side, so the text also confirms which way is up, together with the triangle.

- **Cost:** −0.5 pts against the plain 4x4 marker, −0.2 against today's "#id" (within noise); +0.0 on 6x6.
- **Space:** the top row holds 11 characters at full size on 4x4 and 17 on 6x6. A longer name shrinks, down to a floor of cap 1.0 mm, and below that it is cut with "…". The full name stays on the label under the cut line.
- **Not by default:** the left and right columns cost the same as the top row but read sideways. All four rows are the first measurable step down on 4x4: −0.9 overall, and −3.2 (lost 37 / gained 13) on crisp prints. Use them only on 6x6 and larger dictionaries, where they measure free. Never run text into the corner cells. Two lines of cap 0.3 are a fallback that costs nothing, but they become unreadable below about 20 mm.
- **ChArUco:** the multi-line board label in the top-left black square. Per-marker text is off by default; each marker's id is an option, at 0.3 % of the corners.
