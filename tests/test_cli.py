import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from selects.cli import main

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def populated_folder(tmp_path) -> Path:
    """Real-file copies needed since CLI runs the full decode pipeline."""
    for f in FIXTURES_DIR.iterdir():
        if f.is_file() and f.suffix.lower() in {".jpg", ".jpeg", ".heic", ".heif", ".mp4"}:
            shutil.copy(f, tmp_path / f.name)
    return tmp_path


def test_doctor_runs_and_reports():
    runner = CliRunner()
    result = runner.invoke(main, ["doctor"])
    assert result.exit_code == 0
    assert "ONNX provider" in result.output
    assert "nvImageCodec" in result.output


def test_index_command_indexes(populated_folder):
    runner = CliRunner()
    result = runner.invoke(main, ["index", str(populated_folder), "--pass", "index"])
    assert result.exit_code == 0
    assert "index:" in result.output.lower()


def test_pass_help_includes_persons_and_video():
    runner = CliRunner()
    result = runner.invoke(main, ["index", "--help"])
    assert result.exit_code == 0
    assert "persons" in result.output
    assert "video" in result.output
    assert "category" in result.output


def test_lan_bind_refused_helper(monkeypatch: pytest.MonkeyPatch):
    from selects.cli import lan_bind_refused

    monkeypatch.delenv("SELECTS_ALLOW_LAN", raising=False)
    assert lan_bind_refused("127.0.0.1") is None
    assert lan_bind_refused("0.0.0.0") is not None
    assert lan_bind_refused("::") is not None
    assert "SELECTS_ALLOW_LAN" in lan_bind_refused("0.0.0.0")

    monkeypatch.setenv("SELECTS_ALLOW_LAN", "1")
    assert lan_bind_refused("0.0.0.0") is None
    monkeypatch.setenv("SELECTS_ALLOW_LAN", "true")
    assert lan_bind_refused("::") is None
    monkeypatch.setenv("SELECTS_ALLOW_LAN", "yes")
    assert lan_bind_refused("0.0.0.0") is not None


def test_serve_refuses_lan_bind_without_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("SELECTS_ALLOW_LAN", raising=False)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["serve", str(tmp_path), "--host", "0.0.0.0", "--no-browser", "--no-background"],
    )
    assert result.exit_code != 0
    assert "SELECTS_ALLOW_LAN" in result.output
