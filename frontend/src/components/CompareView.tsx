import { useCallback, useEffect, useRef, useState } from "react";

import { getFaceQuality } from "../api/faceQuality";
import type { FaceQuality } from "../api/faceQuality";
import "./CompareView.css";

/**
 * Side-by-side compare for 2-4 burst frames with SYNCED pan + zoom.
 *
 * Interaction model:
 *  - Wheel over any pane zooms ALL panes toward the same relative point
 *    (pointer position expressed as a fraction of the pane, so panes of
 *    different pixel sizes stay visually aligned).
 *  - Dragging any pane pans all panes by the same relative offset.
 *  - Double-click toggles between fit and 2.5x at the clicked point.
 *  - "0" resets the shared view; Escape closes.
 *  - Per-pane keep / reject buttons report decisions upward.
 *  - Face-quality badges are feature-detected: if the backend endpoint is
 *    missing or errors, the badge silently never appears.
 */

export interface CompareFrame {
  sha256: string;
  previewUrl: string;
  label: string;
}

interface CompareViewProps {
  frames: CompareFrame[];
  onClose: () => void;
  onDecision: (sha256: string, decision: "keep" | "reject") => void;
}

interface ViewTransform {
  /** Scale factor; 1 = fit. */
  s: number;
  /** Pan offsets as fractions of the pane size (translate before scale). */
  tx: number;
  ty: number;
}

const IDENTITY: ViewTransform = { s: 1, tx: 0, ty: 0 };
const MIN_SCALE = 1;
const MAX_SCALE = 8;

function clampScale(s: number): number {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, s));
}

function faceBadge(q: FaceQuality | null | undefined) {
  if (!q || q.faces.length === 0) return null;
  if (q.any_eyes_closed) {
    return <span className="cmp-badge is-bad">eyes closed</span>;
  }
  if (q.all_looking_away) {
    return <span className="cmp-badge is-warn">looking away</span>;
  }
  return (
    <span className="cmp-badge is-good">
      {q.faces.length} face{q.faces.length > 1 ? "s" : ""} · eyes open
    </span>
  );
}

interface PaneProps {
  frame: CompareFrame;
  view: ViewTransform;
  quality: FaceQuality | null | undefined;
  decision: "keep" | "reject" | undefined;
  zoomAt: (px: number, py: number, factor: number) => void;
  panBy: (dx: number, dy: number) => void;
  onDecision: (sha256: string, decision: "keep" | "reject") => void;
}

function Pane({ frame, view, quality, decision, zoomAt, panBy, onDecision }: PaneProps) {
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const drag = useRef<{ x: number; y: number } | null>(null);
  const [dragging, setDragging] = useState(false);

  // Native wheel listener: React's synthetic wheel handlers are passive on
  // most browsers, so preventDefault (needed to stop page scroll) requires
  // an explicit non-passive listener.
  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    function onWheel(e: WheelEvent) {
      e.preventDefault();
      const r = el!.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) return;
      const px = (e.clientX - r.left) / r.width;
      const py = (e.clientY - r.top) / r.height;
      zoomAt(px, py, Math.exp(-e.deltaY * 0.0016));
    }
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [zoomAt]);

  const paneClass =
    "cmp-pane" +
    (decision === "keep" ? " is-keep" : decision === "reject" ? " is-reject" : "");
  const canvasClass =
    "cmp-canvas" + (view.s > 1 ? " is-zoomed" : "") + (dragging ? " is-dragging" : "");

  return (
    <div className={paneClass}>
      <div
        ref={canvasRef}
        className={canvasClass}
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId);
          drag.current = { x: e.clientX, y: e.clientY };
          setDragging(true);
        }}
        onPointerMove={(e) => {
          if (!drag.current) return;
          const r = e.currentTarget.getBoundingClientRect();
          if (r.width === 0 || r.height === 0) return;
          panBy(
            (e.clientX - drag.current.x) / r.width,
            (e.clientY - drag.current.y) / r.height,
          );
          drag.current = { x: e.clientX, y: e.clientY };
        }}
        onPointerUp={() => {
          drag.current = null;
          setDragging(false);
        }}
        onPointerCancel={() => {
          drag.current = null;
          setDragging(false);
        }}
        onDoubleClick={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          if (r.width === 0 || r.height === 0) return;
          const px = (e.clientX - r.left) / r.width;
          const py = (e.clientY - r.top) / r.height;
          zoomAt(px, py, view.s > 1 ? 1 / view.s : 2.5);
        }}
      >
        <img
          src={frame.previewUrl}
          alt={frame.label}
          draggable={false}
          style={{
            transform: `translate(${view.tx * 100}%, ${view.ty * 100}%) scale(${view.s})`,
          }}
        />
      </div>
      <div className="cmp-bar">
        <span className="cmp-label" title={frame.sha256}>
          {frame.label}
        </span>
        {faceBadge(quality)}
        <span className="cmp-bar-spacer" />
        <button
          className={`cmp-btn${decision === "keep" ? " is-keep-active" : ""}`}
          onClick={() => onDecision(frame.sha256, "keep")}
          title="Keep this frame"
        >
          Keep
        </button>
        <button
          className={`cmp-btn${decision === "reject" ? " is-reject-active" : ""}`}
          onClick={() => onDecision(frame.sha256, "reject")}
          title="Reject this frame"
        >
          Reject
        </button>
      </div>
    </div>
  );
}

