import { useEffect, useState } from "react";

import { tasteStatus, trainTaste } from "../api/taste";
import type { TasteStatus } from "../api/taste";
import "./TasteCard.css";

const MIN_SAMPLES = 100;

function pct(v: number): string {
  return `${Math.round(v * 100)}%`;
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
      <div className="taste-head">
        <h2>Your taste</h2>
        {trained && status && (
          <span className="taste-sub">
            {status.auc !== null ? `AUC ${status.auc.toFixed(2)}` : "trained"}
          </span>
        )}
      </div>

      {err && <p className="taste-error">{err}</p>}

      {trained && status ? (
        <p className="taste-line">
          Learned from <strong>{status.n_samples}</strong> decisions · influence{" "}
          <strong>{pct(status.weight)}</strong>
        </p>
      ) : (
        <p className="taste-line">
          Not trained yet · <strong>{labeled}</strong> of {MIN_SAMPLES} decisions collected
        </p>
      )}

      <p className="taste-explain">
        Every keep or reject teaches travelcull what you like. The learned taste
        gently re-ranks curated picks — it ramps up with more decisions but never
        outweighs the aesthetic models (40% max).
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
