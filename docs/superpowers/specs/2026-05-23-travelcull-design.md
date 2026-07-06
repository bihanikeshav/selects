# selects — design spec

**Status:** ready to build
**Date:** 2026-05-23
**Working name:** `selects` (placeholder, rename pre-1.0)
**Validation dataset:** `Z:\Ladakh\Photos` — 1065 files (537 JPG, 465 HEIC, 63 MP4, 9-day trip Mar 28–Apr 6 2026)

## Success criterion

Stories and Clusters views produce shareable output on `Z:\Ladakh\Photos` without manual intervention. Culling is a feature, not the goal — the goal is **a one-shot pipeline that turns a trip folder into post-ready themed clusters and ordered narrative sequences**. We iterate prompts, thresholds, and weights until the output is genuinely usable for posting and lookback.

## Product shape

Three-process local app:
- **Indexer** (Python) — walks folder, EXIF, HEIC/HEVC transcoding to 1024px previews, RAW preview extraction, video demux.
- **ML Worker** (Python + CUDA) — long-running, holds GPU, runs the 6-stage pipeline.
- **Web Server** (FastAPI) — serves the Material 3 React UI at `localhost:5173`.

Data: SQLite sidecar at folder root (`.selects.db`) + previews in `.selects/thumbs/`. Originals never modified.

## Pipeline (photos)

| Stage | Models / tools | VRAM | Speed |
|---|---|---|---|
| 1. Classical | OpenCV (CUDA where available), insightface SCRFD, MediaPipe eyes | <1GB | 50–200/s |
| 2. Embedding + aesthetic | SigLIP-SO400M + Aesthetic Predictor V2.5 head | ~3GB | 40–80/s |
| 3. Burst clustering | NumPy/sklearn — adjacent in time + cosine ≥0.85 | CPU | instant |
| 4. Smart pass | Qwen3-VL-4B AWQ-int4 + logit-readout rubric ("Next Token Is Enough" 2-digit extension) + constrained-JSON tagging | ~5GB | 1.5–3/s |
| 5. Narrative ordering | Qwen3-VL-8B AWQ-int4 — interleaved-MRoPE multi-image | ~7GB | 1 cluster / 10–30s |
| 6. Personalization | Logistic regression on SigLIP embeddings (sklearn) — hybrid per-folder seeded from `%APPDATA%\selects\global_taste.pkl` | negligible | refits in <1s every 10 swipes |

**Model lifecycle:** SigLIP → unload → Qwen3-VL-4B → unload → Qwen3-VL-8B. Peak VRAM ~7GB. Fits 8GB cards.

**Axis rubric** (per photo, logit-readout): composition, lighting, subject, sharpness, memory_value. Five forward passes per photo, visual encoder runs once.

**Tag taxonomy** — fixed default + user-extensible via `.selects/tags.toml`:
- Photo tags: food / landscape / portrait / street / architecture / night / nature / wildlife / interior / vehicles / abstract / document / sky / water / mountain
- Video buckets: vlog / action / landscape / food / people / transit / timelapse

## Pipeline (videos) — GPU-first

| Stage | Tools | Notes |
|---|---|---|
| 0. Demux | ffmpeg with `-hwaccel cuda` (NVDEC), `gpmf-parser` for GoPro | Keep frames on GPU |
| 1. Cheap signals | PySceneDetect AdaptiveDetector + Silero VAD ONNX + librosa + cv2.cuda Farneback flow | 10–30× real-time |
| 2. Shot segmentation | Pure Python merge | Negligible |
| 3. Keyframe selection | OpenCV CUDA — Laplacian × face count × exposure × center bias | 50× real-time |
| 4. Bucket classify | SigLIP-on-frames with text prompts (zero new model) | <1s/shot |
| 5. Score + highlight | Reuse photo pipeline on keyframes; highlight = bucket × stability × audio peak × aesthetic | Reuses photo VRAM |
| 6. Export | NVENC via `ffmpeg -c:v h264_nvenc` when transcoding | GPU-bound, fast |

**Length-aware branching:** `<10s` → single keyframe, no highlight search. `10s–2min` → full pipeline. `>2min` → stability sub-shots within the long clip; suggest clip ranges.

**GPMF gyro signal** (free for action cameras) gives per-frame stability score without ML; angular velocity peaks mark "interesting moments" (jumps, wave hits).

## GPU-first across the board

Following user directive ("always GPU when possible, NVDEC/NVENC much faster"):

- **JPEG decode:** `nvImageCodec` (NVIDIA Python bindings) — GPU-resident output.
- **HEIC decode:** `pillow-heif` for CPU decode (no GPU HEIC codec available in consumer stack), immediate GPU upload.
- **Video decode:** `torchcodec` or PyAV with `-hwaccel cuda` — NVDEC, frames stay on GPU.
- **Preprocessing:** `torchvision.transforms.v2` on `device='cuda'`. No CPU-side resize/normalize.
- **Classical CV:** `cv2.cuda` for blur (Laplacian) and Farneback flow.
- **All inference:** GPU.
- **Video export:** NVENC encoder.

**Falls back to CPU only when** the hardware can't accommodate (smaller GPUs at the 8GB minimum), or when GPU upload+ops > CPU op for a single tiny task.

## Output formats

