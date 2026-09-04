"""GPU capability detection for selects.

Reports which ONNX Runtime execution provider SigLIP (the app's primary model)
will actually use. All ML runs on ORT — there is no torch. ``gpu_available`` is
True only when SigLIP would run on a non-CPU EP; today the published SigLIP
graphs are CPU-only (DirectML cannot run their Reshape pattern).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Friendly names for the execution providers we prioritise in onnx_rt.
_PROVIDER_LABELS = {
    "CUDAExecutionProvider": "CUDA (NVIDIA)",
    "DmlExecutionProvider": "DirectML (DX12 GPU)",
    "CoreMLExecutionProvider": "CoreML (Apple)",
    "CPUExecutionProvider": "CPU",
}


@dataclass
class GpuCapabilities:
    """Snapshot of the active ONNX Runtime provider + hardware codec features."""

    gpu_available: bool = False
    provider: Optional[str] = None          # EP SigLIP would actually use
    device_name: Optional[str] = None       # friendly provider label
    vram_total_mb: Optional[int] = None      # only when an NVML probe succeeds
    installed_providers: list[str] = field(default_factory=list)  # EPs in this ORT build

    # Hardware JPEG/HEIC decode via nvidia-nvimgcodec
    nvimgcodec_available: bool = False
    # OpenCV CUDA support
    cv2_cuda_available: bool = False


def detect_capabilities() -> GpuCapabilities:
    """Detect the ONNX Runtime provider the app will use and return a snapshot."""
    caps = GpuCapabilities()

    # ------------------------------------------------------------------ #
    # Execution provider via onnxruntime (the one thing that runs models) #
    # ------------------------------------------------------------------ #
    try:
        from selects.ml import onnx_rt  # noqa: PLC0415

        caps.installed_providers = list(onnx_rt.available_providers())
        # Honest: SigLIP is in _CPU_ONLY_MODELS, so the EP the app actually uses
        # for embeddings is CPU even when DML/CUDA is installed.
        siglip_cpu_only = (
            "siglip_vision" in onnx_rt._CPU_ONLY_MODELS
            or "siglip_text" in onnx_rt._CPU_ONLY_MODELS
        )
        if siglip_cpu_only:
            caps.provider = "CPUExecutionProvider"
            caps.gpu_available = False
            caps.device_name = _PROVIDER_LABELS["CPUExecutionProvider"]
        else:
            providers = onnx_rt.select_providers()
            caps.provider = providers[0] if providers else None
            caps.gpu_available = bool(caps.provider) and caps.provider != "CPUExecutionProvider"
            caps.device_name = _PROVIDER_LABELS.get(caps.provider, caps.provider)
    except Exception:
        pass

    # ------------------------------------------------------------------ #
    # Optional NVIDIA VRAM readout (nvidia-ml-py); best-effort, no torch   #
    # ------------------------------------------------------------------ #
    try:
        import pynvml  # noqa: PLC0415

        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        caps.vram_total_mb = pynvml.nvmlDeviceGetMemoryInfo(h).total // (1024 * 1024)
        if not caps.device_name or caps.device_name.startswith("CUDA"):
            caps.device_name = pynvml.nvmlDeviceGetName(h)
        pynvml.nvmlShutdown()
    except Exception:
        pass

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
