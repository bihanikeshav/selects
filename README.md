# TravelCull

TravelCull is a local-first, AI-assisted photo culling tool for travel photography. Point it at a
folder of photos (and videos), and it indexes, scores, clusters, and groups them into stories so
you can quickly find your keepers — without uploading anything anywhere. All ML inference (image
embeddings, tagging, face clustering, aesthetic scoring, optional enhancement) runs on your own
machine; the only network calls are optional reverse-geocoding lookups for place names.

## Features

- Fast local indexing of JPEG/HEIC/RAW photos and video files, with thumbnail/preview generation
- Classical signal scoring: blur, exposure, clipped highlights, face detection, auto-reject
- SigLIP image embeddings for semantic search and similarity
- Zero-shot and RAM++-based photo tagging
- ArcFace face embeddings + clustering into "Person" identities, with name labeling
- GPS/time-based location clustering and "story" building (day-by-day, place-by-place)
- "Moment" grouping to collapse near-duplicate bursts into a single best pick
- AP25 + NIMA-based aesthetic curation (per-scope and library-wide percentile gating)
- Optional local enhancement models: NAFNet (deblur), Zero-DCE++ (low-light), CSRNet (retouch)
- React/Vite web UI for reviewing clusters, people, stories, and doing burst culling
- CLI for headless indexing and running individual pipeline stages

## Quickstart

Requires Python 3.11+ and Node 18+.

