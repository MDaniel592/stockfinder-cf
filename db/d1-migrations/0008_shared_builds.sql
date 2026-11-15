-- Configuraciones del configurador compartidas por enlace (/configurador?lista=<uuid>).
-- Solo guarda nombre y slug por hueco; los precios se leen siempre en vivo. ip_hash: límite por hora, nunca se publica.
CREATE TABLE shared_builds (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  parts TEXT NOT NULL,
  created_at TEXT NOT NULL,
  ip_hash TEXT NOT NULL
);
CREATE INDEX shared_builds_ip ON shared_builds(ip_hash, created_at);
