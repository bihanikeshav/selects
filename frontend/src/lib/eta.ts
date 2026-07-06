// Rough per-photo processing cost per stage, CPU vs GPU (seconds). These are
// ballpark constants used only to set expectations before/while indexing runs;
// once a stage is actually processing we prefer its measured live rate.
const PER_PHOTO_SEC: Record<string, { cpu: number; gpu: number }> = {
  index: { cpu: 0.03, gpu: 0.03 },
  classical: { cpu: 0.12, gpu: 0.08 },
  embed: { cpu: 0.9, gpu: 0.12 },
  tag: { cpu: 0.5, gpu: 0.08 },
  story: { cpu: 0.02, gpu: 0.02 },
};

export const STAGE_SEQUENCE = ["index", "classical", "embed", "tag", "story"];

export type Backend = "cpu" | "gpu";

/** Rough total processing time for `n` photos on the given backend. */
export function estimateTotalSeconds(n: number, mode: Backend): number {
  if (!n) return 0;
  return STAGE_SEQUENCE.reduce((s, st) => s + n * PER_PHOTO_SEC[st][mode], 0);
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
    curRemaining = (n || total) * PER_PHOTO_SEC[stage][mode];
  }

  const future = STAGE_SEQUENCE.slice(idx + 1).reduce(
    (s, st) => s + (n || total) * PER_PHOTO_SEC[st][mode],
    0,
  );
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
