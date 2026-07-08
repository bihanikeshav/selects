import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  activateLibrary,
  createLibrary,
  modelsStatus,
  startIndexing,
  startModelsDownload,
} from "../api/client";
import type { ModelInfo } from "../api/types";
import { getSystem, type SystemInfo } from "../api/system";
import {
  estimateRemainingSeconds,
  estimateTotalSeconds,
  fmtDuration,
  type Backend,
} from "../lib/eta";
import FolderPicker from "../components/FolderPicker";

type Stage = "models" | "index" | "classical" | "embed" | "tag" | "story" | "done";

interface ProgressMsg {
  stage: Stage;
  current: number;
  total: number;
  message?: string;
}

const STAGE_LABELS: Record<Stage, string> = {
  models: "Downloading AI models",
  index: "Indexing files",
  classical: "Scoring sharpness & exposure",
  embed: "Understanding each photo",
  tag: "Tagging scenes & subjects",
  story: "Grouping into stories",
  done: "Done",
};

// Stages shown in the indexing checklist (models is handled as its own gate).
const STAGE_ORDER: Stage[] = ["index", "classical", "embed", "tag", "story"];

const STEPS = [
  {
    n: 1,
    title: "Point at a folder",
    body: "Give selects the folder that holds a trip's photos. Everything stays on your machine.",
  },
  {
    n: 2,
    title: "AI does the heavy lifting",
    body: "It indexes, scores and groups every shot — sharpness, faces, scenes and duplicates.",
  },
  {
    n: 3,
    title: "Review the best",
    body: "Walk through curated stories, keep the keepers and skip the rest in seconds.",
  },
];

/** Human-friendly model size: MB under a gigabyte, GB above (so a 1 MB model
 *  never renders as a misleading "0.0 GB"). */
function fmtSize(mb: number): string {
  if (mb <= 0) return "0 MB";
  if (mb < 1) return "<1 MB";
  if (mb < 1024) return `${Math.round(mb)} MB`;
  return `${(mb / 1024).toFixed(1)} GB`;
}

type Phase = "form" | "models" | "indexing" | "done";

