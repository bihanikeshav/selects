import { useCallback, useEffect, useRef, useState } from "react";

import {
  getExportStatus,
  previewXmp,
  startExport,
  writeXmp,
  type ExportJobStatus,
  type ExportMode,
  type ExportStructure,
  type XmpPreviewResponse,
} from "../api/export";
import FolderPicker from "./FolderPicker";
import "./ExportPanel.css";

type Tab = "files" | "xmp";

interface Props {
  /** "curated" | "liked" | "story:<id>" — what set of photos this panel operates on. */
  source: string;
  onClose: () => void;
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(1)} ${units[i]}`;
}

/**
 * Export engine panel: copy/zip keepers out to a folder, or write XMP star
 * ratings back onto the originals (with a preview-before-commit step).
 * Mounted as a modal — see Curated view toolbar for the "Export…" trigger.
 */
export default function ExportPanel({ source, onClose }: Props) {
  const [tab, setTab] = useState<Tab>("files");

  // --- file export state ---
  const [target, setTarget] = useState("");
  const [mode, setMode] = useState<ExportMode>("copy");
  const [structure, setStructure] = useState<ExportStructure>("flat");
  const [job, setJob] = useState<ExportJobStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const [exportErr, setExportErr] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  // --- xmp state ---
  const [preview, setPreview] = useState<XmpPreviewResponse | null>(null);
  const [force, setForce] = useState(false);
  const [xmpLoading, setXmpLoading] = useState(false);
  const [xmpErr, setXmpErr] = useState<string | null>(null);
  const [xmpDone, setXmpDone] = useState<string | null>(null);

  useEffect(() => {
    return () => {
      if (pollRef.current !== null) window.clearInterval(pollRef.current);
    };
  }, []);

  const runExport = useCallback(async () => {
    if (!target.trim()) return;
    setStarting(true);
    setExportErr(null);
    setJob(null);
    try {
      const res = await startExport({ target: target.trim(), mode, source, structure });
      if (pollRef.current !== null) window.clearInterval(pollRef.current);
      pollRef.current = window.setInterval(async () => {
        try {
          const status = await getExportStatus(res.job_id);
          setJob(status);
          if (status.status !== "running" && pollRef.current !== null) {
            window.clearInterval(pollRef.current);
            pollRef.current = null;
          }
        } catch (e) {
          setExportErr(String(e));
          if (pollRef.current !== null) window.clearInterval(pollRef.current);
        }
      }, 400);
    } catch (e) {
      setExportErr(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  }, [target, mode, structure, source]);

  const loadPreview = useCallback(async () => {
    setXmpLoading(true);
    setXmpErr(null);
    setXmpDone(null);
    try {
      const res = await previewXmp(source, force);
      setPreview(res);
    } catch (e) {
      setXmpErr(e instanceof Error ? e.message : String(e));
    } finally {
      setXmpLoading(false);
    }
  }, [source, force]);

  useEffect(() => {
    if (tab === "xmp") loadPreview();
  }, [tab, loadPreview]);

  const confirmXmp = useCallback(async () => {
    setXmpLoading(true);
    setXmpErr(null);
    try {
      const res = await writeXmp(source, force);
      setXmpDone(`Wrote ${res.written}, skipped ${res.skipped}, failed ${res.failed}.`);
      await loadPreview();
    } catch (e) {
      setXmpErr(e instanceof Error ? e.message : String(e));
    } finally {
      setXmpLoading(false);
    }
  }, [source, force, loadPreview]);

  const pct = job && job.total > 0 ? Math.round((job.current / job.total) * 100) : 0;

  return (
    <div className="export-panel" onClick={onClose}>
      <div className="export-panel-card" onClick={(e) => e.stopPropagation()}>
        <div className="export-panel-head">
          <h2 className="export-panel-title">Export</h2>
          <button className="export-panel-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>

        <div className="export-panel-tabs">
          <button
            className={`btn ${tab === "files" ? "btn-filled" : "btn-text"}`}
            onClick={() => setTab("files")}
          >
            Export files
          </button>
          <button
            className={`btn ${tab === "xmp" ? "btn-filled" : "btn-text"}`}
            onClick={() => setTab("xmp")}
          >
            XMP ratings
          </button>
        </div>

        {tab === "files" && (
          <>
            <div className="export-field">
              <label>Destination folder</label>
              <FolderPicker value={target} onChange={setTarget} />
            </div>

            <div className="export-row">
              <div className="export-field">
                <label>Mode</label>
                <select
                  className="export-select"
                  value={mode}
                  onChange={(e) => setMode(e.target.value as ExportMode)}
                >
                  <option value="copy">Copy files</option>
                  <option value="zip">Zip archive</option>
                </select>
              </div>
              <div className="export-field">
                <label>Structure</label>
                <select
                  className="export-select"
                  value={structure}
                  onChange={(e) => setStructure(e.target.value as ExportStructure)}
                >
                  <option value="flat">Flat</option>
                  <option value="by-day">By day</option>
                </select>
              </div>
            </div>

            {job && (
              <div className="export-progress-wrap">
                <div className="export-progress-bar">
                  <div className="export-progress-fill" style={{ width: `${pct}%` }} />
                </div>
                <div className="export-status-line">
                  {job.status === "running" && `${job.current} / ${job.total} (${pct}%)`}
                  {job.status === "error" && `Failed: ${job.error}`}
                  {job.status === "done" && "Done"}
                </div>
              </div>
            )}

            {job?.status === "done" && job.result && (
              <div className="export-result">
                Exported {job.result.count} photo{job.result.count === 1 ? "" : "s"} (
                {formatBytes(job.result.bytes)}) to {job.result.path}
                {job.result.skipped.length > 0 && ` — ${job.result.skipped.length} skipped`}
              </div>
            )}

            {exportErr && <div className="export-error">{exportErr}</div>}

            <div className="export-panel-footer">
              <button
                className="btn btn-filled"
                onClick={runExport}
                disabled={!target.trim() || starting || job?.status === "running"}
              >
                {starting || job?.status === "running" ? "Exporting…" : "Start export"}
              </button>
            </div>
          </>
        )}

        {tab === "xmp" && (
          <>
            <p style={{ margin: 0, fontSize: 12, color: "var(--md-on-surface-var)" }}>
              Writes star ratings onto the originals — 5 for liked, 4 for curated, 1 for
              rejected. RAW files get a sidecar (.xmp) instead of being touched directly.
            </p>

            <div className="export-force-row">
              <input
                id="export-force"
                type="checkbox"
                checked={force}
                onChange={(e) => {
                  setForce(e.target.checked);
                }}
              />
              <label htmlFor="export-force">Force (overwrite existing higher ratings)</label>
            </div>

            {xmpLoading && !preview && (
              <div className="export-status-line">Loading preview…</div>
            )}

            {preview && (
              <>
                <div className="export-status-line">
                  {preview.to_write} to write, {preview.skipped} skipped, {preview.total} total
                </div>
                <div className="export-xmp-list">
                  {preview.plans.map((p) => (
                    <div className="export-xmp-row" key={p.photo_id}>
                      <span className="export-xmp-path" title={p.path}>
                        {p.path}
                      </span>
                      <span>{p.new_rating}★</span>
                      <span className={`export-xmp-badge ${p.action}`}>{p.action}</span>
                    </div>
                  ))}
                </div>
              </>
            )}

            {xmpDone && <div className="export-result">{xmpDone}</div>}
            {xmpErr && <div className="export-error">{xmpErr}</div>}

            <div className="export-panel-footer">
              <button className="btn btn-text" onClick={loadPreview} disabled={xmpLoading}>
                Refresh preview
              </button>
              <button
                className="btn btn-filled"
                onClick={confirmXmp}
                disabled={xmpLoading || !preview || preview.to_write === 0}
              >
                {xmpLoading ? "Writing…" : `Write ${preview?.to_write ?? 0} ratings`}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
