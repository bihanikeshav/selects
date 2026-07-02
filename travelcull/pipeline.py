from __future__ import annotations

import logging
from typing import Callable

import numpy as np
from PIL import Image

from travelcull.classical.auto_reject import RejectInput, evaluate_reject
from travelcull.classical.blur import laplacian_variance
from travelcull.classical.exposure import exposure_score
from travelcull.classical.faces import detect_faces
from travelcull.config import FolderConfig
from travelcull.db import init_db, session_scope
from travelcull.db.models import ClassicalScore, PipelineState, Photo

# Re-export ML stages — lazy so torch is not imported at module load time.
# Callers can do: from travelcull.pipeline import run_embedding_stage
def run_embedding_stage(cfg, on_progress=None, batch_size=16):  # noqa: F811
    """Lazy proxy: imports travelcull.ml.embed.run_embedding_stage on first call."""
    from travelcull.ml.embed import run_embedding_stage as _fn
    return _fn(cfg, on_progress=on_progress, batch_size=batch_size)


def run_tag_stage(cfg, on_progress=None, top_k=3, min_score=0.15, tag_prompts=None):  # noqa: F811
    """Lazy proxy: imports travelcull.ml.tags.run_tag_stage on first call."""
    from travelcull.ml.tags import run_tag_stage as _fn
    return _fn(cfg, on_progress=on_progress, top_k=top_k, min_score=min_score, tag_prompts=tag_prompts)

log = logging.getLogger(__name__)
ProgressCb = Callable[[int, int, str], None] | None


def run_classical_stage(cfg: FolderConfig, on_progress: ProgressCb = None) -> int:
    """Run classical signals on every photo with classical_done=False. Returns count processed."""
    Session = init_db(cfg.db_path)

    with session_scope(Session) as s:
        pending = (
            s.query(Photo, PipelineState)
            .join(PipelineState, Photo.id == PipelineState.photo_id)
            .filter(PipelineState.classical_done.is_(False))
            .all()
        )
        # Detach for use outside session
        pending_ids = [(photo.id, photo.preview_path) for photo, _ in pending]

    if not pending_ids:
        return 0

    total = len(pending_ids)
    for i, (photo_id, preview_path) in enumerate(pending_ids, start=1):
        if on_progress:
            on_progress(i, total, preview_path or "")
        try:
            _score_one(cfg, Session, photo_id, preview_path)
        except Exception as exc:
            log.warning("classical stage failed on photo %s: %s", photo_id, exc)
            with session_scope(Session) as s:
                ps = s.get(PipelineState, photo_id)
                if ps:
                    ps.error = str(exc)[:500]
                    s.add(ps)
    return total


def _score_one(cfg: FolderConfig, Session, photo_id: int, preview_path: str) -> None:
    img = _load_preview(cfg, preview_path)
    blur = laplacian_variance(img)
    exp = exposure_score(img)
    faces = detect_faces(img)
    rej = evaluate_reject(
        RejectInput(
            blur=blur,
            exposure_score=exp.score,
            clipped_ratio=exp.clipped_ratio,
            faces_count=len(faces),
        )
    )

    with session_scope(Session) as s:
        score = s.get(ClassicalScore, photo_id) or ClassicalScore(photo_id=photo_id)
        score.blur = blur
        score.exposure = exp.score
        score.faces_count = len(faces)
        score.auto_reject = rej.auto_reject
        score.reject_reason = rej.reason
        s.add(score)
        ps = s.get(PipelineState, photo_id)
        if ps:
            ps.classical_done = True
            s.add(ps)


def _load_preview(cfg: FolderConfig, preview_path: str) -> np.ndarray:
    preview_abs = cfg.state_dir / preview_path
    with Image.open(preview_abs) as im:
        return np.asarray(im.convert("RGB"), dtype=np.uint8)
