import type { ReactNode } from "react";

import Topbar from "./Topbar";

interface PageHeaderProps {
  /** Breadcrumb context shown in the Topbar (e.g. "libraries"). */
  context: string;
  title: string;
  subtitle?: ReactNode;
  /** Optional right-aligned actions (buttons, toggles) on the title row. */
  actions?: ReactNode;
  /** Optional bar rendered directly under the Topbar (e.g. <ModeViewBar />). */
  above?: ReactNode;
  /** Optional secondary control strip under the title row (tabs, filters). */
  controls?: ReactNode;
}

/**
 * Canonical page header shared across every top-level tab. It renders a
 * fixed-height (170px) shell so every view's header lines up pixel-for-pixel,
 * regardless of whether it carries a mode bar or a control strip:
 *
 *   ┌ page-shell (170px) ─────────────────────────┐
 *   │ Topbar (breadcrumb)                          │
 *   │ [above]      — optional ModeViewBar          │
 *   │ page-header  — title + subtitle + actions    │
 *   │ [controls]   — optional tab / filter strip   │
 *   └──────────────────────────────────────────────┘
 *
 * Using one component keeps the header height and typography identical
 * everywhere instead of each view re-inventing an inline-styled title band.
 */
export default function PageHeader({
  context,
  title,
  subtitle,
  actions,
  above,
  controls,
}: PageHeaderProps) {
  return (
    <div className="page-shell">
      <Topbar folder="selects" context={context} />
      {above}
      <div className="page-header">
        <div className="page-header-titles">
          <h1 className="page-title">{title}</h1>
          {subtitle != null && <div className="page-sub">{subtitle}</div>}
        </div>
        {actions != null && <div className="page-actions">{actions}</div>}
      </div>
      {controls != null && <div className="page-controls">{controls}</div>}
    </div>
  );
}
