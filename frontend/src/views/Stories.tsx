import { useEffect, useState, useCallback, useRef } from "react";
import { Link } from "react-router-dom";
import ModeViewBar, { modeFromPath } from "../components/ModeViewBar";
import Rail from "../components/Rail";
import RecapButton from "../components/RecapButton";
import StackPhoto from "../components/StackPhoto";
import Topbar from "../components/Topbar";
import StatusRow from "../components/StatusRow";
import { useLocation } from "react-router-dom";
import { getLikedStatus } from "../api/client";
import type { StoryEntry, VisitEntry } from "../api/types";
import { useLikeStatus, useToggleLike } from "../hooks/useLikes";

// Google Material accent quartet — rotated by day hash
const ACCENT_COLORS = [
  "var(--g-blue)",
  "var(--g-red)",
  "var(--g-yellow)",
  "var(--g-green)",
];

function dayHash(day: string): number {
  let h = 0;
  for (let i = 0; i < day.length; i++) {
    h = (h * 31 + day.charCodeAt(i)) >>> 0;
  }
  return h;
}

function accentFor(day: string): string {
  return ACCENT_COLORS[dayHash(day) % ACCENT_COLORS.length];
}

function formatIndex(n: number): string {
  return String(n + 1).padStart(2, "0");
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  } catch {
    return "";
  }
}

function formatDayLabel(day: string): string {
  try {
    const d = new Date(day + "T00:00:00");
    return d.toLocaleDateString("en-IN", { weekday: "long", month: "long", day: "numeric" });
  } catch {
    return day;
  }
}

// SVG icon for export
const ExportIcon = (
  <svg
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    width="16"
    height="16"
  >
    <path d="M21 15v3a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3v-3"/>
    <path d="m7 10 5 5 5-5"/>
    <path d="M12 15V3"/>
  </svg>
);

const ChevronDownIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
    strokeLinecap="round" strokeLinejoin="round" width="14" height="14">
    <path d="m6 9 6 6 6-6"/>
  </svg>
);

const ChevronUpIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
    strokeLinecap="round" strokeLinejoin="round" width="14" height="14">
    <path d="m18 15-6-6-6 6"/>
  </svg>
);

