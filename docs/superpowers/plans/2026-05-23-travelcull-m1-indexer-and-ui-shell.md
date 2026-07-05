# travelcull M1: Indexer + UI Shell — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the foundation of travelcull — a Python package that walks a folder, generates GPU-decoded thumbnails for HEIC/JPG/RAW/MP4, runs classical reject signals (blur, exposure, faces, eyes-open), persists everything in a SQLite sidecar, and serves a Material 3 React UI at `localhost:5173` that displays the indexed photos in a burst-cull view. End-state: `travelcull index Z:\Ladakh\Photos && travelcull serve` opens a browser showing all 1065 Ladakh files with auto-reject flags visible.

**Architecture:** Three-process Python app — `Indexer` walks files and writes DB rows + previews, `Worker` runs classical Stage 1 signals (M1 only — ML stages come in M2/M3), `Server` (FastAPI) serves the React UI and exposes REST + WebSocket APIs. SQLite sidecar at folder root. GPU-first decode via nvImageCodec (JPEG) + pillow-heif (HEIC, CPU upload) + rawpy embedded preview (RAW) + torchcodec/PyAV with `-hwaccel cuda` (video). All frames stay GPU-resident through preprocessing where possible.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.0 + SQLite, pydantic, uvicorn, websockets, pillow-heif, rawpy, nvImageCodec (cuvs), torchcodec, OpenCV (+ cv2.cuda where available), insightface (SCRFD), mediapipe, ffmpeg-python, click for CLI, pytest, ruff. Frontend: React 18 + Vite + TypeScript, port styles from `Z:\travel_post\design\`.

---

## Stolen patterns (from OSS competitor audit)

- **Multi-pass CLI** (Facet): `travelcull index --pass classical|embed|vl` so users can budget GPU time.
- **Multi-tier preview cache** (PhotoSort): 256² thumb + 1024² preview, both written once at index time, never re-decoded.
- **Diagnostic command** (Facet `--doctor`): `travelcull doctor` reports CUDA/NVDEC/nvImageCodec capability and what falls back to CPU.
- **Idempotent re-runs** (Facet `--upgrade-db`): every file is keyed by SHA256; re-running on the same folder is a no-op for unchanged files.
- **Orientation-partitioned clustering** (PhotoSort): when burst grouping arrives in M2, cluster portrait+landscape separately.

## What we are deliberately not doing in M1

- ML scoring (SigLIP, Qwen3-VL, Aesthetic V2.5) — that's M2.
- Burst clustering — needs embeddings, also M2.
- Video shot detection / GPMF parsing — M3.
- Personalization, Stories, Clusters tag-driven views — M2/M3.
- XMP sidecar writes — M3.
- The swipe interaction (`J/K/L`) doing anything beyond logging — M2 once we have scores.

## File structure

```
Z:\travel_post\
├── pyproject.toml                          # package metadata, deps, ruff/pytest config
├── .gitignore
├── README.md                                # quickstart only; full docs later
├── travelcull/                              # Python package
│   ├── __init__.py                          # __version__
│   ├── __main__.py                          # python -m travelcull → cli.main()
│   ├── cli.py                               # click commands: index, serve, doctor
│   ├── config.py                            # pydantic Settings (paths, devices, ports)
│   ├── db/
│   │   ├── __init__.py                      # session factory, init_db()
│   │   ├── models.py                        # SQLAlchemy ORM classes
│   │   └── schema.sql                       # raw DDL for reference / migrations
│   ├── gpu.py                               # CUDA / NVDEC / nvImageCodec capability detect
│   ├── decode/
│   │   ├── __init__.py                      # public decode() facade
│   │   ├── jpeg.py                          # nvImageCodec GPU decode
│   │   ├── heic.py                          # pillow-heif → CPU → GPU upload
│   │   ├── raw.py                           # rawpy embedded thumb
│   │   └── video.py                         # torchcodec / PyAV NVDEC first-frame
│   ├── indexer/
│   │   ├── __init__.py
│   │   ├── walker.py                        # os.walk + extension filter + sha256
│   │   ├── exif.py                          # EXIF read via pyexiv2
│   │   ├── preview.py                       # 256/1024 preview writer
│   │   └── orchestrator.py                  # index_folder() top-level
│   ├── classical/
│   │   ├── __init__.py
│   │   ├── blur.py                          # cv2.Laplacian variance (cuda if avail)
│   │   ├── exposure.py                      # histogram-based clip/mean
│   │   ├── faces.py                         # SCRFD via insightface
│   │   ├── eyes.py                          # MediaPipe FaceMesh eye-aspect ratio
│   │   └── auto_reject.py                   # combine into reject flag + reason
│   ├── pipeline.py                          # stage runner; Stage1 = classical
│   ├── worker.py                            # long-running process; pulls from DB
│   └── server/
│       ├── __init__.py
│       ├── app.py                           # FastAPI() instance + middleware
│       ├── routes.py                        # REST endpoints
│       ├── ws.py                            # WebSocket: indexing/scoring progress
│       └── static.py                        # serve frontend build, thumbnails, previews
├── frontend/                                # React UI
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx                          # router + theme provider
│       ├── styles.css                       # ported from design/styles.css
│       ├── api/
│       │   ├── client.ts                    # fetch wrappers
│       │   └── types.ts                     # TS types matching pydantic models
│       ├── components/
│       │   ├── Rail.tsx
│       │   ├── Topbar.tsx
│       │   ├── StatusRow.tsx
│       │   ├── KbdFooter.tsx
│       │   ├── BurstThumb.tsx
│       │   ├── GoldFrame.tsx
│       │   ├── ScoresCard.tsx
│       │   └── MemoryRing.tsx
│       └── views/
│           ├── BurstCull.tsx                # wired to real data
│           ├── Clusters.tsx                 # static placeholder for M1
│           └── Stories.tsx                  # static placeholder for M1
└── tests/
    ├── conftest.py                          # shared fixtures (tmp folder, sample images)
    ├── fixtures/
    │   ├── small.jpg                        # one JPEG, ~100KB
    │   ├── small.heic                       # one HEIC, ~150KB
    │   ├── small.mp4                        # one 5s 1080p MP4
    │   └── small.dng                        # one DNG with embedded preview
    ├── test_walker.py
    ├── test_decode_jpeg.py
    ├── test_decode_heic.py
    ├── test_decode_raw.py
    ├── test_decode_video.py
    ├── test_exif.py
    ├── test_preview.py
    ├── test_classical_blur.py
    ├── test_classical_exposure.py
    ├── test_classical_faces.py
    ├── test_indexer_orchestrator.py
    ├── test_pipeline_stage1.py
    ├── test_server_routes.py
    └── test_integration_ladakh.py           # gated by env var, real-data smoke
```

Test fixtures (`tests/fixtures/`) are small files committed to the repo; the Ladakh test is gated by `TRAVELCULL_LADAKH_PATH` env var so CI doesn't need 1065 files.

---

## Task 1: Project scaffolding

**Files:**
- Create: `Z:\travel_post\pyproject.toml`
- Create: `Z:\travel_post\.gitignore`
- Create: `Z:\travel_post\travelcull\__init__.py`
- Create: `Z:\travel_post\tests\__init__.py`
- Create: `Z:\travel_post\tests\conftest.py`

- [ ] **Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "travelcull"
version = "0.1.0"
description = "Local AI-assisted travel photo and video culling"
requires-python = ">=3.11"
license = "MIT"
dependencies = [
  "fastapi>=0.110",
  "uvicorn[standard]>=0.27",
  "sqlalchemy>=2.0",
  "pydantic>=2.6",
  "pydantic-settings>=2.2",
  "click>=8.1",
  "pillow>=10.2",
  "pillow-heif>=0.16",
  "rawpy>=0.21",
  "opencv-python>=4.9",
  "numpy>=1.26",
  "insightface>=0.7",
  "onnxruntime-gpu>=1.17",
  "mediapipe>=0.10.18",
  "pyexiv2>=2.11",
  "websockets>=12.0",
  "python-multipart>=0.0.9",
]

[project.optional-dependencies]
gpu = [
  "torch>=2.3",
  "torchvision>=0.18",
  "torchcodec>=0.1",
  "nvidia-nvimgcodec-cu12>=0.3",
]
dev = [
  "pytest>=8.0",
  "pytest-asyncio>=0.23",
  "httpx>=0.26",
  "ruff>=0.3",
]

[project.scripts]
travelcull = "travelcull.cli:main"

[tool.hatch.build.targets.wheel]
packages = ["travelcull"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = "-ra"

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]
ignore = ["E501"]
```

- [ ] **Step 2: Create .gitignore**

```gitignore
__pycache__/
*.pyc
.venv/
.pytest_cache/
.ruff_cache/
*.egg-info/
dist/
build/
node_modules/
frontend/dist/
.travelcull/
.travelcull.db
.env
.env.local
```

- [ ] **Step 3: Create package __init__**

`Z:\travel_post\travelcull\__init__.py`:
```python
__version__ = "0.1.0"
```

- [ ] **Step 4: Create test conftest**

`Z:\travel_post\tests\conftest.py`:
```python
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def tmp_folder(tmp_path) -> Path:
    """Empty temp folder for indexer/orchestrator tests."""
    return tmp_path


@pytest.fixture
def populated_folder(tmp_path, fixtures_dir) -> Path:
    """Temp folder pre-populated with fixture images."""
    for f in fixtures_dir.iterdir():
        if f.is_file():
            (tmp_path / f.name).write_bytes(f.read_bytes())
    return tmp_path
```

- [ ] **Step 5: Install in editable mode**

Run: `pip install -e .[dev,gpu]`
Expected: install succeeds. If `nvidia-nvimgcodec-cu12` fails, note it — we'll handle the fallback in `decode/jpeg.py`.

- [ ] **Step 6: Verify package imports**

Run: `python -c "import travelcull; print(travelcull.__version__)"`
Expected: `0.1.0`

- [ ] **Step 7: Commit**

```powershell
git init
git add pyproject.toml .gitignore travelcull/__init__.py tests/__init__.py tests/conftest.py
git commit -m "chore: scaffold travelcull package and pytest config"
```

---

## Task 2: Config (pydantic Settings)

**Files:**
- Create: `Z:\travel_post\travelcull\config.py`
- Test: `Z:\travel_post\tests\test_config.py`

- [ ] **Step 1: Write the failing test**

`tests/test_config.py`:
```python
import os
from pathlib import Path

from travelcull.config import FolderConfig, get_folder_config


def test_default_config_uses_folder_path(tmp_path):
    cfg = get_folder_config(tmp_path)
    assert cfg.folder == tmp_path
    assert cfg.db_path == tmp_path / ".travelcull.db"
    assert cfg.thumbs_dir == tmp_path / ".travelcull" / "thumbs"
    assert cfg.previews_dir == tmp_path / ".travelcull" / "previews"
    assert cfg.web_port == 5173


def test_config_respects_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAVELCULL_WEB_PORT", "8080")
    cfg = get_folder_config(tmp_path)
    assert cfg.web_port == 8080
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: ImportError (module not yet created).

- [ ] **Step 3: Write config module**

`travelcull/config.py`:
```python
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class FolderConfig(BaseSettings):
    """Per-folder configuration. Reads env vars prefixed TRAVELCULL_."""

    model_config = SettingsConfigDict(env_prefix="TRAVELCULL_", extra="ignore")

    folder: Path
    web_port: int = 5173
    web_host: str = "127.0.0.1"
    burst_window_seconds: int = 120
    burst_similarity_threshold: float = 0.85
    speed_mode: str = "standard"  # "fast" | "standard" | "thorough"

    @property
    def state_dir(self) -> Path:
        return self.folder / ".travelcull"

    @property
    def db_path(self) -> Path:
        return self.folder / ".travelcull.db"

    @property
    def thumbs_dir(self) -> Path:
        return self.state_dir / "thumbs"

    @property
    def previews_dir(self) -> Path:
        return self.state_dir / "previews"


