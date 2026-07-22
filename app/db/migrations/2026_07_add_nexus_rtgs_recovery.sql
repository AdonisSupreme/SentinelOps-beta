-- Sentinel Nexus RTGS database assessment and regeneration ledger.
-- Assessment is read-only; regeneration is the only production-changing action.

CREATE TABLE IF NOT EXISTS nexus_rtgs_assessment (
    assessment_id TEXT PRIMARY KEY,
    trigger TEXT NOT NULL CHECK (trigger IN ('scheduled', 'manual')),
    assessed_at TIMESTAMPTZ NOT NULL,
    transaction_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('COMPLETED', 'PARTIAL', 'FAILED')),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_nexus_rtgs_assessment_assessed_at
    ON nexus_rtgs_assessment (assessed_at DESC);

CREATE TABLE IF NOT EXISTS nexus_rtgs_case (
    assessment_id TEXT NOT NULL REFERENCES nexus_rtgs_assessment(assessment_id) ON DELETE CASCADE,
    transaction_id TEXT NOT NULL,
    assessed_at TIMESTAMPTZ NOT NULL,
    entry_date TIMESTAMPTZ NOT NULL,
    age_lane TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (assessment_id, transaction_id)
);

CREATE INDEX IF NOT EXISTS idx_nexus_rtgs_case_transaction
    ON nexus_rtgs_case (transaction_id, assessed_at DESC);

CREATE TABLE IF NOT EXISTS nexus_rtgs_schedule (
    schedule_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    interval_minutes INTEGER NOT NULL DEFAULT 30 CHECK (interval_minutes IN (30, 60)),
    local_time TEXT NOT NULL,
    timezone TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    last_triggered_at TIMESTAMPTZ NULL,
    created_by TEXT NOT NULL DEFAULT 'system',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE nexus_rtgs_schedule
    ADD COLUMN IF NOT EXISTS interval_minutes INTEGER NOT NULL DEFAULT 30;

UPDATE nexus_rtgs_schedule
SET interval_minutes = 30
WHERE interval_minutes IS NULL OR interval_minutes NOT IN (30, 60);

ALTER TABLE nexus_rtgs_schedule
    DROP CONSTRAINT IF EXISTS nexus_rtgs_schedule_interval_minutes_check;

ALTER TABLE nexus_rtgs_schedule
    ADD CONSTRAINT nexus_rtgs_schedule_interval_minutes_check CHECK (interval_minutes IN (30, 60));

CREATE TABLE IF NOT EXISTS nexus_rtgs_action (
    action_id TEXT PRIMARY KEY,
    idempotency_key TEXT UNIQUE NULL,
    transaction_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action = 'regenerate'),
    status TEXT NOT NULL CHECK (status IN ('REQUESTED', 'COMPLETED', 'BLOCKED', 'FAILED', 'NOOP')),
    requested_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_nexus_rtgs_action_transaction
    ON nexus_rtgs_action (transaction_id, created_at DESC);
