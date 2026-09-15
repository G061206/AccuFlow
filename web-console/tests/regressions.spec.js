import { expect, test } from "@playwright/test";
import { mockApi } from "./mock-api";

test("loads stocks even when reports fail and automatically notices disconnect", async ({ page }) => {
  const state = await mockApi(page);
  state.failures["GET /api/reports"] = true;
  await page.goto("/");
  await expect(page.getByText("AAPL", { exact: true })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("报告读取失败");
  state.connected = false;
  await expect(page.getByText("IBKR 未连接", { exact: false })).toBeVisible({ timeout: 12000 });
  delete state.failures["GET /api/reports"];
  await expect(page.getByRole("alert")).toHaveCount(0, { timeout: 12000 });
});

test("failed mutations preserve rows, show errors, and reenable controls", async ({ page }) => {
  const state = await mockApi(page);
  state.failures["PATCH /api/stocks/AAPL"] = true;
  state.failures["DELETE /api/stocks/AAPL"] = true;
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  const row = page.locator(".stock-row").filter({ has: page.getByText("AAPL", { exact: true }) });
  await row.getByRole("button", { name: "暂停", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("操作失败");
  await expect(row.getByRole("button", { name: "暂停", exact: true })).toBeEnabled();
  await row.getByLabel("AAPL 更多操作").click();
  await row.getByRole("button", { name: "移除股票" }).click();
  await expect(page.getByRole("status")).toContainText("操作失败");
  await expect(row).toBeVisible();
  expect(errors).toEqual([]);
});

test("report preferences survive navigation and reload", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const daily = page.getByRole("button", { name: "收盘日报预览", exact: true });
  await expect(daily).toHaveAttribute("aria-pressed", "true");
  await daily.click();
  await expect(daily).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByRole("button", { name: "小时异动提醒", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "跟踪股票", exact: true }).click();
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await expect(daily).toHaveAttribute("aria-pressed", "false");
  await page.reload();
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await expect(daily).toBeEnabled();
  await expect(daily).toHaveAttribute("aria-pressed", "false");
});
