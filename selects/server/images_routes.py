"""Image delivery: thumbs, previews, the in-app editor and enhance previews."""
from __future__ import annotations

import logging

from fastapi import FastAPI, File, Form, HTTPException, Query, Response, UploadFile

from selects.config import FolderConfig
from selects.db import init_db, session_scope
from selects.db.models import ClassicalScore, Photo
from selects.server.http_cache import IMMUTABLE, jpeg_file
from selects.server.schemas import require_sha256
from selects.util import utcnow

log = logging.getLogger(__name__)


def _serve_image_for(cfg: FolderConfig, sha256: str, kind: str):
    require_sha256(sha256)
    parent = cfg.thumbs_dir if kind == "thumb" else cfg.previews_dir
    path = parent / f"{sha256}.jpg"
    if not path.exists():
        raise HTTPException(404, detail=f"{kind} not found")
    return jpeg_file(path)


def register_images_routes(app: FastAPI, cfg: FolderConfig) -> None:
    def Session():
        return init_db(cfg.db_path)()

    @app.get("/api/thumb/{sha256:path}")
    def thumb(sha256: str):
        return _serve_image_for(cfg, sha256, kind="thumb")

    @app.get("/api/preview/{sha256:path}")
    def preview(sha256: str):
        return _serve_image_for(cfg, sha256, kind="preview")

    # ── in-app editor (non-destructive) ──────────────────────────────────────
    @app.get("/api/editor/params/{sha256}")
    def editor_params(sha256: str):
        """Return the saved editor slider params for a photo, or null."""
        import json

        from selects.db.models import PhotoEdit

        with session_scope(Session) as s:
            photo = s.query(Photo).filter(Photo.sha256 == sha256).first()
            if not photo:
                raise HTTPException(404, detail="photo not found")
            pe = s.get(PhotoEdit, photo.id)
            return {"params": json.loads(pe.params) if pe else None}

    @app.post("/api/editor/save/{sha256:path}")
    async def editor_save(
        sha256: str,
        params: str = Form(...),
        image: UploadFile = File(...),
    ):
        """Persist the editor params and the baked JPEG (<state>/edits/<sha>.jpg)."""
        from selects.db.models import PhotoEdit

        require_sha256(sha256)
        data = await image.read()
        with session_scope(Session) as s:
            photo = s.query(Photo).filter(Photo.sha256 == sha256).first()
            if not photo:
                raise HTTPException(404, detail="photo not found")
            edits_dir = cfg.state_dir / "edits"
            edits_dir.mkdir(parents=True, exist_ok=True)
            (edits_dir / f"{sha256}.jpg").write_bytes(data)
            pe = s.get(PhotoEdit, photo.id) or PhotoEdit(photo_id=photo.id)
            pe.params = params
            pe.updated_at = utcnow()
            s.add(pe)
        return {"ok": True}

    @app.get("/api/editor/result/{sha256:path}")
    def editor_result(sha256: str):
        """Serve the baked edited JPEG if one exists, else the preview."""
        require_sha256(sha256)
        out = cfg.state_dir / "edits" / f"{sha256}.jpg"
        if out.exists():
            return jpeg_file(out)
        return _serve_image_for(cfg, sha256, kind="preview")

    @app.get("/api/enhance/{sha256:path}")
    def enhance(
        sha256: str,
        preset: str = Query("film"),
        straighten: bool = Query(False, description="Apply quick auto-straighten"),
        grade: bool = Query(True, description="Apply the classical auto_tone edit"),
    ):
        """Render an edited preview. Either or both of grade/straighten can be
        applied independently.

        Cached per-(sha, preset, grade, straighten).
        """
        require_sha256(sha256)
        from io import BytesIO

        from PIL import Image

        from selects.classical.aesthetic_grade import aesthetic_grade
        from selects.classical.straighten import straighten as do_straighten

        if preset not in ("film", "clarity", "portrait"):
            preset = "film"

        # Build a cache key reflecting all toggles independently.
        parts = []
        if grade:
            parts.append(preset)
        if straighten:
            parts.append("straight")
        if not parts:
            return _serve_image_for(cfg, sha256, kind="preview")
        suffix = "-".join(parts)

        cached = cfg.state_dir / "enhanced" / "v4" / f"{sha256}-{suffix}.jpg"
        cached.parent.mkdir(parents=True, exist_ok=True)
        if cached.exists():
            return jpeg_file(cached)

        preview_path = cfg.previews_dir / f"{sha256}.jpg"
        if not preview_path.exists():
            raise HTTPException(404, detail="preview missing")

        has_face = False
        if grade:
            with session_scope(Session) as s:
                row = (
                    s.query(ClassicalScore.faces_count)
                    .join(Photo, Photo.id == ClassicalScore.photo_id)
                    .filter(Photo.sha256 == sha256)
                    .first()
                )
                if row and row[0] and row[0] > 0:
                    has_face = True

        with Image.open(preview_path) as im:
            out = im
            if straighten:
                out, _angle = do_straighten(out)
            if grade:
                # Enhancement is the assertive Lightroom-style classical auto_tone
                # (the primary, and now only, enhance — it looks good and is
                # instant). All ML enhancers were retired.
                out = aesthetic_grade(out, preset=preset, has_face=has_face)
            buf = BytesIO()
            out.convert("RGB").save(buf, "JPEG", quality=90)
            cached.write_bytes(buf.getvalue())
            return Response(
                content=buf.getvalue(), media_type="image/jpeg", headers=IMMUTABLE
            )

    @app.get("/api/doctor/histogram/{sha256:path}")
    def doctor_histogram(sha256: str):
        """Return per-channel + luminance histogram for a photo (64 bins each).

        Used by the ScoresCard preview to show RGB+luma distribution.
        """
        require_sha256(sha256)
        import numpy as _np
        from PIL import Image as _PILImage

        preview_path = cfg.previews_dir / f"{sha256}.jpg"
        if not preview_path.exists():
            raise HTTPException(404, detail="preview missing")
        with _PILImage.open(preview_path) as im:
            arr = _np.asarray(im.convert("RGB"), dtype=_np.uint8)
        bins = 64
        edges = _np.linspace(0, 256, bins + 1)
        r = _np.histogram(arr[..., 0], bins=edges)[0].astype(int)
        g = _np.histogram(arr[..., 1], bins=edges)[0].astype(int)
        b = _np.histogram(arr[..., 2], bins=edges)[0].astype(int)
        luma = (0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]).astype(_np.uint8)
        luma_hist = _np.histogram(luma, bins=edges)[0].astype(int)
        return {
            "bins": bins,
            "r": r.tolist(),
            "g": g.tolist(),
            "b": b.tolist(),
            "luma": luma_hist.tolist(),
        }
