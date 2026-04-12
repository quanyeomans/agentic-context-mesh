CREATE TABLE schema_version (
  version     INTEGER PRIMARY KEY,
  applied_at  TEXT NOT NULL
);

CREATE TABLE entities (
  id            TEXT PRIMARY KEY,
  type          TEXT NOT NULL CHECK (type IN ('person','organisation','decision','concept','project')),
  name          TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  markdown_path TEXT NOT NULL,
  summary       TEXT,
  agent_scope   TEXT NOT NULL DEFAULT 'shared',
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE relationships (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  from_entity   TEXT NOT NULL REFERENCES entities(id),
  to_entity     TEXT NOT NULL REFERENCES entities(id),
  rel_type      TEXT NOT NULL CHECK (rel_type IN ('reports_to','member_of','decided_by','client_of','relates_to')),
  label         TEXT,
  confidence    REAL NOT NULL DEFAULT 1.0 CHECK (confidence BETWEEN 0 AND 1),
  source_ref    TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE entity_mentions (
  entity_id     TEXT NOT NULL REFERENCES entities(id),
  doc_uri       TEXT NOT NULL,
  mention_count INTEGER NOT NULL DEFAULT 1,
  first_seen    TEXT NOT NULL,
  last_seen     TEXT NOT NULL,
  PRIMARY KEY (entity_id, doc_uri)
);

CREATE TABLE entity_facts (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_id     TEXT NOT NULL REFERENCES entities(id),
  fact_type     TEXT NOT NULL CHECK (fact_type IN ('role','decision','relationship','context','other')),
  fact_text     TEXT NOT NULL,
  valid_from    TEXT,
  valid_until   TEXT,
  source_ref    TEXT,
  created_at    TEXT NOT NULL
);

CREATE INDEX idx_entities_type   ON entities(type);
CREATE INDEX idx_entities_name   ON entities(name);
CREATE INDEX idx_rel_from        ON relationships(from_entity);
CREATE INDEX idx_rel_to          ON relationships(to_entity);
CREATE INDEX idx_facts_entity    ON entity_facts(entity_id);
CREATE INDEX idx_facts_from      ON entity_facts(valid_from);
CREATE INDEX idx_mentions_doc    ON entity_mentions(doc_uri);

INSERT INTO schema_version (version, applied_at) VALUES (1, datetime('now'));
