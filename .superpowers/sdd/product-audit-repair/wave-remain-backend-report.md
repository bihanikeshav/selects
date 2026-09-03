# Remaining backend leftovers — report

**Status:** DONE  
**Branch:** `fix/product-audit-repair`  
**Commit:** `fix: remaining audit leftovers (lock, stories, swipe, lan, xmp, haar, categories)`

No AP25/NIMA or DirectML graph work. No subagents.

## What shipped

| # | Item | Change |
|---|------|--------|
| 1 | Watcher vs indexing lock | `LibraryWatcher.poll_once` calls `manager.begin_indexing()` when stable files exist; skip the poll if False; `finally: end_indexing()`. Wired from `watch_routes` via `get_or_create_watcher(..., manager=manager)`. |
| 2 | Story wipe-then-cancel | Day stories + visits computed in memory first; wipe+insert only after the day loop. Cancel/exception during compute leaves existing stories. Place/pattern/people still run after the swap. |
| 3 | Swipe validation | `record_swipe` calls `_require_sha256`; `decision` must be keep\|reject\|silver\|skip else 400. |
| 4 | `/api/edit/open` allowlist | Only `darktable`\|`rawtherapee`\|`gimp`. Resolves via `_find_editor_binary` / `_find_named_editor`. No `shutil.which` on arbitrary strings. |
| 5 | LAN bind | `lan_bind_refused()` in `cli.py`; `serve` and `desktop.run_app` refuse `0.0.0.0` / `::` unless `SELECTS_ALLOW_LAN` is `1` or `true`. Default host remains `127.0.0.1`. |
| 6 | XMP path jail | `plan_xmp_write` / preview / write take `library_root`. `Path(photo.path).resolve().is_relative_to(cfg.folder.resolve())`; outside → `no_op` / `outside library`. Export routes pass `cfg.folder`. |
| 7 | Video frame SHA jail | Local `_require_sha256` (hex-64, no `..` `/` `\`) on `/api/videos/{sha256}/frames` and `.../frames/{index}`. Not imported from `routes`. |
| 8 | Burst config | `run_moment_stage` uses `cfg.burst_window_seconds` and `cfg.burst_similarity_threshold`. FolderConfig defaults updated **3/0.92 → 12/0.96** so wiring config is not a silent 4× tighter window. |
| 9 | Haar fallback | InsightFace miss → OpenCV Haar (`cv2.data.haarcascades` + `haarcascade_frontalface_default.xml`). Boxes at confidence 0.5, no embeddings. Haar missing → `[]`. |
| 10 | PhotoCategory | New `selects/ml/categories.py` `run_category_stage`. portrait (faces≥1) / landscape (tag words) / object (tagged) / unclassified. Wipe+rewrite. Stage `category` after `tag`, before `ram_tag`. |
| 11 | Nominatim UA | `selects/0.1.13 (https://github.com/bihanikeshav/selects)` |
| 12 | needs_onboarding | True only with no libraries or no active. A registered library with 0 photos is not onboarding. |
| 13 | Export basename collision | Existing dest → `stem (n).suffix` (copy). Zip `arcname` unique-ified the same way. |
| 14 | Geocode extra commit | `reverse_geocode` add/flush only. `build_visits_for_day` uses `session_scope` so the cache write commits without double-committing a session that may hold story rows. |

## Files

- `selects/watcher.py`, `selects/server/watch_routes.py`
- `selects/ml/stories.py`
- `selects/server/routes.py`
- `selects/cli.py`, `selects/desktop.py`
- `selects/export.py`, `selects/server/export_routes.py`
- `selects/server/video_routes.py`
- `selects/ml/moments.py`, `selects/config.py`
- `selects/classical/faces.py`
- `selects/ml/categories.py` (new), `selects/server/pipeline_runner.py`
- `selects/ml/locations.py`
- `selects/server/library_manager.py`
- Tests listed below

## Tests

```
.venv\Scripts\python.exe -m pytest tests/test_watcher.py tests/test_config.py tests/test_pipeline_runner.py tests/test_classical_faces.py tests/test_cli.py tests/test_server_libraries.py tests/test_export.py tests/test_video.py tests/test_ml_categories.py tests/test_server_routes.py tests/test_ml_stories.py tests/test_ml_moments.py -q
```

**120 passed**, 1 warning (pre-existing ONNX CUDA EP miss on InsightFace prepare).

Named coverage for the required items:

1. `test_poll_once_skips_when_indexing_lock_held`, `test_poll_once_acquires_and_releases_indexing_lock`, `test_poll_once_releases_lock_when_pipeline_raises`
3. `test_record_swipe_requires_hex_sha_and_known_decision`
4. `test_edit_open_rejects_non_allowlisted_editor` (`shutil.which` not called for `notepad`)
5. `test_lan_bind_refused_helper`, `test_serve_refuses_lan_bind_without_env`
7. `test_frames_rejects_non_hex_sha`
10. `test_default_stage_order` (category after tag, before ram_tag), `tests/test_ml_categories.py`
12. `test_status_registered_empty_library_is_not_onboarding`
13. `test_copy_uses_unique_name_when_dest_exists`, `test_copy_unique_names_for_same_basename`, `test_zip_unique_arcname_on_collision`

Also: Haar invoke mock, story cancel keeps existing rows, XMP outside-library no-op, burst defaults 12 / 0.96.

## Self-review

- Watcher lock is around incremental index only; empty polls do not take the slot. Skip does not call `end_indexing`.
- Day-story wipe is after compute; place/pattern/people still mutate after the swap (allowed minimum).
- Editor allowlist is a closed set before any PATH lookup.
- XMP jail is in the engine (`library_root`), not only HTTP.
- Category does not invent AP25/NIMA; `primary_category` is faces + tags.

## Concerns

1. **Place/pattern/people after swap.** A cancel during those builders can still drop newly written day stories (old rows already wiped). Day compute cancel is safe.
2. **Haar xml missing.** Some OpenCV builds omit `cv2.data.haarcascades`; then faces are `[]` (same as pre-fix InsightFace miss).
3. **`SELECTS_ALLOW_LAN`.** Only `1` and `true`. `yes` / `on` still refuse.
4. **sklearn/pyarrow import noise.** `test_ml_stories` still prints a Windows access violation while importing sklearn via `locations` (pre-existing). Tests still pass.
5. **Zip arcname separators.** Unique names use `Path` so Windows zips may keep backslashes, matching the previous `str(_dest_rel_path(...))` behaviour.
