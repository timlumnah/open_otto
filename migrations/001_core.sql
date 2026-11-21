-- open_otto migration 001: core schema
-- Target: SQLite 3.35+
-- Timestamps are UTC ISO-8601 text. Booleans are 0/1 integers.

PRAGMA foreign_keys = ON;

CREATE TABLE schema_migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Pipeline stages are per pack. A pack is a use case: gigs, jobs, freelance.
CREATE TABLE pipeline_stages (
    id          INTEGER PRIMARY KEY,
    pack        TEXT    NOT NULL,
    key         TEXT    NOT NULL,
    label       TEXT    NOT NULL,
    position    INTEGER NOT NULL,
    is_terminal INTEGER NOT NULL DEFAULT 0 CHECK (is_terminal IN (0, 1)),
    UNIQUE (pack, key),
    UNIQUE (pack, position)
);

-- An org is the thing you are pursuing: a venue, an employer, a client.
CREATE TABLE orgs (
    id               INTEGER PRIMARY KEY,
    pack             TEXT    NOT NULL,
    name             TEXT    NOT NULL,
    kind             TEXT,
    website          TEXT,
    city             TEXT,
    region           TEXT,
    source           TEXT,
    notes            TEXT,
    stage_id         INTEGER NOT NULL REFERENCES pipeline_stages (id),
    stage_changed_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    created_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (pack, name, city)
);

CREATE INDEX idx_orgs_pack_stage ON orgs (pack, stage_id);

