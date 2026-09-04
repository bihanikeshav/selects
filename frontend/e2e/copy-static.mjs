// Build the SPA and publish it where the FastAPI server looks for it
// (selects/server/static), so the e2e smoke test drives the real server
// rather than the Vite dev server.
import { cp, mkdir, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const dist = path.join(here, "..", "dist");
const target = path.join(here, "..", "..", "selects", "server", "static");

await rm(target, { recursive: true, force: true });
await mkdir(target, { recursive: true });
await cp(dist, target, { recursive: true });
console.log(`copied ${dist} -> ${target}`);
