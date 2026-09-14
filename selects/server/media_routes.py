"""Playback, organization, analysis, and non-destructive video editing APIs."""
from __future__ import annotations

import json
import math
import mimetypes
import threading
import zlib
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from selects.config import FolderConfig
from selects.db import init_db, session_scope
from selects.db.models import (
    MediaJob,
    Video,
    VideoAudioAnalysis,
    VideoCollection,
    VideoCollectionItem,
    VideoEdit,
    VideoKeyframe,
    VideoProxy,
    VideoRating,
    VideoSegment,
    VideoTag,
    VideoTranscriptSegment,
)
from selects.media import (
    RangeNotSatisfiable,
    analyze_audio,
    decide_playback,
    generate_proxy,
    iter_file_range,
    parse_range_header,
    probe_media,
)
from selects.media.edits import EditRecipe, EditRecipeError
from selects.media.export import ExportError, run_export
from selects.media.jobs import JobCancelled, JobEngine
from selects.media.stabilize import analyze_stabilization
from selects.server.schemas import require_sha256
from selects.util import utcnow


PublishFn = Callable[[dict], None]
_AUDIO_VERSION = "audio-energy-v1"
_PROXY_VERSION = "proxy-h264-preview-v2"
_EDIT_VERSION = "video-edit-v1"


class EditBody(BaseModel):
    recipe: dict[str, Any]
    name: Optional[str] = None


class RatingBody(BaseModel):
    rating: Optional[int] = Field(default=None, ge=-1, le=5)


class TagsBody(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=200)


