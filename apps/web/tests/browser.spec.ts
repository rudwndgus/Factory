import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
test.use({
  baseURL: "http://localhost:3000",
  launchOptions: { channel: "msedge" },
});
test("offline office shows truthful state; responsive canvas and dialogs", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "The office is yours." }),
  ).toBeVisible();
  await page.waitForSelector("canvas");
  await page
    .getByRole("button", { name: "Writer OFFLINE", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText("SERVER NOT CONNECTED");
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: "../../data/screenshots/desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: "../../data/screenshots/mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  expect(errors).toEqual([]);
});
test("authenticated office reads real state, creates office, persists settings and reports", async ({
  page,
  request,
}) => {
  page.on("pageerror", (e) => console.log("BROWSER ERROR", e.message));
  page.on("response", (r) => {
    if (r.url().endsWith("/mode")) console.log("MODE RESPONSE", r.status());
  });
  const env = fs.readFileSync(path.resolve("../../.env"), "utf8");
  const password = env.match(/^OWNER_PASSWORD=(.+)$/m)![1].trim();
  const response = await request.post("http://localhost:8000/api/auth/login", {
    data: { password },
  });
  expect(response.ok()).toBeTruthy();
  const { token } = await response.json();
  await page.addInitScript((t) => {
    localStorage.setItem("psf-api", "http://localhost:8000");
    sessionStorage.setItem("psf-token", t);
  }, token);
  await page.goto("/");
  await expect(
    page.getByRole("button", {
      name: "Server connected Private workspace",
      exact: true,
    }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Save office settings" }),
  ).toBeVisible();
  await page.locator("nav button").filter({ hasText: "Reports" }).click();
  await page.getByRole("button", { name: "Generate daily" }).click();
  await expect(page.locator(".report-paper").first()).toBeVisible();
  await page.locator(".report-paper").first().click();
  await expect(page.getByRole("dialog")).toContainText("일일 보고서");
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page.getByRole("button", { name: "Office", exact: true }).click();
  await page.getByRole("button", { name: "Start", exact: true }).click();
  await expect(page.locator(".mode")).toContainText("RUNNING");
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(page.locator(".mode")).toContainText("PAUSED");
  await page
    .getByRole("button", { name: "Emergency stop", exact: true })
    .click();
  await page.getByRole("button", { name: "Confirm emergency stop" }).click();
  await expect(page.locator(".mode")).toContainText("EMERGENCY STOP");
  await page.getByRole("button", { name: "Stop", exact: true }).click();
  await page
    .getByRole("button", { name: "Create an office", exact: true })
    .click();
  await page
    .getByLabel("Office name", { exact: true })
    .fill("Browser acceptance");
  await page
    .getByRole("button", { name: "Create office", exact: true })
    .click();
  await expect(page.locator(".panel-heading h2")).toHaveText(
    "Browser acceptance",
  );
  await page.getByRole("button", { name: "Production", exact: true }).click();
  await expect(page.getByText("The production line is clear.")).toBeVisible();
  await page.screenshot({
    path: "../../data/screenshots/connected.png",
    fullPage: true,
  });
});
