"""Taste personalization — learn from the user's keep/reject swipe decisions.

A tiny logistic-regression head (numpy only, no sklearn) is trained on the
SigLIP image embeddings already stored per photo, using swipe verdicts as
labels (keep/silver → 1, reject → 0, skip ignored). The fitted weights are
persisted to ``<library>/.selects/taste.npz`` together with metadata
(n_samples, holdout AUC, trained_at).

The taste score is blended into the curation ranking (see
:mod:`selects.ml.curation`) as::

    final = (1 - w) * aesthetic + w * taste

where ``w`` ramps linearly from 0 → :data:`MAX_TASTE_WEIGHT` (0.4) as the
number of labeled examples goes 100 → 1000, so learned taste never dominates
the off-the-shelf aesthetic models.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Optional

import numpy as np
from sqlalchemy.orm import Session as OrmSession

if TYPE_CHECKING:  # pragma: no cover
    from selects.config import FolderConfig


TASTE_FILENAME = "taste.npz"

MIN_SAMPLES = 100          # minimum labeled swipes before training is allowed
MIN_PER_CLASS = 10         # need at least this many keeps AND rejects
MAX_TASTE_WEIGHT = 0.4     # taste influence cap on the blended ranking
RAMP_START = 100           # weight is 0 at/below this many samples
RAMP_END = 1000            # weight reaches MAX_TASTE_WEIGHT here

HOLDOUT_FRACTION = 0.2
L2_REG = 1e-2
LEARNING_RATE = 1.0
N_ITERS = 500
RANDOM_SEED = 1152         # SigLIP dim, as good a seed as any

_POSITIVE = ("keep", "silver")
_NEGATIVE = ("reject",)


class TasteTrainingError(RuntimeError):
    """Raised when there is not enough (or not varied enough) swipe data."""


@dataclass
class TasteModel:
    w: np.ndarray            # (d,) float32
    b: float
    n_samples: int
    auc: Optional[float]
    trained_at: str

    @property
    def weight(self) -> float:
        return taste_weight(self.n_samples)


# --------------------------------------------------------------------------- #
# Ramp / blend math                                                            #
# --------------------------------------------------------------------------- #

def taste_weight(n_samples: int) -> float:
    """Blend weight w: ramps 0 → MAX_TASTE_WEIGHT as n goes RAMP_START → RAMP_END."""
    frac = (float(n_samples) - RAMP_START) / float(RAMP_END - RAMP_START)
    return MAX_TASTE_WEIGHT * float(np.clip(frac, 0.0, 1.0))


def blend(aesthetic: float, taste: float, n_samples: int) -> float:
    """final = (1-w)*aesthetic + w*taste, with w from :func:`taste_weight`.

    Both inputs are expected in [0, 1]; the output then also lies in [0, 1].
    """
    w = taste_weight(n_samples)
    return (1.0 - w) * aesthetic + w * taste


# --------------------------------------------------------------------------- #
# Numpy logistic regression                                                    #
# --------------------------------------------------------------------------- #

def _sigmoid(z: np.ndarray) -> np.ndarray:
    out = np.empty_like(z)
    pos = z >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-z[pos]))
    ez = np.exp(z[~pos])
    out[~pos] = ez / (1.0 + ez)
    return out


def _fit_logreg(
    X: np.ndarray,
    y: np.ndarray,
    sample_weight: np.ndarray,
    *,
    l2: float = L2_REG,
    lr: float = LEARNING_RATE,
    n_iters: int = N_ITERS,
) -> tuple[np.ndarray, float]:
    """Full-batch gradient descent on weighted BCE + L2 penalty (bias unpenalized)."""
    n, d = X.shape
    w = np.zeros(d, dtype=np.float64)
    b = 0.0
    sw = sample_weight / sample_weight.sum()  # normalize so lr is scale-free
    for _ in range(n_iters):
        p = _sigmoid(X @ w + b)
        err = (p - y) * sw
        grad_w = X.T @ err + l2 * w
        grad_b = float(err.sum())
        w -= lr * grad_w
        b -= lr * grad_b
    return w, b


def _auc(y_true: np.ndarray, scores: np.ndarray) -> Optional[float]:
    """Rank-based ROC AUC with average ranks for ties. None if one class only."""
    pos = int(y_true.sum())
    neg = len(y_true) - pos
    if pos == 0 or neg == 0:
        return None
    order = np.argsort(scores, kind="mergesort")
    ranks = np.empty(len(scores), dtype=np.float64)
    sorted_scores = scores[order]
    i = 0
    while i < len(scores):
        j = i
        while j + 1 < len(scores) and sorted_scores[j + 1] == sorted_scores[i]:
            j += 1
        ranks[order[i : j + 1]] = 0.5 * (i + j) + 1.0  # average 1-based rank
        i = j + 1
    rank_sum_pos = ranks[y_true == 1].sum()
    return float((rank_sum_pos - pos * (pos + 1) / 2.0) / (pos * neg))


def _decode_embedding(blob: bytes) -> np.ndarray:
    vec = np.frombuffer(blob, dtype=np.float16).astype(np.float32)
    norm = float(np.linalg.norm(vec))
    if norm > 0:
        vec = vec / norm
    return vec


# --------------------------------------------------------------------------- #
# Persistence                                                                  #
# --------------------------------------------------------------------------- #

def _taste_path(state_dir: Path) -> Path:
    return Path(state_dir) / TASTE_FILENAME


def save_model(state_dir: Path, model: TasteModel) -> Path:
    path = _taste_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        path,
        w=model.w.astype(np.float32),
        b=np.float64(model.b),
        n_samples=np.int64(model.n_samples),
        auc=np.float64(model.auc if model.auc is not None else np.nan),
        trained_at=np.str_(model.trained_at),
    )
    return path


def load_model(state_dir: Path) -> Optional[TasteModel]:
    path = _taste_path(state_dir)
    if not path.is_file():
        return None
    try:
        with np.load(path, allow_pickle=False) as z:
            auc_val = float(z["auc"])
            return TasteModel(
                w=np.asarray(z["w"], dtype=np.float32),
                b=float(z["b"]),
                n_samples=int(z["n_samples"]),
                auc=None if np.isnan(auc_val) else auc_val,
                trained_at=str(z["trained_at"]),
            )
    except Exception:
        return None


def state_dir_from_session(s: OrmSession) -> Optional[Path]:
    """Derive <library>/.selects from the session's SQLite file path."""
    try:
        bind = s.get_bind()
        db_file = getattr(getattr(bind, "url", None), "database", None)
        if not db_file or db_file == ":memory:":
            return None
        return Path(db_file).parent
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Data extraction                                                              #
# --------------------------------------------------------------------------- #

