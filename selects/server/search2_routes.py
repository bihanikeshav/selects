"""Hybrid photo/video discovery search.

Photos retain the original response fields and ranking behavior. Videos are
opted into with ``media=videos`` or ``media=all`` and are scored first from
the existing best-frame vector, then from sparse timestamped segment vectors.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, FastAPI, HTTPException, Query
from sqlalchemy import and_, or_, select, text

from selects.config import FolderConfig
from selects.db import init_db, session_scope
from selects.db.models import AestheticScore, Embedding, Photo, PhotoPerson, PhotoTag, Video, VideoTag
from selects.util import chunked

_TAG_MATCH_BONUS = 10.0
_TAG_MATCH_BONUS_STEP = 1.0
_SEM_KEEP_FRACTION = 0.55
_SEM_MIN_RESULTS = 12
_WORD_RE = re.compile(r"[a-z0-9]+")
_MEDIA_VALUES = {"all", "photos", "videos"}


def _query_words(q: str) -> list[str]:
    return [word for word in _WORD_RE.findall(q.lower()) if len(word) > 2]


def build_router(cfg: FolderConfig) -> APIRouter:
    router = APIRouter()

    def Session():
        return init_db(cfg.db_path)()

    @router.get("/api/search2")
    def search2(
        q: Optional[str] = Query(None, min_length=1, description="Free-text semantic query"),
        tags: Optional[str] = Query(None, description="Comma-separated tag filter"),
        person_id: Optional[int] = Query(None, description="Restrict to photos containing this person"),
        date_from: Optional[str] = Query(None, description="ISO date/datetime lower bound"),
        date_to: Optional[str] = Query(None, description="ISO date/datetime upper bound"),
        min_aesthetic: Optional[float] = Query(None, description="Minimum photo aesthetic score"),
        media: str = Query("photos", description="Search photos, videos, or both"),
        limit: int = Query(120, le=1000),
    ):
        if media not in _MEDIA_VALUES:
            raise HTTPException(400, detail="media must be one of: all, photos, videos")
        if not q and not tags and person_id is None and date_from is None and date_to is None and min_aesthetic is None:
            raise HTTPException(400, detail="provide at least one of: q, tags, person_id, date_from/date_to, min_aesthetic")

        dt_from = _parse_dt(date_from, "date_from")
        dt_to = _parse_dt(date_to, "date_to")
        tag_list = [tag.strip() for tag in tags.split(",") if tag.strip()] if tags else []
        want_photos = media in {"photos", "all"}
        want_videos = media in {"videos", "all"}
        photo_filter = bool(person_id is not None or dt_from is not None or dt_to is not None or min_aesthetic is not None or tag_list)

        candidate_ids: Optional[set[int]] = None

        def intersect(ids: set[int]) -> None:
            nonlocal candidate_ids
            candidate_ids = ids if candidate_ids is None else candidate_ids & ids

        tag_hits_by_id: dict[int, int] = {}
        combined_by_id: dict[int, float] = {}
        sem_scores: dict[int, float] = {}
        photo_shas: dict[int, str] = {}
        query_vec = None

        with session_scope(Session) as s:
            if want_photos and person_id is not None:
                intersect({row[0] for row in s.query(PhotoPerson.photo_id).filter(PhotoPerson.person_id == person_id).all()})

            if want_photos and (dt_from is not None or dt_to is not None):
                stmt = select(Photo.id)
                if dt_from is not None:
                    stmt = stmt.where(Photo.taken_at >= dt_from)
                if dt_to is not None:
                    stmt = stmt.where(Photo.taken_at <= dt_to)
                intersect({row[0] for row in s.execute(stmt).all()})

            if want_photos and tag_list:
                intersect({row[0] for row in s.query(PhotoTag.photo_id).filter(PhotoTag.tag.in_(tag_list)).all()})

            if want_photos and min_aesthetic is not None:
                ap_floor = min_aesthetic * 10.0
                score_rows = (
                    s.query(Embedding.photo_id, Embedding.aesthetic_iqa, AestheticScore.ap25_score)
                    .outerjoin(AestheticScore, AestheticScore.photo_id == Embedding.photo_id)
                    .filter(or_(
                        AestheticScore.ap25_score >= ap_floor,
                        and_(AestheticScore.ap25_score.is_(None), Embedding.aesthetic_iqa.isnot(None), Embedding.aesthetic_iqa >= min_aesthetic),
                    ))
                    .all()
                )
                combined_by_id = {
                    pid: float(ap25) / 10.0 if ap25 is not None else float(iqa)
                    for pid, iqa, ap25 in score_rows
                }
                intersect(set(combined_by_id))

            if want_photos and photo_filter and candidate_ids is not None and not candidate_ids:
                photo_shas = {}
            elif want_photos and q:
                words = _query_words(q)
                if words:
                    tag_query = s.query(PhotoTag.photo_id, PhotoTag.tag).filter(
                        or_(*[PhotoTag.tag.ilike(f"%{word}%") for word in words])
                    )
                    tag_rows = []
                    if candidate_ids is not None:
                        for chunk in chunked(list(candidate_ids)):
                            tag_rows.extend(tag_query.filter(PhotoTag.photo_id.in_(chunk)).all())
                    else:
                        tag_rows = tag_query.all()
                    for pid, tag in tag_rows:
                        tag_lower = tag.lower()
                        hits = sum(1 for word in words if word in tag_lower or tag_lower in word)
                        if hits:
                            tag_hits_by_id[pid] = tag_hits_by_id.get(pid, 0) + hits

            if want_photos and q:
                from selects.ml.search import cosine_scores, embed_query, library_embedding_matrix

                matrix, ids, shas = library_embedding_matrix(cfg)
                if candidate_ids is not None:
                    keep = [index for index, photo_id in enumerate(ids) if photo_id in candidate_ids]
                    matrix = matrix[keep] if keep else matrix[:0]
                    ids = [ids[index] for index in keep]
                    shas = [shas[index] for index in keep]
                if ids:
                    query_vec = embed_query(q)
                    scores = cosine_scores(matrix, query_vec)
                    sem_scores = {photo_id: float(score) for photo_id, score in zip(ids, scores)}
                    photo_shas = dict(zip(ids, shas))
            elif want_photos and candidate_ids is not None:
                for chunk in chunked(list(candidate_ids)):
                    photo_shas.update(dict(s.query(Photo.id, Photo.sha256).filter(Photo.id.in_(chunk)).all()))

            video_filter_compatible = person_id is None and min_aesthetic is None
            if want_videos and not q and video_filter_compatible:
                video_query = s.query(Video.id, Video.sha256)
                if dt_from is not None:
                    video_query = video_query.filter(Video.taken_at >= dt_from)
                if dt_to is not None:
                    video_query = video_query.filter(Video.taken_at <= dt_to)
                if tag_list:
                    tagged_ids = {
                        row[0]
                        for row in s.query(VideoTag.video_id)
                        .filter(VideoTag.tag.in_(tag_list))
                        .all()
                    }
                    video_query = video_query.filter(Video.id.in_(tagged_ids))
                video_rows = video_query.all()
            else:
                video_rows = []

        if want_videos and q:
            from selects.ml.search import embed_query
            from selects.ml.video_search import score_video_search

            query_vec = query_vec if query_vec is not None else embed_query(q)
            video_results = score_video_search(cfg, query_vec, limit=limit)
            if dt_from is not None or dt_to is not None:
                video_results = _filter_video_dates(cfg, video_results, dt_from, dt_to)
            if tag_list:
                video_results = _filter_video_tags(cfg, video_results, tag_list)
            video_results = [
                _video_result(
                    item["video_id"], item["sha256"], item["score"],
                    item["semantic_score"], item.get("match_start_sec"),
                    item.get("match_end_sec"), item.get("match_kind", "best-frame"),
                )
                for item in video_results
            ]
            transcript_hits = _transcript_search(cfg, q, limit)
            by_video = {item["video_id"]: item for item in video_results}
            for hit in transcript_hits:
                previous = by_video.get(hit["video_id"])
                if previous is None or hit["score"] >= previous["score"]:
                    by_video[hit["video_id"]] = hit
                elif previous.get("match_start_sec") is None:
                    previous["match_start_sec"] = hit["match_start_sec"]
                    previous["match_end_sec"] = hit["match_end_sec"]
                    previous["match_kind"] = "transcript"
            video_results = list(by_video.values())
        elif want_videos:
            video_results = [
                _video_result(video_id, sha, 0.0, 0.0, None, None, "browse")
                for video_id, sha in video_rows
            ]
        else:
            video_results = []

        photo_results = []
        for photo_id in set(sem_scores) | set(photo_shas):
            semantic = sem_scores.get(photo_id, 0.0)
            tag_hits = tag_hits_by_id.get(photo_id, 0)
            bonus = _TAG_MATCH_BONUS + _TAG_MATCH_BONUS_STEP * (tag_hits - 1) if tag_hits else 0.0
            sha = photo_shas.get(photo_id)
            if sha is None:
                continue
            photo_results.append({
                "asset_type": "photo",
                "photo_id": photo_id,
                "sha256": sha,
                "score": semantic + bonus,
                "semantic_score": semantic,
                "tag_hits": tag_hits,
                "thumb_url": f"/api/thumb/{sha}",
                "preview_url": f"/api/preview/{sha}",
            })

        if not q:
            if combined_by_id:
                photo_results.sort(key=lambda item: combined_by_id.get(item["photo_id"], 0.0), reverse=True)
            else:
                photo_results = _sort_photos_by_date(Session, photo_results)
        else:
            photo_results.sort(key=lambda item: item["score"], reverse=True)
            if sem_scores:
                top_sem = max(sem_scores.values())
                if top_sem > 0:
                    floor = top_sem * _SEM_KEEP_FRACTION
                    kept = [item for item in photo_results if item["tag_hits"] or item["semantic_score"] >= floor]
                    photo_results = kept if len(kept) >= _SEM_MIN_RESULTS else photo_results[:_SEM_MIN_RESULTS]

        results = photo_results + video_results
        results.sort(key=lambda item: item["score"], reverse=True)
        results = results[:limit]
        return {"query": q, "media": media, "total": len(results), "results": results}

    return router


def _sort_photos_by_date(Session, results: list[dict]) -> list[dict]:
    if not results:
        return results
    with session_scope(Session) as s:
        ids = [item["photo_id"] for item in results]
        taken: dict[int, datetime | None] = {}
        for id_chunk in chunked(ids):
            taken.update(
                dict(
                    s.query(Photo.id, Photo.taken_at)
                    .filter(Photo.id.in_(id_chunk))
                    .all()
                )
            )
    return sorted(results, key=lambda item: taken.get(item["photo_id"]) or datetime.min, reverse=True)


def _transcript_search(cfg: FolderConfig, q: str, limit: int) -> list[dict]:
    Session = init_db(cfg.db_path)
    try:
        with session_scope(Session) as s:
            rows = s.execute(
                text(
                    "SELECT s.video_id, v.sha256, s.start_ms, s.end_ms "
                    "FROM video_transcript_fts AS f "
                    "JOIN video_transcript_segments AS s ON s.id = f.rowid "
                    "JOIN videos AS v ON v.id = s.video_id "
                    "WHERE video_transcript_fts MATCH :q LIMIT :lim"
                ),
                {"q": q, "lim": limit},
            ).fetchall()
    except Exception:
        return []
    return [
        _video_result(video_id, sha, 1.15, 1.15, start_ms / 1000.0, end_ms / 1000.0, "transcript")
        for video_id, sha, start_ms, end_ms in rows
        if sha
    ]


def _video_result(video_id: int, sha: str, score: float, semantic: float, start: float | None, end: float | None, kind: str) -> dict:
    return {
        "asset_type": "video",
        "video_id": video_id,
        "sha256": sha,
        "score": score,
        "semantic_score": semantic,
        "tag_hits": 0,
        "thumb_url": f"/api/thumb/{sha}",
        "preview_url": f"/api/preview/{sha}",
        "match_start_sec": start,
        "match_end_sec": end,
        "match_kind": kind,
    }


def _filter_video_dates(cfg: FolderConfig, results: list[dict], dt_from: datetime | None, dt_to: datetime | None) -> list[dict]:
    if not results or (dt_from is None and dt_to is None):
        return results
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        ids = [item["video_id"] for item in results]
        query = s.query(Video.id).filter(Video.id.in_(ids))
        if dt_from is not None:
            query = query.filter(Video.taken_at >= dt_from)
        if dt_to is not None:
            query = query.filter(Video.taken_at <= dt_to)
        allowed = {row[0] for row in query.all()}
    return [item for item in results if item["video_id"] in allowed]


def _filter_video_tags(cfg: FolderConfig, results: list[dict], tags: list[str]) -> list[dict]:
    if not results or not tags:
        return results
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        allowed = {
            row[0]
            for row in s.query(VideoTag.video_id)
            .filter(VideoTag.video_id.in_([item["video_id"] for item in results]))
            .filter(VideoTag.tag.in_(tags))
            .all()
        }
    return [item for item in results if item["video_id"] in allowed]


def _parse_dt(value: Optional[str], field: str) -> Optional[datetime]:
    if value is None or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, detail=f"invalid {field}: {value!r} (expected ISO date/datetime)")


def register_search2_routes(app: FastAPI, cfg: FolderConfig) -> None:
    app.include_router(build_router(cfg))
