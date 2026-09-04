import "./styles.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";

const root = document.getElementById("root");
if (!root) throw new Error("No #root element found");

const render = () =>
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>
  );

// LAN mode: a valid ?token= makes the server set an HttpOnly cookie that
// carries auth from then on, so exchange it once, drop it from the URL, and
// only then mount the app (whose first requests rely on that cookie).
const params = new URLSearchParams(window.location.search);
const token = params.get("token");
if (token) {
  params.delete("token");
  const query = params.toString();
  const url = window.location.pathname + (query ? `?${query}` : "") + window.location.hash;
  fetch(`/api/health?token=${encodeURIComponent(token)}`)
    .catch(() => {})
    .finally(() => {
      window.history.replaceState(null, "", url);
      render();
    });
} else {
  render();
}
