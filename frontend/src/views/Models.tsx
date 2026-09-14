import { useEffect, useState } from "react";

import { modelsStatus, startModelsDownload } from "../api/client";
import type { ModelsStatus } from "../api/types";
import { MODEL_LABELS } from "../components/ModelsCard";
import PageHeader from "../components/PageHeader";
import Rail from "../components/Rail";
import { useProgressSocket, type ProgressMsg } from "../hooks/useProgressSocket";
import "./Models.css";

function fmtSize(mb: number): string {
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`;
  return `${mb} MB`;
}

export default function Models() {
  const [status, setStatus] = useState<ModelsStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [progress, setProgress] = useState<ProgressMsg | null>(null);
  const [socketOn, setSocketOn] = useState(false);
  const [target, setTarget] = useState<string | null>(null);

  async function load() {
    try {
      const next = await modelsStatus();
      setStatus(next);
      setDownloading(next.downloading);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    void load();
  }, []);

  useProgressSocket(
    (msg: ProgressMsg) => {
      if (msg.stage !== "models") return;
      setProgress(msg);
      if (msg.message === "done") {
        setSocketOn(false);
        setDownloading(false);
        setTarget(null);
        setProgress(null);
        void load();
      }
    },
    { enabled: socketOn },
  );

  async function onDownload(assetId?: string) {
    setErr(null);
    setDownloading(true);
    setTarget(assetId ?? "all");
    setSocketOn(true);
    try {
      await startModelsDownload(assetId);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setDownloading(false);
      setTarget(null);
    }
  }

  const missing = status?.models.filter((m) => !m.present) ?? [];
  const present = status?.models.filter((m) => m.present) ?? [];
  const pct =
    progress && progress.total > 0
      ? Math.min(100, Math.round((progress.current / progress.total) * 100))
      : 0;

  return (
    <div className="app-shell">
      <Rail />
      <div className="app-main">
        <PageHeader
          context="setup"
          title="Models"
          subtitle="Weights used for photos, faces, and video. Missing ones skip that step until you download them."
          actions={
            missing.length > 0 ? (
              <button
                className="btn btn-filled"
                type="button"
                disabled={downloading}
                onClick={() => void onDownload()}
              >
                Download missing ({fmtSize(status?.total_missing_mb ?? 0)})
              </button>
            ) : undefined
          }
        />
        <div className="models-page">
          {err && <p className="onb-error">{err}</p>}

          {downloading && (
            <div className="models-progress">
              <div className="onb-bar" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
                <div className="onb-bar-fill" style={{ width: `${pct}%` }} />
              </div>
              <p>
                {progress && progress.total > 0
                  ? `Model ${progress.current} of ${progress.total}`
                  : "Starting download…"}
                {progress?.message && progress.message !== "done" ? ` · ${progress.message}` : ""}
              </p>
            </div>
          )}

          {status && (
            <>
              <p className="models-cache">
                Cache folder: <code>{status.cache_root ?? "—"}</code>
                {" · "}all three packs live here as subfolders
              </p>
              {status.runtime && (
                <p className="models-runtime">
                  This PC runs ONNX as <strong>{status.runtime.device}</strong>
                  {status.runtime.gpu_without_cuda
                    ? " — GPU without NVIDIA CUDA."
                    : status.runtime.using_cuda
                      ? " — NVIDIA CUDA is available."
                      : " — CPU. DirectML (Windows) or CoreML (Mac) would use the GPU without CUDA."}
                </p>
              )}
              <ul className="models-list">
                {status.models.map((model) => {
                  const label = MODEL_LABELS[model.id];
                  return (
                    <li key={model.id} className={"models-row" + (model.present ? " is-present" : " is-missing")}>
                      <span className={"models-dot" + (model.present ? " is-present" : " is-missing")} aria-hidden="true" />
                      <div className="models-copy">
                        <strong>{label?.title ?? model.name}</strong>
                        <span>{label?.tech ?? model.kind} · {model.required_for}</span>
                        <code title={model.cache_path}>{model.cache_path}</code>
                      </div>
                      <span className="models-size">{fmtSize(model.approx_size_mb)}</span>
                      {model.present ? (
                        <span className="models-state">On disk</span>
                      ) : (
                        <button
                          className="btn btn-tonal btn-sm"
                          type="button"
                          disabled={downloading}
                          onClick={() => void onDownload(model.id)}
                        >
                          {target === model.id ? "Downloading…" : "Download"}
                        </button>
                      )}
                    </li>
                  );
                })}
              </ul>
              <p className="models-footnote">
                {present.length} ready, {missing.length} missing.
                First-time analysis will also fetch a missing model if the network is available;
                this page is the explicit control so a cull run does not stall on a multi-gigabyte download.
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
