# Aesthetic-driven curation for Stories and "Best of X"

**Status**: Design accepted on 2026-05-24. Implementation pending.
**Owner**: ankit
**Related**: [2026-05-23-selects-design.md](2026-05-23-selects-design.md)

## Goal

A given photo library should produce, automatically:

- **Stories**: one per place or per day (user toggle), each showing the best photos that completely describe that scope, without near-duplicate burst clutter.
- **"Best of X" facets**: filtered curated sets — best of `person:Ankit`, best of `place:Pangong Tso`, best of `day:2026-04-04`, best landscapes, best portraits, best objects.

Both share the same underlying selection rules. They only differ in scope and grouping.

## Aesthetic signal

A single per-photo score: **`combined_aesthetic = 0.6 × AP_V2.5 + 0.4 × NIMA`**.

- Both component scores are already computed and stored in `aesthetic_scores`.
- Both score on a roughly 1–10 scale, so the weighted sum is on the same scale.
- AP V2.5 carries more weight because it's the stronger signal on this kind of content (travel/landscape/composition).
- The CLIP-IQA score on `Embedding.aesthetic_iqa` is **deprecated for curation**; it stays in the DB for historical comparison.

The personalized aesthetic model (one-class SigLIP centroid) is explored as a future improvement but **deferred**. See "Deferred: personal aesthetic model" below.

## Selection pipeline (applies to Stories and to every "Best of X")

Input: a *scope* — a set of photos to curate. Examples: photos on day 2026-04-04, photos at Pangong Tso, photos containing person id 7.

```
1. Compute combined_aesthetic for every scope photo.
2. Take the top 25% of the *library*-wide combined_aesthetic
   distribution. (Not top-25% within scope — a thin day with no
   keepers should show nothing, not its top quarter of mediocre shots.)
3. Burst-dedup within the surviving set:
     • Group survivors by moment_id (existing Moment table from
       selects.ml.moments).
     • For each moment with 2+ survivors, keep only the highest
       combined_aesthetic member; drop the rest.
     • Survivors with moment_id = NULL pass through unchanged.
4. Order results by taken_at ascending for Stories,
   by combined_aesthetic descending for "Best of X" facets.
```

### Why this shape

- **Library-wide threshold, not scope-relative.** A thin day with only mediocre photos should produce a short story or empty result, not pad to 25% of mediocrity. The library-wide top-25% is the user's curation bar.
- **Burst dedup gated by survival.** Photos in a burst that don't even cross the top-25% threshold are gone before dedup runs, so dedup never has to compare two bad shots. Only when ≥2 shots from the same burst pass the bar does the system pick the best one.
- **Reuses existing moments.** No new clustering needed; `MomentMember` rows give the burst groups for free.

## Story mode

The existing Stories view (`frontend/src/views/Stories.tsx`) currently shows every photo per story. After this change, each story shows only the dedup'd, aesthetic-filtered photos.

- **Group by**: day or place (user-toggle, already present as "By day / By place").
- **People / Pattern groupings**: out of scope for this spec; they collapse into "Best of person X" facets.
- **Empty-day behavior**: if a day has 0 keepers, hide the story rather than showing an empty placeholder.
- **Filter chips**: removed. The Stories view no longer needs the 50+ RAM/cluster tag chips. Replace with the small facet selector described below.

## "Best of X" facet system

A single curated-set view, parameterized by a filter:

| Facet            | Scope filter                                                  |
| ---------------- | ------------------------------------------------------------- |
| `by-person:N`    | photos where person N appears (PhotoPerson table)             |
| `by-day:YYYY-MM-DD` | photos taken on that date                                 |
| `by-place:NAME`  | photos in a Visit with that name                              |
| `by-landscape`   | photos classified as landscape (SigLIP probe, see below)      |
| `by-portrait`    | photos classified as portrait                                 |
| `by-object`      | photos classified as object / still-life                      |

Each facet renders a single grid sorted by `combined_aesthetic` descending, with the same top-25% + burst-dedup pipeline applied.

### Landscape / portrait / object classification

We don't have RAM tags computed yet (`photo_tags` source='ram' is empty). Approach: **SigLIP zero-shot probes**, computed once per library, stored on a new `PhotoCategory` table.

Probes (initial vocabulary, can be tuned):
- `"a landscape photograph of mountains, sky, or open scenery"` → `landscape`
- `"a portrait photograph of one or more people"` → `portrait`
- `"a close-up photograph of an object, food, or still life"` → `object`

For each photo: compute cosine similarity of its SigLIP embedding to each probe (we already have both image embeddings and a text encoder). Store all three sims; classify by argmax with a minimum-confidence gate.

`PhotoCategory`:
```
photo_id        PK (FK photos.id)
landscape_sim   float
portrait_sim    float
object_sim      float
primary_category text   -- "landscape" | "portrait" | "object" | "unclassified"
```

