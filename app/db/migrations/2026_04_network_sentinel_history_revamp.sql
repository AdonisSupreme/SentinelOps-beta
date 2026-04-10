-- Migration: Network Sentinel history + diagnostics revamp
-- Date: April 2026
-- Purpose:
--   - Keep lightweight live status reads in dedicated tables
--   - Store sampled history for charts and availability diagnostics
--   - Store major operational/audit events separately from raw log files
--   - Preserve outage windows for longer-lived incident diagnostics

CREATE TABLE IF NOT EXISTS network_service_samples (
    id BIGSERIAL PRIMARY KEY,
    service_id UUID NOT NULL REFERENCES network_services(id) ON DELETE CASCADE,
    sampled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    overall_status network_overall_status NOT NULL,
    icmp_up BOOLEAN,
    icmp_bytes INTEGER,
    icmp_latency_ms INTEGER,
    icmp_ttl INTEGER,
    tcp_up BOOLEAN,
    tcp_latency_ms INTEGER,
    reason TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_network_service_samples_service_time
    ON network_service_samples(service_id, sampled_at DESC);

CREATE INDEX IF NOT EXISTS idx_network_service_samples_time
    ON network_service_samples(sampled_at DESC);

CREATE INDEX IF NOT EXISTS idx_network_service_samples_status_time
    ON network_service_samples(overall_status, sampled_at DESC);

CREATE TABLE IF NOT EXISTS network_service_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    service_id UUID REFERENCES network_services(id) ON DELETE SET NULL,
    service_name VARCHAR(200),
    service_address TEXT,
    service_port INTEGER,
    category VARCHAR(32) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    severity VARCHAR(16) NOT NULL,
    title VARCHAR(160) NOT NULL,
    summary TEXT,
    details JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_network_service_events_service_time
    ON network_service_events(service_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_network_service_events_time
    ON network_service_events(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_network_service_events_severity_time
    ON network_service_events(severity, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_network_service_outages_active_started
    ON network_service_outages(started_at DESC)
    WHERE ended_at IS NULL;
