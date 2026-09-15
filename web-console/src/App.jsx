import { useEffect, useMemo, useState, useRef } from "react";
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
  return <div className="wordmark">Accu<span>Flow</span></div>;
}

function StatusPill({ status }) {
  const labels = { tracking: "跟踪中", incomplete: "数据不完整", paused: "已暂停" };
  return <span className={`status-pill is-${status}`}><span className="status-dot" />{labels[status]}</span>;
}

function Coverage({ stock }) {
  const complete = stock.dataStatus === "ready";
  const Icon = complete ? CheckCircle : WarningCircle;
  return <div className="coverage-block"><span className="coverage-detail">{stock.coverageDetail}</span><span className={complete ? "coverage-ok" : "coverage-warning"}><Icon weight="fill" size={18} />{stock.coverage}</span></div>;
}

function Signal({ stock }) {
  return <div className="signal-block"><strong>{stock.score ?? "—"}</strong><span className={stock.score >= 60 ? "signal-positive" : "signal-neutral"}>{stock.signal}</span></div>;
}

function Topbar({ health, onConnect, theme, onToggleTheme, busy }) {
  const [userOpen, setUserOpen] = useState(false);
  const connected = Boolean(health?.ibkr?.connected);
  const serverTime = health?.ibkr?.server_time;
  const timeLabel = serverTime ? `${new Date(serverTime).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", timeZone: "America/New_York" })} ET` : "已就绪";
  const ThemeIcon = theme === "dark" ? Sun : Moon;
  const themeLabel = theme === "dark" ? "切换到浅色模式" : "切换到深色模式";
  return <header className="topbar"><button className={`connection ${connected ? "" : "is-disconnected"}`} disabled={busy} onClick={connected ? undefined : onConnect}><span />{connected ? "IBKR 已连接" : "IBKR 未连接"}<i>·</i><b>{connected ? `连接时服务器时间 ${timeLabel}` : "点击连接"}</b></button><button className="icon-button theme-toggle" onClick={onToggleTheme} aria-label={themeLabel} title={themeLabel}><ThemeIcon size={21} /></button><div className="user-area"><button className="user-button" onClick={() => setUserOpen((value) => !value)} aria-expanded={userOpen}><UserCircle size={23} /><span>用户</span><CaretDown size={15} /></button>{userOpen && <div className="user-menu"><strong>AccuFlow 管理员</strong><span>本地控制台</span></div>}</div></header>;
}

function Sidebar({ activePage, setActivePage }) {
  return <aside className="sidebar"><AppLogo /><nav aria-label="主导航">{navItems.map(({ id, label, icon: Icon }) => <button key={id} className={activePage === id ? "nav-item active" : "nav-item"} onClick={() => setActivePage(id)}><Icon size={23} /><span>{label}</span></button>)}</nav><div className="sidebar-foot"><span>统一检测规则 v1</span><span>只读模式</span></div></aside>;
}

function PageIntro({ title, description, backAction }) {
  return <div className="page-intro">{backAction && <button className="back-button" onClick={backAction}><ArrowLeft size={18} />返回</button>}<h1>{title}</h1><p>{description}</p></div>;
}

function StockRow({ stock, onToggle, onRemove, onSync, onAnalyze, busy }) {
  const [menuOpen, setMenuOpen] = useState(false);
  return <div className="stock-row table-grid"><div className="stock-id"><strong>{stock.symbol}</strong><span>{stock.company}</span></div><div><StatusPill status={stock.status} /></div><Coverage stock={stock} /><Signal stock={stock} /><div className="checked-at"><span>{stock.checkedAt || "尚未检测"}</span><small>{stock.reportType || "—"}</small></div><div className="stock-actions"><button className={stock.status === "paused" ? "button primary compact" : "button secondary compact"} disabled={busy} onClick={() => onToggle(stock.symbol)}>{stock.status === "paused" ? "继续" : "暂停"}</button><div className="row-menu-wrap"><button className="icon-button" disabled={busy} aria-label={`${stock.symbol} 更多操作`} onClick={() => setMenuOpen((value) => !value)}><DotsThree size={25} weight="bold" /></button>{menuOpen && <div className="row-menu"><button onClick={() => { setMenuOpen(false); onAnalyze(stock.symbol); }}><ChartLineUp size={17} />检测详情与配置</button><button disabled={busy} onClick={() => { setMenuOpen(false); onSync(stock.symbol); }}><ArrowsClockwise size={17} />同步 IBKR 数据</button><button disabled={busy} onClick={() => onToggle(stock.symbol)}>{stock.status === "paused" ? <Play size={17} /> : <Pause size={17} />}{stock.status === "paused" ? "继续跟踪" : "暂停跟踪"}</button><button className="danger" disabled={busy} onClick={() => onRemove(stock.symbol)}><Trash size={17} />移除股票</button></div>}</div></div></div>;
}

