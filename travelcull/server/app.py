from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from travelcull.config import FolderConfig

from .dedup_routes import register_dedup_routes
from .export_routes import register_export_routes
from .faces2_routes import register_faces2_routes
from .fs_routes import register_fs_routes
from .libraries import register_libraries
from .library_manager import ActiveConfigProxy, LibraryManager
from .models_routes import register_model_routes
from .pipeline_runner import run_pipeline_stages
from .routes import register_routes
from .search2_routes import register_search2_routes
from .ws import progress_bus, register_ws

log = logging.getLogger("travelcull.server")


def _find_static_dir() -> Optional[Path]:
    """Locate the built frontend (``dist``) directory.

    Prefers the packaged location (``travelcull/server/static``) used by
    PyInstaller builds, then falls back to ``<repo>/frontend/dist`` for dev.
    Returns ``None`` if neither contains an ``index.html``.
    """
    pkg_static = Path(__file__).resolve().parent / "static"
    if (pkg_static / "index.html").is_file():
        return pkg_static
    # travelcull/server/app.py -> parents[2] == repo root
    repo_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if (repo_dist / "index.html").is_file():
        return repo_dist
    return None


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built SPA with a deep-link fallback to ``index.html``.

    Registered *after* all API/WS routes so those always win; the catch-all
    only handles GET paths that are not ``/api`` or ``/ws``.
    """
    static_dir = _find_static_dir()
    if static_dir is None:
        log.warning(
            "No built frontend found; UI will not be served. "
            "Run `npm run build` in frontend/ (or `python packaging/build.py`)."
        )
        return

    index_html = static_dir / "index.html"
    assets_dir = static_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        if full_path.startswith(("api/", "ws/")) or full_path in ("api", "ws"):
            return FileResponse(index_html, status_code=404)
        candidate = static_dir / full_path
        if full_path and candidate.is_file() and candidate.resolve().is_relative_to(static_dir):
            return FileResponse(candidate)
        return FileResponse(index_html)


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
    register_search2_routes(app, proxy)
    register_export_routes(app, proxy)
    register_faces2_routes(app, proxy)
    register_dedup_routes(app, manager)
    register_libraries(app, manager, publish)
    register_model_routes(app, publish)
    register_fs_routes(app)
    register_ws(app)
    _mount_frontend(app)
    return app
