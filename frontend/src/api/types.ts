export interface Photo {
  id: number;
  sha256: string;
  path: string;
  format: string | null;
  width: number | null;
  height: number | null;
  taken_at: string | null;
  thumb_url: string;
  preview_url: string;
  blur: number | null;
  exposure: number | null;
  faces_count: number | null;
  auto_reject: boolean | null;
  reject_reason: string | null;
  aesthetic_iqa: number | null;
}

export interface PhotoList { total: number; items: Photo[]; }

export interface ClusterEntry {
  tag: string;
  count: number;
  cover_sha256: string;
  cover_url: string;
  sample_thumbs: string[];
}

export interface ClusterList {
  total: number;
  clusters: ClusterEntry[];
}

export interface ProgressMsg {
  stage: "index" | "classical" | "embed" | "tag" | "vl" | "done";
  current: number;
  total: number;
  message?: string;
}
