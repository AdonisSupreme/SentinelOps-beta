BEGIN;

DROP VIEW IF EXISTS v_active_checklists_by_section;
DROP VIEW IF EXISTS v_active_checklists;

ALTER TABLE checklist_templates
    ALTER COLUMN shift TYPE TEXT USING shift::text;

ALTER TABLE checklist_instances
    ALTER COLUMN shift TYPE TEXT USING shift::text;

CREATE INDEX IF NOT EXISTS idx_checklist_templates_shift_text
    ON checklist_templates (UPPER(shift::text));

CREATE INDEX IF NOT EXISTS idx_checklist_instances_date_shift_text
    ON checklist_instances (checklist_date, UPPER(shift::text));

CREATE OR REPLACE VIEW v_active_checklists AS
SELECT
    ci.id,
    ci.shift,
    ci.checklist_date,
    ci.status,
    COUNT(DISTINCT cp.user_id) as participant_count,
    COUNT(DISTINCT CASE WHEN cii.status = 'COMPLETED' THEN cii.id END) as completed_items,
    COUNT(DISTINCT CASE WHEN cii.status = 'PENDING' THEN cii.id END) as pending_items,
    COUNT(DISTINCT CASE WHEN cii.status = 'SKIPPED' THEN cii.id END) as skipped_items,
    COUNT(DISTINCT CASE WHEN cii.status = 'FAILED' THEN cii.id END) as failed_items
FROM checklist_instances ci
LEFT JOIN checklist_participants cp ON ci.id = cp.instance_id
LEFT JOIN checklist_instance_items cii ON ci.id = cii.instance_id
WHERE ci.status IN ('OPEN', 'IN_PROGRESS', 'PENDING_REVIEW')
GROUP BY ci.id, ci.shift, ci.checklist_date, ci.status
ORDER BY ci.shift_start DESC;

CREATE OR REPLACE VIEW v_active_checklists_by_section AS
SELECT
  ci.id,
  ci.template_id,
  ci.checklist_date,
  ci.shift,
  ci.status,
  ci.section_id,
  COUNT(cp.user_id) FILTER (WHERE cp.user_id IS NOT NULL) AS participants_count
FROM checklist_instances ci
LEFT JOIN checklist_participants cp ON cp.instance_id = ci.id
GROUP BY ci.id, ci.template_id, ci.checklist_date, ci.shift, ci.status, ci.section_id;

COMMIT;
