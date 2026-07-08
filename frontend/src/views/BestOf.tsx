import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, Link } from "react-router-dom";
import Rail from "../components/Rail";
import StackPhoto from "../components/StackPhoto";
import Topbar from "../components/Topbar";
import Viewer from "../components/Viewer";
import PhotoEditor from "../editor/PhotoEditor";
import type { CuratedPhoto } from "../api/types";

type CurateResp = {
  facet: string;
  value: string;
  total: number;
  photos: CuratedPhoto[];
};

const FACET_LABELS: Record<string, string> = {
  day: "Day",
  place: "Place",
  person: "Person",
  category: "Category",
};

export default function BestOf() {
  const { facet, value } = useParams<{ facet: string; value: string }>();
  const [data, setData] = useState<CurateResp | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [lightboxIdx, setLightboxIdx] = useState<number | null>(null);
  const [editShas, setEditShas] = useState<string[] | null>(null);
  const [personLabel, setPersonLabel] = useState<string | null>(null);
  const [focusedPhotoId, setFocusedPhotoId] = useState<number | null>(null);

  useEffect(() => {
    if (!facet || !value) return;
    setLoading(true);
    setErr(null);
    fetch(`/api/curate?facet=${encodeURIComponent(facet)}&value=${encodeURIComponent(value)}`)
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((j: CurateResp) => {
        setData(j);
        setLoading(false);
      })
      .catch((e) => {
        setErr(String(e));
        setLoading(false);
      });
  }, [facet, value]);

  // Look up person label if facet=person
  useEffect(() => {
    if (facet !== "person" || !value) return;
    fetch(`/api/persons`)
      .then((r) => r.json())
      .then((j) => {
        const p = (j.persons || []).find((x: { id: number }) => String(x.id) === value);
        if (p) setPersonLabel(p.label || `P${p.id}`);
      })
      .catch(() => undefined);
  }, [facet, value]);

  const title = useMemo(() => {
    if (!facet || !value) return "Best of";
    if (facet === "person") return `Best of ${personLabel || `P${value}`}`;
    if (facet === "category") return `Best ${value}s`;
    return `Best of ${value}`;
  }, [facet, value, personLabel]);

  const toggle = useCallback((sha: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(sha)) next.delete(sha);
      else next.add(sha);
      return next;
    });
  }, []);

  const selectAll = () =>
    setSelected(new Set((data?.photos ?? []).map((p) => p.sha256)));
  const clearSelection = () => setSelected(new Set());

  const openInEditor = useCallback(() => {
    if (selected.size === 0) return;
    setEditShas(Array.from(selected));
  }, [selected]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (lightboxIdx !== null) {
        if (e.key === "Escape") setLightboxIdx(null);
        else if (e.key === "ArrowRight" || e.key === " ") {
          e.preventDefault();
          setLightboxIdx((i) =>
            i === null || !data ? null : Math.min(data.photos.length - 1, i + 1),
          );
        } else if (e.key === "ArrowLeft") {
          e.preventDefault();
          setLightboxIdx((i) => (i === null ? null : Math.max(0, i - 1)));
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [lightboxIdx, data]);

  return (
    <div className="app">
      <Rail />
      <div
        className="workspace"
        style={{
          display: "grid",
          gridTemplateRows: "auto auto 1fr",
          minHeight: 0,
          maxHeight: "100vh",
          overflow: "hidden",
        }}
      >
        <Topbar folder="selects" context={`best of: ${facet}=${value}`} />

        <div
          style={{
            padding: "10px 24px",
            display: "flex",
            alignItems: "center",
            gap: 12,
            borderBottom: "1px solid var(--md-outline-var)",
          }}
        >
          <div>
            <h1
              style={{
                margin: 0,
                fontFamily: "var(--font-display)",
                fontSize: 22,
                fontWeight: 500,
              }}
            >
              {title}
            </h1>
            <div style={{ color: "var(--md-on-surface-var)", fontSize: 12 }}>
              {data ? `${data.total} curated photos` : loading ? "Loading…" : ""}
              {" · "}
              <span style={{ fontFamily: "var(--font-mono)" }}>
                {FACET_LABELS[facet || ""] ?? facet}: {value}
              </span>
            </div>
          </div>
          <div style={{ flex: 1 }} />
          <button
            className="btn btn-text"
            onClick={selectAll}
            disabled={!data || data.photos.length === 0}
          >
            Select all
          </button>
          <button
            className="btn btn-text"
            onClick={clearSelection}
            disabled={selected.size === 0}
          >
            Clear
          </button>
          <button
            className="btn btn-tonal"
            onClick={openInEditor}
            disabled={selected.size === 0}
            title="Edit the selected photos in the built-in editor"
          >
            {`Edit ${selected.size || ""}`}
          </button>
          <Link to="/cull/stories" className="btn btn-text">
            ← Stories
          </Link>
        </div>

        <div style={{ padding: "12px 24px 16px", overflow: "auto", minHeight: 0 }}>
          {err && (
            <div
              style={{
                padding: 12,
                color: "var(--g-red)",
                background: "color-mix(in srgb, var(--g-red) 12%, transparent)",
                borderRadius: 8,
                marginBottom: 12,
                fontSize: 13,
              }}
            >
              {err}
            </div>
          )}
          {loading ? (
            <div style={{ color: "var(--md-on-surface-var)", padding: 24 }}>Loading…</div>
          ) : data && data.photos.length > 0 ? (
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
                gap: 8,
              }}
            >
              {data.photos.map((p, i) => {
                const isSel = selected.has(p.sha256);
                return (
                  <StackPhoto
                    key={p.photo_id}
                    sha256={p.sha256}
                    thumbUrl={p.thumb_url}
                    momentId={p.moment_id ?? null}
                    momentSize={p.moment_size ?? null}
                    isFocused={focusedPhotoId === p.photo_id}
                    onFocus={() => setFocusedPhotoId(p.photo_id)}
                    onClick={(activeSha) => toggle(activeSha)}
                    onDoubleClick={() => setLightboxIdx(i)}
                    title={`AP ${(p.ap25 ?? 0).toFixed(2)} · NIMA ${(p.nima ?? 0).toFixed(2)}${
                      p.moment_size && p.moment_size > 1 ? ` · burst of ${p.moment_size}` : ""
                    }`}
                    className={isSel ? "is-selected" : undefined}
                    style={{
                      border: isSel
                        ? "3px solid var(--md-primary)"
                        : "1px solid var(--md-outline-var)",
                      borderRadius: 10,
                      overflow: "hidden",
                      background: "var(--md-surface-c-low)",
                      cursor: "pointer",
                      transition: "transform 90ms ease",
                      transform: isSel ? "scale(0.98)" : "scale(1)",
                      aspectRatio: "4/3",
                    }}
                  >
                    <span
                      style={{
                        position: "absolute",
                        top: 6,
                        left: 6,
                        background: "rgba(0,0,0,0.75)",
                        color: "#fff",
                        fontSize: 10,
                        padding: "2px 6px",
                        borderRadius: 4,
                        fontFamily: "var(--font-mono)",
                      }}
                    >
                      #{i + 1}
                    </span>
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
                      {(p.combined ?? 0).toFixed(2)}
                    </span>
                  </StackPhoto>
                );
              })}
            </div>
          ) : (
            <div style={{ color: "var(--md-on-surface-var)", padding: 24 }}>
              No photos meet the curation gate in this scope.
            </div>
          )}
        </div>
      </div>

      {lightboxIdx !== null && data && data.photos[lightboxIdx] && (
        <Viewer
          items={data.photos.map((p) => ({ sha256: p.sha256 }))}
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
