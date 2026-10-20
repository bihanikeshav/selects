"""Review archives, source validation and exportable editorial reports."""
import csv
import hashlib
import io
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from selects.indexer.walker import sha256_of

from .catalog import load_photos
from .schema import DECISIONS, SCHEMA_VERSION, SELECTED, CreativeReview, ReviewInput
from .storage import read_json, write_json, write_text


def _identity(review, catalog):
    current = catalog.get(review.photo_id)
    if current is None:
        raise ValueError(f"unknown photo_id: {review.photo_id}")
    if current["filename"] != review.filename:
        raise ValueError(f"filename mismatch: photo {review.photo_id}")
    if review.sha256 is not None and current["sha256"] != review.sha256:
        raise ValueError(f"photo {review.photo_id} changed since review")


def verify_sources(cfg, reviews, photos):
    """Verify the actual reviewed bytes before any rating sidecars are written.

    This runs only for an explicit export apply, not candidate generation,
    reports, shortlists, or routine reindexing.
    """
    for review in reviews:
        pid = review["photo_id"]
        path = Path(photos[pid]["path"])
        if not path.resolve().is_relative_to(cfg.folder):
            raise ValueError(f"photo {pid} is outside the library")
        expected = review.get("sha256")
        if not expected:
            raise ValueError(f"photo {pid} has no source hash; reindex and review it again")
        try:
            actual = sha256_of(path)
        except OSError as exc:
            raise ValueError(f"photo {pid} cannot be verified; reindex before exporting") from exc
        if actual != expected:
            raise ValueError(f"photo {pid} changed on disk; reindex and review it again")


def validate_selection(reviews, photos):
    groups = defaultdict(list)
    for review in reviews:
        if review["decision"] not in SELECTED:
            continue
        source = photos[review["photo_id"]]
        if source["sha256"]:
            groups[("hash", source["sha256"])].append(review)
        if source["moment_id"] is not None:
            groups[("moment", source["moment_id"])].append(review)
    for (kind, key), members in groups.items():
        if kind == "hash":
            if len(members) > 1:
                raise ValueError("selected photos contain exact duplicates")
        elif len(members) > 2:
            raise ValueError(f"Moment {key} has more than two selections")
        elif len(members) == 2:
            justified = any(r.get("moment_exception") and r.get("moment_exception_reason") for r in members)
            if not justified:
                raise ValueError(f"Moment {key} requires moment_exception")


def load_reviews(cfg, photos, *, replaced_ids=None):
    document = read_json(cfg, "reviews.json")
    if document is None:
        return []
    if document.get("schema_version") != SCHEMA_VERSION or document.get("library") != str(cfg.folder):
        raise ValueError("review archive belongs to an incompatible schema or library")
    output = {}
    for entry in document["reviews"]:
        payload = {k: v for k, v in entry.items() if k not in {"created_at", "updated_at"}}
        validated = CreativeReview.model_validate(payload)
        pid = validated.photo_id
        if pid in output:
            raise ValueError(f"duplicate photo_id in saved reviews: {pid}")
        if pid not in (replaced_ids or ()):
            _identity(validated, photos)
        output[pid] = dict(validated.model_dump(),
                           created_at=entry["created_at"], updated_at=entry["updated_at"])
    return [output[pid] for pid in sorted(output)]


def import_reviews(cfg, source: Path, *, replace=False):
    raw = json.loads(source.read_text(encoding="utf-8"))
    batch = ReviewInput.model_validate(dict(reviews=raw) if isinstance(raw, list) else raw)
    if batch.library is not None and Path(batch.library).expanduser().resolve() != cfg.folder:
        raise ValueError("import belongs to another library")
    incoming = {}
    for review in batch.reviews:
        if review.photo_id in incoming:
            raise ValueError(f"duplicate photo_id in import: {review.photo_id}")
        incoming[review.photo_id] = review
    catalog = load_photos(cfg)
    for review in incoming.values():
        _identity(review, catalog)
    archive = {} if replace else {
        row["photo_id"]: row for row in load_reviews(cfg, catalog, replaced_ids=set(incoming))}
    timestamp = datetime.now(timezone.utc).isoformat()
    for pid, review in incoming.items():
        created = archive.get(pid, {}).get("created_at", timestamp)
        archive[pid] = dict(review.model_dump(), sha256=catalog[pid]["sha256"],
                            created_at=created, updated_at=timestamp)
    merged = [archive[pid] for pid in sorted(archive)]
    validate_selection(merged, catalog)
    destination = write_json(cfg, "reviews.json",
                             dict(library=str(cfg.folder), schema_version=SCHEMA_VERSION, reviews=merged))
    return dict(imported=len(incoming), total_reviewed=len(merged), path=str(destination))


