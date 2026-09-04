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

test("pressing x rejects the photo and the summary counts it", async ({ page, consoleErrors }) => {
  expect(consoleErrors).toEqual([]);
  await page.goto("/cull");
  await expect(page.locator(".cull-stage .gold-frame > img")).toBeVisible();
  await expect(page.locator(".page-sub")).toContainText(`1 of ${PHOTO_COUNT}`);

  // Wait on the verdict POST itself, so a backend failure reports as such
  // rather than as a stale counter 15 seconds later.
  const posted = page.waitForResponse(
    (r) => r.request().method() === "POST" && /\/api\/swipes\/[0-9a-f]{64}$/.test(r.url()),
  );
  await page.locator("body").press("x");
  expect((await posted).status()).toBe(200);

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
