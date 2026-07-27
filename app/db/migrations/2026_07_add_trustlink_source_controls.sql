-- Runtime source routing for the TrustLink account extraction pipeline.
-- At least one ingest must remain enabled; downstream export format is unchanged.

CREATE TABLE IF NOT EXISTS trustlink_pipeline_config (
    config_key TEXT PRIMARY KEY CHECK (config_key = 'account-extraction'),
    idc_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    digipay_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by TEXT NOT NULL DEFAULT 'system',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT trustlink_pipeline_config_source_check
        CHECK (idc_enabled OR digipay_enabled)
);

INSERT INTO trustlink_pipeline_config (
    config_key,
    idc_enabled,
    digipay_enabled,
    updated_by
)
VALUES ('account-extraction', TRUE, TRUE, 'system')
ON CONFLICT (config_key) DO NOTHING;

CREATE TABLE IF NOT EXISTS trustlink_pipeline_config_audit (
    audit_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    previous_idc_enabled BOOLEAN NOT NULL,
    previous_digipay_enabled BOOLEAN NOT NULL,
    idc_enabled BOOLEAN NOT NULL,
    digipay_enabled BOOLEAN NOT NULL,
    changed_by TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_trustlink_pipeline_config_audit_changed_at
    ON trustlink_pipeline_config_audit (changed_at DESC);

ALTER TABLE trustlink_steps
    DROP CONSTRAINT IF EXISTS trustlink_steps_status_check;

ALTER TABLE trustlink_steps
    ADD CONSTRAINT trustlink_steps_status_check
    CHECK (status IN ('pending', 'running', 'completed', 'skipped', 'failed'));

