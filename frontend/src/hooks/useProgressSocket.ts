import { useEffect, useRef } from "react";

import {
  useProgressSocketContext,
  type ProgressMsg,
} from "../components/ProgressSocketProvider";

export type { ProgressMsg };

/**
 * Listen on the page's single `/ws/progress` socket.
 *
 * The socket itself — connection, 1s/2s/4s/max-10s backoff, visibility
 * handling and 4401 auth rejection — belongs to `ProgressSocketProvider`
 * (mounted once in `App`). This hook only registers `onMessage` with it, so
 * any number of components can listen without opening extra connections.
 *
 * `onOpen` fires when the shared socket connects, and immediately if it is
 * already connected when this caller subscribes — callers that render run
 * state use it to reconcile against `GET /api/libraries/status`, since the
 * progress bus has no replay and a reconnect can miss frames.
 *
 * `enabled` (default true) lets a caller stay unsubscribed until it has
 * something to listen for; flipping it to false unsubscribes.
 */
export function useProgressSocket(
  onMessage: (msg: ProgressMsg) => void,
  options: { enabled?: boolean; onOpen?: () => void; onClose?: () => void } = {},
) {
  const { enabled = true } = options;
  const ctx = useProgressSocketContext();
  const handlerRef = useRef(onMessage);
  handlerRef.current = onMessage;
  const optionsRef = useRef(options);
  optionsRef.current = options;

  useEffect(() => {
    if (!enabled || !ctx) return;
    return ctx.subscribe({
      onMessage: (msg) => handlerRef.current(msg),
      onOpen: () => optionsRef.current.onOpen?.(),
      onClose: () => optionsRef.current.onClose?.(),
    });
  }, [enabled, ctx]);
}
