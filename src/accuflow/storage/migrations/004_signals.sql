CREATE TABLE analysis_contexts (
 id INTEGER PRIMARY KEY, instrument_key TEXT NOT NULL, known_at TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE INDEX context_history ON analysis_contexts(instrument_key,known_at);
CREATE TABLE flow_windows (
 instrument_key TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL, captured_at TEXT NOT NULL,
 payload_json TEXT NOT NULL, PRIMARY KEY(instrument_key,start)
);
CREATE TABLE signal_evaluations (
 id TEXT PRIMARY KEY, instrument_key TEXT NOT NULL, symbol TEXT NOT NULL, as_of TEXT NOT NULL,
 rule_version TEXT NOT NULL, snapshot_gzip BLOB NOT NULL, result_json TEXT NOT NULL,
 UNIQUE(instrument_key,as_of,rule_version)
);
CREATE INDEX evaluation_history ON signal_evaluations(instrument_key,as_of);
CREATE TABLE signal_days (
 instrument_key TEXT NOT NULL, session_date TEXT NOT NULL, rule_version TEXT NOT NULL,
 as_of TEXT NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY(instrument_key,session_date,rule_version)
);
CREATE TABLE signal_states (
 instrument_key TEXT NOT NULL, rule_version TEXT NOT NULL, as_of TEXT NOT NULL,
 payload_json TEXT NOT NULL, PRIMARY KEY(instrument_key,rule_version)
);
CREATE TABLE signal_events (
 id TEXT PRIMARY KEY, evaluation_id TEXT NOT NULL REFERENCES signal_evaluations(id),
 payload_json TEXT NOT NULL
);
CREATE TABLE signal_previews (
 id TEXT PRIMARY KEY REFERENCES signal_events(id), status TEXT NOT NULL CHECK(status='preview'),
 subject TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL
);
