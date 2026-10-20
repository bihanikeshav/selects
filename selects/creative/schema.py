"""Validation contract for judgments supplied by a reviewer."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 1
DECISIONS = ("hero", "grade", "story", "maybe", "reject")
SELECTED = frozenset(DECISIONS[:3])
Decision = Literal["hero", "grade", "story", "maybe", "reject"]
Score = Annotated[float, Field(strict=True, ge=0, le=10, allow_inf_nan=False)]
RequiredText = Annotated[str, Field(min_length=1, max_length=4000)]
WEIGHTS = dict(composition=.22, light=.18, moment=.15, grading_potential=.20,
               story_value=.10, uniqueness=.15)


class CreativeReview(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    photo_id: Annotated[int, Field(strict=True, gt=0)]
    filename: RequiredText
    technical_score: Score
    composition: Score
    light: Score
    moment: Score
    story_value: Score
    uniqueness: Score
    grading_potential: Score
    decision: Decision
    reason: RequiredText
    grade_direction: Annotated[str, Field(max_length=4000)]
    sha256: str | None = None
    creative_score: Score | None = None
    reviewer: RequiredText = "agent"
    technical_failure: bool = False
    scene_type: Literal["establishing", "landscape", "human", "portrait", "transition",
                        "transport", "food", "detail", "weather", "other"] | None = None
    location: str | None = None
    subject: str | None = None
    composition_key: str | None = None
    moment_exception: Literal["expression", "orientation", "environment_detail",
                              "sequence", "composition"] | None = None
    moment_exception_reason: str | None = None

    @model_validator(mode="after")
    def validate_judgment(self):
        expected = round(sum(WEIGHTS[name] * getattr(self, name) for name in WEIGHTS), 2)
        if self.creative_score is not None and abs(expected - self.creative_score) > .011:
            raise ValueError(f"weighted creative score must equal {expected:.2f}")
        self.creative_score = expected
        if self.decision in SELECTED:
            if self.technical_failure:
                raise ValueError("selected photo has an unrecoverable technical failure")
            if not self.grade_direction:
                raise ValueError("grade_direction is required for a selected photo")
        if self.moment_exception is not None and not self.moment_exception_reason:
            raise ValueError("moment_exception requires a reason")
        return self


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviews: list[CreativeReview]
    library: str | None = None
    schema_version: Literal[1] = SCHEMA_VERSION
