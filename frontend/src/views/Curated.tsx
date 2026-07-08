import { useCallback, useEffect, useState } from "react";

import { listCurated, recordSwipe } from "../api/client";
import type { CuratedPhoto } from "../api/types";
import ExportPanel from "../components/ExportPanel";
import PageHeader from "../components/PageHeader";
import Rail from "../components/Rail";
import TasteCard from "../components/TasteCard";
import Viewer from "../components/Viewer";
import PhotoEditor from "../editor/PhotoEditor";

type SortMode = "aesthetic" | "taken_at";

export default function Curated() {
  const [photos, setPhotos] = useState<CuratedPhoto[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [sortMode, setSortMode] = useState<SortMode>("aesthetic");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [lightboxIdx, setLightboxIdx] = useState<number | null>(null);
  const [editShas, setEditShas] = useState<string[] | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [focusedIdx, setFocusedIdx] = useState<number | null>(null);
  const [exportOpen, setExportOpen] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listCurated(sortMode);
      setPhotos(data.photos);
      setErr(null);
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }, [sortMode]);

  useEffect(() => {
    reload();
  }, [reload]);

  const unlike = useCallback(async (sha: string) => {
    try {
      await recordSwipe(sha, "skip");
      setPhotos((prev) => prev.filter((p) => p.sha256 !== sha));
      setSelected((prev) => {
        const next = new Set(prev);
        next.delete(sha);
        return next;
      });
    } catch (e) {
      setToast(`Unlike failed: ${e}`);
    }
  }, []);

  const toggleSelected = (sha: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(sha)) next.delete(sha);
      else next.add(sha);
      return next;
    });
  };

  const openInEditor = useCallback(() => {
    if (selected.size === 0) return;
    setEditShas(Array.from(selected));
  }, [selected]);

  // Keyboard
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (lightboxIdx !== null) {
        if (e.key === "Escape") setLightboxIdx(null);
        else if (e.key === "ArrowRight" || e.key === " ") {
          e.preventDefault();
          setLightboxIdx((i) => (i === null ? null : Math.min(photos.length - 1, i + 1)));
        } else if (e.key === "ArrowLeft") {
          e.preventDefault();
          setLightboxIdx((i) => (i === null ? null : Math.max(0, i - 1)));
        } else if (e.key === "f" || e.key === "F") {
          const p = photos[lightboxIdx];
          if (p) unlike(p.sha256);
        }
        return;
      }
      if ((e.key === "f" || e.key === "F") && focusedIdx !== null) {
        const p = photos[focusedIdx];
        if (p) unlike(p.sha256);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [lightboxIdx, photos, focusedIdx, unlike]);

  return (
    <div className="app">
      <Rail />
      <div
        className="workspace"
        style={{
          display: "grid",
          gridTemplateRows: "auto 1fr",
          minHeight: 0,
          maxHeight: "100vh",
          overflow: "hidden",
        }}
      >
        <PageHeader
          context="curated"
          title="Curated"
          subtitle={
            loading
              ? "Loading…"
              : `${photos.length} liked photos · ready to edit & post · press F on a thumb to remove`
          }
          actions={
            <>
              <div className="curated-sort-toggle">
                <button
                  className={`btn ${sortMode === "aesthetic" ? "btn-filled" : "btn-text"}`}
                  onClick={() => setSortMode("aesthetic")}
                >
                  Best ★
                </button>
                <button
                  className={`btn ${sortMode === "taken_at" ? "btn-filled" : "btn-text"}`}
                  onClick={() => setSortMode("taken_at")}
                >
                  Time
                </button>
              </div>
              <button
                className="btn btn-text"
                onClick={() => setSelected(new Set(photos.map((p) => p.sha256)))}
                disabled={photos.length === 0}
              >
                Select all
              </button>
              <button
                className="btn btn-text"
                onClick={() => setSelected(new Set())}
                disabled={selected.size === 0}
              >
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
              <button
                className="btn btn-tonal"
                onClick={() => setExportOpen(true)}
                disabled={photos.length === 0}
                title="Export keepers — copy/zip originals or write XMP ratings back"
              >
                Export…
              </button>
            </>
          }
        />

        <div style={{ padding: "12px 24px 16px", overflow: "auto", minHeight: 0 }}>
          <div style={{ marginBottom: 12 }}>
            <TasteCard />
          </div>
          {err && <div className="view-banner view-banner-error">{err}</div>}
          {toast && (
            <div className="view-banner view-banner-info" style={{ fontSize: 12 }}>
              {toast}
            </div>
          )}
          {!loading && photos.length === 0 && (
            <div className="cluster-detail-empty">
              Nothing curated yet. Open Stories or a Best-Of view and press{" "}
              <kbd className="curated-kbd">F</kbd> on the photos you want to ship.
            </div>
          )}
          {!loading && photos.length > 0 && (
            <div className="curated-grid">
              {photos.map((p, i) => {
                const isSel = selected.has(p.sha256);
                const isFocused = focusedIdx === i;
                return (
                  <div
                    key={p.photo_id}
                    onMouseEnter={() => setFocusedIdx(i)}
                    onClick={() => toggleSelected(p.sha256)}
                    onDoubleClick={() => setLightboxIdx(i)}
                    className={`curated-tile${isSel ? " is-selected" : ""}${isFocused ? " is-focused" : ""}`}
                  >
                    <img src={p.thumb_url} alt="" loading="lazy" />
                    <button
                      className="curated-tile-unlike"
                      onClick={(e) => {
                        e.stopPropagation();
                        unlike(p.sha256);
                      }}
                      title="Unlike (F)"
                    >
                      <svg
                        viewBox="0 0 24 24"
                        width="14"
                        height="14"
                        fill="currentColor"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      >
                        <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z" />
                      </svg>
                    </button>
                    {p.combined != null && (
                      <span className="curated-tile-score">{p.combined.toFixed(2)}</span>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {exportOpen && (
        <ExportPanel source="curated" onClose={() => setExportOpen(false)} />
      )}

      {lightboxIdx !== null && photos[lightboxIdx] && (
        <Viewer
          items={photos.map((p) => ({ sha256: p.sha256 }))}
          index={lightboxIdx}
          onIndex={setLightboxIdx}
          onClose={() => setLightboxIdx(null)}
          renderActions={(it) => (
            <button
              className="btn btn-filled"
              onClick={() => {
                setLightboxIdx(null);
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
