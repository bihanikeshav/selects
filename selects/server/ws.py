from __future__ import annotations

import asyncio
from typing import AsyncIterator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

_BUS: "ProgressBus | None" = None


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


def register_ws(app: FastAPI) -> None:
    @app.websocket("/ws/progress")
    async def ws_progress(websocket: WebSocket) -> None:
        await websocket.accept()
        bus = progress_bus()
        try:
            async for msg in bus.subscribe():
                await websocket.send_json(msg)
        except WebSocketDisconnect:
            return
