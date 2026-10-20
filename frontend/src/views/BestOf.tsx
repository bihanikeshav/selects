import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, Link } from "react-router-dom";
import PageHeader from "../components/PageHeader";
import Rail from "../components/Rail";
import SkeletonGrid from "../components/SkeletonGrid";
import StackPhoto from "../components/StackPhoto";
import Viewer from "../components/Viewer";
import PhotoEditor from "../editor/PhotoEditor";
import type { CuratedPhoto } from "../api/types";
import { stripPlaceSuffix } from "../lib/placeName";
import "./BestOf.css";

type CurateResp = {
  facet: string;
  value: string;
  total: number;
  photos: CuratedPhoto[];
  available?: number;
};

const FACET_LABELS: Record<string, string> = {
  day: "Day",
  place: "Place",
  person: "Person",
  category: "Category",
  library: "Library",
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
  const [budget, setBudget] = useState(facet === "library" ? "40" : "all");
  const [diverse, setDiverse] = useState(true);
  const [activeShas, setActiveShas] = useState<Record<number, string>>({});

  useEffect(() => {
    if (!facet || !value) return;
    setLoading(true);
    setErr(null);
    setData(null);
    setSelected(new Set());
    setLightboxIdx(null);
    setFocusedPhotoId(null);
    setActiveShas({});
    const controller = new AbortController();
    const params = new URLSearchParams({ facet, value, limit: "1000" });
    if (budget !== "all") {
      params.set("budget", budget);
      params.set("diverse", String(diverse));
    }
    fetch(`/api/curate?${params}`, { signal: controller.signal })
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((j: CurateResp) => {
        if (controller.signal.aborted) return;
        setData(j);
        setLoading(false);
      })
      .catch((e) => {
        if (controller.signal.aborted) return;
        setErr(String(e));
        setLoading(false);
      });
    return () => controller.abort();
  }, [facet, value, budget, diverse]);

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
    if (facet === "library") return "Library highlights";
    if (facet === "person") return `Best of ${personLabel || `P${value}`}`;
    if (facet === "category") return `Best ${value}s`;
    return `Best of ${stripPlaceSuffix(value)}`;
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
    setSelected(new Set((data?.photos ?? []).map((p) => activeShas[p.photo_id] ?? p.sha256)));
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
    <div className="app bestof-page">
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
          context={title}
          title={title}
          subtitle={
            <>
              {data ? `${data.total} curated photos` : loading ? "Loading…" : ""}
              {" · "}
              <span style={{ fontFamily: "var(--font-mono)" }}>
                {facet === "library" ? "Whole library" : `${FACET_LABELS[facet || ""] ?? facet}: ${stripPlaceSuffix(value ?? "")}`}
              </span>
            </>
          }
          actions={
            <>
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
            </>
          }
        />

        <div style={{ padding: "12px 24px 16px", overflow: "auto", minHeight: 0 }}>
          <div className="highlights-controls">
            <label>
              Highlights
              <select aria-label="Highlights" value={budget} onChange={(e) => setBudget(e.target.value)}>
                <option value="all">All qualifying photos</option>
                <option value="12">Up to 12</option>
                <option value="40">Up to 40</option>
                <option value="80">Up to 80</option>
              </select>
            </label>
            <label>
              <input type="checkbox" checked={diverse} disabled={budget === "all"}
                onChange={(e) => setDiverse(e.target.checked)} />
              Balance scenes, places and people
            </label>
            <span role="status">
              {loading ? "Finding highlights…" : budget !== "all" && data
                ? `${data.total} of ${data.available ?? data.total} qualifying photos` : ""}
            </span>
          </div>
          <p className="highlights-note">Suggestions leave your photos and keeper decisions unchanged.</p>
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
            <SkeletonGrid count={12} />
          ) : data && data.photos.length > 0 ? (
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
                gap: 8,
              }}
            >
              {data.photos.map((p, i) => {
                const activeSha = activeShas[p.photo_id] ?? p.sha256;
                const isAlternative = activeSha !== p.sha256;
                const isSel = selected.has(activeSha);
                return (
                  <div key={p.photo_id}>
                    <StackPhoto
                      sha256={p.sha256}
                      thumbUrl={p.thumb_url}
                      momentId={p.moment_id ?? null}
                      momentSize={p.moment_size ?? null}
                      isFocused={focusedPhotoId === p.photo_id}
                      onFocus={() => setFocusedPhotoId(p.photo_id)}
                      onClick={(activeSha) => toggle(activeSha)}
                      onDoubleClick={() => setLightboxIdx(i)}
                      hotkeysEnabled={lightboxIdx === null && editShas === null}
                      onActiveChange={(sha) => setActiveShas((prev) => ({ ...prev, [p.photo_id]: sha }))}
                      title={isAlternative ? "Burst alternative" : `${p.selection_reasons?.length ? p.selection_reasons.join(" · ") + " · " : ""}Quality ${
                        (p.combined ?? p.iqa) != null
                          ? (p.combined ?? p.iqa)!.toFixed(2)
                          : "—"
                      }${
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
                        {isAlternative ? "Alternative" : (p.combined ?? p.iqa) != null
                          ? (p.combined ?? p.iqa)!.toFixed(2)
                          : "—"}
                      </span>
                    </StackPhoto>
                    {!isAlternative && !!p.selection_reasons?.length && (
                      <details className="highlight-reasons">
                        <summary>Why this photo?</summary>
                        <ul>{p.selection_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
                      </details>
                    )}
                  </div>
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
          items={data.photos.map((p) => ({ sha256: activeShas[p.photo_id] ?? p.sha256 }))}
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
