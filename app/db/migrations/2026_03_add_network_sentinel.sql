-- Migration: Add Network Sentinel (multi-service monitoring)
-- Date: March 2026
-- Purpose:
--   - Store monitored service/server metadata (address/port + UI metadata) in DB
--   - Store latest real-time status snapshot (fast dashboard reads)
--   - Store outages/incident windows (investigations + durations)
--
-- Notes:
--   - Continuous ping/check history remains in file logs (same line format as existing script).
--   - This migration is forward-only (no down migration).

-- =====================================================
-- 1) ENUMS
-- =====================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'network_overall_status') THEN
        CREATE TYPE network_overall_status AS ENUM ('UNKNOWN', 'UP', 'DEGRADED', 'DOWN');
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'network_outage_cause') THEN
        CREATE TYPE network_outage_cause AS ENUM ('UNKNOWN', 'NETWORK', 'APPLICATION', 'ICMP_BLOCKED', 'DNS', 'CONFIG');
    END IF;
END
$$;

-- =====================================================
-- 2) MONITORED SERVICES (metadata stored in DB)
-- =====================================================

CREATE TABLE IF NOT EXISTS network_services (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Display / UX
    name VARCHAR(200) NOT NULL,
    description TEXT,
    notes TEXT,
    group_name VARCHAR(120),
    environment VARCHAR(60),
    owner_team VARCHAR(120),
    tags TEXT[],
    -- Keep both generic and UI-prefixed names for compatibility with frontend naming.
    color VARCHAR(32),
    icon VARCHAR(64),
    ui_color VARCHAR(32),
    ui_icon VARCHAR(64),

    -- Target / checks
    address TEXT NOT NULL,
    port INTEGER CHECK (port IS NULL OR (port > 0 AND port <= 65535)),
    check_icmp BOOLEAN NOT NULL DEFAULT TRUE,
    check_tcp BOOLEAN NOT NULL DEFAULT TRUE,
    timeout_ms INTEGER NOT NULL DEFAULT 3000 CHECK (timeout_ms >= 250 AND timeout_ms <= 60000),
    interval_seconds INTEGER NOT NULL DEFAULT 2 CHECK (interval_seconds >= 1 AND interval_seconds <= 3600),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,

    -- Audit
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_network_services_name_active
    ON network_services(name)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_network_services_enabled
    ON network_services(enabled)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_network_services_group
    ON network_services(group_name)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_network_services_environment
    ON network_services(environment)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_network_services_tags_gin
    ON network_services USING GIN(tags)
    WHERE deleted_at IS NULL;

-- =====================================================
-- 3) LATEST STATUS SNAPSHOT (one row per service)
-- =====================================================

CREATE TABLE IF NOT EXISTS network_service_status (
    service_id UUID PRIMARY KEY REFERENCES network_services(id) ON DELETE CASCADE,

    last_checked_at TIMESTAMPTZ,

    icmp_up BOOLEAN,
    icmp_bytes INTEGER,
    icmp_latency_ms INTEGER,
    icmp_ttl INTEGER,

    tcp_up BOOLEAN,
    tcp_latency_ms INTEGER,

    overall_status network_overall_status NOT NULL DEFAULT 'UNKNOWN',
    reason TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_state_change_at TIMESTAMPTZ,

    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_network_service_status_overall
    ON network_service_status(overall_status);

CREATE INDEX IF NOT EXISTS idx_network_service_status_last_checked
    ON network_service_status(last_checked_at DESC);

-- =====================================================
-- 4) OUTAGES / INCIDENT WINDOWS
-- =====================================================

CREATE TABLE IF NOT EXISTS network_service_outages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    service_id UUID NOT NULL REFERENCES network_services(id) ON DELETE CASCADE,

    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ,

    duration_seconds INTEGER,
    cause network_outage_cause NOT NULL DEFAULT 'UNKNOWN',
    details JSONB,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_network_service_outage_active
    ON network_service_outages(service_id)
    WHERE ended_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_network_service_outages_service_time
    ON network_service_outages(service_id, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_network_service_outages_active
    ON network_service_outages(ended_at)
    WHERE ended_at IS NULL;

-- =====================================================
-- 5) TRIGGER
-- =====================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'update_network_services_updated_at') THEN
        CREATE OR REPLACE FUNCTION update_network_services_updated_at()
        RETURNS TRIGGER AS $fn$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $fn$ LANGUAGE plpgsql;
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trigger_network_services_updated_at') THEN
        CREATE TRIGGER trigger_network_services_updated_at
            BEFORE UPDATE ON network_services
            FOR EACH ROW
            EXECUTE FUNCTION update_network_services_updated_at();
    END IF;
END
$$;