async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
  });
  if (response.status === 204) return null;
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.detail || `请求失败（${response.status}）`);
    error.status = response.status;
    throw error;
  }
  return payload;
}

export const api = {
  health: () => request("/api/health"),
  connectIBKR: () => request("/api/ibkr/connect", { method: "POST" }),
  stocks: () => request("/api/stocks"),
  addStock: (symbol) =>
    request("/api/stocks", {
      method: "POST",
      body: JSON.stringify({ symbol }),
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
    }),
  reports: () => request("/api/reports?limit=500"),
};
