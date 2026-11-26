BEGIN IMMEDIATE;
ALTER TABLE calls ADD COLUMN revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE calls ADD COLUMN category TEXT NOT NULL DEFAULT '';
ALTER TABLE calls ADD COLUMN summary TEXT NOT NULL DEFAULT '';
ALTER TABLE calls ADD COLUMN review_state TEXT NOT NULL DEFAULT '待复核';
ALTER TABLE calls ADD COLUMN meta TEXT NOT NULL DEFAULT '{}';
CREATE TABLE phone_analyses(
 call_id TEXT NOT NULL REFERENCES calls(id), version INTEGER NOT NULL, at REAL NOT NULL,
 segments TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(call_id,version)
);
CREATE TABLE phone_alerts(
 call_id TEXT PRIMARY KEY REFERENCES calls(id), level TEXT NOT NULL, state TEXT NOT NULL,
 created REAL NOT NULL, first_read REAL, reader TEXT NOT NULL DEFAULT ''
);
CREATE TABLE phone_reviews(
 id INTEGER PRIMARY KEY, call_id TEXT NOT NULL REFERENCES calls(id), actor TEXT NOT NULL,
 action TEXT NOT NULL, note TEXT NOT NULL, changes TEXT NOT NULL, at REAL NOT NULL
);
CREATE TABLE phone_favorites(actor TEXT NOT NULL,call_id TEXT NOT NULL REFERENCES calls(id),PRIMARY KEY(actor,call_id));
CREATE TABLE phone_order_links(item INTEGER PRIMARY KEY REFERENCES items(id),order_id INTEGER NOT NULL REFERENCES orders(id),at REAL NOT NULL,actor TEXT NOT NULL);
CREATE TABLE phone_exports(actor TEXT NOT NULL,at REAL NOT NULL,scope TEXT NOT NULL);
CREATE INDEX calls_period ON calls(test,started);
CREATE INDEX calls_category ON calls(category,review_state);
CREATE INDEX phone_items_call ON items(call_id);
CREATE INDEX phone_orders_history ON history(order_id,at);
PRAGMA user_version=2;
COMMIT;
