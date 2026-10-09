"""Generate the ArUco add-in's toolbar / timeline icons (and a preview sheet).

    python tools/make_aruco_icons.py            # write every icon + the preview
    python tools/make_aruco_icons.py --out DIR  # write the icon folders under DIR instead
    python tools/make_aruco_icons.py --fusion-extrude DIR   # Fusion's solid/extrude folder

Needs Pillow and numpy (e.g. the clank-viser venv), and, for bamArucoExtrude,
an installed Fusion (its own Extrude icon is the base, see below).

WHAT IT DRAWS (docs/proposal_aruco_entry_points.md section 3: a family mark
plus a verb, so the buttons stop looking all the same from the outside):

* bamArucoExtrude -- the main entry point, and the timeline-node icon of the
  custom feature. NOT drawn: each file is Fusion's own Extrude PNG of the same
  name (Fusion/UI/FusionUI/Resources/solid/extrude in the newest webdeploy
  build, found by searching if that path moves), with only the box's TOP FACE
  replaced by an ArUco marker. Box sides, edges, sketch slab and arrow stay
  Fusion's pixels; the script refuses to write a file that differs from
  Fusion's outside the top face. See "ArUco Extrude" below for the face.
* bamArucoIds       -- marker tile + a blue badge with "#": per-copy numbers.
* bamArucoExport    -- a print sheet with a marker on it + Fusion's blue export
  arrow: the PDF / files export.
* bamArucoSendPrusa -- Fusion's 3D Print glyph (printer, orange nozzle) with a
  marker on its bed: send to PrusaSlicer / the printer.
* bamArucoEdit      -- marker tile + a pencil: edit an existing tag.

The flat marker on the secondary icons is DICT_4X4_50 id 7 (the family mark
the report pages use) at 32 and 64 px. At 16 px a 4x4 code is mush, so the
16 px icons use a 3x3 pattern with 2 px cells: it still reads as "a black
square with a white code in it".

FILES per folder (Fusion's resource-folder convention, the same names Fusion's
own bundled add-ins use, e.g. API/InternalAddins/*/Resources):
    16x16.png 16x16@2x.png 32x32.png 32x32@2x.png 64x64.png       (light theme)
    16x16-dark.png 16x16-dark@2x.png 32x32-dark.png 32x32-dark@2x.png 64x64-dark.png
As in Fusion's own folders, NxN@2x is the 2N design (16x16@2x == 32x32).

HOW (the drawn icons): every shape is rasterised by point-sampling an 8x8 grid
per output pixel and box-filtering (exact area coverage, no LANCZOS ringing).
Anything meant to be crisp (the marker cells, the page, the badge) sits on
whole pixels, so it comes out hard-edged; only true diagonals (the arrow head,
the pencil, the nozzle) get anti-aliased edges, as in Fusion's own icons.

The preview sheet shows every icon at 1x on Fusion's light and dark toolbar
greys, then, per size and theme, Fusion's Extrude / ArUco Extrude / their
diff mask at 8x (4x for 64 px).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ROOT / "ArucoMarkers" / "resources"
PREVIEW = ROOT / "reports" / "media" / "aruco" / "icons" / "aruco_icons_preview.png"

# Fusion's own Extrude icon: the base of every bamArucoExtrude file. First the
# known spot in each webdeploy build (newest build wins), then a search for
# any .../solid/extrude/ folder holding the icon set under these roots.
WEBDEPLOY = Path.home() / "AppData/Local/Autodesk/webdeploy/production"
FUSION_EXTRUDE_REL = "Fusion/UI/FusionUI/Resources/solid/extrude"
FUSION_SEARCH_ROOTS = [
    Path.home() / "AppData/Local/Autodesk",
    Path.home() / "Library/Application Support/Autodesk",       # macOS
    Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Autodesk",
]
# Fusion's set, and ours: NxN@2x is the 2N design (16x16@2x == 32x32).
EXTRUDE_FILES = [f"{stem}{suffix}{x2}.png"
                 for suffix in ("", "-dark")
                 for stem, x2 in (("16x16", ""), ("16x16", "@2x"), ("32x32", ""),
                                  ("32x32", "@2x"), ("64x64", ""))]

SS = 8  # samples per output pixel, per axis

# DICT_4X4_50 id 7 including its 1-module black border; 1 = black, row 0 = top.
# Same as bam_cad_core.aruco_dict.marker_grid("DICT_4X4_50", 7).
ID7 = [
    [1, 1, 1, 1, 1, 1],
    [1, 0, 0, 1, 1, 1],
    [1, 1, 0, 1, 1, 1],
    [1, 0, 0, 0, 0, 1],
    [1, 1, 1, 0, 1, 1],
    [1, 1, 1, 1, 1, 1],
]
# The ArUco Extrude top face: DICT_4X4_50 id 23, same convention (=
# marker_grid("DICT_4X4_50", 23)). Not id 7: squashed 3:1 onto the face, id 7's
# full white row and its stem read as a glyph ("I"-beam); id 23's cells stay
# scattered blocks of different sizes, which reads as a code.
ID23 = [
    [1, 1, 1, 1, 1, 1],
    [1, 0, 0, 1, 0, 1],
    [1, 0, 0, 1, 0, 1],
    [1, 0, 1, 1, 1, 1],
    [1, 1, 1, 0, 1, 1],
    [1, 1, 1, 1, 1, 1],
]
# The 16 px face is 8 px x 3 rows: black back row, this code row, black front
# row. 1 = black; the ends are the marker's side borders.
FACE_CODE16 = [1, 0, 0, 1, 0, 1, 0, 1]
# The 16 px stand-in: border + a 3x3 code. Scattered white cells, none in a
# run: runs read as a letter (an "S", a "7") at 16 px, scattered cells read as
# a code.
CODE3 = [
    [1, 1, 1, 1, 1],
    [1, 0, 1, 0, 1],
    [1, 1, 0, 1, 1],
    [1, 0, 1, 1, 1],
    [1, 1, 1, 1, 1],
]


def hexc(s: str):
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


# The accent colours of Fusion's Export DXF (blue) and 3D Print (orange) icons
# and its #666 outline grey.
PALETTES = {
    "light": {
        "black": hexc("141414"), "white": hexc("FFFFFF"),
        "accent": hexc("178FE6"), "accent_dark": hexc("1480CE"),
        "paper": hexc("F3F3F3"), "paper_edge": hexc("666666"), "fold": hexc("D9D9D9"),
        "line": hexc("B2B2B2"),
        "orange": hexc("E67E35"), "orange_dark": hexc("B85F22"),
        "metal": hexc("666666"), "metal_hi": hexc("9C9C9C"),
        "pencil": hexc("F5B82E"), "pencil_dark": hexc("D08F0E"),
        "wood": hexc("F6DDB4"), "lead": hexc("333333"),
        "ferrule": hexc("9C9C9C"), "eraser": hexc("E9878A"),
    },
    "dark": {
        "black": hexc("141414"), "white": hexc("FFFFFF"),
        "accent": hexc("2C9BEB"), "accent_dark": hexc("178FE6"),
        "paper": hexc("F3F3F3"), "paper_edge": hexc("8C8C8C"), "fold": hexc("D9D9D9"),
        "line": hexc("B2B2B2"),
        "orange": hexc("EE8A43"), "orange_dark": hexc("C46A2B"),
        "metal": hexc("BBBBBB"), "metal_hi": hexc("E0E0E0"),
        "pencil": hexc("F5B82E"), "pencil_dark": hexc("D08F0E"),
        "wood": hexc("F6DDB4"), "lead": hexc("333333"),
        "ferrule": hexc("BBBBBB"), "eraser": hexc("E9878A"),
    },
}


# --------------------------------------------------------------------------
# a tiny exact-coverage rasteriser
# --------------------------------------------------------------------------
class Canvas:
    """n x n output pixels, sampled SS x SS per pixel; coordinates are in
    OUTPUT pixels (x right, y down, (0, 0) = top-left corner of pixel 0)."""

    def __init__(self, n: int):
        self.n = n
        N = n * SS
        c = (np.arange(N) + 0.5) / SS
        self.X, self.Y = np.meshgrid(c, c)
        self.rgb = np.zeros((N, N, 3))   # premultiplied
        self.a = np.zeros((N, N))

    # -- masks --------------------------------------------------------------
    def rect(self, x0, y0, x1, y1):
        X, Y = self.X, self.Y
        return (X >= x0) & (X < x1) & (Y >= y0) & (Y < y1)

    def poly(self, pts):
        """Convex polygon, either winding."""
        X, Y = self.X, self.Y
        area = sum(pts[i][0] * pts[(i + 1) % len(pts)][1]
                   - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))
        sgn = 1.0 if area > 0 else -1.0
        m = np.ones_like(X, dtype=bool)
        for i in range(len(pts)):
            (ax, ay), (bx, by) = pts[i], pts[(i + 1) % len(pts)]
            m &= sgn * ((bx - ax) * (Y - ay) - (by - ay) * (X - ax)) >= 0
        return m

    def rrect(self, x0, y0, x1, y1, r):
        X, Y = self.X, self.Y
        dx = np.maximum(np.maximum(x0 + r - X, X - (x1 - r)), 0)
        dy = np.maximum(np.maximum(y0 + r - Y, Y - (y1 - r)), 0)
        return self.rect(x0, y0, x1, y1) & (dx * dx + dy * dy <= r * r)

    def dilate(self, mask, r):
        """Disc dilation by r OUTPUT pixels."""
        k = int(round(r * SS))
        out = mask.copy()
        N = mask.shape[0]
        for dy in range(-k, k + 1):
            w = int((k * k - dy * dy) ** 0.5)
            ys = slice(max(dy, 0), N + min(dy, 0))
            yd = slice(max(-dy, 0), N + min(-dy, 0))
            row = np.zeros_like(mask)
            row[yd] = mask[ys]
            # horizontal run of half-width w: running OR via cumulative sum
            cs = np.cumsum(row, axis=1, dtype=np.int32)
            cs = np.pad(cs, ((0, 0), (1, 0)))
            lo = np.clip(np.arange(N) - w, 0, N)
            hi = np.clip(np.arange(N) + w + 1, 0, N)
            out |= (cs[:, hi] - cs[:, lo]) > 0
        return out

    # -- paint --------------------------------------------------------------
    def fill(self, mask, color, alpha=1.0):
        a = mask * alpha
        c = np.array(color[:3], dtype=float) / 255.0
        self.rgb = self.rgb * (1 - a[..., None]) + c * a[..., None]
        self.a = self.a * (1 - a) + a

    def erase(self, mask):
        keep = ~mask
        self.rgb *= keep[..., None]
        self.a *= keep

    def image(self) -> Image.Image:
        n = self.n
        a = self.a.reshape(n, SS, n, SS).mean(axis=(1, 3))
        rgb = self.rgb.reshape(n, SS, n, SS, 3).mean(axis=(1, 3))
        with np.errstate(invalid="ignore", divide="ignore"):
            col = np.where(a[..., None] > 0, rgb / a[..., None], 0)
        out = np.dstack([np.clip(col, 0, 1), a]) * 255
        return Image.fromarray(np.round(out).astype(np.uint8), "RGBA")


# --------------------------------------------------------------------------
# shared pieces
# --------------------------------------------------------------------------
def marker_spec(n: int):
    """(grid, column widths, row heights) of the FLAT marker's black square
    at output size n; the cells are whole pixels."""
    if n <= 16:
        return CODE3, [1, 2, 2, 2, 1], [1, 2, 2, 2, 1]
    m = 3 * (n // 32)                        # 3 px / module at 32, 6 at 64
    return ID7, [m] * 6, [m] * 6


def draw_tile(cv: Canvas, pal, x0, y0, n, ring=1):
    """The family mark: a flat marker with a white quiet-zone ring. Returns
    the tile's outer size in pixels."""
    grid, cw, rh = marker_spec(n)
    side = sum(cw)
    cv.fill(cv.rect(x0, y0, x0 + side + 2 * ring, y0 + side + 2 * ring), pal["white"])
    y = y0 + ring
    for r, h in enumerate(rh):
        x = x0 + ring
        for c, w in enumerate(cw):
            if grid[r][c]:
                cv.fill(cv.rect(x, y, x + w, y + h), pal["black"])
            x += w
        y += h
    return side + 2 * ring


