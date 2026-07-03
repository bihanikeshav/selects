import { useEffect, useState } from "react";
import Rail from "../components/Rail";
import Topbar from "../components/Topbar";
import StatusRow from "../components/StatusRow";
import { listStories } from "../api/client";
import type { StoryEntry } from "../api/types";

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

function StoryCard({ story }: { story: StoryEntry }) {
  const accent = accentFor(story.day);

  return (
    <article className="story-card" style={{ "--accent": accent } as React.CSSProperties}>
      <div className="story-meta">
        <h2>{story.day}</h2>
        <div className="count">
          {story.photo_count} photos · {story.title.split(",")[1]?.trim() ?? ""}
        </div>
        <div className="story-actions">
          <button className="btn btn-filled">
            {ExportIcon}
            Export carousel
          </button>
          <button className="btn btn-tonal">Refine</button>
        </div>
      </div>
      <div className="story-strip">
        {story.items.map((item, i) => (
          <div className="story-frame" key={item.photo_id}>
            <img
              src={item.thumb_url}
              alt={`Photo ${i + 1}`}
              loading="lazy"
              title={item.taken_at ?? undefined}
            />
            <span className="idx">{formatIndex(item.rank)}</span>
          </div>
        ))}
      </div>
    </article>
  );
}

export default function Stories() {
  const [stories, setStories] = useState<StoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listStories()
      .then(data => {
        setStories(data.stories);
        setLoading(false);
      })
      .catch(err => {
        setError(String(err));
        setLoading(false);
      });
  }, []);

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
                <h1>Narrative sequences</h1>
                <div className="sub">
                  {stories.length} suggested stories — drag to reorder · click to enlarge
                </div>
              </div>

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
