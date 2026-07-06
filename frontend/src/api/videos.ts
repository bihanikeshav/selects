const BASE = "/api";

export interface VideoHighlight {
  start: number;
  end: number;
  frames: number;
}

export interface VideoItem {
  id: number;
  sha256: string | null;
  path: string;
  name: string;
  format: string | null;
  width: number | null;
  height: number | null;
  duration_sec: number | null;
  fps: number | null;
  taken_at: string | null;
  thumb_url: string | null;
  processed: boolean;
  sharpness: number | null;
  exposure: number | null;
  dead_footage: boolean | null;
  highlight_count: number;
  highlights: VideoHighlight[];
  sampled_frames: number;
}

export interface VideoListResponse {
  videos: VideoItem[];
  total: number;
  processed: number;
  dead_footage_count: number;
}

export interface VideoFrame {
  index: number;
  frame_index: number;
  t_sec: number;
  blur: number;
  exposure: number;
  quality: number;
  good: boolean;
  url: string;
}

export interface VideoFramesResponse {
  sha256: string;
  path: string;
  duration_sec: number | null;
  dead_footage: boolean | null;
  highlights: VideoHighlight[];
  best_frame_index: number | null;
  frames: VideoFrame[];
}

export interface VideoProcessStatus {
  running: boolean;
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

/** List every indexed video with its analysis scores and flags. */
export async function listVideos(): Promise<VideoListResponse> {
  const res = await fetch(`${BASE}/videos`);
  if (!res.ok) throw await detailError(res, "listVideos");
  return res.json();
}

/** Fetch the sampled filmstrip (per-frame quality metrics) for one video. */
export async function getVideoFrames(sha256: string): Promise<VideoFramesResponse> {
  const res = await fetch(`${BASE}/videos/${sha256}/frames`);
  if (!res.ok) throw await detailError(res, "getVideoFrames");
  return res.json();
}

/** Kick off the background video-analysis stage. 409s (already running) are
 *  treated as success — keep polling `videoProcessStatus`. */
export async function processVideos(): Promise<boolean> {
  const res = await fetch(`${BASE}/videos/process`, { method: "POST" });
  if (res.status === 409) return false;
  if (!res.ok) throw await detailError(res, "processVideos");
  return true;
}

/** Poll the background analysis state. */
export async function videoProcessStatus(): Promise<VideoProcessStatus> {
  const res = await fetch(`${BASE}/videos/process/status`);
  if (!res.ok) throw await detailError(res, "videoProcessStatus");
  return res.json();
}
