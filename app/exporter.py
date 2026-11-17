"""Export jobs: PNG stills and MP4 (photo loops or videos with their audio), rendered on worker threads."""
import os
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import cv2

from . import engine, media

WORKERS = max(2, min(4, (os.cpu_count() or 4) // 4))


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, kind, out_path):
        self.id = uuid.uuid4().hex[:10]
        self.kind, self.path = kind, out_path
        self.state, self.error = "running", None
        self.done, self.total = 0, 1
        self.started, self.finished = time.time(), None
        self.cancel = False

    def info(self):
        el = (self.finished or time.time()) - self.started
        frac = self.done / max(1, self.total)
        eta = (el / frac - el) if 0 < frac < 1 else 0.0
        return {"id": self.id, "kind": self.kind, "state": self.state, "error": self.error, "done": self.done,
                "total": self.total, "progress": round(frac, 4), "elapsed": round(el, 1), "eta": round(eta, 1),
                "path": self.path, "name": os.path.basename(self.path)}


def save_png(rgb, path):
    ok, buf = cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_PNG_COMPRESSION, 3])
    if not ok:
        raise RuntimeError("PNG encoding failed")
    with open(path, "wb") as f:   # cv2.imwrite can't do non-ASCII Windows paths
        f.write(buf.tobytes())


def run_png(job, src, p, frame):
    job.total = 1
    sf = engine.source_frame(p, frame) if src.kind == "video" else 0
    D = src.darkness(min(sf, src.frames - 1), p)
    save_png(engine.render(D, p, frame), job.path)
    job.done = 1


def _frames_out(job, pages, writer, n_frames, pool, make_page):
    """Write n_frames, rendering each page once (frames in a hold repeat their page)."""
    pending = deque()
    futures = {}
    for f in range(n_frames):
        if job.cancel:
            raise Cancelled()
        start = pages(f)
        if start not in futures:
            futures[start] = pool.submit(make_page, f)
        pending.append(start)
        while len(pending) > 24 or (pending and f == n_frames - 1):
            head = pending.popleft()
            writer.write(futures[head].result())
            job.done += 1
            if head not in pending:
                futures.pop(head, None)
    while pending:
        head = pending.popleft()
        writer.write(futures[head].result())
        job.done += 1


def run_mp4_image(job, src, p):
    fps = p["fps"]
    n = max(1, int(round(p["duration"] * fps)))
    job.total = n
    D = src.darkness(0, p)
    W, H, *_ = engine.canvas_geometry(p)
    writer = media.Mp4Writer(job.path, W, H, fps)
    try:
        with ThreadPoolExecutor(WORKERS) as pool:
            _frames_out(job, lambda f: engine.page_index(p, f)[1], writer, n, pool,
                        lambda f: engine.render(D, p, f))
        writer.close()
    except BaseException:
        writer.abort()
        raise


def run_mp4_video(job, src, p):
    src.wait_frame(src.frames - 1, timeout=300)     # proxy done: exact frame count, framing and levels
    n = src.frames
    job.total = n
    src.rect(p)
    src.tone(p)
    w, h = media.even_fit(src.src_w, src.src_h, 1280)
    reader = media.FrameReader(src.path, w, h)
    W, H, *_ = engine.canvas_geometry(p)
    writer = media.Mp4Writer(job.path, W, H, src.fps, audio_from=src.path if src.has_audio else None)
    frames = {}
    lock = threading.Lock()
    state = {"next": 0, "last": None}

    def source_upto(i):
        """Read the source sequentially up to frame i (reader is single-threaded; callers serialise)."""
        with lock:
            while state["next"] <= i:
                rgb = reader.read()
                if rgb is None:
                    break
                state["last"] = rgb
                if engine.source_frame(p, state["next"]) == state["next"]:
                    frames[state["next"]] = rgb
                state["next"] += 1
            # a decoder that comes up a frame short just repeats its last frame
            return frames.pop(i, None) if i in frames else state["last"]

    def make_page(f):
        sf = engine.source_frame(p, f)
        rgb = source_upto(sf)
        if rgb is None:
            raise RuntimeError(f"Video ended early at frame {sf}")
        return engine.render(src.darkness(sf, p, rgb), p, f)

    try:
        with ThreadPoolExecutor(WORKERS) as pool:
            # page start for stop-motion; every frame otherwise
            _frames_out(job, lambda f: engine.source_frame(p, f) if p["stop_motion"] else f, writer, n, pool,
                        make_page)
        writer.close()
    except BaseException:
        writer.abort()
        raise
    finally:
        reader.close()


def start(kind, src, p, out_path, frame=0, on_done=None):
    job = Job(kind, out_path)

    def run():
        media.no_throttle()
        try:
            if kind == "png":
                run_png(job, src, p, frame)
            elif src.kind == "image":
                run_mp4_image(job, src, p)
            else:
                run_mp4_video(job, src, p)
            job.state = "done"
        except Cancelled:
            job.state = "cancelled"
        except Exception as e:
            job.state, job.error = "error", f"{type(e).__name__}: {e}"
        finally:
            job.finished = time.time()
            if on_done:
                on_done(job)

    threading.Thread(target=run, daemon=True).start()
    return job
