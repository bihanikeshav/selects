export type ReviewSortMode = "aesthetic" | "taken_at" | "random";
export type ReviewQuality =
  | null
  | "underexposed"
  | "overexposed"
  | "out_of_focus"
  | "blurry_keepers";

const QUALITY_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "All" },
  { value: "underexposed", label: "Underexposed" },
  { value: "overexposed", label: "Overexposed" },
  { value: "out_of_focus", label: "Out of focus" },
  { value: "blurry_keepers", label: "Soft but good" },
];

const SORT_OPTIONS: { value: ReviewSortMode; label: string }[] = [
  { value: "aesthetic", label: "Best" },
  { value: "taken_at", label: "Time" },
  { value: "random", label: "Random" },
];

interface Props {
  quality: ReviewQuality;
  onQuality: (q: ReviewQuality) => void;
  sortMode: ReviewSortMode;
  onSortMode: (s: ReviewSortMode) => void;
}

/**
 * The Review header's actions row: a "Show" filter for the quality buckets
 * and the sort-order button group. Kept out of the view so the header stays
 * one short block of markup.
 */
export default function ReviewHeaderControls({
  quality,
  onQuality,
  sortMode,
  onSortMode,
}: Props) {
  return (
    <div className="cull-header-controls">
      <label className="cull-show-filter">
        <span>Show</span>
        <select
          value={quality ?? ""}
          onChange={(e) => onQuality((e.target.value || null) as ReviewQuality)}
        >
          {QUALITY_OPTIONS.map((o) => (
            <option key={o.label} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
      <div className="cull-sort-group" role="group" aria-label="Sort order">
        {SORT_OPTIONS.map((o) => (
          <button
            key={o.value}
            type="button"
            className={`btn ${sortMode === o.value ? "btn-filled" : "btn-text"}`}
            aria-pressed={sortMode === o.value}
            onClick={() => onSortMode(o.value)}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}
