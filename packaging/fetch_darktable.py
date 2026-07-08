#!/usr/bin/env python
"""Download darktable and extract it into ``vendor/darktable`` for bundling.

The desktop bundle can ship darktable so "Edit in darktable" works with no
separate install (see ``packaging/selects.spec`` and
``routes.py:_find_editor_binary``). This script fetches the official Windows
build and lays its install tree at ``vendor/darktable`` so the spec finds
``vendor/darktable/bin``.

Usage:
    python packaging/fetch_darktable.py [--version 5.6.0] [--force]

Windows only for now (the bundled-editor path targets the Windows desktop
build). On macOS/Linux the app falls back to a system darktable install.

Extraction needs no extra tooling: darktable ships an Inno Setup installer,
which supports a silent install to a directory (``/VERYSILENT /DIR=``). We run
it into a temp dir, copy the ``bin``/``lib``/``share`` tree (plus the GPL
license) into ``vendor/darktable``, then run its uninstaller to leave no
Add/Remove-Programs entry behind.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VENDOR_DIR = REPO_ROOT / "vendor" / "darktable"

# Pin a known-good release. Bump this to ship a newer darktable. The GitHub
# asset layout is: release-<ver>/darktable-<ver>-win64.exe
DEFAULT_VERSION = os.environ.get("DARKTABLE_VERSION", "5.6.0")

# Tree folders + license files the real install ships; the rest is installer
# bookkeeping (uninstaller, temp) we don't bundle.
_TREE_DIRS = ("bin", "lib", "share", "etc")
_LICENSE_FILES = ("LICENSE.txt", "AUTHORS.txt")


def _url(version: str) -> str:
    return (
        "https://github.com/darktable-org/darktable/releases/download/"
        f"release-{version}/darktable-{version}-win64.exe"
    )


def _download(url: str, dest: Path) -> None:
    print(f"[fetch-darktable] downloading {url}")
    urllib.request.urlretrieve(url, dest)  # noqa: S310 — trusted GitHub URL
    print(f"[fetch-darktable] downloaded {dest.stat().st_size / 1e6:.0f} MB")


def _extract_with_inno(installer: Path, dest: Path) -> None:
    """Silent-install the Inno Setup package into a temp dir, copy the tree out,
    then uninstall so no Add/Remove-Programs entry is left behind.

    Version-proof (unlike innoextract, which trails new Inno Setup releases) and
    needs no external tool. Inno accepts a quoted ``/DIR`` so spaces are fine.
    """
    tmp = REPO_ROOT / "vendor" / "_dt_install_tmp"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)

    print(f"[fetch-darktable] silent-installing to {tmp}")
    subprocess.run(
        [str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES",
         "/NORESTART", "/NOICONS", f"/DIR={tmp}"],
        check=True,
    )

    dest.mkdir(parents=True, exist_ok=True)
    for name in _TREE_DIRS:
        src = tmp / name
        if src.is_dir():
            shutil.copytree(src, dest / name, dirs_exist_ok=True)
    for name in _LICENSE_FILES:  # GPL: ship darktable's license alongside it
        src = tmp / name
        if src.is_file():
            shutil.copy2(src, dest / name)

    # Best-effort: run the uninstaller (removes the registry/ARP entry), then
    # drop the temp dir. The tree we care about is already copied to `dest`.
    uninst = next(tmp.glob("unins*.exe"), None)
    if uninst is not None:
        try:
            subprocess.run(
                [str(uninst), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                timeout=120,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[fetch-darktable] uninstaller cleanup skipped: {exc}")
    shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch darktable for bundling.")
    ap.add_argument("--version", default=DEFAULT_VERSION,
                    help=f"darktable version (default {DEFAULT_VERSION}).")
    ap.add_argument("--force", action="store_true",
                    help="Re-download even if vendor/darktable already exists.")
    args = ap.parse_args()

    if sys.platform != "win32":
        print("[fetch-darktable] non-Windows platform; skipping (app falls "
              "back to a system darktable install).")
        return

    if (VENDOR_DIR / "bin").is_dir() and not args.force:
        print(f"[fetch-darktable] {VENDOR_DIR} already present; use --force to refresh.")
        return

    VENDOR_DIR.parent.mkdir(parents=True, exist_ok=True)
    if VENDOR_DIR.exists():
        shutil.rmtree(VENDOR_DIR, ignore_errors=True)

    installer = VENDOR_DIR.parent / f"darktable-{args.version}-win64.exe"
    _download(_url(args.version), installer)

    try:
        _extract_with_inno(installer, VENDOR_DIR)
    finally:
        installer.unlink(missing_ok=True)

    dt = VENDOR_DIR / "bin" / "darktable.exe"
    if not dt.exists():
        print(f"[fetch-darktable] ERROR: expected {dt} after extraction. "
              f"Inspect {VENDOR_DIR} and adjust this script.")
        sys.exit(1)
    print(f"[fetch-darktable] SUCCESS: {dt}")


if __name__ == "__main__":
    main()
