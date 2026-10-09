# Proposal: auto-sizing ArUco tags on a face

Status: design proposal, no add-in code changed. Date: 2026-10-07.
Page with the drawings and every worked example: `reports/aruco/autosize-proposals.html`
(built by `docs/build_aruco_autosize_report.py`, whose functions computed every number below).

## 1. What Zach asked for (2026-10-07)

> "I want to make sure its very ergonomic that once we tag the face that a lot of the
> parameters for the size of the marker are being autocomputed. … I think the prior aruco
> studio we have could be a reference.. in terms of like spacing around etc... I think
> generally what I want to control is the marker size. and checker size (if its charuco
> board)... all the other spacing and stuff I think can then be autocalculated off those.
> it should always kinda fit within the face. markers shouldn't ever be overlapping. Lets
> have smart good defaults. I prefer rounding to nearest .5 mm for the defaults on the
> marker sizes."

Requirements: R1 tag a face, numbers appear · R2 the marker size (+ checker size) is the
control · R3 always inside the face · R4 never overlapping · R5 smart defaults, nearest
0.5 mm · R6 the Studio's spacing / his tube rule as the reference · R7 OpenCV-tutorial
ratios as defaults · R8 (implied) always valid for OpenCV, serial ids keep working.

**"Maybe you are doing it already?"** Partly, and backwards. The dialog takes
*columns × rows* and derives the marker size that fills the face (Proposal 2's model,
without rounding, margins, limits or a dictionary pick). The marker size is not an input
on a face at all.

## 2. What we do now

- `ArucoMarkers/aruco_commands.py`, ArUco Tag dialog: target (profile or planar face),
  Type, Dictionary, ID (next free), Columns (3), Rows (2), Tile thickness (profile only),
  Black thickness + Black sits (`depth_m` / `raise_m`), Quiet zone (modules), Rotate, Name;
  a live info text with the label, module, tile size, readable range, and warnings for a
  module under 1.2 mm or ids past the dictionary.
- `aruco_tags.face_frame`: x along the face's longest straight edge; W × H = extent of
  the face's **vertices** along x / y. Holes and notches are invisible; a face with no
  straight edge and no vertices (a disc) measures 0 × 0.
- `markers.fit_board`: single = `min(W,H)·n/(n+2q)`; grid = largest L with gap 0.2 L for
  the given cols × rows; ChArUco = largest square with marker 0.75 S. No rounding; the
  pattern is centred in the box.
- `TagSpec.grow_m` (cell growth, off by default), `depth_m`, `raise_m` are thickness and
  manufacturing choices: none of them moves a marker's side or the layout. A raised layer
  lifts the detector frame (`detector_frame` handles it); an inlay deeper than the body
  under the face is a hole (a check to add, not a sizing input).

Today, for the five test faces, grid 3 × 2 (the dialog default) gives markers of
10.7 / 27.6 / 26.8 / 15.0 / 20.1 mm on the 40×40 tile, 120×70 plate, 100×80 plate,
38.1×168.5 and 50.8×224.4 tube faces (computed with `markers.fit_board`). The tubes get
3 × 2 at 15 and 20 mm, not his 1 × 5 at 27 and 36: two rows must share the short side.

## 3. The geometry every proposal shares

Lengths in mm. `n` = modules per marker side (6 for 4×4), `q` = quiet zone in modules
(default 1), face box W × H with W along the longest straight edge.

```
quiet  Q = q·L/n                                   (single, grid)
gap    s = max(0.2·L, L/n)                         (grid; never under one module)
cols   = ⌊(W − 2Q + s)/(L + s)⌋   rows = ⌊(H − 2Q + s)/(L + s)⌋          (grid)
cols   = ⌊W/S⌋                    rows = ⌊H/S⌋                            (ChArUco)
ChArUco marker L ≤ S·n/(n+2)   (one module of white inside the square; = 0.75 S for 4×4,
                                the OpenCV tutorial ratio, derived rather than assumed)
extent = cols·L + (cols−1)·s | cols·S ;  margins = (W − extent_w)/2, (H − extent_h)/2
```

