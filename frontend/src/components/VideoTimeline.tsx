import { useMemo, useRef } from "react";

import type { VideoFrame, VideoHighlight } from "../api/videos";
import "./VideoTimeline.css";

export interface VideoSearchHit {
  start: number;
  end: number;
  label: string;
}

interface VideoTimelineProps {
  duration: number;
  currentTime: number;
  inPoint: number;
  outPoint: number;
  frames: VideoFrame[];
  highlights: VideoHighlight[];
  waveform?: number[];
  searchHits?: VideoSearchHit[];
  onSeek: (seconds: number) => void;
  onTrimChange: (inPoint: number, outPoint: number) => void;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function timeLabel(seconds: number): string {
  const safe = Math.max(0, seconds);
  const minutes = Math.floor(safe / 60);
  const wholeSeconds = Math.floor(safe % 60);
  return `${minutes}:${wholeSeconds.toString().padStart(2, "0")}`;
}

function percent(seconds: number, duration: number): number {
  return duration > 0 ? (seconds / duration) * 100 : 0;
}

/** Filmstrip, waveform, highlights and trim handles in one keyboard-friendly timeline. */
export default function VideoTimeline({
  duration,
  currentTime,
  inPoint,
  outPoint,
  frames,
  highlights,
  waveform = [],
  searchHits = [],
  onSeek,
  onTrimChange,
}: VideoTimelineProps) {
  const trackRef = useRef<HTMLDivElement | null>(null);
  const safeDuration = Math.max(duration, 0.1);
  const activeFrameIndex = useMemo(() => {
    if (frames.length === 0) return -1;
    return frames.reduce((best, frame, index) => {
      const bestDistance = Math.abs(frames[best]?.t_sec - currentTime);
      return Math.abs(frame.t_sec - currentTime) < bestDistance ? index : best;
    }, 0);
  }, [currentTime, frames]);

  function seekFromPointer(event: React.PointerEvent<HTMLDivElement>) {
    if (!trackRef.current) return;
    const rect = trackRef.current.getBoundingClientRect();
    const ratio = clamp((event.clientX - rect.left) / rect.width, 0, 1);
    onSeek(ratio * safeDuration);
  }

  function moveHandle(handle: "in" | "out", event: React.PointerEvent<HTMLDivElement>) {
    event.currentTarget.setPointerCapture(event.pointerId);
    const move = (moveEvent: PointerEvent) => {
      if (!trackRef.current) return;
      const rect = trackRef.current.getBoundingClientRect();
      const next = clamp(((moveEvent.clientX - rect.left) / rect.width) * safeDuration, 0, safeDuration);
      if (handle === "in") onTrimChange(Math.min(next, outPoint - 0.05), outPoint);
      else onTrimChange(inPoint, Math.max(next, inPoint + 0.05));
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop, { once: true });
  }

  function onHandleKeyDown(handle: "in" | "out", event: React.KeyboardEvent<HTMLDivElement>) {
    const step = event.shiftKey ? 1 : 0.1;
    const current = handle === "in" ? inPoint : outPoint;
    let next = current;
    if (event.key === "ArrowLeft" || event.key === "ArrowDown") next -= step;
    if (event.key === "ArrowRight" || event.key === "ArrowUp") next += step;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = safeDuration;
    if (next === current) return;
    event.preventDefault();
    if (handle === "in") onTrimChange(clamp(Math.min(next, outPoint - 0.05), 0, safeDuration), outPoint);
    else onTrimChange(inPoint, clamp(Math.max(next, inPoint + 0.05), 0, safeDuration));
  }

  return (
    <section className="video-timeline" aria-label="Video timeline">
      <div className="video-timeline-scale" aria-hidden="true">
        <span>0:00</span>
        <span>{timeLabel(safeDuration / 2)}</span>
        <span>{timeLabel(safeDuration)}</span>
      </div>
      <div
        className="video-timeline-track"
        ref={trackRef}
        onPointerDown={seekFromPointer}
        role="presentation"
      >
        <div className="video-timeline-wave" aria-hidden="true">
          {Array.from({ length: 64 }, (_, index) => {
            const frame = frames[index % Math.max(frames.length, 1)];
            const sample = waveform.length > 0 ? waveform[index % waveform.length] : frame?.quality ?? 0.46 + ((index * 17) % 28) / 100;
            const amplitude = clamp(sample, 0.08, 1);
            return <span key={index} style={{ height: `${16 + Math.round(amplitude * 52)}%` }} />;
          })}
        </div>
        <div className="video-timeline-filmstrip" aria-hidden="true">
          {frames.map((frame, index) => (
            <img
              key={`${frame.index}-${index}`}
              src={frame.url}
              alt=""
              className={index === activeFrameIndex ? "is-current" : ""}
            />
          ))}
        </div>
        <div className="video-timeline-overlays" aria-hidden="true">
          {highlights.map((highlight, index) => (
            <span
              className="video-timeline-highlight"
              key={`highlight-${index}`}
              style={{ left: `${percent(highlight.start, safeDuration)}%`, width: `${percent(highlight.end - highlight.start, safeDuration)}%` }}
            />
          ))}
          {searchHits.map((hit, index) => (
            <span
              className="video-timeline-search-hit"
              key={`hit-${index}`}
              title={hit.label}
              style={{ left: `${percent(hit.start, safeDuration)}%`, width: `${percent(hit.end - hit.start, safeDuration)}%` }}
            />
          ))}
          <span
            className="video-timeline-trim-window"
            style={{ left: `${percent(inPoint, safeDuration)}%`, width: `${percent(outPoint - inPoint, safeDuration)}%` }}
          />
          <span className="video-timeline-playhead" style={{ left: `${percent(currentTime, safeDuration)}%` }} />
        </div>
        <div
          className="video-timeline-handle video-timeline-handle-in"
          style={{ left: `${percent(inPoint, safeDuration)}%` }}
          role="slider"
          tabIndex={0}
          aria-label="Trim start"
          aria-valuemin={0}
          aria-valuemax={safeDuration}
          aria-valuenow={inPoint}
          aria-valuetext={timeLabel(inPoint)}
          onPointerDown={(event) => {
            event.stopPropagation();
            moveHandle("in", event);
          }}
          onKeyDown={(event) => onHandleKeyDown("in", event)}
        >
          <span />
        </div>
        <div
          className="video-timeline-handle video-timeline-handle-out"
          style={{ left: `${percent(outPoint, safeDuration)}%` }}
          role="slider"
          tabIndex={0}
          aria-label="Trim end"
          aria-valuemin={0}
          aria-valuemax={safeDuration}
          aria-valuenow={outPoint}
          aria-valuetext={timeLabel(outPoint)}
          onPointerDown={(event) => {
            event.stopPropagation();
            moveHandle("out", event);
          }}
          onKeyDown={(event) => onHandleKeyDown("out", event)}
        >
          <span />
        </div>
      </div>
      <div className="video-timeline-readout">
        <span className="video-timeline-trim-copy">Trim {timeLabel(inPoint)} to {timeLabel(outPoint)}</span>
        <span>{timeLabel(currentTime)} / {timeLabel(safeDuration)}</span>
      </div>
      <div className="video-timeline-legend" aria-hidden="true">
        <span><i className="is-highlight" /> Highlights</span>
        <span><i className="is-search" /> Search hits</span>
        <span><i className="is-trim" /> Export range</span>
      </div>
    </section>
  );
}
