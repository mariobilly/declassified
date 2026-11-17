"""Opened media: a photo or a video, with cached cut-out masks, framing and tone levels.

Videos get an in-memory JPEG proxy (long side 720) for instant scrubbing and preview playback; exports
decode the file again at full working size. Masks are computed per source frame on demand and cached small.
"""
import os
import threading
import time
import uuid
from collections import OrderedDict

import cv2
import numpy as np
from PIL import Image, ImageOps

from . import engine, masks, media

WORK_SIDE = 1600      # photos are worked on at this long side (the page box is ~840 units tall)
PROXY_SIDE = 720      # video preview proxy
EXPORT_SIDE = 1280    # video export decode size
MASK_SIDE = 256       # cached mask size (U2-Net runs at 320 anyway)
SAMPLES = 8           # frames sampled for a video's framing and tone levels


class LRU(OrderedDict):
    def __init__(self, n):
        super().__init__()
        self.n = n
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            if key in self:
                self.move_to_end(key)
                return super().__getitem__(key)
        return None

    def put(self, key, v):
        with self.lock:
            self[key] = v
            self.move_to_end(key)
            while len(self) > self.n:
                self.popitem(last=False)


def _iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _small_mask(m):
    if m is None:
        return None
    h, w = m.shape
    f = MASK_SIDE / max(h, w)
    return (cv2.resize(m, (max(1, int(w * f)), max(1, int(h * f))), interpolation=cv2.INTER_AREA) * 255
            ).astype(np.uint8)


