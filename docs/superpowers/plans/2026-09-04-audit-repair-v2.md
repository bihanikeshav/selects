# Audit Repair v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the audit's engineering defects, simplify the verdict model and UI copy, and prove the result with lint, tests, and a browser smoke test on Linux and Windows CI.

**Architecture:** FastAPI + SQLite backend split into domain route modules; React/Vite frontend with one keyboard layer and a three-state verdict; Playwright smoke test driving a classical-only temp library; GitHub Actions matrix (ubuntu, windows) for pytest, ruff, eslint, build, e2e.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2, Alembic, pytest, ruff; React 18, TypeScript 5.4, Vite 5, ESLint 9 flat config, Playwright.

**Spec:** `docs/superpowers/specs/2026-09-04-audit-repair-v2-spec.md` — read it; every task's exact values live there and are binding.

## Global Constraints

- Version `0.1.14` in pyproject.toml, selects/__init__.py, frontend/package.json; app.py reads `selects.__version__`.
- `pytest -q`, `ruff check selects tests`, `npm run build`, `npm run lint` all pass after every task that touches the relevant side.
- No `pip install torch` in any workflow.
- Keep every API path the frontend calls unless the spec removes it.
- Conventional commit prefixes; no emojis in docs/code; never touch `.superpowers/`, `screenshots/`, root artefacts.
- Work happens on branch `fix/product-audit-repair` in `Z:\travel_post`; Python runs via `.venv/Scripts/python.exe`; frontend via `node`/`npm` in `frontend/`.
- Every task: run the focused tests while iterating, the full relevant suite once before committing, and commit with `git add <files>` (never `git add -A`, the tree has untracked notes).

---

### Task 1: Backend hygiene — version, ruff, utcnow, deps, CI, README

**Spec sections:** A1.

**Files:**
- Modify: `pyproject.toml`, `selects/__init__.py`, `frontend/package.json`, `selects/server/app.py:147`
- Create: `selects/util.py` (`utcnow()`)
- Modify: `selects/db/models.py`, `selects/video.py`, `selects/server/routes.py` (utcnow call sites only; do not restructure routes.py — Task 2 does)
- Modify: every file ruff flags (`.venv/Scripts/python.exe -m ruff check selects tests` lists 53)
- Modify: `.github/workflows/ci.yml`, `.github/workflows/release.yml`, `.github/workflows/nuitka.yml`
- Modify: `README.md`
- Test: `tests/test_util.py` (new), `tests/test_server_routes.py` (version assertion)

**Interfaces:**
- Produces: `selects.util.utcnow() -> datetime` (naive UTC). Later tasks import it for any new timestamp.
- Produces: `[project.optional-dependencies].desktop = ["pywebview>=5.0"]`.

- [ ] **Step 1: Test for utcnow** — `tests/test_util.py`: `utcnow()` returns a naive datetime within 5 s of `datetime.now(timezone.utc).replace(tzinfo=None)`. Run, see ImportError, implement `selects/util.py`, run green.
- [ ] **Step 2: Replace `datetime.utcnow`** everywhere (`grep -rn utcnow selects`), including model `default=` callables (`default=utcnow`). Keep column types naive.
- [ ] **Step 3: Version** — bump three files to `0.1.14`; in `app.py` do `from selects import __version__` and `FastAPI(title="selects", version=__version__, ...)`. Add test: `client.get("/openapi.json").json()["info"]["version"] == selects.__version__`.
- [ ] **Step 4: Dependencies** — `opencv-python` -> `opencv-python-headless>=4.9`; remove `pywebview` from `dependencies`, add extra `desktop = ["pywebview>=5.0"]`. In `packaging/build.py` and both release/nuitka workflows change `".[ml]"` to `".[ml,desktop]"`. In `ci.yml` remove the torch line and the stale comment; keep `pip install -e ".[ml,dev]"`. Add a `lint` job (ubuntu, python 3.11, `pip install ruff`, `ruff check selects tests`). Remove the torch install from release.yml and nuitka.yml too.
- [ ] **Step 5: Ruff** — fix all findings without adding `noqa` except `# noqa: E402` where a module intentionally sets up logging or lazy-imports before imports (keep those minimal). Run `ruff check selects tests` -> "All checks passed".
- [ ] **Step 6: README** — config table defaults (12, 0.96); privacy sentence about Map tiles + optional geocoding; Development section: `npm run lint`, `npm run e2e` (added in Task 6), and `selects[desktop]` for the native window; Install section mentions `pip install "selects[ml,desktop]"` for the desktop window.
- [ ] **Step 7: Verify** — `pytest -q` green, `ruff check selects tests` clean, `cd frontend && npm run build` green. Commit as `chore: single-source version, ruff clean, headless cv2, desktop extra, CI without torch`.

