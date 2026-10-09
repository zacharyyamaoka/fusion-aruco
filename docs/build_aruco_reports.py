#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Media-rich HTML reports for the ArUco / ChArUco-in-Fusion research and proposal.

    # PowerShell
    $env:PYTHONPATH = "<folder that holds cv2>"; python docs/build_aruco_reports.py
    # POSIX
    PYTHONPATH=<folder that holds cv2> python3 docs/build_aruco_reports.py
    # or pass the folder explicitly
    python docs/build_aruco_reports.py --pylib <folder that holds cv2>

Reads the three Markdown docs and writes four self-contained pages:

    docs/research_aruco_prior_art.md           -> reports/aruco/prior-art.html
    docs/research_aruco_fusion_architecture.md -> reports/aruco/fusion-architecture.html
    docs/proposal_charuco_studio_in_fusion.md  -> reports/aruco/charuco-studio-proposals.html
                                                  reports/aruco/index.html (landing page)

WHY a builder and not hand-written HTML: the prose comes from the Markdown, so
a doc edit is one rebuild away from the page; and every marker, board and bit
grid on the pages is drawn from OpenCV's own tables at build time (cv2.aruco),
then inlined as SVG. A hand-drawn marker would be a picture of a marker that
no detector would read. Facts the builder encodes for figures (scores, effort
splits, tool capabilities) are checked against the Markdown text at build time
and a mismatch is printed, so a doc edit cannot silently orphan a figure.

Only stdlib plus cv2/numpy at build time. The pages need no network: all CSS,
JS and images are inline.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
OUT_DIR = ROOT / "reports" / "aruco"
SRC = {
    "prior": DOCS / "research_aruco_prior_art.md",
    "arch": DOCS / "research_aruco_fusion_architecture.md",
    "prop": DOCS / "proposal_charuco_studio_in_fusion.md",
}
PAGES = [
    ("index", "index.html", "Overview"),
    # built by docs/build_aruco_howitworks_report.py (no OpenCV needed)
    ("how", "how-it-works.html", "How it works"),
    ("prior", "prior-art.html", "Prior art"),
    ("arch", "fusion-architecture.html", "Fusion architecture"),
    ("prop", "charuco-studio-proposals.html", "Studio proposals"),
    # built by docs/build_aruco_autosize_faces_report.py (the auto-size verification gallery)
    ("faces", "autosize-faces.html", "Auto-size faces"),
]
BUILT = date.today().isoformat()
BRANCH = "claude/aruco-fusion-plugin-0e9bcb"
WARNINGS: list[str] = []

cv2 = None  # set in main()
np = None


def warn(msg: str) -> None:
    WARNINGS.append(msg)
    print("WARN:", msg, file=sys.stderr)


def need(text: str, *snippets: str) -> None:
    """Every fact a figure encodes must still be in the doc it came from."""
    flat = re.sub(r"\s+", " ", text)
    for s in snippets:
        if re.sub(r"\s+", " ", s) not in flat:
            warn("figure fact not found in source doc: %r" % s)


# ------------------------------------------------------------------ helpers

def esc(s) -> str:
    return html.escape(str(s), quote=True)


def slugify(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    s = s.lower().replace("&amp;", "and")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "s"


def fmt_bytes(n: int) -> str:
    return "%.1f KB" % (n / 1024) if n < 1024 * 1024 else "%.2f MB" % (n / 1024 / 1024)


# ----------------------------------------------------------- markdown -> html
# Just enough Markdown for these three docs: headings, paragraphs, nested
# lists with continuation lines, tables, fences, block quotes, inline code,
# bold, *italic*, links. Underscore italics are deliberately NOT supported:
# "4x4_50 to 7x7_1000" and "_1000 tables" are prose in these docs.

CODE_RE = re.compile(r"`([^`]+)`")
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
LIST_RE = re.compile(r"^( *)([-*]|\d+\.) +(.*)$")
VERIFY_RE = re.compile(r"\(((?:verify|unverified|inferred)[^()]*)\)")


def verdict_class(label: str) -> str | None:
    low = label.lower()
    if low.startswith("adopt") or low.startswith("decals: adopt"):
        return "v-adopt"
    if low.startswith("adapt"):
        return "v-adapt"
    if low.startswith("avoid") or low.startswith("canvases: avoid"):
        return "v-avoid"
    if low.startswith("our own code"):
        return "v-own"
    return None


def inline(t: str) -> str:
    codes: list[str] = []

    def keep(m):
        codes.append("<code>%s</code>" % esc(m.group(1)))
        return "\x00%d\x00" % (len(codes) - 1)

    t = CODE_RE.sub(keep, t)
    t = esc(t)
    t = LINK_RE.sub(lambda m: '<a href="%s" target="_blank" rel="noopener">%s</a>'
                    % (m.group(2), m.group(1)), t)

    def bold(m):
        inner = m.group(1)
        vc = verdict_class(re.sub(r"<[^>]+>", "", inner))
        return '<b class="verdict %s">%s</b>' % (vc, inner) if vc else "<b>%s</b>" % inner

    t = re.sub(r"\*\*(.+?)\*\*", bold, t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", t)
    t = VERIFY_RE.sub(lambda m: '<span class="badge verify" title="Not confirmed in the source">'
                      '%s</span>' % m.group(1), t)
    t = re.sub("\x00(\\d+)\x00", lambda m: codes[int(m.group(1))], t)
    return t


def _lead(s: str) -> int:
    return len(s) - len(s.lstrip(" "))


def render_md(lines: list[str], hooks: dict | None = None, _ctr: dict | None = None) -> str:
    """Block renderer. hooks: {'fence': f(code, info, idx) -> html|None,
    'table': f(head, rows, idx) -> html|None}; idx counts per call tree."""
    hooks = hooks or {}
    ctr = _ctr if _ctr is not None else {"fence": 0, "table": 0}
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        if not ln.strip():
            i += 1
            continue
        if ln.lstrip().startswith("```"):
            info = ln.strip()[3:].strip()
            j = i + 1
            while j < n and not lines[j].lstrip().startswith("```"):
                j += 1
            code = "\n".join(lines[i + 1:j])
            idx = ctr["fence"]
            ctr["fence"] += 1
            got = hooks.get("fence", lambda *a: None)(code, info, idx)
            out.append(got if got is not None else
                       '<pre class="code"><code>%s</code></pre>' % esc(code))
            i = j + 1
            continue
        if ln.startswith(">"):
            q = []
            while i < n and lines[i].startswith(">"):
                q.append(re.sub(r"^> ?", "", lines[i]))
                i += 1
            out.append('<blockquote class="quote">%s</blockquote>' % render_md(q, hooks, ctr))
            continue
        if ln.startswith("|"):
            rows = []
            while i < n and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            rows = [r for r in rows if not all(re.fullmatch(r":?-{2,}:?", c) for c in r)]
            head, body = rows[0], rows[1:]
            idx = ctr["table"]
            ctr["table"] += 1
            got = hooks.get("table", lambda *a: None)(head, body, idx)
            out.append(got if got is not None else md_table(head, body))
            continue
        m = LIST_RE.match(ln)
        if m:
            indent = len(m.group(1))
            ordered = m.group(2)[0].isdigit()
            items = []
            start = int(m.group(2)[:-1]) if ordered else 1
            while i < n:
                m2 = LIST_RE.match(lines[i])
                if not (m2 and len(m2.group(1)) == indent and m2.group(2)[0].isdigit() == ordered):
                    break
                cind = indent + len(m2.group(2)) + 1
                body = [m2.group(3)]
                i += 1
                while i < n:
                    l2 = lines[i]
                    if not l2.strip():
                        j = i + 1
                        while j < n and not lines[j].strip():
                            j += 1
                        if j < n and _lead(lines[j]) > indent:
                            body.append("")
                            i += 1
                            continue
                        break
                    if _lead(l2) > indent:
                        body.append(l2[min(_lead(l2), cind):])
                        i += 1
                        continue
                    break
                items.append(body)
            lis = []
            for body in items:
                para = []
                k = 0
                while k < len(body) and body[k].strip() and not LIST_RE.match(body[k]) \
                        and not body[k].lstrip().startswith("```"):
                    para.append(body[k].strip())
                    k += 1
                rest = body[k:]
                lis.append("<li>%s%s</li>" % (inline(" ".join(para)),
                                              render_md(rest, hooks, ctr) if rest else ""))
            if ordered:
                st = ' start="%d"' % start if start != 1 else ""
                out.append("<ol%s>%s</ol>" % (st, "".join(lis)))
            else:
                out.append("<ul>%s</ul>" % "".join(lis))
            continue
        para = [ln.strip()]
        i += 1
        while i < n and lines[i].strip() and not re.match(r"^(#|```|\||>)", lines[i].lstrip()) \
                and not LIST_RE.match(lines[i]):
            para.append(lines[i].strip())
            i += 1
        out.append("<p>%s</p>" % inline(" ".join(para)))
    return "\n".join(out)


def md_table(head, body, cls="md") -> str:
    th = "".join("<th>%s</th>" % inline(c) for c in head)
    trs = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % inline(c) for c in r) for r in body)
    return ('<div class="tablewrap"><table class="%s"><thead><tr>%s</tr></thead>'
            '<tbody>%s</tbody></table></div>' % (cls, th, trs))


def split_md(text: str):
    """-> (title, preamble lines, flat list of h2/h3 sections)."""
    title, pre, secs, cur = "", [], [], None
    in_fence = False
    seen: set[str] = set()
    for ln in text.splitlines():
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
        m = None if in_fence else re.match(r"^(#{1,3})\s+(.*)$", ln)
        if m:
            lvl = len(m.group(1))
            if lvl == 1:
                title = m.group(2).strip()
                continue
            t = m.group(2).strip()
            num = re.match(r"^(\d+(?:\.\d+)?)\.?\s", t)
            key = num.group(1) if num else slugify(t)
            slug = "s-" + slugify(t)
            while slug in seen:
                slug += "-x"
            seen.add(slug)
            cur = {"level": lvl, "title": t, "key": key, "slug": slug, "lines": []}
            secs.append(cur)
            continue
        (cur["lines"] if cur else pre).append(ln)
    return title, pre, secs


def render_sections(secs, hooks: dict) -> tuple[str, list]:
    """Hooks per section key: lead (after heading), after, replace (instead of
    the prose; the prose then goes into a 'source text' fold), fence, table,
    toc (short TOC label)."""
    parts, toc = [], []
    for s in secs:
        h = hooks.get(s["key"], {})
        tag = "h2" if s["level"] == 2 else "h3"
        toc.append((s["level"], s["slug"], h.get("toc", s["title"])))
        parts.append('<%s id="%s">%s</%s>' % (tag, s["slug"], inline(s["title"]), tag))
        if h.get("lead"):
            parts.append(h["lead"])
        md_hooks = {k: h[k] for k in ("fence", "table") if k in h}
        prose = render_md(s["lines"], md_hooks)
        if h.get("replace") is not None:
            parts.append(h["replace"])
            if prose.strip():
                parts.append('<details class="src"><summary>Section text as written</summary>'
                             '<div class="srcbody">%s</div></details>' % prose)
        else:
            parts.append(prose)
        if h.get("after"):
            parts.append(h["after"])
    return "\n".join(parts), toc


# ------------------------------------------------------------- figure chrome

def fig(inner: str, caption: str = "", kind: str = "", cls: str = "", fid: str = "") -> str:
    k = '<span class="kind k-%s">%s</span> ' % (slugify(kind), esc(kind)) if kind else ""
    cap = "<figcaption>%s%s</figcaption>" % (k, caption) if (caption or kind) else ""
    idattr = ' id="%s"' % fid if fid else ""
    return '<figure class="fig %s"%s><div class="figbody">%s</div>%s</figure>' % (cls, idattr, inner, cap)


def callout(body: str, kind: str = "note", title: str = "") -> str:
    t = '<div class="co-title">%s</div>' % title if title else ""
    return '<aside class="callout co-%s">%s%s</aside>' % (kind, t, body)


def check_box(title: str, body: str) -> str:
    return ('<aside class="callout co-check"><div class="co-title"><span class="kind k-build-time-check">'
            'build-time check</span> %s</div>%s</aside>' % (title, body))


def ascii_fold(code: str, label: str = "ASCII sketch as written in the doc") -> str:
    return '<details class="src"><summary>%s</summary><pre class="code ascii"><code>%s</code></pre></details>' % (
        label, esc(code))


def wrap_lines(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w) if cur else w
    if cur:
        lines.append(cur)
    return lines


def svg_text(x, y, text, cls="", size=None, anchor="start", weight=None, width=None, lh=1.25,
             fill=None, family=None) -> str:
    """Multi-line SVG text (wraps at `width` characters)."""
    lines = wrap_lines(text, width) if width else [text]
    attrs = ' x="%s" y="%s"' % (x, y)
    if cls:
        attrs += ' class="%s"' % cls
    if size:
        attrs += ' font-size="%s"' % size
    if anchor != "start":
        attrs += ' text-anchor="%s"' % anchor
    if weight:
        attrs += ' font-weight="%s"' % weight
    if fill:
        attrs += ' fill="%s"' % fill
    if family:
        attrs += ' font-family="%s"' % family
    if len(lines) == 1:
        return "<text%s>%s</text>" % (attrs, esc(lines[0]))
    fs = size or 13
    sp = "".join('<tspan x="%s" dy="%s">%s</tspan>' % (x, 0 if k == 0 else round(fs * lh, 1), esc(l))
                 for k, l in enumerate(lines))
    return "<text%s>%s</text>" % (attrs, sp)


def arrow_defs(pid: str) -> str:
    return ('<defs><marker id="%s-ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
            'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="ahf"/>'
            '</marker><marker id="%s-ahb" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
            'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="ahf-bad"/>'
            '</marker><marker id="%s-ahg" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
            'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="ahf-good"/>'
            '</marker></defs>') % (pid, pid, pid)


def svg(inner: str, w: float, h: float, label: str, cls: str = "dg", style: str = "") -> str:
    st = ' style="%s"' % style if style else ""
    return ('<svg class="%s" viewBox="0 0 %s %s" role="img" aria-label="%s"%s>'
            '<title>%s</title>%s</svg>') % (cls, w, h, esc(label), st, esc(label), inner)


# ============================================================ OpenCV media
# Every marker on the pages is OpenCV's own rendering, read back as a bit grid
# (1 = black) and re-emitted as SVG rectangles, so it stays crisp at any zoom
# and is byte-for-byte the pattern a detector expects.

_DICT_CACHE: dict = {}


def cvdict(name: str):
    if name not in _DICT_CACHE:
        _DICT_CACHE[name] = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, name))
    return _DICT_CACHE[name]


def marker_grid(dict_name: str, mid: int, border: int = 1) -> list[list[int]]:
    d = cvdict(dict_name)
    side = d.markerSize + 2 * border
    img = cv2.aruco.generateImageMarker(d, int(mid), side, borderBits=border)
    return (img < 128).astype(int).tolist()


def g_bits(grid, x, y, size, quiet=0.0, bg="#fff", fg="#000", extra="") -> str:
    """A bit grid as an SVG group at (x, y), `size` wide including the quiet zone."""
    n = len(grid)
    total = n + 2 * quiet
    k = size / total
    rects = []
    for r, row in enumerate(grid):
        c = 0
        while c < n:
            if row[c]:
                s0 = c
                while c < n and row[c]:
                    c += 1
                rects.append('<rect x="%g" y="%g" width="%g" height="1"/>' % (s0 + quiet, r + quiet, c - s0))
            else:
                c += 1
    bgr = '<rect width="%g" height="%g" fill="%s"/>' % (total, total, bg) if bg else ""
    return ('<g transform="translate(%g %g) scale(%g)" shape-rendering="crispEdges"%s>%s<g fill="%s">%s</g></g>'
            % (x, y, k, extra, bgr, fg, "".join(rects)))


def marker_svg(dict_name: str, mid: int, quiet: float = 1, cls: str = "mk", label: str | None = None,
               grid=None) -> str:
    grid = grid or marker_grid(dict_name, mid)
    n = len(grid) + 2 * quiet
    lab = label or "%s id %d" % (dict_name, mid)
    return ('<svg class="%s" viewBox="0 0 %g %g" role="img" aria-label="%s"><title>%s</title>%s</svg>'
            % (cls, n, n, esc(lab), esc(lab), g_bits(grid, 0, 0, n, quiet)))


def _detect(img, dict_name):
    det = cv2.aruco.ArucoDetector(cvdict(dict_name), cv2.aruco.DetectorParameters())
    corners, ids, _ = det.detectMarkers(img)
    if ids is None or len(ids) == 0:
        return []
    return [(int(i), c.reshape(-1, 2)) for c, i in zip(corners, np.array(ids).flatten())]


