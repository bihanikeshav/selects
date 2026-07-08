import { useCallback, useEffect, useState } from "react";

import { listCurated, recordSwipe } from "../api/client";
import type { CuratedPhoto } from "../api/types";
import ExportPanel from "../components/ExportPanel";
import ModeViewBar from "../components/ModeViewBar";
import PageHeader from "../components/PageHeader";
import Rail from "../components/Rail";
import TasteCard from "../components/TasteCard";

type SortMode = "aesthetic" | "taken_at";

export default function Curated() {
  const [photos, setPhotos] = useState<CuratedPhoto[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [sortMode, setSortMode] = useState<SortMode>("aesthetic");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [lightboxIdx, setLightboxIdx] = useState<number | null>(null);
  const [launching, setLaunching] = useState(false);
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

  const openInDarktable = useCallback(async () => {
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
        const j = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(j.detail || `darktable launch ${res.status}`);
      }
      const j = await res.json();
      setToast(`darktable opened with ${j.opened} photos`);
    } catch (e) {
      setToast(String(e));
    } finally {
      setLaunching(false);
    }
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
          above={<ModeViewBar />}
          actions={
            <>
              <div style={{ display: "flex", gap: 4 }}>
                <button
                  className={`btn ${sortMode === "aesthetic" ? "btn-filled" : "btn-text"}`}
                  onClick={() => setSortMode("aesthetic")}
                  style={{ fontSize: 12 }}
                >
                  Best ★
                </button>
                <button
                  className={`btn ${sortMode === "taken_at" ? "btn-filled" : "btn-text"}`}
                  onClick={() => setSortMode("taken_at")}
                  style={{ fontSize: 12 }}
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
                onClick={openInDarktable}
                disabled={selected.size === 0 || launching}
              >
                {launching
                  ? "Launching…"
                  : `Edit ${selected.size || ""} in darktable`}
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
          {toast && (
            <div
              style={{
                padding: "8px 12px",
                color: "var(--md-on-surface)",
                background: "var(--md-surface-c)",
                border: "1px solid var(--md-outline-var)",
                borderRadius: 8,
                marginBottom: 12,
                fontSize: 12,
              }}
            >
              {toast}
            </div>
          )}
          {!loading && photos.length === 0 && (
            <div
              style={{
                padding: 36,
                textAlign: "center",
                color: "var(--md-on-surface-var)",
                fontSize: 14,
              }}
            >
              Nothing curated yet. Open Stories or a Best-Of view and press{" "}
              <kbd
                style={{
                  background: "var(--md-surface-c)",
                  border: "1px solid var(--md-outline-var)",
                  borderRadius: 4,
                  padding: "1px 6px",
                  fontFamily: "var(--font-mono)",
                  fontSize: 12,
                }}
              >
                F
              </kbd>{" "}
              on the photos you want to ship.
            </div>
          )}
          {!loading && photos.length > 0 && (
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
                gap: 8,
              }}
            >
              {photos.map((p, i) => {
                const isSel = selected.has(p.sha256);
                const isFocused = focusedIdx === i;
                return (
                  <div
                    key={p.photo_id}
                    onMouseEnter={() => setFocusedIdx(i)}
                    onClick={() => toggleSelected(p.sha256)}
                    onDoubleClick={() => setLightboxIdx(i)}
                    style={{
                      position: "relative",
                      border: isSel
                        ? "3px solid var(--md-primary)"
                        : "1px solid var(--md-outline-var)",
                      outline: isFocused ? "2px solid var(--md-primary)" : "none",
                      outlineOffset: 1,
                      borderRadius: 10,
                      overflow: "hidden",
                      background: "var(--md-surface-c-low)",
                      cursor: "pointer",
                      aspectRatio: "4/3",
                    }}
                  >
                    <img
                      src={p.thumb_url}
                      alt=""
                      loading="lazy"
                      style={{
                        width: "100%",
                        height: "100%",
                        objectFit: "cover",
                        display: "block",
                      }}
                    />
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        unlike(p.sha256);
                      }}
                      title="Unlike (F)"
                      style={{
                        position: "absolute",
                        top: 6,
                        left: 6,
                        width: 26,
                        height: 26,
                        display: "grid",
                        placeItems: "center",
                        background: "var(--g-red)",
                        color: "#fff",
                        border: 0,
                        borderRadius: "50%",
                        cursor: "pointer",
                      }}
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
                      <span
                        style={{
                          position: "absolute",
                          top: 6,
                          right: 6,
                          background: "rgba(0,0,0,0.75)",
                          color: "#fff",
                          fontSize: 10,
                          padding: "2px 6px",
                          borderRadius: 4,
                          fontFamily: "var(--font-mono)",
                        }}
                      >
                        {p.combined.toFixed(2)}
                      </span>
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
        <div
          onClick={() => setLightboxIdx(null)}
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.94)",
            zIndex: 90,
            display: "grid",
            placeItems: "center",
            cursor: "zoom-out",
          }}
        >
          <img
            src={photos[lightboxIdx].preview_url}
            alt=""
            style={{ maxWidth: "94vw", maxHeight: "94vh" }}
          />
        </div>
      )}
    </div>
  );
}
