"""Face embedding stage: extract and store ArcFace embeddings for all faces.

Distinct from travelcull/classical/faces.py which only returns face counts.
This module stores per-face 512-d ArcFace embeddings into face_embeddings table.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from travelcull.classical.faces import detect_faces
from travelcull.config import FolderConfig
from travelcull.db import init_db, session_scope
from travelcull.db.models import ClassicalScore, FaceEmbedding, Photo
from travelcull.ml.face_attributes import compute_face_attributes

log = logging.getLogger(__name__)


def run_face_embedding_stage(
    cfg: FolderConfig,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> int:
    """Extract ArcFace embeddings for all photos that have faces but no embeddings yet.

    Returns the number of photos processed.
    """
    Session = init_db(cfg.db_path)

    with session_scope(Session) as s:
        # Photos that have ≥1 face detected in classical stage
        face_photo_ids: set[int] = {
            r[0]
            for r in s.query(ClassicalScore.photo_id)
            .filter(ClassicalScore.faces_count > 0)
            .all()
        }
        # Photos that already have face embeddings stored
        done_ids: set[int] = {
            r[0]
            for r in s.query(FaceEmbedding.photo_id).distinct().all()
        }
        pending_ids = sorted(face_photo_ids - done_ids)
        id_to_preview: dict[int, str | None] = {
            r[0]: r[1]
            for r in s.query(Photo.id, Photo.preview_path)
            .filter(Photo.id.in_(pending_ids))
            .all()
        }

    if not pending_ids:
        log.info("face_embed: all photos already embedded, nothing to do")
        return 0

    total = len(pending_ids)
    log.info("face_embed: processing %d photos with faces", total)
    processed = 0

    for i, photo_id in enumerate(pending_ids):
        preview_path = id_to_preview.get(photo_id)
        if on_progress:
            on_progress(i + 1, total, preview_path or str(photo_id))

        if not preview_path:
            log.warning("face_embed: photo %s has no preview_path, skipping", photo_id)
            continue

        preview_abs = cfg.state_dir / preview_path
        if not Path(preview_abs).exists():
            log.warning("face_embed: preview not found: %s", preview_abs)
            continue

        try:
            with Image.open(preview_abs) as im:
                img = np.asarray(im.convert("RGB"), dtype=np.uint8)

            faces = detect_faces(img)
            if not faces:
                # classical said there were faces, but re-run finds none — skip
                continue

            with session_scope(Session) as s:
                for face_index, face in enumerate(faces):
                    if face.embedding is None:
                        log.warning(
                            "face_embed: photo %s face %d has no embedding (insightface returned None)",
                            photo_id, face_index,
                        )
                        continue
                    blob = face.embedding.astype(np.float16).tobytes()
                    attrs = compute_face_attributes(
                        img_w=img.shape[1],
                        img_h=img.shape[0],
                        bbox_w=face.w,
                        bbox_h=face.h,
                        kps=face.kps,
                        landmark_2d_106=face.landmark_2d_106,
                        pose=face.pose,
                    )
                    fe = FaceEmbedding(
                        photo_id=photo_id,
                        face_index=face_index,
                        embedding=blob,
                        bbox_x=face.x,
                        bbox_y=face.y,
                        bbox_w=face.w,
                        bbox_h=face.h,
                        confidence=face.confidence,
                        eyes_open=attrs.eyes_open,
                        yaw=attrs.yaw,
                        pitch=attrs.pitch,
                        face_area_ratio=attrs.area_ratio,
                    )
                    s.add(fe)

            processed += 1

        except Exception as exc:
            log.warning("face_embed: failed on photo %s: %s", photo_id, exc)

    log.info("face_embed: done — %d photos embedded", processed)
    return processed
