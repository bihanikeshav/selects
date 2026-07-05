"""NAFNet — Nonlinear Activation Free Network for image deblurring.

Vendored from https://github.com/megvii-research/NAFNet (CVPR 2022 oral,
arXiv:2204.04676). Achieves 33.69 dB PSNR on the GoPro benchmark using
8.4% of the FLOPs of the previous SOTA — a real "drop-in deblur" model.

We use the GoPro-trained width=32 variant (~17M params, ~70 MB weights),
downloaded from a HuggingFace mirror. The width=64 variant is more
accurate but ~270 MB; not worth the bytes for our use case.

NOTE: Newer architectures exist (FFTformer, LoFormer, MambaIR) that
benchmark slightly higher, but NAFNet remains the best speed/quality
balance with widely available pretrained weights. See README for a
comparison table.
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

WEIGHTS_URL = (
    "https://huggingface.co/mikestealth/nafnet-models/resolve/main/"
    "NAFNet-GoPro-width32.pth"
)
WEIGHTS_FILENAME = "nafnet_gopro_width32.pth"

# GoPro-width32 config — matches the official upstream training recipe.
ENC_BLKS = [1, 1, 1, 28]
MIDDLE_BLK_NUM = 1
DEC_BLKS = [1, 1, 1, 1]
WIDTH = 32


def _build_model_classes():
    """Construct NAFNet classes lazily so importing this module doesn't
    eagerly load torch (FastAPI worker-thread DLL-load workaround)."""
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    class LayerNormFunction(torch.autograd.Function):
        @staticmethod
        def forward(ctx, x, weight, bias, eps):
            ctx.eps = eps
            N, C, H, W = x.size()
            mu = x.mean(1, keepdim=True)
            var = (x - mu).pow(2).mean(1, keepdim=True)
            y = (x - mu) / (var + eps).sqrt()
            ctx.save_for_backward(y, var, weight)
            return weight.view(1, C, 1, 1) * y + bias.view(1, C, 1, 1)

        @staticmethod
        def backward(ctx, grad_output):
            # Not used in inference but kept for parity with upstream.
            eps = ctx.eps
            N, C, H, W = grad_output.size()
            y, var, weight = ctx.saved_tensors
            g = grad_output * weight.view(1, C, 1, 1)
            mean_g = g.mean(dim=1, keepdim=True)
            mean_gy = (g * y).mean(dim=1, keepdim=True)
            gx = 1.0 / torch.sqrt(var + eps) * (g - y * mean_gy - mean_g)
            return (
                gx,
                (grad_output * y).sum(dim=3).sum(dim=2).sum(dim=0),
                grad_output.sum(dim=3).sum(dim=2).sum(dim=0),
                None,
            )

    class LayerNorm2d(nn.Module):
        def __init__(self, channels, eps=1e-6):
            super().__init__()
            self.register_parameter("weight", nn.Parameter(torch.ones(channels)))
            self.register_parameter("bias", nn.Parameter(torch.zeros(channels)))
            self.eps = eps

        def forward(self, x):
            return LayerNormFunction.apply(x, self.weight, self.bias, self.eps)

    class SimpleGate(nn.Module):
        def forward(self, x):
            x1, x2 = x.chunk(2, dim=1)
            return x1 * x2

    class NAFBlock(nn.Module):
        def __init__(self, c, DW_Expand=2, FFN_Expand=2, drop_out_rate=0.0):
            super().__init__()
            dw_channel = c * DW_Expand
            self.conv1 = nn.Conv2d(c, dw_channel, 1, 1, 0, bias=True)
            self.conv2 = nn.Conv2d(
                dw_channel, dw_channel, 3, 1, 1, groups=dw_channel, bias=True
            )
            self.conv3 = nn.Conv2d(dw_channel // 2, c, 1, 1, 0, bias=True)

            self.sca = nn.Sequential(
                nn.AdaptiveAvgPool2d(1),
                nn.Conv2d(dw_channel // 2, dw_channel // 2, 1, 1, 0, bias=True),
            )

            self.sg = SimpleGate()
            ffn_channel = FFN_Expand * c
            self.conv4 = nn.Conv2d(c, ffn_channel, 1, 1, 0, bias=True)
            self.conv5 = nn.Conv2d(ffn_channel // 2, c, 1, 1, 0, bias=True)

            self.norm1 = LayerNorm2d(c)
            self.norm2 = LayerNorm2d(c)

            self.dropout1 = nn.Dropout(drop_out_rate) if drop_out_rate > 0 else nn.Identity()
            self.dropout2 = nn.Dropout(drop_out_rate) if drop_out_rate > 0 else nn.Identity()

            self.beta = nn.Parameter(torch.zeros((1, c, 1, 1)), requires_grad=True)
            self.gamma = nn.Parameter(torch.zeros((1, c, 1, 1)), requires_grad=True)

        def forward(self, inp):
            x = self.norm1(inp)
            x = self.conv1(x)
            x = self.conv2(x)
            x = self.sg(x)
            x = x * self.sca(x)
            x = self.conv3(x)
            x = self.dropout1(x)
            y = inp + x * self.beta

            x = self.conv4(self.norm2(y))
            x = self.sg(x)
            x = self.conv5(x)
            x = self.dropout2(x)
            return y + x * self.gamma

    class NAFNet(nn.Module):
        def __init__(self, img_channel=3, width=16, middle_blk_num=1,
                     enc_blk_nums=(), dec_blk_nums=()):
            super().__init__()
            self.intro = nn.Conv2d(img_channel, width, 3, 1, 1, bias=True)
            self.ending = nn.Conv2d(width, img_channel, 3, 1, 1, bias=True)

            self.encoders = nn.ModuleList()
            self.decoders = nn.ModuleList()
            self.middle_blks = nn.ModuleList()
            self.ups = nn.ModuleList()
            self.downs = nn.ModuleList()

            chan = width
            for num in enc_blk_nums:
                self.encoders.append(
                    nn.Sequential(*[NAFBlock(chan) for _ in range(num)])
                )
                self.downs.append(nn.Conv2d(chan, 2 * chan, 2, 2))
                chan = chan * 2

            self.middle_blks = nn.Sequential(
                *[NAFBlock(chan) for _ in range(middle_blk_num)]
            )

            for num in dec_blk_nums:
                self.ups.append(
                    nn.Sequential(
                        nn.Conv2d(chan, chan * 2, 1, bias=False),
                        nn.PixelShuffle(2),
                    )
                )
                chan = chan // 2
                self.decoders.append(
                    nn.Sequential(*[NAFBlock(chan) for _ in range(num)])
                )

            self.padder_size = 2 ** len(self.encoders)

        def forward(self, inp):
            B, C, H, W = inp.shape
            inp = self._check_image_size(inp)
            x = self.intro(inp)

            encs = []
            for encoder, down in zip(self.encoders, self.downs):
                x = encoder(x)
                encs.append(x)
                x = down(x)

            x = self.middle_blks(x)

            for decoder, up, enc_skip in zip(self.decoders, self.ups, encs[::-1]):
                x = up(x)
                x = x + enc_skip
                x = decoder(x)

            x = self.ending(x)
            x = x + inp
            return x[:, :, :H, :W]

        def _check_image_size(self, x):
            _, _, h, w = x.size()
            mod_pad_h = (self.padder_size - h % self.padder_size) % self.padder_size
            mod_pad_w = (self.padder_size - w % self.padder_size) % self.padder_size
            return F.pad(x, (0, mod_pad_w, 0, mod_pad_h))

    return NAFNet


_MODEL = None


def _device():
    import torch
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def _ensure_weights(cfg) -> Optional[Path]:
    target = cfg.state_dir / "models" / WEIGHTS_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 1_000_000:
        return target
    import urllib.request
    log.info("nafnet: downloading weights (~70 MB) from %s", WEIGHTS_URL)
    try:
        urllib.request.urlretrieve(WEIGHTS_URL, target)
        return target
    except Exception as exc:
        log.warning("nafnet: weight download failed: %s", exc)
        return None


def _load_model(cfg):
    import torch
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    weights = _ensure_weights(cfg)
    if weights is None:
        raise RuntimeError(
            "NAFNet weights download failed. Manually fetch from "
            f"{WEIGHTS_URL} into {cfg.state_dir / 'models' / WEIGHTS_FILENAME}"
        )

    NAFNet = _build_model_classes()
    model = NAFNet(
        img_channel=3, width=WIDTH,
        middle_blk_num=MIDDLE_BLK_NUM,
        enc_blk_nums=ENC_BLKS, dec_blk_nums=DEC_BLKS,
    )
    state = torch.load(weights, map_location="cpu", weights_only=True)
    # Upstream stores params under "params" key in their training-checkpoint
    # format; HF mirror exports just the raw state dict — handle both.
    if isinstance(state, dict) and "params" in state:
        state = state["params"]
    state = {k.replace("module.", ""): v for k, v in state.items()}
    try:
        model.load_state_dict(state, strict=True)
    except Exception as exc:
        log.warning("nafnet: strict load failed (%s) — trying non-strict", exc)
        model.load_state_dict(state, strict=False)
    _MODEL = model.to(_device()).eval()
    return _MODEL


def deblur_with_nafnet(img: Image.Image, cfg) -> Image.Image:
    """Run NAFNet GoPro deblurring on a PIL Image. Returns a new RGB Image."""
    import torch
    model = _load_model(cfg)
    arr = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0
    tensor = torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0).to(_device())
    with torch.inference_mode():
        out = model(tensor)
    out_np = out.squeeze(0).clamp(0, 1).cpu().permute(1, 2, 0).numpy()
    out_np = (out_np * 255.0).round().clip(0, 255).astype(np.uint8)
    return Image.fromarray(out_np)
