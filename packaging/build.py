#!/usr/bin/env python
"""Build a standalone selects desktop bundle with PyInstaller.

Steps:
  1. Build the frontend (``npm run build``) if npm is available.
  2. Copy ``frontend/dist`` -> ``selects/server/static`` so the packaged
     app can serve the UI same-origin.
  3. Run PyInstaller (onedir) using ``packaging/selects.spec``.

Usage:
    python packaging/build.py [--no-frontend] [--ml]

    --no-frontend   Skip the npm build (reuse an existing frontend/dist).
    --ml            Bundle the ML stack (torch/transformers/...). Large + slow.
                    Equivalent to setting SELECTS_BUNDLE_ML=1.

CPU-only torch tip (much smaller than the CUDA wheels):
    pip install torch --index-url https://download.pytorch.org/whl/cpu
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
SPEC = REPO_ROOT / "packaging" / "selects.spec"
DIST_DIR = REPO_ROOT / "dist"
BUILD_DIR = REPO_ROOT / "build"


def _which(name: str) -> str | None:
    # On Windows npm/npx are .cmd shims; shutil.which finds them via PATHEXT.
    return shutil.which(name) or shutil.which(name + ".cmd")


def build_frontend() -> None:
    npm = _which("npm")
    if npm is None:
        if FRONTEND_DIST.exists():
            print("[build] npm not found; reusing existing frontend/dist.")
            return
        print("[build] ERROR: npm not found and no existing frontend/dist. "
              "Install Node.js or run with --no-frontend after building manually.")
        sys.exit(1)

    if not (FRONTEND_DIR / "node_modules").exists():
        print("[build] installing frontend deps (npm install)...")
        subprocess.run([npm, "install"], cwd=FRONTEND_DIR, check=True)

    print("[build] building frontend (npm run build)...")
    subprocess.run([npm, "run", "build"], cwd=FRONTEND_DIR, check=True)


def copy_static() -> None:
    if not FRONTEND_DIST.exists():
        print("[build] ERROR: frontend/dist missing; cannot copy static assets.")
        sys.exit(1)
    if STATIC_DIR.exists():
        shutil.rmtree(STATIC_DIR)
    shutil.copytree(FRONTEND_DIST, STATIC_DIR)
    print(f"[build] copied frontend/dist -> {STATIC_DIR.relative_to(REPO_ROOT)}")


def ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[build] PyInstaller is not installed. Install it with:\n"
              f"    {sys.executable} -m pip install \"pyinstaller>=6.6\"")
        sys.exit(1)


def run_pyinstaller() -> None:
    for d in (DIST_DIR / "selects", BUILD_DIR / "selects"):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm",
           "--distpath", str(DIST_DIR), "--workpath", str(BUILD_DIR), str(SPEC)]
    print("[build] running:", " ".join(cmd))
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)


def report() -> None:
    exe = DIST_DIR / "selects" / ("selects.exe" if os.name == "nt" else "selects")
    if not exe.exists():
        print("[build] ERROR: expected executable not found:", exe)
        sys.exit(1)
    total = sum(f.stat().st_size for f in (DIST_DIR / "selects").rglob("*") if f.is_file())
    print("\n[build] SUCCESS")
    print(f"[build] executable: {exe}")
    print(f"[build] bundle size: {total / (1024 * 1024):.1f} MB")
    print(f"[build] run it: {exe} --help")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the selects desktop bundle.")
    ap.add_argument("--no-frontend", action="store_true", help="Skip npm build.")
    ap.add_argument("--ml", action="store_true", help="Bundle the ML stack (large).")
    args = ap.parse_args()

    if args.ml:
        os.environ["SELECTS_BUNDLE_ML"] = "1"

    ensure_pyinstaller()
    if not args.no_frontend:
        build_frontend()
    copy_static()
    run_pyinstaller()
    report()


if __name__ == "__main__":
    main()
