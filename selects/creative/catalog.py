"""Detached library snapshot; excludes image and embedding payloads."""
import math
from pathlib import Path

from selects.db import init_db, session_scope
from selects.db.models import (AestheticScore, ClassicalScore, Embedding, Moment,
                               MomentMember, Photo, PhotoCategory, PhotoPerson,
                               PipelineState, Story, StoryItem, Swipe)
from selects.ml.aesthetic import rank_score
from selects.ml.curation import CuratedPhoto, _apply_taste_blend

from .storage import check_state


def number(value):
    return float(value) if value is not None and math.isfinite(value) else None


def load_photos(cfg):
    check_state(cfg)
    if not cfg.db_path.is_file():
        raise ValueError(f"library is not indexed: {cfg.folder}")
    catalog = {}
    with session_scope(init_db(cfg.db_path)) as session:
        technical = {row.photo_id: row for row in session.query(ClassicalScore)}
        aesthetics = {row.photo_id: row for row in session.query(AestheticScore)}
        stages = {row.photo_id: row for row in session.query(PipelineState)}
        decisions = dict(session.query(Swipe.photo_id, Swipe.decision).all())
        categories = dict(session.query(PhotoCategory.photo_id, PhotoCategory.primary_category).all())
        prompt_scores = dict(session.query(Embedding.photo_id, Embedding.aesthetic_iqa).all())
        for photo in session.query(Photo).order_by(Photo.id):
            pid = photo.id
            tech, aesthetic, state = technical.get(pid), aesthetics.get(pid), stages.get(pid)
            preview = (cfg.state_dir / photo.preview_path).resolve() if photo.preview_path else None
            usable = bool(preview and preview.is_relative_to(cfg.previews_dir) and preview.is_file())
            iqa = number(prompt_scores.get(pid))
            hyper = number(aesthetic.ap25_score) if aesthetic else None
            nima = number(aesthetic.nima_score) if aesthetic else None
            score = number(rank_score(hyper, nima, iqa, ap_w=cfg.ap_weight, nima_w=cfg.nima_weight))
            item = dict(photo_id=pid, filename=Path(photo.path).name, path=photo.path,
                        sha256=photo.sha256, format=photo.format,
                        preview_path=str(preview) if usable else None,
                        preview_status="ready" if usable else "missing_or_unsafe",
                        taken_at=photo.taken_at.isoformat() if photo.taken_at else None,
                        gps_lat=number(photo.gps_lat), gps_lon=number(photo.gps_lon),
                        aesthetic=score, selects_score=score, taste=None, hyperiqa=hyper, iqa=iqa,
                        user_decision=decisions.get(pid), category=categories.get(pid),
                        moment_id=None, moment_rank=None, moment_primary=False,
                        story_id=None, story_ids=[], story_scenes=[], person_ids=[],
                        classical_done=bool(state and state.classical_done),
                        embedding_done=bool(state and state.embedding_done))
            for output, field in (("sharpness", "blur"), ("exposure", "exposure"),
                                  ("luma_mean", "luma_mean"), ("clipped_ratio", "clipped_high"),
                                  ("eyes_open_ratio", "eyes_open_ratio")):
                item[output] = number(getattr(tech, field)) if tech else None
            item.update(auto_reject=bool(tech and tech.auto_reject),
                        reject_reason=tech.reject_reason if tech else None,
                        faces_count=tech.faces_count if tech else None)
            catalog[pid] = item
        ranking = [CuratedPhoto(p["photo_id"], p["sha256"] or "", p["taken_at"],
                               p["aesthetic"], None, None, None) for p in catalog.values()]
        _apply_taste_blend(session, ranking)
        for result in ranking:
            catalog[result.photo_id]["taste"] = result.taste
            if result.final is not None:
                catalog[result.photo_id]["selects_score"] = result.final
        primaries = dict(session.query(Moment.id, Moment.primary_photo_id).all())
        for member in session.query(MomentMember).order_by(MomentMember.moment_id):
            if member.photo_id in catalog and catalog[member.photo_id]["moment_id"] is None:
                catalog[member.photo_id].update(moment_id=member.moment_id, moment_rank=member.rank,
                                               moment_primary=primaries.get(member.moment_id) == member.photo_id)
        day_stories = {}
        for story in session.query(Story).order_by(Story.id):
            if len(story.day) == 10 and story.day[4] == "-":
                day_stories.setdefault(story.day, []).append(story.id)
        for item in catalog.values():
            item["story_ids"].extend(day_stories.get((item["taken_at"] or "")[:10], []))
        for slot in session.query(StoryItem).order_by(StoryItem.story_id, StoryItem.rank):
            if slot.photo_id in catalog:
                item = catalog[slot.photo_id]
                if slot.story_id not in item["story_ids"]:
                    item["story_ids"].append(slot.story_id)
                if slot.scene_label:
                    item["story_scenes"].append([slot.story_id, slot.scene_label])
        for pid, person in session.query(PhotoPerson.photo_id, PhotoPerson.person_id).order_by(PhotoPerson.person_id):
            if pid in catalog:
                catalog[pid]["person_ids"].append(person)
    for item in catalog.values():
        item["story_id"] = next(iter(item["story_ids"]), None)
    return catalog
