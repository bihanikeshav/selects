"""Shared full-pipeline runner used by both the startup auto-index and the
/api/libraries/{id}/index endpoint.

Runs the stage sequence index -> video -> classical -> embed -> tag -> story,
publishing progress dicts to a caller-supplied ``publish`` callable (which is
responsible for getting those dicts onto the websocket progress bus in a
thread-safe way). An optional ``should_cancel`` callable lets the caller stop
the run between (and, for most stages, during) stages.
"""
from __future__ import annotations

from typing import Callable, Optional

from selects.config import FolderConfig

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


def run_pipeline_stages(
    cfg: FolderConfig,
    publish: PublishFn,
    should_cancel: Optional[CancelFn] = None,
) -> None:
    """Run all pipeline stages for *cfg*, publishing progress via *publish*.

    Blocking / CPU-bound — call from a worker thread, not the event loop.
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

    stages = [
        ("index", lambda: index_folder(cfg, cb("index"))),
        ("video", lambda: run_video_stage(cfg, cb("video"))),
        ("classical", lambda: run_classical_stage(cfg, cb("classical"))),
        ("embed", lambda: run_embedding_stage(cfg, cb("embed"))),
        ("tag", lambda: run_tag_stage(cfg, cb("tag"))),
        ("story", lambda: run_story_stage(cfg, cb("story"))),
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
