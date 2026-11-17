"""Media helpers: ffmpeg/ffprobe discovery, probing, frame readers and the MP4 writer."""
import json
import os
import shutil
import subprocess
import threading

import numpy as np

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".heic", ".avif"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".wmv", ".mpg", ".mpeg", ".flv", ".3gp", ".ts"}
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def find_tool(name):
    p = shutil.which(name)
    if p:
        return p
    local = os.environ.get("LOCALAPPDATA", "")
    root = os.path.join(local, "Microsoft", "WinGet", "Packages")
    if os.path.isdir(root):
        for d in os.listdir(root):
            if "ffmpeg" in d.lower():
                for dirpath, _, files in os.walk(os.path.join(root, d)):
                    if name + ".exe" in files:
                        return os.path.join(dirpath, name + ".exe")
    return name


FFMPEG, FFPROBE = find_tool("ffmpeg"), find_tool("ffprobe")


def no_throttle(pid=None):
    """Opt a process (default: this one) out of Windows power throttling (EcoQoS).

    Windows slows busy processes that have no window of their own (pythonw, ffmpeg pipes) 4-10x after a few
    seconds. Same fix as Apps\\blob-scope\\media.py. Returns True when applied.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes as wt

        class _PPT(ctypes.Structure):
            _fields_ = [("Version", wt.ULONG), ("ControlMask", wt.ULONG), ("StateMask", wt.ULONG)]

        k = ctypes.windll.kernel32
        k.GetCurrentProcess.restype = wt.HANDLE
        k.OpenProcess.restype = wt.HANDLE
        k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        k.SetProcessInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
        k.SetProcessInformation.restype = wt.BOOL
        k.CloseHandle.argtypes = [wt.HANDLE]
        h = k.GetCurrentProcess() if pid is None else k.OpenProcess(0x0200 | 0x1000, False, pid)
        if not h:
            return False
        st = _PPT(1, 0x1, 0x0)  # execution-speed throttling: controlled by us, and off
        ok = bool(k.SetProcessInformation(h, 4, ctypes.byref(st), ctypes.sizeof(st)))  # 4 = ProcessPowerThrottling
        if pid is not None:
            k.CloseHandle(h)
        return ok
    except Exception:
        return False


def kind(path):
    ext = os.path.splitext(path)[1].lower()
    return "image" if ext in IMAGE_EXT else "video" if ext in VIDEO_EXT else None


def _run(args, **kw):
    return subprocess.run(args, capture_output=True, creationflags=NO_WINDOW, **kw)


def probe(path):
    """Video facts: display width/height (rotation applied), fps, frame count, duration, has_audio."""
    r = _run([FFPROBE, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path], timeout=60)
    if r.returncode != 0:
        raise ValueError("Can't read this file: " + r.stderr.decode(errors="replace").strip()[-300:])
    info = json.loads(r.stdout or b"{}")
    vs = [s for s in info.get("streams", []) if s.get("codec_type") == "video"
          and not (s.get("disposition") or {}).get("attached_pic")]
    if not vs:
        raise ValueError("No video stream in this file.")
    v = vs[0]
    w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    rot = 0
    for sd in v.get("side_data_list") or []:
        if "rotation" in sd:
            rot = int(float(sd["rotation"]))
    rot = int(float((v.get("tags") or {}).get("rotate", rot)))
    if abs(rot) % 180 == 90:
        w, h = h, w

    def rate(s):
        try:
            a, b = s.split("/")
            return float(a) / float(b) if float(b) else 0.0
        except Exception:
            return 0.0
    fps = rate(v.get("avg_frame_rate") or "") or rate(v.get("r_frame_rate") or "") or 30.0
    if fps > 120 or fps < 1:
        fps = rate(v.get("r_frame_rate") or "") or 30.0
    dur = float(v.get("duration") or (info.get("format") or {}).get("duration") or 0.0)
    n = int(v.get("nb_frames") or 0) or int(round(dur * fps))
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    return {"width": w, "height": h, "fps": round(fps, 4), "frames": max(1, n), "duration": dur,
            "has_audio": has_audio}


def even_fit(w, h, max_side):
    f = min(1.0, max_side / max(w, h))
    return max(2, int(w * f) // 2 * 2), max(2, int(h * f) // 2 * 2)


class FrameReader:
    """Sequential RGB frames from a video via an ffmpeg pipe, scaled to (w, h)."""

    def __init__(self, path, w, h, fps=None, start=0.0):
        args = [FFMPEG, "-v", "error", "-nostdin"]
        if start > 0:
            args += ["-ss", f"{start:.3f}"]
        args += ["-i", path, "-an", "-sn"]
        vf = [f"scale={w}:{h}:flags=area"]
        if fps:
            vf.insert(0, f"fps={fps}")
        args += ["-vf", ",".join(vf), "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
        self.w, self.h = w, h
        self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     creationflags=NO_WINDOW, bufsize=w * h * 3 * 2)
        no_throttle(self.proc.pid)

    def read(self):
        n = self.w * self.h * 3
        buf = self.proc.stdout.read(n)
        if not buf or len(buf) < n:
            return None
        return np.frombuffer(buf, np.uint8).reshape(self.h, self.w, 3)

    def close(self):
        try:
            self.proc.stdout.close()
        except Exception:
            pass
        try:
            self.proc.kill()
        except Exception:
            pass
        self.proc.wait()

    def __iter__(self):
        while True:
            f = self.read()
            if f is None:
                return
            yield f


class Mp4Writer:
    """RGB frames -> H.264 MP4 (bt709, yuv420p), with the audio of `audio_from` muxed in when given."""

    def __init__(self, path, w, h, fps, audio_from=None, crf=16):
        self.path = path
        args = [FFMPEG, "-v", "error", "-y", "-nostdin",
                "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", f"{fps}", "-i", "pipe:0"]
        if audio_from:
            args += ["-i", audio_from, "-map", "0:v:0", "-map", "1:a:0?", "-c:a", "aac", "-b:a", "192k",
                     "-shortest"]
        args += ["-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
                 "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-tune", "grain",
                 "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
                 "-movflags", "+faststart", path]
        self.err = []
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
        no_throttle(self.proc.pid)
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        try:
            for line in self.proc.stderr:
                self.err.append(line.decode(errors="replace"))
        finally:
            self.proc.stderr.close()

    def write(self, rgb):
        self.proc.stdin.write(np.ascontiguousarray(rgb).tobytes())

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        rc = self.proc.wait()
        if rc != 0:
            raise RuntimeError("ffmpeg failed: " + "".join(self.err)[-400:])

    def abort(self):
        try:
            self.proc.kill()
        except Exception:
            pass
        self.proc.wait()
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            os.remove(self.path)
        except OSError:
            pass
