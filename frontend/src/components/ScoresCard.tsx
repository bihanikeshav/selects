import { useEffect, useState } from "react";

import type { Photo } from "../api/types";

interface ScoresCardProps {
  photo: Photo | null;
}

interface Histogram {
  bins: number;
  r: number[];
  g: number[];
  b: number[];
  luma: number[];
}

function MiniHistogram({ histogram }: { histogram: Histogram | null }) {
  if (!histogram) {
    return (
      <div
        style={{
          height: 56,
          background: "var(--md-surface-c)",
          borderRadius: 6,
          display: "grid",
          placeItems: "center",
          color: "var(--md-on-surface-var)",
          fontSize: 10,
          fontFamily: "var(--font-mono)",
        }}
      >
        no histogram
      </div>
    );
  }
  // Pre-compute max for normalization
  const max = Math.max(
    ...histogram.luma,
    ...histogram.r,
    ...histogram.g,
    ...histogram.b,
    1,
  );
  const w = 160;
  const h = 50;
  const bw = w / histogram.bins;

  const channels = [
    { data: histogram.r, color: "rgba(234,67,53,0.62)" },   // g-red
    { data: histogram.g, color: "rgba(52,168,83,0.55)" },   // g-green
    { data: histogram.b, color: "rgba(66,133,244,0.55)" },  // g-blue
    { data: histogram.luma, color: "rgba(255,255,255,0.85)" },
  ];

  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      width="100%"
      height={h}
      style={{ display: "block", background: "#0d0d0f", borderRadius: 6 }}
    >
      {channels.map((ch, ci) => {
        const path = ch.data
          .map((v, i) => {
            const x = i * bw;
            const y = h - (v / max) * h;
            return `${i === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
          })
          .concat([`L${w},${h}`, `L0,${h}Z`])
          .join(" ");
        return (
          <path
            key={ci}
            d={path}
            fill={ch.color}
            opacity={ci === 3 ? 1 : 0.55}
          />
        );
      })}
    </svg>
  );
}

export default function ScoresCard({ photo }: ScoresCardProps) {
  const [hist, setHist] = useState<Histogram | null>(null);

  useEffect(() => {
    setHist(null);
    if (!photo?.sha256) return;
    let cancelled = false;
    fetch(`/api/doctor/histogram/${photo.sha256}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => {
        if (!cancelled) setHist(j);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [photo?.sha256]);

  const rejected = photo?.auto_reject ?? false;

  return (
    <div className="scores-card">
      <div className="scores-head">
        <span className="label">Photo</span>
        {rejected && (
          <span
            style={{
              padding: "1px 7px",
              borderRadius: 999,
              background: "color-mix(in srgb, var(--g-red) 18%, transparent)",
              color: "var(--g-red)",
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              fontWeight: 500,
            }}
          >
            auto-reject
          </span>
        )}
      </div>

      <MiniHistogram histogram={hist} />

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "auto 1fr",
          rowGap: 2,
          columnGap: 8,
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          color: "var(--md-on-surface-var)",
          marginTop: 6,
        }}
      >
        <span>iqa</span>
        <span style={{ color: "var(--md-on-surface)", textAlign: "right" }}>
          {photo?.aesthetic_iqa != null ? photo.aesthetic_iqa.toFixed(3) : "—"}
        </span>
        <span>blur</span>
        <span style={{ color: "var(--md-on-surface)", textAlign: "right" }}>
          {photo?.blur != null ? photo.blur.toFixed(0) : "—"}
        </span>
        <span>faces</span>
        <span style={{ color: "var(--md-on-surface)", textAlign: "right" }}>
          {photo?.faces_count != null ? photo.faces_count : "—"}
        </span>
        {photo?.moment_size && photo.moment_size > 1 && (
          <>
            <span>burst</span>
            <span style={{ color: "var(--md-on-surface)", textAlign: "right" }}>
              {photo.moment_size}
            </span>
          </>
        )}
      </div>
    </div>
  );
}
