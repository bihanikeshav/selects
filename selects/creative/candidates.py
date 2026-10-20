"""Review pool policies, grouped batches and protected candidate limits."""
import math
from collections import defaultdict, deque
from datetime import datetime, timezone

import numpy as np

from selects.util import KEEP_DECISIONS

from .catalog import load_photos
from .schema import SCHEMA_VERSION
from .storage import check_state

__all__ = ["load_photos", "check_state", "generate_candidates", "make_batches"]


def _priority(photo):
    return (photo["user_decision"] in KEEP_DECISIONS, photo["moment_primary"],
            -1 if photo["selects_score"] is None else photo["selects_score"],
            -1 if photo["sharpness"] is None else photo["sharpness"], -photo["photo_id"])


def _groups(photos, keys):
    result = defaultdict(list)
    for photo in photos:
        for key in keys(photo):
            result[key].append(photo)
    return result.values()


def make_batches(candidates, batch_size=50):
    if not 30 <= batch_size <= 80:
        raise ValueError("batch_size must be between 30 and 80")
    groups = list(_groups(candidates, lambda p: [("moment", p["moment_id"])
                  if p["moment_id"] is not None else ("photo", p["photo_id"])]))
    groups.sort(key=lambda group: (group[0]["story_id"] or 0,
                                  min(p["taken_at"] or "" for p in group), group[0]["photo_id"]))
    packets = []
    for group in groups:
        ordered = sorted(group, key=lambda p: (not p["moment_primary"],
                                              p["moment_rank"] or 0, p["photo_id"]))
        if len(ordered) > batch_size:
            packets.extend((ordered[start:start + batch_size], True)
                           for start in range(0, len(ordered), batch_size))
        else:
            packets.append((ordered, False))
    batches = []
    for packet, split in packets:
        if not split and batches and not batches[-1]["split_moment"] and len(batches[-1]["photo_ids"]) + len(packet) <= batch_size:
            batches[-1]["photo_ids"].extend(p["photo_id"] for p in packet)
        else:
            batches.append(dict(batch_id=len(batches) + 1,
                                photo_ids=[p["photo_id"] for p in packet], split_moment=split))
    return batches


def generate_candidates(cfg, *, percentile=50, max_candidates=None, include_story=True,
                        include_maybe=False, batch_size=50, moment_id=None, story_id=None):
    if not math.isfinite(percentile) or not 0 <= percentile <= 100:
        raise ValueError("percentile must be between 0 and 100")
    if max_candidates is not None and max_candidates < 1:
        raise ValueError("max_candidates must be positive")
    catalog = load_photos(cfg)
    eligible = [p for p in catalog.values()
                if (p["user_decision"] in KEEP_DECISIONS or
                    (p["user_decision"] != "reject" and not p["auto_reject"]))
                and (moment_id is None or p["moment_id"] == moment_id)
                and (story_id is None or story_id in p["story_ids"])]
    scores = [p["aesthetic"] for p in eligible if p["aesthetic"] is not None]
    sharpness = [p["sharpness"] for p in eligible if p["sharpness"] is not None]
    floor = float(np.percentile(scores, percentile)) if scores else math.inf
    top_quality = float(np.percentile(scores, 90)) if scores else math.inf
    top_sharp = float(np.percentile(sharpness, 90)) if sharpness else math.inf
    reasons = defaultdict(set)
    protected = set()

    def retain(photo, reason, mandatory=False):
        pid = photo["photo_id"]
        reasons[pid].add(reason)
        if mandatory:
            protected.add(pid)

    for photo in eligible:
        score, sharp, mean = photo["aesthetic"], photo["sharpness"], photo["luma_mean"]
        if photo["user_decision"] in KEEP_DECISIONS:
            retain(photo, "user_keep", True)
        if score is None:
            retain(photo, "unscored")
        elif score >= floor:
            retain(photo, "aesthetic_percentile")
        if score is not None and score >= top_quality:
            retain(photo, "high_aesthetic", True)
        if sharp is not None and sharp > 0 and sharp >= top_sharp:
            retain(photo, "high_sharpness", True)
        if mean is not None and (mean < .32 or mean > .78) and not photo["auto_reject"]:
            retain(photo, "extreme_light", True)
        if include_maybe or moment_id is not None:
            retain(photo, "expanded_review")

    for group in _groups(eligible, lambda p: [] if p["moment_id"] is None else [p["moment_id"]]):
        retain(max(group, key=lambda p: (p["moment_primary"], _priority(p))), "moment_primary", True)
    if include_story:
        for group in _groups(eligible, lambda p: p["story_ids"]):
            pool = [p for p in group if p["photo_id"] in reasons]
            retain(max(pool or group, key=_priority), "story_representative", True)
        for group in _groups(eligible, lambda p: [tuple(scene) for scene in p["story_scenes"]]):
            retain(max(group, key=_priority), "scene_representative", True)

    hashes = defaultdict(list)
    for photo in eligible:
        if photo["sha256"]:
            hashes[photo["sha256"]].append(photo["photo_id"])
    for copies in hashes.values():
        nominated = [catalog[pid] for pid in copies if pid in reasons]
        if nominated:
            winner = max(nominated, key=_priority)["photo_id"]
            for photo in nominated:
                pid = photo["photo_id"]
                if pid != winner and pid not in protected:
                    del reasons[pid]
    rows = [dict(catalog[pid], candidate_reasons=sorted(why),
                 exact_duplicate_ids=sorted(hashes.get(catalog[pid]["sha256"], [])))
            for pid, why in reasons.items()]
    pool_size = len(rows)
    if max_candidates is not None and pool_size > max_candidates:
        if len(protected) > max_candidates:
            raise ValueError(f"cap cannot retain {len(protected)} protected photos")
        retained = [p for p in rows if p["photo_id"] in protected]
        optional = [p for p in sorted(rows, key=_priority, reverse=True) if p["photo_id"] not in protected]
        queues = defaultdict(deque)
        for photo in optional:
            queues[str(photo["story_id"] or (photo["taken_at"] or "")[:10])].append(photo)
        cycle = deque(queues[key] for key in sorted(queues))
        while cycle and len(retained) < max_candidates:
            queue = cycle.popleft()
            retained.append(queue.popleft())
            if queue:
                cycle.append(queue)
        rows = retained
    rows.sort(key=lambda p: (p["story_id"] or 0, p["taken_at"] or "", p["photo_id"]))
    options = dict(percentile=percentile, max_candidates=max_candidates,
                   include_story=include_story, include_maybe=include_maybe,
                   batch_size=batch_size, moment_id=moment_id, story_id=story_id)
    summary = dict(indexed=len(catalog), eligible=len(eligible), pool_size=pool_size,
                   candidates=len(rows), protected=len(protected), omitted_by_cap=pool_size - len(rows),
                   missing_previews=sum(p["preview_path"] is None for p in rows))
    return dict(schema_version=SCHEMA_VERSION, library=str(cfg.folder),
                generated_at=datetime.now(timezone.utc).isoformat(), options=options,
                summary=summary, candidates=rows, batches=make_batches(rows, batch_size))
