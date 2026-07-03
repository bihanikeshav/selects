import { useEffect, useCallback, useState } from "react";
import { listPhotos, getPhotoMoment } from "../api/client";
import type { Photo, Moment, MomentMember } from "../api/types";
import Rail from "../components/Rail";
import Topbar from "../components/Topbar";
import StatusRow from "../components/StatusRow";
import KbdFooter from "../components/KbdFooter";
import BurstThumb from "../components/BurstThumb";
import ScoresCard from "../components/ScoresCard";
import MemoryRing from "../components/MemoryRing";

type LoadState = "loading" | "error" | "empty" | "loaded";

export default function BurstCull() {
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [total, setTotal] = useState(0);
  const [idx, setIdx] = useState(0);
  const [loadState, setLoadState] = useState<LoadState>("loading");

  // Moment state: when a photo has a moment, we may expand it
  const [expandedMoment, setExpandedMoment] = useState<Moment | null>(null);
  const [momentLoading, setMomentLoading] = useState(false);
  // When a moment is expanded, momentIdx selects within the moment members
  const [momentIdx, setMomentIdx] = useState(0);

  useEffect(() => {
    let cancelled = false;
    listPhotos({ limit: 200, collapse: "moments" })
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
  }, []);

  // Reset moment expansion when navigating to a different photo
  useEffect(() => {
    setExpandedMoment(null);
    setMomentIdx(0);
  }, [idx]);

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

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "j" || e.key === "ArrowDown" || e.key === "ArrowRight") {
        e.preventDefault();
        next();
      } else if (e.key === "k" || e.key === "ArrowUp" || e.key === "ArrowLeft") {
        e.preventDefault();
        prev();
      } else if (e.key === "Escape" && expandedMoment) {
        e.preventDefault();
        collapseMoment();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [next, prev, expandedMoment, collapseMoment]);

  const currentPhoto = photos[idx] ?? null;

  // When a moment is expanded, the "current" view is the selected moment member
  const activeMember: MomentMember | null = expandedMoment
    ? (expandedMoment.members[momentIdx] ?? null)
    : null;

  const activePreviewUrl = activeMember ? activeMember.preview_url : currentPhoto?.preview_url ?? "";
  const activeFilename = activeMember
    ? activeMember.sha256.slice(0, 8)
    : currentPhoto
      ? (currentPhoto.path.split(/[\\/]/).pop() ?? currentPhoto.path)
      : "";

  // Folder name: take the parent directory of the first photo's path
  const folderName = photos[0]
    ? (() => {
        const parts = photos[0].path.split(/[\\/]/);
        return parts[parts.length - 2] ?? "photos";
      })()
    : "travelcull";

  const hasMoment = Boolean(currentPhoto?.moment_id && (currentPhoto?.moment_size ?? 0) > 1);

  return (
    <div className="app">
      <Rail />

      <div className="workspace">
        <Topbar
          folder={folderName}
          context={loadState === "loaded" ? `photo ${idx + 1} of ${total}` : "cull"}
        />

        <StatusRow
          pos={loadState === "loaded" ? `${idx + 1} / ${total} photos` : undefined}
          details={loadState === "loaded" ? `${total} photos indexed` : undefined}
        />

        {/* Cull stage */}
        {loadState === "loaded" && currentPhoto && (
          <section className="cull-stage">
            <div className="gold-frame">
              <img
                src={activePreviewUrl}
                alt={activeFilename}
              />
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

            <aside className="burst-strip" aria-label="Photos">
              {expandedMoment ? (
                // Expanded moment: show all members in rank order
                expandedMoment.members.map((member, memberI) => (
                  <BurstThumb
                    key={member.photo_id}
                    src={member.thumb_url}
                    badge={String(memberI + 1)}
                    isGold={memberI === momentIdx}
                    onClick={() => setMomentIdx(memberI)}
                    alt={`Moment member ${memberI + 1}`}
                  />
                ))
              ) : (
                // Normal burst view
                photos.slice(Math.max(0, idx - 3), idx + 8).map((p, relI) => {
                  const absI = Math.max(0, idx - 3) + relI;
                  const thumbFilename = p.path.split(/[\\/]/).pop() ?? p.path;
                  return (
                    <BurstThumb
                      key={p.id}
                      src={p.thumb_url}
                      badge={p.moment_size && p.moment_size > 1 ? `+${p.moment_size - 1}` : String(absI + 1)}
                      isGold={absI === idx}
                      onClick={() => setIdx(absI)}
                      alt={thumbFilename}
                    />
                  );
                })
              )}
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
                travelcull serve Z:\Ladakh\Photos
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
                Point travelcull at a folder to get started.
              </div>
            </div>
          </section>
        )}

        {/* Meta row */}
        <section className="meta-row">
          <ScoresCard photo={currentPhoto} />
          <MemoryRing />
        </section>

        <KbdFooter />
      </div>
    </div>
  );
}
