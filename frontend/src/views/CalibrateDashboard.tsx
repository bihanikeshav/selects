import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import Rail from "../components/Rail";
import TasteCard from "../components/TasteCard";
import Topbar from "../components/Topbar";

type Scores = {
  iqa: number | null;
  nima: number | null;
  ap25: number | null;
  personal: number | null;
};

type Row = {
  photo_id: number;
  sha256: string;
  taken_at: string | null;
  thumb_url: string;
  preview_url: string;
  scores: Scores;
  rating: number | null;
};

type SortKey = keyof Scores;

const SCORE_LABELS: Record<SortKey, string> = {
  iqa: "CLIP-IQA",
  nima: "NIMA",
  ap25: "AP V2.5",
  personal: "Personal",
};

function pearson(xs: number[], ys: number[]): number {
  const n = xs.length;
  if (n < 2) return NaN;
  const mx = xs.reduce((a, b) => a + b, 0) / n;
  const my = ys.reduce((a, b) => a + b, 0) / n;
  let num = 0, dx = 0, dy = 0;
  for (let i = 0; i < n; i++) {
    const a = xs[i] - mx;
    const b = ys[i] - my;
    num += a * b;
    dx += a * a;
    dy += b * b;
  }
  return num / (Math.sqrt(dx * dy) || 1);
}

function histogram(values: number[], bins = 20): { x0: number; x1: number; n: number }[] {
  const vs = values.filter((v) => v != null && !isNaN(v));
  if (vs.length === 0) return [];
  const min = Math.min(...vs);
  const max = Math.max(...vs);
  const step = (max - min) / bins || 1;
  const out: { x0: number; x1: number; n: number }[] = [];
  for (let i = 0; i < bins; i++) {
    const x0 = min + i * step;
    const x1 = x0 + step;
    out.push({ x0, x1, n: 0 });
  }
  for (const v of vs) {
    const i = Math.min(bins - 1, Math.floor((v - min) / step));
    out[i].n++;
  }
  return out;
}

function Histogram({ data, color }: { data: { x0: number; x1: number; n: number }[]; color: string }) {
  if (data.length === 0) return <div style={{ height: 60, color: "var(--md-on-surface-var)", fontSize: 12 }}>—</div>;
  const max = Math.max(...data.map((d) => d.n));
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 1, height: 50, marginTop: 4 }}>
      {data.map((d, i) => (
        <div
          key={i}
          title={`${d.x0.toFixed(2)}–${d.x1.toFixed(2)} · n=${d.n}`}
          style={{
            flex: 1,
            height: `${(d.n / max) * 100}%`,
            background: color,
            minHeight: d.n > 0 ? 2 : 0,
            borderRadius: "2px 2px 0 0",
          }}
        />
      ))}
    </div>
  );
}

const SCORE_COLORS: Record<SortKey, string> = {
  iqa: "var(--g-blue)",
  nima: "var(--g-green)",
  ap25: "var(--g-yellow)",
  personal: "var(--g-red)",
};

function formatScore(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return v.toFixed(2);
}

