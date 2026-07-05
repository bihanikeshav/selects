"""Filesystem browsing for the folder picker (localhost-only app).

Lists directories only — never file contents — so the frontend can offer a
native-feeling folder picker instead of a raw path text input.
"""
from __future__ import annotations

import os
import string
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query


def _list_drives() -> list[dict]:
    drives = []
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        if os.path.exists(root):
            drives.append({"name": f"{letter}:", "path": root})
    return drives


def register_fs_routes(app: FastAPI) -> None:
    @app.get("/api/fs/list")
    def fs_list(path: str = Query(default="")):
        """List subdirectories of `path`; with no path, list drive roots (or / on POSIX)."""
        if not path:
            if os.name == "nt":
                return {"path": "", "parent": None, "dirs": _list_drives()}
            path = "/"

        p = Path(path).expanduser()
        if not p.exists() or not p.is_dir():
            raise HTTPException(status_code=400, detail=f"Not a directory: {path}")

        dirs = []
        try:
            for entry in sorted(os.scandir(p), key=lambda e: e.name.lower()):
                try:
                    if entry.is_dir(follow_symlinks=False) and not entry.name.startswith("."):
                        dirs.append({"name": entry.name, "path": entry.path})
                except OSError:
                    continue
        except PermissionError:
            raise HTTPException(status_code=403, detail=f"Permission denied: {path}")

        parent = str(p.parent) if p.parent != p else ""
        return {"path": str(p), "parent": parent, "dirs": dirs}
