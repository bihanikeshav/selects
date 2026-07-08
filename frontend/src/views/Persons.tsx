import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import PageHeader from "../components/PageHeader";
import Rail from "../components/Rail";

interface PersonEntry {
  id: number;
  label: string | null;
  photo_count: number;
  cover_url: string;
  hidden: boolean;
}

async function setPersonHidden(id: number, hidden: boolean) {
  const res = await fetch(`/api/persons/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ hidden }),
  });
  if (!res.ok) throw new Error(`hide failed: HTTP ${res.status}`);
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
  const [showHidden, setShowHidden] = useState(false);

  const loadPersons = useCallback(() => {
    setLoading(true);
    fetch(`/api/persons?include_hidden=${showHidden}`)
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
  }, [showHidden]);

  useEffect(() => {
    loadPersons();
  }, [loadPersons]);

  const toggleHidden = useCallback(async (id: number, hidden: boolean) => {
    try {
      await setPersonHidden(id, hidden);
      // Optimistic: when not showing hidden, a hidden person drops out of the list.
      setPersons(prev =>
        showHidden
          ? prev.map(p => (p.id === id ? { ...p, hidden } : p))
          : prev.filter(p => p.id !== id || !hidden),
      );
    } catch (e) {
      setErr(String(e));
    }
  }, [showHidden]);

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
              <>
                <button className="btn btn-text" type="button" onClick={() => setShowHidden(v => !v)}>
                  {showHidden ? "Hide hidden" : "Show hidden"}
                </button>
                <button className="btn btn-filled" type="button" onClick={() => setMergeMode(true)}>
                  Merge people
                </button>
              </>
            )
          }
        />

        <div className="cluster-detail-wrap cluster-detail-wrap--persons">
          <div className="cluster-detail-grid cluster-detail-grid--wide">
            {persons.map(p => {
              const isEditing = editing === p.id;
              const displayName = p.label || `P${p.id}`;
              const isTarget = mergeTarget === p.id;
              const isSource = sourceIds.includes(p.id);
              return (
                <div
                  key={p.id}
                  className={`cluster-photo person-card${mergeMode ? " is-merge-mode" : ""}${isTarget ? " is-merge-target" : ""}${isSource ? " is-merge-source" : ""}`}
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
                    className="cluster-card-link"
                    style={{ width: "100%", height: "100%" }}
                    onClick={e => { if (isEditing || mergeMode) e.preventDefault(); }}
                  >
                    <img src={p.cover_url} alt={displayName} loading="lazy" />
                  </Link>

                  {mergeMode && (
                    <span className="person-merge-badge">
                      {isTarget ? "Target" : isSource ? "Merge" : "Pick"}
                    </span>
                  )}

                  {!mergeMode && (
                    <button
                      type="button"
                      className="person-hide-btn"
                      title={p.hidden ? "Unhide this person" : "Hide this person"}
                      onClick={e => {
                        e.preventDefault();
                        e.stopPropagation();
                        toggleHidden(p.id, !p.hidden);
                      }}
                    >
                      {p.hidden ? "Unhide" : "Hide"}
                    </button>
                  )}

                  <div className="person-card-footer">
                    {isEditing ? (
                      <input
                        autoFocus
                        type="text"
                        className="person-name-input"
                        value={draft}
                        onChange={e => setDraft(e.target.value)}
                        onBlur={() => commitLabel(p.id)}
                        onKeyDown={e => {
                          if (e.key === "Enter") commitLabel(p.id);
                          if (e.key === "Escape") { setEditing(null); setDraft(""); }
                        }}
                        placeholder={`P${p.id}`}
                      />
                    ) : (
                      <button
                        className="person-name-btn"
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
                        title={mergeMode ? "Select for merge" : "Click to name this person"}
                      >
                        {p.label || <em>name...</em>}
                      </button>
                    )}
                    <span className="person-count-badge">
                      {p.photo_count}p
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
