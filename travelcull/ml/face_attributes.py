"""Per-face attributes: eyes-open score, head pose, face area ratio.

Computed from insightface buffalo_l sub-model outputs already produced by
``travelcull.classical.faces.detect_faces`` (the default ``FaceAnalysis``
loads every model in the pack — detection, recognition, ``landmark_2d_106``
and ``landmark_3d_68``/pose — no ``allowed_modules`` restriction is applied).

  * eyes_open  — Eye Aspect Ratio style openness from the 106-point 2D
                 landmark set, normalized to [0, 1] (1 = clearly open).
                 Robust to landmark index ordering: for each eye we take the
                 landmarks nearest the 5-point eye center and measure the
                 vertical/horizontal extent ratio of that neighborhood.
  * yaw/pitch  — degrees, from the 3D-landmark sub-model's pose when
                 available, else estimated geometrically from the 5-point kps.
  * area ratio — face bbox area / image area.

Also provides:
  * photo-level rollups (any_eyes_closed / all_looking_away)
  * a bounded burst-pick penalty used by ``travelcull.ml.curation`` so that
    among near-equal burst candidates the frame where everyone's eyes are
    open wins, without ever overriding a big aesthetic gap
  * a lazy backfill stage (``run_face_attribute_stage``) for photos whose
    face_embeddings rows predate these columns.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

import numpy as np

log = logging.getLogger(__name__)

# ── tuning constants ──────────────────────────────────────────────────────────

# Extent-ratio (eye height / eye width) mapped linearly onto [0, 1]:
# at or below CLOSED the eye is shut, at or above OPEN it is wide open.
EAR_CLOSED_RATIO = 0.12
EAR_OPEN_RATIO = 0.30

# eyes_open score below this counts as "eyes closed" for rollups/penalties.
EYES_CLOSED_THRESHOLD = 0.35

# |yaw| <= this is "frontal"; |yaw| > LOOKING_AWAY is "looking away".
FRONTAL_YAW_DEG = 30.0
LOOKING_AWAY_YAW_DEG = 45.0

# Faces smaller than this fraction of the image are background faces and are
# ignored by the burst penalty (they still get attributes stored).
MIN_PENALTY_FACE_AREA = 0.005

# Burst-pick penalty, in combined-aesthetic units (AP25/NIMA blend lives on a
# roughly 0-10 scale). The CAP bounds the total penalty so face quality can
# only flip *near-equal* candidates — a bigger aesthetic gap always wins.
PENALTY_CAP = 0.5
CLOSED_EYE_PENALTY_SINGLE = 0.30   # exactly one face, eyes closed
CLOSED_EYE_PENALTY_GROUP = 0.45    # per closed-eye face in a 3+ mostly-frontal group
CLOSED_EYE_PENALTY_OTHER = 0.20    # per closed-eye face otherwise (2 faces, profiles…)

_EYE_NEIGHBORHOOD_K = 8


@dataclass
class FaceAttrs:
    """Attributes for a single detected face. All fields optional — a field is
    None when the sub-model output needed to compute it was unavailable."""

    eyes_open: Optional[float] = None   # [0,1], 1 = clearly open
    yaw: Optional[float] = None         # degrees, 0 = frontal
    pitch: Optional[float] = None       # degrees, 0 = level
    area_ratio: Optional[float] = None  # bbox area / image area, [0,1]


# ── eyes-open (EAR) ───────────────────────────────────────────────────────────

def eye_extent_ratio(landmarks: np.ndarray, eye_center: np.ndarray,
                     k: int = _EYE_NEIGHBORHOOD_K) -> float:
    """Vertical/horizontal extent ratio of the *k* landmarks nearest
    *eye_center*. For the 106-point set those are the eye-contour points, so
    this behaves like an Eye Aspect Ratio without depending on the exact
    index layout of the landmark model.
    """
    pts = np.asarray(landmarks, dtype=np.float64).reshape(-1, 2)
    center = np.asarray(eye_center, dtype=np.float64).reshape(2)
    d = np.linalg.norm(pts - center, axis=1)
    sel = pts[np.argsort(d)[: min(k, len(pts))]]
    width = float(sel[:, 0].max() - sel[:, 0].min())
    height = float(sel[:, 1].max() - sel[:, 1].min())
    if width <= 1e-9:
        return 0.0
    return height / width


def eyes_open_score(landmark_2d_106: np.ndarray, kps: np.ndarray) -> float:
    """Eyes-open score in [0, 1] from the 106-point landmarks.

    *kps* is the 5-point set (left eye, right eye, nose, mouth corners) used
    only to locate the two eye centers. The score is the mean of both eyes'
    extent ratios mapped linearly through [EAR_CLOSED_RATIO, EAR_OPEN_RATIO].
    """
    kps = np.asarray(kps, dtype=np.float64).reshape(-1, 2)
    ratios = [
        eye_extent_ratio(landmark_2d_106, kps[0]),
        eye_extent_ratio(landmark_2d_106, kps[1]),
    ]
    mean_ratio = float(np.mean(ratios))
    score = (mean_ratio - EAR_CLOSED_RATIO) / (EAR_OPEN_RATIO - EAR_CLOSED_RATIO)
    return float(np.clip(score, 0.0, 1.0))


# ── head pose ─────────────────────────────────────────────────────────────────

def estimate_pose_from_kps(kps: np.ndarray) -> tuple[float, float]:
    """(yaw, pitch) in degrees estimated from the 5-point landmarks.

    Coarse but monotonic: yaw from the nose's horizontal offset relative to
    the eye midpoint (normalized by inter-eye distance), pitch from the
    nose's vertical position between the eye line and the mouth line.
    """
    kps = np.asarray(kps, dtype=np.float64).reshape(-1, 2)
    left_eye, right_eye, nose = kps[0], kps[1], kps[2]
    mouth_mid = (kps[3] + kps[4]) / 2.0
    eye_mid = (left_eye + right_eye) / 2.0

    inter_eye = float(np.linalg.norm(right_eye - left_eye))
    if inter_eye <= 1e-9:
        return 0.0, 0.0

    # Frontal: nose_x ~ eye_mid_x. A fully turned head puts the nose roughly
    # one inter-eye distance to the side.
    yaw = float(np.clip((nose[0] - eye_mid[0]) / inter_eye, -1.0, 1.0)) * 90.0

    face_v = float(mouth_mid[1] - eye_mid[1])
    if abs(face_v) <= 1e-9:
        pitch = 0.0
    else:
        # Neutral pose puts the nose tip ~55% of the way from eyes to mouth.
        t = float((nose[1] - eye_mid[1]) / face_v)
        pitch = float(np.clip(0.55 - t, -1.0, 1.0)) * 90.0
    return yaw, pitch


# ── per-face attribute computation ────────────────────────────────────────────

def compute_face_attributes(
    *,
    img_w: int,
    img_h: int,
    bbox_w: int,
    bbox_h: int,
    kps: Optional[np.ndarray] = None,
    landmark_2d_106: Optional[np.ndarray] = None,
    pose: Optional[np.ndarray] = None,
) -> FaceAttrs:
    """Compute FaceAttrs from whatever sub-model outputs are available.

    *pose* is insightface's (pitch, yaw, roll) from the 3D landmark model and
    is preferred for yaw/pitch; the kps-based estimate is the fallback.
    """
    attrs = FaceAttrs()

    if img_w > 0 and img_h > 0:
        attrs.area_ratio = float(
            np.clip((max(bbox_w, 0) * max(bbox_h, 0)) / float(img_w * img_h), 0.0, 1.0)
        )

    if landmark_2d_106 is not None and kps is not None:
        try:
            attrs.eyes_open = eyes_open_score(landmark_2d_106, kps)
        except Exception:  # malformed landmark arrays should never kill a stage
            log.debug("face_attrs: eyes_open computation failed", exc_info=True)

    if pose is not None:
        try:
            p = np.asarray(pose, dtype=np.float64).reshape(-1)
            if p.size >= 2 and np.all(np.isfinite(p[:2])):
                attrs.pitch, attrs.yaw = float(p[0]), float(p[1])
        except Exception:
            log.debug("face_attrs: pose parse failed", exc_info=True)
    if attrs.yaw is None and kps is not None:
        try:
            attrs.yaw, attrs.pitch = estimate_pose_from_kps(kps)
        except Exception:
            log.debug("face_attrs: kps pose estimate failed", exc_info=True)

    return attrs


# ── photo-level rollups ───────────────────────────────────────────────────────

def rollup_face_quality(faces: Iterable[FaceAttrs]) -> dict:
    """Photo-level rollups over per-face attributes.

    any_eyes_closed — at least one face with a known eyes_open score below
                      EYES_CLOSED_THRESHOLD.
    all_looking_away — there is at least one face with a known yaw and every
                       such face has |yaw| > LOOKING_AWAY_YAW_DEG.
    """
    faces = list(faces)
    scored = [f for f in faces if f.eyes_open is not None]
    posed = [f for f in faces if f.yaw is not None]
    return {
        "any_eyes_closed": any(f.eyes_open < EYES_CLOSED_THRESHOLD for f in scored),
        "all_looking_away": bool(posed)
        and all(abs(f.yaw) > LOOKING_AWAY_YAW_DEG for f in posed),
    }


# ── burst-pick penalty ────────────────────────────────────────────────────────

def face_quality_penalty(faces: Iterable[FaceAttrs]) -> float:
    """Bounded penalty (combined-aesthetic units) for burst picking.

    Contextual rules:
      * 3+ faces, mostly frontal (>= 60% within FRONTAL_YAW_DEG): closed eyes
        are ruinous — strong penalty per closed-eye face.
      * exactly 1 face: penalize closed eyes only; an averted gaze on a solo
        subject is often intentional (candid/profile) and is never penalized.
      * otherwise: moderate penalty per closed-eye face.

    Always capped at PENALTY_CAP so a genuinely better photo (bigger
    aesthetic gap than the cap) can never be displaced.
    """
    all_faces = list(faces)
    consider = [
        f for f in all_faces
        if f.area_ratio is None or f.area_ratio >= MIN_PENALTY_FACE_AREA
    ] or all_faces
    n = len(consider)
    if n == 0:
        return 0.0

    closed = [
        f for f in consider
        if f.eyes_open is not None and f.eyes_open < EYES_CLOSED_THRESHOLD
    ]
    if not closed:
        return 0.0

    frontal = [f for f in consider if f.yaw is not None and abs(f.yaw) <= FRONTAL_YAW_DEG]

    if n == 1:
        penalty = CLOSED_EYE_PENALTY_SINGLE
    elif n >= 3 and len(frontal) >= math.ceil(0.6 * n):
        penalty = CLOSED_EYE_PENALTY_GROUP * len(closed)
    else:
        penalty = CLOSED_EYE_PENALTY_OTHER * len(closed)

    return float(min(penalty, PENALTY_CAP))


def stack_face_penalties(s, photo_ids: Iterable[int]) -> dict[int, float]:
    """Map photo_id -> face_quality_penalty from stored face_embeddings rows.

    Photos with no face rows (or rows with no computed attributes) get 0.0.
    *s* is an open SQLAlchemy session.
    """
    from travelcull.db.models import FaceEmbedding

    ids = list(photo_ids)
    if not ids:
        return {}
    rows = (
        s.query(
            FaceEmbedding.photo_id,
            FaceEmbedding.eyes_open,
            FaceEmbedding.yaw,
            FaceEmbedding.pitch,
            FaceEmbedding.face_area_ratio,
        )
        .filter(FaceEmbedding.photo_id.in_(ids))
        .all()
    )
    by_photo: dict[int, list[FaceAttrs]] = {}
    for pid, eyes_open, yaw, pitch, area in rows:
        by_photo.setdefault(pid, []).append(
            FaceAttrs(eyes_open=eyes_open, yaw=yaw, pitch=pitch, area_ratio=area)
        )
    return {pid: face_quality_penalty(fl) for pid, fl in by_photo.items()}


# ── lazy backfill (reindex path) ──────────────────────────────────────────────

def _bbox_iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def backfill_photo_attributes(cfg, Session, photo_id: int, preview_path: str) -> int:
    """Re-detect faces on *photo_id*'s preview and fill missing attribute
    columns on its stored face_embeddings rows (matched by bbox IoU).

    Returns the number of rows updated. Raises on decode/detector errors —
    callers decide whether that is fatal.
    """
    from pathlib import Path

    from PIL import Image

    from travelcull.classical.faces import detect_faces
    from travelcull.db import session_scope
    from travelcull.db.models import FaceEmbedding

    preview_abs = cfg.state_dir / preview_path
    if not Path(preview_abs).exists():
        log.warning("face_attrs: preview not found: %s", preview_abs)
        return 0

    with Image.open(preview_abs) as im:
        img = np.asarray(im.convert("RGB"), dtype=np.uint8)
    detected = detect_faces(img)

    updated = 0
    with session_scope(Session) as s:
        rows = (
            s.query(FaceEmbedding)
            .filter(FaceEmbedding.photo_id == photo_id)
            .filter(FaceEmbedding.eyes_open.is_(None))
            .all()
        )
        for fe in rows:
            best, best_iou = None, 0.0
            for face in detected:
                iou = _bbox_iou(
                    (fe.bbox_x, fe.bbox_y, fe.bbox_w, fe.bbox_h),
                    (face.x, face.y, face.w, face.h),
                )
                if iou > best_iou:
                    best, best_iou = face, iou
            if best is None or best_iou < 0.3:
                continue
            attrs = compute_face_attributes(
                img_w=img.shape[1],
                img_h=img.shape[0],
                bbox_w=fe.bbox_w,
                bbox_h=fe.bbox_h,
                kps=best.kps,
                landmark_2d_106=best.landmark_2d_106,
                pose=best.pose,
            )
            fe.eyes_open = attrs.eyes_open
            fe.yaw = attrs.yaw
            fe.pitch = attrs.pitch
            fe.face_area_ratio = attrs.area_ratio
            s.add(fe)
            updated += 1
    return updated


def run_face_attribute_stage(
    cfg,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> int:
    """Lazily compute face attributes for photos whose face_embeddings rows
    predate the attribute columns (eyes_open IS NULL). Same shape as the other
    pipeline stages in travelcull.pipeline. Returns photos processed.
    """
    from travelcull.db import init_db, session_scope
    from travelcull.db.models import FaceEmbedding, Photo

    Session = init_db(cfg.db_path)

    with session_scope(Session) as s:
        pending = (
            s.query(Photo.id, Photo.preview_path)
            .join(FaceEmbedding, FaceEmbedding.photo_id == Photo.id)
            .filter(FaceEmbedding.eyes_open.is_(None))
            .distinct()
            .all()
        )

    if not pending:
        log.info("face_attrs: no photos missing face attributes")
        return 0

    total = len(pending)
    processed = 0
    for i, (photo_id, preview_path) in enumerate(pending, start=1):
        if on_progress:
            on_progress(i, total, preview_path or str(photo_id))
        if not preview_path:
            continue
        try:
            backfill_photo_attributes(cfg, Session, photo_id, preview_path)
            processed += 1
        except Exception as exc:
            log.warning("face_attrs: failed on photo %s: %s", photo_id, exc)
    log.info("face_attrs: done — %d photos backfilled", processed)
    return processed
