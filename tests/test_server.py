"""End-to-end tests through the HTTP API. They also produce the review files in the test folder (tests\\_out)."""
import os
import struct
import sys
import time
import unittest

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402
from app import engine  # noqa: E402

S = None


def setUpModule():
    global S
    C.prepare_inputs()
    S = C.TestServer()


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", head[16:24])


def stem(name):
    return os.path.splitext(name)[0]


class T1Basics(unittest.TestCase):
    def test_ping_index_static(self):
        st, h, b = S.get("/api/ping")
        self.assertEqual((st, b"declassified" in b), (200, True))
        st, h, b = S.get("/")
        self.assertEqual(st, 200)
        self.assertIn(b"Declassified", b)
        for f in ("app.js", "style.css", "icon.png"):
            self.assertEqual(S.get("/ui/" + f)[0], 200, f)

    def test_config(self):
        st, cfg = S.json("/api/config")
        self.assertEqual(st, 200)
        self.assertEqual(cfg["layouts"]["reel"], {"width": 1080, "height": 1920})
        self.assertEqual(len(cfg["presets"]), 7)
        self.assertEqual(cfg["defaults"]["hold"], 2)

    def test_errors_are_clear(self):
        S.server.STATE["source"] = None
        st, b = S.json("/api/preview", {"params": {}})
        self.assertEqual(st, 409)
        self.assertIn("Open a photo", b["detail"])
        st, h, b = S.req("/api/open", raw=b"hello", headers={"X-Filename": "notes.txt"})
        self.assertEqual(st, 400)
        self.assertEqual(S.post("/api/open_path", {"path": r"C:\nope\missing.mp4"})[0], 404)
        self.assertEqual(S.get("/api/jobs/nope")[0], 404)
        broken = os.path.join(C.TEST_DIR, "_data", "broken.mp4")
        with open(broken, "wb") as f:
            f.write(b"not really a video")
        st, h, b = S.post("/api/open_path", {"path": broken})
        self.assertEqual(st, 400, b)


class T2Photos(unittest.TestCase):
    def test_photos(self):
        for name, path in C.photos().items():
            with self.subTest(photo=name):
                info = S.open_file(path, upload=True)
                self.assertEqual(info["kind"], "image")
                t = time.perf_counter()
                st, h, b = S.post("/api/preview", {"params": {}, "frame": 0, "scale": 0.5})
                ms = (time.perf_counter() - t) * 1000
                self.assertEqual(st, 200)
                img = cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_COLOR)
                self.assertEqual(img.shape[:2], (960, 540))
                self.assertEqual(int(h["x-frames"]), 120)          # 5 s at 24 fps
                png = C.d("photos", stem(name) + " - still.png")
                job = S.export({}, "png", png, frame=0)
                self.assertEqual(job["state"], "done", job)
                self.assertEqual(png_size(png), (1080, 1920))
                mp4 = C.d("photos", stem(name) + " - 4 s loop.mp4")
                job = S.export({"duration": 4}, "mp4", mp4)
                self.assertEqual(job["state"], "done", job)
                pr = C.ffprobe(mp4)
                self.assertEqual((pr["codec"], pr["width"], pr["height"], pr["pix_fmt"]),
                                 ("h264", 1080, 1920, "yuv420p"))
                self.assertEqual(pr["frames"], 96)
                self.assertAlmostEqual(pr["fps"], 24.0)
                self.assertFalse(pr["audio"])
                C.info_add("photos", {"name": name, "input": os.path.relpath(path, C.TEST_DIR),
                                      "png": os.path.relpath(png, C.TEST_DIR), "mp4": os.path.relpath(mp4, C.TEST_DIR),
                                      "preview_ms": round(ms), "export_s": job["elapsed"], "probe": pr})


