# Selects audit repair v2 — spec

Source: repo audit of 2026-09-04 (engineering + UX), run against the live
Ladakh library (1,002 photos). This spec is the binding authority for the
plan `docs/superpowers/plans/2026-09-04-audit-repair-v2.md`.

## Goals

1. Fix the correctness/security defects the audit found.
2. Cut dead surface and split the 2,650-line route module.
3. Simplify the user-facing verdict model to keep / reject / undecided.
4. Replace engineer vocabulary on every screen with plain labels.
5. Prove the result in a sandbox on Linux and Windows (GitHub Actions
   runners + a fresh local venv), with an end-to-end browser smoke test.

## Global constraints

- Version becomes `0.1.14` in `pyproject.toml`, `selects/__init__.py`,
  `frontend/package.json`. `selects/server/app.py` reads the version from
  `selects.__version__` (no fourth literal).
- `pytest -q` and `ruff check selects tests` must both pass. CI runs both.
- `npm run build` and `npm run lint` (new) must pass in `frontend/`.
- No torch anywhere: workflows must not `pip install torch`.
- Behaviour is desktop-only (min window 1024x720); no responsive work.
- Keep existing API paths that the frontend uses; new paths listed below.
- Commit messages: conventional prefix (`fix:`, `feat:`, `refactor:`,
  `chore:`, `docs:`, `test:`), no emojis in docs or code.
- Do not touch `.superpowers/`, `screenshots/`, root PNG/HTML artefacts.

## A. Engineering requirements

### A1 Version, lint, deps, CI hygiene
- Single version source as above; bump to 0.1.14.
- `ruff check selects tests` clean (fix the 53 current findings; do not
  add `noqa` except for the intentional late imports in `selects/ml/*`
  where the import must be lazy; prefer `# noqa: E402` only there).
- Replace `datetime.utcnow` with a helper `selects.util.utcnow()` that
  returns a naive UTC `datetime` (`datetime.now(timezone.utc).replace(tzinfo=None)`),
  used by every model default and route that previously called `utcnow`.
- `pyproject.toml`: `opencv-python` -> `opencv-python-headless`; move
  `pywebview` into a new `desktop` extra; `[ml]` unchanged. Release and
  nuitka workflows install `.[ml,desktop]`. CI test job installs `.[ml,dev]`
  without torch. Add a `lint` job (`ruff check selects tests`).
- README: config table defaults must match `selects/config.py`
  (`burst_window_seconds` 12, `burst_similarity_threshold` 0.96); the
  privacy line must say the Map view loads OpenStreetMap tiles from the
  internet and geocoding is optional; Development section mentions
  `npm run lint` and the e2e smoke command.

### A2 Route module split and dead endpoints
- `selects/server/routes.py` is split into: `photos_routes.py` (photos
  list, moment lookup/primary, swipes, likes, curated, summary),
  `stories_routes.py`, `clusters_routes.py` (clusters, tags),
  `persons_routes.py` (persons, face_crop, merge, person photos),
  `images_routes.py` (thumb, preview, editor params/save/result, enhance,
  histogram), `calibrate_routes.py`, `map_routes.py` (map markers,
  curate, curate/facets), `editor_routes.py` (darktable launch/export,
  editor open, edits status). Shared pydantic models move to
  `selects/server/schemas.py`. `routes.py` keeps only a
  `register_routes(app, cfg)` that calls each module's register function,
  so `app.py` is unchanged in shape.
- Remove endpoints with no caller: `GET /api/doctor/issues`,
  `GET /api/calibrate/next`, `GET /api/search` (v1), `GET /api/moments`,
  `GET /api/photos/{sha256}/tags`. Remove `selects/ml/lowlight.py` only if
  nothing else imports it.
- New `DELETE /api/swipes/{sha256}`: removes the swipe row (404 if photo
  unknown, 200 `{ok: true, deleted: bool}`).
- `GET /api/swipes/summary` accepts `collapse=moments|none` (default
  `moments`) and counts over the same photo set `/api/photos` returns with
  that collapse, so "kept + rejected + undecided == total". `silver` counts
  as kept. `skip` counts as undecided.
