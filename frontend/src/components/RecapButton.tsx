import { useCallback, useState } from "react";

import { generateRecap } from "../api/recap";
import "./RecapButton.css";

interface Props {
  storyId: number;
}

/**
 * "Generate recap" trigger for a story header — builds a self-contained,
 * shareable HTML keepsake page for the whole trip and opens it in a new tab
 * once ready. Mount this next to a story's title/export controls; not wired
 * into Stories.tsx by this feature (see wiring notes).
 */
export default function RecapButton({ storyId }: Props) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const run = useCallback(async () => {
    setBusy(true);
    setErr(null);
    try {
      const res = await generateRecap(storyId);
      window.open(res.download_url, "_blank", "noopener,noreferrer");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [storyId]);

  return (
    <div className="recap-btn-wrap">
      <button className="btn btn-text recap-btn" onClick={run} disabled={busy}>
        {busy ? "Building recap…" : "✦ Trip recap"}
      </button>
      {err && <span className="recap-btn-error">{err}</span>}
    </div>
  );
}
