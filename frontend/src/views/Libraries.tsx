import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  activateLibrary,
  deleteLibrary,
  libraryStatus,
  listLibraries,
  startIndexing,
} from "../api/client";
import type { Library } from "../api/types";
import ModelsCard from "../components/ModelsCard";
import PageHeader from "../components/PageHeader";
import Rail from "../components/Rail";
import WatchCard from "../components/WatchCard";

function fmtDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export default function Libraries() {
  const navigate = useNavigate();
  const [libs, setLibs] = useState<Library[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [listErr, setListErr] = useState<string | null>(null);
  const [rowErr, setRowErr] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [indexingId, setIndexingId] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  async function refresh() {
    try {
      const [data, status] = await Promise.all([listLibraries(), libraryStatus().catch(() => null)]);
      setLibs(data.libraries);
      setActiveId(data.active_id);
      setIndexingId(status && status.indexing && status.active ? status.active.id : null);
      setListErr(null);
    } catch (e) {
      setListErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    return () => {
      if (pollRef.current !== null) window.clearInterval(pollRef.current);
    };
  }, []);

  useEffect(() => {
    if (indexingId && pollRef.current === null) {
      pollRef.current = window.setInterval(refresh, 3000);
    } else if (!indexingId && pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, [indexingId]);

  async function onSwitch(id: string) {
    if (id === activeId) return;
    setRowErr(null);
    setBusyId(id);
    try {
      await activateLibrary(id);
      window.location.assign("/");
    } catch (e) {
      setRowErr(e instanceof Error ? e.message : String(e));
      setBusyId(null);
    }
  }

  async function onReindex(id: string) {
    setRowErr(null);
    setBusyId(id);
    try {
      await startIndexing(id);
      setIndexingId(id);
      await refresh();
    } catch (e) {
      setRowErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

  async function onRemove(id: string) {
    if (confirmId !== id) {
      setConfirmId(id);
      return;
    }
    setRowErr(null);
    setBusyId(id);
    try {
      await deleteLibrary(id);
      setConfirmId(null);
      await refresh();
    } catch (e) {
      setRowErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="app">
      <Rail />
      <div
        className="workspace"
        style={{ display: "grid", gridTemplateRows: "auto 1fr", height: "100vh", maxHeight: "100vh", overflow: "hidden" }}
      >
        <PageHeader
          context="libraries"
          title="Libraries"
          subtitle={loading ? "loading…" : `${libs.length} ${libs.length === 1 ? "library" : "libraries"} · double-click a cover to open`}
          actions={
            <button className="btn btn-filled" type="button" onClick={() => navigate("/onboarding")}>
              Add library
            </button>
          }
        />

        <div className="lib-wrap" style={{ overflowY: "auto" }}>
          {listErr && <p className="onb-error">{listErr}</p>}
          {rowErr && <p className="onb-error">{rowErr}</p>}

          <div className="lib-grid">
            {libs.map((l) => {
              const isActive = l.id === activeId;
              const indexing = l.id === indexingId;
              const busy = busyId === l.id;
              return (
                <div
                  key={l.id}
                  className={"lib-tile" + (isActive ? " is-active" : "")}
                  onDoubleClick={() => onSwitch(l.id)}
                  title={isActive ? "Active library" : "Double-click to open this library"}
                >
                  <div className="lib-tile-cover">
                    <img src={`/api/libraries/${l.id}/cover`} alt="" loading="lazy" />
                    <div className="lib-tile-badges">
                      {isActive && <span className="lib-badge">ACTIVE</span>}
                      {indexing && <span className="lib-badge lib-badge-busy">INDEXING…</span>}
                    </div>
                  </div>

                  <div className="lib-tile-body">
                    <div className="lib-tile-name">{l.name}</div>
                    <div className="lib-tile-meta">
                      {l.photo_count == null ? "—" : `${l.photo_count.toLocaleString()} photos`} · {fmtDate(l.created_at)}
                    </div>
                    <div className="lib-tile-path" title={l.path}>{l.path}</div>

                    <div className="lib-tile-actions">
                      {!isActive && (
                        <button className="btn btn-tonal" onClick={() => onSwitch(l.id)} disabled={busy}>
                          Open
                        </button>
                      )}
                      <button className="btn btn-outlined" onClick={() => onReindex(l.id)} disabled={busy || indexing}>
                        {indexing ? "Re-indexing…" : "Re-index"}
                      </button>
                      <button
                        className={"btn " + (confirmId === l.id ? "btn-filled lib-danger" : "btn-text")}
                        onClick={() => onRemove(l.id)}
                        onBlur={() => confirmId === l.id && setConfirmId(null)}
                        disabled={busy}
                      >
                        {confirmId === l.id ? "Really remove?" : "Remove"}
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}

            <button className="lib-tile lib-tile-add" type="button" onClick={() => navigate("/onboarding")}>
              <span className="lib-tile-add-plus">+</span>
              <span>Add library</span>
            </button>
          </div>

          {!loading && libs.length === 0 && !listErr && (
            <p className="lib-empty">No libraries yet — add one to get started.</p>
          )}

          <details className="lib-active-settings">
            <summary>Active library settings — models &amp; folder watching</summary>
            <div className="lib-active-settings-body">
              <ModelsCard />
              <WatchCard />
            </div>
          </details>
        </div>
      </div>
    </div>
  );
}