- `POST /api/swipes/{sha}` keeps accepting `keep|reject|silver|skip` for
  compatibility.
- Stories: `compute_rank_threshold` result is cached per
  `(db_path, db_mtime_ns, pct_floor, ap_w, nima_w)` in a module-level dict;
  `list_stories` calls it once per request, not once per story.
- Persons: `list_persons` fetches best faces with one query (group by
  person) instead of one query per person.
- Stories title suffixes: a visit name like `Leh (2)` produced by
  geocoder disambiguation must not appear in story titles or breadcrumbs;
  strip a trailing ` (N)` when rendering titles.

### A3 LAN token, websocket auth, shutdown
- When `SELECTS_LAN_TOKEN` is set and the bind host is non-loopback:
  - the middleware also accepts a cookie `selects_token`;
  - a request carrying a valid `?token=` sets that cookie
    (`HttpOnly`, `SameSite=Lax`, path `/`) on the response;
  - `/ws/progress` rejects non-loopback clients that present neither a
    valid `token` query param nor the cookie (close code 4401).
- `main.tsx` no longer monkeypatches `fetch`; it only strips `?token=`
  from the URL after load (the cookie carries auth).
- On app shutdown the lifespan calls `manager.request_cancel()` before
  cancelling tasks so an in-flight index stops at its next checkpoint.

### A4 Pruning and cascades
- `index_folder` (full walk only, not the `paths=` subset) deletes Photo
  and Video rows whose path no longer exists on disk, and removes their
  thumb/preview files when no other row shares the sha256. Progress
  message: `"{n} missing file(s) removed"` when n > 0. Returns unchanged
  (count of new rows).
- Alembic migration adds `ON DELETE CASCADE` to: `swipes.photo_id`,
  `photo_persons.photo_id`, `photo_persons.person_id`,
  `photo_persons.face_embedding_id`, `photo_edits.photo_id`,
  `moments.primary_photo_id`; `ON DELETE SET NULL` to
  `visits.cover_photo_id`, `persons.cover_face_embedding_id`. Uses batch
  mode. Models updated to match. `tests/test_migrations.py` covers
  upgrade from the current head with existing rows.

### A5 Frontend tooling and e2e
- ESLint flat config with `typescript-eslint` recommended +
  `eslint-plugin-react-hooks`; `npm run lint` clean.
- Playwright smoke test in `frontend/e2e/smoke.spec.ts`, run by
  `npm run e2e`. Fixture: a temp library of 6 copies of
  `tests/fixtures/small.jpg` with distinct names, indexed by
  `selects index <dir> --pass index` then `--pass classical` (no ML).
  Server started by the test runner via `python -c "from selects.cli import main; main()" serve <dir> --no-browser --no-background --port 8765`.
  Asserts: `/cull` shows a photo and the counter reads `1 of 6`; pressing
  `x` advances and the summary shows `1 rejected`; `/libraries` lists the
  library; `/search` renders its empty state; `/people` renders its empty
  state; `/map` renders the "no GPS" message; no console errors on any of
  those pages.
- CI job `e2e` runs on `ubuntu-latest` and `windows-latest`: installs
  `.[dev]` (no ML), `npm ci`, `npx playwright install chromium`
  (`--with-deps` on Linux), builds the frontend into
  `selects/server/static`, runs `npm run e2e`.

## B. UX requirements

### B1 Verdict model
- Exactly three user verdicts: **keep**, **reject**, **undecided**.
  "Silver", "like" and "skip" disappear from all UI copy and shortcuts.
  Curated = photos with decision `keep` or `silver` (compat).
- Keyboard in Review: `K` keep, `X` reject, `ArrowLeft/ArrowRight`
  prev/next, `U` undo, `Z` zoom, `V` compare select, `Enter` open
  compare, `Tab` next burst, `[`/`]` burst cycle, `E` auto-edit,
  `S` straighten. No other letter bindings. One keyboard layer
  (`useCullKeys`) owns all of them; `BurstCull` has no inline keydown
  listener.
- Undo restores the previous decision if one was made this session,
  otherwise calls `DELETE /api/swipes/{sha}`.
- Curated tile action is "Remove from keepers" (calls DELETE). Tiles are
  `<button>` elements; the `X` key removes the focused tile (focus, not
  hover).
