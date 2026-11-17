"""Shared test helpers: the test folder (tests\\_out), test inputs, a server on a free port, ffprobe."""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app import media  # noqa: E402

# The review folder: tests\_out in the app folder, or the folder in the DECLASSIFIED_TESTS environment variable.
TEST_DIR = os.environ.get("DECLASSIFIED_TESTS") or os.path.join(ROOT, "tests", "_out")
DIRS = {
    "inputs": "00 Inputs",
    "photos": "01 Photos",
    "videos": "02 Videos",
    "styles": "03 Paper styles",
    "presets": "04 Presets",
    "formats": "05 Formats",
    "ui": "06 App window",
}
INFO_FILE = os.path.join(TEST_DIR, "_data", "info.json")
SAMPLES = os.path.join(ROOT, "samples")   # free test inputs, see samples\CREDITS.md
DANCER = os.path.join(SAMPLES, "dancer.mp4")

# name in the test folder -> source
PHOTOS = {
    "Hanneman - Portrait of a Woman (Met, CC0).jpg": os.path.join(SAMPLES, "hanneman-portrait.jpg"),
    "Velazquez - Portrait of a Man (Met, CC0).jpg": os.path.join(SAMPLES, "velazquez-portrait.jpg"),
    "Antonello da Messina - Portrait of a Young Man (Met, CC0).jpg": os.path.join(SAMPLES, "antonello-portrait.jpg"),
}
# name in the test folder -> (source, start s, length s, frame rate or None to keep it, add a test tone)
VIDEOS = {
    "dancer (3.4 s, 30 fps, test tone).mp4": (DANCER, 0.0, 3.4, 30, True),
    "dancer (6 s, 25 fps, test tone).mp4": (DANCER, 2.0, 6.0, 25, True),
    "dancer (6 s, 29.97 fps, no audio).mp4": (DANCER, 3.0, 6.0, None, False),
    "dancer (4 s, 24 fps, no audio).mp4": (DANCER, 5.0, 4.0, 24, False),
}


def d(key, *parts):
    p = os.path.join(TEST_DIR, DIRS[key], *parts)
    os.makedirs(os.path.dirname(p) if parts else p, exist_ok=True)
    return p


def prepare_inputs():
    """Copy the photos and cut the test clips into 00 Inputs (once).

    The sample clip has no sound, so the clips 'with a test tone' get a generated 440 Hz tone: the sound path is
    tested too."""
    for name, src in PHOTOS.items():
        dst = d("inputs", name)
        if os.path.isfile(src) and not os.path.isfile(dst):
            shutil.copy2(src, dst)
    for name, (src, start, length, fps, tone) in VIDEOS.items():
        dst = d("inputs", name)
        if not os.path.isfile(src) or os.path.isfile(dst):
            continue
        cmd = [media.FFMPEG, "-v", "error", "-y", "-ss", str(start), "-t", str(length), "-i", src]
        if tone:
            cmd += ["-f", "lavfi", "-t", str(length), "-i", "sine=frequency=440:sample_rate=48000"]
        cmd += ["-map", "0:v:0"] + (["-map", "1:a:0", "-c:a", "aac", "-b:a", "160k"] if tone else ["-an"])
        if fps:
            cmd += ["-vf", f"fps={fps}"]
        cmd += ["-c:v", "libx264", "-crf", "17", "-preset", "fast", "-pix_fmt", "yuv420p", "-t", str(length), dst]
        subprocess.run(cmd, check=True, creationflags=media.NO_WINDOW)


def photos():
    return {n: d("inputs", n) for n in PHOTOS if os.path.isfile(d("inputs", n))}


def videos():
    return {n: d("inputs", n) for n in VIDEOS if os.path.isfile(d("inputs", n))}


# ------------------------------------------------------------------------------------------------- info for report
def info_load():
    try:
        with open(INFO_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def info_add(section, item):
    data = info_load()
    data.setdefault(section, []).append(item)
    os.makedirs(os.path.dirname(INFO_FILE), exist_ok=True)
    with open(INFO_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)


# ------------------------------------------------------------------------------------------------- server
class TestServer:
    def __init__(self):
        os.environ["DECLASSIFIED_OUT"] = os.path.join(TEST_DIR, "_data", "default exports")
        from app import server
        self.server = server
        sock = server.make_socket(0)
        self.port = sock.getsockname()[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=server.serve, kwargs={"sock": sock}, daemon=True).start()
        for _ in range(200):
            try:
                if self.get("/api/ping")[0] == 200:
                    break
            except Exception:
                time.sleep(0.05)

    def req(self, path, body=None, raw=None, headers=None, method=None, timeout=600):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        h = {"Content-Type": "application/json"} if body is not None else {}
        h.update(headers or {})
        r = urllib.request.Request(self.base + path, data=data, headers=h,
                                   method=method or ("POST" if data is not None else "GET"))
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()

    def get(self, path):
        return self.req(path)

    def post(self, path, body=None):
        return self.req(path, body=body if body is not None else {}, method="POST")

    def json(self, path, body=None):
        st, h, b = self.post(path, body) if body is not None else self.get(path)
        return st, json.loads(b or b"null")

    def open_file(self, path, upload=False):
        if upload:
            with open(path, "rb") as f:
                st, h, b = self.req("/api/open", raw=f.read(),
                                    headers={"X-Filename": urllib.request.quote(os.path.basename(path))})
        else:
            st, h, b = self.post("/api/open_path", {"path": path})
        assert st == 200, b
        info = json.loads(b)
        while info["kind"] == "video" and info["ready"] < 1:
            time.sleep(0.2)
            info = self.json("/api/source")[1]["source"]
        return info

    def export(self, params, fmt, path, frame=0, timeout=900):
        st, job = self.json("/api/export", {"params": params, "format": fmt, "path": path, "frame": frame})
        assert st == 200, job
        end = time.time() + timeout
        while time.time() < end:
            st, job = self.json(f"/api/jobs/{job['id']}")
            if job["state"] != "running":
                return job
            time.sleep(0.25)
        raise TimeoutError(path)


def ffprobe(path):
    r = subprocess.run([media.FFPROBE, "-v", "error", "-print_format", "json", "-show_streams", "-show_format",
                        "-count_frames", path], capture_output=True, creationflags=media.NO_WINDOW)
    info = json.loads(r.stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = [s for s in info["streams"] if s["codec_type"] == "audio"]
    num, den = v["r_frame_rate"].split("/")
    return {"codec": v["codec_name"], "width": v["width"], "height": v["height"], "pix_fmt": v.get("pix_fmt"),
            "fps": float(num) / float(den), "frames": int(v.get("nb_read_frames") or v.get("nb_frames") or 0),
            "duration": float(info["format"]["duration"]), "audio": bool(a),
            "color": v.get("color_space"), "size_mb": os.path.getsize(path) / 1e6}


def read_frames(path, w=270, h=480):
    """All frames of a video as small grey arrays (for checking the stop-motion rhythm)."""
    import numpy as np
    r = subprocess.run([media.FFMPEG, "-v", "error", "-i", path, "-vf", f"scale={w}:{h}", "-f", "rawvideo",
                        "-pix_fmt", "gray", "pipe:1"], capture_output=True, creationflags=media.NO_WINDOW)
    return np.frombuffer(r.stdout, np.uint8).reshape(-1, h, w)