```bash
# Backend
pip install -e ".[ml]"        # add the ML stack (torch, transformers, insightface, etc.)
travelcull serve /path/to/photos

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

`travelcull serve` starts the FastAPI backend, opens the web UI in your browser (pass
`--no-browser` to skip), and (unless `--no-background` is passed) kicks off indexing in the
background. The frontend dev server (`npm run dev`) proxies to the backend for a hot-reloading UI
during development.

For a single-process run, build the frontend once (`cd frontend && npm run build`) and the backend
will serve the compiled UI itself at the same origin — no separate `npm run dev` needed. You can
also run `travelcull serve` with **no folder argument**: it opens the active library from your
registry, or, if none exists, starts on the onboarding page so you can add your first library from
the browser.

If you only need the classical (non-ML) stages — indexing, previews, blur/exposure/face
auto-reject — you can skip the `ml` extra: `pip install -e .`.

You can also drive the pipeline directly from the CLI:

```bash
travelcull index /path/to/photos               # run all default stages
travelcull index /path/to/photos --pass embed   # run a single named stage
travelcull doctor                               # report CUDA/GPU capabilities
```

## How the pipeline works

Each stage reads/writes to a per-folder SQLite database at `<folder>/.travelcull/index.db` and can
be re-run independently via `travelcull index <folder> --pass <stage>`:

1. **index** — walk the folder, hash files, decode previews/thumbnails, read EXIF/GPS
2. **classical** — blur, exposure, clipped-highlight, and face-count scoring; auto-reject gate
3. **embed** — SigLIP-SO400M image embeddings + CLIP-IQA aesthetic score
4. **tag** — zero-shot tagging via SigLIP text-prompt similarity
5. **ram_tag** — RAM++ open-vocabulary tagging (more descriptive labels than zero-shot)
6. **smart_tag** — HDBSCAN clustering over embeddings + VLM-generated cluster names
7. **thematic / date** — rule-driven location and day clustering from GPS/time
8. **face_embed** — ArcFace face embeddings for detected faces
9. **moment** — collapse near-duplicate/burst photos into a single representative pick
10. **story** — build day/place "stories" combining moments, tags, and locations

Aesthetic curation (surfacing the "best of" a trip) combines AP25 and NIMA scores with configurable
per-scope and library-wide percentile thresholds — see `ap_weight`, `nima_weight`,
`aesthetic_per_scope_pct`, and `aesthetic_library_pct` below.

## Configuration

Configuration is per-folder, via `pydantic-settings`. Every field can be overridden with an
`TRAVELCULL_`-prefixed environment variable (or a `.env` file in the working directory), e.g.
`TRAVELCULL_WEB_PORT=9000`. Fields (see `travelcull/config.py`):

| Field | Default | Notes |
|---|---|---|
| `web_port` | `8765` | Port for the web UI/API |
| `web_host` | `127.0.0.1` | Bind host |
| `burst_window_seconds` | `3` | Time window used to group burst shots |
| `burst_similarity_threshold` | `0.92` | Similarity cutoff for burst grouping |
| `ap_weight` | `0.6` | Weight of AP25 in the combined aesthetic score |
| `nima_weight` | `0.4` | Weight of NIMA in the combined aesthetic score |
| `aesthetic_per_scope_pct` | `75.0` | Photo must be in the top `(100 - pct)`% within its scope |
| `aesthetic_library_pct` | `50.0` | Photo must also be in the top `(100 - pct)`% library-wide |
| `speed_mode` | `full` | `fast` skips some ML stages for a quick preview pass |

Derived, non-configurable paths under `<folder>/.travelcull/`: `index.db`, `thumbs/`, `previews/`.

### Per-trip customization

The location and tagging stages ship with travel-generic defaults, but you can tune them per
library by dropping optional JSON files into `<folder>/.travelcull/`:

| File | Purpose |
|---|---|
| `landmarks.json` | Named GPS landmarks (`{"name", "lat", "lon", "radius_m"}`) used as a fast-path override for reverse geocoding. Without it, geocoding relies entirely on Nominatim. |
| `keywords.json` | Theme buckets (`{label: [keyword, ...]}`) for pattern/thematic stories. |
| `tag_prompts.json` | Zero-shot SigLIP tag taxonomy (`{tag: [prompt, ...]}`). |

Each file is optional; a missing or malformed file falls back to the built-in defaults. See
[`examples/ladakh/`](examples/ladakh/) for a complete worked example (the original Ladakh dataset)
and copy any file into your own `.travelcull/` to customize.

## Hardware notes

The ML stages (embedding, tagging, face recognition, clustering, enhancement) are much faster with
a CUDA GPU — `travelcull doctor` reports what's available (CUDA, NVDEC via torchcodec,
nvImageCodec, `cv2.cuda`). Everything falls back to CPU, but expect the ML stages to be
significantly slower, especially the enhancement models (NAFNet/Zero-DCE++/CSRNet) and the VLM
cluster-naming pass. Classical scoring (blur/exposure/face-detect) and indexing run fine on CPU.

## Project layout

- `travelcull/` — Python package: CLI, config, pipeline orchestration, DB models
  - `classical/` — non-ML signal scoring (blur, exposure, face detect, auto-reject, straighten)
  - `decode/` — image/video/RAW decoding
  - `indexer/` — folder walking, EXIF reading, preview generation, orchestration
  - `ml/` — embedding, tagging, faces, clustering, stories, moments, enhancement models
  - `server/` — FastAPI app, routes, websocket progress bus
- `frontend/` — React + Vite + TypeScript web UI
- `tests/` — pytest suite
- `scripts/` — standalone analysis/benchmarking scripts (not part of the package)
- `docs/` — design notes and specs
- `design/` — visual design references

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

Database schema is managed with Alembic. Migrations ship inside the package at
`travelcull/db/migrations/` (there is no `alembic.ini`), and `init_db()` brings
each library's SQLite DB up to head automatically on open — fresh DBs are created
and stamped, pre-existing DBs are stamped at the baseline and upgraded. After
changing `travelcull/db/models.py`, create a new revision with autogenerate by
building an in-code `Config` pointing `script_location` at
`travelcull/db/migrations` and `sqlalchemy.url` at a throwaway SQLite file, then
calling `alembic.command.revision(cfg, autogenerate=True, message="...")`. Always
review the generated script — SQLite ALTERs must go through `render_as_batch`
(already enabled in `env.py`).

## Packaging a desktop build

Produce a self-contained bundle (no Python install required on the target machine) with
PyInstaller:

```bash
pip install "pyinstaller>=6.6"
python packaging/build.py
```

The script builds the frontend (`npm run build`), copies `frontend/dist` into
`travelcull/server/static/` so the packaged app serves the UI same-origin, then runs PyInstaller in
**onedir** mode using `packaging/travelcull.spec`. The result lands in `dist/travelcull/` — launch
`dist/travelcull/travelcull.exe` (or `./travelcull` on macOS/Linux).

By default the ML stack (torch, transformers, insightface, …) is **excluded** to keep the bundle
small and the build fast — the base app still indexes, previews, and runs the classical auto-reject
stages. To bundle the ML deps, use `python packaging/build.py --ml` (or set
`TRAVELCULL_BUNDLE_ML=1`); any ML package that isn't installed is skipped, so the build always
degrades gracefully.

CPU-torch tip: the default CUDA torch wheels are multiple GB. For a much smaller ML bundle, install
the CPU build first:

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
python packaging/build.py --ml
```

## Known limitations

- Aesthetic and burst-detection thresholds were tuned against a single trip's photo set and may
  need adjustment for very different shooting styles or camera gear
- List endpoints use simple offset/limit pagination, not cursor-based
- RAM++ tagging depends on a model with no PyPI release (installed via git URL); expect a slower,
  less reproducible install than the rest of the `ml` extra

## License

MIT — see [LICENSE](LICENSE).
