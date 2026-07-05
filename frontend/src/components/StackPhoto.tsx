import { useCallback, useEffect, useRef, useState } from "react";

import { getPhotoMoment, setMomentPrimary } from "../api/client";
import type { Moment } from "../api/types";
import { useToggleLike } from "../hooks/useLikes";

/**
 * A thumbnail that knows about burst-stacks.
 *
 * Behaviour:
 *  - When `moment_size > 1`, shows a stack badge ("⛁ N").
 *  - When focused, the user can press `[` / `]` to cycle through stack members.
 *  - The currently-shown sha256 is the "top of stack"; after a brief debounce,
 *    that gets PATCH'd to the backend so the choice persists across reloads.
 *  - Standard click / double-click handlers (passed in) keep working.
 */
export interface StackPhotoProps {
  sha256: string;
  thumbUrl: string;
  momentId?: number | null;
  momentSize?: number | null;
  isFocused: boolean;
  onFocus: () => void;
  /** Called on single click; receives the *currently-displayed* sha. */
  onClick?: (sha: string) => void;
  /** Called on double click; receives the *currently-displayed* sha. */
  onDoubleClick?: (sha: string) => void;
  /** Optional title for tooltip */
  title?: string;
  /** Optional caption-bar children rendered below the image */
  children?: React.ReactNode;
  /** Optional style overrides for the outer cell */
  style?: React.CSSProperties;
  /** Optional className for the outer cell */
  className?: string;
  /** Whether this photo is in the curated/liked set */
  initialLiked?: boolean;
  /** Called whenever like state toggles, with new liked state */
  onLikeChange?: (sha: string, liked: boolean) => void;
}

