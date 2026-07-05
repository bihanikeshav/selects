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
    body: "Give travelcull the folder that holds a trip's photos. Everything stays on your machine.",
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

function gb(mb: number): string {
  return (mb / 1024).toFixed(1);
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
  const [downloading, setDownloading] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const libIdRef = useRef<string | null>(null);

  useEffect(() => {
    return () => {
      wsRef.current?.close();
    };
  }, []);

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
  const mpct =
    modelProgress && modelProgress.total > 0
      ? Math.min(100, Math.round((modelProgress.current / modelProgress.total) * 100))
      : 0;

  return (
    <div className="onb">
      <div className="onb-inner">
        <header className="onb-hero">
          <div className="onb-brand">
            <span className="dot" aria-hidden="true"></span>travelcull
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
                  travelcull needs a few model files for AI scoring, tags and
                  stories. They download once and stay on your machine.
                </p>
                <ul className="onb-models-list">
                  {missing.map((m) => (
                    <li key={m.id} className="onb-model">
                      <div className="onb-model-main">
                        <span className="onb-model-name">{m.name}</span>
                        <span className="onb-model-for">{m.required_for}</span>
                      </div>
                      <span className="onb-model-size">
                        {gb(m.approx_size_mb)} GB
                      </span>
                    </li>
                  ))}
                </ul>
                {err && <p className="onb-error">{err}</p>}
                <div className="onb-models-actions">
                  <button
                    className="btn btn-filled"
                    type="button"
                    onClick={onDownloadModels}
                  >
                    Download models ({gb(missingMb)} GB)
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
