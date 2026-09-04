// Global setup for the Playwright smoke run.
//
// Builds a throwaway library (6 distinct copies of tests/fixtures/small.jpg),
// indexes it with the two ML-free passes, then starts the real FastAPI server
// on port 8765 serving the SPA out of selects/server/static.
//
// The server lifecycle lives here rather than in `webServer` because the
// library has to exist (and its path be known) before `serve` is launched, and
// globalSetup is the only hook guaranteed to run first.
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
export const REPO_ROOT = path.resolve(here, "..", "..");
export const PORT = 8765;
export const BASE_URL = `http://127.0.0.1:${PORT}`;
export const PHOTO_COUNT = 6;

const CLI = ["-c", "from selects.cli import main; main()"];

function python(): string {
  return process.env.SELECTS_PYTHON || "python";
}

/** Temp library dir + registry file, keyed by pid so parallel runs don't clash. */
function paths() {
  const root = path.join(os.tmpdir(), `selects-e2e-${process.pid}`);
  return { libDir: root, registry: path.join(os.tmpdir(), `selects-e2e-registry-${process.pid}.json`) };
}

/**
 * Six copies of the fixture JPEG with distinct names AND distinct bytes: the
 * API keys swipes by sha256, so identical copies would make one rejection look
 * like six.  Bytes after the JPEG EOI marker are ignored by decoders.
 */
function buildLibrary(libDir: string): void {
  rmSync(libDir, { recursive: true, force: true });
  mkdirSync(libDir, { recursive: true });
  const src = readFileSync(path.join(REPO_ROOT, "tests", "fixtures", "small.jpg"));
  for (let i = 1; i <= PHOTO_COUNT; i++) {
    const name = `photo-${String(i).padStart(2, "0")}.jpg`;
    writeFileSync(path.join(libDir, name), Buffer.concat([src, Buffer.from(`\n; selects-e2e ${i}\n`)]));
  }
}

function runPass(libDir: string, env: NodeJS.ProcessEnv, pass: string): void {
  const r = spawnSync(python(), [...CLI, "index", libDir, "--pass", pass], {
    cwd: REPO_ROOT,
    env,
    encoding: "utf8",
    timeout: 10 * 60_000,
  });
  if (r.status !== 0) {
    throw new Error(
      `selects index --pass ${pass} failed (status ${r.status})\n${r.stdout ?? ""}\n${r.stderr ?? ""}`,
    );
  }
}

async function waitForServer(child: ChildProcess, log: string[], timeoutMs = 120_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    if (child.exitCode !== null) {
      throw new Error(`selects serve exited early (code ${child.exitCode})\n${log.join("")}`);
    }
    try {
      const res = await fetch(`${BASE_URL}/api/libraries`);
      if (res.ok) return;
    } catch {
      /* not listening yet */
    }
    if (Date.now() > deadline) {
      throw new Error(`selects serve did not answer on ${BASE_URL} within ${timeoutMs}ms\n${log.join("")}`);
    }
    await new Promise((r) => setTimeout(r, 250));
  }
}

function stop(child: ChildProcess): void {
  if (child.exitCode !== null || child.pid === undefined) return;
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], { stdio: "ignore" });
  } else {
    child.kill("SIGTERM");
  }
}

export default async function globalSetup(): Promise<() => Promise<void>> {
  const { libDir, registry } = paths();
  // A throwaway registry: never touch the developer's ~/.selects/libraries.json.
  const env = { ...process.env, SELECTS_REGISTRY: registry };

  // A stray server left over from an earlier run would answer our polls and
  // serve a different library, producing baffling assertion failures.
  try {
    await fetch(`${BASE_URL}/api/libraries`);
    throw new Error(
      `something is already listening on ${BASE_URL}; stop it before running the e2e smoke test`,
    );
  } catch (e) {
    if (e instanceof Error && e.message.startsWith("something is already listening")) throw e;
  }

  rmSync(registry, { force: true });
  buildLibrary(libDir);
  runPass(libDir, env, "index");
  runPass(libDir, env, "classical");

  const log: string[] = [];
  const child = spawn(
    python(),
    [...CLI, "serve", libDir, "--no-browser", "--no-background", "--port", String(PORT)],
    { cwd: REPO_ROOT, env, stdio: ["ignore", "pipe", "pipe"] },
  );
  child.stdout?.on("data", (d) => log.push(String(d)));
  child.stderr?.on("data", (d) => log.push(String(d)));

  try {
    await waitForServer(child, log);
  } catch (e) {
    stop(child);
    throw e;
  }

  return async () => {
    stop(child);
    rmSync(libDir, { recursive: true, force: true });
    rmSync(registry, { force: true });
  };
}
