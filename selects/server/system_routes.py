"""System capability endpoint — reports CPU vs GPU backend for the UI so the
indexing screen can set expectations (and nudge CPU users toward a GPU build).
"""
from __future__ import annotations

from fastapi import FastAPI


def register_system_routes(app: FastAPI, cfg=None) -> None:
    @app.get("/api/system")
    def system() -> dict:
        from selects.gpu import detect_capabilities

        caps = detect_capabilities()
        speed = "full"
        try:
            if cfg is not None:
                speed = getattr(cfg, "speed_mode", "full") or "full"
        except Exception:
            speed = "full"
        return {
            "backend": "gpu" if caps.gpu_available else "cpu",
            "gpu_available": caps.gpu_available,
            "provider": caps.provider,
            "device_name": caps.device_name,
            "vram_total_mb": caps.vram_total_mb,
            "speed_mode": speed,
        }
