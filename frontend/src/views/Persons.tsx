import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import KbdFooter from "../components/KbdFooter";
import Rail from "../components/Rail";
import StatusRow from "../components/StatusRow";
import Topbar from "../components/Topbar";

interface PersonEntry {
  id: number;
  label: string | null;
  photo_count: number;
  cover_url: string;
}

export default function Persons() {
  const [persons, setPersons] = useState<PersonEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState("");

  useEffect(() => {
    fetch("/api/persons")
      .then(r => r.json())
      .then(d => { setPersons(d.persons); setLoading(false); })
      .catch(e => { setErr(String(e)); setLoading(false); });
  }, []);

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

  return (
    <div className="app">
      <Rail />
      <div className="workspace">
        <Topbar folder="travelcull" context="persons" />
        <StatusRow
          pos={`${persons.length} identities`}
          keepersCount={persons.filter(p => p.label).length}
          details={loading ? "loading…" : err ?? "click a name to label"}
        />

        <div className="cluster-detail-wrap">
          <div className="cluster-detail-toolbar">
            <h1 style={{ margin: 0, fontFamily: "var(--font-display)", fontWeight: 500, fontSize: 28 }}>
              People
            </h1>
            <div style={{ flex: 1 }} />
            <span style={{ color: "var(--md-on-surface-var)", fontSize: 13 }}>
              {persons.filter(p => p.label).length} of {persons.length} named
            </span>
          </div>

          <div
            className="cluster-detail-grid"
            style={{ gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))" }}
          >
            {persons.map(p => {
              const isEditing = editing === p.id;
              const displayName = p.label || `P${p.id}`;
              return (
                <div key={p.id} className="cluster-photo" style={{ position: "relative", aspectRatio: "1/1" }}>
                  <Link
                    to={`/people/${p.id}`}
                    style={{ display: "block", width: "100%", height: "100%" }}
                    onClick={e => { if (isEditing) e.preventDefault(); }}
                  >
                    <img src={p.cover_url} alt={displayName} loading="lazy" />
                  </Link>
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
                        title="Click to name this person"
                      >
                        {p.label || <em style={{ opacity: 0.7 }}>name…</em>}
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
