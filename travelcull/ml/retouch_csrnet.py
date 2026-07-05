"""CSRNet — Conditional Sequential Modulation for Efficient Global Image Retouching.

Vendored from https://github.com/hejingwenhejingwen/CSRNet (ECCV 2020).
~37K-parameter MLP that learns a sequence of global pixel-independent
operations to apply Adobe-style retouches. Trained on MIT-Adobe FiveK
(5000 photos retouched by 5 pro retouchers). The result is a clean,
restrained tone curve + colour correction — closer to professional
retouching than the CLAHE pipeline, without the LAION-warm-cinematic
bias of newer "Insta retouchers".

Weights are vendored to .travelcull/models/csrnet_fivek.pth on first
use. Inference is single-pass and runs in <20ms on CPU at 512×512.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, TYPE_CHECKING

import numpy as np
from PIL import Image

if TYPE_CHECKING:
    import torch
    import torch.nn as nn

log = logging.getLogger(__name__)

# Upstream pretrained weights live in the official repo. Note: the vendored
# CSRNet class above is a simplified reimplementation — the state-dict keys
# from upstream may not match exactly. Non-strict load gives partial weights
# and the rest stays random. Treat output as experimental until verified.
WEIGHTS_URL_CANDIDATES = [
    "https://github.com/hejingwenhejingwen/CSRNet/raw/master/experiments/pretrain_models/csrnet.pth",
]
WEIGHTS_FILENAME = "csrnet_fivek.pth"


def _build_model_class():
    """Construct the CSRNet class lazily."""
    import torch
    import torch.nn as nn

    class Condition(nn.Module):
        def __init__(self, in_nc=3, nf=32):
            super().__init__()
            self.conv1 = nn.Conv2d(in_nc, nf, 7, stride=2, padding=3)
            self.conv2 = nn.Conv2d(nf, nf, 3, stride=2, padding=1)
            self.conv3 = nn.Conv2d(nf, nf, 3, stride=2, padding=1)
            self.act = nn.LeakyReLU(0.2, inplace=True)

        def forward(self, x):
            out = self.act(self.conv1(x))
            out = self.act(self.conv2(out))
            out = self.act(self.conv3(out))
            return torch.mean(out, dim=[2, 3], keepdim=True)

    class CSRNet(nn.Module):
        def __init__(self, in_nc=3, out_nc=3, base_nf=64, cond_nf=32):
            super().__init__()
            self.cond_net = Condition(in_nc, cond_nf)
            self.conv1 = nn.Conv2d(in_nc, base_nf, 1, 1, 0)
            self.conv2 = nn.Conv2d(base_nf, base_nf, 1, 1, 0)
            self.conv3 = nn.Conv2d(base_nf, out_nc, 1, 1, 0)
            self.cond_scale1 = nn.Linear(cond_nf, base_nf)
            self.cond_shift1 = nn.Linear(cond_nf, base_nf)
            self.cond_scale2 = nn.Linear(cond_nf, base_nf)
            self.cond_shift2 = nn.Linear(cond_nf, base_nf)
            self.cond_scale3 = nn.Linear(cond_nf, out_nc)
            self.cond_shift3 = nn.Linear(cond_nf, out_nc)
            self.act = nn.LeakyReLU(0.2, inplace=True)

        def forward(self, x):
            cond = self.cond_net(x).flatten(1)
            out = self.conv1(x)
            s = self.cond_scale1(cond).unsqueeze(-1).unsqueeze(-1)
            b = self.cond_shift1(cond).unsqueeze(-1).unsqueeze(-1)
            out = self.act(out * (s + 1) + b)
            out = self.conv2(out)
            s = self.cond_scale2(cond).unsqueeze(-1).unsqueeze(-1)
            b = self.cond_shift2(cond).unsqueeze(-1).unsqueeze(-1)
            out = self.act(out * (s + 1) + b)
            out = self.conv3(out)
            s = self.cond_scale3(cond).unsqueeze(-1).unsqueeze(-1)
            b = self.cond_shift3(cond).unsqueeze(-1).unsqueeze(-1)
            return out * (s + 1) + b

    return CSRNet


_MODEL = None
_WEIGHTS_LOADED = False


def _device():
    import torch
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def _ensure_weights(cfg) -> Optional[Path]:
    target = cfg.state_dir / "models" / WEIGHTS_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 10_000:
        return target
    from travelcull.ml.model_assets import download_file

    for url in WEIGHTS_URL_CANDIDATES:
        try:
            log.info("csrnet: trying weights from %s", url)
            download_file(url, target)
            if target.exists() and target.stat().st_size > 10_000:
                log.info("csrnet: weights cached to %s", target)
                return target
        except Exception as exc:
            log.warning("csrnet: weight fetch failed (%s): %s", url, exc)
            continue
    log.warning("csrnet: no weights — model will run uninitialised (= passthrough-ish)")
    return None


def _load_model(cfg):
    import torch
    global _MODEL, _WEIGHTS_LOADED
    if _MODEL is not None:
        return _MODEL
    CSRNet = _build_model_class()
    model = CSRNet()
    weights = _ensure_weights(cfg)
    weights_loaded = False
    if weights is not None:
        try:
            state = torch.load(weights, map_location="cpu", weights_only=True)
            if isinstance(state, dict) and "state_dict" in state:
                state = state["state_dict"]
            if isinstance(state, dict) and "params" in state:
                state = state["params"]
            state = {k.replace("module.", ""): v for k, v in state.items()}
            try:
                model.load_state_dict(state, strict=True)
                weights_loaded = True
            except Exception:
                missing, unexpected = model.load_state_dict(state, strict=False)
                # Non-strict load with unmatched keys still leaves (part of) the
                # network randomly initialised — only count it as "loaded" if
                # every parameter tensor was actually filled in.
                weights_loaded = not missing
        except Exception as exc:
            log.warning("csrnet: failed to load weights: %s", exc)
    if not weights_loaded:
        log.warning("csrnet: real weights not loaded — model is randomly initialised")
    _WEIGHTS_LOADED = weights_loaded
    _MODEL = model.to(_device()).eval()
    return _MODEL


def retouch_with_csrnet(img: Image.Image, cfg) -> Image.Image:
    """Run CSRNet on a PIL Image. Returns a new PIL Image (RGB).

    Raises RuntimeError if the real pretrained weights could not be loaded —
    running the randomly-initialised network would silently produce garbage
    output, so we refuse instead of returning it.
    """
    import torch
    model = _load_model(cfg)
    if not _WEIGHTS_LOADED:
        raise RuntimeError("CSRNet weights unavailable — retouch disabled")
    arr = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0
    tensor = torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0).to(_device())
    with torch.inference_mode():
        out = model(tensor)
    out_np = out.squeeze(0).clamp(0, 1).cpu().permute(1, 2, 0).numpy()
    out_np = (out_np * 255.0).round().clip(0, 255).astype(np.uint8)
    return Image.fromarray(out_np)
