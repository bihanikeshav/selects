"""Nested editorial budgets with shared, bounded redundancy ranking."""
from selects.selection import choose_diverse, coverage_keys

from .catalog import load_photos
from .reviews import load_reviews, review_digest, validate_selection
from .schema import SCHEMA_VERSION, SELECTED
from .storage import write_json


def shortlist(cfg, *, heroes=12, grades=40, story=80):
    if not 0 <= heroes <= grades <= story:
        raise ValueError("caps must be ordered: heroes <= grades <= story")
    catalog = load_photos(cfg)
    reviews = load_reviews(cfg, catalog)
    validate_selection(reviews, catalog)
    assignments = {}
    chosen = []
    for tier, cap, allowed in (("hero", heroes, {"hero"}),
                               ("grade", grades, {"hero", "grade"}),
                               ("story", story, SELECTED)):
        candidates = [r for r in reviews if r["decision"] in allowed and r["photo_id"] not in assignments]
        winners = choose_diverse(
            candidates, cap - len(chosen),
            score=lambda r: r["creative_score"] + (.12 * r["story_value"] if tier == "story" else 0),
            features=lambda r: coverage_keys(catalog[r["photo_id"]], r),
            identity=lambda r: r["photo_id"], previous=chosen, penalty=1.2)
        for review in winners:
            assignments[review["photo_id"]] = tier
        chosen.extend(winners)
    rows = []
    for review in sorted(reviews, key=lambda r: r["photo_id"]):
        original = review["decision"]
        decision = assignments.get(review["photo_id"], "maybe" if original in SELECTED else original)
        rows.append(dict(photo_id=review["photo_id"], decision=decision,
                         selection_note="" if decision == original else "Adjusted to the selection budget"))
    result = dict(schema_version=SCHEMA_VERSION, library=str(cfg.folder),
                  source_digest=review_digest(reviews, catalog),
                  caps=dict(heroes=heroes, grades_including_heroes=grades, story_total=story),
                  selection=rows)
    target = write_json(cfg, "shortlist.json", result)
    return dict(path=str(target), heroes=sum(t == "hero" for t in assignments.values()),
                grading_total=sum(t in {"hero", "grade"} for t in assignments.values()),
                story_total=len(chosen))
