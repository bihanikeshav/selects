import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { activateLibrary, createLibrary, startIndexing } from "../api/client";

type Stage = "index" | "classical" | "embed" | "tag" | "story" | "done";

interface ProgressMsg {
  stage: Stage;
  current: number;
  total: number;
  message?: string;
}

const STAGE_LABELS: Record<Stage, string> = {
  index: "Indexing files",
  classical: "Scoring sharpness & exposure",
  embed: "Understanding each photo",
  tag: "Tagging scenes & subjects",
  story: "Grouping into stories",
  done: "Done",
};

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

export default function Onboarding() {
  const navigate = useNavigate();
  const [name, setName] = useState("My Trip");
  const [path, setPath] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [phase, setPhase] = useState<"form" | "indexing" | "done">("form");
  const [progress, setProgress] = useState<ProgressMsg | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    return () => {
      wsRef.current?.close();
    };
  }, []);

  function connectProgress() {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${window.location.host}/ws/progress`);
    wsRef.current = ws;
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data) as ProgressMsg;
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
      // If the stream ended after real progress but never sent an explicit
      // "done", treat that as completion so the user isn't stranded.
      setPhase((p) => (p === "indexing" ? "done" : p));
    };
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
      connectProgress();
      setPhase("indexing");
      await startIndexing(lib.id);
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : String(e2));
      setPhase("form");
      wsRef.current?.close();
    }
  }

  const pct =
    progress && progress.total > 0
      ? Math.min(100, Math.round((progress.current / progress.total) * 100))
      : 0;
  const activeStage = progress?.stage ?? "index";

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
              <label className="onb-field">
                <span className="onb-label">Folder path</span>
                <input
                  className="onb-input onb-input-mono"
                  type="text"
                  value={path}
                  onChange={(e) => setPath(e.target.value)}
                  placeholder="C:\Photos\Ladakh-2025"
                  autoFocus
                />
              </label>
              {err && <p className="onb-error">{err}</p>}
              <button className="btn btn-filled onb-submit" type="submit">
                Start culling
              </button>
            </form>
          </>
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
