"""Shared full-pipeline runner used by CLI ``--pass all``, the startup
auto-index, the /api/libraries/{id}/index endpoint, and the folder watcher.

Runs the stage sequence:

index → video → classical → embed → tag → ram_tag → smart_tag →
face_embed → persons → moment → story → thematic → date

publishing progress dicts to a caller-supplied ``publish`` callable.
An optional ``should_cancel`` callable lets the caller stop the run
between (and, for most stages, during) stages.
"""
from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Callable, Iterable, Optional

from selects.config import FolderConfig
from selects.pipeline import PipelineCancelled

log = logging.getLogger(__name__)

PublishFn = Callable[[dict], None]
CancelFn = Callable[[], bool]

# Logical name → (module, callable). Insertion order is the default pipeline.
# CLI named --pass values and the GUI runner share this so they cannot drift.
STAGE_FUNCS: dict[str, tuple[str, str]] = {
    "index": ("selects.indexer.orchestrator", "index_folder"),
    "video": ("selects.video", "run_video_stage"),
    "classical": ("selects.pipeline", "run_classical_stage"),
    "embed": ("selects.ml.embed", "run_embedding_stage"),
    "tag": ("selects.ml.tags", "run_tag_stage"),
    "ram_tag": ("selects.ml.ram_tags", "run_ram_tagging_stage"),
    "smart_tag": ("selects.ml.smart_clusters", "run_smart_cluster_stage"),
    "face_embed": ("selects.ml.faces", "run_face_embedding_stage"),
    "persons": ("selects.ml.persons", "run_person_stage"),
    "moment": ("selects.ml.moments", "run_moment_stage"),
    "story": ("selects.ml.stories", "run_story_stage"),
    "thematic": ("selects.ml.thematic_clusters", "run_thematic_stage"),
    "date": ("selects.ml.thematic_clusters", "run_date_stage"),
}

DEFAULT_STAGE_ORDER: tuple[str, ...] = tuple(STAGE_FUNCS)

# speed_mode=fast skips these (still runs index/video/classical/embed/tag and rebuilds
# that don't need the skipped stages' outputs).
FAST_SKIP_STAGES = frozenset({"ram_tag", "smart_tag", "face_embed", "persons"})

# Full-rebuild stages: skip when this run indexed+embedded nothing AND output already exists.
REBUILD_STAGES = frozenset({"smart_tag", "persons", "moment", "story", "thematic", "date"})

# Friendly, human-readable status lines so the user sees something is happening
# (the raw stage name / batch index is not reassuring). {n}/{total} is appended
# by the caller when a total is known.
_STAGE_BLURB = {
    "index": "Scanning your photos",
    "video": "Skimming through videos",
    "classical": "Checking focus, exposure & framing",
    "embed": "Learning what's in each photo",
    "tag": "Tagging scenes, places & subjects",
    "ram_tag": "Labelling objects in each photo",
    "smart_tag": "Grouping similar shots",
    "face_embed": "Finding faces",
    "persons": "Grouping people",
    "moment": "Clustering bursts into moments",
    "story": "Piecing your trip into stories",
    "thematic": "Building scene collections",
    "date": "Grouping photos by day",
}


def get_stage_callable(name: str):
    """Import and return the callable for a pipeline stage name."""
    module_name, attr = STAGE_FUNCS[name]
    return getattr(importlib.import_module(module_name), attr)


def _stage_has_output(cfg: FolderConfig, stage: str) -> bool:
    """True if *stage* already has at least one output row in this library."""
    from selects.db import init_db, session_scope

    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        if stage == "story":
            from selects.db.models import Story

            return s.query(Story.id).first() is not None
        if stage == "persons":
            from selects.db.models import Person

            return s.query(Person.id).first() is not None
        if stage == "moment":
            from selects.db.models import Moment

            return s.query(Moment.id).first() is not None
        if stage == "smart_tag":
            from selects.db.models import PhotoTag

            return (
                s.query(PhotoTag.photo_id)
                .filter(PhotoTag.source.in_(("posting", "lookback")))
                .first()
                is not None
            )
        if stage == "thematic":
            from selects.db.models import PhotoTag

            return (
                s.query(PhotoTag.photo_id)
                .filter(PhotoTag.source == "thematic")
                .first()
                is not None
            )
        if stage == "date":
            from selects.db.models import PhotoTag

            return (
                s.query(PhotoTag.photo_id)
                .filter(PhotoTag.source == "date")
                .first()
                is not None
            )
    return False


