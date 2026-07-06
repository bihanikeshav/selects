import { useCallback, useEffect, useState } from "react";
import { useParams, useSearchParams, Link } from "react-router-dom";

import { listClusterPhotos } from "../api/client";
import type { Photo } from "../api/types";
import KbdFooter from "../components/KbdFooter";
import Rail from "../components/Rail";
import StatusRow from "../components/StatusRow";
import Topbar from "../components/Topbar";

interface EditStatus {
  edited: boolean;
  mtime: number | null;
}

export default function ClusterDetail() {
  const { tag = "" } = useParams<{ tag: string }>();
  const [params] = useSearchParams();
  const source = params.get("source") || "thematic";

  const [photos, setPhotos] = useState<Photo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [editStatus, setEditStatus] = useState<Record<string, EditStatus>>({});
  const [lightbox, setLightbox] = useState<string | null>(null);

  const [launching, setLaunching] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

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

  const refreshEditStatus = useCallback(async () => {
    if (photos.length === 0) return;
    const shas = photos.map((p) => p.sha256).join(",");
    try {
      const res = await fetch(`/api/edits/status?shas=${shas}`);
      if (!res.ok) return;
      const data = (await res.json()) as Record<string, EditStatus>;
      setEditStatus(data);
    } catch {
      // non-fatal
    }
  }, [photos]);

  // Poll every 4 seconds while darktable might be writing — cheap.
  useEffect(() => {
    refreshEditStatus();
    const t = setInterval(refreshEditStatus, 4000);
    return () => clearInterval(t);
  }, [refreshEditStatus]);

  function toggle(sha: string) {
    const next = new Set(selected);
    if (next.has(sha)) next.delete(sha);
    else next.add(sha);
    setSelected(next);
  }

  function selectAll() { setSelected(new Set(photos.map((p) => p.sha256))); }
  function clearSelection() { setSelected(new Set()); }
  function selectAllEdited() {
    const editedShas = Object.entries(editStatus)
      .filter(([, s]) => s.edited)
      .map(([sha]) => sha);
    setSelected(new Set(editedShas));
  }

  const editedCount = Object.values(editStatus).filter((s) => s.edited).length;

  async function openInDarktable() {
    if (selected.size === 0) return;
    setLaunching(true);
    setToast(null);
    try {
      const res = await fetch("/api/edit/darktable", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sha256s: Array.from(selected) }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || `darktable launch ${res.status}`);
      }
      const j = await res.json();
      setToast(`darktable opened with ${j.opened} photos. Edit, save (Ctrl+S), close. We'll detect XMPs in real time.`);
    } catch (e) {
      setToast(String(e));
    } finally {
      setLaunching(false);
    }
  }

  async function exportEdited() {
    const editedShas = Object.entries(editStatus)
      .filter(([sha, s]) => s.edited && (selected.size === 0 || selected.has(sha)))
      .map(([sha]) => sha);
    if (editedShas.length === 0) {
      setToast("No edited photos to export. Make edits in darktable and Ctrl+S first.");
      return;
    }
    setExporting(true);
    setToast(null);
    try {
      const res = await fetch("/api/edits/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sha256s: editedShas,
          cluster_name: decoded,
          width: 2048,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || `export ${res.status}`);
      }
      const j = await res.json();
      setToast(`Exported ${j.exported} / ${j.total} JPEGs → ${j.out_dir}`);
    } catch (e) {
      setToast(String(e));
    } finally {
      setExporting(false);
    }
  }

  return (
    <div className="app">
      <Rail />
      <div className="workspace">
        <Topbar folder="selects" context={`cluster · ${decoded}`} />
        <StatusRow
          pos={`${photos.length} photos`}
          keepersCount={editedCount}
          details={`${selected.size} selected · ${editedCount} edited · source: ${source}`}
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
            <button className="btn btn-text" onClick={selectAllEdited} disabled={editedCount === 0} title="Select all photos with darktable edits">
              Select edited ({editedCount})
            </button>
            <button className="btn btn-text" onClick={clearSelection} disabled={selected.size === 0}>Clear</button>

            <button
              className="btn btn-tonal"
              onClick={openInDarktable}
              disabled={selected.size === 0 || launching}
              title="Launch darktable with the selected originals in a per-session library"
            >
              {launching ? "Launching…" : `Edit ${selected.size} in darktable`}
            </button>
            <button
              className="btn btn-filled"
              onClick={exportEdited}
              disabled={exporting || editedCount === 0}
              title="Render the XMP edits to JPEGs via darktable-cli"
            >
              {exporting ? "Exporting…" : `Export ${selected.size > 0 ? "selected" : "all"} edited`}
            </button>
          </div>

          {toast && (
            <div className="cluster-detail-toast">{toast}</div>
          )}

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
            {visiblePhotos.map((p) => {
              const sel = selected.has(p.sha256);
              const isEdited = editStatus[p.sha256]?.edited;
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
                  {isEdited && (
                    <span
                      title={editStatus[p.sha256]?.mtime ? `Edited ${new Date(editStatus[p.sha256]!.mtime! * 1000).toLocaleString()}` : "Edited"}
                      style={{
                        position: "absolute",
                        bottom: 6,
                        left: 6,
                        background: "var(--g-yellow)",
                        color: "#1B1B1F",
                        fontFamily: "var(--font-mono)",
                        fontSize: 10,
                        fontWeight: 700,
                        padding: "2px 6px",
                        borderRadius: 4,
                        boxShadow: "0 2px 4px rgba(0,0,0,0.4)",
                      }}
                    >
                      EDITED
                    </span>
                  )}
                  <button
                    onClick={() => setLightbox(p.sha256)}
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

      {lightbox && (
        <div
          onClick={() => setLightbox(null)}
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.92)", zIndex: 90, display: "grid", placeItems: "center", cursor: "zoom-out" }}
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
