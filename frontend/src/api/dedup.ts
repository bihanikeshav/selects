const BASE = "/api";

export interface DedupPhotoRef {
  library_id: string;
  library_name: string;
  path: string;
  sha256: string | null;
  size_bytes: number | null;
  aesthetic_iqa: number | null;
  in_active_library: boolean;
  thumb_url: string | null;
}

export interface DedupGroup {
  kind: "exact" | "near";
  key: string;
  reclaimable_bytes: number;
  keeper_index: number;
  members: DedupPhotoRef[];
}

export interface DedupReportResult {
  libraries_scanned: number;
  photos_scanned: number;
  exact_group_count: number;
  near_group_count: number;
  total_reclaimable_bytes: number;
  groups: DedupGroup[];
}

export interface DedupReportStatus {
  running: boolean;
  result: DedupReportResult | null;
  error: string | null;
}

/** Parse a `{detail}` error body, falling back to the HTTP status. */
async function detailError(res: Response, fallback: string): Promise<Error> {
  try {
    const body = await res.json();
    if (body && typeof body.detail === "string") return new Error(body.detail);
  } catch {
    /* non-JSON body */
  }
  return new Error(`${fallback} ${res.status}`);
}

/** Fetch the duplicate report's current state. The first call kicks off a
 *  scan (if none is running and no result is cached yet); poll this same
 *  endpoint until `running` is false. */
export async function dedupReport(): Promise<DedupReportStatus> {
  const res = await fetch(`${BASE}/dedup/report`);
  if (!res.ok) throw await detailError(res, "dedupReport");
  return res.json();
}

/** Force a fresh scan even if a result is already cached. 409s (a scan is
 *  already running) are treated as success — the caller should just keep
 *  polling `dedupReport`. */
export async function dedupRescan(): Promise<boolean> {
  const res = await fetch(`${BASE}/dedup/rescan`, { method: "POST" });
  if (res.status === 409) return false;
  if (!res.ok) throw await detailError(res, "dedupRescan");
  return true;
}
