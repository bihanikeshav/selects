export interface WatchStatus {
  enabled: boolean;
  interval: number;
  last_run: string | null;
  new_files_found: number;
  running: boolean;
}

const BASE = "/api";

export async function getWatchStatus(): Promise<WatchStatus> {
  const res = await fetch(`${BASE}/watch`);
  if (!res.ok) throw new Error(`getWatchStatus ${res.status}`);
  return res.json();
}

export async function updateWatch(opts: { enabled?: boolean; interval?: number }): Promise<WatchStatus> {
  const res = await fetch(`${BASE}/watch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(opts),
  });
  if (!res.ok) throw new Error(`updateWatch ${res.status}`);
  return res.json();
}
