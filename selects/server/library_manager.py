"""Multi-library (multi-trip) registry + active-library switching.

The registry is a small JSON file listing every folder the user has registered
as a "library". Exactly one library is active at a time; the active library's
``FolderConfig`` is what every /api/* endpoint serves.

The key trick that lets 40 existing endpoints follow the active library without
being rewritten is :class:`ActiveConfigProxy`: ``build_app`` passes the proxy
(not a raw ``FolderConfig``) into ``register_routes``. Every ``cfg.<attr>``
access inside a handler forwards, at request time, to whichever ``FolderConfig``
is currently active.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from selects.config import FolderConfig, get_folder_config


class LibraryError(Exception):
    """Base class for library-registry errors."""


class DuplicateLibraryError(LibraryError):
    """Raised when a path is already registered."""


class ActiveLibraryError(LibraryError):
    """Raised when trying to delete the active library while others exist."""


def _default_registry_path() -> Path:
    env = os.environ.get("SELECTS_REGISTRY")
    if env:
        return Path(env)
    return Path.home() / ".selects" / "libraries.json"


def _resolve(p: os.PathLike | str) -> str:
    return str(Path(p).expanduser().resolve())


def count_photos(cfg: FolderConfig) -> Optional[int]:
    """Count Photo rows in *cfg*'s database. Returns 0 if the DB doesn't exist
    yet, or None on any failure."""
    try:
        db = cfg.db_path
        if not db.exists():
            return 0
        from selects.db import init_db, session_scope
        from selects.db.models import Photo

        Session = init_db(db)
        with session_scope(Session) as s:
            return int(s.query(Photo).count())
    except Exception:
        return None


class LibraryManager:
    """Holds the library registry + the currently-active FolderConfig."""

    def __init__(
        self,
        bootstrap_cfg: Optional[FolderConfig] = None,
        registry_path: Optional[Path] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._registry_path = Path(registry_path) if registry_path else _default_registry_path()
        self._libraries: list[dict] = []
        self._active_id: Optional[str] = None
        self._active_cfg: Optional[FolderConfig] = None
        self._indexing = False
        self._loop = None
        self._load()
        if bootstrap_cfg is not None:
            self._bootstrap(bootstrap_cfg)

    # ---- persistence ---------------------------------------------------- #
    def _load(self) -> None:
        if not self._registry_path.exists():
            return
        try:
            data = json.loads(self._registry_path.read_text())
            self._libraries = list(data.get("libraries", []))
            self._active_id = data.get("active_id")
        except Exception:
            self._libraries = []
            self._active_id = None

    def _save(self) -> None:
        self._registry_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"active_id": self._active_id, "libraries": self._libraries}
        tmp = self._registry_path.parent / (self._registry_path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self._registry_path)

    # ---- bootstrap ------------------------------------------------------ #
    def _bootstrap(self, cfg: FolderConfig) -> None:
        """Seed the registry from a CLI-provided folder.

        - empty registry: register that folder and make it active.
        - registry has an active library: keep it (CLI folder is just a fallback).
        - registry non-empty but no active: activate the first (or matching) one.
        """
        with self._lock:
            folder = str(cfg.folder)
            match = self._find_by_path(folder)
            if not self._libraries:
                lib = self._make_record(cfg.folder.name or "library", folder)
                self._libraries.append(lib)
                self._active_id = lib["id"]
                self._active_cfg = cfg
                self._save()
            elif self._active_id is None or self._get(self._active_id) is None:
                target = match or self._libraries[0]
                self._active_id = target["id"]
                self._active_cfg = self._cfg_for(target, cfg)
                self._save()
            else:
                active = self._get(self._active_id)
                self._active_cfg = self._cfg_for(active, cfg)

    # ---- internal helpers ---------------------------------------------- #
    def _make_record(self, name: str, path: str) -> dict:
        return {
            "id": uuid.uuid4().hex[:8],
            "name": name,
            "path": _resolve(path),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def _get(self, lib_id: Optional[str]) -> Optional[dict]:
        if lib_id is None:
            return None
        for lib in self._libraries:
            if lib["id"] == lib_id:
                return lib
        return None

    def _find_by_path(self, path: str) -> Optional[dict]:
        rp = _resolve(path)
        for lib in self._libraries:
            if lib["path"] == rp:
                return lib
        return None

    def _cfg_for(self, lib: dict, bootstrap: Optional[FolderConfig]) -> FolderConfig:
        if bootstrap is not None and str(bootstrap.folder) == lib["path"]:
            return bootstrap
        return get_folder_config(lib["path"])

    def _api_dict(self, lib: dict) -> dict:
        is_active = lib["id"] == self._active_id
        cfg = (
            self._active_cfg
            if is_active and self._active_cfg is not None
            else get_folder_config(lib["path"])
        )
        return {
            "id": lib["id"],
            "name": lib["name"],
            "path": lib["path"],
            "active": is_active,
            "photo_count": count_photos(cfg),
            "created_at": lib["created_at"],
        }

    # ---- public API ----------------------------------------------------- #
    @property
    def active_cfg(self) -> Optional[FolderConfig]:
        return self._active_cfg

    @property
    def loop(self):
        return self._loop

    def set_loop(self, loop) -> None:
        self._loop = loop

    def get(self, lib_id: str) -> Optional[dict]:
        with self._lock:
            return self._get(lib_id)

    def find_by_path(self, path: str) -> Optional[dict]:
        with self._lock:
            return self._find_by_path(path)

    def list_libraries(self) -> tuple[list[dict], Optional[str]]:
        with self._lock:
            return [self._api_dict(l) for l in self._libraries], self._active_id

    def add_library(self, name: str, path: str) -> dict:
        with self._lock:
            if self._find_by_path(path) is not None:
                raise DuplicateLibraryError(path)
            lib = self._make_record(name, path)
            self._libraries.append(lib)
            if self._active_id is None:
                self._active_id = lib["id"]
                self._active_cfg = get_folder_config(lib["path"])
            self._save()
            return self._api_dict(lib)

    def activate(self, lib_id: str) -> dict:
        with self._lock:
            lib = self._get(lib_id)
            if lib is None:
                raise KeyError(lib_id)
            self._active_id = lib_id
            self._active_cfg = get_folder_config(lib["path"])
            self._save()
            # Ensure the DB + tables exist so endpoints work immediately.
            try:
                from selects.db import init_db

                init_db(self._active_cfg.db_path)
            except Exception:
                pass
            return self._api_dict(lib)

    def delete(self, lib_id: str) -> None:
        with self._lock:
            lib = self._get(lib_id)
            if lib is None:
                raise KeyError(lib_id)
            if lib_id == self._active_id and len(self._libraries) > 1:
                raise ActiveLibraryError(lib_id)
            self._libraries = [l for l in self._libraries if l["id"] != lib_id]
            if lib_id == self._active_id:
                # Was the only library — clear active.
                self._active_id = None
                self._active_cfg = None
            self._save()

    def status(self) -> dict:
        with self._lock:
            active = self._get(self._active_id)
            if not self._libraries or active is None:
                return {
                    "needs_onboarding": True,
                    "active": None,
                    "photo_count": 0,
                    "indexing": self._indexing,
                }
            cfg = self._active_cfg or get_folder_config(active["path"])
            pc = count_photos(cfg) or 0
            return {
                "needs_onboarding": pc == 0,
                "active": self._api_dict(active),
                "photo_count": pc,
                "indexing": self._indexing,
            }

    # ---- indexing flag -------------------------------------------------- #
    @property
    def indexing(self) -> bool:
        with self._lock:
            return self._indexing

    def begin_indexing(self) -> bool:
        """Try to acquire the indexing slot. Returns False if a run is active."""
        with self._lock:
            if self._indexing:
                return False
            self._indexing = True
            return True

    def end_indexing(self) -> None:
        with self._lock:
            self._indexing = False


class ActiveConfigProxy:
    """Forwards every attribute access to the manager's active FolderConfig.

    Passed into ``register_routes`` in place of a raw ``FolderConfig`` so that
    all existing endpoints transparently follow the active library.
    """

    def __init__(self, manager: LibraryManager) -> None:
        object.__setattr__(self, "_manager", manager)

    def __getattr__(self, name: str):
        manager: LibraryManager = object.__getattribute__(self, "_manager")
        cfg = manager.active_cfg
        if cfg is None:
            raise RuntimeError("No active library configured")
        return getattr(cfg, name)
