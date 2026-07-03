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
  moment_id: number | null;
  moment_size: number | null;
}

export interface MomentMember {
  photo_id: number;
  sha256: string;
  rank: number;
  thumb_url: string;
  preview_url: string;
  taken_at: string | null;
}

export interface Moment {
  id: number;
  primary_photo_id: number;
  primary_sha256: string;
  started_at: string;
  ended_at: string;
  size: number;
  members: MomentMember[];
}

export interface MomentList {
  total: number;
  moments: Moment[];
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

export interface TagEntry {
  tag: string;
  count: number;
}

export interface TagList {
  tags: TagEntry[];
}

export interface StoryItem {
  rank: number;
  photo_id: number;
  sha256: string;
  thumb_url: string;
  preview_url: string;
  scene_label: string | null;
  taken_at: string | null;
  tag: string | null;
}

export interface VisitEntry {
  rank: number;
  name: string;
  summary: string | null;
  lat: number;
  lon: number;
  elevation_m: number | null;
  arrived_at: string;
  departed_at: string;
  photo_count: number;
  cover_thumb_url: string | null;
}

export interface StoryEntry {
  id: number;
  day: string;
  title: string;
  photo_count: number;
  items: StoryItem[];
  visits: VisitEntry[];
  cover_url: string;
  itinerary_breadcrumb: string;
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
