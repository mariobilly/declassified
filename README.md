# Declassified

Turns a photo or a video into a **redacted declassified document**. The dark parts of the picture become black
redaction bars on the typed lines of a court filing or a typewritten memo, mid-tones become toner specks and grey
dots, and the sheet of paper changes every 2 frames (12 sheets a second), so the portrait "boils" from page to page.

**Everything runs on your computer. No account, no API key, nothing is uploaded.** The cut-out model downloads once,
on the first start.

Inspired by the redacted-document portrait in a reel by [Jordan Gladman](https://www.instagram.com/reel/DdjdcquO5QM/).
Declassified is an independent project and shares no code with it.

## Install

You need:
- Windows 10 or 11 (it is built and tested on Windows 11)
- [Python](https://www.python.org/downloads/) 3.11 or newer
- [ffmpeg](https://ffmpeg.org/download.html) on your PATH (ffmpeg and ffprobe), for videos and MP4 export
- Google Chrome or Microsoft Edge: the app opens in its own window
- An NVIDIA GPU makes the subject cut-out fast. Without one it runs on the CPU, which is slower.

```
python -m pip install -r requirements.txt
python launch.pyw
```

For the GPU, install `onnxruntime-gpu` instead of `onnxruntime`. It needs cuDNN 9; the app also finds the copy that
comes with a CUDA build of [PyTorch](https://pytorch.org/get-started/locally/).

The first start downloads rembg's U2-Net person model (about 170 MB) into the `.u2net` folder in your user folder.
The *Object* cut-out downloads a second model the first time you use it.

The launcher starts a local server on `127.0.0.1:48751` and opens the app window. The server quits about 45 s after
the window closes. Set `DECLASSIFIED_PORT` to use another port.

Optional: `powershell -ExecutionPolicy Bypass -File make_shortcut.ps1` puts a **Declassified** shortcut on your
Desktop. You can drop a photo or a video onto it.

## How to use it

1. **Open…** (or drag a file in, or paste an image with Ctrl+V). Photos become an animated loop; videos keep their
   frame rate and sound.
2. Pick a **preset** (Classic, Heavy redaction, Light touch, Typewriter files, Court sealed, Clean bars, Slow boil)
   and adjust:
   - **Subject & framing**: *Person* / *Object* cuts the subject out with AI (U2-Net), so only they are redacted.
     *Fit Face* frames head and shoulders, *Subject* the whole cut-out, *Fill* / *Whole* the whole picture. Zoom and
     pan fine-tune it.
   - **Tone**: *Redaction amount* is the share of the subject that turns into bars (it doesn't depend on the
     exposure when Auto levels is on). Also Contrast, Brightness, Face detail and Invert.
   - **Redaction bars**: line size, shortest bar, ragged edges, block merge.
   - **Toner**: specks, grey dots, ghost tone, scan grit.
   - **Paper**: court filing, typed memo, sealed and privacy sheets; text; text column width; stamps
     (DECLASSIFIED, RECEIVED, BEST COPY AVAILABLE…); format (Reel 9:16 on grey, 4:5, 1:1, 16:9, page only);
     backdrop colour.
   - **Motion**: a new sheet every N frames (2 by default), hold wobble, page shake, stop-motion video, loop length
     and frame rate for photos, and the seed (🎲 gives new papers).
3. **Play** (Space) to watch it boil. ←/→ step one sheet. Hold **Original** to compare.
4. **Export PNG** (Ctrl+S) saves the current frame, **Export MP4** (Ctrl+E) the loop or the whole video.
   - Size 1× is 1080 px wide; 2× is 2160×3840 in the Reel 9:16 format.
   - MP4 is H.264 (bt709), and a video's sound is copied in.
   - Files go to `Videos\Declassified` in your user folder. Set the `DECLASSIFIED_OUT` environment variable to use
     another folder.

Settings are remembered between sessions.

## What's where

| Path | What |
| --- | --- |
| `launch.pyw` | The launcher: starts the server (port **48751**, exclusive bind) and opens a Chrome or Edge app window. |
| `app/engine.py` | The effect: tone mapping, line-grid redaction bars, specks, dots, paper, stamps, scan grain, page timeline. Geometry is in design units (page 810×1000), so preview and export match. |
| `app/documents.py` | Procedural pages: court filings (Times), typewriter memos (Courier), sealed/privacy sheets, stamps. |
| `app/masks.py` | Subject cut-out (rembg's U2-Net models via onnxruntime; on the GPU it takes cuDNN from PyTorch's `lib` folder when needed) and Haar face framing. |
| `app/sources.py` | The open photo or video: preview proxy for videos, cached masks, steady framing and tone over a clip. |
| `app/exporter.py`, `app/media.py` | PNG/MP4 export on worker threads, ffmpeg pipes (H.264, bt709, audio copied in), Windows power-throttling opt-out. |
| `app/server.py` | FastAPI API + UI files. |
| `ui/` | The app window (HTML/CSS/JS, no build step). |
| `assets/` | Icon (`make_icon.py` redraws it). |
| `samples/` | Free test photos and a clip (credits in `samples/CREDITS.md`). |
| `tests/` | Engine, HTTP and UI tests. `run_all.py` runs them all and writes a review folder. |
| `make_shortcut.ps1` | Creates the Desktop shortcut. |

Data the app keeps for itself (uploaded working copies, log, window profile): `%LOCALAPPDATA%\Declassified`.

## Tests

```
python -m pip install websocket-client
python tests\run_all.py
```

This runs 47 tests: the engine, the HTTP API end to end with real photo and video exports, and the UI in headless
Chrome. It also writes `tests\_out\report.html` with every photo and video export, paper styles, presets, formats
and screenshots of the app window. Set `DECLASSIFIED_TESTS` to write the review folder somewhere else.

The tests use the files in `samples/`: three public-domain portraits, and short clips cut from the dancer video (some
get a generated test tone, so the sound path is tested too).

## Credits

- Inspiration: the redacted-document portrait in a reel by
  [Jordan Gladman](https://www.instagram.com/reel/DdjdcquO5QM/).
- Test photos: portraits by Adriaen Hanneman, Velázquez and Antonello da Messina from
  [The Metropolitan Museum of Art](https://www.metmuseum.org/) Open Access program (CC0).
- Test clip: "A Woman Dancing Gracefully" by
  [Pavel Danilyuk](https://www.pexels.com/video/a-woman-dancing-gracefully-6939968/)
  ([Pexels licence](https://www.pexels.com/license/)). See [samples/CREDITS.md](samples/CREDITS.md).
- [rembg](https://github.com/danielgatis/rembg) (MIT) with the [U2-Net](https://github.com/xuebinqin/U-2-Net)
  models (Apache-2.0).

## Licence

MIT, see [LICENSE](LICENSE). The libraries and models above keep their own licences.
