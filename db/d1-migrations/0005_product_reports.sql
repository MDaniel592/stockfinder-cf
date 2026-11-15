CREATE TABLE product_reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_slug TEXT NOT NULL,
  offer_id TEXT,
  note TEXT NOT NULL,
  page_url TEXT,
  status TEXT NOT NULL DEFAULT 'new',
  created_at TEXT NOT NULL,
  ip_hash TEXT NOT NULL
);
CREATE INDEX product_reports_status ON product_reports(status, id);
CREATE INDEX product_reports_ip ON product_reports(ip_hash, created_at);
