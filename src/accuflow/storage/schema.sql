PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tracked_stocks (
    symbol TEXT PRIMARY KEY,
    company_name TEXT NOT NULL DEFAULT '',
    con_id INTEGER,
    primary_exchange TEXT NOT NULL DEFAULT '',
    currency TEXT NOT NULL DEFAULT 'USD',
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    data_status TEXT NOT NULL DEFAULT 'pending',
    data_status_detail TEXT NOT NULL DEFAULT '等待 IBKR 合约解析',
    score INTEGER,
    signal TEXT NOT NULL DEFAULT '等待首次检测',
    last_checked_at TEXT,
    last_report_type TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tracked_stocks_con_id
ON tracked_stocks(con_id)
WHERE con_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS market_bars (
    symbol TEXT NOT NULL,
    con_id INTEGER NOT NULL,
    bar_size TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    average REAL,
    bar_count INTEGER,
    use_rth INTEGER NOT NULL CHECK (use_rth IN (0, 1)),
    source TEXT NOT NULL CHECK (source = 'IBKR'),
    received_at TEXT NOT NULL,
    PRIMARY KEY (con_id, bar_size, timestamp),
    FOREIGN KEY (symbol) REFERENCES tracked_stocks(symbol) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_market_bars_symbol_size_time
ON market_bars(symbol, bar_size, timestamp DESC);

CREATE TABLE IF NOT EXISTS capability_reports (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    con_id INTEGER NOT NULL,
    checked_at TEXT NOT NULL,
    snapshot_status TEXT NOT NULL,
    market_data_type INTEGER,
    historical_bars_status TEXT NOT NULL,
    historical_bars_count INTEGER NOT NULL DEFAULT 0,
    historical_ticks_status TEXT NOT NULL,
    historical_ticks_count INTEGER NOT NULL DEFAULT 0,
    tick_by_tick_last_status TEXT NOT NULL,
    tick_by_tick_bidask_status TEXT NOT NULL,
    details_json TEXT NOT NULL,
    FOREIGN KEY (symbol) REFERENCES tracked_stocks(symbol) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_capability_reports_symbol_time
ON capability_reports(symbol, checked_at DESC);

CREATE INDEX IF NOT EXISTS idx_capability_reports_time
ON capability_reports(checked_at DESC);

CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    report_time TEXT NOT NULL,
    report_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    judgment TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    counter_evidence_json TEXT NOT NULL,
    data_quality TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reports_time
ON reports(report_time DESC);

CREATE TABLE IF NOT EXISTS report_symbols (
    report_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (report_id, symbol),
    FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_report_symbols_symbol
ON report_symbols(symbol);

CREATE TABLE IF NOT EXISTS ibkr_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    request_id INTEGER NOT NULL,
    error_code INTEGER NOT NULL,
    message TEXT NOT NULL,
    symbol TEXT
);

CREATE TABLE IF NOT EXISTS system_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

