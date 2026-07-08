import { useCallback, useEffect, useState } from "react";

import PageHeader from "../components/PageHeader";
import Rail from "../components/Rail";
import "../components/Doctor.css";

type Bucket = "underexposed" | "overexposed" | "out_of_focus" | "blurry_keepers";
type Model = "clahe" | "retinexformer";

interface Issue {
  photo_id: number;
  sha256: string;
  taken_at: string | null;
  thumb_url: string;
  preview_url: string;
  blur: number;
  exposure: number;
  combined: number | null;
  luma_mean?: number;
  clipped_high?: number;
}

interface DoctorResp {
  underexposed: Issue[];
  overexposed: Issue[];
  out_of_focus: Issue[];
  blurry_keepers: Issue[];
  counts: Record<Bucket, number>;
}

const BUCKET_META: Record<
  Bucket,
  { label: string; hint: string; suggestedModel: Model; accent: string }
> = {
  underexposed: {
    label: "Underexposed",
    hint: "Too dark — Brighten (Retinexformer) lifts shadows with natural colour",
    suggestedModel: "retinexformer",
    accent: "var(--g-blue)",
  },
  overexposed: {
    label: "Overexposed",
    hint: "Highlights clipped — Auto fix roll-off helps recover sky/snow detail",
    suggestedModel: "clahe",
    accent: "var(--g-yellow)",
  },
  out_of_focus: {
    label: "Out of focus",
    hint: "Genuinely soft — sharpness below threshold. Review these to reject.",
    suggestedModel: "clahe",
    accent: "var(--g-red)",
  },
  blurry_keepers: {
    label: "Blurry but aesthetic",
    hint: "High aesthetic but slightly soft — worth a second look.",
    suggestedModel: "clahe",
    accent: "var(--g-green)",
  },
};

/** Human-friendly labels for the fix models. The backend `model=` values
 * (clahe / retinexformer) never change — this is presentation only. */
const MODEL_META: Record<Model, { label: string; sub: string }> = {
  clahe: { label: "Auto edit", sub: "Auto tone · WB, exposure, contrast, vibrance" },
  retinexformer: { label: "Brighten (low-light)", sub: "Retinexformer · ICCV'23" },
};

const MODEL_ORDER: Model[] = ["clahe", "retinexformer"];

