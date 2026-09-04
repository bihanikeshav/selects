import { useCallback, useEffect, useState } from "react";

import { deleteSwipe, recordSwipe } from "../api/client";

/**
 * Fetches keep status for a set of photos from `/api/likes/status` and keeps
 * it in state, refetching whenever the set of shas changes.
 *
 * Returns the current map (sha256 -> kept) plus its setter, so callers can
 * apply optimistic updates (see `useToggleKeep`).
 */
export function useKeepStatus(shas: string[]) {
  const [kept, setKept] = useState<Record<string, boolean>>({});
  const key = shas.join(",");

  useEffect(() => {
    if (shas.length === 0) return;
    const controller = new AbortController();
    fetch("/api/likes/status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ shas }),
      signal: controller.signal,
    })
      .then((r) => (r.ok ? r.json() : {}))
      .then((j: Record<string, boolean>) => setKept(j))
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        // Any other failure (network error, bad JSON) leaves `kept` as-is.
      });
    return () => {
      controller.abort();
    };
    // `key` is the stable, content-based representation of `shas`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return { kept, setKept };
}

/**
 * Returns a function `(sha, currentlyKept) => void` that optimistically flips
 * the keep state for a photo, persists it (`POST` a `keep` decision to keep,
 * `DELETE` the swipe to return it to undecided), and reverts the optimistic
 * update if the request fails.
 *
 * `setKept` is expected to be a `Record<string, boolean>` state setter — pass
 * a function that forwards the update to several such setters if multiple
 * views of "kept" need to stay in sync.
 */
export function useToggleKeep(
  setKept: (updater: (prev: Record<string, boolean>) => Record<string, boolean>) => void,
) {
  return useCallback(
    (sha: string, currentlyKept: boolean) => {
      const next = !currentlyKept;
      setKept((prev) => ({ ...prev, [sha]: next }));
      const request = next ? recordSwipe(sha, "keep") : deleteSwipe(sha);
      request.catch(() => {
        setKept((prev) => ({ ...prev, [sha]: currentlyKept }));
      });
    },
    [setKept],
  );
}
