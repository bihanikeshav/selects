/**
 * Bottom keyboard hint bar. Mirrors the actual keys handled in BurstCull.tsx.
 * Updated 2026-05-24: new like / discard / enhance / straighten / burst keys.
 */
export default function KbdFooter() {
  return (
    <footer className="kbd-footer">
      <span className="kbd-action is-positive">
        <span className="kbd">F</span> like
      </span>
      <span className="kbd-action is-danger">
        <span className="kbd">D</span> discard
      </span>
      <span className="kbd-action">
        <span className="kbd">E</span> enhance
      </span>
      <span className="kbd-action">
        <span className="kbd">S</span> straighten
      </span>
      <span className="kbd-action">
        <span className="kbd">[</span> <span className="kbd">]</span> burst cycle
      </span>
      <span className="kbd-action">
        <span className="kbd">←</span> <span className="kbd">→</span> prev / next
      </span>

      <div className="kbd-footer-spacer"></div>

      <span className="kbd-help">
        <span className="kbd">J</span> reject ·
        <span className="kbd" style={{ marginLeft: 6 }}>K</span> keep ·
        <span className="kbd" style={{ marginLeft: 6 }}>L</span> silver
      </span>
    </footer>
  );
}
