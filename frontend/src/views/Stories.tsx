import { useEffect, useState, useCallback } from "react";
import Rail from "../components/Rail";
import Topbar from "../components/Topbar";
import StatusRow from "../components/StatusRow";
import { listStories, listTags } from "../api/client";
import type { StoryEntry, TagEntry, VisitEntry } from "../api/types";

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
          {story.photo_count} photos{scenesCount ? ` · ${scenesCount} scenes` : ""}
        </div>

        {story.itinerary_breadcrumb && (
          <div className="story-breadcrumb" title="Route for the day">
            {story.itinerary_breadcrumb}
          </div>
        )}

        <div className="story-actions">
          <button className="btn btn-filled">
            {ExportIcon}
            Export carousel
          </button>
          <button className="btn btn-tonal">Refine</button>
        </div>

        <ItinerarySection visits={story.visits || []} />
      </div>

      <div className="story-right">
        <div className="story-strip">
          {story.items.length === 0 ? (
            <div className="story-strip-empty">No photos match current filters</div>
          ) : (
            story.items.map((item, i) => (
              <div className="story-frame" key={item.photo_id}>
                <img
                  src={item.thumb_url}
                  alt={`Photo ${i + 1}`}
                  loading="lazy"
                  title={item.taken_at ?? undefined}
                />
                <span className="idx">{formatIndex(item.rank)}</span>
                {item.tag && <span className="story-frame-tag">{item.tag}</span>}
              </div>
            ))
          )}
        </div>
      </div>
    </article>
  );
}

// ---- Filter chip bar ----

function FilterChipBar({ tags, selected, onChange }: {
  tags: TagEntry[];
  selected: Set<string>;
  onChange: (next: Set<string>) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const allSelected = selected.size === tags.length;
  // Show ~2 lines worth of chips by default — roughly 14 chips at 1440 width
  const COLLAPSED_LIMIT = 14;
  const collapsed = !expanded && tags.length > COLLAPSED_LIMIT;
  const visible = collapsed ? tags.slice(0, COLLAPSED_LIMIT) : tags;
  const hidden = tags.length - visible.length;

  function toggleTag(tag: string) {
    const next = new Set(selected);
    if (next.has(tag)) next.delete(tag);
    else next.add(tag);
    onChange(next);
  }

  function selectAll() {
    onChange(new Set(tags.map(t => t.tag)));
  }

  return (
    <div className="filter-chip-bar">
      <div
        className="filter-chip-scroll"
        style={collapsed ? { maxHeight: 84, overflow: "hidden" } : undefined}
      >
        {visible.map(t => {
          const active = selected.has(t.tag);
          return (
            <button
              key={t.tag}
              className={`filter-chip${active ? " filter-chip--active" : ""}`}
              onClick={() => toggleTag(t.tag)}
              aria-pressed={active}
              title={`${t.count} photos`}
            >
              {t.tag}
              <span className="filter-chip-count">{t.count}</span>
            </button>
          );
        })}
        {collapsed && (
          <button
            className="filter-chip filter-chip--more"
            onClick={() => setExpanded(true)}
          >
            +{hidden} more
          </button>
        )}
        {expanded && tags.length > COLLAPSED_LIMIT && (
          <button
            className="filter-chip filter-chip--more"
            onClick={() => setExpanded(false)}
          >
            Show less
          </button>
        )}
      </div>
      {!allSelected && (
        <button className="btn btn-text filter-chip-clear" onClick={selectAll}>
          Clear filters
        </button>
      )}
    </div>
  );
}

// ---- Main view ----

export default function Stories() {
  const [allTags, setAllTags] = useState<TagEntry[]>([]);
  const [selectedTags, setSelectedTags] = useState<Set<string>>(new Set());
  const [stories, setStories] = useState<StoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tagsLoaded, setTagsLoaded] = useState(false);

  // Load tags once on mount
  useEffect(() => {
    listTags()
      .then(data => {
        setAllTags(data.tags);
        setSelectedTags(new Set(data.tags.map(t => t.tag)));
        setTagsLoaded(true);
      })
      .catch(() => {
        // Tags may not exist yet — proceed without filter UI
        setTagsLoaded(true);
      });
  }, []);

  // Load stories whenever tag filter changes (after tags are loaded)
  const fetchStories = useCallback((selected: Set<string>, allTagList: TagEntry[]) => {
    setLoading(true);
    const allSelected = selected.size === allTagList.length || allTagList.length === 0;
    const opts = allSelected
      ? {}
      : { includeTags: Array.from(selected) };

    listStories(opts)
      .then(data => {
        setStories(data.stories);
        setLoading(false);
      })
      .catch(err => {
        setError(String(err));
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    if (!tagsLoaded) return;
    fetchStories(selectedTags, allTags);
  }, [tagsLoaded, selectedTags, allTags, fetchStories]);

  function handleTagChange(next: Set<string>) {
    setSelectedTags(next);
  }

  const statusDetails = loading
    ? "loading…"
    : error
    ? "error loading stories"
    : `${stories.length} suggested stories`;

  return (
    <div className="app">
      <Rail />
      <div className="workspace">
        <Topbar folder="travelcull" context="narrative sequences" />
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
                  travelcull index &lt;folder&gt; --pass story
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
                    {stories.length} suggested stories — drag to reorder · click to enlarge
                  </div>
                </div>
              </div>

              {allTags.length > 0 && (
                <FilterChipBar
                  tags={allTags}
                  selected={selectedTags}
                  onChange={handleTagChange}
                />
              )}

              {stories.map(story => (
                <StoryCard key={story.id} story={story} />
              ))}

              <div style={{ display: "flex", justifyContent: "center", padding: "8px 0 32px" }}>
                <button className="btn btn-tonal">
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
                    <path d="M12 5v14M5 12h14"/>
                  </svg>
                  Build a new story from selection
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