def overlay(cv: Canvas, glyph: Canvas, gap: float):
    """Put `glyph` over `cv`, first clearing a `gap` px moat around it so the
    two shapes stay separate at 16 px."""
    sil = glyph.a > 0.5
    cv.erase(cv.dilate(sil, gap) & ~sil)
    cv.rgb = cv.rgb * (1 - glyph.a[..., None]) + glyph.rgb
    cv.a = cv.a * (1 - glyph.a) + glyph.a


def gap_for(n: int) -> float:
    return {16: 1.0, 32: 1.5, 64: 3.0}[n]


# --------------------------------------------------------------------------
# ArUco Extrude: Fusion's Extrude icon with an ArUco marker as the top face
# --------------------------------------------------------------------------
# Fusion's Extrude box is a 45 degree oblique projection (Fusion's
# 32x32-light_gray.svg: top face (7,0) (1,6) (19,6) (25,0)). Per icon size:
# the face's back-left corner x0, its width W and depth D (rows); the face is
# the parallelogram (x0,0) (x0-D,D) (x0-D+W,D) (x0+W,0). W = 3 D, so with the
# receding axis at half scale (cabinet) the face is (nearly) a square: the
# square marker is mapped onto the whole face, back row = the marker's top
# row, 6 modules of W/6 px across and D/6 rows deep. At 32 px that is 1 row
# per module, so the marker needs all 6 rows of the face: in the light theme
# the face's own 1 px outline (Fusion's 10 % darker back row and left
# staircase, inside the face) lies under the marker's black border.
FACE = {16: (3, 9, 3), 32: (7, 18, 6), 64: (14, 36, 12)}
MARKER_BLACK = (0, 0, 0)        # pure black: the face's back edge touches the
MARKER_WHITE = (255, 255, 255)  # toolbar, and #2b2b2b is dark already


