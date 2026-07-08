import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import PageHeader from "../components/PageHeader";
import Rail from "../components/Rail";

type Scores = {
  iqa: number | null;
  nima: number | null;
  ap25: number | null;
  personal: number | null;
  combined: number | null;
};

type ExtremePhoto = {
  photo_id: number;
  sha256: string;
  taken_at: string | null;
  thumb_url: string;
  preview_url: string;
  scores: Scores;
  default_rating: -1 | 1;
};

type AgreementModel = {
  median_upvote_percentile: number | null;
  n_scored_upvotes: number;
};
type Agreement = {
  models: Record<string, AgreementModel>;
  n_upvotes: number;
};

const AGREEMENT_KEYS = ["iqa", "nima", "ap25", "combined", "personal"] as const;
const AGREEMENT_LABELS: Record<string, string> = {
  iqa: "CLIP-IQA",
  nima: "NIMA",
  ap25: "AP V2.5",
  combined: "NIMA+AP",
  personal: "Personal",
};

function fmtPctile(v: number | null | undefined) {
  return v == null ? "—" : `${v.toFixed(0)}`;
}

const BATCH_SIZE = 60;

export default function Calibrate() {
  const [photos, setPhotos] = useState<ExtremePhoto[]>([]);
  const [flipped, setFlipped] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [retraining, setRetraining] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [totalRated, setTotalRated] = useState(0);
  const [totalIndexed, setTotalIndexed] = useState(0);
  const [agreement, setAgreement] = useState<Agreement | null>(null);
  const [lightboxIdx, setLightboxIdx] = useState<number | null>(null);

  const fetchBatch = useCallback(async () => {
    setLoading(true);
    setFlipped(new Set());
    try {
      const res = await fetch(`/api/calibrate/extremes?bucket=worst&n=${BATCH_SIZE}`);
      if (!res.ok) {
        setToast(`Backend missing /api/calibrate/extremes (HTTP ${res.status}). Restart selects serve.`);
        setPhotos([]);
        return;
      }
      const j = await res.json();
      setPhotos(j.photos as ExtremePhoto[]);
      setTotalRated(j.rated_count);
      setTotalIndexed(j.total_indexed);
      setToast(null);
    } catch (e) {
      setToast(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchAgreement = useCallback(async () => {
    try {
      const res = await fetch("/api/calibrate/agreement");
      if (!res.ok) {
        setAgreement(null);
        return;
      }
      const j = await res.json();
      if (j && j.models) setAgreement(j as Agreement);
      else setAgreement(null);
    } catch {
      setAgreement(null);
    }
  }, []);

  useEffect(() => {
    fetchBatch();
    fetchAgreement();
  }, [fetchBatch, fetchAgreement]);

  const submitBatch = useCallback(async () => {
    if (photos.length === 0) return;
    setSubmitting(true);
    try {
      const ratings = photos.map((p) => ({
        photo_id: p.photo_id,
        rating: flipped.has(p.photo_id) ? -p.default_rating : p.default_rating,
      }));
      const res = await fetch("/api/calibrate/rate_batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ratings }),
      });
      if (!res.ok) {
        setToast(`rate_batch failed: HTTP ${res.status}`);
      } else {
        setToast(`Saved ${ratings.length} ratings (${flipped.size} rescued).`);
        await Promise.all([fetchBatch(), fetchAgreement()]);
      }
    } catch (e) {
      setToast(String(e));
    } finally {
      setSubmitting(false);
    }
  }, [photos, flipped, fetchBatch, fetchAgreement]);

  const retrain = useCallback(async () => {
    setRetraining(true);
    try {
      const res = await fetch("/api/calibrate/retrain", { method: "POST" });
      const j = await res.json();
      if (!res.ok) setToast(j.detail || `retrain failed: ${res.status}`);
      else {
        setToast(
          `Trained on ${j.n_positive} upvotes. Mean upvote self-similarity ${j.mean_positive_similarity?.toFixed(3) ?? "—"}. Scored ${j.n_scored} photos.`,
        );
        await Promise.all([fetchBatch(), fetchAgreement()]);
      }
    } catch (e) {
      setToast(String(e));
    } finally {
      setRetraining(false);
    }
  }, [fetchBatch, fetchAgreement]);

  const toggle = useCallback((photoId: number) => {
    setFlipped((prev) => {
      const next = new Set(prev);
      if (next.has(photoId)) next.delete(photoId);
      else next.add(photoId);
      return next;
    });
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;

      // Lightbox-mode keys take precedence
      if (lightboxIdx !== null) {
        if (e.key === "Escape") {
          setLightboxIdx(null);
        } else if (e.key === "ArrowRight" || e.key === " ") {
          e.preventDefault();
          setLightboxIdx((i) => (i === null ? null : Math.min(photos.length - 1, i + 1)));
        } else if (e.key === "ArrowLeft") {
          e.preventDefault();
          setLightboxIdx((i) => (i === null ? null : Math.max(0, i - 1)));
        } else if (e.key === "f" || e.key === "F") {
          const p = photos[lightboxIdx];
          if (p) toggle(p.photo_id);
        }
        return;
      }

      if (e.key === "Enter") submitBatch();
      else if (e.key === "r" || e.key === "R") retrain();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [submitBatch, retrain, lightboxIdx, photos, toggle]);

  const ACCENT = "var(--g-green)";

  return (
    <div className="app">
      <Rail />
      <div
        className="workspace"
        style={{
          display: "grid",
          gridTemplateRows: "auto 1fr",
          minHeight: 0,
          maxHeight: "100vh",
          overflow: "hidden",
        }}
      >
        <PageHeader
          context="aesthetic calibration"
          title="Calibrate"
          subtitle="Rescue from the bottom of NIMA + AP V2.5 — click the photos that are actually good; they'll train the personal model. Unclicked = confirmed bad."
          actions={
            <>
              <div style={{ color: "var(--md-on-surface-var)", fontSize: 12, fontFamily: "var(--font-mono)" }}>
                {totalRated} rated / {totalIndexed} indexed
              </div>
              <Link to="/calibrate/dashboard" className="btn btn-text">
                Dashboard →
              </Link>
            </>
          }
        />

        {/* Body: grid + side panel */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 300px",
            gap: 16,
            padding: "12px 24px 16px",
            minHeight: 0,
            overflow: "hidden",
          }}
        >
          {/* ── Grid column ────────────────────────────────────────────── */}
          <div
            style={{
              display: "grid",
              gridTemplateRows: "auto 1fr auto",
              gap: 12,
              minHeight: 0,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <h2
                style={{
                  margin: 0,
                  fontFamily: "var(--font-display)",
                  fontSize: 16,
                  fontWeight: 500,
                }}
              >
                Worst by combined score
              </h2>
              <span style={{ fontSize: 12, color: "var(--md-on-surface-var)" }}>
                {photos.length} photos · {flipped.size} rescued
              </span>
              <span
                style={{
                  marginLeft: "auto",
                  fontSize: 11,
                  color: "var(--md-on-surface-var)",
                  fontFamily: "var(--font-mono)",
                }}
              >
                Default 👎 · click to rescue → 👍
              </span>
            </div>

            <div
              style={{
                background: "var(--md-surface-c-low)",
                border: "1px solid var(--md-outline-var)",
                borderRadius: 12,
                overflowY: "auto",
                padding: 8,
                minHeight: 0,
              }}
            >
              {loading ? (
                <div
                  style={{
                    color: "var(--md-on-surface-var)",
                    padding: 24,
                    textAlign: "center",
                  }}
                >
                  Loading…
                </div>
              ) : photos.length === 0 ? (
                <div
                  style={{
                    color: "var(--md-on-surface-var)",
                    padding: 24,
                    textAlign: "center",
                  }}
                >
                  No unrated photos at this end. All caught up.
                </div>
              ) : (
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))",
                    gap: 6,
                  }}
                >
                  {photos.map((p, i) => {
                    const isFlipped = flipped.has(p.photo_id);
                    const finalRating = isFlipped ? -p.default_rating : p.default_rating;
                    return (
                      <div
                        key={p.photo_id}
                        onClick={() => toggle(p.photo_id)}
                        style={{
                          position: "relative",
                          border: isFlipped
                            ? `3px solid ${ACCENT}`
                            : "2px solid transparent",
                          borderRadius: 8,
                          overflow: "hidden",
                          cursor: "pointer",
                          background: "#222",
                          transition: "transform 90ms ease",
                          transform: isFlipped ? "scale(0.97)" : "scale(1)",
                        }}
                        title={`combined ${p.scores.combined?.toFixed(0) ?? "—"} · nima ${p.scores.nima?.toFixed(2) ?? "—"} · ap25 ${p.scores.ap25?.toFixed(2) ?? "—"}`}
                      >
                        <img
                          src={p.thumb_url}
                          alt=""
                          loading="lazy"
                          style={{
                            width: "100%",
                            aspectRatio: "4/3",
                            objectFit: "cover",
                            display: "block",
                          }}
                        />
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setLightboxIdx(i);
                          }}
                          title="View full size (or ← → in lightbox)"
                          aria-label="View full size"
                          style={{
                            position: "absolute",
                            top: 4,
                            left: 4,
                            width: 24,
                            height: 24,
                            display: "grid",
                            placeItems: "center",
                            background: "rgba(0,0,0,0.72)",
                            color: "#fff",
                            border: "none",
                            borderRadius: 4,
                            cursor: "zoom-in",
                            padding: 0,
                          }}
                        >
                          <svg
                            viewBox="0 0 24 24"
                            width="14"
                            height="14"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="2"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          >
                            <path d="M3 9V3h6M21 9V3h-6M3 15v6h6M21 15v6h-6" />
                          </svg>
                        </button>
                        <span
                          style={{
                            position: "absolute",
                            top: 4,
                            right: 4,
                            background: "rgba(0,0,0,0.72)",
                            color: "#fff",
                            fontSize: 10,
                            padding: "2px 5px",
                            borderRadius: 4,
                            fontFamily: "var(--font-mono)",
                          }}
                        >
                          {p.scores.combined?.toFixed(0) ?? "—"}
                        </span>
                        <span
                          style={{
                            position: "absolute",
                            bottom: 4,
                            left: 4,
                            fontSize: 16,
                            filter:
                              "drop-shadow(0 1px 2px rgba(0,0,0,0.6))",
                          }}
                        >
                          {finalRating > 0 ? "👍" : "👎"}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <button
                className="btn btn-text"
                onClick={() => setFlipped(new Set())}
                disabled={flipped.size === 0 || submitting}
              >
                Clear flips
              </button>
              <button
                className="btn btn-text"
                onClick={() => setFlipped(new Set(photos.map((p) => p.photo_id)))}
                disabled={photos.length === 0 || submitting}
              >
                Flip all
              </button>
              <div style={{ flex: 1 }} />
              <button
                className="btn btn-filled"
                onClick={submitBatch}
                disabled={photos.length === 0 || submitting}
                title="Submit ratings and load next batch (Enter)"
              >
                {submitting
                  ? "Saving…"
                  : `Save batch & next (${photos.length - flipped.size} 👎, ${flipped.size} rescued) `}
                <span style={kbdStyle}>↵</span>
              </button>
            </div>
          </div>

          {/* ── Side panel ─────────────────────────────────────────────── */}
          <aside
            style={{
              display: "grid",
              gridTemplateRows: "auto auto 1fr",
              gap: 10,
              minHeight: 0,
              overflow: "hidden",
            }}
          >
            <div style={cardStyle}>
              <div style={cardHeaderStyle}>
                Agreement
                <button
                  className="btn btn-text btn-sm"
                  style={{ marginLeft: "auto" }}
                  onClick={retrain}
                  disabled={retraining}
                  title="Retrain personal model (R)"
                >
                  {retraining ? "Training…" : "Retrain"}
                  <span style={{ ...kbdStyle, marginLeft: 4 }}>R</span>
                </button>
              </div>
              {agreement && agreement.models && agreement.n_upvotes >= 1 ? (
                <>
                  <div
                    style={{
                      fontSize: 11,
                      color: "var(--md-on-surface-var)",
                      marginBottom: 6,
                    }}
                  >
                    Median percentile of your <b>{agreement.n_upvotes}</b> upvotes per model. Higher = model ranks your picks closer to the top.
                  </div>
                  <table style={tableStyle}>
                    <thead>
                      <tr>
                        <th></th>
                        <th style={thStyle}>median ↑pctile</th>
                      </tr>
                    </thead>
                    <tbody>
                      {AGREEMENT_KEYS.map((k) => {
                        const a = agreement.models[k];
                        const v = a?.median_upvote_percentile;
                        const bg = v != null
                          ? `color-mix(in srgb, var(--g-green) ${Math.round((v / 100) * 60)}%, transparent)`
                          : "transparent";
                        return (
                          <tr key={k}>
                            <td style={{ color: "var(--md-on-surface-var)", fontSize: 12 }}>
                              {AGREEMENT_LABELS[k]}
                            </td>
                            <td
                              style={{
                                fontFamily: "var(--font-mono)",
                                textAlign: "right",
                                background: bg,
                                borderRadius: 4,
                                padding: "2px 6px",
                              }}
                            >
                              {fmtPctile(v)}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </>
              ) : (
                <div
                  style={{
                    color: "var(--md-on-surface-var)",
                    fontSize: 12,
                    padding: "4px 0",
                  }}
                >
                  Upvote at least one photo to see agreement.
                </div>
              )}
            </div>

            <div style={cardStyle}>
              <div style={cardHeaderStyle}>How this works</div>
              <ol
                style={{
                  paddingLeft: 18,
                  margin: 0,
                  fontSize: 12,
                  lineHeight: 1.55,
                  color: "var(--md-on-surface)",
                }}
              >
                <li>
                  These are the photos NIMA+AP scored lowest. Most are genuinely bad.
                </li>
                <li>
                  Click any photo that's actually good — it becomes a rescue.
                </li>
                <li>
                  Press <span style={kbdStyle}>↵</span> to save the batch and load the next 60.
                </li>
                <li>
                  Press <span style={kbdStyle}>R</span> after a few batches to retrain.
                  Personal-model agreement rises as it learns your rescues.
                </li>
                <li>
                  Click the ⛶ icon (or double-click) for full-size view with ←/→ nav.
                </li>
              </ol>
            </div>

            <div
              style={{
                ...cardStyle,
                minHeight: 0,
                overflowY: "auto",
                fontSize: 11,
                color: "var(--md-on-surface-var)",
              }}
            >
              {toast || (
                <span style={{ color: "var(--md-on-surface-var)" }}>
                  Ready. Score top right of each photo = combined NIMA+AP percentile.
                  Double-click any thumb to enlarge.
                </span>
              )}
            </div>
          </aside>
        </div>
      </div>

      {lightboxIdx !== null && photos[lightboxIdx] && (() => {
        const p = photos[lightboxIdx];
        const isFlipped = flipped.has(p.photo_id);
        const finalRating = isFlipped ? -p.default_rating : p.default_rating;
        return (
          <div
            onClick={() => setLightboxIdx(null)}
            style={{
              position: "fixed",
              inset: 0,
              background: "rgba(0,0,0,0.96)",
              zIndex: 90,
              display: "grid",
              gridTemplateRows: "auto 1fr auto",
              cursor: "zoom-out",
            }}
          >
            {/* Top bar */}
            <div
              onClick={(e) => e.stopPropagation()}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                padding: "10px 20px",
                color: "#fff",
                background: "rgba(0,0,0,0.5)",
                cursor: "default",
              }}
            >
              <button
                className="btn btn-text"
                style={{ color: "#fff" }}
                onClick={() => setLightboxIdx(null)}
              >
                ← Close (Esc)
              </button>
              <div
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 12,
                  color: "#bcc",
                }}
              >
                {lightboxIdx + 1} / {photos.length}
              </div>
              <div style={{ flex: 1 }} />
              <div
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 12,
                  color: "#bcc",
                  display: "flex",
                  gap: 14,
                }}
              >
                <span>
                  combined{" "}
                  <span style={{ color: "#fff" }}>
                    {p.scores.combined?.toFixed(0) ?? "—"}
                  </span>
                </span>
                <span>
                  nima{" "}
                  <span style={{ color: "#fff" }}>
                    {p.scores.nima?.toFixed(2) ?? "—"}
                  </span>
                </span>
                <span>
                  ap25{" "}
                  <span style={{ color: "#fff" }}>
                    {p.scores.ap25?.toFixed(2) ?? "—"}
                  </span>
                </span>
                <span>
                  iqa{" "}
                  <span style={{ color: "#fff" }}>
                    {p.scores.iqa?.toFixed(3) ?? "—"}
                  </span>
                </span>
              </div>
            </div>

            {/* Image */}
            <div
              style={{
                display: "grid",
                placeItems: "center",
                overflow: "hidden",
                padding: 12,
              }}
            >
              <img
                src={p.preview_url}
                alt=""
                onClick={(e) => e.stopPropagation()}
                style={{
                  maxWidth: "100%",
                  maxHeight: "100%",
                  objectFit: "contain",
                  boxShadow: "0 20px 60px rgba(0,0,0,0.7)",
                  cursor: "default",
                }}
              />
            </div>

            {/* Bottom controls */}
            <div
              onClick={(e) => e.stopPropagation()}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                padding: "12px 20px",
                color: "#fff",
                background: "rgba(0,0,0,0.5)",
                cursor: "default",
              }}
            >
              <button
                className="btn btn-text"
                style={{ color: "#fff" }}
                onClick={() =>
                  setLightboxIdx((i) =>
                    i === null ? null : Math.max(0, i - 1),
                  )
                }
                disabled={lightboxIdx === 0}
              >
                ← Prev
              </button>
              <button
                className="btn btn-text"
                style={{ color: "#fff" }}
                onClick={() =>
                  setLightboxIdx((i) =>
                    i === null ? null : Math.min(photos.length - 1, i + 1),
                  )
                }
                disabled={lightboxIdx === photos.length - 1}
              >
                Next →
              </button>
              <div style={{ flex: 1 }} />
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  color: "#aab",
                }}
              >
                ← → navigate · F flip · Esc close
              </span>
              <button
                className={isFlipped ? "btn btn-filled" : "btn btn-tonal"}
                onClick={() => toggle(p.photo_id)}
                style={{ minWidth: 160 }}
              >
                {finalRating > 0 ? "👍 Good" : "👎 Bad"}{" "}
                {isFlipped && <span style={{ opacity: 0.7, marginLeft: 4 }}>(flipped)</span>}
              </button>
            </div>
          </div>
        );
      })()}
    </div>
  );
}

const cardStyle: React.CSSProperties = {
  background: "var(--md-surface-c-low)",
  border: "1px solid var(--md-outline-var)",
  borderRadius: 12,
  padding: "10px 12px",
};

const cardHeaderStyle: React.CSSProperties = {
  fontSize: 10,
  textTransform: "uppercase",
  letterSpacing: "0.06em",
  color: "var(--md-on-surface-var)",
  marginBottom: 8,
  display: "flex",
  alignItems: "center",
  gap: 6,
};

const tableStyle: React.CSSProperties = {
  width: "100%",
  fontSize: 12,
  borderCollapse: "collapse",
};

const thStyle: React.CSSProperties = {
  fontWeight: 500,
  color: "var(--md-on-surface-var)",
  fontSize: 10,
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  textAlign: "right",
  paddingBottom: 4,
};

const kbdStyle: React.CSSProperties = {
  display: "inline-block",
  background: "var(--md-surface-c)",
  border: "1px solid var(--md-outline-var)",
  borderRadius: 4,
  padding: "1px 5px",
  fontFamily: "var(--font-mono)",
  fontSize: 10,
  marginLeft: 4,
};