def charuco_layout(sx: int, sy: int, dict_name: str, ratio: float = 0.75, ids=None,
                   legacy: bool | None = None) -> dict:
    """OpenCV draws the board; we read back which squares are black and which
    marker ID sits in which white square (by detecting OpenCV's own image),
    then verify every module of every marker against the drawn pixels."""
    d = cvdict(dict_name)
    args = [(sx, sy), 1.0, ratio, d]
    if ids is not None:
        args.append(np.array(ids, dtype=np.int32))
    board = cv2.aruco.CharucoBoard(*args)
    if legacy is not None:
        board.setLegacyPattern(bool(legacy))
    P, m = 120, 60
    img = board.generateImage((sx * P + 2 * m, sy * P + 2 * m), marginSize=m, borderBits=1)
    squares = [[int(img[m + r * P + 3, m + c * P + 3] < 128) for c in range(sx)] for r in range(sy)]
    found = _detect(img, dict_name)
    markers = []
    msz = ratio * P
    off = (P - msz) / 2
    for mid, cs in found:
        cx, cy = cs[:, 0].mean(), cs[:, 1].mean()
        col, row = int((cx - m) // P), int((cy - m) // P)
        grid = marker_grid(dict_name, mid)
        M = len(grid)
        bad = 0
        for r in range(M):
            for c in range(M):
                px = img[int(m + row * P + off + (r + 0.5) * msz / M), int(m + col * P + off + (c + 0.5) * msz / M)]
                bad += int((px < 128) != bool(grid[r][c]))
        if bad:
            raise RuntimeError("ChArUco readback mismatch: id %d, %d modules" % (mid, bad))
        markers.append((mid, col, row, grid))
    expected = len(board.getIds())
    if len(markers) != expected:
        raise RuntimeError("ChArUco readback found %d of %d markers" % (len(markers), expected))
    return {"sx": sx, "sy": sy, "ratio": ratio, "squares": squares, "dict": dict_name,
            "markers": sorted(markers), "legacy": board.getLegacyPattern()}


def g_charuco(lay: dict, x, y, sq, rotate=False, ink="#000") -> str:
    sx, sy = lay["sx"], lay["sy"]
    body = ['<rect width="%g" height="%g" fill="#fff"/>' % (sx, sy)]
    sqr = "".join('<rect x="%d" y="%d" width="1" height="1"/>' % (c, r)
                  for r, row in enumerate(lay["squares"]) for c, v in enumerate(row) if v)
    body.append('<g fill="%s">%s</g>' % (ink, sqr))
    off = (1 - lay["ratio"]) / 2
    for mid, col, row, grid in lay["markers"]:
        body.append(g_bits(grid, col + off, row + off, lay["ratio"], 0, bg=None, fg=ink)
                    .replace("<g ", '<g data-tip="id %d" ' % mid, 1))
    tr = "translate(%g %g) scale(%g)" % (x, y, sq)
    if rotate:  # draw a tall board lying on its side: rotate -90 about the origin
        tr = "translate(%g %g) rotate(-90) scale(%g)" % (x, y + sx * sq, sq)
    return '<g transform="%s" shape-rendering="crispEdges">%s</g>' % (tr, "".join(body))


def charuco_svg(lay: dict, sq: float = 20, pad: float = 0, rotate=False, cls="board", label="") -> str:
    sx, sy = lay["sx"], lay["sy"]
    w, h = (sy, sx) if rotate else (sx, sy)
    W, H = w * sq + 2 * pad, h * sq + 2 * pad
    lab = label or "ChArUco %dx%d %s" % (sx, sy, lay["dict"])
    return ('<svg class="%s" viewBox="0 0 %g %g" role="img" aria-label="%s"><title>%s</title>'
            '<rect width="%g" height="%g" fill="#fff"/>%s</svg>'
            % (cls, W, H, esc(lab), esc(lab), W, H, g_charuco(lay, pad, pad, sq, rotate)))


def gridboard_layout(cols, rows, dict_name, L=1.0, S=0.25, first=0) -> dict:
    d = cvdict(dict_name)
    ids = np.arange(first, first + cols * rows, dtype=np.int32)
    board = cv2.aruco.GridBoard((cols, rows), L, S, d, ids)
    P = 120
    Sp = int(S * P)
    W = cols * P + (cols - 1) * Sp
    H = rows * P + (rows - 1) * Sp
    m = 60
    img = board.generateImage((W + 2 * m, H + 2 * m), marginSize=m, borderBits=1)
    found = {mid: cs for mid, cs in _detect(img, dict_name)}
    markers = []
    for k, mid in enumerate(ids.tolist()):
        r, c = divmod(k, cols)
        x0, y0 = m + c * (P + Sp), m + r * (P + Sp)
        cs = found.get(mid)
        if cs is None or abs(cs[:, 0].min() - x0) > 3 or abs(cs[:, 1].min() - y0) > 3:
            raise RuntimeError("GridBoard readback: id %d not where expected" % mid)
        markers.append((mid, c, r, marker_grid(dict_name, mid)))
    return {"cols": cols, "rows": rows, "L": L, "S": S, "markers": markers, "dict": dict_name}


def gridboard_svg(lay, unit=40, pad=10, cls="board", label="") -> str:
    L, S = lay["L"], lay["S"]
    W = (lay["cols"] * L + (lay["cols"] - 1) * S) * unit + 2 * pad
    H = (lay["rows"] * L + (lay["rows"] - 1) * S) * unit + 2 * pad
    g = "".join(g_bits(grid, pad + c * (L + S) * unit, pad + r * (L + S) * unit, L * unit, 0, bg=None)
                .replace("<g ", '<g data-tip="id %d" ' % mid, 1)
                for mid, c, r, grid in lay["markers"])
    lab = label or "GridBoard %dx%d %s" % (lay["cols"], lay["rows"], lay["dict"])
    return ('<svg class="%s" viewBox="0 0 %g %g" role="img" aria-label="%s"><title>%s</title>'
            '<rect width="%g" height="%g" fill="#fff"/>%s</svg>' % (cls, W, H, esc(lab), esc(lab), W, H, g))


# ------------------------------------------------------- build-time checks

def checks() -> dict:
    """Things the docs claim or flag (verify) that OpenCV on this box can answer."""
    A = cv2.aruco
    out: dict = {"cv": cv2.__version__}
    pre = {}
    for n in (4, 5, 6, 7):
        big = cvdict("DICT_%dX%d_1000" % (n, n)).bytesList
        for N in (50, 100, 250):
            s = cvdict("DICT_%dX%d_%d" % (n, n, N)).bytesList
            pre["%dx%d_%d" % (n, n, N)] = bool(np.array_equal(s, big[:N]))
    out["prefix"] = pre
    corr = {}
    for n in (4, 5, 6, 7):
        for N in (50, 100, 250, 1000):
            corr["DICT_%dX%d_%d" % (n, n, N)] = int(cvdict("DICT_%dX%d_%d" % (n, n, N)).maxCorrectionBits)
    corr["DICT_APRILTAG_36h11"] = int(cvdict("DICT_APRILTAG_36h11").maxCorrectionBits)
    out["corr"] = corr

    def mind(name):
        d = cvdict(name)
        k = d.markerSize
        B = np.array([A.Dictionary.getBitsFromByteList(d.bytesList[i:i + 1], k).flatten()
                      for i in range(d.bytesList.shape[0])])
        best = 99
        for r in range(4):
            R = np.array([np.rot90(x.reshape(k, k), r).flatten() for x in B])
            D = (B[:, None, :] != R[None, :, :]).sum(-1)
            if r == 0:
                np.fill_diagonal(D, 99)
            best = min(best, int(D.min()))
        return best

    out["mindist"] = {N: mind("DICT_4X4_%d" % N) for N in (50, 100, 250, 1000)}

    # mirrored markers: what does the detector make of a decal that came out flipped?
    res = {"none": 0, "same": 0, "other": 0}
    same, other = [], []
    for i in range(50):
        mk = A.generateImageMarker(cvdict("DICT_4X4_50"), i, 120)
        im = np.full((200, 200), 255, np.uint8)
        im[40:160, 40:160] = mk
        got = _detect(np.fliplr(im).copy(), "DICT_4X4_50")
        if not got:
            res["none"] += 1
        elif got[0][0] == i:
            res["same"] += 1
            same.append(i)
        else:
            res["other"] += 1
            other.append((i, got[0][0]))
    out["mirror"] = res
    out["mirror_same"] = same
    out["mirror_other"] = other
    return out


def zmax(s_mm, M, theta_deg, p=5, hfov=69.0, width=1920):
    f = (width / 2) / math.tan(math.radians(hfov / 2))
    return f * (s_mm / 1000.0) * math.cos(math.radians(theta_deg)) / (M * p)


def tag36_byte_story(mid: int) -> dict:
    """36 data bits need 4.5 bytes. OpenCV's own byte list keeps the 5th byte's
    4 bits in its LOW nibble. Reading the HIGH nibble instead is one way to
    mis-unpack that byte (schematic of the failure class the doc names)."""
    d = cvdict("DICT_APRILTAG_36h11")
    full = marker_grid("DICT_APRILTAG_36h11", mid)
    data = [full[r][c] ^ 1 for r in range(1, 7) for c in range(1, 7)]  # 1 = white bit
    raw = d.bytesList[mid].flatten()[:5].tolist()   # rotation 0, as stored
    bits = "".join(format(b, "08b") for b in raw[:4]) + format(raw[4], "08b")[4:]
    assert bits == "".join(map(str, data)), "OpenCV byte-list readback mismatch"
    wrong = "".join(format(b, "08b") for b in raw[:4]) + format(raw[4], "08b")[:4]
    bad = [list(r) for r in full]
    for k in range(36):
        r, c = divmod(k, 6)
        bad[r + 1][c + 1] = 0 if wrong[k] == "1" else 1
    img = np.full((240, 240), 255, np.uint8)
    big = np.kron(np.array(bad), np.ones((20, 20), dtype=int))
    img[40:200, 40:200] = np.where(big == 1, 0, 255).astype(np.uint8)
    got = _detect(img, "DICT_APRILTAG_36h11")
    return {"id": mid, "bytes": raw, "bits": bits, "wrong": wrong, "good": full, "bad": bad,
            "diff": sum(1 for a, b in zip(bits, wrong) if a != b),
            "detect": [g[0] for g in got]}


# ==================================================================== chrome

CSS = r"""
:root{
  color-scheme: light;
  --bg:#f7f6f2; --surface:#ffffff; --surface-2:#f1efe9; --surface-3:#e9e6de;
  --line:#e2dfd6; --line-2:#cbc7bc; --ink:#1c1b19; --ink-2:#45433e; --muted:#6c6961;
  --acc:#2459b8; --acc-weak:#e7eefb; --good:#0f7a3b; --good-weak:#e2f3e7;
  --warn:#9a5a00; --warn-weak:#fcf0d6; --bad:#b3302d; --bad-weak:#fbe5e3;
  --violet:#5a43c0; --vio-weak:#eeebfb; --decide:#a34d06; --decide-bg:#fff6e6; --decide-line:#ec9f2e;
  --code-bg:#f0eee8;
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#eda100; --s5:#e87ba4;
  --sans:"Segoe UI Variable Text","Segoe UI",system-ui,-apple-system,Roboto,"Helvetica Neue",Arial,sans-serif;
  --mono:ui-monospace,"Cascadia Mono","Cascadia Code",Consolas,Menlo,monospace;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    color-scheme: dark;
    --bg:#121211; --surface:#1b1b19; --surface-2:#232320; --surface-3:#2c2b28;
    --line:#33322e; --line-2:#4a4843; --ink:#ecebe5; --ink-2:#c8c6bc; --muted:#9b988e;
    --acc:#7ba7f2; --acc-weak:#1b2a44; --good:#5cc786; --good-weak:#15301f;
    --warn:#e5a94d; --warn-weak:#382a12; --bad:#f07d78; --bad-weak:#3a1a19;
    --violet:#a89af0; --vio-weak:#262143; --decide:#f3b65a; --decide-bg:#2a2112; --decide-line:#b37a22;
    --code-bg:#252421;
    --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500; --s5:#d55181;
  }
}
:root[data-theme="dark"]{
    color-scheme: dark;
    --bg:#121211; --surface:#1b1b19; --surface-2:#232320; --surface-3:#2c2b28;
    --line:#33322e; --line-2:#4a4843; --ink:#ecebe5; --ink-2:#c8c6bc; --muted:#9b988e;
    --acc:#7ba7f2; --acc-weak:#1b2a44; --good:#5cc786; --good-weak:#15301f;
    --warn:#e5a94d; --warn-weak:#382a12; --bad:#f07d78; --bad-weak:#3a1a19;
    --violet:#a89af0; --vio-weak:#262143; --decide:#f3b65a; --decide-bg:#2a2112; --decide-line:#b37a22;
    --code-bg:#252421;
    --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500; --s5:#d55181;
}
*{box-sizing:border-box}
html{scroll-padding-top:72px; -webkit-text-size-adjust:100%}
body{margin:0; background:var(--bg); color:var(--ink); font:17px/1.62 var(--sans); overflow-x:hidden}
a{color:var(--acc); text-underline-offset:2px}
a:hover{text-decoration-thickness:2px}
code{font:0.86em/1.4 var(--mono); background:var(--code-bg); padding:.08em .35em; border-radius:4px;
     overflow-wrap:anywhere}
.topbar{position:sticky; top:0; z-index:50; background:color-mix(in srgb, var(--bg) 88%, transparent);
  backdrop-filter:blur(8px); border-bottom:1px solid var(--line)}
.topbar .in{max-width:1320px; margin:0 auto; padding:9px 16px; display:flex; gap:6px 16px;
  align-items:center; flex-wrap:wrap; font-size:14px}
.topbar .brand{font-weight:650; color:var(--ink); text-decoration:none; display:flex; gap:8px; align-items:center}
.topbar .brand svg{width:22px; height:22px; border-radius:3px}
.topbar nav{display:flex; gap:4px 14px; flex-wrap:wrap}
.topbar nav a{color:var(--muted); text-decoration:none; padding:2px 0; border-bottom:2px solid transparent}
.topbar nav a:hover{color:var(--ink)}
.topbar nav a.on{color:var(--ink); border-bottom-color:var(--acc)}
.topbar .sp{flex:1}
#themebtn{font:inherit; font-size:13px; background:var(--surface); color:var(--ink-2); border:1px solid var(--line-2);
  border-radius:999px; padding:3px 11px; cursor:pointer}
.wrap{max-width:1320px; margin:0 auto; padding:0 16px 120px}
.layout{display:block}
.toc{font-size:14.5px; margin:18px 0 8px}
.toc details{background:var(--surface); border:1px solid var(--line); border-radius:12px; padding:8px 14px}
.toc summary{cursor:pointer; font-weight:600; color:var(--ink-2); padding:4px 0}
.toc ol{list-style:none; margin:6px 0; padding:0}
.toc li{margin:0}
.toc a{display:block; color:var(--muted); text-decoration:none; padding:3px 0 3px 10px; border-left:2px solid transparent;
  line-height:1.35}
.toc a:hover{color:var(--ink)}
.toc a.on{color:var(--ink); border-left-color:var(--acc); font-weight:600}
.toc .l3 a{padding-left:24px; font-size:13.5px}
@media (min-width:1180px){
  .layout{display:grid; grid-template-columns:250px minmax(0,1fr); gap:52px}
  .toc{position:sticky; top:62px; align-self:start; max-height:calc(100vh - 78px); overflow:auto; margin-top:34px}
  .toc details{background:none; border:0; padding:0}
  .toc summary{list-style:none; font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted)}
  .toc summary::-webkit-details-marker{display:none}
}
main.content{min-width:0; max-width:1000px}
main.content > :is(p,ul,ol,blockquote,h2,h3,h4,.narrow){max-width:75ch}
.hero{padding:34px 0 8px}
.eyebrow{font-size:13px; letter-spacing:.09em; text-transform:uppercase; color:var(--muted); font-weight:600}
h1{font-size:clamp(30px,4.2vw,42px); line-height:1.12; margin:8px 0 14px; letter-spacing:-.01em}
h2{font-size:27px; line-height:1.2; margin:64px 0 14px; padding-top:18px; border-top:1px solid var(--line); letter-spacing:-.005em}
h3{font-size:20.5px; line-height:1.3; margin:40px 0 10px}
.lede{font-size:19.5px; line-height:1.55; color:var(--ink-2); max-width:70ch}
.meta{font-size:14px; color:var(--muted); margin:10px 0 0}
p{margin:12px 0}
ul,ol{padding-left:1.4em} li{margin:5px 0} li > ul, li > ol{margin:4px 0}
blockquote.quote{margin:18px 0; padding:14px 20px; border-left:4px solid var(--acc); background:var(--surface);
  border-radius:0 12px 12px 0; font-size:18.5px; color:var(--ink-2); font-style:italic}
blockquote.quote p{margin:4px 0}
.badge{display:inline-block; font:600 11.5px/1.3 var(--sans); letter-spacing:.02em; padding:1px 7px; border-radius:999px;
  vertical-align:1px; white-space:nowrap}
.badge.verify{color:var(--warn); background:var(--warn-weak); border:1px dashed var(--warn)}
.verdict{padding:1px 8px; border-radius:6px; font-weight:650}
.v-adopt{background:var(--good-weak); color:var(--good)}
.v-adapt{background:var(--acc-weak); color:var(--acc)}
.v-avoid{background:var(--bad-weak); color:var(--bad)}
.v-own{background:var(--vio-weak); color:var(--violet)}
.tablewrap{overflow-x:auto; margin:16px 0; border:1px solid var(--line); border-radius:12px; background:var(--surface)}
table.md{border-collapse:collapse; width:100%; font-size:15px; line-height:1.45}
table.md th{background:var(--surface-2); text-align:left; font-weight:620; color:var(--ink-2); padding:9px 12px;
  border-bottom:1px solid var(--line); vertical-align:bottom}
table.md td{padding:9px 12px; border-top:1px solid var(--line); vertical-align:top}
table.md tr:first-child td{border-top:0}
table.md td:first-child{font-weight:560}
pre.code{background:var(--code-bg); border:1px solid var(--line); border-radius:10px; padding:12px 14px; overflow:auto;
  font:13.5px/1.5 var(--mono); margin:14px 0}
pre.code code{background:none; padding:0; font:inherit}
pre.ascii{font-size:12.5px; line-height:1.32}
.formula{font:15px/1.6 var(--mono); background:var(--surface); border:1px solid var(--line); border-left:4px solid var(--violet);
  border-radius:10px; padding:12px 16px; overflow-x:auto; white-space:pre; margin:14px 0}
figure.fig{margin:26px 0; padding:0}
.figbody{background:var(--surface); border:1px solid var(--line); border-radius:14px; padding:16px; overflow-x:auto}
.figbody > svg{display:block; width:100%; height:auto}
.fig.wire .figbody{padding:10px; background:var(--surface-2)}
.fig.wire .figbody > svg{min-width:760px}
.fig.wide .figbody > svg{min-width:680px}
.fig.mid .figbody > svg{min-width:560px}
figcaption{font-size:14.5px; color:var(--muted); margin:9px 4px 0; line-height:1.5; max-width:90ch}
figcaption b{color:var(--ink-2)}
.kind{display:inline-block; font:650 11px/1.4 var(--sans); letter-spacing:.06em; text-transform:uppercase; padding:1px 7px;
  border-radius:5px; margin-right:4px; vertical-align:1px; background:var(--surface-3); color:var(--ink-2)}
.k-generated-by-opencv{background:var(--good-weak); color:var(--good)}
.k-schematic{background:var(--warn-weak); color:var(--warn)}
.k-illustrative{background:var(--warn-weak); color:var(--warn)}
.k-wireframe{background:var(--acc-weak); color:var(--acc)}
.k-build-time-check{background:var(--vio-weak); color:var(--violet)}
.k-from-the-doc{background:var(--acc-weak); color:var(--acc)}
.k-design-response{background:var(--vio-weak); color:var(--violet)}
.callout{border:1px solid var(--line); border-left:5px solid var(--acc); background:var(--surface); border-radius:12px;
  padding:14px 18px; margin:20px 0; max-width:none}
.callout p{margin:8px 0}
.co-title{font-weight:680; margin-bottom:6px; color:var(--ink)}
.co-note{border-left-color:var(--acc)}
.co-warn{border-left-color:var(--warn); background:var(--warn-weak)}
.co-good{border-left-color:var(--good)}
.co-check{border-left-color:var(--violet); border-style:dashed; border-left-style:solid}
.co-mini{font-size:15px; padding:9px 14px; margin:12px 0; border-left-width:4px; max-width:75ch}
.co-mini.decal{border-left-color:var(--decide-line); background:var(--decide-bg)}
.decisions{border:2px solid var(--decide-line); background:var(--decide-bg); border-radius:16px; padding:20px 22px 16px; margin:26px 0}
.decisions h2.dtitle, .decisions .dtitle{border:0; margin:0 0 4px; padding:0; font-size:24px; color:var(--decide)}
.decisions .dsub{color:var(--ink-2); margin:0 0 14px; font-size:15.5px}
.dlist{list-style:none; padding:0; margin:0; counter-reset:dec}
.dlist > li{counter-increment:dec; position:relative; padding:10px 12px 10px 50px; margin:8px 0; background:var(--surface);
  border:1px solid var(--line); border-radius:12px}
.dlist > li::before{content:counter(dec); position:absolute; left:12px; top:10px; width:26px; height:26px; border-radius:50%;
  background:var(--decide-line); color:#fff; font-weight:700; font-size:14px; display:grid; place-items:center}
.dlist .dh{font-weight:680}
.dlist .tags{margin-top:5px; display:flex; gap:6px; flex-wrap:wrap}
.qtag{font:600 12px/1.35 var(--sans); padding:2px 8px; border-radius:999px; border:1px solid var(--line-2); color:var(--ink-2); background:var(--surface-2)}
.qtag.no{border-color:var(--bad); color:var(--bad); background:var(--bad-weak)}
.qtag.yes{border-color:var(--good); color:var(--good); background:var(--good-weak)}
.dgroup{margin:16px 0 4px; font-weight:700; font-size:14px; letter-spacing:.06em; text-transform:uppercase; color:var(--decide)}
.response{border:1px dashed var(--violet); background:var(--vio-weak); border-radius:12px; padding:12px 16px; margin:12px 0}
.response .rh{font-weight:700; color:var(--violet); font-size:14px; letter-spacing:.04em; text-transform:uppercase}
.status{display:flex; gap:10px; align-items:flex-start; margin:14px 0 4px; font-size:15.5px}
.status .dot{width:10px; height:10px; border-radius:50%; background:var(--good); margin-top:8px; flex:none;
  box-shadow:0 0 0 4px var(--good-weak)}
.dfigs{display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr)); gap:14px; margin-top:16px}
.dfigs figure.fig{margin:0}
.cards{display:grid; grid-template-columns:repeat(auto-fill,minmax(min(100%,270px),1fr)); gap:14px; margin:18px 0}
.card{background:var(--surface); border:1px solid var(--line); border-radius:14px; padding:14px 16px; min-width:0}
.card h4{margin:0 0 6px; font-size:16.5px}
.card p{margin:6px 0; font-size:15px; color:var(--ink-2)}
.card .tags{display:flex; gap:6px; flex-wrap:wrap; margin-top:8px}
.stats{display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin:22px 0}
.stat{background:var(--surface); border:1px solid var(--line); border-radius:12px; padding:12px 14px}
.stat b{display:block; font-size:28px; line-height:1.1; letter-spacing:-.01em}
.stat span{font-size:13.5px; color:var(--muted); line-height:1.35; display:block; margin-top:4px}
details.src{margin:10px 0 18px; border:1px solid var(--line); border-radius:10px; background:var(--surface); max-width:none}
details.src > summary{cursor:pointer; padding:7px 12px; font-size:14px; color:var(--muted)}
details.src[open] > summary{border-bottom:1px solid var(--line)}
details.src .srcbody{padding:4px 16px 8px}
details.src pre.code{margin:8px 10px; border:0}
.mkrow{display:flex; flex-wrap:wrap; gap:14px 18px; align-items:flex-end}
.mkrow figure{margin:0; text-align:center}
.mkrow figcaption{margin:5px 0 0; font:12.5px/1.3 var(--mono); color:var(--muted)}
svg.mk{width:88px; height:88px; display:block; border-radius:3px; box-shadow:0 0 0 1px var(--line)}
svg.mk.sm{width:56px; height:56px}
svg.mk.lg{width:150px; height:150px}
svg.board{display:block; height:auto; box-shadow:0 0 0 1px var(--line); border-radius:2px}
.dg text{font-family:var(--sans); fill:var(--ink); font-size:13px}
.dg .t-mut{fill:var(--muted)} .dg .t-ink2{fill:var(--ink-2)}
.dg .t-mono{font-family:var(--mono); font-size:12px}
.dg .t-b{font-weight:650}
.dg .t-acc{fill:var(--acc)} .dg .t-good{fill:var(--good)} .dg .t-bad{fill:var(--bad)} .dg .t-warn{fill:var(--warn)}
.dg .t-vio{fill:var(--violet)} .dg .t-dec{fill:var(--decide)}
.dg .box{fill:var(--surface); stroke:var(--line-2); stroke-width:1.2}
.dg .box2{fill:var(--surface-2); stroke:var(--line-2); stroke-width:1}
.dg .box3{fill:var(--surface-3); stroke:none}
.dg .b-acc{fill:var(--acc-weak); stroke:var(--acc); stroke-width:1.2}
.dg .b-good{fill:var(--good-weak); stroke:var(--good); stroke-width:1.2}
.dg .b-bad{fill:var(--bad-weak); stroke:var(--bad); stroke-width:1.2}
.dg .b-warn{fill:var(--warn-weak); stroke:var(--warn); stroke-width:1.2}
.dg .b-vio{fill:var(--vio-weak); stroke:var(--violet); stroke-width:1.2}
.dg .b-dec{fill:var(--decide-bg); stroke:var(--decide-line); stroke-width:1.4}
.dg .edge{stroke:var(--ink-2); stroke-width:1.4; fill:none}
.dg .e-mut{stroke:var(--line-2)}
.dg .e-bad{stroke:var(--bad)} .dg .e-good{stroke:var(--good)} .dg .e-acc{stroke:var(--acc)} .dg .e-vio{stroke:var(--violet)}
.dg .dash{stroke-dasharray:5 4}
.dg .ahf{fill:var(--ink-2)} .dg .ahf-bad{fill:var(--bad)} .dg .ahf-good{fill:var(--good)}
.dg .f-s1{fill:var(--s1)} .dg .f-s2{fill:var(--s2)} .dg .f-s3{fill:var(--s3)} .dg .f-s4{fill:var(--s4)} .dg .f-s5{fill:var(--s5)}
.dg .st-s1{stroke:var(--s1)} .dg .st-s2{stroke:var(--s2)} .dg .st-s3{stroke:var(--s3)} .dg .st-s4{stroke:var(--s4)} .dg .st-s5{stroke:var(--s5)}
.dg .grid{stroke:var(--line); stroke-width:1}
.dg .axis{stroke:var(--line-2); stroke-width:1}
.dg .band{fill:var(--acc-weak); opacity:.7}
.dg [data-tip]{cursor:default}
.dg .lane{fill:var(--surface-2)}
/* feature matrix */
.matrix{border-collapse:separate; border-spacing:0; font-size:13.5px; min-width:900px; width:100%}
.matrix th{position:sticky; top:0; background:var(--surface-2); font-weight:600; color:var(--ink-2); padding:8px 6px;
  border-bottom:1px solid var(--line); text-align:center; vertical-align:bottom; line-height:1.25}
.matrix th.tool{text-align:left; min-width:210px}
.matrix td{padding:5px 6px; border-top:1px solid var(--line); text-align:center; vertical-align:middle}
.matrix td.tool{text-align:left; line-height:1.3}
.matrix td.tool .plat{display:block; font-size:12px; color:var(--muted)}
.matrix tr.fusion td.tool{box-shadow:inset 3px 0 0 var(--acc)}
.matrix tr.wish td{background:var(--decide-bg); border-top:2px solid var(--decide-line)}
.cell{display:inline-grid; place-items:center; width:26px; height:26px; border-radius:7px; font-weight:700; font-size:14px}
.c-y{background:var(--good-weak); color:var(--good)}
.c-p{background:var(--warn-weak); color:var(--warn)}
.c-n{background:var(--surface-2); color:var(--muted); font-weight:500}
.c-q{background:transparent; color:var(--muted); border:1px dashed var(--line-2); font-weight:500}
.c-w{background:var(--decide-line); color:#fff}
.price{font:12.5px var(--mono); color:var(--ink-2); white-space:nowrap}
.legend{display:flex; flex-wrap:wrap; gap:8px 18px; font-size:14px; color:var(--ink-2); margin:10px 2px 0; align-items:center}
.legend .cell{width:22px; height:22px; font-size:12px; margin-right:6px; vertical-align:middle}
.legend i{display:inline-block; width:14px; height:14px; border-radius:4px; vertical-align:-2px; margin-right:6px}
/* score table */
.scores{width:100%; border-collapse:collapse; font-size:14px; min-width:860px}
.scores th{text-align:left; padding:8px; color:var(--ink-2); font-weight:620; border-bottom:1px solid var(--line); vertical-align:bottom}
.scores td{padding:8px; border-top:1px solid var(--line); vertical-align:top; width:17%}
.scores td.crit{width:15%; font-weight:600}
.sb{display:flex; align-items:center; gap:8px}
.sb .track{flex:1; height:9px; border-radius:5px; background:var(--surface-3); overflow:hidden}
.sb .fill{height:100%; border-radius:5px}
.sb .num{font:700 15px var(--sans); width:14px; text-align:right}
.sb .num.top{color:var(--good)}
.scores .note{font-size:12.5px; color:var(--muted); margin-top:3px; line-height:1.35}
.scores tr.total td{border-top:2px solid var(--line-2); font-weight:700}
.chip{display:inline-block; font:600 12.5px/1.35 var(--sans); padding:2px 9px; border-radius:999px; background:var(--surface-2);
  border:1px solid var(--line); color:var(--ink-2); margin:2px 3px 2px 0}
.chip.s1{border-color:var(--s1)} .chip.s2{border-color:var(--s2)} .chip.s3{border-color:var(--s3)} .chip.s4{border-color:var(--s4)} .chip.s5{border-color:var(--s5)}
.swatch{display:inline-block; width:11px; height:11px; border-radius:3px; margin-right:6px; vertical-align:0}
.steps{display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,190px),1fr)); gap:10px; counter-reset:st; margin:16px 0;
  padding:0; list-style:none}
.steps li{counter-increment:st; background:var(--surface); border:1px solid var(--line); border-radius:12px; padding:12px 14px 12px;
  position:relative; margin:0; font-size:14.5px}
.steps li::before{content:counter(st); display:inline-grid; place-items:center; width:22px; height:22px; border-radius:50%;
  background:var(--acc); color:#fff; font-size:12px; font-weight:700; margin-bottom:6px}
.steps li b{display:block; font-size:15px}
.steps code{font-size:.78em}
.qlist{list-style:none; padding:0; margin:14px 0; counter-reset:q}
.qlist > li{counter-increment:q; background:var(--surface); border:1px solid var(--line); border-radius:12px; padding:12px 16px 12px 56px;
  position:relative; margin:10px 0}
.qlist > li::before{content:"Q" counter(q); position:absolute; left:14px; top:12px; font-weight:800; color:var(--muted)}
.qlist .qd{font-size:14.5px; color:var(--ink-2); margin-top:4px}
.qlist .qs{margin-top:8px; display:flex; gap:8px; flex-wrap:wrap; align-items:center; font-size:14px}
.reports{display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,290px),1fr)); gap:16px; margin:22px 0}
.feature{display:grid; grid-template-columns:auto minmax(0,1fr); gap:18px 22px; align-items:center; margin:26px 0 8px;
  padding:18px 20px; background:var(--surface); border:2px solid var(--acc); border-radius:18px; text-decoration:none;
  color:inherit; transition:transform .15s}
.feature:hover{transform:translateY(-2px)}
.feature .fthumb{display:grid; grid-template-columns:repeat(6,34px); gap:5px}
.feature .fthumb svg{width:34px; height:34px; display:block; border-radius:2px; box-shadow:0 0 0 1px var(--line)}
.feature .fe{font-size:12.5px; letter-spacing:.08em; text-transform:uppercase; color:var(--acc); font-weight:700}
.feature h3{margin:4px 0 6px; font-size:22px}
.feature p{margin:0; color:var(--ink-2); font-size:15.5px}
.feature .go{display:inline-block; margin-top:10px; font-weight:650; color:var(--acc)}
@media (max-width:640px){ .feature{grid-template-columns:1fr} }
.rcard{display:flex; flex-direction:column; background:var(--surface); border:1px solid var(--line); border-radius:16px;
  overflow:hidden; text-decoration:none; color:inherit; transition:border-color .15s, transform .15s}
.rcard:hover{border-color:var(--acc); transform:translateY(-2px)}
.rcard .thumb{background:var(--surface-2); padding:14px; display:flex; gap:8px; align-items:center; justify-content:center; min-height:140px}
.rcard .rb{padding:14px 16px 16px}
.rcard h3{margin:0 0 6px; font-size:19px}
.rcard p{margin:0; font-size:15px; color:var(--ink-2)}
.rcard .rm{font-size:13px; color:var(--muted); margin-top:10px}
.toggle{display:inline-flex; border:1px solid var(--line-2); border-radius:999px; overflow:hidden; margin:0 0 10px}
.toggle button{font:inherit; font-size:13.5px; border:0; background:var(--surface); color:var(--ink-2); padding:4px 14px; cursor:pointer}
.toggle button.on{background:var(--acc); color:#fff}
.hide{display:none !important}
.tip{position:fixed; z-index:99; pointer-events:none; background:var(--ink); color:var(--bg); font:13px/1.35 var(--sans);
  padding:5px 9px; border-radius:7px; max-width:320px; opacity:0; transition:opacity .08s}
.twocol{display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr)); gap:16px; align-items:start}
.foot{margin-top:70px; padding-top:16px; border-top:1px solid var(--line); font-size:14px; color:var(--muted)}
.foot code{font-size:12.5px}
@media (max-width:640px){
  body{font-size:16.5px}
  h2{font-size:23px; margin-top:48px}
  .lede{font-size:18px}
  .decisions{padding:16px 14px 12px}
  .dlist > li{padding-left:44px}
}
@media print{ .topbar,.toc{display:none} .figbody{break-inside:avoid} }
"""

JS = r"""
(function(){
  var root=document.documentElement, btn=document.getElementById('themebtn');
  var modes=['auto','light','dark'], cur='auto';
  try{ cur=localStorage.getItem('aruco-theme')||'auto'; }catch(e){}
  function apply(m){ cur=m; if(m==='auto') root.removeAttribute('data-theme'); else root.setAttribute('data-theme',m);
    if(btn) btn.textContent='Theme: '+m; try{ localStorage.setItem('aruco-theme',m);}catch(e){} }
  apply(cur);
  if(btn) btn.addEventListener('click',function(){ apply(modes[(modes.indexOf(cur)+1)%3]); });
  // TOC: open on wide screens, scrollspy
  var det=document.querySelector('.toc details'), mq=window.matchMedia('(min-width:1180px)');
  function syncToc(){ if(det && mq.matches) det.open=true; }
  syncToc(); if(mq.addEventListener) mq.addEventListener('change',syncToc);
  var links={}; document.querySelectorAll('.toc a[href^="#"]').forEach(function(a){ links[a.getAttribute('href').slice(1)]=a; });
  var heads=[].slice.call(document.querySelectorAll('main h2[id], main h3[id]'));
  if('IntersectionObserver' in window && heads.length){
    var vis={};
    var io=new IntersectionObserver(function(es){
      es.forEach(function(e){ vis[e.target.id]=e.isIntersecting; });
      var top=null; for(var i=0;i<heads.length;i++){ if(vis[heads[i].id]){ top=heads[i].id; break; } }
      if(!top){ var y=window.scrollY+90; for(var j=0;j<heads.length;j++){ if(heads[j].offsetTop<=y) top=heads[j].id; } }
      if(top){ for(var k in links) links[k].classList.toggle('on',k===top); }
    },{rootMargin:'-60px 0px -60% 0px'});
    heads.forEach(function(h){ io.observe(h); });
  }
  // variant toggles
  document.querySelectorAll('[data-toggle]').forEach(function(group){
    var name=group.getAttribute('data-toggle');
    group.querySelectorAll('button').forEach(function(b){
      b.addEventListener('click',function(){
        group.querySelectorAll('button').forEach(function(x){ x.classList.toggle('on',x===b); });
        document.querySelectorAll('[data-variant-of="'+name+'"]').forEach(function(el){
          el.classList.toggle('hide', el.getAttribute('data-variant')!==b.getAttribute('data-v')); });
      });
    });
  });
  // hover tips for anything with data-tip
  var tip=document.createElement('div'); tip.className='tip'; document.body.appendChild(tip);
  document.addEventListener('mousemove',function(e){
    var t=e.target.closest ? e.target.closest('[data-tip]') : null;
    if(!t){ tip.style.opacity=0; return; }
    tip.textContent=t.getAttribute('data-tip'); tip.style.opacity=1;
    var x=e.clientX+14, y=e.clientY+14, w=tip.offsetWidth, h=tip.offsetHeight;
    if(x+w>window.innerWidth-8) x=e.clientX-w-10; if(y+h>window.innerHeight-8) y=e.clientY-h-10;
    tip.style.left=x+'px'; tip.style.top=y+'px';
  });
})();
"""

BRAND_GRID = None  # filled in main(): a tiny real marker for the top bar


def toc_html(toc) -> str:
    items = "".join('<li class="l%d"><a href="#%s">%s</a></li>' % (lvl, sid, inline(t)) for lvl, sid, t in toc)
    return '<nav class="toc" aria-label="Contents"><details><summary>Contents</summary><ol>%s</ol></details></nav>' % items


def page_html(key: str, title: str, eyebrow: str, lede: str, meta: str, body: str, toc, desc: str) -> str:
    nav = "".join('<a href="%s"%s>%s</a>' % (fn, ' class="on" aria-current="page"' if k == key else "", lab)
                  for k, fn, lab in PAGES)
    brand = marker_svg("DICT_4X4_50", 7, quiet=0.6, cls="", label="ArUco DICT_4X4_50 id 7")
    tocb = toc_html(toc) if toc else ""
    layout_cls = "layout" if toc else "layout notoc"
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%s</title>
<meta name="description" content="%s">
<style>%s</style></head>
<body>
<header class="topbar"><div class="in">
<a class="brand" href="index.html">%s<span>ArUco in Fusion</span></a>
<nav aria-label="Reports">%s</nav><span class="sp"></span>
<button id="themebtn" type="button" aria-label="Switch colour theme">Theme: auto</button>
</div></header>
<div class="wrap"><div class="%s">
%s
<main class="content">
<div class="hero"><div class="eyebrow">%s</div><h1>%s</h1><p class="lede">%s</p><p class="meta">%s</p></div>
%s
<div class="foot">Generated %s by <code>docs/build_aruco_reports.py</code> from the Markdown in <code>docs/</code>.
Markers, boards and bit grids are drawn from OpenCV %s's own tables at build time and inlined as SVG; nothing on this page loads from the network.</div>
</main></div></div>
<script>%s</script>
</body></html>""" % (esc(title), esc(desc), CSS, brand, nav, layout_cls, tocb, eyebrow, esc(title), lede, meta,
                     body, BUILT, cv2.__version__, JS)


# ================================================= decisions (2026-10-07)
# Paraphrased from Zach's messages of 2026-10-07; the design response is the
# orchestrator's, and is labelled as such everywhere it appears.

ACT_DICT = "DICT_4X4_100"
ACT_IDS = list(range(21, 27))   # illustrative: the first free range after L1 0-7 and L2 8-20


def fig_six_copies(pid="six") -> str:
    W, H = 960, 382
    o = [arrow_defs(pid)]
    # component definition card
    o.append('<rect x="14" y="34" width="214" height="236" rx="12" class="box2"/>')
    o.append(svg_text(28, 58, "Actuator", cls="t-b", size=15))
    o.append(svg_text(28, 76, "component definition", cls="t-mut", size=12))
    o.append('<rect x="66" y="96" width="110" height="138" rx="14" class="box"/>')
    o.append('<circle cx="121" cy="112" r="8" class="box2"/>')
    o.append('<rect x="93" y="138" width="56" height="56" rx="3" fill="none" class="edge e-acc dash"/>')
    o.append(svg_text(121, 170, "tag slot", cls="t-acc t-b", size=11.5, anchor="middle"))
    o.append(svg_text(28, 254, "slot geometry lives here, once", cls="t-mut", size=12))
    o.append('<path d="M232 152 C262 152 262 152 282 152" class="edge" marker-end="url(#%s-ah)"/>' % pid)
    o.append(svg_text(236, 140, "6 copies", cls="t-mut", size=12))
    # the arm: six copies, each with its own ID (one tag part per ID, snapped to the slot)
    o.append('<rect x="290" y="20" width="656" height="262" rx="12" class="box"/>')
    o.append(svg_text(304, 42, "Arm (sub-assembly): each copy gets its own ID, like a serial number",
                      cls="t-b", size=14))
    x0 = 304
    for k, mid in enumerate(ACT_IDS):
        x = x0 + k * 106
        o.append('<rect x="%d" y="64" width="92" height="132" rx="13" class="box2"/>' % x)
        o.append('<circle cx="%d" cy="80" r="7" class="box"/>' % (x + 46))
        o.append(g_bits(marker_grid(ACT_DICT, mid), x + 16, 98, 60, 0.6,
                        extra=' data-tip="Actuator:%d, tag id %d (%s)"' % (k + 1, mid, ACT_DICT)))
        o.append(svg_text(x + 46, 212, "Actuator:%d" % (k + 1), size=12.5, anchor="middle", cls="t-b"))
        o.append(svg_text(x + 46, 229, "id %d" % mid, size=12.5, anchor="middle", cls="t-acc t-mono"))
        if k < 5:
            o.append('<line x1="%d" y1="130" x2="%d" y2="130" class="edge e-mut"/>' % (x + 92, x + 106))
    o.append(svg_text(304, 256, "ID: overridden per copy at the Arm level, with no new model.", cls="t-ink2", size=12.5))
    o.append(svg_text(304, 273, "Geometry: inherited from the component, never overridden.", cls="t-ink2", size=12.5))
    # what is ruled out
    o.append('<rect x="14" y="296" width="932" height="76" rx="12" class="b-bad"/>')
    o.append(svg_text(30, 322, "Ruled out (decision 1)", cls="t-bad t-b", size=13.5))
    o.append(svg_text(30, 342, "Fusion showing copy #1's", cls="t-ink2", size=12.5))
    o.append(svg_text(30, 359, "image on every copy", cls="t-ink2", size=12.5))
    for k in range(6):
        x = 250 + k * 70
        o.append(g_bits(marker_grid(ACT_DICT, ACT_IDS[0]), x, 306, 56, 0.6,
                        extra=' opacity="0.55" data-tip="copy %d would show id %d: not acceptable"' % (k + 1, ACT_IDS[0])))
    o.append('<line x1="240" y1="364" x2="666" y2="312" stroke-width="3" class="edge e-bad"/>')
    o.append(svg_text(688, 330, "every copy reads id %d" % ACT_IDS[0], cls="t-bad t-b", size=13))
    o.append(svg_text(688, 350, "a detector would get a confident pose", cls="t-ink2", size=12))
    o.append(svg_text(688, 366, "for the wrong copy (doc, section 9)", cls="t-ink2", size=12))
    need(SRC_TEXT["prop"], "it reports a confident pose for the wrong copy")
    return svg("".join(o), W, H, "Six copies of an actuator, each with its own marker ID")


def fig_two_body_tag(pid="tile") -> str:
    """Exploded isometric of the design response: white tile + black-cells body inlaid flush."""
    grid = marker_grid("DICT_4X4_50", 3)       # 6x6 incl. border
    N = 8                                       # tile = marker + 1 module of white each side
    k = 17.0
    cos30, sin30 = math.cos(math.radians(30)), 0.5

    def P(x, y, z, ox, oy):
        return (ox + (x - y) * cos30 * k, oy + (x + y) * sin30 * k - z * k)

    def poly(pts, cls="", fill=None, stroke=None, extra=""):
        a = ' class="%s"' % cls if cls else ""
        f = ' fill="%s"' % fill if fill else ""
        s = ' stroke="%s" stroke-width="0.6"' % stroke if stroke else ""
        return '<polygon points="%s"%s%s%s%s/>' % (" ".join("%.1f,%.1f" % p for p in pts), a, f, s, extra)

    def tile(ox, oy, pockets=True):
        t = 1.4
        o = []
        o.append(poly([P(N, 0, 0, ox, oy), P(N, N, 0, ox, oy), P(N, N, t, ox, oy), P(N, 0, t, ox, oy)], fill="#d9d6cf", stroke="#8d8a83"))
        o.append(poly([P(0, N, 0, ox, oy), P(N, N, 0, ox, oy), P(N, N, t, ox, oy), P(0, N, t, ox, oy)], fill="#ebe9e3", stroke="#8d8a83"))
        o.append(poly([P(0, 0, t, ox, oy), P(N, 0, t, ox, oy), P(N, N, t, ox, oy), P(0, N, t, ox, oy)], fill="#ffffff", stroke="#8d8a83"))
        for r in range(6):
            for c in range(6):
                if grid[r][c]:
                    x, y = c + 1, r + 1
                    fill = "#b9b5ad" if pockets else "#111"
                    o.append(poly([P(x, y, t, ox, oy), P(x + 1, y, t, ox, oy), P(x + 1, y + 1, t, ox, oy), P(x, y + 1, t, ox, oy)], fill=fill))
        return "".join(o)

    def cells(ox, oy, z0, h=0.7):
        o = []
        order = sorted(((r, c) for r in range(6) for c in range(6) if grid[r][c]), key=lambda rc: rc[0] + rc[1])
        for r, c in order:
            x, y = c + 1, r + 1
            o.append(poly([P(x + 1, y, z0, ox, oy), P(x + 1, y + 1, z0, ox, oy), P(x + 1, y + 1, z0 + h, ox, oy), P(x + 1, y, z0 + h, ox, oy)], fill="#3a3a3a"))
            o.append(poly([P(x, y + 1, z0, ox, oy), P(x + 1, y + 1, z0, ox, oy), P(x + 1, y + 1, z0 + h, ox, oy), P(x, y + 1, z0 + h, ox, oy)], fill="#555"))
            o.append(poly([P(x, y, z0 + h, ox, oy), P(x + 1, y, z0 + h, ox, oy), P(x + 1, y + 1, z0 + h, ox, oy), P(x, y + 1, z0 + h, ox, oy)], fill="#111"))
        return "".join(o)

    W, H = 470, 330
    ox, oy = 160, 118
    o = [arrow_defs(pid), '<rect width="%d" height="%d" rx="10" class="box3" opacity=".5"/>' % (W, H)]
    o.append(tile(ox, oy + 30))
    o.append(cells(ox, oy + 30, 5.2))
    o.append('<path d="M%.0f %.0f L%.0f %.0f" class="edge" marker-end="url(#%s-ah)"/>' % (ox + 120, 64, ox + 120, 112, pid))
    o.append(svg_text(14, 24, "body 2: black cells", cls="t-b", size=13))
    o.append(svg_text(14, 41, "inlaid flush into the tile", cls="t-mut", size=12))
    o.append(svg_text(14, 304, "body 1: white tile, pockets", cls="t-b", size=13))
    o.append(svg_text(14, 321, "where the black cells sit", cls="t-mut", size=12))
    # assembled, small, at right
    k2 = 0.55
    sx, sy = 372, 196

    def Q(x, y, z):
        return (sx + (x - y) * cos30 * k * k2, sy + (x + y) * sin30 * k * k2 - z * k * k2)
    t = 1.4
    o.append(poly([Q(N, 0, 0), Q(N, N, 0), Q(N, N, t), Q(N, 0, t)], fill="#d9d6cf", stroke="#8d8a83"))
    o.append(poly([Q(0, N, 0), Q(N, N, 0), Q(N, N, t), Q(0, N, t)], fill="#ebe9e3", stroke="#8d8a83"))
    o.append(poly([Q(0, 0, t), Q(N, 0, t), Q(N, N, t), Q(0, N, t)], fill="#fff", stroke="#8d8a83"))
    for r in range(6):
        for c in range(6):
            if grid[r][c]:
                x, y = c + 1, r + 1
                o.append(poly([Q(x, y, t), Q(x + 1, y, t), Q(x + 1, y + 1, t), Q(x, y + 1, t)], fill="#111"))
    o.append(svg_text(372, 300, "assembled: flush top face", cls="t-mut", size=12, anchor="middle"))
    o.append(svg_text(372, 317, "two bodies, two colours", cls="t-mut", size=12, anchor="middle"))
    return svg("".join(o), W, H, "Two-body 3D-printed tag: white tile and black cells inlaid flush")


def fig_extrude_flow(pid="ext") -> str:
    W, H = 960, 372
    o = [arrow_defs(pid)]
    mk = marker_grid("DICT_4X4_50", 3)

    def step(x, y, w, h, title, sub=""):
        r = ['<rect x="%d" y="%d" width="%d" height="%d" rx="12" class="box"/>' % (x, y, w, h),
             svg_text(x + 12, y + 20, title, cls="t-b", size=13)]
        if sub:
            r.append(svg_text(x + 12, y + 37, sub, cls="t-mut", size=11.5, width=34))
        return "".join(r)

    def arr(x1, y, x2):
        return '<line x1="%d" y1="%d" x2="%d" y2="%d" class="edge" marker-end="url(#%s-ah)"/>' % (x1, y, x2, y, pid)

    # row 1: rectangle -> ArUco extrude
    o.append(svg_text(14, 22, "Path 1: draw a rectangle, then ArUco extrude it", cls="t-b t-dec", size=13.5))
    o.append(step(14, 34, 190, 118, "Sketch rectangle"))
    o.append('<rect x="56" y="70" width="104" height="64" fill="none" stroke="#1f6fd1" stroke-width="2"/>')
    for (cx, cy) in ((56, 70), (160, 70), (56, 134), (160, 134)):
        o.append('<rect x="%d" y="%d" width="5" height="5" fill="#1f6fd1"/>' % (cx - 2, cy - 2))
    o.append(arr(208, 93, 250))
    o.append(step(254, 34, 210, 118, "ArUco Extrude", "instead of a normal extrude"))
    o.append('<rect x="296" y="88" width="126" height="26" rx="5" class="b-acc"/>')
    o.append(svg_text(359, 106, "dictionary, ID, depth", cls="t-acc", size=11.5, anchor="middle"))
    o.append(arr(468, 93, 510))
    o.append(step(514, 34, 196, 118, "Tag body with pattern"))
    o.append('<rect x="560" y="60" width="96" height="84" rx="3" fill="#e9e6de" stroke="#8d8a83"/>')
    o.append(g_bits(mk, 573, 66, 70, 0.5))
    # row 2: body -> convert face
    o.append(svg_text(14, 186, "Path 2: extrude a body normally, then convert one of its faces", cls="t-b t-dec", size=13.5))
    o.append(step(14, 198, 190, 118, "Extruded body"))
    o.append('<polygon points="54,262 130,262 160,240 84,240" fill="#e9e6de" stroke="#8d8a83"/>')
    o.append('<polygon points="54,262 130,262 130,298 54,298" fill="#d9d6cf" stroke="#8d8a83"/>')
    o.append('<polygon points="130,262 160,240 160,276 130,298" fill="#c9c6bf" stroke="#8d8a83"/>')
    o.append(arr(208, 257, 250))
    o.append(step(254, 198, 210, 118, "Convert face to ArUco", "the add-in detects the face's dimensions"))
    o.append('<polygon points="300,290 380,290 410,268 330,268" fill="#cfe6fb" stroke="#1f6fd1" stroke-width="1.6"/>')
    o.append(svg_text(355, 306, "w x h detected", cls="t-acc t-mono", size=11, anchor="middle"))
    o.append(arr(468, 257, 510))
    o.append(step(514, 198, 196, 118, "Face becomes a tag"))
    o.append('<polygon points="552,262 640,262 674,240 586,240" fill="#ffffff" stroke="#8d8a83"/>')
    o.append('<polygon points="552,262 640,262 640,298 552,298" fill="#d9d6cf" stroke="#8d8a83"/>')
    o.append('<polygon points="640,262 674,240 674,276 640,298" fill="#c9c6bf" stroke="#8d8a83"/>')
    # the marker on the top face: unit square -> parallelogram, image top row at the back edge
    o.append('<g transform="matrix(88 0 -34 22 586 240)">%s</g>' % g_bits(mk, 0, 0, 1, 0.8))
    # timeline: the step lives in the timeline and regenerates in place
    o.append('<rect x="730" y="34" width="216" height="282" rx="12" class="b-dec"/>')
    o.append(svg_text(744, 56, "In the timeline", cls="t-b t-dec", size=13.5))
    tl = [("Sketch1", "box2"), ("ArUco Extrude", "b-acc"), ("Hole1", "box2")]
    for k, (lab, cls) in enumerate(tl):
        y = 74 + k * 46
        o.append('<rect x="752" y="%d" width="172" height="34" rx="7" class="%s"/>' % (y, cls))
        o.append(svg_text(838, y + 22, lab, size=12.5, anchor="middle", cls="t-b" if k == 1 else ""))
    o.append(svg_text(744, 232, "Edit the rectangle: the ArUco", cls="t-ink2", size=12))
    o.append(svg_text(744, 249, "step regenerates in place, and", cls="t-ink2", size=12))
    o.append(svg_text(744, 266, "later features (a hole) keep", cls="t-ink2", size=12))
    o.append(svg_text(744, 283, "working.", cls="t-ink2", size=12))
    o.append(svg_text(744, 305, "Test: pattern visible in URDF.", cls="t-b t-good", size=12))
    o.append('<rect x="14" y="334" width="932" height="30" rx="8" class="box2"/>')
    o.append(svg_text(26, 354, "The tag part can carry other features too, so the ArUco step cannot be a one-shot: it must sit in the timeline.",
                      cls="t-ink2", size=12.5))
    return svg("".join(o), W, H, "ArUco extrude: two ways in, one timeline step that regenerates in place")


def sheet_layout(page_w, page_h, items, margin=10.0, gap=6.0, label_h=5.0):
    """Column packing, tallest first. Returns [(item, x, y)] or raises if it does not fit."""
    items = sorted(items, key=lambda it: -it["h"])
    cols, cur, x, y, colw = [], [], margin, margin, 0.0
    placed = []
    for it in items:
        need_h = it["h"] + label_h
        if y + need_h > page_h - margin and cur:
            x += colw + gap
            y, colw, cur = margin, 0.0, []
        placed.append((it, x, y))
        cur.append(it)
        y += need_h + gap
        colw = max(colw, it["w"])
    if x + colw > page_w - margin + 1e-6:
        raise RuntimeError("sheet does not fit")
    return placed


def fig_print_sheet() -> str:
    L1 = charuco_layout(2, 8, ACT_DICT, 0.748, ids=list(range(0, 8)))
    L2 = charuco_layout(2, 13, ACT_DICT, 0.737, ids=list(range(8, 21)))
    need(SRC_TEXT["prop"], "rect 50.8 x 203.2", "L2 2x13 8-20", "L1 2x8 ids0-7", "2 x 13, 19/14 mm",
         "His L1 (0.748) and L2 (0.737) pass")
    items = [{"kind": "board", "lay": L1, "sq": 25.4, "w": 50.8, "h": 203.2, "lab": "L1 2x8 ids 0-7"},
             {"kind": "board", "lay": L2, "sq": 19.0, "w": 38.0, "h": 247.0, "lab": "L2 2x13 ids 8-20"}]
    for k, mid in enumerate(ACT_IDS):
        items.append({"kind": "tag", "id": mid, "w": 40.0, "h": 40.0, "lab": "Actuator:%d id %d" % (k + 1, mid)})
    out = []
    for name, (pw, ph) in (("letter", (215.9, 279.4)), ("a4", (210.0, 297.0))):
        placed = sheet_layout(pw, ph, items)
        g = ['<rect width="%g" height="%g" fill="#fff" stroke="#bbb" stroke-width="0.5"/>' % (pw, ph),
             '<rect x="10" y="10" width="%g" height="%g" fill="none" stroke="#9cc3f0" stroke-width="0.35" stroke-dasharray="2 1.5"/>'
             % (pw - 20, ph - 20)]
        for it, x, y in placed:
            if it["kind"] == "board":
                g.append('<g data-tip="%s: %.1f x %.1f mm at true size">%s</g>' % (
                    esc(it["lab"]), it["w"], it["h"], g_charuco(it["lay"], x, y, it["sq"])))
            else:
                g.append(g_bits(marker_grid(ACT_DICT, it["id"]), x, y, 40, 1,
                                extra=' data-tip="%s: 30 mm black square + 5 mm quiet zone (illustrative size)"' % esc(it["lab"])))
                g.append('<rect x="%g" y="%g" width="40" height="40" fill="none" stroke="#ccc" stroke-width="0.3"/>' % (x, y))
            g.append('<text x="%g" y="%g" font-size="3.2" fill="#444" font-family="Consolas,monospace">%s</text>' % (
                x, y + it["h"] + 3.9, esc(it["lab"])))
        # 50 mm scale bar + header
        g.append('<g transform="translate(%g %g)"><rect width="50" height="1.6" fill="#222"/>'
                 '<text x="0" y="5.6" font-size="3.2" fill="#444" font-family="Consolas,monospace">50 mm</text></g>'
                 % (pw - 62, ph - 7.5))
        g.append('<text x="10" y="6.6" font-size="3.4" fill="#666" font-family="Segoe UI,Arial,sans-serif">'
                 'Marker sheet, %s %.1f x %.1f mm. Print at 100%%.</text>' % ("US Letter" if name == "letter" else "A4", pw, ph))
        lab = "Print sheet, %s" % ("US Letter" if name == "letter" else "A4")
        out.append('<div data-variant-of="sheet" data-variant="%s"%s><svg class="sheet" viewBox="-2 -2 %g %g" '
                   'role="img" aria-label="%s" style="max-width:520px;width:100%%;height:auto;display:block;margin:0 auto">'
                   '<title>%s</title>%s</svg></div>'
                   % (name, "" if name == "letter" else ' class="hide"', pw + 4, ph + 4, lab, lab, "".join(g)))
    toggle = ('<div class="toggle" data-toggle="sheet" role="group" aria-label="Page size">'
              '<button type="button" class="on" data-v="letter">US Letter</button>'
              '<button type="button" data-v="a4">A4</button></div>')
    return toggle + "".join(out)


def decisions_html(hid="decisions", with_figs=True, heading="h2") -> str:
    items_a = [
        ("The image in Fusion is always truthful.",
         "The marker image shown in Fusion must always match the current dictionary and ID. Showing copy #1's "
         "image on every copy is not acceptable.",
         ['<span class="qtag no">Answers Q2: no</span>', '<span class="qtag">Reverses the doc\'s default for Q2</span>']),
        ("No private GitHub repo for Studio marker sets.",
         "Studio marker sets are not to be synced through a private GitHub repo.",
         ['<span class="qtag no">Answers Q4: no</span>', '<span class="qtag">Declines building block S1</span>']),
        ("Output: the markers, plus a print-ready PDF sheet.",
         "The deliverable is the markers themselves plus a printable PDF sheet that lays out every marker "
         "automatically, already scaled to true size, ready to send to a printer. The page size (US Letter, A4, "
         "and so on) is a setting.", []),
        ("Markers are first-class in CAD.",
         "Where each marker is and what its IDs are is defined in Fusion, and that information travels with the "
         "exported robot description (<code>CadDescription</code> / URDF).",
         ['<span class="qtag">Bears on Q1 (where truth lives); storage not stated</span>']),
        ("Per-copy ID overrides at the sub-assembly level.",
         "A marker defined on a component (an actuator, say) can have its ID overridden per copy at the "
         "sub-assembly level, like a serial number: an arm with 6 copies of the actuator gives each copy its own "
         "ID, without making a new model. Only IDs (and similar non-geometric values) can be overridden, never "
         "geometry.", ['<span class="qtag">cf. section 9, option O1</span>']),
    ]
    items_b = [
        ("Tags will be 3D printed: real geometry, not decals.",
         "Markers must be modelled as real geometry (“I cannot use a decal”). Decals may still be "
         "supported later, but the primary representation is bodies with colour; the apriltag3D and "
         "Revise-Robotics generators, which make actual geometry, are the model to follow.",
         ['<span class="qtag no">Reverses Q6\'s default (v1 stickers only)</span>',
          '<span class="qtag">Decal path now secondary</span>']),
        ("Preferred UX: an “ArUco extrude”.",
         "Draw a rectangle and extrude an ArUco pattern instead of a normal extrude; or extrude a body normally "
         "and “convert” one of its faces into an ArUco tag, with the add-in detecting the dimensions. "
         "The tag part can carry other features too (a hole, for example), so the ArUco step must live in the "
         "timeline and regenerate in place so later features keep working.", []),
        ("Success test: the pattern is visible in the URDF export.",
         "When the design is exported to URDF, the marker pattern is visible.", []),
    ]

    def lis(items):
        out = []
        for h, b, tags in items:
            t = '<div class="tags">%s</div>' % "".join(tags) if tags else ""
            out.append('<li><div class="dh">%s</div><div>%s</div>%s</li>' % (h, b, t))
        return "".join(out)

    figs = ""
    if with_figs:
        figs = '%s%s<div class="dfigs">%s%s</div>' % (
            fig(fig_six_copies(), "Decisions 1 and 5: one slot on the component, six copies, six IDs. "
                "IDs 21 to 26 (DICT_4X4_100) are illustrative; the markers are real OpenCV renders.",
                "illustrative", "wide"),
            fig(fig_extrude_flow(), "Decisions 7 and 8: the two ways in Zach described, and why the step must "
                "stay in the timeline.", "illustrative", "wide"),
            fig(fig_print_sheet(), "Decision 3: every marker at true size on one sheet; the page size is a setting. "
                "Content: the doc's L1 and L2 boards (2 x 8 at 25.4 mm squares, 2 x 13 at 19 mm, as their rectangles "
                "measure) and the six actuator tags at an illustrative 30 mm. Toggle the page size; hover a marker.",
                "illustrative"),
            fig(fig_two_body_tag(), "The design response, drawn: body 1 is the white tile, body 2 the black cells "
                "(OpenCV DICT_4X4_50 id 3), inlaid flush. Exploded above, assembled at right.", "design response"))
    head = '<%s class="dtitle" id="%s">Decisions since this was written — Zach, 2026-10-07</%s>' % (heading, hid, heading)
    return """<section class="decisions" aria-labelledby="%s">
%s
<p class="dsub">The three docs below are dated 2026-10-02. These calls came afterwards and override them where they differ.</p>
<ol class="dlist">%s</ol>
<div class="dgroup">Later the same morning</div>
<ol class="dlist" start="6" style="counter-reset:dec 5">%s</ol>
<div class="response"><div class="rh">Orchestrator's design response (not Zach's words)</div>
<p>The tag is <b>two bodies</b>: a white tile and a black-cells body inlaid flush. Two reasons: the exporter writes one
coloured mesh per body, so a split-face appearance would vanish in the URDF; and slicers assign filament per body.</p>
<p>A printed ID is geometry, and Fusion shares geometry across copies. So per-copy ID overrides are realised as
<b>one tag part per ID</b>, placed at the assembly level where the override is made and snapped to a <b>tag slot</b>
defined on the component.</p></div>
<div class="status"><span class="dot"></span><div><b>Status:</b> a prototype implementing this is being built now in the same
worktree, on branch <code>%s</code>.</div></div>
%s
</section>""" % (hid, head, lis(items_a), lis(items_b), BRANCH, figs)


def decal_note(extra: str = "") -> str:
    return ('<aside class="callout co-mini decal"><b>Since 2026-10-07:</b> tags will be 3D printed, so the decal path '
            'described here is now secondary; real geometry is the primary output (<a href="#decisions">decision 6</a>).%s</aside>'
            % (" " + extra if extra else ""))


# ================================================================ prior art

MATRIX_COLS = [
    ("aruco", "ArUco dicts"), ("april", "AprilTag"), ("board", "Grid / ChArUco boards"),
    ("sketch", "Sketch output"), ("solid", "Solid / mesh output"), ("decal", "Decal"),
    ("image", "Image / print file"), ("autoid", "Batch or auto IDs"),
    ("wide", "Design- / assembly-wide IDs"), ("suggest", "Suggests dict / size"),
    ("reedit", "Re-editable in CAD"), ("open", "Open licence"),
]

PA = "https://github.com/"
MATRIX_ROWS = [
    ("Code Generator (“Toby”)", "https://marketplace.autodesk.com/apps/d8d3a13e-0de0-4f9c-adbb-528fd276cca9",
     "Fusion add-in, paid", True, "$24.99",
     dict(aruco=("y", "All OpenCV ArUco, 4x4_50 to 7x7_1000"), april=("y", "16h5, 25h9, 36h10, 36h11"),
          board=("n", "Verdict: misses ChArUco"), sketch=("y", "Sketch on a planar face or plane"),
          solid=("y", "Bodies; optional own component"), decal=("n", "Verdict: misses decals"), image=("n", ""),
          autoid=("n", "One ID typed by hand, validated against the dictionary"),
          wide=("n", "Verdict: no assembly awareness"), suggest=("n", "Verdict: no suggestions"),
          reedit=("n", "Its docs show only a timeline group"), open=("n", "Closed source"))),
    ("arnoldad/apriltag3D", PA + "arnoldad/apriltag3D", "Fusion add-in", True, "—",
     dict(aruco=("n", "No ArUco"), april=("y", "All 9 AprilTag families"), board=("n", ""), sketch=("n", ""),
          solid=("y", "Custom Feature solid: white and black bodies, Joint Origin, socket cutter"),
          decal=("n", ""), image=("n", ""), autoid=("n", "One ID typed by hand"), wide=("n", ""),
          suggest=("n", ""), reedit=("y", "Re-editable Custom Feature"), open=("y", "MIT"))),
    ("wglockner/ArUco-Generator", PA + "wglockner/ArUco-Generator-Fusion360-ADD-IN", "Fusion add-in (likely broken)",
     True, "—",
     dict(aruco=("y", "6 ArUco dicts"), april=("n", ""), board=("y", "Marker, GridBoard or ChArUco; border bits"),
          sketch=("n", "Not geometry"), solid=("n", "Not geometry"), decal=("n", "A Canvas, not a decal"),
          image=("y", "OpenCV PNG inserted as a Canvas"),
          autoid=("n", "One ID; its Start ID only goes into a caption"), wide=("n", ""), suggest=("n", ""),
          reedit=("n", ""), open=("y", "MIT"))),
    ("Revise-Robotics apriltag 3D", PA + "Revise-Robotics/fusion360-apriltag-3d-code-generator", "Fusion script",
     True, "—",
     dict(aruco=("n", ""), april=("p", "tag36h11 only"), board=("n", ""), sketch=("n", ""),
          solid=("y", "Two-color solid"), decal=("n", ""), image=("n", ""), autoid=("n", "Input box"),
          wide=("n", ""), suggest=("n", ""), reedit=("n", "One-shot script"), open=("n", "No licence"))),
    ("jamesemerick/AprilTag2CAD", PA + "jamesemerick/Fusion360AprilTag2CAD", "Fusion add-in", True, "—",
     dict(aruco=("p", "Any PNG"), april=("p", "Any PNG"), board=("n", ""), sketch=("n", ""),
          solid=("y", "Solid built from a pre-made PNG"), decal=("n", ""), image=("n", ""), autoid=("n", "None"),
          wide=("n", ""), suggest=("n", ""), reedit=("n", ""), open=("y", "MIT"))),
    ("Wa-Araki/quARtet-marker", PA + "Wa-Araki/quARtet-marker", "Fusion add-in (with paper)", True, "—",
     dict(aruco=("n", ""), april=("p", "tag36h11"), board=("p", "One four-tag marker"),
          sketch=("?", "Output form not stated: one four-tag marker built from a JSON file the detector also reads"),
          solid=("?", "Output form not stated"), decal=("?", "Not stated"), image=("?", "Not stated"),
          autoid=("n", "Fixed IDs 0-3"), wide=("n", ""), suggest=("n", ""), reedit=("?", "Not stated"),
          open=("y", "MIT"))),
    ("QR relatives (3 add-ins)", PA + "tapnair/QRCoder", "Fusion add-ins", True, "$4.99/mo (Pro)",
     dict(aruco=("n", "QR only"), april=("n", "QR only"), board=("n", ""), sketch=("n", ""),
          solid=("y", "Extruded QR blocks"), decal=("n", ""), image=("n", ""),
          autoid=("p", "QR Code Creator Pro can make “a sequence of QR codes”"), wide=("n", ""),
          suggest=("n", ""), reedit=("n", ""), open=("p", "QRCoder MIT; Pro is paid"))),
    ("ZHN0019/SolidworksArucoGenerator", PA + "ZHN0019/SolidworksArucoGenerator", "SolidWorks 2025 add-in", False,
     "—",
     dict(aruco=("p", "DICT_4X4_50, IDs 0-30"), april=("n", ""), board=("n", ""), sketch=("n", ""),
          solid=("y", "Two-body part, STEP"), decal=("n", ""), image=("y", "PNG"), autoid=("n", "Hand-typed"),
          wide=("n", ""), suggest=("n", ""), reedit=("n", ""), open=("n", "No licence"))),
    ("dogadogan/BrightMarker", PA + "dogadogan/BrightMarker", "Blender add-on (MIT CSAIL)", False, "—",
     dict(aruco=("p", "Bundled ArUco set"), april=("n", ""), board=("n", ""), sketch=("n", ""),
          solid=("y", "Embeds many markers into one mesh"), decal=("n", ""), image=("n", ""),
          autoid=("y", "Sequential IDs from a start ID, on picked faces or spread evenly"),
          wide=("n", "Only inside one object"), suggest=("n", ""), reedit=("n", ""), open=("y", "MIT"))),
    ("dogadogan/InfraredTags", PA + "dogadogan/InfraredTags", "Rhino 6 + Grasshopper", False, "—",
     dict(aruco=("p", "From SVG"), april=("n", ""), board=("n", ""), sketch=("n", ""),
          solid=("y", "Hides a QR or ArUco SVG inside a print"), decal=("n", ""), image=("n", ""),
          autoid=("n", "None"), wide=("n", ""), suggest=("n", ""), reedit=("n", ""), open=("y", "MIT"))),
    ("younghyopark/aprilcube", PA + "younghyopark/aprilcube", "Python CLI", False, "—",
     dict(aruco=("y", "ArUco"), april=("p", "AprilTag 36h11"), board=("n", ""), sketch=("n", ""),
          solid=("y", "Multi-color 3MF cube with a tag per face, plus detector config"), decal=("n", ""),
          image=("n", ""), autoid=("y", "Auto IDs within one cube"), wide=("n", "Only inside one cube"),
          suggest=("n", ""), reedit=("n", ""), open=("y", "MIT (README)"))),
    ("MakerWorld “ArUco Markers Generator”", "https://makerworld.com/en/models/2801571-aruco-markers-generator",
     "OpenSCAD", False, "—",
     dict(aruco=("y", "10 ArUco/AprilTag dicts"), april=("y", "10 ArUco/AprilTag dicts"), board=("n", ""),
          sketch=("n", ""), solid=("y", "Two-part printable tiles"), decal=("n", ""), image=("n", ""),
          autoid=("y", "First-to-last ID grid"), wide=("n", ""), suggest=("n", ""), reedit=("n", ""),
          open=("p", "CC BY-NC-SA"))),
    ("Other printables (5 tools)", PA + "imontesino/3D-aruco-generator", "Python, OpenSCAD, FreeCAD", False, "—",
     dict(aruco=("p", "Varies"), april=("?", "Varies"), board=("n", ""), sketch=("n", ""),
          solid=("y", "STL or STEP files"), decal=("n", ""), image=("n", ""), autoid=("n", "One ID per run"),
          wide=("n", ""), suggest=("n", ""), reedit=("n", ""), open=("p", "AGPL, CC BY; CAD_Engraver has no licence"))),
    ("chev.me/arucogen", "https://chev.me/arucogen/", "Web", False, "—",
     dict(aruco=("y", "10 dicts"), april=("?", "10 dicts; families not listed in the research"), board=("n", ""),
          sketch=("n", ""), solid=("n", ""), decal=("n", ""), image=("y", "SVG, PDF"), autoid=("n", "One ID"),
          wide=("n", ""), suggest=("n", ""), reedit=("n", ""), open=("y", "MIT, 590 stars"))),
    ("calib.io generator", "https://calib.io/pages/camera-calibration-pattern-generator", "Web", False, "—",
     dict(aruco=("?", "ChArUco implies ArUco markers; not listed separately"), april=("p", "AprilGrid"),
          board=("y", "ChArUco, AprilGrid"), sketch=("n", ""), solid=("n", ""), decal=("n", ""),
          image=("y", "PDF"), autoid=("p", "“Start ID” offset"), wide=("n", ""), suggest=("n", ""),
          reedit=("n", ""), open=("n", "Non-commercial only"))),
    ("lyehe/aruco_3d", PA + "lyehe/aruco_3d", "Web", False, "—",
     dict(aruco=("y", "ArUco"), april=("y", "AprilTag"), board=("y", "ChArUco boards"), sketch=("n", ""),
          solid=("y", "3MF"), decal=("n", ""), image=("y", "SVG, PNG, JSON"), autoid=("p", "ID lists"),
          wide=("n", ""), suggest=("n", ""), reedit=("n", ""), open=("y", "AGPL"))),
    ("Sheet generators (3)", PA + "o16s/aruco-tag-generator", "Printable pages", False, "—",
     dict(aruco=("?", "Not stated"), april=("?", "Not stated"), board=("n", ""), sketch=("n", ""),
          solid=("n", ""), decal=("n", ""), image=("y", "Printable pages"),
          autoid=("y", "Pack ID ranges onto printable pages"), wide=("n", ""), suggest=("n", ""),
          reedit=("n", ""), open=("?", "Not stated"))),
]
WISH = dict(aruco="ArUco", board="ChArUco", sketch="“convert this rectangle”", decal="decals",
            autoid="unique or auto IDs", wide="assembly awareness", suggest="suggestions",
            reedit="a re-editable feature")
SYMB = {"y": "✓", "p": "◐", "n": "✗", "?": "?", "w": "★"}
WORD = {"y": "yes", "p": "partial", "n": "no", "?": "not stated in the research", "w": "wanted"}


def prior_matrix() -> str:
    need(SRC_TEXT["prior"], "v1.0.3, 2026-08-11. $24.99, closed source", "All 9 AprilTag families, no ArUco",
         "OpenCV PNG inserted as a **Canvas**", "**Sequential IDs from a start ID**", "Auto IDs within one cube",
         "First-to-last ID grid", "Pro $4.99/month", "Fixed IDs 0-3", "DICT_4X4_50, IDs 0-30",
         "\"Start ID\" offset", "Non-commercial only", "It misses unique or auto IDs, assembly awareness, decals",
         "No tool found, in any CAD package, does ID bookkeeping", "Nothing suggests a dictionary or a minimum size")
    th = '<th class="tool">Tool</th>' + "".join('<th>%s</th>' % esc(lab) for _, lab in MATRIX_COLS)
    trs = []
    for name, url, plat, fusion, price, cells in MATRIX_ROWS:
        pr = ' \u00b7 <b class="price">%s</b>' % esc(price) if price.startswith("$") else ""
        tds = ['<td class="tool"><a href="%s" target="_blank" rel="noopener">%s</a><span class="plat">%s%s</span></td>'
               % (esc(url), esc(name), esc(plat), pr)]
        for key, lab in MATRIX_COLS:
            v, note = cells[key]
            tip = "%s — %s: %s%s" % (name, lab, WORD[v], (". " + note) if note else "")
            tds.append('<td><span class="cell c-%s" data-tip="%s" aria-label="%s">%s</span></td>'
                       % ("q" if v == "?" else v, esc(tip), esc(WORD[v]), SYMB[v]))
        trs.append('<tr class="%s">%s</tr>' % ("fusion" if fusion else "", "".join(tds)))
    wish = ['<td class="tool"><b>What the research looked for</b><span class="plat">from the question and the verdict</span></td>']
    for key, lab in MATRIX_COLS:
        if key in WISH:
            wish.append('<td><span class="cell c-w" data-tip="Wanted: %s">%s</span></td>' % (esc(WISH[key]), SYMB["w"]))
        else:
            wish.append("<td></td>")
    trs.append('<tr class="wish">%s</tr>' % "".join(wish))
    legend = ('<div class="legend"><span><span class="cell c-y">✓</span>yes</span>'
              '<span><span class="cell c-p">◐</span>partial or narrow</span>'
              '<span><span class="cell c-n">✗</span>no, or not among its listed outputs</span>'
              '<span><span class="cell c-q">?</span>not stated in the research</span>'
              '<span><span class="cell c-w">★</span>wanted</span>'
              '<span><i style="background:var(--acc)"></i>Fusion tool (blue edge)</span>'
              '<span>Prices (after the platform) are the only ones the research gives.</span></div>')
    table = '<div class="tablewrap" style="max-height:none"><table class="matrix">%s%s</table></div>' % (
        "<thead><tr>%s</tr></thead>" % th, "<tbody>%s</tbody>" % "".join(trs))
    return fig(table + legend,
               "Every cell is read off the research's <b>Hits</b> table and <b>Verdict</b>; hover a cell for the "
               "wording it came from. Grouped rows (QR relatives, other printables, sheet generators) are scored on "
               "what the research says about the group.", "from the doc", "matrix-fig")


def prior_coverage() -> str:
    """How many of the 17 entries cover each wished-for capability (counted from the matrix)."""
    keys = ["aruco", "board", "solid", "sketch", "decal", "autoid", "reedit", "wide", "suggest"]
    labs = dict(MATRIX_COLS)
    n = len(MATRIX_ROWS)
    W, rowh, left = 900, 34, 250
    H = 40 + rowh * len(keys) + 30
    sc = (W - left - 70) / n
    o = []
    for t in range(0, n + 1, 4):
        x = left + t * sc
        o.append('<line x1="%g" y1="30" x2="%g" y2="%g" class="grid"/>' % (x, x, H - 28))
        o.append(svg_text(x, H - 10, str(t), cls="t-mut", size=11.5, anchor="middle"))
    o.append(svg_text(left, 18, "entries (of %d) that have it" % n, cls="t-mut", size=12))
    for i, k in enumerate(keys):
        y = 36 + i * rowh
        yes = sum(1 for r in MATRIX_ROWS if r[5][k][0] == "y")
        part = sum(1 for r in MATRIX_ROWS if r[5][k][0] == "p")
        lab = labs[k] + (" ★" if k in WISH else "")
        o.append(svg_text(left - 12, y + 16, lab, anchor="end", size=13.5))
        if yes:
            o.append('<rect x="%g" y="%g" width="%g" height="18" rx="4" class="f-s3" data-tip="%s: %d yes"/>'
                     % (left, y + 3, max(yes * sc - 2, 2), esc(labs[k]), yes))
        if part:
            o.append('<rect x="%g" y="%g" width="%g" height="18" rx="4" class="f-s4" data-tip="%s: %d partial"/>'
                     % (left + yes * sc, y + 3, max(part * sc - 2, 2), esc(labs[k]), part))
        txt = "%d" % yes + (" + %d partial" % part if part else "")
        if yes + part == 0:
            txt = "0 — nothing found"
        o.append(svg_text(left + (yes + part) * sc + 8, y + 16, txt, size=12.5,
                          cls="t-bad t-b" if yes + part == 0 else "t-ink2"))
    leg = ('<div class="legend"><span><i style="background:var(--s3)"></i>yes</span>'
           '<span><i style="background:var(--s4)"></i>partial</span><span>★ on the wishlist</span></div>')
    return fig(svg("".join(o), W, H, "Coverage of each capability across the prior art") + leg,
               "Counted from the matrix above. Auto-numbering exists only inside one object; nothing tracks IDs "
               "across a design or an assembly, nothing suggests a dictionary or size, and no tool outputs a "
               "Fusion decal.", "from the doc", "mid")


TIMELINE = [
    ("2021", "tapnair/QRCoder", "Fusion add-in (QR)", 22),
    ("2023-03-10", "jamesemerick/Fusion360AprilTag2CAD", "Fusion add-in", 1),
    ("2023-11-18", "dogadogan/InfraredTags", "Rhino + Grasshopper", 36),
    ("2024-07-18", "dogadogan/BrightMarker", "Blender add-on", 6),
    ("2025-09-08", "wglockner/ArUco-Generator", "Fusion add-in", None),
    ("2026-04-22", "Revise-Robotics generator", "Fusion script", None),
    ("2026-05-14", "MakerWorld ArUco Markers Generator", "OpenSCAD (124 customizations)", None),
    ("2026-06-03", "arnoldad/apriltag3D", "Fusion add-in (18 open issues)", 0),
    ("2026-07-25", "ZHN0019/SolidworksArucoGenerator", "SolidWorks add-in", None),
    ("2026-08-11", "Code Generator v1.0.3", "Fusion add-in, $24.99 (0 reviews)", None),
    ("2026-09-08", "younghyopark/aprilcube", "Python CLI", 121),
    ("2026-10-02", "Wa-Araki/quARtet-marker", "Fusion add-in", None),
]


def prior_timeline() -> str:
    for d, *_ in TIMELINE:
        need(SRC_TEXT["prior"], d)
    rows = []
    mx = 121
    W, rh = 900, 34
    H = 20 + rh * len(TIMELINE)
    o = ['<line x1="128" y1="10" x2="128" y2="%d" class="axis"/>' % (H - 10)]
    for i, (d, name, plat, stars) in enumerate(TIMELINE):
        y = 24 + i * rh
        fusion = "Fusion" in plat
        o.append(svg_text(112, y + 4, d, anchor="end", size=12.5, cls="t-mono t-ink2"))
        o.append('<circle cx="128" cy="%d" r="6" class="%s"/>' % (y, "f-s1" if fusion else "f-s2"))
        o.append(svg_text(146, y + 4, name, size=13.5, cls="t-b"))
        o.append(svg_text(146 + len(name) * 7.3 + 10, y + 4, plat, size=12.5, cls="t-mut"))
        if stars is not None:
            bw = 160 * stars / mx
            o.append('<rect x="700" y="%d" width="%g" height="12" rx="3" class="f-s3" data-tip="%s: %d GitHub stars"/>'
                     % (y - 7, max(bw, 1.5), esc(name), stars))
            o.append(svg_text(700 + max(bw, 1.5) + 6, y + 4, "%d ★" % stars, size=12, cls="t-ink2"))
    o.append(svg_text(700, 12, "GitHub stars, where given", size=11.5, cls="t-mut"))
    leg = ('<div class="legend"><span><i style="background:var(--s1);border-radius:50%"></i>Fusion add-in or script</span>'
           '<span><i style="background:var(--s2);border-radius:50%"></i>other platform</span>'
           '<span><i style="background:var(--s3)"></i>stars</span></div>')
    return fig(svg("".join(o), W, H, "Prior-art timeline") + leg,
               "Dates as given in the research's Status column; it does not say whether each is a release, a last "
               "commit or a creation date. Undated entries (arucogen, 590 stars; aruco_3d, 12; imontesino, 19) are "
               "left off.", "from the doc", "mid")


def prior_kinds() -> str:
    row = "".join('<figure>%s<figcaption>4X4_50 id %d</figcaption></figure>' % (marker_svg("DICT_4X4_50", i), i)
                  for i in range(6))
    row += '<figure>%s<figcaption>36h11 id 0</figcaption></figure>' % marker_svg("DICT_APRILTAG_36h11", 0)
    gb = gridboard_layout(3, 2, "DICT_4X4_50", 1.0, 0.25, first=6)
    ch = charuco_layout(5, 4, "DICT_4X4_50", 0.75)
    boards = ('<div class="mkrow" style="margin-top:14px">'
              '<figure>%s<figcaption>GridBoard 3x2, ids 6-11</figcaption></figure>'
              '<figure>%s<figcaption>ChArUco 5x4, ids 0-9</figcaption></figure></div>'
              % (gridboard_svg(gb, 40, 8).replace('class="board"', 'class="board" style="width:200px"'),
                 charuco_svg(ch, 40, 8).replace('class="board"', 'class="board" style="width:230px"')))
    return fig('<div class="mkrow">%s</div>%s' % (row, boards),
               "What every tool on this page draws: single markers, an AprilTag, a grid board and a ChArUco board. "
               "Each is OpenCV %s's own output, read back as bits and inlined as SVG; zoom in and the edges stay "
               "sharp. A detector reads these." % cv2.__version__, "generated by OpenCV")


def prior_byte_trap(story: dict) -> str:
    W, H = 960, 420
    T = 170                      # tag size incl. 1-module quiet zone
    cell = T / 10.0
    XR = W - 20 - T
    o = []
    o.append(svg_text(20, 24, "Correct: OpenCV's table", cls="t-b t-good", size=14))
    o.append(g_bits(story["good"], 20, 36, T, 1, extra=' data-tip="tag36h11 id %d as OpenCV draws it"' % story["id"]))
    o.append(svg_text(XR, 24, "Last byte mis-unpacked", cls="t-b t-bad", size=14))
    o.append(g_bits(story["bad"], XR, 36, T, 1, extra=' data-tip="the same ID after reading the wrong 4 bits"'))
    for k in range(32, 36):
        r, c = divmod(k, 6)
        if story["bits"][k] != story["wrong"][k]:
            o.append('<rect x="%g" y="%g" width="%g" height="%g" fill="none" class="edge e-bad" stroke-width="3"/>'
                     % (XR + (c + 2) * cell, 36 + (r + 2) * cell, cell, cell))
        o.append('<rect x="%g" y="%g" width="%g" height="%g" fill="none" class="edge e-good" stroke-width="3"/>'
                 % (20 + (c + 2) * cell, 36 + (r + 2) * cell, cell, cell))
    o.append(svg_text(20 + T / 2, 36 + T + 20, "id %d" % story["id"], size=12.5, anchor="middle", cls="t-mono t-b"))
    o.append(svg_text(XR + T / 2, 36 + T + 20, "id %d, last 4 bits wrong" % story["id"], size=12.5, anchor="middle",
                      cls="t-mono t-bad"))
    # byte strip, centred between the two tags
    bw = 11.0
    span = 5 * 8 * bw + 4 * 10
    bx, by = (20 + T + XR) / 2 - span / 2, 78
    o.append(svg_text((20 + T + XR) / 2, 30, "36 data bits = 4 full bytes + half a byte", cls="t-b", size=13, anchor="middle"))
    for b in range(5):
        x = bx + b * (8 * bw + 10)
        bits = format(story["bytes"][b], "08b")
        for j in range(8):
            if b == 4 and j < 4:
                o.append('<rect x="%g" y="%g" width="%g" height="22" class="box2" data-tip="padding bit (always 0)"/>'
                         % (x + j * bw, by, bw - 1))
                o.append(svg_text(x + j * bw + bw / 2 - 0.5, by + 15, "0", size=10, anchor="middle", cls="t-mut t-mono"))
            else:
                o.append('<rect x="%g" y="%g" width="%g" height="22" fill="%s" stroke="#888" stroke-width="0.6" '
                         'data-tip="data bit %d = %s"/>' % (x + j * bw, by, bw - 1, "#ffffff" if bits[j] == "1" else "#111111",
                                                            b * 8 + j + 1 if b < 4 else 33 + j - 4, bits[j]))
        o.append(svg_text(x + 4 * bw, by + 40, "byte %d" % (b + 1), size=11.5, anchor="middle", cls="t-mut"))
        o.append(svg_text(x + 4 * bw, by + 55, "%d" % story["bytes"][b], size=11.5, anchor="middle", cls="t-mono t-ink2"))
    x5 = bx + 4 * (8 * bw + 10)
    o.append('<path d="M%g %g v16 h%g v-16" fill="none" class="edge e-good" stroke-width="2"/>' % (x5 + 4 * bw, by + 62, 4 * bw - 1))
    o.append(svg_text(x5 + 6 * bw, by + 96, "low 4 bits: right", size=12, anchor="middle", cls="t-good t-b"))
    o.append('<path d="M%g %g v-12 h%g v12" fill="none" class="edge e-bad" stroke-width="2"/>' % (x5, by - 4, 4 * bw - 1))
    o.append(svg_text(x5 + 2 * bw, by - 20, "high 4: wrong", size=12, anchor="middle", cls="t-bad t-b"))
    det = story["detect"]
    if not det:
        det_lines = ["OpenCV %s's 36h11 detector finds no marker in" % cv2.__version__,
                     "the corrupted image: the tag is simply lost."]
    elif det == [story["id"]]:
        det_lines = ["OpenCV %s's 36h11 detector still reads id %d:" % (cv2.__version__, story["id"]),
                     "error correction hides the slip from a detector."]
    else:
        det_lines = ["OpenCV %s's 36h11 detector reads it as id %s:" % (cv2.__version__, ", ".join(map(str, det))),
                     "a wrong ID, reported with confidence."]
    nib = format(story["bytes"][4], "04b")
    lines = ["In OpenCV's own byte list the 5th byte keeps its",
             "4 bits in the low nibble (here %d = %s)." % (story["bytes"][4], nib),
             "Reading the high nibble (0000) instead turns the",
             "last %d cells (outlined) from white to black." % story["diff"]] + det_lines
    cx = (20 + T + XR) / 2
    for i, t in enumerate(lines):
        o.append(svg_text(cx, 210 + i * 19, t, size=13, cls="t-ink2", anchor="middle"))
    o.append('<rect x="20" y="342" width="920" height="64" rx="10" class="b-warn"/>')
    o.append(svg_text(36, 367, "Schematic of the failure class only.", cls="t-b t-warn", size=13))
    o.append(svg_text(36, 388, "The research says a mis-unpacked last partial byte broke two tag36h11 IDs for one MakerWorld "
                      "reviewer; it does not say which IDs or the exact slip.", size=12.5, cls="t-ink2"))
    need(SRC_TEXT["prior"], "Unpack the last partial byte with care; a MakerWorld reviewer found that bug broke two tag36h11 IDs",
         "Check every ID against OpenCV")
    return fig(svg("".join(o), W, H, "AprilTag 36h11: the last-partial-byte trap"),
               "Left and centre are measured: OpenCV's tag and its stored bytes. Right is what one plausible "
               "mis-read produces. The detector line is a measurement on this build box.", "schematic", "wide")


def prior_prefix_check(ck: dict) -> str:
    cells = "".join('<span class="chip" style="border-color:var(--%s)">%s %s</span>'
                    % ("good" if ok else "bad", k, "✓" if ok else "✗") for k, ok in ck["prefix"].items())
    ok = sum(ck["prefix"].values())
    need(SRC_TEXT["prior"], "The smaller OpenCV dictionaries are the first N entries of the _1000 tables")
    return check_box("The smaller dictionaries really are prefixes of the _1000 tables",
                     "<p>Claim in the doc: <i>the smaller OpenCV dictionaries are the first N entries of the _1000 "
                     "tables.</i> Compared byte for byte in OpenCV %s: <b>%d of %d</b> match.</p><p>%s</p>"
                     "<p style=\"font-size:14px;color:var(--muted)\">This is what lets section 10 of the proposal "
                     "re-declare a <code>DICT_4X4_1000</code> strip as <code>DICT_4X4_50</code> with no reprint.</p>"
                     % (ck["cv"], ok, len(ck["prefix"]), cells))


def build_prior(ck: dict, story: dict) -> str:
    text = SRC_TEXT["prior"]
    title, pre, secs = split_md(text)
    keys = {s["key"] for s in secs}
    for k in ("hits", "not-hits-checked-and-ruled-out", "demand-signals-forum-wishes-not-tools", "verdict",
              "code-worth-borrowing"):
        if k not in keys:
            warn("prior-art section missing: " + k)
    hooks = {
        "hits": {"lead": prior_matrix() + prior_coverage(), "after": prior_timeline()},
        "not-hits-checked-and-ruled-out": {"toc": "Not hits"},
        "demand-signals-forum-wishes-not-tools": {"toc": "Demand signals"},
        "code-worth-borrowing": {"after": prior_byte_trap(story) + prior_prefix_check(ck) + callout(
            "apriltag3D's fast path (TemporaryBRep boxes, touching cells merged, one union) is drawn on a real "
            "marker in the architecture report: <a href=\"fusion-architecture.html#cells-fig\">cells, clusters and "
            "corner-only contacts</a>.", "note")},
    }
    body, toc = render_sections(secs, hooks)
    intro = render_md(pre)
    kinds = prior_kinds()
    banner = callout('Written 2026-10-02. Later decisions (tags will be 3D printed; per-copy IDs) are on the '
                     '<a href="index.html#decisions">overview page</a>.', "note")
    return page_html("prior", "Prior art: ArUco and ChArUco generators for CAD",
                     "Research 1 of 3 · prior art",
                     "Does anything already make markers in Fusion with unique IDs, smart dictionary and size "
                     "choices, and assembly-wide ID handling? Seventeen entries checked; drawing a marker is solved "
                     "many times over, ID bookkeeping nowhere.",
                     "Source: <code>docs/research_aruco_prior_art.md</code>",
                     banner + intro + kinds + body, toc,
                     "Prior art for an ArUco/ChArUco generator in Fusion: tools, feature matrix, verdict, code to borrow.")


# ============================================================ architecture

LAYERS = [
    ("Command UI", [("Autodesk add-in template", "sample"),
                    ("apriltag3D dialog: preview image, ranges follow dictionary", "donor")]),
    ("Input selection", [("Voronoi selection filters", "donor"), ("face becomes sketch", "donor")]),
    ("Geometry", [("Voronoi importSVG for sketches", "donor"),
                  ("QRCoder / apriltag3D TBM + base feature for emboss or cut", "donor"),
                  ("Decals for image mode", "api")]),
    ("Regeneration", [("ParametricText event hooks", "donor"), ("KiCad placeholder idea", "outside"),
                      ("custom feature only as opt-in “live (preview API)”", "preview")]),
    ("ID registry", [("ParametricText attribute layout", "donor"), ("entityToken copy detection", "own")]),
    ("Assembly uniqueness", [("own code", "own"), ("occurrence-proxy attributes from the top design", "own"),
                             ("allOccurrencesByComponent", "own"), ("xref checks", "own"), ("rescans", "own")]),
    ("Palette", [("Voronoi handshake", "donor"), ("Image2Surface split", "donor"),
                 ("FusionReactr if React", "donor")]),
    ("External sync", [("Read Studio JSON files", "own"),
                       ("any HTTP via the FusionMCPSample thread + custom-event pattern", "sample")]),
]
LAYER_KIND = {"sample": ("b-acc", "Autodesk sample or template"), "donor": ("b-good", "open-source add-in donor"),
              "api": ("box", "released Fusion API"), "own": ("b-vio", "our own code"),
              "preview": ("b-warn", "preview API, opt-in only"), "outside": ("box2", "idea from outside Fusion")}


def arch_layers() -> str:
    need(SRC_TEXT["arch"], "Autodesk add-in template + apriltag3D dialog (preview image, ranges follow dictionary)",
         "Voronoi selection filters; face becomes sketch", "ParametricText event hooks + KiCad placeholder idea",
         "ParametricText attribute layout + `entityToken` copy detection",
         "Voronoi handshake + Image2Surface split (FusionReactr if React)",
         "Read Studio JSON files; any HTTP via the FusionMCPSample thread + custom-event pattern")
    W, left, x0, x1 = 960, 196, 214, 946
    o = []
    y = 10
    for i, (name, chips) in enumerate(LAYERS):
        # lay chips out in lines
        lines, cur, cx = [], [], x0
        for text, kind in chips:
            w = len(text) * 6.7 + 22
            if cur and cx + w > x1:
                lines.append(cur)
                cur, cx = [], x0
            cur.append((text, kind, cx, w))
            cx += w + 8
        lines.append(cur)
        h = 16 + 34 * len(lines)
        o.append('<rect x="8" y="%d" width="%d" height="%d" rx="10" class="%s"/>' % (y, W - 16, h, "lane" if i % 2 == 0 else "box"))
        o.append('<rect x="8" y="%d" width="%d" height="%d" rx="10" class="b-dec" opacity=".9"/>' % (y, left - 8, h))
        o.append(svg_text(22, y + h / 2 + 5, name, cls="t-b", size=14.5))
        o.append(svg_text(left - 16, y + h / 2 + 5, str(i + 1), cls="t-dec t-b", size=13, anchor="end"))
        for li, line in enumerate(lines):
            for text, kind, cx, w in line:
                cy = y + 10 + li * 34
                cls, desc = LAYER_KIND[kind]
                o.append('<g data-tip="%s: %s"><rect x="%g" y="%d" width="%g" height="26" rx="13" class="%s"/>%s</g>'
                         % (esc(name), esc(desc), cx, cy, w, cls, svg_text(cx + w / 2, cy + 17.5, text, size=12.5, anchor="middle")))
        y += h + 6
    H = y + 4
    leg = '<div class="legend">%s</div>' % "".join(
        '<span><i class="lg-%s"></i>%s</span>' % (k, esc(v[1])) for k, v in LAYER_KIND.items())
    leg = leg.replace('class="lg-sample"', 'style="background:var(--acc-weak);border:1px solid var(--acc)"') \
             .replace('class="lg-donor"', 'style="background:var(--good-weak);border:1px solid var(--good)"') \
             .replace('class="lg-api"', 'style="background:var(--surface);border:1px solid var(--line-2)"') \
             .replace('class="lg-own"', 'style="background:var(--vio-weak);border:1px solid var(--violet)"') \
             .replace('class="lg-preview"', 'style="background:var(--warn-weak);border:1px solid var(--warn)"') \
             .replace('class="lg-outside"', 'style="background:var(--surface-2);border:1px solid var(--line-2)"')
    return fig(svg("".join(o), W, H, "Recommended reference architecture, layer by layer") + leg,
               "The doc's closing table drawn as a stack, from the dialog the user sees (top) to sync with the Linux "
               "box (bottom). Chip colour says where each piece comes from; only assembly uniqueness has no donor.",
               "from the doc", "wide", "layers-fig")


def arch_api_timeline() -> str:
    ev = [(2021.0, "Jan 2021", "Custom Features: Preview", "and still Preview today", "b-warn", True),
          (2023.37, "May 2023", "Canvases.createInput", "a tracing image, not a marker", "box", False),
          (2024.67, "Sept 2024", "Decals: released API", "createInput, then add", "b-good", True),
          (2025.0, "Jan 2025", "Forum: decals fail in direct", "modeling; may ignore transform", "b-bad", False),
          (2026.0, "Jan 2026", "Design intent arrives", "Python 3.12 \u2192 3.14", "b-acc", True)]
    need(SRC_TEXT["arch"], "since January 2021", "(May 2023)", "(September 2024)", "A forum report (Jan 2025)",
         "Design intent (January 2026", "Python 3.12 to 3.14 in January 2026")
    W, H = 960, 210
    t0, t1, xa, xb = 2020.6, 2026.5, 40, 920
    X = lambda t: xa + (t - t0) / (t1 - t0) * (xb - xa)
    o = ['<line x1="%d" y1="105" x2="%d" y2="105" class="axis" stroke-width="2"/>' % (xa, xb)]
    for yr in range(2021, 2027):
        o.append('<line x1="%g" y1="100" x2="%g" y2="110" class="axis"/>' % (X(yr), X(yr)))
        o.append(svg_text(X(yr), 124, str(yr), size=11.5, anchor="middle", cls="t-mut"))
    for i, (t, d, a, b, cls, up) in enumerate(ev):
        x = X(t)
        y = 18 if up else 140
        o.append('<line x1="%g" y1="105" x2="%g" y2="%d" class="edge e-mut"/>' % (x, x, y + 26 if up else y))
        o.append('<circle cx="%g" cy="105" r="6" class="%s"/>' % (x, cls))
        bx = min(max(x - 98, 6), W - 202)
        o.append('<rect x="%g" y="%d" width="196" height="56" rx="9" class="%s"/>' % (bx, y, cls))
        o.append(svg_text(bx + 9, y + 17, d, cls="t-b", size=12.5))
        o.append(svg_text(bx + 9, y + 33, a, size=11.5))
        o.append(svg_text(bx + 9, y + 48, b, size=11.5, cls="t-mut"))
    return fig(svg("".join(o), W, H, "Fusion API dates the research relies on"),
               "Every date is from the doc. Autodesk says not to ship preview APIs, which is why the custom "
               "feature stays opt-in.", "from the doc", "wide")


def arch_occ_attrs(pid="occ") -> str:
    need(SRC_TEXT["arch"], "An attribute written on the proxy `root > A:1 > B:1` lives in the root and is invisible from `A`'s own `B:1`",
         "Attributes on a component, or on its native sketch entities, are shared by every instance")
    W, H = 960, 400
    o = [arrow_defs(pid)]

    def node(x, y, label, cls="box", w=96):
        return ('<rect x="%g" y="%g" width="%g" height="30" rx="8" class="%s"/>' % (x, y, w, cls)
                + svg_text(x + w / 2, y + 20, label, anchor="middle", size=13, cls="t-b"))

    def link(x1, y1, x2, y2, cls="edge e-mut"):
        return '<path d="M%g %g V%g H%g" class="%s"/>' % (x1, y1, (y1 + y2) / 2 + 6, x2, cls) + \
               '<line x1="%g" y1="%g" x2="%g" y2="%g" class="%s"/>' % (x2, (y1 + y2) / 2 + 6, x2, y2, cls)

    def tag(x, y, text, cls="b-vio"):
        w = len(text) * 6.6 + 18
        return ('<path d="M%g %g h%g v22 h-%g l-8 -11 z" class="%s"/>' % (x + 8, y, w, w, cls)
                + svg_text(x + 14, y + 15.5, text, size=11.5, cls="t-mono"))

    # panel 1
    o.append('<rect x="10" y="10" width="440" height="330" rx="14" class="box2"/>')
    o.append(svg_text(26, 36, "Top-level design: write on the proxy", cls="t-b", size=14.5))
    o.append(node(185, 52, "root", "b-acc", 90))
    o.append(link(230, 82, 110, 112) + link(230, 82, 330, 112))
    o.append(node(62, 112, "A:1") + node(282, 112, "A:2"))
    o.append(link(110, 142, 110, 170) + link(330, 142, 330, 170))
    o.append(node(62, 170, "B:1") + node(282, 170, "B:1"))
    o.append(tag(28, 214, "root > A:1 > B:1  id 21"))
    o.append(tag(250, 214, "root > A:2 > B:1  id 22"))
    o.append('<path d="M120 238 C120 280 200 280 214 292" class="edge e-vio dash" marker-end="url(#%s-ah)"/>' % pid)
    o.append('<path d="M340 238 C340 280 260 280 246 292" class="edge e-vio dash" marker-end="url(#%s-ah)"/>' % pid)
    o.append('<ellipse cx="230" cy="296" rx="44" ry="8" class="b-acc"/><rect x="186" y="296" width="88" height="26" class="b-acc" stroke="none"/>'
             '<path d="M186 296 v26 a44 8 0 0 0 88 0 v-26" class="b-acc" fill="none"/>')
    o.append(svg_text(230, 316, "stored in root", size=12, anchor="middle", cls="t-b"))
    o.append(svg_text(26, 332, "one value per assembly path", size=12, cls="t-mut"))
    # panel 2
    o.append('<rect x="466" y="10" width="220" height="330" rx="14" class="box2"/>')
    o.append(svg_text(480, 36, "A, opened on its own", cls="t-b", size=14.5))
    o.append(node(528, 82, "A", "b-acc", 96))
    o.append(link(576, 112, 576, 160))
    o.append(node(528, 160, "B:1"))
    o.append('<rect x="490" y="214" width="172" height="44" rx="9" class="b-bad"/>')
    o.append(svg_text(576, 233, "neither id is", size=12.5, anchor="middle", cls="t-bad t-b"))
    o.append(svg_text(576, 250, "visible from here", size=12.5, anchor="middle", cls="t-bad t-b"))
    o.append(svg_text(480, 300, "The root's attributes do", size=12, cls="t-mut"))
    o.append(svg_text(480, 316, "not exist in A's document", size=12, cls="t-mut"))
    # panel 3
    o.append('<rect x="702" y="10" width="248" height="330" rx="14" class="box2"/>')
    o.append(svg_text(716, 36, "Write on the component", cls="t-b", size=14.5))
    o.append(node(778, 66, "component B", "b-warn", 110))
    o.append(tag(734, 108, "attr on B: id 21", "b-warn"))
    o.append('<path d="M800 136 C780 180 760 190 760 214" class="edge" marker-end="url(#%s-ah)"/>' % pid)
    o.append('<path d="M866 136 C886 180 900 190 900 214" class="edge" marker-end="url(#%s-ah)"/>' % pid)
    o.append(node(712, 218, "A:1 > B:1", "box", 96) + node(852, 218, "A:2 > B:1", "box", 92))
    o.append(svg_text(716, 278, "both instances read id 21:", size=12.5, cls="t-bad t-b"))
    o.append(svg_text(716, 296, "shared by every instance,", size=12, cls="t-ink2"))
    o.append(svg_text(716, 312, "native sketch entities too", size=12, cls="t-ink2"))
    o.append('<rect x="10" y="352" width="940" height="40" rx="10" class="b-good"/>')
    o.append(svg_text(26, 377, "So per-instance IDs work if they are written on occurrence proxies from the top design (panel 1), never on "
                      "the component (panel 3).", size=13, cls="t-ink2"))
    return fig(svg("".join(o), W, H, "Occurrence attributes are stored per assembly path"),
               "A Fusion developer, as quoted in the research: “The attributes of occurrence are saving into its "
               "target component.” The A:2 branch and the ID values are added for illustration.",
               "schematic", "wide")


def arch_regen(pid="regen") -> str:
    need(SRC_TEXT["arch"], "`ChangeParameterCommand`, `SketchEditDimensionCmdDef`, `FusionPasteNewCommand`, `RenameCommand`",
         "`documentOpened` and `documentSaving`", "It then runs a hidden `isAutoExecute` command so all edits are one undo step",
         "edits from palette or event handlers scatter across undo", "Group with `timeline.timelineGroups.add`")
    W, H = 960, 392
    o = [arrow_defs(pid)]
    trig = [("Edit a parameter", "ChangeParameterCommand"), ("Edit a sketch dimension", "SketchEditDimensionCmdDef"),
            ("Paste New", "FusionPasteNewCommand"), ("Rename", "RenameCommand"), ("…and more", "")]
    o.append(svg_text(14, 22, "User commands", cls="t-b t-mut", size=12.5))
    for i, (a, b) in enumerate(trig):
        y = 30 + i * 42
        o.append('<rect x="14" y="%d" width="250" height="36" rx="8" class="box"/>' % y)
        o.append(svg_text(26, y + 15, a, size=12.5, cls="t-b"))
        if b:
            o.append(svg_text(26, y + 30, b, size=11.5, cls="t-mono t-mut"))
        o.append('<path d="M264 %d C290 %d 290 120 318 120" class="edge e-mut" marker-end="url(#%s-ah)"/>' % (y + 18, y + 18, pid))
    o.append(svg_text(14, 258, "Document events", cls="t-b t-mut", size=12.5))
    for i, ev in enumerate(("documentOpened", "documentSaving")):
        y = 266 + i * 42
        o.append('<rect x="14" y="%d" width="250" height="34" rx="8" class="b-acc"/>' % y)
        o.append(svg_text(26, y + 22, ev, size=12.5, cls="t-mono"))
        o.append('<path d="M264 %d C300 %d 300 170 332 170" class="edge e-mut" marker-end="url(#%s-ah)"/>' % (y + 17, y + 17, pid))
    # handler
    o.append('<rect x="322" y="92" width="210" height="96" rx="12" class="b-vio"/>')
    o.append(svg_text(336, 116, "ui.commandTerminated", size=13, cls="t-mono t-b"))
    o.append(svg_text(336, 136, "handler switches on", size=12.5))
    o.append(svg_text(336, 153, "commandId; document", size=12.5))
    o.append(svg_text(336, 170, "events land here too", size=12.5))
    o.append('<line x1="532" y1="140" x2="568" y2="140" class="edge" marker-end="url(#%s-ah)"/>' % pid)
    o.append('<rect x="572" y="92" width="190" height="96" rx="12" class="box"/>')
    o.append(svg_text(586, 116, "hidden command", size=13, cls="t-b"))
    o.append(svg_text(586, 136, "isAutoExecute = true", size=12, cls="t-mono"))
    o.append(svg_text(586, 156, "regenerates every", size=12.5))
    o.append(svg_text(586, 173, "affected marker", size=12.5))
    o.append('<line x1="762" y1="140" x2="798" y2="140" class="edge e-good" marker-end="url(#%s-ahg)"/>' % pid)
    # undo stack (good)
    o.append(svg_text(802, 70, "Undo history", cls="t-b", size=12.5))
    for i, lab in enumerate(("Extrude1", "Edit Sketch3", "Update markers")):
        y = 80 + i * 32
        o.append('<rect x="802" y="%d" width="146" height="26" rx="6" class="%s"/>' % (y, "b-good" if i == 2 else "box2"))
        o.append(svg_text(814, y + 17, lab, size=12, cls="t-b" if i == 2 else "t-mut"))
    o.append(svg_text(802, 196, "one undo step", cls="t-good t-b", size=13))
    # bad lane
    o.append('<rect x="322" y="236" width="626" height="146" rx="12" class="b-bad"/>')
    o.append(svg_text(338, 260, "Without the hidden command", cls="t-bad t-b", size=13.5))
    o.append(svg_text(338, 280, "Edits made from a palette or an event handler scatter", size=12.5))
    o.append(svg_text(338, 297, "across undo: one Ctrl+Z per sub-edit.", size=12.5))
    for i, lab in enumerate(("set cells", "move decal", "write attr", "resize", "rename")):
        o.append('<rect x="%d" y="318" width="96" height="26" rx="6" class="box"/>' % (338 + i * 104))
        o.append(svg_text(386 + i * 104, 335, lab, size=11.5, anchor="middle", cls="t-mut"))
    o.append(svg_text(338, 368, "Gotcha 1: route them through a hidden auto-execute command; group with "
                      "timeline.timelineGroups.add.", size=12, cls="t-ink2"))
    return fig(svg("".join(o), W, H, "ParametricText-style regeneration without custom features"),
               "ParametricText regenerates without custom features: event hooks fire a hidden command, so a "
               "regeneration is one undo step. There is no insert event, so the same hooks drive the rescans of "
               "section 5. The sub-edit labels in the red lane are illustrative.", "schematic", "wide")


def arch_sequence(pid="seq") -> str:
    need(SRC_TEXT["arch"], "Messages sent at creation are lost, so Voronoi waits for a `started` ping",
         "`window.fusionJavaScriptHandler.handle` must return a non-empty string",
         "Handlers run on the main thread, so slow work freezes the page", "Palettes die on a workspace switch",
         "Hand results back with `registerCustomEvent` + `fireCustomEvent`")
    W, H = 960, 664
    PY, PA_, WK = 170, 540, 840
    o = [arrow_defs(pid)]
    for x, t1, t2, cls in ((PY, "Python add-in", "main thread", "b-acc"), (PA_, "Palette page", "Qt WebEngine", "b-good"),
                           (WK, "Worker thread", "local HTTP server", "b-vio")):
        o.append('<rect x="%d" y="10" width="200" height="46" rx="10" class="%s"/>' % (x - 100, cls))
        o.append(svg_text(x, 30, t1, anchor="middle", cls="t-b", size=14))
        o.append(svg_text(x, 47, t2, anchor="middle", cls="t-mut", size=12))
        o.append('<line x1="%d" y1="56" x2="%d" y2="%d" class="edge e-mut dash"/>' % (x, x, H - 16))

    def msg(y, x1, x2, label, sub="", cls="", mk="ah", lab_at=None):
        e = '<line x1="%d" y1="%d" x2="%d" y2="%d" class="edge %s" marker-end="url(#%s-%s)"/>' % (x1, y, x2, y, cls, pid, mk)
        lx = lab_at if lab_at is not None else (x1 + x2) / 2
        s = svg_text(lx, y - 8, label, anchor="middle", size=12.5, cls="t-mono")
        if sub:
            s += svg_text(lx, y + 17, sub, anchor="middle", size=11.5, cls="t-mut")
        return e + s

    def note(x, y, w, lines, cls="box"):
        h = 10 + 17 * len(lines)
        r = '<rect x="%d" y="%d" width="%d" height="%d" rx="8" class="%s"/>' % (x, y, w, h, cls)
        for i, l in enumerate(lines):
            r += svg_text(x + 10, y + 19 + i * 17, l, size=12)
        return r

    o.append(note(20, 70, 300, ["ui.palettes.add(id, name, htmlFileURL,", "  isVisible, showCloseButton,",
                                "  isResizable, width, height)"], "box"))
    o.append(msg(100, PY + 150, PA_ - 6, "creates the palette", "the page starts loading"))
    o.append('<line x1="%d" y1="150" x2="%d" y2="150" class="edge e-bad dash" marker-end="url(#%s-ahb)"/>' % (PY, PA_ - 30, pid))
    o.append(svg_text((PY + PA_) / 2, 142, "sendInfoToHTML(...) at creation", anchor="middle", size=12.5, cls="t-mono t-bad"))
    o.append(svg_text(PA_ - 22, 155, "✗", size=18, cls="t-bad t-b"))
    o.append(svg_text((PY + PA_) / 2, 167, "lost: the page is not listening yet", anchor="middle", size=11.5, cls="t-bad"))
    o.append(msg(212, PA_, PY, "adsk.fusionSendData('started', ...)", "returns a Promise; Voronoi's handshake"))
    o.append(note(20, 230, 140, ["incomingFromHTML", "HTMLEventArgs:", "action, data,", "returnData"], "b-acc"))
    o.append(msg(282, PY, PA_, "sendInfoToHTML(action, data)"))
    o.append(note(PA_ - 96, 294, 300, ["window.fusionJavaScriptHandler", "  .handle(action, data) must return", "  a non-empty string"], "b-good"))
    o.append(msg(376, PA_, PY, "fusionSendData(action, data)", "a user click in the page"))
    o.append('<rect x="%d" y="384" width="12" height="76" rx="3" class="b-warn"/>' % (PY - 6))
    o.append(note(PY + 16, 404, 270, ["handler runs on the main thread:", "slow work freezes the page"], "b-warn"))
    o.append('<line x1="%d" y1="476" x2="%d" y2="476" class="edge dash" marker-end="url(#%s-ah)"/>' % (PY, PA_, pid))
    o.append(svg_text((PY + PA_) / 2, 468, "returnData resolves the Promise", anchor="middle", size=12.5, cls="t-mono"))
    # worker lane
    o.append(note(WK - 92, 186, 184, ["never touches the", "Fusion API"], "b-vio"))
    o.append(msg(516, WK, PY, "fireCustomEvent(id, json)", "registerCustomEvent handler runs on main", cls="e-vio", lab_at=(WK + PY) / 2 + 60))
    o.append('<rect x="%d" y="250" width="12" height="268" rx="3" class="b-vio"/>' % (WK - 6))
    o.append(svg_text(WK + 14, 268, "HTTP request", size=12, cls="t-mono"))
    o.append(svg_text(WK + 14, 284, "from localhost", size=12, cls="t-mut"))
    # lifecycle
    o.append('<rect x="20" y="562" width="920" height="84" rx="10" class="b-bad"/>')
    o.append(svg_text(36, 586, "Lifecycle quirks", cls="t-bad t-b", size=13))
    o.append(svg_text(36, 606, "Palettes die on a workspace switch: rebuild and re-handshake.   Docking lost the palette, so "
                      "Voronoi disables docking.", size=12.5))
    o.append(svg_text(36, 626, "useNewWebBrowser is ignored: the engine is always Qt WebEngine now.   A palette fetch() "
                      "to localhost may hit CORS (unverified).", size=12.5))
    return fig(svg("".join(o), W, H, "Palette and Python messaging, with the worker-thread pattern"),
               "Top to bottom is time. Everything named is from the doc's section 6 and gotcha 7; the click in step "
               "five stands in for any page-initiated call.", "from the doc", "wide")


def cells_stats(grid):
    n = len(grid)
    seen, clusters = set(), []
    for r in range(n):
        for c in range(n):
            if grid[r][c] and (r, c) not in seen:
                stack, comp = [(r, c)], []
                seen.add((r, c))
                while stack:
                    a, b = stack.pop()
                    comp.append((a, b))
                    for da, db in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        q = (a + da, b + db)
                        if 0 <= q[0] < n and 0 <= q[1] < n and grid[q[0]][q[1]] and q not in seen:
                            seen.add(q)
                            stack.append(q)
                clusters.append(comp)
    corners = []
    for r in range(n - 1):
        for c in range(n - 1):
            a, b, cc, d = grid[r][c], grid[r][c + 1], grid[r + 1][c], grid[r + 1][c + 1]
            if (a and d and not b and not cc) or (b and cc and not a and not d):
                corners.append((r + 1, c + 1))
    return clusters, corners


def arch_cells() -> str:
    pick = None
    for i in range(50):
        g = marker_grid("DICT_4X4_50", i)
        cl, co = cells_stats(g)
        if len(co) >= 2 and len(cl) >= 2:
            pick = (i, g, cl, co)
            break
    if pick is None:
        i = 0
        g = marker_grid("DICT_4X4_50", 0)
        cl, co = cells_stats(g)
        pick = (i, g, cl, co)
    mid, g, cl, co = pick
    nblack = sum(map(sum, g))
    W, H, S = 960, 340, 216
    o = []
    cols = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)", "var(--s5)", "var(--violet)"]
    panels = [(18, "1. One box per black cell", "%d TemporaryBRep boxes" % nblack),
              (340, "2. Touching cells merged", "%d cluster%s, unioned once" % (len(cl), "" if len(cl) == 1 else "s")),
              (662, "3. Corner-only contacts", "%d places a union goes non-manifold" % len(co))]
    k = S / 6.0
    for px, t, sub in panels:
        o.append(svg_text(px, 22, t, cls="t-b", size=14))
        o.append(svg_text(px, 40, sub, cls="t-mut", size=12.5))
        o.append('<rect x="%d" y="54" width="%g" height="%g" fill="#fff" stroke="#bbb"/>' % (px, S, S))
    # panel 1: boxes with gaps
    for r in range(6):
        for c in range(6):
            if g[r][c]:
                o.append('<rect x="%g" y="%g" width="%g" height="%g" fill="#222" stroke="#fff" stroke-width="2"/>'
                         % (18 + c * k, 54 + r * k, k, k))
    # panel 2: clusters coloured
    for ci, comp in enumerate(cl):
        col = cols[ci % len(cols)]
        for (r, c) in comp:
            o.append('<rect x="%g" y="%g" width="%g" height="%g" style="fill:%s" data-tip="cluster %d: %d cells"/>'
                     % (340 + c * k, 54 + r * k, k + 0.4, k + 0.4, col, ci + 1, len(comp)))
    # panel 3: marker + red rings
    o.append(g_bits(g, 662, 54, S, 0))
    for (r, c) in co:
        o.append('<circle cx="%g" cy="%g" r="11" fill="none" stroke-width="3.5" class="edge e-bad"/>' % (662 + c * k, 54 + r * k))
    o.append(svg_text(18, 300, "DICT_4X4_50 id %d, border included (6 x 6 modules). Counts are computed from OpenCV's "
                      "bits at build time." % mid, size=12.5, cls="t-ink2"))
    o.append(svg_text(18, 320, "Fixes from the doc: merge touching cells (apriltag3D's _find_clusters), and for corner "
                      "contacts cut the white cells instead or merge outlines.", size=12.5, cls="t-ink2"))
    need(SRC_TEXT["arch"], "Corner-only contacts make non-manifold solids, so cut white cells or merge outlines")
    need(SRC_TEXT["prior"], "with touching cells merged (`_find_clusters`)")
    return fig(svg("".join(o), W, H, "Marker cells as geometry: boxes, clusters, corner contacts"),
               "Brian Ekins' advice in the prior-art research (union TemporaryBRep boxes, don't extrude each pixel), "
               "made concrete on one real marker. Panel 2 counts 4-connected clusters; whether "
               "<code>_find_clusters</code> uses exactly that rule is not stated in the research.",
               "generated by OpenCV", "wide", "cells-fig")


DONORS = [
    ("Voronoi", "https://github.com/hanskellner/Fusion360Voronoi", "Adopt", "v-adopt", False,
     "Selection filters (Sketches, Profiles, PlanarFaces); a face becomes a sketch; a palette edits the pattern; "
     "publish is one importSVG, then unfix the pinned points."),
    ("QRCoder", "https://github.com/tapnair/QRCoder", "Adapt for solids", "v-adapt", False,
     "Cells as TemporaryBRepManager boxes + booleanOperation; CustomGraphics preview; commit into a base feature, "
     "then combineFeatures."),
    ("apriltag3D", "https://github.com/arnoldad/apriltag3D", "Adapt", "v-adapt", True,
     "A custom feature wraps a base feature; compute rebuilds via updateBody; state in custom parameters because "
     "attribute writes lag compute by one transaction (#17)."),
    ("ParametricText", "https://github.com/thomasa88/ParametricText", "Adopt", "v-adopt", True,
     "Regenerates without custom features: commandTerminated and document events, then a hidden isAutoExecute "
     "command, so all edits are one undo step."),
    ("Image2Surface", "https://github.com/hanskellner/Fusion360Image2Surface", "Adopt the split", "v-adopt", False,
     "JS in the palette processes the image and sends OBJ text; Python calls meshBodies.add."),
    ("SpurGear", "https://github.com/AutodeskFusion360/SpurGear", "Skeleton only", "v-own", False,
     "A command-dialog skeleton: no selection input, no attributes, no regen."),
    ("TabGen", "https://github.com/hobbyistmaker/TabGen", "Avoid", "v-avoid", True,
     "Abandoned; uses parameter expressions, which cannot change topology."),
    ("Revise-Robotics", "https://github.com/Revise-Robotics/fusion360-apriltag-3d-code-generator", "Avoid", "v-avoid", False,
     "An inputBox script. Borrow its moms_apriltag table and appearance trick."),
    ("wglockner ArUco", "https://github.com/wglockner/ArUco-Generator-Fusion360-ADD-IN", "Avoid", "v-avoid", False,
     "pip-installs OpenCV at runtime and places a canvas."),
    ("WrapSketch", "https://github.com/hanskellner/Fusion360WrapSketch", "Avoid", "v-avoid", False,
     "Cylinder-only work in progress."),
]


def arch_donors() -> str:
    need(SRC_TEXT["arch"], "Only apriltag3D, ParametricText and TabGen regenerate on edit.")
    cards = []
    for name, url, verdict, vc, regen, text in DONORS:
        rg = ('<span class="chip" style="border-color:var(--good);color:var(--good)">regenerates on edit</span>' if regen
              else '<span class="chip">one-shot</span>')
        cards.append('<div class="card"><h4><a href="%s" target="_blank" rel="noopener">%s</a></h4>'
                     '<div class="tags"><span class="verdict %s">%s</span>%s</div><p>%s</p></div>'
                     % (url, esc(name), vc, esc(verdict), rg, esc(text)))
    return ('<div class="cards">%s</div>' % "".join(cards)
            + '<p class="narrow" style="font-size:14.5px;color:var(--muted)">Verdict board for section 1. '
              '“Regenerates on edit” follows the doc\'s line that only apriltag3D, ParametricText and TabGen do; '
              'the rest are one-shot.</p>')


def arch_custom_feature_steps() -> str:
    need(SRC_TEXT["arch"], "`CustomFeatureDefinition.create(id, name, icons)` at startup",
         "Register the edit command before setting `editCommandId`", "fall back to a plain base feature")
    steps = [("At startup", "<code>CustomFeatureDefinition.create(id, name, icons)</code>"),
             ("Edit command first", "Register it before setting <code>editCommandId</code> (apriltag3D)"),
             ("Input", "<code>comp.features.customFeatures.createInput(def)</code>"),
             ("Dependencies", "<code>addDependency</code>: bodies, faces, edges, vertices, sketches, sketch entities, "
                              "profiles, construction geometry, parameters"),
             ("State", "<code>addCustomParameter</code>"),
             ("Span", "<code>setStartAndEndFeatures</code>"),
             ("Add", "<code>add</code>, wrapped in try/except, falling back to a plain base feature")]
    lis = "".join("<li><b>%s</b>%s</li>" % (a, b) for a, b in steps)
    return '<ol class="steps">%s</ol>' % lis


def arch_decal_fig(pid="dec") -> str:
    need(SRC_TEXT["arch"], "`comp.decals.createInput(imagePath, [face], pointOnFace)`", "There is no width field",
         "whose origin must sit on the first face", "The image must be a PNG, JPEG or TIFF file",
         "Faces and transform change only via `Decal.redefine()`")
    W, H = 960, 300
    o = [arrow_defs(pid)]
    boxes = [(14, "comp.decals.createInput(", "imagePath, [face], pointOnFace)", "PNG, JPEG or TIFF file", "box"),
             (262, "DecalInput", "faces, isChainFaces, opacity,", "transform, targetBaseFeature", "b-acc"),
             (510, "comp.decals.add(input)", "returns a Decal", "", "box")]
    for x, a, b, c, cls in boxes:
        o.append('<rect x="%d" y="20" width="226" height="84" rx="12" class="%s"/>' % (x, cls))
        o.append(svg_text(x + 12, 44, a, size=12.5, cls="t-mono t-b"))
        o.append(svg_text(x + 12, 64, b, size=12, cls="t-mono"))
        if c:
            o.append(svg_text(x + 12, 84, c, size=12, cls="t-mut"))
    for x in (240, 488):
        o.append('<line x1="%d" y1="62" x2="%d" y2="62" class="edge" marker-end="url(#%s-ah)"/>' % (x, x + 20, pid))
    o.append('<rect x="14" y="124" width="722" height="160" rx="12" class="box2"/>')
    o.append(svg_text(30, 150, "After creation", cls="t-b", size=13.5))
    o.append(svg_text(30, 174, "Set directly:  imageFilename, opacity", size=12.5, cls="t-mono t-good"))
    o.append(svg_text(30, 196, "Only via Decal.redefine():  faces, transform", size=12.5, cls="t-mono t-warn"))
    o.append(svg_text(30, 216, "redefine() needs the timeline marker placed just before the decal.", size=12.5))
    o.append(svg_text(30, 240, "Proxy-face input: not documented (unverified).", size=12.5, cls="t-mut"))
    o.append(svg_text(30, 262, "Forum, Jan 2025: fails in direct-modeling designs; sometimes ignores transform.", size=12.5, cls="t-bad"))
    # face + decal with transform axes
    o.append('<polygon points="770,250 920,250 946,150 796,150" fill="#e9e6de" stroke="#8d8a83"/>')
    o.append('<g transform="matrix(98 0 -17.7 68 812 164)">%s</g>' % g_bits(marker_grid("DICT_4X4_50", 7), 0, 0, 1, 0.5))
    o.append('<circle cx="853" cy="198" r="5" style="fill:var(--decide)"/>')
    o.append('<line x1="853" y1="198" x2="905" y2="198" stroke="#d13b3b" stroke-width="2.5"/>')
    o.append('<line x1="853" y1="198" x2="862" y2="156" stroke="#1a9a4a" stroke-width="2.5"/>')
    o.append(svg_text(760, 32, "transform", cls="t-b", size=13))
    o.append(svg_text(760, 52, "size, rotation, flip", size=12.5))
    o.append(svg_text(760, 70, "all ride on it", size=12.5))
    o.append(svg_text(760, 90, "no width field", size=12.5, cls="t-bad t-b"))
    o.append(svg_text(760, 118, "origin must sit on the first face", size=11.5, cls="t-mut"))
    o.append(svg_text(760, 282, "(drawn for illustration)", size=11, cls="t-mut"))
    return fig(svg("".join(o), W, H, "The decal API as the research describes it"),
               "Released API since September 2024. A decal inside a shared component shows on every instance, and it "
               "is appearance only, never physical.", "from the doc", "wide")


def arch_registry(pid="reg") -> str:
    need(SRC_TEXT["arch"], "Registry on `design.attributes`: `nextId`, `storageVersion`, one value per ID",
         "Dedupe by keeping `markerId -> entityToken`: the entity whose token does not match is the copy",
         "sweep orphans (`parent` is None)")
    W, H = 960, 300
    o = [arrow_defs(pid)]
    o.append('<rect x="14" y="14" width="292" height="196" rx="12" class="b-acc"/>')
    o.append(svg_text(28, 38, "design.attributes: the registry", cls="t-b", size=13.5))
    for i, (k, v) in enumerate((("nextId", "18"), ("storageVersion", "1"), ("id 17", "token tok_A"), ("id 12", "token tok_B"))):
        y = 52 + i * 36
        o.append('<rect x="28" y="%d" width="264" height="28" rx="6" class="box"/>' % y)
        o.append(svg_text(40, y + 19, k, size=12.5, cls="t-mono t-b"))
        o.append(svg_text(280, y + 19, v, size=12.5, cls="t-mono", anchor="end"))
    o.append('<rect x="370" y="40" width="250" height="78" rx="12" class="box"/>')
    o.append(svg_text(384, 64, "Sketch3 rectangle", cls="t-b", size=13))
    o.append(svg_text(384, 84, "entityToken  tok_A", size=12.5, cls="t-mono"))
    o.append(svg_text(384, 104, "back-pointer: markerId 17", size=12.5, cls="t-mono t-vio"))
    o.append('<path d="M370 79 C340 79 330 140 292 140" class="edge e-good" marker-end="url(#%s-ahg)"/>' % pid)
    o.append(svg_text(318, 112, "match", size=12, cls="t-good t-b"))
    o.append('<rect x="370" y="150" width="250" height="78" rx="12" class="b-bad"/>')
    o.append(svg_text(384, 174, "its Paste New copy", cls="t-b", size=13))
    o.append(svg_text(384, 194, "entityToken  tok_C", size=12.5, cls="t-mono"))
    o.append(svg_text(384, 214, "back-pointer: markerId 17", size=12.5, cls="t-mono t-vio"))
    o.append('<path d="M620 79 C650 79 650 150 636 168" class="edge dash" marker-end="url(#%s-ah)"/>' % pid)
    o.append(svg_text(652, 120, "Paste New copies", size=12, cls="t-mut"))
    o.append(svg_text(652, 136, "the attribute (inferred)", size=12, cls="t-mut"))
    o.append('<rect x="690" y="150" width="256" height="78" rx="12" class="box2"/>')
    o.append(svg_text(704, 174, "token does not match", cls="t-b t-bad", size=13))
    o.append(svg_text(704, 194, "so this one is the copy:", size=12.5))
    o.append(svg_text(704, 214, "give it a fresh ID", size=12.5))
    o.append('<line x1="620" y1="189" x2="686" y2="189" class="edge e-bad" marker-end="url(#%s-ahb)"/>' % pid)
    o.append('<rect x="14" y="240" width="932" height="50" rx="10" class="box2"/>')
    o.append(svg_text(28, 262, "Lookup: findAttributes over every Design product in the document, reading parent and "
                      "otherParents.", size=12.5))
    o.append(svg_text(28, 280, "B-Rep attributes are never auto-deleted: sweep orphans whose parent is None. A migration "
                      "command handles old storage versions.", size=12.5))
    return fig(svg("".join(o), W, H, "Attribute registry with entityToken copy detection"),
               "ParametricText's layout plus the doc's dedupe rule. Token strings and ID values are illustrative; "
               "that Paste New copies attributes is the doc's inference.", "schematic", "wide")


def arch_intent() -> str:
    need(SRC_TEXT["arch"], "Part designs have no occurrences. Assembly designs allow no sketches or features",
         "So generate markers in parts, keep the instance-ID map in the assembly, and treat Hybrid as before")
    y, n = '<span class="cell c-y">✓</span>', '<span class="cell c-n">✗</span>'
    rows = [("Occurrences", n, y, y), ("Sketches and features", y, n, y),
            ("Marker plan", "generate markers here", "keep the instance-ID map here", "as before")]
    trs = "".join("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % r for r in rows)
    return fig('<div class="tablewrap" style="margin:0"><table class="md"><thead><tr><th>design.designIntent</th>'
               '<th>Part</th><th>Assembly</th><th>Hybrid</th></tr></thead><tbody>%s</tbody></table></div>' % trs,
               "Design intent (January 2026) as the research states it.", "from the doc")


def arch_packaging() -> str:
    need(SRC_TEXT["arch"], "apriltag3D ships families as a 3.4 MB `.py`", "Ship `.py`; compiled wheels break on each upgrade")
    return ('<div class="stats"><div class="stat"><b>3.12 → 3.14</b><span>Fusion\'s Python, January 2026</span></div>'
            '<div class="stat"><b>.py only</b><span>compiled wheels break on each upgrade</span></div>'
            '<div class="stat"><b>3.4 MB</b><span>apriltag3D\'s AprilTag families, as one .py</span></div>'
            '<div class="stat"><b>once, offline</b><span>export OpenCV\'s ArUco dictionaries to JSON</span></div></div>')


def build_arch() -> str:
    text = SRC_TEXT["arch"]
    title, pre, secs = split_md(text)
    keys = [s["key"] for s in secs]
    want = ["key-api-facts-read-this-first", "1", "2", "3", "4", "5", "6", "7", "8", "gotchas",
            "recommended-reference-architecture"]
    for k in want:
        if k not in keys:
            warn("architecture section missing: " + k)
    hooks = {
        "key-api-facts-read-this-first": {"toc": "Key API facts", "lead": arch_api_timeline(), "after": arch_occ_attrs()},
        "1": {"lead": arch_donors(), "after": arch_regen()},
        "2": {"lead": arch_custom_feature_steps()},
        "3": {"after": arch_decal_fig()},
        "4": {"after": arch_registry()},
        "5": {"after": arch_intent()},
        "6": {"after": arch_sequence()},
        "7": {"lead": arch_packaging()},
        "gotchas": {"after": arch_cells()},
        "recommended-reference-architecture": {"lead": arch_layers(), "toc": "Reference architecture"},
    }
    body, toc = render_sections(secs, hooks)
    intro = render_md(pre)
    banner = callout('Written 2026-10-02. Since then Zach has decided the tags will be <b>3D printed</b> as real '
                     'geometry and that the ArUco step must regenerate in place in the timeline, which puts weight on the '
                     'geometry and regeneration layers here. See the <a href="index.html#decisions">decisions</a>.', "note")
    teaser = callout('Jump to the <a href="#layers-fig">layered reference architecture</a> for the one-picture answer.', "good")
    return page_html("arch", "Fusion add-in architecture for markers",
                     "Research 2 of 3 · architecture donors",
                     "Which Fusion add-ins can we borrow from, layer by layer, and which API facts constrain an ArUco "
                     "or ChArUco add-in: custom features still in preview, decals released, attributes stored per "
                     "assembly path, palettes that lose early messages.",
                     "Source: <code>docs/research_aruco_fusion_architecture.md</code>",
                     banner + intro + teaser + body, toc,
                     "Architecture donors and Fusion API facts for an ArUco/ChArUco add-in.")


# ========================================================== wireframes A-E
# Drawn from the ASCII mocks in the proposal, in Fusion's light UI. Labels
# are the mock's own; the chrome (toolbar tabs, ViewCube, timeline) is
# generic Fusion furniture so the mock reads as Fusion at a glance.

F = dict(app="#2b2b2b", tb="#f5f5f5", tb2="#e9e9e9", line="#c9c9c9", ink="#262626", mut="#707070",
         acc="#0696d7", c1="#f4f6f8", c2="#d5dbe2", panel="#ffffff", sel="#d3ebfa", dlg="#3d3d3d",
         warn="#d98a00", bad="#d64545", sk="#1f6fd1", skfill="#cfe0f5")
FF = 'font-family="Segoe UI, Arial, sans-serif"'
FM = 'font-family="Consolas, Menlo, monospace"'


def ft(x, y, s, size=11, fill=None, weight=None, anchor="start", mono=False, extra=""):
    a = ' font-size="%s" fill="%s"' % (size, fill or F["ink"])
    if weight:
        a += ' font-weight="%s"' % weight
    if anchor != "start":
        a += ' text-anchor="%s"' % anchor
    return '<text x="%g" y="%g" %s%s%s>%s</text>' % (x, y, FM if mono else FF, a, extra, esc(s))


def frect(x, y, w, h, fill, stroke=None, rx=0, sw=1, extra=""):
    s = ' stroke="%s" stroke-width="%s"' % (stroke, sw) if stroke else ""
    return '<rect x="%g" y="%g" width="%g" height="%g" rx="%g" fill="%s"%s%s/>' % (x, y, w, h, rx, fill, s, extra)


def icon(kind, x, y):
    """12x12 browser icons."""
    if kind == "doc":
        return frect(x, y, 11, 12, "#fff", "#7a8a9a", 1) + frect(x + 2, y + 3, 7, 1.4, "#7a8a9a") + frect(x + 2, y + 6, 7, 1.4, "#7a8a9a")
    if kind == "folder":
        return frect(x, y + 2, 12, 9, "#e9c46a", "#b8913a", 1) + frect(x, y, 5, 3, "#e9c46a", "#b8913a", 1)
    if kind == "comp":
        return ('<polygon points="%g,%g %g,%g %g,%g %g,%g" fill="#cfe2f5" stroke="#3f78b5"/>'
                % (x, y + 3, x + 6, y, x + 12, y + 3, x + 6, y + 6)) + \
               ('<polygon points="%g,%g %g,%g %g,%g %g,%g" fill="#9fc3e8" stroke="#3f78b5"/>'
                % (x, y + 3, x + 6, y + 6, x + 6, y + 12, x, y + 9)) + \
               ('<polygon points="%g,%g %g,%g %g,%g %g,%g" fill="#7eaedd" stroke="#3f78b5"/>'
                % (x + 12, y + 3, x + 6, y + 6, x + 6, y + 12, x + 12, y + 9))
    if kind == "sketch":
        return frect(x, y, 12, 12, "#fff", "#c0392b", 1) + '<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#1f6fd1" stroke-width="1.5"/>' % (x + 2, y + 10, x + 10, y + 2)
    if kind == "body":
        return frect(x, y + 1, 12, 10, "#8fa7c0", "#4f6880", 1, 1)
    if kind == "origin":
        return '<circle cx="%g" cy="%g" r="5" fill="#fff" stroke="#333"/><line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#d13b3b"/><line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#1a9a4a"/>' % (
            x + 6, y + 6, x + 6, y + 6, x + 12, y + 6, x + 6, y + 6, x + 6, y)
    if kind == "decal":
        return frect(x, y + 1, 12, 10, "#fff", "#6b6b6b", 1) + '<polygon points="%g,%g %g,%g %g,%g" fill="#4a9a5a"/>' % (x + 1, y + 10, x + 5, y + 4, x + 9, y + 10) + '<circle cx="%g" cy="%g" r="1.5" fill="#e0a020"/>' % (x + 9, y + 4)
    if kind == "marker":
        return frect(x, y, 12, 12, "#111") + frect(x + 3, y + 3, 3, 3, "#fff") + frect(x + 6, y + 6, 3, 3, "#fff")
    return ""


def browser(x, y, w, rows, title="BROWSER"):
    """rows: (depth, icon, label, flags, note)"""
    h = 30 + 21 * len(rows) + 6
    o = [frect(x, y, w, h, "#ffffff", F["line"], 3, 1, ' opacity="0.97"'),
         frect(x, y, w, 22, F["tb2"], None, 3), ft(x + 8, y + 15, title, 9.5, F["mut"], "600")]
    for i, (d, ic, lab, flags, note) in enumerate(rows):
        ry = y + 28 + i * 21
        if "sel" in flags:
            o.append(frect(x + 2, ry - 3, w - 4, 20, F["sel"]))
        cx = x + 8 + d * 14
        if "open" in flags:
            o.append('<polygon points="%g,%g %g,%g %g,%g" fill="#555"/>' % (cx, ry + 4, cx + 7, ry + 4, cx + 3.5, ry + 9))
        elif "closed" in flags:
            o.append('<polygon points="%g,%g %g,%g %g,%g" fill="#555"/>' % (cx + 1, ry + 2, cx + 6, ry + 6.5, cx + 1, ry + 11))
        o.append(icon(ic, cx + 11, ry))
        col = F["mut"] if "hidden" in flags else F["ink"]
        o.append(ft(cx + 28, ry + 10.5, lab, 11, col, "600" if "bold" in flags else None))
        if "warn" in flags:
            o.append(frect(x + w - 50, ry - 1, 42, 16, "#fdebc8", F["warn"], 3) + ft(x + w - 29, ry + 10.5, "! dup", 10, "#8a5300", "700", "middle"))
        if note:
            o.append(ft(cx + 28 + len(lab) * 6.2 + 8, ry + 10.5, note, 10.5, F["mut"]))
    return "".join(o)


GROUPS = ["CREATE", "MODIFY", "ASSEMBLE", "CONSTRUCT", "INSPECT", "INSERT", "SELECT"]
GCOL = {"CREATE": "#3d8fd6", "MODIFY": "#6aa84f", "ASSEMBLE": "#b07cc6", "CONSTRUCT": "#e69138",
        "INSPECT": "#45818e", "INSERT": "#cc4125", "SELECT": "#666666"}


def fusion_window(pid, x, y, w, h, doc, addins, groups=None, open_group=None, tl=None):
    groups = GROUPS if groups is None else groups
    o = ['<defs><linearGradient id="%s-cg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="%s"/>'
         '<stop offset="1" stop-color="%s"/></linearGradient><filter id="%s-sh" x="-10%%" y="-10%%" width="120%%" height="130%%">'
         '<feDropShadow dx="0" dy="2" stdDeviation="3" flood-opacity="0.25"/></filter></defs>' % (pid, F["c1"], F["c2"], pid)]
    o.append(frect(x, y, w, h, F["tb"], "#9a9a9a", 6, 1))
    o.append(frect(x, y, w, 26, F["app"], None, 6) + frect(x, y + 14, w, 12, F["app"]))
    for k in range(3):
        o.append(frect(x + 10 + k * 18, y + 7, 12, 12, "#5a5a5a", None, 2))
    o.append(frect(x + 72, y + 4, min(170, len(doc) * 7 + 50), 22, F["tb"], None, 4) + ft(x + 84, y + 19, doc, 11.5, F["ink"], "600"))
    # toolbar
    o.append(frect(x + 8, y + 32, 74, 50, "#fff", F["line"], 3) + ft(x + 45, y + 52, "DESIGN", 10.5, F["ink"], "700", "middle")
             + ft(x + 45, y + 68, "▾", 10, F["mut"], None, "middle"))
    tabs = ["SOLID", "SURFACE", "MESH", "SHEET METAL", "PLASTIC", "UTILITIES"]
    tx = x + 96
    for t in tabs:
        tw = len(t) * 6.4 + 16
        if tx + tw > x + w - 10:
            break
        o.append(ft(tx, y + 41, t, 9.5, F["ink"] if t == "SOLID" else F["mut"], "700" if t == "SOLID" else None))
        if t == "SOLID":
            o.append(frect(tx, y + 44, tw - 16, 2, F["acc"]))
        tx += tw
    gx = x + 96
    for g in groups:
        gw = max(58, len(g) * 7 + 18)
        if g == open_group:
            o.append(frect(gx - 4, y + 48, gw, 38, "#dcecf8", None, 3))
        o.append(frect(gx + gw / 2 - 13, y + 51, 22, 18, GCOL.get(g, "#777"), None, 3, 1, ' opacity="0.85"'))
        o.append(ft(gx + gw / 2 - 2, y + 81, g + " ▾", 8.8, F["ink"], "600", "middle"))
        gx += gw + 4
    if addins:
        ax = gx + 6
        o.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="%s"/>' % (ax - 6, y + 50, ax - 6, y + 84, F["line"]))
        for a in addins:
            aw = max(46, len(a) * 5.6 + 12)
            o.append(frect(ax, y + 50, aw, 22, "#fff", "#7aa6c9", 4) + ft(ax + aw / 2, y + 65, a, 9, "#1b5d8f", "600", "middle"))
            ax += aw + 4
        o.append(ft((gx + ax) / 2, y + 81, "ADD-INS ▾", 8.8, F["ink"], "600", "middle"))
    # canvas
    cy0, cy1 = y + 88, y + h - (34 if tl is not None else 0)
    o.append(frect(x + 1, cy0, w - 2, cy1 - cy0, "url(#%s-cg)" % pid))
    vx, vy = x + w - 70, cy0 + 16
    o.append('<polygon points="%g,%g %g,%g %g,%g %g,%g" fill="#fafafa" stroke="#999"/>' % (vx, vy + 12, vx + 24, vy, vx + 48, vy + 12, vx + 24, vy + 24))
    o.append('<polygon points="%g,%g %g,%g %g,%g %g,%g" fill="#e6e6e6" stroke="#999"/>' % (vx, vy + 12, vx + 24, vy + 24, vx + 24, vy + 50, vx, vy + 38))
    o.append('<polygon points="%g,%g %g,%g %g,%g %g,%g" fill="#d4d4d4" stroke="#999"/>' % (vx + 48, vy + 12, vx + 24, vy + 24, vx + 24, vy + 50, vx + 48, vy + 38))
    o.append(ft(vx + 24, vy + 15, "TOP", 7, "#555", "600", "middle") + ft(vx + 12, vy + 34, "FRONT", 6.5, "#555", None, "middle"))
    nx = x + w / 2 - 80
    o.append(frect(nx, cy1 - 30, 160, 22, "#ffffffd0", F["line"], 11))
    for k in range(6):
        o.append(frect(nx + 14 + k * 23, cy1 - 24, 12, 10, "#9aa", None, 2))
    if tl is not None:
        o.append(frect(x + 1, cy1, w - 2, h - (cy1 - y) - 1, F["tb"]))
        o.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="%s"/>' % (x, cy1, x + w, cy1, F["line"]))
        px = x + 12
        for sym in ("⏮", "◀", "▶", "⏭"):
            o.append(ft(px, cy1 + 22, sym, 12, "#555"))
            px += 18
        fx = px + 12
        for lab, hl in tl:
            fw = max(26, len(lab) * 6.6 + 12)
            o.append(frect(fx, cy1 + 7, fw, 20, "#bfe1f6" if hl else "#fff", F["acc"] if hl else "#aaa", 3, 1.6 if hl else 1))
            o.append(ft(fx + fw / 2, cy1 + 21, lab, 10, F["ink"], "700" if hl else None, "middle", True))
            fx += fw + 4
    return "".join(o), (x + 1, cy0, w - 2, cy1 - cy0), fx if tl is not None else None


def dialog(pid, x, y, w, title, rows, buttons=("OK", "Cancel")):
    rh = 27
    h = 30 + sum(rh if r[0] != "chip" else 22 for r in rows) + 44
    o = ['<g filter="url(#%s-sh)">' % pid, frect(x, y, w, h, "#f7f7f7", "#9a9a9a", 4), '</g>',
         frect(x, y, w, 26, F["dlg"], None, 4), frect(x, y + 14, w, 12, F["dlg"]),
         ft(x + 10, y + 17.5, title, 11, "#fff", "700"), ft(x + w - 16, y + 17.5, "ⓘ", 11, "#ccc")]
    cy = y + 32
    lx, cx = x + 10, x + w * 0.36
    cw = x + w - 10 - cx
    for r in rows:
        k = r[0]
        if k == "section":
            o.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#bbb"/>' % (x + 8, cy + 10, x + w - 8, cy + 10))
            o.append(frect(x + 14, cy + 2, len(r[1]) * 6 + 10, 14, "#f7f7f7") + ft(x + 19, cy + 13, r[1], 10, F["mut"], "600"))
            cy += rh
            continue
        if k == "chip":
            _, txt, ok = r
            o.append(frect(x + 10, cy, w - 20, 19, "#e6f3ea" if ok else "#fbefd9", "#7cb58e" if ok else "#d9a441", 3))
            o.append(ft(x + 16, cy + 13.5, txt, 10, F["ink"], None, "start", True))
            cy += 22
            continue
        lab = r[1]
        o.append(ft(lx, cy + 15, lab, 10.5, F["ink"]))
        if k == "sel":
            o.append(frect(cx, cy + 2, cw, 19, F["acc"], None, 3) + ft(cx + 8, cy + 15.5, "↖  " + r[2], 10.5, "#fff", "600"))
        elif k == "drop":
            o.append(frect(cx, cy + 2, cw, 19, "#fff", "#aaa", 2) + ft(cx + 6, cy + 15.5, r[2], 10.5, F["ink"], None, "start", True)
                     + ft(cx + cw - 10, cy + 15.5, "▾", 10, "#555"))
        elif k == "input":
            bw = 0
            if len(r) > 3 and r[3]:
                bw = len(r[3]) * 6.5 + 12
                o.append(frect(cx + cw - bw, cy + 2, bw, 19, "#e8e8e8", "#aaa", 2) + ft(cx + cw - bw / 2, cy + 15.5, r[3], 10, F["ink"], None, "middle"))
            o.append(frect(cx, cy + 2, cw - bw - (6 if bw else 0), 19, "#fff", "#aaa", 2) + ft(cx + 6, cy + 15.5, r[2], 10.5, F["ink"], None, "start", True))
        elif k == "radio":
            ox = cx
            for j, opt in enumerate(r[2]):
                on = j == r[3]
                o.append('<circle cx="%g" cy="%g" r="5" fill="#fff" stroke="#777"/>' % (ox + 5, cy + 11))
                if on:
                    o.append('<circle cx="%g" cy="%g" r="2.6" fill="%s"/>' % (ox + 5, cy + 11, F["acc"]))
                o.append(ft(ox + 13, cy + 15, opt, 10.5))
                ox += len(opt) * 6.2 + 24
        elif k == "ro":
            o.append(ft(cx, cy + 15, r[2], 10.5, F["ink"], "600"))
        elif k == "pair":
            half = cw / 2 - 4
            o.append(frect(cx, cy + 2, 38, 19, "#fff", "#aaa", 2) + ft(cx + 6, cy + 15.5, r[2], 10.5, F["ink"], None, "start", True))
            o.append(ft(cx + 46, cy + 15, r[3], 10.5) + frect(cx + cw - 46, cy + 2, 46, 19, "#fff", "#aaa", 2)
                     + ft(cx + cw - 40, cy + 15.5, r[4], 10.5, F["ink"], None, "start", True))
        elif k == "dropnum":
            o.append(frect(cx, cy + 2, cw - 50, 19, "#fff", "#aaa", 2) + ft(cx + 6, cy + 15.5, r[2], 10.5, F["ink"], None, "start", True)
                     + ft(cx + cw - 60, cy + 15.5, "▾", 10, "#555"))
            o.append(frect(cx + cw - 44, cy + 2, 44, 19, "#fff", "#aaa", 2) + ft(cx + cw - 38, cy + 15.5, r[3], 10.5, F["ink"], None, "start", True))
        elif k == "cam":
            o.append(frect(cx, cy + 2, 92, 19, "#fff", "#aaa", 2) + ft(cx + 5, cy + 15.5, r[2], 9.5, F["ink"], None, "start", True))
            o.append(ft(cx + 98, cy + 15, "Z", 10) + frect(cx + 108, cy + 2, 30, 19, "#fff", "#aaa", 2) + ft(cx + 112, cy + 15.5, r[3], 9.5, F["ink"], None, "start", True))
            o.append(ft(cx + 142, cy + 15, "ang", 10) + frect(cx + 162, cy + 2, cw - 162, 19, "#fff", "#aaa", 2) + ft(cx + 166, cy + 15.5, r[4], 9.5, F["ink"], None, "start", True))
        cy += rh
    bx = x + w - 10
    for b in reversed(buttons):
        bw = max(56, len(b) * 7 + 18)
        bx -= bw
        o.append(frect(bx, cy + 10, bw, 24, F["acc"] if b in ("OK", "Send") else "#fff", F["acc"] if b in ("OK", "Send") else "#999", 3)
                 + ft(bx + bw / 2, cy + 26, b, 11, "#fff" if b in ("OK", "Send") else F["ink"], "600", "middle"))
        bx -= 8
    return "".join(o)


def menu(x, y, items, w=170, hl=None, header=None):
    h = 8 + 22 * len(items) + (20 if header else 0)
    o = [frect(x, y, w, h, "#fff", "#b5b5b5", 3, 1, ' filter="url(#MENU-sh)"')]
    yy = y + 4
    if header:
        o.append(ft(x + 10, yy + 13, header, 9, F["mut"], "700"))
        yy += 20
    for i, it in enumerate(items):
        if it == "-":
            o.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#ddd"/>' % (x + 6, yy + 11, x + w - 6, yy + 11))
        else:
            grey = it.startswith("~")
            lab = it.lstrip("~")
            if hl == i:
                o.append(frect(x + 3, yy, w - 6, 22, F["sel"], None, 2))
            if lab.startswith("[#]"):
                o.append(icon("marker", x + 10, yy + 5))
                lab = lab[3:].strip()
                o.append(ft(x + 28, yy + 15, lab, 11, F["ink"], "600"))
            else:
                o.append(ft(x + 12, yy + 15, lab, 11, "#9a9a9a" if grey else F["ink"]))
        yy += 22
    return "".join(o)


def triad(x, y, s=34, z_in=True):
    return ('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#d13b3b" stroke-width="2.4" marker-end="url(#TRI-r)"/>'
            '<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#1a9a4a" stroke-width="2.4" marker-end="url(#TRI-g)"/>'
            '<circle cx="%g" cy="%g" r="6" fill="#fff" stroke="#2a62c9" stroke-width="1.8"/>'
            '<path d="M%g %g l6 6 M%g %g l-6 6" stroke="#2a62c9" stroke-width="1.6"/>'
            % (x, y, x + s, y, x, y, x, y + s, x, y, x - 3, y - 3, x + 3, y - 3)) + \
        ft(x + s + 4, y + 4, "+X", 10, "#d13b3b", "700") + ft(x - 8, y + s + 2, "+Y", 10, "#1a9a4a", "700", "end") + \
        ft(x - 12, y - 9, "+Z in", 9.5, "#2a62c9", "700", "end")


def wire_defs(pid):
    return ('<defs><marker id="TRI-r" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">'
            '<path d="M0,0 L10,5 L0,10 z" fill="#d13b3b"/></marker>'
            '<marker id="TRI-g" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">'
            '<path d="M0,0 L10,5 L0,10 z" fill="#1a9a4a"/></marker>'
            '<marker id="WA-%s" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
            '<path d="M0,0 L10,5 L0,10 z" fill="#555"/></marker>'
            '<filter id="MENU-sh" x="-10%%" y="-10%%" width="125%%" height="130%%"><feDropShadow dx="0" dy="2" '
            'stdDeviation="3" flood-opacity="0.25"/></filter>'
            '<pattern id="%s-hatch" width="10" height="10" patternUnits="userSpaceOnUse">'
            '<rect width="10" height="10" fill="#cfe3f7" opacity="0.65"/><rect width="5" height="5" fill="#1f6fd1" opacity="0.55"/>'
            '<rect x="5" y="5" width="5" height="5" fill="#1f6fd1" opacity="0.55"/></pattern></defs>') % (pid, pid)


def wsvg(inner, w, h, label, pid):
    inner = inner.replace("TRI-r", pid + "-TRI-r").replace("TRI-g", pid + "-TRI-g").replace("MENU-sh", pid + "-MENU-sh")
    return ('<svg viewBox="0 0 %d %d" role="img" aria-label="%s" %s><title>%s</title>%s</svg>'
            % (w, h, esc(label), FF, esc(label), inner))


def wire_a() -> str:
    pid = "wa"
    W, H = 1000, 640
    win, (cx, cy, cw, ch), _ = fusion_window(pid, 0, 0, W, H, "Wrist v3", ["CAD Exporter", "Datasheet"],
                                             open_group="CREATE", tl=[("sk", 0), ("ext", 0), ("sk", 0), ("#17", 1)])
    o = [wire_defs(pid), win]
    # the face and the rectangle with real cells (DICT_4X4_50 id 17)
    k = 7.0
    rx, ry = 340, 196
    o.append(frect(rx - 70, ry - 50, 35 * k + 140, 35 * k + 100, "#e4e2dc", "#a8a49b", 6))
    o.append(frect(rx, ry, 35 * k, 35 * k, "none", F["sk"], 0, 2))
    for (px, py) in ((rx, ry), (rx + 35 * k, ry), (rx, ry + 35 * k), (rx + 35 * k, ry + 35 * k)):
        o.append(frect(px - 3, py - 3, 6, 6, F["sk"]))
    g = marker_grid("DICT_4X4_50", 17)
    q = (35 - 26) / 2 * k
    m = 26 * k / 6
    for r in range(6):
        for c in range(6):
            if g[r][c]:
                o.append(frect(rx + q + c * m, ry + q + r * m, m, m, "#2b3f5c", "#6f9fdc", 0, 0.8))
    o.append(frect(rx + q, ry + q, 26 * k, 26 * k, "none", F["sk"], 0, 1.2))
    o.append(triad(rx + q, ry + q, 40))
    o.append(ft(rx + q + 16, ry + q - 26, "aruco_frame", 10, "#2a62c9", "700"))
    o.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#555" marker-start="url(#WA-wa)" marker-end="url(#WA-wa)"/>'
             % (rx, ry + 35 * k + 22, rx + 35 * k, ry + 35 * k + 22))
    o.append(ft(rx + 35 * k / 2, ry + 35 * k + 38, "35.00 mm", 11, "#333", None, "middle", True))
    o.append(ft(rx + 35 * k / 2, ry + 35 * k + 70, "real cells in the sketch; triad at the top-left corner", 11, "#444", None, "middle"))
    o.append(ft(rx + 35 * k / 2, ry + 35 * k + 85, "(+X right, +Y down, +Z in)", 11, "#444", None, "middle"))
    # browser
    rows = [(0, "doc", "Wrist v3", {"open", "bold"}, ""), (1, "folder", "Sketches", {"open"}, ""),
            (2, "sketch", "Sketch3", set(), "(rectangle)"), (1, "marker", "Marker 17", {"open", "sel", "bold"}, "4X4_50"),
            (2, "sketch", "Cells sketch", set(), ""), (2, "body", "Body  black_cells", set(), ""),
            (2, "origin", "Joint Origin  aruco_frame", set(), "")]
    o.append(browser(10, cy + 190, 236, rows))
    # CREATE menu
    o.append(menu(96, 88, ["~New Component", "~Create Sketch", "~Extrude", "~Revolve", "-", "[#] Marker Feature", "[#] Marker Socket"], 190, hl=5))
    # dialog
    rows = [("sel", "Input", "1 profile selected"), ("radio", "Kind", ["single", "grid", "ChArUco"], 0),
            ("drop", "Dictionary", "DICT_4X4_50"), ("input", "ID", "auto: 17", "lock"),
            ("pair", "Border", "1", "Quiet zone", "1.5"), ("ro", "Fit", "26.0 mm in 35 x 35 mm"),
            ("dropnum", "Output", "Sketch + emboss", "0.6"), ("section", "Suggest"), ("cam", "Camera", "D435 1080p", "0.8", "45"),
            ("chip", "* 4X4_50   26 mm  5.4 px/bit  ok", True), ("chip", "  5X5_100  26 mm  4.6 px/bit  low", False)]
    o.append(dialog(pid, 676, 100, 314, "MARKER FEATURE", rows))
    o.append(frect(842, H - 64, 150, 24, "#fffbe6", "#d9b44a", 4) + ft(917, H - 48, "double-click to edit", 10.5, "#6b5200", None, "middle"))
    return wsvg("".join(o), W, H, "Proposal A wireframe: Marker Feature dialog, browser and timeline", "wa")


def wire_b() -> str:
    pid = "wb"
    W, H = 1000, 600
    win, (cx, cy, cw, ch), _ = fusion_window(pid, 0, 0, 612, H, "Robot", ["CAD Exporter", "Datasheet", "Studio"],
                                             groups=["CREATE", "MODIFY", "ASSEMBLE", "INSPECT"], tl=[("sk", 0), ("ext", 0), ("decal", 0), ("decal", 0)])
    o = [wire_defs(pid), win]
    rows = [(0, "doc", "Robot", {"open", "bold"}, ""), (1, "comp", "Upper Arm:1", {"open"}, ""),
            (2, "sketch", "Sketch7", {"hidden"}, "(hidden)"), (2, "decal", "Decal Wrist/L1", {"sel"}, ""),
            (1, "comp", "Forearm:1", {"open"}, ""), (2, "decal", "Decal Wrist/L2", set(), ""),
            (1, "comp", "Forearm:2", {"open", "warn"}, ""), (2, "decal", "Decal Wrist/L2", set(), "(same image as :1)")]
    o.append(browser(10, cy + 8, 262, rows))
    # a link with the L1 decal on its face
    L1 = charuco_layout(2, 8, "DICT_4X4_100", 0.748, ids=list(range(0, 8)))
    bx, by = 300, 330
    o.append('<polygon points="%g,%g %g,%g %g,%g %g,%g" fill="#d4d1ca" stroke="#8d8a83"/>' % (bx, by, bx + 270, by, bx + 290, by - 22, bx + 20, by - 22))
    o.append(frect(bx, by, 270, 74, "#e6e3dc", "#8d8a83"))
    o.append('<polygon points="%g,%g %g,%g %g,%g %g,%g" fill="#c4c1ba" stroke="#8d8a83"/>' % (bx + 270, by, bx + 290, by - 22, bx + 290, by + 52, bx + 270, by + 74))
    o.append(g_charuco(L1, bx + 23, by + 9, 28, rotate=True))
    o.append(ft(bx + 135, by + 100, "decal “Wrist/L1” on Upper Arm:1", 11, "#333", "600", "middle"))
    # palette
    px, py, pw, ph = 620, 0, 376, H
    o.append(frect(px, py, pw, ph, "#ffffff", "#9a9a9a", 6))
    o.append(frect(px, py, pw, 26, F["dlg"], None, 6) + frect(px, py + 14, pw, 12, F["dlg"]))
    o.append(ft(px + 10, py + 18, "Studio Dock", 11.5, "#fff", "700") + ft(px + pw - 16, py + 18, "✕", 11, "#ccc"))
    o.append(frect(px + 8, py + 34, pw - 16, 86, "#e8f3fb", "#9cc8ea", 6))
    o.append(ft(px + 18, py + 52, "Fusion: rect 50.8x203.2 on", 11.5, "#11466e", "600", "start", True))
    o.append(ft(px + 18, py + 68, "Upper Arm:1", 11.5, "#11466e", "600", "start", True))
    bxx = px + 18
    for b in ("Fit to selection", "Place", "Sync"):
        bw = len(b) * 6.6 + 18
        o.append(frect(bxx, py + 80, bw, 26, "#1a73c9" if b == "Place" else "#fff", "#1a73c9", 5)
                 + ft(bxx + bw / 2, py + 97, b, 11, "#fff" if b == "Place" else "#1a73c9", "600", "middle"))
        bxx += bw + 6
    o.append(ft(px + pw - 18, py + 97, "set: Wrist ▾", 11, "#333", None, "end"))
    # sets / boards columns (plain web look: different visual style on purpose)
    o.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#ddd"/>' % (px + 8, py + 130, px + pw - 8, py + 130))
    o.append(ft(px + 16, py + 150, "SETS", 10.5, "#666", "700", "start", True) + ft(px + 166, py + 150, "BOARDS", 10.5, "#666", "700", "start", True))
    for i, s in enumerate(("Wrist Proto", "Shoulder Rig", "Iceland Arm")):
        yy = py + 160 + i * 26
        if i == 0:
            o.append(frect(px + 10, yy, 140, 22, "#fff3c4", None, 3))
        o.append(ft(px + 16, yy + 15, s, 11.5, "#1a1a1a", "700" if i == 0 else None, "start", True))
    o.append(ft(px + 16, py + 255, "20 px/mm", 11, "#666", None, "start", True))
    o.append(frect(px + 14, py + 266, 100, 24, "#fff", "#888", 4) + ft(px + 64, py + 282, "Export set", 11, "#222", None, "middle", True))
    for i, (b, ids) in enumerate((("L1 2x8", "ids 0-7"), ("L2 2x13", "8-20"))):
        yy = py + 160 + i * 26
        if i == 0:
            o.append(frect(px + 160, yy, 206, 22, "#fff3c4", None, 3))
        o.append(ft(px + 166, yy + 15, b, 11.5, "#1a1a1a", "700" if i == 0 else None, "start", True) + ft(px + 360, yy + 15, ids, 11.5, "#555", None, "end", True))
    o.append(ft(px + 166, py + 227, "+ Add board", 11.5, "#1a73c9", None, "start", True))
    o.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#ddd"/>' % (px + 158, py + 240, px + pw - 8, py + 240))
    o.append(ft(px + 166, py + 258, "preview PNG", 10.5, "#666", "700", "start", True))
    o.append(g_charuco(L1, px + 186, py + 268, 20))
    o.append(frect(px + 256, py + 300, 100, 24, "#fff", "#888", 4) + ft(px + 306, py + 316, "Download", 11, "#222", None, "middle", True))
    o.append(ft(px + 256, py + 348, "frames: corner", 11, "#555", None, "start", True))
    o.append(ft(px + 256, py + 366, "(aruco_frame,", 10.5, "#777", None, "start", True))
    o.append(ft(px + 256, py + 382, " top-left)", 10.5, "#777", None, "start", True))
    o.append(frect(px + 8, py + 448, pw - 16, 140, "#fafafa", "#ddd", 6))
    o.append(ft(px + 18, py + 470, "Studio's own HTML/CSS/JS, served unchanged", 11, "#666", None, "start"))
    o.append(ft(px + 18, py + 488, "by the add-in on a worker thread; only the", 11, "#666"))
    o.append(ft(px + 18, py + 506, "blue strip above is injected.", 11, "#666"))
    o.append(ft(px + 18, py + 534, "Design tab (not shown): decals per occurrence,", 11, "#666"))
    o.append(ft(px + 18, py + 552, "collisions in red; “Own board for this instance”.", 11, "#666"))
    return wsvg("".join(o), W, H, "Proposal B wireframe: Studio Dock palette beside Fusion", "wb")


def wire_c() -> str:
    pid = "wc"
    W, H = 1000, 590
    win, (cx, cy, cw, ch), _ = fusion_window(pid, 0, 0, W, H, "Robot", ["CAD Exporter", "Datasheet"], tl=[("sk", 0), ("ext", 0), ("sk", 0)])
    o = [wire_defs(pid), win]
    rows = [(0, "doc", "Robot", {"open", "bold"}, ""), (1, "comp", "Forearm:1", {"open"}, ""), (2, "folder", "Sketches", {"open"}, ""),
            (3, "sketch", "Sketch4", set(), "[slot]"), (1, "comp", "Forearm:2", {"open"}, ""), (2, "folder", "Sketches", {"open"}, ""),
            (3, "sketch", "Sketch4", {"sel"}, "[slot]")]
    o.append(browser(10, cy + 8, 236, rows))
    for i, (lab, ids, occ) in enumerate((("slot L2_outer", "ids 8-20", "Forearm:1"), ("slot L2_outer", "ids 21-33", "Forearm:2"))):
        x0 = 290 + i * 330
        o.append(frect(x0 - 26, 200, 300, 150, "#e4e2dc", "#a8a49b", 8))
        o.append(frect(x0, 222, 248, 104, "url(#%s-hatch)" % pid, F["sk"], 2, 2))
        o.append(frect(x0 + 10, 252, 150, 44, "#ffffffe0", None, 4))
        o.append(ft(x0 + 18, 270, lab, 12.5, "#123", "700", "start", True) + ft(x0 + 18, 288, ids, 12.5, "#1f6fd1", "700", "start", True))
        o.append(ft(x0 + 124, 372, occ, 12, "#333", "600", "middle"))
    o.append(ft(560, 412, "custom-graphics overlay; nothing is added to the model", 11, "#555", None, "middle"))
    # context menu
    o.append('<path d="M712 300 l0 16 l4 -4 l3 7 l3 -1 l-3 -7 l6 0 z" fill="#fff" stroke="#111"/>')
    o.append(menu(724, 304, ["Make marker slot", "Remove marker slot", "Show slot IDs"], 180, hl=0, header="SKETCH PROFILE"))
    # export report
    o.append(frect(270, 420, 470, 92, "#ffffff", "#c9c9c9", 6))
    o.append(frect(270, 420, 470, 24, "#f1f1f1", None, 6) + ft(282, 436, "EXPORT REPORT  ·  warnings", 10.5, "#555", "700"))
    o.append(frect(282, 452, 64, 18, "#fdebc8", "#d98a00", 3) + ft(314, 465, "marker", 10, "#8a5300", "700", "middle"))
    o.append(ft(354, 465, "L2_outer#2: ids 21-33 (auto, unpinned),", 11, "#222", None, "start", True))
    o.append(ft(354, 483, "14.0 mm, 5.0 px/bit @0.65 m", 11, "#222", None, "start", True))
    o.append(ft(282, 503, "suggestions arrive as instruction-style warnings", 10.5, "#777"))
    return wsvg("".join(o), W, H, "Proposal C wireframe: ghost slots overlay, context menu and export report", "wc")


def wire_d() -> str:
    pid = "wd"
    W, H = 1000, 650
    win, (cx, cy, cw, ch), _ = fusion_window(pid, 0, 0, 494, 540, "Arm v12", ["CAD Exporter", "Datasheet", "Send", "Apply"],
                                             groups=["CREATE", "INSPECT"])
    o = [wire_defs(pid), win]
    o.append(ft(10, 562, "FUSION (Windows)", 12, "#333", "700"))
    rows = [(0, "doc", "Arm v12", {"open", "bold"}, ""), (1, "decal", "Decal L1_outer#1", set(), ""),
            (1, "decal", "Decal L2_outer#1", set(), ""), (1, "decal", "Decal L2_outer#2", {"sel"}, ""),
            (1, "decal", "Decal hand_tag#1", set(), "")]
    o.append(browser(10, cy + 8, 200, rows, "BROWSER  after Apply"))
    o.append(dialog(pid, 196, 260, 288, "SEND TO STUDIO",
                    [("ro", "Selection", "3 rectangles, 1 face"), ("ro", "Design", "Arm v12"),
                     ("ro", "Instances", "4 found (1 xref)"), ("ro", "Request", "requests/Arm_v12.json")],
                    buttons=("Send", "Cancel")))
    # studio browser window
    sx, sy, sw, sh = 516, 0, 480, 540
    o.append(frect(sx, sy, sw, sh, "#fff", "#9a9a9a", 6))
    o.append(frect(sx, sy, sw, 34, "#e9e9ef", None, 6) + frect(sx, sy + 20, sw, 14, "#e9e9ef"))
    for k, c in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        o.append('<circle cx="%d" cy="17" r="5" fill="%s"/>' % (sx + 14 + k * 16, c))
    o.append(frect(sx + 70, sy + 7, sw - 84, 20, "#fff", "#ccc", 10) + ft(sx + 84, sy + 21, "127.0.0.1:8790  ·  ArUco Setup Assistant", 10.5, "#444"))
    o.append(ft(sx + 16, sy + 62, "Robot: Arm v12", 14, "#111", "700"))
    cols = [(sx + 16, "slot"), (sx + 150, "need"), (sx + 240, "inst"), (sx + 300, "board")]
    for x, t in cols:
        o.append(ft(x, sy + 88, t, 10.5, "#777", "700", "start", True))
    o.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#ddd"/>' % (sx + 12, sy + 95, sx + sw - 12, sy + 95))
    trs = [("L1_outer", "50x203", "1", "L1 2x8", False), ("L2_outer", "38x247", "2", "L2 2x13", False),
           ("  Forearm:1", "ids 8-20", "", "", True), ("  Forearm:2", "ids 21-33", "", "", True),
           ("hand_tag", "38x38", "1", "single", False)]
    for i, (a, b, c, d, sub) in enumerate(trs):
        yy = sy + 116 + i * 26
        col = "#1f6fd1" if sub else "#111"
        o.append(ft(sx + 16, yy, a, 11.5, col, None if sub else "600", "start", True) + ft(sx + 150, yy, b, 11.5, col, None, "start", True)
                 + ft(sx + 240, yy, c, 11.5, "#111", None, "start", True) + ft(sx + 300, yy, d, 11.5, "#111", None, "start", True))
    bx = sx + 16
    for b in ("Suggest", "Allocate", "Export to Fusion"):
        bw = len(b) * 6.8 + 20
        o.append(frect(bx, sy + 254, bw, 26, "#1a73c9" if b.startswith("Export") else "#fff", "#1a73c9", 5)
                 + ft(bx + bw / 2, sy + 271, b, 11, "#fff" if b.startswith("Export") else "#1a73c9", "600", "middle"))
        bx += bw + 8
    o.append(ft(sx + sw - 16, sy + 271, "capacity 35/100", 11, "#555", None, "end", True))
    o.append(frect(sx + 16, sy + 300, 210, 18, "#eee", None, 9) + frect(sx + 16, sy + 300, 210 * 0.35, 18, "#7aa6c9", None, 9))
    o.append(ft(sx + 16, sy + 350, "New Robot view: suggests boards per slot", 11, "#666"))
    o.append(ft(sx + 16, sy + 368, "(suggest_board_grids), allocates IDs across all", 11, "#666"))
    o.append(ft(sx + 16, sy + 386, "instances, exports bam_aruco_library/2 plus PNGs.", 11, "#666"))
    o.append(ft(sx + 10, 562, "STUDIO (Linux, :8790)", 12, "#333", "700"))
    # S1 repo + git arrows
    o.append(frect(150, 596, 700, 44, "#f3f0ff", "#7a63d1", 8))
    o.append(ft(500, 623, "S1 repo:  requests/   library/   png/", 13, "#3d2b8f", "700", "middle", True))
    o.append(frect(712, 604, 128, 26, "#fde5e3", "#d64545", 13) + ft(776, 621, "declined 2026-10-07", 10, "#a12d2d", "700", "middle"))
    arrows = [(200, 544, 200, 594, "git push (request)"), (300, 594, 300, 544, "git pull (library)"),
              (840, 544, 840, 594, "git push (library, PNGs)"), (720, 594, 720, 544, "git pull (request)")]
    for x1, y1, x2, y2, lab in arrows:
        o.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#555" stroke-width="1.6" marker-end="url(#WA-wd)"/>' % (x1, y1, x2, y2))
        o.append(ft(x1 + 6, 575, lab, 10, "#444"))
    return wsvg("".join(o), W, H, "Proposal D wireframe: Send to Studio, the Studio robot view, and the S1 round trip", "wd")


def wire_e() -> str:
    pid = "we"
    W, H = 1000, 640
    Z = dict(bg="#ffffff", line="#e4e4e7", mut="#71717a", ink="#09090b", soft="#f4f4f5", pri="#18181b",
             warn="#d97706", warnbg="#fef3c7", bad="#dc2626", badbg="#fee2e2")
    o = [wire_defs(pid), '<defs><filter id="%s-sh"><feDropShadow dx="0" dy="2" stdDeviation="3" flood-opacity="0.2"/></filter></defs>' % pid]
    o.append(frect(0, 0, W, 498, Z["bg"], "#9a9a9a", 6))
    o.append(frect(0, 0, W, 24, F["dlg"], None, 6) + frect(0, 12, W, 12, F["dlg"]))
    o.append(ft(10, 16.5, "Cockpit", 11, "#fff", "700") + ft(W - 14, 16.5, "✕", 11, "#ccc"))
    bx = 12
    for b in ("Export URDF", "Export & View", "Probe", "Folder", "Sync"):
        bw = len(b) * 6.5 + 20
        o.append(frect(bx, 32, bw, 26, Z["pri"] if b == "Export URDF" else Z["bg"], Z["line"] if b != "Export URDF" else Z["pri"], 6)
                 + ft(bx + bw / 2, 49, b, 11, "#fff" if b == "Export URDF" else Z["ink"], "600", "middle"))
        bx += bw + 6
    o.append(ft(W - 14, 49, "(Fusion only)", 10.5, Z["mut"], None, "end"))
    o.append('<line x1="0" y1="66" x2="%d" y2="66" stroke="%s"/>' % (W, Z["line"]))
    o.append(ft(14, 86, "Arm v12", 12.5, Z["ink"], "700") + ft(76, 86, "33 occ   17/18 joints   12 links", 11, Z["mut"]))
    o.append(frect(560, 72, 230, 24, Z["soft"], Z["line"], 6) + ft(572, 88, "Search", 11, Z["mut"]) + frect(740, 77, 42, 15, "#fff", Z["line"], 3) + ft(761, 88, "Ctrl P", 9, Z["mut"], None, "middle"))
    o.append(frect(806, 72, 86, 24, Z["soft"], Z["line"], 6) + frect(808, 74, 42, 20, "#fff", None, 4) + ft(829, 88, "Tree", 10.5, Z["ink"], "600", "middle") + ft(871, 88, "Insp", 10.5, Z["mut"], None, "middle"))
    o.append('<line x1="0" y1="104" x2="%d" y2="104" stroke="%s"/>' % (W, Z["line"]))
    # left tree
    o.append(ft(14, 124, "ASSEMBLY", 10, Z["mut"], "700"))
    trows = [("Upper Arm:1", False), ("Forearm:1", False), ("Forearm:2", True), ("Hand:1 (xref)", False)]
    for i, (lab, bad) in enumerate(trows):
        yy = 136 + i * 28
        if bad:
            o.append(frect(6, yy, 196, 24, Z["warnbg"], None, 5))
        o.append(ft(14, yy + 16, "▾ " + lab, 11.5, Z["ink"], "600" if bad else None))
        if bad:
            o.append('<circle cx="160" cy="%d" r="7" fill="%s"/>' % (yy + 12, Z["warn"]) + ft(160, yy + 16, "!", 10, "#fff", "800", "middle"))
        o.append(frect(172, yy + 4, 24, 16, Z["soft"], Z["line"], 8) + ft(184, yy + 16, "#1", 9.5, Z["mut"], None, "middle"))
    o.append('<line x1="210" y1="104" x2="210" y2="410" stroke="%s"/>' % Z["line"])
    # center markers lens
    o.append(frect(222, 114, 420, 30, Z["soft"], None, 7))
    tx = 226
    for t in ("SubAssembly", "Joint", "Link", "Markers"):
        tw = len(t) * 7 + 26
        if t == "Markers":
            o.append(frect(tx, 117, tw, 24, "#fff", Z["line"], 5, 1, ' filter="url(#%s-sh)"' % pid))
        o.append(ft(tx + tw / 2, 134, t, 11.5, Z["ink"] if t == "Markers" else Z["mut"], "700" if t == "Markers" else None, "middle"))
        tx += tw + 4
    for x, t in ((230, "instance"), (390, "slot"), (520, "ids")):
        o.append(ft(x, 166, t, 10.5, Z["mut"], "700"))
    lrows = [("Upper Arm:1", "L1_outer", "0-7", False), ("Forearm:1", "L2_outer", "8-20", False),
             ("Forearm:2", "L2_outer", "8-20", True), ("Hand:1 (xref)", "tag", "30", False)]
    for i, (a, b, c, bad) in enumerate(lrows):
        yy = 174 + i * 30
        if bad:
            o.append(frect(222, yy, 420, 26, Z["warnbg"], None, 5))
        o.append('<line x1="222" y1="%d" x2="642" y2="%d" stroke="%s"/>' % (yy, yy, Z["line"]))
        o.append(ft(230, yy + 17, a, 11.5, Z["ink"]) + ft(390, yy + 17, b, 11.5, Z["ink"], None, "start", True) + ft(520, yy + 17, c, 11.5, Z["ink"], "700", "start", True))
        if bad:
            o.append('<circle cx="372" cy="%d" r="7" fill="%s"/>' % (yy + 13, Z["warn"]) + ft(372, yy + 17, "!", 10, "#fff", "800", "middle"))
    o.append('<line x1="222" y1="300" x2="642" y2="300" stroke="%s"/>' % Z["line"])
    o.append(ft(230, 326, "capacity 35/100", 11.5, Z["mut"]) + frect(330, 316, 140, 10, Z["soft"], None, 5) + frect(330, 316, 49, 10, Z["pri"], None, 5))
    o.append(frect(520, 310, 116, 28, Z["bg"], Z["line"], 6) + ft(578, 328, "Renumber all", 11.5, Z["ink"], "600", "middle"))
    o.append('<line x1="654" y1="104" x2="654" y2="410" stroke="%s"/>' % Z["line"])
    # inspector
    o.append(ft(668, 124, "INSPECTOR", 10, Z["mut"], "700"))
    o.append(ft(668, 148, "L2_outer", 14, Z["ink"], "700") + frect(740, 136, 64, 18, Z["soft"], Z["line"], 9) + ft(772, 149, "charuco", 10, Z["mut"], None, "middle"))
    for i, (t, bold) in enumerate((("2 x 13, 19/14 mm", False), ("DICT_4X4_100", True), ("camera D435 1080p", False),
                                   ("Z 0.65 m: 5.0 px/bit", False))):
        o.append(ft(668, 174 + i * 20, t, 11.5, Z["ink"], "600" if bold else None, "start", i == 1))
    o.append(ft(668, 254, "at 45 deg: 3.5", 11.5, Z["ink"]) + frect(762, 242, 40, 17, Z["warnbg"], Z["warn"], 8) + ft(782, 254, "LOW", 9.5, "#92400e", "800", "middle"))
    L2 = charuco_layout(2, 13, "DICT_4X4_100", 0.737, ids=list(range(8, 21)))
    o.append(g_charuco(L2, 668, 270, 10.5, rotate=True))
    o.append(ft(668, 306, "thumbnail: Forearm:1, ids 8-20", 10, Z["mut"]))
    bx = 668
    for b in ("Make Marker", "Refit"):
        bw = len(b) * 7 + 22
        o.append(frect(bx, 322, bw, 28, Z["pri"] if b == "Make Marker" else Z["bg"], Z["pri"] if b == "Make Marker" else Z["line"], 6)
                 + ft(bx + bw / 2, 340, b, 11.5, "#fff" if b == "Make Marker" else Z["ink"], "600", "middle"))
        bx += bw + 8
    # problems drawer
    o.append('<line x1="0" y1="410" x2="%d" y2="410" stroke="%s"/>' % (W, Z["line"]))
    o.append(frect(10, 418, 110, 22, Z["soft"], Z["line"], 5) + ft(65, 433, "PROBLEMS (1)", 10.5, Z["ink"], "700", "middle"))
    o.append(frect(132, 420, 148, 18, Z["warnbg"], Z["warn"], 4) + ft(206, 433, "W_MARKER_DUPLICATE", 10, "#92400e", "800", "middle", True))
    o.append(ft(290, 433, "Forearm:2 repeats ids 8-20 of Forearm:1", 11.5, Z["ink"]))
    o.append(ft(132, 466, "→ right-click:  Renumber instance  |  Same object class  |  Make Independent (Fusion only)", 11, Z["mut"]))
    # fusion canvas strip
    o.append('<defs><linearGradient id="%s-cv" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="%s"/><stop offset="1" stop-color="%s"/></linearGradient></defs>' % (pid, F["c1"], F["c2"]))
    o.append(frect(0, 506, W, 134, "url(#%s-cv)" % pid, "#9a9a9a", 6))
    o.append(ft(12, 524, "FUSION CANVAS", 10.5, "#555", "700"))
    for i, (lab, over) in enumerate((("Forearm:1", None), ("Forearm:2", "ids 21-33"))):
        x0 = 200 + i * 380
        o.append(frect(x0, 540, 300, 64, "#e2dfd8", "#9a968e", 6))
        o.append(g_charuco(L2, x0 + 14, 556, 10.0, rotate=True))
        o.append(ft(x0 + 150, 626, lab, 11, "#333", "600", "middle"))
        if over:
            o.append(frect(x0 + 168, 548, 120, 30, "#1f6fd1", None, 6) + ft(x0 + 228, 568, over, 13, "#fff", "800", "middle"))
            o.append(ft(x0 + 228, 594, "overlay label", 10, "#1f4f8f", None, "middle"))
    return wsvg("".join(o), W, H, "Proposal E wireframe: Cockpit Markers lens, inspector, problems drawer and canvas", "we")


# =============================================================== proposals

PROPS = {"A": ("Marker Feature", "s1"), "B": ("Studio Dock", "s2"), "C": ("Ghost Slots", "s3"),
         "D": ("Studio Remote", "s4"), "E": ("Cockpit Markers", "s5")}
EFFORT = {
    "A": ("L, about 4 weeks", [("Custom Feature, edit command and compute", "Custom Feature", 5), ("Cell geometry", "Cells", 3),
                              ("Emboss, inlay and socket", "Emboss", 3), ("Registry and scanner", "Registry", 3),
                              ("Export", "Export", 2), ("Studio exchange", "Studio", 1), ("Tests and smoke", "Tests", 3)]),
    "B": ("M-L, about 3 weeks", [("Server thread and S3 patch", "Server + S3", 3), ("Strip and bridge", "Strip", 2),
                                ("Place", "Place", 2), ("Schema v3 and cross-set allocation in the Studio (Linux)", "Schema v3", 3),
                                ("Export", "Export", 2), ("Sync", "Sync", 1), ("Smoke", "Smoke", 2)]),
    "C": ("S-M, about 2 weeks", [("Slot command and reader", "Slot cmd", 2), ("Library v2, allocator and pins", "Library v2", 2),
                                ("S3 renderer with golden tests", "S3 renderer", 3), ("Export", "Export", 2),
                                ("Overlay (optional)", "Overlay", 2), ("Print sheet", "Sheet", 1)]),
    "D": ("M, about 2.5 weeks, over half on Linux", [("Fusion: request writer", "Request", 1), ("Fusion: batch apply", "Apply", 2),
                                                    ("Fusion: export wiring", "Export", 2),
                                                    ("Studio: robot view, instance allocation and library export", "Studio robot view", 5),
                                                    ("Sync", "Sync", 1)]),
    "E": ("M, about 3 weeks (2 without the lens)", [("Pure core", "Pure core", 4), ("Export", "Export", 2), ("Make Marker", "Make Marker", 2),
                                                   ("Lens (table, inspector, codes)", "Lens", 4), ("Studio exchange", "Studio", 1),
                                                   ("Smoke", "Smoke", 2)]),
}


def check_effort():
    t = SRC_TEXT["prop"]
    need(t, "Custom Feature, edit command and compute, 5 days", "Cell geometry, 3 d", "Tests and smoke, 3 d",
         "Server thread and S3 patch, 3 d", "Schema v3 and cross-set allocation in the Studio, 3 d (Linux)",
         "Slot command and reader, 2 d", "S3 renderer with golden tests, 3 d", "Print sheet, 1 d",
         "request writer 1 d, batch apply 2 d, export wiring 2 d", "instance allocation and library export, 5 d",
         "Pure core, 4 d", "Lens (table, inspector, codes), 4 d")


def effort_bar(keys, W=960, pid="eff") -> str:
    rowh, left = 46, 290
    H = 18 + rowh * len(keys)
    pxd = (W - left - 70) / 20.0
    o = []
    for d in range(0, 21, 5):
        x = left + d * pxd
        o.append('<line x1="%g" y1="6" x2="%g" y2="%d" class="grid"/>' % (x, x, H - 18))
        o.append(svg_text(x, H - 4, "%d d" % d, size=11, anchor="middle", cls="t-mut"))
    for i, k in enumerate(keys):
        y = 10 + i * rowh
        name, col = PROPS[k]
        size, parts = EFFORT[k]
        o.append(svg_text(left - 12, y + 16, "%s  %s" % (k, name), anchor="end", size=13.5, cls="t-b"))
        o.append(svg_text(left - 12, y + 32, size, anchor="end", size=11, cls="t-mut"))
        x = left
        for j, (full, short, d) in enumerate(parts):
            w = d * pxd - 2
            o.append('<rect x="%g" y="%d" width="%g" height="24" rx="4" class="f-%s" opacity="%s" data-tip="%s %s: %s, %d day%s"/>'
                     % (x, y + 4, w, col, "1" if j % 2 == 0 else "0.62", k, esc(name), esc(full), d, "" if d == 1 else "s"))
            if w > len(short) * 6.6 + 8:
                o.append(svg_text(x + w / 2, y + 20.5, short, size=11, anchor="middle", fill="#fff", weight="600"))
            x += d * pxd
        tot = sum(p[2] for p in parts)
        o.append(svg_text(x + 8, y + 21, "%d d" % tot, size=12.5, cls="t-b"))
    return svg("".join(o), W, H, "Effort breakdown in days")


def prop_requirements(head, rows, idx) -> str:
    cards = "".join('<div class="card"><h4 style="font-size:22px;color:var(--decide)">%s</h4><p>%s</p></div>'
                    % (esc(r[0]), inline(r[1])) for r in rows)
    return ('<div class="cards" style="grid-template-columns:repeat(auto-fill,minmax(min(100%%,200px),1fr))">%s</div>' % cards
            + '<details class="src"><summary>Table as written</summary>%s</details>' % md_table(head, rows))


def prop_studio_model(pid="sm") -> str:
    t = SRC_TEXT["prop"]
    need(t, "a `pixels_per_mm` print density (default 20,\n  which is 508 DPI)", "`marker_count =\n  floor(sx * sy / 2)`",
         "every set starts\n  at ID 0, so two sets that share a dictionary collide", "unknown keys are\n  dropped on save")
    W, H = 960, 330
    o = [arrow_defs(pid)]
    o.append('<rect x="14" y="14" width="262" height="212" rx="12" class="b-acc"/>')
    o.append(svg_text(28, 38, "Set", cls="t-b", size=15))
    for i, s in enumerate(("UUID, name", "one dictionary", "pixels_per_mm = 20 (508 DPI)", "boards [ ... ]", "schema version 2",
                           "one JSON file per set")):
        o.append(svg_text(28, 62 + i * 24, s, size=12.5, cls="t-mono"))
    o.append('<line x1="276" y1="120" x2="306" y2="120" class="edge" marker-end="url(#%s-ah)"/>' % pid)
    o.append('<rect x="310" y="14" width="300" height="212" rx="12" class="box"/>')
    o.append(svg_text(324, 38, "Board (always ChArUco)", cls="t-b", size=15))
    for i, s in enumerate(("squares_x / squares_y", "checker_length_mm", "marker_length_mm", "margin_mm, thickness_mm",
                           "object_transform")):
        o.append(svg_text(324, 62 + i * 22, s, size=12.5, cls="t-mono"))
    o.append(svg_text(324, 180, "derived: outer size, ids,", size=12.5, cls="t-mut"))
    o.append(svg_text(324, 198, "marker_count = floor(sx * sy / 2)", size=12.5, cls="t-mono t-vio"))
    # ID ranges
    o.append('<rect x="626" y="14" width="320" height="212" rx="12" class="box2"/>')
    o.append(svg_text(640, 38, "Managed IDs (normalize_marker_set)", cls="t-b", size=13.5))
    sc = 290 / 34.0

    def bar(y, a, b, cls, lab):
        return ('<rect x="%g" y="%d" width="%g" height="20" rx="4" class="%s"/>' % (640 + a * sc, y, (b - a + 1) * sc - 2, cls)
                + svg_text(640 + (a + b + 1) / 2 * sc, y + 14.5, lab, size=11, anchor="middle", cls="t-b"))
    o.append(svg_text(640, 62, "set “Wrist Proto”, one contiguous range per board", size=11.5, cls="t-mut"))
    o.append(bar(70, 0, 7, "b-good", "L1 0-7") + bar(70, 8, 20, "b-good", "L2 8-20") + bar(70, 21, 33, "box dash", "next: 21-"))
    o.append(svg_text(640, 112, "kept across rename, reorder, resize (if it fits);", size=11.5, cls="t-ink2"))
    o.append(svg_text(640, 128, "new boards take the first free range", size=11.5, cls="t-ink2"))
    o.append(svg_text(640, 154, "a second set with the same dictionary:", size=11.5, cls="t-bad t-b"))
    o.append(bar(162, 0, 12, "b-bad", "starts at 0 too"))
    o.append(svg_text(640, 204, "→ collides with Wrist Proto's IDs", size=11.5, cls="t-bad"))
    # catches + frames
    o.append('<rect x="14" y="240" width="460" height="80" rx="12" class="b-bad"/>')
    o.append(svg_text(28, 262, "Two catches", cls="t-b t-bad", size=13.5))
    o.append(svg_text(28, 282, "1. Every set starts at ID 0 (right).", size=12.5))
    o.append(svg_text(28, 300, "2. Unknown keys are dropped on save: Fusion's additions vanish.", size=12.5))
    o.append('<rect x="490" y="240" width="456" height="80" rx="12" class="box"/>')
    o.append(svg_text(504, 262, "Frames", cls="t-b", size=13.5))
    o.append(svg_text(504, 282, "aruco_frame: printed top-left, +X right, +Y down, +Z in.", size=12.5))
    o.append(svg_text(504, 300, "Five mount_* frames on the back face; the assembler joins by them.", size=12.5))
    return fig(svg("".join(o), W, H, "ChArUco Studio data model, schema v2"),
               "From section 2.1. The second set's range is illustrative of the first catch.", "from the doc", "wide")


def prop_frames(pid="fr") -> str:
    need(SRC_TEXT["prop"], "`aruco_frame` is on the printed top-left corner, with +X\n  right, +Y down and +Z into the board",
         "The decal is centre-based, and its first\n   column points image-left on the visible side")
    lay = charuco_layout(5, 3, "DICT_4X4_50", 0.75)
    W, H, sq = 960, 300, 52
    o = [arrow_defs(pid)]
    bx1, by = 60, 62
    o.append(svg_text(20, 28, "aruco_frame (Studio, detector)", cls="t-b", size=14))
    o.append(g_charuco(lay, bx1, by, sq))
    o.append('<g transform="translate(%d %d)">%s</g>' % (0, 0, triad(bx1, by, 70).replace("TRI-r", "%s-TRI-r" % pid).replace("TRI-g", "%s-TRI-g" % pid)))
    o.append(svg_text(20, 270, "origin on the printed top-left corner", size=12.5, cls="t-ink2"))
    bx2 = 560
    o.append(svg_text(520, 28, "Decal transform (Fusion, decals.py)", cls="t-b", size=14))
    o.append(g_charuco(lay, bx2, by, sq))
    cxm, cym = bx2 + 5 * sq / 2, by + 3 * sq / 2
    o.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#d13b3b" stroke-width="3" marker-end="url(#%s-ahb)"/>'
             % (cxm, cym, cxm - 5 * sq, cym, pid))
    o.append('<circle cx="%g" cy="%g" r="7" fill="#fff" stroke="#d13b3b" stroke-width="3"/>' % (cxm, cym))
    o.append(svg_text(cxm - 5 * sq + 4, by + 3 * sq + 26, "first column: image-left, at full image width", size=12.5, cls="t-bad t-b"))
    o.append(svg_text(520, 270, "translation = the centre; first column points image-left", size=12.5, cls="t-ink2"))
    tri_defs = ('<defs><marker id="%s-TRI-r" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">'
                '<path d="M0,0 L10,5 L0,10 z" fill="#d13b3b"/></marker><marker id="%s-TRI-g" viewBox="0 0 10 10" refX="8" refY="5" '
                'markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#1a9a4a"/></marker></defs>' % (pid, pid))
    return fig(svg(tri_defs + "".join(o), W, H, "Gap 4: two frame conventions for one marker"),
               "Gap 4 in section 2.2: one adapter must own the conversion. The board is a real OpenCV ChArUco "
               "5 x 3; the doc gives the first decal column only, so the second axis is not drawn.",
               "from the doc", "wide")


def prop_pipeline(pid="pl") -> str:
    t = SRC_TEXT["prop"]
    need(t, "**Both are tested\n  but never called by the export.**", "`_collect_decals` reads each decal once\n  per component",
         "(`name#1`, `name#2`)", "writes an OBJ+MTL quad per\n  instance", "massless `<name>_target` link on a fixed mount")
    W, H = 960, 352
    o = [arrow_defs(pid)]

    def box(x, y, w, a, b, cls="box"):
        return ('<rect x="%d" y="%d" width="%d" height="58" rx="10" class="%s"/>' % (x, y, w, cls)
                + svg_text(x + 10, y + 23, a, size=12.5, cls="t-mono t-b") + svg_text(x + 10, y + 43, b, size=11.5, cls="t-mut"))

    def arr(x1, y1, x2, y2, cls="edge", mk="ah"):
        return '<line x1="%d" y1="%d" x2="%d" y2="%d" class="%s" marker-end="url(#%s-%s)"/>' % (x1, y1, x2, y2, cls, pid, mk)

    o.append(svg_text(14, 22, "Decals → textured quads", cls="t-b t-acc", size=13))
    o.append(box(14, 32, 160, "Fusion decals", "on faces, per component", "b-acc"))
    o.append(box(196, 32, 200, "_collect_decals", "once per component"))
    o.append(box(418, 32, 236, "decals_from_payload", "every occurrence: name#1, name#2"))
    o.append(box(676, 32, 270, "resolve_decals", "one OBJ+MTL quad per instance", "b-good"))
    for x1, x2 in ((174, 194), (396, 416), (654, 674)):
        o.append(arr(x1, 61, x2, 61))
    o.append('<circle cx="640" cy="34" r="11" class="b-bad"/>' + svg_text(640, 39, "3", size=12, anchor="middle", cls="t-bad t-b"))
    o.append(svg_text(14, 122, "Fiducials → frames", cls="t-b t-acc", size=13))
    o.append(box(14, 132, 200, "model.fiducials", "hand-typed in cad_config.json", "b-warn"))
    o.append(box(236, 132, 200, "Fiducial", "+ validate_fiducials gates"))
    o.append(box(458, 132, 240, "_add_fiducial_frames", "massless <name>_target, fixed mount"))
    o.append(box(720, 132, 226, "_emit_fiducials", "a thin box; no collision", "b-good"))
    for x1, x2 in ((214, 234), (436, 456), (698, 718)):
        o.append(arr(x1, 161, x2, 161))
    o.append('<circle cx="424" cy="134" r="11" class="b-bad"/>' + svg_text(424, 139, "2", size=12, anchor="middle", cls="t-bad t-b"))
    o.append(svg_text(14, 228, "Written and tested, not wired in", cls="t-b t-bad", size=13))
    o.append(box(14, 238, 230, "load_marker_library", "reads bam_aruco_library/1", "b-bad"))
    o.append(box(266, 238, 300, "fiducials_from_decals", "joins by entity token, then by name", "b-bad"))
    o.append(arr(244, 267, 264, 267, "edge e-bad", "ahb"))
    o.append('<path d="M416 238 C416 210 336 214 336 192" class="edge e-bad dash" marker-end="url(#%s-ahb)"/>' % pid)
    o.append('<circle cx="430" cy="214" r="11" class="b-bad"/>' + svg_text(430, 219, "1", size=12, anchor="middle", cls="t-bad t-b"))
    o.append(svg_text(588, 258, "“Fusion owns WHERE, the library owns WHAT”", size=12.5, cls="t-ink2"))
    o.append(svg_text(588, 278, "Today only hand-typed fiducials reach the URDF.", size=12.5, cls="t-ink2"))
    o.append('<rect x="14" y="310" width="932" height="34" rx="9" class="box2"/>')
    o.append(svg_text(28, 332, "Red numbers: gap 1 (join not wired), gap 2 (Fiducial mis-describes ChArUco), gap 3 (instances "
                      "share IDs: every occurrence gets the same image).", size=12.5))
    return fig(svg("".join(o), W, H, "The CAD Exporter's existing fiducial and decal path"),
               "Section 2.2 as a flow. Function names are the doc's; gaps 4 to 6 (frames, schemas, posture) are not "
               "steps in the flow and are listed below it.", "from the doc", "wide")


def prop_facts_table(head, rows, idx) -> str:
    def src(c):
        parts = [p.strip() for p in re.split(r",\s*", c)]
        out = []
        for p in parts:
            cls = {"repo": "s3", "ref": "s1", "research": "s5", "follows": "s4"}.get(p.split(" ")[0], "")
            out.append('<span class="chip %s">%s</span>' % (cls, inline(p)))
        return "".join(out)
    trs = "".join("<tr><td>%s</td><td>%s</td></tr>" % (inline(r[0]), src(r[1])) for r in rows)
    return ('<div class="tablewrap"><table class="md"><thead><tr><th>%s</th><th>%s</th></tr></thead><tbody>%s</tbody>'
            '</table></div>' % (inline(head[0]), inline(head[1]), trs)
            + '<p class="narrow" style="font-size:14px;color:var(--muted)">Source chips: <span class="chip s1">ref</span> '
              'Autodesk API reference, <span class="chip s3">repo</span> this repo\'s probe of Zach\'s Fusion, '
              '<span class="chip s5">research</span> the architecture research, <span class="chip s4">follows</span> '
              'deduced from the rows above it.</p>')


def prop_anatomy() -> str:
    g = marker_grid("DICT_4X4_50", 1)
    W, H = 960, 330
    k = 30
    x0, y0 = 50, 40                      # top-left of the black square; quiet zone is one module outside
    o = [g_bits(g, x0 - k, y0 - k, 8 * k, 1)]
    o.append('<rect x="%d" y="%d" width="%d" height="%d" fill="none" class="edge e-mut dash"/>' % (x0 - k, y0 - k, 8 * k, 8 * k))
    o.append('<rect x="%d" y="%d" width="%d" height="%d" fill="none" stroke="#1f6fd1" stroke-width="3"/>' % (x0 + k, y0 + k, 4 * k, 4 * k))
    o.append('<line x1="%d" y1="%d" x2="%d" y2="%d" class="edge"/>' % (x0, y0 + 6 * k + 18, x0 + 6 * k, y0 + 6 * k + 18))
    o.append(svg_text(x0 - k, y0 + 6 * k + 40, "M = n + 2b = 6 modules: the black square, side s", size=12.5, cls="t-b"))
    lx = x0 + 7 * k + 14                  # label column right of the marker
    o.append(svg_text(lx, y0 - k + 14, "quiet zone", size=12.5, cls="t-b"))
    o.append(svg_text(lx, y0 - k + 30, "white margin; the", size=12, cls="t-mut"))
    o.append(svg_text(lx, y0 - k + 46, "detector needs it", size=12, cls="t-mut"))
    o.append('<path d="M%d %d H%d" class="edge"/>' % (x0 + 6 * k - k // 2, y0 + k // 2 + 60, lx - 4))
    o.append(svg_text(lx, y0 + k // 2 + 56, "border, b = 1", size=12.5, cls="t-b"))
    o.append(svg_text(lx, y0 + k // 2 + 72, "black ring", size=12, cls="t-mut"))
    o.append('<path d="M%d %d H%d" stroke="#1f6fd1" stroke-width="1.6" fill="none"/>' % (x0 + 5 * k, y0 + 4 * k, lx - 4))
    o.append(svg_text(lx, y0 + 4 * k - 4, "n x n data bits", size=12.5, fill="#1f6fd1", weight="700"))
    o.append(svg_text(lx, y0 + 4 * k + 12, "n = 4 here", size=12, cls="t-mut"))
    # kinds
    kx = 470
    o.append(svg_text(kx, 28, "single", size=13, cls="t-b"))
    o.append(g_bits(marker_grid("DICT_4X4_50", 5), kx, 40, 110, 0.8))
    gb = gridboard_layout(3, 2, "DICT_4X4_50", 1.0, 0.25, first=10)
    o.append(svg_text(kx + 140, 28, "grid board: gaps between markers", size=13, cls="t-b"))
    gbs = gridboard_svg(gb, 40, 6)
    m = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', gbs)
    inner = gbs[gbs.index("</title>") + 8:-6]
    o.append('<g transform="translate(%d 40) scale(%g)">%s</g>' % (kx + 140, 160 / float(m.group(1)), inner))
    ch = charuco_layout(5, 3, "DICT_4X4_50", 0.75)
    o.append(svg_text(kx, 190, "ChArUco board: markers in the white squares of a chessboard", size=13, cls="t-b"))
    o.append(g_charuco(ch, kx, 202, 34))
    o.append(svg_text(kx + 180, 226, "chessboard corners refine", size=12, cls="t-mut"))
    o.append(svg_text(kx + 180, 242, "to sub-pixel accuracy", size=12, cls="t-mut"))
    return fig(svg("".join(o), W, H, "Marker anatomy and the three kinds"),
               "Section 3's vocabulary on real OpenCV renders (DICT_4X4_50). The black square spans M modules; that is "
               "the number pose estimation multiplies by.", "generated by OpenCV", "wide")


def prop_record(code, info, idx) -> str:
    need(SRC_TEXT["prop"], "IDs belong to instances, not slots.", "(pose and host always come from")
    slot = [("key", "\"wrist_L2_outer\""), ("kind", "single | grid | charuco"), ("dictionary", "\"DICT_4X4_100\""),
            ("squares/rows/cols", "marker_length_m"), ("checker_length_m", "(charuco)"), ("separation_m", "(grid), margin_m"),
            ("border_bits", "thickness_m"), ("local_count", "13"), ("studio", "{set_id, board_id} | null")]
    inst = [("slot_key", "\"wrist_L2_outer\""), ("occurrence", "\"Arm:1+Wrist:2\""), ("ids", "[40..52]  ← resolved"),
            ("id_source", "auto | pinned | class"), ("binding", "{decal entity_token, name}"), ("printed", "{date, px_per_mm} | null")]

    def card(title, sub, rows, cls):
        lis = "".join('<div style="display:flex;gap:10px;justify-content:space-between;border-top:1px solid var(--line);'
                      'padding:4px 0"><code>%s</code><span style="font:13px var(--mono);color:var(--ink-2);text-align:right">%s</span></div>'
                      % (esc(a), esc(b)) for a, b in rows)
        return ('<div class="card" style="border-top:4px solid var(--%s)"><h4>%s</h4><p style="margin-top:0">%s</p>%s</div>'
                % (cls, title, sub, lis))
    grid = ('<div class="twocol">%s%s</div>' % (
        card("slot", "per component definition: “I need 13 IDs”", slot, "s1"),
        card("instance", "per occurrence path: “these 13”", inst, "s3")))
    adapters = ('<p style="margin:12px 0 0;font-size:15px">Adapters write: <span class="chip">Studio v2 (ChArUco slots, mm)</span>'
                '<span class="chip">bam_aruco_library/1 (one entry per instance)</span><span class="chip">aruco_objects.yaml '
                '(one object per instance)</span>. Pose and host always come from Fusion, never from the record.</p>')
    return fig(grid + adapters, "<code>bam_marker/1</code>, one record for all five proposals. One slot, many instances; "
               "IDs live on the instance.", "from the doc") + ascii_fold(code, "Record as written in the doc")


def prop_sync(pid="sy") -> str:
    t = SRC_TEXT["prop"]
    need(t, "**S1, git, which works today.**", "**S4, a live link** (Tailscale or an SSH tunnel). Not available on this\n  box",
         "From this Windows box only GitHub\nis reachable")
    W, H = 960, 330
    o = [arrow_defs(pid)]
    o.append('<rect x="14" y="20" width="300" height="250" rx="14" class="box2"/>')
    o.append(svg_text(30, 46, "Windows box: Fusion", cls="t-b", size=14.5))
    o.append('<rect x="30" y="62" width="268" height="58" rx="10" class="b-good"/>')
    o.append(svg_text(42, 84, "S3: render without OpenCV", cls="t-b", size=12.5))
    o.append(svg_text(42, 103, "bit tables as JSON; zlib + struct PNG", size=11.5, cls="t-mut"))
    o.append('<rect x="30" y="132" width="268" height="58" rx="10" class="b-warn"/>')
    o.append(svg_text(42, 154, "S2: the Studio on Windows (verify)", cls="t-b", size=12.5))
    o.append(svg_text(42, 173, "stdlib server + OpenCV via pixi", size=11.5, cls="t-mut"))
    o.append(svg_text(30, 216, "~/CADExporter/ data repo", size=12.5, cls="t-mono"))
    o.append(svg_text(30, 234, "(cad_config.json lives here)", size=11.5, cls="t-mut"))
    o.append('<rect x="646" y="20" width="300" height="250" rx="14" class="box2"/>')
    o.append(svg_text(662, 46, "Linux box: ChArUco Studio", cls="t-b", size=14.5))
    o.append(svg_text(662, 72, "http://127.0.0.1:8790/", size=12, cls="t-mono"))
    o.append(svg_text(662, 96, "store: one JSON per set", size=12.5))
    o.append(svg_text(662, 114, "~/.local/share/bam_aruco/", size=11.5, cls="t-mono t-mut"))
    o.append(svg_text(662, 130, "  aruco_sets/sets/", size=11.5, cls="t-mono t-mut"))
    o.append(svg_text(662, 156, "shared with Keras Workbench", size=12.5))
    o.append(svg_text(662, 174, "and the 3D assembler", size=12.5))
    o.append('<rect x="380" y="70" width="200" height="150" rx="14" class="b-acc"/>')
    o.append(svg_text(480, 96, "GitHub", cls="t-b", size=15, anchor="middle"))
    o.append(svg_text(480, 116, "the only thing both", size=12, anchor="middle", cls="t-mut"))
    o.append(svg_text(480, 132, "machines reach", size=12, anchor="middle", cls="t-mut"))
    o.append('<line x1="314" y1="225" x2="378" y2="170" class="edge e-good" marker-end="url(#%s-ahg)"/>' % pid)
    o.append('<line x1="582" y1="170" x2="644" y2="210" class="edge e-good" marker-end="url(#%s-ahg)"/>' % pid)
    o.append(svg_text(480, 160, "data repo via Sync:", size=11.5, anchor="middle", cls="t-good t-b"))
    o.append(svg_text(480, 176, "works today", size=11.5, anchor="middle", cls="t-good"))
    o.append('<line x1="644" y1="120" x2="582" y2="120" class="edge e-bad dash" marker-end="url(#%s-ahb)"/>' % pid)
    o.append('<line x1="378" y1="120" x2="316" y2="120" class="edge e-bad dash" marker-end="url(#%s-ahb)"/>' % pid)
    o.append(svg_text(480, 200, "S1: Studio store repo", size=11.5, anchor="middle", cls="t-bad t-b"))
    o.append(svg_text(480, 214, "declined 2026-10-07", size=11, anchor="middle", cls="t-bad"))
    o.append('<path d="M164 270 C164 312 796 312 796 270" class="edge e-mut dash"/>')
    o.append(svg_text(480, 304, "S4: live link (Tailscale or SSH tunnel): not available on this box; nothing depends on it",
                      size=12, anchor="middle", cls="t-mut"))
    return fig(svg("".join(o), W, H, "Cross-machine building blocks S1 to S4"),
               "Section 3's building blocks. S1 (the Studio store as a private GitHub repo) was the doc's default "
               "and is declined by decision 2; the data-repo path is the existing Sync of <code>~/CADExporter/</code>.",
               "from the doc", "wide")


def prop_overview_table(head, rows, idx) -> str:
    trs = []
    for r in rows:
        k = r[0].strip()[0]
        col = PROPS.get(k, ("", "s1"))[1]
        trs.append('<tr><td><span class="swatch" style="background:var(--%s)"></span>%s</td>%s</tr>'
                   % (col, inline(r[0]), "".join("<td>%s</td>" % inline(c) for c in r[1:])))
    th = "".join("<th>%s</th>" % inline(h) for h in head)
    return '<div class="tablewrap"><table class="md"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>' % (th, "".join(trs))


def wire_fig(key, code, fn) -> str:
    name, col = PROPS[key]
    return fig(fn(), "Proposal %s, %s, drawn from the doc's ASCII mock (below, as written). Markers and boards in it "
               "are real OpenCV renders; Fusion's chrome is generic." % (key, name), "wireframe", "wire") + ascii_fold(code)


def prop_effort_one(key) -> str:
    return fig(effort_bar([key], pid="ef" + key), "Effort as the doc splits it; hover a segment.", "from the doc", "mid")


def prop_nesting(pid="ne") -> str:
    need(SRC_TEXT["prop"], "an `Arm` with two `Forearm`s, inserted\ntwice, gives four copies of IDs 8-20",
         "give new instances the first\n  free range")
    W, H = 960, 352
    o = [arrow_defs(pid)]

    def node(x, y, lab, cls="box", w=110):
        return ('<rect x="%d" y="%d" width="%d" height="26" rx="7" class="%s"/>' % (x, y, w, cls)
                + svg_text(x + w / 2, y + 17.5, lab, size=12.5, anchor="middle", cls="t-b"))
    o.append(svg_text(14, 24, "The design", cls="t-b", size=14))
    o.append(node(14, 40, "Robot", "b-acc", 90))
    paths = []
    for a in (1, 2):
        ay = 76 + (a - 1) * 124
        o.append('<path d="M40 66 V%d H60" class="edge e-mut"/>' % (ay + 13))
        o.append(node(60, ay, "Arm:%d" % a))
        for f in (1, 2):
            fy = ay + 36 + (f - 1) * 36
            o.append('<path d="M90 %d V%d H110" class="edge e-mut"/>' % (ay + 26, fy + 13))
            o.append(node(110, fy, "Forearm:%d" % f, "box2", 104))
            paths.append("Arm:%d+Forearm:%d" % (a, f))
    o.append(svg_text(14, 334, "a decal drawn once in Forearm", size=12, cls="t-mut"))
    L = [charuco_layout(2, 13, "DICT_4X4_100", 0.737, ids=list(range(8 + 13 * i, 21 + 13 * i))) for i in range(4)]
    sq = 13.0
    o.append('<rect x="250" y="10" width="300" height="332" rx="12" class="b-bad"/>')
    o.append(svg_text(264, 32, "Today: one image on every copy", cls="t-b t-bad", size=13.5))
    for i in range(4):
        x = 272 + i * 68
        o.append(g_charuco(L[0], x, 46, sq))
        o.append(svg_text(x + sq, 46 + 13 * sq + 18, "8-20", size=12, anchor="middle", cls="t-mono t-bad t-b"))
    o.append(svg_text(264, 314, "four copies of IDs 8-20: a confident pose", size=12, cls="t-ink2"))
    o.append(svg_text(264, 330, "for the wrong copy; ChArUco merges two", size=12, cls="t-ink2"))
    o.append('<line x1="556" y1="200" x2="590" y2="200" class="edge e-good" marker-end="url(#%s-ahg)"/>' % pid)
    o.append('<rect x="596" y="10" width="350" height="332" rx="12" class="b-good"/>')
    o.append(svg_text(610, 32, "Instance table at the top: one range per path", cls="t-b t-good", size=13.5))
    for i in range(4):
        x = 618 + i * 82
        o.append(g_charuco(L[i], x + 8, 46, sq))
        lo = 8 + 13 * i
        o.append(svg_text(x + 8 + sq, 46 + 13 * sq + 18, "%d-%d" % (lo, lo + 12), size=12, anchor="middle", cls="t-mono t-good t-b"))
        o.append(svg_text(x + 8 + sq, 46 + 13 * sq + 36, paths[i].split("+")[0], size=10.5, anchor="middle", cls="t-mut"))
        o.append(svg_text(x + 8 + sq, 46 + 13 * sq + 50, paths[i].split("+")[1], size=10.5, anchor="middle", cls="t-mut"))
    o.append(svg_text(610, 314, "allocated once, at the top, over fully", size=12, cls="t-ink2"))
    o.append(svg_text(610, 330, "expanded leaves; printed ranges are pinned", size=12, cls="t-ink2"))
    return fig(svg("".join(o), W, H, "Composition multiplies one decal into four colliding copies"),
               "Section 9's example, with real 2 x 13 ChArUco boards (DICT_4X4_100). Ranges after 8-20 follow the "
               "“first free range” rule and are illustrative.", "illustrative", "wide")


def prop_capacity() -> str:
    need(SRC_TEXT["prop"], "C      = smallest of {50, 100, 250, 1000} with C >= max(1.5 * N_need, N_need + 10)",
         "Two arms with L1 + L2 (21 markers each) plus a hand tag is 43, which\nsuggests `_100`")
    N = 21 * 2 + 1
    thr = max(1.5 * N, N + 10)
    C = min(c for c in (50, 100, 250, 1000) if c >= thr)
    W, H = 960, 170
    lx = lambda v: 40 + (math.log10(v) - math.log10(20)) / (math.log10(1300) - math.log10(20)) * 880
    o = ['<line x1="40" y1="96" x2="920" y2="96" class="axis" stroke-width="2"/>']
    for c in (50, 100, 250, 1000):
        cls = "b-good" if c == C else "box"
        o.append('<rect x="%g" y="82" width="56" height="28" rx="7" class="%s"/>' % (lx(c) - 28, cls))
        o.append(svg_text(lx(c), 101, "_%d" % c, size=12.5, anchor="middle", cls="t-mono t-b"))
    o.append('<line x1="%g" y1="40" x2="%g" y2="82" class="edge e-acc" stroke-width="2"/>' % (lx(N), lx(N)))
    o.append(svg_text(lx(N), 34, "N_need = %d" % N, size=12.5, anchor="middle", cls="t-acc t-b"))
    o.append('<line x1="%g" y1="110" x2="%g" y2="140" class="edge e-vio" stroke-width="2"/>' % (lx(thr), lx(thr)))
    o.append(svg_text(lx(thr), 156, "headroom: max(1.5 x %d, %d + 10) = %g" % (N, N, thr), size=12.5, anchor="middle", cls="t-vio t-b"))
    o.append(svg_text(lx(C) + 40, 70, "smallest that fits: _%d" % C, size=12.5, cls="t-good t-b"))
    return fig(svg("".join(o), W, H, "Capacity rule worked through"),
               "Recomputed at build time from the doc's formula: 2 arms x (L1 + L2 = 21) + 1 hand tag = %d, so "
               "<code>DICT_4X4_%d</code>, as the doc says. Log scale." % (N, C), "from the doc", "mid")


def parse_zmax_table():
    t = SRC_TEXT["prop"]
    rows = []
    for ln in t.splitlines():
        m = re.match(r"^\| (.+?) \| ([\d.]+) mm \| (\d) \| ([\d.]+) m \| ([\d.]+) m \|$", ln)
        if m:
            rows.append((m.group(1), float(m.group(2)), int(m.group(3)), float(m.group(4)), float(m.group(5))))
    return rows


def prop_zmax() -> str:
    rows = parse_zmax_table()
    if len(rows) != 5:
        warn("Z_max table parse found %d rows" % len(rows))
    ok, tot = 0, 0
    for lab, s, M, z0, z45 in rows:
        for th, z in ((0, z0), (45, z45)):
            tot += 1
            ok += int(abs(round(zmax(s, M, th), 2) - z) < 0.006)
    W, H = 960, 400
    L, R, T, B = 70, 740, 24, 350
    X = lambda th: L + th / 60.0 * (R - L)
    Y = lambda z: B - z / 1.4 * (B - T)
    o = ['<rect x="%g" y="%g" width="%g" height="%g" class="band"/>' % (L, Y(0.8), R - L, Y(0.3) - Y(0.8))]
    o.append(svg_text(L + 8, Y(0.8) + 16, "default working range 0.3-0.8 m (open question 5)", size=11.5, cls="t-acc"))
    for z in (0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4):
        o.append('<line x1="%g" y1="%g" x2="%g" y2="%g" class="grid"/>' % (L, Y(z), R, Y(z)))
        o.append(svg_text(L - 8, Y(z) + 4, "%.1f" % z, size=11.5, anchor="end", cls="t-mut"))
    for th in range(0, 61, 15):
        o.append(svg_text(X(th), B + 18, "%d°" % th, size=11.5, anchor="middle", cls="t-mut"))
    o.append(svg_text(L, 14, "Z_max (m)", size=12, cls="t-mut"))
    o.append(svg_text((L + R) / 2, B + 40, "worst view angle theta", size=12, anchor="middle", cls="t-mut"))
    o.append('<line x1="%g" y1="%g" x2="%g" y2="%g" class="axis"/>' % (X(45), T, X(45), B))
    labels = []
    for i, (lab, s, M, z0, z45) in enumerate(rows):
        col = "s%d" % (i + 1)
        pts = " ".join("%g,%g" % (X(th), Y(zmax(s, M, th))) for th in range(0, 61, 2))
        o.append('<polyline points="%s" fill="none" stroke-width="2" class="st-%s"/>' % (pts, col))
        for th, z in ((0, z0), (45, z45)):
            o.append('<circle cx="%g" cy="%g" r="5" class="f-%s" stroke="var(--surface)" stroke-width="2" data-tip="%s at %d°: '
                     'doc %.2f m, recomputed %.3f m"/>' % (X(th), Y(zmax(s, M, th)), col, esc(lab), th, z, zmax(s, M, th)))
        labels.append((Y(zmax(s, M, 60)), re.sub(r"\s*\(.*?\)", "", lab), col, s, M))
    labels.sort()
    last = -99
    for y, lab, col, s, M in labels:
        y = max(y, last + 30)
        last = y
        o.append('<rect x="%g" y="%g" width="10" height="10" rx="2" class="f-%s"/>' % (R + 10, y - 9, col))
        o.append(svg_text(R + 26, y, lab, size=12, cls="t-b"))
        o.append(svg_text(R + 26, y + 14, "s = %g mm, M = %d" % (s, M), size=11, cls="t-mut"))
    chart = svg("".join(o), W, H, "Maximum detection range by view angle")
    return fig(chart, "Z_max = f_px * s * cos(theta) / (M * p), with f_px = 960 / tan(34.5°) = %.0f (D435 colour, HFOV "
               "about 69°, unverified), p = 5, b = 1. Dots mark the table's two columns; %d of %d table values "
               "reproduce to two decimals." % ((1920 / 2) / math.tan(math.radians(34.5)), ok, tot), "from the doc", "wide")


def prop_corr_check(ck) -> str:
    c = ck["corr"]
    rows = "".join("<tr><td>%dx%d</td>%s</tr>" % (n, n, "".join("<td>%d</td>" % c["DICT_%dX%d_%d" % (n, n, N)]
                                                              for N in (50, 100, 250, 1000))) for n in (4, 5, 6, 7))
    rows += '<tr><td>AprilTag 36h11</td><td colspan="4">%d</td></tr>' % c["DICT_APRILTAG_36h11"]
    md_ = ck["mindist"]
    bars = "".join('<div class="sb" style="margin:4px 0"><span style="width:110px;font:13px var(--mono)">4X4_%d</span>'
                   '<span class="track"><span class="fill" style="display:block;width:%d%%;background:var(--s1)"></span></span>'
                   '<span class="num">%d</span></div>' % (N, md_[N] * 20, md_[N]) for N in (50, 100, 250, 1000))
    return check_box("Correction bits and distances, read from OpenCV %s" % ck["cv"],
                     "<p>The family table marks OpenCV's stored correction bits as unverified. "
                     "<code>maxCorrectionBits</code> as this OpenCV build reports it:</p>"
                     '<div class="tablewrap" style="max-width:520px"><table class="md"><thead><tr><th>bits</th>'
                     "<th>_50</th><th>_100</th><th>_250</th><th>_1000</th></tr></thead><tbody>%s</tbody></table></div>"
                     "<p>And the doc's “within one bit size, smaller is safer”: the minimum Hamming distance "
                     "between any two IDs (all four rotations) in each 4x4 dictionary:</p>%s"
                     "<p style=\"font-size:14px;color:var(--muted)\">The Studio's OpenCV may differ in version; this "
                     "settles the question for this build only.</p>" % (rows, bars))


def prop_ratios() -> str:
    need(SRC_TEXT["prop"], "`marker <= checker * M / (M + 2)`: 0.75 for 4x4,\n0.78 for 5x5, 0.80 for 6x6",
         "His L1 (0.748) and L2 (0.737) pass")
    W, H = 960, 210
    L, R = 200, 900
    X = lambda v: L + (v - 0.6) / 0.25 * (R - L)
    o = []
    for v in (0.6, 0.65, 0.7, 0.75, 0.8, 0.85):
        o.append('<line x1="%g" y1="20" x2="%g" y2="170" class="grid"/>' % (X(v), X(v)))
        o.append(svg_text(X(v), 188, "%.2f" % v, size=11.5, anchor="middle", cls="t-mut"))
    o.append(svg_text(L, 206, "marker / checker", size=12, cls="t-mut"))
    lims = [("4x4 (M = 6)", 6), ("5x5 (M = 7)", 7), ("6x6 (M = 8)", 8)]
    for i, (lab, M) in enumerate(lims):
        y = 30 + i * 46
        lim = M / (M + 2.0)
        o.append(svg_text(L - 12, y + 16, lab, size=13, anchor="end", cls="t-b"))
        o.append('<rect x="%g" y="%d" width="%g" height="22" rx="4" class="f-s1" opacity="0.28"/>' % (L, y, X(lim) - L))
        o.append('<line x1="%g" y1="%d" x2="%g" y2="%d" class="edge e-acc" stroke-width="3"/>' % (X(lim), y - 4, X(lim), y + 26))
        o.append(svg_text(X(lim) + 6, y + 16, "limit %.3f" % lim, size=12, cls="t-acc t-b"))
        if M == 6:
            for v, nm in ((0.748, "L1 0.748"), (0.737, "L2 0.737")):
                o.append('<circle cx="%g" cy="%d" r="6" class="f-s3" data-tip="%s, under the 4x4 limit: passes"/>' % (X(v), y + 11, nm))
            o.append(svg_text(X(0.737) - 8, y + 16, "L2, L1 ✓", size=12, anchor="end", cls="t-good t-b"))
    return fig(svg("".join(o), W, H, "ChArUco marker-to-checker ratio limits"),
               "Rule 5: a ChArUco marker sits in a white square, so marker &lt;= checker x M / (M + 2), which keeps one "
               "module of quiet zone on each side. Zach's L1 and L2 sit just under the 4x4 limit.", "from the doc", "mid")


def prop_physical(head, rows, idx) -> str:
    def cls(c):
        c = c.lower()
        if c.startswith("native"):
            return "c-y"
        if c.startswith("later"):
            return "c-p"
        if c.startswith("none"):
            return "c-n"
        return "c-q"
    th = "".join("<th>%s</th>" % inline(h) for h in head[1:])
    trs = []
    for r in rows:
        k = r[0].strip()
        col = PROPS.get(k, ("", "s1"))[1]
        tds = "".join('<td><span class="cell %s" style="width:auto;height:auto;padding:4px 9px;display:inline-block;'
                      'font-weight:600;font-size:13.5px;text-align:left;line-height:1.35">%s</span></td>' % (cls(c), inline(c)) for c in r[1:])
        trs.append('<tr><td><span class="swatch" style="background:var(--%s)"></span><b>%s</b> %s</td>%s</tr>'
                   % (col, k, esc(PROPS.get(k, ("",))[0]), tds))
    leg = ('<div class="legend"><span><span class="cell c-y">✓</span>native</span><span><span class="cell c-p">◐</span>later'
           '</span><span><span class="cell c-n">✗</span>none</span><span><span class="cell c-q">?</span>other</span></div>')
    return ('<div class="tablewrap"><table class="md"><thead><tr><th>Proposal</th>%s</tr></thead><tbody>%s</tbody></table></div>'
            % (th, "".join(trs)) + leg
            + '<aside class="callout co-mini decal"><b>Since 2026-10-07:</b> the <b>3D print</b> column is now the one that '
              'matters: tags will be 3D printed as real geometry (<a href="#decisions">decision 6</a>).</aside>')


def parse_scores():
    t = SRC_TEXT["prop"]
    sec = t[t.index("## 12. Comparison matrix"):t.index("## 13. Recommendation")]
    rows = [ln for ln in sec.splitlines() if ln.startswith("|")]
    rows = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    rows = [r for r in rows if not all(re.fullmatch(r":?-{2,}:?", c) for c in r)]
    head, body = rows[0], rows[1:]
    out = []
    for r in body:
        crit = r[0].replace("**", "")
        cells = []
        for c in r[1:]:
            c2 = c.replace("**", "")
            m = re.match(r"^(\d+)(?::\s*(.*))?$", c2)
            cells.append((int(m.group(1)), m.group(2) or "") if m else (None, c2))
        out.append((crit, cells))
    return head, out


def prop_scores(head, rows, idx) -> str:
    h, data = parse_scores()
    keys = [c.split(" ")[0] for c in h[1:]]
    th = '<th>Criterion</th>' + "".join('<th><span class="swatch" style="background:var(--%s)"></span>%s</th>'
                                        % (PROPS[k][1], inline(c)) for k, c in zip(keys, h[1:]))
    trs = []
    totals = None
    for crit, cells in data:
        if crit.lower().startswith("total"):
            totals = cells
            continue
        best = max(c[0] for c in cells)
        tds = []
        for k, (v, note) in zip(keys, cells):
            tds.append('<td><div class="sb"><span class="num%s">%d</span><span class="track"><span class="fill" '
                       'style="display:block;width:%d%%;background:var(--%s)"></span></span></div>%s</td>'
                       % (" top" if v == best else "", v, v * 20, PROPS[k][1],
                          '<div class="note">%s</div>' % inline(note) if note else ""))
        trs.append('<tr><td class="crit">%s</td>%s</tr>' % (inline(crit), "".join(tds)))
    if totals:
        computed = [sum(cells[i][0] for crit, cells in data if not crit.lower().startswith("total")) for i in range(5)]
        for (v, _), c in zip(totals, computed):
            if v != c:
                warn("comparison total %s != column sum %s" % (v, c))
        best = max(v for v, _ in totals)
        tds = "".join('<td><div class="sb"><span class="num%s" style="width:24px">%d</span><span class="track" '
                      'style="height:14px"><span class="fill" style="display:block;width:%.1f%%;background:var(--%s)"></span>'
                      '</span></div><div class="note">of 40</div></td>' % (" top" if v == best else "", v, v / 40 * 100, PROPS[k][1])
                      for k, (v, _) in zip(keys, totals))
        trs.append('<tr class="total"><td class="crit">Total /40</td>%s</tr>' % tds)
    table = '<div class="tablewrap"><table class="scores"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>' % (th, "".join(trs))
    return (fig(table, "The doc's matrix with each score drawn as a bar out of 5 (green number = best in the row). "
                "Totals are re-added at build time and match the doc. For effort and risk, 5 means cheap and safe.",
                "from the doc")
            + '<details class="src"><summary>Table as written</summary>%s</details>' % md_table(head, rows))


def prop_reco(pid="rc") -> str:
    need(SRC_TEXT["prop"], "Ship C's engine behind E's front door as v1, grow into full E, and keep\nB and A as optional later add-ons",
         "Custom Features stay out until Autodesk\nreleases them from preview")
    W, H = 960, 420
    o = [arrow_defs(pid)]
    o.append('<rect x="14" y="14" width="932" height="34" rx="9" class="b-vio"/>')
    o.append(svg_text(480, 36, "Truth: model.markers in the sidecar (cad_config.json). IDs belong to instances.", size=13, anchor="middle", cls="t-b"))
    o.append('<rect x="14" y="66" width="250" height="132" rx="12" class="box" style="stroke:var(--s3);stroke-width:2.5"/>')
    o.append(svg_text(28, 90, "C, Ghost Slots: the engine", cls="t-b", size=13.5))
    for i, s in enumerate(("instance table", "allocator: retention + pins", "per-instance render at export", "exact pose from the rectangle")):
        o.append(svg_text(36, 114 + i * 20, "• " + s, size=12.5))
    o.append('<rect x="14" y="214" width="250" height="132" rx="12" class="box" style="stroke:var(--s5);stroke-width:2.5"/>')
    o.append(svg_text(28, 238, "E, Cockpit Markers: front door", cls="t-b", size=13.5))
    for i, s in enumerate(("Make Marker command", "rectangle or face → decal", "coded warnings", "later: the Markers lens")):
        o.append(svg_text(36, 262 + i * 20, "• " + s, size=12.5))
    o.append('<path d="M264 132 C300 132 300 200 330 200" class="edge" marker-end="url(#%s-ah)"/>' % pid)
    o.append('<path d="M264 280 C300 280 300 210 330 210" class="edge" marker-end="url(#%s-ah)"/>' % pid)
    o.append('<rect x="334" y="66" width="270" height="280" rx="12" class="b-good"/>')
    o.append(svg_text(348, 90, "v1", cls="t-b t-good", size=15))
    v1 = ["markers.py: record + adapters", "bit tables + S3 renderer", "instance allocator", "suggestion engine",
          "ChArUco-aware Fiducial", "export: per-instance + print folder", "Make Marker command", "W_MARKER_* warnings",
          "S1: Studio store git repo"]
    for i, s in enumerate(v1):
        o.append(svg_text(352, 114 + i * 25, "%d. %s" % (i + 1, s), size=12.5, cls="t-bad" if i == 8 else ""))
    o.append('<line x1="348" y1="%d" x2="%d" y2="%d" class="edge e-bad" stroke-width="2"/>' % (114 + 8 * 25 - 4, 352 + 200, 114 + 8 * 25 - 4))
    o.append('<line x1="604" y1="140" x2="630" y2="140" class="edge" marker-end="url(#%s-ah)"/>' % pid)
    o.append('<rect x="634" y="66" width="312" height="132" rx="12" class="box"/>')
    o.append(svg_text(648, 90, "v2", cls="t-b", size=15))
    for i, s in enumerate(("Cockpit Markers lens", "commandTerminated auto-refit", "per-instance parent decals, if the", "  probe allows them")):
        o.append(svg_text(652, 114 + i * 20, ("• " if not s.startswith("  ") else "  ") + s.strip(), size=12.5))
    o.append('<line x1="790" y1="198" x2="790" y2="212" class="edge" marker-end="url(#%s-ah)"/>' % pid)
    o.append('<rect x="634" y="214" width="312" height="132" rx="12" class="box2"/>')
    o.append(svg_text(648, 238, "v3, if wanted", cls="t-b", size=15))
    for i, s in enumerate(("B's board editor, docked", "for ChArUco editing", "one-shot Materialize as bodies", "(apriltag3D's cluster method)")):
        o.append(svg_text(652, 262 + i * 20, s, size=12.5, cls="t-mut" if i % 2 else ""))
    o.append('<rect x="14" y="362" width="932" height="46" rx="10" class="b-warn"/>')
    o.append(svg_text(30, 383, "Custom Features stay out until Autodesk releases them from preview.", size=13, cls="t-b"))
    o.append(svg_text(30, 400, "Since 2026-10-07: S1 is declined (item 9 struck), and real geometry in the timeline is the primary output (decisions 6-7).",
                      size=12, cls="t-dec"))
    return fig(svg("".join(o), W, H, "Recommendation: C's engine behind E's front door"),
               "Section 13 as a flow. Item 9 is struck because decision 2 declines S1; in this plan geometry "
               "appeared only in v3, and decisions 6 and 7 since make it the primary output.", "from the doc", "wide")


def prop_steps() -> str:
    need(SRC_TEXT["prop"], "**Measure before building (about 1 day).**", "**Pure core with tests (about 4 days).**",
         "**Export wiring, then the command (about 4 days).**")
    seg = [("Measure", "about 1 day", 1, "s4"), ("Pure core with tests", "about 4 days", 4, "s3"),
           ("Export wiring, then Make Marker", "about 4 days", 4, "s1")]
    cells = "".join('<div data-tip="%s, %s" style="flex:%d;min-width:0;background:var(--%s);color:#fff;border-radius:8px;'
                    'padding:8px 10px;font-size:14px;line-height:1.3"><b style="display:block">%d. %s</b>%s</div>'
                    % (a, b, d, c, i + 1, a, b) for i, (a, b, d, c) in enumerate(seg))
    return fig('<div style="display:flex;gap:4px">%s</div>' % cells,
               "The first three build steps, to scale: about 9 working days before the command is on the rig.",
               "from the doc", "")


def prop_mirror(ck) -> str:
    g = marker_grid("DICT_4X4_50", 3)
    gm = [list(reversed(r)) for r in g]
    res = ck["mirror"]
    need(SRC_TEXT["prop"], "a mirrored\n   ArUco reads as nothing or as another ID")
    vis = ('<div class="mkrow"><figure>%s<figcaption>id 3</figcaption></figure><figure>%s<figcaption>id 3, mirrored</figcaption>'
           '</figure></div>' % (marker_svg("DICT_4X4_50", 3), marker_svg("DICT_4X4_50", 3, grid=gm, label="DICT_4X4_50 id 3 mirrored")))
    same = ", ".join(map(str, ck["mirror_same"])) or "none"
    return check_box("What a mirrored decal does to detection",
                     '<div class="twocol"><div>%s</div><div><p>Every ID of <code>DICT_4X4_50</code>, flipped left to right '
                     'and run through OpenCV %s\'s detector:</p><p><b>%d</b> read as nothing, <b>%d</b> as another ID, '
                     '<b>%d</b> as themselves (ids %s: their patterns survive a mirror plus a turn).</p><p style="font-size:14px;'
                     'color:var(--muted)">Top risk 1 says a mirrored ArUco reads as nothing or as another ID. In this '
                     'dictionary most mirrored IDs simply vanish. The doc makes step 1\'s round trip a standing test for this.</p>'
                     '</div></div>' % (vis, ck["cv"], res["none"], res["other"], res["same"], same))


OPEN_Q_STATUS = {
    1: ("open", "Not answered directly. Decision 4 says positions and IDs are defined in Fusion and travel with the export."),
    2: ("no", "Decision 1: the image in Fusion must always be truthful to the current dictionary and ID."),
    3: ("open", ""),
    4: ("no", "Decision 2: no private GitHub repo for Studio marker sets."),
    5: ("open", ""),
    6: ("yes", "Decision 6: tags will be 3D printed as real geometry, so the default “v1 is stickers only” no longer holds."),
}


def open_questions_html() -> str:
    t = SRC_TEXT["prop"]
    sec = t[t.index("## 14. Open questions for Zach"):t.index("## Appendix")]
    items = re.findall(r"^\d+\.\s+(.*?)(?=^\d+\.\s|\Z)", sec, flags=re.S | re.M)
    out = []
    for i, it in enumerate(items, 1):
        it = " ".join(it.split())
        m = re.match(r"\*\*(.+?)\*\*\s*(.*)$", it)
        q, rest = (m.group(1), m.group(2)) if m else (it, "")
        st, why = OPEN_Q_STATUS.get(i, ("open", ""))
        tag = {"open": '<span class="qtag">still open</span>', "no": '<span class="qtag no">answered: no</span>',
               "yes": '<span class="qtag yes">answered: yes, 3D printed</span>'}[st]
        out.append('<li><div><b>%s</b> %s</div><div class="qs">%s<span>%s</span></div></li>'
                   % (inline(q), '<span class="qd">%s</span>' % inline(rest) if rest else "", tag, esc(why)))
    if len(items) != 6:
        warn("expected 6 open questions, found %d" % len(items))
    return '<ol class="qlist">%s</ol>' % "".join(out)


def prop_legacy() -> str:
    a = charuco_layout(2, 8, "DICT_4X4_50", 0.75, legacy=False)
    b = charuco_layout(2, 8, "DICT_4X4_50", 0.75, legacy=True)
    dflt = cv2.aruco.CharucoBoard((2, 8), 1.0, 0.75, cvdict("DICT_4X4_50")).getLegacyPattern()
    diff = sum(1 for r in range(8) for c in range(2) if a["squares"][r][c] != b["squares"][r][c])
    need(SRC_TEXT["prop"], "OpenCV 4.6's even-row ChArUco pattern change (`setLegacyPattern`)")
    vis = ('<div class="mkrow"><figure>%s<figcaption>legacy = False</figcaption></figure><figure>%s<figcaption>legacy = True'
           '</figcaption></figure></div>' % (charuco_svg(a, 22, 2).replace('class="board"', 'class="board" style="width:60px"'),
                                             charuco_svg(b, 22, 2).replace('class="board"', 'class="board" style="width:60px"')))
    return check_box("The even-row ChArUco pattern, on a 2 x 8 board",
                     '<div class="twocol"><div>%s</div><div><p>The same 2 x 8 board (L1\'s shape), drawn by OpenCV %s both '
                     'ways. <b>%d of 16</b> squares swap colour, so every marker moves to a different square. This build\'s '
                     'default is <code>legacy = %s</code>.</p><p style="font-size:14px;color:var(--muted)">Still unverified: '
                     'which pattern the Studio\'s OpenCV draws, which is why the doc wants the golden tests to '
                     'cover it.</p></div></div>' % (vis, cv2.__version__, diff, dflt))


def build_prop(ck: dict) -> str:
    t = SRC_TEXT["prop"]
    title, pre, secs = split_md(t)
    check_effort()
    keys = [s["key"] for s in secs]
    for k in ("1", "2", "2.1", "2.2", "2.3", "2.4", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14"):
        if k not in keys:
            warn("proposal section missing: " + k)
    wires = {"4": ("A", wire_a), "5": ("B", wire_b), "6": ("C", wire_c), "7": ("D", wire_d), "8": ("E", wire_e)}
    hooks: dict = {
        "1": {"table": prop_requirements},
        "2.1": {"after": prop_studio_model()},
        "2.2": {"lead": prop_pipeline(), "after": prop_frames()},
        "2.4": {"table": prop_facts_table},
        "3": {"lead": prop_anatomy(),
              "fence": prop_record,
              "table": lambda h, r, i: prop_overview_table(h, r, i),
              "after": prop_sync()},
        "9": {"lead": prop_nesting(),
              "after": callout('Decision 5 (2026-10-07) asks for exactly this override, per copy, at the sub-assembly '
                               'level and for IDs only. Because the tags are now printed geometry, the design response '
                               'realises each override as one tag part per ID snapped to a slot on the component. '
                               '<a href="#decisions">See the decisions.</a>', "note")},
        "10": {"fence": lambda code, info, idx: ('<div class="formula">%s</div>' % esc(code)) + (prop_capacity() if idx == 0 else ""),
               "table": lambda h, r, idx: md_table(h, r) + (prop_zmax() if idx == 0 else prop_corr_check(ck)),
               "after": prop_ratios()},
        "11": {"table": prop_physical},
        "12": {"table": prop_scores, "after": fig(effort_bar(list("ABCDE"), pid="efall"),
                                                   "Effort side by side, in the doc's own day counts.", "from the doc", "mid")},
        "13": {"lead": prop_reco(), "after": prop_steps() + prop_mirror(ck) + callout(
            "Top risk 2 asked Zach to accept that Fusion shows instance #1's IDs on every copy. "
            "<b>Decision 1 rejects that</b>: the image must be truthful on every copy.", "warn", "Since 2026-10-07")},
        "14": {"replace": open_questions_html()},
        "appendix-unverified-facts": {"toc": "Appendix: unverified facts", "after": prop_legacy()},
    }
    for sk, (pk, fn) in wires.items():
        hooks[sk] = {"fence": (lambda pk_, fn_: (lambda code, info, idx: wire_fig(pk_, code, fn_)))(pk, fn),
                     "after": prop_effort_one(pk)}
    notes = {"4": callout("A is the one proposal that builds real bodies in the timeline and regenerates them, which is "
                          "the shape decisions 6 and 7 ask for; its decal mode is the secondary path now.", "note",
                          "Since 2026-10-07"),
             "5": decal_note(), "7": decal_note(), "8": decal_note(
                 "Its fallback of showing instance #1's image on every copy is ruled out by decision 1.")}
    for sk, html_ in notes.items():
        hooks[sk]["lead"] = html_
    body, toc = render_sections(secs, hooks)
    toc.insert(0, (2, "decisions", "Decisions since (2026-10-07)"))
    intro = render_md(pre)
    head = decisions_html("decisions", with_figs=True)
    return page_html("prop", "ChArUco Studio in Fusion: five proposals",
                     "Proposal · 3 of 3",
                     "Five ways to give Fusion an ArUco and ChArUco generator with unique IDs, smart suggestions and "
                     "assembly-wide uniqueness, each drawn as a wireframe and scored against Zach's seven requirements. "
                     "The recommendation joins C's engine to E's front door.",
                     "Source: <code>docs/proposal_charuco_studio_in_fusion.md</code> (about 8,000 words; every section "
                     "is below).", head + intro + body, toc,
                     "Five UI/UX proposals for ChArUco Studio in Fusion, with wireframes, scores and a recommendation.")


# ==================================================================== index

def build_index(ck: dict) -> str:
    _, _, psecs = split_md(SRC_TEXT["prop"])
    app = next((s for s in psecs if s["key"].startswith("appendix")), None)
    n_unver = sum(1 for ln in (app["lines"] if app else []) if ln.startswith("- "))
    _, data = parse_scores()
    tot = [c for crit, c in data if crit.lower().startswith("total")][0]
    best = max(v for v, _ in tot)
    hero_lay = charuco_layout(13, 2, "DICT_4X4_100", 0.737, ids=list(range(8, 21)))
    hero = ('<div class="figbody" style="padding:14px;display:flex;gap:16px;align-items:center;flex-wrap:wrap;justify-content:center">'
            '%s</div>' % charuco_svg(hero_lay, 30, 0, label="ChArUco 13 x 2, ids 8-20").replace(
                'class="board"', 'class="board" style="width:min(100%,560px)"'))
    where = """
