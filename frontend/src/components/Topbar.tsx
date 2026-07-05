interface TopbarProps {
  folder: string;
  context: string;
}

export default function Topbar({ folder, context }: TopbarProps) {
  return (
    <header className="topbar">
      <div className="topbar-folder">
        <span className="crumb-folder">{folder}</span>
        <span className="crumb-sep">/</span>
        <span className="crumb-context">{context}</span>
      </div>
      <div className="topbar-grow"></div>

      <button className="icon-btn" title="Search">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="11" cy="11" r="7"/>
          <path d="m20 20-3.5-3.5"/>
        </svg>
      </button>
      <button className="icon-btn" title="Command palette">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M8 3H6a3 3 0 0 0-3 3v2m18 0V6a3 3 0 0 0-3-3h-2M3 16v2a3 3 0 0 0 3 3h2m13-5v2a3 3 0 0 1-3 3h-2M9 9h6v6H9z"/>
        </svg>
      </button>
    </header>
  );
}
