import { useEffect, useMemo, useRef, useState } from "react";

import { ADJUSTMENTS, defaultParams, isDefault, type EditParams } from "./adjustments";
import { EditorEngine } from "./engine";
import "./editor.css";

interface Props {
  sha: string;
  onClose: () => void;
}

/** Full-screen non-destructive editor: edits the 1024px preview live in WebGL,
 *  bakes the result on Save. Adjustments come from the ADJUSTMENTS registry, so
 *  the slider list here needs no changes to gain a new tool. */
export default function PhotoEditor({ sha, onClose }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const engineRef = useRef<EditorEngine | null>(null);
  const rafRef = useRef<number | null>(null);
  const [params, setParams] = useState<EditParams>(defaultParams);
  const [ready, setReady] = useState(false);
  const [saving, setSaving] = useState(false);
  const [compare, setCompare] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const groups = useMemo(() => {
    const g: Record<string, typeof ADJUSTMENTS> = {};
    for (const a of ADJUSTMENTS) (g[a.group] ??= []).push(a);
    return g;
  }, []);

  // Init engine + load the preview image and any saved params.
  useEffect(() => {
    let cancelled = false;
    const canvas = canvasRef.current;
    if (!canvas) return;
    let engine: EditorEngine;
    try {
      engine = new EditorEngine(canvas);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      return;
    }
    engineRef.current = engine;

    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      if (cancelled) return;
      engine.setImage(img, img.naturalWidth, img.naturalHeight);
      setReady(true);
    };
    img.onerror = () => !cancelled && setErr("could not load the photo preview");
    img.src = `/api/preview/${sha}`;

    fetch(`/api/editor/params/${sha}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (!cancelled && d && d.params) setParams({ ...defaultParams(), ...d.params }); })
      .catch(() => {});

    return () => {
      cancelled = true;
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      engine.dispose();
      engineRef.current = null;
    };
  }, [sha]);

  // Render whenever params (or compare) change — throttled to a frame.
  useEffect(() => {
    if (!ready) return;
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(() => {
      engineRef.current?.render(compare ? defaultParams() : params);
    });
  }, [params, compare, ready]);

  function set(key: string, value: number) {
    setParams((p) => ({ ...p, [key]: value }));
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
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setSaving(false);
    }
  }

  return (
    <div className="editor-overlay" role="dialog" aria-modal="true">
      <div className="editor-topbar">
        <button className="btn btn-text" onClick={onClose} disabled={saving}>Cancel</button>
        <span className="editor-title">Edit</span>
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
          <button className="btn btn-text" onClick={() => setParams(defaultParams())} disabled={isDefault(params)}>
            Reset
          </button>
          <button className="btn btn-filled" onClick={onSave} disabled={saving || !ready}>
            {saving ? "Saving…" : "Save"}
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
    </div>
  );
}
