import { useEffect, useRef, useState } from "react";

import {
  activateLibrary,
  createLibrary,
  deleteLibrary,
  libraryStatus,
  listLibraries,
  startIndexing,
} from "../api/client";
import type { Library } from "../api/types";
import FolderPicker from "../components/FolderPicker";
import ModelsCard from "../components/ModelsCard";
import Rail from "../components/Rail";
import WatchCard from "../components/WatchCard";

function fmtDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export default function Libraries() {
  const [libs, setLibs] = useState<Library[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [listErr, setListErr] = useState<string | null>(null);

  const [name, setName] = useState("My Trip");
  const [path, setPath] = useState("");
  const [formErr, setFormErr] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [rowErr, setRowErr] = useState<string | null>(null);

  // `indexing` is only reported by the status endpoint, and applies to the
  // active library (the one being processed). `indexingId` is which library
  // that is, so we can badge/disable just that card.
  const [indexingId, setIndexingId] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  async function refresh() {
    try {
      const [data, status] = await Promise.all([listLibraries(), libraryStatus().catch(() => null)]);
      setLibs(data.libraries);
      setActiveId(data.active_id);
      setIndexingId(status && status.indexing && status.active ? status.active.id : null);
      setListErr(null);
      return data.libraries;
    } catch (e) {
      setListErr(e instanceof Error ? e.message : String(e));
      return null;
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

  // Poll every 3s while a library is indexing, so photo counts and the
  // indexing flag stay fresh without a manual reload.
  useEffect(() => {
    if (indexingId && pollRef.current === null) {
      pollRef.current = window.setInterval(refresh, 3000);
    } else if (!indexingId && pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, [indexingId]);

  async function onAdd(e: React.FormEvent) {
    e.preventDefault();
    setFormErr(null);
    const n = name.trim();
    const p = path.trim();
    if (!n || !p) {
      setFormErr("Please give the library a name and a folder path.");
      return;
    }
    setCreating(true);
    try {
      await createLibrary(n, p);
      setName("My Trip");
      setPath("");
      await refresh();
    } catch (e) {
      setFormErr(e instanceof Error ? e.message : String(e));
    } finally {
      setCreating(false);
    }
  }

  async function onSwitch(id: string) {
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
      <div className="workspace">
        <div className="lib-wrap">
          <header className="lib-header">
            <h1>Libraries</h1>
            <span className="lib-sub">
              {loading ? "loading…" : `${libs.length} ${libs.length === 1 ? "library" : "libraries"}`}
            </span>
          </header>

          {listErr && <p className="onb-error">{listErr}</p>}
          {rowErr && <p className="onb-error">{rowErr}</p>}

          <div className="lib-list">
            {libs.map((l) => {
              const indexing = l.id === indexingId;
              const isActive = l.id === activeId;
              const busy = busyId === l.id;
              return (
                <div key={l.id} className={"lib-card" + (isActive ? " is-active" : "")}>
                  <div className="lib-card-main">
                    <div className="lib-card-title">
                      <span className="lib-name">{l.name}</span>
                      {isActive && <span className="lib-badge">ACTIVE</span>}
                      {indexing && <span className="lib-badge lib-badge-busy">INDEXING…</span>}
                    </div>
                    <div className="lib-path">{l.path}</div>
                    <div className="lib-meta">
                      <span>{l.photo_count == null ? "—" : `${l.photo_count.toLocaleString()} photos`}</span>
                      <span className="lib-dot">·</span>
                      <span>{fmtDate(l.created_at)}</span>
                    </div>
                  </div>
                  <div className="lib-actions">
                    {!isActive && (
                      <button
                        className="btn btn-tonal"
                        onClick={() => onSwitch(l.id)}
                        disabled={busy}
                      >
                        Switch to
                      </button>
                    )}
                    <button
                      className="btn btn-outlined"
                      onClick={() => onReindex(l.id)}
                      disabled={busy || indexing}
                    >
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
              );
            })}
            {!loading && libs.length === 0 && !listErr && (
              <p className="lib-empty">No libraries yet — add one below.</p>
            )}
          </div>

          <form className="lib-add" onSubmit={onAdd}>
            <h2>Add library</h2>
            <div className="lib-add-row">
              <label className="onb-field">
                <span className="onb-label">Name</span>
                <input
                  className="onb-input"
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="My Trip"
                />
              </label>
              <div className="onb-field lib-add-path">
                <span className="onb-label">Folder path</span>
                <FolderPicker value={path} onChange={setPath} />
              </div>
              <button className="btn btn-filled lib-add-btn" type="submit" disabled={creating}>
                {creating ? "Adding…" : "Add"}
              </button>
            </div>
            {formErr && <p className="onb-error">{formErr}</p>}
          </form>

          <ModelsCard />

          <WatchCard />
        </div>
      </div>
    </div>
  );
}
