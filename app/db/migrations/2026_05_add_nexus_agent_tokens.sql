-- Migration: Add Sentinel Nexus DB-backed light-agent tokens
-- Date: May 2026
-- Purpose:
--   - Let SentinelOps administrators generate and rotate Nexus light-agent credentials from Nexus.
--   - Store only salted token hashes in Postgres, never plaintext agent tokens.
--   - Allow sentinelops-ai to honor the latest active token without service restart.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS nexus_agent_token (
    token_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash TEXT NOT NULL,
    token_salt TEXT NOT NULL,
    token_ciphertext BYTEA NOT NULL,
    token_prefix TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at TIMESTAMPTZ,
    revoked_by TEXT,
    last_used_at TIMESTAMPTZ,
    usage_count INTEGER NOT NULL DEFAULT 0,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT chk_nexus_agent_token_status
        CHECK (status IN ('active', 'revoked'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_nexus_agent_token_one_active
    ON nexus_agent_token ((status))
    WHERE status = 'active' AND revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_nexus_agent_token_created_at
    ON nexus_agent_token (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_nexus_agent_token_last_used
    ON nexus_agent_token (last_used_at DESC)
    WHERE last_used_at IS NOT NULL;
