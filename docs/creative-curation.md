# Creative review

Selects can prepare a broad set of candidates for an editorial pass using cached
previews. Review judgments stay separate from machine scores and keeper decisions.
No additional model assigns composition, story value or editing potential.

## Commands

```bash
selects creative status /path/to/library
selects creative candidates /path/to/library --percentile 50 --batch-size 50
selects creative schema
selects creative import /path/to/library reviewed-batch.json
selects creative shortlist /path/to/library --heroes 12 --grades 40 --story 80
selects creative report /path/to/library
selects creative xmp /path/to/library --dry-run
```

Use `python -m selects` in place of `selects` when running from a checkout.

## Candidates

Run indexing first. Candidate generation reads the existing index; it does not
decode originals or download models. Missing or unsafe previews are reported
with a null preview path so they can be repaired through the index pass.

The percentile is a lower bound on machine quality: 50 normally retains the
top half of scored eligible photographs. Unscored photos remain reviewable.
Retention rules additionally protect explicit keeps, burst representatives,
top quality/sharpness examples, unusual lighting, and optional story/scene
coverage. Technical rejects are excluded unless explicitly kept; manual rejects
are excluded. These rules can produce more candidates than the percentile alone.

Use `--include-maybe` to include every eligible photograph, `--moment ID` for
burst alternatives, or `--story ID` for a story. `--no-include-story` disables
story/scene retention.

`--max-candidates N` preserves protected entries and distributes remaining slots
across stories or dates. A cap smaller than the protected set fails explicitly.
Discretionary identical copies are collapsed using stored hashes. Protected
copies remain available for comparison.

Batch size is 30–80, default 50. Ordinary bursts stay together. Oversized bursts
are split into adjacent batches with `split_moment: true`. The manifest contains
the full candidate metadata and batch IDs. `--batch N` prints only that batch
while saving the complete manifest. Scoped manifests use distinct filenames.

## Review format

Import an array or an object containing `schema_version: 1`, optional `library`,
and `reviews`. Run `creative schema` for the complete field definitions.

Each review identifies `photo_id` and `filename`; include `sha256` to verify
the source. Required numerical fields are `technical_score`, `composition`,
`light`, `moment`, `story_value`, `uniqueness`, and `grading_potential`.
Scores are finite numbers from 0 to 10. Numeric strings and booleans are invalid.
Required text includes `reason` and `grade_direction`; selected photos need a
nonempty grading direction.

Decisions are `hero`, `grade`, `story`, `maybe`, or `reject`. The creative
score is calculated as:

```text
composition × .22 + light × .18 + moment × .15
+ grading_potential × .20 + story_value × .10 + uniqueness × .15
```

An explicit creative score must agree with that calculation. Technical score is
kept separate. An observed `technical_failure: true` prevents a selected
decision. Preview appearance does not establish measured RAW recovery latitude.

Optional annotations describe scene type, location, subject and composition.
Normally nominate one frame per burst. A second needs a justified
`moment_exception`; three selected siblings are refused. Identical copies
cannot both be nominated for a selected tier.

Imports validate the entire merged set before replacing saved state. Updates
preserve creation timestamps and bind the review to the current source hash.
Changed or missing sources invalidate saved judgments until they are corrected.
`--replace` replaces the review archive with a complete supplied set.

## Shortlists and reports

Caps are nested: grades includes heroes, and story includes both. A shared
selection engine ranks quality with a bounded metadata-overlap penalty to
reduce repeated scenes and subjects. It uses only supplied nominations and
never promotes maybe/reject to fill unused slots. The shortlist is temporary
selection state; original review judgments remain intact.

Review or index changes invalidate a saved shortlist. Reports warn and fall
back to current nominations; applying ratings requires a refreshed shortlist.

Files are stored inside `<library>/.selects/creative/`:

- `reviews.json`: authoritative judgments.
- `candidates.json`: candidate manifest; scoped requests add a filename suffix.
- `shortlist.json`: budgeted decisions and their source fingerprint.
- `creative_results.json`, `creative_results.csv`, `selection.md`: reports.

CSV output preserves text and protects formula-like cells. Unreviewed photos
remain unreviewed. State files are replaced atomically. Keep a single sequential
review writer per library.

## Original-file preservation

Creative review never deletes, renames or changes original image bytes.
XMP export defaults to a preview plan. Use `creative xmp <library> --apply`
to write ratings: hero 5, grade 4, story 3, maybe 2, reject 1.

Applying ratings verifies the content hash of every reviewed original before
writing any sidecar. Changed or missing files stop the export; reindex and
review changed sources before retrying. This reads reviewed originals during
apply only. Candidate generation, reports and dry runs continue using the index.

RAW ratings use `stem.xmp`; other stills use `stem.ext.xmp`, keeping RAW/JPEG
pair ratings separate. Existing sidecar metadata is preserved. Higher ratings
are retained unless `--force` is supplied. Linked targets, unsupported formats,
outside-library paths and colliding destinations are rejected.

The existing general export feature retains its separate behavior. These
original-file guarantees apply to the creative sidecar command.
