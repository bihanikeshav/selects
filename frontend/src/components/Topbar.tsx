import { useNavigate } from "react-router-dom";

import Logo from "./Logo";

interface TopbarProps {
  folder: string;
  context: string;
}

export default function Topbar({ folder, context }: TopbarProps) {
  const navigate = useNavigate();
  return (
    <header className="topbar">
      <div className="topbar-folder">
        <Logo size={18} className="topbar-logo" />
        <span className="crumb-folder">{folder}</span>
        <span className="crumb-sep">/</span>
        <span className="crumb-context">{context}</span>
      </div>
      <div className="topbar-grow"></div>

      <button
        className="icon-btn"
        title="Search photos"
        onClick={() => navigate("/search")}
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="11" cy="11" r="7"/>
          <path d="m20 20-3.5-3.5"/>
        </svg>
      </button>
    </header>
  );
}
