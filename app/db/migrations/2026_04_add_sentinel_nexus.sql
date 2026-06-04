-- Migration: Add Sentinel Nexus
-- Date: April 2026
-- Purpose:
--   - Store Sentinel Nexus service catalog, dependency graph, signals, incidents, and actions in SentinelOps Postgres.
--   - Keep Nexus aligned with the existing SentinelOps database/auth/session model.
--   - Replace runtime table creation and local JSON state with explicit database schema.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- =====================================================
-- 1) NEXUS METADATA
-- =====================================================

CREATE TABLE IF NOT EXISTS nexus_meta (
    meta_key TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =====================================================
-- 2) SERVICE CATALOG AND DEPENDENCY GRAPH
-- =====================================================

CREATE TABLE IF NOT EXISTS service_catalog (
    service_id TEXT PRIMARY KEY,
    service_name TEXT NOT NULL,
    environment TEXT NOT NULL,
    service_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dependency_cluster (
    cluster_id TEXT PRIMARY KEY,
    cluster_name TEXT NOT NULL,
    environment TEXT NOT NULL,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dependency_edge (
    edge_id TEXT PRIMARY KEY,
    cluster_id TEXT NULL REFERENCES dependency_cluster(cluster_id) ON DELETE SET NULL,
    from_service_id TEXT NOT NULL REFERENCES service_catalog(service_id) ON DELETE CASCADE,
    to_service_id TEXT NOT NULL REFERENCES service_catalog(service_id) ON DELETE CASCADE,
    dependency_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =====================================================
-- 3) OSEMN EVIDENCE LAYERS
-- =====================================================

CREATE TABLE IF NOT EXISTS signal_event (
    signal_id TEXT PRIMARY KEY,
    service_id TEXT NOT NULL REFERENCES service_catalog(service_id) ON DELETE CASCADE,
    signal_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS change_event (
    change_id TEXT PRIMARY KEY,
    service_id TEXT NOT NULL REFERENCES service_catalog(service_id) ON DELETE CASCADE,
    change_type TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_heartbeat (
    heartbeat_key TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    service_id TEXT NOT NULL REFERENCES service_catalog(service_id) ON DELETE CASCADE,
    timestamp TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);

-- =====================================================
-- 4) INCIDENT INTELLIGENCE
-- =====================================================

CREATE TABLE IF NOT EXISTS incident (
    incident_id UUID PRIMARY KEY,
    incident_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    start_time TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS incident_service (
    incident_service_key TEXT PRIMARY KEY,
    incident_id UUID NOT NULL REFERENCES incident(incident_id) ON DELETE CASCADE,
    service_id TEXT NOT NULL REFERENCES service_catalog(service_id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS task_handoff (
    task_id TEXT PRIMARY KEY,
    incident_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS operator_feedback (
    feedback_id TEXT PRIMARY KEY,
    incident_id UUID NOT NULL,
    feedback_type TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS diagnostic_bundle (
    bundle_id TEXT PRIMARY KEY,
    incident_id UUID NOT NULL,
    service_id TEXT NOT NULL REFERENCES service_catalog(service_id) ON DELETE CASCADE,
    requested_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS action_execution (
    action_execution_id TEXT PRIMARY KEY,
    incident_id UUID NOT NULL,
    service_id TEXT NOT NULL REFERENCES service_catalog(service_id) ON DELETE CASCADE,
    action_type TEXT NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);

-- =====================================================
-- 5) INDEXES
-- =====================================================

CREATE INDEX IF NOT EXISTS idx_service_catalog_environment ON service_catalog(environment);
CREATE INDEX IF NOT EXISTS idx_service_catalog_service_type ON service_catalog(service_type);
CREATE INDEX IF NOT EXISTS idx_service_catalog_payload_gin ON service_catalog USING GIN(payload);

CREATE INDEX IF NOT EXISTS idx_dependency_cluster_environment ON dependency_cluster(environment);
CREATE INDEX IF NOT EXISTS idx_dependency_edge_cluster ON dependency_edge(cluster_id);
CREATE INDEX IF NOT EXISTS idx_dependency_edge_from ON dependency_edge(from_service_id);
CREATE INDEX IF NOT EXISTS idx_dependency_edge_to ON dependency_edge(to_service_id);

CREATE INDEX IF NOT EXISTS idx_signal_event_service_time ON signal_event(service_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_signal_event_time ON signal_event(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_signal_event_type_severity ON signal_event(signal_type, severity, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_signal_event_payload_gin ON signal_event USING GIN(payload);

CREATE INDEX IF NOT EXISTS idx_change_event_service_time ON change_event(service_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_change_event_type_time ON change_event(change_type, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_incident_start_time ON incident(start_time DESC);
CREATE INDEX IF NOT EXISTS idx_incident_status_time ON incident(status, start_time DESC);
CREATE INDEX IF NOT EXISTS idx_incident_payload_gin ON incident USING GIN(payload);
CREATE INDEX IF NOT EXISTS idx_incident_service_incident ON incident_service(incident_id);
CREATE INDEX IF NOT EXISTS idx_incident_service_service ON incident_service(service_id);

CREATE INDEX IF NOT EXISTS idx_task_handoff_incident_time ON task_handoff(incident_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_operator_feedback_incident_time ON operator_feedback(incident_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_diagnostic_bundle_incident_time ON diagnostic_bundle(incident_id, requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_action_execution_incident_time ON action_execution(incident_id, requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_action_execution_service_time ON action_execution(service_id, requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_heartbeat_service_time ON agent_heartbeat(service_id, timestamp DESC);
