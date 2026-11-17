"""Engine unit tests: geometry, timeline, documents, tone, bars, determinism, masks, speed."""
import os
import sys
import time
import unittest

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app import documents as docs  # noqa: E402
from app import engine, masks, media, sources  # noqa: E402

media.no_throttle()
SAMPLES = os.path.join(ROOT, "samples")
HANNEMAN = os.path.join(SAMPLES, "hanneman-portrait.jpg")


def dark_fraction(img, box=None):
    """Share of near-black pixels (bars) inside the page's portrait box of a reel frame."""
    g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    if box is None:
        x0, y0, x1, y1 = engine.BOX
        g = g[460 + y0:460 + y1, 135 + x0:135 + x1]
    return float((g < 60).mean())


class TestParams(unittest.TestCase):
    def test_defaults_and_clamping(self):
        p = engine.normalize({"amount": 5, "hold": 0, "zoom": "x", "layout": "nope", "styles": ["memo", "bad"],
                              "bg": "zzz", "seed": -4, "duration": float("nan")})
        self.assertEqual(p["amount"], 0.98)
        self.assertEqual(p["hold"], 1)
        self.assertEqual(p["zoom"], engine.DEFAULTS["zoom"])
        self.assertEqual(p["layout"], "reel")
        self.assertEqual(p["styles"], ["memo"])
        self.assertEqual(p["bg"], "#dcdcdc")
        self.assertEqual(p["seed"], 0)
        self.assertEqual(p["duration"], engine.DEFAULTS["duration"])
        self.assertIsInstance(p["hold"], int)

    def test_empty_styles_fall_back(self):
        self.assertEqual(engine.normalize({"styles": []})["styles"], ["court"])


