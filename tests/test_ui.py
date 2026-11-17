"""UI test: the real page in headless Chrome, driven with mouse and keyboard events; screenshots go to the
test folder (06 App window)."""
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402
from cdp import Chrome  # noqa: E402

S = None
B = None


def setUpModule():
    global S, B
    C.prepare_inputs()
    S = C.TestServer()
    S.server.STATE["source"] = None
    B = Chrome(S.base + "/")
    B.wait_for("document.querySelector('#presets .preset')", 30)
    B.js("localStorage.clear()")


def tearDownModule():
    if B:
        B.close()


def shot(name, caption):
    path = C.d("ui", name + ".png")
    B.screenshot(path)
    C.info_add("ui", {"png": os.path.relpath(path, C.TEST_DIR), "caption": caption})


def preview_src():
    return B.js("document.querySelector('#preview').src")


def wait_new_preview(old, timeout=60):
    B.wait_for(f"document.querySelector('#preview').src !== {old!r} && document.querySelector('#preview').complete",
               timeout)


def idle(timeout=60):
    """No preview in flight or queued, and the image on screen is decoded."""
    B.wait_for("!inflight && !dirty && document.querySelector('#preview').complete", timeout)


def page_info():
    return B.js("document.querySelector('#pageInfo').textContent")


def job_id():
    return B.js("job ? job.id : null")


def wait_job(old_id, timeout=600):
    """Wait for a job newer than `old_id` to finish; returns the export bar's text."""
    B.wait_for(f"job && job.id !== {json.dumps(old_id)} && job.state !== 'running'", timeout)
    B.sleep(0.2)
    return B.js("document.querySelector('#expText').textContent")


