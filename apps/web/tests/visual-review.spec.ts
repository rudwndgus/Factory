import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
test.use({
  baseURL: "http://localhost:3000",
  launchOptions: { channel: "msedge" },
});
test("visual settings, employee reports and mobile review are readable", async ({
  page,
  request,
}) => {
  const text = fs.readFileSync(path.resolve("../../.env"), "utf8");
  const password = text
    .match(/^OWNER_PASSWORD=(.+)$/m)![1]
    .trim()
    .replace(/^['"]|['"]$/g, "");
  const auth = await request.post("http://localhost:8000/api/auth/login", {
    data: { password },
  });
  expect(auth.ok()).toBeTruthy();
  const { token } = await auth.json();
  const authHeaders = { Authorization: `Bearer ${token}` };
  const offices = await (
    await request.get("http://localhost:8000/api/offices", {
      headers: authHeaders,
    })
  ).json();
  await request.post(
    `http://localhost:8000/api/offices/${offices[0].id}/jobs`,
    { headers: authHeaders, data: { test_mode: true, ai_visuals: false } },
  );
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
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
    page.getByRole("combobox", { name: "Visual Source Mode", exact: true }),
  ).toHaveValue("AI First");
  await expect(
    page.getByRole("textbox", { name: "Visual Style Preset", exact: true }),
  ).toHaveValue(/cinematic/);
  await page.locator("nav button").filter({ hasText: "Reports" }).click();
  await page
    .locator(".report-paper")
    .filter({ hasText: "Why space is silent" })
    .first()
    .click();
  await expect(page.getByRole("dialog")).toContainText("부서별 수행 내역");
  await expect(page.getByRole("dialog")).toContainText("장면 연출");
  await page.screenshot({
    path: "../../data/screenshots/employee-report.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: /확인 완료/ }).click();
  await page.getByRole("button", { name: /^정리함/ }).click();
  await expect(
    page.locator(".report-paper").filter({ hasText: "Why space is silent" }).first(),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Video library", exact: true })
    .click();
  await page
    .locator(".video-card")
    .filter({ hasText: "TEST_COMPLETE" })
    .first()
    .click();
  await expect(page.locator(".scene-review-card")).toHaveCount(5);
  await expect(page.locator("video")).toBeVisible({ timeout: 30000 });
  await expect(page.locator(".scene-review-card img")).toHaveCount(5);
  await expect(
    page.getByRole("button", { name: "이 장면 이미지 재생성" }),
  ).toHaveCount(5);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "../../data/screenshots/ai-review-mobile.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