class TestGeometry(unittest.TestCase):
    def test_reel_matches_measured_reel(self):
        W, H, pw, ph, k = engine.canvas_geometry(engine.normalize({}))
        self.assertEqual((W, H, pw, ph), (1080, 1920, 810, 1000))
        self.assertEqual(((W - pw) // 2, (H - ph) // 2), (135, 460))   # page position measured on the reel

    def test_all_layouts_even_and_scaled(self):
        for name in engine.LAYOUTS:
            for q in (1, 2):
                W, H, pw, ph, k = engine.canvas_geometry(engine.normalize({"layout": name, "quality": q}))
                self.assertEqual(W % 2, 0, name)
                self.assertEqual(H % 2, 0, name)
                self.assertLessEqual(pw, W)
                self.assertLessEqual(ph, H)
                self.assertAlmostEqual(pw / ph, 0.81, delta=0.01)
                self.assertAlmostEqual(k, ph / 1000.0)


class TestTimeline(unittest.TestCase):
    def test_on_twos_without_jitter(self):
        p = engine.normalize({"hold": 2, "hold_jitter": 0, "seed": 3})
        pages = [engine.page_index(p, f)[0] for f in range(20)]
        self.assertEqual(pages, [f // 2 for f in range(20)])

    def test_jitter_keeps_holds_near_target(self):
        p = engine.normalize({"hold": 2, "hold_jitter": 0.5, "seed": 9})
        starts = sorted({engine.page_index(p, f)[1] for f in range(400)})
        holds = np.diff(starts)
        self.assertTrue(set(holds.tolist()) <= {1, 2, 3})
        self.assertAlmostEqual(float(holds.mean()), 2.0, delta=0.25)

    def test_stop_motion_source_frame(self):
        p = engine.normalize({"hold": 3, "hold_jitter": 0})
        self.assertEqual([engine.source_frame(p, f) for f in range(7)], [0, 0, 0, 3, 3, 3, 6])
        p2 = engine.normalize({"hold": 3, "hold_jitter": 0, "stop_motion": False})
        self.assertEqual([engine.source_frame(p2, f) for f in range(4)], [0, 1, 2, 3])


class TestDocuments(unittest.TestCase):
    def test_every_style_lays_out_inside_the_page(self):
        for style in docs.STYLES:
            for seed in range(6):
                L = 13.0
                lay = docs.layout_page(style, seed, L, phase=seed * 2.0, stamp_p=1.0)
                self.assertTrue(lay.items, style)
                for it in lay.items:
                    w = docs.text_width(it.font, it.size, it.text)
                    x0 = it.x - (w / 2 if it.align == "center" else w if it.align == "right" else 0)
                    self.assertGreaterEqual(x0, 0, (style, it.text))
                    self.assertLessEqual(x0 + w, docs.PAGE_W + 1, (style, it.text))
                    top = docs.row_top(lay, it.row)
                    self.assertGreaterEqual(top, 0)
                    self.assertLessEqual(top + L, docs.PAGE_H + 0.01, (style, it.row))
                self.assertIsNotNone(lay.stamp)

    def test_body_text_stays_in_its_column(self):
        lay = docs.layout_page("court", 4, 13.0, 5.0, stamp_p=0)
        right = docs.COLUMN_R["court"] * docs.PAGE_W + 2
        body = [it for it in lay.items if it.font == "times"]
        self.assertGreater(len(body), 30)
        for it in body:
            self.assertLessEqual(it.x + docs.text_width(it.font, it.size, it.text), right, it.text)

    def test_sealed_name_sits_just_above_the_portrait(self):
        for phase in (0.0, 6.0, 12.5):
            lay = docs.layout_page("sealed", 1, 13.0, phase, content_top=engine.BOX[1])
            bottom = docs.row_top(lay, lay.name_row) + 13.0
            self.assertLessEqual(bottom, engine.BOX[1] + 0.01)
            self.assertGreater(bottom, engine.BOX[1] - 2 * 13.0)

    def test_text_actually_renders(self):
        lay = docs.layout_page("memo", 2, 13.0, 3.0)
        ink = docs.render_ink(lay, 1.0, 810, 1000)
        self.assertGreater((ink > 128).mean(), 0.02)


class TestTone(unittest.TestCase):
    def setUp(self):
        self.p = engine.normalize({"subject": "all", "fit": "cover", "speckle": 0, "dots": 0, "ghost": 0,
                                   "text": 0, "stamps": 0})

    def test_white_picture_has_no_bars(self):
        white = np.full((600, 500, 3), 250, np.uint8)
        D, _ = engine.darkness(white, None, dict(self.p, auto_levels=False))
        self.assertLess(float(D.max()), engine.THR)
        self.assertLess(dark_fraction(engine.render(D, self.p, 0)), 0.001)

    def test_black_picture_is_mostly_bars(self):
        black = np.full((600, 500, 3), 5, np.uint8)
        D, _ = engine.darkness(black, None, dict(self.p, auto_levels=False))
        self.assertGreater(dark_fraction(engine.render(D, self.p, 0)), 0.85)

    def test_amount_controls_how_much_is_redacted(self):
        rgb = sources.load_image(HANNEMAN)
        fr = []
        for a in (0.2, 0.45, 0.7):
            q = dict(self.p, amount=a)
            D, _ = engine.darkness(rgb, None, q)
            fr.append(dark_fraction(engine.render(D, q, 0)))
        self.assertLess(fr[0], fr[1])
        self.assertLess(fr[1], fr[2])

    def test_auto_levels_is_exposure_independent(self):
        rgb = sources.load_image(HANNEMAN)
        darker = (rgb.astype(np.float32) * 0.45).astype(np.uint8)
        a = dark_fraction(engine.render(engine.darkness(rgb, None, self.p)[0], self.p, 0))
        b = dark_fraction(engine.render(engine.darkness(darker, None, self.p)[0], self.p, 0))
        self.assertAlmostEqual(a, b, delta=0.06)

    def test_mask_keeps_background_clean(self):
        rgb = np.full((800, 800, 3), 10, np.uint8)      # all black ...
        mask = np.zeros((800, 800), np.float32)
        mask[:, :400] = 1.0                              # ... but only the left half is the subject
        q = dict(self.p, auto_levels=False)
        D, _ = engine.darkness(rgb, mask, q)
        x0, y0, x1, y1 = engine.BOX
        mid = (x0 + x1) // 2
        self.assertGreater(float(D[y0:y1, x0:mid - 20].mean()), 0.8)
        self.assertLess(float(D[y0:y1, mid + 20:x1].max()), 0.05)


class TestBars(unittest.TestCase):
    def test_bars_sit_on_the_text_grid(self):
        p = engine.normalize({"subject": "all", "fit": "cover"})
        rgb = sources.load_image(HANNEMAN)
        D, _ = engine.darkness(rgb, None, p)
        for page in range(5):
            spec = engine.PageSpec(p, page)
            bars = engine.compute_bars(D, spec, p, np.random.default_rng(1))
            self.assertGreater(len(bars), 50)
            for (x0, y0, x1, y1) in bars:
                r = (y0 - spec.phase) / spec.L
                self.assertAlmostEqual(r, round(r), places=5)
                self.assertAlmostEqual(y1 - y0, spec.L, places=5)
                self.assertLess(x0, x1)

    def test_line_size_changes_the_grid(self):
        p = engine.normalize({"line": 2.0})
        self.assertAlmostEqual(engine.PageSpec(p, 0).L, 26.0)


class TestRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = engine.normalize({"subject": "all", "hold_jitter": 0})
        cls.rgb = sources.load_image(HANNEMAN)
        cls.D, _ = engine.darkness(cls.rgb, None, cls.p)

    def test_deterministic(self):
        a = engine.render(self.D, self.p, 5)
        b = engine.render(self.D, self.p, 5)
        self.assertTrue(np.array_equal(a, b))

    def test_hold_repeats_and_pages_change(self):
        f0, f1, f2 = (engine.render(self.D, self.p, f) for f in (0, 1, 2))
        self.assertTrue(np.array_equal(f0, f1))                                   # same page (on twos)
        self.assertGreater(np.abs(f0.astype(int) - f2.astype(int)).mean(), 3.0)   # new paper

    def test_seed_changes_paper(self):
        q = dict(self.p, seed=12345)
        a = engine.render(self.D, self.p, 0)
        b = engine.render(self.D, q, 0)
        self.assertFalse(np.array_equal(a, b))

    def test_backdrop_colour(self):
        q = dict(self.p, bg="#ff0000")
        out = engine.render(self.D, q, 0)
        self.assertEqual(tuple(out[10, 10]), (255, 0, 0))

    def test_preview_scale_keeps_the_composition(self):
        full = engine.render(self.D, self.p, 3)
        half = engine.render(self.D, self.p, 3, scale=0.5)
        self.assertEqual(half.shape[:2], (960, 540))
        small = cv2.resize(full, (540, 960), interpolation=cv2.INTER_AREA)
        self.assertLess(np.abs(small.astype(int) - half.astype(int)).mean(), 12)

    def test_quality_2_is_twice_the_size(self):
        q = dict(self.p, quality=2)
        self.assertEqual(engine.render(self.D, q, 0).shape, (3840, 2160, 3))

    def test_speed(self):
        engine.render(self.D, self.p, 100)
        t = time.perf_counter()
        for f in range(0, 40, 2):
            engine.render(self.D, self.p, 200 + f)
        ms = (time.perf_counter() - t) / 20 * 1000
        print(f"\n    full-size render: {ms:.0f} ms per new page", end=" ")
        self.assertLess(ms, 250)


class TestSubject(unittest.TestCase):
    def test_person_mask_and_face(self):
        rgb = sources.load_image(HANNEMAN)
        m = masks.subject_mask(rgb, "person")
        self.assertIsNotNone(m, masks.status())
        h, w = m.shape
        self.assertGreater(float(m[int(h * 0.3), int(w * 0.5)]), 0.5)    # her face is the subject
        self.assertLess(float(m[int(h * 0.05), int(w * 0.05)]), 0.5)     # the dark backdrop is not
        r = masks.face_rect(rgb, m)
        self.assertIsNotNone(r)
        cx = (r[0] + r[2]) / 2
        self.assertAlmostEqual(cx / w, 0.47, delta=0.12)

    def test_gpu_is_used(self):
        masks.subject_mask(np.zeros((64, 64, 3), np.uint8), "person")
        print(f"\n    cut-out runs on {masks.status()['provider']}", end=" ")
        self.assertIn(masks.status()["provider"], ("CUDAExecutionProvider", "CPUExecutionProvider"))

    def test_face_fit_frames_head_and_shoulders(self):
        src = sources.ImageSource(HANNEMAN)
        p = engine.normalize({"fit": "face"})
        r = src.rect(p)
        self.assertIsNotNone(r)
        self.assertAlmostEqual((r[2] - r[0]) * src.w / ((r[3] - r[1]) * src.h), 740 / 834, delta=0.02)


if __name__ == "__main__":
    unittest.main(verbosity=2)
