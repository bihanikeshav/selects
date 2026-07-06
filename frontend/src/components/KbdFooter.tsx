/**
 * Bottom keyboard hint bar. Mirrors the keys handled in BurstCull.tsx and the
 * useCullKeys layer, as one consistent single-line chip row grouped by
 * decisions / edit / view / navigation. Kept to a single line so it never wraps
 * and clips against the fixed-height footer; scrolls horizontally if too narrow.
 */
type Chip = { keys: string[]; label: string; tone?: "positive" | "danger" | "primary" };

const GROUPS: Chip[][] = [
  [
    { keys: ["C"], label: "keep", tone: "positive" },
    { keys: ["X"], label: "reject", tone: "danger" },
    { keys: ["L"], label: "silver" },
  ],
  [
    { keys: ["F"], label: "like" },
    { keys: ["D"], label: "discard" },
    { keys: ["E"], label: "enhance" },
    { keys: ["S"], label: "straighten" },
  ],
  [
    { keys: ["Z"], label: "zoom" },
    { keys: ["V"], label: "compare", tone: "primary" },
    { keys: ["U"], label: "undo" },
  ],
  [
    { keys: ["↑", "↓"], label: "prev / next" },
    { keys: ["Tab"], label: "next burst" },
    { keys: ["[", "]"], label: "burst cycle" },
  ],
];

export default function KbdFooter() {
  return (
    <footer className="kbd-footer">
      {GROUPS.map((group, gi) => (
        <div className="kbd-group" key={gi}>
          {group.map((chip) => (
            <span
              className={`kbd-action${chip.tone ? ` is-${chip.tone}` : ""}`}
              key={chip.label}
            >
              {chip.keys.map((k) => (
                <span className="kbd" key={k}>
                  {k}
                </span>
              ))}
              {chip.label}
            </span>
          ))}
          {gi < GROUPS.length - 1 && (
            <span className="kbd-divider" aria-hidden="true" />
          )}
        </div>
      ))}
    </footer>
  );
}
