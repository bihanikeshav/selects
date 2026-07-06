import { useEffect, useRef, useState } from "react";

import { getWatchStatus, updateWatch } from "../api/watch";
import type { WatchStatus } from "../api/watch";
import "./WatchCard.css";

interface WatchEventMsg {
  type?: string;
  stage?: string;
  new_files_found?: number;
  message?: string;
}

function formatLastRun(iso: string | null): string {
  if (!iso) return "never";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "never";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** Compact "watch folder" card: toggle polling for new files, show interval,
 *  last-run time, and most-recent new-file count. Listens on the shared
 *  `/ws/progress` socket for `type:"watch"` events pushed when new photos
 *  land, so the count updates live without polling the REST endpoint. */
export default function WatchCard() {
  const [status, setStatus] = useState<WatchStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  async function load() {
    try {
      const s = await getWatchStatus();
      setStatus(s);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    load();
    connect();
    return () => {
      wsRef.current?.close();
      wsRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function connect() {
    if (wsRef.current) return;
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${window.location.host}/ws/progress`);
    wsRef.current = ws;
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data) as WatchEventMsg;
        if (msg.type !== "watch" && msg.stage !== "watch") return;
        load();
      } catch {
        /* ignore malformed frames */
      }
    };
    ws.onclose = () => {
      wsRef.current = null;
    };
  }

  async function onToggle() {
    if (!status) return;
    setErr(null);
    setBusy(true);
    try {
      const next = await updateWatch({ enabled: !status.enabled });
      setStatus(next);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onIntervalChange(seconds: number) {
    if (!status) return;
    setErr(null);
    try {
      const next = await updateWatch({ interval: seconds });
      setStatus(next);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  if (!status && !err) return null;

  return (
    <div className="watch-card">
      <div className="watch-card-head">
        <h2>Watch folder</h2>
        <label className="watch-toggle">
          <input
            type="checkbox"
            checked={!!status?.enabled}
            disabled={busy || !status}
            onChange={onToggle}
          />
          <span className="watch-toggle-track" aria-hidden="true" />
        </label>
      </div>

      {err && <p className="onb-error">{err}</p>}

      {status && (
        <div className="watch-card-body">
          <div className="watch-row">
            <span className="watch-label">Check every</span>
            <select
              className="watch-select"
              value={status.interval}
              disabled={busy}
              onChange={(e) => onIntervalChange(Number(e.target.value))}
            >
              <option value={30}>30s</option>
              <option value={60}>1 min</option>
              <option value={300}>5 min</option>
              <option value={900}>15 min</option>
            </select>
          </div>
          <div className="watch-row">
            <span className="watch-label">Last checked</span>
            <span className="watch-value">{formatLastRun(status.last_run)}</span>
          </div>
          <div className="watch-row">
            <span className="watch-label">Last import</span>
            <span className="watch-value">
              {status.new_files_found > 0
                ? `${status.new_files_found} new file${status.new_files_found === 1 ? "" : "s"}`
                : "none"}
            </span>
          </div>
          <p className="watch-status-line">
            <span
              className={"watch-dot" + (status.running ? " is-active" : "")}
              aria-hidden="true"
            />
            {status.running ? "Watching for new photos" : "Not watching"}
          </p>
        </div>
      )}
    </div>
  );
}
