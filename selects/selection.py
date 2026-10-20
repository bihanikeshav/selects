"""Deterministic quality ranking with an optional redundancy penalty."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeVar

T = TypeVar("T")
Coverage = frozenset[tuple[str, str]]


def coverage_keys(photo: dict, review: dict | None = None) -> Coverage:
    annotations = review or {}
    keys = set()
    for axis, value in (
        ("category", annotations.get("scene_type") or photo.get("category")),
        ("place", annotations.get("location")),
        ("subject", annotations.get("subject")),
        ("composition", annotations.get("composition_key")),
    ):
        if value and value != "unclassified":
            keys.add((axis, str(value).casefold()))
    for story in photo.get("story_ids", []):
        keys.add(("story", str(story)))
    if photo.get("taken_at"):
        keys.add(("day", photo["taken_at"][:10]))
    if not annotations.get("location") and photo.get("gps_lat") is not None and photo.get("gps_lon") is not None:
        keys.add(("place", f'{photo["gps_lat"]:.2f},{photo["gps_lon"]:.2f}'))
    keys.update(("person", str(pid)) for pid in photo.get("person_ids", []))
    return frozenset(keys)


def choose_diverse(items: Iterable[T], limit: int, *, score: Callable[[T], float],
                   features: Callable[[T], Coverage], identity: Callable[[T], int],
                   previous: Iterable[T] = (), penalty: float = .12) -> list[T]:
    """Greedy maximal marginal relevance using set overlap.

    Similarity is in [0, 1], so metadata can change a ranking by at most
    penalty. Missing metadata never earns a coverage bonus.
    """
    if limit <= 0:
        return []
    remaining = {identity(item): item for item in items}
    scores = {pid: score(item) for pid, item in remaining.items()}
    if penalty == 0:
        ranked = sorted(remaining, key=lambda pid: (-scores[pid], pid))
        return [remaining[pid] for pid in ranked[:limit]]

    cached = {pid: features(item) for pid, item in remaining.items()}
    overlaps = dict.fromkeys(remaining, 0.0)

    def update_overlaps(reference):
        for pid in remaining:
            own = cached[pid]
            intersection = len(own & reference)
            union = len(own) + len(reference) - intersection
            similarity = intersection / union if union else 0.0
            overlaps[pid] = max(overlaps[pid], similarity)

    for item in previous:
        update_overlaps(features(item))
    output = []
    while remaining and len(output) < limit:
        winner = max(remaining, key=lambda pid: (scores[pid] - penalty * overlaps[pid], -pid))
        item = remaining.pop(winner)
        output.append(item)
        if len(output) < limit:
            update_overlaps(cached[winner])
    return output
