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
  /** Optional fields returned by the editor-aware video API. */
  has_audio?: boolean | null;
  edited?: boolean;
  review_state?: "new" | "review" | "kept" | "archived" | null;
  tags?: string[];
  rating?: number | null;
  collection_ids?: number[];
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

export interface VideoEditState {
  sha256: string;
  in_sec: number;
  out_sec: number | null;
  stabilization: boolean;
  muted: boolean;
  volume: number;
  title: string;
  tags: string[];
  updated_at?: string | null;
}

export interface VideoEditPatch {
  in_sec?: number;
  out_sec?: number | null;
  stabilization?: boolean;
  muted?: boolean;
  volume?: number;
  title?: string;
  tags?: string[];
}

export interface VideoExportJob {
  id: string;
  status: "queued" | "running" | "complete" | "cancelled" | "failed";
  progress: number;
  download_url?: string | null;
  error?: string | null;
}

export interface VideoPlaybackInfo {
  source_url: string;
  proxy_ready: boolean;
  proxy_required: boolean;
  duration_sec: number | null;
  reason: string;
}

export interface VideoTimelineResponse {
  sha256: string;
  duration_sec: number | null;
  frames: VideoFrame[];
  highlights: VideoHighlight[];
  search_hits?: Array<{ start: number; end: number; label: string }>;
  waveform?: number[];
  audio?: {
    available: boolean;
    sample_rate: number | null;
    silence_ratio: number | null;
    speech_ratio: number | null;
  } | null;
}

interface StoredVideoEdit {
  recipe: {
    segments?: Array<{ start_sec: number; end_sec: number }>;
    mute_audio?: boolean;
    gain_db?: number;
    stabilize?: { enabled?: boolean };
  };
  name?: string | null;
  updated_at?: string | null;
}

export interface VideoRating {
  sha256: string;
  rating: number | null;
}

export interface VideoTagsResponse {
  sha256: string;
  tags: string[];
}

export interface VideoCollection {
  id: string;
  name: string;
  video_count?: number;
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

/** Browser playback URL reserved for the editor-aware video route. */
export async function getVideoPlayback(sha256: string): Promise<VideoPlaybackInfo> {
  const res = await fetch(`${BASE}/videos/${encodeURIComponent(sha256)}/playback`);
  if (!res.ok) throw await detailError(res, "getVideoPlayback");
  return res.json();
}

/** Direct stream URL for clients that need a range-friendly source. */
export function videoStreamUrl(sha256: string): string {
  return `${BASE}/videos/${encodeURIComponent(sha256)}/stream`;
}

/** Fetch the editor timeline contract (filmstrip plus derived markers). */
export async function getVideoTimeline(sha256: string): Promise<VideoTimelineResponse> {
  const res = await fetch(`${BASE}/videos/${encodeURIComponent(sha256)}/timeline`);
  if (!res.ok) throw await detailError(res, "getVideoTimeline");
  return res.json();
}

/** Fetch the non-destructive edit state. Backend support is intentionally optional. */
export async function getVideoEdit(sha256: string): Promise<VideoEditState> {
  const [editRes, tagsRes] = await Promise.all([
    fetch(`${BASE}/videos/${encodeURIComponent(sha256)}/edit`),
    fetch(`${BASE}/videos/${encodeURIComponent(sha256)}/tags`),
  ]);
  if (!editRes.ok) throw await detailError(editRes, "getVideoEdit");
  const stored = (await editRes.json()) as StoredVideoEdit;
  const tags = tagsRes.ok ? ((await tagsRes.json()) as VideoTagsResponse).tags : [];
  const segment = stored.recipe.segments?.[0];
  const gainDb = stored.recipe.gain_db ?? 0;
  return {
    sha256,
    in_sec: segment?.start_sec ?? 0,
    out_sec: segment?.end_sec ?? null,
    stabilization: stored.recipe.stabilize?.enabled ?? false,
    muted: stored.recipe.mute_audio ?? false,
    volume: Math.max(0, Math.min(1, Math.pow(10, gainDb / 20))),
    title: stored.name ?? "",
    tags,
    updated_at: stored.updated_at,
  };
}

/** Save a non-destructive edit state without transcoding the source. */
export async function saveVideoEdit(sha256: string, patch: VideoEditPatch): Promise<VideoEditState> {
  const volume = patch.volume ?? 1;
  const segments = patch.out_sec != null && patch.out_sec > (patch.in_sec ?? 0)
    ? [{ start_sec: patch.in_sec ?? 0, end_sec: patch.out_sec }]
    : [];
  const res = await fetch(`${BASE}/videos/${encodeURIComponent(sha256)}/edit`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: patch.title ?? null,
      recipe: {
        schema_version: 1,
        segments,
        mute_audio: patch.muted ?? false,
        gain_db: volume <= 0 ? -60 : 20 * Math.log10(volume),
        stabilize: { enabled: patch.stabilization ?? false, strength: "medium", crop: 0.08 },
        export: { quality: "balanced", container: "mp4" },
        source_sha256: sha256,
      },
    }),
  });
  if (!res.ok) throw await detailError(res, "saveVideoEdit");
  if (patch.tags) await putVideoTags(sha256, patch.tags);
  return getVideoEdit(sha256);
}

/** Store a rating used by the review workflow. */
export async function updateVideoRating(sha256: string, rating: number | null): Promise<VideoRating> {
  const res = await fetch(`${BASE}/videos/${encodeURIComponent(sha256)}/rating`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rating }),
  });
  if (!res.ok) throw await detailError(res, "updateVideoRating");
  return res.json();
}

