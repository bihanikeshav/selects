"""GPU capability detection for selects.

Reports which ONNX Runtime execution provider SigLIP 2 (the app's primary model)
will actually use. All vision graphs run on ORT — there is no torch.
``gpu_available`` is True when the selected EP is not CPU.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# insightface / faster-whisper depend on the CPU ``onnxruntime`` wheel, which
# overwrites ``onnxruntime-gpu``. ``selects doctor --fix`` reinstalls these.
GPU_ORT_PACKAGES = (
    "onnxruntime-gpu[cuda,cudnn]>=1.24",
    "nvidia-cublas~=13.0",
)

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


def nvidia_gpu_present() -> bool:
    """True when an NVIDIA GPU is visible (NVML or nvidia-smi)."""
    try:
        import pynvml

        pynvml.nvmlInit()
        count = pynvml.nvmlDeviceGetCount()
        pynvml.nvmlShutdown()
        return count > 0
    except Exception:
        pass
    try:
        import subprocess

        proc = subprocess.run(
            ["nvidia-smi", "-L"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return proc.returncode == 0 and "GPU" in (proc.stdout or "")
    except Exception:
        return False


def cpu_ort_hiding_nvidia() -> bool:
    """NVIDIA GPU is present but this ORT build has no CUDA execution provider."""
    if not nvidia_gpu_present():
        return False
    try:
        from selects.ml.onnx_rt import available_providers

        return "CUDAExecutionProvider" not in available_providers()
    except Exception:
        return True


def repair_gpu_runtime() -> None:
    """Uninstall the CPU ``onnxruntime`` wheel and install the CUDA extras.

    insightface and faster-whisper depend on ``onnxruntime``, which pip
    installs *over* ``onnxruntime-gpu``. After ``pip install selects[ml]``
    NVIDIA machines often have a CPU-only ORT until this runs.
    """
    import subprocess
    import sys

    uninstall = subprocess.run(
        [sys.executable, "-m", "pip", "uninstall", "-y", "onnxruntime"],
        check=False,
    )
    if uninstall.returncode != 0:
        raise RuntimeError("pip uninstall onnxruntime failed")
    install = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", *GPU_ORT_PACKAGES],
        check=False,
    )
    if install.returncode != 0:
        raise RuntimeError("pip install onnxruntime-gpu failed")
    from selects.ml import onnx_rt

    onnx_rt._GPU_DLLS_READY = False
    onnx_rt._SESSIONS.clear()
