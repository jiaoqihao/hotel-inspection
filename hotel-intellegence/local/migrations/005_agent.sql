BEGIN IMMEDIATE;
CREATE TABLE agent_threads(id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL, updated REAL NOT NULL);
CREATE INDEX agent_owner ON agent_threads(owner,updated);
CREATE TABLE agent_messages(id TEXT PRIMARY KEY, thread TEXT NOT NULL REFERENCES agent_threads(id),
    role TEXT NOT NULL, text TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '[]', state TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX agent_timeline ON agent_messages(thread,created);
CREATE TABLE agent_calls(id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_key TEXT NOT NULL, name TEXT NOT NULL,
    arguments TEXT NOT NULL, state TEXT NOT NULL, result TEXT, created REAL NOT NULL, UNIQUE(owner,request_key));
CREATE TABLE agent_assets(id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX agent_asset_owner ON agent_assets(owner,kind);
PRAGMA user_version=5;
COMMIT;
