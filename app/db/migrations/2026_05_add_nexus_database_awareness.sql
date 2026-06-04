-- Sentinel Nexus database-awareness upgrade.
-- Purpose:
--   - Make application database dependencies queryable from the shared SentinelOps database.
--   - Keep the canonical contract in service_catalog.payload / dependency_edge.payload while indexing
--     the database fields Nexus uses for root-cause, blast-radius, and onboarding review.

CREATE INDEX IF NOT EXISTS idx_nexus_service_database_enabled
    ON service_catalog ((payload #>> '{database_profile,enabled}'));

CREATE INDEX IF NOT EXISTS idx_nexus_service_database_platform
    ON service_catalog ((payload #>> '{database_profile,platform}'));

CREATE INDEX IF NOT EXISTS idx_nexus_service_database_name
    ON service_catalog ((payload #>> '{database_profile,database_name}'));

CREATE INDEX IF NOT EXISTS idx_nexus_service_database_shared
    ON service_catalog ((payload #>> '{database_profile,shared_dependency}'));

CREATE INDEX IF NOT EXISTS idx_nexus_service_database_profile_gin
    ON service_catalog USING GIN ((payload -> 'database_profile'));

CREATE INDEX IF NOT EXISTS idx_nexus_dependency_database_access_gin
    ON dependency_edge USING GIN ((payload -> 'database_access'));

CREATE INDEX IF NOT EXISTS idx_nexus_signal_database_name
    ON signal_event ((payload #>> '{attributes,database_name}'), timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_nexus_signal_database_error_codes
    ON signal_event USING GIN ((payload #> '{attributes,db_error_codes}'));

CREATE INDEX IF NOT EXISTS idx_nexus_signal_failure_database
    ON signal_event (timestamp DESC)
    WHERE failure_domain_hint = 'database'
       OR payload #>> '{failure_domain_hint}' = 'database'
       OR payload #>> '{signature,signature_family}' LIKE 'database%';
