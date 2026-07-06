const BASE = "/api";

export interface TasteStatus {
  trained: boolean;
  n_samples: number;
  auc: number | null;
  weight: number;
  trained_at?: string;
  labeled_available: number;
}

export interface TasteTrainResult {
  n_samples: number;
  auc: number | null;
  weight: number;
  trained_at: string;
  path: string;
}

export async function tasteStatus(): Promise<TasteStatus> {
  const res = await fetch(`${BASE}/taste/status`);
  if (!res.ok) throw new Error(`tasteStatus ${res.status}`);
  return res.json();
}

export async function trainTaste(): Promise<TasteTrainResult> {
  const res = await fetch(`${BASE}/taste/train`, { method: "POST" });
  if (!res.ok) {
    let detail = `trainTaste ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* keep default message */
    }
    throw new Error(detail);
  }
  return res.json();
}