class TestUI(unittest.TestCase):
    def test_1_empty_state(self):
        B.js("location.reload()")
        B.wait_for("document.querySelector('#presets .preset')", 30)
        self.assertFalse(B.js("document.querySelector('#empty').hidden"))
        self.assertTrue(B.js("document.querySelector('#btnMp4').disabled"))
        shot("1 Start - drop a photo or a video", "Start: nothing open yet; Export is disabled.")

    def test_2_photo(self):
        han = C.photos()["Hanneman - Portrait of a Woman (Met, CC0).jpg"]
        B.set_files("#fileInput", han)
        B.wait_for("document.querySelector('#preview').naturalWidth > 0", 90)
        B.sleep(0.5)
        self.assertTrue(B.js("document.querySelector('#empty').hidden"))
        self.assertIn("Hanneman", B.text("#fileInfo"))
        self.assertIn("page 1", page_info())
        shot("2 Photo opened - Classic preset", "A photo opened with the default Classic look.")

        old = preview_src()
        B.drag_slider("[data-key=amount] input", 0.75)
        wait_new_preview(old)
        val = B.js("document.querySelector('[data-key=amount] .val').textContent")
        self.assertNotEqual(val, "45%")
        self.assertTrue(B.js("document.querySelector('[data-key=amount]').classList.contains('changed')"))
        shot("3 Redaction amount raised", f"Redaction amount dragged to {val}: more of her turns into bars.")

        old = preview_src()
        B.js("document.querySelector('[data-key=amount] .lab span').dispatchEvent(new MouseEvent('dblclick', {bubbles: true}))")
        wait_new_preview(old)
        self.assertEqual(B.js("document.querySelector('[data-key=amount] .val').textContent"), "45%")

    def test_3_presets_and_styles(self):
        idle()
        B.click(".preset[data-id=memo]")
        B.wait_for("document.querySelector('#pageInfo').textContent.includes('typed memo')", 30)
        idle()
        self.assertIn("typed memo", page_info())
        self.assertTrue(B.js("document.querySelector('.preset[data-id=memo]').classList.contains('on')"))
        shot("4 Preset - Typewriter files", "Preset 'Typewriter files': only typed memos, more stamps.")
        old = preview_src()
        B.click(".preset[data-id=reel]")
        wait_new_preview(old)

    def test_4_play_and_step(self):
        t0 = B.text("#time")
        B.key(" ", code="Space", vk=32)
        B.sleep(2.0)
        self.assertTrue(B.js("!document.querySelector('.i-pause').hidden"))
        t1 = B.text("#time")
        B.key(" ", code="Space", vk=32)
        self.assertNotEqual(t0, t1)
        idle()
        for _ in range(4):   # each arrow press is exactly one new sheet
            p1 = page_info().split(" · ")[0]
            B.key("ArrowRight", code="ArrowRight", vk=39)
            B.wait_for(f"!document.querySelector('#pageInfo').textContent.startsWith({json.dumps(p1 + ' ')})", 30)
            idle()
            n1, n2 = int(p1.split()[1]), int(page_info().split(" · ")[0].split()[1])
            self.assertEqual(n2, n1 + 1)
        shot("5 Playing - the paper boils", f"Played for 2 s ({t0.split(' / ')[0]} -> {t1.split(' / ')[0]}); "
             "each step is a new sheet.")

    def test_5_compare_original(self):
        b = B.bbox("#btnCompare")
        x, y = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
        B.mouse("mouseMoved", x, y)
        B.mouse("mousePressed", x, y)
        B.wait_for("!document.querySelector('#original').hidden && document.querySelector('#original').complete", 20)
        B.sleep(0.3)
        shot("6 Hold Original - the source photo", "Holding 'Original' shows the untouched picture.")
        B.mouse("mouseReleased", x, y)
        B.wait_for("document.querySelector('#original').hidden", 10)

    def test_6_formats_and_framing(self):
        old = preview_src()
        B.js("(() => { const s = document.querySelector('#layout'); s.value = 'square';"
             " s.dispatchEvent(new Event('change', {bubbles: true})); })()")
        wait_new_preview(old)
        w, h = B.js("[document.querySelector('#preview').naturalWidth, document.querySelector('#preview').naturalHeight]")
        self.assertEqual(w, h)
        old = preview_src()
        B.click(".seg[data-key=fit] button[data-v=contain]")
        wait_new_preview(old)
        shot("7 Square format, whole picture", "Format 'Square 1:1' and Fit 'Whole'.")
        old = preview_src()
        B.js("(() => { const s = document.querySelector('#layout'); s.value = 'reel';"
             " s.dispatchEvent(new Event('change', {bubbles: true})); })()")
        B.click(".seg[data-key=fit] button[data-v=face]")
        wait_new_preview(old)

    def test_7_export_png_and_mp4_photo(self):
        before = set(os.listdir(os.environ["DECLASSIFIED_OUT"])) if os.path.isdir(os.environ["DECLASSIFIED_OUT"]) else set()
        jid = job_id()
        B.click("#btnPng")
        text = wait_job(jid, 120)
        self.assertIn("Saved", text)
        shot("8 Export PNG done", "Export PNG: saved, with 'Show in folder'.")
        B.click("#btnClose")
        B.js("(() => { const e = document.querySelector('[data-key=duration] input'); e.value = 2;"
             " e.dispatchEvent(new Event('input', {bubbles: true})); })()")
        jid = job_id()
        B.click("#btnMp4")
        B.wait_for(f"job && job.id !== {json.dumps(jid)} && job.done > 5", 60)
        shot("9 Export MP4 rendering", "Export MP4 in progress (frame count and time left).")
        text = wait_job(jid, 300)
        self.assertIn("Saved", text)
        after = set(os.listdir(os.environ["DECLASSIFIED_OUT"]))
        new = sorted(after - before)
        self.assertEqual(len([f for f in new if f.endswith(".png")]), 1, new)
        mp4 = [f for f in new if f.endswith(".mp4")]
        self.assertEqual(len(mp4), 1, new)
        pr = C.ffprobe(os.path.join(os.environ["DECLASSIFIED_OUT"], mp4[0]))
        self.assertEqual(pr["frames"], 48)
        B.click("#btnClose")

    def test_8_video(self):
        vid = C.videos()["dancer (3.4 s, 30 fps, test tone).mp4"]
        old = preview_src()
        B.set_files("#fileInput", vid)
        B.wait_for(f"document.querySelector('#preview').src !== {old!r}", 120)
        B.wait_for("document.querySelector('#busy').hidden", 120)
        self.assertIn("30 fps", B.text("#fileInfo"))
        self.assertIn("audio", B.text("#fileInfo"))
        self.assertTrue(B.js("document.body.classList.contains('is-video')"))
        # scrub to the middle
        old = preview_src()
        B.js("(() => { const s = document.querySelector('#scrub'); s.value = Math.floor(s.max / 2);"
             " s.dispatchEvent(new Event('input', {bubbles: true})); })()")
        wait_new_preview(old)
        shot("10 Video opened - scrubbed to the middle", "A video (30 fps, with audio), scrubbed to the middle.")
        before = set(os.listdir(os.environ["DECLASSIFIED_OUT"]))
        jid = job_id()
        B.click("#btnMp4")
        text = wait_job(jid, 600)
        self.assertIn("Saved", text)
        shot("11 Video exported", "The video exported as MP4 with its audio.")
        new = sorted(set(os.listdir(os.environ["DECLASSIFIED_OUT"])) - before)
        self.assertEqual(len(new), 1)
        pr = C.ffprobe(os.path.join(os.environ["DECLASSIFIED_OUT"], new[0]))
        self.assertTrue(pr["audio"])
        self.assertEqual(pr["frames"], 102)

    def test_9_settings_persist_and_reset(self):
        B.click(".preset[data-id=heavy]")
        B.js("location.reload()")
        B.wait_for("document.querySelector('#presets .preset')", 30)
        B.wait_for("document.querySelector('#preview').naturalWidth > 0", 60)  # the open video comes back too
        self.assertTrue(B.js("document.querySelector('.preset[data-id=heavy]').classList.contains('on')"))
        B.click("#btnReset")
        self.assertTrue(B.js("document.querySelector('.preset[data-id=reel]').classList.contains('on')"))
        errors = [l for l in B.logs if l.startswith(("pageerror", "error"))]
        self.assertEqual(errors, [], errors)
        shot("12 After reload - settings kept, then reset", "Settings survive a reload; 'Reset all' brings back the default look.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
