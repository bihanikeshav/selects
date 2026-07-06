"""GPU capability detection for selects."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GpuCapabilities:
    """Snapshot of available GPU hardware and codec features."""

    cuda_available: bool = False
    device_name: Optional[str] = None
    cuda_capability: Optional[tuple[int, int]] = None  # (major, minor)
    vram_total_mb: Optional[int] = None

    # Hardware video decode via torchcodec
    nvdec_available: bool = False

    # Hardware JPEG/HEIC decode via nvidia-nvimgcodec
    nvimgcodec_available: bool = False

    # OpenCV CUDA support
    cv2_cuda_available: bool = False


def detect_capabilities() -> GpuCapabilities:
    """Detect available GPU capabilities and return a GpuCapabilities snapshot."""
    caps = GpuCapabilities()

    # ------------------------------------------------------------------ #
    # CUDA via torch                                                       #
    # ------------------------------------------------------------------ #
    try:
        import torch  # noqa: PLC0415

        caps.cuda_available = torch.cuda.is_available()
        if caps.cuda_available:
            idx = torch.cuda.current_device()
            caps.device_name = torch.cuda.get_device_name(idx)
            major, minor = torch.cuda.get_device_capability(idx)
            caps.cuda_capability = (major, minor)
            mem = torch.cuda.get_device_properties(idx).total_memory
            caps.vram_total_mb = mem // (1024 * 1024)
    except Exception:
        pass

    # ------------------------------------------------------------------ #
    # torchcodec (NVDEC hardware video decode)                             #
    # ------------------------------------------------------------------ #
    try:
        import torchcodec  # noqa: PLC0415, F401

        caps.nvdec_available = True
    except Exception:
        caps.nvdec_available = False

    # ------------------------------------------------------------------ #
    # nvidia-nvimgcodec (hardware JPEG/HEIC decode)                       #
    # ------------------------------------------------------------------ #
    try:
        import nvidia.nvimgcodec  # noqa: PLC0415, F401

        caps.nvimgcodec_available = True
    except Exception:
        caps.nvimgcodec_available = False

    # ------------------------------------------------------------------ #
    # OpenCV CUDA                                                          #
    # ------------------------------------------------------------------ #
    try:
        import cv2  # noqa: PLC0415

        caps.cv2_cuda_available = cv2.cuda.getCudaEnabledDeviceCount() > 0
    except Exception:
        caps.cv2_cuda_available = False

    return caps
