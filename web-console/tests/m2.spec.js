import { expect, test } from "@playwright/test";
import { mockApi } from "./mock-api";

test("M2 details, verification context, replay and preview preferences", async ({ page }) => {
  await mockApi(page);
  let saved = {};
  const families = Object.fromEntries(["core_behavior", "cross_day", "price_response", "relative_strength", "distribution"].map((name) => [name, { available: true, value: .8 }]));
  families.cross_day.timeline = [{ date: "2026-09-14", evaluated: true, supported: true, closed: false }, { date: "2026-09-11", evaluated: false, supported: null, closed: true }];
  await page.route("**/api/stocks/AAPL/analysis-context", (route) => {
    if (route.request().method() === "POST") saved = route.request().postDataJSON();
    return route.fulfill({ json: saved });
  });
  await page.route("**/api/signals?**", (route) => route.fulfill({ json: [{ score: 72, status_label: "新异动", as_of: "2026-09-14T16:30:00Z", rule_version: "unified-v1-m2.1", snapshot_id: "fixture", families, contradiction_penalty: 0, counter_evidence: ["逐笔方向不可用，缺失权重未重新分配"] }] }));
  await page.route("**/api/signals/fixture/replay", (route) => route.fulfill({ json: { matches: true } }));
  const errors = []; page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page.getByLabel("AAPL 更多操作").click();
  await page.getByRole("button", { name: "检测详情与配置" }).click();
  await expect(page.getByRole("heading", { name: "统一评分 72" })).toBeVisible();
  await expect(page.getByText("核心行为：24.0 / 30")).toBeVisible();
  await page.getByRole("button", { name: "回放检测快照" }).click();
  await expect(page.getByRole("status")).toContainText("回放一致");
  await page.getByLabel("已核验 IBKR 成交量单位为股").check();
  await page.getByLabel("已核验公司行动与复权口径").check();
  await page.getByLabel("已核验截至当前的事件背景").check();
  await page.getByLabel("核验记录").fill("浏览器验收 fixture");
  await page.getByRole("button", { name: "保存分析配置" }).click();
  await expect(page.getByRole("status")).toContainText("配置已保存");
  expect(saved.units_verified).toBe(true); expect(saved.event_review_through).toBeTruthy();
  await page.locator(".report-drawer").evaluate((node) => { node.scrollTop = 0; });
  await page.screenshot({ path: "qa-artifacts/m2-details.png" });
  await page.getByLabel("关闭检测详情").click();
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const hourly = page.getByRole("button", { name: "小时异动提醒", exact: true });
  await hourly.click(); await expect(hourly).toHaveAttribute("aria-pressed", "true");
  await page.reload(); await page.getByRole("button", { name: "设置", exact: true }).click();
  await expect(hourly).toHaveAttribute("aria-pressed", "true");
  expect(errors).toEqual([]);
});
