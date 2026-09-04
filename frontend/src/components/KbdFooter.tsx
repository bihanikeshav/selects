/**
 * Bottom keyboard hint bar. Cull variant mirrors BurstCull / useCullKeys.
 * Browse variant is for Search/Map/detail lightboxes — no keep/reject/burst
 * chips, just Esc and arrows. Kept to a single line so it never wraps.
 */
type Chip = { keys: string[]; label: string; tone?: "positive" | "danger" | "primary" };
type Variant = "cull" | "browse";

const CULL_GROUPS: Chip[][] = [
  [
    { keys: ["K"], label: "keep", tone: "positive" },
    { keys: ["X"], label: "reject", tone: "danger" },
    { keys: ["U"], label: "undo" },
  ],
  [
    { keys: ["←", "→"], label: "prev / next" },
    { keys: ["Tab"], label: "next burst" },
    { keys: ["[", "]"], label: "burst cycle" },
  ],
  [
    { keys: ["Z"], label: "zoom" },
    { keys: ["V"], label: "compare", tone: "primary" },
    { keys: ["E"], label: "auto edit" },
    { keys: ["S"], label: "straighten" },
  ],
];

const BROWSE_GROUPS: Chip[][] = [
  [
    { keys: ["Esc"], label: "close" },
    { keys: ["←", "→"], label: "prev / next" },
  ],
];

export default function KbdFooter({ variant = "cull" }: { variant?: Variant }) {
  const groups = variant === "browse" ? BROWSE_GROUPS : CULL_GROUPS;
  return (
    <footer className="kbd-footer">
      {groups.map((group, gi) => (
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
          {gi < groups.length - 1 && (
            <span className="kbd-divider" aria-hidden="true" />
          )}
        </div>
      ))}
    </footer>
  );
}