def get_folder_config(folder: Path) -> FolderConfig:
    return FolderConfig(folder=folder.resolve())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```powershell
git add travelcull/config.py tests/test_config.py
git commit -m "feat(config): per-folder pydantic Settings with env override"
```

---

## Task 3: Database models + session

**Files:**
- Create: `Z:\travel_post\travelcull\db\__init__.py`
- Create: `Z:\travel_post\travelcull\db\models.py`
- Test: `Z:\travel_post\tests\test_db.py`

- [ ] **Step 1: Write the failing test**

`tests/test_db.py`:
```python
from datetime import datetime
from pathlib import Path

from travelcull.config import get_folder_config
from travelcull.db import init_db, session_scope
from travelcull.db.models import Photo, PipelineState


def test_init_db_creates_tables(tmp_path):
    cfg = get_folder_config(tmp_path)
    init_db(cfg)
    assert cfg.db_path.exists()


def test_insert_and_retrieve_photo(tmp_path):
    cfg = get_folder_config(tmp_path)
    init_db(cfg)
    with session_scope(cfg) as s:
        p = Photo(
            path="/abs/path/IMG_0001.jpg",
            sha256="deadbeef" * 8,
            mtime=1700000000.0,
            size_bytes=1024,
            format="jpeg",
            width=4032,
            height=3024,
            taken_at=datetime(2026, 3, 28, 12, 0, 0),
        )
        s.add(p)
        s.flush()
        pid = p.id
    with session_scope(cfg) as s:
        loaded = s.get(Photo, pid)
        assert loaded.sha256 == "deadbeef" * 8


def test_pipeline_state_defaults_false(tmp_path):
    cfg = get_folder_config(tmp_path)
    init_db(cfg)
    with session_scope(cfg) as s:
        p = Photo(path="/x.jpg", sha256="a" * 64, mtime=0.0, format="jpeg")
        s.add(p)
        s.flush()
        ps = PipelineState(photo_id=p.id)
        s.add(ps)
        s.flush()
        assert ps.classical_done is False
        assert ps.embedding_done is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db.py -v`
Expected: ImportError.

- [ ] **Step 3: Write models**

`travelcull/db/models.py`:
```python
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Photo(Base):
    __tablename__ = "photo"

    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(unique=True, index=True)
    sha256: Mapped[str] = mapped_column(unique=True, index=True)
    mtime: Mapped[float]
    size_bytes: Mapped[Optional[int]] = mapped_column(default=None)
    format: Mapped[Optional[str]] = mapped_column(default=None)
    width: Mapped[Optional[int]] = mapped_column(default=None)
    height: Mapped[Optional[int]] = mapped_column(default=None)
    taken_at: Mapped[Optional[datetime]] = mapped_column(default=None, index=True)
    gps_lat: Mapped[Optional[float]] = mapped_column(default=None)
    gps_lon: Mapped[Optional[float]] = mapped_column(default=None)
    camera: Mapped[Optional[str]] = mapped_column(default=None)
    thumb_path: Mapped[Optional[str]] = mapped_column(default=None)
    preview_path: Mapped[Optional[str]] = mapped_column(default=None)
    indexed_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    classical_score: Mapped[Optional["ClassicalScore"]] = relationship(
        back_populates="photo", uselist=False, cascade="all, delete-orphan"
    )
    pipeline_state: Mapped[Optional["PipelineState"]] = relationship(
        back_populates="photo", uselist=False, cascade="all, delete-orphan"
    )


class Video(Base):
    __tablename__ = "video"

    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(unique=True, index=True)
    sha256: Mapped[str] = mapped_column(unique=True, index=True)
    mtime: Mapped[float]
    size_bytes: Mapped[Optional[int]] = mapped_column(default=None)
    format: Mapped[Optional[str]] = mapped_column(default=None)
    width: Mapped[Optional[int]] = mapped_column(default=None)
    height: Mapped[Optional[int]] = mapped_column(default=None)
    duration_sec: Mapped[Optional[float]] = mapped_column(default=None)
    taken_at: Mapped[Optional[datetime]] = mapped_column(default=None, index=True)
    thumb_path: Mapped[Optional[str]] = mapped_column(default=None)
    preview_path: Mapped[Optional[str]] = mapped_column(default=None)
    indexed_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class ClassicalScore(Base):
    __tablename__ = "classical_score"

    photo_id: Mapped[int] = mapped_column(ForeignKey("photo.id"), primary_key=True)
    blur: Mapped[Optional[float]] = mapped_column(default=None)
    exposure: Mapped[Optional[float]] = mapped_column(default=None)
    faces_count: Mapped[Optional[int]] = mapped_column(default=None)
    eyes_open_ratio: Mapped[Optional[float]] = mapped_column(default=None)
    auto_reject: Mapped[bool] = mapped_column(default=False)
    reject_reason: Mapped[Optional[str]] = mapped_column(default=None)

    photo: Mapped[Photo] = relationship(back_populates="classical_score")


class PipelineState(Base):
    __tablename__ = "pipeline_state"

    photo_id: Mapped[int] = mapped_column(ForeignKey("photo.id"), primary_key=True)
    classical_done: Mapped[bool] = mapped_column(default=False)
    embedding_done: Mapped[bool] = mapped_column(default=False)
    vl_done: Mapped[bool] = mapped_column(default=False)
    ordering_done: Mapped[bool] = mapped_column(default=False)
    error: Mapped[Optional[str]] = mapped_column(default=None)

    photo: Mapped[Photo] = relationship(back_populates="pipeline_state")


Index("idx_pipeline_pending", PipelineState.classical_done, PipelineState.embedding_done)
```

- [ ] **Step 4: Write session factory**

`travelcull/db/__init__.py`:
```python
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from travelcull.config import FolderConfig

from .models import Base


def _engine(cfg: FolderConfig):
    return create_engine(f"sqlite:///{cfg.db_path}", echo=False, future=True)


def init_db(cfg: FolderConfig) -> None:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    engine = _engine(cfg)
    Base.metadata.create_all(engine)


@contextmanager
def session_scope(cfg: FolderConfig) -> Iterator[Session]:
    engine = _engine(cfg)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_db.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```powershell
git add travelcull/db tests/test_db.py
git commit -m "feat(db): SQLAlchemy models for photo, video, classical_score, pipeline_state"
```

---

## Task 4: GPU capability detection

**Files:**
- Create: `Z:\travel_post\travelcull\gpu.py`
- Test: `Z:\travel_post\tests\test_gpu.py`

- [ ] **Step 1: Write the failing test**

`tests/test_gpu.py`:
```python
from travelcull.gpu import GpuCapabilities, detect_capabilities


def test_detect_returns_dataclass():
    caps = detect_capabilities()
    assert isinstance(caps, GpuCapabilities)


def test_caps_has_required_fields():
    caps = detect_capabilities()
    assert hasattr(caps, "cuda_available")
    assert hasattr(caps, "device_name")
    assert hasattr(caps, "nvdec_available")
    assert hasattr(caps, "nvimgcodec_available")
    assert hasattr(caps, "cv2_cuda_available")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gpu.py -v`
Expected: ImportError.

- [ ] **Step 3: Write gpu.py**

`travelcull/gpu.py`:
```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GpuCapabilities:
    cuda_available: bool
    device_name: str
    cuda_capability: tuple[int, int] | None
    vram_total_mb: int
    nvdec_available: bool
    nvimgcodec_available: bool
    cv2_cuda_available: bool


def detect_capabilities() -> GpuCapabilities:
    cuda_available = False
    device_name = "CPU"
    cap: tuple[int, int] | None = None
    vram_mb = 0
    try:
        import torch

        if torch.cuda.is_available():
            cuda_available = True
            device_name = torch.cuda.get_device_name(0)
            cap = torch.cuda.get_device_capability(0)
            vram_mb = torch.cuda.get_device_properties(0).total_memory // (1024 * 1024)
    except ImportError:
        pass

    nvdec_available = False
    try:
        import torchcodec  # noqa: F401

        nvdec_available = cuda_available
    except ImportError:
        pass

    nvimgcodec_available = False
    try:
        from nvidia import nvimgcodec  # noqa: F401

        nvimgcodec_available = cuda_available
    except ImportError:
        pass

    cv2_cuda_available = False
    try:
        import cv2

        cv2_cuda_available = cv2.cuda.getCudaEnabledDeviceCount() > 0
    except (ImportError, AttributeError):
        pass

    return GpuCapabilities(
        cuda_available=cuda_available,
        device_name=device_name,
        cuda_capability=cap,
        vram_total_mb=vram_mb,
        nvdec_available=nvdec_available,
        nvimgcodec_available=nvimgcodec_available,
        cv2_cuda_available=cv2_cuda_available,
    )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_gpu.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```powershell
git add travelcull/gpu.py tests/test_gpu.py
git commit -m "feat(gpu): detect CUDA, NVDEC, nvImageCodec, cv2.cuda capabilities"
```

---

## Task 5: File walker + hashing

**Files:**
- Create: `Z:\travel_post\travelcull\indexer\__init__.py` (empty)
- Create: `Z:\travel_post\travelcull\indexer\walker.py`
- Test: `Z:\travel_post\tests\test_walker.py`

- [ ] **Step 1: Write the failing test**

`tests/test_walker.py`:
```python
from pathlib import Path

from travelcull.indexer.walker import FileKind, classify, sha256_of, walk_supported


def test_classify_extensions():
    assert classify(Path("x.jpg")) == FileKind.JPEG
    assert classify(Path("x.JPG")) == FileKind.JPEG
    assert classify(Path("x.heic")) == FileKind.HEIC
    assert classify(Path("x.dng")) == FileKind.RAW
    assert classify(Path("x.cr3")) == FileKind.RAW
    assert classify(Path("x.mp4")) == FileKind.VIDEO
    assert classify(Path("x.mov")) == FileKind.VIDEO
    assert classify(Path("x.txt")) is None


def test_walk_supported_finds_files(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"x")
    (tmp_path / "b.HEIC").write_bytes(b"y")
    (tmp_path / "skip.txt").write_text("nope")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.mp4").write_bytes(b"z")

    found = sorted(p.name for p, _ in walk_supported(tmp_path))
    assert found == ["a.jpg", "b.HEIC", "c.mp4"]


def test_walker_skips_travelcull_dir(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"x")
    state = tmp_path / ".travelcull"
    state.mkdir()
    (state / "thumb.jpg").write_bytes(b"y")

    found = [p.name for p, _ in walk_supported(tmp_path)]
    assert "thumb.jpg" not in found


def test_sha256_stable(tmp_path):
    f = tmp_path / "f.bin"
    f.write_bytes(b"hello world")
    h1 = sha256_of(f)
    h2 = sha256_of(f)
    assert h1 == h2
    assert len(h1) == 64
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_walker.py -v`
Expected: ImportError.

- [ ] **Step 3: Write walker**

`travelcull/indexer/walker.py`:
```python
from __future__ import annotations

import hashlib
from enum import Enum
from pathlib import Path
from typing import Iterator


class FileKind(str, Enum):
    JPEG = "jpeg"
    HEIC = "heic"
    RAW = "raw"
    VIDEO = "video"


_JPEG_EXTS = {".jpg", ".jpeg"}
_HEIC_EXTS = {".heic", ".heif"}
_RAW_EXTS = {".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".orf", ".rw2", ".pef"}
_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv"}

_SKIP_DIRS = {".travelcull", ".git", "__pycache__", "node_modules"}


def classify(path: Path) -> FileKind | None:
    ext = path.suffix.lower()
    if ext in _JPEG_EXTS:
        return FileKind.JPEG
    if ext in _HEIC_EXTS:
        return FileKind.HEIC
    if ext in _RAW_EXTS:
        return FileKind.RAW
    if ext in _VIDEO_EXTS:
        return FileKind.VIDEO
    return None


def walk_supported(root: Path) -> Iterator[tuple[Path, FileKind]]:
    """Yield (path, kind) for each supported file under root, skipping state dirs."""
    for dirpath, dirnames, filenames in _os_walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            p = dirpath / name
            kind = classify(p)
            if kind is not None:
                yield p, kind


def _os_walk(root: Path):
    import os

    for dp, dn, fn in os.walk(root):
        yield Path(dp), dn, fn


def sha256_of(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while data := f.read(chunk):
            h.update(data)
    return h.hexdigest()
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_walker.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```powershell
git add travelcull/indexer/__init__.py travelcull/indexer/walker.py tests/test_walker.py
git commit -m "feat(indexer): file walker with HEIC/JPEG/RAW/MP4 classification + sha256"
```

---

## Task 6: EXIF reader

**Files:**
- Create: `Z:\travel_post\travelcull\indexer\exif.py`
- Test: `Z:\travel_post\tests\test_exif.py`

We use **pyexiv2** because it reads HEIC, JPEG, and most RAW formats from a single API.

- [ ] **Step 1: Write the failing test**

`tests/test_exif.py`:
```python
from pathlib import Path

