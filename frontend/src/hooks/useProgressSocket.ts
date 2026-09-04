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
 * `onMessage`, and reconnects with 1s/2s/4s/max-10s backoff. The socket is
 * closed while the tab is hidden and reopened when it becomes visible again,
 * so a backgrounded window costs nothing.
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

    function open() {
      if (disposed || ws || document.visibilityState === "hidden") return;
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
        if (disposed || document.visibilityState === "hidden") return;
        const delay = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)];
        attempt += 1;
        timer = window.setTimeout(open, delay);
      };
    }

    function onVisibility() {
      if (document.visibilityState === "hidden") {
        if (timer !== undefined) {
          window.clearTimeout(timer);
          timer = undefined;
        }
        ws?.close();
      } else if (!ws && timer === undefined) {
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