/** Read library tags for one video. */
export async function getVideoTags(sha256: string): Promise<VideoTagsResponse> {
  const res = await fetch(`${BASE}/videos/${encodeURIComponent(sha256)}/tags`);
  if (!res.ok) throw await detailError(res, "getVideoTags");
  return res.json();
}

/** Replace library tags for one video. */
export async function putVideoTags(sha256: string, tags: string[]): Promise<VideoTagsResponse> {
  const res = await fetch(`${BASE}/videos/${encodeURIComponent(sha256)}/tags`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tags }),
  });
  if (!res.ok) throw await detailError(res, "putVideoTags");
  return res.json();
}

/** Batch tag update used by the library selection toolbar. */
export async function batchOrganizeVideos(shas: string[], patch: { tags?: string[] }): Promise<{ updated: number }> {
  await Promise.all(shas.map((sha256) => putVideoTags(sha256, patch.tags ?? [])));
  return { updated: shas.length };
}

/** Request audio analysis for a video. */
export async function analyzeVideoAudio(sha256: string): Promise<VideoExportJob> {
  const res = await fetch(`${BASE}/videos/${encodeURIComponent(sha256)}/audio/analyze`, { method: "POST" });
  if (!res.ok) throw await detailError(res, "analyzeVideoAudio");
  const body = await res.json();
  return { id: String(body.job_id), status: "queued", progress: 0 };
}

/** Request a browser-friendly proxy when the source codec cannot play inline. */
export async function createVideoProxy(sha256: string): Promise<VideoExportJob> {
  const res = await fetch(`${BASE}/videos/${encodeURIComponent(sha256)}/proxy`, { method: "POST" });
  if (!res.ok) throw await detailError(res, "createVideoProxy");
  const body = await res.json();
  return { id: String(body.job_id), status: "queued", progress: 0 };
}

/** Start a server-side export job for the current edit state. */
export async function startVideoExport(
  sha256: string,
  body: { in_sec: number; out_sec: number | null; stabilization: boolean; muted: boolean; volume: number },
): Promise<VideoExportJob> {
  const res = await fetch(`${BASE}/videos/${encodeURIComponent(sha256)}/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mode: body.stabilization || body.muted || body.volume !== 1 ? "precise" : "fast",
      recipe: {
        schema_version: 1,
        segments: body.out_sec != null && body.out_sec > body.in_sec
          ? [{ start_sec: body.in_sec, end_sec: body.out_sec }]
          : [],
        mute_audio: body.muted,
        gain_db: body.volume <= 0 ? -60 : 20 * Math.log10(body.volume),
        stabilize: { enabled: body.stabilization, strength: "medium", crop: 0.08 },
        export: { quality: "balanced", container: "mp4" },
        source_sha256: sha256,
      },
    }),
  });
  if (!res.ok) throw await detailError(res, "startVideoExport");
  const response = await res.json();
  return { id: String(response.job_id), status: "queued", progress: 0 };
}

/** Poll a video export job. */
export async function getVideoExportStatus(jobId: string): Promise<VideoExportJob> {
  const res = await fetch(`${BASE}/media/jobs/${encodeURIComponent(jobId)}`);
  if (!res.ok) throw await detailError(res, "getVideoExportStatus");
  const body = await res.json();
  const status = body.status === "completed" ? "complete" : body.status;
  return {
    id: String(body.id),
    status,
    progress: Math.max(0, Math.min(1, Number(body.progress ?? 0) / 100)),
    download_url: status === "complete" && body.output_path
      ? `${BASE}/media/jobs/${encodeURIComponent(jobId)}/output`
      : null,
    error: body.error ?? null,
  };
}

/** Request cancellation of an in-flight export job. */
export async function cancelVideoExport(jobId: string): Promise<void> {
  const res = await fetch(`${BASE}/media/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" });
  if (!res.ok) throw await detailError(res, "cancelVideoExport");
}

/** Create a named collection for organizing a selection of videos. */
export async function listVideoCollections(): Promise<VideoCollection[]> {
  const res = await fetch(`${BASE}/video-collections`);
  if (!res.ok) throw await detailError(res, "listVideoCollections");
  const body = await res.json();
  return (body.collections ?? []).map((item: { id: number; name: string; count?: number }) => ({
    id: String(item.id),
    name: item.name,
    video_count: item.count ?? 0,
  }));
}

/** Create a named collection for organizing a selection of videos. */
export async function createVideoCollection(name: string): Promise<VideoCollection> {
  const res = await fetch(`${BASE}/video-collections`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw await detailError(res, "createVideoCollection");
  const item = await res.json();
  return { id: String(item.id), name: item.name, video_count: item.count ?? 0 };
}

/** Add a video to a collection. */
export async function addVideoToCollection(collectionId: string, sha256: string): Promise<void> {
  const res = await fetch(`${BASE}/video-collections/${encodeURIComponent(collectionId)}/videos/${encodeURIComponent(sha256)}`, { method: "POST" });
  if (!res.ok) throw await detailError(res, "addVideoToCollection");
}

/** Remove a video from a collection. */
export async function removeVideoFromCollection(collectionId: string, sha256: string): Promise<void> {
  const res = await fetch(`${BASE}/video-collections/${encodeURIComponent(collectionId)}/videos/${encodeURIComponent(sha256)}`, { method: "DELETE" });
  if (!res.ok) throw await detailError(res, "removeVideoFromCollection");
}