export default function CalibrateDashboard() {
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [sortKey, setSortKey] = useState<SortKey>("personal");
  const [sortDesc, setSortDesc] = useState(true);
  const [lightboxIdx, setLightboxIdx] = useState<number | null>(null);

  useEffect(() => {
    fetch("/api/calibrate/dashboard")
      .then((r) => r.json())
      .then((j) => {
        setRows(j.photos);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  const sortedRows = useMemo(() => {
    const r = [...rows];
    r.sort((a, b) => {
      const va = a.scores[sortKey];
      const vb = b.scores[sortKey];
      if (va === null && vb === null) return 0;
      if (va === null) return 1;
      if (vb === null) return -1;
      return sortDesc ? vb - va : va - vb;
    });
    return r;
  }, [rows, sortKey, sortDesc]);

  const histograms = useMemo(() => {
    const out: Record<SortKey, ReturnType<typeof histogram>> = {} as never;
    for (const k of Object.keys(SCORE_LABELS) as SortKey[]) {
      out[k] = histogram(
        rows.map((r) => r.scores[k]).filter((v): v is number => v != null),
      );
    }
    return out;
  }, [rows]);

  const correlations = useMemo(() => {
    const keys = Object.keys(SCORE_LABELS) as SortKey[];
    const out: Record<string, number> = {};
    for (const a of keys) {
      for (const b of keys) {
        const pairs = rows
          .map((r) => [r.scores[a], r.scores[b]] as [number | null, number | null])
          .filter((p): p is [number, number] => p[0] != null && p[1] != null);
        out[`${a}|${b}`] =
          pairs.length >= 2
            ? pearson(
                pairs.map((p) => p[0]),
                pairs.map((p) => p[1]),
              )
            : NaN;
      }
    }
    return out;
  }, [rows]);

  const ratedCount = rows.filter((r) => r.rating != null && r.rating !== 0).length;

  return (
    <div className="app">
      <Rail />
      <div className="workspace">
        <Topbar folder="travelcull" context="calibration dashboard" />

        <div style={{ gridRow: "2 / span 4", overflow: "auto", padding: "16px 24px 24px" }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 16, marginBottom: 16 }}>
            <h1 style={{ margin: 0, fontFamily: "var(--font-display)", fontSize: 22 }}>
              Calibration dashboard
            </h1>
            <div style={{ color: "var(--md-on-surface-var)", fontSize: 13 }}>
              {rows.length} photos · {ratedCount} rated
            </div>
            <div style={{ marginLeft: "auto" }}>
              <Link to="/calibrate" className="btn btn-text">
                ← Back to rating
              </Link>
            </div>
          </div>

          {loading ? (
            <div style={{ color: "var(--md-on-surface-var)" }}>Loading…</div>
          ) : (
            <>
              {/* Distributions + correlations */}
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: 16,
                  marginBottom: 24,
                }}
              >
                <div style={cardStyle}>
                  <div style={cardHeaderStyle}>Score distributions</div>
                  <div style={{ display: "grid", gap: 14 }}>
                    {(Object.keys(SCORE_LABELS) as SortKey[]).map((k) => {
                      const data = histograms[k];
                      const min = data[0]?.x0;
                      const max = data[data.length - 1]?.x1;
                      return (
                        <div key={k}>
                          <div
                            style={{
                              display: "flex",
                              alignItems: "baseline",
                              gap: 8,
                              fontSize: 12,
                              color: "var(--md-on-surface-var)",
                            }}
                          >
                            <span style={{ color: SCORE_COLORS[k], fontWeight: 600 }}>
                              {SCORE_LABELS[k]}
                            </span>
                            <span style={{ fontFamily: "var(--font-mono)" }}>
                              {min !== undefined ? `${min.toFixed(2)} – ${max?.toFixed(2)}` : "—"}
                            </span>
                          </div>
                          <Histogram data={data} color={SCORE_COLORS[k]} />
                        </div>
                      );
                    })}
                  </div>
                </div>

                <div style={cardStyle}>
                  <div style={cardHeaderStyle}>Pearson correlation between models</div>
                  <table style={{ width: "100%", fontSize: 13, borderCollapse: "collapse" }}>
                    <thead>
                      <tr>
                        <th></th>
                        {(Object.keys(SCORE_LABELS) as SortKey[]).map((k) => (
                          <th
                            key={k}
                            style={{ fontWeight: 500, color: "var(--md-on-surface-var)", textAlign: "center" }}
                          >
                            {SCORE_LABELS[k]}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {(Object.keys(SCORE_LABELS) as SortKey[]).map((a) => (
                        <tr key={a}>
                          <td style={{ color: "var(--md-on-surface-var)" }}>{SCORE_LABELS[a]}</td>
                          {(Object.keys(SCORE_LABELS) as SortKey[]).map((b) => {
                            const r = correlations[`${a}|${b}`];
                            const bg = !isNaN(r)
                              ? `color-mix(in srgb, ${
                                  r >= 0 ? "var(--g-green)" : "var(--g-red)"
                                } ${Math.round(Math.abs(r) * 50)}%, transparent)`
                              : "transparent";
                            return (
                              <td
                                key={b}
                                style={{
                                  fontFamily: "var(--font-mono)",
                                  textAlign: "center",
                                  background: bg,
                                  padding: "4px 8px",
                                  borderRadius: 4,
                                }}
                              >
                                {isNaN(r) ? "—" : (r >= 0 ? "+" : "") + r.toFixed(2)}
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div style={{ marginTop: 8, fontSize: 11, color: "var(--md-on-surface-var)" }}>
                    +1.0 = identical ranking · 0 = unrelated · –1.0 = opposite.
                    Look for models that disagree (low correlation) — they're capturing different things.
                  </div>
                </div>
              </div>

              <div style={{ marginBottom: 24 }}>
                <TasteCard />
              </div>

              {/* Sort controls */}
              <div style={{ ...cardStyle, marginBottom: 16, display: "flex", alignItems: "center", gap: 12 }}>
                <span style={{ color: "var(--md-on-surface-var)", fontSize: 12 }}>Sort by:</span>
                {(Object.keys(SCORE_LABELS) as SortKey[]).map((k) => (
                  <button
                    key={k}
                    className={`btn ${sortKey === k ? "btn-filled" : "btn-text"}`}
                    onClick={() => {
                      if (sortKey === k) setSortDesc((d) => !d);
                      else {
                        setSortKey(k);
                        setSortDesc(true);
                      }
                    }}
                  >
                    {SCORE_LABELS[k]} {sortKey === k ? (sortDesc ? "↓" : "↑") : ""}
                  </button>
                ))}
              </div>

              {/* Photo grid */}
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
                  gap: 10,
                }}
              >
                {sortedRows.map((r, idx) => (
                  <div
                    key={r.photo_id}
                    style={{
                      background: "var(--md-surface-c-low)",
                      border:
                        r.rating === 1
                          ? "2px solid var(--g-green)"
                          : r.rating === -1
                          ? "2px solid var(--g-red)"
                          : "1px solid var(--md-outline-var)",
                      borderRadius: 10,
                      overflow: "hidden",
                      cursor: "zoom-in",
                    }}
                    onClick={() => setLightboxIdx(idx)}
                  >
                    <div style={{ aspectRatio: "4/3", background: "#222" }}>
                      <img
                        src={r.thumb_url}
                        alt=""
                        loading="lazy"
                        style={{ width: "100%", height: "100%", objectFit: "cover" }}
                      />
                    </div>
                    <div
                      style={{
                        padding: "6px 8px",
                        fontFamily: "var(--font-mono)",
                        fontSize: 10,
                        display: "grid",
                        gap: 2,
                        color: "var(--md-on-surface-var)",
                      }}
                    >
                      <div style={{ display: "flex", justifyContent: "space-between" }}>
                        <span>iqa</span>
                        <span style={{ color: "var(--md-on-surface)" }}>{formatScore(r.scores.iqa)}</span>
                      </div>
                      <div style={{ display: "flex", justifyContent: "space-between" }}>
                        <span>nima</span>
                        <span style={{ color: "var(--md-on-surface)" }}>{formatScore(r.scores.nima)}</span>
                      </div>
                      <div style={{ display: "flex", justifyContent: "space-between" }}>
                        <span>ap25</span>
                        <span style={{ color: "var(--md-on-surface)" }}>{formatScore(r.scores.ap25)}</span>
                      </div>
                      <div style={{ display: "flex", justifyContent: "space-between" }}>
                        <span style={{ color: SCORE_COLORS.personal }}>personal</span>
                        <span style={{ color: "var(--md-on-surface)" }}>
                          {formatScore(r.scores.personal)}
                        </span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      {lightboxIdx !== null && sortedRows[lightboxIdx] && (
        <div
          onClick={() => setLightboxIdx(null)}
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.92)",
            zIndex: 90,
            display: "grid",
            placeItems: "center",
            cursor: "zoom-out",
          }}
        >
          <img
            src={sortedRows[lightboxIdx].preview_url}
            alt=""
            style={{ maxWidth: "94vw", maxHeight: "94vh" }}
          />
        </div>
      )}
    </div>
  );
}

const cardStyle: React.CSSProperties = {
  background: "var(--md-surface-c-low)",
  border: "1px solid var(--md-outline-var)",
  borderRadius: 14,
  padding: "14px 16px",
};

const cardHeaderStyle: React.CSSProperties = {
  fontSize: 11,
  textTransform: "uppercase",
  letterSpacing: "0.06em",
  color: "var(--md-on-surface-var)",
  marginBottom: 10,
};
