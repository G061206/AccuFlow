import { useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  ArrowsClockwise,
  CaretDown,
  ChartLineUp,
  CheckCircle,
  DotsThree,
  FileText,
  GearSix,
  MagnifyingGlass,
  Moon,
  Pause,
  Play,
  Pulse,
  SlidersHorizontal,
  Sun,
  Trash,
  UserCircle,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { api } from "./api";

const navItems = [
  { id: "stocks", label: "跟踪股票", icon: ChartLineUp },
  { id: "reports", label: "历次报告", icon: FileText },
  { id: "health", label: "运行状态", icon: Pulse },
  { id: "settings", label: "设置", icon: GearSix },
];

const THEME_STORAGE_KEY = "accuflow-theme";

function initialTheme() {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // Storage can be unavailable in hardened browser contexts.
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function AppLogo() {
  return <div className="wordmark">AccuFlow</div>;
}

function StatusPill({ status }) {
  const labels = { tracking: "跟踪中", incomplete: "数据不完整", paused: "已暂停" };
  return <span className={`status-pill is-${status}`}><span className="status-dot" />{labels[status]}</span>;
}

function Coverage({ stock }) {
  const complete = stock.coverage === "完整";
  const Icon = complete ? CheckCircle : WarningCircle;
  return <div className="coverage-block"><span className="coverage-detail">{stock.coverageDetail}</span><span className={complete ? "coverage-ok" : "coverage-warning"}><Icon weight="fill" size={18} />{stock.coverage}</span></div>;
}

function Signal({ stock }) {
  return <div className="signal-block"><strong>{stock.score}</strong><span className={stock.score >= 60 ? "signal-positive" : "signal-neutral"}>{stock.signal}</span></div>;
}

function Topbar({ health, onConnect, theme, onToggleTheme }) {
  const [userOpen, setUserOpen] = useState(false);
  const connected = Boolean(health?.ibkr?.connected);
  const serverTime = health?.ibkr?.server_time;
  const timeLabel = serverTime ? `${new Date(serverTime).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", timeZone: "America/New_York" })} ET` : "已就绪";
  const ThemeIcon = theme === "dark" ? Sun : Moon;
  const themeLabel = theme === "dark" ? "切换到浅色模式" : "切换到深色模式";
  return <header className="topbar"><button className={`connection ${connected ? "" : "is-disconnected"}`} onClick={connected ? undefined : onConnect}><span />{connected ? "IBKR 已连接" : "IBKR 未连接"}<i>·</i><b>{connected ? `服务器时间 ${timeLabel}` : "点击连接"}</b></button><button className="icon-button theme-toggle" onClick={onToggleTheme} aria-label={themeLabel} title={themeLabel}><ThemeIcon size={21} /></button><div className="user-area"><button className="user-button" onClick={() => setUserOpen((value) => !value)} aria-expanded={userOpen}><UserCircle size={23} /><span>用户</span><CaretDown size={15} /></button>{userOpen && <div className="user-menu"><strong>AccuFlow 管理员</strong><span>本地控制台</span></div>}</div></header>;
}

function Sidebar({ activePage, setActivePage }) {
  return <aside className="sidebar"><AppLogo /><nav aria-label="主导航">{navItems.map(({ id, label, icon: Icon }) => <button key={id} className={activePage === id ? "nav-item active" : "nav-item"} onClick={() => setActivePage(id)}><Icon size={23} /><span>{label}</span></button>)}</nav><div className="sidebar-foot"><span>统一检测规则 v1</span><span>只读模式</span></div></aside>;
}

function PageIntro({ title, description, backAction }) {
  return <div className="page-intro">{backAction && <button className="back-button" onClick={backAction}><ArrowLeft size={18} />返回</button>}<h1>{title}</h1><p>{description}</p></div>;
}

function StockRow({ stock, onToggle, onRemove, onSync }) {
  const [menuOpen, setMenuOpen] = useState(false);
  return <div className="stock-row table-grid"><div className="stock-id"><strong>{stock.symbol}</strong><span>{stock.company}</span></div><div><StatusPill status={stock.status} /></div><Coverage stock={stock} /><Signal stock={stock} /><div className="checked-at"><span>{stock.checkedAt || "尚未检测"}</span><small>{stock.reportType || "—"}</small></div><div className="stock-actions"><button className={stock.status === "paused" ? "button primary compact" : "button secondary compact"} onClick={() => onToggle(stock.symbol)}>{stock.status === "paused" ? "继续" : "暂停"}</button><div className="row-menu-wrap"><button className="icon-button" aria-label={`${stock.symbol} 更多操作`} onClick={() => setMenuOpen((value) => !value)}><DotsThree size={25} weight="bold" /></button>{menuOpen && <div className="row-menu"><button onClick={() => { setMenuOpen(false); onSync(stock.symbol); }}><ArrowsClockwise size={17} />同步 IBKR 数据</button><button onClick={() => onToggle(stock.symbol)}>{stock.status === "paused" ? <Play size={17} /> : <Pause size={17} />}{stock.status === "paused" ? "继续跟踪" : "暂停跟踪"}</button><button className="danger" onClick={() => onRemove(stock.symbol)}><Trash size={17} />移除股票</button></div>}</div></div></div>;
}

function ReportsTable({ rows, onOpen }) {
  return <div className="reports-table"><div className="report-header report-grid"><span>报告时间</span><span>类型</span><span>涉及股票</span><span>摘要</span><span>操作</span></div>{rows.map((report) => <div className="report-row report-grid" key={report.id}><span>{report.time}</span><span><span className={`report-type ${report.type === "收盘报告" ? "closing" : "hourly"}`}>{report.type}</span></span><span className="report-symbols">{report.symbols.join(", ")}</span><span className="report-summary">{report.summary}</span><span><button className="button secondary small" onClick={() => onOpen(report)}>查看</button></span></div>)}</div>;
}

function StocksPage({ stocks, reports, addStock, toggleStock, removeStock, syncStock, openReport, showReports }) {
  const [ticker, setTicker] = useState("");
  const [error, setError] = useState("");
  async function submit(event) {
    event.preventDefault();
    const normalized = ticker.trim().toUpperCase();
    if (!/^[A-Z][A-Z.-]{0,5}$/.test(normalized)) return setError("请输入有效的美股代码");
    if (stocks.some((stock) => stock.symbol === normalized)) return setError(`${normalized} 已在跟踪列表中`);
    try {
      await addStock(normalized); setTicker(""); setError("");
    } catch (requestError) {
      setError(requestError.message);
    }
  }
  return <><PageIntro title="跟踪股票" description="添加并管理要监控的美股，基于 IBKR 实时数据检测主力积累信号。" /><form className="add-stock" onSubmit={submit}><label className="sr-only" htmlFor="ticker">股票代码</label><input id="ticker" value={ticker} onChange={(event) => { setTicker(event.target.value); setError(""); }} placeholder="输入股票代码（如 AAPL）" autoComplete="off" /><button className="button primary" type="submit">添加股票</button>{error && <span className="form-error" role="alert">{error}</span>}</form><section className="data-section stocks-section" aria-label="股票跟踪列表"><div className="stock-header table-grid"><span>股票</span><span>状态</span><span>数据覆盖</span><span>最新信号</span><span>最近检测</span><span>操作</span></div>{stocks.length ? stocks.map((stock) => <StockRow key={stock.symbol} stock={stock} onToggle={toggleStock} onRemove={removeStock} onSync={syncStock} />) : <div className="empty-state"><ChartLineUp size={34} /><strong>还没有跟踪股票</strong><span>在上方输入代码开始第一次监控。</span></div>}</section><section className="data-section recent-section"><div className="section-heading"><h2>最近报告</h2><button onClick={showReports}>查看全部报告 <ArrowRight size={18} /></button></div><ReportsTable rows={reports.slice(0, 3)} onOpen={openReport} /></section></>;
}

function ReportsPage({ reports, openReport }) {
  const [query, setQuery] = useState("");
  const [type, setType] = useState("all");
  const filtered = useMemo(() => reports.filter((report) => (type === "all" || report.type === type) && `${report.symbols.join(" ")} ${report.summary} ${report.time}`.toLowerCase().includes(query.toLowerCase())), [query, type]);
  return <><PageIntro title="历次报告" description="查看每个检查点保存的判断、证据、反证和数据质量。" /><div className="filter-bar"><label className="search-control"><MagnifyingGlass size={19} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索股票或报告摘要" /></label><label className="select-control"><SlidersHorizontal size={18} /><select aria-label="报告类型" value={type} onChange={(event) => setType(event.target.value)}><option value="all">全部类型</option><option value="收盘报告">收盘报告</option><option value="小时报告">小时报告</option></select></label></div><section className="data-section full-reports">{filtered.length ? <ReportsTable rows={filtered} onOpen={openReport} /> : <div className="empty-state"><FileText size={34} /><strong>没有匹配的报告</strong><span>试试清除搜索条件或切换报告类型。</span></div>}</section></>;
}

function HealthPage({ health, onConnect }) {
  const connected = Boolean(health?.ibkr?.connected);
  const rows = [
    ["IBKR 会话", connected ? "已连接" : "未连接", `${health?.ibkr?.host || "127.0.0.1"}:${health?.ibkr?.port || "—"} · clientId ${health?.ibkr?.client_id || "—"}`],
    ["SQLite 存储", health?.database === "ok" ? "正常" : "异常", "跟踪列表、行情 bars 与历次报告持久化"],
    ["行情类型", health?.ibkr?.market_data_type === 1 ? "实时" : "延迟/冻结", "由 IBKR 账户订阅和 ACCUFLOW_IBKR_MARKET_DATA_TYPE 决定"],
  ];
  return <><PageIntro title="运行状态" description="检查唯一行情源 IBKR 和本地持久化服务。" /><section className={`health-summary ${connected ? "" : "is-warning"}`}><div><span className="health-orb">{connected ? <CheckCircle size={24} weight="fill" /> : <WarningCircle size={24} weight="fill" />}</span><div><strong>{connected ? "行情连接正常" : "IBKR 尚未连接"}</strong><p>{connected ? "只读 API 会话已建立。" : health?.ibkr?.last_error || "请确认 Gateway/TWS 已启动并开放 API 端口。"}</p></div></div>{!connected && <button className="button primary" onClick={onConnect}>连接 IBKR</button>}</section><section className="data-section health-list">{rows.map(([name, state, detail]) => { const warning = state === "未连接" || state === "异常"; return <div className="health-row" key={name}><div><strong>{name}</strong><span>{detail}</span></div><span className={`health-badge ${warning ? "is-warning" : ""}`}>{warning ? <WarningCircle size={18} weight="fill" /> : <CheckCircle size={18} weight="fill" />}{state}</span></div>; })}</section></>;
}

function SettingsPage({ theme, onThemeChange }) {
  const [dailyReport, setDailyReport] = useState(true);
  const [hourlyAlert, setHourlyAlert] = useState(true);
  return <><PageIntro title="设置" description="管理界面、报告和提醒偏好；行情来源固定为 IBKR。" /><section className="settings-panel data-section"><div className="settings-group"><div><strong>界面主题</strong><span>选择适合当前环境的显示模式</span></div><div className="theme-options" role="group" aria-label="界面主题"><button className={theme === "light" ? "selected" : ""} onClick={() => onThemeChange("light")} aria-pressed={theme === "light"}><Sun size={17} />浅色</button><button className={theme === "dark" ? "selected" : ""} onClick={() => onThemeChange("dark")} aria-pressed={theme === "dark"}><Moon size={17} />深色</button></div></div><div className="settings-group"><div><strong>收盘日报</strong><span>每个交易日收盘后发送完整报告</span></div><button className={`switch ${dailyReport ? "on" : ""}`} onClick={() => setDailyReport((value) => !value)} aria-pressed={dailyReport}><span /></button></div><div className="settings-group"><div><strong>小时异动提醒</strong><span>仅在新异动、显著增强或失效时发送</span></div><button className={`switch ${hourlyAlert ? "on" : ""}`} onClick={() => setHourlyAlert((value) => !value)} aria-pressed={hourlyAlert}><span /></button></div><div className="settings-group"><div><strong>行情来源</strong><span>ib_async · IBKR API · 只读连接</span></div><span className="locked-value">固定</span></div></section></>;
}

function ReportDrawer({ report, onClose }) {
  if (!report) return null;
  return <div className="drawer-layer" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><aside className="report-drawer" role="dialog" aria-modal="true" aria-labelledby="report-title"><div className="drawer-head"><div><span>{report.type}</span><h2 id="report-title">{report.time}</h2></div><button className="icon-button" onClick={onClose} aria-label="关闭报告"><X size={22} /></button></div><div className="drawer-meta"><span>涉及股票</span><strong>{report.symbols.join(" · ")}</strong></div><section><h3>判断</h3><p>{report.conclusion}</p></section><section><h3>支持证据</h3><ul>{report.evidence.map((item) => <li key={item}>{item}</li>)}</ul></section><section><h3>反证</h3><ul>{report.counter.map((item) => <li key={item}>{item}</li>)}</ul></section><section><h3>数据质量</h3><p>{report.quality}</p></section><div className="drawer-foot">本报告为规则检测结果，不构成投资建议。</div></aside></div>;
}

export function App() {
  const [activePage, setActivePage] = useState("stocks");
  const [theme, setTheme] = useState(initialTheme);
  const [stocks, setStocks] = useState([]);
  const [reports, setReports] = useState([]);
  const [health, setHealth] = useState(null);
  const [selectedReport, setSelectedReport] = useState(null);
  const [toast, setToast] = useState("");
  function notify(message) { setToast(message); window.setTimeout(() => setToast(""), 2400); }
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      // Keep the selected theme for this session when storage is unavailable.
    }
  }, [theme]);
  useEffect(() => {
    Promise.all([api.stocks(), api.reports(), api.health()])
      .then(([storedStocks, storedReports, currentHealth]) => {
        setStocks(storedStocks); setReports(storedReports); setHealth(currentHealth);
      })
      .catch((error) => notify(`无法读取后端：${error.message}`));
  }, []);
  async function addStock(symbol) { const created = await api.addStock(symbol); setStocks((items) => [...items, created]); notify(`${symbol} 已加入跟踪列表`); }
  async function toggleStock(symbol) { const stock = stocks.find((item) => item.symbol === symbol); const updated = await api.updateStock(symbol, stock.status === "paused"); setStocks((items) => items.map((item) => item.symbol === symbol ? updated : item)); notify(`${symbol} 已${updated.status === "paused" ? "暂停" : "继续"}跟踪`); }
  async function removeStock(symbol) { await api.removeStock(symbol); setStocks((items) => items.filter((stock) => stock.symbol !== symbol)); notify(`${symbol} 已从列表移除`); }
  async function syncStock(symbol) { try { const result = await api.backfillStock(symbol); setStocks(await api.stocks()); notify(`${symbol} 已同步：日线 ${result.stored.daily}，1分钟 ${result.stored.minute}`); } catch (error) { notify(error.message); } }
  async function connectIBKR() { try { await api.connectIBKR(); setHealth(await api.health()); notify("IBKR 只读会话已连接"); } catch (error) { setHealth(await api.health().catch(() => health)); notify(error.message); } }
  return <div className="app-shell"><Sidebar activePage={activePage} setActivePage={setActivePage} /><div className="workspace"><Topbar health={health} onConnect={connectIBKR} theme={theme} onToggleTheme={() => setTheme((value) => value === "dark" ? "light" : "dark")} /><main className="content">{activePage === "stocks" && <StocksPage stocks={stocks} reports={reports} addStock={addStock} toggleStock={toggleStock} removeStock={removeStock} syncStock={syncStock} openReport={setSelectedReport} showReports={() => setActivePage("reports")} />}{activePage === "reports" && <ReportsPage reports={reports} openReport={setSelectedReport} />}{activePage === "health" && <HealthPage health={health} onConnect={connectIBKR} />}{activePage === "settings" && <SettingsPage theme={theme} onThemeChange={setTheme} />}</main></div><ReportDrawer report={selectedReport} onClose={() => setSelectedReport(null)} />{toast && <div className="toast" role="status"><CheckCircle size={19} weight="fill" />{toast}</div>}</div>;
}
