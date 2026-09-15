const stocks = [
  { symbol: "AAPL", company: "Apple Inc.", status: "tracking", active: true, conId: 265598, coverage: "完整", coverageDetail: "实时 · 1分钟 · 1小时 · 日线", score: 68, signal: "中等偏强", checkedAt: "2026-09-11 16:00 ET", reportType: "收盘检测", dataStatus: "ready" },
  { symbol: "NVDA", company: "NVIDIA Corporation", status: "tracking", active: true, conId: 4815747, coverage: "完整", coverageDetail: "实时 · 1分钟 · 1小时 · 日线", score: 72, signal: "较强", checkedAt: "2026-09-11 16:00 ET", reportType: "收盘检测", dataStatus: "ready" },
  { symbol: "AMD", company: "Advanced Micro Devices", status: "incomplete", active: true, conId: 4391, coverage: "缺少日线数据", coverageDetail: "实时 · 1分钟 · 1小时", score: 54, signal: "中性", checkedAt: "2026-09-11 15:00 ET", reportType: "1小时检测", dataStatus: "error" },
  { symbol: "PLTR", company: "Palantir Technologies", status: "paused", active: false, conId: 444857009, coverage: "完整", coverageDetail: "实时 · 1分钟 · 1小时 · 日线", score: 48, signal: "中性", checkedAt: "2026-09-11 16:00 ET", reportType: "收盘检测", dataStatus: "ready" },
  { symbol: "MSFT", company: "Microsoft Corporation", status: "tracking", active: true, conId: 272093, coverage: "完整", coverageDetail: "实时 · 1分钟 · 1小时 · 日线", score: 61, signal: "中等偏强", checkedAt: "2026-09-11 16:00 ET", reportType: "收盘检测", dataStatus: "ready" },
];

const reports = [
  { id: "closing-20260911", time: "2026-09-11 16:00 ET", type: "收盘报告", symbols: ["AAPL", "NVDA", "AMD", "PLTR", "MSFT"], summary: "5 只股票完成收盘检测 · 2 只信号较强 · 1 只数据不完整", conclusion: "NVDA 的持续需求迹象最强；AAPL 与 MSFT 保持中等偏强，AMD 因日线缺口限制结论升级。", evidence: ["NVDA 的买方压力分布在多个完整窗口", "AAPL 回踩参考区后恢复，成交重心保持", "MSFT 相对行业表现稳定"], counter: ["行业整体同步走强，股票特异性有所减弱", "AMD 缺少完整日线基线"], quality: "4 只完整，1 只数据不完整" },
  { id: "hourly-20260911-1500", time: "2026-09-11 15:00 ET", type: "小时报告", symbols: ["AAPL", "NVDA", "AMD", "PLTR", "MSFT"], summary: "5 只股票完成小时检测 · 1 只信号较强 · 1 只数据不完整", conclusion: "NVDA 出现新增独立证据，其他股票没有需要立即通知的状态变化。", evidence: ["NVDA 连续多个片段维持正向压力", "剔除最大贡献片段后异常仍存在"], counter: ["后续价格响应区间尚未完全结束"], quality: "整体良好，AMD 日线基线不完整" },
  { id: "hourly-20260911-1400", time: "2026-09-11 14:00 ET", type: "小时报告", symbols: ["AAPL", "NVDA", "AMD", "MSFT"], summary: "4 只股票完成小时检测 · 整体信号平稳", conclusion: "本检查点没有出现新的重要异动，既有状态保持。", evidence: ["AAPL 价格重心保持", "NVDA 相对强弱仍为正向"], counter: ["暂无新增跨日证据"], quality: "数据完整" },
  { id: "closing-20260910", time: "2026-09-10 16:00 ET", type: "收盘报告", symbols: ["AAPL", "NVDA", "AMD", "PLTR", "MSFT"], summary: "5 只股票完成收盘检测 · 1 只进入观察", conclusion: "NVDA 首次进入观察状态，尚未满足跨日升级门槛。", evidence: ["成交量和相对强弱高于同时间段常态"], counter: ["仅有一个完整交易日支持"], quality: "数据完整" },
];

export async function mockApi(page) {
  const state = {
    failures: {},
    connected: true,
    settings: { monitoring_enabled: false, daily_report: true, hourly_alert: false, delivery_mode: "preview", hourly_alert_available: true },
    stocks: structuredClone(stocks),
    reports: structuredClone(reports),
  };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    if (state.failures[`${method} ${path}`]) return route.fulfill({ status: 503, json: { detail: "测试服务暂不可用" } });
    if (path === "/api/notifications/deliveries" || path === "/api/workflow/jobs" || path === "/api/notifications/outbox") return route.fulfill({ json: [] });
    if (path === "/api/settings") {
      if (method === "PATCH") Object.assign(state.settings, request.postDataJSON());
      return route.fulfill({ json: state.settings });
    }
    if (path === "/api/health") {
      return route.fulfill({ json: { service: "ok", database: "ok", ibkr: { connected: state.connected, host: "127.0.0.1", port: 4002, client_id: 17, server_time: "2026-09-11T17:32:00Z", last_error: null, market_data_type: 1 } } });
    }
    if (path === "/api/ibkr/connect" && method === "POST") {
      return route.fulfill({ json: { connected: true } });
    }
    if (path === "/api/reports" && method === "GET") {
      return route.fulfill({ json: state.reports });
    }
    if (path === "/api/stocks" && method === "GET") {
      return route.fulfill({ json: state.stocks });
    }
    if (path === "/api/stocks" && method === "POST") {
      const { symbol } = request.postDataJSON();
      const stock = { symbol, company: symbol === "META" ? "Meta Platforms, Inc." : `${symbol} Corporation`, status: "incomplete", active: true, conId: null, coverage: "等待 IBKR 合约解析", coverageDetail: "等待 IBKR 合约解析", score: null, signal: "等待首次检测", checkedAt: null, reportType: null, dataStatus: "pending" };
      state.stocks.push(stock);
      return route.fulfill({ status: 201, json: stock });
    }
    const backfillMatch = path.match(/^\/api\/stocks\/([^/]+)\/backfill$/);
    if (backfillMatch && method === "POST") {
      const symbol = decodeURIComponent(backfillMatch[1]);
      const stock = state.stocks.find((item) => item.symbol === symbol);
      stock.dataStatus = "ready";
      stock.coverage = "完整";
      stock.coverageDetail = "实时 · 1分钟 · 1小时 · 日线";
      stock.status = stock.active ? "tracking" : "paused";
      return route.fulfill({ json: { symbol, stored: { daily: 252, minute: 780 } } });
    }
    const match = path.match(/^\/api\/stocks\/([^/]+)$/);
    if (match && method === "PATCH") {
      const symbol = decodeURIComponent(match[1]);
      const stock = state.stocks.find((item) => item.symbol === symbol);
      const { active } = request.postDataJSON();
      stock.active = active;
      stock.status = active ? (stock.dataStatus === "ready" ? "tracking" : "incomplete") : "paused";
      return route.fulfill({ json: stock });
    }
    if (match && method === "DELETE") {
      const symbol = decodeURIComponent(match[1]);
      state.stocks = state.stocks.filter((item) => item.symbol !== symbol);
      return route.fulfill({ status: 204, body: "" });
    }
    return route.fulfill({ status: 404, json: { detail: "mock route not found" } });
  });
  return state;
}