---

### Task 2: Route split, dead endpoints, swipe summary/delete, stories cache, persons query

**Spec sections:** A2.

**Files:**
- Create: `selects/server/schemas.py`, `selects/server/photos_routes.py`, `selects/server/stories_routes.py`, `selects/server/clusters_routes.py`, `selects/server/persons_routes.py`, `selects/server/images_routes.py`, `selects/server/calibrate_routes.py`, `selects/server/map_routes.py`, `selects/server/editor_routes.py`
- Modify: `selects/server/routes.py` (becomes a thin aggregator), `selects/ml/curation.py` (threshold cache), `selects/ml/stories.py` or wherever titles/breadcrumbs are rendered (strip ` (N)`), `selects/server/routes.py` `_build_breadcrumb`/`_story_to_out` moved to `stories_routes.py`
- Delete: `selects/ml/lowlight.py` (only import was the removed doctor endpoint; confirm with grep)
- Test: `tests/test_server_routes.py` (add: delete swipe; summary collapse arithmetic; removed endpoints return 404; threshold cache invalidates when the DB changes), `tests/test_ml_curation.py` (cache hit/miss), `tests/test_ml_stories.py` (title suffix stripped)

**Interfaces:**
- Consumes: `selects.util.utcnow`.
- Produces: `DELETE /api/swipes/{sha256}` -> `{"ok": true, "deleted": bool}`; `GET /api/swipes/summary?collapse=moments|none` -> `{total_photos, kept, rejected, undecided}` with `kept + rejected + undecided == total_photos`; `selects.ml.curation.compute_rank_threshold` unchanged signature but cached; `selects.ml.curation.clear_threshold_cache()` for tests.

- [ ] **Step 1: Mechanical split.** Move each endpoint group verbatim into its module with a `register_<group>_routes(app, cfg)`; shared `Session()` closure pattern stays (`init_db(cfg.db_path)()`), models to `schemas.py`, `_require_sha256` and `_serve_image_for` to `images_routes.py` and imported where needed. `routes.py` ends up under 60 lines. Run the full suite after the move; nothing may change behaviour yet.
- [ ] **Step 2: Remove** `/api/doctor/issues`, `/api/calibrate/next`, `/api/search` (v1, keep `selects/ml/search.py` because search2 uses it), `/api/moments`, `/api/photos/{sha256}/tags`, and `selects/ml/lowlight.py`. Tests: each removed path returns 404 on the test client.
- [ ] **Step 3: Swipe summary with collapse** — TDD: create 3 photos, moment of 2 with a primary, swipes keep on primary and reject on the non-primary; `collapse=moments` -> total 2, kept 1, rejected 0, undecided 1; `collapse=none` -> total 3, kept 1, rejected 1, undecided 1. Implement by reusing the same collapse predicate `/api/photos` uses (extract a helper `collapsed_photo_ids(s)` in `photos_routes.py`).
- [ ] **Step 4: DELETE swipe** — TDD: POST keep then DELETE -> `{ok, deleted: true}`; second DELETE -> `deleted: false`; unknown sha -> 404.
- [ ] **Step 5: Threshold cache** in `curation.py`: key `(str(db_url), mtime_ns of the DB file, pct_floor, ap_w, nima_w)`; get the DB path from the session bind URL (see `taste.state_dir_from_session` for the pattern). Test: two calls hit once (patch `np.percentile` and count); touching the DB file (write a row) invalidates. `list_stories` computes the library threshold once and passes it through `curate(..., library_threshold=<value>)` (add an optional kwarg that bypasses `compute_rank_threshold` when given).
- [ ] **Step 6: Persons single query** — replace the per-person face loop with one query joining `PhotoPerson` and `FaceEmbedding` filtered on the visible person ids, grouped in Python. Existing persons tests must pass.
- [ ] **Step 7: Title suffix** — where story titles and `itinerary_breadcrumb` are built, strip a trailing ` (\d+)` from visit names for display only (Visit.name in the DB keeps the suffix so `/best/place/<name>` still works). Test: a visit named `Leh (2)` renders `Leh` in title and breadcrumb.
- [ ] **Step 8: Verify** — `pytest -q`, `ruff check selects tests`. Commit `refactor: split routes by domain, drop dead endpoints, collapse-aware swipe summary, cached story threshold`.

---