export default function Onboarding() {
  const navigate = useNavigate();
  const [name, setName] = useState("My Trip");
  const [path, setPath] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>("form");
  const [progress, setProgress] = useState<ProgressMsg | null>(null);
  const [modelProgress, setModelProgress] = useState<ProgressMsg | null>(null);
  const [missing, setMissing] = useState<ModelInfo[]>([]);
  const [missingMb, setMissingMb] = useState(0);
  const [installedCount, setInstalledCount] = useState(0);
  const [installedMb, setInstalledMb] = useState(0);
  const [downloading, setDownloading] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const libIdRef = useRef<string | null>(null);
  const [sys, setSys] = useState<SystemInfo | null>(null);
  const [nPhotos, setNPhotos] = useState(0);
  const [, setTick] = useState(0);
  const stageStartRef = useRef<{ stage: string; at: number }>({ stage: "", at: 0 });

  useEffect(() => {
    return () => {
      wsRef.current?.close();
    };
  }, []);

  // Detect CPU vs GPU once so the indexing screen can set expectations.
  useEffect(() => {
    getSystem().then(setSys).catch(() => {});
  }, []);

  // Tick every second while indexing so the "time remaining" estimate updates.
  useEffect(() => {
    if (phase !== "indexing") return;
    const id = window.setInterval(() => setTick((t) => t + 1), 1000);
    return () => window.clearInterval(id);
  }, [phase]);

  // One socket is shared between the models download and the indexing run.
  // Messages are routed by `stage`: "models" frames drive the download gate
  // (and, on their "done" message, hand off to indexing on the same socket);
  // every other stage drives the indexing checklist, ending at stage "done".
  function connectProgress() {
    if (wsRef.current) return;
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${window.location.host}/ws/progress`);
    wsRef.current = ws;
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data) as ProgressMsg;
        if (msg.stage === "models") {
          setModelProgress(msg);
          if (msg.message === "done") {
            beginIndexing();
          }
          return;
        }
        setProgress(msg);
        // Record when each stage started (for live ETA) and learn the photo
        // count from the per-photo stages (their total == number of photos).
        if (stageStartRef.current.stage !== msg.stage) {
          stageStartRef.current = { stage: msg.stage, at: Date.now() };
        }
        if (
          msg.total > 0 &&
          (msg.stage === "index" ||
            msg.stage === "classical" ||
            msg.stage === "embed" ||
            msg.stage === "tag")
        ) {
          setNPhotos((prev) => Math.max(prev, msg.total));
        }
        if (msg.stage === "done") {
          setPhase("done");
          ws.close();
        }
      } catch {
        /* ignore malformed frames */
      }
    };
    ws.onclose = () => {
      wsRef.current = null;
      // If the stream ended after real indexing progress but never sent an
      // explicit "done", treat that as completion so the user isn't stranded.
      setPhase((p) => (p === "indexing" ? "done" : p));
    };
  }

  async function beginIndexing() {
    const id = libIdRef.current;
    if (!id) return;
    setPhase("indexing");
    try {
      await startIndexing(id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    const trimmedName = name.trim();
    const trimmedPath = path.trim();
    if (!trimmedName || !trimmedPath) {
      setErr("Please give your library a name and a folder path.");
      return;
    }
    try {
      const lib = await createLibrary(trimmedName, trimmedPath);
      await activateLibrary(lib.id);
      libIdRef.current = lib.id;
      // Gate on model weights before indexing.
      const status = await modelsStatus();
      const miss = status.models.filter((m) => !m.present);
      const have = status.models.filter((m) => m.present);
      setInstalledCount(have.length);
      setInstalledMb(have.reduce((s, m) => s + m.approx_size_mb, 0));
      if (miss.length === 0) {
        connectProgress();
        beginIndexing();
      } else {
        setMissing(miss);
        setMissingMb(status.total_missing_mb);
        setDownloading(status.downloading);
        setPhase("models");
        // A download may already be in flight from a previous run.
        if (status.downloading) connectProgress();
      }
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : String(e2));
      setPhase("form");
      wsRef.current?.close();
    }
  }

  async function onDownloadModels() {
    setErr(null);
    setDownloading(true);
    connectProgress();
    try {
      // A `false` return means a 409 (already running) — progress still
      // arrives on the socket we just opened, so nothing else to do.
      await startModelsDownload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setDownloading(false);
    }
  }

  function onSkipModels() {
    connectProgress();
    beginIndexing();
  }

  const pct =
    progress && progress.total > 0
      ? Math.min(100, Math.round((progress.current / progress.total) * 100))
      : 0;
  const activeStage = progress?.stage ?? "index";
  const mode: Backend = sys?.backend === "gpu" ? "gpu" : "cpu";
  const stageElapsedSec = stageStartRef.current.at
    ? (Date.now() - stageStartRef.current.at) / 1000
    : 0;
  const remainingSec = progress
    ? estimateRemainingSeconds({
        n: nPhotos,
        mode,
        stage: progress.stage,
        current: progress.current,
        total: progress.total,
        stageElapsedSec,
      })
    : estimateTotalSeconds(nPhotos, mode);
  const mpct =
    modelProgress && modelProgress.total > 0
      ? Math.min(100, Math.round((modelProgress.current / modelProgress.total) * 100))
      : 0;

  return (
    <div className="onb">
      <div className="onb-inner">
        <header className="onb-hero">
          <div className="onb-brand">
            <span className="dot" aria-hidden="true"></span>selects
          </div>
          <p className="onb-pitch">
            Cull your travel photos locally with AI — nothing leaves your machine.
          </p>
        </header>

        {phase === "form" && (
          <>
            <ol className="onb-steps">
              {STEPS.map((s) => (
                <li key={s.n} className="onb-step">
                  <span className="onb-step-num">{s.n}</span>
                  <div>
                    <h3>{s.title}</h3>
                    <p>{s.body}</p>
                  </div>
                </li>
              ))}
            </ol>

            <form className="onb-form" onSubmit={onSubmit}>
              <label className="onb-field">
                <span className="onb-label">Library name</span>
                <input
                  className="onb-input"
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="My Trip"
                />
              </label>
              <div className="onb-field">
                <span className="onb-label">Folder path</span>
                <FolderPicker value={path} onChange={setPath} />
              </div>
              {err && <p className="onb-error">{err}</p>}
              <button className="btn btn-filled onb-submit" type="submit">
                Start culling
              </button>
            </form>
          </>
        )}

        {phase === "models" && (
          <div className="onb-progress">
            {downloading ? (
              <>
                <h2>Downloading AI models…</h2>
                <div
                  className="onb-bar"
                  role="progressbar"
                  aria-valuenow={mpct}
                  aria-valuemin={0}
                  aria-valuemax={100}
                >
                  <div className="onb-bar-fill" style={{ width: `${mpct}%` }} />
                </div>
                <p className="onb-progress-caption">
                  {modelProgress && modelProgress.total > 0
                    ? `Model ${modelProgress.current} of ${modelProgress.total}`
                    : "Starting download…"}
                  {modelProgress?.message && modelProgress.message !== "done"
                    ? ` · ${modelProgress.message}`
                    : ""}
                </p>
                <p className="onb-models-note">
                  Indexing starts automatically once the models are in place.
                </p>
              </>
            ) : (
              <>
                <h2>Download AI models</h2>
                <p className="onb-progress-caption">
                  {installedCount > 0
                    ? `${missing.length} model${missing.length === 1 ? "" : "s"} left to download — the rest are already on your machine. They power AI scoring, tags, stories and enhancement.`
                    : "selects needs these model files for AI scoring, tags, stories and enhancement. They download once and stay on your machine."}
                </p>
                <ul className="onb-models-list">
                  {missing.map((m) => (
                    <li key={m.id} className="onb-model">
                      <div className="onb-model-main">
                        <span className="onb-model-name">{m.name}</span>
                        <span className="onb-model-for">{m.required_for}</span>
                      </div>
                      <span className="onb-model-size">
                        {fmtSize(m.approx_size_mb)}
                      </span>
                    </li>
                  ))}
                </ul>
                {installedCount > 0 && (
                  <p
                    className="onb-models-note"
                    style={{ marginTop: 0, color: "var(--md-tertiary, #1a7f37)" }}
                  >
                    ✓ Already installed: {installedCount} model
                    {installedCount === 1 ? "" : "s"} ({fmtSize(installedMb)})
                  </p>
                )}
                {err && <p className="onb-error">{err}</p>}
                <div className="onb-models-actions">
                  <button
                    className="btn btn-filled"
                    type="button"
                    onClick={onDownloadModels}
                  >
                    Download models ({fmtSize(missingMb)})
                  </button>
                  <button
                    className="btn btn-text"
                    type="button"
                    onClick={onSkipModels}
                  >
                    Skip for now
                  </button>
                </div>
                <p className="onb-models-note">
                  AI scoring, tags and stories need these models — you can
                  download later from the Libraries page.
                </p>
              </>
            )}
          </div>
        )}

        {(phase === "indexing" || phase === "done") && (
          <div className="onb-progress">
            <h2>{phase === "done" ? "Your library is ready" : "Building your library…"}</h2>

            <div className="onb-bar" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
              <div className="onb-bar-fill" style={{ width: `${phase === "done" ? 100 : pct}%` }} />
            </div>

            <p className="onb-progress-caption">
              {phase === "done"
                ? "Indexed, scored and grouped."
                : `${STAGE_LABELS[activeStage]}${
                    progress && progress.total > 0 ? ` — ${progress.current}/${progress.total}` : "…"
                  }`}
              {progress?.message ? ` · ${progress.message}` : ""}
            </p>

            {phase !== "done" && (
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  fontSize: 13,
                  color: "#5f6368",
                  margin: "2px 0 10px",
                }}
              >
                <span>
                  {sys
                    ? sys.backend === "gpu"
                      ? `GPU · ${sys.device_name ?? "GPU"}`
                      : "Running on CPU"
                    : "…"}
                </span>
                <span style={{ fontVariantNumeric: "tabular-nums" }}>
                  {nPhotos > 0 || (progress && progress.total > 0)
                    ? `${fmtDuration(remainingSec)} remaining`
                    : "estimating…"}
                </span>
              </div>
            )}

            {phase !== "done" && mode === "cpu" && nPhotos > 0 && (
              <div
                style={{
                  fontSize: 13,
                  lineHeight: 1.5,
                  color: "#3c4043",
                  background: "rgba(251,188,4,0.12)",
                  border: "1px solid rgba(251,188,4,0.35)",
                  borderRadius: 10,
                  padding: "10px 12px",
                  margin: "0 0 12px",
                }}
              >
                <strong>Heads up:</strong> {nPhotos.toLocaleString()} photos on CPU
                take about {fmtDuration(estimateTotalSeconds(nPhotos, "cpu"))}. An
                NVIDIA GPU would cut this to roughly{" "}
                {fmtDuration(estimateTotalSeconds(nPhotos, "gpu"))}. You can leave
                this running — it keeps going in the background.
                <details style={{ marginTop: 8 }}>
                  <summary style={{ cursor: "pointer", fontWeight: 600 }}>
                    Have an NVIDIA GPU? Enable it
                  </summary>
                  <div style={{ marginTop: 6, fontSize: 12.5 }}>
                    Install a CUDA build of PyTorch, then restart selects — it
                    detects the GPU automatically on the next launch:
                    <code
                      style={{
                        display: "block",
                        marginTop: 6,
                        padding: "6px 8px",
                        borderRadius: 6,
                        background: "rgba(0,0,0,0.06)",
                        fontFamily: "var(--font-mono)",
                        fontSize: 12,
                        whiteSpace: "pre-wrap",
                        wordBreak: "break-all",
                      }}
                    >
                      pip install torch --index-url
                      https://download.pytorch.org/whl/cu124
                    </code>
                  </div>
                </details>
              </div>
            )}

            <ol className="onb-stage-list">
              {STAGE_ORDER.map((st) => {
                const activeIdx = STAGE_ORDER.indexOf(activeStage);
                const thisIdx = STAGE_ORDER.indexOf(st);
                const state =
                  phase === "done" || thisIdx < activeIdx
                    ? "done"
                    : thisIdx === activeIdx
                    ? "active"
                    : "pending";
                return (
                  <li key={st} className={`onb-stage onb-stage-${state}`}>
                    <span className="onb-stage-dot" aria-hidden="true" />
                    {STAGE_LABELS[st]}
                  </li>
                );
              })}
            </ol>

            {phase === "done" && (
              <button
                className="btn btn-filled onb-submit"
                type="button"
                onClick={() => navigate("/cull")}
              >
                Open your library
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
