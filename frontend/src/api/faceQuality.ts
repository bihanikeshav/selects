// Face-quality API (eyes-closed / head-pose aware culling).
// Mirrors the fetch style of ./client.ts; kept in its own file per the
// project's conflict rules (client.ts is owned by another feature).

const BASE = "/api";

export interface FaceQualityFace {
  /** Eyes-open score in [0,1]; 1 = clearly open. null = not computed. */
  eyes_open: number | null;
  /** Head yaw in degrees; 0 = frontal. null = not computed. */
  yaw: number | null;
  /** Head pitch in degrees; 0 = level. null = not computed. */
  pitch: number | null;
  /** Face bbox area / image area, [0,1]. null = not computed. */
  area: number | null;
}

export interface FaceQuality {
  faces: FaceQualityFace[];
  any_eyes_closed: boolean;
  all_looking_away: boolean;
}

export async function getFaceQuality(
  sha256: string,
  opts: { compute?: boolean } = {},
): Promise<FaceQuality> {
  const params = new URLSearchParams();
  if (opts.compute !== undefined) params.set("compute", String(opts.compute));
  const qs = params.toString();
  const res = await fetch(
    `${BASE}/photos/${sha256}/face_quality${qs ? `?${qs}` : ""}`,
  );
  if (!res.ok) throw new Error(`getFaceQuality ${res.status}`);
  return res.json();
}
