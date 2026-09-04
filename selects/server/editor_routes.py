"""External editor integration: darktable launch/export, generic open, XMP status."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, HTTPException, Query

from selects.config import FolderConfig
from selects.db import init_db, session_scope
from selects.db.models import Photo

log = logging.getLogger(__name__)


def _detached_popen_kwargs() -> dict:
    """subprocess.Popen kwargs to fully detach a launched GUI editor.

    Windows: DETACHED_PROCESS (no console window, survives parent exit).
    POSIX (macOS/Linux): start_new_session puts the child in its own
    session so it isn't tied to the server process/terminal.
    """
    import subprocess
    import sys

    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "DETACHED_PROCESS", 0)}
    return {"start_new_session": True}


def _find_editor_binary(
    names: list[str],
    windows_candidates: list[Path],
    bundle_subdir: Optional[str] = None,
) -> Optional[str]:
    """Locate an external editor binary across Windows/macOS/Linux.

    Resolution order:

      1. A copy bundled inside the app (PyInstaller onedir). When the
         release build ships ``<app>/darktable/bin`` alongside the binary,
         we use it first so editing "just works" with no system install.
      2. ``shutil.which`` for each of ``names`` (binary on PATH).
      3. Well-known per-platform install locations:
         * Windows: caller-supplied ``windows_candidates``.
         * macOS: ``/Applications/<app>.app/Contents/MacOS/<name>``.
         * Linux: ``/usr/bin``, ``/usr/local/bin``, and — best-effort —
           the Flatpak export path for darktable.
    """
    import platform
    import shutil
    import sys

    system = platform.system()

    # (1) Bundled copy — data files land under sys._MEIPASS in a frozen
    # onedir build (the app's `_internal` dir). Skipped when not frozen.
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root and bundle_subdir:
        suffix = ".exe" if system == "Windows" else ""
        for name in names:
            cand = Path(bundle_root) / bundle_subdir / "bin" / f"{name}{suffix}"
            if cand.exists():
                return str(cand)

    # (2) On PATH.
    for name in names:
        found = shutil.which(name)
        if found:
            return found

    candidates: list[Path] = []
    if system == "Windows":
        candidates.extend(windows_candidates)
    elif system == "Darwin":
        for name in names:
            # darktable and darktable-cli both live inside darktable.app;
            # take the part before the first "-" as the app bundle name.
            app_name = name.split("-")[0]
            candidates.append(
                Path("/Applications")
                / f"{app_name}.app" / "Contents" / "MacOS" / name
            )
    else:  # Linux and other POSIX systems
        for base in (Path("/usr/bin"), Path("/usr/local/bin")):
            for name in names:
                candidates.append(base / name)
        if any(n.startswith("darktable") for n in names):
            # Flatpak exports use the app's reverse-DNS id rather than
            # the raw binary name; best-effort guess.
            candidates.append(
                Path("/var/lib/flatpak/exports/bin/org.darktable.Darktable")
            )

    for cand in candidates:
        if cand.exists():
            return str(cand)
    return None


def _find_darktable() -> Optional[str]:
    """Return the path to the darktable executable, if discoverable."""
    return _find_editor_binary(
        ["darktable"],
        windows_candidates=[
            Path(r"C:\Program Files\darktable\bin\darktable.exe"),
            Path(r"C:\Program Files (x86)\darktable\bin\darktable.exe"),
            Path(r"C:\Program Files\darktable\darktable.exe"),
            Path(r"C:\darktable\bin\darktable.exe"),
        ],
        bundle_subdir="darktable",
    )


def _find_darktable_cli() -> Optional[str]:
    """Same auto-discovery for darktable-cli (ships next to darktable)."""
    return _find_editor_binary(
        ["darktable-cli"],
        windows_candidates=[
            Path(r"C:\Program Files\darktable\bin\darktable-cli.exe"),
            Path(r"C:\Program Files (x86)\darktable\bin\darktable-cli.exe"),
        ],
        bundle_subdir="darktable",
    )


def _find_named_editor(editor: str) -> Optional[str]:
    """Resolve an allowlisted editor name to a binary path."""
    if editor == "darktable":
        return _find_darktable()
    if editor == "rawtherapee":
        return _find_editor_binary(
            ["rawtherapee"],
            windows_candidates=[
                Path(r"C:\Program Files\RawTherapee\rawtherapee.exe"),
                Path(r"C:\Program Files (x86)\RawTherapee\rawtherapee.exe"),
            ],
        )
    if editor == "gimp":
        return _find_editor_binary(
            ["gimp", "gimp-2.10", "gimp-3"],
            windows_candidates=[
                Path(r"C:\Program Files\GIMP 3\bin\gimp-3.exe"),
                Path(r"C:\Program Files\GIMP 2\bin\gimp-2.10.exe"),
            ],
        )
    return None


def register_editor_routes(app: FastAPI, cfg: FolderConfig) -> None:
    def Session():
        return init_db(cfg.db_path)()

    @app.get("/api/edits/status")
    def edits_status(shas: str = Query("", description="comma-separated sha256 list")):
        """Report which of the given photos have an XMP sidecar.

        XMP next to an original = darktable (or any editor) has saved develop
        instructions for it. The presence of a fresh XMP = "edited".
        """
        sha_list = [s for s in shas.split(",") if s.strip()] if shas else None
        out: dict[str, dict] = {}
        with session_scope(Session) as s:
            q = s.query(Photo.sha256, Photo.path)
            if sha_list:
                q = q.filter(Photo.sha256.in_(sha_list))
            for sha, path_str in q.all():
                p = Path(path_str)
                xmp = p.with_suffix(p.suffix + ".xmp")
                alt = p.with_suffix(".xmp")
                edited = False
                mtime = None
                for cand in (xmp, alt):
                    if cand.exists():
                        edited = True
                        mtime = cand.stat().st_mtime
                        break
                out[sha] = {"edited": edited, "mtime": mtime}
        return out

    @app.post("/api/edit/darktable")
    def launch_darktable(payload: dict = Body(...)):
        """Launch darktable with the selected originals in a per-session library.

        Body: {sha256s: list[str]}

        Per-session library avoids polluting the user's main catalog and lets
        us round-trip XMP edits cleanly. Darktable writes XMPs next to the
        original file when the user saves — no further coordination needed.
        """
        import subprocess
        import uuid

        sha256s = payload.get("sha256s") or []
        if not isinstance(sha256s, list) or not sha256s:
            raise HTTPException(400, detail="sha256s must be a non-empty list of strings")

        editor_cmd = _find_darktable()
        if not editor_cmd:
            raise HTTPException(
                400,
                detail=(
                    "darktable not found. Install it from "
                    "https://www.darktable.org/install/ — the launcher checks "
                    "PATH plus common per-OS install locations "
                    "(e.g. Program Files on Windows, /Applications on macOS, "
                    "/usr/bin or Flatpak on Linux)."
                ),
            )

        with session_scope(Session) as s:
            paths = [
                r[0]
                for r in s.query(Photo.path).filter(Photo.sha256.in_(sha256s)).all()
            ]
        if not paths:
            raise HTTPException(404, detail="no matching photos")

        session_id = uuid.uuid4().hex[:8]
        lib_dir = cfg.state_dir / "darktable-sessions"
        lib_dir.mkdir(parents=True, exist_ok=True)
        library_path = lib_dir / f"session-{session_id}.db"

        cmd = [editor_cmd, "--library", str(library_path), *paths]
        try:
            subprocess.Popen(cmd, close_fds=True, **_detached_popen_kwargs())
        except Exception as exc:
            raise HTTPException(500, detail=f"failed to launch darktable: {exc}")

        return {"opened": len(paths), "session": session_id, "library": str(library_path)}

    @app.post("/api/edits/export")
    def export_edits(payload: dict = Body(...)):
        """Render XMP edits to JPEGs via darktable-cli into
        .selects/exports/<cluster>/<timestamp>/.

        Body: {sha256s: list[str], cluster_name?: str, width?: int, height?: int}.
        """
        import subprocess
        from datetime import datetime

        sha256s = payload.get("sha256s") or []
        cluster_name = payload.get("cluster_name") or "untitled"
        width = int(payload.get("width") or 2048)
        height = int(payload.get("height") or 0)
        if not isinstance(sha256s, list) or not sha256s:
            raise HTTPException(400, detail="sha256s must be a non-empty list")

        dt_cli = _find_darktable_cli()
        if not dt_cli:
            raise HTTPException(
                400,
                detail="darktable-cli not found. It ships with darktable; install from "
                       "https://www.darktable.org/install/ or add its bin dir to PATH.",
            )

        with session_scope(Session) as s:
            rows = s.query(Photo.sha256, Photo.path).filter(
                Photo.sha256.in_(sha256s)
            ).all()
        if not rows:
            raise HTTPException(404, detail="no matching photos")

        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        clean = "".join(c if c.isalnum() or c in "-_" else "_" for c in cluster_name) or "untitled"
        out_dir = cfg.state_dir / "exports" / clean / ts
        out_dir.mkdir(parents=True, exist_ok=True)

        results = []
        for sha, path_str in rows:
            src = Path(path_str)
            out = out_dir / f"{src.stem}.jpg"
            cmd = [
                dt_cli,
                str(src),
                str(out),
                "--width", str(width),
                "--height", str(height),
                "--core", "--conf", "plugins/imageio/format/jpeg/quality=92",
            ]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
                ok = proc.returncode == 0 and out.exists()
                results.append({
                    "sha256": sha, "ok": ok,
                    "out": str(out) if ok else None,
                    "stderr": (proc.stderr[-300:] if proc.stderr else None) if not ok else None,
                })
            except subprocess.TimeoutExpired:
                results.append({"sha256": sha, "ok": False, "out": None, "stderr": "timeout"})

        return {
            "out_dir": str(out_dir),
            "total": len(results),
            "exported": sum(1 for r in results if r["ok"]),
            "results": results,
        }

    @app.post("/api/edit/open")
    def open_in_editor(payload: dict = Body(...)):
        """Launch the chosen OSS editor with the original photo paths for the
        given sha256s. Editor runs detached — server returns immediately.

        Body: {sha256s: list[str], editor?: "darktable"|"rawtherapee"|"gimp"}.
        """
        import subprocess

        sha256s = payload.get("sha256s") or []
        editor = payload.get("editor") or "darktable"
        if not isinstance(sha256s, list) or not sha256s:
            raise HTTPException(400, detail="sha256s must be a non-empty list")
        if not isinstance(editor, str):
            raise HTTPException(400, detail="editor must be darktable, rawtherapee, or gimp")
        editor = editor.strip().lower()
        if editor not in ("darktable", "rawtherapee", "gimp"):
            raise HTTPException(400, detail="editor must be darktable, rawtherapee, or gimp")

        editor_cmd = _find_named_editor(editor)
        if not editor_cmd:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{editor}' not found. Install it or pick a different "
                    "editor (darktable / rawtherapee / gimp)."
                ),
            )

        with session_scope(Session) as s:
            rows = s.query(Photo.path).filter(Photo.sha256.in_(sha256s)).all()
            paths = [r[0] for r in rows]
        if not paths:
            raise HTTPException(404, detail="no matching photos")

        try:
            subprocess.Popen(
                [editor_cmd, *paths], close_fds=True, **_detached_popen_kwargs()
            )
        except Exception as exc:
            raise HTTPException(500, detail=f"failed to launch {editor}: {exc}")
        return {"opened": len(paths), "editor": editor}
