import { useEffect, useState } from "react";

import { tasteStatus, trainTaste } from "../api/taste";
import type { TasteStatus } from "../api/taste";
import "./TasteCard.css";

const MIN_SAMPLES = 100;

function pct(v: number): string {
  return `${Math.round(v * 100)}%`;
}

/** Plain-language read on the model's accuracy (AUC) — no jargon on screen. */
function tasteQuality(auc: number | null): string {
  if (auc === null) return "learning your taste";
  if (auc >= 0.9) return "knows your taste really well";
  if (auc >= 0.8) return "knows your taste well";
  if (auc >= 0.7) return "getting to know your taste";
  if (auc >= 0.6) return "still learning your taste";
  return "just getting started";
}

/** Compact "Your taste" card: shows how many keep/reject decisions the
 *  personalized taste model was trained on, how much it currently influences
 *  the curated ranking, and a button to (re)train it. */
export default function TasteCard() {
  const [status, setStatus] = useState<TasteStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [training, setTraining] = useState(false);

  async function load() {
    try {
      const s = await tasteStatus();
      setStatus(s);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function onTrain() {
    setErr(null);
    setTraining(true);
    try {
      await trainTaste();
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setTraining(false);
    }
  }

  if (!status && !err) return null;

  const trained = status?.trained ?? false;
  const labeled = status?.labeled_available ?? 0;
  const canTrain = labeled >= MIN_SAMPLES;
  const stale = trained && status !== null && labeled > status.n_samples;

  return (
    <div className="taste-card">
      <div className="taste-main">
        <div className="taste-head">
          <h2>Your taste</h2>
          {trained && status && (
            <span className="taste-sub" title={status.auc !== null ? `AUC ${status.auc.toFixed(2)}` : undefined}>
              {tasteQuality(status.auc)}
            </span>
          )}
        </div>

        {err ? (
          <p className="taste-error">{err}</p>
        ) : trained && status ? (
          <p className="taste-line">
            Trained on <strong>{status.n_samples}</strong> of your keep/skip choices ·{" "}
            {status.weight > 0 ? (
              <>shaping your picks by <strong>{pct(status.weight)}</strong></>
            ) : (
              <>not shaping your picks yet — keep sorting</>
            )}
          </p>
        ) : (
          <p className="taste-line">
            Still warming up · <strong>{labeled}</strong> of {MIN_SAMPLES} choices made
          </p>
        )}
      </div>

      <p className="taste-explain">
        Every photo you keep or skip teaches selects what you like. The more you
        sort, the more it leans on your taste — but it only ever nudges the order
        of your picks, never more than 40%, so it can't take over.
      </p>

      <button
        className="btn btn-filled"
        type="button"
        disabled={training || !canTrain}
        onClick={onTrain}
      >
        {training
          ? "Training…"
          : trained
            ? stale
              ? `Retrain (${labeled} decisions now)`
              : "Retrain"
            : canTrain
              ? "Train from my decisions"
              : `Need ${MIN_SAMPLES - labeled} more decisions`}
      </button>
    </div>
  );
}
