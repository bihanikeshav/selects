"""Locate FFmpeg safely in source, bundled, and installed environments."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


@dataclass(frozen=True)
class FFmpegRuntime:
    """Resolved executable paths.

    ``ffprobe`` can be absent when only the imageio-ffmpeg binary is
    available.  Consumers should inspect ``available``/``probe_available``
    rather than assuming both tools exist.
    """

    ffmpeg: Path | None
    ffprobe: Path | None
    source: str | None = None

    @property
    def available(self) -> bool:
        return self.ffmpeg is not None

    @property
    def probe_available(self) -> bool:
        return self.ffprobe is not None


_RUNTIME: FFmpegRuntime | None = None


def _executable_name(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


def _valid(path: Path | str | None) -> Path | None:
    if not path:
        return None
    candidate = Path(path).expanduser()
    return candidate.resolve() if candidate.is_file() else None


def _candidate_binaries(name: str) -> list[Path]:
    exe = _executable_name(name)
    package_root = Path(__file__).resolve().parents[2]
    roots = [
        Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "_MEIPASS", None) else None,
        Path(sys.executable).resolve().parent,
        package_root,
        package_root / "vendor",
        package_root / "vendor" / "ffmpeg",
    ]
    result: list[Path] = []
    relatives = (
        Path(),
        Path("bin"),
        Path("ffmpeg"),
        Path("ffmpeg") / "bin",
        Path("vendor") / "ffmpeg",
        Path("vendor") / "ffmpeg" / "bin",
    )
    for root in roots:
        if root is None:
            continue
        result.extend((root / relative / exe for relative in relatives))
    return result


def _imageio_ffmpeg() -> Path | None:
    try:
        import imageio_ffmpeg  # type: ignore[import-not-found]

        return _valid(imageio_ffmpeg.get_ffmpeg_exe())
    except (ImportError, OSError, RuntimeError, ValueError):
        return None


def resolve_ffmpeg(*, refresh: bool = False) -> FFmpegRuntime:
    """Resolve FFmpeg once, checking bundle/vendor paths before ``PATH``.

    ``SELECTS_FFMPEG_PATH``/``SELECTS_FFPROBE_PATH`` (and their shorter
    ``SELECTS_FFMPEG``/``SELECTS_FFPROBE`` aliases) may point at explicit
    files. ``SELECTS_FFMPEG_DIR`` may point at a directory containing both
    tools. They are useful to packaged deployments and make the resolver easy
    to control in tests. Resolution never invokes an executable.
    """

    global _RUNTIME
    if _RUNTIME is not None and not refresh:
        return _RUNTIME

    ffmpeg = _valid(os.environ.get("SELECTS_FFMPEG_PATH") or os.environ.get("SELECTS_FFMPEG"))
    ffprobe = _valid(os.environ.get("SELECTS_FFPROBE_PATH") or os.environ.get("SELECTS_FFPROBE"))
    source = "environment" if ffmpeg or ffprobe else None

    explicit_dir = os.environ.get("SELECTS_FFMPEG_DIR")
    if explicit_dir:
        directory = Path(explicit_dir).expanduser()
        ffmpeg = ffmpeg or _valid(directory / _executable_name("ffmpeg"))
        ffprobe = ffprobe or _valid(directory / _executable_name("ffprobe"))
        source = source or "environment directory"

    if ffmpeg is None:
        for candidate in _candidate_binaries("ffmpeg"):
            ffmpeg = _valid(candidate)
            if ffmpeg:
                source = "bundle/vendor"
                break
    if ffprobe is None:
        for candidate in _candidate_binaries("ffprobe"):
            ffprobe = _valid(candidate)
            if ffprobe:
                source = source or "bundle/vendor"
                break

    if ffmpeg is None:
        ffmpeg = _valid(shutil.which("ffmpeg"))
        if ffmpeg:
            source = "PATH"
    if ffprobe is None:
        ffprobe = _valid(shutil.which("ffprobe"))
        if ffprobe:
            source = source or "PATH"

    if ffmpeg is None:
        ffmpeg = _imageio_ffmpeg()
        if ffmpeg:
            source = "imageio_ffmpeg"

    _RUNTIME = FFmpegRuntime(ffmpeg, ffprobe, source)
    return _RUNTIME


def ffmpeg_available(*, refresh: bool = False) -> bool:
    """Return whether an FFmpeg executable can be resolved."""

    return resolve_ffmpeg(refresh=refresh).available


def resolve_ffprobe(*, refresh: bool = False) -> Path | None:
    """Return the resolved ffprobe path, if available."""

    return resolve_ffmpeg(refresh=refresh).ffprobe


def ffprobe_available(*, refresh: bool = False) -> bool:
    """Return whether an ffprobe executable can be resolved."""

    return resolve_ffprobe(refresh=refresh) is not None


def get_ffmpeg(*, refresh: bool = False) -> Path | None:
    """Compatibility alias for callers that prefer a getter name."""

    return resolve_ffmpeg(refresh=refresh).ffmpeg


def get_ffprobe(*, refresh: bool = False) -> Path | None:
    """Compatibility alias for callers that prefer a getter name."""

    return resolve_ffprobe(refresh=refresh)


def _command(executable: Path, args: Sequence[object]) -> list[str]:
    return [str(executable), *(str(arg) for arg in args)]


def run_ffmpeg(
    args: Sequence[object],
    *,
    runtime: FFmpegRuntime | None = None,
    check: bool = True,
    **kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    """Run FFmpeg with an argument array and ``shell=False``.

    This is the sole subprocess convenience used by this package; callers
    never need to concatenate user-controlled paths into a shell command.
    """

    resolved = runtime or resolve_ffmpeg()
    if resolved.ffmpeg is None:
        raise FileNotFoundError("FFmpeg is not available")
    kwargs["shell"] = False
    return subprocess.run(_command(resolved.ffmpeg, args), check=check, **kwargs)


def spawn_ffmpeg(
    args: Sequence[object],
    *,
    runtime: FFmpegRuntime | None = None,
    **kwargs: Any,
) -> subprocess.Popen[Any]:
    """Start FFmpeg with an argument array and ``shell=False``."""

    resolved = runtime or resolve_ffmpeg()
    if resolved.ffmpeg is None:
        raise FileNotFoundError("FFmpeg is not available")
    kwargs["shell"] = False
    return subprocess.Popen(_command(resolved.ffmpeg, args), **kwargs)
