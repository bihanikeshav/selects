"""Photo listing, moment lookup/primary, swipes, likes, curated and summary."""
from __future__ import annotations

import logging
from typing import Literal, Optional

from fastapi import Body, FastAPI, HTTPException, Query
from sqlalchemy import select

from selects.config import FolderConfig
from selects.db import init_db, session_scope
from selects.db.models import (
    AestheticScore, ClassicalScore, Embedding, Moment, MomentMember, Photo, PhotoTag,
)
from selects.server.schemas import (
    MomentMemberOut, MomentOut, PhotoList, QualityBucket, StatusRequest, photo_out,
    require_sha256,
)
from selects.util import KEEP_DECISIONS, chunked

log = logging.getLogger(__name__)


def collapse_to_moment_primaries(stmt):
    """Restrict a Photo-rooted statement to one row per burst.

    A photo survives when it belongs to no moment, or when it is its moment's
    primary. ``/api/photos?collapse=moments`` and ``/api/swipes/summary`` share
    this predicate so their totals always agree.
    """
    return (
        stmt.outerjoin(MomentMember, Photo.id == MomentMember.photo_id)
        .outerjoin(Moment, MomentMember.moment_id == Moment.id)
        .where(
            (MomentMember.photo_id.is_(None))
            | (Moment.primary_photo_id == Photo.id)
        )
    )


