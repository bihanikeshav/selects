// Rough per-photo processing cost per stage, CPU vs GPU (seconds). These are
// ballpark constants used only to set expectations before/while indexing runs;
// once a stage is actually processing we prefer its measured live rate.
const PER_PHOTO_SEC: Record<string, { cpu: number; gpu: number }> = {
  index: { cpu: 0.03, gpu: 0.03 },
  video: { cpu: 0.08, gpu: 0.05 },
  classical: { cpu: 0.12, gpu: 0.08 },
  embed: { cpu: 0.9, gpu: 0.12 },
  aesthetic: { cpu: 0.01, gpu: 0.01 },
  tag: { cpu: 0.5, gpu: 0.08 },
  category: { cpu: 0.01, gpu: 0.01 },
  ram_tag: { cpu: 0.8, gpu: 0.12 },
  smart_tag: { cpu: 0.15, gpu: 0.08 },
  face_embed: { cpu: 0.4, gpu: 0.08 },
  persons: { cpu: 0.02, gpu: 0.02 },
  moment: { cpu: 0.02, gpu: 0.02 },
  story: { cpu: 0.02, gpu: 0.02 },
  thematic: { cpu: 0.02, gpu: 0.02 },
  date: { cpu: 0.01, gpu: 0.01 },
};

/** Pipeline stages in the order the backend runs them (DEFAULT_STAGE_ORDER). */
const ORDERED_STAGES = [
  "index",
  "video",
  "classical",
  "embed",
  "aesthetic",
  "tag",
  "category",
  "ram_tag",
  "smart_tag",
  "face_embed",
  "persons",
  "moment",
  "story",
  "thematic",
  "date",
] as const;

export type Stage = (typeof ORDERED_STAGES)[number];

export const STAGE_SEQUENCE: string[] = [...ORDERED_STAGES];

/**
 * Plain-language name for every pipeline stage — the single source of truth
 * for what the onboarding checklist and the rail's indexing pill display.
 * The `satisfies` clause makes a missing label a compile error.
 */
const PIPELINE_STAGE_LABELS = {
  index: "Scanning photos",
  video: "Skimming videos",
  classical: "Checking focus and exposure",
  embed: "Understanding each photo",
  aesthetic: "Scoring looks",
  tag: "Tagging scenes",
  category: "Sorting by subject",
  ram_tag: "Labelling objects",
  smart_tag: "Grouping similar shots",
  face_embed: "Finding faces",
  persons: "Grouping people",
  moment: "Grouping bursts",
  story: "Building stories",
  thematic: "Building collections",
  date: "Grouping by day",
} satisfies Record<Stage, string>;

/** Stage labels plus the two out-of-pipeline stages the socket also emits. */
export const STAGE_LABELS: Record<string, string> = {
  ...PIPELINE_STAGE_LABELS,
  models: "Downloading AI models",
  done: "Done",
  cancelled: "Stopped",
};

export type Backend = "cpu" | "gpu";

/** Rough total processing time for `n` photos on the given backend. */
export function estimateTotalSeconds(n: number, mode: Backend): number {
  if (!n) return 0;
  return STAGE_SEQUENCE.reduce((s, st) => {
    const cost = PER_PHOTO_SEC[st]?.[mode] ?? 0;
    return s + n * cost;
  }, 0);
}

/**
 * Estimate seconds remaining. Uses the current stage's measured rate when we
 * have live progress, and falls back to the constants for stages not yet run.
 */
export function estimateRemainingSeconds(opts: {
  n: number;
  mode: Backend;
  stage: string;
  current: number;
  total: number;
  stageElapsedSec: number;
}): number {
  const { n, mode, stage, current, total, stageElapsedSec } = opts;
  const idx = STAGE_SEQUENCE.indexOf(stage);
  if (idx < 0) return estimateTotalSeconds(n, mode);

  let curRemaining: number;
  if (current > 0 && total > 0 && stageElapsedSec > 1.5) {
    const rate = current / stageElapsedSec; // items/sec, measured
    curRemaining = rate > 0 ? (total - current) / rate : 0;
  } else {
    const cost = PER_PHOTO_SEC[stage]?.[mode] ?? 0.02;
    curRemaining = (n || total) * cost;
  }

  const future = STAGE_SEQUENCE.slice(idx + 1).reduce((s, st) => {
    const cost = PER_PHOTO_SEC[st]?.[mode] ?? 0;
    return s + (n || total) * cost;
  }, 0);
  return Math.max(0, curRemaining + future);
}

/** Human-friendly duration, e.g. "~12 min", "~1h 5m", "under a minute". */
export function fmtDuration(sec: number): string {
  if (!isFinite(sec) || sec <= 0) return "moments";
  const m = Math.round(sec / 60);
  if (m < 1) return "under a minute";
  if (m < 60) return `~${m} min`;
  const h = Math.floor(m / 60);
  const rem = m % 60;
  return rem ? `~${h}h ${rem}m` : `~${h}h`;
}
