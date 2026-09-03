import "./styles.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";

const params = new URLSearchParams(window.location.search);
const lanToken = params.get("token") || sessionStorage.getItem("selects-lan-token");
if (lanToken) {
  sessionStorage.setItem("selects-lan-token", lanToken);
  const origFetch = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init: RequestInit = {}) => {
    const headers = new Headers(init.headers);
    if (!headers.has("Authorization")) {
      headers.set("Authorization", `Bearer ${lanToken}`);
    }
    return origFetch(input, { ...init, headers });
  };
}

const root = document.getElementById("root");
if (!root) throw new Error("No #root element found");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>
);
