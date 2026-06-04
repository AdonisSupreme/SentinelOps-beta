-- SentinelOps application-wide timezone setting.
-- Timestamps remain stored in UTC/TIMESTAMPTZ; this setting controls operator-facing display.

CREATE TABLE IF NOT EXISTS app_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value JSONB NOT NULL,
    description TEXT,
    updated_by TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO app_settings (setting_key, setting_value, description, updated_by)
VALUES (
    'application_timezone',
    '{"timezone": "Africa/Harare"}'::jsonb,
    'IANA timezone used for operator-facing SentinelOps date/time display.',
    'migration'
)
ON CONFLICT (setting_key) DO NOTHING;

CREATE INDEX IF NOT EXISTS idx_app_settings_updated_at
    ON app_settings (updated_at DESC);
