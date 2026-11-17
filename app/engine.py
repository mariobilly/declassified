"""Declassified render engine: a picture becomes redaction bars on a typed document page.

Pipeline per output frame
  source frame -> (subject mask) -> fit into the page's portrait box -> tone map -> darkness map D
  page n = frame // hold: style, text, grid phase, paper and noise all come from a per-page seed
  D sampled per text-line row -> runs above the threshold -> snapped/merged redaction bars
  mid-tones -> toner specks + grey dots; then paper, text, specks, dots, bars, stamp, scan grain.

Geometry is computed in design units (page = 810 x 1000) so a preview and a 2x export get the same bars.
The sheet is rendered in one grey channel (paper, toner and bars are all neutral) and tinted at the end.
"""
import functools
import math
import threading
import zlib
from collections import OrderedDict

import cv2
import numpy as np

from . import documents as docs

PAGE_W, PAGE_H = int(docs.PAGE_W), int(docs.PAGE_H)
# Portrait box inside the page, measured on the reel (design units).
BOX = (36, 106, 776, 940)          # x0, y0, x1, y1
BASE_L = 13.0                      # line height on the reel (77 lines per page)
THR = 0.5                          # darkness above which a row turns into a bar

LAYOUTS = {
    # name: (canvas w, canvas h, page height on the canvas)
    "reel": (1080, 1920, 1000),     # exactly the reel: 810 x 1000 page at (135, 460) on #dcdcdc
    "portrait": (1080, 1350, 1150),
    "square": (1080, 1080, 940),
    "landscape": (1920, 1080, 940),
    "page": (1080, 1334, 1334),     # the page alone, edge to edge
}

STYLE_WEIGHTS = {"court": 0.42, "memo": 0.42, "sealed": 0.10, "privacy": 0.06}

DEFAULTS = {
    "layout": "reel", "quality": 1, "bg": "#dcdcdc",
    "styles": ["court", "memo", "sealed", "privacy"],
    "subject": "person",           # all | person | object
    "auto_levels": True, "amount": 0.45, "contrast": 1.0, "brightness": 0.0, "detail": 0.35, "invert": False,
    "line": 1.0, "min_bar": 1.0, "ragged": 0.45, "merge": 0.6,
    "speckle": 0.6, "dots": 0.45, "ghost": 0.1,
    "text": 1.0, "text_width": 1.0, "stamps": 0.35, "scan": 0.6,
    "hold": 2, "hold_jitter": 0.2, "jitter": 1.0, "stop_motion": True,
    "fit": "face", "zoom": 1.0, "pan_x": 0.0, "pan_y": 0.0,
    "seed": 7, "fps": 24, "duration": 5.0,
}

LIMITS = {
    "quality": (1, 2), "amount": (0.02, 0.98), "contrast": (0.2, 3.0), "brightness": (-0.6, 0.6),
    "detail": (0.0, 1.0), "line": (0.6, 2.5), "min_bar": (0.0, 6.0), "ragged": (0.0, 1.0),
    "merge": (0.0, 1.0), "speckle": (0.0, 1.5), "dots": (0.0, 1.5), "ghost": (0.0, 1.0), "text": (0.0, 1.0),
    "text_width": (0.6, 2.0), "stamps": (0.0, 1.0), "scan": (0.0, 1.5), "hold": (1, 24),
    "hold_jitter": (0.0, 1.0), "jitter": (0.0, 4.0), "zoom": (0.3, 4.0), "pan_x": (-1.0, 1.0),
    "pan_y": (-1.0, 1.0), "seed": (0, 2 ** 31 - 1), "fps": (6, 60), "duration": (0.5, 120.0),
}


def normalize(params=None):
    """Merge with defaults, coerce types, clamp ranges."""
    p = dict(DEFAULTS)
    for k, v in (params or {}).items():
        if k in DEFAULTS and v is not None:
            p[k] = v
    for k, (lo, hi) in LIMITS.items():
        try:
            v = float(p[k])
        except (TypeError, ValueError):
            v = float(DEFAULTS[k])
        if not math.isfinite(v):
            v = float(DEFAULTS[k])
        v = min(hi, max(lo, v))
        p[k] = int(round(v)) if isinstance(DEFAULTS[k], int) and not isinstance(DEFAULTS[k], bool) else v
    for k in ("auto_levels", "invert", "stop_motion"):
        p[k] = bool(p[k])
    if p["layout"] not in LAYOUTS:
        p["layout"] = DEFAULTS["layout"]
    if p["subject"] not in ("all", "person", "object"):
        p["subject"] = DEFAULTS["subject"]
    if p["fit"] not in ("face", "subject", "cover", "contain"):
        p["fit"] = "face"
    styles = p["styles"] if isinstance(p["styles"], (list, tuple)) else []
    p["styles"] = [s for s in STYLE_WEIGHTS if s in styles] or ["court"]
    p["bg"] = parse_hex(p["bg"], DEFAULTS["bg"])
    return p


