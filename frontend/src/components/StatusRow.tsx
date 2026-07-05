import type { ReactNode } from "react";

interface StatusRowProps {
  pos?: string;
  keepersCount?: number;
  details?: string;
  right?: ReactNode;
}

/**
 * Lightweight informational row: position counter, keepers tally, and an
 * optional right-aligned slot for view-specific controls (sort toggles etc.).
 *
 * The Cull/Clusters/Stories tabs that used to live here have moved to
 * ``ModeViewBar`` so we don't have two competing navigation bars.
 */
export default function StatusRow({ pos, keepersCount, details, right }: StatusRowProps) {
  return (
    <div className="status-row">
      {pos && <span className="pos">{pos}</span>}
      {pos && <span className="div">·</span>}
      {keepersCount !== undefined && (
        <>
          <span className="keepers">
            <span className="keepers-dot" aria-hidden="true"></span>
            {keepersCount} keepers so far
          </span>
          <span className="div">·</span>
        </>
      )}
      {details && <span className="status-details">{details}</span>}

      <div className="status-row-spacer"></div>

      {right && <div className="status-row-right">{right}</div>}
    </div>
  );
}
