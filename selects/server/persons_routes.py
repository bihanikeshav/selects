"""Person (face identity cluster) listing, labelling, merging and photos."""
from __future__ import annotations

import logging

from fastapi import Body, FastAPI, HTTPException, Query, Response
from sqlalchemy import select

from selects.config import FolderConfig
from selects.db import init_db, session_scope
from selects.db.models import ClassicalScore, Embedding, Photo
from selects.server.http_cache import IMMUTABLE
from selects.server.schemas import PersonList, PersonOut, PhotoList, PhotoOut

log = logging.getLogger(__name__)


def register_persons_routes(app: FastAPI, cfg: FolderConfig) -> None:
    def Session():
        return init_db(cfg.db_path)()

    @app.get("/api/persons", response_model=PersonList)
    def list_persons(
        min_confidence: float = Query(0.55, ge=0.0, le=1.0),
        min_face_px: int = Query(50, ge=0),
        min_photo_count: int = Query(2, ge=1),
        include_hidden: bool = Query(False),
    ):
        """List Person identities. Picks each cluster's BEST face (highest
        confidence × bbox-area) as the cover so a person's surfacing isn't
        gated by whichever face happened to be chosen at clustering time.

        Drops clusters whose best face fails the confidence/size thresholds —
        these are typically ArcFace false positives on paintings, animals,
        statues, etc.
        """
        from sqlalchemy import func

        from selects.db.models import FaceEmbedding, Person, PhotoPerson

        speed = "full"
        try:
            speed = getattr(cfg, "speed_mode", "full") or "full"
        except Exception:
            speed = "full"

        with session_scope(Session) as s:
            face_n = s.query(func.count(FaceEmbedding.id)).scalar() or 0
            faces_ran = bool(face_n) or speed != "fast"
            persons_all = s.query(Person).order_by(Person.photo_count.desc()).all()
            visible = [
                p for p in persons_all
                if p.photo_count >= min_photo_count and (include_hidden or not p.hidden)
            ]

            # Every candidate cluster's faces in one query, grouped in Python.
            # This used to be one round trip per person.
            faces_by_person: dict[int, list] = {}
            if visible:
                for person_id, face in (
                    s.query(PhotoPerson.person_id, FaceEmbedding)
                    .join(FaceEmbedding, PhotoPerson.face_embedding_id == FaceEmbedding.id)
                    .filter(PhotoPerson.person_id.in_([p.id for p in visible]))
                    .all()
                ):
                    faces_by_person.setdefault(person_id, []).append(face)

            persons = []
            for p in visible:
                # Pick the best face in this cluster as cover: rank by
                # confidence * sqrt(area). Skip the cluster if no face in it
                # clears the thresholds.
                face_rows = faces_by_person.get(p.id)
                if not face_rows:
                    continue
                best_face = max(
                    face_rows,
                    key=lambda f: (f.confidence or 0) * ((f.bbox_w * f.bbox_h) ** 0.5),
                )
                if best_face.confidence < min_confidence:
                    continue
                if max(best_face.bbox_w, best_face.bbox_h) < min_face_px:
                    continue
                persons.append(PersonOut(
                    id=p.id, label=p.label,
                    photo_count=p.photo_count,
                    cover_url=f"/api/face_crop/{best_face.id}",
                    hidden=bool(p.hidden),
                ))
        return PersonList(
            total=len(persons),
            persons=persons,
            speed_mode=speed,
            faces_ran=faces_ran,
        )

    @app.get("/api/face_crop/{face_id}")
    def face_crop(face_id: int):
        """Return the face's bounding box cropped from the 1024px preview."""
        from io import BytesIO

        from PIL import Image

        from selects.db.models import FaceEmbedding

        with session_scope(Session) as s:
            row = s.query(FaceEmbedding, Photo).join(
                Photo, FaceEmbedding.photo_id == Photo.id
            ).filter(FaceEmbedding.id == face_id).first()
            if not row:
                raise HTTPException(404, detail="face not found")
            fe, photo = row
            preview_abs = cfg.state_dir / photo.preview_path

        try:
            with Image.open(preview_abs) as im:
                margin = 20
                x1 = max(0, fe.bbox_x - margin)
                y1 = max(0, fe.bbox_y - margin)
                x2 = min(im.width, fe.bbox_x + fe.bbox_w + margin)
                y2 = min(im.height, fe.bbox_y + fe.bbox_h + margin)
                crop = im.crop((x1, y1, x2, y2)).convert("RGB")
                buf = BytesIO()
                crop.save(buf, "JPEG", quality=88)
                return Response(
                    content=buf.getvalue(), media_type="image/jpeg", headers=IMMUTABLE
                )
        except FileNotFoundError:
            raise HTTPException(404, detail="preview missing")

    @app.patch("/api/persons/{person_id}")
    def label_person(person_id: int, payload: dict = Body(...)):
        """Body: {label?: str | null, hidden?: bool}."""
        from selects.db.models import Person, Story

        has_label = "label" in payload
        label = payload.get("label")
        if has_label and label is not None and not isinstance(label, str):
            raise HTTPException(400, detail="label must be a string or null")
        if isinstance(label, str):
            label = label.strip() or None

        with session_scope(Session) as s:
            person = s.get(Person, person_id)
            if not person:
                raise HTTPException(404, detail="person not found")

            # Toggle hidden independently of label edits.
            if "hidden" in payload:
                person.hidden = bool(payload["hidden"])
                if not has_label:
                    s.flush()
                    return {"ok": True, "id": person_id, "hidden": person.hidden}

            old_label = person.label
            person.label = label
            s.flush()

            # Cascade rename to story titles + synthetic_day keys
            new_name = label or f"P{person_id}"
            old_name = old_label or f"P{person_id}"
            for story in s.query(Story).filter(Story.day.like("people:%")).all():
                if old_name in story.day or old_name in story.title:
                    story.day = story.day.replace(old_name, new_name)
                    story.title = story.title.replace(old_name, new_name)
                    s.add(story)

        return {"ok": True, "label": label}

    @app.post("/api/persons/merge")
    def merge_persons(payload: dict = Body(...)):
        """Merge one or more source Person identities into a target.

        Body: {target_id: int, source_ids: int[]}
        """
        from sqlalchemy import func

        from selects.db.models import Person, PhotoPerson

        target_id = payload.get("target_id")
        source_ids_raw = payload.get("source_ids")
        if not isinstance(target_id, int):
            raise HTTPException(400, detail="target_id must be an integer")
        if not isinstance(source_ids_raw, list) or not source_ids_raw:
            raise HTTPException(400, detail="source_ids must be a non-empty list")

        source_ids = []
        for value in source_ids_raw:
            if not isinstance(value, int):
                raise HTTPException(400, detail="source_ids must contain only integers")
            if value != target_id and value not in source_ids:
                source_ids.append(value)
        if not source_ids:
            raise HTTPException(400, detail="choose at least one source person")

        with session_scope(Session) as s:
            target = s.get(Person, target_id)
            if not target:
                raise HTTPException(404, detail="target person not found")

            sources = s.query(Person).filter(Person.id.in_(source_ids)).all()
            if len(sources) != len(source_ids):
                raise HTTPException(404, detail="one or more source persons were not found")

            if target.label is None:
                first_source_label = next((p.label for p in sources if p.label), None)
                if first_source_label:
                    target.label = first_source_label

            moved = 0
            source_rows = (
                s.query(PhotoPerson)
                .filter(PhotoPerson.person_id.in_(source_ids))
                .all()
            )
            # Track which target (photo_id) rows exist so two source persons in the
            # same photo don't try to insert a duplicate (photo_id, target) PK.
            target_photos: set[int] = {
                r.photo_id for r in s.query(PhotoPerson.photo_id)
                .filter(PhotoPerson.person_id == target_id).all()
            }
            for row in source_rows:
                if row.photo_id not in target_photos:
                    s.add(PhotoPerson(
                        photo_id=row.photo_id,
                        person_id=target_id,
                        face_embedding_id=row.face_embedding_id,
                        confidence=row.confidence,
                    ))
                    target_photos.add(row.photo_id)
                    moved += 1
                else:
                    existing = s.get(PhotoPerson, (row.photo_id, target_id))
                    if existing is not None and row.confidence > existing.confidence:
                        existing.face_embedding_id = row.face_embedding_id
                        existing.confidence = row.confidence
                s.delete(row)

            # Flush the photo_persons inserts/deletes BEFORE deleting the persons —
            # SQLite enforces the FK immediately, so the referencing rows must be
            # gone first (else "FOREIGN KEY constraint failed").
            s.flush()

            for person in sources:
                s.delete(person)

            s.flush()
            target.photo_count = (
                s.query(func.count(func.distinct(PhotoPerson.photo_id)))
                .filter(PhotoPerson.person_id == target_id)
                .scalar()
                or 0
            )

        return {"ok": True, "target_id": target_id, "source_ids": source_ids, "moved": moved}

    @app.get("/api/persons/{person_id}/photos", response_model=PhotoList)
    def person_photos(person_id: int, limit: int = Query(500, le=2000)):
        from selects.db.models import PhotoPerson

        with session_scope(Session) as s:
            ids = [
                r[0]
                for r in s.query(PhotoPerson.photo_id)
                .filter(PhotoPerson.person_id == person_id)
                .all()
            ]
            if not ids:
                return PhotoList(total=0, items=[])

            rows = s.execute(
                select(Photo, ClassicalScore, Embedding)
                .join(ClassicalScore, Photo.id == ClassicalScore.photo_id, isouter=True)
                .join(Embedding, Photo.id == Embedding.photo_id, isouter=True)
                .where(Photo.id.in_(ids))
                .order_by(Embedding.aesthetic_iqa.desc().nulls_last())
                .limit(limit)
            ).all()

            items = []
            for photo, classical, _emb in rows:
                items.append(PhotoOut(
                    id=photo.id, sha256=photo.sha256, path=photo.path,
                    format=photo.format, width=photo.width, height=photo.height,
                    taken_at=photo.taken_at.isoformat() if photo.taken_at else None,
                    thumb_url=f"/api/thumb/{photo.sha256}",
                    preview_url=f"/api/preview/{photo.sha256}",
                    blur=classical.blur if classical else None,
                    exposure=classical.exposure if classical else None,
                    faces_count=classical.faces_count if classical else None,
                    auto_reject=classical.auto_reject if classical else None,
                    reject_reason=classical.reject_reason if classical else None,
                ))
        return PhotoList(total=len(items), items=items)