def parse_hex(v, fallback):
    s = str(v or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    try:
        int(s, 16)
        if len(s) == 6:
            return "#" + s.lower()
    except ValueError:
        pass
    return fallback


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def seed_of(*parts):
    return zlib.crc32(repr(parts).encode()) & 0x7FFFFFFF


# --------------------------------------------------------------------------------------------- geometry
def canvas_geometry(p, scale=1.0):
    """Canvas size, page size in px, and k = px per design unit."""
    W, H, page_h = LAYOUTS[p["layout"]]
    q = p["quality"] * scale
    if p["layout"] == "page":
        W, H = W * q, H * q
        pw, ph = W, H
    else:
        W, H, ph = W * q, H * q, page_h * q
        pw = ph * docs.PAGE_W / docs.PAGE_H
    W, H = int(round(W)), int(round(H))
    pw, ph = int(round(pw)), int(round(ph))
    if scale == 1.0:  # exports: yuv420 wants even sizes
        W, H = W - W % 2, H - H % 2
        if p["layout"] == "page":
            pw, ph = W, H
    return W, H, pw, ph, ph / docs.PAGE_H


# --------------------------------------------------------------------------------------------- timeline
class Timeline:
    """Maps output frames to paper pages: each page is held for `hold` frames (+-1 with `jitter`)."""

    def __init__(self, seed, hold, jitter):
        self.hold, self.jitter = max(1, int(hold)), float(jitter)
        self.starts = [0]
        self._rng = np.random.default_rng(seed_of("timeline", seed, hold, round(jitter, 3)))
        self._lock = threading.Lock()

    def _extend(self, frame):
        while self.starts[-1] <= frame:
            n = self.hold
            if self.hold > 1 and self._rng.random() < self.jitter:
                n += int(self._rng.choice([-1, 1]))
            elif self.hold == 1 and self._rng.random() < self.jitter * 0.5:
                n += 1
            self.starts.append(self.starts[-1] + max(1, n))

    def page_of(self, frame):
        """(page index, first frame of that page)."""
        frame = max(0, int(frame))
        with self._lock:
            self._extend(frame)
            i = int(np.searchsorted(self.starts, frame, side="right")) - 1
            return i, self.starts[i]


@functools.lru_cache(maxsize=32)
def timeline(seed, hold, jitter):
    return Timeline(seed, hold, jitter)


def page_index(p, frame):
    return timeline(p["seed"], p["hold"], round(p["hold_jitter"], 3)).page_of(frame)


def source_frame(p, frame):
    """Which source frame feeds output frame `frame` (stop-motion holds the source too, like the reel)."""
    return page_index(p, frame)[1] if p["stop_motion"] else int(frame)


# --------------------------------------------------------------------------------------------- page spec
class PageSpec:
    """Everything random about one sheet of paper."""

    def __init__(self, p, page_idx):
        s = seed_of("page", p["seed"], page_idx)
        rng = np.random.default_rng(s)
        styles = p["styles"]
        w = np.array([STYLE_WEIGHTS[x] for x in styles], dtype=float)
        self.style = styles[int(rng.choice(len(styles), p=w / w.sum()))]
        self.L = BASE_L * p["line"]
        self.phase = float(rng.uniform(0, self.L))
        self.seed = s
        self.dx = float(rng.uniform(-2, 2) * p["jitter"])
        self.dy = float(rng.uniform(-2, 2) * p["jitter"])
        self.shadow = int(rng.choice([0, 0, 1, 1, 2]))   # 0 none, 1 left edge, 2 right edge
        self.punch = bool(rng.random() < 0.15)
        self.key = (self.style, s, round(self.L, 3), round(self.phase, 3), round(p["text_width"], 3),
                    round(p["stamps"], 3))
        self.layout = docs.layout_page(self.style, s, self.L, self.phase, text_width=p["text_width"],
                                       stamp_p=p["stamps"], content_top=BOX[1])


class LRU(OrderedDict):
    """Small thread-safe cache (exports render pages on several threads)."""

    def __init__(self, n):
        super().__init__()
        self.n = n
        self.lock = threading.Lock()

    def get_or(self, key, make):
        with self.lock:
            if key in self:
                self.move_to_end(key)
                return self[key]
        v = make()
        with self.lock:
            self[key] = v
            while len(self) > self.n:
                self.popitem(last=False)
        return v


_ink_cache = LRU(48)
_paper_cache = LRU(24)
_noise_cache = LRU(8)


def ink_layer(spec, k, pw, ph):
    return _ink_cache.get_or(spec.key + (round(k, 4), pw, ph), lambda: docs.render_ink(spec.layout, k, pw, ph))


def stamp_layer(spec, k):
    return _ink_cache.get_or(("stamp",) + spec.key + (round(k, 4),), lambda: docs.render_stamp(spec.layout.stamp, k))


def grain(seed, shape):
    """Fixed per-size noise texture (N(0,1)), reused across frames."""
    return _noise_cache.get_or((seed % 4, shape), lambda: np.random.default_rng(seed % 4).standard_normal(shape)
                               .astype(np.float32))


def paper_layer(spec, p, k, pw, ph):
    def make():
        rng = np.random.default_rng(spec.seed ^ 0x5A5A)
        scan = p["scan"]
        lo = rng.standard_normal((max(2, ph // 40), max(2, pw // 40))).astype(np.float32)
        lo = cv2.resize(lo, (pw, ph), interpolation=cv2.INTER_CUBIC) * (1.8 * scan)
        base = 249.0 + lo
        yy = np.linspace(-1, 1, ph, dtype=np.float32)[:, None]
        base = base - (1.5 * scan) * yy ** 2
        if spec.shadow:
            wd = max(2.0, 0.018 * pw)
            xx = np.arange(pw, dtype=np.float32)[None, :]
            dist = xx if spec.shadow == 1 else (pw - 1 - xx)
            base = base - (34.0 * scan) * np.exp(-dist / wd)
        img = np.ascontiguousarray(base, dtype=np.float32)
        if spec.punch:
            for cy in (0.22, 0.5, 0.78):
                cv2.circle(img, (int(0.028 * pw), int(cy * ph)), max(2, int(0.008 * pw)), 175.0, -1, cv2.LINE_AA)
        return np.clip(img, 0, 255)
    return _paper_cache.get_or((spec.seed, round(k, 4), pw, ph, round(p["scan"], 3)), make)


# --------------------------------------------------------------------------------------------- tone
_clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))


def fit_matrix(sw, sh, bw, bh, p, rect=None):
    """2x3 affine from source px to box px.

    cover / contain: the whole picture fills / fits the box. face / subject: `rect` (x0, y0, x1, y1 in source
    px: head and shoulders, or the cut-out's bounding box) is fitted into the box, like the reel's portrait.
    Zoom scales around the centre; pan moves the picture by up to half the overflow (or half the box).
    """
    if p["fit"] in ("face", "subject") and rect is not None:
        rx0, ry0, rx1, ry1 = rect
        rw, rh = max(1.0, rx1 - rx0), max(1.0, ry1 - ry0)
        s = min(bw / rw, bh / rh) * p["zoom"]
        cx, cy = (rx0 + rx1) / 2, (ry0 + ry1) / 2
        tx = bw / 2 - cx * s - p["pan_x"] * bw / 2
        ty = bh / 2 - cy * s - p["pan_y"] * bh / 2
        return np.array([[s, 0, tx], [0, s, ty]], dtype=np.float32), s
    s = (max if p["fit"] != "contain" else min)(bw / sw, bh / sh) * p["zoom"]
    ex, ey = sw * s - bw, sh * s - bh            # overflow in box px (negative = empty margin)
    tx = -ex / 2 - p["pan_x"] * abs(ex) / 2
    ty = -ey / 2 - p["pan_y"] * abs(ey) / 2
    return np.array([[s, 0, tx], [0, s, ty]], dtype=np.float32), s


def subject_rect(mask, w, h):
    """Bounding box of the subject (mask > 0.5) with a margin and headroom, in source px; None if no subject."""
    if mask is None:
        return None
    m = mask > 0.5
    if m.sum() < 0.002 * m.size:
        return None
    ys, xs = np.nonzero(m.any(axis=1))[0], np.nonzero(m.any(axis=0))[0]
    sy, sx = h / m.shape[0], w / m.shape[1]
    x0, x1, y0, y1 = xs[0] * sx, (xs[-1] + 1) * sx, ys[0] * sy, (ys[-1] + 1) * sy
    mw, mh = x1 - x0, y1 - y0
    return (x0 - 0.06 * mw, y0 - 0.07 * mh, x1 + 0.06 * mw, y1 + 0.03 * mh)


def union_rect(rects):
    rects = [r for r in rects if r is not None]
    if not rects:
        return None
    a = np.array(rects)
    return (float(a[:, 0].min()), float(a[:, 1].min()), float(a[:, 2].max()), float(a[:, 3].max()))


def _warp(img, M, s, size, border):
    # Pre-shrink big sources so the linear warp doesn't alias.
    if s < 0.5:
        f = min(1.0, s * 2)
        img = cv2.resize(img, (max(1, int(img.shape[1] * f)), max(1, int(img.shape[0] * f))),
                         interpolation=cv2.INTER_AREA)
        M = M.copy()
        M[:, :2] /= f
    return cv2.warpAffine(img, M, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=border)


def levels_of(dr, m, amount):
    """Tone anchors for a darkness image: (t, spread). The darkest `amount` of the subject lies above t."""
    sel = dr[m > 0.5]
    if sel.size < 200:
        sel = dr[m > 0.02] if int((m > 0.02).sum()) >= 200 else dr.ravel()
    sel = sel[::max(1, sel.size // 60000)]
    q = np.quantile(sel, [0.08, 1.0 - amount, 0.92])
    return float(q[1]), float(max(0.06, q[2] - q[0]))


def darkness(src, mask, p, levels=None, rect=None):
    """Darkness map D (PAGE_H x PAGE_W float32, 0 = paper, 1 = ink, bars above THR) for one source frame.

    src: HxWx3 uint8 RGB or HxW uint8 gray. mask: HxW float 0..1 (subject) or None. rect: subject box for
    fit = "subject" (defaults to this frame's mask box; videos pass one fixed box so the framing holds still).
    With auto levels the darkest `amount` of the subject lands above THR whatever the exposure.
    Returns (D, levels used) so a video export can smooth the levels over time.
    """
    gray = src if src.ndim == 2 else cv2.cvtColor(src, cv2.COLOR_RGB2GRAY)
    x0, y0, x1, y1 = BOX
    bw, bh = x1 - x0, y1 - y0
    sh_, sw_ = gray.shape[:2]
    if rect is None and p["fit"] in ("face", "subject"):
        rect = subject_rect(mask, sw_, sh_)
    M, s = fit_matrix(sw_, sh_, bw, bh, p, rect)
    g = _warp(gray, M, s, (bw, bh), 255)
    # Where the picture actually lands in the box (contain / zoom-out leave paper around it).
    m = np.zeros((bh, bw), np.float32)
    ax0, ay0 = float(M[0, 2]), float(M[1, 2])
    ax1, ay1 = ax0 + sw_ * s, ay0 + sh_ * s
    m[max(0, int(math.ceil(ay0))):max(0, int(ay1)), max(0, int(math.ceil(ax0))):max(0, int(ax1))] = 1.0
    if mask is not None:
        mm = _warp((np.clip(mask, 0, 1) * 255).astype(np.uint8), M, s, (bw, bh), 0).astype(np.float32) / 255.0
        m *= np.clip((mm - 0.25) * 2.0, 0, 1)
    if p["detail"] > 0:
        g = cv2.addWeighted(g, 1 - p["detail"], _clahe.apply(g), p["detail"], 0)
    dr = 1.0 - g.astype(np.float32) / 255.0
    if p["invert"]:
        dr = 1.0 - dr
    if p["auto_levels"]:
        t, spread = levels if levels is not None else levels_of(dr, m, p["amount"])
        d = THR + (dr - t) * (p["contrast"] * 0.55 / spread)
    else:
        t, spread = 1.0 - p["amount"], 0.5
        d = THR + (dr - t) * p["contrast"]
    d -= p["brightness"]
    np.clip(d, 0, 1, out=d)
    d *= m
    d = cv2.GaussianBlur(d, (0, 0), 1.1)
    D = np.zeros((PAGE_H, PAGE_W), np.float32)
    D[y0:y1, x0:x1] = d
    return D, (t, spread)


# --------------------------------------------------------------------------------------------- bars
def compute_bars(D, spec, p, rng):
    """Redaction bars as (x0, y0, x1, y1) in design units, one or more per text-line row."""
    L, phase = spec.L, spec.phase
    ragged, merge = p["ragged"], p["merge"]
    cw = L * 0.6                                   # one typewriter character
    snap = max(1.0, cw * 0.5)
    min_len = p["min_bar"] * cw
    merge_d = merge * 2.2 * cw
    cs = np.vstack([np.zeros((1, PAGE_W), np.float32), np.cumsum(D, axis=0, dtype=np.float32)])
    ncell = int(PAGE_W / cw) + 3
    xs_cells = np.arange(PAGE_W, dtype=np.float32) / cw
    bars, prev_edges = [], np.empty(0)
    for r in range(docs.grid_rows(L, phase)):
        y0 = phase + r * L
        a, b = int(round(y0)), int(round(min(y0 + L, PAGE_H)))
        if b - a < 2:
            continue
        prof = (cs[b] - cs[a]) / (b - a)
        if prof.max() < THR - 0.35:
            prev_edges = np.empty(0)
            continue
        noise = np.interp(xs_cells, np.arange(ncell), rng.standard_normal(ncell)) * (0.16 * ragged)
        on = (prof + noise) > THR
        if not on.any():
            prev_edges = np.empty(0)
            continue
        d = np.diff(np.concatenate(([0], on.astype(np.int8), [0])))
        starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
        runs = []
        for s, e in zip(starts.tolist(), ends.tolist()):
            if runs and s - runs[-1][1] < 0.8 * cw:      # close hairline gaps inside a word
                runs[-1][1] = e
            else:
                runs.append([s, e])
        edges = []
        for s, e in runs:
            if e - s < min_len:
                if prof[s:e].max() < THR + 0.22:
                    continue
                c = (s + e) / 2                          # very dark but tiny (pupils, nostrils): one char
                s, e = c - cw / 2, c + cw / 2
            x0 = math.floor(s / snap) * snap
            x1 = math.ceil(e / snap) * snap
            if rng.random() < ragged * 0.45:
                x0 -= snap * int(rng.integers(1, 3))
            if rng.random() < ragged * 0.45:
                x1 += snap * int(rng.integers(1, 3))
            if merge_d > 0 and prev_edges.size:
                j = int(np.argmin(np.abs(prev_edges - x0)))
                if abs(prev_edges[j] - x0) <= merge_d:
                    x0 = float(prev_edges[j])
                j = int(np.argmin(np.abs(prev_edges - x1)))
                if abs(prev_edges[j] - x1) <= merge_d:
                    x1 = float(prev_edges[j])
            x0, x1 = max(0.0, x0), min(float(PAGE_W), x1)
            if x1 - x0 >= 1:
                bars.append((x0, y0, x1, y0 + L))
                edges += [x0, x1]
        prev_edges = np.array(edges) if edges else np.empty(0)
    return bars


# --------------------------------------------------------------------------------------------- render
def render(D, p, frame, scale=1.0):
    """Render output frame `frame` from darkness map D. Returns HxWx3 uint8 RGB."""
    page_idx, _ = page_index(p, frame)
    spec = PageSpec(p, page_idx)
    W, H, pw, ph, k = canvas_geometry(p, scale)
    rng = np.random.default_rng(spec.seed ^ 0x77)
    scan = p["scan"]

    img = paper_layer(spec, p, k, pw, ph).copy()

    # Mid-tone band (where the picture is grey rather than black): specks, dots, ghost.
    spread = 0.3
    band = np.clip((D - (THR - spread)) * (1.0 / spread), 0, 1)
    if p["ghost"] > 0:
        gh = cv2.GaussianBlur(band * (D < THR + 0.1), (0, 0), 9)
        gh = cv2.resize(gh, (pw, ph), interpolation=cv2.INTER_LINEAR)
        img *= 1.0 - (0.28 * p["ghost"]) * gh

    # Text: img = img + a * (ink - img).
    if p["text"] > 0:
        a = ink_layer(spec, k, pw, ph).astype(np.float32) * (p["text"] / 255.0)
        img += a * (float(spec.layout.ink_rgb[0]) - img)

    # Toner specks (1-2 units).
    if p["speckle"] > 0:
        prob = (p["speckle"] * 0.12) * band ** 2.2
        hits1 = rng.random(D.shape, dtype=np.float32) < prob
        hits2 = (rng.random(D.shape, dtype=np.float32) < prob * 0.12).astype(np.uint8)
        hits = hits1 | cv2.dilate(hits2, np.ones((2, 2), np.uint8)).astype(bool)
        n = int(hits.sum())
        if n:
            sp = np.zeros(D.shape, np.float32)
            sp[hits] = rng.uniform(0.55, 0.92, n).astype(np.float32)
            sp = cv2.resize(sp, (pw, ph), interpolation=cv2.INTER_NEAREST if k >= 1 else cv2.INTER_AREA)
            img *= 1.0 - sp

    # Grey dots (round toner blobs).
    if p["dots"] > 0:
        cell = 12
        bc = cv2.resize(band, (PAGE_W // cell, PAGE_H // cell), interpolation=cv2.INTER_AREA)
        cy, cx = np.nonzero(rng.random(bc.shape) < (p["dots"] * 0.11) * bc ** 1.2)
        if cx.size:
            x = (cx * cell + rng.uniform(0, cell, cx.size)) * k
            y = (cy * cell + rng.uniform(0, cell, cx.size)) * k
            rad = rng.uniform(2.0, 3.6, cx.size) * k
            tone = rng.uniform(150.0, 165.0, cx.size)
            for xi, yi, ri, ti in zip(x.tolist(), y.tolist(), rad.tolist(), tone.tolist()):
                cv2.circle(img, (int(xi * 16), int(yi * 16)), max(8, int(ri * 16)), ti, -1, cv2.LINE_AA, 4)

    # Redaction bars.
    bars = compute_bars(D, spec, p, rng)
    if bars:
        g = grain(spec.seed, (ph, pw))
        seam_h = max(1, int(round(0.8 * k)))
        seams = rng.choice(3, len(bars), p=[0.4, 0.35, 0.25])   # stacked rows: solid / faint seam / white seam
        for (x0, y0, x1, y1), sm in zip(bars, seams.tolist()):
            X0, X1 = int(round(x0 * k)), int(round(x1 * k))
            Y0, Y1 = int(round(y0 * k)), int(round(y1 * k))
            if X1 > X0 and Y1 > Y0:
                img[Y0:Y1, X0:X1] = 31.0 + float(rng.uniform(-1.5, 1.5)) + (3.0 * scan) * g[Y0:Y1, X0:X1]
                if sm and Y1 - Y0 > seam_h + 1:
                    img[Y0:Y0 + seam_h, X0:X1] = 120.0 if sm == 1 else 228.0
        if scan > 0:  # a few toner drop-outs inside the black
            fleck = (g > 3.2) & (img < 45)
            img[fleck] = 125.0 + 50.0 * (g[fleck] - 3.2)

    # Stamp (multiplied, so it greys the text but bars stay black).
    st = stamp_layer(spec, k) if (spec.layout.stamp and p["stamps"] > 0) else None
    if st is not None:
        arr, sx, sy = st
        y0, x0 = max(0, sy), max(0, sx)
        y1, x1 = min(ph, sy + arr.shape[0]), min(pw, sx + arr.shape[1])
        if y1 > y0 and x1 > x0:
            a = arr[y0 - sy:y1 - sy, x0 - sx:x1 - sx].astype(np.float32) * (spec.layout.stamp["alpha"] / 255.0)
            img[y0:y1, x0:x1] *= 1.0 - a * (1.0 - 100.0 / 255.0)

    # Scan: a touch of blur and grain.
    if scan > 0:
        sig = 0.42 * k * min(1.0, scan + 0.2)
        if sig >= 0.25:
            img = cv2.GaussianBlur(img, (0, 0), sig)
        img += (2.2 * scan) * grain(spec.seed + 1, (ph, pw))

    page = np.clip(img, 0, 255).astype(np.uint8)
    page = cv2.merge([page, page, cv2.subtract(page, 1)])  # a hint of warm paper

    # Place the page on the backdrop.
    canvas = np.empty((H, W, 3), np.uint8)
    canvas[:] = hex_rgb(p["bg"])
    if p["layout"] == "page":
        ox, oy = 0, 0
    else:
        ox = (W - pw) // 2 + int(round(spec.dx * k))
        oy = (H - ph) // 2 + int(round(spec.dy * k))
    cx0, cy0 = max(0, ox), max(0, oy)
    cx1, cy1 = min(W, ox + pw), min(H, oy + ph)
    canvas[cy0:cy1, cx0:cx1] = page[cy0 - oy:cy1 - oy, cx0 - ox:cx1 - ox]
    return canvas


def render_frame(src, mask, p, frame, scale=1.0, levels=None):
    """Convenience: source frame -> output frame."""
    D, _ = darkness(src, mask, p, levels)
    return render(D, p, frame, scale)
