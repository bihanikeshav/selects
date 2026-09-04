from __future__ import annotations

import asyncio
import hmac
from typing import AsyncIterator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from .fs_routes import is_loopback_host

#: Cookie the UI carries once a valid ``?token=`` has been presented once.
LAN_COOKIE = "selects_token"

_BUS: "ProgressBus | None" = None


def lan_auth_ok(
    lan_token: str | None,
    client_host: str,
    *,
    query_token: str | None = None,
    cookie: str | None = None,
    authorization: str | None = None,
) -> bool:
    """Whether a request/websocket from *client_host* may pass the LAN gate.

    Shared by the HTTP middleware in ``app.py`` and the websocket handler below
    so both accept exactly the same credentials.
    """
    if not lan_token:
        return True
    if is_loopback_host(client_host):
        return True
    return (
        _matches(authorization, f"Bearer {lan_token}")
        or _matches(query_token, lan_token)
        or _matches(cookie, lan_token)
    )


def _matches(candidate: str | None, secret: str) -> bool:
    """Constant-time comparison that tolerates a missing candidate."""
    if candidate is None:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), secret.encode("utf-8"))


class ProgressBus:
    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue] = []

    async def publish(self, msg: dict) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    async def subscribe(self) -> AsyncIterator[dict]:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.append(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._subscribers.remove(q)


def progress_bus() -> ProgressBus:
    global _BUS
    if _BUS is None:
        _BUS = ProgressBus()
    return _BUS


def register_ws(app: FastAPI, lan_token: str | None = None) -> None:
    @app.websocket("/ws/progress")
    async def ws_progress(websocket: WebSocket) -> None:
        client = websocket.client.host if websocket.client else ""
        if not lan_auth_ok(
            lan_token,
            client,
            query_token=websocket.query_params.get("token"),
            cookie=websocket.cookies.get(LAN_COOKIE),
        ):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        bus = progress_bus()
        try:
            async for msg in bus.subscribe():
                await websocket.send_json(msg)
        except WebSocketDisconnect:
            return
