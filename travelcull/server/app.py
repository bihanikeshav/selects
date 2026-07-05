from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from travelcull.config import FolderConfig

from .libraries import register_libraries
from .library_manager import ActiveConfigProxy, LibraryManager
from .pipeline_runner import run_pipeline_stages
from .routes import register_routes
from .ws import progress_bus, register_ws


def build_app(
    cfg: Optional[FolderConfig] = None,
    run_background: bool = True,
    manager: Optional[LibraryManager] = None,
) -> FastAPI:
    """Build the FastAPI app.

    *cfg* bootstraps a single-folder library when *manager* is not supplied
    (the CLI path). Tests can inject a *manager* with an isolated registry.
    All /api/* endpoints follow the manager's active library via a proxy.
    """
    if manager is None:
        manager = LibraryManager(bootstrap_cfg=cfg)
    proxy = ActiveConfigProxy(manager)

    def publish(msg: dict) -> None:
        loop = manager.loop
        if loop is not None:
            asyncio.run_coroutine_threadsafe(progress_bus().publish(msg), loop)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        manager.set_loop(asyncio.get_running_loop())

        if not run_background or manager.active_cfg is None:
            yield
            return

        async def background():
            if not manager.begin_indexing():
                return

            def worker():
                try:
                    run_pipeline_stages(manager.active_cfg, publish)
                finally:
                    manager.end_indexing()

            await asyncio.to_thread(worker)

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

    register_routes(app, proxy)
    register_libraries(app, manager, publish)
    register_ws(app)
    return app
