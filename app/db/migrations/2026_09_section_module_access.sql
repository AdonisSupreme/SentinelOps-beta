-- Additive migration. Apply once before deploying both APIs and the frontend.
BEGIN;
SELECT pg_advisory_xact_lock(19780915);
CREATE TABLE IF NOT EXISTS access_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());
ALTER TABLE sections ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT true;
CREATE TABLE IF NOT EXISTS access_modules (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 module_key text NOT NULL UNIQUE, name text NOT NULL, description text NOT NULL,
 page_key text NOT NULL, is_active boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS section_modules (
 section_id uuid NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
 module_id uuid NOT NULL REFERENCES access_modules(id) ON DELETE CASCADE,
 created_at timestamptz NOT NULL DEFAULT now(), created_by uuid REFERENCES users(id) ON DELETE SET NULL,
 PRIMARY KEY (section_id, module_id)
);
CREATE INDEX IF NOT EXISTS section_modules_module_idx ON section_modules(module_id);
INSERT INTO access_modules(module_key,name,description,page_key) VALUES
('checklists.execution','Checklist operations','Checklist operations','Checklists'),
('checklists.templates','Template authoring','Template authoring','Checklists'),
('task_manager.tasks','Task workspace and analytics','Task workspace and analytics','Task Manager'),
('database.statistics','Database growth and capacity','Database growth and capacity','Database'),
('performance.dashboard','Performance and achievements','Performance and achievements','Performance'),
('team.scheduling','Team scheduling and patterns','Team scheduling and patterns','Team'),
('team.schedule','Personal schedule','Personal schedule','Team'),
('network_sentinel.monitoring','Service monitoring and investigation','Service monitoring and investigation','Network Sentinel'),
('network_sentinel.outage_history','Outage ledger and event history','Outage ledger and event history','Network Sentinel'),
('network_sentinel.configuration','Service configuration and controls','Service configuration and controls','Network Sentinel'),
('trustlink.daily_extraction','Daily extraction pipeline','Daily extraction pipeline','Trustlink'),
('trustlink.run_history','Extraction run history','Extraction run history','Trustlink'),
('trustlink.manual_run','Manual extraction and overwrite','Manual extraction and overwrite','Trustlink'),
('trustlink.configuration','Extraction source configuration','Extraction source configuration','Trustlink'),
('trustlink.rtgs','RTGS recovery and automation','RTGS recovery and automation','Trustlink'),
('nexus.dashboard','Fabric overview','Fabric overview','Nexus'),
('nexus.incidents','Incident investigation','Incident investigation','Nexus'),
('nexus.services','Service catalog and controls','Service catalog and controls','Nexus'),
('nexus.agents','Agent monitoring','Agent monitoring','Nexus'),
('nexus.databases','Database fabric','Database fabric','Nexus'),
('nexus.clusters','Service clusters','Service clusters','Nexus'),
('nexus.flows','Business flows','Business flows','Nexus'),
('nexus.dependencies','Dependency topology','Dependency topology','Nexus'),
('nexus.onboarding','Agent and service onboarding','Agent and service onboarding','Nexus'),
('nexus.rollover','Environment rollover','Environment rollover','Nexus'),
('nexus.sops','Procedure library','Procedure library','Nexus'),
('reports.crb','CRB reporting','CRB reporting','Reporting'),
('reports.hovering','Hovering queue','Hovering queue','Reporting'),
('reports.hovering_settings','Hovering robot settings','Hovering robot settings','Reporting'),
('funds_custody.workspace','Funds custody and clearing','Funds custody and clearing','Funds Custody')
ON CONFLICT(module_key) DO UPDATE SET name=EXCLUDED.name, description=EXCLUDED.description, page_key=EXCLUDED.page_key;
-- Defaults are applied ONLY once. Re-running never restores revoked grants.
DO $$
DECLARE legacy_section uuid;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM access_migrations WHERE version='2026_09_section_modules') THEN
  -- Preserve existing unsectioned accounts without a permanent authorization bypass.
  IF EXISTS(SELECT 1 FROM users WHERE section_id IS NULL) THEN
   SELECT id INTO legacy_section FROM sections WHERE lower(section_name)='legacy access migration' LIMIT 1;
   IF legacy_section IS NULL THEN
    INSERT INTO sections(section_name) VALUES ('Legacy access migration') RETURNING id INTO legacy_section;
   END IF;
   INSERT INTO ops_events(event_type,entity_type,entity_id,payload)
    SELECT 'USER_SECTION_CHANGED','ACCESS_MANAGEMENT',id,
      jsonb_build_object('actor','migration','target_user',id,'old_value',NULL,'new_value',legacy_section)
    FROM users WHERE section_id IS NULL;
   UPDATE users SET section_id=legacy_section WHERE section_id IS NULL;
  END IF;
  INSERT INTO section_modules(section_id,module_id)
   SELECT s.id,m.id FROM sections s CROSS JOIN access_modules m
   WHERE m.module_key NOT LIKE 'nexus.%' AND m.module_key NOT LIKE 'reports.%'
     AND m.module_key NOT IN ('trustlink.rtgs','funds_custody.workspace')
   ON CONFLICT DO NOTHING;
  -- Snapshot the pre-existing section restriction, not a runtime authorization rule.
  -- Review this UUID if NEXUS_ALLOWED_SECTION_IDS was customized in deployment.
  INSERT INTO section_modules(section_id,module_id)
   SELECT s.id,m.id FROM sections s CROSS JOIN access_modules m
   WHERE s.id='7bd4144d-68d8-4ac3-897d-245941612daf'::uuid
   ON CONFLICT DO NOTHING;
  INSERT INTO ops_events(event_type,entity_type,entity_id,payload)
   SELECT 'MODULE_ASSIGNED','ACCESS_MANAGEMENT',sm.section_id,
    jsonb_build_object('actor','migration','section',sm.section_id,'module',sm.module_id,'old_value',false,'new_value',true)
   FROM section_modules sm;
  INSERT INTO access_migrations(version) VALUES ('2026_09_section_modules');
 END IF;
END $$;
COMMIT;
