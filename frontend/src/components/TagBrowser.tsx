import { useEffect, useMemo, useState } from "react";

import { listAllTags, type TagEntry } from "../api/search2";
import "./TagBrowser.css";

interface TagBrowserProps {
  selected: string[];
  onToggle: (tag: string) => void;
  onClear: () => void;
}

export default function TagBrowser({ selected, onToggle, onClear }: TagBrowserProps) {
  const [tags, setTags] = useState<TagEntry[]>([]);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    listAllTags()
      .then(data => {
        if (!cancelled) setTags(data.tags);
      })
      .catch(e => {
        if (!cancelled) setErr(String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const filtered = useMemo(() => {
    const f = filter.trim().toLowerCase();
    if (!f) return tags;
    return tags.filter(t => t.tag.toLowerCase().includes(f));
  }, [tags, filter]);

  return (
    <aside className="tag-browser">
      <div className="tag-browser-header">
        <h2>Tags</h2>
        {selected.length > 0 && (
          <button className="tag-browser-clear" onClick={onClear} type="button">
            clear ({selected.length})
          </button>
        )}
      </div>

      <input
        className="tag-browser-filter"
        type="search"
        placeholder="Filter tags…"
        value={filter}
        onChange={e => setFilter(e.target.value)}
        aria-label="Filter tag list"
      />

      <div className="tag-browser-list">
        {loading && <div className="tag-browser-empty">loading…</div>}
        {err && !loading && <div className="tag-browser-empty error">{err}</div>}
        {!loading && !err && filtered.length === 0 && (
          <div className="tag-browser-empty">no tags match “{filter}”</div>
        )}
        {!loading &&
          !err &&
          filtered.map(t => {
            const active = selected.includes(t.tag);
            return (
              <button
                key={t.tag}
                type="button"
                className={`tag-browser-item${active ? " is-active" : ""}`}
                onClick={() => onToggle(t.tag)}
                aria-pressed={active}
              >
                <span className="tag-browser-item-name">{t.tag}</span>
                <span className="tag-browser-item-count">{t.count}</span>
              </button>
            );
          })}
      </div>
    </aside>
  );
}
