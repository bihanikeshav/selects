"""Quick auto-straighten using probabilistic Hough lines.

Finds near-horizontal lines (skies, horizons, building tops) and rotates the
image so their median angle becomes exactly horizontal. Robust enough for
casual phone photos; not a full perspective correction.
"""
from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

# Maximum rotation we'll apply (degrees). Anything more is usually a
# deliberate composition, not an error.
MAX_ROTATION_DEG = 12.0
# We only consider line segments whose angle is within this band of strictly
# horizontal (in degrees). Outside this band the line probably isn't a horizon.
HORIZONTAL_BAND_DEG = 20.0


def estimate_rotation_angle(img: Image.Image) -> float:
    """Return the angle (degrees) by which the image should be rotated to
    appear straight. Positive = rotate CCW. Returns 0 if no good signal.
    """
    arr = np.array(img.convert("RGB"))
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    # Mild blur to suppress micro-edges
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, 60, 180)
    h, w = edges.shape

    min_line_len = int(min(h, w) * 0.18)
    max_line_gap = int(min(h, w) * 0.03)

    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 360.0,
        threshold=80,
        minLineLength=min_line_len,
        maxLineGap=max_line_gap,
    )
    if lines is None or len(lines) == 0:
        return 0.0

    angles = []
    weights = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        dx = x2 - x1
        dy = y2 - y1
        # Skip vertical-ish lines — building edges shouldn't drive the horizon
        length = (dx * dx + dy * dy) ** 0.5
        if length < 4:
            continue
        # Angle relative to horizontal, in (-90, 90]
        angle = np.degrees(np.arctan2(dy, dx))
        # Normalise so we only look at near-horizontal candidates
        if angle > 90:
            angle -= 180
        if angle < -90:
            angle += 180
        if abs(angle) > HORIZONTAL_BAND_DEG:
            continue
        angles.append(angle)
        weights.append(length)

    if not angles:
        return 0.0

    # Weighted median: more confidence in longer lines
    pairs = sorted(zip(angles, weights))
    total = sum(w for _, w in pairs)
    acc = 0.0
    median_angle = 0.0
    for a, w in pairs:
        acc += w
        if acc >= total / 2:
            median_angle = a
            break

    # Clamp to a sane maximum so we don't twist images sideways
    if median_angle > MAX_ROTATION_DEG:
        median_angle = MAX_ROTATION_DEG
    elif median_angle < -MAX_ROTATION_DEG:
        median_angle = -MAX_ROTATION_DEG

    return float(median_angle)


def straighten(img: Image.Image) -> tuple[Image.Image, float]:
    """Return (straightened image, applied angle in degrees)."""
    angle = estimate_rotation_angle(img)
    if abs(angle) < 0.4:
        return img.convert("RGB"), 0.0

    arr = np.array(img.convert("RGB"))
    h, w = arr.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    rotated = cv2.warpAffine(
        arr, M, (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    # Crop a small border to hide the rotation triangles
    if abs(angle) > 0.4:
        crop_pct = min(0.04, abs(angle) / MAX_ROTATION_DEG * 0.04)
        cx = int(w * crop_pct)
        cy = int(h * crop_pct)
        rotated = rotated[cy : h - cy, cx : w - cx]
    return Image.fromarray(rotated), float(angle)