CREATE TABLE contacts (
    id         INTEGER PRIMARY KEY,
    org_id     INTEGER NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    name       TEXT    NOT NULL,
    role       TEXT,
    email      TEXT,
    phone      TEXT,
    notes      TEXT,
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_contacts_org ON contacts (org_id);
-- At most one primary contact per org.
CREATE UNIQUE INDEX idx_contacts_one_primary ON contacts (org_id) WHERE is_primary = 1;

-- Drafts are agent- or user-written messages awaiting a human decision.
CREATE TABLE drafts (
    id           INTEGER PRIMARY KEY,
    org_id       INTEGER NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    contact_id   INTEGER REFERENCES contacts (id) ON DELETE SET NULL,
    channel      TEXT    NOT NULL CHECK (channel IN ('email', 'phone', 'text', 'social', 'in_person', 'other')),
    subject      TEXT,
    body         TEXT    NOT NULL,
    final_body   TEXT,
    status       TEXT    NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending', 'approved', 'rejected', 'sent')),
    created_by   TEXT    NOT NULL CHECK (created_by IN ('user', 'agent')),
    model        TEXT,
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    decided_at   TEXT,
    sent_at      TEXT,
    -- Nothing reaches 'sent' without passing through a recorded decision.
    CHECK (status NOT IN ('approved', 'sent') OR decided_at IS NOT NULL),
    CHECK (status <> 'sent' OR sent_at IS NOT NULL)
);

CREATE INDEX idx_drafts_status ON drafts (status);
CREATE INDEX idx_drafts_org ON drafts (org_id);

-- Interactions are things that actually happened.
CREATE TABLE interactions (
    id          INTEGER PRIMARY KEY,
    org_id      INTEGER NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    contact_id  INTEGER REFERENCES contacts (id) ON DELETE SET NULL,
    draft_id    INTEGER REFERENCES drafts (id) ON DELETE SET NULL,
    channel     TEXT    NOT NULL CHECK (channel IN ('email', 'phone', 'text', 'social', 'in_person', 'other')),
    direction   TEXT    NOT NULL CHECK (direction IN ('outbound', 'inbound')),
    summary     TEXT    NOT NULL,
    occurred_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_interactions_org_time ON interactions (org_id, occurred_at DESC);

CREATE TABLE tasks (
    id           INTEGER PRIMARY KEY,
    org_id       INTEGER REFERENCES orgs (id) ON DELETE CASCADE,
    contact_id   INTEGER REFERENCES contacts (id) ON DELETE SET NULL,
    title        TEXT    NOT NULL,
    due_at       TEXT,
    status       TEXT    NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done', 'dropped')),
    created_by   TEXT    NOT NULL DEFAULT 'user' CHECK (created_by IN ('user', 'agent', 'scheduler')),
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    completed_at TEXT,
    CHECK (status = 'open' OR completed_at IS NOT NULL)
);

CREATE INDEX idx_tasks_open_due ON tasks (due_at) WHERE status = 'open';

-- Append-only record of every meaningful action, by anyone or anything.
CREATE TABLE audit_log (
    id          INTEGER PRIMARY KEY,
    at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id   INTEGER,
    detail      TEXT CHECK (detail IS NULL OR json_valid(detail))
);

CREATE INDEX idx_audit_entity ON audit_log (entity_type, entity_id);

CREATE TRIGGER trg_audit_no_update BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;

CREATE TRIGGER trg_audit_no_delete BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;

-- Keep updated_at honest.
CREATE TRIGGER trg_orgs_touch AFTER UPDATE ON orgs
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE orgs SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = NEW.id;
END;

-- Stage changes stamp the org and write an audit row.
CREATE TRIGGER trg_orgs_stage_change AFTER UPDATE OF stage_id ON orgs
WHEN NEW.stage_id <> OLD.stage_id
BEGIN
    UPDATE orgs SET stage_changed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = NEW.id;
    INSERT INTO audit_log (actor, action, entity_type, entity_id, detail)
    VALUES ('system', 'stage_changed', 'org', NEW.id,
            json_object('from', OLD.stage_id, 'to', NEW.stage_id));
END;

-- Draft decisions and sends are audited automatically.
CREATE TRIGGER trg_drafts_status_change AFTER UPDATE OF status ON drafts
WHEN NEW.status <> OLD.status
BEGIN
    INSERT INTO audit_log (actor, action, entity_type, entity_id, detail)
    VALUES ('system', 'draft_' || NEW.status, 'draft', NEW.id,
            json_object('from', OLD.status, 'to', NEW.status));
END;

-- A sent draft is final. Its text may not be rewritten afterward.
CREATE TRIGGER trg_drafts_sent_immutable BEFORE UPDATE OF body, final_body ON drafts
WHEN OLD.status = 'sent'
BEGIN
    SELECT RAISE(ABORT, 'sent drafts are immutable');
END;

-- Last real contact per org, and days of silence since.
CREATE VIEW v_org_activity AS
SELECT
    o.id AS org_id,
    o.pack,
    o.name,
    s.key AS stage_key,
    s.is_terminal,
    MAX(i.occurred_at) AS last_interaction_at,
    MAX(CASE WHEN i.direction = 'outbound' THEN i.occurred_at END) AS last_outbound_at,
    MAX(CASE WHEN i.direction = 'inbound'  THEN i.occurred_at END) AS last_inbound_at,
    CAST(julianday('now') - julianday(COALESCE(MAX(i.occurred_at), o.created_at)) AS INTEGER) AS days_silent
FROM orgs o
JOIN pipeline_stages s ON s.id = o.stage_id
LEFT JOIN interactions i ON i.org_id = o.id
GROUP BY o.id;

-- Candidates for a follow-up: active orgs where we spoke last and the silence is 7+ days,
-- with no open task and no pending draft already covering it.
CREATE VIEW v_followup_candidates AS
SELECT a.*
FROM v_org_activity a
WHERE a.is_terminal = 0
  AND a.days_silent >= 7
  AND (a.last_inbound_at IS NULL OR a.last_outbound_at > a.last_inbound_at)
  AND a.last_outbound_at IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.org_id = a.org_id AND t.status = 'open')
  AND NOT EXISTS (SELECT 1 FROM drafts d WHERE d.org_id = a.org_id AND d.status = 'pending');

INSERT INTO schema_migrations (version, name) VALUES (1, 'core');
