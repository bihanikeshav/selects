"""Video-culling HTTP endpoints.

Registered the same way as :mod:`travelcull.server.recap_routes` — a
``register_video_routes(app, cfg, publish)`` call the orchestrator
(``build_app``) is expected to wire in (see wiring_needed in the feature's
final report). ``cfg`` may be the ``ActiveConfigProxy`` so these follow
whichever library is active.

Endpoints:
    GET  /api/videos                          list videos with scores / flags
    GET  /api/videos/{sha256}/frames          sampled filmstrip + per-frame metrics
    GET  /api/videos/{sha256}/frames/{index}  one filmstrip JPEG
    POST /api/videos/process                  run the analysis stage (background)
    GET  /api/videos/process/status           poll the background analysis
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable, Optional

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from travelcull.config import FolderConfig
from travelcull.db import init_db, session_scope
from travelcull.db.models import Video
from travelcull.video import frames_dir_for, run_video_stage

PublishFn = Callable[[dict], None]


class VideoOut(BaseModel):
    id: int
    sha256: Optional[str]
    path: str
    name: str
    format: Optional[str]
    width: Optional[int]
    height: Optional[int]
    duration_sec: Optional[float]
    fps: Optional[float]
    taken_at: Optional[str]
    thumb_url: Optional[str]
    processed: bool
    sharpness: Optional[float]
    exposure: Optional[float]
    dead_footage: Optional[bool]
    highlight_count: int
    highlights: list[dict]
    sampled_frames: int


class VideoListOut(BaseModel):
    videos: list[VideoOut]
    total: int
    processed: int
    dead_footage_count: int


class VideoFrameOut(BaseModel):
    index: int
    frame_index: int
    t_sec: float
    blur: float
    exposure: float
    quality: float
    good: bool
    url: str


class VideoFramesOut(BaseModel):
    sha256: str
    path: str
    duration_sec: Optional[float]
    dead_footage: Optional[bool]
    highlights: list[dict]
    best_frame_index: Optional[int]
    frames: list[VideoFrameOut]


def _video_to_out(v: Video) -> VideoOut:
    highlights = json.loads(v.highlights_json) if v.highlights_json else []
    frames = json.loads(v.frames_json) if v.frames_json else []
    return VideoOut(
        id=v.id,
        sha256=v.sha256,
        path=v.path,
        name=Path(v.path).name,
        format=v.format,
        width=v.width,
        height=v.height,
        duration_sec=v.duration_sec,
        fps=v.fps,
        taken_at=v.taken_at.isoformat() if v.taken_at else None,
        thumb_url=f"/api/thumb/{v.sha256}" if v.sha256 else None,
        processed=v.processed_at is not None,
        sharpness=v.sharpness,
        exposure=v.exposure,
        dead_footage=v.dead_footage,
        highlight_count=len(highlights),
        highlights=highlights,
        sampled_frames=len(frames),
    )


def register_video_routes(
    app: FastAPI,
    cfg: FolderConfig,
    publish: Optional[PublishFn] = None,
) -> None:
    # Router is created per-call: a module-level router would accumulate
    # duplicate routes (closing over the first cfg) when build_app is
    # called more than once, e.g. across tests.
    router = APIRouter()

    # Single in-flight analysis guard, shared across requests.
    state = {"running": False, "error": None}
    lock = threading.Lock()

    @router.get("/api/videos", response_model=VideoListOut)
    def list_videos():
        Session = init_db(cfg.db_path)
        with session_scope(Session) as s:
            rows = s.query(Video).order_by(Video.taken_at.is_(None), Video.taken_at, Video.path).all()
            out = [_video_to_out(v) for v in rows]
        return VideoListOut(
            videos=out,
            total=len(out),
            processed=sum(1 for v in out if v.processed),
            dead_footage_count=sum(1 for v in out if v.dead_footage),
        )

    @router.get("/api/videos/{sha256}/frames", response_model=VideoFramesOut)
    def video_frames(sha256: str):
        Session = init_db(cfg.db_path)
        with session_scope(Session) as s:
            v = s.query(Video).filter(Video.sha256 == sha256).one_or_none()
            if v is None:
                raise HTTPException(404, detail="video not found")
            if v.frames_json is None:
                raise HTTPException(409, detail="video not analysed yet — POST /api/videos/process first")
            frames_raw = json.loads(v.frames_json)
            highlights = json.loads(v.highlights_json) if v.highlights_json else []
            return VideoFramesOut(
                sha256=sha256,
                path=v.path,
                duration_sec=v.duration_sec,
                dead_footage=v.dead_footage,
                highlights=highlights,
                best_frame_index=v.best_frame_index,
                frames=[
                    VideoFrameOut(**f, url=f"/api/videos/{sha256}/frames/{f['index']}")
                    for f in frames_raw
                ],
            )

    @router.get("/api/videos/{sha256}/frames/{index}")
    def video_frame_image(sha256: str, index: int):
        if index < 0 or index > 999:
            raise HTTPException(404, detail="frame not found")
        path = frames_dir_for(cfg, sha256) / f"{index:02d}.jpg"
        if not path.exists():
            raise HTTPException(404, detail="frame not found")
        return FileResponse(path, media_type="image/jpeg")

    @router.post("/api/videos/process")
    def process_videos():
        with lock:
            if state["running"]:
                raise HTTPException(409, detail="video analysis is already in progress")
            state["running"] = True
            state["error"] = None

        def worker() -> None:
            def cb(i: int, total: int, name: str) -> None:
                if publish is not None:
                    publish({"stage": "video", "current": i, "total": total, "message": name})

            try:
                run_video_stage(cfg, cb)
            except Exception as e:  # noqa: BLE001 — surfaced via /process/status
                with lock:
                    state["error"] = str(e)
            finally:
                with lock:
                    state["running"] = False

        threading.Thread(target=worker, daemon=True).start()
        return {"started": True}

    @router.get("/api/videos/process/status")
    def process_status():
        with lock:
            return {"running": state["running"], "error": state["error"]}

    app.include_router(router)
