"""Hybrid discovery search: SigLIP semantic similarity + RAM++ exact tag matches,
with filters for person, date range, and minimum aesthetic score.

A NEW endpoint (``/api/search2``) that lives alongside — and does not replace —
the existing ``/api/search`` (plain SigLIP-only search in ``routes.py``). Kept
in its own file/router per the project's conflict rules; not wired into
``app.py`` by this feature (see wiring_needed in the task report).

Ranking: any candidate with a matching exact/substring tag hit gets a large
fixed bonus so it always ranks above a photo that only has weak semantic
similarity — then, within each tier, results are ordered by the underlying
SigLIP cosine score.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, FastAPI, HTTPException, Query
from sqlalchemy import select

from travelcull.config import FolderConfig
from travelcull.db import init_db, session_scope
from travelcull.db.models import AestheticScore, Embedding, Photo, PhotoPerson, PhotoTag

# Fixed bonus added per matching tag so exact tag hits always outrank a
# semantic-only match (SigLIP cosine scores live in roughly [-1, 1]).
_TAG_MATCH_BONUS = 10.0
_TAG_MATCH_BONUS_STEP = 1.0  # extra per additional matching tag, small tie-breaker only

_WORD_RE = re.compile(r"[a-z0-9]+")


def _query_words(q: str) -> list[str]:
    return [w for w in _WORD_RE.findall(q.lower()) if len(w) > 2]


def build_router(cfg: FolderConfig) -> APIRouter:
    router = APIRouter()

    def Session():
        return init_db(cfg.db_path)()

    @router.get("/api/search2")
    def search2(
        q: Optional[str] = Query(None, min_length=1, description="Free-text semantic query"),
        tags: Optional[str] = Query(None, description="Comma-separated tag filter (OR match, from photo_tags)"),
        person_id: Optional[int] = Query(None, description="Restrict to photos containing this person"),
        date_from: Optional[str] = Query(None, description="ISO date/datetime lower bound (inclusive) on taken_at"),
        date_to: Optional[str] = Query(None, description="ISO date/datetime upper bound (inclusive) on taken_at"),
        min_aesthetic: Optional[float] = Query(
            None, description="Minimum combined aesthetic score (avg of nima_score/ap25_score, 0-10ish scale)"
        ),
        limit: int = Query(120, le=1000),
    ):
        if not q and not tags and person_id is None and date_from is None and date_to is None and min_aesthetic is None:
            raise HTTPException(400, detail="provide at least one of: q, tags, person_id, date_from/date_to, min_aesthetic")

        dt_from = _parse_dt(date_from, "date_from")
        dt_to = _parse_dt(date_to, "date_to")
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

        has_structured_filter = bool(
            person_id is not None or dt_from is not None or dt_to is not None
            or min_aesthetic is not None or tag_list
        )

        with session_scope(Session) as s:
            # ── candidate id set from structured filters (intersected pairwise) ──
            candidate_ids: Optional[set[int]] = None  # None = "no structured filter yet"

            def _intersect(ids: set[int]) -> None:
                nonlocal candidate_ids
                candidate_ids = ids if candidate_ids is None else (candidate_ids & ids)

            if person_id is not None:
                _intersect({r[0] for r in s.query(PhotoPerson.photo_id).filter(
                    PhotoPerson.person_id == person_id
                ).all()})

            if dt_from is not None or dt_to is not None:
                stmt = select(Photo.id)
                if dt_from is not None:
                    stmt = stmt.where(Photo.taken_at >= dt_from)
                if dt_to is not None:
                    stmt = stmt.where(Photo.taken_at <= dt_to)
                _intersect({r[0] for r in s.execute(stmt).all()})

            if tag_list:
                _intersect({r[0] for r in s.query(PhotoTag.photo_id).filter(
                    PhotoTag.tag.in_(tag_list)
                ).all()})

            combined_by_id: dict[int, float] = {}
            if min_aesthetic is not None:
                aes_rows = s.query(AestheticScore.photo_id, AestheticScore.nima_score, AestheticScore.ap25_score).all()
                for pid, nima, ap25 in aes_rows:
                    vals = [v for v in (nima, ap25) if v is not None]
                    if vals:
                        combined_by_id[pid] = sum(vals) / len(vals)
                passing = {pid for pid, val in combined_by_id.items() if val >= min_aesthetic}
                _intersect(passing)

            if has_structured_filter and not candidate_ids:
                return {"query": q, "total": 0, "results": []}

            # ── tag match bonus (against the free-text query words) ────────
            tag_hits_by_id: dict[int, int] = {}
            if q:
                words = _query_words(q)
                if words:
                    tag_rows = s.query(PhotoTag.photo_id, PhotoTag.tag).all()
                    for pid, tag in tag_rows:
                        if has_structured_filter and pid not in candidate_ids:
                            continue
                        tag_l = tag.lower()
                        n = sum(1 for w in words if w in tag_l or tag_l in w)
                        if n:
                            tag_hits_by_id[pid] = tag_hits_by_id.get(pid, 0) + n

            # ── semantic similarity ─────────────────────────────────────────
            sem_scores: dict[int, float] = {}
            shas: dict[int, str] = {}
            if q:
                from travelcull.ml.search import cosine_scores, embed_query, siglip_bytes_to_matrix

                emb_stmt = select(Photo.id, Photo.sha256, Embedding.siglip).join(
                    Embedding, Embedding.photo_id == Photo.id
                )
                if has_structured_filter:
                    emb_stmt = emb_stmt.where(Photo.id.in_(candidate_ids))
                rows = s.execute(emb_stmt).all()
                if rows:
                    ids = [r[0] for r in rows]
                    for pid, sha in zip(ids, (r[1] for r in rows)):
                        shas[pid] = sha
                    mat = siglip_bytes_to_matrix([r[2] for r in rows])
                    qvec = embed_query(q)
                    sims = cosine_scores(mat, qvec)
                    for pid, sim in zip(ids, sims):
                        sem_scores[pid] = float(sim)
            else:
                # no free-text query: pure filter/tag browsing — fetch sha256s for
                # whatever candidate set structured filters produced.
                rows = s.query(Photo.id, Photo.sha256).filter(Photo.id.in_(candidate_ids)).all()
                for pid, sha in rows:
                    shas[pid] = sha

            # ── merge into one ranked list ───────────────────────────────────
            all_ids = set(sem_scores) | set(shas)
            results = []
            for pid in all_ids:
                sem = sem_scores.get(pid, 0.0)
                n_tag_hits = tag_hits_by_id.get(pid, 0)
                bonus = (_TAG_MATCH_BONUS + _TAG_MATCH_BONUS_STEP * (n_tag_hits - 1)) if n_tag_hits else 0.0
                score = sem + bonus
                results.append((pid, shas.get(pid), score, sem, n_tag_hits))

            if not q:
                # no ranking signal available; order by aesthetic (if filtered) else taken_at desc
                if combined_by_id:
                    results.sort(key=lambda r: combined_by_id.get(r[0], 0.0), reverse=True)
                else:
                    taken_at_by_id = {
                        pid: t for pid, t in s.query(Photo.id, Photo.taken_at).filter(Photo.id.in_(all_ids)).all()
                    }
                    results.sort(key=lambda r: taken_at_by_id.get(r[0]) or datetime.min, reverse=True)
            else:
                results.sort(key=lambda r: r[2], reverse=True)

            results = results[:limit]

        return {
            "query": q,
            "total": len(results),
            "results": [
                {
                    "photo_id": pid,
                    "sha256": sha,
                    "score": score,
                    "semantic_score": sem,
                    "tag_hits": n_tag_hits,
                    "thumb_url": f"/api/thumb/{sha}",
                    "preview_url": f"/api/preview/{sha}",
                }
                for pid, sha, score, sem, n_tag_hits in results
                if sha is not None
            ],
        }

    return router


def _parse_dt(value: Optional[str], field: str) -> Optional[datetime]:
    if value is None or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, detail=f"invalid {field}: {value!r} (expected ISO date/datetime)")


def register_search2_routes(app: FastAPI, cfg: FolderConfig) -> None:
    """Register ``/api/search2`` on *app*.

    Mirrors :func:`travelcull.server.routes.register_routes`'s pattern of
    resolving the sessionmaker at request time via *cfg* (which may be an
    ``ActiveConfigProxy``), so switching the active library is picked up.
    NOT called from ``app.py`` by this feature — see wiring_needed.
    """
    app.include_router(build_router(cfg))
