BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS badge_wearers (
    device INTEGER NOT NULL, since TEXT NOT NULL, employee TEXT NOT NULL,
    PRIMARY KEY(device, since)
);
CREATE TABLE IF NOT EXISTS badge_script_versions (
    version INTEGER PRIMARY KEY, rule TEXT NOT NULL, since TEXT NOT NULL, definition TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS badge_script_effective ON badge_script_versions(rule,since,version);
CREATE TABLE IF NOT EXISTS badge_wecom_config (
    id INTEGER PRIMARY KEY CHECK (id=1), secret BLOB, clock TEXT NOT NULL DEFAULT '09:00',
    enabled INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO badge_wecom_config(id) VALUES(1);
CREATE TABLE IF NOT EXISTS badge_wecom_runs (
    id INTEGER PRIMARY KEY, day TEXT NOT NULL, revision INTEGER NOT NULL, snapshot TEXT NOT NULL,
    parts TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0, next_try REAL NOT NULL DEFAULT 0, info TEXT NOT NULL DEFAULT '',
    updated TEXT NOT NULL, UNIQUE(day,revision)
);
PRAGMA user_version=7;
COMMIT;
