import { useEffect, useRef } from "react";

/**
 * A frame from `/ws/progress`. Indexing frames carry `{stage, current, total,
 * message}`; the folder watcher pushes `{type: "watch", ...}`. Missing numeric
 * fields are normalised to 0 so callers never have to null-check them.
 */
export interface ProgressMsg {
  stage: string;
  current: number;
  total: number;
  message?: string;
  type?: string;
}

/** Reconnect delays, in order; the last one repeats. */
const BACKOFF_MS = [1000, 2000, 4000, 10000];

function socketUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws/progress`;
}

/**
 * The single `/ws/progress` client. Opens one socket, hands every frame to
 * `onMessage`, and reconnects with 1s/2s/4s/max-10s backoff.
 *
 * The socket is deliberately kept OPEN while the tab is hidden: the server's
 * progress bus has no replay, so a run that finishes in a background tab would
 * otherwise never be observed (the indexing pill would stay stuck at 87% and
 * onboarding would never reach "done"). What the hidden state does suppress is
 * *reconnect* attempts — backoff only runs while the page is visible, and a
 * pending reconnect is fired as soon as the tab comes back.
 *
 * Because a reconnect can miss frames, callers that render run state should use
 * `onOpen` to reconcile against `GET /api/libraries/status`.
 *
 * `enabled` (default true) lets a caller keep the socket closed until it has
 * something to listen for — flipping it to false closes the socket for good.
 */
export function useProgressSocket(
  onMessage: (msg: ProgressMsg) => void,
  options: { enabled?: boolean; onOpen?: () => void; onClose?: () => void } = {},
) {
  const { enabled = true } = options;
  const handlerRef = useRef(onMessage);
  handlerRef.current = onMessage;
  const optionsRef = useRef(options);
  optionsRef.current = options;

  useEffect(() => {
    if (!enabled) return;

    let disposed = false;
    let ws: WebSocket | null = null;
    let timer: number | undefined;
    let attempt = 0;
    // Set when a close happened while the tab was hidden, so the reconnect is
    // deferred to the next "visible" instead of being dropped.
    let reconnectPending = false;

    function open() {
      if (disposed || ws) return;
      timer = undefined;
      const sock = new WebSocket(socketUrl());
      ws = sock;
      sock.onopen = () => {
        attempt = 0;
        optionsRef.current.onOpen?.();
      };
      sock.onmessage = (ev) => {
        try {
          const raw = JSON.parse(ev.data) as Partial<ProgressMsg>;
          handlerRef.current({
            stage: raw.stage ?? "",
            current: raw.current ?? 0,
            total: raw.total ?? 0,
            message: raw.message,
            type: raw.type,
          });
        } catch {
          /* ignore malformed frames */
        }
      };
      sock.onclose = () => {
        ws = null;
        optionsRef.current.onClose?.();
        if (disposed) return;
        // Backoff only runs while the page is visible; otherwise remember that
        // a reconnect is owed and do it the moment the tab is shown again.
        if (document.visibilityState === "hidden") {
          reconnectPending = true;
          return;
        }
        const delay = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)];
        attempt += 1;
        timer = window.setTimeout(open, delay);
      };
    }

    function onVisibility() {
      if (document.visibilityState === "hidden") {
        // Keep the live socket open — only pause pending reconnect timers.
        if (timer !== undefined) {
          window.clearTimeout(timer);
          timer = undefined;
          reconnectPending = true;
        }
      } else if (!ws && timer === undefined && reconnectPending) {
        reconnectPending = false;
        attempt = 0;
        open();
      }
    }

    open();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      disposed = true;
      document.removeEventListener("visibilitychange", onVisibility);
      if (timer !== undefined) window.clearTimeout(timer);
      ws?.close();
    };
  }, [enabled]);
}