### Task 3: LAN token cookie, websocket auth, shutdown cancel, pruning, cascades

**Spec sections:** A3, A4.

**Files:**
- Modify: `selects/server/app.py` (middleware + lifespan), `selects/server/ws.py`, `frontend/src/main.tsx`
- Modify: `selects/indexer/orchestrator.py`, `selects/db/models.py`
- Create: `selects/db/migrations/versions/<rev>_cascade_fks.py`
- Test: `tests/test_server_lan.py` (new), `tests/test_indexer_orchestrator.py`, `tests/test_migrations.py`, `tests/test_db.py`

**Interfaces:**
- Consumes: `LibraryManager.request_cancel()`.
- Produces: cookie name `selects_token`; websocket close code 4401; `index_folder` progress message `"{n} missing file(s) removed"`.

- [ ] **Step 1: LAN cookie** — TDD with `build_app(cfg, run_background=False, bind_host="0.0.0.0")` and `SELECTS_LAN_TOKEN` set, using `TestClient` with a non-loopback `client` (Starlette `TestClient(app, client=("10.0.0.5", 1234))`): request without token -> 401; with `?token=` -> 200 and `set-cookie: selects_token=...; HttpOnly; SameSite=Lax; Path=/`; subsequent request with only the cookie -> 200; `/api/health` always 200.
- [ ] **Step 2: Websocket** — in `ws.py`, `register_ws(app, lan_token=None)`: if `lan_token` and the client host is non-loopback, require `token` query param or cookie, else `await websocket.close(code=4401)` before accept. `app.py` passes `lan_token if lan_exposed else None`. Test with `client.websocket_connect("/ws/progress")` raising on 4401 and succeeding with `?token=`.
- [ ] **Step 3: main.tsx** — delete the fetch monkeypatch; if `token` is in the query, do one `fetch("/api/health?token=…")` so the cookie is set, then `history.replaceState` to drop the param. Keep the file under 30 lines.
- [ ] **Step 4: Shutdown** — in the lifespan `finally`, call `manager.request_cancel()` first. Test: after `TestClient` context exit with a fake long-running `run_pipeline_stages` (monkeypatched to loop until `should_cancel()`), the worker thread ends within 5 s.
- [ ] **Step 5: Pruning** — TDD in `test_indexer_orchestrator.py`: index a folder with two jpg copies (same sha, different names); delete one file; re-run `index_folder` (no `paths=`); the row is gone, the thumb still exists (shared sha); delete the other, re-run: thumb and preview removed. With `paths=[...]` nothing is pruned. Implement after the walk: compare DB paths to walked paths, delete rows in one transaction, then unlink orphaned thumb/preview files.
- [ ] **Step 6: Cascade migration** — update models per spec A4; write a batch-mode Alembic revision that recreates the FKs with `ondelete`. Test in `test_migrations.py`: build a DB at the previous head with a photo, swipe, photo_person, photo_edit, moment(primary), visit(cover), person(cover_face); upgrade; delete the photo via ORM; assert swipe/photo_person/photo_edit/moment rows gone, `visits.cover_photo_id` is NULL. Also assert a fresh `create_all` DB has the same FK actions (`PRAGMA foreign_key_list`).
- [ ] **Step 7: Verify** — `pytest -q`, `ruff check selects tests`, `npm run build`. Commit `fix: LAN cookie auth incl. websocket, cancel index on shutdown, prune missing files, cascade FKs`.

---

### Task 4: Review screen and the three-state verdict (frontend)

**Spec sections:** B1, B2.

**Files:**
- Modify: `frontend/src/views/BurstCull.tsx`, `frontend/src/hooks/useCullKeys.ts`, `frontend/src/hooks/useLikes.ts` (rename to `useKeep.ts`, exports `useKeepStatus`, `useToggleKeep`), `frontend/src/components/KbdFooter.tsx`, `frontend/src/views/Curated.tsx`, `frontend/src/components/CompareView.tsx`, `frontend/src/components/StackPhoto.tsx`, `frontend/src/components/BurstThumb.tsx`, `frontend/src/api/client.ts` (`deleteSwipe`, `swipeSummary(collapse)`), `frontend/src/api/types.ts`, `frontend/src/styles.css` (only the `.cull-*`, `.gold-frame`, `.kbd-*`, `.curated-*`, `.page-*` blocks)
- Do NOT modify: `Rail.tsx`, `Topbar.tsx`, `PageHeader.tsx` (Task 5 owns them).

**Interfaces:**
- Consumes: `DELETE /api/swipes/{sha}`, `GET /api/swipes/summary?collapse=moments`.
- Produces: `useToggleKeep(setKept)` hook used by Stories in Task 5; `KbdFooter` chip list per spec B1.

