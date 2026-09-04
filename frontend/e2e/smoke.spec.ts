// End-to-end smoke test: drives the real FastAPI server over the built SPA
// against a throwaway 6-photo library (see e2e/fixture.ts).
import { expect, test as base } from "@playwright/test";

import { PHOTO_COUNT } from "./fixture";

/**
 * Every test gets a `consoleErrors` sink wired up before the first navigation;
 * the afterEach below asserts it stayed empty.
 */
const test = base.extend<{ consoleErrors: string[] }>({
  consoleErrors: async ({ page }, use) => {
    const errors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(msg.text());
    });
    page.on("pageerror", (err) => errors.push(String(err)));
    await use(errors);
  },
});

test.afterEach(async ({ consoleErrors }) => {
  expect(consoleErrors, `console errors: ${consoleErrors.join(" | ")}`).toEqual([]);
});

test("review screen shows a photo and the 1 of N counter", async ({ page, consoleErrors }) => {
  expect(consoleErrors).toEqual([]);
  await page.goto("/cull");

  await expect(page.locator(".cull-stage .gold-frame > img")).toBeVisible();
  await expect(page.locator(".page-sub")).toContainText(`1 of ${PHOTO_COUNT}`);
});

test("review header shell stays within its height budget", async ({ page, consoleErrors }) => {
  expect(consoleErrors).toEqual([]);
  await page.goto("/cull");
  await expect(page.locator(".cull-stage .gold-frame > img")).toBeVisible();

  // The header is a fixed-height shell (~148px); anything taller means it has
  // grown a row and is eating the space the photo needs.
  const box = await page.locator(".page-shell").boundingBox();
  expect(box).not.toBeNull();
  expect(box!.height).toBeLessThanOrEqual(150);
});

test("pressing x rejects the photo and the summary counts it", async ({ page, consoleErrors }) => {
  expect(consoleErrors).toEqual([]);
  await page.goto("/cull");
  await expect(page.locator(".cull-stage .gold-frame > img")).toBeVisible();
  await expect(page.locator(".page-sub")).toContainText(`1 of ${PHOTO_COUNT}`);

  // Wait on the verdict POST itself, so a backend failure reports as such
  // rather than as a stale counter 15 seconds later. The "rejected" count
  // only updates once BurstCull's debounced refreshSummary() lands its own
  // GET — without waiting on that response too, the toContainText below is
  // racing a network round trip against its own polling budget, which is
  // flaky on a cold server.
  const posted = page.waitForResponse(
    (r) => r.request().method() === "POST" && /\/api\/swipes\/[0-9a-f]{64}$/.test(r.url()),
  );
  const summaryRefreshed = page.waitForResponse(
    (r) => r.request().method() === "GET" && r.url().includes("/api/swipes/summary"),
  );
  await page.locator("body").press("x");
  expect((await posted).status()).toBe(200);
  expect((await summaryRefreshed).status()).toBe(200);

  await expect(page.locator(".page-sub")).toContainText(`2 of ${PHOTO_COUNT}`);
  await expect(page.locator(".page-sub")).toContainText("1 rejected");
});

test("libraries lists the e2e library", async ({ page, consoleErrors }) => {
  expect(consoleErrors).toEqual([]);
  await page.goto("/libraries");

  const tiles = page.locator(".lib-tile-name");
  await expect(tiles.first()).toBeVisible();
  await expect(tiles.filter({ hasText: /^selects-e2e-\d+$/ })).toHaveCount(1);
});

test("libraries opens exactly one progress websocket", async ({ page, consoleErrors }) => {
  expect(consoleErrors).toEqual([]);
  // The indexing pill, the models card and the watch card all listen for
  // progress; they must share the one socket ProgressSocketProvider owns.
  const sockets: string[] = [];
  page.on("websocket", (ws) => sockets.push(ws.url()));

  await page.goto("/libraries");
  await expect(page.locator(".lib-tile-name").first()).toBeVisible();
  // Both progress-listening cards live inside a collapsed <details>. Open it
  // and wait for each card to render: once both are on screen every subscriber
  // has mounted and had its chance to (wrongly) open a socket of its own, so
  // the count below is deterministic without a fixed sleep.
  await page.locator(".lib-active-settings > summary").click();
  await expect(page.locator(".lib-models")).toBeVisible();
  await expect(page.locator(".watch-card")).toBeVisible();

  expect(sockets.filter((u) => u.includes("/ws/progress"))).toHaveLength(1);
});

test("search renders its empty state", async ({ page, consoleErrors }) => {
  expect(consoleErrors).toEqual([]);
  await page.goto("/search");

  await expect(page.locator(".cluster-detail-empty")).toHaveText(
    "Type a place, a scene or a moment. Try 'monastery courtyard' or 'snow on the pass'.",
  );
});

test("people renders its empty state", async ({ page, consoleErrors }) => {
  expect(consoleErrors).toEqual([]);
  await page.goto("/people");

  await expect(page.locator(".cluster-detail-empty")).toBeVisible();
  await expect(page.locator(".cluster-detail-empty")).toHaveText(
    /No people yet|Face grouping is off/,
  );
});

test("map reports that no photo has GPS", async ({ page, consoleErrors }) => {
  expect(consoleErrors).toEqual([]);
  await page.goto("/map");

  await expect(page.getByText("No photos with GPS metadata yet.")).toBeVisible();
});
