/**
 * Bottom keyboard hint bar. Mirrors the actual keys handled in BurstCull.tsx
 * (its own handler + the useCullKeys layer).
 * Updated 2026-07-06: keyboard-first culling — X/C decisions, undo, zoom,
 * burst jump and compare selection.
 */
export default function KbdFooter() {
  return (
    <footer className="kbd-footer">
      <span className="kbd-action is-positive">
        <span className="kbd">C</span> <span className="kbd">→</span>{" "}
        <span className="kbd">Space</span> keep
      </span>
      <span className="kbd-action is-danger">
        <span className="kbd">X</span> <span className="kbd">←</span> reject
      </span>
      <span className="kbd-action">
        <span className="kbd">U</span> undo
      </span>
      <span className="kbd-action">
        <span className="kbd">Z</span> zoom 100%
      </span>
      <span className="kbd-action is-primary">
        <span className="kbd">V</span> compare 2–4
      </span>
      <span className="kbd-action">
        <span className="kbd">Tab</span> next burst
      </span>
      <span className="kbd-action">
        <span className="kbd">[</span> <span className="kbd">]</span> burst cycle
      </span>
      <span className="kbd-action">
        <span className="kbd">↑</span> <span className="kbd">↓</span> prev / next
      </span>

      <div className="kbd-footer-spacer"></div>

      <span className="kbd-help">
        <span className="kbd">F</span> like ·
        <span className="kbd" style={{ marginLeft: 6 }}>D</span> discard ·
        <span className="kbd" style={{ marginLeft: 6 }}>E</span> enhance ·
        <span className="kbd" style={{ marginLeft: 6 }}>S</span> straighten ·
        <span className="kbd" style={{ marginLeft: 6 }}>J</span>/
        <span className="kbd">K</span>/<span className="kbd">L</span> reject / keep / silver
      </span>
    </footer>
  );
}