export default function CompareView({ frames, onClose, onDecision }: CompareViewProps) {
  const [view, setView] = useState<ViewTransform>(IDENTITY);
  const [decided, setDecided] = useState<Record<string, "keep" | "reject">>({});
  const [fq, setFq] = useState<Record<string, FaceQuality | null>>({});

  // Feature-detected face quality: fetch per frame, degrade silently on any
  // failure (404 when the endpoint doesn't exist, network error, etc.).
  const shaKey = frames.map((f) => f.sha256).join(",");
  useEffect(() => {
    let cancelled = false;
    for (const sha of shaKey.split(",")) {
      if (!sha) continue;
      getFaceQuality(sha)
        .then((q) => {
          if (!cancelled) setFq((m) => ({ ...m, [sha]: q }));
        })
        .catch(() => {
          if (!cancelled) setFq((m) => ({ ...m, [sha]: null }));
        });
    }
    return () => {
      cancelled = true;
    };
  }, [shaKey]);

  // Synced zoom toward the same relative point in every pane. With the
  // transform `translate(tx, ty) scale(s)` (fractions of pane size, origin
  // 0 0), the content point c maps to screen fraction p = tx + c*s. Zooming
  // keeps the content point under the cursor fixed.
  const zoomAt = useCallback((px: number, py: number, factor: number) => {
    setView((v) => {
      const s = clampScale(v.s * factor);
      if (s === 1) return IDENTITY;
      const cx = (px - v.tx) / v.s;
      const cy = (py - v.ty) / v.s;
      return { s, tx: px - cx * s, ty: py - cy * s };
    });
  }, []);

  const panBy = useCallback((dx: number, dy: number) => {
    setView((v) => (v.s === 1 ? v : { ...v, tx: v.tx + dx, ty: v.ty + dy }));
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      } else if (e.key === "0") {
        e.preventDefault();
        setView(IDENTITY);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const decide = useCallback(
    (sha: string, decision: "keep" | "reject") => {
      setDecided((m) => ({ ...m, [sha]: decision }));
      onDecision(sha, decision);
    },
    [onDecision],
  );

  return (
    <div className="cmp-overlay">
      <div className="cmp-header">
        <span>Compare {frames.length} frames</span>
        <span className="cmp-hint">
          wheel = synced zoom · drag = synced pan · dbl-click = 2.5x · 0 = reset · Esc = close
        </span>
        <span className="cmp-spacer" />
        {view.s > 1 && (
          <button className="cmp-close" onClick={() => setView(IDENTITY)}>
            Reset zoom ({view.s.toFixed(1)}x)
          </button>
        )}
        <button className="cmp-close" onClick={onClose}>
          Close · Esc
        </button>
      </div>
      <div className="cmp-grid">
        {frames.map((f) => (
          <Pane
            key={f.sha256}
            frame={f}
            view={view}
            quality={fq[f.sha256]}
            decision={decided[f.sha256]}
            zoomAt={zoomAt}
            panBy={panBy}
            onDecision={decide}
          />
        ))}
      </div>
    </div>
  );
}
