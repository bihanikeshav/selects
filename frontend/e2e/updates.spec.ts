import { expect, test } from "@playwright/test";

const installed = {
  current_version: "0.1.18", platform: "Windows", installation: "desktop",
  status: "unchecked", latest_version: null, download_url: null,
  release_url: "https://github.com/bihanikeshav/selects/releases/latest",
  checked_at: null, message: null,
};

test("Updates checks only on request and offers the desktop download", async ({ page }) => {
  let checks = 0;
  await page.route("**/api/updates", (route) => route.fulfill({ json: installed }));
  await page.route("**/api/updates/check", async (route) => {
    checks++;
    await route.fulfill({ json: { ...installed, status: "available", latest_version: "0.1.19",
      download_url: "https://github.com/bihanikeshav/selects/releases/download/v0.1.19/selects-windows-x64.zip",
      checked_at: "2026-10-15T03:00:00Z" } });
  });
  await page.goto("/updates");
  await expect(page.getByText("Selects 0.1.18", { exact: true })).toBeVisible();
  const rail = await page.getByRole("navigation", { name: "Primary" }).boundingBox();
  const title = await page.getByRole("heading", { name: "Updates", exact: true }).boundingBox();
  expect(rail!.width).toBeLessThan(120);
  expect(title!.x).toBeGreaterThan(rail!.width);
  expect(checks).toBe(0);
  await page.getByRole("button", { name: "Check for updates" }).click();
  await expect(page.getByRole("heading", { name: "Selects 0.1.19 is available" })).toBeVisible();
  const download = page.getByRole("link", { name: "Download for Windows" });
  await expect(download).toHaveAttribute("href", /\/v0\.1\.19\/selects-windows-x64.zip$/);
  await expect(page.getByText("Quit Selects, then open the new copy.")).toBeVisible();
  await page.screenshot({ path: "test-results/updates-desktop.png", fullPage: true });
  expect(checks).toBe(1);
});

test("Updates handles offline checks and retry", async ({ page }) => {
  await page.route("**/api/updates", (route) => route.fulfill({ json: installed }));
  let checks = 0;
  await page.route("**/api/updates/check", (route) => {
    checks++;
    return route.fulfill({ json: { ...installed,
      status: checks === 1 ? "unavailable" : "current",
      message: "Could not check for updates. Check your connection and try again in a minute.",
    } });
  });
  await page.goto("/updates");
  await page.getByRole("button", { name: "Check for updates" }).click();
  await expect(page.getByRole("status")).toContainText("Check your connection");
  await page.getByRole("button", { name: "Check for updates" }).click();
  await expect(page.getByRole("heading", { name: "You’re up to date" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Download for Windows" })).toHaveCount(0);
});

test("Updates shows Python upgrade commands instead of a desktop replacement", async ({ page }) => {
  await page.route("**/api/updates", (route) => route.fulfill({ json: installed }));
  await page.route("**/api/updates/check", (route) => route.fulfill({ json: {
    ...installed, installation: "python", status: "available", latest_version: "0.1.19",
  } }));
  await page.goto("/updates");
  await page.getByRole("button", { name: "Check for updates" }).click();
  await expect(page.getByText("uv tool upgrade selects", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Download for Windows" })).toHaveCount(0);
});

test("Updates remains available before onboarding, in dark mode at laptop size", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 720 });
  await page.addInitScript(() => localStorage.setItem("tc-theme", "dark"));
  await page.route("**/api/libraries/status", (route) => route.fulfill({ json: {
    needs_onboarding: true, active: null, photo_count: 0, indexing: false,
  } }));
  await page.route("**/api/updates", (route) => route.fulfill({ json: {
    ...installed, platform: "macOS", status: "available", latest_version: "0.1.19",
  } }));
  await page.goto("/updates");
  await expect(page.getByRole("heading", { name: "Selects 0.1.19 is available" })).toBeVisible();
  await expect(page).toHaveURL(/\/updates$/);
  await expect(page.getByText("No desktop download is available", { exact: false })).toBeVisible();
  await page.getByRole("link", { name: "View releases on GitHub" }).scrollIntoViewIfNeeded();
  await expect(page.getByRole("link", { name: "View releases on GitHub" })).toBeVisible();
  await page.screenshot({ path: "test-results/updates-dark.png", fullPage: true });
});
