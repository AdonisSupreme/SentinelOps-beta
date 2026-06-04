-- Sentinel Nexus service-control hardening.
-- Allows audited service-level diagnostics/control actions that are not tied to an incident.

ALTER TABLE diagnostic_bundle
    ALTER COLUMN incident_id DROP NOT NULL;

ALTER TABLE action_execution
    ALTER COLUMN incident_id DROP NOT NULL;

CREATE INDEX IF NOT EXISTS idx_action_execution_type_time
    ON action_execution(action_type, requested_at DESC);

COMMENT ON COLUMN diagnostic_bundle.incident_id IS
    'Nullable because Nexus can dispatch diagnostics directly from the service command center before an incident exists.';

COMMENT ON COLUMN action_execution.incident_id IS
    'Nullable because Nexus can execute planned START/STOP/RESTART actions for restart-ready services outside an incident.';
