"""Filesystem browsing for the folder picker (localhost-only app).

Lists directories only — never file contents — so the frontend can offer a
native-feeling folder picker instead of a raw path text input.

Disabled when the process is bound to a non-loopback address, and always
refuses non-loopback clients (LAN).
"""
from __future__ import annotations

import os
import string
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request

_LOOPBACK_NAMES = frozenset({"::1", "localhost", "testclient", "test"})


def is_loopback_host(host: str | None) -> bool:
    """True for IPv4/IPv6 loopback (and Starlette TestClient's 'testclient')."""
    if not host:
        return False
    h = host.strip().lower()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if h.startswith("::ffff:"):
        h = h[7:]
    if h in _LOOPBACK_NAMES:
        return True
    parts = h.split(".")
    return len(parts) == 4 and parts[0] == "127" and all(p.isdigit() for p in parts)


def _list_drives() -> list[dict]:
    drives = []
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        if os.path.exists(root):
            drives.append({"name": f"{letter}:", "path": root})
    return drives


def register_fs_routes(app: FastAPI, bind_host: str = "127.0.0.1") -> None:
    bind_ok = is_loopback_host(bind_host)

    @app.get("/api/fs/list")
    def fs_list(request: Request, path: str = Query(default="")):
        """List subdirectories of `path`; with no path, list drive roots (or / on POSIX)."""
        client_host = request.client.host if request.client else ""
        if not bind_ok or not is_loopback_host(client_host):
            raise HTTPException(
                status_code=403,
                detail="Filesystem browsing is only available on localhost",
            )
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
