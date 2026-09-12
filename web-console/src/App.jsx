import { useMemo, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  CaretDown,
  ChartLineUp,
  CheckCircle,
  DotsThree,
  FileText,
  GearSix,
  MagnifyingGlass,
  Pause,
  Play,
  Pulse,
  SlidersHorizontal,
  Trash,
  UserCircle,
  WarningCircle,
  X,
} from "@phosphor-icons/react";

const initialStocks = [
  {
    symbol: "AAPL",
    company: "Apple Inc.",
    status: "tracking",
    coverage: "完整",
    coverageDetail: "实时 · 1分钟 · 1小时 · 日线",
    score: 68,
    signal: "中等偏强",
    checkedAt: "2026-09-11 16:00 ET",
    reportType: "收盘检测",
  },
  {
    symbol: "NVDA",
    company: "NVIDIA Corporation",
    status: "tracking",
    coverage: "完整",
    coverageDetail: "实时 · 1分钟 · 1小时 · 日线",
    score: 72,
    signal: "较强",
    checkedAt: "2026-09-11 16:00 ET",
    reportType: "收盘检测",
  },
  {
    symbol: "AMD",
    company: "Advanced Micro Devices",
    status: "incomplete",
    coverage: "缺少日线数据",
    coverageDetail: "实时 · 1分钟 · 1小时",
    score: 54,
    signal: "中性",
    checkedAt: "2026-09-11 15:00 ET",
    reportType: "1小时检测",
  },
  {
    symbol: "PLTR",
    company: "Palantir Technologies",
    status: "paused",
    coverage: "完整",
    coverageDetail: "实时 · 1分钟 · 1小时 · 日线",
    score: 48,
    signal: "中性",
    checkedAt: "2026-09-11 16:00 ET",
    reportType: "收盘检测",
  },
  {
    symbol: "MSFT",
    company: "Microsoft Corporation",
    status: "tracking",
    coverage: "完整",
    coverageDetail: "实时 · 1分钟 · 1小时 · 日线",
    score: 61,
    signal: "中等偏强",
    checkedAt: "2026-09-11 16:00 ET",
    reportType: "收盘检测",
  },
];

const reports = [
  {
    id: "closing-20260911",
    time: "2026-09-11 16:00 ET",
    type: "收盘报告",
    symbols: ["AAPL", "NVDA", "AMD", "PLTR", "MSFT"],
    summary: "5 只股票完成收盘检测 · 2 只信号较强 · 1 只数据不完整",
    conclusion: "NVDA 的持续需求迹象最强；AAPL 与 MSFT 保持中等偏强，AMD 因日线缺口限制结论升级。",
    evidence: ["NVDA 的买方压力分布在多个完整窗口", "AAPL 回踩参考区后恢复，成交重心保持", "MSFT 相对行业表现稳定"],
    counter: ["行业整体同步走强，股票特异性有所减弱", "AMD 缺少完整日线基线"],
    quality: "4 只完整，1 只数据不完整",
  },
  {
    id: "hourly-20260911-1500",
    time: "2026-09-11 15:00 ET",
    type: "小时报告",
    symbols: ["AAPL", "NVDA", "AMD", "PLTR", "MSFT"],
    summary: "5 只股票完成小时检测 · 1 只信号较强 · 1 只数据不完整",
    conclusion: "NVDA 出现新增独立证据，其他股票没有需要立即通知的状态变化。",
    evidence: ["NVDA 连续多个片段维持正向压力", "剔除最大贡献片段后异常仍存在"],
    counter: ["后续价格响应区间尚未完全结束"],
    quality: "整体良好，AMD 日线基线不完整",
  },
  {
    id: "hourly-20260911-1400",
    time: "2026-09-11 14:00 ET",
    type: "小时报告",
    symbols: ["AAPL", "NVDA", "AMD", "MSFT"],
    summary: "4 只股票完成小时检测 · 整体信号平稳",
    conclusion: "本检查点没有出现新的重要异动，既有状态保持。",
    evidence: ["AAPL 价格重心保持", "NVDA 相对强弱仍为正向"],
    counter: ["暂无新增跨日证据"],
    quality: "数据完整",
  },
  {
    id: "closing-20260910",
    time: "2026-09-10 16:00 ET",
    type: "收盘报告",
    symbols: ["AAPL", "NVDA", "AMD", "PLTR", "MSFT"],
    summary: "5 只股票完成收盘检测 · 1 只进入观察",
    conclusion: "NVDA 首次进入观察状态，尚未满足跨日升级门槛。",
    evidence: ["成交量和相对强弱高于同时间段常态"],
    counter: ["仅有一个完整交易日支持"],
    quality: "数据完整",
  },
];