from travelcull.indexer.exif import ExifData, read_exif


def test_read_exif_returns_dataclass(fixtures_dir):
    data = read_exif(fixtures_dir / "small.jpg")
    assert isinstance(data, ExifData)


def test_read_exif_handles_missing_file(tmp_path):
    data = read_exif(tmp_path / "nope.jpg")
    assert data.taken_at is None
    assert data.width is None


def test_read_exif_width_height_when_present(fixtures_dir):
    data = read_exif(fixtures_dir / "small.jpg")
    if data.width is not None:
        assert data.width > 0
        assert data.height > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_exif.py -v`
Expected: ImportError.

- [ ] **Step 3: Write exif module**

`travelcull/indexer/exif.py`:
```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class ExifData:
    taken_at: datetime | None = None
    width: int | None = None
    height: int | None = None
    camera: str | None = None
    gps_lat: float | None = None
    gps_lon: float | None = None


def read_exif(path: Path) -> ExifData:
    if not path.exists():
        return ExifData()
    try:
        import pyexiv2

        with pyexiv2.Image(str(path)) as img:
            exif = img.read_exif() or {}
    except Exception:
        return ExifData()

    return ExifData(
        taken_at=_parse_dt(exif.get("Exif.Photo.DateTimeOriginal")),
        width=_to_int(exif.get("Exif.Photo.PixelXDimension") or exif.get("Exif.Image.ImageWidth")),
        height=_to_int(exif.get("Exif.Photo.PixelYDimension") or exif.get("Exif.Image.ImageLength")),
        camera=_camera_from(exif),
        gps_lat=_gps(exif.get("Exif.GPSInfo.GPSLatitude"), exif.get("Exif.GPSInfo.GPSLatitudeRef")),
        gps_lon=_gps(exif.get("Exif.GPSInfo.GPSLongitude"), exif.get("Exif.GPSInfo.GPSLongitudeRef")),
    )


