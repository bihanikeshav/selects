import type {
  ClusterList,
  CuratedPhoto,
  Library,
  LibraryList,
  LibraryStatus,
  Moment,
  PhotoList,
} from "./types";

const BASE = "/api";

export async function listPhotos(opts: {
  offset?: number;
  limit?: number;
  rejected?: boolean;
  tag?: string;
  collapse?: "moments" | "none";
  sort?: "taken_at" | "aesthetic" | "iqa" | "random";
  min_aesthetic_pct?: number;
} = {}): Promise<PhotoList> {
  const params = new URLSearchParams();
  if (opts.offset !== undefined) params.set("offset", String(opts.offset));
  if (opts.limit !== undefined) params.set("limit", String(opts.limit));
  if (opts.rejected !== undefined) params.set("rejected", String(opts.rejected));
  if (opts.tag !== undefined) params.set("tag", opts.tag);
  if (opts.collapse !== undefined) params.set("collapse", opts.collapse);
  if (opts.sort !== undefined) params.set("sort", opts.sort);
  if (opts.min_aesthetic_pct !== undefined) params.set("min_aesthetic_pct", String(opts.min_aesthetic_pct));
  const res = await fetch(`${BASE}/photos?${params}`);
  if (!res.ok) throw new Error(`listPhotos ${res.status}`);
  return res.json();
}

export async function listClusters(opts: { source?: "thematic" | "date" | "lookback" | "posting" | "" } = {}): Promise<ClusterList> {
  const params = new URLSearchParams();
  if (opts.source !== undefined) params.set("source", opts.source);
  const qs = params.toString();
  const res = await fetch(`${BASE}/clusters${qs ? `?${qs}` : ""}`);
  if (!res.ok) throw new Error(`listClusters ${res.status}`);
  return res.json();
}

export async function listClusterPhotos(tag: string, source = "thematic", limit = 500): Promise<PhotoList> {
  const params = new URLSearchParams({ source, limit: String(limit) });
  const res = await fetch(`${BASE}/clusters/${encodeURIComponent(tag)}/photos?${params}`);
  if (!res.ok) throw new Error(`listClusterPhotos ${res.status}`);
  return res.json();
}

export async function getPhotoMoment(sha256: string): Promise<Moment | null> {
  const res = await fetch(`${BASE}/photos/${sha256}/moment`);
  if (!res.ok) throw new Error(`getPhotoMoment ${res.status}`);
  return res.json();
}

export async function setMomentPrimary(momentId: number, photoId: number): Promise<void> {
  const res = await fetch(`${BASE}/moments/${momentId}/primary`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ photo_id: photoId }),
  });
  if (!res.ok) throw new Error(`setMomentPrimary ${res.status}`);
}

export async function recordSwipe(sha256: string, decision: "keep" | "reject" | "silver" | "skip"): Promise<void> {
  const res = await fetch(`${BASE}/swipes/${sha256}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision }),
  });
  if (!res.ok) throw new Error(`recordSwipe ${res.status}`);
}

export async function getLikedStatus(sha256s: string[]): Promise<Record<string, boolean>> {
  if (sha256s.length === 0) return {};
  const res = await fetch(`${BASE}/likes/status?shas=${sha256s.join(",")}`);
  if (!res.ok) throw new Error(`getLikedStatus ${res.status}`);
  return res.json();
}

export async function listCurated(sort: "aesthetic" | "taken_at" = "aesthetic"): Promise<{ total: number; photos: CuratedPhoto[] }> {
  const res = await fetch(`${BASE}/curated?sort=${sort}`);
  if (!res.ok) throw new Error(`listCurated ${res.status}`);
  return res.json();
}

// ===== libraries =========================================================

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

export async function listLibraries(): Promise<LibraryList> {
  const res = await fetch(`${BASE}/libraries`);
  if (!res.ok) throw new Error(`listLibraries ${res.status}`);
  return res.json();
}

export async function libraryStatus(): Promise<LibraryStatus> {
  const res = await fetch(`${BASE}/libraries/status`);
  if (!res.ok) throw new Error(`libraryStatus ${res.status}`);
  return res.json();
}

export async function createLibrary(name: string, path: string): Promise<Library> {
  const res = await fetch(`${BASE}/libraries`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, path }),
  });
  if (!res.ok) throw await detailError(res, "createLibrary");
  const body = await res.json();
  return body.library;
}

export async function activateLibrary(id: string): Promise<Library> {
  const res = await fetch(`${BASE}/libraries/${encodeURIComponent(id)}/activate`, {
    method: "POST",
  });
  if (!res.ok) throw await detailError(res, "activateLibrary");
  const body = await res.json();
  return body.library;
}

export async function deleteLibrary(id: string): Promise<void> {
  const res = await fetch(`${BASE}/libraries/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw await detailError(res, "deleteLibrary");
}

export async function startIndexing(id: string): Promise<void> {
  const res = await fetch(`${BASE}/libraries/${encodeURIComponent(id)}/index`, {
    method: "POST",
  });
  if (!res.ok) throw await detailError(res, "startIndexing");
}
