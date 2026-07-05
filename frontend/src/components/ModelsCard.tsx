import { useEffect, useRef, useState } from "react";

import { modelsStatus, startModelsDownload } from "../api/client";
import type { ModelsStatus } from "../api/types";

interface ProgressMsg {
  stage: string;
  current: number;
  total: number;
  message?: string;
}

function gb(mb: number): string {
  return (mb / 1024).toFixed(1);
}

/** Compact "AI models" card: per-model status dots, total missing size, and a
 *  "Download missing" button that streams `stage:"models"` progress over the
 *  shared `/ws/progress` socket. */
export default function ModelsCard() {
  const [status, setStatus] = useState<ModelsStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [progress, setProgress] = useState<ProgressMsg | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  async function load() {
    try {
      const s = await modelsStatus();
      setStatus(s);
      setDownloading(s.downloading);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    load();
    return () => {
      wsRef.current?.close();
    };
  }, []);

  function connect() {
    if (wsRef.current) return;
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${window.location.host}/ws/progress`);
    wsRef.current = ws;
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data) as ProgressMsg;
        // This card only cares about the models stage; ignore everything else
        // that may be flowing on the shared socket.
        if (msg.stage !== "models") return;
        setProgress(msg);
        if (msg.message === "done") {
          ws.close();
          setDownloading(false);
          setProgress(null);
          load();
        }
      } catch {
        /* ignore malformed frames */
      }
    };
    ws.onclose = () => {
      wsRef.current = null;
    };
  }

  async function onDownload() {
    setErr(null);
    setDownloading(true);
    connect();
    try {
      await startModelsDownload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setDownloading(false);
    }
  }

  if (!status && !err) return null;

  const missingCount = status ? status.models.filter((m) => !m.present).length : 0;
  const mpct =
    progress && progress.total > 0
      ? Math.min(100, Math.round((progress.current / progress.total) * 100))
      : 0;

  return (
    <div className="lib-models">
      <div className="lib-models-head">
        <h2>AI models</h2>
        <span className="lib-sub">
          {status && status.total_missing_mb > 0
            ? `${gb(status.total_missing_mb)} GB missing`
            : "all present"}
        </span>
      </div>

      {err && <p className="onb-error">{err}</p>}

      {status && (
        <ul className="lib-models-list">
          {status.models.map((m) => (
            <li key={m.id} className="lib-model">
              <span
                className={"lib-model-dot" + (m.present ? " is-present" : " is-missing")}
                aria-hidden="true"
              />
              <span className="lib-model-name">{m.name}</span>
              <span className="lib-model-for">{m.required_for}</span>
              <span className="lib-model-size">{gb(m.approx_size_mb)} GB</span>
            </li>
          ))}
        </ul>
      )}

      {downloading ? (
        <div className="lib-models-progress">
          <div className="onb-bar" role="progressbar" aria-valuenow={mpct} aria-valuemin={0} aria-valuemax={100}>
            <div className="onb-bar-fill" style={{ width: `${mpct}%` }} />
          </div>
          <p className="onb-progress-caption">
            {progress && progress.total > 0
              ? `Model ${progress.current} of ${progress.total}`
              : "Starting download…"}
            {progress?.message && progress.message !== "done" ? ` · ${progress.message}` : ""}
          </p>
        </div>
      ) : (
        missingCount > 0 && (
          <button className="btn btn-filled" type="button" onClick={onDownload}>
            Download missing ({status ? gb(status.total_missing_mb) : "0"} GB)
          </button>
        )
      )}
    </div>
  );
}
