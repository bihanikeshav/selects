"""Background warmup for SigLIP text search so the first query isn't a 7s hitch."""
from __future__ import annotations

import logging
import threading
from typing import Optional

from selects.config import FolderConfig

log = logging.getLogger(__name__)

_lock = threading.Lock()
_text_ready = False
_error: str | None = None
_matrix_key: str | None = None
_bg_thread: threading.Thread | None = None


def is_search_ready() -> bool:
    return _text_ready


def wait_for_warmup() -> None:
    """Block until an in-flight warmup releases the lock (no-op if idle)."""
    with _lock:
        pass


def search_warmup_error() -> str | None:
    return _error


def reset_for_tests() -> None:
    """Clear process-global warmup flags. Tests only."""
    global _text_ready, _error, _matrix_key, _bg_thread
    with _lock:
        _text_ready = False
        _error = None
        _matrix_key = None
        _bg_thread = None


def warmup_search(cfg: Optional[FolderConfig] = None, *, embeddings: bool = True) -> None:
    """Load the SigLIP text session (and optionally the library matrix). Idempotent."""
    global _text_ready, _error, _matrix_key
    with _lock:
        if not _text_ready:
            try:
                from selects.ml.embed import encode_text_prompts

                encode_text_prompts(["a photo"])
                _text_ready = True
                _error = None
            except Exception as exc:
                _error = str(exc)
                log.exception("search text-model warmup failed")
                raise
        if embeddings and cfg is not None:
            key = str(cfg.db_path)
            if _matrix_key == key:
                return
            try:
                from selects.ml.search import library_embedding_matrix

                library_embedding_matrix(cfg)
                _matrix_key = key
            except Exception:
                log.exception("library embedding matrix warmup skipped")


def ensure_warmup(
    cfg: Optional[FolderConfig] = None,
    *,
    embeddings: bool = True,
    wait: bool = False,
) -> None:
    """Start warmup if needed. ``wait=False`` returns immediately (daemon thread)."""
    global _bg_thread
    if wait:
        warmup_search(cfg, embeddings=embeddings)
        return
    if _text_ready and not embeddings:
        return
    t = _bg_thread
    if t is not None and t.is_alive():
        return

    def run() -> None:
        try:
            warmup_search(cfg, embeddings=embeddings)
        except Exception:
            log.exception("background search warmup failed")

    _bg_thread = threading.Thread(target=run, name="selects-search-warmup", daemon=True)
    _bg_thread.start()
