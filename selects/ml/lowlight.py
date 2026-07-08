"""Retinexformer low-light enhancement — ONNX Runtime.

Retinexformer (ICCV 2023, caiyuanhao1998/Retinexformer, MIT) is an
illumination-guided transformer for low-light enhancement. We ship the
MIT-Adobe FiveK checkpoint (natural expert-retouch — brightens genuinely dark
scenes with rich, cast-free colour without over-brightening well-exposed ones),
exported to ONNX (opset-17 legacy export, DirectML-safe) and served via
onnxruntime — no torch at runtime. Replaces the old Zero-DCE++ export, which
put a heavy purple cast on everything (broken export).

The 2-level encoder downsamples by ``SCALE_FACTOR`` (=4), so H/W fed to the
graph must be a multiple of it (reflect-pad, then crop back). ONNX parity vs
PyTorch was verified at 1.5e-6 max abs diff on real photos.

Usage:
    from selects.ml.lowlight import enhance_with_retinexformer
    out_img = enhance_with_retinexformer(pil_img)
"""
from __future__ import annotations

import logging

import numpy as np
from PIL import Image

from selects.ml.onnx_rt import model_session

log = logging.getLogger(__name__)

# Retinexformer(stage=1, level=2): 2² spatial downsampling, so H/W must be a
# multiple of 4 (the repo reflect-pads to this and crops back).
SCALE_FACTOR = 4


def enhance_with_retinexformer(img: Image.Image, cfg=None) -> Image.Image:
    """Run Retinexformer (FiveK) low-light enhancement. Returns a new RGB Image.

    ``cfg`` is accepted for call-site compatibility but unused (weights come from
    the shared HF ONNX repo).
    """
    sess = model_session("retinexformer")
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


def is_low_light(img: Image.Image, threshold: float = 0.30) -> bool:
    """Cheap classifier: True if the image's mean luma is below the threshold
    (0-1 scale). Used by the Image Doctor to decide whether to suggest a
    Zero-DCE++ fix.
    """
    gray = np.asarray(img.convert("L"), dtype=np.float32) / 255.0
    return float(gray.mean()) < threshold


# Optional helper that returns full luma stats — used by the doctor classifier
def luma_stats(img: Image.Image) -> dict:
    gray = np.asarray(img.convert("L"), dtype=np.float32) / 255.0
    return {
        "mean": float(gray.mean()),
        "std": float(gray.std()),
        "clipped_low": float((gray < 8 / 255).mean()),
        "clipped_high": float((gray > 247 / 255).mean()),
        "p05": float(np.percentile(gray, 5)),
        "p95": float(np.percentile(gray, 95)),
    }
