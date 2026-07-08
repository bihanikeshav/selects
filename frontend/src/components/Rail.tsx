import { useEffect } from "react";
import { NavLink, useLocation } from "react-router-dom";

import { modeFromPath } from "./ModeViewBar";

/** Retint the native desktop title bar to match the theme. No-op in a browser
 *  (window.pywebview only exists inside the pywebview desktop shell). */
function syncNativeTheme(isDark: boolean) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (window as any).pywebview?.api?.set_theme?.(isDark);
}

function toggleTheme() {
  const root = document.documentElement;
  const isDark = root.getAttribute("data-theme") === "dark";
  if (isDark) {
    root.removeAttribute("data-theme");
    localStorage.setItem("tc-theme", "light");
    syncNativeTheme(false);
  } else {
    root.setAttribute("data-theme", "dark");
    localStorage.setItem("tc-theme", "dark");
    syncNativeTheme(true);
  }
}

/**
 * Rail layout (top to bottom):
 *   - Brand
 *   - Workflow tabs: Cull, Curated (mode entry points)
 *   - Cross-cutting tools: People, Search, Map, Best-of (via the curate dropdown elsewhere)
 *   - Spacer → Calibrate, Theme toggle pinned to the bottom
 */
export default function Rail() {
  useEffect(() => {
    const isDark = localStorage.getItem("tc-theme") === "dark";
    if (isDark) {
      document.documentElement.setAttribute("data-theme", "dark");
    }
    // Sync the native title bar once pywebview is ready (and immediately, in
    // case it already is). Harmless in a browser.
    const apply = () => syncNativeTheme(isDark);
    apply();
    window.addEventListener("pywebviewready", apply);
    return () => window.removeEventListener("pywebviewready", apply);
  }, []);

  const { pathname } = useLocation();
  const mode = modeFromPath(pathname);

  const cullActive = mode === "cull";
  const curatedActive = mode === "curated";

  return (
    <nav className="rail" aria-label="Primary">
      {/* Workflow modes */}
      <NavLink
        to="/cull"
        className={"rail-item" + (cullActive ? " is-active" : "")}
        title="Sort — decide what's worth keeping"
      >
        <span className="icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="3" width="7" height="7" rx="1.5" />
            <rect x="14" y="3" width="7" height="7" rx="1.5" />
            <rect x="3" y="14" width="7" height="7" rx="1.5" />
            <rect x="14" y="14" width="7" height="7" rx="1.5" />
          </svg>
        </span>
        Sort
      </NavLink>

      <NavLink
        to="/curated"
        className={"rail-item" + (curatedActive ? " is-active" : "")}
        title="Curated — photos you've Liked, ready to edit & post"
      >
        <span className="icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z" />
          </svg>
        </span>
        Curated
      </NavLink>

      <div className="rail-divider" aria-hidden="true" />

      {/* Cross-cutting tools */}
      <NavLink
        to="/people"
        className={({ isActive }) => "rail-item" + (isActive ? " is-active" : "")}
        title="People — by face identity"
      >
        <span className="icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="9" cy="8" r="3" />
            <circle cx="17" cy="9" r="2.5" />
            <path d="M3 20c0-3 3-5 6-5s6 2 6 5" />
            <path d="M14 20c0-2 2-4 4-4s3.5 1 3.5 3.5" />
          </svg>
        </span>
        People
      </NavLink>

      <NavLink
        to="/search"
        className={({ isActive }) => "rail-item" + (isActive ? " is-active" : "")}
        title="Search — natural-language image search"
      >
        <span className="icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="11" cy="11" r="7" />
            <path d="m20 20-3.5-3.5" />
          </svg>
        </span>
        Search
      </NavLink>

      <NavLink
        to="/map"
        className={({ isActive }) => "rail-item" + (isActive ? " is-active" : "")}
        title="Map — by GPS"
      >
        <span className="icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M9 4l6 2 6-2v15l-6 2-6-2-6 2V6z" />
            <path d="M9 4v15M15 6v15" />
          </svg>
        </span>
        Map
      </NavLink>

      <NavLink
        to="/videos"
        className={({ isActive }) => "rail-item" + (isActive ? " is-active" : "")}
        title="Videos — frame quality, dead footage & highlights"
      >
        <span className="icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="2" y="5" width="20" height="14" rx="2" />
            <path d="m10 9 5 3-5 3z" />
          </svg>
        </span>
        Videos
      </NavLink>

      <NavLink
        to="/duplicates"
        className={({ isActive }) => "rail-item" + (isActive ? " is-active" : "")}
        title="Duplicates — cross-library duplicate report"
      >
        <span className="icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="8" y="8" width="12" height="12" rx="2" />
            <path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" />
          </svg>
        </span>
        Dupes
      </NavLink>

      <NavLink
        to="/doctor"
        className={({ isActive }) => "rail-item" + (isActive ? " is-active" : "")}
        title="Image Doctor — detect & fix problem photos"
      >
        <span className="icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M9 3v18M15 3v18M3 9h18M3 15h18" />
            <circle cx="12" cy="12" r="3" fill="currentColor" opacity="0.4" />
          </svg>
        </span>
        Doctor
      </NavLink>

      <div className="rail-spacer"></div>

      <NavLink
        to="/libraries"
        className={({ isActive }) => "rail-item" + (isActive ? " is-active" : "")}
        title="Libraries — switch or add photo libraries"
      >
        <span className="icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 5v14M8 5v14" />
            <rect x="11" y="4" width="4" height="16" rx="1" />
            <path d="M18 5l3 14-3 .6-2.5-.5" />
          </svg>
        </span>
        Library
      </NavLink>

      <NavLink
        to="/calibrate"
        className={({ isActive }) => "rail-item" + (isActive ? " is-active" : "")}
        title="Calibrate the personal aesthetic model"
      >
        <span className="icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
            <circle cx="12" cy="12" r="4" />
          </svg>
        </span>
        Calibrate
      </NavLink>

      <button
        className="rail-item theme-toggle"
        onClick={toggleTheme}
        title="Toggle light / dark"
      >
        <span className="icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
          </svg>
        </span>
        Theme
      </button>
    </nav>
  );
}
