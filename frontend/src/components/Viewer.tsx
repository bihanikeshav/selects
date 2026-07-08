import { useCallback, useEffect } from "react";
import "./Viewer.css";

export interface ViewerItem {
  sha256: string;
  caption?: string;
}

interface Props {
  items: ViewerItem[];
  index: number;
  onIndex: (i: number) => void;
  onClose: () => void;
  /** Optional toolbar actions for the current item (e.g. Keep, Edit). */
  renderActions?: (item: ViewerItem, index: number) => React.ReactNode;
  /** Optional: mark filmstrip thumbs (e.g. kept copies in the dedup view). */
  isMarked?: (index: number) => boolean;
  markLabel?: string;
}

/** Shared full-screen photo viewer: preview-res image, prev/next carousel,
 *  keyboard nav, and a filmstrip. Reused across Search / Curated / Duplicates /
 *  Sort so every "open a photo" is the same high-quality experience. */
export default function Viewer({
  items,
  index,
  onIndex,
  onClose,
  renderActions,
  isMarked,
  markLabel,
}: Props) {
  const n = items.length;
  const cur = items[index];

  const go = useCallback(
    (delta: number) => {
      if (n === 0) return;
      onIndex((index + delta + n) % n);
    },
    [index, n, onIndex],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowRight") go(1);
      else if (e.key === "ArrowLeft") go(-1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go, onClose]);

  if (!cur) return null;

  return (
    <div className="viewer" role="dialog" aria-modal="true">
      <div className="viewer-topbar">
        <button className="viewer-icon-btn" onClick={onClose} aria-label="Close" title="Close (Esc)">✕</button>
        <span className="viewer-count">{index + 1} / {n}</span>
        <div className="viewer-actions">{renderActions?.(cur, index)}</div>
      </div>

      <div className="viewer-stage">
        {n > 1 && (
          <button className="viewer-nav viewer-prev" onClick={() => go(-1)} aria-label="Previous" title="←">‹</button>
        )}
        <img className="viewer-img" src={`/api/preview/${cur.sha256}`} alt={cur.caption ?? ""} />
        {n > 1 && (
          <button className="viewer-nav viewer-next" onClick={() => go(1)} aria-label="Next" title="→">›</button>
        )}
      </div>

      {cur.caption && <div className="viewer-caption">{cur.caption}</div>}

      {n > 1 && (
        <div className="viewer-strip">
          {items.map((it, i) => (
            <button
              key={`${it.sha256}:${i}`}
              className={
                "viewer-thumb" +
                (i === index ? " is-current" : "") +
                (isMarked?.(i) ? " is-marked" : "")
              }
              onClick={() => onIndex(i)}
              title={it.caption ?? ""}
            >
              <img src={`/api/thumb/${it.sha256}`} alt="" loading="lazy" />
              {isMarked?.(i) && <span className="viewer-thumb-mark">{markLabel ?? "✓"}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
