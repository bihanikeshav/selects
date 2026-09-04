"""Aggregator for the per-domain API route modules.

Each module owns one slice of the API and registers itself against the app
with the same ``(app, cfg)`` signature. ``cfg`` may be an
``ActiveConfigProxy``, so every module resolves its sessionmaker at request
time rather than at registration time.
"""
from __future__ import annotations

from fastapi import FastAPI

from selects.config import FolderConfig

from .calibrate_routes import register_calibrate_routes
from .clusters_routes import register_clusters_routes
from .editor_routes import register_editor_routes
from .images_routes import register_images_routes
from .map_routes import register_map_routes
from .persons_routes import register_persons_routes
from .photos_routes import register_photos_routes
from .stories_routes import register_stories_routes


def register_routes(app: FastAPI, cfg: FolderConfig) -> None:
    register_photos_routes(app, cfg)
    register_clusters_routes(app, cfg)
    register_stories_routes(app, cfg)
    register_persons_routes(app, cfg)
    register_images_routes(app, cfg)
    register_calibrate_routes(app, cfg)
    register_map_routes(app, cfg)
    register_editor_routes(app, cfg)
