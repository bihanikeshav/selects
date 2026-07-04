import { useState } from "react";

import KbdFooter from "../components/KbdFooter";
import Rail from "../components/Rail";
import StatusRow from "../components/StatusRow";
import Topbar from "../components/Topbar";

interface SearchHit {
  photo_id: number;
  sha256: string;
  score: number;
  thumb_url: string;
  preview_url: string;
}

export default function Search() {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [lightbox, setLightbox] = useState<string | null>(null);

  async function runSearch(e?: React.FormEvent) {
    if (e) e.preventDefault();
    if (!q.trim()) return;
    setLoading(true);
    setErr(null);
    try {
      const res = await fetch(`/api/search?q=${encodeURIComponent(q)}&k=60`);
      if (!res.ok) throw new Error(`search ${res.status}`);
      const data = await res.json();
      setHits(data.results);
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }

  const SUGGESTIONS = [
    "monastery interior",
    "yak in the mountains",
    "golden hour landscape",
    "food on a plate",
    "prayer flags",
    "snow on rocks",
    "river reflection",
    "person smiling",
  ];

  return (
    <div className="app">
      <Rail />
      <div className="workspace">
        <Topbar folder="travelcull" context="search" />
        <StatusRow
          pos={hits.length > 0 ? `${hits.length} hits` : "search"}
          keepersCount={0}
          details={loading ? "searching…" : err ?? "type and press Enter"}
        />

        <div className="cluster-detail-wrap" style={{ paddingTop: 20 }}>
          <form onSubmit={runSearch} style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <input
              type="search"
              value={q}
              onChange={e => setQ(e.target.value)}
              placeholder="Search photos — try 'monastery interior' or 'yaks in pasture'"
              autoFocus
              style={{
                flex: 1,
                height: 44,
                padding: "0 18px",
                borderRadius: 22,
                border: "1px solid var(--md-outline)",
                background: "var(--md-surface)",
                color: "var(--md-on-surface)",
                fontFamily: "var(--font-body)",
                fontSize: 14,
                outline: "none",
              }}
            />
            <button type="submit" className="btn btn-filled" disabled={loading || !q.trim()}>
              {loading ? "…" : "Search"}
            </button>
          </form>

          {hits.length === 0 && !loading && (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
              {SUGGESTIONS.map(s => (
                <button
                  key={s}
                  className="filter-chip"
                  onClick={() => {
                    setQ(s);
                    setTimeout(runSearch, 0);
                  }}
                >
                  {s}
                </button>
              ))}
            </div>
          )}

          <div className="cluster-detail-grid">
            {hits.map((h, i) => (
              <button
                key={h.sha256}
                className="cluster-photo"
                onClick={() => setLightbox(h.sha256)}
                style={{ cursor: "zoom-in" }}
                title={`rank ${i + 1} · score ${h.score.toFixed(3)}`}
              >
                <img src={h.thumb_url} alt="" loading="lazy" />
                <span
                  className="cluster-photo-check"
                  style={{
                    background: "rgba(0,0,0,0.55)",
                    color: "#fff",
                    fontSize: 11,
                    fontWeight: 500,
                    fontFamily: "var(--font-mono)",
                  }}
                >
                  {h.score.toFixed(2)}
                </span>
              </button>
            ))}
          </div>
        </div>

        <KbdFooter />
      </div>

      {lightbox && (
        <div
          onClick={() => setLightbox(null)}
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
            src={`/api/preview/${lightbox}`}
            alt=""
            style={{ maxWidth: "94vw", maxHeight: "94vh", boxShadow: "0 12px 60px rgba(0,0,0,0.8)" }}
          />
        </div>
      )}
    </div>
  );
}
