/**
 * E2E: dashboard flows, operator actions, emergency stop.
 * Run with: npx playwright test (from dashboard/)
 * Requires backend and dashboard running; set BASE_URL and API_URL if needed.
 */

import { test, expect } from "@playwright/test";

const BASE_URL = process.env.BASE_URL || "http://localhost:3000";

test.describe("Dashboard", () => {
  test("home and login link", async ({ page }) => {
    await page.goto(BASE_URL);
    await expect(page.locator("h1")).toContainText("Defense System");
    await page.click("text=Login");
    await expect(page).toHaveURL(/\/login/);
  });

  test("login flow redirects to dashboard", async ({ page }) => {
    await page.goto(`${BASE_URL}/login`);
    await page.fill('input[type="email"]', "op@test.local");
    await page.fill('input[type="password"]', "any");
    await page.click("button[type=submit]");
    await expect(page).toHaveURL(/\/dashboard/);
  });

  test("dashboard shows map and nav", async ({ page }) => {
    await page.goto(`${BASE_URL}/login`);
    await page.fill('input[type="email"]', "op@test.local");
    await page.fill('input[type="password"]', "any");
    await page.click("button[type=submit]");
    await expect(page.locator("text=Operations map")).toBeVisible();
    await page.click("text=Alerts");
    await expect(page).toHaveURL(/\/dashboard\/alerts/);
    await page.click("text=Control");
    await expect(page).toHaveURL(/\/dashboard\/control/);
  });

  test("control page has emergency stop button", async ({ page }) => {
    await page.goto(`${BASE_URL}/login`);
    await page.fill('input[type="email"]', "op@test.local");
    await page.fill('input[type="password"]', "any");
    await page.click("button[type=submit]");
    await page.goto(`${BASE_URL}/dashboard/control`);
    await expect(page.locator("text=Emergency stop")).toBeVisible();
  });
});