class T3Videos(unittest.TestCase):
    def test_videos(self):
        for name, path in C.videos().items():
            with self.subTest(video=name):
                src = C.ffprobe(path)
                info = S.open_file(path)
                self.assertEqual(info["kind"], "video")
                self.assertEqual(info["has_audio"], src["audio"])
                mid = info["frames"] // 2
                st, h, b = S.post("/api/preview", {"params": {}, "frame": mid, "scale": 0.5})
                self.assertEqual(st, 200)
                png = C.d("videos", stem(name) + " - still at middle.png")
                self.assertEqual(S.export({}, "png", png, frame=mid)["state"], "done")
                mp4 = C.d("videos", stem(name) + " - declassified.mp4")
                job = S.export({}, "mp4", mp4)
                self.assertEqual(job["state"], "done", job)
                pr = C.ffprobe(mp4)
                self.assertEqual((pr["codec"], pr["width"], pr["height"]), ("h264", 1080, 1920))
                self.assertAlmostEqual(pr["fps"], src["fps"], delta=0.01)
                self.assertLessEqual(abs(pr["frames"] - src["frames"]), 1, (pr["frames"], src["frames"]))
                self.assertEqual(pr["audio"], src["audio"])
                self.assertAlmostEqual(pr["duration"], src["duration"], delta=0.15)
                # Stop-motion rhythm: frames inside a page hold are the same picture, a new page is new paper.
                p = engine.normalize({})
                fr = C.read_frames(mp4).astype(np.int16)
                same, new = [], []
                for f in range(1, len(fr)):
                    diff = float(np.abs(fr[f] - fr[f - 1]).mean())
                    (new if engine.page_index(p, f)[1] == f else same).append(diff)
                self.assertLess(np.mean(same), 1.0, "frames inside a hold should repeat")
                self.assertGreater(np.min(new), 2.0, "every new page should change the paper")
                C.info_add("videos", {"name": name, "input": os.path.relpath(path, C.TEST_DIR),
                                      "mp4": os.path.relpath(mp4, C.TEST_DIR), "png": os.path.relpath(png, C.TEST_DIR),
                                      "export_s": job["elapsed"], "probe": pr, "source": src,
                                      "hold_diff": round(float(np.mean(same)), 3),
                                      "page_diff": round(float(np.mean(new)), 2),
                                      "pages": len(new) + 1})


class T4Looks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.han = C.photos()["Hanneman - Portrait of a Woman (Met, CC0).jpg"]

    def test_paper_styles(self):
        S.open_file(self.han)
        names = {"court": "Court filing", "memo": "Typed memo", "sealed": "Sealed sheet", "privacy": "Privacy sheet"}
        for st, label in names.items():
            path = C.d("styles", f"{label}.png")
            job = S.export({"styles": [st], "stamps": 0.7}, "png", path, frame=4)
            self.assertEqual(job["state"], "done")
            C.info_add("styles", {"name": label, "png": os.path.relpath(path, C.TEST_DIR)})

    def test_presets(self):
        S.open_file(self.han)
        st, cfg = S.json("/api/config")
        for pr in cfg["presets"]:
            path = C.d("presets", f"{pr['name']}.png")
            job = S.export(pr["params"], "png", path, frame=2)
            self.assertEqual(job["state"], "done", pr)
            C.info_add("presets", {"name": pr["name"], "hint": pr["hint"], "png": os.path.relpath(path, C.TEST_DIR)})

    def test_formats(self):
        S.open_file(self.han)
        labels = {"reel": "Reel 9x16", "portrait": "Post 4x5", "square": "Square 1x1", "landscape": "Landscape 16x9",
                  "page": "Page only"}
        st, cfg = S.json("/api/config")
        for lay, label in labels.items():
            path = C.d("formats", f"{label}.png")
            job = S.export({"layout": lay}, "png", path, frame=0)
            self.assertEqual(job["state"], "done")
            want = cfg["layouts"][lay]
            self.assertEqual(png_size(path), (want["width"], want["height"]))
            C.info_add("formats", {"name": label, "png": os.path.relpath(path, C.TEST_DIR),
                                   "size": f"{want['width']}x{want['height']}"})
        path = C.d("formats", "Reel 9x16 at 2x (2160x3840).png")
        self.assertEqual(S.export({"quality": 2}, "png", path)["state"], "done")
        self.assertEqual(png_size(path), (2160, 3840))
        C.info_add("formats", {"name": "Reel at 2x", "png": os.path.relpath(path, C.TEST_DIR), "size": "2160x3840"})

    def test_framing_modes(self):
        S.open_file(C.photos()["Antonello da Messina - Portrait of a Young Man (Met, CC0).jpg"])
        for fit in ("face", "subject", "cover", "contain"):
            path = C.d("formats", f"Fit {fit} (Antonello portrait).png")
            self.assertEqual(S.export({"fit": fit}, "png", path)["state"], "done")
            C.info_add("fits", {"name": fit, "png": os.path.relpath(path, C.TEST_DIR)})

    def test_cancel(self):
        S.open_file(self.han)
        path = os.path.join(C.TEST_DIR, "_data", "cancelled.mp4")
        st, job = S.json("/api/export", {"params": {"duration": 60}, "format": "mp4", "path": path})
        time.sleep(1.0)
        S.post(f"/api/jobs/{job['id']}/cancel")
        for _ in range(100):
            st, job = S.json(f"/api/jobs/{job['id']}")
            if job["state"] != "running":
                break
            time.sleep(0.1)
        self.assertEqual(job["state"], "cancelled")
        self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
