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


def _luma(x: np.ndarray) -> np.ndarray:
    return x @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def auto_tone(img: Image.Image) -> Image.Image:
    """Assertive, tasteful auto-edit in the spirit of Lightroom's *Auto* button.

    Unlike ``auto_edit`` (which was tuned to "do almost nothing" and therefore
    looked broken), this always makes a visible but natural improvement:
      1. White balance   — shades-of-gray illuminant estimate (robust vs a flat
                           gray-world), blended so intentional warmth survives.
      2. Black/White pts — clip the 0.4% luminance tails and stretch.
      3. Exposure        — nudge mean luminance toward a target via gamma.
      4. Shadows/Highl.  — lift shadows + roll off highlights as a luminance
                           gain (hue/saturation preserved).
      5. Contrast        — mild global S-curve.
      6. Vibrance        — saturation-aware boost (lifts muted colours more).
    All strengths are moderate so good photos stay natural and flat/dull travel
    shots get a clear lift.
    """
    rgb = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0
    eps = 1e-5

    # 1 — white balance: CONSERVATIVE shades-of-gray. Gray-world assumptions
    #     wreck non-neutral scenes (snow, sunsets), so clamp per-channel scale
    #     tightly (±12%) and only blend 30% — enough to nudge an obvious cast,
    #     not enough to orange-ify a snowy selfie or blue-ify a warm alley.
    p = 6.0
    illum = np.power(np.mean(np.power(rgb, p), axis=(0, 1)), 1.0 / p)
    illum = illum / (illum.mean() + eps)
    scale = np.clip(1.0 / np.clip(illum, eps, None), 0.88, 1.12)
    rgb = np.clip(0.70 * rgb + 0.30 * (rgb * scale), 0, 1)

    # 2 — black & white points from luminance percentiles (no-op on wide-range)
    lum = _luma(rgb)
    lo, hi = np.percentile(lum, [0.4, 99.6])
    if hi - lo > 0.03:
        rgb = np.clip((rgb - lo) / (hi - lo), 0, 1)

    # 3 — exposure toward a target mean via gamma (gently clamped)
    m = float(_luma(rgb).mean())
    if m > eps:
        gamma = float(np.clip(np.log(0.46) / np.log(m + eps), 0.7, 1.5))
        rgb = np.power(rgb, gamma)

    # 4 — shadow lift + highlight roll-off applied ADDITIVELY in luminance.
    #     Adding the same delta to R,G,B keeps shadows neutral — a multiplicative
    #     gain explodes near black and amplifies colour noise into a magenta cast.
    lum = _luma(rgb)
    shadow = 0.16 * np.clip(1.0 - lum / 0.5, 0, 1) ** 2      # lift deep shadows
    highl = 0.12 * np.clip((lum - 0.6) / 0.4, 0, 1) ** 2      # compress highlights
    delta = (shadow * (1.0 - lum) - highl * lum)[..., None]
    rgb = np.clip(rgb + delta, 0, 1)

    # 5 — gentle global contrast S-curve around mid-grey
    rgb = np.clip(0.5 + (rgb - 0.5) * 1.07, 0, 1)

    # 6 — vibrance: boost less-saturated pixels more (protects skin/skies)
    hsv = cv2.cvtColor(rgb.astype(np.float32), cv2.COLOR_RGB2HSV)
    s = hsv[..., 1]
    hsv[..., 1] = np.clip(s * (1.0 + 0.22 * (1.0 - s)), 0, 1)
    rgb = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)

    return Image.fromarray((np.clip(rgb, 0, 1) * 255.0).round().astype(np.uint8))


def aesthetic_grade(
    img: Image.Image,
    preset: str = "natural",
    has_face: bool = False,
) -> Image.Image:
    """Backwards-compat shim — old callers passed preset / has_face.

    Now routes to the assertive Lightroom-style ``auto_tone``.
    """
    _ = preset, has_face
    return auto_tone(img)
