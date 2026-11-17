"""Declassified local server: FastAPI on 127.0.0.1:48751. Serves the UI and the render API.

  GET  /api/ping                      -> {"app": "declassified"}
  GET  /api/config                    -> defaults, limits, layouts, presets, output folder, GPU status
  POST /api/open        (raw body, X-Filename header)   copy an upload in and open it
  POST /api/open_path   {"path"}      open a local file in place
  GET  /api/source                    -> the open file (with video proxy progress)
  POST /api/preview     {"params", "frame", "scale"}  -> JPEG of that output frame
  POST /api/export      {"params", "format": png|mp4, "frame"}  -> job
  GET  /api/jobs/{id}   POST /api/jobs/{id}/cancel
  POST /api/reveal      {"path"}      show a file in Explorer;  POST /api/open_folder
  POST /api/heartbeat                 the window is alive (the launcher's server quits ~45 s after it closes)
"""
import datetime
import os
import socket
import subprocess
import threading
import time
import urllib.parse

import cv2
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import engine, exporter, masks, media, sources

VERSION = "1.0.0"
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.path.join(APP_DIR, "ui")
PORT = int(os.environ.get("DECLASSIFIED_PORT") or 48751)
DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", APP_DIR), "Declassified")
MEDIA_DIR = os.path.join(DATA_DIR, "media")
APP_NAME = "Declassified"

PRESETS = [
    {"id": "reel", "name": "Classic", "hint": "The default look", "params": {}},
    {"id": "heavy", "name": "Heavy redaction", "hint": "More black, chunkier blocks",
     "params": {"amount": 0.6, "min_bar": 0.5, "merge": 0.9, "speckle": 0.8, "dots": 0.6}},
    {"id": "light", "name": "Light touch", "hint": "Only the darkest parts",
     "params": {"amount": 0.3, "speckle": 0.45, "dots": 0.3, "merge": 0.5}},
    {"id": "memo", "name": "Typewriter files", "hint": "Only typed memos, more stamps",
     "params": {"styles": ["memo"], "stamps": 0.65}},
    {"id": "court", "name": "Court sealed", "hint": "Filings and sealed sheets",
     "params": {"styles": ["court", "sealed", "privacy"], "stamps": 0.15}},
    {"id": "clean", "name": "Clean bars", "hint": "No toner, crisp redaction",
     "params": {"speckle": 0.0, "dots": 0.0, "ghost": 0.0, "scan": 0.15, "ragged": 0.2}},
    {"id": "slow", "name": "Slow boil", "hint": "Paper changes every 4 frames",
     "params": {"hold": 4, "hold_jitter": 0.1}},
]

STATE = {"source": None, "jobs": {}, "last_beat": 0.0, "beats": 0}
_open_lock = threading.Lock()


def output_dir():
    """Videos\\Declassified in the user folder, or the folder set in the DECLASSIFIED_OUT environment variable.

    Falls back to Pictures\\Declassified, then to the app's data folder, when a folder can't be written to.
    """
    home = os.path.expanduser("~")
    if os.environ.get("DECLASSIFIED_OUT"):        # another folder (the tests use this too)
        os.makedirs(os.environ["DECLASSIFIED_OUT"], exist_ok=True)
        return os.environ["DECLASSIFIED_OUT"]
    for d in (os.path.join(home, "Videos", APP_NAME), os.path.join(home, "Pictures", APP_NAME),
              os.path.join(DATA_DIR, "exports")):
        try:
            os.makedirs(d, exist_ok=True)
            if os.access(d, os.W_OK):
                return d
        except OSError:
            continue
    return DATA_DIR


app = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def no_cache(request, call_next):
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/ui/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/")
def index():
    return FileResponse(os.path.join(UI_DIR, "index.html"))


app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")


@app.get("/api/ping")
def ping():
    return {"app": "declassified", "version": VERSION}