def load_image(path):
    im = Image.open(path)
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))   # transparent areas read as paper
        im = Image.alpha_composite(bg, im)
    rgb = np.asarray(im.convert("RGB"))
    h, w = rgb.shape[:2]
    f = min(1.0, WORK_SIDE / max(h, w))
    if f < 1:
        rgb = cv2.resize(rgb, (int(w * f), int(h * f)), interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(rgb)


class Source:
    """Base: id, path, name, kind, size, fps, frame count."""

    def __init__(self, path, display_name=None):
        self.id = uuid.uuid4().hex[:10]
        self.path = path
        self.name = display_name or os.path.basename(path)
        self.masks = LRU(1200)
        self.rects = {}
        self.levels = {}
        self.lock = threading.Lock()

    # -- per frame
    def mask(self, i, kind, rgb=None):
        """Subject mask for source frame i (float32 at the frame's size), or None for 'all'."""
        if kind == "all":
            return None
        key = (i, kind)
        small = self.masks.get(key)
        if small is None:
            rgb = self.frame(i) if rgb is None else rgb
            m = masks.subject_mask(rgb, kind)
            if m is None:
                return None
            small = _small_mask(m)
            self.masks.put(key, small)
        rgb_shape = (self.h, self.w) if rgb is None else rgb.shape[:2]
        return cv2.resize(small, (rgb_shape[1], rgb_shape[0]), interpolation=cv2.INTER_LINEAR).astype(
            np.float32) / 255.0

    def sample_indices(self):
        return [0]

    def rect(self, p):
        """Framing rect for fit = face / subject, steady over the whole clip (None for cover / contain)."""
        if p["fit"] not in ("face", "subject"):
            return None
        key = (p["fit"], p["subject"])
        if key in self.rects:
            return self.rects[key]
        faces, bodies = [], []
        for i in self.sample_indices():
            rgb = self.frame(i)
            m = self.mask(i, p["subject"], rgb) if p["subject"] != "all" else None
            if p["fit"] == "face":
                faces.append(masks.face_rect(rgb, m))
            if m is not None:
                bodies.append(engine.subject_rect(m, rgb.shape[1], rgb.shape[0]))
        n = len(faces)
        faces = [r for r in faces if r is not None]
        r = None
        if faces and (n == 1 or len(faces) >= max(2, 0.25 * n)):
            # The median face box holds still and shrugs off a missed or odd frame, but only when the faces
            # agree: clips with cuts or several people get the whole cut-out area instead.
            med = np.median(np.array(faces), axis=0)
            agree = [f for f in faces if _iou(f, med) > 0.3]
            if len(agree) >= 0.6 * len(faces):
                r = tuple(float(v) for v in np.median(np.array(agree), axis=0))
        if r is None:   # no (steady) face, or fit = subject: everything the cut-out covers over the clip
            r = engine.union_rect(bodies)
        if r is not None:   # framing at work size -> store as fractions so export (other size) can scale it
            r = (r[0] / self.w, r[1] / self.h, r[2] / self.w, r[3] / self.h)
        self.rects[key] = r
        return r

    def rect_px(self, p, w, h):
        r = self.rect(p)
        return None if r is None else (r[0] * w, r[1] * h, r[2] * w, r[3] * h)

    def tone(self, p):
        """Tone levels steady over the clip: median over sampled frames (None = per frame, for photos)."""
        return None

    def darkness(self, i, p, rgb=None):
        rgb = self.frame(i) if rgb is None else rgb
        m = self.mask(i, p["subject"], rgb)
        rect = self.rect_px(p, rgb.shape[1], rgb.shape[0])
        D, _ = engine.darkness(rgb, m, p, levels=self.tone(p), rect=rect)
        return D

    def info(self):
        # width/height: the working size (the preview proxy for a video); source_*: the file's real size, for display
        return {"id": self.id, "name": self.name, "kind": self.kind, "width": self.w, "height": self.h,
                "source_width": getattr(self, "src_w", self.w), "source_height": getattr(self, "src_h", self.h),
                "fps": self.fps, "frames": self.frames, "duration": self.duration, "has_audio": self.has_audio,
                "ready": self.ready_fraction(), "path": self.path}

    def ready_fraction(self):
        return 1.0

    def close(self):
        pass


class ImageSource(Source):
    kind = "image"

    def __init__(self, path, display_name=None):
        super().__init__(path, display_name)
        self.rgb = load_image(path)
        self.h, self.w = self.rgb.shape[:2]
        self.fps, self.frames, self.duration, self.has_audio = None, 1, 0.0, False

    def frame(self, i=0):
        return self.rgb


class VideoSource(Source):
    kind = "video"

    def __init__(self, path, display_name=None):
        super().__init__(path, display_name)
        pr = media.probe(path)
        self.fps, self.frames, self.duration, self.has_audio = pr["fps"], pr["frames"], pr["duration"], \
            pr["has_audio"]
        self.src_w, self.src_h = pr["width"], pr["height"]
        self.w, self.h = media.even_fit(self.src_w, self.src_h, PROXY_SIDE)
        self.proxy = []
        self.proxy_done = False
        self.proxy_error = None
        self._cv = threading.Condition()
        self._stop = False
        self._thread = threading.Thread(target=self._decode, daemon=True)
        self._thread.start()

    def _decode(self):
        reader = None
        try:
            reader = media.FrameReader(self.path, self.w, self.h)
            for rgb in reader:
                if self._stop:
                    break
                ok, buf = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 90])
                with self._cv:
                    self.proxy.append(buf.tobytes())
                    self._cv.notify_all()
        except Exception as e:
            self.proxy_error = str(e)
        finally:
            if reader:
                reader.close()
            with self._cv:
                self.proxy_done = True
                if self.proxy:
                    self.frames = len(self.proxy)    # the real count (nb_frames can be off)
                self._cv.notify_all()

    def ready_fraction(self):
        return 1.0 if self.proxy_done else min(0.99, len(self.proxy) / max(1, self.frames))

    def wait_frame(self, i, timeout=30.0):
        end = time.time() + timeout
        with self._cv:
            while len(self.proxy) <= i and not self.proxy_done:
                left = end - time.time()
                if left <= 0:
                    break
                self._cv.wait(left)
            if not self.proxy:
                raise RuntimeError(self.proxy_error or "This video has no readable frames.")
            return min(i, len(self.proxy) - 1)

    def frame(self, i=0):
        i = self.wait_frame(max(0, int(i)))
        buf = np.frombuffer(self.proxy[i], np.uint8)
        return cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)

    def sample_indices(self):
        self.wait_frame(self.frames - 1, timeout=120)   # sampling spans the clip: wait for the proxy
        n = len(self.proxy)
        return sorted({int(v) for v in np.linspace(0, n - 1, min(SAMPLES, n))})

    def tone(self, p):
        if not p["auto_levels"]:
            return None
        key = (p["amount"], p["detail"], p["invert"], p["subject"], p["fit"], p["zoom"], p["pan_x"], p["pan_y"])
        with self.lock:
            if key in self.levels:
                return self.levels[key]
        ts, ss = [], []
        q = dict(p)
        for i in self.sample_indices():
            rgb = self.frame(i)
            m = self.mask(i, p["subject"], rgb)
            _, (t, s) = engine.darkness(rgb, m, q, rect=self.rect_px(p, rgb.shape[1], rgb.shape[0]))
            ts.append(t)
            ss.append(s)
        lv = (float(np.median(ts)), float(np.median(ss)))
        with self.lock:
            self.levels[key] = lv
        return lv

    def close(self):
        self._stop = True