- [ ] **Step 1: Client** — add `deleteSwipe(sha)` and `swipeSummary(collapse)` to `client.ts`; type `SwipeSummary` in `types.ts`.
- [ ] **Step 2: Keyboard layer** — `useCullKeys` handles exactly: ArrowLeft/ArrowRight (prev/next), K keep, X reject, U undo, Z zoom, V compare toggle, Enter compare open (when provided), Tab next burst, `[`/`]` burst cycle, E enhance, S straighten. Remove ArrowUp/Down, C, Space, J, L, D, F. Delete the inline `window.addEventListener("keydown")` effect in BurstCull; wire `[`/`]`/E/S through the hook.
- [ ] **Step 3: Verdict** — `decide(sha, "keep"|"reject")`; undo restores the previous session decision or calls `deleteSwipe`. Buttons: "Keep · K" (filled green when kept), "Reject · X", "Auto edit · E", "Straighten · S". Remove the `silver` path and the Like wording everywhere in this view.
- [ ] **Step 4: Header** — per B2: subtitle from `swipeSummary("moments")`, quality `<select>` labelled "Show" in the actions row, no "Sort:" label, `aria-label="Sort order"` on the button group, stamp removed / burst stamp text, empty state links to `/libraries` with `<Link>`, poll every 15 s and pause when `document.visibilityState !== "visible"`.
- [ ] **Step 5: Stage width** — CSS: `.gold-frame { max-width: 100%; }` and the stage grid gives the image column `1fr`; the image scales by height. Verify visually with `npm run dev` or the built app if a library is available; otherwise reason from CSS and note it in the report.
- [ ] **Step 6: Curated** — tiles are `<button type="button">` with `aria-pressed` for selection; hover no longer sets focus; keyboard focus + `X` removes from keepers via `deleteSwipe`; the per-tile heart becomes a small "Remove" icon button titled "Remove from keepers (X)"; score badge removed; subtitle `"{n} keepers · ready to edit & export"`; empty state: "No keepers yet. Press K on a photo in Review to keep it."
- [ ] **Step 7: KbdFooter** — cull chips exactly: K keep, X reject, U undo, ← → prev/next, Tab next burst, [ ] burst cycle, Z zoom, V compare, E auto edit, S straighten. Browse variant unchanged.
- [ ] **Step 8: Verify** — `npm run build` clean; grep the frontend for `silver`, `Like`, `useLikes`, `"f"`/`"F"` bindings and remove leftovers in the files this task owns. Commit `feat(ui): three-state verdict, single keyboard layer, review header`.

---

### Task 5: Copy, navigation, indexing pill, Stories/Collections/Search polish (frontend + tiny backend)

**Spec sections:** B3, B4.

**Files:**
- Modify: `frontend/src/components/Rail.tsx` (label Review, remove Calibrate, add `IndexingPill`), Create: `frontend/src/components/IndexingPill.tsx`, `frontend/src/hooks/useProgressSocket.ts` (shared reconnecting socket used by IndexingPill, Onboarding, ModelsCard, WatchCard)
- Modify: `frontend/src/views/Onboarding.tsx`, `frontend/src/lib/eta.ts` (`STAGE_LABELS`), `frontend/src/views/Stories.tsx`, `frontend/src/views/Clusters.tsx`, `frontend/src/views/ClusterDetail.tsx`, `frontend/src/views/Search.tsx`, `frontend/src/components/TagBrowser.tsx`, `frontend/src/components/TasteCard.tsx`, `frontend/src/views/Libraries.tsx`, `frontend/src/views/Map.tsx`, `frontend/src/views/Videos.tsx`, `frontend/src/views/Persons.tsx`, `frontend/src/views/PersonDetail.tsx`, `frontend/src/views/Dedup.tsx`, `frontend/src/views/Calibrate.tsx`, `frontend/src/views/CalibrateDashboard.tsx`, `frontend/src/components/Topbar.tsx`, `frontend/src/components/PageHeader.tsx`, `frontend/src/App.tsx`, `frontend/src/styles.css` (blocks other than the ones Task 4 owns)
- Modify: `frontend/src/views/BurstCull.tsx` only to change the `context="Review"` prop if Task 4 left it otherwise.

**Interfaces:**
- Consumes: `useToggleKeep` from `hooks/useKeep.ts` (Task 4); `/ws/progress` messages `{stage, current, total, message}`.
- Produces: `useProgressSocket(onMessage)` hook with backoff 1s/2s/4s/max 10s, paused while hidden.