def _should_skip_rebuild(stage: str, cfg: FolderConfig, counts: dict[str, int]) -> bool:
    if stage not in REBUILD_STAGES:
        return False
    if not _stage_has_output(cfg, stage):
        return False
    # Story skip keeps the existing upstream signal (index + tag); other rebuild
    # stages skip when this run indexed and embedded nothing.
    if stage == "story":
        return counts.get("index", 1) == 0 and counts.get("tag", 1) == 0
    return counts.get("index", 1) == 0 and counts.get("embed", 1) == 0


def _invoke_stage(
    name: str,
    cfg: FolderConfig,
    on_progress,
    paths: Optional[Iterable[Path]] = None,
) -> int:
    fn = get_stage_callable(name)
    if name == "index":
        return fn(cfg, on_progress, paths=paths) or 0
    return fn(cfg, on_progress) or 0


def run_pipeline_stages(
    cfg: FolderConfig,
    publish: PublishFn,
    should_cancel: Optional[CancelFn] = None,
    paths: Optional[Iterable[Path]] = None,
) -> dict[str, int]:
    """Run all pipeline stages for *cfg*, publishing progress via *publish*.

    Blocking / CPU-bound — call from a worker thread, not the event loop.

    Rebuild stages (smart_tag, persons, moment, story, thematic, date) are
    skipped when this run indexed and embedded nothing *and* the stage already
    has output rows. Incremental stages no-op on empty pending themselves.

    ``paths``, when given, is forwarded to the index stage so a watcher can
    ingest only newly-detected files.

    Returns a dict of stage name → rows processed (0 if skipped/failed).
    """

    def _cancelled() -> bool:
        return should_cancel is not None and should_cancel()

    def cb(stage: str):
        blurb = _STAGE_BLURB.get(stage, stage)

        def _progress(i, total, name):
            # Interrupt mid-stage when possible (stages that don't swallow it).
            if _cancelled():
                raise PipelineCancelled()
            msg = f"{blurb}… {i:,}/{total:,}" if total else f"{blurb}…"
            publish({"stage": stage, "current": i, "total": total, "message": msg})

        return _progress

    counts: dict[str, int] = {}
    fast = cfg.speed_mode == "fast"

    try:
        for name in DEFAULT_STAGE_ORDER:
            if _cancelled():
                raise PipelineCancelled()
            if fast and name in FAST_SKIP_STAGES:
                log.info("%s stage: skipped (speed_mode=fast)", name)
                counts[name] = 0
                continue
            if _should_skip_rebuild(name, cfg, counts):
                log.info("%s stage: nothing new since last build, skipping", name)
                counts[name] = 0
                continue
            try:
                counts[name] = _invoke_stage(name, cfg, cb(name), paths=paths) or 0
            except PipelineCancelled:
                raise
            except ImportError as exc:
                log.warning("%s stage skipped (missing extra): %s", name, exc)
                publish({"stage": "error", "message": str(exc)})
                counts[name] = 0
            except Exception as exc:
                log.exception("%s stage failed: %s", name, exc)
                publish({"stage": "error", "message": str(exc)})
                counts[name] = 0
    except PipelineCancelled:
        publish(
            {
                "stage": "cancelled",
                "current": 0,
                "total": 0,
                "message": "Stopped — you can pick up where you left off",
            }
        )
        return counts

    publish({"stage": "done", "current": 1, "total": 1, "message": "All set!"})
    return counts
