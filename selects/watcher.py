"""Polling-based watch-folder + incremental import.

No new required dependency: polls the filesystem every ``interval`` seconds
(default 60) on a background thread. If the optional ``watchdog`` package is
importable, that fact is exposed via :data:`HAS_WATCHDOG`, but the poller
itself is always used for the actual detection loop — this keeps behaviour
identical across environments and avoids a hard dependency.

Detection: compares files under the library root against the ``photos`` /
``videos`` tables by path + mtime. A path is a "new file candidate" when it
either has no matching row, or its mtime differs from the recorded one.

Debounce: a candidate must be observed with an *unchanged* (size, mtime) pair
across two consecutive polls before it is considered stable and eligible for
indexing. This avoids picking up a file mid-copy.

Once a batch of stable new files is found, they are indexed incrementally
(only those paths — see :func:`selects.indexer.orchestrator.index_folder`'s
``paths=`` parameter) and run through the rest of the pipeline stages, which
already restrict themselves to not-yet-processed rows via ``PipelineState``.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from sqlalchemy import select

from selects.config import FolderConfig
from selects.indexer.walker import walk_supported

log = logging.getLogger(__name__)

try:
    import watchdog  # noqa: F401

    HAS_WATCHDOG = True
except Exception:
    HAS_WATCHDOG = False

PublishFn = Callable[[dict], None]

DEFAULT_INTERVAL_SECONDS = 60


# --------------------------------------------------------------------------- #
# Persisted per-library watch settings
# --------------------------------------------------------------------------- #


@dataclass
class WatchSettings:
    enabled: bool = False
    interval: int = DEFAULT_INTERVAL_SECONDS
    last_run: Optional[str] = None
    new_files_found: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _settings_path(cfg: FolderConfig) -> Path:
    return cfg.state_dir / "watch_settings.json"


def load_watch_settings(cfg: FolderConfig) -> WatchSettings:
    p = _settings_path(cfg)
    if not p.exists():
        return WatchSettings()
    try:
        data = json.loads(p.read_text())
        return WatchSettings(
            enabled=bool(data.get("enabled", False)),
            interval=int(data.get("interval", DEFAULT_INTERVAL_SECONDS)),
            last_run=data.get("last_run"),
            new_files_found=int(data.get("new_files_found", 0)),
        )
    except Exception:
        return WatchSettings()


def save_watch_settings(cfg: FolderConfig, settings: WatchSettings) -> None:
    p = _settings_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.parent / (p.name + ".tmp")
    tmp.write_text(json.dumps(settings.to_dict(), indent=2))
    tmp.replace(p)


# --------------------------------------------------------------------------- #
# Detection + debounce
# --------------------------------------------------------------------------- #


def _existing_path_mtimes(cfg: FolderConfig) -> dict[str, float]:
    """Return {path: mtime} for every row already present in photos/videos."""
    from selects.db import init_db, session_scope
    from selects.db.models import Photo, Video

    Session = init_db(cfg.db_path)
    out: dict[str, float] = {}
    with session_scope(Session) as s:
        for path, mtime in s.execute(select(Photo.path, Photo.mtime)).all():
            out[path] = mtime or 0.0
        for path, mtime in s.execute(select(Video.path, Video.mtime)).all():
            out[path] = mtime or 0.0
    return out


def detect_candidates(cfg: FolderConfig) -> list[Path]:
    """Return paths under the library root that are new or modified relative
    to what's already indexed (by path + mtime)."""
    existing = _existing_path_mtimes(cfg)
    candidates: list[Path] = []
    for path, _kind in walk_supported(cfg.folder):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        known = existing.get(str(path))
        if known is None or abs(known - mtime) > 1e-6:
            candidates.append(path)
    return candidates


class Debouncer:
    """Tracks (size, mtime) of candidate paths across polls; a path is
    considered "stable" (safe to index) once it is seen twice in a row with an
    unchanged (size, mtime) pair — i.e. it isn't still being written to."""

    def __init__(self) -> None:
        self._pending: dict[str, tuple[int, float]] = {}

    def poll(self, candidates: list[Path]) -> list[Path]:
        stable: list[Path] = []
        seen_keys: set[str] = set()
        next_pending: dict[str, tuple[int, float]] = {}

        for path in candidates:
            key = str(path)
            seen_keys.add(key)
            try:
                st = path.stat()
            except OSError:
                continue
            fingerprint = (st.st_size, st.st_mtime)
            prev = self._pending.get(key)
            if prev is not None and prev == fingerprint:
                stable.append(path)
                # Once stable we drop it from pending; if indexing fails to
                # pick it up (e.g. exception) it will simply reappear as a
                # fresh candidate on next poll's detect_candidates() call.
            else:
                next_pending[key] = fingerprint

        self._pending = next_pending
        return stable


