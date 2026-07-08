"""Shared ONNX Runtime helpers.

Picks the best execution provider available in the *installed* onnxruntime so a
single ``.onnx`` model runs GPU-accelerated on Windows (DirectML), macOS
(CoreML) and Linux/NVIDIA (CUDA), falling back to CPU everywhere.

Packaging-agnostic on purpose: we intersect a priority list with
``onnxruntime.get_available_providers()``, so swapping the bundled runtime
(``onnxruntime-directml`` vs ``-gpu`` vs plain ``onnxruntime``) needs no code
change — the provider simply appears (or doesn't) and we adapt.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Sequence

log = logging.getLogger(__name__)

# All ONNX weights live in one public HF repo (see scratchpad upload_hf.py).
HF_ONNX_REPO = "bihanikeshav/selects-onnx"

# Logical model name -> files to fetch. The first entry is the graph passed to
# make_session; the rest are external-data siblings that ORT resolves by bare
# basename from the same directory, so they must be co-located (hf_hub_download
# places every file from a repo into the same snapshot dir, so they are).
_MODEL_FILES: dict[str, tuple[str, ...]] = {
    "siglip_vision": ("siglip_vision.onnx",),          # fp16, self-contained
    "siglip_text": ("siglip_text.onnx",),              # fp16, self-contained
    "ram_plus": ("ram_plus.onnx", "ram_plus.onnx.data"),   # fp32 + external data
    # NOTE: all ML enhancers were retired. csrnet/zero_dce produced broken output;
    # nafnet was weak; Restormer (deblur) needed GBs of activation memory; and
    # Retinexformer (low-light) was dropped in favour of the classical auto_tone,
    # which looks good and is instant. Their .onnx files stay in the HF repo (not
    # downloaded) if we ever revisit them. Enhancement is now purely classical.
}

# First available wins. CPU is always appended as the last-resort fallback.
_EP_PRIORITY: tuple[str, ...] = (
    "CUDAExecutionProvider",    # NVIDIA (Linux / Windows, onnxruntime-gpu)
    "DmlExecutionProvider",     # any DX12 GPU (Windows, onnxruntime-directml)
    "CoreMLExecutionProvider",  # Apple Silicon GPU / ANE (macOS)
    "CPUExecutionProvider",
)

_SESSIONS: dict[str, "object"] = {}


def available_providers() -> list[str]:
    import onnxruntime as ort

    return list(ort.get_available_providers())


def select_providers(prefer: Sequence[str] | None = None) -> list[str]:
    """Return the providers to use, highest-priority-available first, CPU last."""
    import onnxruntime as ort

    avail = set(ort.get_available_providers())
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

    def _build(self, cpu_only: bool):
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        provs = ["CPUExecutionProvider"] if cpu_only else self._providers
        s = ort.InferenceSession(self._path, sess_options=so, providers=provs)
        log.info("ONNX session %s on %s", Path(self._path).name, s.get_providers())
        return s

    def _session(self):
        if self._sess is None:
            self._sess = self._build(self._cpu_only)
        return self._sess

    def run(self, output_names, input_feed, run_options=None):
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

    sess = _ResilientSession(key, select_providers(prefer))
    if cache:
        _SESSIONS[key] = sess
    return sess


def _onnx_dir() -> Path:
    """Flat local dir for the downloaded ONNX files.

    We download with ``local_dir`` (real filenames, co-located) rather than the
    default HF blob cache: an external-data ``.onnx`` references its ``.data`` by
    bare basename, and ORT resolves that against the graph's directory — which in
    the blob cache is a hashed ``blobs/`` path where the sibling name doesn't exist.
    """
    env = os.environ.get("SELECTS_MODELS_DIR")
    base = Path(env) if env else Path.home() / ".cache" / "selects" / "models"
    d = base / "selects-onnx"
    d.mkdir(parents=True, exist_ok=True)
    return d


_FILE_CACHE: dict[str, str] = {}


def repo_file(filename: str) -> str:
    """Download a single file from the selects-onnx HF repo, return local path.

    Used for the ONNX graphs, their external-data siblings, and companion assets
    (ram_meta.npz, ram_tags.json, the SigLIP tokenizer). Downloaded into a flat
    local dir so external-data references resolve.

    The resolved path is cached per process: without this, every call re-runs
    ``hf_hub_download``, which does a network HEAD to check the etag even for an
    already-cached file — so each search query spammed a HEAD request to HF.
    """
    cached = _FILE_CACHE.get(filename)
    if cached is not None and Path(cached).exists():
        return cached

    from huggingface_hub import hf_hub_download

    local = _onnx_dir() / filename
    if local.exists() and local.stat().st_size > 0:
        # Already downloaded — trust it and skip the network entirely.
        path = str(local)
    else:
        path = hf_hub_download(HF_ONNX_REPO, filename, local_dir=str(_onnx_dir()))
    _FILE_CACHE[filename] = path
    return path


def model_path(name: str) -> str:
    """Fetch a logical model's file(s) from HF and return its local .onnx path.

    Downloads external-data siblings too (into the same snapshot dir) so ORT can
    resolve them. ``name`` is a key of ``_MODEL_FILES``.
    """
    files = _MODEL_FILES[name]
    graph_path: str | None = None
    for fn in files:
        p = repo_file(fn)
        if graph_path is None:
            graph_path = p  # the .onnx graph is always first
    assert graph_path is not None
    return graph_path


# The currently-published SigLIP/RAM++ graphs were dynamo-exported, and DirectML
# cannot run their Reshape pattern (it throws mid-run). Rather than attempt DML,
# fail, freeze, and fall back per request, we build these directly on CPU. (The
# conv nets run fine on DML.) If these are ever re-exported with the legacy
# TorchScript exporter — which produces DML-compatible graphs — drop them from
# this set to get GPU acceleration back.
_CPU_ONLY_MODELS = {"siglip_text", "siglip_vision", "ram_plus"}


def model_session(name: str, prefer: Sequence[str] | None = None, cache: bool = True):
    """Convenience: download logical model *name* and build a cached ORT session."""
    if prefer is None and name in _CPU_ONLY_MODELS:
        prefer = ["CPUExecutionProvider"]
    return make_session(model_path(name), prefer=prefer, cache=cache)


# Every file the app needs from the repo: model graphs + external data + the
# RAM++ post-processing metadata + the SigLIP tokenizer. Used by model_assets to
# pre-fetch/verify the whole bundle in one place.
ALL_FILES: tuple[str, ...] = tuple(
    f for files in _MODEL_FILES.values() for f in files
) + ("ram_meta.npz", "ram_tags.json", "spiece.model")


def all_present() -> bool:
    """True if every bundle file is already downloaded to the local onnx dir."""
    d = _onnx_dir()
    return all((d / f).exists() and (d / f).stat().st_size > 0 for f in ALL_FILES)


def ensure_all(progress=None) -> None:
    """Download every bundle file (skips those already present). ``progress`` is
    an optional callback receiving ``(index, total, filename)`` before each fetch."""
    total = len(ALL_FILES)
    for i, fn in enumerate(ALL_FILES, 1):
        if progress is not None:
            progress(i, total, fn)
        repo_file(fn)
