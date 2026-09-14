"""Browser playback compatibility policy for probed media."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .probe import MediaProbe, VideoStream


@dataclass(frozen=True)
class PlaybackDecision:
    compatible: bool
    needs_proxy: bool
    reason: str
    container: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None

    @property
    def direct(self) -> bool:
        return self.compatible and not self.needs_proxy

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _video_ok(video: VideoStream) -> tuple[bool, str]:
    if video.codec not in {"h264", "avc1"}:
        return False, f"video codec {video.codec or 'unknown'} is not browser-safe"
    if video.pixel_format not in {None, "yuv420p", "yuvj420p"}:
        return False, f"pixel format {video.pixel_format} is not broadly supported"
    if video.level is not None and video.level > 52:
        return False, "H.264 level is above the broadly supported range"
    return True, ""


def decide_playback(probe: MediaProbe) -> PlaybackDecision:
    """Decide whether a resource can be served directly to a browser.

    Policy is intentionally conservative: MP4/MOV with H.264 4:2:0 video and
    AAC (or no audio) is direct-playable; everything else should be proxied.
    """

    if probe.error:
        return PlaybackDecision(False, True, probe.error)
    container = (probe.format_name or "").split(",", 1)[0].lower() or None
    if not probe.video:
        return PlaybackDecision(False, True, "media has no video stream", container, None, probe.audio_codec)
    ok, reason = _video_ok(probe.video)
    if not ok:
        return PlaybackDecision(False, True, reason, container, probe.video_codec, probe.audio_codec)
    if container not in {"mov", "mp4", "m4v", "isom", "iso2", "avc1"}:
        return PlaybackDecision(False, True, f"container {container or 'unknown'} is not MP4-compatible", container, probe.video_codec, probe.audio_codec)
    if probe.audio and probe.audio.codec not in {"aac", "mp4a"}:
        return PlaybackDecision(False, True, f"audio codec {probe.audio_codec} needs transcoding", container, probe.video_codec, probe.audio_codec)
    return PlaybackDecision(True, False, "browser-compatible H.264/AAC media", container, probe.video_codec, probe.audio_codec)


def is_playback_compatible(probe: MediaProbe) -> bool:
    """Boolean convenience wrapper around :func:`decide_playback`."""

    return decide_playback(probe).compatible