def face_mask(n: int) -> np.ndarray:
    """The top face: pixels whose CENTRE is strictly inside the face. The
    pixels its edges pass through (the half-covered staircase on the left, the
    top/side seam on the right) are Fusion's edge lines and stay Fusion's.
    Row y covers x = x0-y .. x0+W-2-y."""
    x0, W, D = FACE[n]
    m = np.zeros((n, n), bool)
    for y in range(D):
        m[y, x0 - y:x0 + W - 1 - y] = True
    return m


def face_rows(n: int):
    """[(y, [(x, w, black)])]: the marker cells of each pixel row of the face,
    whole pixels. Row y is shifted 1 px left per row (the receding axis)."""
    x0, W, D = FACE[n]
    rows = []
    if n == 16:
        # 3 rows: black back border, one code row, black front border
        for y in range(D):
            pat = FACE_CODE16 if y == 1 else [1] * (W - 1)
            rows.append((y, [(x0 - y + i, 1, v) for i, v in enumerate(pat)]))
        return rows
    u = n // 32
    # The face is W px wide but its edges cut the outer pixels in half, so the
    # face's own pixels are W-1 wide: 6 modules of 3u px, the half pixel off
    # the right border. Module edges stay on whole pixels (crisp), and the
    # code sits centred on the W px the face shows (its own W-1 + the seam).
    cw = [3 * u] * 5 + [W - 1 - 15 * u]
    rh = D // 6
    for y in range(D):
        r, x, segs = y // rh, x0 - y, []
        for c, w in enumerate(cw):
            segs.append((x, w, ID23[r][c]))
            x += w
        rows.append((y, segs))
    return rows


