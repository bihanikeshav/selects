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

export default function ModeViewBar() {
  const nav = useNavigate();
  const { pathname } = useLocation();
  const view = viewFromPath(pathname);

  // Sort-only view switcher now — All / Clusters / Stories. The Sort↔Curated
  // toggle was removed: Curated is a standalone page reached from the rail.
  return (
    <nav className="mode-view-bar" aria-label="View">
      <div className="mvb-group" role="tablist" aria-label="View">
        {VIEWS.map((v) => (
          <button
            key={v.key}
            role="tab"
            aria-selected={view === v.key}
            className={`mvb-view${view === v.key ? " is-active" : ""}`}
            onClick={() => nav(routeFor("cull", v.key))}
            title={v.hint}
          >
            {v.label}
          </button>
        ))}
      </div>
    </nav>
  );
}
