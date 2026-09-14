# Bundled media runtime

The desktop builds can bundle `ffmpeg` and `ffprobe` without discovering or
copying an arbitrary executable from the build machine's `PATH`. This keeps
the artifact deterministic and makes the codec source an explicit release
decision.

## Approved sources

Resolution precedence is:

1. An explicit file path.
2. An explicit directory containing `ffmpeg` and/or `ffprobe`.
3. `vendor/ffmpeg` in the repository.
4. The optional `imageio-ffmpeg` package for `ffmpeg` only.

The build scripts never call `shutil.which("ffmpeg")`, never copy the first
system executable they find, and never mutate `PATH`.

The preferred repository layout is:

```text
vendor/
  ffmpeg/
    ffmpeg.exe       # Windows; use ffmpeg on macOS/Linux
    ffprobe.exe      # Windows; use ffprobe on macOS/Linux
```

The packaged executables are placed beside the `selects` application binary:

```text
selects/
  selects.exe
  ffmpeg.exe
  ffprobe.exe
```

Keeping the binaries at the application root is intentional. Windows resolves
the existing decoder's bare `ffmpeg` and `ffprobe` subprocess names from the
application directory, so the desktop bundle does not need a `PATH` change.
The application also retains its OpenCV fallback when a codec or runtime is
unavailable.

## Nuitka

Install the optional media dependency when using its fallback binary:

```powershell
python -m pip install -e ".[desktop,media]"
```

For a release build with a reviewed runtime in the repository:

```powershell
python packaging/build_nuitka.py --no-frontend --ml --ffmpeg-dir vendor/ffmpeg
```

Equivalent explicit paths are available when the runtime is kept outside the
repository:

```powershell
python packaging/build_nuitka.py --no-frontend `
  --ffmpeg C:\approved\ffmpeg\ffmpeg.exe `
  --ffprobe C:\approved\ffmpeg\ffprobe.exe
```

`--no-ffmpeg` disables all media-runtime collection. The same options are
available through `SELECTS_FFMPEG_DIR`, `SELECTS_FFMPEG_PATH`, and
`SELECTS_FFPROBE_PATH`; explicit CLI options take precedence.

## PyInstaller

`packaging/selects.spec` reads the same environment variables:

```powershell
$env:SELECTS_BUNDLE_FFMPEG = "1"
$env:SELECTS_FFMPEG_DIR = (Resolve-Path vendor\ffmpeg).Path
python packaging/build.py --no-frontend --ml
```

Or specify files individually:

```powershell
$env:SELECTS_FFMPEG_PATH = "C:\approved\ffmpeg\ffmpeg.exe"
$env:SELECTS_FFPROBE_PATH = "C:\approved\ffmpeg\ffprobe.exe"
python packaging/build.py --no-frontend
```

Set `SELECTS_BUNDLE_FFMPEG=0` to disable automatic collection. An explicit
file or directory remains an intentional build input. `imageio-ffmpeg` can
provide `ffmpeg` when installed, but it does not provide `ffprobe`; a reviewed
`ffprobe` must come from `vendor/ffmpeg` or `SELECTS_FFPROBE_PATH`.

## Licensing and notices

Do not populate `vendor/ffmpeg` by blindly copying `ffmpeg` from a developer
machine. Record the exact source archive, version, configure flags, license,
and notices shipped with the selected binaries.

FFmpeg itself is generally LGPL, but enabling or distributing GPL components
such as `libx264`, `libx265`, or `libvidstab` can make the build GPL or create
additional source and notice obligations. H.264 and HEVC may also involve
separate patent and distribution considerations. `imageio-ffmpeg` is a Python
wrapper/package, but its included binary still needs the same license review.

For a conservative default, use an LGPL-compatible FFmpeg build, ship its
`LICENSE`/notice files alongside the application, and avoid GPL-only filters
unless the project is prepared to satisfy the corresponding obligations. The
packaging scripts intentionally do not download, select, or vendor a build on
their own.

## Runtime limitation

This packaging-only change does not alter application code. The existing
decoder uses bare executable names and has a documented OpenCV fallback. The
Windows desktop bundle can resolve the adjacent binaries without `PATH`
mutation. A future cross-platform runtime resolver should consume the same
application-root layout (or `SELECTS_FFMPEG_PATH`/`SELECTS_FFPROBE_PATH`) before
calling FFmpeg on macOS or Linux.
