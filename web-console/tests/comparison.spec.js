import { test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

test("capture normalized source and implementation comparison", async ({ page }) => {
  const source = readFileSync(resolve("reference-option-1.png")).toString("base64");
  const implementation = readFileSync(resolve("qa-artifacts/implementation-main.png")).toString("base64");

  await page.setViewportSize({ width: 1920, height: 724 });
  await page.setContent(`
    <style>
      * { box-sizing: border-box; }
      body { margin: 0; padding: 18px; background: #e9eef5; font-family: Inter, Arial, sans-serif; color: #10203d; }
      main { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
      figure { margin: 0; padding: 10px; border: 1px solid #cad5e3; border-radius: 12px; background: white; box-shadow: 0 4px 18px rgba(25, 48, 82, .08); }
      figcaption { height: 28px; padding: 2px 4px 8px; font-size: 14px; font-weight: 700; }
      img { display: block; width: 100%; aspect-ratio: 1.40625; object-fit: fill; border: 1px solid #e1e7ef; }
    </style>
    <main>
      <figure><figcaption>参考设计（归一化至 1440 × 1024）</figcaption><img src="data:image/png;base64,${source}"></figure>
      <figure><figcaption>实际实现（1440 × 1024）</figcaption><img src="data:image/png;base64,${implementation}"></figure>
    </main>
  `);
  await page.screenshot({ path: "qa-artifacts/comparison-main.png", fullPage: false });
});