def apply_quality_filter(stmt, quality: QualityBucket, s, cfg):
    """Apply the quick-sort quality-bucket predicate to a Photo-rooted statement.

    The statement must already be outer-joined to ``ClassicalScore`` and
    ``Embedding``. ``/api/photos`` and ``/api/swipes/summary`` both call this so
    a filtered list and its tally can never disagree. Same thresholds the old
    Doctor used, applied straight in SQL — no preview decode.
    """
    if quality == "underexposed":
        return stmt.where(ClassicalScore.luma_mean < 0.32)
    if quality == "overexposed":
        return stmt.where(
            (ClassicalScore.luma_mean > 0.78) | (ClassicalScore.clipped_high > 0.07)
        )
    if quality == "out_of_focus":
        return stmt.where(ClassicalScore.blur < 150.0)
    if quality == "blurry_keepers":
        from selects.ml.curation import compute_library_threshold
        iqa_floor = compute_library_threshold(s, pct_floor=cfg.aesthetic_library_pct)
        keepers = [
            ClassicalScore.blur < 400.0,
            ClassicalScore.blur >= 150.0,
            Embedding.aesthetic_iqa.isnot(None),
        ]
        if iqa_floor is not None:
            keepers.append(Embedding.aesthetic_iqa >= iqa_floor)
        return stmt.where(*keepers)
    return stmt


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
        collapse: Literal["moments", "none"] = Query(
            "moments",
            description="'moments' collapses to primaries only; 'none' returns all",
        ),
        sort: str = Query(
            "taken_at",
            description="'taken_at' (default), 'aesthetic' (CLIP-IQA descending, nulls last), 'iqa', 'random'",
        ),
        min_aesthetic_pct: float = Query(
            0.0, ge=0.0, le=100.0,
            description="Drop photos whose CLIP-IQA percentile is below this value",
        ),
        quality: QualityBucket = Query(
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
            # Doctor surfaced), shared with /api/swipes/summary.
            base = apply_quality_filter(base, quality, s, cfg)

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
                base = collapse_to_moment_primaries(base)

            # Sort's null policy is a filter (iqa) or not (aesthetic = nulls last).
            # Count AFTER every filter, then order and page.
            if sort == "iqa":
                base = base.where(Embedding.aesthetic_iqa.isnot(None))

            # DISTINCT: the collapse outer-joins can emit a photo more than
            # once (a photo that is the primary of two moments), and the total
            # must agree with /api/swipes/summary, which counts a set of ids.
            total = s.execute(
                base.with_only_columns(_func.count(_func.distinct(Photo.id)))
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

            # Page over DISTINCT photo ids, not over join rows: a photo that is
            # the primary of two moments occupies two rows, and paging those
            # would hand back a page shorter than `limit` while `total` still
            # promised a full one. GROUP BY (not DISTINCT) so the ORDER BY may
            # name columns that are not in the select list.
            page_ids = list(
                s.scalars(
                    base.with_only_columns(Photo.id)
                    .group_by(Photo.id)
                    .offset(offset)
                    .limit(limit)
                )
            )

            # Re-read the full rows for exactly that page. Chunked because a
            # page can be up to 2000 ids and SQLite caps bound variables.
            by_id: dict[int, tuple] = {}
            for chunk in chunked(page_ids):
                for photo, score, emb, aest in s.execute(
                    base.where(Photo.id.in_(chunk))
                ).all():
                    by_id.setdefault(photo.id, (photo, score, emb, aest))
            rows = [by_id[pid] for pid in page_ids if pid in by_id]

            moment_of: dict[int, tuple[int, int, bool]] = {}
            for chunk in chunked(page_ids):
                for mm_pid, mm_mid, mom_size, mom_primary in (
                    s.query(
                        MomentMember.photo_id,
                        MomentMember.moment_id,
                        Moment.size,
                        Moment.primary_photo_id,
                    )
                    .join(Moment, Moment.id == MomentMember.moment_id)
                    .filter(MomentMember.photo_id.in_(chunk))
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
                    photo_out(
                        photo, score, emb,
                        moment_id=moment_id,
                        moment_size=moment_size,
                    )
                )
        return PhotoList(total=total, items=items)

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

        require_sha256(sha256)
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

    @app.delete("/api/swipes/{sha256}")
    def delete_swipe(sha256: str):
        """Clear a photo's verdict, putting it back to undecided."""
        from selects.db.models import Swipe

        require_sha256(sha256)
        with session_scope(Session) as s:
            photo = s.query(Photo).filter(Photo.sha256 == sha256).first()
            if not photo:
                raise HTTPException(404, detail="photo not found")
            existing = s.get(Swipe, photo.id)
            if existing is None:
                return {"ok": True, "deleted": False}
            s.delete(existing)
        return {"ok": True, "deleted": True}

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
                .filter(Swipe.decision.in_(KEEP_DECISIONS))
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

    def _likes_status(sha_list: Optional[list[str]]) -> dict[str, bool]:
        from selects.db.models import Swipe
        out: dict[str, bool] = {}
        with session_scope(Session) as s:
            q = s.query(Photo.sha256, Swipe.decision).join(
                Swipe, Swipe.photo_id == Photo.id
            )
            if sha_list is not None:
                # Chunked: the client sends one sha per visible photo. An
                # explicit empty list means "no shas" (returns {}), distinct
                # from the GET's "no ?shas at all" (returns everything).
                rows = []
                for chunk in chunked(sha_list):
                    rows.extend(q.filter(Photo.sha256.in_(chunk)).all())
            else:
                rows = q.all()
            for sha, decision in rows:
                out[sha] = decision in KEEP_DECISIONS
        return out

    @app.get("/api/likes/status")
    def likes_status(shas: str = Query("", description="comma-separated sha256s")):
        """Return {sha256: kept_bool} for the given list."""
        sha_list = [s for s in shas.split(",") if s.strip()] if shas else None
        return _likes_status(sha_list)

    @app.post("/api/likes/status")
    def likes_status_post(payload: StatusRequest):
        """Same as GET, but the sha list travels in a JSON body.

        Avoids the URL-length ceiling GET hits once a library-sized sha list
        is passed in the query string.
        """
        return _likes_status(payload.shas)

    @app.get("/api/swipes/summary")
    def swipes_summary(
        collapse: Literal["moments", "none"] = Query(
            "moments",
            description="'moments' counts one photo per burst (as /api/photos does); 'none' counts all",
        ),
        quality: QualityBucket = Query(
            None,
            description=(
                "Same quick-sort quality bucket /api/photos accepts: "
                "underexposed | overexposed | out_of_focus | blurry_keepers"
            ),
        ),
    ):
        """Verdict counts over the same photo set ``/api/photos`` returns.

        ``collapse`` and ``quality`` mean exactly what they mean on
        ``/api/photos`` (they run through the same predicates), so the tally
        always adds up to the list total the Review page is paging through.

        Exactly three verdicts are reported, and they always add up to
        ``total_photos``: a "skip" is undecided, a legacy "silver" is kept.
        """
        from selects.db.models import Swipe

        with session_scope(Session) as s:
            base = (
                select(Photo.id)
                .join(ClassicalScore, Photo.id == ClassicalScore.photo_id, isouter=True)
                .join(Embedding, Photo.id == Embedding.photo_id, isouter=True)
            )
            base = apply_quality_filter(base, quality, s, cfg)
            if collapse == "moments":
                base = collapse_to_moment_primaries(base)
            counted_ids = set(s.execute(base).scalars().all())

            kept = rejected = 0
            for photo_id, decision in s.execute(
                select(Swipe.photo_id, Swipe.decision)
            ).all():
                if photo_id not in counted_ids:
                    continue
                if decision in KEEP_DECISIONS:
                    kept += 1
                elif decision == "reject":
                    rejected += 1

        total_photos = len(counted_ids)
        return {
            "total_photos": total_photos,
            "kept": kept,
            "rejected": rejected,
            "undecided": total_photos - kept - rejected,
        }
