import { useEffect, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import ModeViewBar, { modeFromPath } from "../components/ModeViewBar";
import Rail from "../components/Rail";
import Topbar from "../components/Topbar";
import StatusRow from "../components/StatusRow";
import { listClusters } from "../api/client";
import type { ClusterEntry } from "../api/types";
import { stripPlaceSuffix } from "../lib/placeName";
import SkeletonGrid from "../components/SkeletonGrid";

// Google Material quartet — rotated by tag hash
const ACCENT_COLORS = [
  "var(--g-blue)",
  "var(--g-red)",
  "var(--g-yellow)",
  "var(--g-green)",
];

function tagHash(tag: string): number {
  let h = 0;
  for (let i = 0; i < tag.length; i++) {
    h = (h * 31 + tag.charCodeAt(i)) >>> 0;
  }
  return h;
}

function accentFor(tag: string): string {
  return ACCENT_COLORS[tagHash(tag) % ACCENT_COLORS.length];
}

// SVG glyph map — covers all taxonomy tags
const GLYPHS: Record<string, JSX.Element> = {
  landscape: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="m2 22 7-13 5 7 3-4 5 10z"/>
      <circle cx="17" cy="6" r="2"/>
    </svg>
  ),
  mountain: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="m2 22 7-13 5 7 3-4 5 10z"/>
      <circle cx="17" cy="6" r="2"/>
    </svg>
  ),
  sky: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2v2"/>
      <path d="M4.93 4.93 6.34 6.34"/>
      <path d="M2 12h2"/>
      <path d="M19.07 4.93 17.66 6.34"/>
      <path d="M22 12h-2"/>
      <path d="M17 17H7a5 5 0 1 1 4.9-6H12a3 3 0 0 1 3 3h2a2 2 0 1 1 0 4z"/>
    </svg>
  ),
  monastery: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 21h18"/>
      <path d="M5 21V8l7-5 7 5v13"/>
      <path d="M9 12h2v3H9z"/>
      <path d="M13 12h2v3h-2z"/>
    </svg>
  ),
  architecture: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 21h18"/>
      <path d="M5 21V8l7-5 7 5v13"/>
      <path d="M9 12h2v3H9z"/>
      <path d="M13 12h2v3h-2z"/>
    </svg>
  ),
  portrait: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="8" r="4"/>
      <path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>
    </svg>
  ),
  people: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="9" cy="8" r="3"/>
      <circle cx="17" cy="9" r="2.5"/>
      <path d="M3 20c0-3 3-5 6-5s6 2 6 5"/>
      <path d="M14 20c0-2 2-4 4-4s3.5 1 3.5 3.5"/>
    </svg>
  ),
  food: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M7 2v8a2 2 0 0 0 2 2h0a2 2 0 0 0 2-2V2"/>
      <path d="M11 2v20"/>
      <path d="M16 2v9a3 3 0 0 0 3 3v8"/>
    </svg>
  ),
  transit: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M5 17H3a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11a2 2 0 0 1 2 2v3"/>
      <rect x="9" y="11" width="14" height="10" rx="2"/>
      <circle cx="12" cy="21" r="1"/>
      <circle cx="20" cy="21" r="1"/>
    </svg>
  ),
  interior: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>
      <polyline points="9 22 9 12 15 12 15 22"/>
    </svg>
  ),
  water: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2C6 8 4 12 4 15a8 8 0 0 0 16 0c0-3-2-7-8-13z"/>
    </svg>
  ),
  night: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>
    </svg>
  ),
  animal: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10 5.172C10 3.782 8.423 2.679 6.5 3c-2.823.47-4.113 6.006-4 7 .08.703 1.725 1.722 3.656 1 1.261-.472 1.96-1.45 2.344-2.5"/>
      <path d="M14.267 5.172c0-1.39 1.577-2.493 3.5-2.172 2.823.47 4.113 6.006 4 7-.08.703-1.725 1.722-3.656 1-1.261-.472-1.855-1.45-2.239-2.5"/>
      <path d="M8 14v.5"/>
      <path d="M16 14v.5"/>
      <path d="M11.25 16.25h1.5L12 17l-.75-.75z"/>
      <path d="M4.42 11.247A13.152 13.152 0 0 0 4 14.556C4 18.728 7.582 21 12 21s8-2.272 8-6.444c0-1.061-.162-2.2-.493-3.309m-9.243-6.082A8.801 8.801 0 0 1 12 5c.78 0 1.5.108 2.161.306"/>
    </svg>
  ),
  abstract: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3"/>
      <path d="M3 7l3 3-3 3"/>
      <path d="M21 17l-3-3 3-3"/>
      <path d="M7 3l3 3-3 3"/>
      <path d="M17 21l-3-3 3-3"/>
    </svg>
  ),
  documents: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
      <polyline points="14 2 14 8 20 8"/>
      <line x1="16" y1="13" x2="8" y2="13"/>
      <line x1="16" y1="17" x2="8" y2="17"/>
      <polyline points="10 9 9 9 8 9"/>
    </svg>
  ),
};

const DOT_GLYPH = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="1.5"/>
  </svg>
);

