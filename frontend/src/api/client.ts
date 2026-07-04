import type { ClusterList, Moment, MomentList, PhotoList, PhotoTagsResponse, StoryList, TagList } from "./types";

const BASE = "/api";

export async function listPhotos(opts: { offset?: number; limit?: number; rejected?: boolean; tag?: string; collapse?: "moments" | "none" } = {}): Promise<PhotoList> {
  const params = new URLSearchParams();
  if (opts.offset !== undefined) params.set("offset", String(opts.offset));
  if (opts.limit !== undefined) params.set("limit", String(opts.limit));
  if (opts.rejected !== undefined) params.set("rejected", String(opts.rejected));
  if (opts.tag !== undefined) params.set("tag", opts.tag);
  if (opts.collapse !== undefined) params.set("collapse", opts.collapse);
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

export async function openInEditor(sha256s: string[], editor = "darktable"): Promise<{ opened: number; editor: string }> {
  const res = await fetch(`${BASE}/edit/open`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sha256s, editor }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `openInEditor ${res.status}`);
  }
  return res.json();
}

export async function getPhotoTags(sha256: string): Promise<PhotoTagsResponse> {
  const res = await fetch(`${BASE}/photos/${sha256}/tags`);
  if (!res.ok) throw new Error(`getPhotoTags ${res.status}`);
  return res.json();
}

export async function listTags(): Promise<TagList> {
  const res = await fetch(`${BASE}/tags`);
  if (!res.ok) throw new Error(`listTags ${res.status}`);
  return res.json();
}

export async function listStories(opts: { includeTags?: string[]; excludeTags?: string[] } = {}): Promise<StoryList> {
  const params = new URLSearchParams();
  if (opts.includeTags && opts.includeTags.length > 0) {
    params.set("include_tags", opts.includeTags.join(","));
  }
  if (opts.excludeTags && opts.excludeTags.length > 0) {
    params.set("exclude_tags", opts.excludeTags.join(","));
  }
  const qs = params.toString();
  const res = await fetch(`${BASE}/stories${qs ? `?${qs}` : ""}`);
  if (!res.ok) throw new Error(`listStories ${res.status}`);
  return res.json();
}

export async function listMoments(): Promise<MomentList> {
  const res = await fetch(`${BASE}/moments`);
  if (!res.ok) throw new Error(`listMoments ${res.status}`);
  return res.json();
}

export async function getPhotoMoment(sha256: string): Promise<Moment | null> {
  const res = await fetch(`${BASE}/photos/${sha256}/moment`);
  if (!res.ok) throw new Error(`getPhotoMoment ${res.status}`);
  return res.json();
}

export function progressSocket(onMessage: (m: unknown) => void): WebSocket {
  const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/progress`;
  const ws = new WebSocket(url);
  ws.addEventListener("message", e => onMessage(JSON.parse(e.data as string)));
  return ws;
}
