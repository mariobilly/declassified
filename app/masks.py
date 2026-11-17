"""Subject cut-out masks (U2-Net via rembg's model files, on the GPU when available).

Only the subject gets redacted, the way the reel's portrait sits on clean paper. onnxruntime-gpu needs cuDNN 9;
it isn't installed system-wide here, but torch ships it in torch/lib, so that folder is added to the DLL path.
"""
import importlib.util
import os
import threading

import cv2
import numpy as np

MODELS = {"person": "u2net_human_seg", "object": "u2net"}   # both 320x320 U2-Net: ~60 ms on the GPU
_sessions = {}
_lock = threading.Lock()   # one inference at a time
_status = {"provider": None, "error": None}
_dll_ready = False


def _prepare_dlls():
    global _dll_ready
    if _dll_ready:
        return
    _dll_ready = True
    try:
        spec = importlib.util.find_spec("torch")   # locate only; torch itself is never imported here
        if spec and spec.submodule_search_locations:
            lib = os.path.join(list(spec.submodule_search_locations)[0], "lib")
            if os.path.isfile(os.path.join(lib, "cudnn64_9.dll")):
                os.add_dll_directory(lib)
                os.environ["PATH"] = lib + os.pathsep + os.environ.get("PATH", "")
                import onnxruntime as ort
                if hasattr(ort, "preload_dlls"):
                    ort.preload_dlls(directory=lib)
    except Exception as e:  # CPU fallback still works
        _status["error"] = f"cuda setup: {e}"


def _session(kind):
    if kind not in _sessions:
        _prepare_dlls()
        from rembg import new_session  # slow import (~2 s), only when a cut-out is asked for
        try:
            s = new_session(MODELS[kind], providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
        except Exception:
            s = new_session(MODELS[kind], providers=["CPUExecutionProvider"])
        inner = s.inner_session
        _status["provider"] = inner.get_providers()[0]
        _sessions[kind] = inner
    return _sessions[kind]


MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)


def subject_mask(rgb, kind):
    """Float32 HxW mask in 0..1 for an RGB uint8 frame. kind: 'person' | 'object'; None for 'all' or on error."""
    if kind not in MODELS:
        return None
    h, w = rgb.shape[:2]
    x = cv2.resize(rgb, (320, 320), interpolation=cv2.INTER_AREA).astype(np.float32)
    x /= max(float(x.max()), 1e-6)
    x = ((x - MEAN) / STD).transpose(2, 0, 1)[None]
    try:
        with _lock:
            sess = _session(kind)
            out = sess.run(None, {sess.get_inputs()[0].name: np.ascontiguousarray(x, np.float32)})[0][0, 0]
        _status["error"] = None
    except Exception as e:  # never break a render over the cut-out: fall back to the whole picture
        _status["error"] = f"{type(e).__name__}: {e}"
        return None
    mi, ma = float(out.min()), float(out.max())
    m = (out - mi) / max(ma - mi, 1e-6)
    return cv2.resize(m.astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR)


_face_lock = threading.Lock()   # Haar cascades crash the process when two threads use one at once
_cascades = {}


def _cascade(name):
    if name not in _cascades:
        _cascades[name] = cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, name))
    return _cascades[name]


BOX_ASPECT = 740 / 834   # the page's portrait box (engine.BOX)


def face_rect(rgb, mask=None):
    """Head-and-shoulders framing around the largest face, in source px (x0, y0, x1, y1); None if no face.

    The rect has the portrait box's aspect, the face ~40% of its width and centred 42% down, like the reel.
    With a subject mask, faces must sit on the subject (Haar finds 'faces' in clothes and walls otherwise).
    """
    h, w = rgb.shape[:2]
    f = min(1.0, 640 / max(h, w))
    small = cv2.resize(rgb, (max(1, int(w * f)), max(1, int(h * f))), interpolation=cv2.INTER_AREA)
    gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_RGB2GRAY))
    minsz = max(20, int(min(gray.shape) * 0.07))
    faces = []
    with _face_lock:
        for name, flip in (("haarcascade_frontalface_alt2.xml", False), ("haarcascade_profileface.xml", False),
                           ("haarcascade_profileface.xml", True)):
            g = cv2.flip(gray, 1) if flip else gray
            found = _cascade(name).detectMultiScale(g, scaleFactor=1.1, minNeighbors=5, minSize=(minsz, minsz))
            for (x, y, fw, fh) in (found if len(found) else []):
                if flip:
                    x = g.shape[1] - x - fw
                faces.append((x, y, fw, fh))
            if faces:
                break
    if mask is not None and faces:
        mh, mw = mask.shape[:2]
        ys = np.nonzero((mask > 0.5).any(axis=1))[0]
        top, bottom = (ys[0], ys[-1]) if ys.size else (0, mh)

        def on_subject(r):
            x, y, fw, fh = (v / f for v in r)
            sx, sy = mw / w, mh / h
            patch = mask[int((y + fh * 0.25) * sy):max(int((y + fh * 0.25) * sy) + 1, int((y + fh * 0.75) * sy)),
                         int((x + fw * 0.25) * sx):max(int((x + fw * 0.25) * sx) + 1, int((x + fw * 0.75) * sx))]
            cy = (y + fh / 2) * sy
            # on the person (pale faces get a soft mask), in the upper part of the silhouette
            return patch.size and patch.mean() > 0.25 and (cy - top) <= 0.5 * (bottom - top) + fh * sy
        faces = [r for r in faces if on_subject(r)]
    if not faces:
        return None
    x, y, fw, fh = max(faces, key=lambda r: r[2] * r[3])
    x, y, fw, fh = x / f, y / f, fw / f, fh / f
    rw = fw * 2.5
    rh = rw / BOX_ASPECT
    cx, cy = x + fw / 2, y + fh / 2
    return (cx - rw / 2, cy - 0.42 * rh, cx + rw / 2, cy + 0.58 * rh)


def warm(kind="person"):
    """Load the model ahead of the first preview (first CUDA run takes ~1 s)."""
    try:
        subject_mask(np.zeros((64, 64, 3), np.uint8), kind)
    except Exception:
        pass


def status():
    return dict(_status)
