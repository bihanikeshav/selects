"""AP-V2.5 aesthetic head on stored SigLIP embeddings (no extra vision pass).

The official predictor is a 5-layer MLP on L2-normalised SigLIP-SO400M
(1152-d) pooled embeddings. We already store those in ``embeddings.siglip``.
Weights are downloaded from the upstream GitHub release on first use and
cached as float32 ``.npz`` so later runs do not need torch.

License of the head weights: AGPL-3.0 (upstream). We fetch at runtime and
do not vendor them in the MIT sdist.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import numpy as np

from selects.config import FolderConfig
from selects.db import init_db, session_scope
from selects.db.models import AestheticScore, Embedding

log = logging.getLogger(__name__)

HEAD_URL = (
    "https://github.com/discus0434/aesthetic-predictor-v2-5/raw/main/"
    "models/aesthetic_predictor_v2_5.pth"
)
HEAD_DIM = 1152
_LAYERS: list[tuple[np.ndarray, np.ndarray]] | None = None


def _npz_path() -> Path:
    from selects.ml.model_assets import models_dir

    return models_dir() / "aesthetic_predictor_v2_5.npz"


def _pth_path() -> Path:
    from selects.ml.model_assets import models_dir

    return models_dir() / "aesthetic_predictor_v2_5.pth"


def _layers_from_state_dict(sd: dict) -> list[tuple[np.ndarray, np.ndarray]]:
    """``scoring_head.{0,2,4,6,8}.{weight,bias}`` → list of (W, b) float32.

    torch.nn.Linear: y = x @ W.T + b. We store W as (out, in).
    """
    layers = []
    for idx in (0, 2, 4, 6, 8):
        w = sd[f"scoring_head.{idx}.weight"].detach().float().cpu().numpy()
        b = sd[f"scoring_head.{idx}.bias"].detach().float().cpu().numpy()
        layers.append((np.ascontiguousarray(w), np.ascontiguousarray(b)))
    if layers[0][0].shape[1] != HEAD_DIM:
        raise ValueError(f"AP-V2.5 head in-dim {layers[0][0].shape[1]} != {HEAD_DIM}")
    return layers


def _convert_pth(pth: Path, npz: Path) -> list[tuple[np.ndarray, np.ndarray]]:
    import torch

    sd = torch.load(pth, map_location="cpu", weights_only=True)
    layers = _layers_from_state_dict(sd)
    np.savez(
        npz,
        **{f"w{i}": w for i, (w, _) in enumerate(layers)},
        **{f"b{i}": b for i, (_, b) in enumerate(layers)},
    )
    return layers


def _load_npz(npz: Path) -> list[tuple[np.ndarray, np.ndarray]]:
    data = np.load(npz)
    return [(data[f"w{i}"], data[f"b{i}"]) for i in range(5)]


def load_ap25_head(*, force_download: bool = False) -> list[tuple[np.ndarray, np.ndarray]]:
    """Return the five (W, b) pairs, downloading/converting on first call."""
    global _LAYERS
    if _LAYERS is not None and not force_download:
        return _LAYERS
    npz = _npz_path()
    if npz.exists() and npz.stat().st_size > 1000 and not force_download:
        _LAYERS = _load_npz(npz)
        return _LAYERS
    from selects.ml.model_assets import download_file

    pth = _pth_path()
    if force_download or not pth.exists() or pth.stat().st_size < 1000:
        download_file(HEAD_URL, pth, timeout=60.0)
    _LAYERS = _convert_pth(pth, npz)
    return _LAYERS


def ensemble_score(
    ap25: float | None,
    nima: float | None,
    iqa: float | None,
    *,
    ap_w: float = 0.6,
    nima_w: float = 0.4,
) -> float | None:
    """Blend AP-V2.5 + NIMA when both exist; otherwise AP25, else IQA on a 1–10 scale."""
    if ap25 is not None and nima is not None:
        return float(ap_w * ap25 + nima_w * nima)
    if ap25 is not None:
        return float(ap25)
    if iqa is not None:
        return float(iqa) * 10.0
    return None


def rank_score(
    ap25: float | None,
    nima: float | None,
    iqa: float | None,
    *,
    ap_w: float = 0.6,
    nima_w: float = 0.4,
) -> float | None:
    """0–1 ranking score so AP-V2.5 (≈1–10) and CLIP-IQA (0–1) share a scale."""
    ens = ensemble_score(ap25, nima, iqa, ap_w=ap_w, nima_w=nima_w)
    if ens is None:
        return None
    return float(ens) / 10.0


def score_embeddings(feats: np.ndarray) -> np.ndarray:
    """L2-normalised [N, 1152] float32 → AP-V2.5 scores [N] (typically ~1–10)."""
    layers = load_ap25_head()
    x = np.asarray(feats, dtype=np.float32)
    if x.ndim == 1:
        x = x[None, :]
    nrm = np.linalg.norm(x, axis=1, keepdims=True)
    x = x / np.clip(nrm, 1e-9, None)
    for w, b in layers:
        x = x @ w.T + b
    return x.reshape(-1)


def run_aesthetic_stage(
    cfg: FolderConfig,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> int:
    """Write ``AestheticScore.ap25_score`` for every embedded photo. Returns count."""
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        rows = s.query(Embedding.photo_id, Embedding.siglip).all()
    if not rows:
        return 0
    try:
        load_ap25_head()
    except Exception as exc:
        log.warning("AP-V2.5 head unavailable (%s); skipping aesthetic stage", exc)
        if on_progress:
            on_progress(0, 0, "AP-V2.5 weights unavailable")
        return 0

    total = len(rows)
    ids = [r[0] for r in rows]
    feats = np.stack(
        [np.frombuffer(r[1], dtype=np.float16).astype(np.float32) for r in rows]
    )
    if on_progress:
        on_progress(0, total, "scoring")
    scores = score_embeddings(feats)
    with session_scope(Session) as s:
        for pid, sc in zip(ids, scores):
            row = s.get(AestheticScore, pid) or AestheticScore(photo_id=pid)
            row.ap25_score = float(sc)
            s.add(row)
    if on_progress:
        on_progress(total, total, "ap25 written")
    log.info("aesthetic stage: scored %d photos", total)
    return total