- **JSON manifest** — primary output, contains everything (paths, scores, tags, story orderings, decisions).
- **XMP sidecars** — write `IMG_4781.HEIC.xmp` next to each photo with: star rating (derived from `final_score`), color label (cluster category), keywords, picked/rejected flag, custom `selects:*` namespace for round-tripping our scores. Standard format consumed by **darktable** (primary integration target), RawTherapee, Lightroom.
- **Symlink folder** — `<folder>/keepers/` organized by cluster or by story.
- **Flat JPEG copy** — for sharing; HEIC transcoded via NVENC where applicable.
- **Per-story carousel** — sequentially numbered JPEGs ready for Instagram drag-drop.
- **"Open keepers in darktable"** command palette action.

## Speed modes

Three modes selected at CLI / config:

- `--mode fast` — ~5 min total: classical + V2.5 aesthetic + tags only; no rubric, no ordering.
- `--mode standard` — ~1 hr: full pipeline, Stories enabled, rubric on all photos.
- `--mode thorough` — overnight: deeper video sampling, rubric on all photos (including burst losers), wider story candidate pool.

## UI (Material 3 inspired, light default, dark essential)

Three views, keyboard-first:
- **Burst cull** — large gold photo, vertical burst strip with silver promotion (S+N), multi-axis score bars (Google color quartet mapped to axes: composition=blue, lighting=yellow, subject=green, sharpness=red), memory value ring as the signature visual (multi-color gradient).
- **Clusters** — tinted-glyph card grid; click into a cluster for in-cluster ranked grid.
- **Stories** — horizontal-scroll narrative sequences with rationale + carousel export.

Top app bar, status row with view-tab pills, navigation rail on left, keyboard hint footer. No emojis anywhere. Mockup at `Z:\travel_post\design\index.html`.

## Personalization

Hybrid model — each folder gets its own `~/.selects/models/<folder-hash>.pkl`, **seeded from** `%APPDATA%\selects\global_taste.pkl` so a new folder doesn't start from scratch. Re-fits in <1s every 10 swipes via sklearn. Trained on positive (keep/silver) vs negative (reject) labels over SigLIP embeddings.

Final ranking:
```
final_score = 0.4 * aesthetic_v25_norm
            + 0.3 * vl_rubric_avg_norm
            + 0.3 * personalization_prob    (grows 0 → 0.3 with confidence)
```

## Data model

SQLite sidecar at `.selects.db`. Eight core tables: `photo`, `video`, `video_shot`, `classical_score`, `embedding`, `burst`, `burst_member`, `vl_score`, `photo_tag`, `swipe`, `pipeline_state`. Schema is content-hash keyed for instant re-runs.

Storage budget for Ladakh dataset (1065 files): ~30MB SQLite, ~180MB thumbnails+previews. Originals untouched.

## Scope

**v1 in scope:**
- Photos (HEIC/JPG/RAW)
- Videos (HEVC/H.264, MP4/MOV), GPU-decoded
- Stages 1–6 photo pipeline
- Stages 0–6 video pipeline
- All three UI views including Stories
- XMP sidecar writer + darktable launch action
- Watch-folder mode
- Three speed modes
- Windows-only, `pip install selects`
- MIT license

**Out of scope:**
- Editing (crops, exposure, color)
- Caption generation
- Aspect-ratio reframe
- Cloud sync, multi-user, mobile native
- Face identity recognition
- macOS / Linux (community contributions welcome)
- Auto-update
- Any telemetry

## Differentiation vs OSS competitors

Verified by survey of Facet (96★), PhotoSort (11★), RapidRAW (7.6k★ but RAW editor not culler), QuickRawPicker (no AI), digiKam IQS (DB UI not swipe), donwrightdesigns AI culling RTX (Ollama-based, slower).

Our novel combination:
1. **Video first-class** with NVDEC + GPMF gyro signal. No OSS culler does this.
2. **Qwen3-VL rubric via logit-readout** (Q-Align trick + "Next Token Is Enough" 2-digit extension). No OSS culler uses this.
3. **Narrative ordering (Stories)** via Qwen3-VL-8B interleaved-MRoPE. Genuinely novel.
4. **Hybrid personalization** that seeds from global taste. No OSS tool does the seeding.
5. **GPU-first end-to-end** (NVDEC decode + GPU preprocessing + GPU CV + NVENC encode). Existing tools are CPU-heavy.
6. **Darktable XMP integration depth** including watch-folder live mode.
7. **The combination itself** — Photo Mechanic-grade keyboard speed × Aftershoot-style multi-axis AI × clean XMP handoff — is the explicit gap the OSS survey identified.

## Risks

- Qwen3-VL int4 inference on Windows: vLLM on Windows is fragile. Fallback: `transformers` + `bitsandbytes`.
- HEVC decode driver quirks on consumer Windows. Mitigation: detect NVDEC capability at startup, fall back to CPU decode with a UI warning.
- Model download size (~12GB on first run). Mitigation: first-run wizard with progress + ability to defer Qwen3-VL-8B until first Stories use.
- Cold-start personalization (first ~30 swipes use only global model). Mitigation: explain in onboarding.
- Story ordering quality on 9-day trips: 30+ landscape photos may not have a single coherent narrative. Mitigation: cluster within tag by time-of-day + location before ordering.

## Milestones

- **M1 (~1 week):** indexer + UI shell, classical signals, web UI wired to real Ladakh data — see thumbnails + auto-rejects.
- **M2 (~2 weeks):** SigLIP + Aesthetic V2.5 + Qwen3-VL-4B smart pass + burst clustering + personalization + functional swipe UI. **Clusters view quality milestone.**
- **M3 (~2 weeks):** video pipeline (NVDEC, GPMF, scene detection, bucket classify) + Qwen3-VL-8B Stories + XMP sidecar + darktable launch + watch-folder. **Stories view quality milestone.**

Each milestone ends with a demo on Z:\Ladakh\Photos.
