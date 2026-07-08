import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import KbdFooter from "../components/KbdFooter";
import PageHeader from "../components/PageHeader";
import Rail from "../components/Rail";

interface PersonEntry {
  id: number;
  label: string | null;
  photo_count: number;
  cover_url: string;
}

async function mergePeople(targetId: number, sourceIds: number[]) {
  const res = await fetch("/api/persons/merge", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target_id: targetId, source_ids: sourceIds }),
  });
  if (!res.ok) throw new Error(`merge failed: HTTP ${res.status}`);
}

export default function Persons() {
  const [persons, setPersons] = useState<PersonEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [mergeMode, setMergeMode] = useState(false);
  const [mergeTarget, setMergeTarget] = useState<number | null>(null);
  const [mergeSources, setMergeSources] = useState<Set<number>>(() => new Set());
  const [mergeBusy, setMergeBusy] = useState(false);

  const loadPersons = useCallback(() => {
    setLoading(true);
    fetch("/api/persons")
      .then(r => r.json())
      .then(d => {
        setPersons(d.persons);
        setErr(null);
        setLoading(false);
      })
      .catch(e => {
        setErr(String(e));
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    loadPersons();
  }, [loadPersons]);

  async function commitLabel(id: number) {
    const label = draft.trim() || null;
    try {
      const res = await fetch(`/api/persons/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label }),
      });
      if (res.ok) {
        setPersons(prev => prev.map(p => p.id === id ? { ...p, label } : p));
      } else {
        setErr(`rename failed: HTTP ${res.status}`);
      }
    } catch (e) {
      setErr(String(e));
    } finally {
      setEditing(null);
      setDraft("");
    }
  }

  const targetPerson = useMemo(
    () => persons.find(p => p.id === mergeTarget) ?? null,
    [persons, mergeTarget]
  );

  const sourceIds = useMemo(
    () => Array.from(mergeSources).filter(id => id !== mergeTarget),
    [mergeSources, mergeTarget]
  );

  function resetMerge() {
    setMergeMode(false);
    setMergeTarget(null);
    setMergeSources(new Set());
  }

  function onMergeCardClick(id: number) {
    if (!mergeMode) return;
    if (mergeTarget === null) {
      setMergeTarget(id);
      return;
    }
    if (id === mergeTarget) {
      setMergeTarget(null);
      return;
    }
    setMergeSources(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function onDropPerson(sourceId: number, targetId: number) {
    if (!mergeMode || sourceId === targetId) return;
    setMergeTarget(targetId);
    setMergeSources(prev => new Set(prev).add(sourceId));
  }

  async function commitMerge() {
    if (mergeTarget === null || sourceIds.length === 0) return;
    setMergeBusy(true);
    setErr(null);
    try {
      await mergePeople(mergeTarget, sourceIds);
      resetMerge();
      loadPersons();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setMergeBusy(false);
    }
  }

  const mergeSummary = mergeMode
    ? mergeTarget === null
      ? "Choose the person to keep, then click or drag other identities into it"
      : `${sourceIds.length} selected to merge into ${targetPerson?.label || `P${mergeTarget}`}`
    : `${persons.filter(p => p.label).length} of ${persons.length} named - click a name to label`;

  return (
    <div className="app">
      <Rail />
      <div
        className="workspace"
        style={{
          display: "grid",
          gridTemplateRows: "auto 1fr auto",
          height: "100vh",
          maxHeight: "100vh",
          overflow: "hidden",
        }}
      >
        <PageHeader
          context="persons"
          title="People"
          subtitle={loading ? "loading..." : err ?? mergeSummary}
          actions={
            mergeMode ? (
              <>
                <button className="btn btn-text" type="button" onClick={resetMerge} disabled={mergeBusy}>
                  Cancel
                </button>
                <button
                  className="btn btn-filled"
                  type="button"
                  onClick={commitMerge}
                  disabled={mergeBusy || mergeTarget === null || sourceIds.length === 0}
                >
                  {mergeBusy ? "Merging..." : `Merge ${sourceIds.length || ""}`}
                </button>
              </>
            ) : (
              <button className="btn btn-filled" type="button" onClick={() => setMergeMode(true)}>
                Merge people
              </button>
            )
          }
        />

        <div className="cluster-detail-wrap" style={{ gridRow: "2", minHeight: 0, overflowY: "auto" }}>
          <div
            className="cluster-detail-grid"
            style={{ gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))" }}
          >
            {persons.map(p => {
              const isEditing = editing === p.id;
              const displayName = p.label || `P${p.id}`;
              const isTarget = mergeTarget === p.id;
              const isSource = sourceIds.includes(p.id);
              return (
                <div
                  key={p.id}
                  className={`cluster-photo person-card${mergeMode ? " is-merge-mode" : ""}${isTarget ? " is-merge-target" : ""}${isSource ? " is-merge-source" : ""}`}
                  style={{ position: "relative", aspectRatio: "1/1" }}
                  draggable={mergeMode}
                  onDragStart={e => e.dataTransfer.setData("text/person-id", String(p.id))}
                  onDragOver={e => {
                    if (mergeMode) e.preventDefault();
                  }}
                  onDrop={e => {
                    e.preventDefault();
                    const sourceId = Number(e.dataTransfer.getData("text/person-id"));
                    if (Number.isFinite(sourceId)) onDropPerson(sourceId, p.id);
                  }}
                  onClick={e => {
                    if (mergeMode) {
                      e.preventDefault();
                      onMergeCardClick(p.id);
                    }
                  }}
                >
                  <Link
                    to={`/people/${p.id}`}
                    style={{ display: "block", width: "100%", height: "100%" }}
                    onClick={e => { if (isEditing || mergeMode) e.preventDefault(); }}
                  >
                    <img src={p.cover_url} alt={displayName} loading="lazy" />
                  </Link>

                  {mergeMode && (
                    <span className="person-merge-badge">
                      {isTarget ? "Target" : isSource ? "Merge" : "Pick"}
                    </span>
                  )}

                  <div
                    style={{
                      position: "absolute",
                      inset: "auto 0 0 0",
                      background: "linear-gradient(180deg, transparent, rgba(0,0,0,0.7))",
                      padding: "20px 10px 8px",
                      color: "#fff",
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "flex-end",
                      gap: 8,
                    }}
                  >
                    {isEditing ? (
                      <input
                        autoFocus
                        type="text"
                        value={draft}
                        onChange={e => setDraft(e.target.value)}
                        onBlur={() => commitLabel(p.id)}
                        onKeyDown={e => {
                          if (e.key === "Enter") commitLabel(p.id);
                          if (e.key === "Escape") { setEditing(null); setDraft(""); }
                        }}
                        placeholder={`P${p.id}`}
                        style={{
                          flex: 1,
                          background: "rgba(255,255,255,0.95)",
                          color: "#1B1B1F",
                          border: 0,
                          padding: "4px 8px",
                          borderRadius: 4,
                          fontFamily: "var(--font-body)",
                          fontSize: 13,
                          outline: "none",
                        }}
                      />
                    ) : (
                      <button
                        onClick={e => {
                          e.preventDefault();
                          e.stopPropagation();
                          if (mergeMode) {
                            onMergeCardClick(p.id);
                            return;
                          }
                          setEditing(p.id);
                          setDraft(p.label ?? "");
                        }}
                        style={{
                          background: "transparent",
                          border: 0,
                          color: "#fff",
                          fontFamily: "var(--font-display)",
                          fontSize: 14,
                          fontWeight: 500,
                          cursor: "pointer",
                          padding: 0,
                          textAlign: "left",
                          flex: 1,
                        }}
                        title={mergeMode ? "Select for merge" : "Click to name this person"}
                      >
                        {p.label || <em style={{ opacity: 0.7 }}>name...</em>}
                      </button>
                    )}
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, opacity: 0.85 }}>
                      {p.photo_count}p
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <KbdFooter />
      </div>
    </div>
  );
}
