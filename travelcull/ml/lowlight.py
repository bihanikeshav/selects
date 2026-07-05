"""Zero-DCE++ low-light image enhancement.

Vendored from https://github.com/Li-Chongyi/Zero-DCE_extension (TPAMI 2022)
under the original repository's license. ~10K-param depth-wise separable
network that estimates curve maps to brighten a low-light image without
any reference data. Inference is single-pass, ~10ms on GPU at 800×600.

Usage:
    from travelcull.ml.lowlight import enhance_with_zero_dce_plus
    out_img = enhance_with_zero_dce_plus(pil_img)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, TYPE_CHECKING

import numpy as np
from PIL import Image

# torch is imported lazily inside the functions that need it — the FastAPI
# worker-thread initialisation can fail on some Windows + CUDA setups when
# torch is loaded eagerly at module import time.
if TYPE_CHECKING:
    import torch
    import torch.nn as nn

log = logging.getLogger(__name__)

# Weights pulled from the upstream snapshots folder. The file is ~50KB.
# Use raw.githubusercontent.com with literal `+` (URL-encoded %2B 404s here).
WEIGHTS_URL = (
    "https://raw.githubusercontent.com/Li-Chongyi/Zero-DCE_extension/master/"
    "Zero-DCE++/snapshots_Zero_DCE++/Epoch99.pth"
)
WEIGHTS_FILENAME = "zero_dce_plus_epoch99.pth"


def _build_model_classes():
    """Construct the Zero-DCE++ classes lazily so importing this module
    doesn't pull torch in. Returns (CSDN_Tem, EnhanceNetNoPool)."""
    import torch
    import torch.nn as nn

    class CSDN_Tem(nn.Module):
        def __init__(self, in_ch, out_ch):
            super().__init__()
            self.depth_conv = nn.Conv2d(in_ch, in_ch, 3, 1, 1, groups=in_ch)
            self.point_conv = nn.Conv2d(in_ch, out_ch, 1, 1, 0, groups=1)

        def forward(self, x):
            return self.point_conv(self.depth_conv(x))

    class EnhanceNetNoPool(nn.Module):
        def __init__(self, scale_factor=1):
            super().__init__()
            self.scale_factor = scale_factor
            self.upsample = nn.UpsamplingBilinear2d(scale_factor=scale_factor)
            self.relu = nn.ReLU(inplace=True)
            n = 32
            self.e_conv1 = CSDN_Tem(3, n)
            self.e_conv2 = CSDN_Tem(n, n)
            self.e_conv3 = CSDN_Tem(n, n)
            self.e_conv4 = CSDN_Tem(n, n)
            self.e_conv5 = CSDN_Tem(n * 2, n)
            self.e_conv6 = CSDN_Tem(n * 2, n)
            self.e_conv7 = CSDN_Tem(n * 2, 3)

        def forward(self, x):
            x_ds = x
            if self.scale_factor != 1:
                x_ds = nn.functional.interpolate(
                    x, scale_factor=1.0 / self.scale_factor,
                    mode="bilinear", align_corners=False,
                )
            x1 = self.relu(self.e_conv1(x_ds))
            x2 = self.relu(self.e_conv2(x1))
            x3 = self.relu(self.e_conv3(x2))
            x4 = self.relu(self.e_conv4(x3))
            x5 = self.relu(self.e_conv5(torch.cat([x3, x4], dim=1)))
            x6 = self.relu(self.e_conv6(torch.cat([x2, x5], dim=1)))
            x_r = torch.tanh(self.e_conv7(torch.cat([x1, x6], dim=1)))
            if self.scale_factor != 1:
                x_r = self.upsample(x_r)
            out = x
            for _ in range(8):
                out = out + x_r * (out.pow(2) - out)
            return out

    return CSDN_Tem, EnhanceNetNoPool


_MODEL_CACHE: dict = {}


def _device():
    import torch
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def _weights_path(cfg) -> Path:
    p = cfg.state_dir / "models" / WEIGHTS_FILENAME
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _ensure_weights(cfg) -> Path:
    """Download the Epoch99 weights into the state dir on first use."""
    target = _weights_path(cfg)
    if target.exists() and target.stat().st_size > 1000:
        return target
    log.info("zero-dce++: downloading weights to %s", target)
    from travelcull.ml.model_assets import download_file
    download_file(WEIGHTS_URL, target)
    return target


def _load_model(cfg):
    import torch
    key = str(_weights_path(cfg))
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]
    weights = _ensure_weights(cfg)
    _, EnhanceNetNoPool = _build_model_classes()
    model = EnhanceNetNoPool(scale_factor=12)
    state = torch.load(weights, map_location="cpu", weights_only=True)
    state = {k.replace("module.", ""): v for k, v in state.items()}
    model.load_state_dict(state)
    model = model.to(_device()).eval()
    _MODEL_CACHE[key] = model
    return model


def enhance_with_zero_dce_plus(img: Image.Image, cfg) -> Image.Image:
    """Run Zero-DCE++ on a PIL Image. Returns a new PIL Image (RGB)."""
    import torch
    model = _load_model(cfg)
    arr = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0
    sf = model.scale_factor
    h, w = arr.shape[:2]
    pad_h = (sf - h % sf) % sf
    pad_w = (sf - w % sf) % sf
    if pad_h or pad_w:
        arr = np.pad(arr, ((0, pad_h), (0, pad_w), (0, 0)), mode="reflect")
    tensor = torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0).to(_device())
    with torch.inference_mode():
        out = model(tensor)
    out_np = out.squeeze(0).clamp(0, 1).cpu().permute(1, 2, 0).numpy()
    out_np = out_np[:h, :w]
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