const companyNames = {
  TSLA: "Tesla, Inc.", META: "Meta Platforms, Inc.", AMZN: "Amazon.com, Inc.",
  GOOGL: "Alphabet Inc.", AVGO: "Broadcom Inc.",
};

const navItems = [
  { id: "stocks", label: "跟踪股票", icon: ChartLineUp },
  { id: "reports", label: "历次报告", icon: FileText },
  { id: "health", label: "运行状态", icon: Pulse },
  { id: "settings", label: "设置", icon: GearSix },
];

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

function Topbar() {
  const [userOpen, setUserOpen] = useState(false);
  return <header className="topbar"><div className="connection"><span />IBKR 已连接<i>·</i><b>数据截至 13:32 ET</b></div><div className="user-area"><button className="user-button" onClick={() => setUserOpen((value) => !value)} aria-expanded={userOpen}><UserCircle size={23} /><span>用户</span><CaretDown size={15} /></button>{userOpen && <div className="user-menu"><strong>AccuFlow 管理员</strong><span>本地控制台</span></div>}</div></header>;
}

function Sidebar({ activePage, setActivePage }) {
  return <aside className="sidebar"><AppLogo /><nav aria-label="主导航">{navItems.map(({ id, label, icon: Icon }) => <button key={id} className={activePage === id ? "nav-item active" : "nav-item"} onClick={() => setActivePage(id)}><Icon size={23} /><span>{label}</span></button>)}</nav><div className="sidebar-foot"><span>统一检测规则 v1</span><span>只读模式</span></div></aside>;
}

function PageIntro({ title, description, backAction }) {
  return <div className="page-intro">{backAction && <button className="back-button" onClick={backAction}><ArrowLeft size={18} />返回</button>}<h1>{title}</h1><p>{description}</p></div>;
}

function StockRow({ stock, onToggle, onRemove }) {
  const [menuOpen, setMenuOpen] = useState(false);
  return <div className="stock-row table-grid"><div className="stock-id"><strong>{stock.symbol}</strong><span>{stock.company}</span></div><div><StatusPill status={stock.status} /></div><Coverage stock={stock} /><Signal stock={stock} /><div className="checked-at"><span>{stock.checkedAt}</span><small>{stock.reportType}</small></div><div className="stock-actions"><button className={stock.status === "paused" ? "button primary compact" : "button secondary compact"} onClick={() => onToggle(stock.symbol)}>{stock.status === "paused" ? "继续" : "暂停"}</button><div className="row-menu-wrap"><button className="icon-button" aria-label={`${stock.symbol} 更多操作`} onClick={() => setMenuOpen((value) => !value)}><DotsThree size={25} weight="bold" /></button>{menuOpen && <div className="row-menu"><button onClick={() => onToggle(stock.symbol)}>{stock.status === "paused" ? <Play size={17} /> : <Pause size={17} />}{stock.status === "paused" ? "继续跟踪" : "暂停跟踪"}</button><button className="danger" onClick={() => onRemove(stock.symbol)}><Trash size={17} />移除股票</button></div>}</div></div></div>;
}

function ReportsTable({ rows, onOpen }) {
  return <div className="reports-table"><div className="report-header report-grid"><span>报告时间</span><span>类型</span><span>涉及股票</span><span>摘要</span><span>操作</span></div>{rows.map((report) => <div className="report-row report-grid" key={report.id}><span>{report.time}</span><span><span className={`report-type ${report.type === "收盘报告" ? "closing" : "hourly"}`}>{report.type}</span></span><span className="report-symbols">{report.symbols.join(", ")}</span><span className="report-summary">{report.summary}</span><span><button className="button secondary small" onClick={() => onOpen(report)}>查看</button></span></div>)}</div>;
}

