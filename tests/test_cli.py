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
    assert "CUDA" in result.output
    assert "nvImageCodec" in result.output


def test_index_command_indexes(populated_folder):
    runner = CliRunner()
    result = runner.invoke(main, ["index", str(populated_folder)])
    assert result.exit_code == 0
    assert "indexed" in result.output.lower()
