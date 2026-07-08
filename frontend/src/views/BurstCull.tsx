import { useEffect, useCallback, useMemo, useRef, useState } from "react";
import { listPhotos, getPhotoMoment, setMomentPrimary } from "../api/client";
import type { Photo, Moment, MomentMember } from "../api/types";
import { useLikeStatus, useToggleLike } from "../hooks/useLikes";
import { useCullKeys } from "../hooks/useCullKeys";
import CompareView from "../components/CompareView";
import Rail from "../components/Rail";
import PageHeader from "../components/PageHeader";
import ModeViewBar from "../components/ModeViewBar";
import KbdFooter from "../components/KbdFooter";
import BurstThumb from "../components/BurstThumb";
import EyesBadge from "../components/EyesBadge";
import ScoresCard from "../components/ScoresCard";

type LoadState = "loading" | "error" | "empty" | "loaded";

interface SwipeSummary {
  total_photos: number;
  kept: number;
  rejected: number;
  skipped: number;
  undecided: number;
}

type SortMode = "aesthetic" | "taken_at" | "random";

/** Numbered badge shown on thumbs selected for compare (V / shift-click). */
const compareSelBadgeStyle: React.CSSProperties = {
  position: "absolute",
  bottom: 4,
  left: 4,
  zIndex: 3,
  width: 18,
  height: 18,
  display: "grid",
  placeItems: "center",
  background: "var(--md-primary)",
  color: "var(--md-on-primary)",
  borderRadius: 999,
  fontFamily: "var(--font-mono)",
  fontSize: 11,
  fontWeight: 700,
  boxShadow: "0 0 0 2px var(--md-surface), 0 1px 4px rgba(0,0,0,0.35)",
  pointerEvents: "none",
};

interface UndoEntry {
  sha: string;
  /** Decision this session had previously recorded for the sha, if any. */
  prevDecision: string | null;
  /** Photo index at the time of the decision, to jump back to on undo. */
  idx: number;
}