The classification is library-wide and one-shot (re-run when new photos arrive). The "Best of [landscape/portrait/object]" views filter on `primary_category`.

## UX surface

- **Stories page** keeps its day/place tab toggle, drops the tag chips, adds a small "**Best of**" dropdown next to it that opens a new route.
- **`/best/:facet/:value`** route hosts the facet grid (e.g. `/best/person/7`, `/best/place/Pangong+Tso`, `/best/landscape/all`).
- **Person detail page** gets a "Best of [name]" button that links into `/best/person/<id>`.
- **Place detail / map view** gets a "Best of [place]" link similarly.

## Backend changes

1. **New endpoint `GET /api/curate/scope`** — accepts `?day=` or `?place=` or `?person_id=` or `?category=landscape|portrait|object`, returns the dedup'd top-25% sorted set.
2. **New endpoint `POST /api/categories/compute`** — runs the SigLIP probe pass over photos missing a `PhotoCategory` row.
3. **`/api/stories` updated** — replace the existing per-story photo list with the dedup'd top-25% pipeline output.
4. **`/api/tags` and tag-chip params** — deprecated; the frontend stops calling these on Stories.

## Frontend changes

1. **`Stories.tsx`**: remove `FilterChipBar`, remove tag fetching, add a "Best of" facet selector linking to `/best/:facet/:value`.
2. **New `BestOf.tsx`** view: takes facet + value from route params, fetches `/api/curate/scope`, renders a single grid with the lightbox + edit-in-darktable bulk action.
3. **`PersonDetail.tsx` / `Map.tsx`**: add "Best of" links.

## Deferred: personal aesthetic model

We built and evaluated a one-class personalized model:

- **Method**: collected user 👍/👎 ratings via `/calibrate`, then computed the unit-vector centroid of upvoted SigLIP embeddings. `personal_score` for any photo = cosine similarity to the centroid. Stored in `AestheticScore.personal_score`.
- **Result on the Ladakh library** (235 upvotes, 1002 photos):
  - Median percentile rank of upvotes under NIMA+AP combined: **47**
  - Median percentile rank of upvotes under personal: **67** (+20 pts)
  - Recall@100 of upvotes in top-100: **NIMA+AP 0.0%**, personal 15.3%.
- **Why deferred**: the model demotes some genuinely good photos in the "biggest drops" sanity check, suggesting it's overfitting to specific stylistic features of the upvote set rather than learning a clean "good travel photo" boundary. Needs more work — likely a richer architecture (small MLP, regularization toward NIMA+AP), a held-out validation set, or fewer-but-cleaner labels.
- **What stays in the codebase**:
  - `aesthetic_scores.personal_score` column (kept; nullable).
  - `photo_ratings` table (kept; the 600+ ratings are preserved on disk and on the `index.db.backup-*` snapshot).
  - `/api/calibrate/*` endpoints (kept; available for future iteration).
  - `/calibrate` and `/calibrate/dashboard` views (kept; not linked from primary UX but reachable).
- **What does not depend on it**: the curation pipeline above uses **only** `0.6 × AP_V2.5 + 0.4 × NIMA`. Personal score is currently unused.

A revisit is worth doing once we have either (a) more rating signal across a broader percentile range, or (b) a held-out test set to gate the model on.

## Tunable constants (initial values, calibrated empirically)

| Name           | Initial value | Where set        | Notes                              |
| -------------- | ------------- | ---------------- | ---------------------------------- |
| `AP_WEIGHT`    | 0.6           | `cfg`            | weight for AP V2.5 in combined     |
| `NIMA_WEIGHT`  | 0.4           | `cfg`            | weight for NIMA in combined        |
| `AESTHETIC_PCT_FLOOR` | 75     | `cfg`            | top-25% gate; >= p75 keeps the photo |
| `PROBE_MIN_SIM` | 0.18         | `cfg`            | min cosine sim to assign category  |

All four are config-only and tunable per-folder via `SELECTS_*` env vars.

## Now in scope (added 2026-05-24)

- **Natural-language semantic search on Stories.** Single text input above the story list; SigLIP-encoded query, stories ranked by their best-photo cosine similarity to the query, threshold filters out non-matching stories. Live as you type (debounced).
- **Person identity labeling UI.** PersonDetail page gets an inline editable label. PATCH `/api/persons/{id}` already exists; just needs a UI that calls it.
- **Pattern-based grouping collapses into landscape/portrait/object facets.** The "By pattern" tab in Stories is removed; users access these via the Best-Of dropdown.
- **Video curation** — out of pragmatic scope for this spec (video indexing isn't built in M1); revisit when M3 ships.

## Locked decisions (2026-05-24)

- **Aesthetic gate**: library-wide top 25% by `combined_aesthetic`, **tunable per folder** via `SELECTS_AESTHETIC_PCT_FLOOR`. No auto-target-story-length.
- **Best-Of facets are pure ranked grids** — no chronological mini-stories. Each grid sorts by `combined_aesthetic` descending.
