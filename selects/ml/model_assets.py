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
# insightface model-pack name/url (kind="insightface"). ``filename`` (url only)
# is the on-disk basename under the shared models dir. Sizes are approximate
# download footprints in MB.
MANIFEST: list[dict] = [
    {
        "id": "siglip",
        "name": "SigLIP SO400M (image embeddings)",
        "kind": "hf",
        "ref": "google/siglip-so400m-patch14-384",
        "approx_size_mb": 3400,
        "required_for": "photo scoring",
        "sha256": None,  # n/a for hf assets
    },
    {
        "id": "qwen3vl",
        "name": "Qwen3-VL-2B-Instruct (vision-language model)",
        "kind": "hf",
        "ref": "Qwen/Qwen3-VL-2B-Instruct",
        "approx_size_mb": 4100,
        "required_for": "captions and cluster naming",
        "sha256": None,  # n/a for hf assets
    },
    {
        "id": "ram_plus",
        "name": "RAM++ (Recognize Anything Plus)",
        "kind": "hf",
        "ref": "xinyu1205/recognize-anything-plus-model",
        "approx_size_mb": 2800,
        "required_for": "tagging",
        "sha256": None,  # n/a for hf assets
    },
    {
        "id": "buffalo_l",
        "name": "InsightFace buffalo_l (face detection + recognition)",
        "kind": "insightface",
        "ref": "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip",
        "approx_size_mb": 330,
        "required_for": "face recognition",
        "sha256": None,  # insightface manages/verifies its own pack
    },
    {
        "id": "nafnet",
        "name": "NAFNet GoPro width32 (deblur)",
        "kind": "url",
        "ref": (
            "https://huggingface.co/mikestealth/nafnet-models/resolve/main/"
            "NAFNet-GoPro-width32.pth"
        ),
        "filename": "nafnet_gopro_width32.pth",
        "approx_size_mb": 70,
        "required_for": "enhancement (deblur)",
        "sha256": None,  # not cached at authoring time
    },
    {
        "id": "zero_dce",
        "name": "Zero-DCE++ Epoch99 (low-light)",
        "kind": "url",
        "ref": (
            "https://raw.githubusercontent.com/Li-Chongyi/Zero-DCE_extension/master/"
            "Zero-DCE++/snapshots_Zero_DCE++/Epoch99.pth"
        ),
        "filename": "zero_dce_plus_epoch99.pth",
        "approx_size_mb": 1,
        "required_for": "enhancement (low-light)",
        "sha256": None,  # not cached at authoring time
    },
    {
        "id": "csrnet",
        "name": "CSRNet FiveK (retouch)",
        "kind": "url",
        "ref": (
            "https://github.com/hejingwenhejingwen/CSRNet/raw/master/"
            "experiments/pretrain_models/csrnet.pth"
        ),
        "filename": "csrnet_fivek.pth",
        "approx_size_mb": 1,
        "required_for": "enhancement (retouch)",
        "sha256": None,  # not cached at authoring time
    },
]


# --------------------------------------------------------------------------- #
# Paths                                                                        #
# --------------------------------------------------------------------------- #

def models_dir() -> Path:
    """Shared cache directory for ``kind="url"`` weight files.

    Overridable via ``SELECTS_MODELS_DIR`` (used by tests). Defaults to
    ``~/.cache/selects/models``.
    """
    env = os.environ.get("SELECTS_MODELS_DIR")
    if env:
        return Path(env)
    return Path.home() / ".cache" / "selects" / "models"


def insightface_dir() -> Path:
    """Root under which insightface stores its model packs."""
    return Path.home() / ".insightface" / "models"


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


def asset_present(asset: dict, base_models_dir: Optional[Path] = None) -> bool:
    """Return True if *asset* is already available on disk."""
    kind = asset["kind"]
    if kind == "hf":
        return _hf_repo_cached(asset["ref"])
    if kind == "insightface":
        # insightface unpacks the zip into <root>/buffalo_l/
        return (insightface_dir() / "buffalo_l").is_dir()
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
                "present": present,
                "approx_size_mb": int(a["approx_size_mb"]),
                "required_for": a["required_for"],
            }
        )
    return {"models": models, "total_missing_mb": total_missing}


def _download_asset(asset: dict, base_models_dir: Optional[Path]) -> None:
    kind = asset["kind"]
    if kind == "hf":
        from huggingface_hub import snapshot_download

        snapshot_download(asset["ref"])
    elif kind == "url":
        download_file(
            asset["ref"],
            _url_target(asset, base_models_dir),
            sha256=asset.get("sha256"),
        )
    elif kind == "insightface":
        # Mirror classical/faces.py's convention: insightface fetches and
        # unpacks buffalo_l on first access. ensure_available downloads the zip
        # from its release URL into ~/.insightface/models/buffalo_l.
        from insightface.utils import storage

        storage.ensure_available("models", "buffalo_l")
    else:
        raise ValueError(f"unknown asset kind: {kind!r}")


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
