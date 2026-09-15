CREATE TABLE delivery_outbox (
 id TEXT PRIMARY KEY, source_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('preview','pending','preparing','sending','retry','sent','uncertain','failed','cancelled')),
 subject TEXT NOT NULL, body TEXT NOT NULL, html TEXT NOT NULL, message_id TEXT NOT NULL UNIQUE,
 mime BLOB NOT NULL, mime_sha256 TEXT NOT NULL, envelope_json TEXT NOT NULL, transport_hash TEXT,
 attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT NOT NULL,
 owner TEXT, lease_until TEXT, last_error TEXT, sent_at TEXT, expires_at TEXT
);
CREATE INDEX delivery_due ON delivery_outbox(status,next_attempt_at);
CREATE TABLE delivery_events (event_id TEXT PRIMARY KEY, delivery_id TEXT NOT NULL REFERENCES delivery_outbox(id));
CREATE TABLE delivery_audit (id INTEGER PRIMARY KEY, delivery_id TEXT NOT NULL REFERENCES delivery_outbox(id),
 at TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE runtime_samples (at TEXT PRIMARY KEY, payload_json TEXT NOT NULL);