def find_fusion_extrude(hint: Path | None = None) -> Path | None:
    """Fusion's solid/extrude icon folder: `hint` if given, else the newest
    webdeploy build's, else whatever a search of FUSION_SEARCH_ROOTS finds
    (newest first). A folder only counts if it holds the whole icon set."""
    def complete(d: Path) -> bool:
        return all((d / f).is_file() for f in EXTRUDE_FILES)

    if hint is not None:
        return hint if complete(hint) else None
    found = []
    if WEBDEPLOY.is_dir():
        found = [p / FUSION_EXTRUDE_REL for p in WEBDEPLOY.iterdir()
                 if complete(p / FUSION_EXTRUDE_REL)]
    if not found:
        for root in FUSION_SEARCH_ROOTS:
            if not root.is_dir():
                continue
            for dp, _dirs, files in os.walk(root):
                d = Path(dp)
                if (d.name.lower() == "extrude" and d.parent.name.lower() == "solid"
                        and "32x32.png" in files and complete(d)):
                    found.append(d)
    if not found:
        return None
    return max(found, key=lambda d: (d / "32x32.png").stat().st_mtime)


def check_fusion_face(img: np.ndarray, n: int, name: str):
    """Refuse an icon whose box is not where FACE says (a redesigned Fusion
    icon): the face must be opaque and the staircase left of it translucent."""
    x0, W, D = FACE[n]
    m = face_mask(n)
    ok = bool((img[..., 3][m] == 255).all())
    for y in range(D):
        ok &= 0 < img[y, x0 - y - 1, 3] < 255
    if not ok:
        raise SystemExit(f"error: Fusion's {name} no longer has its top face at {FACE[n]} "
                         f"(x0, W, D); update FACE in {Path(__file__).name}")


def extrude_icon(fusion_png: Path):
    """(Fusion's image, ours, face mask) for one of Fusion's Extrude PNGs."""
    fus = np.array(Image.open(fusion_png).convert("RGBA"))
    n = fus.shape[0]
    if fus.shape[:2] != (n, n) or n not in FACE:
        raise SystemExit(f"error: unexpected size {fus.shape[:2]} of {fusion_png}")
    check_fusion_face(fus, n, fusion_png.name)
    out = fus.copy()
    for y, segs in face_rows(n):
        for x, w, black in segs:
            out[y, x:x + w, :3] = MARKER_BLACK if black else MARKER_WHITE
            out[y, x:x + w, 3] = 255
    return fus, out, face_mask(n)


def diff_counts(fus: np.ndarray, ours: np.ndarray, mask: np.ndarray):
    """(pixels differing OUTSIDE the top face, pixels differing inside it)."""
    d = np.any(fus != ours, axis=2)
    return int((d & ~mask).sum()), int((d & mask).sum())


