# -*- coding: utf-8 -*-
""""Auto size on a clicked face" -- the verification gallery as one page.

    -> reports/aruco/autosize-faces.html

Reads what the two runs wrote into reports/media/aruco/autosize/:
  offline_gallery.json + offline_*.png   tools/render_autosize_gallery.py
  fusion_gallery.json  + fusion_*.png    bridge action "autosize_gallery"
and re-runs the brute-force raster of tests/test_aruco_autosize.py for the
verification table. The look (CSS, top bar, theme switch) is lifted from
autosize-proposals.html so the pages stay one family.

Run:  <clank-viser venv>/python docs/build_aruco_autosize_faces_report.py
"""
from __future__ import annotations

import html
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "CADExporter"), str(ROOT / "ArucoMarkers"), str(ROOT / "tests")]

from bam_cad_core import aruco_autosize as A  # noqa: E402
import aruco_autosize_gallery as G  # noqa: E402

OUT = ROOT / "reports" / "aruco" / "autosize-faces.html"
MEDIA = ROOT / "reports" / "media" / "aruco" / "autosize"
REL = "../media/aruco/autosize/"
SIBLING = ROOT / "reports" / "aruco" / "autosize-proposals.html"
NAV = [("index.html", "Overview"), ("how-it-works.html", "How it works"), ("prior-art.html", "Prior art"),
       ("fusion-architecture.html", "Fusion architecture"), ("charuco-studio-proposals.html", "Studio proposals"),
       ("autosize-proposals.html", "Auto-sizing"), ("autosize-faces.html", "Auto-size faces")]
KIND_NAME = {"single": "Single", "grid": "Grid", "charuco": "ChArUco"}

EXTRA_CSS = """
.gal{display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; margin:14px 0 8px}
@media (max-width:980px){.gal{grid-template-columns:1fr}}
.card{background:var(--surface); border:1px solid var(--line); border-radius:12px; padding:10px 10px 12px; min-width:0}
.card h4{margin:0 0 8px; font-size:15px; display:flex; gap:8px; align-items:baseline; flex-wrap:wrap}
.card h4 .k{font-size:11.5px; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); font-weight:650}
.pair{display:grid; grid-template-columns:1fr 1fr; gap:6px}
.pair figure{margin:0}
.pair img{display:block; width:100%; aspect-ratio:1/1; object-fit:cover; object-position:top; border-radius:7px;
  border:1px solid var(--line); background:var(--surface-2)}
.pair figcaption{font-size:11.5px; color:var(--muted); margin:3px 2px 0; letter-spacing:.04em; text-transform:uppercase}
.nums{font-size:13.5px; line-height:1.45; color:var(--ink-2); margin:8px 2px 0}
.nums b{color:var(--ink)}
.nums .m{color:var(--muted)}
.match{font:650 11px/1.4 var(--sans); padding:1px 7px; border-radius:999px}
.match.ok{color:var(--good); background:var(--good-weak); border:1px solid var(--good)}
.match.no{color:var(--bad); background:var(--bad-weak); border:1px solid var(--bad)}
.match.na{color:var(--warn); background:var(--warn-weak); border:1px solid var(--warn)}
.duo{display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; margin:16px 0}
@media (max-width:760px){.duo{grid-template-columns:1fr}}
.duo img{display:block; width:100%; height:auto; border-radius:10px; border:1px solid var(--line)}
.duo figcaption{font-size:14px; color:var(--muted); margin:6px 2px 0}
.sheet img{display:block; width:100%; height:auto; border-radius:10px; border:1px solid var(--line)}
ol.steps li{margin:6px 0}
"""


def e(t):
    return html.escape(str(t), quote=True)


def mm(v):
    return ("%.2f" % v).rstrip("0").rstrip(".") if abs(v - round(v, 1)) > 1e-9 else "%.1f" % v


def box(t):
    if not t:
        return "-"
    w, h = t
    return "%s mm" % mm(w) if abs(w - h) < 1e-6 else "%s × %s mm" % (mm(w), mm(h))


def lift():
    src = SIBLING.read_text(encoding="utf-8")
    css = re.search(r"<style>(.*?)</style>", src, re.S).group(1)
    brand = re.search(r'(<a class="brand".*?</a>)', src, re.S).group(1)
    script = re.search(r"(<script>.*?</script>)", src, re.S).group(1)
    return css, brand, script