function StocksPage({ stocks, addStock, toggleStock, removeStock, openReport, showReports }) {
  const [ticker, setTicker] = useState("");
  const [error, setError] = useState("");
  function submit(event) {
    event.preventDefault();
    const normalized = ticker.trim().toUpperCase();
    if (!/^[A-Z][A-Z.-]{0,5}$/.test(normalized)) return setError("请输入有效的美股代码");
    if (stocks.some((stock) => stock.symbol === normalized)) return setError(`${normalized} 已在跟踪列表中`);
    addStock(normalized); setTicker(""); setError("");
  }
  return <><PageIntro title="跟踪股票" description="添加并管理要监控的美股，基于 IBKR 实时数据检测主力积累信号。" /><form className="add-stock" onSubmit={submit}><label className="sr-only" htmlFor="ticker">股票代码</label><input id="ticker" value={ticker} onChange={(event) => { setTicker(event.target.value); setError(""); }} placeholder="输入股票代码（如 AAPL）" autoComplete="off" /><button className="button primary" type="submit">添加股票</button>{error && <span className="form-error" role="alert">{error}</span>}</form><section className="data-section stocks-section" aria-label="股票跟踪列表"><div className="stock-header table-grid"><span>股票</span><span>状态</span><span>数据覆盖</span><span>最新信号</span><span>最近检测</span><span>操作</span></div>{stocks.length ? stocks.map((stock) => <StockRow key={stock.symbol} stock={stock} onToggle={toggleStock} onRemove={removeStock} />) : <div className="empty-state"><ChartLineUp size={34} /><strong>还没有跟踪股票</strong><span>在上方输入代码开始第一次监控。</span></div>}</section><section className="data-section recent-section"><div className="section-heading"><h2>最近报告</h2><button onClick={showReports}>查看全部报告 <ArrowRight size={18} /></button></div><ReportsTable rows={reports.slice(0, 3)} onOpen={openReport} /></section></>;
}

function ReportsPage({ openReport }) {
  const [query, setQuery] = useState("");
  const [type, setType] = useState("all");
  const filtered = useMemo(() => reports.filter((report) => (type === "all" || report.type === type) && `${report.symbols.join(" ")} ${report.summary} ${report.time}`.toLowerCase().includes(query.toLowerCase())), [query, type]);
  return <><PageIntro title="历次报告" description="查看每个检查点保存的判断、证据、反证和数据质量。" /><div className="filter-bar"><label className="search-control"><MagnifyingGlass size={19} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索股票或报告摘要" /></label><label className="select-control"><SlidersHorizontal size={18} /><select aria-label="报告类型" value={type} onChange={(event) => setType(event.target.value)}><option value="all">全部类型</option><option value="收盘报告">收盘报告</option><option value="小时报告">小时报告</option></select></label></div><section className="data-section full-reports">{filtered.length ? <ReportsTable rows={filtered} onOpen={openReport} /> : <div className="empty-state"><FileText size={34} /><strong>没有匹配的报告</strong><span>试试清除搜索条件或切换报告类型。</span></div>}</section></>;
}

function HealthPage() {
  const rows = [["IBKR 会话", "已连接", "最近心跳 13:32:08 ET"], ["历史数据服务", "正常", "最近成功请求 13:31:42 ET"], ["逐笔订阅", "正常", "2 个成交流 · 2 个 BidAsk 流"], ["任务调度", "正常", "下一检查点 14:30 ET"], ["邮件服务", "正常", "最近投递 2026-09-11 16:21 ET"]];
  return <><PageIntro title="运行状态" description="检查行情连接、数据新鲜度、任务调度与通知服务。" /><section className="health-summary"><div><span className="health-orb"><CheckCircle size={24} weight="fill" /></span><div><strong>系统运行正常</strong><p>所有关键服务均在预期范围内。</p></div></div><span>更新于 13:32 ET</span></section><section className="data-section health-list">{rows.map(([name, status, detail]) => <div className="health-row" key={name}><div><strong>{name}</strong><span>{detail}</span></div><span className="health-badge"><CheckCircle size={18} weight="fill" />{status}</span></div>)}</section></>;
}

