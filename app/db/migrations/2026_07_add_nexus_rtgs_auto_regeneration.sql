-- Admin-controlled RTGS auto-regeneration policy and immutable toggle audit.

CREATE TABLE IF NOT EXISTS nexus_rtgs_auto_policy (
    policy_key TEXT PRIMARY KEY CHECK (policy_key = 'latest-five-day-window'),
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    window_days INTEGER NOT NULL DEFAULT 5 CHECK (window_days = 5),
    updated_by TEXT NOT NULL DEFAULT 'system',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_run_at TIMESTAMPTZ NULL,
    last_attempted_count INTEGER NOT NULL DEFAULT 0,
    last_completed_count INTEGER NOT NULL DEFAULT 0,
    last_run_status TEXT NOT NULL DEFAULT 'NEVER'
        CHECK (last_run_status IN ('NEVER', 'COMPLETED', 'PARTIAL', 'FAILED'))
);

INSERT INTO nexus_rtgs_auto_policy (policy_key, enabled, window_days, updated_by)
VALUES ('latest-five-day-window', FALSE, 5, 'system')
ON CONFLICT (policy_key) DO NOTHING;

CREATE TABLE IF NOT EXISTS nexus_rtgs_auto_policy_audit (
    audit_id TEXT PRIMARY KEY,
    previous_enabled BOOLEAN NOT NULL,
    enabled BOOLEAN NOT NULL,
    changed_by TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    window_days INTEGER NOT NULL DEFAULT 5 CHECK (window_days = 5)
);

CREATE INDEX IF NOT EXISTS idx_nexus_rtgs_auto_policy_audit_changed_at
    ON nexus_rtgs_auto_policy_audit (changed_at DESC);

CREATE INDEX IF NOT EXISTS idx_nexus_rtgs_action_mode_lookup
    ON nexus_rtgs_action (transaction_id, status, created_at DESC)
    WHERE action = 'regenerate';