def brute(loops, lay, pl):
    """The raster band for one result (see tests/test_aruco_autosize.Raster)."""
    from test_aruco_autosize import Raster
    R = Raster(loops)
    step = A.STEP_M
    if lay.kind == "single":
        f = 1.0 + 2.0 * lay.quiet_modules / lay.n
        hi = max([k for k in range(1, 400) if R.maybe(k * step * f, k * step * f)] or [0]) * step
        lo = max([k for k in range(1, 400) if R.surely(k * step * f, k * step * f)] or [0]) * step
        return "%s–%s" % (mm(lo * 1e3), mm(hi * 1e3)) if lo != hi else mm(lo * 1e3)
    c = (pl or {}).get("clear")
    if not c:
        return "-"
    wb, hb = R.largest(c["w"] / c["h"])
    return "%s × %s" % (mm(wb * 1e3), mm(hb * 1e3))


def main():
    css, brand, script = lift()
    off = json.loads((MEDIA / "offline_gallery.json").read_text(encoding="utf-8"))
    fus_path = MEDIA / "fusion_gallery.json"
    fus = json.loads(fus_path.read_text(encoding="utf-8")) if fus_path.exists() else {"faces": {}}
    built = time.strftime("%Y-%m-%d")
    nav = "".join('<a href="%s"%s>%s</a>' % (fn, ' class="on" aria-current="page"' if fn == OUT.name else "", e(lab))
                  for fn, lab in NAV)
    faces = {f["key"]: f for f in off["faces"]}
    disc = faces["disc4"]
    d1 = disc["auto"]["single"]
    fd1 = fus["faces"].get("disc4", {}).get("single", {})

    # ------------------------------------------------------------ numbers
    n_match = n_total = n_live = n_err = 0
    for f in off["faces"]:
        for kind in G.KINDS:
            o = f["auto"][kind]
            r = fus["faces"].get(f["key"], {}).get(kind)
            if r is None:
                continue
            n_total += 1
            if r.get("label") and r["label"] == o["label"]:
                n_match += 1
            elif not o["ok"] and r.get("error", "").startswith("ValueError: no marker fits"):
                n_match += 1                     # both: nothing printable, the dialog refuses
            n_live += bool(r.get("live"))
            n_err += bool(r.get("error"))
    times = [f["auto"][k]["fit_ms"] for f in off["faces"] for k in G.KINDS] + \
        [t["fit_ms"] for f in off["faces"] for t in f["typed"]]

    toc = [("disc", "His disc"), ("how", "How the default is found"), ("gallery", "The gallery"),
           ("typed", "Typed sizes"), ("check", "Checked against a brute force"), ("fusion", "In Fusion"),
           ("dialog", "In the dialog"), ("changed", "What changed")]
    out = []
    w = out.append
    w('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n'
      '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
      '<title>Auto-size on faces</title>\n'
      '<meta name="description" content="ArUco Extrude: the default size of a tag on a clicked face comes from '
      'the face\'s largest clear rectangle; ten faces checked offline, in Fusion and against a brute force.">\n'
      '<style>%s%s</style></head>\n<body>\n' % (css, EXTRA_CSS))
    w('<header class="topbar"><div class="in">\n%s\n<nav aria-label="Reports">%s</nav><span class="sp"></span>\n'
      '<button id="themebtn" type="button" aria-label="Switch colour theme">Theme: auto</button>\n</div></header>\n'
      % (brand, nav))
    w('<div class="wrap"><div class="layout">\n<nav class="toc" aria-label="Contents"><details><summary>Contents'
      '</summary><ol>%s</ol></details></nav>\n<main class="content">\n'
      % "".join('<li class="l2"><a href="#%s">%s</a></li>' % (a, e(t)) for a, t in toc))
    w('<div class="hero"><div class="eyebrow">Verification · %s · branch <code>claude/aruco-fusion-plugin-0e9bcb</code>'
      '</div><h1>Auto size on a clicked face: the largest clear rectangle</h1>'
      '<p class="lede">Click a face and the default size now comes from the largest rectangle that is clear of its '
      'outline, holes and notches, not from the face\'s bounding box. Ten faces, each sized offline and by the real '
      'add-in in Fusion, all checked against a brute force.</p>'
      '<p class="meta">Code: <code>CADExporter/bam_cad_core/aruco_autosize.py</code> (<code>fit_on_loops</code>, '
      '<code>largest_clear_rect</code>, <code>face_default</code>). Faces: <code>ArucoMarkers/aruco_autosize_gallery.py'
      '</code>. Tests: <code>tests/test_aruco_autosize.py</code>.</p></div>\n' % built)
    w('<aside class="callout co-good"><div class="co-title">In short</div>'
      '<p><b>His Ø53 disc now defaults to a 28.0 mm marker, a 37.3 mm sticker</b> (marker + a 1-module white '
      'border) in its 37.4 mm largest clear square. Before, the 70 %% rule ran on the bounding box (37.0 mm), was '
      'shrunk to 27.5 mm by a coarse search, and said “shrunk for the face shape: 37.0 mm does not fit”.</p>'
      '<p><b>One rule on every face:</b> a single marker\'s sticker fills the largest clear square (never exactly '
      'flush); a grid or ChArUco board follows the doc\'s 70 %% rule inside the largest clear rectangle. In Fusion, '
      '<b>%d of %d</b> tags built through the face-click path match the offline numbers exactly.</p></aside>\n'
      % (n_match, n_total))

    # ------------------------------------------------------------ his disc
    w('<h2 id="disc">His disc</h2>')
    w('<blockquote class="quote"><p>“When I click the face I want it to also detect the largest rectangle and then '
      'use that to intelligently set the default, but then I can of course override it afterwards.”</p>'
      '<p>— Zach, 2026-10-08, on the Ø53 mm disc with four Ø2.85 mm holes</p></blockquote>')
    w('<div class="duo"><figure><img src="%s%s" alt="Offline: the disc with its largest clear square dashed and '
      'the 28 mm marker"><figcaption>Offline (<code>tools/render_autosize_gallery.py</code>): blue dashes are the '
      'largest clear square, orange the sticker.</figcaption></figure>' % (REL, d1["png"]))
    if fd1.get("shot"):
        w('<figure><img src="%s%s" alt="Fusion: the same disc with the ArUco Extrude built by the add-in">'
          '<figcaption>Fusion: the add-in\'s ArUco Extrude on the same face (black only; the disc is the white), '
          'top view, joint glyphs hidden.</figcaption></figure>' % (REL, fd1["shot"]))
    w('</div>')
    w('<div class="tablewrap"><table class="md"><tr><th></th><th>Before</th><th>Now</th></tr>'
      '<tr><td>Sized from</td><td>the bounding box, 53 × 53 mm (70 %% → 37.0)</td><td>the largest clear square, '
      '%s mm (the rim binds at the corners; the holes on the axes do not)</td></tr>'
      '<tr><td>Single, auto</td><td class="num">27.5 mm</td><td class="num">%s mm marker · %s sticker</td></tr>'
      '<tr><td>Note</td><td>“shrunk for the face shape: 37.0 mm does not fit, using 27.5 mm”</td>'
      '<td>“%s”</td></tr>'
      '<tr><td>Typed 37</td><td>clamped</td><td>clamped to 28.0, said so: “clamped: 37.0 mm does not fit, using 28.0 '
      'mm”</td></tr>'
      '<tr><td>Grid / ChArUco, auto</td><td>from the box</td><td class="num">%s · %s</td></tr></table></div>'
      % (mm(d1["clear_mm"]["w"]), mm(d1["marker_mm"]), box(d1["tile_mm"]), e(d1["notes"][0]),
         e(disc["auto"]["grid"]["label"]), e(disc["auto"]["charuco"]["label"])))
    w('<aside class="callout co-mini"><p>Why 28 and not 37: the square he sees is the <b>sticker</b>, and OpenCV '
      'needs the black square plus at least one module of white around it. 37.4 × 6/8 = 28.05, so 28.0 is the '
      'largest 0.5 mm step. A 37 mm <i>black</i> square does fit inside the disc (its diagonal is 52.3 mm), but its '
      'white border would fall off the rim at the corners. Size still means the black square; the dialog now '
      'shows both numbers.</p></aside>')

    # ------------------------------------------------------------ how
    w('<h2 id="how">How the default is found</h2><ol class="steps">'
      '<li><b>The face\'s loops</b>, outline and holes, stroked the way Fusion strokes an edge (0.05 mm chords).</li>'
      '<li><b>The largest clear rectangle of a given aspect</b> is found exactly, not sampled. A w × h box centred '
      'at p is clear iff no boundary segment comes nearer than h/2 in the norm max(|dx|/a, |dy|), so the best box '
      'is twice the deepest point in that norm. A branch and bound over square cells finds that point with a proven '
      'bound (0.02 mm): a cell can do no better than its centre\'s distance plus its half-size, so most cells are '
      'dropped unseen (Mapbox\'s polylabel method, in the box norm).</li>'
      '<li><b>The largest of all</b>: 33 aspects from 1:16 to 16:1, the best refined by golden section, then each '
      'side pushed out by bisection to a maximal clear rectangle (an L\'s leg comes out exactly 20 × 60).</li>'
      '<li><b>Single</b>: the largest 0.5 mm marker whose sticker fits the largest clear square, never exactly '
      'flush (5 µm). A flush white tile makes Fusion merge its walls with the body\'s; the live test caught this '
      'on a 40 mm plate. The step above is tried and refused exactly: a “does it fit?” search dives toward the '
      'best centres, so a 50 mm tile in a 50 mm gap is found (50 nm contact tolerance).</li>'
      '<li><b>Grid / ChArUco</b>: the doc\'s size-first rule (<code>default_layout</code>, 70 % of the short '
      'side) on that rectangle, the board centred in it. If the largest rectangle holds no valid board, the next '
      'largest is used.</li>'
      '<li><b>A plain rectangle</b> is its own largest clear rectangle: the same rules on the box '
      '(<code>face_default</code>), and the same for a rectangular sketch area.</li>'
      '<li><b>A typed size</b> is kept when any placement holds it, with as many markers as fit (count first). '
      'If none fits, it is clamped to the largest step that does, with a note.</li></ol>')
    w('<p>Every fit, auto or typed, takes %.0f ms on average and %.0f ms at worst (%d fits, pure Python, the '
      'same code Fusion runs). His disc: %.0f ms single, %.0f / %.0f ms grid / ChArUco.</p>'
      % (sum(times) / len(times), max(times), len(times), d1["fit_ms"], disc["auto"]["grid"]["fit_ms"],
         disc["auto"]["charuco"]["fit_ms"]))

    # ------------------------------------------------------------ gallery
    w('<h2 id="gallery">The gallery</h2><p>Each card: offline on the left (face, holes, <span style="color:#2459b8">'
      'largest clear rectangle dashed</span>, <span style="color:#eb6834">sticker</span>, real black cells), Fusion on '
      'the right (the add-in\'s ArUco Extrude, black only, on a 3 mm body of that face). The badge compares the two '
      'labels.</p>')
    w('<figure class="sheet"><a href="%sgallery_offline.png"><img src="%sgallery_offline.png" alt="All ten faces, '
      'three layouts each, offline" loading="lazy"></a><figcaption>The whole offline sheet (click for full size).'
      '</figcaption></figure>' % (REL, REL))
    for f in off["faces"]:
        w('<h3 id="f-%s">%s</h3><div class="gal">' % (e(f["key"]), e(f["title"])))
        for kind in G.KINDS:
            o = f["auto"][kind]
            r = fus["faces"].get(f["key"], {}).get(kind, {})
            if r.get("label"):
                badge = ('<span class="match ok">Fusion: same</span>' if r["label"] == o["label"]
                         else '<span class="match no">Fusion: %s</span>' % e(r["label"]))
            elif r.get("error", "").startswith("ValueError: no marker fits"):
                badge = '<span class="match na">nothing printable: refused in both</span>'
            elif r:
                badge = '<span class="match no">Fusion error</span>'
            else:
                badge = '<span class="match na">not run in Fusion</span>'
            w('<div class="card"><h4><span class="k">%s</span>%s</h4><div class="pair">' % (KIND_NAME[kind], badge))
            w('<figure><img src="%s%s" alt="offline %s %s" loading="lazy"><figcaption>offline</figcaption></figure>'
              % (REL, o["png"], e(f["key"]), kind))
            if r.get("shot"):
                w('<figure><img src="%s%s" alt="Fusion %s %s" loading="lazy"><figcaption>Fusion%s</figcaption>'
                  '</figure>' % (REL, r["shot"], e(f["key"]), kind, "" if r.get("live") else " (timeline group)"))
            else:
                w('<figure><div style="aspect-ratio:1/1;border:1px dashed var(--line-2);border-radius:7px;'
                  'display:grid;place-items:center;font-size:12px;color:var(--muted);padding:8px;text-align:center">'
                  '%s</div><figcaption>Fusion</figcaption></figure>'
                  % e((r.get("error") or "no capture").split(":", 1)[-1][:120]))
            w('</div>')
            cl = o.get("clear_mm")
            first = ("board %s (no border)" if kind == "charuco" else "sticker %s") % box(o["tile_mm"])
            w('<div class="nums"><b>%s</b>%s<br>%s<br><span class="m">clear %s · fill %.0f %% · %.0f ms</span></div>'
              % (e(o["label"]), "" if o["ok"] else ' <span class="match no">not printable</span>', first,
                 ("%s × %s mm" % (mm(cl["w"]), mm(cl["h"]))) if cl else "-", 100 * o["fill"], o["fit_ms"]))
            w('</div>')
        w('</div>')

    # ------------------------------------------------------------ typed
    w('<h2 id="typed">Typed sizes</h2><p>A size he types is kept when it fits anywhere, with as many markers as '
      'fit; when it does not, it is clamped to the largest step that does, with a note. 30.0 mm single and 8.0 mm '
      'grid on every face, plus the 37.0 he typed on his disc.</p>')
    w('<div class="tablewrap"><table class="md"><tr><th>Face</th><th>Typed</th><th>Result</th><th>Note</th></tr>')
    for f in off["faces"]:
        for t in f["typed"]:
            note = "; ".join(n for n in t["notes"] if not n.startswith("not a rectangle")) or "kept"
            w('<tr><td>%s</td><td class="num">%s %s mm</td><td class="num">%s</td><td>%s</td></tr>'
              % (e(f["key"]), t["kind_typed"], mm(t["typed_mm"]), e(t["label"]), e(note)))
    w('</table></div>')

    # ------------------------------------------------------------ check
    w('<h2 id="check">Checked against a brute force</h2><p>The reference shares no code with the solver: each face '
      'is rasterised at 0.1 mm (pixel centres, even-odd over every loop), and every window of every size is tested '
      'with a summed-area table. It can be off by about a pixel, so it gives a band: “surely fits” (a window one '
      'pixel bigger all round is inside) to “may fit” (one pixel smaller). The test suite asserts that every auto '
      'and typed result lies in the band, which is within one 0.5 mm step. It also asserts that every tile is '
      'inside its face and clear of the holes (an independent dense-sample check), that the largest clear '
      'rectangle matches the raster\'s within 3 %, and that a face turned by 90°, 180° or 270° gets the same '
      'answer.</p>')
    w('<div class="tablewrap"><table class="md"><tr><th>Face</th><th>Single: ours</th><th>Brute force</th>'
      '<th>Grid: clear rect (ours / raster)</th><th>ChArUco: clear rect (ours / raster)</th></tr>')
    for f0 in G.FACES:
        loops = G.loops_m(f0)
        cells = []
        for kind in G.KINDS:
            lay, pl = A.fit_on_loops(kind, loops)
            if kind == "single":
                cells.append('<td class="num">%s</td><td class="num">%s</td>' % (mm(lay.marker_m * 1e3),
                                                                                 brute(loops, lay, pl)))
            else:
                c = (pl or {}).get("clear")
                ours = ("%s × %s" % (mm(c["w"] * 1e3), mm(c["h"] * 1e3))) if c else "-"
                cells.append('<td class="num">%s / %s</td>' % (ours, brute(loops, lay, pl)))
        w('<tr><td>%s</td>%s</tr>' % (e(f0["key"]), "".join(cells)))
    w('</table></div><p class="meta">Rectangles (the strip) skip the search: the face is its own clear rectangle. '
      'Where the band is one step wide (a sticker that fits within a pixel), the exact answer is the solver\'s: '
      'its step above is refused by an exact containment test.</p>')

    # ------------------------------------------------------------ fusion
    w('<h2 id="fusion">In Fusion</h2><p>The bridge action <code>autosize_gallery</code> builds every face three '
      'times in a new document of its own, extrudes it 3 mm, makes an ArUco Extrude on the top face by the same '
      'path a click takes (<code>aruco_feature.create</code> with auto size, inside <code>in_command</code>), '
      'captures an orthographic top view with the joint, Joint Origin, sketch and origin folders hidden, then '
      'closes the document and puts the user\'s back.</p>')
    w('<ul><li><b>%d of %d</b> results equal the offline ones, counting the two faces where nothing printable '
      'fits a ChArUco board (the ring, the 14 mm strip): offline says ok = False, and Fusion refuses with the '
      'same note.</li>' % (n_match, n_total))
    w('<li><b>%d</b> are live ArUco Extrude features. The others are ChArUco boards as wide as the face (the L\'s '
      'leg, the plate beside its hole, the notched plate): their inlay splits the top face, so the live feature '
      'cannot hold on to it and the tag is made as a timeline group. This was already true on rectangles (a 6 × 2 '
      'board on a 20 mm strip), where it used to fail outright. <code>create()</code> now reads the face\'s token '
      'before the cut, so the tag is still made.</li>' % n_live)
    w('<li>The end-to-end <code>live_test</code> (ten tags across faces, sketches, a child component, parameter, '
      'upstream and structural edits, colours, Joint Origins) passes after these changes: 39 of 39 steps, Joint '
      'Origin verdict PASS. Its colour checks are what caught the flush sticker.</li></ul>')

    # ------------------------------------------------------------ dialog
    w('<h2 id="dialog">In the dialog</h2><ul>'
      '<li><b>Info line</b>: “marker 28.0 mm · sticker 37.3 mm (white border 1 module = 4.67 mm)”. A board says '
      '“marker … · sticker W × H mm”; ChArUco says “marker … · checker … · board W × H mm (no outer border)”.</li>'
      '<li><b>Size tooltip</b>: Size is the black square (OpenCV\'s marker length); the white border, the quiet '
      'zone in Advanced, comes on top: the sticker is Size + 2 modules. Size keeps its name and its meaning.</li>'
      '<li><b>Auto size tooltip</b>: from the face\'s largest clear rectangle.</li>'
      '<li><b>Reset</b> in Advanced: a short label (“Defaults”) and the button\'s own text (“Reset”), not the same '
      'words twice. Not yet seen in his Fusion.</li>'
      '<li>The Black height arrow limit is left to the height rework that follows this.</li></ul>')

    # ------------------------------------------------------------ changed
    w('<h2 id="changed">What changed</h2><ul>'
      '<li><b>Non-rectangular faces</b>: the default comes from the largest clear rectangle instead of the bounding '
      'box; no “shrunk …” note for an auto default.</li>'
      '<li><b>Single on any face or sketch area, rectangles too</b>: the sticker fills the clear square, so a '
      '40 × 40 face gives 29.5 (sticker 39.3), not the doc\'s 28.0 (70 %). Boards keep the doc\'s rule; '
      '<code>default_layout</code> itself is unchanged, and so is its doc table.</li>'
      '<li><b>Exact search</b>: the cell-sampled <code>largest_rect</code> (≈ 0.44 mm cells) is replaced, inside '
      'the fitter, by the branch and bound. A typed 8 mm grid on the L now gets 6 along the 60 mm leg; the sampling '
      'found 5.</li></ul>')
    w('<div class="foot">Generated %s by <code>docs/build_aruco_autosize_faces_report.py</code> from '
      '<code>reports/media/aruco/autosize/*.json</code>. Offline panels: <code>tools/render_autosize_gallery.py'
      '</code>. Fusion: bridge action <code>autosize_gallery</code> (<code>ArucoMarkers/aruco_autosize_gallery.py'
      '</code>).</div>\n</main></div></div>\n%s\n</body></html>\n' % (built, script))
    OUT.write_text("".join(out), encoding="utf-8")
    print("wrote", OUT, "(%d of %d match, %d live)" % (n_match, n_total, n_live))


if __name__ == "__main__":
    main()