def _labeled_query(s: OrmSession):
    from selects.db.models import Embedding, Swipe

    return (
        s.query(Swipe.decision, Embedding.siglip)
        .join(Embedding, Embedding.photo_id == Swipe.photo_id)
        .filter(Swipe.decision.in_(list(_POSITIVE) + list(_NEGATIVE)))
    )


def count_labeled(s: OrmSession) -> int:
    """Number of keep/silver/reject swipes that have a SigLIP embedding."""
    return int(_labeled_query(s).count())


def _load_training_data(s: OrmSession) -> tuple[np.ndarray, np.ndarray]:
    rows = _labeled_query(s).all()
    X_list, y_list = [], []
    for decision, blob in rows:
        if not blob:
            continue
        X_list.append(_decode_embedding(blob))
        y_list.append(1.0 if decision in _POSITIVE else 0.0)
    if not X_list:
        return np.zeros((0, 0), dtype=np.float32), np.zeros(0)
    return np.stack(X_list), np.array(y_list, dtype=np.float64)


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #

def train_taste_model(cfg: "FolderConfig") -> dict:
    """Train the taste head from swipes and persist it to <state_dir>/taste.npz.

    Returns metadata dict: {n_samples, auc, weight, trained_at, path}.
    Raises :class:`TasteTrainingError` when data is insufficient.
    """
    from selects.db import init_db, session_scope

    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        X, y = _load_training_data(s)

    n = len(y)
    if n < MIN_SAMPLES:
        raise TasteTrainingError(
            f"need at least {MIN_SAMPLES} keep/reject decisions with embeddings, have {n}"
        )
    n_pos = int(y.sum())
    n_neg = n - n_pos
    if n_pos < MIN_PER_CLASS or n_neg < MIN_PER_CLASS:
        raise TasteTrainingError(
            f"need at least {MIN_PER_CLASS} decisions of each kind "
            f"(have {n_pos} keeps, {n_neg} rejects)"
        )

    # Class-balanced sample weights: each class contributes equally.
    class_w = {1.0: n / (2.0 * n_pos), 0.0: n / (2.0 * n_neg)}
    sw = np.array([class_w[v] for v in y], dtype=np.float64)

    # Stratified holdout split for the reported AUC.
    rng = np.random.default_rng(RANDOM_SEED)
    holdout_mask = np.zeros(n, dtype=bool)
    for cls in (0.0, 1.0):
        idx = np.flatnonzero(y == cls)
        rng.shuffle(idx)
        k = max(1, int(round(len(idx) * HOLDOUT_FRACTION)))
        holdout_mask[idx[:k]] = True
    train_mask = ~holdout_mask

    w_tr, b_tr = _fit_logreg(X[train_mask], y[train_mask], sw[train_mask])
    holdout_scores = _sigmoid(X[holdout_mask] @ w_tr + b_tr)
    auc = _auc(y[holdout_mask], holdout_scores)

    # Final fit on ALL data — the holdout was only for the reported AUC.
    w_all, b_all = _fit_logreg(X, y, sw)

    model = TasteModel(
        w=w_all.astype(np.float32),
        b=float(b_all),
        n_samples=n,
        auc=auc,
        trained_at=datetime.now(timezone.utc).isoformat(),
    )
    path = save_model(cfg.state_dir, model)
    return {
        "n_samples": model.n_samples,
        "auc": model.auc,
        "weight": model.weight,
        "trained_at": model.trained_at,
        "path": str(path),
    }


