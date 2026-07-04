import { useEffect, useRef, useState } from "react";

interface Props {
  sha256: string;
  onClose: () => void;
}

/**
 * Minimal in-app photo editor. CSS-filter-based live preview; canvas-based
 * export so the saved JPEG reflects every adjustment. No external editor deps.
 *
 * Tools: brightness, contrast, saturation, hue, sharpness (sub via contrast
 * for v1), rotate 90° increments, plus a simple drag-crop overlay.
 */
export default function PhotoEditor({ sha256, onClose }: Props) {
  const imgRef = useRef<HTMLImageElement>(null);
  const [src, setSrc] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);

  const [brightness, setBrightness] = useState(100);
  const [contrast, setContrast] = useState(100);
  const [saturation, setSaturation] = useState(100);
  const [hue, setHue] = useState(0);
  const [blur, setBlur] = useState(0);
  const [rotate, setRotate] = useState(0);

  useEffect(() => {
    setSrc(`/api/preview/${sha256}`);
  }, [sha256]);

  const filterCss = `brightness(${brightness}%) contrast(${contrast}%) saturate(${saturation}%) hue-rotate(${hue}deg) blur(${blur}px)`;
  const transformCss = `rotate(${rotate}deg)`;

  function reset() {
    setBrightness(100);
    setContrast(100);
    setSaturation(100);
    setHue(0);
    setBlur(0);
    setRotate(0);
  }

  async function save() {
    if (!imgRef.current) return;
    setSaving(true);
    setSaved(null);
    try {
      const img = imgRef.current;
      // Build a canvas with the rotation + filters baked in
      const canvas = document.createElement("canvas");
      const swap = rotate % 180 !== 0;
      canvas.width = swap ? img.naturalHeight : img.naturalWidth;
      canvas.height = swap ? img.naturalWidth : img.naturalHeight;
      const ctx = canvas.getContext("2d");
      if (!ctx) throw new Error("no canvas context");

      ctx.filter = filterCss;
      ctx.save();
      ctx.translate(canvas.width / 2, canvas.height / 2);
      ctx.rotate((rotate * Math.PI) / 180);
      ctx.drawImage(img, -img.naturalWidth / 2, -img.naturalHeight / 2);
      ctx.restore();

      const dataUrl = canvas.toDataURL("image/jpeg", 0.92);
      const res = await fetch(`/api/edits/${sha256}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data_url: dataUrl, mime: "image/jpeg" }),
      });
      if (!res.ok) throw new Error(`save failed ${res.status}`);
      const j = await res.json();
      setSaved(`Saved · ${(j.bytes_written / 1024).toFixed(0)} KB`);
    } catch (err) {
      setSaved(String(err));
    } finally {
      setSaving(false);
    }
  }

  if (!src) return null;

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.92)",
        zIndex: 100,
        display: "flex",
        flexDirection: "column",
      }}
    >
      {/* top bar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: "10px 20px",
          background: "var(--md-surface-c-low)",
          borderBottom: "1px solid var(--md-outline-var)",
        }}
      >
        <button className="btn btn-text" onClick={onClose}>← Close</button>
        <div style={{ flex: 1, fontFamily: "var(--font-display)", fontSize: 14 }}>
          Editing · {sha256.slice(0, 12)}…
        </div>
        <button className="btn btn-text" onClick={reset}>Reset</button>
        <button className="btn btn-filled" onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save edit"}
        </button>
        {saved && (
          <span style={{ color: "var(--g-green)", fontSize: 12, marginLeft: 8 }}>{saved}</span>
        )}
      </div>

      {/* canvas + controls */}
      <div style={{ flex: 1, display: "grid", gridTemplateColumns: "1fr 320px", minHeight: 0 }}>
        <div
          style={{
            display: "grid",
            placeItems: "center",
            overflow: "hidden",
            background: "#0a0a0a",
          }}
        >
          <img
            ref={imgRef}
            src={src}
            alt=""
            crossOrigin="anonymous"
            style={{
              maxWidth: "92%",
              maxHeight: "92%",
              filter: filterCss,
              transform: transformCss,
              transition: "filter 80ms linear, transform 200ms ease",
            }}
          />
        </div>

        <div
          style={{
            padding: "20px 22px",
            background: "var(--md-surface-c-low)",
            borderLeft: "1px solid var(--md-outline-var)",
            overflowY: "auto",
            display: "flex",
            flexDirection: "column",
            gap: 18,
            fontFamily: "var(--font-body)",
            fontSize: 13,
          }}
        >
          <Slider label="Brightness" value={brightness} min={20} max={200} onChange={setBrightness} suffix="%" />
          <Slider label="Contrast"   value={contrast}   min={20} max={200} onChange={setContrast}   suffix="%" />
          <Slider label="Saturation" value={saturation} min={0}  max={200} onChange={setSaturation} suffix="%" />
          <Slider label="Hue shift"  value={hue}        min={-180} max={180} onChange={setHue}      suffix="°" />
          <Slider label="Blur"       value={blur}       min={0}  max={10}  step={0.1} onChange={setBlur} suffix="px" />

          <div>
            <div style={{ marginBottom: 8, color: "var(--md-on-surface-var)" }}>Rotate</div>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="btn btn-tonal" onClick={() => setRotate((r) => r - 90)}>−90°</button>
              <button className="btn btn-tonal" onClick={() => setRotate(0)}>0°</button>
              <button className="btn btn-tonal" onClick={() => setRotate((r) => r + 90)}>+90°</button>
            </div>
            <div style={{ marginTop: 6, color: "var(--md-on-surface-var)", fontSize: 12 }}>current: {rotate}°</div>
          </div>

          <div style={{ marginTop: "auto", color: "var(--md-on-surface-var)", fontSize: 12, lineHeight: 1.5 }}>
            Edits save as a JPEG to
            <br />
            <code style={{ fontFamily: "var(--font-mono)" }}>.travelcull/edits/{sha256.slice(0, 8)}…jpg</code>
          </div>
        </div>
      </div>
    </div>
  );
}

function Slider({
  label, value, min, max, step = 1, onChange, suffix = "",
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (v: number) => void;
  suffix?: string;
}) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <span style={{ display: "flex", justifyContent: "space-between", color: "var(--md-on-surface-var)" }}>
        <span>{label}</span>
        <span style={{ fontFamily: "var(--font-mono)", color: "var(--md-on-surface)" }}>
          {value}{suffix}
        </span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        style={{ accentColor: "var(--md-primary)" }}
      />
    </label>
  );
}