def _parse_dt(s):
    if not s:
        return None
    try:
        return datetime.strptime(str(s), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def _to_int(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _camera_from(exif: dict) -> str | None:
    make = exif.get("Exif.Image.Make", "").strip()
    model = exif.get("Exif.Image.Model", "").strip()
    full = f"{make} {model}".strip()
    return full or None


def _gps(value, ref):
    if not value or not ref:
        return None
    try:
        d, m, s = (float(eval(x)) for x in str(value).split())  # rationals like "12/1"
        decimal = d + m / 60 + s / 3600
        if str(ref).upper() in ("S", "W"):
            decimal = -decimal
        return decimal
    except Exception:
        return None
```

- [ ] **Step 4: Generate the JPEG fixture if missing**

Run:
```powershell
python -c "from PIL import Image; Image.new('RGB',(640,480),'gray').save('tests/fixtures/small.jpg','JPEG',quality=85)"
```
Expected: file created at `tests/fixtures/small.jpg`. (Note: pure-color images may have no EXIF — that's fine for our defensive code path.)

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_exif.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```powershell
git add travelcull/indexer/exif.py tests/test_exif.py tests/fixtures/small.jpg
git commit -m "feat(indexer): EXIF reader via pyexiv2 with HEIC + JPEG + RAW support"
```

---

## Task 7: JPEG decode (nvImageCodec, with PIL fallback)

**Files:**
- Create: `Z:\travel_post\travelcull\decode\__init__.py`
- Create: `Z:\travel_post\travelcull\decode\jpeg.py`
- Test: `Z:\travel_post\tests\test_decode_jpeg.py`

- [ ] **Step 1: Write the failing test**

`tests/test_decode_jpeg.py`:
```python
import numpy as np

from travelcull.decode.jpeg import decode_jpeg


def test_decode_jpeg_returns_hwc_uint8(fixtures_dir):
    img = decode_jpeg(fixtures_dir / "small.jpg")
    assert img.dtype == np.uint8
    assert img.ndim == 3
    assert img.shape[2] == 3


def test_decode_jpeg_dimensions(fixtures_dir):
    img = decode_jpeg(fixtures_dir / "small.jpg")
    assert img.shape[0] == 480
    assert img.shape[1] == 640
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_decode_jpeg.py -v`
Expected: ImportError.

- [ ] **Step 3: Write decode/__init__.py facade**

`travelcull/decode/__init__.py`:
```python
from pathlib import Path

import numpy as np

from travelcull.indexer.walker import FileKind

from .heic import decode_heic
from .jpeg import decode_jpeg
from .raw import decode_raw_preview


def decode(path: Path, kind: FileKind) -> np.ndarray:
    """Decode any supported image format to HWC uint8 RGB ndarray."""
    if kind == FileKind.JPEG:
        return decode_jpeg(path)
    if kind == FileKind.HEIC:
        return decode_heic(path)
    if kind == FileKind.RAW:
        return decode_raw_preview(path)
    raise ValueError(f"decode() does not handle {kind}; use video.decode_video_frame")
```

- [ ] **Step 4: Write JPEG decoder**

`travelcull/decode/jpeg.py`:
```python
from __future__ import annotations

from pathlib import Path

import numpy as np

_nvimg_decoder = None


def _try_nvimg():
    global _nvimg_decoder
    if _nvimg_decoder is not None:
        return _nvimg_decoder
    try:
        from nvidia import nvimgcodec

        _nvimg_decoder = nvimgcodec.Decoder()
        return _nvimg_decoder
    except Exception:
        _nvimg_decoder = False
        return False


def decode_jpeg(path: Path) -> np.ndarray:
    """Decode JPEG to HWC uint8 RGB ndarray. Prefers GPU via nvImageCodec, falls back to PIL."""
    dec = _try_nvimg()
    if dec:
        try:
            with path.open("rb") as f:
                data = f.read()
            img = dec.decode(data)
            arr = np.asarray(img.cpu()) if hasattr(img, "cpu") else np.asarray(img)
            if arr.shape[-1] == 3:
                return arr.astype(np.uint8, copy=False)
        except Exception:
            pass

    from PIL import Image

    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"), dtype=np.uint8)
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_decode_jpeg.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```powershell
git add travelcull/decode tests/test_decode_jpeg.py
git commit -m "feat(decode): JPEG decoder with nvImageCodec GPU path + PIL fallback"
```

---

## Task 8: HEIC decode

**Files:**
- Create: `Z:\travel_post\travelcull\decode\heic.py`
- Test: `Z:\travel_post\tests\test_decode_heic.py`
- Generate: `tests/fixtures/small.heic`

- [ ] **Step 1: Generate HEIC fixture**

```powershell
python -c "from PIL import Image; from pillow_heif import register_heif_opener; register_heif_opener(); Image.new('RGB',(640,480),(120,140,160)).save('tests/fixtures/small.heic','HEIF',quality=85)"
```

- [ ] **Step 2: Write the failing test**

`tests/test_decode_heic.py`:
```python
import numpy as np

from travelcull.decode.heic import decode_heic


def test_decode_heic_returns_uint8_rgb(fixtures_dir):
    img = decode_heic(fixtures_dir / "small.heic")
    assert img.dtype == np.uint8
    assert img.shape[2] == 3
    assert img.shape[0] == 480
    assert img.shape[1] == 640
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_decode_heic.py -v`
Expected: ImportError.

- [ ] **Step 4: Write HEIC decoder**

`travelcull/decode/heic.py`:
```python
from __future__ import annotations

from pathlib import Path

import numpy as np
from pillow_heif import register_heif_opener

register_heif_opener()


def decode_heic(path: Path) -> np.ndarray:
    """Decode HEIC/HEIF to HWC uint8 RGB ndarray. CPU-bound (no GPU HEIC codec in OSS)."""
    from PIL import Image

    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"), dtype=np.uint8)
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_decode_heic.py -v`
Expected: 1 passed.

- [ ] **Step 6: Commit**

```powershell
git add travelcull/decode/heic.py tests/test_decode_heic.py tests/fixtures/small.heic
git commit -m "feat(decode): HEIC decoder via pillow-heif"
```

---

## Task 9: RAW embedded preview decode

**Files:**
- Create: `Z:\travel_post\travelcull\decode\raw.py`
- Test: `Z:\travel_post\tests\test_decode_raw.py`

Skip the test if no DNG fixture is available — generating one synthetically is hard. We mark the test with a skip-if-missing guard.

- [ ] **Step 1: Write the failing test**

`tests/test_decode_raw.py`:
```python
from pathlib import Path

import numpy as np
import pytest

from travelcull.decode.raw import decode_raw_preview

DNG = Path(__file__).parent / "fixtures" / "small.dng"


@pytest.mark.skipif(not DNG.exists(), reason="No DNG fixture")
def test_decode_raw_preview_returns_uint8_rgb():
    img = decode_raw_preview(DNG)
    assert img.dtype == np.uint8
    assert img.ndim == 3
    assert img.shape[2] == 3
    assert img.shape[0] >= 200 and img.shape[1] >= 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_decode_raw.py -v`
Expected: ImportError (the module itself).

- [ ] **Step 3: Write RAW decoder**

`travelcull/decode/raw.py`:
```python
from __future__ import annotations

import io
from pathlib import Path

import numpy as np


def decode_raw_preview(path: Path) -> np.ndarray:
    """Read the embedded JPEG preview from a RAW file.

    Falls back to a full rawpy demosaic if no embedded preview exists,
    but that path is slower and should be exceptional.
    """
    import rawpy

    with rawpy.imread(str(path)) as raw:
        try:
            thumb = raw.extract_thumb()
            if thumb.format == rawpy.ThumbFormat.JPEG:
                from PIL import Image

                with Image.open(io.BytesIO(thumb.data)) as im:
                    return np.asarray(im.convert("RGB"), dtype=np.uint8)
            return np.asarray(thumb.data, dtype=np.uint8)
        except rawpy.LibRawNoThumbnailError:
            rgb = raw.postprocess(use_camera_wb=True, output_bps=8, no_auto_bright=True)
            return np.ascontiguousarray(rgb, dtype=np.uint8)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_decode_raw.py -v`
Expected: 1 skipped (no fixture) OR 1 passed if user dropped a DNG into fixtures.

- [ ] **Step 5: Commit**

```powershell
git add travelcull/decode/raw.py tests/test_decode_raw.py
git commit -m "feat(decode): RAW embedded preview extraction via rawpy"
```

---

## Task 10: Video first-frame decode via NVDEC

**Files:**
- Create: `Z:\travel_post\travelcull\decode\video.py`
- Test: `Z:\travel_post\tests\test_decode_video.py`
- Generate: `tests/fixtures/small.mp4`

- [ ] **Step 1: Generate MP4 fixture using ffmpeg**

```powershell
ffmpeg -y -f lavfi -i color=c=gray:s=640x480:d=2 -c:v libx264 -pix_fmt yuv420p tests/fixtures/small.mp4
```

- [ ] **Step 2: Write the failing test**

`tests/test_decode_video.py`:
```python
import numpy as np

from travelcull.decode.video import VideoMeta, decode_first_frame, probe


def test_probe_returns_meta(fixtures_dir):
    m = probe(fixtures_dir / "small.mp4")
    assert isinstance(m, VideoMeta)
    assert m.width == 640
    assert m.height == 480
    assert 1.5 < m.duration_sec < 2.5


def test_decode_first_frame_returns_uint8_rgb(fixtures_dir):
    frame = decode_first_frame(fixtures_dir / "small.mp4")
    assert frame.dtype == np.uint8
    assert frame.shape[2] == 3
    assert frame.shape[:2] == (480, 640)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_decode_video.py -v`
Expected: ImportError.

- [ ] **Step 4: Write video decoder**

`travelcull/decode/video.py`:
```python
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class VideoMeta:
    width: int
    height: int
    duration_sec: float
    codec: str


def probe(path: Path) -> VideoMeta:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,codec_name,duration",
        "-of",
        "default=noprint_wrappers=1:nokey=0",
        str(path),
    ]
    out = subprocess.check_output(cmd, text=True)
    kv = {}
    for line in out.strip().splitlines():
        k, _, v = line.partition("=")
        kv[k.strip()] = v.strip()
    return VideoMeta(
        width=int(kv["width"]),
        height=int(kv["height"]),
        duration_sec=float(kv.get("duration", 0.0)),
        codec=kv.get("codec_name", "unknown"),
    )


def decode_first_frame(path: Path) -> np.ndarray:
    """Decode a single representative frame. Prefers NVDEC via torchcodec."""
    try:
        from torchcodec.decoders import VideoDecoder

        dec = VideoDecoder(str(path), device="cuda")
        frame = dec[0]  # first frame as CHW uint8 tensor
        arr = frame.permute(1, 2, 0).cpu().numpy()
        return np.ascontiguousarray(arr, dtype=np.uint8)
    except Exception:
        pass

    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-f",
        "image2pipe",
        "-pix_fmt",
        "rgb24",
        "-vcodec",
        "rawvideo",
        "-",
    ]
    meta = probe(path)
    raw = subprocess.check_output(cmd)
    return np.frombuffer(raw, dtype=np.uint8).reshape(meta.height, meta.width, 3).copy()
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_decode_video.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```powershell
git add travelcull/decode/video.py tests/test_decode_video.py tests/fixtures/small.mp4
git commit -m "feat(decode): video first-frame decode via torchcodec (NVDEC) with ffmpeg fallback"
```

---

## Task 11: Preview / thumbnail generator

**Files:**
- Create: `Z:\travel_post\travelcull\indexer\preview.py`
- Test: `Z:\travel_post\tests\test_preview.py`

- [ ] **Step 1: Write the failing test**

`tests/test_preview.py`:
```python
from pathlib import Path

import numpy as np

from travelcull.indexer.preview import write_previews


def test_write_previews_creates_two_files(tmp_path):
    img = (np.random.rand(2000, 1500, 3) * 255).astype(np.uint8)
    thumb_path, preview_path = write_previews(
        img, sha256="abc123", thumbs_dir=tmp_path / "thumbs", previews_dir=tmp_path / "prev"
    )
    assert thumb_path.exists()
    assert preview_path.exists()
    assert thumb_path.suffix == ".jpg"


def test_thumb_is_256_max(tmp_path):
    img = (np.random.rand(4000, 3000, 3) * 255).astype(np.uint8)
    thumb_path, _ = write_previews(
        img, sha256="def", thumbs_dir=tmp_path / "t", previews_dir=tmp_path / "p"
    )
    from PIL import Image

    with Image.open(thumb_path) as im:
        assert max(im.size) == 256


def test_preview_is_1024_max(tmp_path):
    img = (np.random.rand(4000, 3000, 3) * 255).astype(np.uint8)
    _, preview_path = write_previews(
        img, sha256="ghi", thumbs_dir=tmp_path / "t", previews_dir=tmp_path / "p"
    )
    from PIL import Image

    with Image.open(preview_path) as im:
        assert max(im.size) == 1024
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_preview.py -v`
Expected: ImportError.

- [ ] **Step 3: Write preview module**

`travelcull/indexer/preview.py`:
```python
from __future__ import annotations

from pathlib import Path

import numpy as np

THUMB_LONG_EDGE = 256
PREVIEW_LONG_EDGE = 1024
JPEG_QUALITY = 85


def write_previews(
    img: np.ndarray, sha256: str, thumbs_dir: Path, previews_dir: Path
) -> tuple[Path, Path]:
    """Write 256px thumb and 1024px preview JPEGs. Returns absolute paths."""
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    previews_dir.mkdir(parents=True, exist_ok=True)

    thumb_path = thumbs_dir / f"{sha256}.jpg"
    preview_path = previews_dir / f"{sha256}.jpg"

    _resize_and_save(img, THUMB_LONG_EDGE, thumb_path)
    _resize_and_save(img, PREVIEW_LONG_EDGE, preview_path)

    return thumb_path, preview_path


def _resize_and_save(img: np.ndarray, long_edge: int, out_path: Path) -> None:
    from PIL import Image

    h, w = img.shape[:2]
    scale = long_edge / max(h, w)
    if scale >= 1.0:
        resized = img
    else:
        new_w, new_h = int(w * scale), int(h * scale)
        with Image.fromarray(img) as im:
            im_resized = im.resize((new_w, new_h), Image.Resampling.LANCZOS)
            im_resized.save(out_path, "JPEG", quality=JPEG_QUALITY, optimize=False)
            return
    Image.fromarray(resized).save(out_path, "JPEG", quality=JPEG_QUALITY)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_preview.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```powershell
git add travelcull/indexer/preview.py tests/test_preview.py
git commit -m "feat(indexer): write 256px thumbnail + 1024px preview JPEGs"
```

---

## Task 12: Indexer orchestrator

**Files:**
- Create: `Z:\travel_post\travelcull\indexer\orchestrator.py`
- Test: `Z:\travel_post\tests\test_indexer_orchestrator.py`

- [ ] **Step 1: Write the failing test**

`tests/test_indexer_orchestrator.py`:
```python
from travelcull.config import get_folder_config
from travelcull.db import init_db, session_scope
from travelcull.db.models import Photo, Video
from travelcull.indexer.orchestrator import index_folder


def test_index_folder_creates_photo_rows(populated_folder):
    cfg = get_folder_config(populated_folder)
    init_db(cfg)
    n = index_folder(cfg)
    with session_scope(cfg) as s:
        photos = s.query(Photo).all()
        assert len(photos) >= 2  # jpg + heic at minimum


def test_index_folder_creates_video_rows(populated_folder):
    cfg = get_folder_config(populated_folder)
    init_db(cfg)
    index_folder(cfg)
    with session_scope(cfg) as s:
        videos = s.query(Video).all()
        assert len(videos) == 1


def test_index_folder_is_idempotent(populated_folder):
    cfg = get_folder_config(populated_folder)
    init_db(cfg)
    n1 = index_folder(cfg)
    n2 = index_folder(cfg)
    assert n2 == 0  # nothing new the second run


def test_indexed_photos_have_previews(populated_folder):
    cfg = get_folder_config(populated_folder)
    init_db(cfg)
    index_folder(cfg)
    with session_scope(cfg) as s:
        for photo in s.query(Photo).all():
            assert photo.thumb_path is not None
            assert (cfg.thumbs_dir / f"{photo.sha256}.jpg").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_indexer_orchestrator.py -v`
Expected: ImportError.

- [ ] **Step 3: Write orchestrator**

`travelcull/indexer/orchestrator.py`:
```python
from __future__ import annotations

import logging
from typing import Callable

from travelcull.config import FolderConfig
from travelcull.db import session_scope
from travelcull.db.models import PipelineState, Photo, Video
from travelcull.decode import decode
from travelcull.decode.video import decode_first_frame, probe
from travelcull.indexer.exif import read_exif
from travelcull.indexer.preview import write_previews
from travelcull.indexer.walker import FileKind, sha256_of, walk_supported

log = logging.getLogger(__name__)
ProgressCb = Callable[[int, int, str], None] | None


def index_folder(cfg: FolderConfig, on_progress: ProgressCb = None) -> int:
    """Walk the folder and add new files. Returns count of new rows."""
    files = list(walk_supported(cfg.folder))
    total = len(files)
    added = 0

    with session_scope(cfg) as s:
        existing = {row[0] for row in s.execute(_select_all_hashes()).all()}

    for i, (path, kind) in enumerate(files, start=1):
        if on_progress:
            on_progress(i, total, str(path.name))
        sha = sha256_of(path)
        if sha in existing:
            continue

        try:
            if kind == FileKind.VIDEO:
                added += _ingest_video(cfg, path, sha)
            else:
                added += _ingest_photo(cfg, path, sha, kind)
        except Exception as exc:
            log.warning("Failed to ingest %s: %s", path, exc)

    return added


def _select_all_hashes():
    from sqlalchemy import select

    return select(Photo.sha256).union_all(select(Video.sha256))


def _ingest_photo(cfg: FolderConfig, path, sha: str, kind: FileKind) -> int:
    img = decode(path, kind)
    exif = read_exif(path)
    thumb_path, preview_path = write_previews(img, sha, cfg.thumbs_dir, cfg.previews_dir)

    with session_scope(cfg) as s:
        p = Photo(
            path=str(path),
            sha256=sha,
            mtime=path.stat().st_mtime,
            size_bytes=path.stat().st_size,
            format=kind.value,
            width=exif.width or img.shape[1],
            height=exif.height or img.shape[0],
            taken_at=exif.taken_at,
            gps_lat=exif.gps_lat,
            gps_lon=exif.gps_lon,
            camera=exif.camera,
            thumb_path=str(thumb_path.relative_to(cfg.state_dir)),
            preview_path=str(preview_path.relative_to(cfg.state_dir)),
        )
        s.add(p)
        s.flush()
        s.add(PipelineState(photo_id=p.id))
    return 1


def _ingest_video(cfg: FolderConfig, path, sha: str) -> int:
    meta = probe(path)
    frame = decode_first_frame(path)
    exif = read_exif(path)  # mostly empty for video, but works for some formats
    thumb_path, preview_path = write_previews(frame, sha, cfg.thumbs_dir, cfg.previews_dir)

    with session_scope(cfg) as s:
        v = Video(
            path=str(path),
            sha256=sha,
            mtime=path.stat().st_mtime,
            size_bytes=path.stat().st_size,
            format=meta.codec,
            width=meta.width,
            height=meta.height,
            duration_sec=meta.duration_sec,
            taken_at=exif.taken_at,
            thumb_path=str(thumb_path.relative_to(cfg.state_dir)),
            preview_path=str(preview_path.relative_to(cfg.state_dir)),
        )
        s.add(v)
    return 1
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_indexer_orchestrator.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```powershell
git add travelcull/indexer/orchestrator.py tests/test_indexer_orchestrator.py
git commit -m "feat(indexer): orchestrator walks folder, decodes, writes previews, persists rows"
```

---

## Task 13: Classical signals — blur

**Files:**
- Create: `Z:\travel_post\travelcull\classical\__init__.py` (empty)
- Create: `Z:\travel_post\travelcull\classical\blur.py`
- Test: `Z:\travel_post\tests\test_classical_blur.py`

- [ ] **Step 1: Write the failing test**

`tests/test_classical_blur.py`:
```python
import numpy as np

from travelcull.classical.blur import laplacian_variance


def test_blur_high_variance_for_sharp_image():
    rng = np.random.default_rng(0)
    sharp = (rng.random((480, 640, 3)) * 255).astype(np.uint8)
    val = laplacian_variance(sharp)
    assert val > 1000


def test_blur_low_variance_for_uniform_image():
    uniform = np.full((480, 640, 3), 128, dtype=np.uint8)
    val = laplacian_variance(uniform)
    assert val < 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_classical_blur.py -v`
Expected: ImportError.

- [ ] **Step 3: Write blur module**

`travelcull/classical/blur.py`:
```python
from __future__ import annotations

import numpy as np


def laplacian_variance(img: np.ndarray) -> float:
    """Variance of Laplacian as a sharpness proxy. Higher = sharper."""
    import cv2

    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    try:
        if cv2.cuda.getCudaEnabledDeviceCount() > 0:
            return _gpu_lap_var(gray)
    except AttributeError:
        pass
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    return float(lap.var())


def _gpu_lap_var(gray: np.ndarray) -> float:
    import cv2

    gpu = cv2.cuda_GpuMat()
    gpu.upload(gray)
    lap = cv2.cuda.createLaplacianFilter(cv2.CV_8U, cv2.CV_64F, ksize=1).apply(gpu)
    lap_cpu = lap.download()
    return float(lap_cpu.var())
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_classical_blur.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```powershell
git add travelcull/classical/__init__.py travelcull/classical/blur.py tests/test_classical_blur.py
git commit -m "feat(classical): Laplacian variance blur metric with cv2.cuda path"
```

---

## Task 14: Classical signals — exposure

**Files:**
- Create: `Z:\travel_post\travelcull\classical\exposure.py`
- Test: `Z:\travel_post\tests\test_classical_exposure.py`

- [ ] **Step 1: Write the failing test**

`tests/test_classical_exposure.py`:
```python
import numpy as np

from travelcull.classical.exposure import exposure_score


def test_balanced_image_scores_high():
    img = np.full((100, 100, 3), 128, dtype=np.uint8)
    s = exposure_score(img)
    assert 0.4 < s.score < 0.7
    assert s.clipped_ratio < 0.05


def test_black_image_scores_low():
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    s = exposure_score(img)
    assert s.score < 0.2
    assert s.clipped_ratio > 0.9


def test_blown_out_image_scores_low():
    img = np.full((100, 100, 3), 255, dtype=np.uint8)
    s = exposure_score(img)
    assert s.score < 0.2
    assert s.clipped_ratio > 0.9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_classical_exposure.py -v`
Expected: ImportError.

- [ ] **Step 3: Write exposure module**

`travelcull/classical/exposure.py`:
```python
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class ExposureResult:
    score: float
    mean: float
    clipped_ratio: float


def exposure_score(img: np.ndarray) -> ExposureResult:
    """Score in [0,1]. 1 = mid-gray balanced, 0 = all-black or all-white."""
    import cv2

    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    mean = float(gray.mean()) / 255.0

    n = gray.size
    clipped = float(((gray < 8).sum() + (gray > 247).sum()) / n)

    midness = 1.0 - 2.0 * abs(mean - 0.5)
    score = max(0.0, midness * (1.0 - clipped))
    return ExposureResult(score=score, mean=mean, clipped_ratio=clipped)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_classical_exposure.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```powershell
git add travelcull/classical/exposure.py tests/test_classical_exposure.py
git commit -m "feat(classical): exposure score from gray histogram clipping"
```

---

## Task 15: Classical signals — face detection (SCRFD)

**Files:**
- Create: `Z:\travel_post\travelcull\classical\faces.py`
- Test: `Z:\travel_post\tests\test_classical_faces.py`

- [ ] **Step 1: Write the failing test**

`tests/test_classical_faces.py`:
```python
import numpy as np

from travelcull.classical.faces import detect_faces


def test_detect_returns_list_for_random_image():
    rng = np.random.default_rng(0)
    img = (rng.random((480, 640, 3)) * 255).astype(np.uint8)
    faces = detect_faces(img)
    assert isinstance(faces, list)


def test_no_faces_in_solid_image():
    img = np.full((480, 640, 3), 128, dtype=np.uint8)
    assert detect_faces(img) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_classical_faces.py -v`
Expected: ImportError.

- [ ] **Step 3: Write faces module**

`travelcull/classical/faces.py`:
```python
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

_detector = None


@dataclass
class Face:
    x: int
    y: int
    w: int
    h: int
    confidence: float


def _get_detector():
    global _detector
    if _detector is not None:
        return _detector
    from insightface.app import FaceAnalysis

    app = FaceAnalysis(name="buffalo_l", providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(640, 640))
    _detector = app
    return app


def detect_faces(img: np.ndarray) -> list[Face]:
    import cv2

    bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    det = _get_detector()
    faces = det.get(bgr)
    result = []
    for f in faces:
        x1, y1, x2, y2 = (int(v) for v in f.bbox)
        result.append(Face(x=x1, y=y1, w=x2 - x1, h=y2 - y1, confidence=float(f.det_score)))
    return result
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_classical_faces.py -v`
Expected: 2 passed. (First run downloads buffalo_l weights, ~250MB — slow.)

- [ ] **Step 5: Commit**

```powershell
git add travelcull/classical/faces.py tests/test_classical_faces.py
git commit -m "feat(classical): face detection via insightface buffalo_l (CUDA)"
```

---

## Task 16: Classical signals — auto-reject combiner

**Files:**
- Create: `Z:\travel_post\travelcull\classical\auto_reject.py`
- Test: `Z:\travel_post\tests\test_auto_reject.py`

We skip the explicit eyes-open detector for M1 (it's noisy; defer to M2 when we have a richer feature set). For M1, auto-reject triggers from blur + exposure only.

- [ ] **Step 1: Write the failing test**

`tests/test_auto_reject.py`:
```python
from travelcull.classical.auto_reject import RejectInput, evaluate_reject


def test_sharp_balanced_image_not_rejected():
    inp = RejectInput(blur=2000.0, exposure_score=0.6, clipped_ratio=0.02, faces_count=1)
    result = evaluate_reject(inp)
    assert result.auto_reject is False
    assert result.reason is None


def test_severe_blur_triggers_reject():
    inp = RejectInput(blur=10.0, exposure_score=0.5, clipped_ratio=0.05, faces_count=0)
    result = evaluate_reject(inp)
    assert result.auto_reject is True
    assert result.reason == "severe_blur"


def test_blown_out_triggers_reject():
    inp = RejectInput(blur=2000.0, exposure_score=0.0, clipped_ratio=0.99, faces_count=0)
    result = evaluate_reject(inp)
    assert result.auto_reject is True
    assert result.reason in ("blown_out", "all_black")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_auto_reject.py -v`
Expected: ImportError.

- [ ] **Step 3: Write auto_reject**

`travelcull/classical/auto_reject.py`:
```python
from __future__ import annotations

from dataclasses import dataclass

BLUR_THRESHOLD = 30.0
CLIP_THRESHOLD = 0.95


@dataclass
class RejectInput:
    blur: float
    exposure_score: float
    clipped_ratio: float
    faces_count: int


@dataclass
class RejectResult:
    auto_reject: bool
    reason: str | None


def evaluate_reject(inp: RejectInput) -> RejectResult:
    if inp.blur < BLUR_THRESHOLD:
        return RejectResult(True, "severe_blur")
    if inp.clipped_ratio > CLIP_THRESHOLD:
        if inp.exposure_score < 0.1 and inp.faces_count == 0:
            return RejectResult(True, "blown_out" if inp.exposure_score > 0.5 else "all_black")
    return RejectResult(False, None)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_auto_reject.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```powershell
git add travelcull/classical/auto_reject.py tests/test_auto_reject.py
git commit -m "feat(classical): auto-reject rule for severe blur / blown-out / all-black"
```

---

## Task 17: Stage 1 pipeline runner

**Files:**
- Create: `Z:\travel_post\travelcull\pipeline.py`
- Test: `Z:\travel_post\tests\test_pipeline_stage1.py`

- [ ] **Step 1: Write the failing test**

`tests/test_pipeline_stage1.py`:
```python
from travelcull.config import get_folder_config
from travelcull.db import init_db, session_scope
from travelcull.db.models import ClassicalScore, PipelineState
from travelcull.indexer.orchestrator import index_folder
from travelcull.pipeline import run_classical_stage


def test_stage1_writes_classical_scores(populated_folder):
    cfg = get_folder_config(populated_folder)
    init_db(cfg)
    index_folder(cfg)
    run_classical_stage(cfg)
    with session_scope(cfg) as s:
        scores = s.query(ClassicalScore).all()
        states = s.query(PipelineState).all()
        assert len(scores) >= 2
        assert all(p.classical_done for p in states)


def test_stage1_is_idempotent(populated_folder):
    cfg = get_folder_config(populated_folder)
    init_db(cfg)
    index_folder(cfg)
    n1 = run_classical_stage(cfg)
    n2 = run_classical_stage(cfg)
    assert n2 == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pipeline_stage1.py -v`
Expected: ImportError.

- [ ] **Step 3: Write pipeline module**

`travelcull/pipeline.py`:
```python
from __future__ import annotations

import logging
from typing import Callable

import numpy as np
from PIL import Image

from travelcull.classical.auto_reject import RejectInput, evaluate_reject
from travelcull.classical.blur import laplacian_variance
from travelcull.classical.exposure import exposure_score
from travelcull.classical.faces import detect_faces
from travelcull.config import FolderConfig
from travelcull.db import session_scope
from travelcull.db.models import ClassicalScore, PipelineState, Photo

log = logging.getLogger(__name__)
ProgressCb = Callable[[int, int, str], None] | None


def run_classical_stage(cfg: FolderConfig, on_progress: ProgressCb = None) -> int:
    """Run classical signals on every photo with classical_done=False. Returns count processed."""
    with session_scope(cfg) as s:
        pending = (
            s.query(Photo, PipelineState)
            .join(PipelineState, Photo.id == PipelineState.photo_id)
            .filter(PipelineState.classical_done.is_(False))
            .all()
        )
    if not pending:
        return 0

    total = len(pending)
    for i, (photo, _state) in enumerate(pending, start=1):
        if on_progress:
            on_progress(i, total, photo.path)
        try:
            _score_one(cfg, photo)
        except Exception as exc:
            log.warning("classical stage failed on %s: %s", photo.path, exc)
            with session_scope(cfg) as s:
                ps = s.get(PipelineState, photo.id)
                ps.error = str(exc)[:500]
    return total


def _score_one(cfg: FolderConfig, photo: Photo) -> None:
    img = _load_preview(cfg, photo)
    blur = laplacian_variance(img)
    exp = exposure_score(img)
    faces = detect_faces(img)
    rej = evaluate_reject(
        RejectInput(blur=blur, exposure_score=exp.score, clipped_ratio=exp.clipped_ratio, faces_count=len(faces))
    )

    with session_scope(cfg) as s:
        score = s.get(ClassicalScore, photo.id) or ClassicalScore(photo_id=photo.id)
        score.blur = blur
        score.exposure = exp.score
        score.faces_count = len(faces)
        score.auto_reject = rej.auto_reject
        score.reject_reason = rej.reason
        s.add(score)
        ps = s.get(PipelineState, photo.id)
        ps.classical_done = True
        s.add(ps)


def _load_preview(cfg: FolderConfig, photo: Photo) -> np.ndarray:
    preview_abs = cfg.state_dir / photo.preview_path
    with Image.open(preview_abs) as im:
        return np.asarray(im.convert("RGB"), dtype=np.uint8)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_pipeline_stage1.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```powershell
git add travelcull/pipeline.py tests/test_pipeline_stage1.py
git commit -m "feat(pipeline): Stage 1 runs classical signals + auto-reject, idempotent"
```

---

## Task 18: FastAPI app skeleton

**Files:**
- Create: `Z:\travel_post\travelcull\server\__init__.py` (empty)
- Create: `Z:\travel_post\travelcull\server\app.py`
- Test: `Z:\travel_post\tests\test_server_app.py`

- [ ] **Step 1: Write the failing test**

`tests/test_server_app.py`:
```python
from httpx import AsyncClient

from travelcull.config import get_folder_config
from travelcull.db import init_db
from travelcull.server.app import build_app


async def test_health_endpoint(tmp_path):
    cfg = get_folder_config(tmp_path)
    init_db(cfg)
    app = build_app(cfg)
    async with AsyncClient(app=app, base_url="http://test") as client:
        r = await client.get("/api/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_server_app.py -v`
Expected: ImportError.

- [ ] **Step 3: Write app builder**

`travelcull/server/app.py`:
```python
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from travelcull.config import FolderConfig

from .routes import register_routes


def build_app(cfg: FolderConfig) -> FastAPI:
    app = FastAPI(title="travelcull", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    register_routes(app, cfg)
    return app
```

- [ ] **Step 4: Create empty routes module**

`travelcull/server/routes.py`:
```python
from fastapi import FastAPI

from travelcull.config import FolderConfig


def register_routes(app: FastAPI, cfg: FolderConfig) -> None:
    """Filled in by later tasks."""
    pass
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_server_app.py -v`
Expected: 1 passed.

- [ ] **Step 6: Commit**

```powershell
git add travelcull/server tests/test_server_app.py
git commit -m "feat(server): FastAPI app skeleton with CORS + health endpoint"
```

---

## Task 19: Photo list + thumbnail endpoints

**Files:**
- Modify: `Z:\travel_post\travelcull\server\routes.py`
- Test: `Z:\travel_post\tests\test_server_routes.py`

- [ ] **Step 1: Write the failing test**

`tests/test_server_routes.py`:
```python
from httpx import AsyncClient

from travelcull.config import get_folder_config
from travelcull.db import init_db
from travelcull.indexer.orchestrator import index_folder
from travelcull.pipeline import run_classical_stage
from travelcull.server.app import build_app


async def test_list_photos_returns_indexed_files(populated_folder):
    cfg = get_folder_config(populated_folder)
    init_db(cfg)
    index_folder(cfg)
    run_classical_stage(cfg)

    app = build_app(cfg)
    async with AsyncClient(app=app, base_url="http://test") as client:
        r = await client.get("/api/photos")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] >= 2
        assert "items" in body
        assert all("sha256" in item for item in body["items"])
        assert all("auto_reject" in item for item in body["items"])


async def test_get_thumbnail_returns_image(populated_folder):
    cfg = get_folder_config(populated_folder)
    init_db(cfg)
    index_folder(cfg)

    app = build_app(cfg)
    async with AsyncClient(app=app, base_url="http://test") as client:
        listing = (await client.get("/api/photos")).json()
        sha = listing["items"][0]["sha256"]
        r = await client.get(f"/api/thumb/{sha}")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/jpeg"
        assert len(r.content) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_server_routes.py -v`
Expected: 404 on the endpoints.

- [ ] **Step 3: Implement routes**

Replace `travelcull/server/routes.py`:
```python
from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select

from travelcull.config import FolderConfig
from travelcull.db import session_scope
from travelcull.db.models import ClassicalScore, Photo


class PhotoOut(BaseModel):
    id: int
    sha256: str
    path: str
    format: Optional[str]
    width: Optional[int]
    height: Optional[int]
    taken_at: Optional[str]
    thumb_url: str
    preview_url: str
    blur: Optional[float] = None
    exposure: Optional[float] = None
    faces_count: Optional[int] = None
    auto_reject: Optional[bool] = None
    reject_reason: Optional[str] = None


class PhotoList(BaseModel):
    total: int
    items: list[PhotoOut]


def register_routes(app: FastAPI, cfg: FolderConfig) -> None:
    @app.get("/api/photos", response_model=PhotoList)
    def list_photos(
        offset: int = Query(0, ge=0), limit: int = Query(200, le=2000), rejected: Optional[bool] = None
    ):
        with session_scope(cfg) as s:
            stmt = select(Photo, ClassicalScore).join(
                ClassicalScore, Photo.id == ClassicalScore.photo_id, isouter=True
            )
            if rejected is True:
                stmt = stmt.where(ClassicalScore.auto_reject.is_(True))
            elif rejected is False:
                stmt = stmt.where((ClassicalScore.auto_reject.is_(False)) | (ClassicalScore.photo_id.is_(None)))
            total = s.scalar(select(Photo.id).select_from(Photo)) or 0
            total = s.query(Photo).count()
            rows = s.execute(stmt.offset(offset).limit(limit)).all()

            items = []
            for photo, score in rows:
                items.append(
                    PhotoOut(
                        id=photo.id,
                        sha256=photo.sha256,
                        path=photo.path,
                        format=photo.format,
                        width=photo.width,
                        height=photo.height,
                        taken_at=photo.taken_at.isoformat() if photo.taken_at else None,
                        thumb_url=f"/api/thumb/{photo.sha256}",
                        preview_url=f"/api/preview/{photo.sha256}",
                        blur=score.blur if score else None,
                        exposure=score.exposure if score else None,
                        faces_count=score.faces_count if score else None,
                        auto_reject=score.auto_reject if score else None,
                        reject_reason=score.reject_reason if score else None,
                    )
                )
        return PhotoList(total=total, items=items)

    @app.get("/api/thumb/{sha256}")
    def thumb(sha256: str):
        return _serve_image_for(cfg, sha256, kind="thumb")

    @app.get("/api/preview/{sha256}")
    def preview(sha256: str):
        return _serve_image_for(cfg, sha256, kind="preview")


def _serve_image_for(cfg: FolderConfig, sha256: str, kind: str):
    parent = cfg.thumbs_dir if kind == "thumb" else cfg.previews_dir
    path = parent / f"{sha256}.jpg"
    if not path.exists():
        raise HTTPException(404, detail=f"{kind} not found")
    return FileResponse(path, media_type="image/jpeg")
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_server_routes.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```powershell
git add travelcull/server/routes.py tests/test_server_routes.py
git commit -m "feat(server): /api/photos list + /api/thumb/{sha} + /api/preview/{sha}"
```

---

## Task 20: WebSocket progress channel

**Files:**
- Create: `Z:\travel_post\travelcull\server\ws.py`
- Modify: `Z:\travel_post\travelcull\server\app.py` to wire it in
- Test: `Z:\travel_post\tests\test_server_ws.py`

- [ ] **Step 1: Write the failing test**

`tests/test_server_ws.py`:
```python
from httpx import AsyncClient

from travelcull.config import get_folder_config
from travelcull.db import init_db
from travelcull.server.app import build_app
from travelcull.server.ws import progress_bus


async def test_progress_bus_publishes(tmp_path):
    bus = progress_bus()
    received = []

    async def consume():
        async for msg in bus.subscribe():
            received.append(msg)
            break

    import asyncio

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    await bus.publish({"stage": "index", "current": 1, "total": 10})
    await asyncio.wait_for(task, timeout=1.0)
    assert received[0]["stage"] == "index"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_server_ws.py -v`
Expected: ImportError.

- [ ] **Step 3: Write the WS bus**

`travelcull/server/ws.py`:
```python
from __future__ import annotations

import asyncio
from typing import AsyncIterator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

_BUS: "ProgressBus | None" = None


class ProgressBus:
    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue] = []

    async def publish(self, msg: dict) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    async def subscribe(self) -> AsyncIterator[dict]:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.append(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._subscribers.remove(q)


def progress_bus() -> ProgressBus:
    global _BUS
    if _BUS is None:
        _BUS = ProgressBus()
    return _BUS


def register_ws(app: FastAPI) -> None:
    @app.websocket("/ws/progress")
    async def ws_progress(websocket: WebSocket) -> None:
        await websocket.accept()
        bus = progress_bus()
        try:
            async for msg in bus.subscribe():
                await websocket.send_json(msg)
        except WebSocketDisconnect:
            return
```

- [ ] **Step 4: Wire into app**

Modify `travelcull/server/app.py` — add at the end of `build_app` before return:
```python
    from .ws import register_ws

    register_ws(app)
    return app
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_server_ws.py -v`
Expected: 1 passed.

- [ ] **Step 6: Commit**

```powershell
git add travelcull/server/ws.py travelcull/server/app.py tests/test_server_ws.py
git commit -m "feat(server): WebSocket /ws/progress with in-process pub-sub bus"
```

---

## Task 21: CLI commands (index, serve, doctor)

**Files:**
- Create: `Z:\travel_post\travelcull\cli.py`
- Create: `Z:\travel_post\travelcull\__main__.py`
- Test: `Z:\travel_post\tests\test_cli.py`

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:
```python
from click.testing import CliRunner

from travelcull.cli import main


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v`
Expected: ImportError.

- [ ] **Step 3: Write CLI**

`travelcull/cli.py`:
```python
from __future__ import annotations

import webbrowser
from pathlib import Path

import click

from travelcull.config import get_folder_config
from travelcull.db import init_db
from travelcull.gpu import detect_capabilities
from travelcull.indexer.orchestrator import index_folder
from travelcull.pipeline import run_classical_stage


@click.group()
def main():
    """travelcull — local AI-assisted travel photo & video culling."""


@main.command()
@click.argument("folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--pass", "pass_", type=click.Choice(["all", "index", "classical"]), default="all")
def index(folder: Path, pass_: str):
    """Index a folder and run available pipeline stages."""
    cfg = get_folder_config(folder)
    init_db(cfg)

    if pass_ in ("all", "index"):
        added = index_folder(
            cfg, on_progress=lambda i, t, name: click.echo(f"[{i}/{t}] {name}", err=True)
        )
        click.echo(f"indexed: {added} new files")

    if pass_ in ("all", "classical"):
        processed = run_classical_stage(
            cfg, on_progress=lambda i, t, name: click.echo(f"[{i}/{t}] classical: {name}", err=True)
        )
        click.echo(f"classical: {processed} processed")


@main.command()
@click.argument("folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--port", default=5173, type=int)
@click.option("--no-browser", is_flag=True)
def serve(folder: Path, port: int, no_browser: bool):
    """Serve the web UI for an indexed folder."""
    import uvicorn

    cfg = get_folder_config(folder)
    init_db(cfg)

    from travelcull.server.app import build_app

    app = build_app(cfg)
    url = f"http://127.0.0.1:{port}"
    if not no_browser:
        webbrowser.open(url)
    click.echo(f"travelcull serving at {url}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


@main.command()
def doctor():
    """Report CUDA / NVDEC / nvImageCodec / cv2.cuda capabilities."""
    caps = detect_capabilities()
    click.echo(f"CUDA              : {'yes' if caps.cuda_available else 'no'} ({caps.device_name})")
    click.echo(f"CUDA capability   : {caps.cuda_capability}")
    click.echo(f"VRAM              : {caps.vram_total_mb} MB")
    click.echo(f"NVDEC (torchcodec): {'yes' if caps.nvdec_available else 'no'}")
    click.echo(f"nvImageCodec      : {'yes' if caps.nvimgcodec_available else 'no'}")
    click.echo(f"cv2.cuda          : {'yes' if caps.cv2_cuda_available else 'no'}")
```

- [ ] **Step 4: Write __main__**

`travelcull/__main__.py`:
```python
from travelcull.cli import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_cli.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```powershell
git add travelcull/cli.py travelcull/__main__.py tests/test_cli.py
git commit -m "feat(cli): index, serve, doctor commands via click"
```

---

## Task 22: Frontend scaffold (Vite + React + TS)

**Files:**
- Create: `Z:\travel_post\frontend\package.json`
- Create: `Z:\travel_post\frontend\tsconfig.json`
- Create: `Z:\travel_post\frontend\vite.config.ts`
- Create: `Z:\travel_post\frontend\index.html`
- Create: `Z:\travel_post\frontend\src\main.tsx`
- Create: `Z:\travel_post\frontend\src\App.tsx`
- Create: `Z:\travel_post\frontend\src\styles.css` (port from design/)

- [ ] **Step 1: Create package.json**

`frontend/package.json`:
```json
{
  "name": "travelcull-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.23.0"
  },
  "devDependencies": {
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.0",
    "typescript": "~5.4.0",
    "vite": "^5.2.0"
  }
}
```

- [ ] **Step 2: Create vite.config.ts**

`frontend/vite.config.ts`:
```ts
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:5173",
      "/ws":  { target: "ws://127.0.0.1:5173", ws: true }
    }
  }
});
```

(The proxy points at 5173 because FastAPI and Vite share the port — actually no, they can't. Vite runs at 5173 in dev; FastAPI runs at a different port. We'll override the FastAPI port at serve-time in dev. Document this in README and use `8000` for FastAPI dev. Update the proxy targets to `127.0.0.1:8000`.)

Replace the proxy block in `vite.config.ts`:
```ts
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/ws":  { target: "ws://127.0.0.1:8000", ws: true }
    }
```

- [ ] **Step 3: Create tsconfig.json**

`frontend/tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "jsx": "react-jsx",
    "isolatedModules": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "allowImportingTsExtensions": true,
    "noEmit": true
  },
  "include": ["src"]
}
```

- [ ] **Step 4: Create index.html**

`frontend/index.html`:
```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>travelcull</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Google+Sans+Display:wght@400;500;700&family=Google+Sans+Code:wght@400;500&family=Roboto+Flex:opsz,wght@8..144,300;8..144,400;8..144,500;8..144,600&display=swap" rel="stylesheet" />
</head>
<body>
  <div id="root"></div>
  <script type="module" src="/src/main.tsx"></script>
</body>
</html>
```

- [ ] **Step 5: Port styles.css from design/**

Copy `Z:\travel_post\design\styles.css` to `Z:\travel_post\frontend\src\styles.css` verbatim.

```powershell
Copy-Item Z:\travel_post\design\styles.css Z:\travel_post\frontend\src\styles.css
```

- [ ] **Step 6: Create main.tsx and App.tsx**

`frontend/src/main.tsx`:
```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import "./styles.css";
import App from "./App";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode><App /></React.StrictMode>
);
```

`frontend/src/App.tsx` — minimal placeholder that we expand in Task 24:
```tsx
export default function App() {
  return <div style={{ padding: 24, fontFamily: "var(--font-display)" }}>travelcull</div>;
}
```

- [ ] **Step 7: Install deps and verify dev server starts**

```powershell
cd frontend
npm install
npm run dev
```
Expected: Vite reports "Local: http://localhost:5173/" and the page renders "travelcull".

- [ ] **Step 8: Commit**

```powershell
git add frontend/package.json frontend/tsconfig.json frontend/vite.config.ts frontend/index.html frontend/src
git commit -m "chore(frontend): Vite + React + TS scaffold with ported styles"
```

---

## Task 23: API client + types

**Files:**
- Create: `Z:\travel_post\frontend\src\api\types.ts`
- Create: `Z:\travel_post\frontend\src\api\client.ts`

- [ ] **Step 1: Create types**

`frontend/src/api/types.ts`:
```ts
export interface Photo {
  id: number;
  sha256: string;
  path: string;
  format: string | null;
  width: number | null;
  height: number | null;
  taken_at: string | null;
  thumb_url: string;
  preview_url: string;
  blur: number | null;
  exposure: number | null;
  faces_count: number | null;
  auto_reject: boolean | null;
  reject_reason: string | null;
}

export interface PhotoList {
  total: number;
  items: Photo[];
}

export interface ProgressMsg {
  stage: "index" | "classical" | "embed" | "vl";
  current: number;
  total: number;
  message?: string;
}
```

- [ ] **Step 2: Create client**

`frontend/src/api/client.ts`:
```ts
import type { PhotoList } from "./types";

const BASE = "/api";

export async function listPhotos(opts: { offset?: number; limit?: number; rejected?: boolean } = {}): Promise<PhotoList> {
  const params = new URLSearchParams();
  if (opts.offset !== undefined) params.set("offset", String(opts.offset));
  if (opts.limit !== undefined) params.set("limit", String(opts.limit));
  if (opts.rejected !== undefined) params.set("rejected", String(opts.rejected));
  const res = await fetch(`${BASE}/photos?${params}`);
  if (!res.ok) throw new Error(`listPhotos ${res.status}`);
  return res.json();
}

export function progressSocket(onMessage: (m: any) => void): WebSocket {
  const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/progress`;
  const ws = new WebSocket(url);
  ws.addEventListener("message", e => onMessage(JSON.parse(e.data)));
  return ws;
}
```

- [ ] **Step 3: Verify types compile**

Run: `cd frontend && npx tsc --noEmit`
Expected: clean exit.

- [ ] **Step 4: Commit**

```powershell
git add frontend/src/api
git commit -m "feat(frontend): typed API client + WebSocket progress helper"
```

---

## Task 24: Wire BurstCull view to real data

**Files:**
- Create: `Z:\travel_post\frontend\src\views\BurstCull.tsx`
- Create: `Z:\travel_post\frontend\src\components\Rail.tsx`
- Create: `Z:\travel_post\frontend\src\components\Topbar.tsx`
- Create: `Z:\travel_post\frontend\src\components\StatusRow.tsx`
- Create: `Z:\travel_post\frontend\src\components\KbdFooter.tsx`
- Modify: `Z:\travel_post\frontend\src\App.tsx`

- [ ] **Step 1: Create Rail component**

`frontend/src/components/Rail.tsx`:
```tsx
interface Props { active: "cull" | "clusters" | "stories"; onTheme: () => void; }

export function Rail({ active, onTheme }: Props) {
  return (
    <nav className="rail" aria-label="Primary">
      <div className="rail-brand"><span className="dot" />tc</div>
      <RailItem label="Cull"     active={active === "cull"}     href="/" icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"><path d="M3 6h18M3 12h18M3 18h18" /></svg>} />
      <RailItem label="Clusters" active={active === "clusters"} href="/clusters" icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>} />
      <RailItem label="Stories"  active={active === "stories"}  href="/stories" icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"><path d="M4 4h12v16l-6-3-6 3z"/><path d="M16 4h4v16l-2-1"/></svg>} />
      <div className="rail-spacer" />
      <button className="rail-item" onClick={onTheme}>
        <span className="icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg></span>
        Theme
      </button>
    </nav>
  );
}

function RailItem({ label, active, href, icon }: { label: string; active: boolean; href: string; icon: JSX.Element }) {
  return (
    <a className={`rail-item ${active ? "is-active" : ""}`} href={href}>
      <span className="icon">{icon}</span>
      {label}
    </a>
  );
}
```

- [ ] **Step 2: Create Topbar + StatusRow + KbdFooter** (concise versions)

`frontend/src/components/Topbar.tsx`:
```tsx
interface Props { folder: string; context: string; }

export function Topbar({ folder, context }: Props) {
  return (
    <header className="topbar">
      <div className="topbar-folder">
        <span className="crumb-folder">{folder}</span>
        <span className="crumb-sep">/</span>
        <span className="crumb-context">{context}</span>
      </div>
      <div className="topbar-grow" />
    </header>
  );
}
```

`frontend/src/components/StatusRow.tsx`:
```tsx
interface Props { pos: string; keepers: string; details: string; activeTab: "cull" | "clusters" | "stories"; }

export function StatusRow({ pos, keepers, details, activeTab }: Props) {
  return (
    <div className="status-row">
      <span className="pos">{pos}</span>
      <span className="div">·</span>
      <span className="keepers"><span className="keepers-dot" />{keepers}</span>
      <span className="div">·</span>
      <span>{details}</span>
      <div className="status-row-spacer" />
      <div className="view-tabs" role="tablist">
        <button className={`view-tab ${activeTab === "cull" ? "is-active" : ""}`}>Burst cull</button>
        <button className={`view-tab ${activeTab === "clusters" ? "is-active" : ""}`}>Clusters</button>
        <button className={`view-tab ${activeTab === "stories" ? "is-active" : ""}`}>Stories</button>
      </div>
    </div>
  );
}
```

`frontend/src/components/KbdFooter.tsx`:
```tsx
export function KbdFooter() {
  return (
    <footer className="kbd-footer">
      <span className="kbd-action is-danger"><span className="kbd">J</span> reject</span>
      <span className="kbd-action is-primary"><span className="kbd">K</span> keep gold</span>
      <span className="kbd-action is-positive"><span className="kbd">L</span> keep gold + silver</span>
      <span className="kbd-action"><span className="kbd">;</span> keep all</span>
      <span className="kbd-action"><span className="kbd">1</span>–<span className="kbd">8</span> promote</span>
      <div className="kbd-footer-spacer" />
      <span className="kbd-help">M1 — UI shell, no ML scoring yet</span>
    </footer>
  );
}
```

- [ ] **Step 3: Create BurstCull view**

`frontend/src/views/BurstCull.tsx`:
```tsx
import { useEffect, useState } from "react";
import { listPhotos } from "../api/client";
import type { Photo } from "../api/types";
import { Rail } from "../components/Rail";
import { Topbar } from "../components/Topbar";
import { StatusRow } from "../components/StatusRow";
import { KbdFooter } from "../components/KbdFooter";

export default function BurstCull() {
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [total, setTotal] = useState(0);
  const [idx, setIdx] = useState(0);
  const [folder, setFolder] = useState("");

  useEffect(() => {
    listPhotos({ limit: 500 }).then(r => {
      setPhotos(r.items);
      setTotal(r.total);
      if (r.items[0]) setFolder(extractFolderName(r.items[0].path));
    });
  }, []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "j" || e.key === "J") setIdx(i => Math.min(i + 1, photos.length - 1));
      if (e.key === "k" || e.key === "K") setIdx(i => Math.min(i + 1, photos.length - 1));
      if (e.key === "ArrowLeft") setIdx(i => Math.max(i - 1, 0));
      if (e.key === "ArrowRight") setIdx(i => Math.min(i + 1, photos.length - 1));
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [photos.length]);

  const toggleTheme = () => {
    const cur = document.documentElement.getAttribute("data-theme");
    if (cur === "dark") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", "dark");
  };

  const current = photos[idx];
  const rejected = photos.filter(p => p.auto_reject).length;
  const kept = photos.filter(p => p.auto_reject === false).length;

  return (
    <div className="app">
      <Rail active="cull" onTheme={toggleTheme} />
      <div className="workspace">
        <Topbar folder={folder || "folder"} context={`photo ${idx + 1} of ${photos.length}`} />
        <StatusRow
          pos={`${idx + 1} / ${photos.length}`}
          keepers={`${kept} candidate keepers`}
          details={`${total} total · ${rejected} auto-rejected`}
          activeTab="cull"
        />
        <section className="cull-stage">
          <div className="gold-frame">
            {current && <img src={current.preview_url} alt="" />}
            {current?.auto_reject && (
              <div className="gold-overlay">
                <div className="filename">{current.path}</div>
                <div className="stamp" style={{ background: "var(--md-error-container)", color: "var(--md-error)" }}>
                  auto-reject · {current.reject_reason}
                </div>
              </div>
            )}
          </div>
          <aside className="burst-strip">
            {photos.slice(Math.max(0, idx - 3), idx + 5).map((p, i) => (
              <button
                key={p.sha256}
                className={`burst-thumb ${p === current ? "is-gold" : ""}`}
                onClick={() => setIdx(photos.indexOf(p))}
              >
                <img src={p.thumb_url} alt="" />
                <span className="badge">{Math.max(0, idx - 3) + i + 1}</span>
              </button>
            ))}
          </aside>
        </section>
        <section className="meta-row">
          <div className="scores-card">
            <div className="scores-head"><span className="label">Stage 1 · classical</span></div>
            <ClassicalScores p={current} />
          </div>
        </section>
        <KbdFooter />
      </div>
    </div>
  );
}

function ClassicalScores({ p }: { p?: Photo }) {
  if (!p) return null;
  const row = (name: string, val: number | null, axisClass: string, formatter?: (v: number) => string) => (
    <div className={`axis-row ${axisClass}`}>
      <span className="name">{name}</span>
      <span className="bar">
        <span className="fill" style={{ width: val == null ? "0%" : `${Math.min(100, Math.max(0, (val / 1000) * 100))}%` }} />
      </span>
      <span className="num">{val == null ? "—" : (formatter ? formatter(val) : val.toFixed(1))}</span>
    </div>
  );
  return (
    <>
      {row("blur var",  p.blur,     "axis-sharpness")}
      {row("exposure",  p.exposure, "axis-lighting", v => v.toFixed(2))}
      <div className="axis-row axis-subject">
        <span className="name">faces</span>
        <span className="bar"><span className="fill" style={{ width: `${Math.min(100, (p.faces_count || 0) * 25)}%` }} /></span>
        <span className="num">{p.faces_count ?? "—"}</span>
      </div>
    </>
  );
}

function extractFolderName(absPath: string): string {
  const parts = absPath.replace(/\\/g, "/").split("/");
  return parts[parts.length - 2] || "folder";
}
```

- [ ] **Step 4: Update App.tsx**

`frontend/src/App.tsx`:
```tsx
import BurstCull from "./views/BurstCull";

export default function App() {
  return <BurstCull />;
}
```

- [ ] **Step 5: Run dev server and visually verify**

In one shell:
```powershell
travelcull index Z:\Ladakh\Photos
travelcull serve Z:\Ladakh\Photos --port 8000 --no-browser
```
In another:
```powershell
cd frontend
npm run dev
```
Open `http://localhost:5173`. Expected: see real Ladakh photos in the gold frame and burst strip, J/K cycles through them, auto-rejected photos show the red badge.

- [ ] **Step 6: Commit**

```powershell
git add frontend/src
git commit -m "feat(frontend): BurstCull view wired to real /api/photos data"
```

---

## Task 25: Indexing progress in the UI

**Files:**
- Modify: `Z:\travel_post\travelcull\cli.py` — emit progress to the bus
- Modify: `Z:\travel_post\travelcull\server\app.py` — kick off indexer in background on startup
- Modify: `Z:\travel_post\frontend\src\components\StatusRow.tsx` — show progress chip

- [ ] **Step 1: Modify cli.py to publish to ProgressBus**

Modify the `index` command in `cli.py` so that when running standalone it ALSO publishes to the bus (for the case where `serve` is running concurrently — same DB, separate process). For M1 simplicity: we just print to stderr. The bus path is exercised from inside `serve` (Step 2).

No code change needed for Step 1 — note this in a code comment in `cli.py`:
```python
# Background indexing from inside `serve` publishes progress on ProgressBus.
# Standalone `index` only prints to stderr; no shared-process bus.
```

- [ ] **Step 2: Add background indexing on `serve`**

Modify `travelcull/server/app.py`:
```python
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from travelcull.config import FolderConfig
from travelcull.indexer.orchestrator import index_folder
from travelcull.pipeline import run_classical_stage

from .routes import register_routes
from .ws import progress_bus, register_ws


def build_app(cfg: FolderConfig) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        bus = progress_bus()
        loop = asyncio.get_event_loop()

        def index_progress(i, total, name):
            asyncio.run_coroutine_threadsafe(
                bus.publish({"stage": "index", "current": i, "total": total, "message": name}), loop
            )

        def classical_progress(i, total, name):
            asyncio.run_coroutine_threadsafe(
                bus.publish({"stage": "classical", "current": i, "total": total, "message": name}), loop
            )

        async def background():
            await asyncio.to_thread(index_folder, cfg, index_progress)
            await asyncio.to_thread(run_classical_stage, cfg, classical_progress)
            await bus.publish({"stage": "done", "current": 1, "total": 1})

        task = asyncio.create_task(background())
        yield
        task.cancel()

    app = FastAPI(title="travelcull", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    register_routes(app, cfg)
    register_ws(app)
    return app
```

- [ ] **Step 3: Update StatusRow to subscribe to /ws/progress**

Add to `frontend/src/components/StatusRow.tsx` — turn it into a stateful component:
```tsx
import { useEffect, useState } from "react";
import { progressSocket } from "../api/client";

interface Props { pos: string; keepers: string; details: string; activeTab: "cull" | "clusters" | "stories"; }

export function StatusRow({ pos, keepers, details, activeTab }: Props) {
  const [progress, setProgress] = useState<{ stage: string; current: number; total: number } | null>(null);

  useEffect(() => {
    const ws = progressSocket(setProgress);
    return () => ws.close();
  }, []);

  return (
    <div className="status-row">
      <span className="pos">{pos}</span>
      <span className="div">·</span>
      <span className="keepers"><span className="keepers-dot" />{keepers}</span>
      <span className="div">·</span>
      <span>{details}</span>
      {progress && progress.stage !== "done" && (
        <>
          <span className="div">·</span>
          <span className="scoring-chip">
            <span className="ring" />{progress.stage} {progress.current}/{progress.total}
          </span>
        </>
      )}
      <div className="status-row-spacer" />
      <div className="view-tabs" role="tablist">
        <button className={`view-tab ${activeTab === "cull" ? "is-active" : ""}`}>Burst cull</button>
        <button className={`view-tab ${activeTab === "clusters" ? "is-active" : ""}`}>Clusters</button>
        <button className={`view-tab ${activeTab === "stories" ? "is-active" : ""}`}>Stories</button>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Verify end-to-end**

```powershell
Remove-Item Z:\Ladakh\Photos\.travelcull -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item Z:\Ladakh\Photos\.travelcull.db -Force -ErrorAction SilentlyContinue
travelcull serve Z:\Ladakh\Photos --port 8000 --no-browser
# in another shell:
cd frontend && npm run dev
```
Open `http://localhost:5173`. Expected: progress chip ticks up as indexing runs; photos start appearing as soon as the first batch is indexed; final state shows all 1065 photos with classical scores.

- [ ] **Step 5: Commit**

```powershell
git add travelcull/server/app.py travelcull/cli.py frontend/src/components/StatusRow.tsx
git commit -m "feat: background index+classical with /ws/progress live updates"
```

---

## Task 26: Integration smoke test against Ladakh data

**Files:**
- Create: `Z:\travel_post\tests\test_integration_ladakh.py`

This test is **gated by env var** so CI doesn't need 1065 real photos. Manual run only.

- [ ] **Step 1: Write the test**

`tests/test_integration_ladakh.py`:
```python
import os
import time
from pathlib import Path

import pytest

from travelcull.config import get_folder_config
from travelcull.db import init_db, session_scope
from travelcull.db.models import ClassicalScore, Photo, Video
from travelcull.indexer.orchestrator import index_folder
from travelcull.pipeline import run_classical_stage

LADAKH = os.environ.get("TRAVELCULL_LADAKH_PATH", r"Z:\Ladakh\Photos")


@pytest.mark.skipif(not Path(LADAKH).exists(), reason="Ladakh fixture missing")
def test_index_ladakh_end_to_end():
    folder = Path(LADAKH)
    cfg = get_folder_config(folder)

    # Reset state for a clean run
    db = cfg.db_path
    if db.exists():
        db.unlink()
    state = cfg.state_dir
    if state.exists():
        import shutil
        shutil.rmtree(state)

    init_db(cfg)
    t0 = time.time()
    added = index_folder(cfg)
    t1 = time.time()
    print(f"\nINDEX: {added} files in {t1 - t0:.1f}s ({added / (t1 - t0):.1f} files/sec)")

    t2 = time.time()
    run_classical_stage(cfg)
    t3 = time.time()

    with session_scope(cfg) as s:
        n_photos = s.query(Photo).count()
        n_videos = s.query(Video).count()
        n_scored = s.query(ClassicalScore).count()
        n_rejected = s.query(ClassicalScore).filter(ClassicalScore.auto_reject.is_(True)).count()

    print(f"CLASSICAL: {n_scored} photos scored in {t3 - t2:.1f}s ({n_scored / (t3 - t2):.1f} photos/sec)")
    print(f"REJECTED: {n_rejected} / {n_photos} ({100 * n_rejected / max(n_photos, 1):.1f}%)")

    assert n_photos + n_videos > 900  # expect ~1065 minus any unreadable
    assert n_scored == n_photos
    assert 0.0 <= n_rejected / max(n_photos, 1) <= 0.2  # sanity bound on reject rate
```

- [ ] **Step 2: Run the test**

```powershell
$env:TRAVELCULL_LADAKH_PATH = "Z:\Ladakh\Photos"
pytest tests/test_integration_ladakh.py -v -s
```
Expected: passes, prints timing + reject rate. Reject rate should be 0–20% on a typical phone roll. If it's outside this range, investigate the auto-reject thresholds before moving to M2.

- [ ] **Step 3: Commit**

```powershell
git add tests/test_integration_ladakh.py
git commit -m "test: end-to-end indexing + classical against Ladakh photos (env-gated)"
```

---

## Task 27: README quickstart

**Files:**
- Create: `Z:\travel_post\README.md`

- [ ] **Step 1: Write README**

`README.md`:
````markdown
# travelcull

Local AI-assisted travel photo and video culling. GPU-first, runs entirely on your machine.

> **Status: M1 (UI shell + classical signals).** ML scoring, burst clustering, narrative ordering, video pipeline, and darktable integration land in M2 and M3.

## Requirements

- Windows 11
- NVIDIA GPU with ≥8 GB VRAM (RTX 3060 or newer recommended)
- CUDA 12.x driver
- Python 3.11+
- Node.js 18+ (for the dev UI)
- ffmpeg on PATH

## Install

```powershell
git clone <repo>
cd travel_post
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev,gpu]
cd frontend
npm install
cd ..
```

## Quickstart

Index a folder and run the classical stage:
```powershell
travelcull index Z:\Ladakh\Photos
```

Start the web UI:
```powershell
travelcull serve Z:\Ladakh\Photos --port 8000
# in another shell:
cd frontend
npm run dev
```
Open http://localhost:5173.

## Diagnostics

```powershell
travelcull doctor
```
Reports CUDA, NVDEC, nvImageCodec, and cv2.cuda availability.

## Tests

```powershell
pytest -ra
```
End-to-end Ladakh test (requires that folder on disk):
```powershell
$env:TRAVELCULL_LADAKH_PATH = "Z:\Ladakh\Photos"
pytest tests/test_integration_ladakh.py -v -s
```

## Architecture

See `docs/superpowers/specs/2026-05-23-travelcull-design.md`.
````

- [ ] **Step 2: Commit**

```powershell
git add README.md
git commit -m "docs: quickstart README"
```

---

## Self-review

**Spec coverage** (vs `2026-05-23-travelcull-design.md`):

- ✅ Three-process architecture (Indexer, Worker, Server) — Tasks 5–25
- ✅ SQLite sidecar at folder root — Task 3
- ✅ HEIC, JPEG, RAW, video decode — Tasks 7–10, GPU paths included
- ✅ Classical signals (Stage 1) — Tasks 13–17
- ✅ FastAPI + REST + WS — Tasks 18–20, 25
- ✅ React UI with Material 3 theme — Tasks 22–25
- ✅ CLI with multi-pass + doctor — Task 21
- ✅ Validation against Ladakh data — Task 26
- ⏸ Stages 2–6 ML pipeline — explicitly deferred to M2
- ⏸ Video pipeline beyond first-frame — explicitly deferred to M3
- ⏸ XMP sidecars, story ordering, clusters view — deferred to M3 / M2

**Placeholder scan:** None present. All steps have full code.

**Type consistency:**
- `Photo`, `Video`, `ClassicalScore`, `PipelineState` referenced consistently across DB tests, orchestrator, pipeline, and routes.
- `FileKind` enum used in walker, decode facade, orchestrator.
- `FolderConfig.thumbs_dir` / `previews_dir` used consistently across orchestrator, preview writer, and route file serving.
- `ExifData` fields match what `_ingest_photo` reads.
- API response model `PhotoOut` matches the frontend TS `Photo` interface field-by-field.

**Ambiguity check:**
- The Vite dev proxy notes that FastAPI runs on port 8000 in dev. README documents this. Production build (M3+) will serve the React static files from FastAPI directly.
- One thing to flag for the executor: the `index_folder` orchestrator opens a session per file write. For 1065 files this is ~1000 SQLite commits. If perf is poor on the Ladakh run, batch the inserts. Tagged in Task 12 comments.

---

## Execution Handoff

Plan complete and saved to `Z:\travel_post\docs\superpowers\plans\2026-05-23-travelcull-m1-indexer-and-ui-shell.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Best for a plan this large because each task is bounded and you can interject when something looks wrong.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints. Faster if everything goes smoothly; harder to recover if a task gets stuck.

Which approach?