def write_extrude_folder(folder: Path, fusion_dir: Path):
    """Write every bamArucoExtrude file from Fusion's file of the same name.
    Returns ({theme: {n: Image}}, {file: (fusion, ours, mask, outside, inside)})."""
    folder.mkdir(parents=True, exist_ok=True)
    sets, stats = {"light": {}, "dark": {}}, {}
    for fn in EXTRUDE_FILES:
        fus, ours, mask = extrude_icon(fusion_dir / fn)
        outside, inside = diff_counts(fus, ours, mask)
        if outside:
            raise SystemExit(f"error: {fn} differs from Fusion's outside the top face "
                             f"({outside} px); not written")
        img = Image.fromarray(ours, "RGBA")
        img.save(folder / fn)
        stats[fn] = (fus, ours, mask, outside, inside)
        if "@2x" not in fn:
            sets["dark" if "-dark" in fn else "light"][ours.shape[0]] = img
    return sets, stats


# --------------------------------------------------------------------------
# secondary commands: tile + verb
# --------------------------------------------------------------------------
def draw_ids(cv: Canvas, pal, n: int):
    u = n // 16
    draw_tile(cv, pal, 0, 0, n)
    g = Canvas(n)
    # blue badge, bottom-right
    bx0, by0 = 7 * u, 7 * u
    g.fill(g.rrect(bx0, by0, n, n, 1.5 * u), pal["accent"])
    # "#": two slanted verticals, two horizontals
    if n <= 16:
        for x in (9, 13):
            g.fill(g.rect(x, 8, x + 1, 15), pal["white"])
        for y in (10, 13):
            g.fill(g.rect(8, y, 15, y + 1), pal["white"])
    else:
        s = 1.0 * u                              # stroke width
        cxm, cym = (bx0 + n) / 2, (by0 + n) / 2
        half = 3.4 * u                           # half extent of the glyph
        off = 1.6 * u                            # stroke offset from centre
        slant = 0.6 * u
        for dx in (-off, off):
            xc = cxm + dx
            g.fill(g.poly([(xc - s / 2 + slant, cym - half), (xc + s / 2 + slant, cym - half),
                           (xc + s / 2 - slant, cym + half), (xc - s / 2 - slant, cym + half)]),
                   pal["white"])
        for dy in (-off, off):
            yc = round(cym + dy - s / 2)
            g.fill(g.rect(cxm - half, yc, cxm + half, yc + s), pal["white"])
    overlay(cv, g, gap_for(n))


