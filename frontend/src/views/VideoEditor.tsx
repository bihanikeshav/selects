import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import {
  analyzeVideoAudio,
  cancelVideoExport,
  createVideoProxy,
  getVideoEdit,
  getVideoExportStatus,
  getVideoFrames,
  getVideoPlayback,
  getVideoTimeline,
  listVideos,
  saveVideoEdit,
  startVideoExport,
  updateVideoRating,
} from "../api/videos";
import type { VideoEditState, VideoExportJob, VideoFrame, VideoFramesResponse, VideoItem, VideoTimelineResponse } from "../api/videos";
import VideoTimeline from "../components/VideoTimeline";
import type { VideoSearchHit } from "../components/VideoTimeline";
import "../editor/VideoEditor.css";

type InspectorTab = "organize" | "adjust" | "info";

function Icon({ name }: { name: "back" | "forward" | "play" | "pause" | "close" | "save" | "download" | "scissors" | "sliders" | "info" | "volume" | "mute" | "check" | "x" }) {
  const paths: Record<typeof name, ReactNode> = {
    back: <><path d="M5 12h14" /><path d="m11 6-6 6 6 6" /></>,
    forward: <><path d="M19 12H5" /><path d="m13 6 6 6-6 6" /></>,
    play: <path d="m9 7 8 5-8 5V7Z" />,
    pause: <><path d="M9 6v12M15 6v12" /></>,
    close: <><path d="m6 6 12 12M18 6 6 18" /></>,
    save: <><path d="M5 4h12l2 2v14H5V4Z" /><path d="M8 4v5h8V4M8 20v-6h8v6" /></>,
    download: <><path d="M12 4v11" /><path d="m8 11 4 4 4-4" /><path d="M5 20h14" /></>,
    scissors: <><circle cx="6" cy="7" r="2" /><circle cx="6" cy="17" r="2" /><path d="m8 8 10 8M8 16 18 8" /></>,
    sliders: <><path d="M4 6h6M14 6h6M4 12h2M10 12h10M4 18h10M18 18h2" /><circle cx="12" cy="6" r="2" /><circle cx="8" cy="12" r="2" /><circle cx="16" cy="18" r="2" /></>,
    info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></>,
    volume: <><path d="M4 10v4h4l5 4V6l-5 4H4Z" /><path d="M16 9a5 5 0 0 1 0 6M18 6a9 9 0 0 1 0 12" /></>,
    mute: <><path d="M4 10v4h4l5 4V6l-5 4H4Z" /><path d="m18 9 4 6M22 9l-4 6" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    x: <><path d="m7 7 10 10M17 7 7 17" /></>,
  };
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

function formatTime(seconds: number): string {
  const safe = Math.max(0, seconds);
  return `${Math.floor(safe / 60)}:${Math.floor(safe % 60).toString().padStart(2, "0")}`;
}

function defaultEdit(sha: string, duration: number): VideoEditState {
  return { sha256: sha, in_sec: 0, out_sec: duration || null, stabilization: false, muted: false, volume: 1, title: "", tags: [] };
}

function tagList(value: string): string[] {
  return value.split(",").map((tag) => tag.trim()).filter(Boolean);
}

const REVIEW_TAGS = new Set(["review", "kept", "archived"]);

function reviewState(tags: string[]): "new" | "review" | "kept" | "archived" {
  return (tags.find((tag) => REVIEW_TAGS.has(tag)) as "review" | "kept" | "archived" | undefined) ?? "new";
}

export default function VideoEditor() {
  const navigate = useNavigate();
  const { sha = "" } = useParams();
  const [searchParams] = useSearchParams();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [video, setVideo] = useState<VideoItem | null>(null);
  const [frames, setFrames] = useState<VideoFramesResponse | null>(null);
  const [timeline, setTimeline] = useState<VideoTimelineResponse | null>(null);
  const [duration, setDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [edit, setEdit] = useState<VideoEditState>(() => defaultEdit(sha, 0));
  const [rating, setRating] = useState<number | null>(null);
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("organize");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exportJob, setExportJob] = useState<VideoExportJob | null>(null);
  const [playbackJob, setPlaybackJob] = useState<VideoExportJob | null>(null);
  const [audioJob, setAudioJob] = useState<VideoExportJob | null>(null);
  const [playbackSrc, setPlaybackSrc] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const requestedTime = useMemo(() => {
    const value = Number(searchParams.get("t"));
    return Number.isFinite(value) && value >= 0 ? value : null;
  }, [searchParams]);
  const initialSeekApplied = useRef(false);

  const outPoint = edit.out_sec ?? duration;
  const hasChanges = edit.in_sec > 0 || (edit.out_sec != null && duration > 0 && edit.out_sec < duration) || edit.stabilization || edit.muted || edit.volume !== 1 || edit.title.length > 0 || edit.tags.length > 0;

  const loadVideo = useCallback(async () => {
    if (!sha) return;
    setLoading(true);
    setError(null);
    try {
      const [videosResult, framesResult, timelineResult, editResult] = await Promise.allSettled([listVideos(), getVideoFrames(sha), getVideoTimeline(sha), getVideoEdit(sha)]);
      if (videosResult.status === "fulfilled") {
        const match = videosResult.value.videos.find((item) => item.sha256 === sha) ?? null;
        setVideo(match);
        setRating(match?.rating ?? null);
      }
      if (framesResult.status === "fulfilled") {
        setFrames(framesResult.value);
        if (framesResult.value.duration_sec != null) setDuration(framesResult.value.duration_sec);
      }
      if (timelineResult.status === "fulfilled") {
        setTimeline(timelineResult.value);
        if (timelineResult.value.duration_sec != null) setDuration(timelineResult.value.duration_sec);
      }
      if (editResult.status === "fulfilled") setEdit(editResult.value);
      try {
        const playback = await getVideoPlayback(sha);
        if (playback.proxy_required && !playback.proxy_ready) {
          setPlaybackJob(await createVideoProxy(sha));
        } else {
          setPlaybackSrc(playback.source_url);
        }
      } catch (playbackError) {
        setError(playbackError instanceof Error ? playbackError.message : String(playbackError));
      }
      if (videosResult.status === "rejected" && framesResult.status === "rejected") setError("Could not load video details. Return to the library and try again.");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setLoading(false); }
  }, [sha]);

  useEffect(() => {
    if (!playbackJob || !["queued", "running"].includes(playbackJob.status)) return;
    const timer = window.setInterval(async () => {
      try {
        const next = await getVideoExportStatus(playbackJob.id);
        setPlaybackJob(next);
        if (next.status === "complete") {
          const playback = await getVideoPlayback(sha);
          setPlaybackSrc(playback.source_url);
        } else if (next.status === "failed") {
          setError(next.error ?? "Could not prepare this video for playback.");
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    }, 700);
    return () => window.clearInterval(timer);
  }, [playbackJob, sha]);

  useEffect(() => { void loadVideo(); }, [loadVideo]);
  useEffect(() => {
    if (duration > 0 && edit.out_sec == null) setEdit((current) => ({ ...current, out_sec: duration }));
  }, [duration, edit.out_sec]);

  const seek = useCallback((seconds: number) => {
    const next = Math.min(Math.max(seconds, 0), duration || 0);
    if (videoRef.current) videoRef.current.currentTime = next;
    setCurrentTime(next);
  }, [duration]);

  useEffect(() => {
    if (requestedTime == null || !playbackSrc || duration <= 0 || initialSeekApplied.current) return;
    initialSeekApplied.current = true;
    seek(requestedTime);
  }, [duration, playbackSrc, requestedTime, seek]);

  const togglePlayback = useCallback(() => {
    const element = videoRef.current;
    if (!element) return;
    if (element.paused) void element.play().then(() => setPlaying(true)).catch(() => setError("Playback is not available for this source yet."));
    else { element.pause(); setPlaying(false); }
  }, []);

  const handleShortcut = useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement;
    if (["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(target.tagName)) return;
    const key = event.key.toLowerCase();
    if (key === "j") seek(currentTime - 5);
    else if (key === "k" || event.key === " ") { event.preventDefault(); togglePlayback(); }
    else if (key === "l") seek(currentTime + 5);
    else if (key === "i") setEdit((current) => ({ ...current, in_sec: currentTime }));
    else if (key === "o") setEdit((current) => ({ ...current, out_sec: currentTime }));
    else if (event.key === "ArrowLeft") seek(currentTime - (event.shiftKey ? 10 : 1));
    else if (event.key === "ArrowRight") seek(currentTime + (event.shiftKey ? 10 : 1));
    else if (event.key === "Home") seek(0);
    else if (event.key === "End") seek(duration);
    else return;
    event.preventDefault();
  }, [currentTime, duration, seek, togglePlayback]);

  function updateTrim(inPoint: number, out: number) {
    setEdit((current) => ({ ...current, in_sec: Math.max(0, inPoint), out_sec: Math.min(duration, out) }));
    setSaved(false);
  }

  async function saveChanges() {
    setSaving(true); setError(null);
    try {
      const savedEdit = await saveVideoEdit(sha, { in_sec: edit.in_sec, out_sec: edit.out_sec, stabilization: edit.stabilization, muted: edit.muted, volume: edit.volume, title: edit.title, tags: edit.tags });
      await updateVideoRating(sha, rating);
      setEdit(savedEdit); setSaved(true);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setSaving(false); }
  }

  async function beginExport() {
    setExportError(null);
    try {
      const job = await startVideoExport(sha, { in_sec: edit.in_sec, out_sec: edit.out_sec, stabilization: edit.stabilization, muted: edit.muted, volume: edit.volume });
      setExportJob(job);
    } catch (e) { setExportError(e instanceof Error ? e.message : String(e)); }
  }

  useEffect(() => {
    if (!exportJob || !["queued", "running"].includes(exportJob.status)) return;
    const timer = window.setInterval(async () => {
      try { setExportJob(await getVideoExportStatus(exportJob.id)); }
      catch (e) { setExportError(e instanceof Error ? e.message : String(e)); }
    }, 700);
    return () => window.clearInterval(timer);
  }, [exportJob]);

  async function stopExport() {
    if (!exportJob) return;
    try { await cancelVideoExport(exportJob.id); setExportJob({ ...exportJob, status: "cancelled" }); }
    catch (e) { setExportError(e instanceof Error ? e.message : String(e)); }
  }

  async function beginAudioAnalysis() {
    try {
      setAudioJob(await analyzeVideoAudio(sha));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    if (!audioJob || !["queued", "running"].includes(audioJob.status)) return;
    const timer = window.setInterval(async () => {
      try {
        const next = await getVideoExportStatus(audioJob.id);
        setAudioJob(next);
        if (next.status === "complete") setTimeline(await getVideoTimeline(sha));
        if (next.status === "failed") setError(next.error ?? "Audio analysis failed.");
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    }, 700);
    return () => window.clearInterval(timer);
  }, [audioJob, sha]);

  const searchHits = useMemo<VideoSearchHit[]>(() => (timeline?.search_hits ?? []).map((hit) => ({ start: hit.start, end: hit.end, label: hit.label })), [timeline]);
  const sourceFrames: VideoFrame[] = timeline?.frames ?? frames?.frames ?? [];
  const title = edit.title || video?.name || frames?.path.replace(/\\/g, "/").split("/").pop() || "Video editor";
  const isExporting = exportJob != null && ["queued", "running"].includes(exportJob.status);

  return (
    <div ref={rootRef} className="video-editor" tabIndex={-1} onKeyDown={handleShortcut}>
      <header className="video-editor-topbar">
        <button type="button" className="video-editor-back" onClick={() => navigate("/videos")} aria-label="Back to videos"><Icon name="back" /><span>Videos</span></button>
        <div className="video-editor-heading"><strong>{title}</strong><span>{video?.format?.toUpperCase() ?? "VIDEO"} / {formatTime(duration)}</span></div>
        <div className="video-editor-top-actions"><span className="video-editor-shortcuts">J K L / I O</span><button type="button" className="btn btn-outlined" onClick={() => void saveChanges()} disabled={saving || loading}><Icon name="save" /> {saving ? "Saving..." : saved ? "Saved" : "Save changes"}</button><button type="button" className="btn btn-filled" onClick={() => void beginExport()} disabled={isExporting || loading}><Icon name="download" /> {isExporting ? "Exporting..." : "Export"}</button></div>
      </header>
      <div className="video-editor-main">
        <section className="video-editor-stage" aria-label="Video preview">
          {loading && <div className="video-editor-loading">Loading video...</div>}
          {playbackSrc ? <video ref={videoRef} className="video-editor-player" src={playbackSrc} preload="metadata" playsInline aria-label={`Preview ${title}`} onLoadedMetadata={(event) => { if (event.currentTarget.duration && duration === 0) setDuration(event.currentTarget.duration); }} onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onError={() => setError("Playback is not available for this source yet.")} /> : <div className="video-editor-loading">{playbackJob ? "Preparing playback..." : "Loading video..."}</div>}
          <div className="video-editor-transport"><button type="button" className="icon-btn" onClick={() => seek(currentTime - 5)} aria-label="Seek back five seconds"><Icon name="back" /></button><button type="button" className="video-editor-play" onClick={togglePlayback} aria-label={playing ? "Pause" : "Play"}><Icon name={playing ? "pause" : "play"} /></button><button type="button" className="icon-btn" onClick={() => seek(currentTime + 5)} aria-label="Seek forward five seconds"><Icon name="forward" /></button><span className="video-editor-time">{formatTime(currentTime)} / {formatTime(duration)}</span><span className="video-editor-transport-hint">J / K / L to step and play</span></div>
          {error && <div className="video-editor-error" role="alert">{error}</div>}
        </section>
        <aside className="video-editor-inspector" aria-label="Video inspector">
          <div className="video-editor-tabs" role="tablist">{(["organize", "adjust", "info"] as InspectorTab[]).map((tab) => <button type="button" key={tab} role="tab" aria-selected={inspectorTab === tab} className={inspectorTab === tab ? "is-active" : ""} onClick={() => setInspectorTab(tab)}><Icon name={tab === "organize" ? "scissors" : tab === "adjust" ? "sliders" : "info"} /> {tab === "organize" ? "Organize" : tab === "adjust" ? "Adjust" : "Info"}</button>)}</div>
          {inspectorTab === "organize" && <div className="video-editor-panel"><label className="video-editor-field"><span>Title</span><input value={edit.title} placeholder={video?.name ?? "Untitled video"} onChange={(event) => { setEdit((current) => ({ ...current, title: event.target.value })); setSaved(false); }} /></label><label className="video-editor-field"><span>Tags</span><input value={edit.tags.join(", ")} placeholder="travel, people, moment" onChange={(event) => { setEdit((current) => ({ ...current, tags: tagList(event.target.value) })); setSaved(false); }} /></label><div className="video-editor-field"><span>Rating</span><div className="video-editor-rating" role="group" aria-label="Video rating"><button type="button" className={rating === null ? "is-active" : ""} onClick={() => { setRating(null); setSaved(false); }}>None</button>{[1, 2, 3, 4, 5].map((value) => <button type="button" key={value} className={rating === value ? "is-active" : ""} aria-label={`Rate ${value} out of 5`} aria-pressed={rating === value} onClick={() => { setRating(value); setSaved(false); }}>{value}</button>)}</div></div><label className="video-editor-field"><span>Review status</span><select value={reviewState(edit.tags)} onChange={(event) => { const next = event.target.value; setEdit((current) => ({ ...current, tags: [...current.tags.filter((tag) => !REVIEW_TAGS.has(tag)), ...(next === "new" ? [] : [next])] })); setSaved(false); }}><option value="new">New</option><option value="review">Needs review</option><option value="kept">Kept</option><option value="archived">Archived</option></select></label><div className="video-editor-note">Edits stay non-destructive until you export a new file.</div></div>}
          {inspectorTab === "adjust" && <div className="video-editor-panel"><div className="video-editor-section-title">Sound</div><label className="video-editor-toggle"><input type="checkbox" checked={edit.muted} onChange={(event) => setEdit((current) => ({ ...current, muted: event.target.checked }))} /><span><Icon name={edit.muted ? "mute" : "volume"} /> Mute audio</span></label><label className="video-editor-range"><span>Volume <b>{Math.round(edit.volume * 100)}%</b></span><input type="range" min="0" max="1" step="0.05" value={edit.volume} onChange={(event) => setEdit((current) => ({ ...current, volume: Number(event.target.value) }))} /></label><button type="button" className="btn btn-outlined" onClick={() => void beginAudioAnalysis()} disabled={audioJob != null && ["queued", "running"].includes(audioJob.status)}>{audioJob != null && ["queued", "running"].includes(audioJob.status) ? "Analyzing audio..." : timeline?.audio ? "Refresh audio analysis" : "Analyze audio"}</button><div className="video-editor-section-title">Stabilization</div><label className="video-editor-toggle"><input type="checkbox" checked={edit.stabilization} onChange={(event) => setEdit((current) => ({ ...current, stabilization: event.target.checked }))} /><span><Icon name="check" /> Apply on export</span></label><button type="button" className="btn btn-text video-editor-reset" onClick={() => setEdit((current) => ({ ...current, in_sec: 0, out_sec: duration, stabilization: false, muted: false, volume: 1 }))}>Reset adjustments</button></div>}
          {inspectorTab === "info" && <div className="video-editor-panel"><dl className="video-editor-details"><div><dt>Source</dt><dd>{video?.path ?? frames?.path ?? "Unavailable"}</dd></div><div><dt>Dimensions</dt><dd>{video?.width && video?.height ? `${video.width} x ${video.height}` : "Unknown"}</dd></div><div><dt>Frame rate</dt><dd>{video?.fps ? `${video.fps.toFixed(2)} fps` : "Unknown"}</dd></div><div><dt>Analysis</dt><dd>{frames ? `${frames.frames.length} sampled frames` : "Not available"}</dd></div></dl></div>}
        </aside>
      </div>
      <VideoTimeline duration={duration} currentTime={currentTime} inPoint={edit.in_sec} outPoint={outPoint} frames={sourceFrames} highlights={timeline?.highlights ?? frames?.highlights ?? []} waveform={timeline?.waveform} searchHits={searchHits} onSeek={seek} onTrimChange={updateTrim} />
      <footer className="video-editor-footer"><span className="video-editor-footer-copy"><Icon name="scissors" /> {hasChanges ? "Unsaved edit" : "Original range"}</span><span>Trim handles are keyboard accessible. Press I and O to set the export range.</span>{exportJob?.status === "complete" && (exportJob.download_url ? <a className="btn btn-tonal btn-sm" href={exportJob.download_url} download><Icon name="download" /> Download export</a> : <span className="video-editor-export-ready"><Icon name="check" /> Export ready</span>)}{isExporting && <><div className="video-editor-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(exportJob.progress * 100)}><span style={{ width: `${Math.max(4, exportJob.progress * 100)}%` }} /></div><button type="button" className="btn btn-text btn-sm" onClick={() => void stopExport()}>Cancel</button></>}{exportError && <span className="video-editor-export-error">{exportError}</span>}</footer>
    </div>
  );
}
