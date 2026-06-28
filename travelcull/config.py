"""travelcull configuration via pydantic-settings."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class FolderConfig(BaseSettings):
    """Per-folder runtime configuration.

    All fields can be overridden via env vars prefixed TRAVELCULL_.
    Example: TRAVELCULL_WEB_PORT=9000
    """

    model_config = SettingsConfigDict(
        env_prefix="TRAVELCULL_",
        env_file=".env",
        extra="ignore",
    )

    folder: Path
    web_port: int = 8765
    web_host: str = "127.0.0.1"

    # Burst detection
    burst_window_seconds: int = 3
    burst_similarity_threshold: float = 0.92

    # Processing speed mode: "fast" skips some ML steps for quick preview
    speed_mode: Literal["fast", "full"] = "full"

    @field_validator("folder", mode="before")
    @classmethod
    def resolve_folder(cls, v: object) -> Path:
        return Path(str(v)).expanduser().resolve()

    # ------------------------------------------------------------------ #
    # Derived paths (stored under <folder>/.travelcull/)                  #
    # ------------------------------------------------------------------ #

    @property
    def state_dir(self) -> Path:
        """Hidden state directory inside the watched folder."""
        return self.folder / ".travelcull"

    @property
    def db_path(self) -> Path:
        return self.state_dir / "index.db"

    @property
    def thumbs_dir(self) -> Path:
        return self.state_dir / "thumbs"

    @property
    def previews_dir(self) -> Path:
        return self.state_dir / "previews"


def get_folder_config(folder: Path | str, **overrides: object) -> FolderConfig:
    """Create a FolderConfig for the given folder, with optional field overrides."""
    return FolderConfig(folder=folder, **overrides)  # type: ignore[arg-type]
