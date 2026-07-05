import { useCallback, useEffect, useState } from "react";

import Rail from "../components/Rail";
import Topbar from "../components/Topbar";

type Bucket = "underexposed" | "overexposed" | "blurry" | "blurry_keepers";
type Model = "clahe" | "zero-dce-plus" | "csrnet" | "nafnet";

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
  blurry: Issue[];
  blurry_keepers: Issue[];
  counts: Record<Bucket, number>;
}

const BUCKET_META: Record<
  Bucket,
  { label: string; hint: string; suggestedModel: Model; accent: string }
> = {
  underexposed: {
    label: "Underexposed",
    hint: "Too dark — Zero-DCE++ can lift shadows naturally",
    suggestedModel: "zero-dce-plus",
    accent: "var(--g-blue)",
  },
  overexposed: {
    label: "Overexposed",
    hint: "Highlights clipped — CLAHE roll-off helps recover sky/snow detail",
    suggestedModel: "clahe",
    accent: "var(--g-yellow)",
  },
  blurry: {
    label: "Blurry",
    hint: "Genuinely soft — NAFNet deblur (TODO) would rescue these",
    suggestedModel: "nafnet",
    accent: "var(--g-red)",
  },
  blurry_keepers: {
    label: "Blurry but aesthetic",
    hint: "High aesthetic, slightly soft — worth a deblur pass",
    suggestedModel: "nafnet",
    accent: "var(--g-green)",
  },
};