function VisitRow({ visit, expanded, onToggle }: {
  visit: VisitEntry;
  expanded: boolean;
  onToggle: () => void;
}) {
  const timeRange = `${formatTime(visit.arrived_at)} – ${formatTime(visit.departed_at)}`;
  const summaryShort = visit.summary
    ? (visit.summary.length > 240 ? visit.summary.slice(0, 240) + "…" : visit.summary)
    : null;
  const [showFull, setShowFull] = useState(false);
  const hasSummary = !!visit.summary;

  return (
    <div className="visit-row">
      <div className="visit-row-header" onClick={onToggle} role="button" tabIndex={0}
        onKeyDown={e => e.key === "Enter" && onToggle()}
        aria-expanded={expanded}>
        {visit.cover_thumb_url && (
          <div className="visit-cover">
            <img src={visit.cover_thumb_url} alt={visit.name} loading="lazy" />
          </div>
        )}
        <div className="visit-info">
          <div className="visit-name">{visit.name}</div>
          <div className="visit-meta">
            <span className="visit-time">{timeRange}</span>
            <span className="visit-dot" aria-hidden="true" />
            <span className="visit-count">{visit.photo_count} photos</span>
            {visit.elevation_m && (
              <>
                <span className="visit-dot" aria-hidden="true" />
                <span className="visit-elevation">{visit.elevation_m.toLocaleString()}m</span>
              </>
            )}
          </div>
        </div>
        {hasSummary && (
          <span className="visit-chevron" aria-hidden="true">
            {expanded ? ChevronUpIcon : ChevronDownIcon}
          </span>
        )}
      </div>

      {expanded && hasSummary && (
        <div className="visit-summary">
          <p>{showFull ? visit.summary : summaryShort}</p>
          {visit.summary && visit.summary.length > 240 && (
            <button
              className="visit-summary-toggle"
              onClick={e => { e.stopPropagation(); setShowFull(f => !f); }}
            >
              {showFull ? "Show less" : "Read more"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function ItinerarySection({ visits }: { visits: VisitEntry[] }) {
  const [open, setOpen] = useState(false);
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  if (!visits || visits.length === 0) return null;

  const toggle = (i: number) =>
    setExpandedIdx(prev => (prev === i ? null : i));

  return (
    <div className="itinerary-section">
      <button
        className="itinerary-toggle"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
      >
        <span>Itinerary</span>
        <span className="itinerary-toggle-count">{visits.length} stops</span>
        <span className="itinerary-toggle-chevron" aria-hidden="true">
          {open ? ChevronUpIcon : ChevronDownIcon}
        </span>
      </button>

      {open && (
        <div className="itinerary-body">
          {visits.map((v, i) => (
            <VisitRow
              key={i}
              visit={v}
              expanded={expandedIdx === i}
              onToggle={() => toggle(i)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function StoryCard({ story }: { story: StoryEntry }) {
  const accent = accentFor(story.day);
  const dayLabel = formatDayLabel(story.day);
  const [playerStartIdx, setPlayerStartIdx] = useState<number | null>(null);
  const [playerOpen, setPlayerOpen] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportResult, setExportResult] = useState<string | null>(null);
  const [focusedPhotoId, setFocusedPhotoId] = useState<number | null>(null);
  const [likedMap, setLikedMap] = useState<Record<string, boolean>>({});

  useEffect(() => {
    const shas = story.items.map((it) => it.sha256);
    if (shas.length === 0) return;
    getLikedStatus(shas)
      .then(setLikedMap)
      .catch(() => undefined);
  }, [story.items]);

  async function exportStory() {
    setExporting(true);
    setExportResult(null);
    try {
      const res = await fetch(`/api/stories/${story.id}/export`, { method: "POST" });
      if (!res.ok) {
        const j = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(j.detail);
      }
      const j = await res.json();
      setExportResult(`Copied ${j.copied} photos → ${j.out_dir}`);
    } catch (e) {
      setExportResult(String(e));
    } finally {
      setExporting(false);
    }
  }

  // Parse scene count from title: "... · N photos, M scenes"
  const scenesMatch = story.title.match(/(\d+)\s+scenes/);
  const scenesCount = scenesMatch ? scenesMatch[1] : "";

  return (
    <article className="story-card" style={{ "--accent": accent } as React.CSSProperties}>
      <div className="story-meta">
        <div className="story-day-label">{dayLabel}</div>
        <h2 className="story-title">
          {story.visits && story.visits.length > 0
            ? (story.visits.length === 1
                ? story.visits[0].name
                : `${story.visits[0].name} to ${story.visits[story.visits.length - 1].name}`)
            : story.day}
        </h2>
        <div className="count">
          {story.items.length} photos{scenesCount ? ` · ${scenesCount} scenes` : ""}
        </div>

        {story.itinerary_breadcrumb && (
          <div className="story-breadcrumb" title="Route for the day">
            {story.itinerary_breadcrumb}
          </div>
        )}

        <div className="story-actions">
          <button
            className="btn btn-filled"
            onClick={() => setPlayerOpen(true)}
            disabled={story.items.length === 0}
            title="Fullscreen slideshow"
          >
            ▶ Play
          </button>
          <button
            className="btn btn-tonal"
            onClick={exportStory}
            disabled={exporting || story.items.length === 0}
            title="Copy this story's photos to .selects/exports/stories/<title>/ in carousel order"
          >
            {ExportIcon}
            {exporting ? "Exporting…" : "Export"}
          </button>
          <RecapButton storyId={story.id} />
        </div>

        {exportResult && (
          <div
            style={{
              marginTop: 10,
              padding: "8px 12px",
              borderRadius: 10,
              background: "color-mix(in srgb, var(--g-green) 14%, transparent)",
              color: "color-mix(in srgb, var(--g-green) 78%, var(--md-on-surface))",
              fontSize: 12,
              fontFamily: "var(--font-mono)",
              wordBreak: "break-all",
            }}
          >
            {exportResult}
          </div>
        )}

        <ItinerarySection visits={story.visits || []} />
      </div>

      <div className="story-right">
        <div className="story-strip">
          {story.items.length === 0 ? (
            <div className="story-strip-empty">No photos match current filters</div>
          ) : (
            story.items.map((item, i) => {
              const hasStack = !!(item.moment_id && item.moment_size && item.moment_size > 1);
              return (
                <div
                  className="story-frame"
                  key={item.photo_id}
                  title={
                    (item.taken_at ?? "") +
                    (hasStack ? ` · burst of ${item.moment_size} — press [ ] in player to cycle` : "")
                  }
                  style={{ cursor: "zoom-in" }}
                >
                  <StackPhoto
                    sha256={item.sha256}
                    thumbUrl={item.thumb_url}
                    momentId={item.moment_id ?? null}
                    momentSize={item.moment_size ?? null}
                    isFocused={focusedPhotoId === item.photo_id}
                    onFocus={() => setFocusedPhotoId(item.photo_id)}
                    onClick={() => {
                      setPlayerStartIdx(i);
                      setPlayerOpen(true);
                    }}
                    initialLiked={likedMap[item.sha256] ?? false}
                    onLikeChange={(sha, liked) =>
                      setLikedMap((m) => ({ ...m, [sha]: liked }))
                    }
                    style={{
                      width: "100%",
                      height: "100%",
                      overflow: "hidden",
                      borderRadius: "inherit",
                    }}
                  >
                    <span className="idx">{formatIndex(item.rank)}</span>
                    {item.tag && <span className="story-frame-tag">{item.tag}</span>}
                  </StackPhoto>
                </div>
              );
            })
          )}
        </div>
      </div>
      {playerOpen && (
        <StoryPlayer
          story={story}
          initialIndex={playerStartIdx ?? 0}
          onClose={() => {
            setPlayerOpen(false);
            setPlayerStartIdx(null);
          }}
        />
      )}
    </article>
  );
}

function StoryPlayer({
  story,
  initialIndex = 0,
  onClose,
}: {
  story: StoryEntry;
  initialIndex?: number;
  onClose: () => void;
}) {
  const [index, setIndex] = useState(initialIndex);
  const [playing, setPlaying] = useState(false); // user opened it manually — don't autoplay by default
  const [enhancedOn, setEnhancedOn] = useState(false);
  const [straightenOn, setStraightenOn] = useState(false);
  const [stackMoment, setStackMoment] = useState<import("../api/types").Moment | null>(null);
  const [stackIdx, setStackIdx] = useState(0);
  const persistTimer = useRef<number | null>(null);
  const SLIDE_MS = 4000;

  const item = story.items[index];

  // Reset per-photo state when index changes
  useEffect(() => {
    setEnhancedOn(false);
    setStraightenOn(false);
    setStackMoment(null);
    setStackIdx(0);
  }, [index]);

  // Load liked status for current photo
  const activeSha = stackMoment?.members[stackIdx]?.sha256 ?? item?.sha256 ?? null;
  const { liked: activeLikedMap, setLiked: setActiveLikedMap } = useLikeStatus(
    activeSha ? [activeSha] : [],
  );
  const liked = Boolean(activeSha && activeLikedMap[activeSha]);

  // Autoplay
  useEffect(() => {
    if (!playing) return;
    const t = setTimeout(() => {
      setIndex((i) => (i + 1) % story.items.length);
    }, SLIDE_MS);
    return () => clearTimeout(t);
  }, [index, playing, story.items.length]);

  const toggleLikeInner = useToggleLike(setActiveLikedMap);
  const toggleLike = useCallback(() => {
    if (activeSha) toggleLikeInner(activeSha, liked);
  }, [activeSha, liked, toggleLikeInner]);

  const cycleStack = useCallback(
    async (delta: number) => {
      if (!item?.moment_id || !(item.moment_size && item.moment_size > 1)) return;
      let mom = stackMoment;
      if (!mom) {
        try {
          const res = await fetch(`/api/photos/${item.sha256}/moment`);
          if (!res.ok) return;
          mom = await res.json();
        } catch {
          return;
        }
        if (!mom) return;
        setStackMoment(mom);
        setStackIdx(0);
      }
      const n = mom.members.length;
      if (n === 0) return;
      const nextIdx = (stackIdx + delta + n) % n;
      setStackIdx(nextIdx);
      if (persistTimer.current) window.clearTimeout(persistTimer.current);
      const newPrimary = mom.members[nextIdx];
      persistTimer.current = window.setTimeout(() => {
        fetch(`/api/moments/${mom!.id}/primary`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ photo_id: newPrimary.photo_id }),
        }).catch(() => undefined);
      }, 450);
    },
    [item, stackMoment, stackIdx],
  );

  // Keyboard
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "Escape") {
        onClose();
      } else if (e.key === "[") {
        e.preventDefault();
        cycleStack(-1);
      } else if (e.key === "]") {
        e.preventDefault();
        cycleStack(1);
      } else if (e.key === "ArrowRight" || e.key === " ") {
        e.preventDefault();
        setIndex((i) => (i + 1) % story.items.length);
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        setIndex((i) => (i - 1 + story.items.length) % story.items.length);
      } else if (e.key === "f" || e.key === "F") {
        e.preventDefault();
        toggleLike();
      } else if (e.key === "e" || e.key === "E") {
        e.preventDefault();
        setEnhancedOn((v) => !v);
      } else if (e.key === "s" || e.key === "S") {
        e.preventDefault();
        setStraightenOn((v) => !v);
      } else if (e.key.toLowerCase() === "p") {
        e.preventDefault();
        setPlaying((p) => !p);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, story.items.length, cycleStack, toggleLike]);

  if (!item) return null;

  const displayedSha = activeSha ?? item.sha256;
  const imageUrl =
    enhancedOn || straightenOn
      ? `/api/enhance/${displayedSha}?preset=film&grade=${enhancedOn ? "true" : "false"}&straighten=${straightenOn ? "true" : "false"}`
      : `/api/preview/${displayedSha}`;
  const isBurst = !!(item.moment_id && item.moment_size && item.moment_size > 1);

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 100,
        background: "#000",
        display: "grid",
        gridTemplateRows: "auto 1fr auto",
      }}
    >
      {/* Top bar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: "10px 20px",
          color: "#fff",
          background: "rgba(0,0,0,0.5)",
        }}
      >
        <button onClick={onClose} className="btn btn-text" style={{ color: "#fff" }}>
          ← Close
        </button>
        <div style={{ flex: 1, fontFamily: "var(--font-display)", fontSize: 16 }}>
          {story.title}
        </div>

        {/* Action cluster */}
        <button
          onClick={toggleLike}
          title={liked ? "Unlike (F)" : "Like — F"}
          style={{
            padding: "6px 12px 6px 10px",
            borderRadius: 999,
            border: 0,
            background: liked ? "var(--g-red)" : "rgba(255,255,255,0.12)",
            color: "#fff",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            gap: 6,
            fontFamily: "var(--font-display)",
            fontSize: 12,
            fontWeight: 500,
          }}
        >
          <svg
            viewBox="0 0 24 24"
            width="13"
            height="13"
            fill={liked ? "currentColor" : "none"}
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z" />
          </svg>
          {liked ? "Liked" : "Like · F"}
        </button>
        <button
          onClick={() => setEnhancedOn((v) => !v)}
          title="Enhance (E)"
          style={{
            padding: "6px 12px",
            borderRadius: 999,
            border: 0,
            background: enhancedOn ? "var(--md-primary)" : "rgba(255,255,255,0.12)",
            color: "#fff",
            cursor: "pointer",
            fontFamily: "var(--font-display)",
            fontSize: 12,
            fontWeight: 500,
          }}
        >
          {enhancedOn ? "Auto-edited" : "Auto edit · E"}
        </button>
        <button
          onClick={() => setStraightenOn((v) => !v)}
          title="Straighten (S)"
          style={{
            padding: "6px 12px",
            borderRadius: 999,
            border: 0,
            background: straightenOn ? "var(--md-primary)" : "rgba(255,255,255,0.12)",
            color: "#fff",
            cursor: "pointer",
            fontFamily: "var(--font-display)",
            fontSize: 12,
            fontWeight: 500,
          }}
        >
          {straightenOn ? "Straightened" : "Straighten · S"}
        </button>

        <span style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>
          {index + 1} / {story.items.length}
        </span>
      </div>

      {/* Image — square letterbox */}
      <div
        style={{
          display: "grid",
          placeItems: "center",
          overflow: "hidden",
          padding: 16,
          position: "relative",
        }}
      >
        {/* Burst badge (top-left of stage) */}
        {isBurst && (
          <div
            key={`stack-badge-${displayedSha}`}
            style={{
              position: "absolute",
              top: 24,
              left: 24,
              zIndex: 2,
              display: "flex",
              alignItems: "center",
              gap: 10,
              background: "var(--g-yellow)",
              color: "#000",
              padding: "8px 14px 8px 12px",
              borderRadius: 999,
              fontFamily: "var(--font-display)",
              fontSize: 13,
              fontWeight: 600,
              boxShadow: "0 4px 14px rgba(0,0,0,0.45)",
              animation: "burst-pulse 1500ms ease-out 1",
            }}
          >
            <svg
              viewBox="0 0 24 24"
              width="16"
              height="16"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <rect x="3" y="7" width="14" height="14" rx="2" />
              <rect x="7" y="3" width="14" height="14" rx="2" />
            </svg>
            <span>
              Burst{" "}
              <span style={{ fontFamily: "var(--font-mono)" }}>
                {stackMoment ? stackIdx + 1 : 1} / {item.moment_size}
              </span>
            </span>
            <button
              onClick={() => cycleStack(-1)}
              title="Previous (key: [)"
              style={{
                background: "rgba(0,0,0,0.12)",
                color: "#000",
                border: 0,
                borderRadius: 4,
                padding: "2px 8px",
                cursor: "pointer",
                fontFamily: "var(--font-mono)",
                fontWeight: 700,
                fontSize: 13,
              }}
            >
              [
            </button>
            <button
              onClick={() => cycleStack(1)}
              title="Next (key: ])"
              style={{
                background: "rgba(0,0,0,0.12)",
                color: "#000",
                border: 0,
                borderRadius: 4,
                padding: "2px 8px",
                cursor: "pointer",
                fontFamily: "var(--font-mono)",
                fontWeight: 700,
                fontSize: 13,
              }}
            >
              ]
            </button>
          </div>
        )}

        <img
          key={imageUrl}
          src={imageUrl}
          alt=""
          style={{
            maxWidth: "100%",
            maxHeight: "100%",
            objectFit: "contain",
            boxShadow: "0 20px 60px rgba(0,0,0,0.7)",
            animation: "stack-swap-fade 200ms ease",
          }}
        />
      </div>

      {/* Progress + controls */}
      <div style={{ background: "rgba(0,0,0,0.6)", color: "#fff" }}>
        <div style={{ height: 3, background: "rgba(255,255,255,0.15)" }}>
          <div
            key={index}
            style={{
              width: "100%",
              height: "100%",
              background: "var(--md-primary)",
              transform: playing ? "scaleX(1)" : "scaleX(0)",
              transformOrigin: "left",
              transition: playing ? `transform ${SLIDE_MS}ms linear` : "none",
            }}
          />
        </div>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 14,
            padding: "10px 20px",
            justifyContent: "center",
          }}
        >
          <button
            onClick={() => setIndex((i) => (i - 1 + story.items.length) % story.items.length)}
            className="btn btn-text"
            style={{ color: "#fff" }}
          >
            ← Prev
          </button>
          <button onClick={() => setPlaying((p) => !p)} className="btn btn-filled">
            {playing ? "❚❚ Pause" : "▶ Play"}
          </button>
          <button
            onClick={() => setIndex((i) => (i + 1) % story.items.length)}
            className="btn btn-text"
            style={{ color: "#fff" }}
          >
            Next →
          </button>
          <span
            style={{
              marginLeft: 16,
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              opacity: 0.6,
            }}
          >
            ← → Prev/Next · F like · E enhance · S straighten · [ ] burst cycle · P play · Esc close
          </span>
        </div>
      </div>
    </div>
  );
}

// Lightbox component removed — clicking a story photo now opens the full
// StoryPlayer at that photo, so a one-shot zoom view is no longer needed.

// Filter chip bar removed (2026-05-24). Stories are now aesthetic-curated
// instead of tag-filtered. See docs/superpowers/specs/2026-05-24-aesthetic-curation-design.md

// ---- Main view ----

type StoryGroup = "day" | "people";

function StoryGroupTabs({ value, onChange }: { value: StoryGroup; onChange: (g: StoryGroup) => void }) {
  const opts: { key: StoryGroup; label: string }[] = [
    { key: "day", label: "By day" },
    { key: "people", label: "By people" },
  ];
  return (
    <div className="story-group-tabs" role="tablist">
      {opts.map(o => (
        <button
          key={o.key}
          className={`story-group-tab${value === o.key ? " is-active" : ""}`}
          onClick={() => onChange(o.key)}
          role="tab"
          aria-selected={value === o.key}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

// ── Best-Of dropdown ─────────────────────────────────────────────────────

type FacetsResp = {
  days: { value: string; count: number }[];
  places: { value: string; count: number }[];
  persons: { value: string; label: string; count: number }[];
  categories: { value: string; count: number }[];
};

function BestOfDropdown() {
  const [open, setOpen] = useState(false);
  const [facets, setFacets] = useState<FacetsResp | null>(null);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open || facets) return;
    fetch("/api/curate/facets")
      .then((r) => (r.ok ? r.json() : null))
      .then(setFacets)
      .catch(() => undefined);
  }, [open, facets]);

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    if (open) document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        className={`btn ${open ? "btn-filled" : "btn-tonal"}`}
        onClick={() => setOpen((o) => !o)}
      >
        Best of ▾
      </button>
      {open && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            right: 0,
            zIndex: 50,
            minWidth: 380,
            maxHeight: 480,
            overflowY: "auto",
            background: "var(--md-surface)",
            border: "1px solid var(--md-outline-var)",
            borderRadius: 12,
            boxShadow: "0 12px 32px rgba(0,0,0,0.25)",
            padding: 12,
          }}
        >
          {!facets ? (
            <div style={{ padding: 12, color: "var(--md-on-surface-var)", fontSize: 13 }}>
              Loading…
            </div>
          ) : (
            <>
              <FacetGroup label="Category" entries={facets.categories.map(c => ({ value: c.value, label: c.value, count: c.count }))} hrefBase="/best/category/" />
              <FacetGroup label="People" entries={facets.persons.map(p => ({ value: p.value, label: p.label, count: p.count }))} hrefBase="/best/person/" />
              <FacetGroup label="Place" entries={facets.places.map(p => ({ value: p.value, label: p.value, count: p.count }))} hrefBase="/best/place/" />
              <FacetGroup label="Day" entries={facets.days.map(d => ({ value: d.value, label: d.value, count: d.count }))} hrefBase="/best/day/" />
            </>
          )}
        </div>
      )}
    </div>
  );
}

function FacetGroup({
  label,
  entries,
  hrefBase,
}: {
  label: string;
  entries: { value: string; label: string; count: number }[];
  hrefBase: string;
}) {
  if (entries.length === 0) return null;
  return (
    <div style={{ marginBottom: 10 }}>
      <div
        style={{
          fontSize: 10,
          textTransform: "uppercase",
          letterSpacing: "0.06em",
          color: "var(--md-on-surface-var)",
          padding: "4px 8px",
        }}
      >
        {label}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
        {entries.slice(0, 24).map((e) => (
          <Link
            key={e.value}
            to={`${hrefBase}${encodeURIComponent(e.value)}`}
            className="btn btn-text"
            style={{
              fontSize: 12,
              padding: "4px 9px",
              borderRadius: 999,
              background: "var(--md-surface-c)",
              border: "1px solid var(--md-outline-var)",
              textDecoration: "none",
            }}
          >
            {e.label}{" "}
            <span style={{ color: "var(--md-on-surface-var)", fontFamily: "var(--font-mono)", fontSize: 11 }}>
              {e.count}
            </span>
          </Link>
        ))}
      </div>
    </div>
  );
}

export default function Stories() {
  const [stories, setStories] = useState<StoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [groupBy, setGroupBy] = useState<StoryGroup>("day");
  const [searchInput, setSearchInput] = useState("");
  const [searchQ, setSearchQ] = useState(""); // debounced
  const [scopePct, setScopePct] = useState<number>(75);
  const [libraryPct, setLibraryPct] = useState<number>(50);
  const { pathname } = useLocation();
  // Mode comes from the URL — /curated/* means liked-only. The legacy in-page
  // "Culling / Curated ♥" toggle was redundant once mode lives in the route.
  const likedOnly = modeFromPath(pathname) === "curated";

  // Debounce searchInput → searchQ
  useEffect(() => {
    const id = setTimeout(() => setSearchQ(searchInput.trim()), 350);
    return () => clearTimeout(id);
  }, [searchInput]);

  const fetchStories = useCallback(
    (q: string, scope: number, lib: number, likedOnlyVal: boolean) => {
      setLoading(true);
      const params = new URLSearchParams();
      if (q) params.set("q", q);
      params.set("scope_pct", String(scope));
      params.set("library_pct", String(lib));
      if (likedOnlyVal) params.set("liked_only", "true");
      fetch(`/api/stories?${params}`)
        .then((r) => {
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          return r.json();
        })
        .then((data) => {
          setStories(data.stories);
          setLoading(false);
        })
        .catch((err) => {
          setError(String(err));
          setLoading(false);
        });
    },
    [],
  );

  useEffect(() => {
    fetchStories(searchQ, scopePct, libraryPct, likedOnly);
  }, [searchQ, scopePct, libraryPct, likedOnly, fetchStories]);

  const statusDetails = loading
    ? "loading…"
    : error
    ? "error loading stories"
    : searchQ
    ? `${stories.length} stories match "${searchQ}"`
    : `${stories.length} curated stories`;

  return (
    <div className="app">
      <Rail />
      <div className="workspace">
        <Topbar folder="selects" context={likedOnly ? "curated · stories" : "stories"} />
        <ModeViewBar />
        <StatusRow details={statusDetails} />

        <div className="stories-wrap" style={{ gridRow: "3 / span 3" }}>
          {loading && (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                height: 240,
                color: "var(--md-on-surface-var)",
                fontFamily: "var(--font-display)",
                fontSize: 15,
              }}
            >
              Loading stories…
            </div>
          )}

          {!loading && error && (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                height: 240,
                gap: 12,
              }}
            >
              <div
                style={{
                  color: "var(--md-on-surface)",
                  fontFamily: "var(--font-display)",
                  fontSize: 18,
                }}
              >
                Stories not yet available
              </div>
              <div style={{ color: "var(--md-on-surface-var)", fontSize: 13 }}>
                Run{" "}
                <code
                  style={{
                    fontFamily: "var(--font-mono)",
                    background: "var(--md-surface-c)",
                    padding: "2px 6px",
                    borderRadius: 4,
                  }}
                >
                  selects index &lt;folder&gt; --pass story
                </code>{" "}
                to build narrative sequences
              </div>
            </div>
          )}

          {!loading && !error && stories.length === 0 && (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                height: 240,
                gap: 12,
              }}
            >
              <div
                style={{
                  color: "var(--md-on-surface)",
                  fontFamily: "var(--font-display)",
                  fontSize: 18,
                }}
              >
                No stories yet
              </div>
              <div style={{ color: "var(--md-on-surface-var)", fontSize: 13 }}>
                Run the story stage to build narrative sequences from your photos.
              </div>
            </div>
          )}

          {!loading && !error && stories.length > 0 && (
            <>
              <div className="stories-header">
                <div>
                  <h1>Narrative sequences</h1>
                  <div className="sub">
                    Aesthetic-curated · {stories.length} stories · top 25% by combined AP V2.5 + NIMA, burst-deduped
                  </div>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <StoryGroupTabs value={groupBy} onChange={setGroupBy} />
                  <BestOfDropdown />
                </div>
              </div>

              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  padding: "8px 0 4px",
                }}
              >
                <input
                  type="search"
                  value={searchInput}
                  onChange={(e) => setSearchInput(e.target.value)}
                  placeholder="Search stories — e.g. 'monastery courtyard at dusk', 'snowy passes', 'high-altitude lake'"
                  style={{
                    flex: 1,
                    background: "var(--md-surface-c-low)",
                    border: "1px solid var(--md-outline-var)",
                    borderRadius: 999,
                    padding: "10px 18px",
                    fontFamily: "inherit",
                    fontSize: 14,
                    color: "var(--md-on-surface)",
                    outline: "none",
                  }}
                />
                {searchInput && (
                  <button
                    className="btn btn-text"
                    onClick={() => setSearchInput("")}
                    style={{ fontSize: 12 }}
                  >
                    Clear
                  </button>
                )}
              </div>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 16,
                  fontSize: 11,
                  color: "var(--md-on-surface-var)",
                  padding: "6px 0 18px",
                }}
              >
                <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span title="Per-scope percentile gate. Higher = stricter; 75 = top 25% of the day/place/person scope">
                    Per-scope ≥ p{scopePct}
                  </span>
                  <input
                    type="range"
                    min={0}
                    max={95}
                    step={5}
                    value={scopePct}
                    onChange={(e) => setScopePct(Number(e.target.value))}
                    style={{ width: 160 }}
                  />
                </label>
                <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span title="Library-wide percentile floor. A photo below this percentile in the whole library is dropped regardless of scope">
                    Library ≥ p{libraryPct}
                  </span>
                  <input
                    type="range"
                    min={0}
                    max={95}
                    step={5}
                    value={libraryPct}
                    onChange={(e) => setLibraryPct(Number(e.target.value))}
                    style={{ width: 160 }}
                  />
                </label>
                <button
                  className="btn btn-text"
                  onClick={() => {
                    setScopePct(75);
                    setLibraryPct(50);
                  }}
                  style={{ fontSize: 11 }}
                >
                  Reset
                </button>
              </div>

              {stories
                .filter(story => {
                  if (groupBy === "people") return story.day.startsWith("people:");
                  return !story.day.startsWith("place:")
                      && !story.day.startsWith("people:")
                      && !story.day.startsWith("pattern:");
                })
                .map(story => (
                  <StoryCard key={story.id} story={story} />
                ))}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