@app.get("/api/config")
def config():
    layouts = {}
    for name in engine.LAYOUTS:
        W, H, pw, ph, _ = engine.canvas_geometry(engine.normalize({"layout": name}))
        layouts[name] = {"width": W, "height": H}
    return {"defaults": engine.normalize({}), "limits": engine.LIMITS, "layouts": layouts,
            "styles": list(engine.STYLE_WEIGHTS), "presets": PRESETS, "output_dir": output_dir(),
            "gpu": masks.status(), "version": VERSION}


def _set_source(src):
    old = STATE["source"]
    STATE["source"] = src
    if old is not None and old is not src:
        old.close()
    return src.info()


def open_source(path, display_name=None):
    k = media.kind(path)
    if k is None:
        raise HTTPException(400, "Unsupported file type. Use a photo (jpg, png, webp...) or a video (mp4, mov...).")
    try:
        src = sources.ImageSource(path, display_name) if k == "image" else sources.VideoSource(path, display_name)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Couldn't open {display_name or os.path.basename(path)}: {e}")
    with _open_lock:
        return _set_source(src)


@app.post("/api/open")
async def api_open(request: Request):
    name = urllib.parse.unquote(request.headers.get("x-filename") or "upload.bin")
    name = os.path.basename(name.replace("\\", "/")) or "upload.bin"
    ext = os.path.splitext(name)[1].lower()
    if media.kind(name) is None:
        raise HTTPException(400, "Unsupported file type. Use a photo (jpg, png, webp...) or a video (mp4, mov...).")
    os.makedirs(MEDIA_DIR, exist_ok=True)
    dst = os.path.join(MEDIA_DIR, f"{int(time.time())}_{os.getpid()}_{threading.get_ident() % 10000}{ext}")
    size = 0
    with open(dst, "wb") as f:
        async for chunk in request.stream():
            f.write(chunk)
            size += len(chunk)
    if size == 0:
        os.remove(dst)
        raise HTTPException(400, "The file was empty.")
    return await run_in_threadpool(open_source, dst, name)


@app.post("/api/open_path")
async def api_open_path(request: Request):
    body = await request.json()
    path = str(body.get("path") or "")
    if not os.path.isfile(path):
        raise HTTPException(404, f"File not found: {path}")
    return await run_in_threadpool(open_source, path)


@app.get("/api/source")
def api_source():
    src = STATE["source"]
    return {"source": src.info() if src else None}


@app.get("/api/source_frame/{i}")
def api_source_frame(i: int):
    """The untouched source frame (for the hold-to-compare button)."""
    src = _need_source()
    rgb = src.frame(max(0, min(i, src.frames - 1)))
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 88])
    return Response(buf.tobytes(), media_type="image/jpeg")


def _need_source():
    src = STATE["source"]
    if src is None:
        raise HTTPException(409, "Open a photo or a video first.")
    return src


def total_frames(src, p):
    return src.frames if src.kind == "video" else max(1, int(round(p["duration"] * p["fps"])))


@app.post("/api/preview")
def api_preview(body: dict):
    src = _need_source()
    p = engine.normalize(body.get("params") or {})
    n = total_frames(src, p)
    frame = int(body.get("frame") or 0) % n
    scale = min(1.0, max(0.15, float(body.get("scale") or 0.5)))
    t0 = time.perf_counter()
    sf = engine.source_frame(p, frame) if src.kind == "video" else 0
    D = src.darkness(sf, p)
    out = engine.render(D, p, frame, scale=scale)
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(out, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 88])
    page, start = engine.page_index(p, frame)
    spec = engine.PageSpec(p, page)
    nxt = next((f for f in range(frame + 1, frame + 64) if engine.page_index(p, f)[0] != page), frame + 1) % n
    prev = engine.page_index(p, start - 1)[1] if start > 0 else engine.page_index(p, n - 1)[1]
    ms = (time.perf_counter() - t0) * 1000
    return Response(buf.tobytes(), media_type="image/jpeg", headers={
        "X-Render-Ms": f"{ms:.0f}", "X-Page": str(page), "X-Page-Start": str(start), "X-Style": spec.style,
        "X-Next-Start": str(nxt), "X-Prev-Start": str(prev),
        "X-Frames": str(n), "X-Width": str(out.shape[1]), "X-Height": str(out.shape[0]),
        "Access-Control-Expose-Headers": "*"})