export default function Doctor() {
  const [data, setData] = useState<DoctorResp | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [bucket, setBucket] = useState<Bucket>("underexposed");
  const [lightboxSha, setLightboxSha] = useState<string | null>(null);
  const [showFix, setShowFix] = useState<Record<string, boolean>>({});
  const [perPhotoModel, setPerPhotoModel] = useState<Record<string, Model>>({});

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

  // Lightbox keyboard
  useEffect(() => {
    if (!lightboxSha) return;
    function onKey(e: KeyboardEvent) {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "Escape") setLightboxSha(null);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [lightboxSha]);

  return (
    <div className="app">
      <Rail />
      <div
        className="workspace"
        style={{
          display: "grid",
          gridTemplateRows: "auto auto auto 1fr",
          height: "100vh",
          maxHeight: "100vh",
          overflow: "hidden",
        }}
      >
        <Topbar folder="travelcull" context="image doctor" />

        <div
          style={{
            padding: "12px 24px",
            borderBottom: "1px solid var(--md-outline-var)",
            display: "flex",
            alignItems: "center",
            gap: 14,
          }}
        >
          <h1
            style={{
              margin: 0,
              fontFamily: "var(--font-display)",
              fontSize: 22,
              fontWeight: 500,
            }}
          >
            Doctor
          </h1>
          <span style={{ color: "var(--md-on-surface-var)", fontSize: 13 }}>
            Photos with detectable issues. Pick a model, preview the fix.
          </span>
        </div>

        <div
          style={{
            padding: "8px 24px",
            display: "flex",
            gap: 6,
            borderBottom: "1px solid var(--md-outline-var)",
            overflowX: "auto",
          }}
        >
          {(Object.keys(BUCKET_META) as Bucket[]).map((b) => {
            const meta = BUCKET_META[b];
            const n = counts?.[b] ?? 0;
            const active = b === bucket;
            return (
              <button
                key={b}
                onClick={() => setBucket(b)}
                title={meta.hint}
                style={{
                  background: active
                    ? meta.accent
                    : "var(--md-surface-c)",
                  color: active ? "#000" : "var(--md-on-surface)",
                  border: `1px solid ${active ? meta.accent : "var(--md-outline-var)"}`,
                  padding: "8px 14px",
                  borderRadius: 999,
                  fontFamily: "var(--font-display)",
                  fontSize: 13,
                  fontWeight: active ? 600 : 500,
                  cursor: "pointer",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 7,
                  whiteSpace: "nowrap",
                }}
              >
                {meta.label}
                <span
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: 11,
                    background: active
                      ? "rgba(0,0,0,0.18)"
                      : "var(--md-surface)",
                    padding: "1px 7px",
                    borderRadius: 999,
                  }}
                >
                  {n}
                </span>
              </button>
            );
          })}
        </div>

        <div
          style={{
            padding: "12px 24px 16px",
            overflowY: "auto",
            minHeight: 0,
          }}
        >
          {err && (
            <div
              style={{
                padding: 12,
                color: "var(--g-red)",
                background: "color-mix(in srgb, var(--g-red) 12%, transparent)",
                borderRadius: 8,
                marginBottom: 12,
              }}
            >
              {err}
            </div>
          )}

          {loading ? (
            <div style={{ color: "var(--md-on-surface-var)", padding: 24 }}>Loading…</div>
          ) : photos.length === 0 ? (
            <div
              style={{
                padding: 36,
                textAlign: "center",
                color: "var(--md-on-surface-var)",
                fontSize: 14,
              }}
            >
              No photos in the <b>{BUCKET_META[bucket].label}</b> bucket — clean library on this axis.
            </div>
          ) : (
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
                gap: 12,
              }}
            >
              {photos.map((p) => {
                const fix = !!showFix[p.sha256];
                const m = modelFor(p.sha256);
                const fixUrl = `/api/enhance/${p.sha256}?model=${m}&grade=true`;
                return (
                  <div
                    key={p.photo_id}
                    style={{
                      background: "var(--md-surface-c-low)",
                      border: "1px solid var(--md-outline-var)",
                      borderRadius: 12,
                      overflow: "hidden",
                      display: "grid",
                      gridTemplateRows: "auto auto auto",
                    }}
                  >
                    <div
                      style={{
                        position: "relative",
                        aspectRatio: "4/3",
                        background: "#0d0d0f",
                        cursor: "zoom-in",
                      }}
                      onClick={() => setLightboxSha(p.sha256)}
                      title="Click to enlarge"
                    >
                      <img
                        src={fix ? fixUrl : p.thumb_url}
                        alt=""
                        style={{
                          width: "100%",
                          height: "100%",
                          objectFit: "cover",
                          display: "block",
                          transition: "opacity 160ms ease",
                        }}
                      />
                      {fix && (
                        <span
                          style={{
                            position: "absolute",
                            top: 8,
                            left: 8,
                            background: BUCKET_META[bucket].accent,
                            color: "#000",
                            padding: "3px 8px",
                            borderRadius: 999,
                            fontSize: 11,
                            fontFamily: "var(--font-mono)",
                            fontWeight: 600,
                          }}
                        >
                          {m === "zero-dce-plus" ? "Zero-DCE++" : m.toUpperCase()}
                        </span>
                      )}
                    </div>

                    <div
                      style={{
                        padding: "8px 12px",
                        display: "flex",
                        gap: 10,
                        flexWrap: "wrap",
                        fontFamily: "var(--font-mono)",
                        fontSize: 11,
                        color: "var(--md-on-surface-var)",
                      }}
                    >
                      {p.luma_mean !== undefined && (
                        <span>
                          luma <span style={{ color: "var(--md-on-surface)" }}>{(p.luma_mean * 100).toFixed(0)}%</span>
                        </span>
                      )}
                      {p.clipped_high !== undefined && (
                        <span>
                          clip <span style={{ color: "var(--md-on-surface)" }}>{(p.clipped_high * 100).toFixed(1)}%</span>
                        </span>
                      )}
                      <span>
                        blur <span style={{ color: "var(--md-on-surface)" }}>{p.blur.toFixed(0)}</span>
                      </span>
                      {p.combined != null && (
                        <span>
                          ★ <span style={{ color: "var(--md-on-surface)" }}>{p.combined.toFixed(2)}</span>
                        </span>
                      )}
                    </div>

                    <div
                      style={{
                        padding: "8px 10px 10px",
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        borderTop: "1px solid var(--md-outline-var)",
                      }}
                    >
                      <select
                        value={m}
                        onChange={(e) => setModel(p.sha256, e.target.value as Model)}
                        style={{
                          flex: 1,
                          background: "var(--md-surface)",
                          color: "var(--md-on-surface)",
                          border: "1px solid var(--md-outline-var)",
                          borderRadius: 6,
                          padding: "5px 8px",
                          fontFamily: "var(--font-mono)",
                          fontSize: 11,
                          cursor: "pointer",
                        }}
                        title="Model"
                      >
                        <option value="clahe">CLAHE (classical, fast)</option>
                        <option value="zero-dce-plus">Zero-DCE++ (low-light, ICCV'19)</option>
                        <option value="csrnet">CSRNet (retouch, experimental)</option>
                        <option value="nafnet">NAFNet (deblur, GoPro-trained)</option>
                      </select>
                      <button
                        onClick={() => toggleFix(p.sha256)}
                        className={`btn ${fix ? "btn-filled" : "btn-tonal"}`}
                        style={{ fontSize: 12, padding: "6px 10px" }}
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

      {lightboxSha && (
        <div
          onClick={() => setLightboxSha(null)}
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.95)",
            zIndex: 90,
            display: "grid",
            placeItems: "center",
            cursor: "zoom-out",
          }}
        >
          <img
            src={
              showFix[lightboxSha]
                ? `/api/enhance/${lightboxSha}?model=${modelFor(lightboxSha)}&grade=true`
                : `/api/preview/${lightboxSha}`
            }
            alt=""
            style={{ maxWidth: "94vw", maxHeight: "94vh" }}
          />
        </div>
      )}
    </div>
  );
}
