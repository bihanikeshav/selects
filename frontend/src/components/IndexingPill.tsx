import { useRef, useState } from "react";
import { Link } from "react-router-dom";

import { libraryStatus } from "../api/client";
import { useProgressSocket, type ProgressMsg } from "../hooks/useProgressSocket";
import { STAGE_LABELS } from "../lib/eta";

/** Stages that end a run — the pill hides on any of them. */
const END_STAGES = new Set(["done", "cancelled"]);

/**
 * Global "something is indexing" pill in the rail. It listens on the shared
 * `/ws/progress` socket, so it appears on every page as soon as a run starts
 * and disappears when the run finishes, is cancelled, or was never running.
 */
export default function IndexingPill() {
  const [msg, setMsg] = useState<ProgressMsg | null>(null);
  const msgRef = useRef<ProgressMsg | null>(null);
  msgRef.current = msg;

  useProgressSocket(
    (m) => {
      if (m.type === "watch" || m.stage === "watch" || !m.stage) return;
      setMsg(END_STAGES.has(m.stage) ? null : m);
    },
    {
      // The progress bus has no replay, so a reconnect (or a first connect
      // after the run ended) can miss the terminal frame. Ask the server what
      // is actually running and clear a stale pill.
      onOpen: () => {
        const current = msgRef.current;
        // A models download is not an indexing run — status can't speak to it.
        if (!current || current.stage === "models") return;
        libraryStatus()
          .then((st) => {
            if (!st.indexing) setMsg(null);
          })
          .catch(() => {});
      },
    },
  );

  if (!msg) return null;

  const blurb = STAGE_LABELS[msg.stage] ?? msg.message ?? "Working";
  const pct =
    msg.total > 0 ? Math.min(100, Math.round((msg.current / msg.total) * 100)) : null;
  // A models download is not indexing — name it for what it is.
  const label = msg.stage === "models" || pct === null ? blurb : `Indexing · ${pct}%`;

  return (
    <Link
      to="/libraries"
      className="indexing-pill"
      title={`${blurb}${pct !== null ? ` — ${msg.current} of ${msg.total}` : ""}`}
    >
      <span className="indexing-pill-dot" aria-hidden="true" />
      <span className="indexing-pill-label">{label}</span>
    </Link>
  );
}
