from __future__ import annotations

from dataclasses import dataclass

BLUR_THRESHOLD = 30.0
CLIP_THRESHOLD = 0.95
# Split high vs low clip using luma mean (exposure_score.mean in [0, 1]).
MEAN_BLOWN_OUT = 0.5


@dataclass
class RejectInput:
    blur: float
    exposure_score: float
    clipped_ratio: float
    faces_count: int
    mean: float


@dataclass
class RejectResult:
    auto_reject: bool
    reason: str | None


def evaluate_reject(inp: RejectInput) -> RejectResult:
    if inp.blur < BLUR_THRESHOLD:
        return RejectResult(True, "severe_blur")
    if inp.clipped_ratio > CLIP_THRESHOLD:
        if inp.mean >= MEAN_BLOWN_OUT:
            return RejectResult(True, "blown_out")
        return RejectResult(True, "all_black")
    return RejectResult(False, None)