def draw_export(cv: Canvas, pal, n: int):
    u = n // 16
    # the sheet, with a dog-ear, and a marker printed on it
    px0, py0, px1, py1 = 0, 0, 12 * u, 16 * u
    ear = 3 * u
    e = 1 if n <= 32 else 2                      # outline width
    cv.fill(cv.poly([(px0, py0), (px1 - ear, py0), (px1, py0 + ear), (px1, py1), (px0, py1)]),
            pal["paper_edge"])
    cv.fill(cv.poly([(px0 + e, py0 + e), (px1 - ear - e * 0.41, py0 + e),
                     (px1 - e, py0 + ear + e * 0.41), (px1 - e, py1 - e), (px0 + e, py1 - e)]),
            pal["paper"])
    cv.fill(cv.poly([(px1 - ear, py0), (px1 - ear, py0 + ear), (px1, py0 + ear)]), pal["paper_edge"])
    cv.fill(cv.poly([(px1 - ear + e, py0 + e * 2.4), (px1 - ear + e, py0 + ear - e),
                     (px1 - e * 2.4, py0 + ear - e)]), pal["fold"])
    grid, cw, rh = marker_spec(n)
    mx, my = 2 * u, 2 * u
    if n <= 16:
        cw, rh = [1, 2, 2, 2, 1], [1, 2, 2, 2, 1]
    else:
        m = 2 * (n // 32)                        # 2 px / module at 32
        cw = rh = [m] * 6
    y = my
    for r, h in enumerate(rh):
        x = mx
        for c, w in enumerate(cw):
            if grid[r][c]:
                cv.fill(cv.rect(x, y, x + w, y + h), pal["black"])
            x += w
        y += h
    # two text lines under the marker
    ly = my + sum(rh) + (2 if n <= 16 else 2 * u)
    for i in range(2 if n > 16 else 1):
        cv.fill(cv.rect(2 * u, ly + i * 2 * u, 7 * u, ly + i * 2 * u + max(1, u // 2 + (u > 1))),
                pal["line"])
    # Fusion's blue export arrow, out of the sheet's lower right
    g = Canvas(n)
    ay = 12.5 * u                                # arrow centre line
    sh = 2 * u if n > 16 else 2                  # shaft thickness
    g.fill(g.rect(8 * u, ay - sh / 2, 12.5 * u, ay + sh / 2), pal["accent"])
    g.fill(g.poly([(11.5 * u, ay - 3.5 * u), (16 * u, ay), (11.5 * u, ay + 3.5 * u)]), pal["accent"])
    overlay(cv, g, gap_for(n))


def draw_send_prusa(cv: Canvas, pal, n: int):
    """Fusion's 3D Print glyph (a printer with an orange nozzle) printing a
    marker on its bed."""
    u = n // 16
    e = 1 if n <= 32 else 2                      # outline width
    # printer body: chamfered top corners, grey outline, light fill
    cv.fill(cv.poly([(0, 3 * u), (2 * u, 1 * u), (14 * u, 1 * u), (16 * u, 3 * u),
                     (16 * u, 16 * u), (0, 16 * u)]), pal["metal"])
    cv.fill(cv.poly([(e, 3 * u + e * 0.41), (2 * u + e * 0.41, 1 * u + e),
                     (14 * u - e * 0.41, 1 * u + e), (16 * u - e, 3 * u + e * 0.41),
                     (16 * u - e, 16 * u - e), (e, 16 * u - e)]), pal["fold"])
    # build volume, and the tag on the bed
    cv.fill(cv.rect(2 * u, 5 * u, 14 * u, 15 * u), pal["paper"])
    grid, cw, rh = marker_spec(n)
    if n <= 16:
        x0, y0 = 4, 6
        cw = rh = [1, 2, 2, 2, 1]
    else:
        x0 = (n - sum(cw)) // 2
        y0 = 15 * u - sum(rh) - u // 2
    y = y0
    for r, h in enumerate(rh):
        x = x0
        for c, w in enumerate(cw):
            if grid[r][c]:
                cv.fill(cv.rect(x, y, x + w, y + h), pal["black"])
            x += w
        y += h
    # print head: heater block + orange nozzle, tip on the tag
    cx, top = 8 * u, 1 * u + (1 if n <= 16 else u // 2)
    cv.fill(cv.rect(cx - 3 * u, top, cx + 3 * u, top + 2 * u), pal["metal"])
    cv.fill(cv.rect(cx - 2 * u, top + 2 * u, cx + 2 * u, top + 3 * u), pal["orange"])
    if n <= 16:
        cv.fill(cv.rect(cx - 1, top + 3, cx + 1, top + 4), pal["orange"])
    else:
        cv.fill(cv.poly([(cx - 1.5 * u, top + 3 * u - 0.01), (cx + 1.5 * u, top + 3 * u - 0.01),
                         (cx + 0.5 * u, top + 4.5 * u), (cx - 0.5 * u, top + 4.5 * u)]),
                pal["orange"])


def draw_edit(cv: Canvas, pal, n: int):
    u = n // 16
    draw_tile(cv, pal, 0, 0, n)
    g = Canvas(n)
    if n <= 16:
        # hand-pixelled: a 3-diagonal band x + y in {19, 20, 21}, tip at
        # (5, 15); a = x - y runs along the pencil (-10 tip .. 10 eraser)
        for y in range(16):
            for x in range(16):
                pp, a = x + y, x - y
                if pp not in (19, 20, 21) or not -10 <= a <= 10:
                    continue
                if a == -10:
                    col = pal["lead"]
                elif a <= -7:
                    col = pal["wood"]
                elif a <= 5:
                    col = pal["pencil_dark"] if pp == 21 else pal["pencil"]
                elif a <= 7:
                    col = pal["ferrule"]
                else:
                    col = pal["eraser"]
                g.fill(g.rect(x, y, x + 1, y + 1), col)
    else:
        # along the 45 degree diagonal, tip bottom-left
        d = np.array([1.0, -1.0]) / np.sqrt(2)   # tip -> eraser
        nn = np.array([1.0, 1.0]) / np.sqrt(2)   # across, towards bottom-right
        tip = np.array([5.2, 15.8]) * u
        hw = 2.0 * u                             # half width

        def seg(t0, t1, w0, w1, color, lo=-1.0, hi=1.0):
            a0, a1 = tip + d * t0, tip + d * t1
            g.fill(g.poly([tuple(a0 + nn * w0 * lo), tuple(a1 + nn * w1 * lo),
                           tuple(a1 + nn * w1 * hi), tuple(a0 + nn * w0 * hi)]), color)

        seg(0.0, 1.4 * u, 0.0, 0.6 * u, pal["lead"])
        seg(1.4 * u, 4.2 * u, 0.6 * u, hw, pal["wood"])
        seg(4.2 * u, 11.6 * u, hw, hw, pal["pencil"])
        seg(4.2 * u, 11.6 * u, hw, hw, pal["pencil_dark"], lo=0.3)   # shaded side
        seg(11.6 * u, 12.8 * u, hw, hw, pal["ferrule"])
        seg(12.8 * u, 14.2 * u, hw, hw, pal["eraser"])
    overlay(cv, g, gap_for(n))


ICONS = {           # the drawn ones; bamArucoExtrude is built from Fusion's PNGs
    "bamArucoIds": draw_ids,
    "bamArucoExport": draw_export,
    "bamArucoSendPrusa": draw_send_prusa,
    "bamArucoEdit": draw_edit,
}
LABELS = {"bamArucoExtrude": "ArUco Extrude", "bamArucoIds": "IDs",
          "bamArucoExport": "Export", "bamArucoSendPrusa": "Send to Prusa",
          "bamArucoEdit": "Edit"}


def render(name: str, n: int, theme: str) -> Image.Image:
    cv = Canvas(n)
    ICONS[name](cv, PALETTES[theme], n)
    return cv.image()


def write_folder(folder: Path, name: str) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    out = {}
    for theme, suffix in (("light", ""), ("dark", "-dark")):
        im = {n: render(name, n, theme) for n in (16, 32, 64)}
        files = {f"16x16{suffix}.png": im[16], f"16x16{suffix}@2x.png": im[32],
                 f"32x32{suffix}.png": im[32], f"32x32{suffix}@2x.png": im[64],
                 f"64x64{suffix}.png": im[64]}
        for fn, img in files.items():
            img.save(folder / fn)
        out[theme] = im
    return out


# --------------------------------------------------------------------------
# preview sheet
# --------------------------------------------------------------------------
def _font(size):
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def make_preview(sets: dict, stats: dict, path: Path):
    """1. Every icon at 1x (16 / 32 / 64) on Fusion's light and dark toolbar
    greys, Fusion's own Extrude first.
    2. Per size and theme: Fusion's Extrude | ArUco Extrude | diff mask, 8x
    (64 px at 4x), nearest neighbour, with both at 1x on the left. Diff mask:
    magenta = the pixel differs inside the top face, red = it differs outside
    the top face (there must be none).
    3. The diff counts of every file."""
    BG = {"light": (240, 240, 240, 255), "dark": (43, 43, 43, 255)}
    FG = {"light": (70, 70, 70, 255), "dark": (205, 205, 205, 255)}
    INK = (40, 40, 40, 255)
    IN, OUT = (226, 0, 154, 255), (255, 0, 0, 255)
    font, small = _font(14), _font(12)
    fus = {"light": {}, "dark": {}}
    for fn, (f_arr, *_rest) in stats.items():
        if "@2x" not in fn:
            fus["dark" if "-dark" in fn else "light"][f_arr.shape[0]] = Image.fromarray(f_arr, "RGBA")
    cols = [("Fusion Extrude", None)] + [(LABELS[k], k) for k in sets]

    left, col_w, head_h, strip_h = 190, 140, 30, 84
    strips = [("light", "light", "light  #f0f0f0"),
              ("dark", "dark", "dark  #2b2b2b  (-dark files)"),
              ("dark", "light", "dark, fallback: light files")]
    zoom = {16: 8, 32: 8, 64: 4}
    gap, ggap, lab = 12, 48, 44
    group_w = max((n + gap) + 3 * n * zoom[n] + 2 * gap for n in zoom)
    cmp_h = {n: lab + n * zoom[n] + 20 for n in zoom}
    y_cmp = head_h + strip_h * len(strips) + 34
    y_tab = y_cmp + sum(cmp_h.values()) + 12
    W = max(left + col_w * len(cols) + 10, 20 + 2 * group_w + ggap + 20)
    H = y_tab + 22 + 5 * 20 + 12
    sheet = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    d = ImageDraw.Draw(sheet)

    def put(img, x, y, bg, scale=1):
        b = Image.new("RGBA", img.size, bg)
        b.alpha_composite(img)
        if scale != 1:
            b = b.resize((img.width * scale, img.height * scale), Image.NEAREST)
        sheet.paste(b, (x, y))

    # 1. every icon at 1x
    for j, (label, _) in enumerate(cols):
        d.text((left + j * col_w, 8), label, fill=INK, font=font)
    d.text((12, 8), "16 / 32 / 64 px at 1x", fill=INK, font=small)
    for i, (bg, files, tag) in enumerate(strips):
        y0 = head_h + i * strip_h
        d.rectangle([0, y0, W, y0 + strip_h - 1], fill=BG[bg])
        d.text((12, y0 + 34), tag, fill=FG[bg], font=small)
        for j, (_, key) in enumerate(cols):
            x = left + j * col_w
            im = fus[files] if key is None else sets[key][files]
            put(im[16], x, y0 + 10, BG[bg])
            put(im[32], x + 24, y0 + 10, BG[bg])
            put(im[64], x + 64, y0 + 10, BG[bg])

    # 2. Fusion | ours | diff mask, per size and theme
    d.text((12, y_cmp - 26), "ArUco Extrude vs Fusion's Extrude:   Fusion  |  ours  |  diff mask "
           "(magenta = changed inside the top face, red = changed outside it)", fill=INK, font=font)
    y0 = y_cmp
    for n in (16, 32, 64):
        z = zoom[n]
        for t, theme in enumerate(("light", "dark")):
            x0 = 20 + t * (group_w + ggap)
            d.rectangle([x0 - (20 if t == 0 else ggap // 2), y0,
                         x0 + group_w + (ggap // 2 if t == 0 else 20), y0 + cmp_h[n] - 1],
                        fill=BG[theme])
            sfx = "-dark" if theme == "dark" else ""
            files = [f"{n}x{n}{sfx}.png"] + ([f"{n // 2}x{n // 2}{sfx}@2x.png"] if n > 16 else [])
            f_arr, o_arr, mask, outside, inside = stats[files[0]]
            d.text((x0, y0 + 6), f"{' = '.join(files)}   ({theme}, {z}x)", fill=FG[theme], font=small)
            d.text((x0, y0 + 22), f"outside the top face: {outside} px differ    "
                   f"inside: {inside} of {int(mask.sum())}", fill=FG[theme], font=small)
            yz = y0 + lab
            f_img, o_img = Image.fromarray(f_arr, "RGBA"), Image.fromarray(o_arr, "RGBA")
            put(f_img, x0, yz, BG[theme])
            put(o_img, x0, yz + n + 6, BG[theme])
            diff = np.any(f_arr != o_arr, axis=2)
            dm = np.zeros((n, n, 4), np.uint8)
            dm[diff & mask] = IN
            dm[diff & ~mask] = OUT
            x = x0 + n + gap
            for img in (f_img, o_img, Image.fromarray(dm, "RGBA")):
                put(img, x, yz, BG[theme], z)
                d.rectangle([x - 1, yz - 1, x + n * z, yz + n * z], outline=(128, 128, 128, 255))
                x += n * z + gap
        y0 += cmp_h[n]

    # 3. every file's counts
    d.text((12, y_tab), "every file: pixels that differ from Fusion's file of the same name, "
           "outside / inside the top face", fill=INK, font=font)
    for i, fn in enumerate(EXTRUDE_FILES):
        _f, _o, mask, outside, inside = stats[fn]
        d.text((24 + (i // 5) * 380, y_tab + 22 + (i % 5) * 20),
               f"{fn}:   outside {outside}   inside {inside} / {int(mask.sum())}",
               fill=INK, font=small)
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.convert("RGB").save(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=RESOURCES,
                    help="folder that receives the icon folders (default: ArucoMarkers/resources)")
    ap.add_argument("--preview", type=Path, default=PREVIEW)
    ap.add_argument("--fusion-extrude", type=Path, default=None,
                    help="Fusion's solid/extrude icon folder (default: find it in the install)")
    args = ap.parse_args()
    fusion_dir = find_fusion_extrude(args.fusion_extrude)
    sets = {name: write_folder(args.out / name, name) for name in ICONS}
    print("wrote", ", ".join(ICONS), "under", args.out)
    if fusion_dir is None:
        where = (f"{args.fusion_extrude} is not a complete Extrude icon set"
                 if args.fusion_extrude else
                 f"Fusion's Extrude icon set not found (looked in {WEBDEPLOY}, then searched "
                 + ", ".join(str(r) for r in FUSION_SEARCH_ROOTS) + "); pass --fusion-extrude DIR")
        print(f"error: {where}. bamArucoExtrude and the preview were NOT regenerated.",
              file=sys.stderr)
        return 1
    ext, stats = write_extrude_folder(args.out / "bamArucoExtrude", fusion_dir)
    print("wrote bamArucoExtrude from", fusion_dir)
    for fn in EXTRUDE_FILES:
        print(f"  {fn:<20} differs from Fusion's: {stats[fn][3]} px outside the top face, "
              f"{stats[fn][4]} inside")
    make_preview({"bamArucoExtrude": ext, **sets}, stats, args.preview)
    print("preview", args.preview)
    return 0


if __name__ == "__main__":
    sys.exit(main())