- [ ] **Step 1: eta.ts** — export `STAGE_LABELS: Record<string,string>` with the spec B3 strings, keyed by stage id; Onboarding uses it (drop its local map). Add a tiny unit test if a runner exists; otherwise a type-level check that every `STAGE_SEQUENCE` id has a label (`satisfies`).
- [ ] **Step 2: Progress socket hook + pill** — implement `useProgressSocket`; refactor Onboarding, ModelsCard, WatchCard to use it; Onboarding Retry checks `/api/libraries/status` first (B4). `IndexingPill` in Rail above Library.
- [ ] **Step 3: Rail** — label "Review", remove Calibrate entry; Libraries page gets the "Advanced: calibrate the scoring model" link inside the settings `<details>`.
- [ ] **Step 4: Stories** — title/subtitle per B3; Strictness segmented control replaces both sliders; one `getLikedStatus` call for all visible stories (lift to the parent, pass a map down); hearts use `useToggleKeep`; strip nothing client-side (backend did it).
- [ ] **Step 5: Collections** — rename, tab labels/subtitles per B3, "Everything else" last; ClusterDetail breadcrumb "Collections".
- [ ] **Step 6: Search** — tag label formatting (`_` -> space) in chips, datalist and TagBrowser; empty-state copy; footer only when lightbox open.
- [ ] **Step 7: Curated taste card, Map fitBounds, Videos badge, breadcrumbs** — per B3/B4; `Topbar` context = page title on every page.
- [ ] **Step 8: Verify** — `npm run build`; grep for "Sort" (rail), "Narrative", "IQA", "HDBSCAN", "p75", "silver", "Like" in `frontend/src` and fix leftovers in owned files. Commit `feat(ui): plain-language labels, indexing pill, strictness control, collections`.

---

### Task 6: Frontend lint, Playwright e2e smoke, CI e2e matrix

**Spec sections:** A5.

**Files:**
- Create: `frontend/eslint.config.js`, `frontend/e2e/smoke.spec.ts`, `frontend/e2e/fixture.ts` (builds the temp library and starts the server), `frontend/playwright.config.ts`
- Modify: `frontend/package.json` (scripts `lint`, `e2e`; devDeps `eslint`, `@eslint/js`, `typescript-eslint`, `eslint-plugin-react-hooks`, `@playwright/test`), `frontend/package-lock.json`, `.github/workflows/ci.yml` (frontend job runs lint; new `e2e` job on ubuntu-latest + windows-latest), `README.md` (Development: `npm run e2e`)
- Modify: any `frontend/src` file ESLint flags (mechanical fixes only).

**Interfaces:**
- Consumes: CLI `selects index <dir> --pass index|classical`; `serve <dir> --no-browser --no-background --port 8765`; spec A5 assertions.

- [ ] **Step 1: ESLint** — flat config: `@eslint/js` recommended, `typescript-eslint` recommended, `react-hooks` rules; ignore `dist`, `e2e/**` not ignored. `npm run lint` clean after mechanical fixes.
- [ ] **Step 2: Playwright** — `playwright.config.ts` with `webServer` pointing at a small Node script (`e2e/fixture.ts` executed via `tsx`? no — keep it plain: a `globalSetup` that copies `tests/fixtures/small.jpg` 6 times into `os.tmpdir()/selects-e2e-<pid>`, runs the two `selects index` passes with the repo's Python (`process.env.SELECTS_PYTHON || "python"`), then `webServer.command` starts `serve` on port 8765 with `reuseExistingServer: false`). `smoke.spec.ts` asserts per spec A5 and fails on any console error.
- [ ] **Step 3: Static build for e2e** — the server serves `selects/server/static`; the e2e script (`npm run e2e`) runs `npm run build` and copies `dist` to `../selects/server/static` first (cross-platform via a small Node script `e2e/copy-static.mjs`).
- [ ] **Step 4: CI** — `frontend` job adds `npm run lint`; new `e2e` job matrix ubuntu-latest + windows-latest: setup python 3.11 + node 20, `pip install -e ".[dev]"`, `npm ci`, `npx playwright install chromium` (`--with-deps` on Linux), `npm run e2e` with `SELECTS_PYTHON=python`. Upload `frontend/test-results` on failure.
- [ ] **Step 5: Verify locally on Windows** — `npm run lint`, `npm run e2e` green with `SELECTS_PYTHON=../.venv/Scripts/python.exe`. Commit `test: eslint config, playwright smoke e2e, CI e2e on linux and windows`.
