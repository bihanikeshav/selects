import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";

import type { Photo } from "../api/types";
import KbdFooter from "../components/KbdFooter";
import Rail from "../components/Rail";
import StatusRow from "../components/StatusRow";
import Topbar from "../components/Topbar";
import Viewer from "../components/Viewer";
import PhotoEditor from "../editor/PhotoEditor";

export default function PersonDetail() {
  const { id } = useParams();
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [label, setLabel] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [lightbox, setLightbox] = useState<number | null>(null);
  const [editShas, setEditShas] = useState<string[] | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    fetch(`/api/persons/${id}/photos`)
      .then(r => r.json())
      .then(d => { setPhotos(d.items); setLoading(false); })
      .catch(e => { setErr(String(e)); setLoading(false); });
    fetch("/api/persons")
      .then(r => r.json())
      .then(d => {
        const me = d.persons.find((p: { id: number; label: string | null }) => String(p.id) === id);
        if (me) setLabel(me.label);
      });
  }, [id]);

  function toggle(sha: string) {
    const next = new Set(selected);
    if (next.has(sha)) next.delete(sha); else next.add(sha);
    setSelected(next);
  }

  function openInEditor() {
    if (selected.size === 0) return;
    setEditShas(Array.from(selected));
  }

  const displayName = label || `P${id}`;
  const [editingLabel, setEditingLabel] = useState(false);
  const [labelDraft, setLabelDraft] = useState("");

  async function saveLabel() {
    const newLabel = labelDraft.trim() || null;
    try {
      const res = await fetch(`/api/persons/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label: newLabel }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setLabel(newLabel);
      setEditingLabel(false);
      setToast(`Renamed to ${newLabel || `P${id}`}`);
    } catch (e) {
      setToast(`Rename failed: ${e}`);
    }
  }

  return (
    <div className="app">
      <Rail />
      <div className="workspace">
        <Topbar folder="selects" context={`person · ${displayName}`} />
        <StatusRow
          pos={`${photos.length} photos`}
          keepersCount={selected.size}
          details={loading ? "loading…" : err ?? `${selected.size} selected`}
        />

        <div className="cluster-detail-wrap">
          <div className="cluster-detail-toolbar">
            <Link to="/people" className="btn btn-text" style={{ paddingLeft: 8 }}>← All people</Link>
            {editingLabel ? (
              <input
                autoFocus
                className="person-detail-title-input"
                value={labelDraft}
                onChange={(e) => setLabelDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") saveLabel();
                  if (e.key === "Escape") setEditingLabel(false);
                }}
                onBlur={saveLabel}
                placeholder={`P${id} (clear to unset)`}
              />
            ) : (
              <h1
                className="person-detail-title"
                onClick={() => {
                  setLabelDraft(label || "");
                  setEditingLabel(true);
                }}
                title="Click to rename"
              >
                {displayName}
              </h1>
            )}
            <div style={{ flex: 1 }} />
            <Link
              to={`/best/person/${id}`}
              className="btn btn-tonal"
              title="Best photos of this person"
            >
              ★ Best of {displayName}
            </Link>
            <button className="btn btn-text" onClick={() => setSelected(new Set(photos.map(p => p.sha256)))}>
              Select all
            </button>
            <button className="btn btn-text" onClick={() => setSelected(new Set())} disabled={selected.size === 0}>
              Clear
            </button>
            <button
              className="btn btn-filled"
              onClick={openInEditor}
              disabled={selected.size === 0}
              title="Edit the selected photos in the built-in editor"
            >
              {`Edit ${selected.size || ""}`}
            </button>
          </div>

          {toast && <div className="cluster-detail-toast">{toast}</div>}

          <div className="cluster-detail-grid">
            {photos.map((p, i) => (
              <div
                key={p.sha256}
                className={`cluster-photo${selected.has(p.sha256) ? " is-selected" : ""}`}
                onDoubleClick={() => setLightbox(i)}
              >
                <button
                  onClick={() => toggle(p.sha256)}
                  className="cluster-photo-btn"
                  aria-pressed={selected.has(p.sha256)}
                  title="Click to select · double-click to view"
                >
                  <img src={p.thumb_url} alt="" loading="lazy" />
                </button>
                {selected.has(p.sha256) && <span className="cluster-photo-check">✓</span>}
                <button
                  onClick={() => setLightbox(i)}
                  title="View"
                  aria-label="View photo"
                  className="cluster-photo-zoom"
                >
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
                  </svg>
                </button>
              </div>
            ))}
          </div>
        </div>

        <KbdFooter />
      </div>

      {lightbox !== null && photos[lightbox] && (
        <Viewer
          items={photos.map((p) => ({ sha256: p.sha256 }))}
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
