-- Migration: Add Sentinel Nexus managed SOP registry
-- Date: May 2026
-- Purpose:
--   - Give Nexus an operator-governed SOP control plane instead of relying only on file-ingested SOPs.
--   - Store SOP validation state, approval status, service bindings, and editable procedure sections in SentinelOps Postgres.
--   - Allow Nexus Copilot to merge approved database-managed SOPs into the runtime RAG index.

CREATE TABLE IF NOT EXISTS nexus_sop_registry (
    sop_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    class_code TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT chk_nexus_sop_registry_class
        CHECK (class_code IN ('A', 'B', 'C', 'D', 'E', 'F')),
    CONSTRAINT chk_nexus_sop_registry_severity
        CHECK (severity IN ('critical', 'high', 'medium', 'low', 'info')),
    CONSTRAINT chk_nexus_sop_registry_status
        CHECK (status IN ('draft', 'needs_review', 'approved', 'deprecated'))
);

CREATE INDEX IF NOT EXISTS idx_nexus_sop_registry_status_updated
    ON nexus_sop_registry (status, updated_at DESC)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_nexus_sop_registry_class
    ON nexus_sop_registry (class_code)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_nexus_sop_registry_payload_gin
    ON nexus_sop_registry USING GIN (payload);

CREATE INDEX IF NOT EXISTS idx_nexus_sop_registry_services_gin
    ON nexus_sop_registry USING GIN ((payload -> 'services'))
    WHERE deleted_at IS NULL;
