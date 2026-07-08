import { useCallback, useEffect, useRef, useState } from "react";

import { dedupReport, dedupRescan } from "../api/dedup";
import type { DedupGroup, DedupPhotoRef, DedupReportResult } from "../api/dedup";
import PageHeader from "../components/PageHeader";
import Rail from "../components/Rail";
import Viewer from "../components/Viewer";
import "../components/Dedup.css";

function fmtBytes(n: number): string {
  if (n <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n;
  let u = 0;
  while (v >= 1024 && u < units.length - 1) {
    v /= 1024;
    u += 1;
  }
  return `${v.toFixed(u === 0 ? 0 : 1)} ${units[u]}`;
}

function shortPath(path: string): string {
  const norm = path.replace(/\\/g, "/");
  const parts = norm.split("/").filter(Boolean);
  if (parts.length <= 2) return norm;
  return `.../${parts.slice(-2).join("/")}`;
}

function GroupThumb({
  member,
  isKeeper,
  onKeep,
  onZoom,
}: {
  member: DedupPhotoRef;
  isKeeper: boolean;
  onKeep: () => void;
  onZoom: () => void;
}) {
  return (
    <div
      className={"dedup-thumb" + (isKeeper ? " is-keeper" : "")}
      onClick={onKeep}
      onDoubleClick={onZoom}
      title={isKeeper ? "Kept · double-click to zoom" : "Click to keep this one · double-click to zoom"}
      style={{ cursor: "pointer" }}
    >
      {isKeeper ? (
        <span className="dedup-keeper-badge">KEEP</span>
      ) : (
        <span className="dedup-keeper-badge dedup-keeper-badge-pick">Keep this</span>
      )}
      {member.thumb_url ? (
        <img src={member.thumb_url} alt="" loading="lazy" />
      ) : (
        <div className="dedup-thumb-fallback" title={member.path}>
          <span className="dedup-thumb-fallback-icon" aria-hidden="true">
            files
          </span>
          <span className="dedup-thumb-fallback-path">{shortPath(member.path)}</span>
        </div>
      )}
      <div className="dedup-thumb-meta">
        <span className="dedup-lib-name">{member.library_name}</span>
        <span className="dedup-size">{member.size_bytes != null ? fmtBytes(member.size_bytes) : "-"}</span>
      </div>
    </div>
  );
}

function GroupRow({
  group,
  kept,
  onToggleKeep,
  onZoom,
}: {
  group: DedupGroup;
  kept: Set<number>;
  onToggleKeep: (i: number) => void;
  onZoom: (i: number) => void;
}) {
  const keepCount = kept.size;
  return (
    <div className="dedup-group">
      <div className="dedup-group-header">
        <span className={"dedup-kind-badge dedup-kind-" + group.kind}>
          {group.kind === "exact" ? "Exact" : "Near"}
        </span>
        <span className="dedup-group-count">{group.members.length} copies</span>
        <span className="dedup-group-reclaim">
          keeping {keepCount} · reclaim {fmtBytes(group.reclaimable_bytes)}
        </span>
      </div>
      <div className="dedup-group-thumbs">
        {group.members.map((m, i) => (
          <GroupThumb
            key={`${m.library_id}:${m.path}`}
            member={m}
            isKeeper={kept.has(i)}
            onKeep={() => onToggleKeep(i)}
            onZoom={() => onZoom(i)}
          />
        ))}
      </div>
    </div>
  );
}

type Filter = "all" | "exact" | "near";

export default function Dedup() {
  const [status, setStatus] = useState<"loading" | "scanning" | "done" | "error">("loading");
  const [result, setResult] = useState<DedupReportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  // Per-group set of member indices to KEEP (multi-select). Defaults lazily to
  // the auto-picked keeper; the user can keep several.
  const [kept, setKept] = useState<Record<string, Set<number>>>({});
  const [viewer, setViewer] = useState<{ key: string; index: number } | null>(null);
  const pollRef = useRef<number | null>(null);

  function keptFor(g: DedupGroup): Set<number> {
    return kept[g.key] ?? new Set([g.keeper_index]);
  }
  function toggleKeep(g: DedupGroup, i: number) {
    setKept((prev) => {
      const cur = new Set(prev[g.key] ?? new Set([g.keeper_index]));
      if (cur.has(i)) {
        if (cur.size > 1) cur.delete(i); // always keep at least one
      } else cur.add(i);
      return { ...prev, [g.key]: cur };
    });
  }

  const poll = useCallback(async () => {
    try {
      const st = await dedupReport();
      if (st.error) {
        setError(st.error);
        setStatus("error");
        return;
      }
      if (st.running) {
        setStatus("scanning");
        return;
      }
      if (st.result) {
        setResult(st.result);
        setStatus("done");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStatus("error");
    }
  }, []);

  useEffect(() => {
    poll();
    return () => {
      if (pollRef.current !== null) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [poll]);

  useEffect(() => {
    if (status === "loading" || status === "scanning") {
      if (pollRef.current === null) {
        pollRef.current = window.setInterval(poll, 2000);
      }
    } else if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, [status, poll]);

  async function onRescan() {
    setStatus("scanning");
    setError(null);
    try {
      await dedupRescan();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStatus("error");
      return;
    }
    poll();
  }

  const groups = result?.groups ?? [];
  const filtered = groups.filter((g) => filter === "all" || g.kind === filter);

  return (
    <div className="app">
      <Rail />
      <div
        className="workspace"
        style={{
          display: "grid",
          gridTemplateRows: "auto 1fr",
          height: "100vh",
          maxHeight: "100vh",
          overflow: "hidden",
        }}
      >
        <PageHeader
          context="duplicates"
          title="Duplicates"
          subtitle={
            result
              ? `${result.libraries_scanned} ${result.libraries_scanned === 1 ? "library" : "libraries"} - ${result.photos_scanned.toLocaleString()} photos scanned`
              : "Scanning your libraries for duplicates..."
          }
          actions={
            <>
              {result && (
                <div className="dedup-summary dedup-summary-header">
                  <div className="dedup-summary-tile dedup-summary-tile-hero">
                    <span className="dedup-summary-value">{fmtBytes(result.total_reclaimable_bytes)}</span>
                    <span className="dedup-summary-label">reclaimable</span>
                  </div>
                  <div className="dedup-summary-tile">
                    <span className="dedup-summary-value">{result.exact_group_count}</span>
                    <span className="dedup-summary-label">exact groups</span>
                  </div>
                  <div className="dedup-summary-tile">
                    <span className="dedup-summary-value">{result.near_group_count}</span>
                    <span className="dedup-summary-label">near groups</span>
                  </div>
                </div>
              )}
              <button className="btn btn-outlined" onClick={onRescan} disabled={status === "scanning"}>
                {status === "scanning" ? "Scanning..." : "Rescan"}
              </button>
            </>
          }
          controls={
            result && result.groups.length > 0 ? (
              <div className="dedup-filters">
                {(["all", "exact", "near"] as Filter[]).map((f) => (
                  <button
                    key={f}
                    className={"dedup-filter-btn" + (filter === f ? " is-active" : "")}
                    onClick={() => setFilter(f)}
                  >
                    {f === "all" ? "All" : f === "exact" ? "Exact" : "Near"}
                  </button>
                ))}
              </div>
            ) : null
          }
        />
        <div className="dedup-wrap">
          {error && <p className="onb-error">{error}</p>}

          {(status === "loading" || status === "scanning") && !result && (
            <p className="dedup-empty">Scanning all registered libraries. This can take a moment...</p>
          )}

          {status === "done" && result && result.groups.length === 0 && (
            <p className="dedup-empty">No duplicates found. Your libraries are clean.</p>
          )}

          <div className="dedup-groups">
            {filtered.map((g) => (
              <GroupRow
                key={g.key}
                group={g}
                kept={keptFor(g)}
                onToggleKeep={(i) => toggleKeep(g, i)}
                onZoom={(i) => setViewer({ key: g.key, index: i })}
              />
            ))}
          </div>
        </div>
      </div>

      {viewer && (() => {
        const g = groups.find((x) => x.key === viewer.key);
        if (!g) return null;
        const kset = keptFor(g);
        const items = g.members.map((m) => ({
          sha256: m.sha256 ?? "",
          caption: `${m.library_name} · ${fmtBytes(m.size_bytes ?? 0)}`,
        }));
        return (
          <Viewer
            items={items}
            index={viewer.index}
            onIndex={(i) => setViewer({ key: g.key, index: i })}
            onClose={() => setViewer(null)}
            isMarked={(i) => kset.has(i)}
            markLabel="KEEP"
            renderActions={(_it, i) => (
              <button className="btn btn-filled" onClick={() => toggleKeep(g, i)}>
                {kset.has(i) ? "Keeping ✓" : "Keep this"}
              </button>
            )}
          />
        );
      })()}
    </div>
  );
}
