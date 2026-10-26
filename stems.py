"""Optional: the vocals of a piece of music as a track of their own (DJ transitions that leave the singing alone, see djmix.py).

Off unless DJ_STEMS=1 and PyTorch and `demucs` are installed (they are large: see Dockerfile.stems). The model (htdemucs) is loaded when it is first needed and
let go of again after a few minutes without use. A piece of 40 seconds takes about 8 s on a fast laptop and one to two minutes on a small server; the results
are part of the bridge files, which are kept, so a pair of songs is separated once.
"""
import importlib.util
import logging
import os
import threading
import time
from pathlib import Path

import db

try:
    import numpy as np
except ImportError:
    np = None

log = logging.getLogger("stems")

ENABLED = os.getenv("DJ_STEMS", "0") == "1"
MODEL = os.getenv("DJ_STEMS_MODEL", "htdemucs")
THREADS = max(1, int(os.getenv("DJ_STEMS_THREADS", str(min(4, os.cpu_count() or 2)))))
IDLE_SECONDS = 300
MODEL_DIR = Path(os.getenv("DJ_STEMS_DIR") or Path(db.DB_PATH).parent / "models")
SR = 44100  # what the model is made for

_lock = threading.Lock()
_model = None
_timer: threading.Timer | None = None


def available() -> bool:
    return bool(ENABLED and np is not None and importlib.util.find_spec("torch") and importlib.util.find_spec("demucs"))


def _unload() -> None:
    global _model
    with _lock:
        if _model is not None:
            _model = None
            log.info("stems: model unloaded after %d s without use", IDLE_SECONDS)


def vocals(x):
    """The singing in `x` (float32, shape (n, 2), 44100 Hz) as an array of the same shape. Raises if the model cannot run."""
    global _model, _timer
    with _lock:
        if _timer:
            _timer.cancel()
        import torch  # noqa: PLC0415 (large: only when asked for)
        from demucs.apply import apply_model  # noqa: PLC0415
        if _model is None:
            os.environ.setdefault("TORCH_HOME", str(MODEL_DIR))
            from demucs.pretrained import get_model  # noqa: PLC0415
            t0 = time.time()
            _model = get_model(MODEL)
            _model.eval()
            log.info("stems: model %s loaded in %.1f s", MODEL, time.time() - t0)
        torch.set_num_threads(THREADS)
        wav = torch.from_numpy(np.ascontiguousarray(x.T))
        ref = wav.mean(0)
        wav = (wav - ref.mean()) / (ref.std() + 1e-8)  # the model expects a normalized input
        t0 = time.time()
        with torch.no_grad():
            out = apply_model(_model, wav[None], device="cpu", split=True, overlap=0.1, shifts=0, progress=False)[0]
        out = out[_model.sources.index("vocals")] * (ref.std() + 1e-8) + ref.mean()
        log.info("stems: %.0f s of music separated in %.1f s", len(x) / SR, time.time() - t0)
        _timer = threading.Timer(IDLE_SECONDS, _unload)
        _timer.daemon = True
        _timer.start()
        return out.numpy().T.astype(np.float32)
