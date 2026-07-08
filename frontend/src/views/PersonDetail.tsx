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
                value={labelDraft}
                onChange={(e) => setLabelDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") saveLabel();
                  if (e.key === "Escape") setEditingLabel(false);
                }}
                onBlur={saveLabel}
                placeholder={`P${id} (clear to unset)`}
                style={{
                  fontFamily: "var(--font-display)",
                  fontSize: 26,
                  fontWeight: 500,
                  background: "var(--md-surface-c-low)",
                  border: "1px solid var(--md-outline-var)",
                  borderRadius: 8,
                  padding: "4px 10px",
                  color: "var(--md-on-surface)",
                  outline: "none",
                  minWidth: 220,
                }}
              />
            ) : (
              <h1
                onClick={() => {
                  setLabelDraft(label || "");
                  setEditingLabel(true);
                }}
                title="Click to rename"
                style={{
                  margin: 0,
                  fontFamily: "var(--font-display)",
                  fontWeight: 500,
                  fontSize: 28,
                  cursor: "text",
                  borderBottom: "1px dashed transparent",
                }}
                onMouseEnter={(e) => (e.currentTarget.style.borderBottomColor = "var(--md-outline-var)")}
                onMouseLeave={(e) => (e.currentTarget.style.borderBottomColor = "transparent")}
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
              <div key={p.sha256} className={`cluster-photo${selected.has(p.sha256) ? " is-selected" : ""}`} style={{ position: "relative" }}>
                <button
                  onClick={() => toggle(p.sha256)}
                  style={{ display: "block", width: "100%", height: "100%", padding: 0, border: 0, background: "transparent", cursor: "pointer" }}
                  aria-pressed={selected.has(p.sha256)}
                >
                  <img src={p.thumb_url} alt="" loading="lazy" />
                </button>
                {selected.has(p.sha256) && <span className="cluster-photo-check">✓</span>}
                <button
                  onClick={() => setLightbox(i)}
                  title="Enlarge"
                  style={{
                    position: "absolute", top: 6, right: 6, width: 24, height: 24,
                    borderRadius: "50%", background: "rgba(0,0,0,0.55)", color: "#fff",
                    border: 0, cursor: "zoom-in", fontSize: 14,
                    display: "grid", placeItems: "center",
                  }}
                >
                  ↗
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
