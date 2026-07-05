import { useEffect, useRef, useState } from "react";

import { fsList } from "../api/client";
import type { FsDir } from "../api/types";

interface Props {
  value: string;
  onChange: (path: string) => void;
}

/**
 * Controlled folder picker: a text input (manual paste still works) plus a
 * "Browse…" button that opens an inline panel driven by `GET /api/fs/list`.
 * Committing a folder calls `onChange` and closes the panel; Escape closes.
 */
export default function FolderPicker({ value, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const [cur, setCur] = useState("");
  const [parent, setParent] = useState<string | null>(null);
  const [dirs, setDirs] = useState<FsDir[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  async function browse(path: string) {
    setLoading(true);
    setErr(null);
    try {
      const data = await fsList(path);
      setCur(data.path);
      setParent(data.parent);
      setDirs(data.dirs);
    } catch (e) {
      // Keep the current listing visible; just surface the detail inline.
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  function openPanel() {
    setOpen(true);
    // Seed the panel from the typed value if any, else drive roots.
    browse(value.trim());
  }

  // Close on Escape and on outside click while open.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    function onDown(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  return (
    <div className="fp" ref={wrapRef}>
      <div className="fp-input-row">
        <input
          className="onb-input onb-input-mono fp-input"
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="C:\Photos\Ladakh-2025"
        />
        <button
          type="button"
          className="btn btn-outlined fp-browse"
          onClick={() => (open ? setOpen(false) : openPanel())}
        >
          Browse…
        </button>
      </div>

      {open && (
        <div className="fp-panel" role="dialog" aria-label="Choose a folder">
          <div className="fp-panel-head">
            <button
              type="button"
              className="fp-up"
              onClick={() => parent !== null && browse(parent)}
              disabled={parent === null || loading}
              title="Parent folder"
              aria-label="Parent folder"
            >
              ↑
            </button>
            <span className="fp-cur" title={cur}>
              {cur || "This PC"}
            </span>
          </div>

          {err && <p className="fp-err">{err}</p>}

          <ul className="fp-list">
            {dirs.length === 0 && !loading && !err && (
              <li className="fp-empty">No subfolders here.</li>
            )}
            {dirs.map((d) => (
              <li key={d.path}>
                <button
                  type="button"
                  className="fp-dir"
                  onClick={() => browse(d.path)}
                  disabled={loading}
                >
                  <span className="fp-dir-ico" aria-hidden="true">📁</span>
                  <span className="fp-dir-name">{d.name}</span>
                </button>
              </li>
            ))}
          </ul>

          <div className="fp-panel-foot">
            <button
              type="button"
              className="btn btn-text"
              onClick={() => setOpen(false)}
            >
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-filled"
              onClick={() => {
                onChange(cur);
                setOpen(false);
              }}
              disabled={!cur}
            >
              Select this folder
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
