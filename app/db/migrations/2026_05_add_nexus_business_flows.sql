-- Sentinel Nexus production intelligence upgrade:
-- business-flow-aware dependency semantics, signal vantage points, and health snapshots.

CREATE TABLE IF NOT EXISTS business_flow (
    flow_id TEXT PRIMARY KEY,
    flow_name TEXT NOT NULL,
    environment TEXT NOT NULL,
    criticality TEXT NOT NULL DEFAULT 'high',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_business_flow_environment
    ON business_flow (environment);

CREATE INDEX IF NOT EXISTS idx_business_flow_criticality
    ON business_flow (criticality);

CREATE INDEX IF NOT EXISTS idx_business_flow_payload_gin
    ON business_flow USING GIN (payload);

CREATE TABLE IF NOT EXISTS business_flow_step (
    step_id TEXT PRIMARY KEY,
    flow_id TEXT NOT NULL REFERENCES business_flow(flow_id) ON DELETE CASCADE,
    service_id TEXT NOT NULL REFERENCES service_catalog(service_id) ON DELETE CASCADE,
    step_order INTEGER NOT NULL,
    service_role TEXT NOT NULL,
    required BOOLEAN NOT NULL DEFAULT TRUE,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_business_flow_step_flow
    ON business_flow_step (flow_id, step_order);

CREATE INDEX IF NOT EXISTS idx_business_flow_step_service
    ON business_flow_step (service_id);

CREATE INDEX IF NOT EXISTS idx_business_flow_step_payload_gin
    ON business_flow_step USING GIN (payload);

CREATE TABLE IF NOT EXISTS service_health_snapshot (
    snapshot_id TEXT PRIMARY KEY,
    service_id TEXT NOT NULL REFERENCES service_catalog(service_id) ON DELETE CASCADE,
    environment TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    vantage_point TEXT NOT NULL,
    observation_layer TEXT NOT NULL,
    health_status TEXT NOT NULL,
    failure_domain_hint TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_service_health_snapshot_service_time
    ON service_health_snapshot (service_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_service_health_snapshot_domain_time
    ON service_health_snapshot (failure_domain_hint, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_service_health_snapshot_payload_gin
    ON service_health_snapshot USING GIN (payload);

ALTER TABLE dependency_edge
    ADD COLUMN IF NOT EXISTS dependency_purpose TEXT,
    ADD COLUMN IF NOT EXISTS dependency_scope TEXT NOT NULL DEFAULT 'global',
    ADD COLUMN IF NOT EXISTS business_flow_ids TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    ADD COLUMN IF NOT EXISTS valid_failure_domains TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    ADD COLUMN IF NOT EXISTS expected_evidence TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[];

CREATE INDEX IF NOT EXISTS idx_dependency_edge_scope
    ON dependency_edge (dependency_scope);

CREATE INDEX IF NOT EXISTS idx_dependency_edge_business_flows
    ON dependency_edge USING GIN (business_flow_ids);

CREATE INDEX IF NOT EXISTS idx_dependency_edge_failure_domains
    ON dependency_edge USING GIN (valid_failure_domains);

ALTER TABLE signal_event
    ADD COLUMN IF NOT EXISTS vantage_point TEXT,
    ADD COLUMN IF NOT EXISTS observation_layer TEXT,
    ADD COLUMN IF NOT EXISTS failure_domain_hint TEXT,
    ADD COLUMN IF NOT EXISTS business_flow_id TEXT REFERENCES business_flow(flow_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_signal_event_vantage_time
    ON signal_event (vantage_point, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_signal_event_failure_domain_time
    ON signal_event (failure_domain_hint, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_signal_event_business_flow_time
    ON signal_event (business_flow_id, timestamp DESC);

ALTER TABLE incident
    ADD COLUMN IF NOT EXISTS primary_business_flow_id TEXT REFERENCES business_flow(flow_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS failure_domain TEXT;

CREATE INDEX IF NOT EXISTS idx_incident_business_flow
    ON incident (primary_business_flow_id);

CREATE INDEX IF NOT EXISTS idx_incident_failure_domain
    ON incident (failure_domain);
