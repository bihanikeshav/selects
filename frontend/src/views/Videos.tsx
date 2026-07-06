import { useCallback, useEffect, useRef, useState } from "react";

import {
  getVideoFrames,
  listVideos,
  processVideos,
  videoProcessStatus,
} from "../api/videos";
import type { VideoFramesResponse, VideoItem, VideoListResponse } from "../api/videos";
import Rail from "../components/Rail";
import "../components/Videos.css";

function fmtDuration(sec: number | null): string {
  if (sec == null || !isFinite(sec) || sec <= 0) return "–:––";
  const s = Math.round(sec);
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${r.toString().padStart(2, "0")}`;
}

function fmtTimestamp(sec: number): string {
  const s = Math.floor(sec);
  const m = Math.floor(s / 60);
  const r = s % 60;
  const tenths = Math.floor((sec - s) * 10);
  return `${m}:${r.toString().padStart(2, "0")}.${tenths}`;
}

function VideoCard({ video, onOpen }: { video: VideoItem; onOpen: (v: VideoItem) => void }) {
  const clickable = video.processed && video.sha256 != null && video.sampled_frames > 0;
  return (
    <button
      className={"video-card" + (video.dead_footage ? " is-dead" : "")}
      onClick={() => clickable && onOpen(video)}
      disabled={!clickable}
      title={video.path}
    >
      <div className="video-card-thumb">
        {video.thumb_url ? (
          <img src={video.thumb_url} alt="" loading="lazy" />
        ) : (
          <div className="video-card-thumb-fallback" aria-hidden="true">▶</div>
        )}
        <span className="video-duration-badge">{fmtDuration(video.duration_sec)}</span>
        {video.dead_footage && (
          <span className="video-dead-badge" title="Most sampled frames are blurry or dark">
            Dead footage
          </span>
        )}
      </div>
      <div className="video-card-meta">
        <span className="video-card-name">{video.name}</span>
        <span className="video-card-sub">
          {video.processed ? (
            video.highlight_count > 0 ? (
              <span className="video-highlights-chip">
                ✦ {video.highlight_count} highlight{video.highlight_count === 1 ? "" : "s"}
              </span>
            ) : video.dead_footage ? (
              <span className="video-sub-muted">no usable segments</span>
            ) : (
              <span className="video-sub-muted">no highlights</span>
            )
          ) : (
            <span className="video-sub-muted">not analysed yet</span>
          )}
        </span>
      </div>
    </button>
  );
}

function QualityBar({ value, good }: { value: number; good: boolean }) {
  const pct = Math.max(3, Math.min(100, Math.round(value * 100)));
  return (
    <div className="video-qbar" role="img" aria-label={`quality ${pct}%`}>
      <div
        className={"video-qbar-fill" + (good ? " is-good" : " is-bad")}
        style={{ height: `${pct}%` }}
      />
    </div>
  );
}

function Filmstrip({
  data,
  onClose,
}: {
  data: VideoFramesResponse;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const name = data.path.replace(/\\/g, "/").split("/").filter(Boolean).pop() ?? data.path;

  return (
    <div className="video-strip-overlay" onClick={onClose}>
      <div className="video-strip-panel" onClick={(e) => e.stopPropagation()}>
        <header className="video-strip-header">
          <div>
            <h2>{name}</h2>
            <span className="video-strip-sub">
              {fmtDuration(data.duration_sec)}
              {data.dead_footage ? " · dead footage" : ""}
              {data.highlights.length > 0
                ? ` · ${data.highlights.length} highlight${data.highlights.length === 1 ? "" : "s"}`
                : ""}
            </span>
          </div>
          <button className="btn btn-outlined" onClick={onClose}>
            Close
          </button>
        </header>

        {data.highlights.length > 0 && (
          <div className="video-strip-highlights">
            {data.highlights.map((h, i) => (
              <span key={i} className="video-highlights-chip">
                ✦ {fmtTimestamp(h.start)} – {fmtTimestamp(h.end)}
              </span>
            ))}
          </div>
        )}

        <div className="video-strip-frames">
          {data.frames.map((f) => (
            <figure
              key={f.index}
              className={
                "video-strip-frame" +
                (f.index === data.best_frame_index ? " is-best" : "") +
                (f.good ? "" : " is-bad")
              }
            >
              <div className="video-strip-frame-row">
                <img src={f.url} alt={`frame at ${fmtTimestamp(f.t_sec)}`} loading="lazy" />
                <QualityBar value={f.quality} good={f.good} />
              </div>
              <figcaption>
                <span>{fmtTimestamp(f.t_sec)}</span>
                {f.index === data.best_frame_index && (
                  <span className="video-best-badge">best</span>
                )}
              </figcaption>
            </figure>
          ))}
        </div>
      </div>
    </div>
  );
}

export default function Videos() {
  const [result, setResult] = useState<VideoListResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [analysing, setAnalysing] = useState(false);
  const [strip, setStrip] = useState<VideoFramesResponse | null>(null);
  const pollRef = useRef<number | null>(null);

  const load = useCallback(async () => {
    try {
      setResult(await listVideos());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const pollAnalysis = useCallback(async () => {
    try {
      const st = await videoProcessStatus();
      if (st.error) setError(st.error);
      if (!st.running) {
        setAnalysing(false);
        await load();
      }
    } catch {
      setAnalysing(false);
    }
  }, [load]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (analysing) {
      if (pollRef.current === null) {
        pollRef.current = window.setInterval(pollAnalysis, 2000);
      }
    } else if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
    return () => {
      if (pollRef.current !== null) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [analysing, pollAnalysis]);

  async function onAnalyse() {
    setError(null);
    setAnalysing(true);
    try {
      await processVideos();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setAnalysing(false);
    }
  }

  async function onOpen(v: VideoItem) {
    if (!v.sha256) return;
    try {
      setStrip(await getVideoFrames(v.sha256));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const videos = result?.videos ?? [];
  const pending = result ? result.total - result.processed : 0;

  return (
    <div className="app">
      <Rail />
      <div className="workspace">
        <div className="videos-wrap">
          <header className="videos-header">
            <div>
              <h1>Videos</h1>
              <span className="videos-sub">
                {result
                  ? `${result.total} video${result.total === 1 ? "" : "s"} · ${result.processed} analysed` +
                    (result.dead_footage_count > 0
                      ? ` · ${result.dead_footage_count} dead footage`
                      : "")
                  : "Loading…"}
              </span>
            </div>
            <button
              className="btn btn-outlined"
              onClick={onAnalyse}
              disabled={analysing || (result != null && pending === 0)}
            >
              {analysing ? "Analysing…" : pending > 0 ? `Analyse ${pending} pending` : "All analysed"}
            </button>
          </header>

          {error && <p className="videos-error">{error}</p>}

          {result && videos.length === 0 && (
            <p className="videos-empty">
              No videos in this library. Drop MP4 / MOV / MKV files into the watched folder and
              re-index.
            </p>
          )}

          <div className="videos-grid">
            {videos.map((v) => (
              <VideoCard key={v.id} video={v} onOpen={onOpen} />
            ))}
          </div>
        </div>
      </div>

      {strip && <Filmstrip data={strip} onClose={() => setStrip(null)} />}
    </div>
  );
}
