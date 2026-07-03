import type { ClusterList, PhotoList, StoryList } from "./types";

const BASE = "/api";

export async function listPhotos(opts: { offset?: number; limit?: number; rejected?: boolean; tag?: string } = {}): Promise<PhotoList> {
  const params = new URLSearchParams();
  if (opts.offset !== undefined) params.set("offset", String(opts.offset));
  if (opts.limit !== undefined) params.set("limit", String(opts.limit));
  if (opts.rejected !== undefined) params.set("rejected", String(opts.rejected));
  if (opts.tag !== undefined) params.set("tag", opts.tag);
  const res = await fetch(`${BASE}/photos?${params}`);
  if (!res.ok) throw new Error(`listPhotos ${res.status}`);
  return res.json();
}

export async function listClusters(): Promise<ClusterList> {
  const res = await fetch(`${BASE}/clusters`);
  if (!res.ok) throw new Error(`listClusters ${res.status}`);
  return res.json();
}

export async function listStories(): Promise<StoryList> {
  const res = await fetch(`${BASE}/stories`);
  if (!res.ok) throw new Error(`listStories ${res.status}`);
  return res.json();
}

export function progressSocket(onMessage: (m: unknown) => void): WebSocket {
  const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/progress`;
  const ws = new WebSocket(url);
  ws.addEventListener("message", e => onMessage(JSON.parse(e.data as string)));
  return ws;
}
