import type { Photo } from "../api/types";

interface ScoresCardProps {
  photo: Photo | null;
}

interface AxisRowProps {
  axisClass: string;
  name: string;
  value: number | null;
  maxValue?: number;
}

function AxisRow({ axisClass, name, value, maxValue = 100 }: AxisRowProps) {
  const pct = value !== null ? Math.min(100, Math.max(0, (value / maxValue) * 100)) : 0;
  const display = value !== null ? value.toFixed(1) : "—";

  return (
    <div className={`axis-row ${axisClass}`}>
      <span className="name">{name}</span>
      <span className="bar">
        <span className="fill" style={{ width: `${pct}%` }}></span>
      </span>
      <span className="num">{display}</span>
    </div>
  );
}

export default function ScoresCard({ photo }: ScoresCardProps) {
  return (
    <div className="scores-card">
      <div className="scores-head">
        <span className="label">Stage 1 · Classical signals</span>
        <div className="tags"></div>
      </div>

      {/* blur → axis-sharpness (inverted: lower blur = higher sharpness) */}
      <AxisRow
        axisClass="axis-sharpness"
        name="sharpness"
        value={photo?.blur !== null && photo?.blur !== undefined ? Math.max(0, 100 - photo.blur) : null}
        maxValue={100}
      />

      {/* exposure → axis-lighting */}
      <AxisRow
        axisClass="axis-lighting"
        name="lighting"
        value={photo?.exposure ?? null}
        maxValue={100}
      />

      {/* faces_count → axis-subject (clamped to 0–10 scale for display) */}
      <AxisRow
        axisClass="axis-subject"
        name="subject"
        value={photo?.faces_count !== null && photo?.faces_count !== undefined
          ? Math.min(100, photo.faces_count * 10)
          : null}
        maxValue={100}
      />
    </div>
  );
}
