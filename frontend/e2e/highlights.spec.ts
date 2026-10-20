import { expect, test } from "@playwright/test";

test("highlight budgets, reasons and score-only selection fit the existing review flow", async ({ page, request }) => {
  const listing = await (await request.get("/api/photos?collapse=none")).json();
  const originals = listing.items.slice(0, 3);
  expect(originals.length).toBe(3);
  const requests: URL[] = [];
  // Deterministic suggestions make the UI check independent of the fixture's
  // quality scores. Backend selection is covered against a real DB in pytest.
  await page.route("**/api/curate?*", async (route) => {
    const url = new URL(route.request().url());
    requests.push(url);
    const balanced = url.searchParams.get("diverse") !== "false";
    const photos = originals.slice(0, balanced ? 2 : 3).map((p: { id: number; sha256: string }, i: number) => ({
      photo_id: p.id, sha256: p.sha256, taken_at: null,
      thumb_url: `/api/thumb/${p.sha256}`, preview_url: `/api/preview/${p.sha256}`,
      combined: 0.92 - i * 0.01, iqa: 0.7, ap25: null, nima: null,
      moment_id: null, moment_size: 1,
      selection_reasons: ["Passed the quality selection for this scope", "Adds coverage: day, subject type"],
    }));
    await route.fulfill({ json: { facet: "library", value: "all", total: photos.length, available: 3, photos } });
  });
  await page.goto("/best/library/all");
  await expect(page.getByRole("heading", { name: "Library highlights" })).toBeVisible();
  await expect(page.getByLabel("Highlights", { exact: true })).toHaveValue("40");
  await expect(page.getByRole("status").filter({ hasText: "2 of 3 qualifying photos" })).toBeVisible();
  await page.getByText("Why this photo?", { exact: true }).first().click();
  await expect(page.getByText("Adds coverage: day, subject type").first()).toBeVisible();
  await page.getByLabel("Highlights", { exact: true }).selectOption("12");
  await expect.poll(() => requests.at(-1)?.searchParams.get("budget")).toBe("12");
  await page.getByLabel("Balance scenes, places and people").uncheck();
  await expect(page.getByRole("status").filter({ hasText: "3 of 3 qualifying photos" })).toBeVisible();
  expect(requests.at(-1)?.searchParams.get("diverse")).toBe("false");
  await page.getByLabel("Highlights", { exact: true }).selectOption("all");
  await expect(page.getByLabel("Balance scenes, places and people")).toBeDisabled();
  await expect.poll(() => requests.at(-1)?.searchParams.has("budget")).toBe(false);
  await page.getByLabel("Highlights", { exact: true }).selectOption("12");
  await expect(page.getByRole("status").filter({ hasText: "3 of 3 qualifying photos" })).toBeVisible();
  await page.getByText("Why this photo?", { exact: true }).first().click();
  await page.screenshot({ path: "test-results/highlights-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByLabel("Highlights", { exact: true })).toBeVisible();
  const back = await page.getByRole("link", { name: "← Stories" }).boundingBox();
  expect(back).not.toBeNull();
  expect(back!.x + back!.width).toBeLessThanOrEqual(390);
  await page.screenshot({ path: "test-results/highlights-mobile.png", fullPage: true });
});

test("burst alternatives do not inherit the highlighted frame's explanation", async ({ page, request }) => {
  const listing = await (await request.get("/api/photos?collapse=none")).json();
  const [lead, alternative] = listing.items;
  await page.route("**/api/curate?*", (route) => route.fulfill({ json: {
    facet: "library", value: "all", total: 1, available: 1, photos: [{
      photo_id: lead.id, sha256: lead.sha256, taken_at: null,
      thumb_url: lead.thumb_url, preview_url: lead.preview_url,
      combined: 0.9, iqa: null, ap25: null, nima: null, moment_id: 999, moment_size: 2,
      selection_reasons: ["Representative of a burst of 2"],
    }],
  } }));
  await page.route(`**/api/photos/${lead.sha256}/moment`, (route) => route.fulfill({ json: {
    id: 999, primary_photo_id: lead.id, size: 2,
    members: [lead, alternative].map((p, rank) => ({ photo_id: p.id, sha256: p.sha256, rank })),
  } }));
  await page.route("**/api/moments/999/primary", (route) => route.fulfill({ json: { ok: true } }));
  await page.goto("/best/library/all");
  await expect(page.getByText("Why this photo?", { exact: true })).toBeVisible();
  const tile = page.locator('[data-kbd-scope="stack"]');
  await tile.focus();
  await tile.press("]");
  await expect(page.getByText("Alternative", { exact: true })).toBeVisible();
  await expect(page.getByText("Why this photo?", { exact: true })).toHaveCount(0);
  await tile.press("[");
  await expect(page.getByText("Why this photo?", { exact: true })).toBeVisible();
});

test("real highlights keep rejected photos excluded when switching to all qualifying", async ({ page, request }) => {
  const listing = await (await request.get("/api/photos?collapse=none")).json();
  const [keeper, rejected] = listing.items;
  expect((await request.post(`/api/swipes/${keeper.sha256}`, { data: { decision: "keep" } })).ok()).toBe(true);
  expect((await request.post(`/api/swipes/${rejected.sha256}`, { data: { decision: "reject" } })).ok()).toBe(true);
  try {
    const initial = page.waitForResponse((response) =>
      response.url().includes("/api/curate?") && new URL(response.url()).searchParams.has("budget"));
    await page.goto("/best/library/all");
    const initialResponse = await initial;
    expect(initialResponse.ok()).toBe(true);
    await expect(page.locator(`img[src="/api/thumb/${keeper.sha256}"]`)).toBeVisible();
    await expect(page.locator(`img[src="/api/thumb/${rejected.sha256}"]`)).toHaveCount(0);

    const all = page.waitForResponse((response) =>
      response.url().includes("/api/curate?") && !new URL(response.url()).searchParams.has("budget"));
    await page.getByLabel("Highlights", { exact: true }).selectOption("all");
    const response = await all;
    expect(response.ok()).toBe(true);
    const body = await response.json();
    expect(body.photos.map((photo: { sha256: string }) => photo.sha256)).not.toContain(rejected.sha256);
    await expect(page.locator(`img[src="/api/thumb/${keeper.sha256}"]`)).toBeVisible();
    await expect(page.locator(`img[src="/api/thumb/${rejected.sha256}"]`)).toHaveCount(0);
  } finally {
    await request.delete(`/api/swipes/${keeper.sha256}`);
    await request.delete(`/api/swipes/${rejected.sha256}`);
  }
});
