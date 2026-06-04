-- Migration: Add Sentinel Nexus environment rollover control plane
-- Date: May 2026
-- Purpose:
--   - Store Oracle environment rollover profiles, encrypted credentials, editable replacement rules,
--     OTP-approved execution evidence, and reminder-only schedules in SentinelOps Postgres.
--   - Keep rollover independent from Nexus autonomous incident intelligence while preserving an easy
--     future bridge into DR and service orchestration workflows.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS nexus_rollover_environment (
    environment_id TEXT PRIMARY KEY,
    environment_name TEXT NOT NULL,
    environment_type TEXT NOT NULL,
    service_environment TEXT,
    enabled BOOLEAN NOT NULL DEFAULT true,
    credential_ciphertext BYTEA,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ,
    CONSTRAINT chk_nexus_rollover_environment_type
        CHECK (environment_type IN ('uat', 'dr', 'sandbox', 'test', 'production_clone', 'other'))
);

CREATE TABLE IF NOT EXISTS nexus_rollover_execution (
    execution_id TEXT PRIMARY KEY,
    environment_id TEXT NOT NULL REFERENCES nexus_rollover_environment(environment_id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT chk_nexus_rollover_execution_status
        CHECK (status IN ('PENDING', 'APPROVED', 'COMPLETED', 'BLOCKED', 'FAILED', 'NOOP'))
);

CREATE TABLE IF NOT EXISTS nexus_rollover_reminder (
    reminder_id TEXT PRIMARY KEY,
    environment_id TEXT NOT NULL REFERENCES nexus_rollover_environment(environment_id) ON DELETE CASCADE,
    scheduled_for TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'scheduled',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ,
    CONSTRAINT chk_nexus_rollover_reminder_status
        CHECK (status IN ('scheduled', 'cancelled', 'notified'))
);

CREATE INDEX IF NOT EXISTS idx_nexus_rollover_environment_type
    ON nexus_rollover_environment (environment_type)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_nexus_rollover_environment_service_env
    ON nexus_rollover_environment (service_environment)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_nexus_rollover_environment_payload_gin
    ON nexus_rollover_environment USING GIN (payload)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_nexus_rollover_execution_env_time
    ON nexus_rollover_execution (environment_id, requested_at DESC);

CREATE INDEX IF NOT EXISTS idx_nexus_rollover_execution_status_time
    ON nexus_rollover_execution (status, requested_at DESC);

CREATE INDEX IF NOT EXISTS idx_nexus_rollover_execution_payload_gin
    ON nexus_rollover_execution USING GIN (payload);

CREATE INDEX IF NOT EXISTS idx_nexus_rollover_reminder_due
    ON nexus_rollover_reminder (status, scheduled_for ASC)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_nexus_rollover_reminder_env
    ON nexus_rollover_reminder (environment_id, scheduled_for ASC)
    WHERE deleted_at IS NULL;

COMMENT ON TABLE nexus_rollover_environment IS
    'Nexus-managed environment rollover profiles. Credentials are encrypted with pgcrypto and never returned to clients.';

COMMENT ON TABLE nexus_rollover_execution IS
    'Audited OTP-approved rollover attempts and their pre/post assessments.';

COMMENT ON TABLE nexus_rollover_reminder IS
    'Reminder-only rollover schedules. Nexus must not autonomously execute rollover from this table.';
