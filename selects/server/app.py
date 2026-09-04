from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

from selects import __version__
from selects.config import FolderConfig

from .dedup_routes import register_dedup_routes
from .export_routes import register_export_routes
from .faces2_routes import register_faces2_routes
from .fs_routes import is_loopback_host, register_fs_routes
from .http_cache import SkipImageGZipMiddleware
from .libraries import register_libraries
from .library_manager import ActiveConfigProxy, LibraryManager
from .models_routes import register_model_routes
from .pipeline_runner import run_pipeline_stages
from .recap_routes import register_recap_routes
from .routes import register_routes
from .search2_routes import register_search2_routes
from .system_routes import register_system_routes
from .taste_routes import register_taste_routes
from .video_routes import register_video_routes
from .watch_routes import register_watch_routes
from .ws import progress_bus, register_ws

log = logging.getLogger("selects.server")


def _find_static_dir() -> Optional[Path]:
    """Locate the built frontend (``dist``) directory.

    Prefers the packaged location (``selects/server/static``) used by
    PyInstaller builds, then falls back to ``<repo>/frontend/dist`` for dev.
    Returns ``None`` if neither contains an ``index.html``.
    """
    pkg_static = Path(__file__).resolve().parent / "static"
    if (pkg_static / "index.html").is_file():
        return pkg_static
    # selects/server/app.py -> parents[2] == repo root
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
        return FileResponse(index_html, headers={"Cache-Control": "no-cache"})


def build_app(
    cfg: Optional[FolderConfig] = None,
    run_background: bool = True,
    manager: Optional[LibraryManager] = None,
    bind_host: str = "127.0.0.1",
) -> FastAPI:
    """Build the FastAPI app.

    *cfg* bootstraps a single-folder library when *manager* is not supplied
    (the CLI path). Tests can inject a *manager* with an isolated registry.
    All /api/* endpoints follow the manager's active library via a proxy.
    *bind_host* is the address uvicorn will bind; non-loopback disables
    ``/api/fs/list`` so a LAN bind cannot browse the host filesystem.
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
        tasks: list[asyncio.Task] = []

        async def warmup(*, embeddings: bool) -> None:
            from selects.ml.warmup import warmup_search

            await asyncio.to_thread(
                warmup_search, manager.active_cfg, embeddings=embeddings
            )

        if run_background:
            # Load SigLIP text in the background so the first search isn't ~7s.
            tasks.append(asyncio.create_task(warmup(embeddings=False)))
            if manager.active_cfg is not None:
                async def background():
                    started = manager.begin_indexing()
                    if started:
                        def worker():
                            try:
                                run_pipeline_stages(
                                    manager.active_cfg,
                                    publish,
                                    should_cancel=manager.should_cancel,
                                )
                            finally:
                                manager.end_indexing()

                        await asyncio.to_thread(worker)
                    await warmup(embeddings=True)

                tasks.append(asyncio.create_task(background()))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()

    app = FastAPI(title="selects", version=__version__, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(SkipImageGZipMiddleware, minimum_size=500)

    lan_token = os.environ.get("SELECTS_LAN_TOKEN", "").strip() or None
    lan_exposed = not is_loopback_host(bind_host)
    if lan_exposed:
        log.warning(
            "Bound to %s — this library is reachable on the LAN. "
            "Filesystem browsing is disabled. Set SELECTS_LAN_TOKEN and open "
            "the UI with ?token= to require a bearer token from non-local clients.",
            bind_host,
        )

    @app.middleware("http")
    async def cache_hashed_assets(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    if lan_exposed and lan_token:
        @app.middleware("http")
        async def require_lan_token(request: Request, call_next):
            client = request.client.host if request.client else ""
            if is_loopback_host(client) or request.url.path in ("/api/health",):
                return await call_next(request)
            auth = request.headers.get("authorization", "")
            qtok = request.query_params.get("token", "")
            if auth == f"Bearer {lan_token}" or qtok == lan_token:
                return await call_next(request)
            return JSONResponse({"detail": "LAN token required"}, status_code=401)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "lan_exposed": lan_exposed}

    @app.get("/api/search/ready")
    def search_ready():
        from selects.ml.warmup import is_search_ready, search_warmup_error

        return {"ready": is_search_ready(), "error": search_warmup_error()}

    @app.post("/api/search/warmup")
    def search_warmup():
        from selects.ml.warmup import ensure_warmup, is_search_ready

        ensure_warmup(manager.active_cfg, wait=False)
        return {"ready": is_search_ready(), "started": True}

    register_routes(app, proxy)
    register_search2_routes(app, proxy)
    register_export_routes(app, proxy)
    register_faces2_routes(app, proxy)
    register_dedup_routes(app, manager)
    register_libraries(app, manager, publish)
    register_model_routes(app, publish)
    register_taste_routes(app, proxy)
    register_recap_routes(app, proxy)
    register_video_routes(app, proxy, publish)
    register_watch_routes(app, manager, publish)
    register_fs_routes(app, bind_host=bind_host)
    register_system_routes(app, proxy)
    register_ws(app)
    _mount_frontend(app)
    return app