# --------------------------------------------------------------------------- #
# Incremental pipeline run
# --------------------------------------------------------------------------- #


def run_incremental_index(
    cfg: FolderConfig,
    paths: list[Path],
    publish: Optional[PublishFn] = None,
) -> int:
    """Index just *paths* (new/changed files), then run the remaining pipeline
    stages. Those stages already restrict themselves to rows whose
    ``PipelineState`` flags aren't done yet, so no further subsetting is
    needed beyond the initial ingest.

    Returns the number of new rows ingested.
    """
    from selects.indexer.orchestrator import index_folder
    from selects.pipeline import run_classical_stage
    from selects.ml.embed import run_embedding_stage
    from selects.ml.tags import run_tag_stage
    from selects.ml.stories import run_story_stage

    def cb(stage: str):
        def _progress(i, total, name):
            if publish:
                publish({"stage": stage, "current": i, "total": total, "message": name})

        return _progress

    added = index_folder(cfg, cb("index"), paths=paths)
    if added:
        run_classical_stage(cfg, cb("classical"))
        run_embedding_stage(cfg, cb("embed"))
        run_tag_stage(cfg, cb("tag"))
        run_story_stage(cfg, cb("story"))
    return added


# --------------------------------------------------------------------------- #
# Background watcher thread
# --------------------------------------------------------------------------- #


class LibraryWatcher:
    """Polls one library's folder on a background thread and, when new files
    are found and stable, indexes them incrementally."""

    def __init__(
        self,
        cfg: FolderConfig,
        publish: Optional[PublishFn] = None,
        interval: Optional[int] = None,
    ) -> None:
        self.cfg = cfg
        self.publish = publish
        settings = load_watch_settings(cfg)
        self.interval = interval or settings.interval or DEFAULT_INTERVAL_SECONDS
        self._debouncer = Debouncer()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    # ---- lifecycle ---------------------------------------------------- #
    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._lock:
            if self.is_running:
                return
            self._stop_event.clear()
            settings = load_watch_settings(self.cfg)
            settings.enabled = True
            settings.interval = self.interval
            save_watch_settings(self.cfg, settings)
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()
            settings = load_watch_settings(self.cfg)
            settings.enabled = False
            save_watch_settings(self.cfg, settings)

    def status(self) -> dict:
        settings = load_watch_settings(self.cfg)
        d = settings.to_dict()
        d["running"] = self.is_running
        return d

    # ---- loop ----------------------------------------------------------- #
    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception:
                log.exception("watch poll failed")
            if self._stop_event.wait(self.interval):
                break

    def poll_once(self) -> int:
        """Run a single detection + debounce + (maybe) index cycle. Returns
        the number of files newly indexed (0 if none were stable yet)."""
        candidates = detect_candidates(self.cfg)
        stable = self._debouncer.poll(candidates)

        settings = load_watch_settings(self.cfg)
        settings.last_run = datetime.now(timezone.utc).isoformat()

        added = 0
        if stable:
            added = run_incremental_index(self.cfg, stable, self.publish)
            settings.new_files_found = added
            if added and self.publish:
                self.publish(
                    {
                        "type": "watch",
                        "stage": "watch",
                        "new_files_found": added,
                        "message": f"{added} new file(s) indexed",
                    }
                )
        save_watch_settings(self.cfg, settings)
        return added


# --------------------------------------------------------------------------- #
# Per-library registry of watcher instances (keyed by resolved folder path)
# --------------------------------------------------------------------------- #

_watchers: dict[str, LibraryWatcher] = {}
_watchers_lock = threading.Lock()


def get_or_create_watcher(
    cfg: FolderConfig,
    publish: Optional[PublishFn] = None,
) -> LibraryWatcher:
    key = str(cfg.folder)
    with _watchers_lock:
        w = _watchers.get(key)
        if w is None:
            w = LibraryWatcher(cfg, publish=publish)
            _watchers[key] = w
        elif publish is not None:
            w.publish = publish
        return w