def review_digest(reviews, photos):
    # Canonical order makes fingerprints insensitive to caller dictionary order.
    records = sorted(reviews, key=lambda r: r["photo_id"])
    payload = dict(reviews=records, sources={str(r["photo_id"]): photos[r["photo_id"]] for r in records})
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def effective_reviews(cfg, photos):
    nominations = load_reviews(cfg, photos)
    shortlist = read_json(cfg, "shortlist.json")
    warnings = []
    if shortlist is not None:
        if shortlist.get("library") != str(cfg.folder) or shortlist.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("shortlist schema/library mismatch")
        if shortlist.get("source_digest") != review_digest(nominations, photos):
            warnings.append("Shortlist is stale. Refresh it before exporting selections.")
        else:
            assignments = {}
            for item in shortlist["selection"]:
                pid = item["photo_id"]
                if pid in assignments:
                    raise ValueError("duplicate photo_id in shortlist")
                if item["decision"] not in DECISIONS:
                    raise ValueError("unknown shortlist decision")
                assignments[pid] = item
            if set(assignments) != {r["photo_id"] for r in nominations}:
                raise ValueError("shortlist does not match reviewed photos")
            nominations = [
                dict(row, review_decision=row["decision"],
                     decision=assignments[row["photo_id"]]["decision"],
                     selection_note=assignments[row["photo_id"]].get("selection_note", ""))
                for row in nominations
            ]
    validate_selection(nominations, photos)
    return nominations, warnings


def _csv_cell(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _markdown(rows, indexed, warnings):
    lines = ["# Trip Selection", "", f"Reviewed {len(rows)} of {indexed} photos.", "",
             "Creative scores are preview judgments, not measurements of RAW latitude.", ""]
    lines.extend(warnings)
    for tier in DECISIONS:
        lines.extend(["", f"## {tier.title()}", ""])
        for row in rows:
            if row["decision"] == tier:
                lines.extend([f'### {row["filename"]} (photo {row["photo_id"]})', "",
                              f'Creative score: {row["creative_score"]:.2f}', "",
                              f'Source: {row["path"]}', "", row["reason"], "",
                              row["grade_direction"] or "No grading direction supplied.", ""])
                if row.get("selection_note"):
                    lines.append(row["selection_note"])
    return "\n".join(lines) + "\n"


def report(cfg):
    catalog = load_photos(cfg)
    reviews, warnings = effective_reviews(cfg, catalog)
    rows = []
    for review in reviews:
        source = catalog[review["photo_id"]]
        row = dict(review)
        row.update({key: source[key] for key in
                    ("path", "preview_path", "selects_score", "moment_id", "story_id", "taken_at")})
        rows.append(row)
    rows.sort(key=lambda r: (DECISIONS.index(r["decision"]), -r["creative_score"], r["photo_id"]))
    counts = Counter(r["decision"] for r in rows)
    summary = {tier: counts[tier] for tier in DECISIONS}
    summary.update(indexed=len(catalog), reviewed=len(rows), unreviewed=len(catalog) - len(rows))
    document = dict(schema_version=SCHEMA_VERSION, library=str(cfg.folder),
                    generated_at=datetime.now(timezone.utc).isoformat(),
                    reviews=rows, summary=summary, warnings=warnings)
    outputs = [write_json(cfg, "creative_results.json", document)]
    columns = list(CreativeReview.model_fields)
    columns += ["selects_score", "path", "preview_path", "moment_id", "story_id", "taken_at",
                "created_at", "updated_at", "review_decision", "selection_note"]
    buffer = io.StringIO(newline="")
    table = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    table.writeheader()
    table.writerows({k: _csv_cell(v) for k, v in row.items()} for row in rows)
    outputs.append(write_text(cfg, "creative_results.csv", buffer.getvalue()))
    outputs.append(write_text(cfg, "selection.md", _markdown(rows, len(catalog), warnings)))
    return dict(summary=summary, warnings=warnings, paths=[str(path) for path in outputs])
