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

export interface StoryItem {
  rank: number;
  photo_id: number;
  sha256: string;
  thumb_url: string;
  preview_url: string;
  scene_label: string | null;
  taken_at: string | null;
}

export interface StoryEntry {
  id: number;
  day: string;
  title: string;
  photo_count: number;
  items: StoryItem[];
  cover_url: string;
}

export interface StoryList {
  total: number;
  stories: StoryEntry[];
}

export interface ProgressMsg {
  stage: "index" | "classical" | "embed" | "tag" | "story" | "vl" | "done";
  current: number;
  total: number;
  message?: string;
}