- Stories per-frame heart becomes a keep toggle using the same
  `useToggleKeep` hook (renamed from `useToggleLike`).

### B2 Review screen (route `/cull`)
- Rail label "Sort" becomes "Review"; page title "Review"; breadcrumb
  context "Review".
- Subtitle: `Photo {i} of {total} · {kept} kept · {rejected} rejected · {undecided} to review`,
  where all four numbers come from `/api/swipes/summary?collapse=moments`
  and `total` equals the list total.
- Header: quality chips move into the actions row as a single
  `<select>` labelled "Show" (All / Underexposed / Overexposed /
  Out of focus / Soft but good); the "Sort:" text label is removed and the
  three buttons get `aria-label="Sort order"`. Header height <= 150px.
- The stage image may use the full width between the rail and the side
  panel (`max-width: 100%`), letterboxed by height only.
- The "Gold pick · photo N" stamp is removed; inside a burst the stamp
  reads `Burst · {i} of {n}`.
- Footer chips list only the bindings in B1.
- Empty state links to `/libraries`.
- The swipe-summary poll interval becomes 15s and stops when the tab is
  hidden (`document.visibilityState`).

### B3 Copy and labels
- Onboarding stage list shows: Scanning photos, Skimming videos,
  Checking focus and exposure, Understanding each photo, Scoring looks,
  Tagging scenes, Sorting by subject, Labelling objects, Grouping similar
  shots, Finding faces, Grouping people, Grouping bursts, Building
  stories, Building collections, Grouping by day (same order as
  `DEFAULT_STAGE_ORDER`). Source of truth: a `STAGE_LABELS` map exported
  from `frontend/src/lib/eta.ts` next to `STAGE_SEQUENCE`.
- Stories: page title "Stories"; subtitle
  `{n} days · best shots of each day, one per burst`; the two percentile
  sliders become one "Strictness" segmented control with Relaxed /
  Balanced / Strict mapping to `(scope_pct, library_pct)` =
  (60, 40) / (75, 50) / (90, 65); Balanced is default.
- Clusters: title "Collections"; tabs Places / Scenes / Days / Themes /
  Sessions with subtitles: "Named places from your trip", "What the
  photo is of", "One collection per day", "Visual themes across the whole
  trip", "Tight groups from one shooting session". `Uncategorized` sorts
  last and is labelled "Everything else".
- Search: tag chips render `_` as a space; the empty state reads
  "Type a place, a scene or a moment. Try 'monastery courtyard' or 'snow
  on the pass'."; the browse footer is shown only when the lightbox is
  open.
- Curated: the score badge on tiles is removed; taste card copy: when
  weight is 0 say "Trained on {n} decisions · not shaping your picks yet";
  when weight > 0 say "Trained on {n} decisions · nudging your picks by up
  to {w}%". Never say "knows your taste well".
- Calibrate leaves the rail; `/libraries` gets a link "Advanced: calibrate
  the scoring model" under the settings details. Route unchanged.
- Breadcrumb context equals the page title on every page (People, Map,
  Videos, Duplicates, Libraries, Search, Curated, Review, Stories,
  Collections, Calibrate).
- Videos: a video flagged dead footage that still has highlights shows
  "Mostly static" instead of "DEAD FOOTAGE".

### B4 Indexing visibility
- A global indexing pill in the rail (above Library) subscribes to
  `/ws/progress`, shows `Indexing · {pct}%` (or the stage blurb when total
  is 0), links to `/libraries`, and hides on `done`/`cancelled`/no run.
  It reconnects with backoff (1s, 2s, 4s, max 10s) while the page is
  visible.
- Onboarding "Retry" checks `/api/libraries/status`; if `indexing` is
  true it only reconnects the socket and does not POST index again.
- Map fits bounds to the marker set on load (padding 40px), not a fixed
  zoom.

## Acceptance

- All A and B items have a test or an e2e assertion where feasible; the
  rest are verified by the final review against screenshots.
- CI (ubuntu + windows) green on the branch: test, lint, frontend, e2e.
- A fresh local venv on Windows (`.[dev]`) runs `pytest -q` and the e2e
  smoke green.
