from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Iterable, Optional

from sqlalchemy import select

from selects.config import FolderConfig
from selects.db import init_db, session_scope
from selects.db.models import PipelineState, Photo, Video
from selects.decode import decode
from selects.decode.video import decode_first_frame, probe
from selects.indexer.exif import read_exif
from selects.indexer.preview import write_previews
from selects.indexer.walker import FileKind, classify_paths, sha256_of, walk_supported

log = logging.getLogger(__name__)
ProgressCb = Callable[[int, int, str], None] | None

# Video analysis columns cleared when the file at a path is replaced.
_VIDEO_ANALYSIS_ATTRS = (
    "fps",
    "frame_count",
    "best_frame_index",
    "sharpness",
    "exposure",
    "dead_footage",
    "frames_json",
    "highlights_json",
    "siglip",
    "processed_at",
)


def index_folder(
    cfg: FolderConfig,
    on_progress: ProgressCb = None,
    paths: Optional[Iterable[Path]] = None,
) -> int:
    """Walk the folder (or an explicit *paths* subset) and upsert files by path.

    Identity is path: a new path is INSERT even if sha256 collides with another
    row. An existing path with unchanged sha is skipped. An existing path whose
    content changed is UPDATE (thumbs rewritten, pipeline flags reset).

    When *paths* is given, only those paths are classified/ingested instead of
    walking the whole tree — used by the watch-folder poller to index just the
    newly-detected files without re-scanning the entire library.

    Returns count of new rows.
    """
    # Emit an immediate ping so the UI shows "Scanning folder…" while the
    # (potentially slow, count-unknown) directory walk runs, instead of an
    # empty bar with no feedback.
    if on_progress:
        on_progress(0, 0, "Scanning folder…")
    files = list(classify_paths(paths)) if paths is not None else list(walk_supported(cfg.folder))
    total = len(files)
    added = 0
    failed = 0

    Session = init_db(cfg.db_path)

    with session_scope(Session) as s:
        photo_by_path = {
            path: (id_, sha)
            for path, id_, sha in s.execute(select(Photo.path, Photo.id, Photo.sha256))
        }
        video_by_path = {
            path: (id_, sha)
            for path, id_, sha in s.execute(select(Video.path, Video.id, Video.sha256))
        }

    for i, (path, kind) in enumerate(files, start=1):
        if on_progress:
            on_progress(i, total, str(path.name))
        try:
            sha = sha256_of(path)
            key = str(path)
            if kind == FileKind.VIDEO:
                existing = video_by_path.get(key)
                if existing is not None and existing[1] == sha:
                    continue
                video_id = existing[0] if existing is not None else None
                added += _ingest_video(cfg, Session, path, sha, video_id=video_id)
                video_by_path[key] = (video_id or 0, sha)
            else:
                existing = photo_by_path.get(key)
                if existing is not None and existing[1] == sha:
                    continue
                photo_id = existing[0] if existing is not None else None
                added += _ingest_photo(cfg, Session, path, sha, kind, photo_id=photo_id)
                photo_by_path[key] = (photo_id or 0, sha)
        except Exception as exc:
            failed += 1
            log.warning("Failed to ingest %s: %s", path, exc)

    if failed:
        log.warning("%d file(s) could not be read during index", failed)
        if on_progress:
            on_progress(total, total, f"{failed} file(s) could not be read")

    return added


def _ingest_photo(
    cfg: FolderConfig,
    Session,
    path,
    sha: str,
    kind: FileKind,
    photo_id: Optional[int] = None,
) -> int:
    img = decode(path, kind)
    exif = read_exif(path)
    thumb_path, preview_path = write_previews(img, sha, cfg.thumbs_dir, cfg.previews_dir)
    st = path.stat()

    with session_scope(Session) as s:
        p = s.get(Photo, photo_id) if photo_id is not None else None
        if p is None:
            p = Photo(path=str(path))
            s.add(p)
            s.flush()
            s.add(PipelineState(photo_id=p.id))
            is_new = True
        else:
            is_new = False
            _reset_pipeline(s, p.id)

        p.sha256 = sha
        p.mtime = st.st_mtime
        p.size_bytes = st.st_size
        p.format = kind.value
        p.width = img.shape[1]
        p.height = img.shape[0]
        p.taken_at = exif.taken_at
        p.gps_lat = exif.gps_lat
        p.gps_lon = exif.gps_lon
        p.camera = exif.camera
        p.thumb_path = str(thumb_path.relative_to(cfg.state_dir))
        p.preview_path = str(preview_path.relative_to(cfg.state_dir))
    return 1 if is_new else 0


def _ingest_video(
    cfg: FolderConfig, Session, path, sha: str, video_id: Optional[int] = None
) -> int:
    meta = probe(path)
    frame = decode_first_frame(path)
    exif = read_exif(path)
    thumb_path, preview_path = write_previews(frame, sha, cfg.thumbs_dir, cfg.previews_dir)
    st = path.stat()

    with session_scope(Session) as s:
        v = s.get(Video, video_id) if video_id is not None else None
        if v is None:
            v = Video(path=str(path))
            s.add(v)
            is_new = True
        else:
            is_new = False
            for attr in _VIDEO_ANALYSIS_ATTRS:
                setattr(v, attr, None)

        v.sha256 = sha
        v.mtime = st.st_mtime
        v.size_bytes = st.st_size
        v.format = meta.codec
        v.width = meta.width
        v.height = meta.height
        v.duration_sec = meta.duration_sec
        v.taken_at = exif.taken_at
        v.thumb_path = str(thumb_path.relative_to(cfg.state_dir))
        v.preview_path = str(preview_path.relative_to(cfg.state_dir))
    return 1 if is_new else 0


def _reset_pipeline(s, photo_id: int) -> None:
    ps = s.get(PipelineState, photo_id)
    if ps is None:
        s.add(PipelineState(photo_id=photo_id))
        return
    ps.classical_done = False
    ps.embedding_done = False
    ps.vl_done = False
    ps.ordering_done = False
    ps.error = None
