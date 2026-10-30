"""Camada SQLite: schema, conexão e configurações."""
import sqlite3
import time

from config import DB_PATH, DEFAULT_SETTINGS

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS log_groups (
    name           TEXT PRIMARY KEY,
    stored_bytes   INTEGER,
    retention_days INTEGER,
    creation_time  INTEGER,
    updated_at     INTEGER
);

CREATE TABLE IF NOT EXISTS issues (
    fingerprint TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    category    TEXT NOT NULL,
    service     TEXT NOT NULL,
    log_group   TEXT NOT NULL,
    first_seen  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL,
    count       INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'open',   -- open | resolved | ignored
    created_at  INTEGER NOT NULL,               -- quando o monitor viu pela 1a vez
    resolved_at INTEGER,
    regressed_at INTEGER
);
CREATE INDEX IF NOT EXISTS ix_issues_last ON issues(last_seen);
CREATE INDEX IF NOT EXISTS ix_issues_created ON issues(created_at);

CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY,
    event_key    TEXT UNIQUE NOT NULL,
    fingerprint  TEXT NOT NULL,
    ts           INTEGER NOT NULL,
    service      TEXT NOT NULL,
    log_group    TEXT NOT NULL,
    log_stream   TEXT NOT NULL,
    category     TEXT NOT NULL,
    message      TEXT NOT NULL,
    collected_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS ix_events_fp_ts ON events(fingerprint, ts);
CREATE INDEX IF NOT EXISTS ix_events_svc_ts ON events(service, ts);

CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY,
    kind         TEXT NOT NULL DEFAULT 'live',   -- live | backfill
    started_at   INTEGER NOT NULL,
    finished_at  INTEGER,
    window_start INTEGER,
    window_end   INTEGER,
    groups       INTEGER,
    rows_found   INTEGER,
    events_new   INTEGER,
    issues_new   INTEGER,
    status       TEXT,       -- running | ok | partial | error
    error        TEXT
);

CREATE TABLE IF NOT EXISTS alarms (
    name             TEXT PRIMARY KEY,
    state            TEXT,
    reason           TEXT,
    namespace        TEXT,
    metric           TEXT,
    state_updated_at INTEGER,
    seen_at          INTEGER
);

-- Alertas na tela do monitor (regras do usuário + disparos)
CREATE TABLE IF NOT EXISTS alert_rules (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL,          -- new_issue | regression | spike | anomaly | match | cw_alarm | stalled
    enabled      INTEGER NOT NULL DEFAULT 0,
    severity     TEXT NOT NULL DEFAULT 'critical',   -- warning | critical
    params       TEXT NOT NULL DEFAULT '{}',         -- JSON com os parâmetros da regra
    cooldown_min INTEGER NOT NULL DEFAULT 30,
    created_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_events (
    id              INTEGER PRIMARY KEY,
    rule_id         INTEGER,
    rule_name       TEXT NOT NULL,
    severity        TEXT NOT NULL,
    title           TEXT NOT NULL,
    detail          TEXT,
    fingerprint     TEXT,                -- issue relacionada (para "Ver issue")
    service         TEXT,
    dedupe_key      TEXT,
    ts              INTEGER NOT NULL,
    acknowledged_at INTEGER
);
CREATE INDEX IF NOT EXISTS ix_alert_events_ts ON alert_events(ts);
CREATE INDEX IF NOT EXISTS ix_alert_events_key ON alert_events(rule_id, dedupe_key, ts);

CREATE TABLE IF NOT EXISTS alarm_history (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    old_state TEXT,
    new_state TEXT,
    reason    TEXT,
    ts        INTEGER NOT NULL
);
"""


def now_ms() -> int:
    return int(time.time() * 1000)


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        if "kind" not in {r[1] for r in conn.execute("PRAGMA table_info(runs)")}:
            conn.execute("ALTER TABLE runs ADD COLUMN kind TEXT NOT NULL DEFAULT 'live'")
        if "creation_time" not in {r[1] for r in conn.execute("PRAGMA table_info(log_groups)")}:
            conn.execute("ALTER TABLE log_groups ADD COLUMN creation_time INTEGER")
            conn.execute("UPDATE log_groups SET updated_at = 0")  # força recarregar com a data de criação
        conn.execute("DELETE FROM settings WHERE key = 'max_gap_hours'")  # substituído por auto_recover_hours
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value))


def get_settings(conn) -> dict:
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}


def set_setting(conn, key: str, value) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def rows(cursor) -> list[dict]:
    return [dict(r) for r in cursor]
