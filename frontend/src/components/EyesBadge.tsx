import { useEffect, useState } from "react";
import { getFaceQuality, type FaceQuality } from "../api/faceQuality";
import "./EyesBadge.css";

// Session-scoped cache so rendering a strip of thumbnails doesn't refetch
// the same photo's face quality on every re-render / remount.
const cache = new Map<string, FaceQuality | null>();
const inflight = new Map<string, Promise<FaceQuality | null>>();

function fetchCached(sha256: string): Promise<FaceQuality | null> {
  if (cache.has(sha256)) return Promise.resolve(cache.get(sha256) ?? null);
  const pending = inflight.get(sha256);
  if (pending) return pending;
  const p = getFaceQuality(sha256)
    .then((q) => {
      cache.set(sha256, q);
      return q;
    })
    .catch(() => {
      cache.set(sha256, null); // don't hammer a failing endpoint
      return null;
    })
    .finally(() => {
      inflight.delete(sha256);
    });
  inflight.set(sha256, p);
  return p;
}

interface EyesBadgeProps {
  sha256: string;
  /** Absolutely position the badge in the parent's bottom-left corner. */
  overlay?: boolean;
  /** Also show a quiet "eyes open" confirmation when everything is fine. */
  showOk?: boolean;
}

/**
 * Tiny face-quality pill: warns when someone in the shot has their eyes
 * closed, or when every detected face is looking away from the camera.
 * Renders nothing for photos without detected faces (or before data loads).
 */
export default function EyesBadge({ sha256, overlay = false, showOk = false }: EyesBadgeProps) {
  const [quality, setQuality] = useState<FaceQuality | null>(
    () => cache.get(sha256) ?? null,
  );

  useEffect(() => {
    let cancelled = false;
    setQuality(cache.get(sha256) ?? null);
    fetchCached(sha256).then((q) => {
      if (!cancelled) setQuality(q);
    });
    return () => {
      cancelled = true;
    };
  }, [sha256]);

  if (!quality || quality.faces.length === 0) return null;

  let variant: "closed" | "away" | "open";
  let label: string;
  if (quality.any_eyes_closed) {
    variant = "closed";
    label = "Eyes closed";
  } else if (quality.all_looking_away) {
    variant = "away";
    label = "Looking away";
  } else if (showOk && quality.faces.some((f) => f.eyes_open !== null)) {
    variant = "open";
    label = "Eyes open";
  } else {
    return null;
  }

  return (
    <span
      className={`eyes-badge eyes-badge--${variant}${overlay ? " eyes-badge--overlay" : ""}`}
      title={`${label} — ${quality.faces.length} face${quality.faces.length === 1 ? "" : "s"} detected`}
      aria-label={label}
    >
      {variant === "closed" ? (
        // eye-off icon
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 10 8 10 8a13.16 13.16 0 0 1-1.67 2.68" />
          <path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 8 10 8a9.74 9.74 0 0 0 5.39-1.61" />
          <line x1="2" y1="2" x2="22" y2="22" />
        </svg>
      ) : (
        // eye icon
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M2 12s3-8 10-8 10 8 10 8-3 8-10 8-10-8-10-8z" />
          <circle cx="12" cy="12" r="3" />
        </svg>
      )}
      {label}
    </span>
  );
}
