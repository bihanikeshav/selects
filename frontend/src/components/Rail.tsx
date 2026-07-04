import { NavLink } from "react-router-dom";
import { useEffect } from "react";

function toggleTheme() {
  const root = document.documentElement;
  const isDark = root.getAttribute("data-theme") === "dark";
  if (isDark) {
    root.removeAttribute("data-theme");
    localStorage.setItem("tc-theme", "light");
  } else {
    root.setAttribute("data-theme", "dark");
    localStorage.setItem("tc-theme", "dark");
  }
}

export default function Rail() {
  useEffect(() => {
    const saved = localStorage.getItem("tc-theme");
    if (saved === "dark") {
      document.documentElement.setAttribute("data-theme", "dark");
    }
  }, []);

  return (
    <nav className="rail" aria-label="Primary">
      <div className="rail-brand" title="travelcull">
        <span className="dot"></span>tc
      </div>

      <NavLink
        to="/"
        end
        className={({ isActive }) => "rail-item" + (isActive ? " is-active" : "")}
      >
        <span className="icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 6h18M3 12h18M3 18h18"/>
          </svg>
        </span>
        Cull
      </NavLink>

      <NavLink
        to="/clusters"
        className={({ isActive }) => "rail-item" + (isActive ? " is-active" : "")}
      >
        <span className="icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="3" width="7" height="7" rx="1.5"/>
            <rect x="14" y="3" width="7" height="7" rx="1.5"/>
            <rect x="3" y="14" width="7" height="7" rx="1.5"/>
            <rect x="14" y="14" width="7" height="7" rx="1.5"/>
          </svg>
        </span>
        Clusters
      </NavLink>

      <NavLink
        to="/stories"
        className={({ isActive }) => "rail-item" + (isActive ? " is-active" : "")}
      >
        <span className="icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 4h12v16l-6-3-6 3z"/>
            <path d="M16 4h4v16l-2-1"/>
          </svg>
        </span>
        Stories
      </NavLink>

      <div className="rail-spacer"></div>

      <button
        className="rail-item"
        onClick={toggleTheme}
        title="Toggle theme (Ctrl+T)"
      >
        <span className="icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>
          </svg>
        </span>
        Theme
      </button>
    </nav>
  );
}