def _out_name(src, ext):
    stem = os.path.splitext(src.name)[0][:60].strip() or "declassified"
    stem = "".join(c if c.isalnum() or c in " -_()" else "_" for c in stem).strip()
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return os.path.join(output_dir(), f"{stem} - declassified {stamp}.{ext}")


@app.post("/api/export")
def api_export(body: dict):
    src = _need_source()
    p = engine.normalize(body.get("params") or {})
    fmt = body.get("format") if body.get("format") in ("png", "mp4") else "mp4"
    frame = int(body.get("frame") or 0) % total_frames(src, p)
    path = body.get("path") or _out_name(src, fmt)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    job = exporter.start(fmt, src, p, path, frame=frame)
    STATE["jobs"][job.id] = job
    return job.info()


@app.get("/api/jobs/{jid}")
def api_job(jid: str):
    job = STATE["jobs"].get(jid)
    if not job:
        raise HTTPException(404, "No such export")
    return job.info()


@app.post("/api/jobs/{jid}/cancel")
def api_cancel(jid: str):
    job = STATE["jobs"].get(jid)
    if not job:
        raise HTTPException(404, "No such export")
    job.cancel = True
    return job.info()


@app.post("/api/reveal")
def api_reveal(body: dict):
    path = str(body.get("path") or "")
    if os.path.isfile(path):
        subprocess.Popen(["explorer.exe", "/select,", os.path.normpath(path)])
        return {"ok": True}
    raise HTTPException(404, "File not found")


@app.post("/api/open_folder")
def api_open_folder():
    d = output_dir()
    subprocess.Popen(["explorer.exe", os.path.normpath(d)])
    return {"ok": True, "path": d}


@app.post("/api/heartbeat")
def api_heartbeat():
    STATE["last_beat"] = time.time()
    STATE["beats"] += 1
    return {"ok": True}


@app.exception_handler(Exception)
async def on_error(request, exc):
    return JSONResponse({"detail": f"{type(exc).__name__}: {exc}"}, status_code=500)


# --------------------------------------------------------------------------------------------- serving
def make_socket(port=PORT):
    """Bind exclusively: Windows otherwise lets a second program bind the same port and split the traffic."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(128)
    return sock


def cleanup_media(max_age_days=3):
    """Uploaded copies are working files: drop the ones older than a few days."""
    if not os.path.isdir(MEDIA_DIR):
        return
    cutoff = time.time() - max_age_days * 86400
    for f in os.listdir(MEDIA_DIR):
        fp = os.path.join(MEDIA_DIR, f)
        try:
            if os.path.getmtime(fp) < cutoff:
                os.remove(fp)
        except OSError:
            pass


def serve(port=PORT, auto_exit=False, sock=None, open_path=None):
    import uvicorn
    media.no_throttle()
    os.makedirs(DATA_DIR, exist_ok=True)
    cleanup_media()
    sock = sock or make_socket(port)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False, timeout_keep_alive=30))
    threading.Thread(target=masks.warm, daemon=True).start()   # load the cut-out model before the first preview
    if open_path:
        try:
            open_source(open_path)
        except Exception as e:
            print("could not open", open_path, e)
    if auto_exit:
        def watchdog():
            while not server.should_exit:
                time.sleep(5)
                busy = any(j.state == "running" for j in STATE["jobs"].values())
                if STATE["beats"] and not busy and time.time() - STATE["last_beat"] > 45:
                    server.should_exit = True
        threading.Thread(target=watchdog, daemon=True).start()
    server.run(sockets=[sock])


if __name__ == "__main__":
    serve()