export default function Doctor() {
  const [data, setData] = useState<DoctorResp | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [bucket, setBucket] = useState<Bucket>("underexposed");
  const [showFix, setShowFix] = useState<Record<string, boolean>>({});
  const [perPhotoModel, setPerPhotoModel] = useState<Record<string, Model>>({});

  // Full-image viewer state
  const [viewerSha, setViewerSha] = useState<string | null>(null);
  const [viewerShowFixed, setViewerShowFixed] = useState(false);
  const [viewerLoading, setViewerLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    fetch("/api/doctor/issues")
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((j: DoctorResp) => {
        setData(j);
        setLoading(false);
      })
      .catch((e) => {
        setErr(String(e));
        setLoading(false);
      });
  }, []);

  const photos = data ? data[bucket] : [];
  const counts = data?.counts;

  const toggleFix = useCallback((sha: string) => {
    setShowFix((prev) => ({ ...prev, [sha]: !prev[sha] }));
  }, []);

  const setModel = useCallback((sha: string, m: Model) => {
    setPerPhotoModel((prev) => ({ ...prev, [sha]: m }));
  }, []);

  const modelFor = (sha: string): Model =>
    perPhotoModel[sha] ?? BUCKET_META[bucket].suggestedModel;

  const openViewer = useCallback((sha: string) => {
    setViewerSha(sha);
    setViewerShowFixed(false);
  }, []);

  const closeViewer = useCallback(() => setViewerSha(null), []);

  // Viewer keyboard (Esc to close, Left/Right to toggle original/fixed)
  useEffect(() => {
    if (!viewerSha) return;
    function onKey(e: KeyboardEvent) {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "Escape") setViewerSha(null);
      if (e.key === "ArrowLeft") setViewerShowFixed(false);
      if (e.key === "ArrowRight") setViewerShowFixed(true);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [viewerSha]);

  const viewerPhoto = viewerSha ? photos.find((p) => p.sha256 === viewerSha) ?? null : null;
  const viewerModel = viewerSha ? modelFor(viewerSha) : "clahe";
  const viewerSrc = viewerSha
    ? viewerShowFixed
      ? `/api/enhance/${viewerSha}?model=${viewerModel}&grade=true`
      : `/api/preview/${viewerSha}`
    : "";

  return (
    <div className="app">
      <Rail />
      <div
        className="workspace"
        style={{
          display: "grid",
          gridTemplateRows: "auto 1fr",
          height: "100vh",
          maxHeight: "100vh",
          overflow: "hidden",
        }}
      >
        <PageHeader
          context="image doctor"
          title="Doctor"
          subtitle="Photos with detectable issues. Pick a fix, preview the result."
          controls={(Object.keys(BUCKET_META) as Bucket[]).map((b) => {
            const meta = BUCKET_META[b];
            const n = counts?.[b] ?? 0;
            const active = b === bucket;
            return (
              <button
                key={b}
                onClick={() => setBucket(b)}
                title={meta.hint}
                className={`doctor-bucket-tab${active ? " is-active" : ""}`}
                style={
                  active
                    ? { background: meta.accent, borderColor: meta.accent }
                    : undefined
                }
              >
                {meta.label}
                <span className="doctor-bucket-count">{n}</span>
              </button>
            );
          })}
        />

        <div className="doctor-body">
          {err && <div className="view-banner view-banner-error">{err}</div>}

          {loading ? (
            <div className="doctor-loading">Loading…</div>
          ) : photos.length === 0 ? (
            <div className="doctor-empty">
              No photos in the <b>{BUCKET_META[bucket].label}</b> bucket — clean library on this axis.
            </div>
          ) : (
            <div className="doctor-grid">
              {photos.map((p) => {
                const fix = !!showFix[p.sha256];
                const m = modelFor(p.sha256);
                const meta = MODEL_META[m];
                const fixUrl = `/api/enhance/${p.sha256}?model=${m}&grade=true`;
                return (
                  <div key={p.photo_id} className="doctor-card">
                    <div
                      className="doctor-thumb-wrap"
                      onClick={() => openViewer(p.sha256)}
                      title="Click to open full viewer"
                    >
                      <img
                        className="doctor-thumb"
                        src={fix ? fixUrl : p.preview_url}
                        alt=""
                      />
                      {fix && (
                        <span
                          className="doctor-thumb-badge"
                          style={{ background: BUCKET_META[bucket].accent }}
                        >
                          {meta.label}
                        </span>
                      )}
                      <span className="doctor-thumb-expand">Open viewer ⤢</span>
                    </div>

                    <div className="doctor-stats">
                      {p.luma_mean !== undefined && (
                        <span>
                          luma <b>{(p.luma_mean * 100).toFixed(0)}%</b>
                        </span>
                      )}
                      {p.clipped_high !== undefined && (
                        <span>
                          clip <b>{(p.clipped_high * 100).toFixed(1)}%</b>
                        </span>
                      )}
                      <span>
                        sharpness <b>{p.blur.toFixed(0)}</b>
                      </span>
                      {p.combined != null && (
                        <span>
                          ★ <b>{p.combined.toFixed(2)}</b>
                        </span>
                      )}
                    </div>

                    <div className="doctor-controls-row">
                      <div className="doctor-model-field">
                        <select
                          className="doctor-model-select"
                          value={m}
                          onChange={(e) => setModel(p.sha256, e.target.value as Model)}
                          title="Fix"
                        >
                          {MODEL_ORDER.map((mv) => (
                            <option key={mv} value={mv}>
                              {MODEL_META[mv].label}
                            </option>
                          ))}
                        </select>
                        <span className="doctor-model-hint">{meta.sub}</span>
                      </div>
                      <button
                        onClick={() => toggleFix(p.sha256)}
                        className={`btn ${fix ? "btn-filled" : "btn-tonal"}`}
                        style={{ fontSize: 12, padding: "6px 10px", alignSelf: "flex-start" }}
                      >
                        {fix ? "Original" : "Preview fix"}
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {viewerSha && (
        <div className="doctor-viewer-backdrop" onClick={closeViewer}>
          <div className="doctor-viewer-topbar" onClick={(e) => e.stopPropagation()}>
            <div className="doctor-viewer-title">
              <strong>{BUCKET_META[bucket].label}</strong>
              {viewerPhoto?.taken_at && <span>{new Date(viewerPhoto.taken_at).toLocaleString()}</span>}
            </div>
            <button className="doctor-viewer-close" onClick={closeViewer} title="Close (Esc)">
              ×
            </button>
          </div>

          <div className="doctor-viewer-stage" onClick={(e) => e.stopPropagation()}>
            {viewerLoading && <div className="doctor-viewer-spinner">Loading fix…</div>}
            <img
              key={viewerSrc}
              className="doctor-viewer-img"
              src={viewerSrc}
              alt=""
              onLoadStart={() => viewerShowFixed && setViewerLoading(true)}
              onLoad={() => setViewerLoading(false)}
              onError={() => setViewerLoading(false)}
            />
          </div>

          <div className="doctor-viewer-toolbar" onClick={(e) => e.stopPropagation()}>
            <div className="doctor-toggle">
              <button
                className={!viewerShowFixed ? "active" : ""}
                onClick={() => setViewerShowFixed(false)}
              >
                Original
              </button>
              <button
                className={viewerShowFixed ? "active" : ""}
                onClick={() => setViewerShowFixed(true)}
              >
                Fixed
              </button>
            </div>

            <div className="doctor-viewer-model-field">
              <label htmlFor="doctor-viewer-model">Fix</label>
              <select
                id="doctor-viewer-model"
                className="doctor-viewer-model-select"
                value={viewerModel}
                onChange={(e) => setModel(viewerSha, e.target.value as Model)}
              >
                {MODEL_ORDER.map((mv) => (
                  <option key={mv} value={mv}>
                    {MODEL_META[mv].label} — {MODEL_META[mv].sub}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