<h2 id="where">Where this stands</h2>
<ul>
<li><b>2026-10-02: research and proposal.</b> The prior-art survey found that drawing a marker is solved many times over,
but no tool in any CAD package keeps IDs unique across a design or an assembly, and none suggests a dictionary or a size.
The architecture survey picked a donor add-in for each layer of a Fusion add-in, except assembly-wide uniqueness, which no donor handles. The proposal drew five designs, scored them out
of 40, and recommended <b>C's engine behind E's front door</b> (both scored %d).</li>
<li><b>2026-10-07: Zach's decisions.</b> They overturn several of the proposal's defaults: the image in Fusion must be
truthful on every copy, there is no GitHub sync for Studio sets, the tags will be <b>3D printed as real geometry</b>
(decals become secondary), the ArUco step must live in the timeline and regenerate in place, a true-size PDF sheet is part
of the output, and IDs can be overridden per copy at the sub-assembly level.</li>
<li><b>Now:</b> a working prototype of the decisions is on branch <code>%s</code>, tested in Fusion and through the
URDF export: see <a href="how-it-works.html">How the prototype works</a>.</li>
<li><b>2026-10-08: auto size on a clicked face.</b> The default now comes from the face&#x27;s largest clear rectangle (holes and notches avoided); ten faces checked offline, in Fusion and against a brute force: see <a href="autosize-faces.html">Auto-size on faces</a>.</li>
</ul>""" % (best, BRANCH)
    stats = ('<div class="stats"><div class="stat"><b>%d</b><span>prior-art entries checked (some group several tools)</span></div>'
             '<div class="stat"><b>0</b><span>tools that track IDs across a design or an assembly</span></div>'
             '<div class="stat"><b>5</b><span>proposals, each with a wireframe</span></div>'
             '<div class="stat"><b>%d/40</b><span>top score, a tie between C and E</span></div>'
             '<div class="stat"><b>%d</b><span>facts the proposal flags as unverified</span></div>'
             '<div class="stat"><b>8</b><span>decisions since, on 2026-10-07</span></div></div>'
             % (len(MATRIX_ROWS), best, n_unver))
    thumbs = {
        "prior": "".join(marker_svg("DICT_4X4_50", i, cls="mk sm") for i in range(3)) + marker_svg("DICT_APRILTAG_36h11", 0, cls="mk sm"),
        "arch": svg("".join('<rect x="0" y="%d" width="200" height="14" rx="4" class="%s"/>' % (i * 18, c)
                            for i, c in enumerate(["b-dec", "b-good", "b-good", "b-acc", "b-vio", "b-vio", "b-good", "b-acc"])),
                    200, 142, "layer stack", style="width:180px"),
        "prop": charuco_svg(charuco_layout(5, 3, "DICT_4X4_50", 0.75), 26, 0).replace('class="board"', 'class="board" style="width:150px"')
                + marker_svg("DICT_4X4_50", 17, cls="mk"),
    }
    cards = [
        ("prior", "prior-art.html", "Prior art", "Seventeen entries across Fusion, SolidWorks, Blender, Rhino, OpenSCAD and the "
         "web, in one capability matrix; the verdict; the code worth borrowing; the AprilTag partial-byte trap.",
         "feature matrix · coverage chart · timeline · real markers · bit-level schematic"),
        ("arch", "fusion-architecture.html", "Fusion architecture", "Donor add-ins layer by layer, and the API facts that "
         "constrain a marker add-in: preview custom features, released decals, per-path occurrence attributes, palettes.",
         "layered architecture · attribute paths · regeneration flow · palette sequence · cell geometry"),
        ("prop", "charuco-studio-proposals.html", "Studio proposals", "Five proposals (A Marker Feature to E Cockpit "
         "Markers), drawn as Fusion wireframes and scored; ID uniqueness in composition; smart suggestions; the "
         "recommendation and open questions.",
         "5 wireframes · score matrix · range chart · ID allocation · recommendation flow"),
    ]
    rc = "".join('<a class="rcard" href="%s"><div class="thumb">%s</div><div class="rb"><h3>%s</h3><p>%s</p>'
                 '<div class="rm">%s</div></div></a>' % (fn, thumbs[k], t, d, m) for k, fn, t, d, m in cards)
    feature = ('<a class="feature" href="how-it-works.html"><div class="fthumb" aria-hidden="true">%s</div><div>'
               '<div class="fe">New · the prototype</div><h3>ArUco tags in Fusion: how the prototype works</h3>'
               '<p>Three toolbar commands, tags modelled as real two-colour geometry, per-copy ids at any assembly level '
               '(the demo\'s 12 copies go from all printing #0 to ids 0&ndash;11), the export chain to the URDF, and the evidence: '
               'Fusion screenshots, Fusion and URDF renders with the ids OpenCV reads back.</p>'
               '<span class="go">Open the report &rarr;</span></div></a>'
               % "".join(marker_svg("DICT_4X4_50", i, quiet=0.6, cls="") for i in range(12)))
    body = (feature + hero + where + stats + decisions_html("decisions", with_figs=True)
            + '<h2 id="reports">The three reports</h2><div class="reports">%s</div>' % rc
            + '<h2 id="questions">Open questions, after the decisions</h2>'
              '<p>The proposal ended with six questions and a default for each. Two are answered no, one yes; three stay open.</p>'
            + open_questions_html())
    toc = [(2, "where", "Where this stands"), (2, "decisions", "Decisions since (2026-10-07)"),
           (2, "reports", "The three reports"), (2, "questions", "Open questions")]
    return page_html("index", "ArUco markers in Fusion",
                     "Research and proposals · October 2026",
                     "An ArUco and ChArUco generator built into Fusion: what already exists, which add-ins to borrow "
                     "from, five ways to build it, and what Zach decided on 2026-10-07.",
                     "Three reports, generated from the Markdown in <code>docs/</code>. Built %s." % BUILT,
                     body, toc, "Overview of the ArUco-in-Fusion research, proposals and the 2026-10-07 decisions.")


# ===================================================================== main

SRC_TEXT: dict = {}


def main() -> int:
    global cv2, np
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pylib", help="folder to put on sys.path before importing cv2")
    ap.add_argument("--out", default=str(OUT_DIR), help="output folder (default reports/aruco)")
    a = ap.parse_args()
    if a.pylib:
        sys.path.insert(0, a.pylib)
    try:
        import cv2 as _cv2
        import numpy as _np
    except ImportError as e:
        print("needs OpenCV (cv2 with aruco) and numpy at build time: %s" % e, file=sys.stderr)
        return 2
    cv2, np = _cv2, _np
    for k, p in SRC.items():
        SRC_TEXT[k] = p.read_text(encoding="utf-8")
    ck = checks()
    story = tag36_byte_story(next(i for i in range(587)
                                  if all(marker_grid("DICT_APRILTAG_36h11", i)[6][c] == 0 for c in range(3, 7))))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pages = {"index.html": build_index(ck), "prior-art.html": build_prior(ck, story),
             "fusion-architecture.html": build_arch(), "charuco-studio-proposals.html": build_prop(ck)}
    for fn, html_ in pages.items():
        p = out / fn
        p.write_text(html_, encoding="utf-8")
        print("wrote %s (%s)" % (p, fmt_bytes(p.stat().st_size)))
    if WARNINGS:
        print("%d warning(s); see above" % len(WARNINGS), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
