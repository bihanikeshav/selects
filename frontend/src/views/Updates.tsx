import { useEffect, useState } from "react";
import { getUpdates, type UpdateStatus } from "../api/updates";
import PageHeader from "../components/PageHeader";
import Rail from "../components/Rail";
import "./Updates.css";

export default function Updates() {
  const [info, setInfo] = useState<UpdateStatus | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    getUpdates().then((result) => { if (active) setInfo(result); })
      .catch(() => { if (active) setError("Could not load version details. Try checking again."); });
    return () => { active = false; };
  }, []);

  async function check() {
    setChecking(true);
    setError(null);
    try {
      setInfo(await getUpdates(true));
    } catch {
      setError("Could not check for updates. Check your connection and try again.");
    } finally {
      setChecking(false);
    }
  }

  const available = info?.status === "available";
  return (
    <div className="app">
      <Rail />
      <div className="workspace updates-workspace">
        <PageHeader context="setup" title="Updates" subtitle="Keep Selects up to date." />
        <div className="updates-page">
          <section className="updates-version" aria-labelledby="installed-version">
            <div>
              <h2 id="installed-version">Installed version</h2>
              <p>{info ? `Selects ${info.current_version}` : "Loading version…"}</p>
              {info && <span>{info.platform} · {info.installation === "desktop" ? "Desktop bundle" : "Python installation"}</span>}
            </div>
            <button className="btn btn-tonal" type="button" disabled={checking} onClick={() => void check()}>
              {checking ? "Checking…" : "Check for updates"}
            </button>
          </section>
          <div className="updates-status" role="status" aria-live="polite" aria-busy={checking}>
            {error ? <p className="onb-error">{error}</p> : info?.status === "unavailable" ? (
              <p>{info.message}</p>
            ) : available ? (
              <>
                <h2>Selects {info.latest_version} is available</h2>
                <p>A newer stable release is ready.</p>
              </>
            ) : info?.status === "current" ? (
              <><h2>You’re up to date</h2><p>You’re running the latest stable version or newer.</p></>
            ) : <p>Check when you’re ready. Updates are downloaded only when you choose.</p>}
            {info?.checked_at && <p className="updates-muted">Last checked {new Date(info.checked_at).toLocaleString()}</p>}
          </div>
          {available && info.installation === "desktop" && (
            <section className="updates-instructions" aria-labelledby="download-update">
              <h2 id="download-update">Update the desktop app</h2>
              {info.download_url ? (
                <>
                <a className="btn btn-filled" href={info.download_url} target="_blank" rel="noopener noreferrer">
                  Download for {info.platform}
                </a>
                <ol>
                  <li>Download and extract the new bundle into a new folder.</li>
                  <li>Quit Selects, then open the new copy.</li>
                </ol>
                <p>Your photo libraries and downloaded models stay where they are. Keep the old app folder until the new copy opens successfully.</p>
                </>
              ) : <p>No desktop download is available for this platform yet. Check the release page for installation options.</p>}
            </section>
          )}
          {available && info.installation === "python" && (
            <section className="updates-instructions" aria-labelledby="python-update">
              <h2 id="python-update">Update your installation</h2>
              <p>Quit Selects, then use the command for how you installed it.</p>
              <p>Installed with uv (including the one-line installer):</p>
              <pre><code>uv tool upgrade selects</code></pre>
              <p>Installed with pip: run this in the same Python environment.</p>
              <pre><code>python -m pip install --upgrade selects</code></pre>
              <p>Open Selects again after the upgrade. Your libraries and models stay in place.</p>
            </section>
          )}
          <div className="updates-footer">
            <a href={info?.release_url ?? "https://github.com/bihanikeshav/selects/releases/latest"} target="_blank" rel="noopener noreferrer">View releases on GitHub ↗</a>
            <p>Checking contacts GitHub for release information. Your photos are never sent.</p>
          </div>
        </div>
      </div>
    </div>
  );
}
