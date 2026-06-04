-- Migration: Network Sentinel monitor profiles
-- Date: May 2026
-- Purpose:
--   - Let monitored assets declare whether they are services, channels, VPNs, or network paths
--   - Allow tunnel-aware ICMP interpretation where "TTL expired in transit" is the expected reachability signal

ALTER TABLE network_services
    ADD COLUMN IF NOT EXISTS target_kind VARCHAR(32) NOT NULL DEFAULT 'SERVICE';

ALTER TABLE network_services
    ADD COLUMN IF NOT EXISTS allow_ttl_expired BOOLEAN NOT NULL DEFAULT false;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_network_services_target_kind'
    ) THEN
        ALTER TABLE network_services
            ADD CONSTRAINT chk_network_services_target_kind
            CHECK (target_kind IN ('SERVICE', 'CHANNEL', 'VPN', 'NETWORK'));
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_network_services_target_kind
    ON network_services(target_kind)
    WHERE deleted_at IS NULL;
