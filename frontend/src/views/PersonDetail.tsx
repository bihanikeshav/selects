import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";

import type { Photo } from "../api/types";
import KbdFooter from "../components/KbdFooter";
import Rail from "../components/Rail";
import StatusRow from "../components/StatusRow";
import Topbar from "../components/Topbar";

export default function PersonDetail() {
  const { id } = useParams();
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [label, setLabel] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [lightbox, setLightbox] = useState<string | null>(null);
  const [launching, setLaunching] = useState(false);
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

  async function openInDarktable() {
    if (selected.size === 0) return;
    setLaunching(true);
    try {
      const res = await fetch("/api/edit/darktable", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sha256s: Array.from(selected) }),
      });
      if (!res.ok) {
        const j = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(j.detail);
      }
      const j = await res.json();
      setToast(`Launched darktable with ${j.opened} photos`);
    } catch (e) {
      setToast(String(e));
    } finally {
      setLaunching(false);
    }
  }

  const displayName = label || `P${id}`;

  return (
    <div className="app">
      <Rail />
      <div className="workspace">
        <Topbar folder="travelcull" context={`person · ${displayName}`} />
        <StatusRow
          pos={`${photos.length} photos`}
          keepersCount={selected.size}
          details={loading ? "loading…" : err ?? `${selected.size} selected`}
        />

        <div className="cluster-detail-wrap">
          <div className="cluster-detail-toolbar">
            <Link to="/persons" className="btn btn-text" style={{ paddingLeft: 8 }}>← All persons</Link>
            <h1 style={{ margin: 0, fontFamily: "var(--font-display)", fontWeight: 500, fontSize: 28 }}>
              {displayName}
            </h1>
            <div style={{ flex: 1 }} />
            <button className="btn btn-text" onClick={() => setSelected(new Set(photos.map(p => p.sha256)))}>
              Select all
            </button>
            <button className="btn btn-text" onClick={() => setSelected(new Set())} disabled={selected.size === 0}>
              Clear
            </button>
            <button
              className="btn btn-filled"
              onClick={openInDarktable}
              disabled={selected.size === 0 || launching}
            >
              {launching ? "Launching…" : `Edit ${selected.size} in darktable`}
            </button>
          </div>

          {toast && <div className="cluster-detail-toast">{toast}</div>}

          <div className="cluster-detail-grid">
            {photos.map(p => (
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
                  onClick={() => setLightbox(p.sha256)}
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

      {lightbox && (
        <div onClick={() => setLightbox(null)} style={{
          position: "fixed", inset: 0, background: "rgba(0,0,0,0.92)", zIndex: 90,
          display: "grid", placeItems: "center", cursor: "zoom-out",
        }}>
          <img src={`/api/preview/${lightbox}`} alt="" style={{ maxWidth: "94vw", maxHeight: "94vh" }} />
        </div>
      )}
    </div>
  );
}
