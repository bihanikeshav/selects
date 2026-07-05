import { useCallback, useEffect, useState } from "react";

/**
 * Fetches liked status for a set of photos from `/api/likes/status` and
 * keeps it in state, refetching whenever the set of shas changes.
 *
 * Returns the current map (sha256 -> liked) plus its setter, so callers can
 * apply optimistic updates (see `useToggleLike`).
 */
export function useLikeStatus(shas: string[]) {
  const [liked, setLiked] = useState<Record<string, boolean>>({});
  const key = shas.join(",");

  useEffect(() => {
    if (shas.length === 0) return;
    let cancelled = false;
    fetch(`/api/likes/status?shas=${key}`)
      .then((r) => (r.ok ? r.json() : {}))
      .then((j: Record<string, boolean>) => {
        if (!cancelled) setLiked(j);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
    // `key` is the stable, content-based representation of `shas`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return { liked, setLiked };
}

/**
 * Returns a function `(sha, currentlyLiked) => void` that optimistically
 * flips the liked state for a photo, persists it via a swipe decision
 * (`"keep"` to like, `"skip"` to unlike), and reverts the optimistic update
 * if the request fails.
 *
 * `setLiked` is expected to be a `Record<string, boolean>` state setter —
 * pass a function that forwards the update to one or more such setters if
 * multiple views of "liked" need to stay in sync.
 */
export function useToggleLike(
  setLiked: (updater: (prev: Record<string, boolean>) => Record<string, boolean>) => void,
) {
  return useCallback(
    (sha: string, currentlyLiked: boolean) => {
      const newLiked = !currentlyLiked;
      setLiked((prev) => ({ ...prev, [sha]: newLiked }));
      fetch(`/api/swipes/${sha}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision: newLiked ? "keep" : "skip" }),
      }).catch(() => {
        setLiked((prev) => ({ ...prev, [sha]: currentlyLiked }));
      });
    },
    [setLiked],
  );
}
