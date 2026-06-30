import shutil
from pathlib import Path

import pytest

from travelcull.config import get_folder_config
from travelcull.db import init_db, session_scope
from travelcull.db.models import ClassicalScore, PipelineState
from travelcull.indexer.orchestrator import index_folder
from travelcull.pipeline import run_classical_stage

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def populated_folder(tmp_path) -> Path:
    """Override conftest's populated_folder with real-file copies for decode-dependent tests."""
    for f in FIXTURES_DIR.iterdir():
        if f.is_file() and f.suffix.lower() in {".jpg", ".jpeg", ".heic", ".heif", ".mp4"}:
            shutil.copy(f, tmp_path / f.name)
    return tmp_path


def test_stage1_writes_classical_scores(populated_folder):
    cfg = get_folder_config(populated_folder)
    Session = init_db(cfg.db_path)
    index_folder(cfg)
    run_classical_stage(cfg)
    with session_scope(Session) as s:
        scores = s.query(ClassicalScore).all()
        states = s.query(PipelineState).all()
        assert len(scores) >= 2
        assert all(p.classical_done for p in states)


def test_stage1_is_idempotent(populated_folder):
    cfg = get_folder_config(populated_folder)
    init_db(cfg.db_path)
    index_folder(cfg)
    run_classical_stage(cfg)
    n2 = run_classical_stage(cfg)
    assert n2 == 0
