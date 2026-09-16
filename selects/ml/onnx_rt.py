"""Shared ONNX Runtime helpers.

Picks the best execution provider available in the *installed* onnxruntime so a
single ``.onnx`` model runs GPU-accelerated on Windows (CUDA), macOS (CoreML)
and Linux/NVIDIA (CUDA), falling back to CPU everywhere.

Packaging-agnostic on purpose: we intersect a priority list with
``onnxruntime.get_available_providers()``, so swapping the bundled runtime
(``onnxruntime-gpu`` vs ``-directml`` vs plain ``onnxruntime``) needs no code
change — the provider simply appears (or doesn't) and we adapt.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from typing import Sequence

log = logging.getLogger(__name__)

_GPU_DLLS_READY = False
_GPU_DLLS_LOCK = threading.Lock()


def cuda_dll_dirs() -> list[Path]:
    """Directories that contain CUDA/cuDNN/cuBLAS shared libraries.

    Search order: NVIDIA pip wheels (``nvidia/*/bin``), ``CUDA_PATH``, then
    ``torch/lib`` only if torch is already imported. Selects does not depend
    on torch; the official extra is ``onnxruntime-gpu[cuda,cudnn]`` +
    ``nvidia-cublas``.
    """
    dirs: list[Path] = []
    try:
        import site

        roots: list[str] = []
        try:
            roots.extend(site.getsitepackages())
        except Exception:
            pass
        try:
            roots.append(site.getusersitepackages())
        except Exception:
            pass
        for root in roots:
            nvidia = Path(root) / "nvidia"
            if not nvidia.is_dir():
                continue
            for bin_dir in list(nvidia.glob("*/bin")) + list(nvidia.glob("*/*/bin")):
                if bin_dir.is_dir():
                    dirs.append(bin_dir)
    except Exception:
        pass
    for key in ("CUDA_PATH", "CUDA_HOME"):
        env = os.environ.get(key)
        if env:
            bin_dir = Path(env) / "bin"
            if bin_dir.is_dir():
                dirs.append(bin_dir)
    torch_mod = sys.modules.get("torch")
    if torch_mod is not None:
        try:
            lib = Path(torch_mod.__file__).resolve().parent / "lib"
            if lib.is_dir():
                dirs.append(lib)
        except Exception:
            pass
    unique: list[Path] = []
    seen: set[Path] = set()
    for item in dirs:
        try:
            resolved = item.resolve()
        except OSError:
            continue
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def _register_dll_dir(path: Path) -> None:
    try:
        os.add_dll_directory(str(path))
    except (AttributeError, OSError):
        pass
    os.environ["PATH"] = str(path) + os.pathsep + os.environ.get("PATH", "")


def _preload_gpu_dlls() -> None:
    """Make CUDAExecutionProvider actually load.

    ``onnxruntime-gpu`` 1.27+ *advertises* CUDA even when ``cublasLt64_13.dll``
    is missing, then silently binds CPU. Official fix: install
    ``onnxruntime-gpu[cuda,cudnn]`` + ``nvidia-cublas`` and call
    ``ort.preload_dlls()``. We also put those wheel ``bin`` dirs on the DLL
    search path before creating a session.
    """
    global _GPU_DLLS_READY
    if _GPU_DLLS_READY:
        return
    with _GPU_DLLS_LOCK:
        if _GPU_DLLS_READY:
            return
        dirs = cuda_dll_dirs()
        if not dirs:
            # Last resort: importing torch loads CUDA 13 DLLs from torch/lib.
            try:
                import torch  # noqa: F401

                dirs = cuda_dll_dirs()
            except Exception:
                pass
        for path in dirs:
            _register_dll_dir(path)
        try:
            import onnxruntime as ort

            if hasattr(ort, "preload_dlls"):
                try:
                    # "" = nvidia site-packages (not torch). Dirs are already on PATH.
                    ort.preload_dlls(cuda=True, cudnn=True, directory="")
                except TypeError:
                    ort.preload_dlls()
        except Exception as exc:
            log.debug("ORT preload_dlls skipped: %s", exc)
        _GPU_DLLS_READY = True

# Logical ORT session name -> (manifest asset id, relative path inside that
# asset's cache folder). Graphs come from different HF repos; model_assets owns
# the download, this map is how inference finds the file.
_MODEL_SPECS: dict[str, tuple[str, str]] = {
    "siglip_vision": ("siglip2", "onnx/vision_model_fp16.onnx"),
    "siglip_text": ("siglip2", "onnx/text_model_fp16.onnx"),
    "ram_plus": ("ram_plus", "ram_plus.onnx"),
    "hyperiqa": ("hyperiqa", "hyperiqa_model.onnx"),
}

# Back-compat alias used by a few RAM helpers.
HF_ONNX_REPO = "bihanikeshav/selects-onnx"

# First available wins. CPU is always appended as the last-resort fallback.
_EP_PRIORITY: tuple[str, ...] = (
    "CUDAExecutionProvider",    # NVIDIA (Linux / Windows, onnxruntime-gpu)
    "DmlExecutionProvider",     # any DX12 GPU (Windows, onnxruntime-directml)
    "CoreMLExecutionProvider",  # Apple Silicon GPU / ANE (macOS)
    "CPUExecutionProvider",
)

_SESSIONS: dict[str, "object"] = {}
_SESSIONS_LOCK = threading.Lock()


def available_providers() -> list[str]:
    _preload_gpu_dlls()
    import onnxruntime as ort

    return list(ort.get_available_providers())


def select_providers(prefer: Sequence[str] | None = None) -> list[str]:
    """Return the providers to use, highest-priority-available first, CPU last."""
    avail = set(available_providers())
    order = tuple(prefer) if prefer else _EP_PRIORITY
    chosen = [ep for ep in order if ep in avail]
    if "CPUExecutionProvider" not in chosen:
        chosen.append("CPUExecutionProvider")
    return chosen


class _ResilientSession:
    """ORT session that falls back to CPU when the GPU provider fails at runtime.

    DirectML (and, less often, other GPU EPs) have incomplete op coverage — e.g.
    DML cannot run the Reshape in the SigLIP/RAM++ transformer graphs and throws a
    RUNTIME_EXCEPTION mid-run. Rather than crash the request, we transparently
    rebuild the session on CPU (the models are all CPU-parity-verified) and use
    CPU for that model from then on. Conv nets that DML handles keep the GPU.
    """

    def __init__(self, path: str, providers: list[str]):
        self._path = path
        self._providers = providers
        self._sess = None       # active underlying InferenceSession
        self._cpu_only = False
        self._build_lock = threading.Lock()
        self._run_lock = threading.Lock()

    def _build(self, cpu_only: bool):
        import onnxruntime as ort

        _preload_gpu_dlls()
        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        candidates = ["CPUExecutionProvider"] if cpu_only else list(self._providers)
        if "CPUExecutionProvider" not in candidates:
            candidates.append("CPUExecutionProvider")
        last_err: Exception | None = None
        for index, wanted in enumerate(candidates):
            provs = candidates[index:]
            try:
                s = ort.InferenceSession(self._path, sess_options=so, providers=provs)
            except Exception as exc:
                last_err = exc
                log.warning(
                    "ONNX session %s failed on %s (%s)",
                    Path(self._path).name, wanted, type(exc).__name__,
                )
                continue
            got = list(s.get_providers())
            if wanted not in got:
                log.warning(
                    "ONNX session %s requested %s but bound %s; trying next EP",
                    Path(self._path).name, wanted, got,
                )
                continue
            log.info("ONNX session %s on %s", Path(self._path).name, got)
            if wanted == "CPUExecutionProvider":
                self._cpu_only = True
            return s
        if last_err is not None:
            raise last_err
        raise RuntimeError(f"no ONNX execution provider could load {self._path}")

    def _session(self):
        if self._sess is not None:
            return self._sess
        with self._build_lock:
            if self._sess is None:
                self._sess = self._build(self._cpu_only)
            return self._sess

    def run(self, output_names, input_feed, run_options=None):
        with self._run_lock:
            try:
                return self._session().run(output_names, input_feed, run_options)
            except Exception as exc:
                if self._cpu_only:
                    raise
                log.warning(
                    "ONNX run failed on %s for %s (%s); falling back to CPU for this model",
                    self._providers, Path(self._path).name, type(exc).__name__,
                )
                self._cpu_only = True
                self._sess = self._build(True)
                return self._sess.run(output_names, input_feed, run_options)

    def __getattr__(self, name):
        # Delegate everything else (get_inputs/get_outputs/get_providers/...).
        return getattr(self._session(), name)


def make_session(onnx_path, prefer: Sequence[str] | None = None, cache: bool = True):
    """Build (and optionally cache) a CPU-fallback ORT session on the best EP."""
    key = str(Path(onnx_path).resolve())
    if cache and key in _SESSIONS:
        return _SESSIONS[key]
    with _SESSIONS_LOCK:
        if cache and key in _SESSIONS:
            return _SESSIONS[key]
        sess = _ResilientSession(key, select_providers(prefer))
        if cache:
            _SESSIONS[key] = sess
        return sess


def _onnx_dir() -> Path:
    """RAM++ cache folder (tags/meta live next to the graph)."""
    from selects.ml.model_assets import asset_dir

    d = asset_dir("ram_plus")
    d.mkdir(parents=True, exist_ok=True)
    return d


_PROVIDER_LABELS = {
    "CUDAExecutionProvider": "NVIDIA CUDA",
    "DmlExecutionProvider": "DirectML (Windows GPU, no CUDA)",
    "CoreMLExecutionProvider": "CoreML (Apple GPU, no CUDA)",
    "CPUExecutionProvider": "CPU",
}


def runtime_info() -> dict:
    """What this install can run graphs on. CUDA is optional, not required."""
    try:
        providers = available_providers()
        selected = select_providers()
    except Exception:
        providers = ["CPUExecutionProvider"]
        selected = ["CPUExecutionProvider"]
    primary = selected[0] if selected else "CPUExecutionProvider"
    return {
        "installed_providers": providers,
        "selected": selected,
        "device": _PROVIDER_LABELS.get(primary, primary),
        "cuda_required": False,
        "gpu_without_cuda": primary in {"DmlExecutionProvider", "CoreMLExecutionProvider"},
        "using_cuda": primary == "CUDAExecutionProvider",
        "note": (
            "ONNX Runtime uses CUDA if the GPU wheel is installed and CUDA 13 "
            "DLLs are visible (torch/lib or nvidia-cublas). DirectML/CoreML "
            "are GPU paths that do not need CUDA. insightface and "
            "faster-whisper pull the CPU onnxruntime wheel — reinstall "
            "onnxruntime-gpu after them if CUDA disappears."
        ),
    }


_FILE_CACHE: dict[str, str] = {}


def repo_file(filename: str) -> str:
    """Local path for a RAM++ sidecar (tags/meta) next to the graph."""
    cached = _FILE_CACHE.get(filename)
    if cached is not None and Path(cached).exists():
        return cached
    local = _onnx_dir() / filename
    if local.exists() and local.stat().st_size > 0:
        path = str(local)
    else:
        from selects.ml.model_assets import download_one

        download_one("ram_plus")
        path = str(_onnx_dir() / filename)
    _FILE_CACHE[filename] = path
    return path


def model_path(name: str) -> str:
    """Return the local .onnx path for a logical model, downloading if needed."""
    asset_id, rel = _MODEL_SPECS[name]
    from selects.ml.model_assets import asset_dir, asset_present, download_one, MANIFEST

    path = asset_dir(asset_id) / rel
    if not path.exists() or path.stat().st_size <= 0:
        asset = next(item for item in MANIFEST if item["id"] == asset_id)
        if not asset_present(asset):
            download_one(asset_id)
        path = asset_dir(asset_id) / rel
    return str(path)


def model_session(name: str, prefer: Sequence[str] | None = None, cache: bool = True):
    """Download logical model *name* and build a cached ORT session on the best EP."""
    return make_session(model_path(name), prefer=prefer, cache=cache)


def all_present() -> bool:
    """True if the SigLIP 2 towers (search/embed) are on disk."""
    from selects.ml.model_assets import MANIFEST, asset_present

    asset = next((item for item in MANIFEST if item["id"] == "siglip2"), None)
    return bool(asset) and asset_present(asset)


def pooled_output(sess, feed: dict) -> "object":
    """Return the [B, D] embedding from a vision/text tower.

    Transformers.js SigLIP 2 graphs emit ``last_hidden_state`` first and
    ``pooler_output`` second. Taking output 0 would be a sequence tensor.
    """
    names = [out.name for out in sess.get_outputs()]
    outs = sess.run(None, feed)
    by_name = dict(zip(names, outs))
    for key in ("pooler_output", "image_embeds", "text_embeds"):
        arr = by_name.get(key)
        if arr is not None and getattr(arr, "ndim", 0) == 2:
            return arr
    for arr in outs:
        if getattr(arr, "ndim", 0) == 2:
            return arr
    for arr in outs:
        if getattr(arr, "ndim", 0) == 3:
            return arr.mean(axis=1)
    raise RuntimeError(f"no pooled embedding in outputs {names}")
