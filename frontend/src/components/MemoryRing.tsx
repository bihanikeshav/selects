/**
 * MemoryRing — multi-color gradient ring showing Memory Value score.
 * For M1, VL scores are not yet available so we render a dash placeholder.
 */
export default function MemoryRing() {
  return (
    <div className="memory-ring">
      {/* SVG defs must live inside the same document; we inline them here */}
      <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden="true">
        <defs>
          <linearGradient id="memory-gradient" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%"   stopColor="#4285F4"/>
            <stop offset="33%"  stopColor="#34A853"/>
            <stop offset="66%"  stopColor="#FBBC04"/>
            <stop offset="100%" stopColor="#EA4335"/>
          </linearGradient>
        </defs>
      </svg>

      <div className="ring-wrap">
        <svg viewBox="0 0 120 120" aria-hidden="true">
          <circle className="ring-bg"   cx="60" cy="60" r="54" />
          <circle
            className="ring-fill"
            cx="60" cy="60" r="54"
            style={
              { "--ring-len": "339.3", "--ring-offset": "339.3" } as React.CSSProperties
            }
          />
        </svg>
        <div className="ring-num">
          —
        </div>
      </div>
      <div className="ring-label">memory value</div>
    </div>
  );
}
