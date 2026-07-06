from __future__ import annotations

from dataclasses import dataclass

BLUR_THRESHOLD = 30.0
CLIP_THRESHOLD = 0.95


@dataclass
class RejectInput:
    blur: float
    exposure_score: float
    clipped_ratio: float
    faces_count: int


@dataclass
class RejectResult:
    auto_reject: bool
    reason: str | None


def evaluate_reject(inp: RejectInput) -> RejectResult:
    if inp.blur < BLUR_THRESHOLD:
        return RejectResult(True, "severe_blur")
    if inp.clipped_ratio > CLIP_THRESHOLD:
        if inp.exposure_score < 0.1 and inp.faces_count == 0:
            return RejectResult(True, "blown_out" if inp.exposure_score > 0.5 else "all_black")
    return RejectResult(False, None)
