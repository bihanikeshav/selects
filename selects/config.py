"""selects configuration via pydantic-settings."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class FolderConfig(BaseSettings):
    """Per-folder runtime configuration.

    All fields can be overridden via env vars prefixed SELECTS_.
    Example: SELECTS_WEB_PORT=9000
    """

    model_config = SettingsConfigDict(
        env_prefix="SELECTS_",
        env_file=".env",
        extra="ignore",
    )

    folder: Path
    web_port: int = 8765
    web_host: str = "127.0.0.1"

    # Burst detection
    burst_window_seconds: int = 3
    burst_similarity_threshold: float = 0.92

    # Aesthetic curation thresholds (combined = AP_WEIGHT*AP + NIMA_WEIGHT*NIMA).
    # Per-scope gate: photo must be in the top 25% of its scope (day/place/person).
    # Library-wide floor: photo must also be in the top 35% globally — a
    # mediocre photo isn't rescued just because its scope is thin.
    ap_weight: float = 0.6
    nima_weight: float = 0.4
    aesthetic_per_scope_pct: float = 75.0   # top 25% within scope
    aesthetic_library_pct: float = 50.0     # top 50% globally — generous default;
                                            # bump higher (e.g. 65) to tighten

    # Processing speed mode: "fast" skips some ML steps for quick preview
    speed_mode: Literal["fast", "full"] = "full"

    @field_validator("folder", mode="before")
    @classmethod
    def resolve_folder(cls, v: object) -> Path:
        return Path(str(v)).expanduser().resolve()

    # ------------------------------------------------------------------ #
    # Derived paths (stored under <folder>/.selects/)                  #
    # ------------------------------------------------------------------ #

    @property
    def state_dir(self) -> Path:
        """Hidden state directory inside the watched folder."""
        return self.folder / ".selects"

    @property
    def db_path(self) -> Path:
        return self.state_dir / "index.db"

    @property
    def thumbs_dir(self) -> Path:
        return self.state_dir / "thumbs"

    @property
    def previews_dir(self) -> Path:
        return self.state_dir / "previews"


_STATE_DIR = ".selects"
_LEGACY_STATE_DIR = ".travelcull"  # pre-rebrand name


def _photo_count(db_path: Path) -> int | None:
    """Row count of the photos table via raw sqlite (no schema migration).

    Returns None if the DB/table is missing or unreadable.
    """
    import sqlite3

    try:
        if not db_path.exists():
            return 0
        con = sqlite3.connect(str(db_path))
        try:
            return int(con.execute("SELECT COUNT(*) FROM photos").fetchone()[0])
        finally:
            con.close()
    except Exception:
        return None


def migrate_legacy_state_dir(folder: Path) -> None:
    """Carry a pre-rebrand ``<folder>/.travelcull`` data dir over to ``.selects``
    so libraries indexed before the rename keep their data.

    - If ``.selects`` doesn't exist yet: rename ``.travelcull`` to it.
    - If an *empty* ``.selects`` was already created (0 photos) but the legacy
      dir has real data: replace the empty one with the legacy data.
    - Otherwise: leave everything as-is (never clobber a populated ``.selects``).
    """
    import shutil

    try:
        new = folder / _STATE_DIR
        old = folder / _LEGACY_STATE_DIR
        if not old.is_dir():
            return
        if not new.exists():
            old.rename(new)
            return
        new_photos = _photo_count(new / "index.db") or 0
        old_photos = _photo_count(old / "index.db") or 0
        if new_photos == 0 and old_photos > 0:
            shutil.rmtree(new)
            old.rename(new)
    except OSError:
        pass


def get_folder_config(folder: Path | str, **overrides: object) -> FolderConfig:
    """Create a FolderConfig for the given folder, with optional field overrides."""
    resolved = Path(str(folder)).expanduser().resolve()
    if resolved.is_dir():
        migrate_legacy_state_dir(resolved)
    return FolderConfig(folder=folder, **overrides)  # type: ignore[arg-type]