def score_embeddings(model: TasteModel, X: np.ndarray) -> np.ndarray:
    """Sigmoid probability per row of X (rows should be L2-normalized)."""
    if X.size == 0:
        return np.zeros(0, dtype=np.float64)
    return _sigmoid(X @ model.w.astype(np.float64) + model.b)


def taste_scores_by_photo_id(
    s: OrmSession, model: TasteModel, photo_ids: Iterable[int]
) -> dict[int, float]:
    """Batch 0-1 taste score per photo id (photos without embeddings omitted)."""
    from selects.db.models import Embedding

    ids = list(photo_ids)
    if not ids:
        return {}
    out: dict[int, float] = {}
    CHUNK = 500
    for i in range(0, len(ids), CHUNK):
        rows = (
            s.query(Embedding.photo_id, Embedding.siglip)
            .filter(Embedding.photo_id.in_(ids[i : i + CHUNK]))
            .all()
        )
        pids = [pid for pid, blob in rows if blob]
        if not pids:
            continue
        X = np.stack([_decode_embedding(blob) for pid, blob in rows if blob])
        scores = score_embeddings(model, X)
        out.update({pid: float(sc) for pid, sc in zip(pids, scores)})
    return out


def taste_score(cfg: "FolderConfig", sha256s: Iterable[str]) -> dict[str, float]:
    """Batch taste score (0-1) per sha256. Empty dict when no model is trained."""
    from selects.db import init_db, session_scope
    from selects.db.models import Embedding, Photo

    model = load_model(cfg.state_dir)
    if model is None:
        return {}
    shas = [sh for sh in sha256s if sh]
    if not shas:
        return {}

    Session = init_db(cfg.db_path)
    out: dict[str, float] = {}
    CHUNK = 500
    with session_scope(Session) as s:
        for i in range(0, len(shas), CHUNK):
            rows = (
                s.query(Photo.sha256, Embedding.siglip)
                .join(Embedding, Embedding.photo_id == Photo.id)
                .filter(Photo.sha256.in_(shas[i : i + CHUNK]))
                .all()
            )
            keep = [(sh, blob) for sh, blob in rows if blob]
            if not keep:
                continue
            X = np.stack([_decode_embedding(blob) for _, blob in keep])
            scores = score_embeddings(model, X)
            out.update({sh: float(sc) for (sh, _), sc in zip(keep, scores)})
    return out


def taste_status(cfg: "FolderConfig") -> dict:
    """Status dict for the API: {trained, n_samples, auc, weight, labeled_available}."""
    from selects.db import init_db, session_scope

    model = load_model(cfg.state_dir)
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        labeled = count_labeled(s)
    if model is None:
        return {
            "trained": False,
            "n_samples": 0,
            "auc": None,
            "weight": 0.0,
            "labeled_available": labeled,
        }
    return {
        "trained": True,
        "n_samples": model.n_samples,
        "auc": model.auc,
        "weight": model.weight,
        "trained_at": model.trained_at,
        "labeled_available": labeled,
    }
