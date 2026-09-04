/**
 * Compatibility shim for callers not yet migrated to `useKeep`.
 *
 * The keep/reject/undecided verdict model replaced "like"; this file only
 * exists so `views/Stories.tsx` keeps building until it is renamed too.
 * Do not add anything here — import from `./useKeep` instead.
 */
import { useKeepStatus } from "./useKeep";

export { useToggleKeep as useToggleLike } from "./useKeep";

export function useLikeStatus(shas: string[]) {
  const { kept, setKept } = useKeepStatus(shas);
  return { liked: kept, setLiked: setKept };
}
