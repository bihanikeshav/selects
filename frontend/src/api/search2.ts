// Hybrid discovery search API (SigLIP semantic + RAM++ tag matches + filters).
// Talks to /api/search2 (own new endpoint — see travelcull/server/search2_routes.py).
// Mirrors the fetch style of ./client.ts.

const BASE = "/api";

export interface Search2Hit {
  photo_id: number;
  sha256: string;
  score: number;
  semantic_score: number;
  tag_hits: number;
  thumb_url: string;
  preview_url: string;
}

export interface Search2Result {
  query: string | null;
  total: number;
  results: Search2Hit[];
}

export interface TagEntry {
  tag: string;
  count: number;
}

export interface TagList {
  tags: TagEntry[];
}

export interface Search2Opts {
  q?: string;
  tags?: string[];
  person_id?: number;
  date_from?: string;
  date_to?: string;
  min_aesthetic?: number;
  limit?: number;
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

export async function search2(opts: Search2Opts): Promise<Search2Result> {
  const params = new URLSearchParams();
  if (opts.q) params.set("q", opts.q);
  if (opts.tags && opts.tags.length) params.set("tags", opts.tags.join(","));
  if (opts.person_id !== undefined) params.set("person_id", String(opts.person_id));
  if (opts.date_from) params.set("date_from", opts.date_from);
  if (opts.date_to) params.set("date_to", opts.date_to);
  if (opts.min_aesthetic !== undefined) params.set("min_aesthetic", String(opts.min_aesthetic));
  if (opts.limit !== undefined) params.set("limit", String(opts.limit));

  const res = await fetch(`${BASE}/search2?${params}`);
  if (!res.ok) throw await detailError(res, "search2");
  return res.json();
}

/** Reuses the existing /api/tags endpoint (owned by routes.py) for the TagBrowser sidebar. */
export async function listAllTags(): Promise<TagList> {
  const res = await fetch(`${BASE}/tags`);
  if (!res.ok) throw new Error(`listAllTags ${res.status}`);
  return res.json();
}

export interface PersonEntry {
  id: number;
  label: string | null;
  photo_count: number;
  cover_url: string;
}

/** Reuses the existing /api/persons endpoint (owned by routes.py) for the person filter. */
export async function listPersonsForFilter(): Promise<PersonEntry[]> {
  const res = await fetch(`${BASE}/persons`);
  if (!res.ok) throw new Error(`listPersonsForFilter ${res.status}`);
  const body = await res.json();
  return body.persons;
}
