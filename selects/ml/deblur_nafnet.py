"""NAFNet GoPro deblur — ONNX Runtime.

NAFNet (Nonlinear Activation Free Network, CVPR 2022 oral, arXiv:2204.04676)
achieves 33.69 dB PSNR on the GoPro benchmark at 8.4% of the previous SOTA's
FLOPs — a real "drop-in deblur". We use the GoPro-trained width=32 variant
(~17M params), exported to ONNX (fp16) and served via onnxruntime so the app
needs no torch. The graph bakes in the pad-to-multiple-of-16 + crop-back that
the original model did internally, so any input H/W works.
"""
from __future__ import annotations

import logging

import numpy as np
from PIL import Image

from selects.ml.onnx_rt import model_session

log = logging.getLogger(__name__)


def deblur_with_nafnet(img: Image.Image, cfg=None) -> Image.Image:
    """Run NAFNet GoPro deblurring on a PIL Image. Returns a new RGB Image.

    ``cfg`` is accepted for call-site compatibility but unused — weights are
    fetched from the shared HF ONNX repo, not the per-folder state dir.
    """
    sess = model_session("nafnet")
    arr = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0  # [H,W,3]
    x = np.ascontiguousarray(arr.transpose(2, 0, 1)[None])          # [1,3,H,W]
    out = sess.run(None, {"input": x})[0]                           # [1,3,H,W]
    out_np = np.clip(out[0].transpose(1, 2, 0), 0.0, 1.0)           # [H,W,3]
    out_np = (out_np * 255.0).round().clip(0, 255).astype(np.uint8)
    return Image.fromarray(out_np)