export default function BurstCull() {
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [total, setTotal] = useState(0);
  const [idx, setIdx] = useState(0);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [swipeSummary, setSwipeSummary] = useState<SwipeSummary | null>(null);
  const [sortMode, setSortMode] = useState<SortMode>("aesthetic");
  // Quick-sort quality filter (the retired Doctor's buckets): restrict the cull
  // queue to just photos with a given issue so they can be reviewed/rejected fast.
  const [quality, setQuality] = useState<
    null | "underexposed" | "overexposed" | "out_of_focus" | "blurry_keepers"
  >(null);

  // Moment state: when a photo has a moment, we may expand it
  const [expandedMoment, setExpandedMoment] = useState<Moment | null>(null);
  const [momentLoading, setMomentLoading] = useState(false);
  // When a moment is expanded, momentIdx selects within the moment members
  const [momentIdx, setMomentIdx] = useState(0);

  // Poll swipe summary
  useEffect(() => {
    function refresh() {
      fetch("/api/swipes/summary")
        .then((r) => r.json())
        .then(setSwipeSummary)
        .catch(() => {});
    }
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoadState("loading");
    // When filtering to a quality bucket, don't collapse moments — we want every
    // matching photo, not just burst primaries.
    listPhotos({
      limit: 200,
      collapse: quality ? "none" : "moments",
      sort: sortMode,
      quality: quality ?? undefined,
    })
      .then((data) => {
        if (cancelled) return;
        if (data.items.length === 0) {
          setLoadState("empty");
        } else {
          setPhotos(data.items);
          setTotal(data.total);
          setIdx(0);
          setLoadState("loaded");
        }
      })
      .catch(() => {
        if (!cancelled) setLoadState("error");
      });
    return () => { cancelled = true; };
  }, [sortMode, quality]);

  // Reset moment expansion, per-photo edit toggles and the compare
  // selection when navigating to a different photo/group.
  useEffect(() => {
    setExpandedMoment(null);
    setMomentIdx(0);
    setBurstLiked({});
    setCompareSel([]);
  }, [idx]);

  // Liked status for every member of the expanded burst — so the badge can
  // show how many of the stack the user has liked and the pip strip can
  // highlight liked alternates. Refetches whenever the expanded moment
  // changes.
  const { liked: burstLiked, setLiked: setBurstLiked } = useLikeStatus(
    expandedMoment ? expandedMoment.members.map((m) => m.sha256) : [],
  );

  // The "current" sha whose like state the F key / like button act on —
  // computed early so it (and the derived liked flag below) are available
  // to the keyboard-shortcut effect further down.
  const currentPhotoForLike = photos[idx] ?? null;
  const activeMemberForLike: MomentMember | null = expandedMoment
    ? (expandedMoment.members[momentIdx] ?? null)
    : null;
  const activeShaForUrl = activeMemberForLike
    ? activeMemberForLike.sha256
    : currentPhotoForLike?.sha256;

  // Liked status for the active photo — refetches whenever it changes.
  const { liked: activeLikedMap, setLiked: setActiveLikedMap } = useLikeStatus(
    activeShaForUrl ? [activeShaForUrl] : [],
  );
  const activeLiked = Boolean(activeShaForUrl && activeLikedMap[activeShaForUrl]);

  // Toggling likes updates both the single active-photo map and the
  // burst-wide map together, so the badge/pip strip and the like button
  // stay in sync from a single swipe POST.
  const setBothLiked = useCallback(
    (updater: (prev: Record<string, boolean>) => Record<string, boolean>) => {
      setActiveLikedMap(updater);
      setBurstLiked(updater);
    },
    [setActiveLikedMap, setBurstLiked],
  );
  const toggleLike = useToggleLike(setBothLiked);

  const expandMoment = useCallback(async (photo: Photo) => {
    if (!photo.moment_id || !photo.sha256) return;
    setMomentLoading(true);
    try {
      const moment = await getPhotoMoment(photo.sha256);
      if (moment) {
        setExpandedMoment(moment);
        setMomentIdx(0);
      }
    } catch {
      // silently fail
    } finally {
      setMomentLoading(false);
    }
  }, []);

  const collapseMoment = useCallback(() => {
    setExpandedMoment(null);
    setMomentIdx(0);
  }, []);

  const prev = useCallback(() => {
    if (expandedMoment) {
      setMomentIdx((i) => Math.max(0, i - 1));
    } else {
      setIdx((i) => Math.max(0, i - 1));
    }
  }, [expandedMoment]);

  const next = useCallback(() => {
    if (expandedMoment) {
      setMomentIdx((i) => Math.min(expandedMoment.members.length - 1, i + 1));
    } else {
      setIdx((i) => Math.min(photos.length - 1, i + 1));
    }
  }, [expandedMoment, photos.length]);

  // Swipe persistence
  async function recordSwipe(sha: string, decision: string) {
    try {
      await fetch(`/api/swipes/${sha}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision }),
      });
    } catch {
      // non-fatal
    }
  }

  // Stack-cycle within the current burst moment. Lazily expands the moment
  // if not already expanded, then advances / regresses momentIdx, and
  // persists the new top-of-stack to the backend (debounced).
  const cycleStackTimer = useRef<number | null>(null);
  const cycleStack = useCallback(
    async (delta: number) => {
      const currentPhotoLocal = photos[idx];
      if (!currentPhotoLocal?.moment_id) return;
      let mom = expandedMoment;
      if (!mom) {
        try {
          mom = await getPhotoMoment(currentPhotoLocal.sha256);
        } catch {
          return;
        }
        if (!mom) return;
        setExpandedMoment(mom);
        setMomentIdx(0);
      }
      const n = mom.members.length;
      if (n === 0) return;
      const next = (momentIdx + delta + n) % n;
      setMomentIdx(next);
      const newPrimary = mom.members[next];
      // Debounced persistence
      if (cycleStackTimer.current) window.clearTimeout(cycleStackTimer.current);
      cycleStackTimer.current = window.setTimeout(() => {
        setMomentPrimary(mom!.id, newPrimary.photo_id).catch(() => {
          /* non-fatal */
        });
      }, 450);
    },
    [photos, idx, expandedMoment, momentIdx],
  );

  // ── Session culling state: undo stack + progress ─────────────────────────
  const [undoStack, setUndoStack] = useState<UndoEntry[]>([]);
  const [sessionCulled, setSessionCulled] = useState(0);
  // Decisions made THIS session (sha -> decision), so undo can restore the
  // previous in-session decision rather than blindly clearing.
  const sessionDecisions = useRef<Map<string, string>>(new Map());

  const decide = useCallback(
    (sha: string, decision: string, advance = true) => {
      const prevDecision = sessionDecisions.current.get(sha) ?? null;
      sessionDecisions.current.set(sha, decision);
      setUndoStack((st) => [...st, { sha, prevDecision, idx }]);
      setSessionCulled((n) => n + 1);
      recordSwipe(sha, decision);
      if (advance) next();
    },
    [idx, next],
  );

  const undo = useCallback(() => {
    if (undoStack.length === 0) return;
    const last = undoStack[undoStack.length - 1];
    // Restore the previous in-session decision; if there was none, reset to
    // "skip" (the closest server-side representation of "undecided").
    recordSwipe(last.sha, last.prevDecision ?? "skip");
    if (last.prevDecision) sessionDecisions.current.set(last.sha, last.prevDecision);
    else sessionDecisions.current.delete(last.sha);
    setUndoStack((st) => st.slice(0, -1));
    setSessionCulled((n) => Math.max(0, n - 1));
    setIdx(last.idx);
  }, [undoStack]);

  // ── Zoom-at-cursor (Z toggles 100%) ──────────────────────────────────────
  const stageImgRef = useRef<HTMLImageElement | null>(null);
  const cursorRef = useRef({ x: 0.5, y: 0.5 });
  const [zoom, setZoom] = useState<{ x: number; y: number; scale: number } | null>(null);
  const toggleZoom = useCallback(() => {
    setZoom((z) => {
      if (z) return null;
      const img = stageImgRef.current;
      // 100% = one image pixel per screen pixel; fall back to 2.5x when the
      // natural size isn't known yet.
      let scale = 2.5;
      if (img && img.naturalWidth > 0 && img.clientWidth > 0) {
        scale = Math.max(1.5, img.naturalWidth / img.clientWidth);
      }
      return { ...cursorRef.current, scale };
    });
  }, []);

  // ── Compare mode: 2-4 frame selection (V key / shift-click) ─────────────
  const [compareSel, setCompareSel] = useState<string[]>([]);
  const [compareOpen, setCompareOpen] = useState(false);
  const toggleCompareSel = useCallback((sha: string) => {
    setCompareSel((prevSel) =>
      prevSel.includes(sha)
        ? prevSel.filter((s) => s !== sha)
        : prevSel.length >= 4
          ? prevSel
          : [...prevSel, sha],
    );
  }, []);
  const compareFrames = useMemo(
    () =>
      compareSel.map((sha, i) => ({
        sha256: sha,
        previewUrl: `/api/preview/${sha}`,
        label: `${i + 1} · ${sha.slice(0, 8)}`,
      })),
    [compareSel],
  );

  // Tab: jump to the next burst group (next collapsed photo with a stack).
  const nextGroup = useCallback(() => {
    collapseMoment();
    setIdx((i) => {
      for (let j = i + 1; j < photos.length; j++) {
        if ((photos[j].moment_size ?? 0) > 1) return j;
      }
      return Math.min(photos.length - 1, i + 1);
    });
  }, [photos, collapseMoment]);

  // Global-in-view keyboard layer: arrows navigate, X/left reject, C/right/
  // space keep, U undo, Z zoom, Tab next burst, V compare-select, Enter
  // opens compare when 2+ frames are selected. Suspended while the compare
  // overlay is open (it handles its own keys).
  useCullKeys({
    enabled: loadState === "loaded" && !compareOpen,
    onPrev: prev,
    onNext: next,
    onReject: () => {
      if (activeShaForUrl) decide(activeShaForUrl, "reject");
    },
    onKeep: () => {
      if (activeShaForUrl) decide(activeShaForUrl, "keep");
    },
    onUndo: undo,
    onZoomToggle: toggleZoom,
    onNextGroup: nextGroup,
    onCompareToggle: () => {
      if (activeShaForUrl) toggleCompareSel(activeShaForUrl);
    },
    onCompareOpen:
      compareSel.length >= 2 ? () => setCompareOpen(true) : undefined,
  });

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (compareOpen) return;
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      const sha = (expandedMoment?.members[momentIdx]?.sha256) || photos[idx]?.sha256;

      // Stack-cycle keys stay inside burst view
      if (e.key === "[") {
        e.preventDefault();
        cycleStack(-1);
        return;
      }
      if (e.key === "]") {
        e.preventDefault();
        cycleStack(1);
        return;
      }

      // Keys that operate on the CURRENT burst alternate without leaving
      // the burst view. F/E/S all toggle state on the displayed photo and
      // are core to the multi-like-within-burst workflow. Every key owned
      // by the useCullKeys layer is also listed so its handlers (which are
      // burst-aware themselves) don't get pre-empted by a collapse here.
      const STAY_IN_BURST = [
        "[", "]", "f", "F", "e", "E", "s", "S",
        "v", "V", "z", "Z", "u", "U", "x", "X", "c", "C",
        " ", "Enter", "Tab",
        "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown",
      ];

      // Any OTHER key while inside burst view pops us back out first,
      // then falls through to its normal handler (j/k/l/d/arrows).
      if (expandedMoment && !STAY_IN_BURST.includes(e.key)) {
        collapseMoment();
        if (e.key === "Escape") {
          e.preventDefault();
          return;
        }
      }

      if (e.key === "j" || e.key === "J") {
        e.preventDefault();
        if (sha) decide(sha, "reject");
        else next();
      } else if (e.key === "k" || e.key === "K") {
        e.preventDefault();
        if (sha) decide(sha, "keep");
        else next();
      } else if (e.key === "l" || e.key === "L") {
        e.preventDefault();
        if (sha) decide(sha, "silver");
        else next();
      } else if (e.key === "f" || e.key === "F") {
        e.preventDefault();
        if (sha) toggleLike(sha, activeLiked);
      } else if (e.key === "e" || e.key === "E") {
        e.preventDefault();
        setEnhancedOn((v) => !v);
      } else if (e.key === "s" || e.key === "S") {
        e.preventDefault();
        setStraightenOn((v) => !v);
      } else if (e.key === "d" || e.key === "D") {
        e.preventDefault();
        if (sha) decide(sha, "reject");
        else next();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [next, expandedMoment, collapseMoment, idx, momentIdx, photos, cycleStack, activeLiked, toggleLike, decide, compareOpen]);

  const currentPhoto = currentPhotoForLike;

  // When a moment is expanded, the "current" view is the selected moment member
  const activeMember = activeMemberForLike;

  const [enhancedOn, setEnhancedOn] = useState(false);
  const [straightenOn, setStraightenOn] = useState(false);

  // Reset enhance/straighten/zoom per photo so each shot is judged fresh
  useEffect(() => {
    setEnhancedOn(false);
    setStraightenOn(false);
    setZoom(null);
  }, [activeShaForUrl]);

  const toggleLikeActive = useCallback(() => {
    if (activeShaForUrl) toggleLike(activeShaForUrl, activeLiked);
  }, [activeShaForUrl, activeLiked, toggleLike]);
  const activePreviewUrl = activeShaForUrl
    ? (enhancedOn || straightenOn
        ? `/api/enhance/${activeShaForUrl}?preset=film&grade=${enhancedOn ? "true" : "false"}&straighten=${straightenOn ? "true" : "false"}`
        : `/api/preview/${activeShaForUrl}`)
    : "";
  const activeFilename = activeMember
    ? activeMember.sha256.slice(0, 8)
    : currentPhoto
      ? (currentPhoto.path.split(/[\\/]/).pop() ?? currentPhoto.path)
      : "";

  const hasMoment = Boolean(currentPhoto?.moment_id && (currentPhoto?.moment_size ?? 0) > 1);

  return (
    <div className="app">
      <Rail />

      <div
        className="workspace"
        style={{
          display: "grid",
          gridTemplateRows: "auto 1fr auto auto",
          height: "100vh",
          maxHeight: "100vh",
          overflow: "hidden",
        }}
      >
        <PageHeader
          context={loadState === "loaded" ? `sort · ${idx + 1} of ${total}` : "sort"}
          title="Sort"
          subtitle={
            loadState === "loaded"
              ? swipeSummary
                ? `${idx + 1} / ${total} · ${swipeSummary.kept} kept · ${swipeSummary.rejected} rejected · ${swipeSummary.undecided} to review`
                : `${total} photos indexed`
              : "Decide what's worth keeping — C keep, X reject"
          }
          above={
            <>
              <ModeViewBar />
              <div className="sort-quality-chips">
                {([
                  [null, "All"],
                  ["underexposed", "Underexposed"],
                  ["overexposed", "Overexposed"],
                  ["out_of_focus", "Out of focus"],
                  ["blurry_keepers", "Blurry"],
                ] as const).map(([key, label]) => (
                  <button
                    key={label}
                    className={"dedup-filter-btn" + (quality === key ? " is-active" : "")}
                    onClick={() => setQuality(key)}
                    title={key ? `Cull only ${label.toLowerCase()} photos` : "All photos"}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </>
          }
          actions={
            <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
              <span style={{ fontSize: 11, color: "var(--md-on-surface-var)", marginRight: 6 }}>
                Sort:
              </span>
              {(["aesthetic", "taken_at", "random"] as const).map((m) => (
                <button
                  key={m}
                  className={`btn ${sortMode === m ? "btn-filled" : "btn-text"}`}
                  style={{ fontSize: 12, padding: "3px 10px" }}
                  onClick={() => setSortMode(m)}
                >
                  {m === "aesthetic" ? "Best ★" : m === "taken_at" ? "Time" : "Random"}
                </button>
              ))}
            </div>
          }
        />

        {/* Cull stage */}
        {loadState === "loaded" && currentPhoto && (
          <section className="cull-stage">
            <div
              className="gold-frame"
              style={{
                // Clip the image when Z-zoomed to 100%
                overflow: "hidden",
                ...(hasMoment
                  ? {
                      // Yellow ring around the frame to scream "this is a stack"
                      boxShadow:
                        "0 0 0 3px var(--g-yellow), 0 14px 40px rgba(0,0,0,0.4)",
                    }
                  : {}),
              }}
            >
              <img
                key={activePreviewUrl}
                ref={stageImgRef}
                src={activePreviewUrl}
                alt={activeFilename}
                onMouseMove={(e) => {
                  // Remember the cursor point (fraction of the un-zoomed
                  // image) so Z zooms exactly where the user is looking.
                  if (!zoom) {
                    const r = e.currentTarget.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) {
                      cursorRef.current = {
                        x: (e.clientX - r.left) / r.width,
                        y: (e.clientY - r.top) / r.height,
                      };
                    }
                  }
                }}
                onClick={() => {
                  if (zoom) setZoom(null);
                }}
                style={{
                  animation: hasMoment ? "stack-swap-fade 200ms ease" : undefined,
                  ...(zoom
                    ? {
                        transform: `scale(${zoom.scale})`,
                        transformOrigin: `${zoom.x * 100}% ${zoom.y * 100}%`,
                        transition: "transform 120ms ease",
                        cursor: "zoom-out",
                      }
                    : {}),
                }}
              />

              {/* Big burst badge — top-left, bright accent, always visible */}
              {hasMoment && (() => {
                const likedCount = expandedMoment
                  ? expandedMoment.members.filter((m) => burstLiked[m.sha256]).length
                  : Object.values(burstLiked).filter(Boolean).length;
                return (
                <div
                  style={{
                    position: "absolute",
                    top: 12,
                    left: 12,
                    zIndex: 2,
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    background: "var(--g-yellow)",
                    color: "#000",
                    padding: "7px 12px 7px 10px",
                    borderRadius: 999,
                    fontFamily: "var(--font-display)",
                    fontSize: 13,
                    fontWeight: 600,
                    boxShadow: "0 4px 14px rgba(0,0,0,0.35)",
                    animation: "burst-pulse 1500ms ease-out 1",
                  }}
                  key={`badge-${activeShaForUrl}`}
                  title={`Burst of ${currentPhoto.moment_size} — [ ] cycle, F likes each independently`}
                >
                  <svg
                    viewBox="0 0 24 24"
                    width="15"
                    height="15"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <rect x="3" y="7" width="14" height="14" rx="2" />
                    <rect x="7" y="3" width="14" height="14" rx="2" />
                  </svg>
                  <span>
                    <span style={{ fontFamily: "var(--font-mono)" }}>
                      {(expandedMoment ? momentIdx + 1 : 1)} / {currentPhoto.moment_size}
                    </span>
                  </span>
                  {likedCount > 0 && (
                    <span
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 3,
                        background: "var(--g-red)",
                        color: "#fff",
                        padding: "2px 8px 2px 6px",
                        borderRadius: 999,
                        fontFamily: "var(--font-mono)",
                        fontSize: 11,
                        fontWeight: 700,
                      }}
                    >
                      <svg
                        viewBox="0 0 24 24"
                        width="11"
                        height="11"
                        fill="currentColor"
                        aria-hidden="true"
                      >
                        <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z" />
                      </svg>
                      {likedCount}
                    </span>
                  )}
                  <button
                    onClick={() => cycleStack(-1)}
                    title="Previous in stack (key: [)"
                    style={{
                      background: "rgba(0,0,0,0.12)",
                      color: "#000",
                      border: 0,
                      borderRadius: 4,
                      padding: "2px 7px",
                      cursor: "pointer",
                      fontFamily: "var(--font-mono)",
                      fontWeight: 700,
                      fontSize: 13,
                    }}
                  >
                    [
                  </button>
                  <button
                    onClick={() => cycleStack(1)}
                    title="Next in stack (key: ])"
                    style={{
                      background: "rgba(0,0,0,0.12)",
                      color: "#000",
                      border: 0,
                      borderRadius: 4,
                      padding: "2px 7px",
                      cursor: "pointer",
                      fontFamily: "var(--font-mono)",
                      fontWeight: 700,
                      fontSize: 13,
                    }}
                  >
                    ]
                  </button>
                </div>
                );
              })()}


              <div
                style={{
                  position: "absolute",
                  top: 12,
                  right: 12,
                  zIndex: 2,
                  display: "flex",
                  gap: 6,
                }}
              >
                <button
                  onClick={toggleLikeActive}
                  title={activeLiked ? "Unlike (F)" : "Like — adds to Curated (F)"}
                  className="cull-action-btn"
                  style={{
                    background: activeLiked ? "var(--g-red)" : "rgba(0,0,0,0.55)",
                    color: "#fff",
                    boxShadow: activeLiked
                      ? "0 0 0 2px color-mix(in srgb, var(--g-red) 35%, transparent), 0 2px 8px rgba(0,0,0,0.3)"
                      : "0 2px 8px rgba(0,0,0,0.3)",
                  }}
                >
                  <svg
                    viewBox="0 0 24 24"
                    width="13"
                    height="13"
                    fill={activeLiked ? "currentColor" : "none"}
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z" />
                  </svg>
                  {activeLiked ? "Liked" : "Like · F"}
                </button>

                <button
                  onClick={() => {
                    if (activeShaForUrl) decide(activeShaForUrl, "reject");
                  }}
                  title="Discard — record a reject and move on (D or X or ←)"
                  className="cull-action-btn"
                  style={{
                    background: "rgba(0,0,0,0.55)",
                    color: "#fff",
                    boxShadow: "0 2px 8px rgba(0,0,0,0.3)",
                  }}
                >
                  <svg
                    viewBox="0 0 24 24"
                    width="13"
                    height="13"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M3 6h18" />
                    <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                    <path d="m5 6 1 14a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2l1-14" />
                  </svg>
                  Discard · D
                </button>
                <button
                  onClick={() => setEnhancedOn((v) => !v)}
                  title="Auto-edit: stretch exposure, lift shadows, recover highlights, white-balance (E)"
                  className="cull-action-btn"
                  style={{
                    background: enhancedOn ? "var(--md-primary)" : "rgba(0,0,0,0.55)",
                    color: enhancedOn ? "var(--md-on-primary)" : "#fff",
                    boxShadow: "0 2px 8px rgba(0,0,0,0.3)",
                  }}
                >
                  {enhancedOn ? "Auto-edited" : "Auto edit · E"}
                </button>
                <button
                  onClick={() => setStraightenOn((v) => !v)}
                  title="Quick auto-straighten (S)"
                  disabled={!activeShaForUrl}
                  className="cull-action-btn"
                  style={{
                    background: straightenOn ? "var(--md-primary)" : "rgba(0,0,0,0.55)",
                    color: straightenOn ? "var(--md-on-primary)" : "#fff",
                    boxShadow: "0 2px 8px rgba(0,0,0,0.3)",
                  }}
                >
                  {straightenOn ? "Straightened" : "Straighten · S"}
                </button>
              </div>
              <div className="gold-overlay">
                <div>
                  <div className="filename">{activeFilename}</div>
                  {expandedMoment && (
                    <button
                      onClick={collapseMoment}
                      style={{
                        marginTop: 4,
                        background: "rgba(255,255,255,0.15)",
                        border: "1px solid rgba(255,255,255,0.3)",
                        borderRadius: 6,
                        color: "#fff",
                        fontSize: 12,
                        padding: "2px 8px",
                        cursor: "pointer",
                      }}
                    >
                      ← back to cull
                    </button>
                  )}
                </div>
                <div className="stamp" title="Gold pick of this burst">
                  <svg viewBox="0 0 24 24" fill="currentColor">
                    <path d="m12 2 2.6 7.3 7.4.5-5.8 4.7 2 7.5L12 17.7 5.8 22l2-7.5L2 9.8l7.4-.5z"/>
                  </svg>
                  {expandedMoment
                    ? `Moment · ${momentIdx + 1} of ${expandedMoment.size}`
                    : `Gold pick · photo ${idx + 1}`}
                </div>
              </div>

              {/* Moment badge — shown when collapsed and a moment exists */}
              {!expandedMoment && hasMoment && (
                <button
                  onClick={() => expandMoment(currentPhoto)}
                  disabled={momentLoading}
                  title={`This photo is part of a moment with ${currentPhoto.moment_size} similar shots. Click to expand.`}
                  style={{
                    position: "absolute",
                    bottom: 48,
                    right: 12,
                    background: "rgba(0,0,0,0.65)",
                    border: "1px solid rgba(255,255,255,0.25)",
                    borderRadius: 8,
                    color: "#fff",
                    fontSize: 12,
                    fontWeight: 600,
                    padding: "4px 10px",
                    cursor: momentLoading ? "wait" : "pointer",
                    backdropFilter: "blur(4px)",
                    display: "flex",
                    alignItems: "center",
                    gap: 4,
                  }}
                >
                  <svg viewBox="0 0 24 24" fill="currentColor" style={{ width: 14, height: 14 }}>
                    <path d="M4 6h16v2H4zm0 5h16v2H4zm0 5h16v2H4z"/>
                  </svg>
                  +{(currentPhoto.moment_size ?? 1) - 1} similar shots
                </button>
              )}
            </div>

            <aside className="cull-side">
              <ScoresCard photo={currentPhoto} />
              {activeShaForUrl && <EyesBadge sha256={activeShaForUrl} />}
              <div className="burst-strip" aria-label="Photos">
                {expandedMoment ? (
                  expandedMoment.members.map((member, memberI) => {
                    const selPos = compareSel.indexOf(member.sha256);
                    return (
                      <div
                        key={member.photo_id}
                        style={{ position: "relative" }}
                        onClickCapture={(e) => {
                          if (e.shiftKey) {
                            e.preventDefault();
                            e.stopPropagation();
                            toggleCompareSel(member.sha256);
                          }
                        }}
                        title="Shift-click to add to compare"
                      >
                        <BurstThumb
                          src={member.thumb_url}
                          badge={String(memberI + 1)}
                          isGold={memberI === momentIdx}
                          isLiked={burstLiked[member.sha256] === true}
                          onClick={() => setMomentIdx(memberI)}
                          alt={`Moment member ${memberI + 1}`}
                        />
                        <EyesBadge sha256={member.sha256} overlay />
                        {selPos >= 0 && (
                          <span style={compareSelBadgeStyle}>{selPos + 1}</span>
                        )}
                      </div>
                    );
                  })
                ) : (
                  photos.slice(Math.max(0, idx - 3), idx + 8).map((p, relI) => {
                    const absI = Math.max(0, idx - 3) + relI;
                    const thumbFilename = p.path.split(/[\\/]/).pop() ?? p.path;
                    const selPos = compareSel.indexOf(p.sha256);
                    return (
                      <div
                        key={p.id}
                        style={{ position: "relative" }}
                        onClickCapture={(e) => {
                          if (e.shiftKey) {
                            e.preventDefault();
                            e.stopPropagation();
                            toggleCompareSel(p.sha256);
                          }
                        }}
                        title="Shift-click to add to compare"
                      >
                        <BurstThumb
                          src={p.thumb_url}
                          badge={p.moment_size && p.moment_size > 1 ? `+${p.moment_size - 1}` : String(absI + 1)}
                          isGold={absI === idx}
                          onClick={() => setIdx(absI)}
                          alt={thumbFilename}
                        />
                        {selPos >= 0 && (
                          <span style={compareSelBadgeStyle}>{selPos + 1}</span>
                        )}
                      </div>
                    );
                  })
                )}
              </div>
            </aside>
          </section>
        )}

        {loadState === "loading" && (
          <section className="cull-stage" style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
            <div style={{ color: "var(--md-on-surface-var)", fontFamily: "var(--font-display)" }}>
              Loading photos...
            </div>
          </section>
        )}

        {loadState === "error" && (
          <section className="cull-stage" style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
            <div style={{ textAlign: "center", color: "var(--md-on-surface-var)", fontFamily: "var(--font-display)", lineHeight: 1.6 }}>
              <div style={{ fontSize: 18, fontWeight: 500, color: "var(--md-on-surface)", marginBottom: 8 }}>
                Indexer not running
              </div>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 13, background: "var(--md-surface-c)", padding: "8px 16px", borderRadius: "var(--r-md)", display: "inline-block" }}>
                selects serve Z:\Ladakh\Photos
              </div>
              <div style={{ marginTop: 8, fontSize: 13 }}>
                Run that in another terminal, then refresh.
              </div>
            </div>
          </section>
        )}

        {loadState === "empty" && (
          <section className="cull-stage" style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
            <div style={{ textAlign: "center", color: "var(--md-on-surface-var)", fontFamily: "var(--font-display)" }}>
              <div style={{ fontSize: 18, fontWeight: 500, color: "var(--md-on-surface)", marginBottom: 8 }}>
                No photos indexed yet
              </div>
              <div style={{ fontSize: 13 }}>
                Point selects at a folder to get started.
              </div>
            </div>
          </section>
        )}

        {/* Progress strip: session cull progress + undo depth + compare bar */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 14,
            padding: "5px 24px",
            fontSize: 11,
            fontFamily: "var(--font-mono)",
            color: "var(--md-on-surface-var)",
            borderTop: "1px solid var(--md-outline-var)",
            background: "var(--md-surface-c-low)",
          }}
        >
          <span title="Keep/reject decisions made this session">
            {sessionCulled} of {total} culled this session
          </span>
          <span title="Press U to undo the most recent decision">
            undo ×{undoStack.length}
          </span>
          <div style={{ flex: 1 }} />
          {zoom && <span>100% zoom — Z or click exits</span>}
          {compareSel.length > 0 && (
            <>
              <span>
                {compareSel.length}/4 selected for compare (V / shift-click)
              </span>
              <button
                className="btn btn-text"
                style={{ fontSize: 11, padding: "1px 8px" }}
                onClick={() => setCompareSel([])}
              >
                Clear
              </button>
              <button
                className="btn btn-filled"
                style={{ fontSize: 11, padding: "1px 10px" }}
                onClick={() => setCompareOpen(true)}
                disabled={compareSel.length < 2}
                title="Open side-by-side compare (Enter)"
              >
                Compare
              </button>
            </>
          )}
        </div>

        <KbdFooter />
      </div>

      {compareOpen && compareFrames.length >= 2 && (
        <CompareView
          frames={compareFrames}
          onClose={() => setCompareOpen(false)}
          onDecision={(sha, d) => decide(sha, d, false)}
        />
      )}
    </div>
  );
}
