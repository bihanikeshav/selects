// API client for the export engine: file export (copy/zip) + XMP rating write-back.
// Own file per project convention — mirrors the fetch style of client.ts.

const BASE = "/api";

export type ExportMode = "copy" | "zip";
export type ExportStructure = "flat" | "by-day";
export type ExportSource = "curated" | "liked" | `story:${number}`;

export interface StartExportRequest {
  target: string;
  mode: ExportMode;
  source: ExportSource | string;
  structure: ExportStructure;
}

export interface StartExportResponse {
  job_id: string;
  total: number;
}

export interface ExportJobStatus {
  status: "running" | "done" | "error";
  current: number;
  total: number;
  result: {
    count: number;
    bytes: number;
    path: string;
    skipped: { photo_id: number; reason: string }[];
  } | null;
  error: string | null;
}

export interface XmpPlan {
  photo_id: number;
  path: string;
  verdict: string;
  new_rating: number;
  target: string;
  is_sidecar: boolean;
  existing_rating?: number | null;
  action: "write" | "skip_lower" | "skip_same" | "no_op";
  reason: string | null;
}

export interface XmpPreviewResponse {
  total: number;
  to_write: number;
  skipped: number;
  plans: XmpPlan[];
}

export interface XmpWriteResponse {
  total: number;
  written: number;
  skipped: number;
  failed: number;
  plans: XmpPlan[];
}

export async function startExport(req: StartExportRequest): Promise<StartExportResponse> {
  const res = await fetch(`${BASE}/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const j = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(j.detail || `startExport ${res.status}`);
  }
  return res.json();
}

export async function getExportStatus(jobId: string): Promise<ExportJobStatus> {
  const res = await fetch(`${BASE}/export/status/${jobId}`);
  if (!res.ok) throw new Error(`getExportStatus ${res.status}`);
  return res.json();
}

export async function previewXmp(source: string, force = false): Promise<XmpPreviewResponse> {
  const params = new URLSearchParams({ source, force: String(force) });
  const res = await fetch(`${BASE}/export/xmp/preview?${params}`);
  if (!res.ok) {
    const j = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(j.detail || `previewXmp ${res.status}`);
  }
  return res.json();
}

export async function writeXmp(source: string, force = false): Promise<XmpWriteResponse> {
  const res = await fetch(`${BASE}/export/xmp`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source, force }),
  });
  if (!res.ok) {
    const j = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(j.detail || `writeXmp ${res.status}`);
  }
  return res.json();
}
