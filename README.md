<div align="center">

<img src="packaging/assets/selects-logo.svg" width="92" alt="selects logo" />

# Selects

**Cull thousands of travel photos down to your keepers — locally, privately, on your own machine.**

Point it at a folder of photos and videos. It indexes, scores, clusters, and groups them into
day-by-day stories, surfaces the best shots, and gets out of your way. Nothing is uploaded anywhere.
(The Map view is an exception: it loads OpenStreetMap tiles from the internet, and reverse
geocoding of GPS coordinates into place names is optional and can be disabled.)

[![PyPI](https://img.shields.io/pypi/v/selects?color=1f6feb)](https://pypi.org/project/selects/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![CI](https://github.com/bihanikeshav/selects/actions/workflows/ci.yml/badge.svg)](https://github.com/bihanikeshav/selects/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

</div>

---

## Why Selects

I built Selects for the post-trip mess: 3,000 photos, five versions of the same mountain, ten blurry food shots, and one frame where everyone actually has their eyes open.

The app is meant to do the boring first pass locally, then leave the final taste call to you.

| | |
|---|---|
| **Private by design** | All inference runs locally. No cloud, no account, no upload. |
| **More than EXIF sorting** | Semantic search, face grouping, aesthetic scoring, eyes-open burst picking. |
| **Photos and video** | Frame scoring, dead-footage detection, highlight segments. |
| **Fast to cull** | Keyboard-first review, side-by-side compare, learns your taste. |
| **Yours to extend** | Clean FastAPI backend + React UI + a scriptable CLI. |

The only network call is an optional place-name lookup for geotagged shots.

## Features

| Feature | What it does |
|---|---|
| Discovery search | Natural-language + tag search over on-device SigLIP embeddings |
| Auto tagging | Zero-shot + RAM++ open-vocabulary labels |
| People | ArcFace embeddings clustered into named "Person" identities |
| Face-aware culling | Eyes-open / head-pose scoring picks the best frame in a burst |
| Stories | GPS + time clustering into day-by-day, place-by-place trips |
| Aesthetic curation | CLIP-IQA scoring with percentile "best-of" gating |
| Duplicate finder | Exact + near-duplicate report with reclaimable-storage summary |
| Keyboard culling | Arrow-key review, undo, 100% zoom, synced-zoom compare |
| Taste learning | A local model that nudges scoring toward your keep/reject history |
| Export | Copy/zip keepers or write XMP star ratings to Lightroom/darktable |
| Trip recap | A self-contained shareable HTML keepsake per trip |
| Video culling | Frame sampling, quality scoring, dead-footage flags, highlights |
| Watch folder | Point it at your camera dump; new files index automatically |

## Install

**One line (recommended)** — installs via [uv](https://docs.astral.sh/uv/), which brings its own
Python, so there is nothing to install first and no downloaded app for macOS Gatekeeper or Windows
SmartScreen to flag. It opens the web UI; AI models download from the app's first-run setup screen.

```bash
# macOS / Linux
curl -LsSf https://bihanikeshav.github.io/selects/install.sh | sh

# Windows (PowerShell)
irm https://bihanikeshav.github.io/selects/install.ps1 | iex
```

**Desktop bundle** — prefer a self-contained download? Grab the bundle for your OS from
[Releases](https://github.com/bihanikeshav/selects/releases) and run it. No Python required; it
downloads its AI models on first launch. (Unsigned, so the OS shows a one-time "unverified" prompt —
right-click → Open on macOS, or More info → Run anyway on Windows.)

**Via pip / uv** (Python 3.11+):

```bash
uv tool install "selects[ml]"  # or: pip install "selects[ml]"
pip install selects            # base app + web GUI + CLI, no on-device AI
pip install "selects[ml,desktop]"  # AI + a native desktop window instead of a browser tab
selects serve                  # open the web UI (port 8000; honors SELECTS_WEB_PORT)
selects index /path/to/trip    # or run headless from the CLI
```

RAM++ is the ONNX graph in the `selects-onnx` bundle — it is already part of `[ml]`. No extra
`pip install` and no `recognize-anything` git checkout.

### Platform support

SigLIP, CLIP-IQA, and RAM++ run on **CPU** on every platform. The Windows `[ml]` extra installs
DirectML (`onnxruntime-directml`), but those transformer graphs are forced onto the CPU EP —
DirectML is listed, not used for scoring. `selects doctor` reports whether SigLIP would actually
run on a non-CPU provider (today: no). GPU acceleration is on the [roadmap](#roadmap).

| Platform | Scoring | GPU EP |
|---|---|---|
| Windows (x64) | CPU | DirectML bundled; unused for SigLIP/RAM++ |
| macOS (Apple Silicon) | CPU | CoreML may be present; unused for SigLIP/RAM++ |
| macOS (Intel) | CPU | — |
| Linux (x64) | CPU | CUDA may be present; unused for SigLIP/RAM++ |

## Architecture

**API-first**: a FastAPI backend does all the work; every client — web UI, CLI, future mobile — is
just another consumer of the same `/api` surface. State lives in a per-library SQLite DB inside the
photo folder (`<folder>/.selects/`), so a library is self-contained and portable.

```mermaid
flowchart TD
    Clients["Web UI · CLI · Mobile (planned)"]
    API["FastAPI + WebSocket API"]
    Pipeline["Pipeline orchestrator"]
    Classical["Classical scoring<br/>(blur, exposure, faces)"]
    ML["ML stages<br/>(SigLIP, CLIP-IQA, ArcFace, RAM++)"]
    Files[("Photos & videos<br/>local disk")]
    DB[("Per-library SQLite<br/>&lt;folder&gt;/.selects/")]

    Clients --> API
    API --> Pipeline
    API --> DB
    Pipeline --> Classical
    Pipeline --> ML
    Pipeline --> Files
    Pipeline --> DB
```

Each stage reads/writes `<folder>/.selects/index.db` and is independently re-runnable via
`selects index <folder> --pass <stage>`:

| # | Stage | Does |
|---|---|---|
| 1 | `index` | walk & hash files, decode previews/thumbnails, read EXIF/GPS |
| 2 | `video` | sample frames, classical quality, highlights, dead-footage flags |
| 3 | `classical` | blur / exposure / clipped-highlight / face scoring; auto-reject gate |
| 4 | `embed` | SigLIP-SO400M image embeddings + CLIP-IQA aesthetic score |
| 5 | `tag` | zero-shot tagging via SigLIP text-prompt similarity |
| 6 | `ram_tag` | RAM++ open-vocabulary tagging (ONNX in `selects-onnx`) |
| 7 | `smart_tag` | HDBSCAN clustering over embeddings + SigLIP zero-shot names |
| 8 | `face_embed` | ArcFace embeddings for detected faces |
| 9 | `persons` | cluster ArcFace embeddings into named Person identities |
| 10 | `moment` | collapse near-duplicate/burst photos into one best pick |
| 11 | `story` | build day/place stories from moments, tags, and locations |
| 12 | `thematic` | rule-driven location/theme clusters from GPS, people, tags, time |
| 13 | `date` | group photos by calendar day |

`speed_mode=fast` skips `ram_tag`, `smart_tag`, `face_embed`, and `persons`. Aesthetic curation
ranks on CLIP-IQA (`Embedding.aesthetic_iqa` in [0, 1]) with configurable per-scope and
library-wide percentile thresholds (see [Configuration](#configuration)).

## Roadmap

**Shipped (v0.1)**
- [x] Discovery search, tagging, people, face-aware culling
- [x] Stories, aesthetic curation, duplicate finder
- [x] Keyboard culling + compare, taste learning
- [x] Export (copy/zip + XMP), trip recap, video culling, watch folder
- [x] CPU desktop builds for Windows, macOS, Linux + PyPI package

**Planned**
- [ ] **GPU acceleration** — Apple Silicon (MPS/CoreML) & NVIDIA (CUDA) first, AMD (DirectML/ROCm) later
- [ ] **Android companion** — LAN remote that drives the desktop backend from your phone
- [ ] **Android standalone** — on-device culling for small libraries (no desktop needed)
- [ ] Cursor-based pagination, auto-tuned aesthetic/burst thresholds, iOS parity

## Quickstart (from source)

Requires Python 3.11+ and Node 18+.

```bash
pip install -e ".[ml]"        # ML stack (onnxruntime, insightface, sklearn, …); omit [ml] for classical-only
selects serve /path/to/photos # backend + web UI (indexes in the background)

cd frontend && npm install && npm run dev   # hot-reloading UI (separate terminal)
```

`selects serve` opens the web UI (`--no-browser` to skip) and indexes in the background
(`--no-background` to skip). With the frontend built once (`npm run build`), the backend serves the
UI same-origin — no `npm run dev` needed. With **no folder argument** it opens the active library, or
onboarding if none exists. Drive stages directly with `selects index <folder> [--pass <stage>]` and
check hardware with `selects doctor`.

## Configuration

`selects serve` binds **8000** by default. If `--port` is omitted, it honors `SELECTS_WEB_PORT`.
Per-folder settings via `pydantic-settings`; override any field with a `SELECTS_`-prefixed env
var (or `.env`). See `selects/config.py`.

| Field | Default | Notes |
|---|---|---|
| `web_port` | `8000` | Web UI/API port (`selects serve` / `SELECTS_WEB_PORT`) |
| `web_host` | `127.0.0.1` | Bind host |
| `burst_window_seconds` | `12` | Time window for grouping burst shots |
| `burst_similarity_threshold` | `0.96` | Similarity cutoff for burst grouping |
| `aesthetic_per_scope_pct` | `75.0` | CLIP-IQA: must be top `(100 - pct)`% within its scope |
| `aesthetic_library_pct` | `50.0` | CLIP-IQA: must also be top `(100 - pct)`% library-wide |
| `speed_mode` | `full` | `fast` skips `ram_tag`, `smart_tag`, `face_embed`, `persons` |

Derived paths under `<folder>/.selects/`: `index.db`, `thumbs/`, `previews/`.

**Per-trip customization** — drop optional JSON into `<folder>/.selects/` (missing/malformed falls
back to defaults); see [`examples/ladakh/`](examples/ladakh/):

| File | Purpose |
|---|---|
| `landmarks.json` | Named GPS landmarks — fast-path override for reverse geocoding |
| `keywords.json` | Theme buckets for pattern/thematic stories |
| `tag_prompts.json` | Zero-shot SigLIP tag taxonomy |

## Development

```bash
pip install -e ".[dev]" && pytest && ruff check .
cd frontend && npm run lint && npm run e2e   # ESLint + Playwright smoke test
```

`npm run e2e` needs Chromium once (`npx playwright install chromium`). It builds the SPA into
`selects/server/static/`, indexes a throwaway six-photo library in the temp directory and drives the
real server on port 8765. Point `SELECTS_PYTHON` at the interpreter that has `selects` installed if
it is not the one on `PATH` (e.g. `SELECTS_PYTHON=../.venv/Scripts/python.exe`).

For the native desktop window (`pywebview`), install `selects[desktop]` (or `selects[ml,desktop]`
for AI + the desktop window).

Schema is managed with Alembic; migrations ship in `selects/db/migrations/` (no `alembic.ini`) and
`init_db()` upgrades each library's DB to head on open. After editing `selects/db/models.py`,
autogenerate a revision against a throwaway SQLite URL and review it — SQLite ALTERs go through
`render_as_batch` (enabled in `env.py`).

**Layout**

| Path | Contents |
|---|---|
| `selects/` | Package: CLI, config, pipeline, DB models |
| `selects/classical/` | Non-ML scoring (blur, exposure, faces, auto-reject) |
| `selects/decode/` | Image / video / RAW decoding |
| `selects/indexer/` | Folder walking, EXIF, previews, orchestration |
| `selects/ml/` | Embedding, tagging, faces, clustering, stories, enhancement |
| `selects/server/` | FastAPI app, routes, WebSocket progress bus |
| `frontend/` | React + Vite + TypeScript web UI |
| `tests/` · `docs/` | Test suite · landing page (GitHub Pages) |

**Desktop build** — `pip install "pyinstaller>=6.6"` then `python packaging/build.py [--ml]`. It
builds the frontend into `selects/server/static/` (same-origin UI) and runs PyInstaller (onedir) via
`packaging/selects.spec` into `dist/selects/`.

## Contributing

1. Fork, branch, `pip install -e ".[dev]"`, add tests under `tests/`.
2. `pytest` and `ruff check .` must be green.
3. Open a PR — the [architecture](#architecture) and [roadmap](#roadmap) are the best places to find direction.

**Good first areas:** GPU execution providers, the mobile client (the API already exists), export
formats, and tuning aesthetic/burst defaults for different shooting styles.

## Known limitations

- [ ] Aesthetic/burst thresholds were tuned on a single trip; may need adjustment for other styles/gear.
- [ ] List endpoints use offset/limit, not cursor-based, pagination.

## License

MIT — see [LICENSE](LICENSE).
