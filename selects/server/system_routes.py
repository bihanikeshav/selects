"""System capability endpoint — reports CPU vs GPU backend for the UI so the
indexing screen can set expectations (and nudge CPU users toward a GPU build).
"""
from __future__ import annotations

from fastapi import FastAPI


def register_system_routes(app: FastAPI) -> None:
    @app.get("/api/system")
    def system() -> dict:
        from selects.gpu import detect_capabilities

        caps = detect_capabilities()
        return {
            "backend": "gpu" if caps.cuda_available else "cpu",
            "cuda_available": caps.cuda_available,
            "device_name": caps.device_name,
            "vram_total_mb": caps.vram_total_mb,
        }