function ReportsTable({ rows, onOpen }) {
  return <div className="reports-table"><div className="report-header report-grid"><span>报告时间</span><span>类型</span><span>涉及股票</span><span>摘要</span><span>操作</span></div>{rows.map((report) => <div className="report-row report-grid" key={report.id}><span>{report.time}</span><span><span className={`report-type ${report.type === "收盘报告" ? "closing" : "hourly"}`}>{report.type}</span></span><span className="report-symbols">{report.symbols.join(", ")}</span><span className="report-summary">{report.summary}</span><span><button className="button secondary small" onClick={() => onOpen(report)}>查看</button></span></div>)}</div>;
}

function StocksPage({ stocks, reports, addStock, toggleStock, removeStock, syncStock, openReport, showReports, busy, generateReport, onAnalyze }) {
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
  return <><PageIntro title="跟踪股票" description="管理 IBKR 跟踪股票，查看统一评分、跨日迹象与数据质量。" /><form className="add-stock" onSubmit={submit}><label className="sr-only" htmlFor="ticker">股票代码</label><input id="ticker" value={ticker} onChange={(event) => { setTicker(event.target.value); setError(""); }} placeholder="输入股票代码（如 AAPL）" autoComplete="off" /><button className="button primary" type="submit" disabled={busy.add}>添加股票</button>{error && <span className="form-error" role="alert">{error}</span>}</form><section className="data-section stocks-section" aria-label="股票跟踪列表"><div className="stock-header table-grid"><span>股票</span><span>状态</span><span>数据覆盖</span><span>最新信号</span><span>最近检测</span><span>操作</span></div>{stocks.length ? stocks.map((stock) => <StockRow key={stock.symbol} stock={stock} onToggle={toggleStock} onRemove={removeStock} onSync={syncStock} onAnalyze={onAnalyze} busy={busy[stock.symbol]} />) : <div className="empty-state"><ChartLineUp size={34} /><strong>还没有跟踪股票</strong><span>在上方输入代码开始第一次监控。</span></div>}</section><section className="data-section recent-section"><div className="section-heading"><h2>最近报告</h2><button disabled={busy.report} onClick={generateReport}>{busy.report ? "正在生成…" : "生成检测报告"}</button><button onClick={showReports}>查看全部报告 <ArrowRight size={18} /></button></div><ReportsTable rows={reports.slice(0, 3)} onOpen={openReport} /></section></>;
}

function ReportsPage({ reports, openReport }) {
  const [query, setQuery] = useState("");
  const [type, setType] = useState("all");
  const filtered = useMemo(() => reports.filter((report) => (type === "all" || report.type === type) && `${report.symbols.join(" ")} ${report.summary} ${report.time}`.toLowerCase().includes(query.toLowerCase())), [reports, query, type]);
  return <><PageIntro title="历次报告" description="查看每个检查点保存的判断、证据、反证和数据质量。" /><div className="filter-bar"><label className="search-control"><MagnifyingGlass size={19} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索股票或报告摘要" /></label><label className="select-control"><SlidersHorizontal size={18} /><select aria-label="报告类型" value={type} onChange={(event) => setType(event.target.value)}><option value="all">全部类型</option><option value="收盘报告">收盘报告</option><option value="小时报告">小时报告</option></select></label></div><section className="data-section full-reports">{filtered.length ? <ReportsTable rows={filtered} onOpen={openReport} /> : <div className="empty-state"><FileText size={34} /><strong>没有匹配的报告</strong><span>试试清除搜索条件或切换报告类型。</span></div>}</section></>;
}

