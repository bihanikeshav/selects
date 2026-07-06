// API client for the trip-recap generator: a self-contained shareable HTML
// keepsake page. Own file per project convention — mirrors client.ts fetch style.

const BASE = "/api";

export interface RecapResponse {
  story_id: number;
  path: string;
  download_url: string;
}

export async function generateRecap(storyId: number): Promise<RecapResponse> {
  const res = await fetch(`${BASE}/recap/${storyId}`, { method: "POST" });
  if (!res.ok) {
    const j = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(j.detail || `generateRecap ${res.status}`);
  }
  return res.json();
}

export function recapDownloadUrl(storyId: number): string {
  return `${BASE}/recap/${storyId}/download`;
}
