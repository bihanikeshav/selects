import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

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

/** What a `useProgressSocket` caller registers with the provider. */
export interface ProgressSubscriber {
  onMessage: (msg: ProgressMsg) => void;
  onOpen?: () => void;
  onClose?: () => void;
}

export interface ProgressSocketValue {
  /** Register a subscriber; returns the unsubscribe function. */
  subscribe: (sub: ProgressSubscriber) => () => void;
  /** True once the server closed the socket with 4401 (LAN token required). */
  authRejected: boolean;
}

const ProgressSocketContext = createContext<ProgressSocketValue | null>(null);

/** Reconnect delays, in order; the last one repeats. */
const BACKOFF_MS = [1000, 2000, 4000, 10000];

/** Close code the server uses when the LAN token is missing or wrong. */
const AUTH_CLOSE_CODE = 4401;

function socketUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws/progress`;
}

/**
 * Owns the one and only `/ws/progress` client for the page.
 *
 * Every consumer goes through `useProgressSocket`, which registers a handler
 * here instead of opening a socket of its own — so `/libraries` (indexing pill
 * + models card + watch card) holds a single connection, not three.
 *
 * The socket is deliberately kept OPEN while the tab is hidden: the server's
 * progress bus has no replay, so a run that finishes in a background tab would
 * otherwise never be observed (the indexing pill would stay stuck at 87% and
 * onboarding would never reach "done"). What the hidden state does suppress is
 * *reconnect* attempts — backoff only runs while the page is visible, and a
 * pending reconnect is fired as soon as the tab comes back.
 *
 * Because a reconnect can miss frames, subscribers that render run state use
 * `onOpen` to reconcile against `GET /api/libraries/status`. `onOpen` also
 * fires immediately for a subscriber that joins while the socket is already
 * open, so a late joiner reconciles exactly like a fresh connection.
 *
 * A close with code 4401 means the server wants a LAN token: reconnecting
 * would only fail the same way, so the provider stops and flips `authRejected`
 * for the UI to explain.
 */
export function ProgressSocketProvider({ children }: { children: ReactNode }) {
  const subscribersRef = useRef(new Set<ProgressSubscriber>());
  const openRef = useRef(false);
  const [authRejected, setAuthRejected] = useState(false);

  const subscribe = useCallback((sub: ProgressSubscriber) => {
    subscribersRef.current.add(sub);
    // The socket may already be up; a joiner still needs its reconcile pass.
    if (openRef.current) sub.onOpen?.();
    return () => {
      subscribersRef.current.delete(sub);
    };
  }, []);

  useEffect(() => {
    let disposed = false;
    let ws: WebSocket | null = null;
    let timer: number | undefined;
    let attempt = 0;
    // Set when a close happened while the tab was hidden, so the reconnect is
    // deferred to the next "visible" instead of being dropped.
    let reconnectPending = false;
    // Set on a 4401 close: never reconnect, the token will not appear by itself.
    let rejected = false;

    function fanOut(fn: (sub: ProgressSubscriber) => void) {
      for (const sub of Array.from(subscribersRef.current)) fn(sub);
    }

    function open() {
      if (disposed || ws || rejected) return;
      timer = undefined;
      const sock = new WebSocket(socketUrl());
      ws = sock;
      sock.onopen = () => {
        attempt = 0;
        openRef.current = true;
        fanOut((s) => s.onOpen?.());
      };
      sock.onmessage = (ev) => {
        try {
          const raw = JSON.parse(ev.data) as Partial<ProgressMsg>;
          const msg: ProgressMsg = {
            stage: raw.stage ?? "",
            current: raw.current ?? 0,
            total: raw.total ?? 0,
            message: raw.message,
            type: raw.type,
          };
          fanOut((s) => s.onMessage(msg));
        } catch {
          /* ignore malformed frames */
        }
      };
      sock.onclose = (ev) => {
        ws = null;
        openRef.current = false;
        fanOut((s) => s.onClose?.());
        if (disposed) return;
        if (ev.code === AUTH_CLOSE_CODE) {
          rejected = true;
          setAuthRejected(true);
          return;
        }
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
      } else if (!ws && timer === undefined && reconnectPending && !rejected) {
        reconnectPending = false;
        attempt = 0;
        open();
      }
    }

    open();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      disposed = true;
      openRef.current = false;
      document.removeEventListener("visibilitychange", onVisibility);
      if (timer !== undefined) window.clearTimeout(timer);
      ws?.close();
    };
  }, []);

  const value = useMemo<ProgressSocketValue>(
    () => ({ subscribe, authRejected }),
    [subscribe, authRejected],
  );

  return (
    <ProgressSocketContext.Provider value={value}>{children}</ProgressSocketContext.Provider>
  );
}

/** The provider value, or null when rendered outside a provider. */
export function useProgressSocketContext(): ProgressSocketValue | null {
  return useContext(ProgressSocketContext);
}

/** True once the server rejected the socket with 4401 (LAN token required). */
export function useProgressAuthRejected(): boolean {
  return useContext(ProgressSocketContext)?.authRejected ?? false;
}
