import { useNavigate, useLocation } from "react-router-dom";

/**
 * Two-tab nav for the cull/curated workflow.
 *
 *  Mode  : Cull | Curated  (high-level intent — what photo set to work with)
 *  View  : All  | Clusters | Stories  (slice of that set)
 *
 * Routes:
 *    /cull           = Cull + All        (BurstCull)
 *    /cull/clusters  = Cull + Clusters
 *    /cull/stories   = Cull + Stories
 *    /curated        = Curated + All     (Curated grid)
 *    /curated/clusters = Curated + Clusters
 *    /curated/stories  = Curated + Stories
 *
 * Mode and view are both derived from the URL — no context, no localStorage.
 */
export type Mode = "cull" | "curated";
export type View = "all" | "clusters" | "stories";

export function modeFromPath(pathname: string): Mode {
  return pathname.startsWith("/curated") ? "curated" : "cull";
}

export function viewFromPath(pathname: string): View {
  if (pathname.endsWith("/clusters") || pathname.includes("/clusters")) return "clusters";
  if (pathname.endsWith("/stories") || pathname.includes("/stories")) return "stories";
  return "all";
}

function routeFor(mode: Mode, view: View): string {
  const base = mode === "cull" ? "/cull" : "/curated";
  if (view === "all") return base;
  return `${base}/${view}`;
}

const VIEWS: { key: View; label: string; hint: string }[] = [
  { key: "all", label: "All", hint: "Single grid · BurstCull (cull) or Liked grid (curated)" },
  { key: "clusters", label: "Clusters", hint: "By visual theme" },
  { key: "stories", label: "Stories", hint: "By day / people / place" },
];

const MODES: { key: Mode; label: string; tone: string; hint: string }[] = [
  { key: "cull", label: "Cull", tone: "var(--md-primary)", hint: "Decide what's worth keeping" },
  { key: "curated", label: "Curated", tone: "var(--g-red)", hint: "Your liked set — edit & post" },
];

export default function ModeViewBar() {
  const nav = useNavigate();
  const { pathname } = useLocation();
  const mode = modeFromPath(pathname);
  const view = viewFromPath(pathname);

  return (
    <nav className="mode-view-bar" aria-label="Mode and view">
      <div className="mvb-group" role="tablist" aria-label="Mode">
        {MODES.map((m) => (
          <button
            key={m.key}
            role="tab"
            aria-selected={mode === m.key}
            className={`mvb-mode${mode === m.key ? " is-active" : ""}`}
            style={{ ["--tone" as string]: m.tone }}
            onClick={() => nav(routeFor(m.key, view))}
            title={m.hint}
          >
            {m.key === "curated" && (
              <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true">
                <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z" />
              </svg>
            )}
            {m.label}
          </button>
        ))}
      </div>

      <div className="mvb-sep" aria-hidden="true" />

      <div className="mvb-group" role="tablist" aria-label="View">
        {VIEWS.map((v) => (
          <button
            key={v.key}
            role="tab"
            aria-selected={view === v.key}
            className={`mvb-view${view === v.key ? " is-active" : ""}`}
            onClick={() => nav(routeFor(mode, v.key))}
            title={v.hint}
          >
            {v.label}
          </button>
        ))}
      </div>
    </nav>
  );
}
