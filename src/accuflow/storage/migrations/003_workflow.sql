CREATE TABLE job_runs (
    job_key TEXT PRIMARY KEY, checkpoint TEXT NOT NULL, report_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('running','completed','failed')),
    owner TEXT NOT NULL, lease_until TEXT NOT NULL, attempts INTEGER NOT NULL,
    symbols_json TEXT NOT NULL, error TEXT, report_id TEXT, updated_at TEXT NOT NULL
);
CREATE INDEX idx_jobs_status ON job_runs(status, checkpoint);
CREATE TABLE checkpoint_inputs (
    job_key TEXT NOT NULL REFERENCES job_runs(job_key), symbol TEXT NOT NULL,
    payload_json TEXT NOT NULL, PRIMARY KEY(job_key,symbol)
);
CREATE TABLE report_inputs (
    report_id TEXT PRIMARY KEY REFERENCES reports(id), payload_json TEXT NOT NULL,
    sha256 TEXT NOT NULL, captured_at TEXT NOT NULL
);
CREATE TABLE notification_outbox (
    id TEXT PRIMARY KEY, report_id TEXT NOT NULL UNIQUE REFERENCES reports(id),
    status TEXT NOT NULL CHECK(status='preview'), subject TEXT NOT NULL,
    body TEXT NOT NULL, created_at TEXT NOT NULL
);
