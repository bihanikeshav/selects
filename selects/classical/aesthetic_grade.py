"""Natural auto-edit for selects's /api/enhance endpoint.

Rewritten 2026-05-25 — user feedback: previous CLAHE pipeline still over-
sharpened and bumped contrast on already-good photos. The new approach is
"do as little as possible" — only correct what's actually wrong:

  - If the dynamic range is already wide (p1→p99 spans most of [0,255]),
    skip the stretch entirely.
  - If shadows aren't actually blocked, skip CLAHE.
  - Always skip the unsharp mask (was the main over-sharpening offender).
  - White balance correction is *very* subtle (15% pull toward neutral)
    so warm sunsets and cool snow scenes are preserved.
  - Highlight roll-off only kicks in if there's measurable clipping.

For a photo that's already well-exposed and well-balanced, this function
is now nearly a passthrough — which is the desired behaviour.
"""
from __future__ import annotations

import cv2
import numpy as np
from PIL import Image


def auto_edit(img: Image.Image) -> Image.Image:
    """Apply a natural auto-edit. Returns a new RGB Image."""
    arr = np.array(img.convert("RGB"))

    lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)
    L, A, B = cv2.split(lab)

    L_f = L.astype(np.float32)

    # ── Diagnostics on the input image ─────────────────────────────────────
    p_low, p_high = np.percentile(L_f, [1.0, 99.0])
    dyn_range = p_high - p_low
    # How much of the image is sitting against the top of the curve.
    pct_clipped_hi = float((L_f > 245).mean())
    pct_clipped_lo = float((L_f < 10).mean())
    # Where the median sits — used to decide if shadows are genuinely blocked.
    median = float(np.median(L_f))

    needs_stretch = dyn_range < 200  # default 8-bit range is ~255, so this
                                     # only triggers on low-contrast images
    needs_shadow_lift = median < 80 and pct_clipped_lo > 0.05
    needs_highlight_recover = pct_clipped_hi > 0.04

    L_out = L  # default: untouched

    # ── 1. Percentile stretch — only if range is genuinely narrow ──────────
    if needs_stretch and dyn_range > 8:
        L_out = np.clip(
            (L_f - p_low) * (255.0 / dyn_range),
            0, 255,
        ).astype(np.uint8)

    # ── 2. CLAHE — only if shadows are actually blocked ────────────────────
    if needs_shadow_lift:
        clahe = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8))
        L_clahe = clahe.apply(L_out)
        # 75% original + 25% CLAHE — very gentle local lift.
        L_out = cv2.addWeighted(L_out, 0.75, L_clahe, 0.25, 0)

    # ── 3. Highlight roll-off — only if clipping detected ──────────────────
    if needs_highlight_recover:
        roll_lut = np.arange(256, dtype=np.float32)
        above = roll_lut > 232
        roll_lut[above] = 232 + (roll_lut[above] - 232) * 0.70
        roll_lut = np.clip(roll_lut, 0, 255).astype(np.uint8)
        L_out = cv2.LUT(L_out, roll_lut)

    # ── 4. White balance — always subtle (15% pull toward neutral) ─────────
    a_mean = float(A.mean())
    b_mean = float(B.mean())
    a_drift = a_mean - 128.0
    b_drift = b_mean - 128.0
    # Only correct if there's an obvious cast (>5 units off neutral).
    if abs(a_drift) > 5 or abs(b_drift) > 5:
        a_corr = np.clip(A.astype(np.float32) - a_drift * 0.15, 0, 255).astype(np.uint8)
        b_corr = np.clip(B.astype(np.float32) - b_drift * 0.15, 0, 255).astype(np.uint8)
    else:
        a_corr, b_corr = A, B

    lab_out = cv2.merge([L_out, a_corr, b_corr])
    rgb_out = cv2.cvtColor(lab_out, cv2.COLOR_LAB2RGB)

    # NOTE: removed the unsharp-mask step. The user reported it over-sharpens
    # everything. Sharpening should be a deliberate per-photo edit, not part
    # of an auto pipeline.

    return Image.fromarray(rgb_out)


def aesthetic_grade(
    img: Image.Image,
    preset: str = "natural",
    has_face: bool = False,
) -> Image.Image:
    """Backwards-compat shim — old callers passed preset / has_face."""
    _ = preset, has_face
    return auto_edit(img)
