import { useEffect, useState } from "react";
import FilerobotImageEditor, { TABS, TOOLS } from "react-filerobot-image-editor";

interface Props {
  sha256: string;
  onClose: () => void;
}

export default function PhotoEditor({ sha256, onClose }: Props) {
  const [src, setSrc] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);

  // We pull the preview JPEG (1024px max) for editing; saving to a sidecar.
  useEffect(() => {
    setSrc(`/api/preview/${sha256}`);
  }, [sha256]);

  async function saveEdit(imageData: { imageBase64?: string; fullName?: string; mimeType?: string }) {
    if (!imageData.imageBase64) return;
    setSaving(true);
    setSaved(null);
    try {
      const res = await fetch(`/api/edits/${sha256}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          data_url: imageData.imageBase64,
          mime: imageData.mimeType ?? "image/jpeg",
        }),
      });
      if (!res.ok) throw new Error(`save failed ${res.status}`);
      const j = await res.json();
      setSaved(`Saved · ${j.bytes_written} bytes → ${j.path}`);
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
        background: "rgba(0,0,0,0.85)",
        zIndex: 100,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: "12px 24px",
          background: "var(--md-surface-c-low)",
          borderBottom: "1px solid var(--md-outline-var)",
        }}
      >
        <button className="btn btn-text" onClick={onClose}>
          ← Close editor
        </button>
        <div style={{ flex: 1, fontFamily: "var(--font-display)", fontSize: 15 }}>
          Editing {sha256.slice(0, 12)}…
        </div>
        {saving && <span style={{ color: "var(--md-on-surface-var)", fontSize: 13 }}>Saving…</span>}
        {saved && (
          <span style={{ color: "var(--g-green)", fontSize: 13 }}>{saved}</span>
        )}
      </div>

      <div style={{ flex: 1, minHeight: 0 }}>
        <FilerobotImageEditor
          source={src}
          onSave={(imageData) => saveEdit(imageData)}
          onClose={onClose}
          annotationsCommon={{ fill: "#1A5DCC" }}
          Text={{ text: "" }}
          Rotate={{ angle: 0, componentType: "slider" }}
          tabsIds={[TABS.ADJUST, TABS.FINETUNE, TABS.FILTERS, TABS.ANNOTATE, TABS.RESIZE]}
          defaultTabId={TABS.ADJUST}
          defaultToolId={TOOLS.CROP}
          savingPixelRatio={2}
          previewPixelRatio={1.5}
          theme={{
            palette: {
              "bg-primary": "var(--md-surface)",
              "accent-primary": "var(--md-primary)",
              "txt-primary": "var(--md-on-surface)",
            },
            typography: {
              fontFamily: '"Google Sans Display", "Roboto Flex", sans-serif',
            },
          }}
        />
      </div>
    </div>
  );
}
