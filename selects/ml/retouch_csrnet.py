"""CSRNet global image retouching — ONNX Runtime.

CSRNet (Conditional Sequential Modulation, ECCV 2020) is a ~37K-param network
that applies a sequence of global, pixel-independent operations — a restrained
tone-curve + colour correction trained on MIT-Adobe FiveK. Exported to ONNX and
served via onnxruntime (no torch). Runs on any input size.
"""
from __future__ import annotations

import logging

import numpy as np
from PIL import Image

from selects.ml.onnx_rt import model_session

log = logging.getLogger(__name__)


def retouch_with_csrnet(img: Image.Image, cfg=None) -> Image.Image:
    """Run CSRNet on a PIL Image. Returns a new RGB PIL Image.

    ``cfg`` is accepted for call-site compatibility but unused (weights come from
    the shared HF ONNX repo). The published ONNX carries the real FiveK weights,
    so — unlike the old torch path — there is no random-init fallback to guard.
    """
    sess = model_session("csrnet")
    arr = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0  # [H,W,3]
    x = np.ascontiguousarray(arr.transpose(2, 0, 1)[None])          # [1,3,H,W]
    out = sess.run(None, {"input": x})[0]                           # [1,3,H,W]
    out_np = np.clip(out[0].transpose(1, 2, 0), 0.0, 1.0)
    out_np = (out_np * 255.0).round().clip(0, 255).astype(np.uint8)
    return Image.fromarray(out_np)
