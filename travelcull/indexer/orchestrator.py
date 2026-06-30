from __future__ import annotations

import logging
from typing import Callable

from sqlalchemy import select

from travelcull.config import FolderConfig
from travelcull.db import init_db, session_scope
from travelcull.db.models import PipelineState, Photo, Video
from travelcull.decode import decode
from travelcull.decode.video import decode_first_frame, probe
from travelcull.indexer.exif import read_exif
from travelcull.indexer.preview import write_previews
from travelcull.indexer.walker import FileKind, sha256_of, walk_supported

log = logging.getLogger(__name__)
ProgressCb = Callable[[int, int, str], None] | None


def index_folder(cfg: FolderConfig, on_progress: ProgressCb = None) -> int:
    """Walk the folder and add new files. Returns count of new rows."""
    files = list(walk_supported(cfg.folder))
    total = len(files)
    added = 0

    Session = init_db(cfg.db_path)

    with session_scope(Session) as s:
        existing_photo = {row[0] for row in s.execute(select(Photo.sha256)).all()}
        existing_video = {row[0] for row in s.execute(select(Video.sha256)).all()}
    existing = existing_photo | existing_video

    for i, (path, kind) in enumerate(files, start=1):
        if on_progress:
            on_progress(i, total, str(path.name))
        try:
            sha = sha256_of(path)
            if sha in existing:
                continue

            if kind == FileKind.VIDEO:
                added += _ingest_video(cfg, Session, path, sha)
            else:
                added += _ingest_photo(cfg, Session, path, sha, kind)
            existing.add(sha)
        except Exception as exc:
            log.warning("Failed to ingest %s: %s", path, exc)

    return added


def _ingest_photo(cfg: FolderConfig, Session, path, sha: str, kind: FileKind) -> int:
    img = decode(path, kind)
    exif = read_exif(path)
    thumb_path, preview_path = write_previews(img, sha, cfg.thumbs_dir, cfg.previews_dir)

    with session_scope(Session) as s:
        p = Photo(
            path=str(path),
            sha256=sha,
            mtime=path.stat().st_mtime,
            size_bytes=path.stat().st_size,
            format=kind.value,
            width=exif.width or img.shape[1],
            height=exif.height or img.shape[0],
            taken_at=exif.taken_at,
            gps_lat=exif.gps_lat,
            gps_lon=exif.gps_lon,
            camera=exif.camera,
            thumb_path=str(thumb_path.relative_to(cfg.state_dir)),
            preview_path=str(preview_path.relative_to(cfg.state_dir)),
        )
        s.add(p)
        s.flush()
        s.add(PipelineState(photo_id=p.id))
    return 1


def _ingest_video(cfg: FolderConfig, Session, path, sha: str) -> int:
    meta = probe(path)
    frame = decode_first_frame(path)
    exif = read_exif(path)
    thumb_path, preview_path = write_previews(frame, sha, cfg.thumbs_dir, cfg.previews_dir)

    with session_scope(Session) as s:
        v = Video(
            path=str(path),
            sha256=sha,
            mtime=path.stat().st_mtime,
            size_bytes=path.stat().st_size,
            format=meta.codec,
            width=meta.width,
            height=meta.height,
            duration_sec=meta.duration_sec,
            taken_at=exif.taken_at,
            thumb_path=str(thumb_path.relative_to(cfg.state_dir)),
            preview_path=str(preview_path.relative_to(cfg.state_dir)),
        )
        s.add(v)
    return 1
