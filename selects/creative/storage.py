"""Safe storage for local review artifacts."""
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def check_state(cfg):
    for path in (cfg.state_dir, cfg.previews_dir, cfg.thumbs_dir, cfg.state_dir / "creative"):
        if path.is_symlink() or not path.resolve().is_relative_to(cfg.folder):
            raise ValueError(f"unsafe state path: {path}")
    if cfg.db_path.is_symlink():
        raise ValueError("unsafe linked index database")


def artifact_path(cfg, name):
    check_state(cfg)
    if not name or name in {".", ".."} or Path(name).name != name:
        raise ValueError("artifact name must be a filename")
    result = cfg.state_dir / "creative" / name
    if result.is_symlink() or not result.resolve().is_relative_to(result.parent.resolve()):
        raise ValueError(f"unsafe review artifact: {result}")
    return result


def write_text(cfg, name, text):
    result = artifact_path(cfg, name)
    result.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=result.parent,
                                prefix=".review-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, result)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return result


def write_json(cfg, name, value):
    return write_text(cfg, name, json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def read_json(cfg, name):
    source = artifact_path(cfg, name)
    if not source.exists():
        return None
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"invalid review document: {name}")
    return value
