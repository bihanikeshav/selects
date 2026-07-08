import { useEffect, useState } from "react";
import { useParams, useSearchParams, Link } from "react-router-dom";

import { listClusterPhotos } from "../api/client";
import type { Photo } from "../api/types";
import KbdFooter from "../components/KbdFooter";
import Rail from "../components/Rail";
import Viewer from "../components/Viewer";
import PhotoEditor from "../editor/PhotoEditor";
import StatusRow from "../components/StatusRow";
import Topbar from "../components/Topbar";

export default function ClusterDetail() {
  const { tag = "" } = useParams<{ tag: string }>();
  const [params] = useSearchParams();
  const source = params.get("source") || "thematic";

  const [photos, setPhotos] = useState<Photo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [lightbox, setLightbox] = useState<number | null>(null);
  const [editShas, setEditShas] = useState<string[] | null>(null);

  // Aesthetic filter: drop photos below this library-wide percentile.
  // 0 = show all; 50 = top half; 75 = top 25%.
  const [aestheticPct, setAestheticPct] = useState<number>(0);
  const [sortByAesthetic, setSortByAesthetic] = useState<boolean>(false);

  const decoded = decodeURIComponent(tag);

  useEffect(() => {
    setLoading(true);
    listClusterPhotos(decoded, source, 500)
      .then((r) => {
        setPhotos(r.items);
        setLoading(false);
      })
      .catch((e) => {
        setError(String(e));
        setLoading(false);
      });
  }, [decoded, source]);

  // Derived: filter + sort
  const visiblePhotos = (() => {
    let list = photos;
    if (aestheticPct > 0) {
      const scored = photos.filter((p) => p.aesthetic_iqa != null);
      if (scored.length > 1) {
        const sorted = [...scored].map((p) => p.aesthetic_iqa as number).sort((a, b) => a - b);
        const idx = Math.floor((aestheticPct / 100) * (sorted.length - 1));
        const threshold = sorted[idx];
        list = photos.filter((p) => (p.aesthetic_iqa ?? -1) >= threshold);
      }
    }
    if (sortByAesthetic) {
      list = [...list].sort((a, b) => (b.aesthetic_iqa ?? -1) - (a.aesthetic_iqa ?? -1));
    }
    return list;
  })();

  function toggle(sha: string) {
    const next = new Set(selected);
    if (next.has(sha)) next.delete(sha);
    else next.add(sha);
    setSelected(next);
  }

  function selectAll() { setSelected(new Set(photos.map((p) => p.sha256))); }
  function clearSelection() { setSelected(new Set()); }

  function openInEditor() {
    if (selected.size === 0) return;
    setEditShas(Array.from(selected));
  }

  return (
    <div className="app">
      <Rail />
      <div className="workspace">
        <Topbar folder="selects" context={`cluster · ${decoded}`} />
        <StatusRow
          pos={`${photos.length} photos`}
          keepersCount={selected.size}
          details={`${selected.size} selected · source: ${source}`}
        />

        <div className="cluster-detail-wrap">
          <div className="cluster-detail-toolbar">
            <Link to="/cull/clusters" className="btn btn-text" style={{ paddingLeft: 8 }}>
              ← All clusters
            </Link>
            <h1 style={{ margin: 0, fontFamily: "var(--font-display)", fontWeight: 500, fontSize: 28 }}>
              {decoded}
            </h1>

            <div style={{ flex: 1 }} />

            <button className="btn btn-text" onClick={selectAll} disabled={photos.length === 0}>Select all</button>
            <button className="btn btn-text" onClick={clearSelection} disabled={selected.size === 0}>Clear</button>

            <button
              className="btn btn-filled"
              onClick={openInEditor}
              disabled={selected.size === 0}
              title="Edit the selected photos in the built-in editor"
            >
              {`Edit ${selected.size || ""}`}
            </button>
          </div>

          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              padding: "8px 0",
              borderTop: "1px solid var(--md-outline-var)",
              borderBottom: "1px solid var(--md-outline-var)",
              fontSize: 12,
              color: "var(--md-on-surface-var)",
            }}
          >
            <span>Aesthetic ≥ p{aestheticPct}</span>
            <input
              type="range"
              min={0}
              max={95}
              step={5}
              value={aestheticPct}
              onChange={(e) => setAestheticPct(Number(e.target.value))}
              style={{ width: 220 }}
            />
            <span style={{ fontFamily: "var(--font-mono)", color: "var(--md-on-surface)" }}>
              {visiblePhotos.length} / {photos.length}
            </span>
            <div style={{ flex: 1 }} />
            <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={sortByAesthetic}
                onChange={(e) => setSortByAesthetic(e.target.checked)}
              />
              <span>Sort by aesthetic ★</span>
            </label>
          </div>

          {loading && <div className="cluster-detail-empty">Loading…</div>}
          {error && <div className="cluster-detail-empty error">{error}</div>}
          {!loading && !error && visiblePhotos.length === 0 && (
            <div className="cluster-detail-empty">
              {photos.length === 0 ? "No photos in this cluster." : "No photos match the aesthetic filter."}
            </div>
          )}

          <div className="cluster-detail-grid">
            {visiblePhotos.map((p, i) => {
              const sel = selected.has(p.sha256);
              return (
                <div key={p.sha256} className={`cluster-photo${sel ? " is-selected" : ""}`} style={{ position: "relative" }}>
                  <button
                    onClick={() => toggle(p.sha256)}
                    style={{ display: "block", width: "100%", height: "100%", padding: 0, border: 0, background: "transparent", cursor: "pointer" }}
                    aria-pressed={sel}
                    aria-label={`select photo ${p.sha256.slice(0, 8)}`}
                  >
                    <img src={p.thumb_url} alt="" loading="lazy" />
                  </button>
                  {sel && <span className="cluster-photo-check">✓</span>}
                  <button
                    onClick={() => setLightbox(i)}
                    aria-label="enlarge"
                    title="Enlarge"
                    style={{
                      position: "absolute",
                      top: 6,
                      right: 6,
                      width: 24,
                      height: 24,
                      borderRadius: "50%",
                      background: "rgba(0,0,0,0.55)",
                      color: "#fff",
                      border: 0,
                      cursor: "zoom-in",
                      fontSize: 14,
                      display: "grid",
                      placeItems: "center",
                    }}
                  >
                    ↗
                  </button>
                </div>
              );
            })}
          </div>
        </div>

        <KbdFooter />
      </div>

      {lightbox !== null && visiblePhotos[lightbox] && (
        <Viewer
          items={visiblePhotos.map((p) => ({ sha256: p.sha256 }))}
          index={lightbox}
          onIndex={setLightbox}
          onClose={() => setLightbox(null)}
          renderActions={(it) => (
            <button
              className="btn btn-filled"
              onClick={() => {
                setLightbox(null);
                setEditShas([it.sha256]);
              }}
            >
              Edit
            </button>
          )}
        />
      )}

      {editShas && <PhotoEditor shas={editShas} onClose={() => setEditShas(null)} />}
    </div>
  );
}
