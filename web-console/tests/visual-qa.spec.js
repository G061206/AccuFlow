import { expect, test } from "@playwright/test";
import { mockApi } from "./mock-api";

const artifactDir = "qa-artifacts";

test("desktop console matches the selected information architecture and core flow", async ({ page }) => {
  await mockApi(page);
  const consoleErrors = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(error.message));

  await page.setViewportSize({ width: 1440, height: 1024 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "跟踪股票" })).toBeVisible();
  await expect(page.getByText("IBKR 已连接")).toBeVisible();
  await expect(page.getByText("最近报告", { exact: true })).toBeVisible();
  await expect(page.getByText("AAPL", { exact: true })).toBeVisible();
  await page.screenshot({ path: `${artifactDir}/implementation-main.png`, fullPage: false });

  const exclAapl = page.getByLabel("股票代码");
  await exclAapl.fill("AAPL");
  await page.getByRole("button", { name: "添加股票" }).click();
  await expect(page.getByRole("alert")).toHaveText("AAPL 已在跟踪列表中");

  await exclAapl.fill("META");
  await page.getByRole("button", { name: "添加股票" }).click();
  await expect(page.getByText("META", { exact: true })).toBeVisible();
  await expect(page.getByRole("status")).toContainText("META 已加入跟踪列表");

  const aaplRow = page.getByText("AAPL", { exact: true }).locator("xpath=ancestor::div[contains(@class,'stock-row')]");
  await aaplRow.getByRole("button", { name: "AAPL 更多操作" }).click();
  await aaplRow.getByRole("button", { name: "同步 IBKR 数据" }).click();
  await expect(page.getByRole("status")).toContainText("AAPL 已同步");

  const pltrRow = page.getByText("PLTR", { exact: true }).locator("xpath=ancestor::div[contains(@class,'stock-row')]");
  await pltrRow.getByRole("button", { name: "继续" }).click();
  await expect(pltrRow.getByText("跟踪中")).toBeVisible();

  await page.getByRole("button", { name: "查看", exact: true }).first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("heading", { name: "判断" })).toBeVisible();
  await page.screenshot({ path: `${artifactDir}/implementation-report.png`, fullPage: false });
  await page.getByRole("button", { name: "关闭报告" }).click();

  const metaRow = page.getByText("META", { exact: true }).locator("xpath=ancestor::div[contains(@class,'stock-row')]");
  await metaRow.getByRole("button", { name: "META 更多操作" }).click();
  await metaRow.getByRole("button", { name: "移除股票" }).click();
  await expect(page.getByText("META", { exact: true })).toHaveCount(0);

  await page.getByRole("button", { name: "用户" }).click();
  await expect(page.getByText("AccuFlow 管理员")).toBeVisible();
  await page.getByRole("button", { name: "用户" }).click();

  await page.getByRole("button", { name: "历次报告" }).click();
  await expect(page.getByRole("heading", { name: "历次报告" })).toBeVisible();
  await page.getByPlaceholder("搜索股票或报告摘要").fill("NVDA");
  await expect(page.locator(".report-row")).toHaveCount(4);
  await page.getByLabel("报告类型").selectOption({ label: "收盘报告" });
  await expect(page.locator(".report-row")).toHaveCount(2);

  await page.getByRole("button", { name: "设置" }).click();
  await expect(page.getByRole("heading", { name: "设置" })).toBeVisible();
  await expect(page.locator(".switch").first()).toHaveAttribute("aria-pressed", "true");
  await page.locator(".switch").first().click();
  await expect(page.locator(".switch").first()).toHaveAttribute("aria-pressed", "false");

  expect(consoleErrors, consoleErrors.join("\n")).toEqual([]);
});

test("narrow layout keeps the primary controls usable", async ({ page }) => {
  await mockApi(page);
  const consoleErrors = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(error.message));

  await page.setViewportSize({ width: 820, height: 1050 });
  await page.goto("/");
  await expect(page.getByRole("button", { name: "跟踪股票" })).toBeVisible();
  await expect(page.getByLabel("股票代码")).toBeVisible();
  await expect(page.getByRole("button", { name: "添加股票" })).toBeVisible();
  await page.screenshot({ path: `${artifactDir}/implementation-narrow.png`, fullPage: true });
  expect(consoleErrors, consoleErrors.join("\n")).toEqual([]);
});