function HealthPage({ health, onConnect, busy, jobs, outbox, deliveries, onResolved }) {
  const connected = Boolean(health?.ibkr?.connected);
  const rows = [
    ["IBKR 会话", connected ? "已连接" : "未连接", `${health?.ibkr?.host || "127.0.0.1"}:${health?.ibkr?.port || "—"} · clientId ${health?.ibkr?.client_id || "—"}`],
    ["自动检查点", health?.workflow?.running_key ? "运行中" : "空闲", health?.workflow?.last_error || "按交易日生成小时和收盘检测报告"],
    ["邮件投递", health?.delivery?.mode === "smtp" ? "已启用 SMTP" : "仅本地预览", health?.delivery?.last_error || "投递不确定时暂停重试，避免重复通知"],
    ["运行资源", health?.telemetry?.latest ? `${(health.telemetry.latest.process_rss_bytes / 1048576).toFixed(0)} MiB` : "等待采样", health?.telemetry?.latest ? `CPU ${health.telemetry.latest.process_cpu_percent.toFixed(1)}% · Gateway 进程 ${health.telemetry.latest.gateway_processes} · 事件循环延迟 ${health.telemetry.latest.event_loop_lag_seconds.toFixed(2)}s` : "自动记录 CPU、内存、任务队列与磁盘增长"],
    ["SQLite 存储", health?.database === "ok" ? "正常" : "异常", "跟踪列表、行情 bars 与历次报告持久化"],
    ["行情请求配置", health?.ibkr?.market_data_type === 1 ? "请求实时" : "请求延迟/冻结", "配置值不代表已获得对应行情权限；实际覆盖需通过能力探测确认"],
  ];
  return <><PageIntro title="运行状态" description="检查唯一行情源 IBKR 和本地持久化服务。" /><section className={`health-summary ${connected ? "" : "is-warning"}`}><div><span className="health-orb">{connected ? <CheckCircle size={24} weight="fill" /> : <WarningCircle size={24} weight="fill" />}</span><div><strong>{connected ? "行情连接正常" : "IBKR 尚未连接"}</strong><p>{connected ? "只读 API 会话已建立。" : health?.ibkr?.last_error || "请确认 Gateway/TWS 已启动并开放 API 端口。"}</p></div></div>{!connected && <button className="button primary" disabled={busy} onClick={onConnect}>连接 IBKR</button>}</section><section className="data-section health-list">{rows.map(([name, state, detail]) => { const warning = state === "未连接" || state === "异常"; return <div className="health-row" key={name}><div><strong>{name}</strong><span>{detail}</span></div><span className={`health-badge ${warning ? "is-warning" : ""}`}>{warning ? <WarningCircle size={18} weight="fill" /> : <CheckCircle size={18} weight="fill" />}{state}</span></div>; })}</section><section className="data-section workflow-section"><div className="section-heading"><h2>检查点任务</h2></div>{jobs.length ? jobs.map((job) => <div className="health-row" key={job.job_key}><div><strong>{job.report_type} · {new Date(job.checkpoint).toLocaleString("zh-CN")}</strong><span>{job.error || job.report_id || "等待任务完成"}</span></div><span>{({ completed:"已完成", running:"运行中", failed:"失败" })[job.status]}</span></div>) : <p className="workflow-empty">暂无任务。在设置中开启自动质量监测后，将按交易日运行。</p>}</section>
    <DeliveryPanel rows={deliveries} onResolved={onResolved} /><section className="data-section workflow-section"><div className="section-heading"><h2>历史通知预览</h2></div>{outbox.length ? outbox.map((item) => <details className="notification-preview" key={item.id}><summary>{item.subject} · 未发送</summary><p>{item.body}</p></details>) : <p className="workflow-empty">暂无通知预览。生成收盘检测报告后可在此查看。</p>}</section></>;
}

