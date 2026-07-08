"""Zero-DCE++ low-light enhancement — ONNX Runtime.

Zero-DCE++ (TPAMI 2022, Li-Chongyi/Zero-DCE_extension) is a ~10K-param
depth-wise-separable network that estimates curve maps to brighten a low-light
image with no reference data. Exported to ONNX and served via onnxruntime (no
torch). The network downsamples by ``SCALE_FACTOR`` internally, so the input
must be padded to a multiple of it (reflect) and cropped back afterwards.

Usage:
    from selects.ml.lowlight import enhance_with_zero_dce_plus
    out_img = enhance_with_zero_dce_plus(pil_img)
"""
from __future__ import annotations

import logging

import numpy as np
from PIL import Image

from selects.ml.onnx_rt import model_session

log = logging.getLogger(__name__)

# EnhanceNetNoPool(scale_factor=12): internal down/up-sampling by this factor,
# so H and W fed to the graph must be multiples of it.
SCALE_FACTOR = 12


def enhance_with_zero_dce_plus(img: Image.Image, cfg=None) -> Image.Image:
    """Run Zero-DCE++ on a PIL Image. Returns a new RGB PIL Image.

    ``cfg`` is accepted for call-site compatibility but unused (weights come from
    the shared HF ONNX repo).
    """
    sess = model_session("zero_dce")
    arr = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0  # [H,W,3]
    h, w = arr.shape[:2]
    pad_h = (SCALE_FACTOR - h % SCALE_FACTOR) % SCALE_FACTOR
    pad_w = (SCALE_FACTOR - w % SCALE_FACTOR) % SCALE_FACTOR
    if pad_h or pad_w:
        arr = np.pad(arr, ((0, pad_h), (0, pad_w), (0, 0)), mode="reflect")

    x = np.ascontiguousarray(arr.transpose(2, 0, 1)[None])          # [1,3,H',W']
    out = sess.run(None, {"input": x})[0]                           # [1,3,H',W']
    out_np = out[0].transpose(1, 2, 0)[:h, :w]                      # crop padding
    out_np = np.clip(out_np, 0.0, 1.0)
    out_np = (out_np * 255.0).round().clip(0, 255).astype(np.uint8)
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
