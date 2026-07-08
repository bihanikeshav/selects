# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for selects (onedir).

onedir (not onefile): the ML stack (onnxruntime + insightface + sklearn) is too
large to unpack from a onefile bundle on every launch.

ML bundling is opt-in and degrades gracefully:
  * By default the base app (FastAPI + imaging stack) is packed and ML deps
    are left out, keeping the build fast and small.
  * Set env ``SELECTS_BUNDLE_ML=1`` to also collect onnxruntime/sentencepiece/
    huggingface_hub/insightface/sklearn when they are importable. Any that are
    not installed are simply skipped, so an ML-less venv still builds. Model
    weights themselves are ONNX and fetched at runtime from the selects-onnx HF
    repo, not bundled.
"""
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

# --- repo layout -----------------------------------------------------------
# When run via `pyinstaller packaging/selects.spec`, SPECPATH is packaging/.
try:
    REPO_ROOT = os.path.dirname(os.path.abspath(SPECPATH))  # noqa: F821
except NameError:
    REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ENTRY = os.path.join(REPO_ROOT, "packaging", "entry.py")
STATIC_DIR = os.path.join(REPO_ROOT, "selects", "server", "static")
MIGRATIONS_DIR = os.path.join(REPO_ROOT, "selects", "db", "migrations")
ICON = os.path.join(REPO_ROOT, "packaging", "selects.ico")
ICON = ICON if os.path.isfile(ICON) else None

binaries = []
datas = []
hiddenimports = []

# Bundle the built frontend so FastAPI can serve it from the package.
if os.path.isdir(STATIC_DIR):
    datas.append((STATIC_DIR, os.path.join("selects", "server", "static")))
else:
    print("[selects.spec] WARNING: no frontend static dir; UI will not be bundled.")

# Bundle the Alembic migration scripts. They're loaded by path at runtime
# (selects/db/__init__.py -> Path(__file__).parent / "migrations"), so
# PyInstaller doesn't pick them up automatically — without this the app
# crashes with "Path doesn't exist: .../selects/db/migrations" on first DB open.
if os.path.isdir(MIGRATIONS_DIR):
    datas.append((MIGRATIONS_DIR, os.path.join("selects", "db", "migrations")))
else:
    print("[selects.spec] WARNING: no migrations dir; DB init will fail at runtime.")

# --- base hidden imports (the classic PyInstaller pain points) -------------
hiddenimports += collect_submodules("uvicorn")
hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    "websockets",
    "websockets.legacy",
    "anyio",
    "click",
]
hiddenimports += collect_submodules("pydantic")
hiddenimports += collect_submodules("pydantic_settings")
hiddenimports += collect_submodules("sqlalchemy")

# Native desktop window (pywebview + the OS webview backend).
hiddenimports += collect_submodules("webview")
hiddenimports += ["webview.platforms.winforms", "webview.platforms.edgechromium",
                  "webview.platforms.cocoa", "webview.platforms.gtk", "webview.platforms.qt"]


def _try_collect_all(name, want_binaries=True, want_datas=True):
    """collect_all(name) that no-ops if the package isn't importable."""
    try:
        d, b, h = collect_all(name)
    except Exception as exc:  # not installed / broken
        print(f"[selects.spec] skip {name}: {exc}")
        return
    if want_datas:
        datas.extend(d)
    if want_binaries:
        binaries.extend(b)
    hiddenimports.extend(h)


# Imaging stack — these ship native libs / data that PyInstaller misses.
for _pkg in ("PIL", "pillow_heif", "rawpy", "pyexiv2", "cv2", "numpy"):
    _try_collect_all(_pkg)

# Native window stack — pywebview and (on Windows) pythonnet/clr for WebView2.
_try_collect_all("webview")
if sys.platform == "win32":
    hiddenimports.append("clr")
    for _pkg in ("pythonnet", "clr_loader"):
        _try_collect_all(_pkg)

# --- optional ML stack -----------------------------------------------------
BUNDLE_ML = os.environ.get("SELECTS_BUNDLE_ML", "").lower() in ("1", "true", "yes")
# All models run on ONNX Runtime now — no torch/torchvision/transformers.
ML_PKGS = ["onnxruntime", "sentencepiece", "huggingface_hub", "insightface",
           "sklearn", "hdbscan", "umap"]

# torch & friends are no longer used — always exclude them so a stray dev-venv
# install can never get pulled in transitively and bloat the build by gigabytes.
DEAD_PKGS = ["torch", "torchvision", "torchcodec", "transformers", "ram"]

if BUNDLE_ML:
    print("[selects.spec] SELECTS_BUNDLE_ML set — collecting ML deps.")
    for _pkg in ML_PKGS:
        _try_collect_all(_pkg)
    excludes = list(DEAD_PKGS)
else:
    print("[selects.spec] ML deps excluded (set SELECTS_BUNDLE_ML=1 to include).")
    # Excluding keeps them from being pulled in transitively and bloating the build.
    excludes = list(ML_PKGS) + DEAD_PKGS + ["scipy", "pandas", "matplotlib", "IPython", "notebook"]

# --- optional bundled editor (darktable) -----------------------------------
# The "Edit in darktable" feature launches a real darktable install. Shipping
# darktable inside the bundle makes it work with no separate install. Opt-in
# and layout-agnostic: point ``SELECTS_DARKTABLE_DIR`` at an extracted
# darktable tree (must contain a ``bin/`` with darktable[.exe] +
# darktable-cli[.exe]), or drop it at ``<repo>/vendor/darktable``. It lands at
# ``<app>/darktable`` where routes.py:_find_editor_binary looks first.
DARKTABLE_DIR = os.environ.get(
    "SELECTS_DARKTABLE_DIR", os.path.join(REPO_ROOT, "vendor", "darktable")
)
if os.path.isdir(os.path.join(DARKTABLE_DIR, "bin")):
    datas.append((DARKTABLE_DIR, "darktable"))
    print(f"[selects.spec] bundling darktable from {DARKTABLE_DIR}")
else:
    print("[selects.spec] no darktable tree found "
          f"(looked in {DARKTABLE_DIR}); 'Edit in darktable' will fall back "
          "to a system install.")

block_cipher = None

a = Analysis(
    [ENTRY],
    pathex=[REPO_ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="selects",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    icon=ICON,
    # TODO(macOS): unsigned builds will be Gatekeeper-blocked on other
    # machines. Set codesign_identity + entitlements_file (and staple a
    # notarization ticket) before distributing a macOS build. Out of scope
    # for this pass.
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="selects",
)