function DeliveryPanel({ rows, onResolved }) {
  const [content, setContent] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const labels = { preview: "仅预览", pending: "等待发送", preparing: "正在连接", sending: "正在提交", retry: "等待重试", sent: "服务器已接收", uncertain: "投递结果不确定", failed: "投递失败", cancelled: "已取消" };
  async function show(id) { setError(""); try { setContent(await api.deliveryContent(id)); } catch (e) { setError(e.message); } }
  async function resolve(id, value) {
    setBusy(true); setError("");
    try { await api.resolveDelivery(id, value); await onResolved(); } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  return <section className="data-section workflow-section"><div className="section-heading"><h2>邮件投递队列</h2></div>
    {rows.length ? rows.map((item) => <div className="notification-preview" key={item.id}><strong>{item.subject}</strong><p>{labels[item.status]} · 已尝试 {item.attempts} 次</p>{item.last_error && <p>{item.last_error}</p>}
      <button className="button secondary small" onClick={() => show(item.id)}>查看邮件</button>
      {item.status === "uncertain" && <div><p>连接中断前邮件可能已被接收。请先核对邮箱或 SMTP 日志，再确认结果；系统不会自动重发。</p><button disabled={busy} className="button secondary small" onClick={() => resolve(item.id, "confirmed_sent")}>已核实服务器接收</button> <button disabled={busy} className="button secondary small" onClick={() => resolve(item.id, "cancelled")}>取消后续处理</button></div>}
    </div>) : <p className="workflow-empty">暂无邮件。生成收盘日报后可查看完整内容。</p>}
    {error && <p role="alert">{error}</p>}
    {content && <div className="drawer-layer"><aside className="report-drawer" role="dialog" aria-modal="true" aria-label="邮件预览"><div className="drawer-head"><h2>邮件预览</h2><button className="icon-button" aria-label="关闭邮件预览" onClick={() => setContent(null)}><X size={22} /></button></div><p>{content.subject}</p><iframe title="HTML 邮件" sandbox="" srcDoc={content.html} style={{ width: "100%", height: 480, border: 0 }} /><details><summary>纯文本版本</summary><pre style={{ whiteSpace: "pre-wrap" }}>{content.body}</pre></details></aside></div>}
  </section>;
}

function SettingsPage({ theme, onThemeChange, preferences, onSave, busy }) {
  const live = preferences?.delivery_mode === "smtp";
  return <><PageIntro title="设置" description={preferences?.delivery_mode === "smtp" ? "SMTP 投递已启用，新通知按服务端配置发送。" : "当前只保存邮件预览；SMTP 由服务端环境配置启用。"} />
    <section className="settings-panel data-section">
      <div className="settings-group"><div><strong>界面主题</strong><span>选择显示模式</span></div><div className="theme-options" role="group" aria-label="界面主题">{["light", "dark"].map((value) => <button key={value} className={theme === value ? "selected" : ""} onClick={() => onThemeChange(value)} aria-pressed={theme === value}>{value === "light" ? "浅色" : "深色"}</button>)}</div></div>
      <div className="settings-group"><div><strong>自动质量监测</strong><span>按交易日自动连接 IBKR、补数并生成检查点报告</span></div><button aria-label="自动质量监测" disabled={!preferences || busy} className={`switch ${preferences?.monitoring_enabled ? "on" : ""}`} onClick={() => onSave({ monitoring_enabled: !preferences.monitoring_enabled })} aria-pressed={Boolean(preferences?.monitoring_enabled)}><span /></button></div>
      <div className="settings-group"><div><strong>{live ? "收盘日报邮件" : "收盘日报预览"}</strong><span>生成并保存收盘日报，是否发送取决于当前投递配置</span></div><button aria-label={live ? "收盘日报邮件" : "收盘日报预览"} disabled={!preferences || busy} className={`switch ${preferences?.daily_report ? "on" : ""}`} onClick={() => onSave({ daily_report: !preferences.daily_report })} aria-pressed={Boolean(preferences?.daily_report)}><span /></button></div>
      <div className="settings-group"><div><strong>小时异动提醒</strong><span>合并新异动、增强与失效事件；按当前投递配置处理</span></div><button aria-label="小时异动提醒" disabled={!preferences?.hourly_alert_available || busy} className={`switch ${preferences?.hourly_alert ? "on" : ""}`} onClick={() => onSave({ hourly_alert: !preferences.hourly_alert })} aria-pressed={Boolean(preferences?.hourly_alert)}><span /></button></div>
      <div className="settings-group"><div><strong>行情来源</strong><span>IBKR · 只读连接</span></div><span className="locked-value">固定</span></div>
    </section></>;
}

function ReportDrawer({ report, onClose }) {
  if (!report) return null;
  return <div className="drawer-layer" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><aside className="report-drawer" role="dialog" aria-modal="true" aria-labelledby="report-title"><div className="drawer-head"><div><span>{report.type}</span><h2 id="report-title">{report.time}</h2></div><button className="icon-button" onClick={onClose} aria-label="关闭报告"><X size={22} /></button></div><div className="drawer-meta"><span>涉及股票</span><strong>{report.symbols.join(" · ")}</strong></div><section><h3>判断</h3><p>{report.conclusion}</p></section><section><h3>支持证据</h3><ul>{report.evidence.map((item) => <li key={item}>{item}</li>)}</ul></section><section><h3>反证</h3><ul>{report.counter.map((item) => <li key={item}>{item}</li>)}</ul></section><section><h3>数据质量</h3><p>{report.quality}</p></section><div className="drawer-foot">本报告为规则检测结果，不构成投资建议。</div></aside></div>;
}

function AnalysisDrawer({ symbol, onClose }) {
  const [context, setContext] = useState(null);
  const [signal, setSignal] = useState(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!symbol) return;
    let active = true;
    Promise.all([api.context(symbol), api.signals(symbol)]).then(([value, rows]) => {
      if (!active) return;
      setContext({ units_verified: false, adjustment_verified: false, major_event: false, market_symbol: "SPY", sector_symbol: "", event_notes: "", ...value });
      setSignal(rows[0] || null);
    }).catch((error) => { if (active) setMessage(error.message); });
    return () => { active = false; };
  }, [symbol]);
  if (!symbol) return null;
  async function save(event) {
    event.preventDefault(); setBusy(true); setMessage("");
    try {
      const now = new Date();
      const effective = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
      const saved = await api.saveContext(symbol, { ...context, known_at: now.toISOString(), effective_session: effective, sector_symbol: context.sector_symbol || null, event_review_through: context.review_now ? now.toISOString() : (context.event_review_through || null), review_now: undefined });
      setContext(saved); setMessage("配置已保存，下一次检测使用。历史快照保持不变。");
    } catch (error) { setMessage(error.message); } finally { setBusy(false); }
  }
  async function replay() {
    setBusy(true);
    try { const value = await api.signalReplay(signal.snapshot_id); setMessage(value.matches ? "回放一致：评分、状态与事件均与原始快照相同。" : "回放不一致，请检查规则版本。"); }
    catch (error) { setMessage(error.message); } finally { setBusy(false); }
  }
  const names = { core_behavior: "核心行为", cross_day: "跨日持续", price_response: "价格响应", relative_strength: "相对强弱", distribution: "分布与VWAP" };
  const weights = { core_behavior: 30, cross_day: 25, price_response: 20, relative_strength: 15, distribution: 10 };
  return <div className="drawer-layer"><aside className="report-drawer" role="dialog" aria-modal="true" aria-labelledby="analysis-title">
    <div className="drawer-head"><h2 id="analysis-title">{symbol} · 检测详情</h2><button className="icon-button" onClick={onClose} aria-label="关闭检测详情"><X size={22} /></button></div>
    {signal ? <><section><h3>统一评分 {signal.score ?? "—"}</h3><p>{signal.status_label} · {signal.as_of}</p><small>{signal.rule_version}</small>
      <ul>{Object.entries(names).map(([key, name]) => <li key={key}>{name}：{signal.families[key].available ? (signal.families[key].value * weights[key]).toFixed(1) : "不可用"} / {weights[key]}</li>)}</ul><p>反证扣分：{signal.contradiction_penalty}</p></section>
      <section><h3>最近五个交易日</h3><ul>{signal.families.cross_day.timeline.map((day) => <li key={day.date}>{day.date}：{!day.evaluated ? "数据不足" : day.supported ? "有支持证据" : "无支持证据"}{day.closed ? " · 已收盘" : " · 盘中"}</li>)}</ul></section>
      <section><h3>反证与缺失项</h3><ul>{signal.counter_evidence.map((text) => <li key={text}>{text}</li>)}</ul><button className="button secondary" disabled={busy} onClick={replay}>回放检测快照</button></section></> : <p>尚无检测记录。保存配置并生成检测报告后可查看。</p>}
    {context && <form className="analysis-form" onSubmit={save}><h3>分析配置</h3><p>仅勾选已完成核验的项目。事件核验按当前时间记录，缺失会限制信号升级。</p>
      {[['units_verified','已核验 IBKR 成交量单位为股'],['adjustment_verified','已核验公司行动与复权口径'],['review_now','已核验截至当前的事件背景'],['major_event','存在影响价格的重大事件']].map(([key,label]) => <label key={key}><input type="checkbox" checked={Boolean(context[key])} onChange={(event) => setContext({ ...context, [key]: event.target.checked })} />{label}</label>)}
      <label>大盘参照<input value={context.market_symbol || ""} onChange={(event) => setContext({ ...context, market_symbol: event.target.value.toUpperCase() })} maxLength={12} /></label>
      <label>行业参照（可选）<input value={context.sector_symbol || ""} onChange={(event) => setContext({ ...context, sector_symbol: event.target.value.toUpperCase() })} maxLength={12} /></label>
      <p>参照股票需加入跟踪并同步行情，缺失时对应分项不可用。</p><label>核验记录<textarea value={context.event_notes} maxLength={2000} onChange={(event) => setContext({ ...context, event_notes: event.target.value })} /></label>
      <button className="button primary" disabled={busy} type="submit">保存分析配置</button></form>}
    {message && <p role="status">{message}</p>}
  </aside></div>;
}