Invariants (checked by code, every candidate): margins ≥ Q (grid/single), margins ≥ 0
(ChArUco needs no outer quiet zone: each marker sits in its own white square), s > 0,
L < S, ids < capacity — the last four are `aruco_board.check_spec`, the gate that mirrors
what cv2 refuses. Limits: module ≥ 1.2 mm (three 0.4 mm lines); a grid has ≥ 2 markers;
ChArUco ≥ 2×2 squares (OpenCV's floor) and we advise ≥ 4 inner corners for a pose board
(2 × N strips, his L1/L2, are allowed).

Rounding: defaults → **nearest** 0.5 mm, then re-check; if rounding up broke the fit or
lost a marker, step down 0.5. Sizes derived from a count → **down** to 0.5 (they are
maxima). Typed values are used as typed.

Dictionary auto-pick: keep the family (4×4 unless chosen), pick the capacity from the
design: smallest of 50/100/250/1000 holding *ids in use + this board + headroom*, headroom
= max(2·need, need+10) as `markers.suggest` does. 5 markers, empty design → 4X4_50;
with 40 in use → 4X4_100; 13 markers with 60 in use → 4X4_250. Growing capacity never
reprints (4X4_50 ⊂ 4X4_1000).

Non-rectangular faces, honestly: the box is all we size from. Computable: `face.area` /
box area detects it (hole 0.9, L 0.75, disc 0.785; trigger < 0.98); then test the
candidate against the real face with `face.evaluator.getParameterAtPoint` +
`isParameterOnFace` on the pattern outline and every marker corner, and shrink (P1 steps
the size down, P2 drops a row/column, P3 filters) until it passes. A hole in the quiet
zone passes; under a marker it fails; a disc converges to the inscribed square. Not
computable cheaply: the best *placement* on an L-shape — we centre in the box; sketch a
rectangle (ArUco Extrude) or split the face.

## 4. Proposal 1 — Size first

**You set:** Marker size [auto]; ChArUco adds Checker size [auto]. Dictionary [auto],
First id [auto]. Advanced (folded, pre-filled): quiet zone, gap ratio, black thickness /
sits, grow cells, rotate, name.

**Computed:** cols × rows = as many as fit; gap; quiet zone; margins (leftover split,
centred); dictionary; ids; chips with the layout and readable range.

**Default size** = his tube rule generalised: single L = 0.7·short. Grid: k markers
across the short side span 70 % of it, k = 1 on a strip (long/short ≥ 3), 2 on a plate:
L = 0.7·short/(k + 0.2(k−1)). ChArUco: k squares fill the short side, k = 2 on a strip
(his L1/L2), 3 on a plate; marker = 0.75 S. Nearest 0.5 mm, checked, step down if needed.

**Can't fit:** clamp to the largest 0.5 mm step that fits (and still is a board), say so
in orange ("clamped: 30.0 mm does not fit, using 15.5 mm"). Never silent, never rejected.

**Worked examples (defaults, computed):**

| face | single | grid | ChArUco (sq / marker) |
|---|---|---|---|
| 40 × 40 tile | 28.0 | 2 × 2 @ 12.5, gap 2.5, margins 6.3/6.3 | 3 × 3 @ 13.0 / 9.5 |
| 120 × 70 plate | 49.0 | 4 × 2 @ 22.5, gap 4.5, margins 8.3/10.3, ids 0–7 | 5 × 3 @ 23.0 / 17.0, 8 corners |
| 100 × 80 plate | 56.0 | 3 × 2 @ 25.5, gap 5.1, margins 6.7/12.0 | 3 × 3 @ 26.0 / 19.5 |
| 38.1 × 168.5 tube | 26.5 | **5 × 1 @ 26.5**, gap 5.3, margins 7.4/5.8 (he built 27.1, gap 5.5) | **8 × 2 @ 19.0 / 14.0** (= his L2) |
| 50.8 × 224.4 tube | 35.5 | **5 × 1 @ 35.5**, gap 7.1, margins 9.3/7.7 (he specified 36.0) | 8 × 2 @ 25.0 / 18.5 (L1 is 25.4 / 19) |

Pros: exactly the words; the default re-creates his boards; always valid; a pure function
with tests. Cons: no direct "2 × 3"; the 2-across plate default is a heuristic (P3's list
shows the alternative). Effort ~2 days. Serials: a size edit changes the count → the id
block length; see §7.

## 5. Proposal 2 — Count first (today's model, finished)

**You set:** Columns × rows [auto from the aspect: 1 row on a strip, 2 on a plate;
ChArUco 2 / 3 rows; cols = rows·W/H]. Marker size is read-only: the largest that fills
the face, rounded **down** to 0.5 mm.

**Computed:** L (S and L), gap, quiet zone, margins, dictionary, ids. **Can't fit:**
impossible by construction; printability can fail (module < 1.2 mm orange, < 0.6 mm red,
OK refused); a 1 × 1 "grid" is renamed a single.

Worked examples: grid 2 × 2 @ 15.5 (tile), 3 × 2 @ 27.5 (120×70), 2 × 2 @ 31.5 (100×80),
**4 × 1 @ 28.5** (38.1 tube — not his 1 × 5: the quiet zone alone gives 28.5 and five do
not fit; 18.7 mm of leftover sits in the margins), 4 × 1 @ 38.0 (50.8 tube). ChArUco
3 × 3 @ 13/9.5, 5 × 3 @ 23/17, 4 × 3 @ 25/18.5 (margin 0 in x, allowed), 9 × 2 @ 18.5/13.5,
9 × 2 @ 24.5/18.

Pros: smallest change (80 % exists); direct count control; never fails to fit. Cons: the
opposite of the request (size is the output); sizes are whatever the face gives, boards on
different faces never share a size. Effort ~1 day.

## 6. Proposal 3 — Pick a layout (the Studio's assistant on a face)

**You set:** nothing, or a filter (marker size ≥, markers ≥). A **Layout** dropdown lists
every distinct cols × rows that fits, each at the largest 0.5 mm size that gives it,
biggest marker first, badged *largest marker / best fill / most markers*; the default pick
is P1's default. The port of ChArUco Studio's `suggest_board_grids` from an envelope to a
face: step the size down 0.5 mm from the largest that fits to the printable minimum and
keep the first size that yields each new count.

Worked examples (grid): 38.1 tube → 4×1 @ 28.5 (largest), **5×1 @ 27.0**, 6×1 @ 22.5,
7×1 @ 19.5, 8×1 @ 17.0, 9×2 @ 15.0 (best fill 82 %), 10×2 @ 13.5, 11×2 @ 12.5.
120×70 plate → 2×1 @ 47.0, 3×1 @ 32.0, 3×2 @ 27.5, 4×2 @ 24.0, 5×2 @ 19.5, 5×3 @ 18.5,
6×3 @ 16.0, 7×4 @ 14.0 (best fill 88 %). The full lists for all faces and ChArUco are on
the page.

Pros: no heuristic decides; trade-offs visible with numbers; same engine as P1. Cons: an
8-entry Fusion dropdown is not the Studio's web page; an exact size is a filter, not a
field, unless combined with P1. Effort ~3 days.

## 7. Per-copy serial ids when the count changes

A board's block is `marker_count` long; an override stores only the first id. Resizing a
template never edits a copy's record but can make blocks overlap. Computed with
`markers.plan_serials` / `resolve`: three copies of a 1 × 5 strip serialled 0 / 5 / 10;
grow the template to 1 × 6 → blocks 0–5, 5–10, 10–15, #5 and #10 printed twice
(`resolve().duplicates()` flags them); "Number duplicates" again → Tube:3 → 16–21,
Tube:2 → 22–27, the first copy keeps 0–5. Shrinking only leaves gaps. Rule for all three
proposals: a size/count change on a template with serialled copies re-runs the plan inside
the same command (one undo step) and the chip reports "block 5 → 6, 2 copies renumbered".

## 8. Recommendation

**Build P1, with P3's ranked list as a "Layout" dropdown under the size field.** The size
field is the control (R2); the list is how you see what else fits and get a particular
count without hunting for the size. Both run the same pure function; P2 falls out for
free (pick an entry, the size reads back). ~3 days:

1. `markers.autosize`: `layout_for_size`, `default_sizes`, `clamp_to_fit`, `candidates`,
   `auto_dictionary` in metres, returning a `TagSpec` plus margins and notes; the numbers
   on the page become the expected values of the tests.
2. Dialog: Face → Type → Marker size [auto] → Checker size [auto] → Layout (ranked) →
   Dictionary [auto] → First id [auto] → chips; Advanced folds the rest.
3. The fit test against the real face for non-rectangles, shrinking until it passes.
4. ArUco Edit gains the size field; a count change re-plans serials and says so.

### Reference implementation (mm; the add-in version divides by 1000)

```python
STEP, GAP, FILL, MIN_MODULE, STRIP = 0.5, 0.2, 0.7, 1.2, 3.0

def rnd(x, down=False):
    return (math.floor(x / STEP + 1e-9) if down else round(x / STEP)) * STEP

def layout_for_size(kind, W, H, L, S=0.0, n=6, q=1.0, force=None):
    """Sizes in -> counts, gap, margins out; ok=False with a reason if invalid."""
    Q = q * L / n
    if kind == "single":
        cols = rows = 1; sep = 0.0
        ok = L + 2 * Q <= min(W, H)
    elif kind == "grid":
        sep = max(GAP * L, L / n)
        cols = floor((W - 2 * Q + sep) / (L + sep)); rows = floor((H - 2 * Q + sep) / (L + sep))
        if force: cols, rows = force if force <= (cols, rows) else (0, 0)
        ok = cols >= 1 and rows >= 1          # a grid wants cols*rows >= 2
    else:  # charuco: marker must leave one module of white inside its square
        sep = 0.0
        cols, rows = floor(W / S), floor(H / S)
        if force: cols, rows = force if force <= (cols, rows) else (0, 0)
        ok = L <= S * n / (n + 2) and cols >= 2 and rows >= 2
    ext = (cols * L + (cols - 1) * sep, rows * L + (rows - 1) * sep) if kind != "charuco" else (cols * S, rows * S)
    margins = ((W - ext[0]) / 2, (H - ext[1]) / 2)
    ok = ok and min(margins) >= (Q if kind != "charuco" else 0) and aruco_board_check_spec_passes(...)
    notes = ["module < 1.2 mm"] if L / n < MIN_MODULE else []
    return Layout(kind, L, S, cols, rows, sep, margins, ok, notes)

def default_sizes(kind, W, H, n=6):
    """Zach's rule generalised, nearest 0.5 mm, stepped down until it fits as intended."""
    short, long_ = min(W, H), max(W, H)
    strip = long_ / short >= STRIP
    if kind == "single":  L, S, k = rnd(FILL * short), 0.0, 1
    elif kind == "grid":  k = 1 if strip else 2; L, S = rnd(FILL * short / (k + (k - 1) * GAP)), 0.0
    else:                 k = 2 if strip else 3; S = rnd(short / k); L = rnd(S * n / (n + 2))
    while L >= MIN_MODULE * n:
        lay = layout_for_size(kind, W, H, L, S, n)
        if lay.ok and (kind == "single" or min(lay.cols, lay.rows) >= k):
            return lay
        if kind == "charuco": S -= STEP; L = rnd(S * n / (n + 2), down=True)
        else:                 L -= STEP
    return Layout(ok=False, notes=["no default fits this face"])

def clamp_to_fit(kind, W, H, L, S=0.0, n=6):
    """A typed size that overhangs -> the largest 0.5 mm step at or below it that fits."""
    while L >= MIN_MODULE * n:
        lay = layout_for_size(kind, W, H, L, S, n)
        if lay.ok and (kind != "grid" or lay.cols * lay.rows >= 2):
            return lay
        if kind == "charuco": S = rnd(S - STEP, down=True); L = rnd(min(L, S * n / (n + 2)), down=True)
        else:                 L = rnd(L - STEP, down=True)

def candidates(kind, W, H, n=6):
    """Every distinct cols x rows that fits, at the largest size that gives it (P3)."""
    out, seen = [], set()
    L = rnd(min(W, H) * n / (n + 2), down=True)        # or S for charuco, L = 0.75 S
    while L >= MIN_MODULE * n:
        lay = layout_for_size(kind, W, H, L, ...)
        if lay.ok and (lay.cols, lay.rows) not in seen:
            seen.add((lay.cols, lay.rows)); out.append(lay)
        L -= STEP
    return out      # badge: out[0] largest marker; max count; max coverage
```

The executable version (with `force`, the ChArUco branch of `candidates`, the OpenCV gate
and the id/dictionary helpers) is the "algorithms" section of
`docs/build_aruco_autosize_report.py`; it is what produced every number above.

## 9. Open questions for Zach

1. Default on a plate: 2 across (this doc: 4 × 2 @ 22.5 on 120×70) or 1 across (2 × 1 @
   47, biggest markers)? Strips are unaffected.
2. ChArUco minimum: accept OpenCV's 2 × 2 (one corner), or insist on ≥ 4 inner corners on
   a plate? (2 × N strips stay allowed.)
3. Should a count change re-serial copies automatically (one undo step) or only warn
   (keeps printed stickers valid until you decide)?
4. May the fit test shrink a tag on a non-rectangular face without asking, as long as the
   chip says so?
