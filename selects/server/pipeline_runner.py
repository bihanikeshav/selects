"""Shared full-pipeline runner used by both the startup auto-index and the
/api/libraries/{id}/index endpoint.

Runs the stage sequence index -> video -> classical -> embed -> tag -> story,
publishing progress dicts to a caller-supplied ``publish`` callable (which is
responsible for getting those dicts onto the websocket progress bus in a
thread-safe way). An optional ``should_cancel`` callable lets the caller stop
the run between (and, for most stages, during) stages.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from selects.config import FolderConfig

log = logging.getLogger(__name__)

PublishFn = Callable[[dict], None]
CancelFn = Callable[[], bool]


class PipelineCancelled(Exception):
    """Raised out of a progress callback to unwind a stage when cancelled."""


# Friendly, human-readable status lines so the user sees something is happening
# (the raw stage name / batch index is not reassuring). {n}/{total} is appended
# by the caller when a total is known.
_STAGE_BLURB = {
    "index": "Scanning your photos",
    "video": "Skimming through videos",
    "classical": "Checking focus, exposure & framing",
    "embed": "Learning what's in each photo",
    "tag": "Tagging scenes, places & subjects",
    "story": "Piecing your trip into stories",
}


def _has_pending_tags(cfg: FolderConfig) -> bool:
    """True if any embedded photo hasn't been through the tag stage yet.

    Cheap EXISTS-style check (LIMIT 1) so we don't scan the whole table just
    to decide whether the tag stage has anything to do.
    """
    from selects.db import init_db, session_scope
    from selects.db.models import PipelineState

    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        row = (
            s.query(PipelineState.photo_id)
            .filter(PipelineState.embedding_done.is_(True), PipelineState.vl_done.is_(False))
            .first()
        )
    return row is not None


def _has_any_stories(cfg: FolderConfig) -> bool:
    from selects.db import init_db, session_scope
    from selects.db.models import Story

    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        row = s.query(Story.id).first()
    return row is not None


def run_pipeline_stages(
    cfg: FolderConfig,
    publish: PublishFn,
    should_cancel: Optional[CancelFn] = None,
) -> None:
    """Run all pipeline stages for *cfg*, publishing progress via *publish*.

    Blocking / CPU-bound — call from a worker thread, not the event loop.

    The tag and story stages are expensive (model inference / full rebuild)
    but idempotent, so on an already fully-processed library we skip them
    when we can cheaply prove there's nothing new for them to do. When that
    can't be established with confidence, we run the stage anyway —
    correctness over speed.
    """
    from selects.indexer.orchestrator import index_folder
    from selects.pipeline import run_classical_stage
    from selects.ml.embed import run_embedding_stage
    from selects.ml.tags import run_tag_stage
    from selects.ml.stories import run_story_stage
    from selects.video import run_video_stage

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

    # Counts from the earlier stages tell us whether anything actually
    # changed this run; that in turn decides whether tag/story need to run.
    counts: dict[str, int] = {}

    def _run_index():
        counts["index"] = index_folder(cfg, cb("index")) or 0

    def _run_video():
        counts["video"] = run_video_stage(cfg, cb("video")) or 0

    def _run_classical():
        counts["classical"] = run_classical_stage(cfg, cb("classical")) or 0

    def _run_embed():
        counts["embed"] = run_embedding_stage(cfg, cb("embed")) or 0

    def _run_tag():
        # Skip when nothing indexed/embedded is new AND no embedded photo is
        # still waiting to be tagged. If either signal is ambiguous/positive,
        # just run it.
        if counts.get("index", 1) == 0 and counts.get("embed", 1) == 0 and not _has_pending_tags(cfg):
            log.info("tag stage: nothing pending, skipping")
            counts["tag"] = 0
            return
        counts["tag"] = run_tag_stage(cfg, cb("tag")) or 0

    def _run_story():
        # Skip only if stories already exist and nothing upstream changed
        # (no new photos indexed, no new tags written this run).
        if (
            counts.get("index", 1) == 0
            and counts.get("tag", 1) == 0
            and _has_any_stories(cfg)
        ):
            log.info("story stage: nothing new since last build, skipping")
            counts["story"] = 0
            return
        counts["story"] = run_story_stage(cfg, cb("story")) or 0

    stages = [
        ("index", _run_index),
        ("video", _run_video),
        ("classical", _run_classical),
        ("embed", _run_embed),
        ("tag", _run_tag),
        ("story", _run_story),
    ]

    try:
        for _name, run in stages:
            if _cancelled():
                raise PipelineCancelled()
            run()
    except PipelineCancelled:
        publish({"stage": "cancelled", "current": 0, "total": 0,
                 "message": "Stopped — you can pick up where you left off"})
        return

    publish({"stage": "done", "current": 1, "total": 1, "message": "All set!"})
