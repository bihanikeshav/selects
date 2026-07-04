export default function KbdFooter() {
  return (
    <footer className="kbd-footer">
      <span className="kbd-action is-danger">
        <span className="kbd">J</span> reject
      </span>
      <span className="kbd-action is-primary">
        <span className="kbd">K</span> keep gold
      </span>
      <span className="kbd-action is-positive">
        <span className="kbd">L</span> keep gold + silver
      </span>
      <span className="kbd-action">
        <span className="kbd">;</span> keep all
      </span>
      <span className="kbd-action">
        <span className="kbd">1</span>–<span className="kbd">8</span> promote
      </span>
      <span className="kbd-action">
        <span className="kbd">S</span>+N silver
      </span>

      <div className="kbd-footer-spacer"></div>

      <span className="kbd-help">Ctrl+K · command palette</span>
    </footer>
  );
}
