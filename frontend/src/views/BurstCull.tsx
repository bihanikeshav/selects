import { useEffect, useCallback, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  deleteSwipe,
  getPhotoMoment,
  listPhotos,
  recordSwipe,
  setMomentPrimary,
  swipeSummary,
} from "../api/client";
import type { Photo, Moment, MomentMember, SwipeSummary } from "../api/types";
import { useKeepStatus } from "../hooks/useKeep";
import { useCullKeys } from "../hooks/useCullKeys";
import CompareView from "../components/CompareView";
import Rail from "../components/Rail";
import PageHeader from "../components/PageHeader";
import ModeViewBar from "../components/ModeViewBar";
import KbdFooter from "../components/KbdFooter";
import BurstThumb from "../components/BurstThumb";
import EyesBadge from "../components/EyesBadge";
import ScoresCard from "../components/ScoresCard";
import ReviewHeaderControls from "../components/ReviewHeaderControls";
import type { ReviewQuality, ReviewSortMode } from "../components/ReviewHeaderControls";

type LoadState = "loading" | "error" | "empty" | "loaded";

/** The only verdicts the Review screen records. */
type Verdict = "keep" | "reject";

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
  /** Verdict this session had previously recorded for the sha, if any. */
  prevDecision: Verdict | null;
  /** Photo index at the time of the decision, to jump back to on undo. */
  idx: number;
}

