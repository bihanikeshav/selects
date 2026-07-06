"""Tests for selects.config."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from selects.config import FolderConfig, get_folder_config


class TestFolderConfigDefaults:
    def test_defaults_derive_from_folder(self, tmp_path: Path) -> None:
        cfg = get_folder_config(tmp_path)
        assert cfg.folder == tmp_path.resolve()
        assert cfg.web_port == 8765
        assert cfg.web_host == "127.0.0.1"
        assert cfg.burst_window_seconds == 3
        assert cfg.burst_similarity_threshold == 0.92
        assert cfg.speed_mode == "full"

    def test_state_dir_under_folder(self, tmp_path: Path) -> None:
        cfg = get_folder_config(tmp_path)
        assert cfg.state_dir == tmp_path.resolve() / ".selects"

    def test_db_path_under_state_dir(self, tmp_path: Path) -> None:
        cfg = get_folder_config(tmp_path)
        assert cfg.db_path == cfg.state_dir / "index.db"

    def test_thumbs_dir_under_state_dir(self, tmp_path: Path) -> None:
        cfg = get_folder_config(tmp_path)
        assert cfg.thumbs_dir == cfg.state_dir / "thumbs"

    def test_previews_dir_under_state_dir(self, tmp_path: Path) -> None:
        cfg = get_folder_config(tmp_path)
        assert cfg.previews_dir == cfg.state_dir / "previews"


class TestFolderConfigEnvOverride:
    def test_env_override_port(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SELECTS_WEB_PORT", "9999")
        cfg = get_folder_config(tmp_path)
        assert cfg.web_port == 9999

    def test_env_override_host(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SELECTS_WEB_HOST", "0.0.0.0")
        cfg = get_folder_config(tmp_path)
        assert cfg.web_host == "0.0.0.0"

    def test_env_override_speed_mode(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SELECTS_SPEED_MODE", "fast")
        cfg = get_folder_config(tmp_path)
        assert cfg.speed_mode == "fast"

    def test_kwarg_override(self, tmp_path: Path) -> None:
        cfg = get_folder_config(tmp_path, web_port=1234)
        assert cfg.web_port == 1234
