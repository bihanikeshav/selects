import type { Photo } from "../api/types";

interface ScoresCardProps {
  photo: Photo | null;
}

interface AxisRowProps {
  axisClass: string;
  name: string;
  value01: number | null;          // already 0..1
  display: string;
}

function AxisRow({ axisClass, name, value01, display }: AxisRowProps) {
  const pct = value01 !== null ? Math.min(100, Math.max(0, value01 * 100)) : 0;
  return (
    <div className={`axis-row ${axisClass}`}>
      <span className="name">{name}</span>
      <span className="bar">
        <span className="fill" style={{ width: `${pct}%` }}></span>
      </span>
      <span className="num">{value01 === null ? "—" : display}</span>
    </div>
  );
}

// Map Laplacian variance to a 0..1 sharpness — clamp at 1500 since anything
// above is "very sharp" and beyond that the eye doesn't care.
function sharpness01(blur: number | null | undefined): number | null {
  if (blur === null || blur === undefined) return null;
  return Math.min(1, blur / 1500);
}

export default function ScoresCard({ photo }: ScoresCardProps) {
  const sharp = sharpness01(photo?.blur);
  const exp = photo?.exposure ?? null;          // already 0..1
  const faces = photo?.faces_count ?? null;
  const rejected = photo?.auto_reject ?? false;

  return (
    <div className="scores-card">
      <div className="scores-head">
        <span className="label">Stage 1 · Classical signals</span>
        {rejected && (
          <span
            style={{
              padding: "2px 8px",
              borderRadius: 999,
              background: "var(--md-error-container)",
              color: "var(--md-error)",
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              fontWeight: 500,
            }}
          >
            auto-reject · {photo?.reject_reason ?? "unknown"}
          </span>
        )}
      </div>

      <AxisRow
        axisClass="axis-sharpness"
        name="sharpness"
        value01={sharp}
        display={sharp !== null ? (sharp * 10).toFixed(1) : "—"}
      />
      <AxisRow
        axisClass="axis-lighting"
        name="lighting"
        value01={exp}
        display={exp !== null ? (exp * 10).toFixed(1) : "—"}
      />
      <AxisRow
        axisClass="axis-subject"
        name="people"
        value01={faces !== null ? Math.min(1, faces / 4) : null}
        display={faces !== null ? String(faces) : "—"}
      />
    </div>
  );
}
