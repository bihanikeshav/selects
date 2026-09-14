"""First-class model-weights management for selects.

Every ML stage in selects silently downloads several GB of weights on first
use. This module makes those downloads explicit and inspectable:

* ``MANIFEST`` enumerates every model asset the app can fetch, its rough size
  and what feature needs it.
* ``asset_present`` / ``status`` report what is already on disk.
* ``download_all`` fetches everything that is missing, publishing progress in
  the same ``{"stage", "current", "total", "message"}`` shape used by the rest
  of the pipeline.
* ``download_file`` is a hardened HTTP downloader (connect timeout, ``.part``
  temp file + atomic rename, optional sha256 verification) shared with the
  three enhancement modules that used to call ``urllib.request.urlretrieve``
  with no timeout and no checksum.

Kinds of asset:

* ``"hf"``          — a HuggingFace repo, fetched via ``snapshot_download``.
* ``"url"``         — a single weight file downloaded over HTTP.
* ``"insightface"`` — the insightface ``buffalo_l`` model pack.

The ``sha256`` field is populated for ``kind="url"`` assets only, and only when
the file was already present on disk when this manifest was authored. None of
the url weights were cached at authoring time, so every ``sha256`` is currently
``None`` (noted per-asset below); the hardened downloader still verifies any
checksum that is later filled in.
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Manifest                                                                     #
# --------------------------------------------------------------------------- #

# ``ref`` is the HF repo id (kind="hf"), the download URL (kind="url"), or the
# insightface model-pack name/url (kind="insightface"). kind="onnx" is the shared
# selects-onnx bundle managed by selects.ml.onnx_rt (SigLIP + RAM++ + the three
# enhancement nets + tokenizer/metadata) — all ML runs on ONNX Runtime now, so
# there is no bundled torch and no per-model .pth downloads. Sizes are approximate
# download footprints in MB.
MANIFEST: list[dict] = [
    {
        "id": "selects_onnx",
        "name": "selects ONNX models (SigLIP, RAM++, enhancement)",
        "kind": "onnx",
        "ref": "bihanikeshav/selects-onnx",
        "approx_size_mb": 3130,
        "required_for": "photo scoring, tagging, enhancement",
        "sha256": None,  # n/a for hf assets
    },
    {
        "id": "buffalo_l",
        "name": "InsightFace buffalo_l (face detection + recognition)",
        "kind": "insightface",
        "ref": "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip",
        "approx_size_mb": 330,
        "required_for": "face recognition and video highlight faces",
        "sha256": None,  # insightface manages/verifies its own pack
    },
    {
        "id": "whisper_small_onnx",
        "name": "Whisper small ONNX (word-timed transcript)",
        "kind": "hf",
        "ref": "onnx-community/whisper-small",
        "approx_size_mb": 500,
        "required_for": "video transcript, silence and filler selects",
        "sha256": None,
    },
]


# --------------------------------------------------------------------------- #
# Paths                                                                        #
# --------------------------------------------------------------------------- #

# Every pack lives under models_dir() / <folder>. InsightFace's FaceAnalysis
# API is {root}/models/{name}, so buffalo_l is models_dir()/buffalo_l with
# root = models_dir().parent (the default models_dir is named "models").
_ASSET_FOLDERS = {
    "selects_onnx": "selects-onnx",
    "whisper_small_onnx": "whisper-small",
    "buffalo_l": "buffalo_l",
}

_LEGACY_INSIGHTFACE = Path.home() / ".insightface" / "models" / "buffalo_l"


def models_dir() -> Path:
    """Canonical weights root. Override with ``SELECTS_MODELS_DIR``.

    Default: ``~/.cache/selects/models``. Every pack is a subdirectory:
    ``selects-onnx``, ``whisper-small``, ``buffalo_l``.
    """
    env = os.environ.get("SELECTS_MODELS_DIR")
    if env:
        return Path(env)
    return Path.home() / ".cache" / "selects" / "models"


def asset_dir(asset_id: str, base_models_dir: Optional[Path] = None) -> Path:
    """Directory for one manifest asset under the canonical cache."""
    base = Path(base_models_dir) if base_models_dir is not None else models_dir()
    return base / _ASSET_FOLDERS.get(asset_id, asset_id)


def insightface_dir() -> Path:
    """Directory that contains the buffalo_l pack (canonical cache)."""
    return asset_dir("buffalo_l")


def insightface_root(base_models_dir: Optional[Path] = None) -> Path:
    """FaceAnalysis ``root`` so ``{root}/models/buffalo_l`` is ``asset_dir('buffalo_l')``."""
    base = Path(base_models_dir) if base_models_dir is not None else models_dir()
    return base.parent


def _url_target(asset: dict, base: Optional[Path]) -> Path:
    base = base if base is not None else models_dir()
    return base / asset["filename"]


# --------------------------------------------------------------------------- #
# Hardened downloader                                                          #
# --------------------------------------------------------------------------- #

def download_file(
    url: str,
    target: Path | str,
    sha256: Optional[str] = None,
    timeout: float = 30.0,
    chunk_size: int = 1 << 20,
) -> Path:
    """Download *url* to *target* safely.

    Hardened compared to ``urllib.request.urlretrieve``:

    * a connect/read timeout so a dead host can't hang the worker forever,
    * streamed to a ``<target>.part`` temp file then atomically renamed, so an
      interrupted download never leaves a truncated file at the real path,
    * optional sha256 verification — on mismatch the temp file is removed and a
      clear ``ValueError`` is raised (nothing is written to *target*).

    Returns the final path on success.
    """
    import requests

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")

    hasher = hashlib.sha256()
    try:
        with requests.get(url, stream=True, timeout=timeout) as resp:
            resp.raise_for_status()
            with open(part, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=chunk_size):
                    if chunk:
                        fh.write(chunk)
                        hasher.update(chunk)
    except Exception:
        # Never leave a partial temp file behind on a failed transfer.
        try:
            part.unlink()
        except FileNotFoundError:
            pass
        raise

    if sha256:
        actual = hasher.hexdigest()
        if actual.lower() != sha256.lower():
            try:
                part.unlink()
            except FileNotFoundError:
                pass
            raise ValueError(
                f"sha256 mismatch for {url}: expected {sha256}, got {actual}"
            )

    os.replace(part, target)  # atomic on same filesystem
    return target


# --------------------------------------------------------------------------- #
# Presence checks                                                              #
# --------------------------------------------------------------------------- #

def _hf_repo_cached(repo_id: str) -> bool:
    from huggingface_hub import snapshot_download

    try:
        snapshot_download(repo_id, local_files_only=True)
        return True
    except Exception:
        return False


def asset_cache_path(asset: dict, base_models_dir: Optional[Path] = None) -> str:
    """Directory or file where this asset lives on disk."""
    if asset["kind"] == "url":
        return str(_url_target(asset, base_models_dir))
    return str(asset_dir(asset["id"], base_models_dir))


def asset_present(asset: dict, base_models_dir: Optional[Path] = None) -> bool:
    """Return True if *asset* is already available on disk."""
    kind = asset["kind"]
    if kind == "onnx":
        from selects.ml.onnx_rt import all_present

        return all_present()
    if kind == "hf":
        if asset.get("id") == "whisper_small_onnx":
            from selects.ml.video_speech import whisper_files_present

            return whisper_files_present()
        return _hf_repo_cached(asset["ref"])
    if kind == "insightface":
        pack = asset_dir("buffalo_l", base_models_dir)
        if pack.is_dir() and any(pack.iterdir()):
            return True
        # Existing installs that used InsightFace's default home.
        if base_models_dir is None and _LEGACY_INSIGHTFACE.is_dir():
            return True
        return False
    if kind == "url":
        target = _url_target(asset, base_models_dir)
        if not target.exists() or target.stat().st_size <= 0:
            return False
        sha = asset.get("sha256")
        if sha:
            h = hashlib.sha256()
            with open(target, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            return h.hexdigest().lower() == sha.lower()
        return True
    raise ValueError(f"unknown asset kind: {kind!r}")


# --------------------------------------------------------------------------- #
# Status + downloads                                                           #
# --------------------------------------------------------------------------- #

def status(base_models_dir: Optional[Path] = None) -> dict:
    """Return the presence/size summary for every manifest asset."""
    models = []
    total_missing = 0
    for a in MANIFEST:
        present = asset_present(a, base_models_dir)
        if not present:
            total_missing += int(a["approx_size_mb"])
        models.append(
            {
                "id": a["id"],
                "name": a["name"],
                "kind": a["kind"],
                "ref": a["ref"],
                "present": present,
                "approx_size_mb": int(a["approx_size_mb"]),
                "required_for": a["required_for"],
                "cache_path": asset_cache_path(a, base_models_dir),
            }
        )
    runtime = {}
    try:
        from selects.ml.onnx_rt import runtime_info

        runtime = runtime_info()
    except Exception:
        runtime = {"device": "CPU", "cuda_required": False, "gpu_without_cuda": False}
    return {
        "models": models,
        "total_missing_mb": total_missing,
        "cache_root": str(base_models_dir or models_dir()),
        "runtime": runtime,
    }


def _download_asset(asset: dict, base_models_dir: Optional[Path]) -> None:
    kind = asset["kind"]
    if kind == "onnx":
        from selects.ml.onnx_rt import ensure_all

        ensure_all()
    elif kind == "hf":
        if asset.get("id") == "whisper_small_onnx":
            from selects.ml.video_speech import _ensure_whisper_files

            _ensure_whisper_files()
        else:
            from huggingface_hub import snapshot_download

            snapshot_download(asset["ref"])
    elif kind == "url":
        download_file(
            asset["ref"],
            _url_target(asset, base_models_dir),
            sha256=asset.get("sha256"),
        )
    elif kind == "insightface":
        from insightface.utils import storage

        storage.ensure_available(
            "models",
            "buffalo_l",
            root=str(insightface_root(base_models_dir)),
        )
    else:
        raise ValueError(f"unknown asset kind: {kind!r}")


def download_one(
    asset_id: str,
    publish: Optional[Callable[[dict], None]] = None,
    base_models_dir: Optional[Path] = None,
) -> dict:
    """Download a single manifest asset by id. Raises ``KeyError`` if unknown."""
    asset = next((item for item in MANIFEST if item["id"] == asset_id), None)
    if asset is None:
        raise KeyError(asset_id)
    if publish is not None:
        publish({"stage": "models", "current": 1, "total": 1, "message": asset["name"]})
    log.info("downloading model asset %s (%s)", asset["id"], asset["name"])
    _download_asset(asset, base_models_dir)
    return {"id": asset_id, "present": asset_present(asset, base_models_dir)}


def download_all(
    publish: Optional[Callable[[dict], None]] = None,
    only_missing: bool = True,
    base_models_dir: Optional[Path] = None,
) -> int:
    """Download every (missing) manifest asset.

    *publish* receives ``{"stage": "models", "current": i, "total": n,
    "message": "<asset name>"}`` before each asset is fetched. Returns the
    number of assets that were (attempted to be) downloaded.
    """
    todo = [
        a
        for a in MANIFEST
        if not (only_missing and asset_present(a, base_models_dir))
    ]
    total = len(todo)
    for i, asset in enumerate(todo, start=1):
        if publish is not None:
            publish(
                {
                    "stage": "models",
                    "current": i,
                    "total": total,
                    "message": asset["name"],
                }
            )
        log.info("downloading model asset %s (%s)", asset["id"], asset["name"])
        _download_asset(asset, base_models_dir)
    return total
