"""Restormer motion-deblurring — ONNX Runtime.

Restormer (CVPR 2022 oral, swz30/Restormer, MIT) is an efficient transformer
for image restoration; we ship the GoPro-trained Motion_Deblurring checkpoint
(~26M params), exported to ONNX (opset-17 legacy export → DirectML-safe) and
served via onnxruntime — no torch at runtime. Replaces the weaker NAFNet deblur.

The 4-level encoder/decoder downsamples by ``SCALE_FACTOR`` (=8), so H/W fed to
the graph must be a multiple of it (reflect-pad, then crop back). ONNX parity vs
PyTorch was verified at 1.6e-5 max abs diff on real photos.

Note: this is a heavy transformer — expect a few seconds per photo on a GPU
execution provider and materially longer on pure CPU. It runs on demand (a
user's explicit "sharpen / deblur"), not in bulk.
"""
from __future__ import annotations

import logging

import numpy as np
from PIL import Image

from selects.ml.onnx_rt import model_session

log = logging.getLogger(__name__)

# Restormer(num_blocks=[4,6,6,8]): 3× PixelUnshuffle(2) → factor 8.
SCALE_FACTOR = 8


def deblur_with_restormer(img: Image.Image, cfg=None) -> Image.Image:
    """Run Restormer GoPro motion-deblurring. Returns a new RGB PIL Image.

    ``cfg`` is accepted for call-site compatibility but unused (weights come from
    the shared HF ONNX repo).
    """
    sess = model_session("restormer")
    arr = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0  # [H,W,3], RGB [0,1]
    h, w = arr.shape[:2]
    pad_h = (SCALE_FACTOR - h % SCALE_FACTOR) % SCALE_FACTOR
    pad_w = (SCALE_FACTOR - w % SCALE_FACTOR) % SCALE_FACTOR
    if pad_h or pad_w:
        arr = np.pad(arr, ((0, pad_h), (0, pad_w), (0, 0)), mode="reflect")

    x = np.ascontiguousarray(arr.transpose(2, 0, 1)[None])          # [1,3,H',W']
    iname = sess.get_inputs()[0].name
    out = sess.run(None, {iname: x})[0]                            # [1,3,H',W']
    out_np = np.clip(out[0].transpose(1, 2, 0)[:h, :w], 0.0, 1.0)   # crop + clamp
    out_np = (out_np * 255.0).round().astype(np.uint8)
    return Image.fromarray(out_np)
