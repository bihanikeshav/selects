import { useEffect, useRef } from "react";

/**
 * The single keyboard layer for the Review screen.
 *
 * Bindings (all suppressed while an input/textarea/select/contenteditable is
 * focused, and while any of Ctrl/Meta/Alt is held so browser shortcuts pass
 * through):
 *
 *   ArrowLeft / ArrowRight -> onPrev / onNext   (navigate)
 *   K                  -> onKeep            (keep + advance, caller decides)
 *   X                  -> onReject          (reject + advance)
 *   U                  -> onUndo            (undo last decision)
 *   Z                  -> onZoomToggle      (100% zoom at cursor point)
 *   V                  -> onCompareToggle   (toggle frame in compare selection)
 *   Enter              -> onCompareOpen     (only handled when provided)
 *   Tab                -> onNextGroup       (jump to next burst)
 *   [ / ]              -> onBurstPrev / onBurstNext (cycle within a burst)
 *   E                  -> onEnhance         (auto edit)
 *   S                  -> onStraighten
 *
 * No other letter is bound. Handlers are read through a ref, so inline
 * closures are fine — the listener itself is attached once.
 */
export interface CullKeyHandlers {
  /** Set false to suspend the whole layer (e.g. while a modal is open). */
  enabled?: boolean;
  onPrev?: () => void;
  onNext?: () => void;
  onReject?: () => void;
  onKeep?: () => void;
  onUndo?: () => void;
  onZoomToggle?: () => void;
  onNextGroup?: () => void;
  onCompareToggle?: () => void;
  /** Handled only when defined — leave undefined to let Enter through. */
  onCompareOpen?: () => void;
  onBurstPrev?: () => void;
  onBurstNext?: () => void;
  onEnhance?: () => void;
  onStraighten?: () => void;
}

function isEditableTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  const tag = t.tagName;
  return (
    tag === "INPUT" ||
    tag === "TEXTAREA" ||
    tag === "SELECT" ||
    t.isContentEditable
  );
}

export function useCullKeys(handlers: CullKeyHandlers): void {
  const ref = useRef(handlers);
  ref.current = handlers;

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const h = ref.current;
      if (h.enabled === false) return;
      if (isEditableTarget(e.target)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      switch (e.key) {
        case "ArrowLeft":
          e.preventDefault();
          h.onPrev?.();
          break;
        case "ArrowRight":
          e.preventDefault();
          h.onNext?.();
          break;
        case "k":
        case "K":
          e.preventDefault();
          h.onKeep?.();
          break;
        case "x":
        case "X":
          e.preventDefault();
          h.onReject?.();
          break;
        case "u":
        case "U":
          e.preventDefault();
          h.onUndo?.();
          break;
        case "z":
        case "Z":
          e.preventDefault();
          h.onZoomToggle?.();
          break;
        case "Tab":
          e.preventDefault();
          h.onNextGroup?.();
          break;
        case "[":
          e.preventDefault();
          h.onBurstPrev?.();
          break;
        case "]":
          e.preventDefault();
          h.onBurstNext?.();
          break;
        case "e":
        case "E":
          e.preventDefault();
          h.onEnhance?.();
          break;
        case "s":
        case "S":
          e.preventDefault();
          h.onStraighten?.();
          break;
        case "v":
        case "V":
          e.preventDefault();
          h.onCompareToggle?.();
          break;
        case "Enter":
          if (h.onCompareOpen) {
            e.preventDefault();
            h.onCompareOpen();
          }
          break;
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}
