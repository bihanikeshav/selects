"""Shared full-pipeline runner used by both the startup auto-index and the
/api/libraries/{id}/index endpoint.

Runs the exact same stage sequence as the original ``build_app`` background
task: index -> video -> classical -> embed -> tag -> story, publishing progress dicts
to a caller-supplied ``publish`` callable (which is responsible for getting
those dicts onto the websocket progress bus in a thread-safe way).
"""
from __future__ import annotations

from typing import Callable

from travelcull.config import FolderConfig

PublishFn = Callable[[dict], None]


def run_pipeline_stages(cfg: FolderConfig, publish: PublishFn) -> None:
    """Run all pipeline stages for *cfg*, publishing progress via *publish*.

    Blocking / CPU-bound — call from a worker thread, not the event loop.
    """
    from travelcull.indexer.orchestrator import index_folder
    from travelcull.pipeline import run_classical_stage
    from travelcull.ml.embed import run_embedding_stage
    from travelcull.ml.tags import run_tag_stage
    from travelcull.ml.stories import run_story_stage
    from travelcull.video import run_video_stage

    def cb(stage: str):
        def _progress(i, total, name):
            publish({"stage": stage, "current": i, "total": total, "message": name})
        return _progress

    index_folder(cfg, cb("index"))
    run_video_stage(cfg, cb("video"))
    run_classical_stage(cfg, cb("classical"))
    run_embedding_stage(cfg, cb("embed"))
    run_tag_stage(cfg, cb("tag"))
    run_story_stage(cfg, cb("story"))
    publish({"stage": "done", "current": 1, "total": 1})
