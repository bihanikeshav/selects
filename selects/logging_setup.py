"""Persistent file logging so crashes in the windowed (no-console) build are
recoverable. Writes to ``~/.selects/logs/selects.log`` (rotating), and routes
uncaught exceptions — main thread and worker threads — into the same file.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
from pathlib import Path

_CONFIGURED = False


def log_dir() -> Path:
    d = Path.home() / ".selects" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_file() -> Path:
    return log_dir() / "selects.log"


def setup_logging(level: int = logging.INFO) -> Path:
    """Idempotently attach a rotating file handler to the root logger and install
    exception hooks. Returns the log file path."""
    global _CONFIGURED
    path = log_file()
    if _CONFIGURED:
        return path

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = logging.handlers.RotatingFileHandler(
        path, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    fh.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(fh)

    # Third-party libraries log HTTP/transfer chatter at INFO — e.g. httpx logs
    # every HuggingFace HEAD/GET. Quiet them so the log (and console) stays useful.
    for noisy in ("httpx", "httpcore", "huggingface_hub", "urllib3", "filelock"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Silence noisy third-party deprecation warnings that we can't act on
    # (e.g. insightface's face_align calling scikit-image's deprecated estimate()).
    import warnings

    warnings.filterwarnings("ignore", category=FutureWarning, module=r"insightface\..*")
    warnings.filterwarnings("ignore", message=r".*estimate.*is deprecated.*")
    # Keep console output too when a console exists (dev / CLI).
    if sys.stderr is not None:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)

    def _excepthook(exc_type, exc, tb):
        logging.getLogger("selects").critical(
            "uncaught exception", exc_info=(exc_type, exc, tb)
        )

    sys.excepthook = _excepthook

    def _thread_excepthook(args: threading.ExceptHookArgs):
        logging.getLogger("selects").critical(
            "uncaught exception in thread %s",
            args.thread.name if args.thread else "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = _thread_excepthook

    logging.getLogger("selects").info("logging to %s", path)
    _CONFIGURED = True
    return path
