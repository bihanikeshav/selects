"""Media runtime helpers used by playback and media-serving layers.

The package is deliberately independent of FastAPI.  Callers can use the
range iterator with any HTTP framework and can choose how to turn the
structured results into HTTP responses.
"""

from .audio import AudioAnalysis, EnergyWindow, WaveformBin, analyze_audio
from .playback import PlaybackDecision, decide_playback, is_playback_compatible
from .probe import AudioStream, MediaProbe, VideoStream, probe_media
from .proxy import ProxyProgress, ProxyResult, generate_proxy, proxy_cache_path
from .ranges import (
    ByteRange,
    RangeNotSatisfiable,
    iter_file_range,
    iter_range,
    parse_byte_range,
    parse_range_header,
)
from .runtime import (
    FFmpegRuntime,
    ffmpeg_available,
    ffprobe_available,
    get_ffmpeg,
    get_ffprobe,
    resolve_ffmpeg,
    resolve_ffprobe,
    run_ffmpeg,
    spawn_ffmpeg,
)

__all__ = [
    "AudioAnalysis",
    "AudioStream",
    "ByteRange",
    "EnergyWindow",
    "FFmpegRuntime",
    "MediaProbe",
    "PlaybackDecision",
    "ProxyProgress",
    "ProxyResult",
    "RangeNotSatisfiable",
    "VideoStream",
    "WaveformBin",
    "analyze_audio",
    "decide_playback",
    "ffmpeg_available",
    "ffprobe_available",
    "get_ffmpeg",
    "get_ffprobe",
    "generate_proxy",
    "is_playback_compatible",
    "iter_file_range",
    "iter_range",
    "parse_byte_range",
    "parse_range_header",
    "probe_media",
    "proxy_cache_path",
    "resolve_ffmpeg",
    "resolve_ffprobe",
    "run_ffmpeg",
    "spawn_ffmpeg",
]