class CollectionBody(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    description: Optional[str] = Field(default=None, max_length=2000)


class ExportBody(BaseModel):
    destination: Optional[str] = None
    mode: Literal["fast", "precise"] = "precise"
    recipe: Optional[dict[str, Any]] = None


def _source_fingerprint(video: Video) -> str:
    return f"{video.sha256 or ''}:{video.size_bytes or 0}:{video.mtime or 0}"


def _json_loads(value: Optional[str], fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _mime(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def register_media_routes(
    app: FastAPI,
    cfg: FolderConfig,
    publish: Optional[PublishFn] = None,
) -> None:
    router = APIRouter()
    engine = JobEngine()
    engine_ids: dict[int, str] = {}
    engine_lock = threading.RLock()

    def Session():
        return init_db(cfg.db_path)()

    def video_for_sha(session, sha256: str) -> Video:
        require_sha256(sha256)
        video = session.query(Video).filter(Video.sha256 == sha256).one_or_none()
        if video is None:
            raise HTTPException(404, detail="video not found")
        source = Path(video.path)
        if not source.is_file():
            raise HTTPException(410, detail="video source is missing")
        return video

    def stream_path(path: Path, request: Request):
        size = path.stat().st_size
        try:
            selected = parse_range_header(request.headers.get("range"), size)
        except RangeNotSatisfiable as exc:
            raise HTTPException(
                416,
                detail=str(exc),
                headers={"Content-Range": f"bytes */{size}"},
            ) from exc

        headers = {
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, max-age=3600",
            "Content-Length": str(selected.length if selected else size),
        }
        status = 200
        if selected is not None:
            status = 206
            headers["Content-Range"] = selected.content_range
        return StreamingResponse(
            iter_file_range(path, selected),
            status_code=status,
            media_type=_mime(path),
            headers=headers,
        )

    def create_job(video: Video, job_type: str, payload: dict[str, Any]) -> int:
        with session_scope(Session) as session:
            row = MediaJob(
                video_id=video.id,
                job_type=job_type,
                status="queued",
                progress=0.0,
                payload_json=json.dumps(payload),
                source_fingerprint=_source_fingerprint(video),
                processor_version=_EDIT_VERSION,
            )
            session.add(row)
            session.flush()
            return row.id

    def update_job(job_id: int, **changes: Any) -> None:
        with session_scope(Session) as session:
            row = session.get(MediaJob, job_id)
            if row is None:
                return
            for key, value in changes.items():
                setattr(row, key, value)
            row.updated_at = utcnow()

    def submit_job(
        video: Video,
        job_type: str,
        payload: dict[str, Any],
        work: Callable[[Any], Any],
    ) -> int:
        db_job_id = create_job(video, job_type, payload)

        def wrapped(context):
            update_job(db_job_id, status="running", started_at=utcnow())

            def report(progress: float | None, message: str) -> None:
                pct = max(0.0, min(100.0, float(progress or 0.0)))
                context.report(pct, message)
                update_job(db_job_id, progress=pct)
                if publish is not None:
                    publish({
                        "stage": "media",
                        "current": int(pct),
                        "total": 100,
                        "message": message,
                    })

            context.media_report = report
            try:
                result = work(context)
            except JobCancelled:
                update_job(db_job_id, status="cancelled", finished_at=utcnow())
                raise
            except BaseException as exc:
                update_job(
                    db_job_id,
                    status="failed",
                    error_text=str(exc),
                    finished_at=utcnow(),
                )
                raise
            output_path = None
            if hasattr(result, "output_path"):
                output_path = str(result.output_path)
            elif hasattr(result, "path") and result.path is not None:
                output_path = str(result.path)
            update_job(
                db_job_id,
                status="completed",
                progress=100.0,
                output_path=output_path,
                finished_at=utcnow(),
            )
            return result

        engine_id = engine.submit(wrapped, kind=job_type)
        with engine_lock:
            engine_ids[db_job_id] = engine_id
        return db_job_id

    @router.get("/api/videos/{sha256}/stream")
    def video_stream(sha256: str, request: Request):
        with session_scope(Session) as session:
            video = video_for_sha(session, sha256)
            source = Path(video.path)
        return stream_path(source, request)

    @router.get("/api/videos/{sha256}/proxy/stream")
    def proxy_stream(sha256: str, request: Request):
        with session_scope(Session) as session:
            video = video_for_sha(session, sha256)
            proxy = (
                session.query(VideoProxy)
                .filter(
                    VideoProxy.video_id == video.id,
                    VideoProxy.status == "ready",
                    VideoProxy.source_fingerprint == _source_fingerprint(video),
                    VideoProxy.processor_version == _PROXY_VERSION,
                )
                .order_by(VideoProxy.updated_at.desc())
                .first()
            )
            if proxy is None or not Path(proxy.path).is_file():
                raise HTTPException(404, detail="playback proxy is not ready")
            path = Path(proxy.path)
        return stream_path(path, request)

    @router.get("/api/videos/{sha256}/playback")
    def playback_info(sha256: str):
        with session_scope(Session) as session:
            video = video_for_sha(session, sha256)
            source = Path(video.path)
            ready_proxy = (
                session.query(VideoProxy)
                .filter(
                    VideoProxy.video_id == video.id,
                    VideoProxy.status == "ready",
                    VideoProxy.source_fingerprint == _source_fingerprint(video),
                    VideoProxy.processor_version == _PROXY_VERSION,
                )
                .order_by(VideoProxy.updated_at.desc())
                .first()
            )
            proxy_ready = bool(ready_proxy and Path(ready_proxy.path).is_file())
        probe = probe_media(source)
        decision = decide_playback(probe)
        use_proxy = decision.needs_proxy and proxy_ready
        return {
            **decision.as_dict(),
            "source_url": f"/api/videos/{sha256}/proxy/stream" if use_proxy else f"/api/videos/{sha256}/stream",
            "proxy_ready": proxy_ready,
            "proxy_required": decision.needs_proxy,
            "duration_sec": probe.duration,
        }

    @router.post("/api/videos/{sha256}/proxy", status_code=202)
    def create_proxy(sha256: str):
        with session_scope(Session) as session:
            video = video_for_sha(session, sha256)
            video_id = video.id
            source = Path(video.path)
            fingerprint = _source_fingerprint(video)
            detached = video

        def work(context):
            result = generate_proxy(
                source,
                cfg.state_dir / "video_proxies",
                cancel=context.cancel_event,
                progress=lambda p: context.media_report((p.fraction or 0.0) * 100.0, "Preparing playback"),
            )
            if not result.success or result.path is None:
                if result.cancelled:
                    raise JobCancelled("proxy generation cancelled")
                raise RuntimeError(result.error or "proxy generation failed")
            with session_scope(Session) as session:
                row = (
                    session.query(VideoProxy)
                    .filter(
                        VideoProxy.video_id == video_id,
                        VideoProxy.preset == "editor",
                        VideoProxy.source_fingerprint == fingerprint,
                    )
                    .one_or_none()
                )
                if row is None:
                    row = VideoProxy(
                        video_id=video_id,
                        preset="editor",
                        path=str(result.path),
                        status="ready",
                        source_fingerprint=fingerprint,
                        processor_version=_PROXY_VERSION,
                    )
                    session.add(row)
                else:
                    row.path = str(result.path)
                    row.status = "ready"
                    row.updated_at = utcnow()
                row.size_bytes = result.path.stat().st_size
                row.format = "mp4"
                row.codec = "h264"
            return result

        return {"job_id": submit_job(detached, "proxy", {}, work)}

    @router.get("/api/videos/{sha256}/timeline")
    def video_timeline(sha256: str):
        with session_scope(Session) as session:
            video = video_for_sha(session, sha256)
            frames = _json_loads(video.frames_json, [])
            highlights = _json_loads(video.highlights_json, [])
            keyframes = (
                session.query(VideoKeyframe)
                .filter(VideoKeyframe.video_id == video.id)
                .order_by(VideoKeyframe.timestamp_ms)
                .all()
            )
            segments = (
                session.query(VideoSegment)
                .filter(VideoSegment.video_id == video.id)
                .order_by(VideoSegment.start_ms)
                .all()
            )
            transcript = (
                session.query(VideoTranscriptSegment)
                .filter(VideoTranscriptSegment.video_id == video.id)
                .order_by(VideoTranscriptSegment.start_ms)
                .all()
            )
            audio = (
                session.query(VideoAudioAnalysis)
                .filter(VideoAudioAnalysis.video_id == video.id)
                .order_by(VideoAudioAnalysis.updated_at.desc())
                .first()
            )
            waveform = []
            if audio and audio.waveform_blob:
                try:
                    waveform = json.loads(zlib.decompress(audio.waveform_blob))
                except (zlib.error, json.JSONDecodeError, TypeError):
                    waveform = []
            def _seg(kind: str) -> list[dict]:
                return [
                    {
                        "id": row.id,
                        "start": row.start_ms / 1000.0,
                        "end": row.end_ms / 1000.0,
                        "start_sec": row.start_ms / 1000.0,
                        "end_sec": row.end_ms / 1000.0,
                        "kind": row.kind,
                        "score": row.score,
                        "reason": row.ocr_text,
                        "decision": row.decision,
                    }
                    for row in segments
                    if row.kind == kind
                ]

            return {
                "sha256": sha256,
                "duration_sec": video.duration_sec,
                "frames": [
                    {
                        **frame,
                        "url": frame.get("url", f"/api/videos/{sha256}/frames/{frame.get('index', index)}"),
                    }
                    for index, frame in enumerate(frames)
                ],
                "highlights": _seg("highlight") or highlights,
                "scenes": _seg("scene"),
                "dead": _seg("dead"),
                "silence": _seg("silence"),
                "filler": _seg("filler"),
                "topics": _seg("topic"),
                "quotes": _seg("quote"),
                "keyframes": [
                    {
                        "id": row.id,
                        "frame_index": row.frame_index,
                        "t_sec": row.timestamp_ms / 1000.0,
                        "kind": row.kind,
                        "quality": row.quality,
                        "url": f"/api/videos/{sha256}/keyframes/{row.id}",
                    }
                    for row in keyframes
                ],
                "segments": [
                    {
                        "id": row.id,
                        "start_sec": row.start_ms / 1000.0,
                        "end_sec": row.end_ms / 1000.0,
                        "kind": row.kind,
                        "score": row.score,
                    }
                    for row in segments
                ],
                "audio": None if audio is None else {
                    "available": audio.audio_present,
                    "sample_rate": audio.sample_rate,
                    "duration_sec": (audio.duration_ms or 0) / 1000.0,
                    "integrated_lufs": audio.integrated_lufs,
                    "true_peak_db": audio.true_peak_db,
                    "silence_ratio": audio.silence_ratio,
                    "speech_ratio": audio.speech_ratio,
                    "waveform": waveform,
                    "speech_windows": _json_loads(audio.speech_segments_json, []),
                },
                "waveform": [float(item.get("peak", item.get("rms", 0.0))) for item in waveform],
                "search_hits": [
                    {
                        "start": row.start_ms / 1000.0,
                        "end": row.end_ms / 1000.0,
                        "label": row.ocr_text or row.kind,
                    }
                    for row in segments
                    if row.kind in {"semantic", "search", "scene"}
                ],
                "transcript": [
                    {
                        "start_sec": row.start_ms / 1000.0,
                        "end_sec": row.end_ms / 1000.0,
                        "text": row.text,
                        "confidence": row.confidence,
                    }
                    for row in transcript
                ],
            }

    @router.get("/api/videos/{sha256}/keyframes/{keyframe_id}")
    def keyframe_image(sha256: str, keyframe_id: int):
        from selects.server.http_cache import jpeg_file

        with session_scope(Session) as session:
            video = video_for_sha(session, sha256)
            row = session.get(VideoKeyframe, keyframe_id)
            if row is None or row.video_id != video.id or not row.image_path:
                raise HTTPException(404, detail="keyframe not found")
            path = Path(row.image_path)
            if not path.is_absolute():
                path = cfg.state_dir / path
            if not path.is_file():
                raise HTTPException(404, detail="keyframe image is missing")
        return jpeg_file(path)

    @router.post("/api/videos/{sha256}/audio/analyze", status_code=202)
    def analyze_video_audio(sha256: str):
        with session_scope(Session) as session:
            video = video_for_sha(session, sha256)
            video_id = video.id
            source = Path(video.path)
            fingerprint = _source_fingerprint(video)
            detached = video

        def work(context):
            result = analyze_audio(source, bins=512, cancel=context.cancel_event)
            if result.cancelled:
                raise JobCancelled("audio analysis cancelled")
            bins = [item.__dict__ for item in result.bins]
            speech = [item.__dict__ for item in result.speech_windows if item.speech_like]
            speech_duration = sum(max(0.0, item["end"] - item["start"]) for item in speech)
            duration = result.duration or 0.0
            lufs = 20.0 * math.log10(max(result.rms, 1e-9)) if result.available else None
            peak_db = 20.0 * math.log10(max(result.peak, 1e-9)) if result.available else None
            with session_scope(Session) as session:
                session.query(VideoAudioAnalysis).filter(
                    VideoAudioAnalysis.video_id == video_id
                ).delete(synchronize_session=False)
                session.add(VideoAudioAnalysis(
                    video_id=video_id,
                    source_fingerprint=fingerprint,
                    audio_present=result.available,
                    sample_rate=result.sample_rate,
                    duration_ms=round(duration * 1000),
                    integrated_lufs=lufs,
                    true_peak_db=peak_db,
                    silence_ratio=result.silence_ratio,
                    speech_ratio=(speech_duration / duration) if duration else 0.0,
                    waveform_blob=zlib.compress(json.dumps(bins).encode("utf-8")),
                    silence_segments_json="[]",
                    speech_segments_json=json.dumps(speech),
                    processor_version=_AUDIO_VERSION,
                ))
            context.media_report(100.0, "Audio analysis complete")
            return result

        return {"job_id": submit_job(detached, "audio", {}, work)}

    @router.post("/api/videos/{sha256}/stabilization/analyze")
    def stabilization_analysis(sha256: str):
        with session_scope(Session) as session:
            video = video_for_sha(session, sha256)
            source = Path(video.path)
        result = analyze_stabilization(source)
        return {
            "motion_score": result.motion_score,
            "sample_count": result.sample_count,
            "method": result.method,
            "suggested": result.suggested,
            "note": result.note,
        }

    @router.get("/api/videos/{sha256}/edit")
    def get_edit(sha256: str):
        with session_scope(Session) as session:
            video = video_for_sha(session, sha256)
            row = (
                session.query(VideoEdit)
                .filter(VideoEdit.video_id == video.id)
                .order_by(VideoEdit.updated_at.desc())
                .first()
            )
            return {
                "recipe": _json_loads(row.recipe_json, {}) if row else {},
                "name": row.name if row else None,
                "updated_at": row.updated_at.isoformat() if row else None,
            }

    @router.put("/api/videos/{sha256}/edit")
    def save_edit(sha256: str, body: EditBody):
        with session_scope(Session) as session:
            video = video_for_sha(session, sha256)
            try:
                recipe = EditRecipe.from_dict(body.recipe).validate(video.duration_sec)
            except EditRecipeError as exc:
                raise HTTPException(400, detail=str(exc)) from exc
            if recipe.source_sha256 and recipe.source_sha256 != sha256:
                raise HTTPException(409, detail="edit recipe belongs to another source")
            row = (
                session.query(VideoEdit)
                .filter(VideoEdit.video_id == video.id)
                .order_by(VideoEdit.updated_at.desc())
                .first()
            )
            if row is None:
                row = VideoEdit(
                    video_id=video.id,
                    recipe_json=json.dumps(recipe.to_dict()),
                    source_fingerprint=_source_fingerprint(video),
                    processor_version=_EDIT_VERSION,
                )
                session.add(row)
            else:
                row.recipe_json = json.dumps(recipe.to_dict())
                row.source_fingerprint = _source_fingerprint(video)
                row.updated_at = utcnow()
            row.name = body.name
            session.flush()
            return {"ok": True, "edit_id": row.id, "recipe": recipe.to_dict()}

    @router.put("/api/videos/{sha256}/rating")
    def rate_video(sha256: str, body: RatingBody):
        with session_scope(Session) as session:
            video = video_for_sha(session, sha256)
            row = session.get(VideoRating, video.id)
            if body.rating is None:
                if row is not None:
                    session.delete(row)
            elif row is None:
                row = VideoRating(video_id=video.id, rating=body.rating)
                session.add(row)
            else:
                row.rating = body.rating
                row.rated_at = utcnow()
                row.updated_at = utcnow()
        return {"ok": True, "sha256": sha256, "rating": body.rating}

    @router.get("/api/videos/{sha256}/tags")
    def get_video_tags(sha256: str):
        with session_scope(Session) as session:
            video = video_for_sha(session, sha256)
            rows = session.query(VideoTag).filter(VideoTag.video_id == video.id).all()
            return {"tags": [row.tag for row in rows]}

    @router.put("/api/videos/{sha256}/tags")
    def set_video_tags(sha256: str, body: TagsBody):
        clean = sorted({tag.strip() for tag in body.tags if tag.strip()})
        if any(len(tag) > 128 for tag in clean):
            raise HTTPException(400, detail="tags must be 128 characters or shorter")
        with session_scope(Session) as session:
            video = video_for_sha(session, sha256)
            session.query(VideoTag).filter(
                VideoTag.video_id == video.id,
                VideoTag.source == "manual",
            ).delete(synchronize_session=False)
            session.add_all([
                VideoTag(video_id=video.id, tag=tag, source="manual", score=1.0)
                for tag in clean
            ])
        return {"ok": True, "tags": clean}

    @router.get("/api/video-collections")
    def list_collections():
        with session_scope(Session) as session:
            rows = session.query(VideoCollection).order_by(VideoCollection.name).all()
            return {"collections": [
                {
                    "id": row.id,
                    "name": row.name,
                    "description": row.description,
                    "count": session.query(VideoCollectionItem).filter(
                        VideoCollectionItem.collection_id == row.id
                    ).count(),
                }
                for row in rows
            ]}

    @router.post("/api/video-collections", status_code=201)
    def create_collection(body: CollectionBody):
        with session_scope(Session) as session:
            if session.query(VideoCollection).filter(VideoCollection.name == body.name.strip()).first():
                raise HTTPException(409, detail="a collection with that name already exists")
            row = VideoCollection(name=body.name.strip(), description=body.description, kind="manual")
            session.add(row)
            session.flush()
            return {"id": row.id, "name": row.name, "description": row.description, "count": 0}

    @router.post("/api/video-collections/{collection_id}/videos/{sha256}")
    def add_to_collection(collection_id: int, sha256: str):
        with session_scope(Session) as session:
            collection = session.get(VideoCollection, collection_id)
            if collection is None:
                raise HTTPException(404, detail="collection not found")
            video = video_for_sha(session, sha256)
            row = session.get(VideoCollectionItem, (collection_id, video.id))
            if row is None:
                position = session.query(VideoCollectionItem).filter(
                    VideoCollectionItem.collection_id == collection_id
                ).count()
                session.add(VideoCollectionItem(
                    collection_id=collection_id,
                    video_id=video.id,
                    position=position,
                ))
        return {"ok": True}

    @router.delete("/api/video-collections/{collection_id}/videos/{sha256}")
    def remove_from_collection(collection_id: int, sha256: str):
        with session_scope(Session) as session:
            video = video_for_sha(session, sha256)
            deleted = session.query(VideoCollectionItem).filter(
                VideoCollectionItem.collection_id == collection_id,
                VideoCollectionItem.video_id == video.id,
            ).delete(synchronize_session=False)
        return {"ok": True, "deleted": bool(deleted)}

    @router.post("/api/videos/{sha256}/export", status_code=202)
    def export_video(sha256: str, body: ExportBody):
        with session_scope(Session) as session:
            video = video_for_sha(session, sha256)
            source = Path(video.path)
            detached = video
            recipe_data = body.recipe
            if recipe_data is None:
                saved = (
                    session.query(VideoEdit)
                    .filter(VideoEdit.video_id == video.id)
                    .order_by(VideoEdit.updated_at.desc())
                    .first()
                )
                recipe_data = _json_loads(saved.recipe_json, {}) if saved else {}
            try:
                recipe = EditRecipe.from_dict(recipe_data or {}).validate(video.duration_sec)
            except EditRecipeError as exc:
                raise HTTPException(400, detail=str(exc)) from exc
            if body.destination:
                destination = Path(body.destination).expanduser().resolve()
            else:
                extension = recipe.export.container
                export_dir = cfg.folder / "Selects Exports"
                destination = export_dir / f"{source.stem}-selects.{extension}"
                suffix = 2
                while destination.exists():
                    destination = export_dir / f"{source.stem}-selects-{suffix}.{extension}"
                    suffix += 1
            if destination == source.resolve():
                raise HTTPException(400, detail="export cannot overwrite the source video")

        def work(context):
            try:
                result = run_export(
                    source,
                    destination,
                    recipe,
                    mode=body.mode,
                    process_runner=context.run_process,
                    cancel_event=context.cancel_event,
                )
            except ExportError:
                raise
            context.media_report(100.0, "Export complete")
            return result

        payload = {"destination": str(destination), "mode": body.mode, "recipe": recipe.to_dict()}
        return {"job_id": submit_job(detached, "export", payload, work)}

    @router.get("/api/media/jobs/{job_id}")
    def media_job(job_id: int):
        with session_scope(Session) as session:
            row = session.get(MediaJob, job_id)
            if row is None:
                raise HTTPException(404, detail="media job not found")
            with engine_lock:
                engine_id = engine_ids.get(job_id)
            message = None
            if engine_id:
                try:
                    snapshot = engine.snapshot(engine_id)
                    message = snapshot.message
                except KeyError:
                    pass
            return {
                "id": row.id,
                "video_id": row.video_id,
                "type": row.job_type,
                "status": row.status,
                "progress": row.progress,
                "message": message,
                "output_path": row.output_path,
                "error": row.error_text,
                "cancel_requested": row.cancel_requested,
            }

    @router.get("/api/media/jobs/{job_id}/output")
    def media_job_output(job_id: int, request: Request):
        with session_scope(Session) as session:
            row = session.get(MediaJob, job_id)
            if row is None:
                raise HTTPException(404, detail="media job not found")
            if row.status != "completed" or not row.output_path:
                raise HTTPException(409, detail="media job output is not ready")
            path = Path(row.output_path)
            if not path.is_file():
                raise HTTPException(410, detail="media job output is missing")
        return stream_path(path, request)

    @router.post("/api/media/jobs/{job_id}/cancel")
    def cancel_media_job(job_id: int):
        with session_scope(Session) as session:
            row = session.get(MediaJob, job_id)
            if row is None:
                raise HTTPException(404, detail="media job not found")
            if row.status in {"completed", "failed", "cancelled"}:
                return {"ok": False, "status": row.status}
            row.cancel_requested = True
            row.updated_at = utcnow()
        with engine_lock:
            engine_id = engine_ids.get(job_id)
        cancelled = bool(engine_id and engine.cancel(engine_id))
        if cancelled:
            update_job(job_id, status="cancelled", finished_at=utcnow())
        return {"ok": cancelled, "status": "cancelled" if cancelled else "cancelling"}

    app.include_router(router)
