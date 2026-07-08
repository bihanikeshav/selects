import { useEffect, useMemo, useRef, useState } from "react";

import { ADJUSTMENTS, defaultParams, isDefault, type EditParams } from "./adjustments";
import { EditorEngine } from "./engine";
import "./editor.css";

interface Props {
  /** One or more photos to edit; a filmstrip switches between them. */
  shas: string[];
  onClose: () => void;
}

/** Full-screen non-destructive editor. Edits the 1024px preview live in WebGL and
 *  bakes on Save. Adjustments come from the ADJUSTMENTS registry (add a tool =
 *  one registry entry). Multi-image: edits are kept per-photo in memory; a
 *  filmstrip switches between them and Save bakes the current one. */
export default function PhotoEditor({ shas, onClose }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const engineRef = useRef<EditorEngine | null>(null);
  const rafRef = useRef<number | null>(null);

  const [cur, setCur] = useState(0);
  const sha = shas[cur];
  // Per-photo params kept in memory so switching doesn't lose in-progress edits.
  const [allParams, setAllParams] = useState<Record<string, EditParams>>({});
  const params = allParams[sha] ?? defaultParams();

  const [ready, setReady] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<Set<string>>(new Set());
  const [compare, setCompare] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const groups = useMemo(() => {
    const g: Record<string, typeof ADJUSTMENTS> = {};
    for (const a of ADJUSTMENTS) (g[a.group] ??= []).push(a);
    return g;
  }, []);

  // Init the WebGL engine once.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    try {
      engineRef.current = new EditorEngine(canvas);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      engineRef.current?.dispose();
      engineRef.current = null;
    };
  }, []);

  // Load the current photo's preview + saved params whenever the selection changes.
  useEffect(() => {
    const engine = engineRef.current;
    if (!engine || !sha) return;
    let cancelled = false;
    setReady(false);
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      if (cancelled) return;
      engine.setImage(img, img.naturalWidth, img.naturalHeight);
      setReady(true);
    };
    img.onerror = () => !cancelled && setErr("could not load the photo preview");
    img.src = `/api/preview/${sha}`;

    if (!(sha in allParams)) {
      fetch(`/api/editor/params/${sha}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => {
          if (!cancelled && d && d.params)
            setAllParams((p) => ({ ...p, [sha]: { ...defaultParams(), ...d.params } }));
        })
        .catch(() => {});
    }
    return () => { cancelled = true; };
  }, [sha]); // eslint-disable-line react-hooks/exhaustive-deps

  // Live render on param / compare / photo change.
  useEffect(() => {
    if (!ready) return;
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(() => {
      engineRef.current?.render(compare ? defaultParams() : params);
    });
  }, [params, compare, ready]);

  function set(key: string, value: number) {
    setAllParams((p) => ({ ...p, [sha]: { ...(p[sha] ?? defaultParams()), [key]: value } }));
  }
  function reset() {
    setAllParams((p) => ({ ...p, [sha]: defaultParams() }));
  }

  async function onSave() {
    const engine = engineRef.current;
    if (!engine) return;
    setSaving(true);
    setErr(null);
    try {
      engine.render(params);
      const blob = await engine.toBlob("image/jpeg", 0.92);
      const fd = new FormData();
      fd.append("params", JSON.stringify(params));
      fd.append("image", blob, `${sha}.jpg`);
      const res = await fetch(`/api/editor/save/${sha}`, { method: "POST", body: fd });
      if (!res.ok) throw new Error(`save failed: HTTP ${res.status}`);
      setSaved((s) => new Set(s).add(sha));
      // Advance to the next unsaved photo if there is one.
      const next = shas.findIndex((s2, i) => i > cur && !saved.has(s2) && s2 !== sha);
      if (next >= 0) setCur(next);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="editor-overlay" role="dialog" aria-modal="true">
      <div className="editor-topbar">
        <button className="btn btn-text" onClick={onClose} disabled={saving}>Close</button>
        <span className="editor-title">Edit{shas.length > 1 ? ` · ${cur + 1}/${shas.length}` : ""}</span>
        <div className="editor-top-actions">
          <button
            className="btn btn-text"
            onMouseDown={() => setCompare(true)}
            onMouseUp={() => setCompare(false)}
            onMouseLeave={() => setCompare(false)}
            title="Hold to see the original"
          >
            Compare
          </button>
          <button className="btn btn-text" onClick={reset} disabled={isDefault(params)}>Reset</button>
          <button className="btn btn-filled" onClick={onSave} disabled={saving || !ready}>
            {saving ? "Saving…" : saved.has(sha) ? "Save again" : "Save"}
          </button>
        </div>
      </div>

      <div className="editor-body">
        <div className="editor-stage">
          {err && <p className="onb-error editor-err">{err}</p>}
          <canvas ref={canvasRef} className="editor-canvas" />
        </div>

        <div className="editor-panel">
          {Object.entries(groups).map(([group, items]) => (
            <div key={group} className="editor-group">
              <div className="editor-group-title">{group}</div>
              {items.map((a) => {
                const v = params[a.key] ?? a.default;
                return (
                  <div key={a.key} className="editor-slider">
                    <div className="editor-slider-head">
                      <span>{a.label}</span>
                      <span className={"editor-slider-val" + (v !== a.default ? " is-set" : "")}>
                        {v > 0 && a.default === 0 ? `+${v}` : v}
                      </span>
                    </div>
                    <input
                      type="range"
                      min={a.min} max={a.max} step={a.step} value={v}
                      onChange={(e) => set(a.key, parseFloat(e.target.value))}
                      onDoubleClick={() => set(a.key, a.default)}
                    />
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </div>

      {shas.length > 1 && (
        <div className="editor-filmstrip">
          {shas.map((s, i) => (
            <button
              key={s}
              className={"editor-film-thumb" + (i === cur ? " is-current" : "")}
              onClick={() => setCur(i)}
              title={saved.has(s) ? "Saved" : "Edit this one"}
            >
              <img src={`/api/thumb/${s}`} alt="" loading="lazy" />
              {saved.has(s) && <span className="editor-film-saved">✓</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
