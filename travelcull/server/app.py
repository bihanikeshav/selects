from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from travelcull.config import FolderConfig
from travelcull.indexer.orchestrator import index_folder
from travelcull.pipeline import run_classical_stage

from .routes import register_routes
from .ws import progress_bus, register_ws


def build_app(cfg: FolderConfig, run_background: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not run_background:
            yield
            return

        bus = progress_bus()
        loop = asyncio.get_event_loop()

        def index_progress(i, total, name):
            asyncio.run_coroutine_threadsafe(
                bus.publish({"stage": "index", "current": i, "total": total, "message": name}),
                loop,
            )

        def classical_progress(i, total, name):
            asyncio.run_coroutine_threadsafe(
                bus.publish({"stage": "classical", "current": i, "total": total, "message": name}),
                loop,
            )

        def embed_progress(i, total, name):
            asyncio.run_coroutine_threadsafe(
                bus.publish({"stage": "embed", "current": i, "total": total, "message": name}),
                loop,
            )

        def tag_progress(i, total, name):
            asyncio.run_coroutine_threadsafe(
                bus.publish({"stage": "tag", "current": i, "total": total, "message": name}),
                loop,
            )

        async def background():
            await asyncio.to_thread(index_folder, cfg, index_progress)
            await asyncio.to_thread(run_classical_stage, cfg, classical_progress)
            from travelcull.ml.embed import run_embedding_stage
            from travelcull.ml.tags import run_tag_stage
            await asyncio.to_thread(run_embedding_stage, cfg, embed_progress)
            await asyncio.to_thread(run_tag_stage, cfg, tag_progress)
            await bus.publish({"stage": "done", "current": 1, "total": 1})

        task = asyncio.create_task(background())
        try:
            yield
        finally:
            task.cancel()

    app = FastAPI(title="travelcull", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    register_routes(app, cfg)
    register_ws(app)
    return app