function tagLabel(tag: string): string {
  const labels: Record<string, string> = {
    landscape: "Landscape",
    mountain: "Mountain",
    sky: "Sky",
    monastery: "Monastery",
    architecture: "Architecture",
    portrait: "Portrait",
    people: "People",
    food: "Food",
    transit: "Transit",
    interior: "Interior",
    water: "Water",
    night: "Night",
    animal: "Animal",
    abstract: "Abstract",
    documents: "Documents",
    uncategorized: "Everything else",
  };
  const known = labels[tag.toLowerCase()];
  if (known) return known;
  const spaced = stripPlaceSuffix(tag).replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** The catch-all bucket always sorts to the bottom of the grid. */
function isCatchAll(tag: string): boolean {
  return tag.toLowerCase() === "uncategorized";
}

function ClusterCard({
  cluster,
  source,
  mode,
}: {
  cluster: ClusterEntry;
  source: string;
  mode: "cull" | "curated";
}) {
  const accent = accentFor(cluster.tag);
  const glyph = GLYPHS[cluster.tag] ?? DOT_GLYPH;

  return (
    <Link
      to={`${mode === "curated" ? "/curated" : "/cull"}/clusters/${encodeURIComponent(cluster.tag)}?source=${source}`}
      className="cluster-card cluster-card-link"
      style={{ "--accent": accent } as React.CSSProperties}
    >
      <div className="cluster-cover">
        <img src={cluster.cover_url} alt={cluster.tag} loading="lazy" />
        {cluster.sample_thumbs.length > 0 && (
          <div className="cluster-mini-grid">
            {cluster.sample_thumbs.slice(0, 4).map((url, i) => (
              <img key={i} src={url} alt="" loading="lazy" />
            ))}
          </div>
        )}
        <div className="accent-band" />
      </div>
      <div className="cluster-body">
        <div className="label">
          <span className="glyph">{glyph}</span>
          {tagLabel(cluster.tag)}
        </div>
        <span className="count">{cluster.count}</span>
      </div>
    </Link>
  );
}

type ClusterSource = "thematic" | "date" | "lookback" | "posting" | "";

const SOURCE_TABS: ClusterSource[] = ["thematic", "", "date", "lookback", "posting"];

const SOURCE_LABELS: Record<ClusterSource, { label: string; sub: string }> = {
  thematic: {
    label: "Places",
    sub: "Named places from your trip",
  },
  "": {
    label: "Scenes",
    sub: "What the photo is of",
  },
  date: {
    label: "Days",
    sub: "One collection per day",
  },
  lookback: {
    label: "Themes",
    sub: "Visual themes across the whole trip",
  },
  posting: {
    label: "Sessions",
    sub: "Tight groups from one shooting session",
  },
};

function parseClusterSource(raw: string | null): ClusterSource {
  if (raw === "" || raw === "thematic" || raw === "date" || raw === "lookback" || raw === "posting") {
    return raw;
  }
  return "thematic";
}

function SourceToggle({
  value,
  onChange,
}: {
  value: ClusterSource;
  onChange: (v: ClusterSource) => void;
}) {
  return (
    <div className="story-group-tabs" role="tablist" aria-label="Collection type">
      {SOURCE_TABS.map(src => (
        <button
          key={src || "scenes"}
          type="button"
          role="tab"
          aria-selected={value === src}
          title={SOURCE_LABELS[src].sub}
          className={`story-group-tab${value === src ? " is-active" : ""}`}
          onClick={() => onChange(src)}
        >
          {SOURCE_LABELS[src].label}
        </button>
      ))}
    </div>
  );
}

export default function Clusters() {
  const [params, setParams] = useSearchParams();
  const [source, setSource] = useState<ClusterSource>(() => parseClusterSource(params.get("source")));
  const [clusters, setClusters] = useState<ClusterEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { pathname } = useLocation();
  const mode = modeFromPath(pathname);

  function changeSource(v: ClusterSource) {
    setSource(v);
    const next = new URLSearchParams(params);
    next.set("source", v);
    setParams(next, { replace: true });
  }

  useEffect(() => {
    setLoading(true);
    setError(null);
    listClusters({ source })
      .then(data => {
        // "Everything else" is a leftovers bucket — never the first thing you see.
        setClusters(
          [...data.clusters].sort(
            (a, b) => Number(isCatchAll(a.tag)) - Number(isCatchAll(b.tag)),
          ),
        );
        setTotal(data.total);
        setLoading(false);
      })
      .catch(err => {
        setError(String(err));
        setLoading(false);
      });
  }, [source]);

  const sourceInfo = SOURCE_LABELS[source];

  return (
    <div className="app">
      <Rail />
      <div className="workspace">
        <Topbar folder="selects" context="Collections" />
        <ModeViewBar />
        <StatusRow
          details={loading ? "loading…" : error ? "error loading clusters" : `${clusters.length} ${source === "lookback" ? "themes" : "collections"} · ${total} photos`}
        />

        <div className="clusters-wrap" style={{ gridRow: "3 / span 3" }}>
          <div className="clusters-header">
            <div className="clusters-header-row">
              <h1>Collections</h1>
              <SourceToggle value={source} onChange={changeSource} />
            </div>
            <div className="sub">{sourceInfo.sub}</div>
          </div>

          {loading && <SkeletonGrid count={12} />}

          {!loading && error && (
            <div className="stories-state">
              <div className="stories-state-title">Collections not yet available</div>
              <div className="stories-state-sub">
                Index this library first — collections are built while your photos
                are being understood and tagged.
              </div>
            </div>
          )}

          {!loading && !error && clusters.length === 0 && (
            <div className="stories-state">
              <div className="stories-state-title">No collections yet</div>
              <div className="stories-state-sub">
                Nothing has been grouped this way yet — try another tab above.
              </div>
            </div>
          )}

          {!loading && !error && clusters.length > 0 && (
            <div className="cluster-grid">
              {clusters.map(c => (
                <ClusterCard key={c.tag} cluster={c} source={source} mode={mode} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
