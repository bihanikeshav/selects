import { useEffect, useState } from "react";
import { useParams, useSearchParams, Link } from "react-router-dom";

import { listClusterPhotos, openInEditor } from "../api/client";
import type { Photo } from "../api/types";
import KbdFooter from "../components/KbdFooter";
import Rail from "../components/Rail";
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
  const [editor, setEditor] = useState<"darktable" | "rawtherapee" | "gimp">("darktable");
  const [opening, setOpening] = useState(false);
  const [openResult, setOpenResult] = useState<string | null>(null);

  const decoded = decodeURIComponent(tag);

  useEffect(() => {
    setLoading(true);
    listClusterPhotos(decoded, source, 500)
      .then(r => {
        setPhotos(r.items);
        setLoading(false);
      })
      .catch(e => {
        setError(String(e));
        setLoading(false);
      });
  }, [decoded, source]);

  function toggle(sha: string) {
    const next = new Set(selected);
    if (next.has(sha)) next.delete(sha);
    else next.add(sha);
    setSelected(next);
  }

  function selectAll() {
    setSelected(new Set(photos.map(p => p.sha256)));
  }

  function clearSelection() {
    setSelected(new Set());
  }

  async function openSelected() {
    if (selected.size === 0) return;
    setOpening(true);
    setOpenResult(null);
    try {
      const r = await openInEditor(Array.from(selected), editor);
      setOpenResult(`Opened ${r.opened} photos in ${r.editor}`);
    } catch (e) {
      setOpenResult(String(e));
    } finally {
      setOpening(false);
    }
  }

  return (
    <div className="app">
      <Rail />
      <div className="workspace">
        <Topbar folder="travelcull" context={`cluster · ${decoded}`} />
        <StatusRow
          pos={`${photos.length} photos`}
          keepersCount={selected.size}
          details={`source: ${source} · ${selected.size} selected`}
        />

        <div className="cluster-detail-wrap">
          <div className="cluster-detail-toolbar">
            <Link to="/clusters" className="btn btn-text" style={{ paddingLeft: 8 }}>
              ← All clusters
            </Link>
            <h1 style={{ margin: 0, fontFamily: "var(--font-display)", fontWeight: 500, fontSize: 28 }}>
              {decoded}
            </h1>

            <div style={{ flex: 1 }} />

            <button className="btn btn-text" onClick={selectAll} disabled={photos.length === 0}>
              Select all
            </button>
            <button className="btn btn-text" onClick={clearSelection} disabled={selected.size === 0}>
              Clear
            </button>
            <select
              value={editor}
              onChange={e => setEditor(e.target.value as typeof editor)}
              className="editor-picker"
              aria-label="Editor"
            >
              <option value="darktable">darktable</option>
              <option value="rawtherapee">RawTherapee</option>
              <option value="gimp">GIMP</option>
            </select>
            <button
              className="btn btn-filled"
              onClick={openSelected}
              disabled={selected.size === 0 || opening}
            >
              {opening ? "Opening…" : `Open ${selected.size} in editor`}
            </button>
          </div>

          {openResult && (
            <div className="cluster-detail-toast">{openResult}</div>
          )}

          {loading && <div className="cluster-detail-empty">Loading…</div>}
          {error && <div className="cluster-detail-empty error">{error}</div>}
          {!loading && !error && photos.length === 0 && (
            <div className="cluster-detail-empty">No photos in this cluster.</div>
          )}

          <div className="cluster-detail-grid">
            {photos.map(p => {
              const sel = selected.has(p.sha256);
              return (
                <button
                  key={p.sha256}
                  className={`cluster-photo${sel ? " is-selected" : ""}`}
                  onClick={() => toggle(p.sha256)}
                  aria-pressed={sel}
                >
                  <img src={p.thumb_url} alt="" loading="lazy" />
                  {sel && <span className="cluster-photo-check">✓</span>}
                </button>
              );
            })}
          </div>
        </div>

        <KbdFooter />
      </div>
    </div>
  );
}
