import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { addVideoToCollection, batchOrganizeVideos, createVideoCollection, listVideoCollections, listVideos, processVideos, videoProcessStatus } from "../api/videos";
import type { VideoCollection, VideoItem, VideoListResponse } from "../api/videos";
import PageHeader from "../components/PageHeader";
import Rail from "../components/Rail";
import SkeletonGrid from "../components/SkeletonGrid";
import "../components/VideoLibrary.css";

type FilterKey = "all" | "highlights" | "review" | "edited" | "no-audio" | `collection:${string}`;
type SortKey = "recent" | "name" | "duration" | "highlights";
type ViewMode = "grid" | "list";

function Icon({ name }: { name: "play" | "list" | "grid" | "check" | "tag" | "sort" | "refresh" | "close" | "folder" | "plus" }) {
  const paths: Record<typeof name, JSX.Element> = {
    play: <path d="m9 7 8 5-8 5V7Z" />,
    list: <><path d="M8 6h12M8 12h12M8 18h12" /><path d="M4 6h.01M4 12h.01M4 18h.01" /></>,
    grid: <><rect x="4" y="4" width="6" height="6" rx="1" /><rect x="14" y="4" width="6" height="6" rx="1" /><rect x="4" y="14" width="6" height="6" rx="1" /><rect x="14" y="14" width="6" height="6" rx="1" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    tag: <><path d="m20 13-7 7-9-9V4h7l9 9Z" /><path d="M8 8h.01" /></>,
    sort: <><path d="M8 6h12M8 12h8M8 18h4" /><path d="M4 6h.01M4 12h.01M4 18h.01" /></>,
    refresh: <><path d="M20 11a8 8 0 0 0-14.7-4L4 9" /><path d="M4 4v5h5" /><path d="M4 13a8 8 0 0 0 14.7 4L20 15" /><path d="M20 20v-5h-5" /></>,
    close: <><path d="m6 6 12 12M18 6 6 18" /></>,
    folder: <><path d="M3 7h7l2 2h9v10H3V7Z" /><path d="M3 7V5h7l2 2" /></>,
    plus: <><path d="M12 5v14M5 12h14" /></>,
  };
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

function fmtDuration(sec: number | null): string {
  if (sec == null || !isFinite(sec) || sec < 0) return "--:--";
  const total = Math.round(sec);
  return `${Math.floor(total / 60)}:${(total % 60).toString().padStart(2, "0")}`;
}

function videoKey(video: VideoItem): string {
  return video.sha256 ?? `id-${video.id}`;
}

function isNeedsReview(video: VideoItem): boolean {
  return video.review_state === "review" || !video.processed || video.dead_footage === true;
}

function isEdited(video: VideoItem): boolean {
  return video.edited === true;
}

function VideoCard({ video, selected, list, onOpen, onSelect }: { video: VideoItem; selected: boolean; list: boolean; onOpen: (video: VideoItem) => void; onSelect: (video: VideoItem) => void }) {
  const openable = video.sha256 != null;
  const subtitle = video.highlight_count > 0 ? `${video.highlight_count} highlight${video.highlight_count === 1 ? "" : "s"}` : video.processed ? "No highlights" : "Not analysed";
  return (
    <article className={`video-library-card${selected ? " is-selected" : ""}${list ? " is-list" : ""}`}>
      <div className="video-library-thumb-wrap">
        <button type="button" className="video-library-thumb-button" onClick={() => openable && onOpen(video)} disabled={!openable} aria-label={`Open ${video.name}`}>
          {video.thumb_url ? <img src={video.thumb_url} alt="" loading="lazy" /> : <span className="video-library-thumb-empty"><Icon name="play" /></span>}
          <span className="video-library-play"><Icon name="play" /></span>
          <span className="video-library-duration">{fmtDuration(video.duration_sec)}</span>
        </button>
        <label className="video-library-check"><input type="checkbox" checked={selected} onChange={() => onSelect(video)} aria-label={`Select ${video.name}`} /><span><Icon name="check" /></span></label>
        {video.dead_footage && <span className="video-library-status is-warning">Mostly static</span>}
        {!video.processed && <span className="video-library-status">Needs analysis</span>}
      </div>
      <div className="video-library-card-body">
        <button type="button" className="video-library-name" onClick={() => openable && onOpen(video)} disabled={!openable} title={video.path}>{video.name}</button>
        <div className="video-library-meta"><span>{subtitle}</span>{video.has_audio === false && <span className="video-library-dot">No audio</span>}</div>
        {list && <div className="video-library-path">{video.path}</div>}
      </div>
    </article>
  );
}

export default function Videos() {
  const navigate = useNavigate();
  const [result, setResult] = useState<VideoListResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [analysing, setAnalysing] = useState(false);
  const [filter, setFilter] = useState<FilterKey>("all");
  const [sort, setSort] = useState<SortKey>("recent");
  const [viewMode, setViewMode] = useState<ViewMode>("grid");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [batchBusy, setBatchBusy] = useState(false);
  const [collections, setCollections] = useState<VideoCollection[]>([]);
  const [targetCollection, setTargetCollection] = useState("");
  const [collectionDraft, setCollectionDraft] = useState("");
  const pollRef = useRef<number | null>(null);

  const load = useCallback(async () => {
    try { setResult(await listVideos()); setError(null); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }, []);

  const pollAnalysis = useCallback(async () => {
    try {
      const status = await videoProcessStatus();
      if (status.error) setError(status.error);
      if (!status.running) { setAnalysing(false); await load(); }
    } catch { setAnalysing(false); }
  }, [load]);

  useEffect(() => {
    void load();
    void listVideoCollections().then(setCollections).catch(() => setCollections([]));
  }, [load]);
  useEffect(() => {
    if (analysing && pollRef.current === null) pollRef.current = window.setInterval(() => void pollAnalysis(), 2000);
    if (!analysing && pollRef.current !== null) { window.clearInterval(pollRef.current); pollRef.current = null; }
    return () => { if (pollRef.current !== null) window.clearInterval(pollRef.current); pollRef.current = null; };
  }, [analysing, pollAnalysis]);

  async function onAnalyse() {
    setError(null); setAnalysing(true);
    try { await processVideos(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); setAnalysing(false); }
  }

  const visibleVideos = useMemo(() => {
    const videos = (result?.videos ?? []).filter((video) => {
      if (filter === "highlights") return video.highlight_count > 0;
      if (filter === "review") return isNeedsReview(video);
      if (filter === "edited") return isEdited(video);
      if (filter === "no-audio") return video.has_audio === false;
      if (filter.startsWith("collection:")) return (video.collection_ids ?? []).includes(Number(filter.slice(11)));
      return true;
    });
    return [...videos].sort((a, b) => {
      if (sort === "name") return a.name.localeCompare(b.name);
      if (sort === "duration") return (b.duration_sec ?? 0) - (a.duration_sec ?? 0);
      if (sort === "highlights") return b.highlight_count - a.highlight_count;
      return (b.taken_at ?? "").localeCompare(a.taken_at ?? "");
    });
  }, [filter, result, sort]);

  function toggleSelected(video: VideoItem) {
    const key = videoKey(video);
    setSelected((current) => { const next = new Set(current); if (next.has(key)) next.delete(key); else next.add(key); return next; });
  }

  function selectVisible() {
    setSelected((current) => {
      const next = new Set(current);
      if (visibleVideos.every((video) => next.has(videoKey(video)))) visibleVideos.forEach((video) => next.delete(videoKey(video)));
      else visibleVideos.forEach((video) => next.add(videoKey(video)));
      return next;
    });
  }

  async function markSelectedForReview() {
    const shas = [...selected].filter((key) => !key.startsWith("id-"));
    if (shas.length === 0) return;
    setBatchBusy(true);
    try { await batchOrganizeVideos(shas, { tags: ["review"] }); await load(); setSelected(new Set()); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBatchBusy(false); }
  }

  async function addSelectedToCollection() {
    const shas = [...selected].filter((key) => !key.startsWith("id-"));
    if (!targetCollection || shas.length === 0) return;
    setBatchBusy(true);
    try {
      await Promise.all(shas.map((sha) => addVideoToCollection(targetCollection, sha)));
      const next = await listVideoCollections();
      setCollections(next);
      await load();
      setSelected(new Set());
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBatchBusy(false); }
  }

  async function makeCollection() {
    const name = collectionDraft.trim();
    if (!name) return;
    setBatchBusy(true);
    try {
      const created = await createVideoCollection(name);
      setCollections((current) => [...current, created].sort((a, b) => a.name.localeCompare(b.name)));
      setTargetCollection(created.id);
      setCollectionDraft("");
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBatchBusy(false); }
  }

  const pending = result ? result.total - result.processed : 0;
  const allVisibleSelected = visibleVideos.length > 0 && visibleVideos.every((video) => selected.has(videoKey(video)));
  const filterLabels: Array<[FilterKey, string]> = [["all", "All"], ["highlights", "Highlights"], ["review", "Needs review"], ["edited", "Edited"], ["no-audio", "No audio"]];

  return (
    <div className="app">
      <Rail />
      <div className="workspace video-library-workspace">
        <PageHeader
          context="Videos"
          title="Videos"
          subtitle={result ? `${result.total} videos / ${result.processed} analysed${result.dead_footage_count > 0 ? ` / ${result.dead_footage_count} flagged` : ""}` : "Loading..."}
          actions={<><button type="button" className="btn btn-outlined" onClick={() => void load()} disabled={!result}><Icon name="refresh" /> Refresh</button><button type="button" className="btn btn-filled" onClick={() => void onAnalyse()} disabled={analysing || (result != null && pending === 0)}>{analysing ? "Analysing..." : pending > 0 ? `Analyse ${pending}` : "All analysed"}</button></>}
          controls={<div className="video-library-controls"><div className="video-library-filter-tabs" role="tablist" aria-label="Video filters">{filterLabels.map(([key, label]) => <button type="button" key={key} role="tab" aria-selected={filter === key} className={`video-library-filter${filter === key ? " is-active" : ""}`} onClick={() => setFilter(key)}>{label}</button>)}{collections.map((collection) => <button type="button" key={collection.id} role="tab" aria-selected={filter === `collection:${collection.id}`} className={`video-library-filter${filter === `collection:${collection.id}` ? " is-active" : ""}`} onClick={() => setFilter(`collection:${collection.id}`)}><Icon name="folder" /> {collection.name}</button>)}</div><div className="video-library-control-spacer" /><label className="video-library-sort"><Icon name="sort" /><span>Sort</span><select value={sort} onChange={(e) => setSort(e.target.value as SortKey)} aria-label="Sort videos"><option value="recent">Recent</option><option value="name">Name</option><option value="duration">Duration</option><option value="highlights">Highlights</option></select></label><div className="video-library-view-toggle" role="group" aria-label="View mode"><button type="button" className={`icon-btn${viewMode === "grid" ? " is-active" : ""}`} onClick={() => setViewMode("grid")} aria-label="Grid view" aria-pressed={viewMode === "grid"}><Icon name="grid" /></button><button type="button" className={`icon-btn${viewMode === "list" ? " is-active" : ""}`} onClick={() => setViewMode("list")} aria-label="List view" aria-pressed={viewMode === "list"}><Icon name="list" /></button></div></div>}
        />
        <div className="videos-wrap video-library-wrap">
          {error && <div className="videos-error" role="alert">{error}</div>}
          {result && visibleVideos.length > 0 && <div className="video-library-selection-bar"><label><input type="checkbox" checked={allVisibleSelected} onChange={selectVisible} /> Select visible</label><span>{selected.size} selected</span><div className="video-library-collection-tools"><input value={collectionDraft} onChange={(event) => setCollectionDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void makeCollection(); }} placeholder="New collection" aria-label="New collection name" /><button type="button" className="icon-btn" onClick={() => void makeCollection()} disabled={batchBusy || !collectionDraft.trim()} aria-label="Create collection" title="Create collection"><Icon name="plus" /></button><select value={targetCollection} onChange={(event) => setTargetCollection(event.target.value)} aria-label="Collection"><option value="">Add to collection...</option>{collections.map((collection) => <option key={collection.id} value={collection.id}>{collection.name}</option>)}</select><button type="button" className="btn btn-tonal btn-sm" onClick={() => void addSelectedToCollection()} disabled={batchBusy || selected.size === 0 || !targetCollection}><Icon name="folder" /> Add</button></div><div className="video-library-selection-actions"><button type="button" className="btn btn-tonal btn-sm" onClick={() => void markSelectedForReview()} disabled={batchBusy || selected.size === 0}><Icon name="tag" /> {batchBusy ? "Updating..." : "Mark for review"}</button>{selected.size > 0 && <button type="button" className="btn btn-text btn-sm" onClick={() => setSelected(new Set())}><Icon name="close" /> Clear</button>}</div></div>}
          {!result && !error && <SkeletonGrid count={8} />}
          {result && visibleVideos.length === 0 && <div className="video-library-empty"><div className="video-library-empty-mark"><Icon name="play" /></div><h2>{result.videos.length === 0 ? "No videos yet" : "Nothing in this view"}</h2><p>{result.videos.length === 0 ? "Add video files to the watched folder, then re-index the library." : "Try another filter or clear the current view."}</p><button type="button" className="btn btn-outlined" onClick={() => setFilter("all")}>Show all videos</button></div>}
          {visibleVideos.length > 0 && <div className={`video-library-items${viewMode === "list" ? " is-list" : ""}`}>{visibleVideos.map((video) => <VideoCard key={videoKey(video)} video={video} selected={selected.has(videoKey(video))} list={viewMode === "list"} onOpen={(item) => item.sha256 && navigate(`/videos/${encodeURIComponent(item.sha256)}`)} onSelect={toggleSelected} />)}</div>}
        </div>
      </div>
    </div>
  );
}
