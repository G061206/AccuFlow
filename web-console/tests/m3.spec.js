import { expect, test } from "@playwright/test";
import { mockApi } from "./mock-api";

test("M3 shows uncertain delivery, sandboxed email and explicit resolution", async ({ page }) => {
  await mockApi(page);
  let status = "uncertain";
  await page.route("**/api/notifications/deliveries", (route) => route.fulfill({ json: [{ id: "m3", subject: "[AccuFlow][收盘报告][数据不足]", status, attempts: 1, last_error: "SMTPServerDisconnected" }] }));
  await page.route("**/api/notifications/deliveries/m3/content", (route) => route.fulfill({ json: { subject: "数据不足", body: "今日无法完整评估", html: "<h1>数据不足</h1><p>今日无法完整评估</p>" } }));
  await page.route("**/api/notifications/deliveries/m3/resolve", (route) => { expect(route.request().postDataJSON().resolution).toBe("confirmed_sent"); status = "sent"; return route.fulfill({ json: { id: "m3", status } }); });
  const errors = []; page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page.getByRole("button", { name: "运行状态", exact: true }).click();
  await expect(page.getByText("投递结果不确定 · 已尝试 1 次")).toBeVisible();
  await page.getByRole("button", { name: "查看邮件", exact: true }).click();
  await expect(page.locator('iframe[title="HTML 邮件"]')).toHaveAttribute("sandbox", "");
  await expect(page.frameLocator('iframe[title="HTML 邮件"]').getByRole("heading", { name: "数据不足" })).toBeVisible();
  await page.screenshot({ path: "qa-artifacts/m3-email.png" });
  await page.getByLabel("关闭邮件预览").click();
  await page.getByRole("button", { name: "已核实服务器接收", exact: true }).click();
  await expect(page.getByText("服务器已接收 · 已尝试 1 次")).toBeVisible();
  expect(errors).toEqual([]);
});
