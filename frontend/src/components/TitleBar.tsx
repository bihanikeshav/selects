import { useEffect, useState } from "react";
import "./TitleBar.css";

// pywebview injects window.pywebview + a `pywebviewready` event when running
// inside the desktop app. In a normal browser this stays undefined and the
// title bar renders nothing, leaving the web UI untouched.
declare global {
  interface Window {
    pywebview?: {
      api?: {
        minimize?: () => void;
        toggle_maximize?: () => void;
        close?: () => void;
      };
    };
  }
}

export default function TitleBar() {
  const [desktop, setDesktop] = useState<boolean>(
    () => typeof window !== "undefined" && !!window.pywebview,
  );

  useEffect(() => {
    const onReady = () => setDesktop(true);
    window.addEventListener("pywebviewready", onReady);
    if (window.pywebview) setDesktop(true);
    return () => window.removeEventListener("pywebviewready", onReady);
  }, []);

  useEffect(() => {
    document.documentElement.classList.toggle("selects-desktop", desktop);
  }, [desktop]);

  if (!desktop) return null;

  const api = () => window.pywebview?.api;

  return (
    <div className="titlebar">
      <div className="titlebar-drag pywebview-drag-region" aria-hidden="true" />
      <div className="titlebar-accent" aria-hidden="true" />
      <div className="titlebar-center">
        <span className="titlebar-dot" aria-hidden="true" />
        <span className="titlebar-title">Selects</span>
      </div>
      <div className="titlebar-controls">
        <button
          className="tb-btn"
          title="Minimize"
          onClick={() => api()?.minimize?.()}
        >
          <svg width="10" height="10" viewBox="0 0 10 10"><rect x="1" y="4.5" width="8" height="1" fill="currentColor" /></svg>
        </button>
        <button
          className="tb-btn"
          title="Maximize"
          onClick={() => api()?.toggle_maximize?.()}
        >
          <svg width="10" height="10" viewBox="0 0 10 10"><rect x="1.5" y="1.5" width="7" height="7" fill="none" stroke="currentColor" strokeWidth="1" /></svg>
        </button>
        <button
          className="tb-btn tb-close"
          title="Close"
          onClick={() => api()?.close?.()}
        >
          <svg width="10" height="10" viewBox="0 0 10 10"><path d="M1 1 L9 9 M9 1 L1 9" stroke="currentColor" strokeWidth="1.2" /></svg>
        </button>
      </div>
    </div>
  );
}
