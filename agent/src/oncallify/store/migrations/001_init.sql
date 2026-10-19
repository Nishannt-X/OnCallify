-- OnCallify platform schema. Plain SQL so every query is explainable.
-- JSONB holds records whose shape belongs to the application (evidence, diagnosis);
-- everything that is queried or constrained is a real column.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS incidents (
    id           TEXT PRIMARY KEY,
    service      TEXT NOT NULL,
    status       TEXT NOT NULL,
    title        TEXT NOT NULL,
    opened_at    TIMESTAMPTZ NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL,
    closed_at    TIMESTAMPTZ,
    primary_rule TEXT NOT NULL,
    variant      TEXT,
    proposal_id  TEXT,
    body         JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS incidents_open_by_service ON incidents (service, opened_at DESC) WHERE closed_at IS NULL;
CREATE INDEX IF NOT EXISTS incidents_opened ON incidents (opened_at DESC);

CREATE TABLE IF NOT EXISTS proposals (
    id          TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incidents(id),
    action      TEXT NOT NULL,
    params      JSONB NOT NULL,
    params_hash TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('open','approved','rejected','withdrawn','expired','executed')),
    created_at  TIMESTAMPTZ NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    body        JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS proposals_incident ON proposals (incident_id);

CREATE TABLE IF NOT EXISTS approvals (
    id              TEXT PRIMARY KEY,
    proposal_id     TEXT NOT NULL REFERENCES proposals(id),
    incident_id     TEXT NOT NULL REFERENCES incidents(id),
    action          TEXT NOT NULL,
    params          JSONB NOT NULL,
    params_hash     TEXT NOT NULL,
    approver        TEXT NOT NULL,
    source          TEXT NOT NULL CHECK (source IN ('human','contract')),
    confirm_text    TEXT NOT NULL,
    evidence_digest TEXT NOT NULL,
    contract_id     TEXT,
    granted_at      TIMESTAMPTZ NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,
    used_at         TIMESTAMPTZ,
    executed_at     TIMESTAMPTZ,
    execute_result  JSONB
);
CREATE INDEX IF NOT EXISTS approvals_incident ON approvals (incident_id);
-- at most one approval may ever be consumed per proposal
CREATE UNIQUE INDEX IF NOT EXISTS approvals_one_use_per_proposal ON approvals (proposal_id) WHERE used_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS contracts (
    id              TEXT PRIMARY KEY,
    rule            TEXT NOT NULL,
    cause           TEXT NOT NULL,
    action          TEXT NOT NULL,
    params          JSONB NOT NULL,
    params_hash     TEXT NOT NULL,
    max_uses        INT  NOT NULL CHECK (max_uses > 0),
    uses            INT  NOT NULL DEFAULT 0 CHECK (uses >= 0),
    min_confidence  DOUBLE PRECISION NOT NULL,
    granted_by      TEXT NOT NULL,
    granted_at      TIMESTAMPTZ NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('active','revoked')),
    source_incident TEXT,
    CHECK (uses <= max_uses)
);

CREATE TABLE IF NOT EXISTS audit (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor       TEXT NOT NULL,
    kind        TEXT NOT NULL,
    incident_id TEXT,
    detail      JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_ts ON audit (ts DESC);

CREATE TABLE IF NOT EXISTS experiment_reports (
    id         BIGSERIAL PRIMARY KEY,
    name       TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    report     JSONB NOT NULL
);
