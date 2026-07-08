#!/usr/bin/env python
"""Build a standalone selects desktop bundle with Nuitka (Windows / macOS / Linux).

Nuitka compiles the Python to C for a faster-starting, leaner bundle than the
PyInstaller onedir. This mirrors what ``packaging/selects.spec`` bundles:

  * the built frontend (``selects/server/static``) and Alembic migrations,
  * the base runtime (FastAPI + uvicorn + pydantic + SQLAlchemy + pywebview,
    and pythonnet/clr for WebView2 on Windows),
  * the imaging stack (PIL/pillow_heif/rawpy/pyexiv2/cv2/numpy),
  * optionally (``--ml``) the ONNX ML stack (onnxruntime/sentencepiece/
    huggingface_hub/insightface/sklearn). Model weights are fetched at runtime,
    never baked in.

Usage:
    python packaging/build_nuitka.py [--no-frontend] [--ml] [--onefile]

Cross-platform notes:
  * Nuitka cannot cross-compile — run this ON each target OS (see the
    ``nuitka`` CI job, which runs it on windows/macos/ubuntu runners).
  * A C compiler is required. On Windows Nuitka uses MSVC if present, else
    auto-downloads MinGW64 (``--assume-yes-for-downloads``). macOS needs the
    Xcode command-line tools; Linux needs gcc + patchelf.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = REPO_ROOT / "frontend"
FRONTEND_DIST = FRONTEND_DIR / "dist"
STATIC_DIR = REPO_ROOT / "selects" / "server" / "static"
MIGRATIONS_DIR = REPO_ROOT / "selects" / "db" / "migrations"
ENTRY = REPO_ROOT / "packaging" / "entry.py"
ICON_ICO = REPO_ROOT / "packaging" / "selects.ico"
SPLASH = REPO_ROOT / "packaging" / "splash.png"
OUT_DIR = REPO_ROOT / "dist" / "nuitka"

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

# Packages that are imported dynamically / via plugins and that Nuitka's import
# following can miss — force them in. Mirrors selects.spec's hiddenimports.
BASE_INCLUDE_PACKAGES = [
    "selects",
    "uvicorn",
    "anyio",
    "click",
    "websockets",
    "PIL",
    # NOTE: do NOT force-include the whole "webview" package — Nuitka's pywebview
    # plugin decides which platform backend to bundle (winforms/edgechromium on
    # Windows, cocoa on macOS, gtk on Linux). Forcing it pulls every backend
    # (incl. android) and conflicts with the plugin.
]
BASE_INCLUDE_MODULES = [
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
]

# Native imaging libs whose data/DLLs must ride along.
DATA_PACKAGES = ["PIL", "pillow_heif", "rawpy", "pyexiv2", "cv2"]

ML_PACKAGES = ["onnxruntime", "sentencepiece", "huggingface_hub", "insightface",
               "sklearn", "hdbscan", "umap", "numpy"]

# Never follow into these — torch is fully gone (ONNX only); the rest are heavy
# transitive deps we don't ship (mirrors selects.spec excludes).
DEAD_PACKAGES = ["torch", "torchvision", "torchcodec", "transformers", "ram"]
NONML_EXTRA_EXCLUDES = ["scipy", "pandas", "matplotlib", "IPython", "notebook"]


def _which(name: str) -> str | None:
    return shutil.which(name) or shutil.which(name + ".cmd")


def _app_version() -> str:
    """selects version as a 4-part-safe X.Y.Z (Nuitka wants numeric versions)."""
    try:
        import selects
        v = getattr(selects, "__version__", "0.0.0")
    except Exception:
        v = "0.0.0"
    # keep only leading numeric dotted part
    parts = v.split("+")[0].split("-")[0].split(".")
    nums = [p for p in parts if p.isdigit()][:4]
    return ".".join(nums) or "0.0.0"


def build_frontend() -> None:
    npm = _which("npm")
    if npm is None:
        if FRONTEND_DIST.exists():
            print("[nuitka] npm not found; reusing existing frontend/dist.")
            return
        print("[nuitka] ERROR: npm not found and no frontend/dist. Build the "
              "frontend first or pass --no-frontend.")
        sys.exit(1)
    if not (FRONTEND_DIR / "node_modules").exists():
        subprocess.run([npm, "install"], cwd=FRONTEND_DIR, check=True)
    subprocess.run([npm, "run", "build"], cwd=FRONTEND_DIR, check=True)


def copy_static() -> None:
    if not FRONTEND_DIST.exists():
        print("[nuitka] ERROR: frontend/dist missing; cannot copy static assets.")
        sys.exit(1)
    if STATIC_DIR.exists():
        shutil.rmtree(STATIC_DIR)
    shutil.copytree(FRONTEND_DIST, STATIC_DIR)
    print(f"[nuitka] copied frontend/dist -> {STATIC_DIR.relative_to(REPO_ROOT)}")


def _installed(mod: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


def nuitka_args(bundle_ml: bool, onefile: bool) -> list[str]:
    args = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--assume-yes-for-downloads",     # non-interactive MinGW/dep fetch
        "--lto=no",                       # keep compile time sane on the ML stack
        f"--output-dir={OUT_DIR}",
        "--output-filename=selects" + (".exe" if IS_WIN else ""),
        "--company-name=selects",
        "--product-name=selects",
        f"--product-version={_app_version()}",
        f"--file-version={_app_version()}",
        "--python-flag=-O",
        # bundled assets (same as the PyInstaller spec)
        f"--include-data-dir={STATIC_DIR}=selects/server/static",
        f"--include-data-dir={MIGRATIONS_DIR}=selects/db/migrations",
        # pywebview pulls Qt/GTK optionals we don't use — let Nuitka's plugin
        # resolve the right backend and skip Qt to avoid multi-hundred-MB bloat.
        "--enable-plugin=no-qt",
    ]
    if onefile:
        args.append("--onefile")
        if IS_WIN and SPLASH.exists():
            # Nuitka only supports a boot splash in onefile mode on Windows.
            args.append(f"--onefile-windows-splash-screen-image={SPLASH}")

    for p in BASE_INCLUDE_PACKAGES:
        args.append(f"--include-package={p}")
    for m in BASE_INCLUDE_MODULES:
        args.append(f"--include-module={m}")
    for p in DATA_PACKAGES:
        if _installed(p):
            args.append(f"--include-package-data={p}")

    # Windowed (no console) + icon, per OS.
    if IS_WIN:
        args.append("--windows-console-mode=disable")
        if ICON_ICO.exists():
            args.append(f"--windows-icon-from-ico={ICON_ICO}")
        # WebView2 via pythonnet/clr.
        for p in ("clr_loader", "pythonnet"):
            if _installed(p):
                args.append(f"--include-package={p}")
        args.append("--include-module=clr")
    elif IS_MAC:
        args += ["--macos-create-app-bundle", "--macos-app-name=selects",
                 "--macos-app-mode=gui"]
        if (REPO_ROOT / "packaging" / "selects.icns").exists():
            args.append(f"--macos-app-icon={REPO_ROOT/'packaging'/'selects.icns'}")
    elif IS_LINUX:
        if (REPO_ROOT / "packaging" / "selects.png").exists():
            args.append(f"--linux-icon={REPO_ROOT/'packaging'/'selects.png'}")

    # ML stack (opt-in) vs. hard excludes.
    if bundle_ml:
        for p in ML_PACKAGES:
            if _installed(p):
                args += [f"--include-package={p}", f"--include-package-data={p}"]
        excludes = list(DEAD_PACKAGES)
    else:
        excludes = list(DEAD_PACKAGES) + ML_PACKAGES + NONML_EXTRA_EXCLUDES
    for p in excludes:
        args.append(f"--nofollow-import-to={p}")

    args.append(str(ENTRY))
    return args


def report() -> None:
    # standalone drops a <entry>.dist dir; app-bundle/onefile differ.
    candidates = [
        OUT_DIR / "entry.dist" / ("selects.exe" if IS_WIN else "selects"),
        OUT_DIR / ("selects.exe" if IS_WIN else "selects"),
        OUT_DIR / "selects.app",
    ]
    found = next((c for c in candidates if c.exists()), None)
    if not found:
        print("[nuitka] WARNING: could not locate the built artifact under", OUT_DIR)
        return
    root = found if found.is_dir() else found.parent
    total = sum(f.stat().st_size for f in root.rglob("*") if f.is_file())
    print("\n[nuitka] SUCCESS")
    print(f"[nuitka] artifact: {found}")
    print(f"[nuitka] bundle size: {total / (1024 * 1024):.1f} MB")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the selects bundle with Nuitka.")
    ap.add_argument("--no-frontend", action="store_true", help="Skip npm build.")
    ap.add_argument("--ml", action="store_true", help="Bundle the ML stack (large, slow).")
    ap.add_argument("--onefile", action="store_true",
                    help="Produce a single-file exe (slower first launch; splash on Windows).")
    args = ap.parse_args()

    if not args.no_frontend:
        build_frontend()
    copy_static()

    cmd = nuitka_args(bundle_ml=args.ml, onefile=args.onefile)
    print("[nuitka] running:\n  " + " ".join(cmd))
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)
    report()


if __name__ == "__main__":
    main()
