import { useRef, useState } from "react";
import { Link } from "react-router-dom";

import { libraryStatus } from "../api/client";
import { useProgressAuthRejected } from "./ProgressSocketProvider";
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
  const authRejected = useProgressAuthRejected();
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
      // after the run started or ended) can miss both the first and the
      // terminal frame. Spec B4: reconcile against /api/libraries/status on
      // every open, in both directions — clear a pill whose run has finished,
      // and *show* one for a run that started while the socket was down.
      onOpen: () => {
        const current = msgRef.current;
        // A models download is not an indexing run — status can't speak to it,
        // so leave that pill alone.
        if (current?.stage === "models") return;
        libraryStatus()
          .then((st) => {
            if (st.indexing) {
              // Only synthesise when no frame has been seen; a real frame
              // carries the live stage and counts and must win.
              if (!msgRef.current) {
                setMsg({ stage: "index", current: 0, total: 0, message: "Indexing…" });
              }
            } else if (msgRef.current?.stage !== "models") {
              setMsg(null);
            }
          })
          .catch(() => {});
      },
    },
  );

  // The server closed the progress socket with 4401: this browser has no LAN
  // session. Retrying can only fail the same way, so say what to do instead.
  if (authRejected) {
    return (
      <span
        className="indexing-pill is-auth"
        title="This device is not signed in. Open the LAN link Selects printed on the host machine — the one ending in ?token=… — to get access."
      >
        <span className="indexing-pill-dot" aria-hidden="true" />
        <span className="indexing-pill-label">Sign in with the LAN link</span>
      </span>
    );
  }

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
