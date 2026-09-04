"""Photo listing, moment lookup/primary, swipes, likes, curated and summary."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import Body, FastAPI, HTTPException, Query
from sqlalchemy import select

from selects.config import FolderConfig
from selects.db import init_db, session_scope
from selects.db.models import (
    AestheticScore, ClassicalScore, Embedding, Moment, MomentMember, Photo, PhotoTag,
)
from selects.server.images_routes import _require_sha256
from selects.server.schemas import (
    MomentList, MomentMemberOut, MomentOut, PhotoList, PhotoOut,
)

log = logging.getLogger(__name__)


def register_photos_routes(app: FastAPI, cfg: FolderConfig) -> None:
    # Resolve the sessionmaker at REQUEST time (not registration time) so that
    # switching the active library at runtime is picked up. ``cfg`` may be an
    # ActiveConfigProxy that forwards to whichever library is active; ``init_db``
    # is idempotent + per-path cached, so this call is cheap. ``session_scope``
    # only ever calls ``Session()``, so a plain callable is a drop-in.
    def Session():
        return init_db(cfg.db_path)()

    @app.get("/api/photos", response_model=PhotoList)
    def list_photos(
        offset: int = Query(0, ge=0),
        limit: int = Query(200, le=2000),
        rejected: Optional[bool] = None,
        tag: Optional[str] = None,
        collapse: str = Query("moments", description="'moments' collapses to primaries only; 'none' returns all"),
        sort: str = Query(
            "taken_at",
            description="'taken_at' (default), 'aesthetic' (CLIP-IQA descending, nulls last), 'iqa', 'random'",
        ),
        min_aesthetic_pct: float = Query(
            0.0, ge=0.0, le=100.0,
            description="Drop photos whose CLIP-IQA percentile is below this value",
        ),
        quality: Optional[str] = Query(
            None,
            description=(
                "Quick-sort quality bucket: "
                "underexposed | overexposed | out_of_focus | blurry_keepers"
            ),
        ),
    ):
        from sqlalchemy import func as _func

        with session_scope(Session) as s:
            base = (
                select(Photo, ClassicalScore, Embedding, AestheticScore)
                .join(ClassicalScore, Photo.id == ClassicalScore.photo_id, isouter=True)
                .join(Embedding, Photo.id == Embedding.photo_id, isouter=True)
                .join(AestheticScore, AestheticScore.photo_id == Photo.id, isouter=True)
            )
            if rejected is True:
                base = base.where(ClassicalScore.auto_reject.is_(True))
            elif rejected is False:
                base = base.where(
                    (ClassicalScore.auto_reject.is_(False))
                    | (ClassicalScore.photo_id.is_(None))
                )
            if tag is not None:
                base = base.join(PhotoTag, Photo.id == PhotoTag.photo_id).where(
                    PhotoTag.tag == tag
                )

            # Quality-bucket filter for quick sorting (the 4 buckets the old
            # Doctor surfaced). Same thresholds, applied straight in SQL on the
            # stored ClassicalScore/AestheticScore columns — no preview decode.
            if quality == "underexposed":
                base = base.where(ClassicalScore.luma_mean < 0.32)
            elif quality == "overexposed":
                base = base.where(
                    (ClassicalScore.luma_mean > 0.78)
                    | (ClassicalScore.clipped_high > 0.07)
                )
            elif quality == "out_of_focus":
                base = base.where(ClassicalScore.blur < 150.0)
            elif quality == "blurry_keepers":
                from selects.ml.curation import compute_library_threshold
                iqa_floor = compute_library_threshold(
                    s, pct_floor=cfg.aesthetic_library_pct,
                )
                keepers = [
                    ClassicalScore.blur < 400.0,
                    ClassicalScore.blur >= 150.0,
                    Embedding.aesthetic_iqa.isnot(None),
                ]
                if iqa_floor is not None:
                    keepers.append(Embedding.aesthetic_iqa >= iqa_floor)
                base = base.where(*keepers)

            # Aesthetic-percentile floor needs the library distribution
            if min_aesthetic_pct > 0:
                from selects.ml.curation import compute_library_threshold
                aesthetic_floor_val = compute_library_threshold(
                    s, pct_floor=min_aesthetic_pct,
                )
                if aesthetic_floor_val is not None:
                    base = base.where(Embedding.aesthetic_iqa >= aesthetic_floor_val)

            # Collapse: drop non-primary moment members in SQL, before offset/limit,
            # so pagination doesn't lose photos across page boundaries.
            if collapse == "moments":
                base = base.outerjoin(
                    MomentMember, Photo.id == MomentMember.photo_id
                ).outerjoin(
                    Moment, MomentMember.moment_id == Moment.id
                ).where(
                    (MomentMember.photo_id.is_(None))
                    | (Moment.primary_photo_id == Photo.id)
                )

            # Sort's null policy is a filter (iqa) or not (aesthetic = nulls last).
            # Count AFTER every filter, then order and page.
            if sort == "iqa":
                base = base.where(Embedding.aesthetic_iqa.isnot(None))

            total = s.execute(
                select(_func.count()).select_from(base.subquery())
            ).scalar_one()

            if sort == "aesthetic":
                base = base.order_by(
                    AestheticScore.ap25_score.desc().nulls_last(),
                    Embedding.aesthetic_iqa.desc().nulls_last(),
                )
            elif sort == "iqa":
                base = base.order_by(Embedding.aesthetic_iqa.desc())
            elif sort == "random":
                base = base.order_by(_func.random())
            else:  # taken_at
                base = base.order_by(Photo.taken_at.asc().nullslast())

            rows = s.execute(base.offset(offset).limit(limit)).all()
            page_ids = [photo.id for photo, *_ in rows]
            moment_of: dict[int, tuple[int, int, bool]] = {}
            if page_ids:
                for mm_pid, mm_mid, mom_size, mom_primary in (
                    s.query(
                        MomentMember.photo_id,
                        MomentMember.moment_id,
                        Moment.size,
                        Moment.primary_photo_id,
                    )
                    .join(Moment, Moment.id == MomentMember.moment_id)
                    .filter(MomentMember.photo_id.in_(page_ids))
                    .all()
                ):
                    moment_of[mm_pid] = (mm_mid, mom_size, mm_pid == mom_primary)

            items = []
            for photo, score, emb, _aest in rows:
                moment_id: Optional[int] = None
                moment_size: Optional[int] = None
                if photo.id in moment_of:
                    moment_id, moment_size, _ = moment_of[photo.id]

                items.append(
                    PhotoOut(
                        id=photo.id,
                        sha256=photo.sha256,
                        path=photo.path,
                        format=photo.format,
                        width=photo.width,
                        height=photo.height,
                        taken_at=photo.taken_at.isoformat() if photo.taken_at else None,
                        thumb_url=f"/api/thumb/{photo.sha256}",
                        preview_url=f"/api/preview/{photo.sha256}",
                        blur=score.blur if score else None,
                        exposure=score.exposure if score else None,
                        faces_count=score.faces_count if score else None,
                        auto_reject=score.auto_reject if score else None,
                        reject_reason=score.reject_reason if score else None,
                        aesthetic_iqa=emb.aesthetic_iqa if emb else None,
                        moment_id=moment_id,
                        moment_size=moment_size,
                    )
                )
        return PhotoList(total=total, items=items)

    @app.get("/api/moments", response_model=MomentList)
    def list_moments():
        """Return all moments with their members."""
        with session_scope(Session) as s:
            moments = s.query(Moment).order_by(Moment.started_at).all()
            result = []
            for mom in moments:
                primary_photo = s.get(Photo, mom.primary_photo_id)
                member_rows = (
                    s.query(MomentMember, Photo)
                    .join(Photo, Photo.id == MomentMember.photo_id)
                    .filter(MomentMember.moment_id == mom.id)
                    .order_by(MomentMember.rank)
                    .all()
                )
                members = [
                    MomentMemberOut(
                        photo_id=p.id,
                        sha256=p.sha256,
                        rank=mm.rank,
                        thumb_url=f"/api/thumb/{p.sha256}",
                        preview_url=f"/api/preview/{p.sha256}",
                        taken_at=p.taken_at.isoformat() if p.taken_at else None,
                    )
                    for mm, p in member_rows
                ]
                result.append(
                    MomentOut(
                        id=mom.id,
                        primary_photo_id=mom.primary_photo_id,
                        primary_sha256=primary_photo.sha256 if primary_photo else "",
                        started_at=mom.started_at.isoformat(),
                        ended_at=mom.ended_at.isoformat(),
                        size=mom.size,
                        members=members,
                    )
                )
        return MomentList(total=len(result), moments=result)

    @app.get("/api/photos/{sha256}/moment", response_model=Optional[MomentOut])
    def get_photo_moment(sha256: str):
        """Return the moment a photo belongs to, or null if it's not in a moment."""
        with session_scope(Session) as s:
            photo = s.query(Photo).filter(Photo.sha256 == sha256).first()
            if photo is None:
                raise HTTPException(404, detail="Photo not found")

            mm = s.query(MomentMember).filter(MomentMember.photo_id == photo.id).first()
            if mm is None:
                return None

            mom = s.get(Moment, mm.moment_id)
            if mom is None:
                return None

            primary_photo = s.get(Photo, mom.primary_photo_id)
            member_rows = (
                s.query(MomentMember, Photo)
                .join(Photo, Photo.id == MomentMember.photo_id)
                .filter(MomentMember.moment_id == mom.id)
                .order_by(MomentMember.rank)
                .all()
            )
            members = [
                MomentMemberOut(
                    photo_id=p.id,
                    sha256=p.sha256,
                    rank=m.rank,
                    thumb_url=f"/api/thumb/{p.sha256}",
                    preview_url=f"/api/preview/{p.sha256}",
                    taken_at=p.taken_at.isoformat() if p.taken_at else None,
                )
                for m, p in member_rows
            ]
            return MomentOut(
                id=mom.id,
                primary_photo_id=mom.primary_photo_id,
                primary_sha256=primary_photo.sha256 if primary_photo else "",
                started_at=mom.started_at.isoformat(),
                ended_at=mom.ended_at.isoformat(),
                size=mom.size,
                members=members,
            )

    @app.patch("/api/moments/{moment_id}/primary")
    def set_moment_primary(moment_id: int, payload: dict = Body(...)):
        """Set the top-of-stack photo for a moment.

        Body: {photo_id: int}

        Persists the user's choice so subsequent curated views surface this
        photo as the visible member of the burst stack.
        """
        photo_id = payload.get("photo_id")
        if not isinstance(photo_id, int):
            raise HTTPException(400, detail="photo_id must be an int")

        with session_scope(Session) as s:
            mom = s.get(Moment, moment_id)
            if mom is None:
                raise HTTPException(404, detail="moment not found")
            member = (
                s.query(MomentMember)
                .filter(MomentMember.moment_id == moment_id)
                .filter(MomentMember.photo_id == photo_id)
                .first()
            )
            if member is None:
                raise HTTPException(
                    400, detail=f"photo_id {photo_id} is not a member of moment {moment_id}",
                )
            mom.primary_photo_id = photo_id

            # Re-order MomentMember.rank so the chosen photo is rank 0
            all_members = (
                s.query(MomentMember)
                .filter(MomentMember.moment_id == moment_id)
                .order_by(MomentMember.rank)
                .all()
            )
            # Pull the chosen one to the front, keep others in their existing order
            new_order = [mm for mm in all_members if mm.photo_id == photo_id]
            new_order.extend(mm for mm in all_members if mm.photo_id != photo_id)
            for rank, mm in enumerate(new_order):
                mm.rank = rank

        return {"ok": True, "moment_id": moment_id, "primary_photo_id": photo_id}

    # ── Cull swipes (keep/reject persistence) ────────────────────────────────
    @app.post("/api/swipes/{sha256}")
    def record_swipe(sha256: str, decision: str = Body(..., embed=True)):
        from selects.db.models import Swipe

        _require_sha256(sha256)
        if decision not in ("keep", "reject", "silver", "skip"):
            raise HTTPException(400, detail="decision must be keep, reject, silver, or skip")

        with session_scope(Session) as s:
            photo = s.query(Photo).filter(Photo.sha256 == sha256).first()
            if not photo:
                raise HTTPException(404, detail="photo not found")
            existing = s.get(Swipe, photo.id)
            if existing:
                existing.decision = decision
                s.add(existing)
            else:
                s.add(Swipe(photo_id=photo.id, decision=decision))
        return {"ok": True, "decision": decision}

    @app.get("/api/curated")
    def list_curated(
        sort: str = Query("aesthetic", description="aesthetic | taken_at"),
    ):
        """Return all photos the user has kept (Swipe.decision in keep/silver),
        sorted by CLIP-IQA descending (nulls last) by default.

        This is the curated set — the user's chosen keepers post-cull,
        post-curate, ready for edit and post. Kept photos missing IQA
        stay in the set; they sort last under sort=aesthetic.
        """
        from selects.db.models import Swipe

        with session_scope(Session) as s:
            base = (
                s.query(Photo, Embedding.aesthetic_iqa)
                .join(Swipe, Swipe.photo_id == Photo.id)
                .outerjoin(Embedding, Embedding.photo_id == Photo.id)
                .filter(Swipe.decision.in_(["keep", "silver"]))
            )
            if sort == "aesthetic":
                base = base.order_by(Embedding.aesthetic_iqa.desc().nulls_last())
            rows = base.all()
            entries = []
            for photo, iqa in rows:
                iqa_f = float(iqa) if iqa is not None else None
                entries.append({
                    "photo_id": photo.id,
                    "sha256": photo.sha256,
                    "taken_at": photo.taken_at.isoformat() if photo.taken_at else None,
                    "thumb_url": f"/api/thumb/{photo.sha256}",
                    "preview_url": f"/api/preview/{photo.sha256}",
                    "combined": iqa_f,
                    "iqa": iqa_f,
                    "ap25": None,
                    "nima": None,
                })
            if sort != "aesthetic":
                entries.sort(key=lambda e: e["taken_at"] or "")
        return {"total": len(entries), "photos": entries}

    @app.get("/api/likes/status")
    def likes_status(shas: str = Query("", description="comma-separated sha256s")):
        """Return {sha256: kept_bool} for the given list."""
        from selects.db.models import Swipe
        sha_list = [s for s in shas.split(",") if s.strip()] if shas else None
        out: dict[str, bool] = {}
        with session_scope(Session) as s:
            q = s.query(Photo.sha256, Swipe.decision).join(
                Swipe, Swipe.photo_id == Photo.id
            )
            if sha_list:
                q = q.filter(Photo.sha256.in_(sha_list))
            for sha, decision in q.all():
                out[sha] = decision in ("keep", "silver")
        return out

    @app.get("/api/swipes/summary")
    def swipes_summary():
        from sqlalchemy import func

        from selects.db.models import Swipe

        with session_scope(Session) as s:
            rows = s.query(Swipe.decision, func.count(Swipe.photo_id)).group_by(Swipe.decision).all()
            counts = {d: c for d, c in rows}
            total_photos = s.query(Photo).count()
        return {
            "total_photos": total_photos,
            "kept": counts.get("keep", 0) + counts.get("silver", 0),
            "rejected": counts.get("reject", 0),
            "skipped": counts.get("skip", 0),
            "undecided": total_photos - sum(counts.values()),
        }

    @app.get("/api/search")
    def search(q: str = Query(..., min_length=1), k: int = Query(60, le=300)):
        """Free-text photo search via SigLIP image-text similarity."""
        from selects.ml.search import search_photos

        results = search_photos(cfg, q, k=k)
        return {
            "query": q,
            "total": len(results),
            "results": [
                {
                    "photo_id": pid,
                    "sha256": sha,
                    "score": score,
                    "thumb_url": f"/api/thumb/{sha}",
                    "preview_url": f"/api/preview/{sha}",
                }
                for pid, sha, score in results
            ],
        }
