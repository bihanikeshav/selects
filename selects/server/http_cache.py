"""Cache headers and gzip skip lists for hashed / content-addressed bytes."""
from __future__ import annotations

from pathlib import Path

from fastapi.responses import FileResponse

IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}

# Paths that are already-compressed images (or tiny PNG placeholders). Gzip
# wastes CPU and often inflates JPEGs.
SKIP_GZIP_PREFIXES = (
    "/api/thumb/",
    "/api/preview/",
    "/api/enhance/",
    "/api/editor/result/",
    "/api/libraries/",
    "/api/videos/",
    "/api/face_crop/",
)


def skip_gzip_path(path: str) -> bool:
    return any(path.startswith(p) for p in SKIP_GZIP_PREFIXES)


def jpeg_file(path: str | Path, **kwargs) -> FileResponse:
    headers = {**IMMUTABLE, **(kwargs.pop("headers", None) or {})}
    return FileResponse(path, media_type="image/jpeg", headers=headers, **kwargs)


class SkipImageGZipMiddleware:
    """GZip JSON/HTML/JS, but not content-addressed JPEGs."""

    def __init__(self, app, minimum_size: int = 500):
        from starlette.middleware.gzip import GZipMiddleware

        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=minimum_size)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and skip_gzip_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        await self.gzip(scope, receive, send)