export default function StackPhoto({
  sha256,
  thumbUrl,
  momentId,
  momentSize,
  isFocused,
  onFocus,
  onClick,
  onDoubleClick,
  title,
  children,
  style,
  className,
  initialLiked = false,
  onLikeChange,
}: StackPhotoProps) {
  const hasStack = !!(momentId && momentSize && momentSize > 1);

  // Stack state — lazy loaded when first cycled
  const [moment, setMoment] = useState<Moment | null>(null);
  const [stackIdx, setStackIdx] = useState(0);
  const [activeSha, setActiveSha] = useState(sha256);
  const [activeThumb, setActiveThumb] = useState(thumbUrl);
  const [liked, setLiked] = useState(initialLiked);
  const persistTimer = useRef<number | null>(null);

  // Reset when the underlying photo changes (e.g. data reload)
  useEffect(() => {
    setActiveSha(sha256);
    setActiveThumb(thumbUrl);
    setMoment(null);
    setStackIdx(0);
    setLiked(initialLiked);
  }, [sha256, thumbUrl, initialLiked]);

  // Bridge the boolean `liked` state to the Record-shaped setter that
  // useToggleLike expects, and forward every update (optimistic set +
  // any revert) to the caller via onLikeChange.
  const setLikedRecord = useCallback(
    (updater: (prev: Record<string, boolean>) => Record<string, boolean>) => {
      setLiked((prevLiked) => {
        const nextLiked = updater({ [activeSha]: prevLiked })[activeSha];
        onLikeChange?.(activeSha, nextLiked);
        return nextLiked;
      });
    },
    [activeSha, onLikeChange],
  );
  const toggleLikeHook = useToggleLike(setLikedRecord);
  const toggleLike = useCallback(() => {
    toggleLikeHook(activeSha, liked);
  }, [toggleLikeHook, activeSha, liked]);

  const ensureMoment = useCallback(async () => {
    if (moment || !hasStack) return moment;
    try {
      const m = await getPhotoMoment(sha256);
      setMoment(m);
      return m;
    } catch {
      return null;
    }
  }, [moment, hasStack, sha256]);

  const cycle = useCallback(
    async (delta: number) => {
      if (!hasStack) return;
      const m = await ensureMoment();
      if (!m || m.members.length === 0) return;
      const next = (stackIdx + delta + m.members.length) % m.members.length;
      setStackIdx(next);
      const mem = m.members[next];
      setActiveSha(mem.sha256);
      setActiveThumb(`/api/thumb/${mem.sha256}`);

      // Persist new primary after a short debounce
      if (persistTimer.current) window.clearTimeout(persistTimer.current);
      persistTimer.current = window.setTimeout(() => {
        setMomentPrimary(m.id, mem.photo_id).catch(() => {
          /* swallow — non-fatal */
        });
      }, 450);
    },
    [hasStack, ensureMoment, stackIdx],
  );

  // Brief "I just swapped" animation
  const [pulseKey, setPulseKey] = useState(0);
  useEffect(() => {
    setPulseKey((k) => k + 1);
  }, [activeSha]);

  // Hotkeys when focused
  useEffect(() => {
    if (!isFocused) return;
    function onKey(e: KeyboardEvent) {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "[") {
        e.preventDefault();
        cycle(-1);
      } else if (e.key === "]") {
        e.preventDefault();
        cycle(1);
      } else if (e.key === "f" || e.key === "F") {
        e.preventDefault();
        toggleLike();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isFocused, cycle, toggleLike]);

  return (
    <div
      className={className}
      style={{
        position: "relative",
        outline: isFocused ? "2px solid var(--md-primary)" : "none",
        outlineOffset: 1,
        borderRadius: 8,
        boxShadow: hasStack
          ? "0 0 0 3px var(--g-yellow), 0 2px 10px rgba(0,0,0,0.25)"
          : undefined,
        ...style,
      }}
      onMouseEnter={onFocus}
      onClick={() => onClick?.(activeSha)}
      onDoubleClick={() => onDoubleClick?.(activeSha)}
      title={title}
    >
      <img
        key={pulseKey}
        src={activeThumb}
        alt=""
        loading="lazy"
        style={{
          display: "block",
          width: "100%",
          height: "100%",
          objectFit: "cover",
          animation: hasStack ? "stack-swap-fade 160ms ease" : undefined,
        }}
      />

      {/* Big stack-of-cards icon top-right of every burst photo */}
      {hasStack && (
        <div
          style={{
            position: "absolute",
            top: 6,
            right: 6,
            display: "flex",
            alignItems: "center",
            gap: 4,
            background: "var(--g-yellow)",
            color: "#000",
            padding: "3px 8px 3px 6px",
            borderRadius: 999,
            fontSize: 11,
            fontFamily: "var(--font-mono)",
            fontWeight: 700,
            boxShadow: "0 2px 6px rgba(0,0,0,0.4)",
            pointerEvents: "auto",
            cursor: "pointer",
          }}
          onClick={(e) => {
            e.stopPropagation();
            cycle(1);
          }}
          title={`Burst of ${momentSize} — focus the photo and press [ ] to cycle`}
        >
          <svg
            viewBox="0 0 24 24"
            width="13"
            height="13"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <rect x="3" y="7" width="14" height="14" rx="2" />
            <rect x="7" y="3" width="14" height="14" rx="2" />
          </svg>
          {(moment ? stackIdx + 1 : 1)}/{momentSize}
        </div>
      )}

      {/* Like heart — top-left, glows when liked */}
      <button
        onClick={(e) => {
          e.stopPropagation();
          toggleLike();
        }}
        title={liked ? "Liked — press F to remove" : "Press F to like"}
        style={{
          position: "absolute",
          top: 6,
          left: 6,
          width: 26,
          height: 26,
          display: "grid",
          placeItems: "center",
          background: liked
            ? "var(--g-red)"
            : "rgba(0,0,0,0.5)",
          color: liked ? "#fff" : "rgba(255,255,255,0.85)",
          border: 0,
          borderRadius: "50%",
          cursor: "pointer",
          padding: 0,
          boxShadow: liked
            ? "0 0 0 2px color-mix(in srgb, var(--g-red) 35%, transparent), 0 2px 6px rgba(0,0,0,0.4)"
            : "0 1px 4px rgba(0,0,0,0.3)",
          transition: "background 120ms ease, transform 120ms ease",
          transform: liked ? "scale(1.08)" : "scale(1)",
        }}
      >
        <svg
          viewBox="0 0 24 24"
          width="14"
          height="14"
          fill={liked ? "currentColor" : "none"}
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
        </svg>
      </button>

      {children}
    </div>
  );
}
