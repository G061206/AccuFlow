async function request(path, options = {}) {
  const { timeoutMs = 15000, ...fetchOptions } = options;
  const response = await fetch(path, {
    ...fetchOptions,
    signal: options.signal || AbortSignal.timeout(timeoutMs),
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
  });
  if (response.status === 204) return null;
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(payload.detail) ? payload.detail.map((item) => item.msg).join("；") : payload.detail;
    const error = new Error(detail || `请求失败（${response.status}）`);
    error.status = response.status;
    throw error;
  }
  return payload;
}

export const api = {
  deliveries: (options) => request("/api/notifications/deliveries", options),
  deliveryContent: (id) => request(`/api/notifications/deliveries/${encodeURIComponent(id)}/content`),
  resolveDelivery: (id, resolution) => request(`/api/notifications/deliveries/${encodeURIComponent(id)}/resolve`, { method: "POST", body: JSON.stringify({ resolution }) }),
  signals: (symbol) => request(`/api/signals?symbol=${encodeURIComponent(symbol)}&limit=1`),
  signalReplay: (id) => request(`/api/signals/${encodeURIComponent(id)}/replay`, { timeoutMs: 120000 }),
  context: (symbol) => request(`/api/stocks/${encodeURIComponent(symbol)}/analysis-context`),
  saveContext: (symbol, values) => request(`/api/stocks/${encodeURIComponent(symbol)}/analysis-context`, { method: "POST", body: JSON.stringify(values) }),
  health: (options) => request("/api/health", options),
  jobs: (options) => request("/api/workflow/jobs", options),
  outbox: (options) => request("/api/notifications/outbox", options),
  replay: (id) => request(`/api/reports/${encodeURIComponent(id)}/replay`),
  settings: (options) => request("/api/settings", options),
  saveSettings: (values) => request("/api/settings", { method: "PATCH", body: JSON.stringify(values) }),
  generateReport: () => request("/api/reports/generate", { method: "POST", body: "{}", timeoutMs: 120000 }),
  connectIBKR: () => request("/api/ibkr/connect", { method: "POST" }),
  stocks: (options) => request("/api/stocks", options),
  addStock: (symbol) =>
    request("/api/stocks", {
      method: "POST",
      body: JSON.stringify({ symbol }),
      timeoutMs: 180000,
    }),
  updateStock: (symbol, active) =>
    request(`/api/stocks/${encodeURIComponent(symbol)}`, {
      method: "PATCH",
      body: JSON.stringify({ active }),
    }),
  removeStock: (symbol) =>
    request(`/api/stocks/${encodeURIComponent(symbol)}`, {
      method: "DELETE",
    }),
  backfillStock: (symbol) =>
    request(`/api/stocks/${encodeURIComponent(symbol)}/backfill`, {
      method: "POST",
      body: JSON.stringify({ include_daily: true, include_minute: true }),
      timeoutMs: 3600000,
    }),
  reports: (options) => request("/api/reports?limit=500", options),
};
