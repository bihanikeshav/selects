"""Validated, non-destructive video edit recipes.

The recipe is deliberately independent of SQLAlchemy and FFmpeg.  It is a
small JSON-compatible value object that can be stored by a caller and later
rendered by :mod:`selects.media.export`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal, Mapping


class EditRecipeError(ValueError):
    """Raised when an edit recipe cannot be rendered safely."""


Strength = Literal["low", "medium", "high"]
Quality = Literal["source", "high", "balanced", "small"]


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EditRecipeError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise EditRecipeError(f"{name} must be finite")
    return result


@dataclass(frozen=True)
class EditSegment:
    """One source interval, expressed in seconds, inclusive at start."""

    start_sec: float
    end_sec: float

    def __post_init__(self) -> None:
        start = _finite_number(self.start_sec, "segment start_sec")
        end = _finite_number(self.end_sec, "segment end_sec")
        if start < 0:
            raise EditRecipeError("segment start_sec cannot be negative")
        if end <= start:
            raise EditRecipeError("segment end_sec must be greater than start_sec")
        object.__setattr__(self, "start_sec", start)
        object.__setattr__(self, "end_sec", end)

    @property
    def duration_sec(self) -> float:
        return self.end_sec - self.start_sec

    def to_dict(self) -> dict[str, float]:
        return {"start_sec": self.start_sec, "end_sec": self.end_sec}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EditSegment":
        if not isinstance(value, Mapping):
            raise EditRecipeError("segment must be an object")
        try:
            return cls(value["start_sec"], value["end_sec"])
        except KeyError as exc:
            raise EditRecipeError(f"segment missing {exc.args[0]}") from exc


@dataclass(frozen=True)
class StabilizeOptions:
    enabled: bool = False
    strength: Strength = "medium"
    crop: float = 0.08

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise EditRecipeError("stabilize.enabled must be a boolean")
        if self.strength not in {"low", "medium", "high"}:
            raise EditRecipeError("stabilize.strength must be low, medium, or high")
        crop = _finite_number(self.crop, "stabilize.crop")
        if not 0 <= crop < 0.5:
            raise EditRecipeError("stabilize.crop must be between 0 and 0.5")
        object.__setattr__(self, "crop", crop)

    def to_dict(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "strength": self.strength, "crop": self.crop}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "StabilizeOptions":
        if value is None:
            return cls()
        if not isinstance(value, Mapping):
            raise EditRecipeError("stabilize must be an object")
        return cls(
            enabled=value.get("enabled", False),
            strength=value.get("strength", "medium"),
            crop=value.get("crop", 0.08),
        )


@dataclass(frozen=True)
class ExportProfile:
    """Output settings shared by fast and precise exports."""

    quality: Quality = "balanced"
    container: Literal["mp4", "mov", "mkv"] = "mp4"
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    audio_bitrate: str = "160k"
    preset: str = "fast"

    def __post_init__(self) -> None:
        if self.quality not in {"source", "high", "balanced", "small"}:
            raise EditRecipeError("export.quality is invalid")
        if self.container not in {"mp4", "mov", "mkv"}:
            raise EditRecipeError("export.container is invalid")
        for name in ("video_codec", "audio_codec", "audio_bitrate", "preset"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or any(c in value for c in "\r\n"):
                raise EditRecipeError(f"export.{name} is invalid")

    def to_dict(self) -> dict[str, str]:
        return {
            "quality": self.quality,
            "container": self.container,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "audio_bitrate": self.audio_bitrate,
            "preset": self.preset,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "ExportProfile":
        if value is None:
            return cls()
        if not isinstance(value, Mapping):
            raise EditRecipeError("export must be an object")
        allowed = {"quality", "container", "video_codec", "audio_codec", "audio_bitrate", "preset"}
        unknown = set(value) - allowed
        if unknown:
            raise EditRecipeError(f"unknown export fields: {', '.join(sorted(map(str, unknown)))}")
        return cls(**{key: value[key] for key in allowed if key in value})


@dataclass(frozen=True)
class EditRecipe:
    """A validated edit recipe that never writes to the source file."""

    segments: tuple[EditSegment, ...] = ()
    mute_audio: bool = False
    gain_db: float = 0.0
    stabilize: StabilizeOptions = StabilizeOptions()
    export: ExportProfile = ExportProfile()
    source_sha256: str | None = None

    def __post_init__(self) -> None:
        segments = tuple(
            segment if isinstance(segment, EditSegment) else EditSegment.from_dict(segment)
            for segment in self.segments
        )
        if len(segments) > 64:
            raise EditRecipeError("a recipe may contain at most 64 segments")
        ordered = sorted(segments, key=lambda item: (item.start_sec, item.end_sec))
        if any(left.end_sec > right.start_sec for left, right in zip(ordered, ordered[1:])):
            raise EditRecipeError("segments cannot overlap")
        if not isinstance(self.mute_audio, bool):
            raise EditRecipeError("mute_audio must be a boolean")
        gain = _finite_number(self.gain_db, "gain_db")
        if not -60 <= gain <= 24:
            raise EditRecipeError("gain_db must be between -60 and 24")
        if not isinstance(self.stabilize, StabilizeOptions):
            raise EditRecipeError("stabilize must be StabilizeOptions")
        if not isinstance(self.export, ExportProfile):
            raise EditRecipeError("export must be ExportProfile")
        if self.source_sha256 is not None:
            if not isinstance(self.source_sha256, str) or len(self.source_sha256) != 64:
                raise EditRecipeError("source_sha256 must be a 64-character hash")
        object.__setattr__(self, "segments", segments)
        object.__setattr__(self, "gain_db", gain)

    def validate(self, duration_sec: float | None = None) -> "EditRecipe":
        """Validate optional source-duration bounds and return ``self``."""
        if duration_sec is not None:
            duration = _finite_number(duration_sec, "duration_sec")
            if duration <= 0:
                raise EditRecipeError("duration_sec must be positive")
            if any(segment.end_sec > duration + 1e-6 for segment in self.segments):
                raise EditRecipeError("a segment extends beyond the source duration")
        return self

    @property
    def duration_sec(self) -> float | None:
        if not self.segments:
            return None
        return sum(segment.duration_sec for segment in self.segments)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "segments": [segment.to_dict() for segment in self.segments],
            "mute_audio": self.mute_audio,
            "gain_db": self.gain_db,
            "stabilize": self.stabilize.to_dict(),
            "export": self.export.to_dict(),
            "source_sha256": self.source_sha256,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EditRecipe":
        if not isinstance(value, Mapping):
            raise EditRecipeError("recipe must be an object")
        version = value.get("schema_version", 1)
        if version != 1:
            raise EditRecipeError(f"unsupported edit recipe schema: {version}")
        segments_raw = value.get("segments", [])
        if not isinstance(segments_raw, (list, tuple)):
            raise EditRecipeError("segments must be an array")
        return cls(
            segments=tuple(EditSegment.from_dict(item) for item in segments_raw),
            mute_audio=value.get("mute_audio", False),
            gain_db=value.get("gain_db", 0.0),
            stabilize=StabilizeOptions.from_dict(value.get("stabilize")),
            export=ExportProfile.from_dict(value.get("export")),
            source_sha256=value.get("source_sha256"),
        )

    @classmethod
    def coerce(cls, value: "EditRecipe | Mapping[str, Any]") -> "EditRecipe":
        if isinstance(value, cls):
            return value
        return cls.from_dict(value)