function SettingsPage() {
  const [dailyReport, setDailyReport] = useState(true);
  const [hourlyAlert, setHourlyAlert] = useState(true);
  return <><PageIntro title="设置" description="管理报告和提醒偏好；行情来源固定为 IBKR。" /><section className="settings-panel data-section"><div className="settings-group"><div><strong>收盘日报</strong><span>每个交易日收盘后发送完整报告</span></div><button className={`switch ${dailyReport ? "on" : ""}`} onClick={() => setDailyReport((value) => !value)} aria-pressed={dailyReport}><span /></button></div><div className="settings-group"><div><strong>小时异动提醒</strong><span>仅在新异动、显著增强或失效时发送</span></div><button className={`switch ${hourlyAlert ? "on" : ""}`} onClick={() => setHourlyAlert((value) => !value)} aria-pressed={hourlyAlert}><span /></button></div><div className="settings-group"><div><strong>行情来源</strong><span>ib_async · IBKR API · 只读连接</span></div><span className="locked-value">固定</span></div></section></>;
}

function ReportDrawer({ report, onClose }) {
  if (!report) return null;
  return <div className="drawer-layer" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><aside className="report-drawer" role="dialog" aria-modal="true" aria-labelledby="report-title"><div className="drawer-head"><div><span>{report.type}</span><h2 id="report-title">{report.time}</h2></div><button className="icon-button" onClick={onClose} aria-label="关闭报告"><X size={22} /></button></div><div className="drawer-meta"><span>涉及股票</span><strong>{report.symbols.join(" · ")}</strong></div><section><h3>判断</h3><p>{report.conclusion}</p></section><section><h3>支持证据</h3><ul>{report.evidence.map((item) => <li key={item}>{item}</li>)}</ul></section><section><h3>反证</h3><ul>{report.counter.map((item) => <li key={item}>{item}</li>)}</ul></section><section><h3>数据质量</h3><p>{report.quality}</p></section><div className="drawer-foot">本报告为规则检测结果，不构成投资建议。</div></aside></div>;
}

export function App() {
  const [activePage, setActivePage] = useState("stocks");
  const [stocks, setStocks] = useState(initialStocks);
  const [selectedReport, setSelectedReport] = useState(null);
  const [toast, setToast] = useState("");
  function notify(message) { setToast(message); window.setTimeout(() => setToast(""), 2400); }
  function addStock(symbol) { setStocks((items) => [...items, { symbol, company: companyNames[symbol] || `${symbol} Corporation`, status: "tracking", coverage: "等待数据", coverageDetail: "合约解析中", score: "—", signal: "等待首次检测", checkedAt: "尚未检测", reportType: "—" }]); notify(`${symbol} 已加入跟踪列表`); }
  function toggleStock(symbol) { const wasPaused = stocks.find((stock) => stock.symbol === symbol)?.status === "paused"; setStocks((items) => items.map((stock) => stock.symbol === symbol ? { ...stock, status: wasPaused ? (stock.coverage === "完整" ? "tracking" : "incomplete") : "paused" } : stock)); notify(`${symbol} 已${wasPaused ? "继续" : "暂停"}跟踪`); }
  function removeStock(symbol) { setStocks((items) => items.filter((stock) => stock.symbol !== symbol)); notify(`${symbol} 已从列表移除`); }
  return <div className="app-shell"><Sidebar activePage={activePage} setActivePage={setActivePage} /><div className="workspace"><Topbar /><main className="content">{activePage === "stocks" && <StocksPage stocks={stocks} addStock={addStock} toggleStock={toggleStock} removeStock={removeStock} openReport={setSelectedReport} showReports={() => setActivePage("reports")} />}{activePage === "reports" && <ReportsPage openReport={setSelectedReport} />}{activePage === "health" && <HealthPage />}{activePage === "settings" && <SettingsPage />}</main></div><ReportDrawer report={selectedReport} onClose={() => setSelectedReport(null)} />{toast && <div className="toast" role="status"><CheckCircle size={19} weight="fill" />{toast}</div>}</div>;
}