export default function BurstCull() {
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [total, setTotal] = useState(0);
  const [idx, setIdx] = useState(0);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [summary, setSummary] = useState<SwipeSummary | null>(null);
  const [sortMode, setSortMode] = useState<ReviewSortMode>("aesthetic");
  // Quality filter: restrict the review queue to photos with a given issue so
  // they can be looked at and rejected fast.
  const [quality, setQuality] = useState<ReviewQuality>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const pagingRef = useRef(false);
  const exhaustedRef = useRef(false);

  // Moment state: when a photo has a moment, we may expand it
  const [expandedMoment, setExpandedMoment] = useState<Moment | null>(null);
  const [momentLoading, setMomentLoading] = useState(false);
  // When a moment is expanded, momentIdx selects within the moment members
  const [momentIdx, setMomentIdx] = useState(0);

  // Verdict tally, refreshed every 15s while the tab is visible.
  const [summaryTick, setSummaryTick] = useState(0);
  const refreshSummary = useCallback(() => {
    swipeSummary("moments")
      .then(setSummary)
      .catch(() => {});
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer: number | null = null;

    function refresh() {
      if (cancelled) return;
      refreshSummary();
    }
    function stop() {
      if (timer !== null) {
        window.clearInterval(timer);
        timer = null;
      }
    }
    function start() {
      if (timer !== null) return;
      timer = window.setInterval(refresh, 15000);
    }
    function onVisibility() {
      if (document.visibilityState === "visible") {
        refresh();
        start();
      } else {
        stop();
      }
    }

    if (document.visibilityState === "visible") {
      refresh();
      start();
    }
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelled = true;
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [refreshSummary]);

  // After a verdict, pull fresh counts without waiting for the next poll.
  useEffect(() => {
    if (summaryTick === 0) return;
    const t = window.setTimeout(refreshSummary, 500);
    return () => window.clearTimeout(t);
  }, [summaryTick, refreshSummary]);

  useEffect(() => {
    let cancelled = false;
    pagingRef.current = false;
    exhaustedRef.current = false;
    setLoadState("loading");
    setLoadError(null);
    setCompareSel([]);
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
        setPhotos(data.items);
        setTotal(data.total);
        setIdx(0);
        setLoadState(data.items.length === 0 ? "empty" : "loaded");
      })
      .catch((e) => {
        if (cancelled) return;
        setPhotos([]);
        setTotal(0);
        setLoadError(e instanceof Error ? e.message : String(e));
        setLoadState("error");
      });
    return () => { cancelled = true; };
  }, [sortMode, quality]);

  // Reset moment expansion when navigating to a different photo/group.
  // compareSel is kept across idx so V / shift-click can span nearby frames.
  useEffect(() => {
    setExpandedMoment(null);
    setMomentIdx(0);
    setBurstKept({});
  }, [idx]);

  // Paginate: when within 20 of the loaded tail, append the next page.
  useEffect(() => {
    if (loadState !== "loaded") return;
    if (photos.length === 0) return;
    if (photos.length >= total) {
      exhaustedRef.current = true;
      return;
    }
    if (idx < photos.length - 20) return;
    if (pagingRef.current || exhaustedRef.current) return;
    pagingRef.current = true;
    let cancelled = false;
    const offset = photos.length;
    listPhotos({
      limit: 200,
      offset,
      collapse: quality ? "none" : "moments",
      sort: sortMode,
      quality: quality ?? undefined,
    })
      .then((data) => {
        if (cancelled) return;
        setTotal(data.total);
        if (data.items.length === 0 || offset >= data.total) {
          exhaustedRef.current = true;
          return;
        }
        setPhotos((prev) => {
          // Same SHA at two paths is two reviewable rows; dedup extra pages by id only.
          const seenId = new Set(prev.map((p) => p.id));
          const extra = data.items.filter((p) => !seenId.has(p.id));
          return extra.length ? [...prev, ...extra] : prev;
        });
      })
      .catch(() => {
        // Leave exhaustedRef false so the next idx tick retries.
      })
      .finally(() => {
        pagingRef.current = false;
      });
    return () => {
      cancelled = true;
    };
  }, [idx, photos.length, total, sortMode, quality, loadState]);

  // Keep status for every member of the expanded burst — so the badge can show
  // how many of the stack are kept and the pip strip can highlight them.
  const { kept: burstKept, setKept: setBurstKept } = useKeepStatus(
    expandedMoment ? expandedMoment.members.map((m) => m.sha256) : [],
  );

  const currentPhoto = photos[idx] ?? null;
  const activeMember: MomentMember | null = expandedMoment
    ? (expandedMoment.members[momentIdx] ?? null)
    : null;
  const activeShaForUrl = activeMember ? activeMember.sha256 : currentPhoto?.sha256;

  // Keep status for the active photo — refetches whenever it changes.
  const { kept: activeKeptMap, setKept: setActiveKeptMap } = useKeepStatus(
    activeShaForUrl ? [activeShaForUrl] : [],
  );
  const activeKept = Boolean(activeShaForUrl && activeKeptMap[activeShaForUrl]);

  // Verdicts update both the single active-photo map and the burst-wide map
  // together, so the badge/pip strip and the Keep button stay in sync.
  const setBothKept = useCallback(
    (updater: (prev: Record<string, boolean>) => Record<string, boolean>) => {
      setActiveKeptMap(updater);
      setBurstKept(updater);
    },
    [setActiveKeptMap, setBurstKept],
  );

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
      const nextIdx = (momentIdx + delta + n) % n;
      setMomentIdx(nextIdx);
      const newPrimary = mom.members[nextIdx];
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

  // ── Session verdict state: undo stack + progress ─────────────────────────
  const [undoStack, setUndoStack] = useState<UndoEntry[]>([]);
  const [sessionDecided, setSessionDecided] = useState(0);
  // Verdicts made THIS session (sha -> verdict), so undo can restore the
  // previous in-session verdict rather than blindly clearing.
  const sessionDecisions = useRef<Map<string, Verdict>>(new Map());

  const decide = useCallback(
    (sha: string, decision: Verdict, advance = true) => {
      const prevDecision = sessionDecisions.current.get(sha) ?? null;
      sessionDecisions.current.set(sha, decision);
      setUndoStack((st) => [...st, { sha, prevDecision, idx }]);
      setSessionDecided((n) => n + 1);
      recordSwipe(sha, decision).catch(() => {
        /* non-fatal */
      });
      setBothKept((prevMap) => ({ ...prevMap, [sha]: decision === "keep" }));
      setSummaryTick((t) => t + 1);
      if (advance) next();
    },
    [idx, next, setBothKept],
  );

  const undo = useCallback(() => {
    if (undoStack.length === 0) return;
    const last = undoStack[undoStack.length - 1];
    // Restore the previous in-session verdict; if there was none, the photo
    // goes back to undecided by deleting the swipe row entirely.
    if (last.prevDecision) {
      sessionDecisions.current.set(last.sha, last.prevDecision);
      recordSwipe(last.sha, last.prevDecision).catch(() => {});
    } else {
      sessionDecisions.current.delete(last.sha);
      deleteSwipe(last.sha).catch(() => {});
    }
    setUndoStack((st) => st.slice(0, -1));
    setSessionDecided((n) => Math.max(0, n - 1));
    setBothKept((prevMap) => ({
      ...prevMap,
      [last.sha]: last.prevDecision === "keep",
    }));
    setSummaryTick((t) => t + 1);
    setIdx(last.idx);
  }, [undoStack, setBothKept]);

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
        previewUrl: `/api/editor/result/${sha}`,
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

  const [enhancedOn, setEnhancedOn] = useState(false);
  const [straightenOn, setStraightenOn] = useState(false);

  // The one keyboard layer for this view. Suspended while the compare overlay
  // is open (it handles its own keys).
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
    onBurstPrev: () => cycleStack(-1),
    onBurstNext: () => cycleStack(1),
    onEnhance: () => setEnhancedOn((v) => !v),
    onStraighten: () => setStraightenOn((v) => !v),
    onCompareToggle: () => {
      if (activeShaForUrl) toggleCompareSel(activeShaForUrl);
    },
    onCompareOpen:
      compareSel.length >= 2 ? () => setCompareOpen(true) : undefined,
  });

  // Reset enhance/straighten/zoom per photo so each shot is judged fresh
  useEffect(() => {
    setEnhancedOn(false);
    setStraightenOn(false);
    setZoom(null);
  }, [activeShaForUrl]);

  const activePreviewUrl = activeShaForUrl
    ? (enhancedOn || straightenOn
        ? `/api/enhance/${activeShaForUrl}?preset=film&grade=${enhancedOn ? "true" : "false"}&straighten=${straightenOn ? "true" : "false"}`
        : `/api/editor/result/${activeShaForUrl}`)
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
          context="Review"
          title="Review"
          subtitle={
            loadState === "loaded"
              ? summary
                ? `Photo ${idx + 1} of ${total} · ${summary.kept} kept · ${summary.rejected} rejected · ${summary.undecided} to review`
                : `Photo ${idx + 1} of ${total}`
              : "Decide what's worth keeping — K keep, X reject"
          }
          above={<ModeViewBar />}
          actions={
            <ReviewHeaderControls
              quality={quality}
              onQuality={setQuality}
              sortMode={sortMode}
              onSortMode={setSortMode}
            />
          }
        />

        {/* Review stage */}
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
                onError={(e) => {
                  const img = e.currentTarget;
                  if (!activeShaForUrl || img.src.includes("/api/preview/")) return;
                  img.src = `/api/preview/${activeShaForUrl}`;
                }}
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

              {/* Burst badge — top-left, bright accent, always visible */}
              {hasMoment && (() => {
                const keptCount = expandedMoment
                  ? expandedMoment.members.filter((m) => burstKept[m.sha256]).length
                  : Object.values(burstKept).filter(Boolean).length;
                return (
                <div
                  className="burst-badge"
                  key={`badge-${activeShaForUrl}`}
                  title={`Burst of ${currentPhoto.moment_size} — [ ] cycle, K keeps each frame independently`}
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
                  <span className="burst-badge-count">
                    {(expandedMoment ? momentIdx + 1 : 1)} / {currentPhoto.moment_size}
                  </span>
                  {keptCount > 0 && (
                    <span className="burst-badge-kept">{keptCount} kept</span>
                  )}
                  <button
                    type="button"
                    className="burst-badge-step"
                    onClick={() => cycleStack(-1)}
                    title="Previous in stack (key: [)"
                  >
                    [
                  </button>
                  <button
                    type="button"
                    className="burst-badge-step"
                    onClick={() => cycleStack(1)}
                    title="Next in stack (key: ])"
                  >
                    ]
                  </button>
                </div>
                );
              })()}

              <div className="cull-actions">
                <button
                  type="button"
                  onClick={() => {
                    if (activeShaForUrl) decide(activeShaForUrl, "keep");
                  }}
                  title="Keep this photo — adds it to Curated (K)"
                  aria-pressed={activeKept}
                  className={`cull-action-btn${activeKept ? " is-kept" : ""}`}
                >
                  <svg
                    viewBox="0 0 24 24"
                    width="13"
                    height="13"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.4"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M20 6 9 17l-5-5" />
                  </svg>
                  {activeKept ? "Kept" : "Keep · K"}
                </button>

                <button
                  type="button"
                  onClick={() => {
                    if (activeShaForUrl) decide(activeShaForUrl, "reject");
                  }}
                  title="Reject this photo and move on (X)"
                  className="cull-action-btn"
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
                  Reject · X
                </button>
                <button
                  type="button"
                  onClick={() => setEnhancedOn((v) => !v)}
                  title="Auto edit: stretch exposure, lift shadows, recover highlights, white-balance (E)"
                  aria-pressed={enhancedOn}
                  className={`cull-action-btn${enhancedOn ? " is-on" : ""}`}
                >
                  {enhancedOn ? "Auto edited" : "Auto edit · E"}
                </button>
                <button
                  type="button"
                  onClick={() => setStraightenOn((v) => !v)}
                  title="Quick auto-straighten (S)"
                  disabled={!activeShaForUrl}
                  aria-pressed={straightenOn}
                  className={`cull-action-btn${straightenOn ? " is-on" : ""}`}
                >
                  {straightenOn ? "Straightened" : "Straighten · S"}
                </button>
              </div>
              <div className="gold-overlay">
                <div>
                  <div className="filename">{activeFilename}</div>
                  {expandedMoment && (
                    <button
                      type="button"
                      className="gold-overlay-back"
                      onClick={collapseMoment}
                    >
                      ← back to review
                    </button>
                  )}
                </div>
                {expandedMoment && (
                  <div className="stamp" title="Position inside this burst">
                    {`Burst · ${momentIdx + 1} of ${expandedMoment.size}`}
                  </div>
                )}
              </div>

              {/* Moment badge — shown when collapsed and a moment exists */}
              {!expandedMoment && hasMoment && (
                <button
                  type="button"
                  className="cull-similar-btn"
                  onClick={() => expandMoment(currentPhoto)}
                  disabled={momentLoading}
                  title={`This photo is part of a burst of ${currentPhoto.moment_size} similar shots. Click to expand.`}
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
                          isKept={burstKept[member.sha256] === true}
                          onClick={() => setMomentIdx(memberI)}
                          alt={`Burst frame ${memberI + 1}`}
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
          <section className="cull-stage cull-stage--skeleton" aria-busy="true">
            <div className="cull-skeleton-hero" />
            <div className="cull-skeleton-film">
              {Array.from({ length: 8 }, (_, i) => (
                <div key={i} className="skeleton-tile" />
              ))}
            </div>
          </section>
        )}

        {loadState === "error" && (
          <section className="cull-stage cull-stage--message">
            <div className="cull-message">
              <div className="cull-message-title">Couldn't load photos</div>
              <div className="cull-message-detail">{loadError || "no active library"}</div>
            </div>
          </section>
        )}

        {loadState === "empty" && (
          <section className="cull-stage cull-stage--message">
            <div className="cull-message">
              <div className="cull-message-title">
                {quality ? "No photos in that bucket" : "No photos indexed yet"}
              </div>
              <div className="cull-message-body">
                {quality ? (
                  "Try another option in Show, or All."
                ) : (
                  <>
                    Point selects at a folder to get started —{" "}
                    <Link to="/libraries">open Libraries</Link>.
                  </>
                )}
              </div>
            </div>
          </section>
        )}

        {/* Progress strip: session progress + undo depth + compare bar */}
        <div className="cull-progress-strip">
          <span title="Keep/reject decisions made this session">
            {sessionDecided} of {total} decided this session
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
                type="button"
                className="btn btn-text"
                style={{ fontSize: 11, padding: "1px 8px" }}
                onClick={() => setCompareSel([])}
              >
                Clear
              </button>
              <button
                type="button"
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
          onClose={() => {
            setCompareOpen(false);
            setCompareSel([]);
          }}
          onDecision={(sha, d) => decide(sha, d, false)}
        />
      )}
    </div>
  );
}