export function App() {
  const [activePage, setActivePage] = useState("stocks");
  const [theme, setTheme] = useState(initialTheme);
  const [stocks, setStocks] = useState([]);
  const [reports, setReports] = useState([]);
  const [health, setHealth] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [outbox, setOutbox] = useState([]);
  const [deliveries, setDeliveries] = useState([]);
  const [selectedReport, setSelectedReport] = useState(null);
  const [analysisSymbol, setAnalysisSymbol] = useState(null);
  const [toast, setToast] = useState("");
  const [errors, setErrors] = useState({});
  const [preferences, setPreferences] = useState(null);
  const [busy, setBusy] = useState({});
  const locks = useRef(new Set());
  const revision = useRef(0);
  const toastTimer = useRef();
  function notify(message) {
    setToast(message);
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(""), 5000);
  }
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { window.localStorage.setItem(THEME_STORAGE_KEY, theme); } catch {}
  }, [theme]);
  useEffect(() => {
    let stopped = false;
    let timer;
    let controller;
    async function refresh() {
      controller = new AbortController();
      const started = revision.current;
      const resources = [
        ["stocks", "股票", api.stocks, setStocks],
        ["reports", "报告", api.reports, setReports],
        ["health", "运行状态", api.health, setHealth],
        ["settings", "设置", api.settings, setPreferences],
        ["jobs", "检查点任务", api.jobs, setJobs],
        ["outbox", "通知预览", api.outbox, setOutbox],
        ["deliveries", "邮件投递", api.deliveries, setDeliveries],
      ];
      const results = await Promise.allSettled(resources.map(([, , fetcher]) => fetcher({ signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]) })));
      if (stopped) return;
      results.forEach((result, index) => {
        const [key, label, , setter] = resources[index];
        if (result.status === "fulfilled") {
          if (key === "health" || (started === revision.current && locks.current.size === 0)) setter(result.value);
          setErrors((old) => ({ ...old, [key]: "" }));
        } else {
          setErrors((old) => ({ ...old, [key]: `${label}读取失败：${result.reason.message}（自动重试中）` }));
          if (key === "health") setHealth(null);
        }
      });
      timer = window.setTimeout(refresh, 5000);
    }
    refresh();
    return () => { stopped = true; controller?.abort(); window.clearTimeout(timer); window.clearTimeout(toastTimer.current); };
  }, []);
  async function action(key, operation, rethrow = false) {
    if (locks.current.has(key)) return;
    locks.current.add(key); revision.current += 1;
    setBusy((old) => ({ ...old, [key]: true }));
    try { return await operation(); }
    catch (error) { notify(`操作失败：${error.message}`); if (rethrow) throw error; }
    finally { locks.current.delete(key); revision.current += 1; setBusy((old) => ({ ...old, [key]: false })); }
  }
  async function addStock(symbol) {
    return action("add", async () => { const created = await api.addStock(symbol); setStocks((items) => [...items.filter((x) => x.symbol !== symbol), created]); notify(`${symbol} 已加入跟踪列表`); }, true);
  }
  async function toggleStock(symbol) {
    return action(symbol, async () => { const stock = stocks.find((item) => item.symbol === symbol); const updated = await api.updateStock(symbol, !stock.active); setStocks((items) => items.map((item) => item.symbol === symbol ? updated : item)); notify(`${symbol} 已${updated.active ? "继续" : "暂停"}跟踪`); });
  }
  async function removeStock(symbol) {
    return action(symbol, async () => { await api.removeStock(symbol); setStocks((items) => items.filter((stock) => stock.symbol !== symbol)); notify(`${symbol} 已从列表移除`); });
  }
  async function syncStock(symbol) {
    return action(symbol, async () => { notify(`${symbol} 正在分片同步，可继续浏览其他页面`); const result = await api.backfillStock(symbol); setStocks(await api.stocks()); notify(`${symbol} 已同步：日线 ${result.stored.daily}，1分钟 ${result.stored.minute}`); });
  }
  async function connectIBKR() {
    return action("connect", async () => { await api.connectIBKR(); setHealth(await api.health()); notify("IBKR 只读会话已连接"); });
  }
  async function savePreferences(values) {
    return action("settings", async () => { setPreferences(await api.saveSettings(values)); notify("设置已保存；邮件发送尚未启用"); });
  }
  async function generateReport() {
    return action("report", async () => { await api.generateReport(); setReports(await api.reports()); setStocks(await api.stocks()); setOutbox(await api.outbox()); notify("检测报告已保存"); });
  }
  return <div className="app-shell"><Sidebar activePage={activePage} setActivePage={setActivePage} /><div className="workspace"><Topbar busy={busy.connect} health={health} onConnect={connectIBKR} theme={theme} onToggleTheme={() => setTheme((value) => value === "dark" ? "light" : "dark")} /><main className="content">{Object.entries(errors).filter(([, message]) => message).map(([key, message]) => <p className="request-error" role="alert" key={key}>{message}</p>)}{activePage === "stocks" && <StocksPage onAnalyze={setAnalysisSymbol} busy={busy} generateReport={generateReport} stocks={stocks} reports={reports} addStock={addStock} toggleStock={toggleStock} removeStock={removeStock} syncStock={syncStock} openReport={setSelectedReport} showReports={() => setActivePage("reports")} />}{activePage === "reports" && <ReportsPage reports={reports} openReport={setSelectedReport} />}{activePage === "health" && <HealthPage deliveries={deliveries} onResolved={async () => setDeliveries(await api.deliveries())} jobs={jobs} outbox={outbox} busy={busy.connect} health={health} onConnect={connectIBKR} />}{activePage === "settings" && <SettingsPage preferences={preferences} onSave={savePreferences} busy={busy.settings} theme={theme} onThemeChange={setTheme} />}</main></div><AnalysisDrawer key={analysisSymbol} symbol={analysisSymbol} onClose={() => setAnalysisSymbol(null)} /><ReportDrawer report={selectedReport} onClose={() => setSelectedReport(null)} />{toast && <div className="toast" role="status"><CheckCircle size={19} weight="fill" />{toast}</div>}</div>;
}
