-- 2026-04-17: Add explicit final verdict fields for checklist instance items.
-- Final verdicts are distinct from completion notes and are used for
-- exception handovers when an operator needs to summarize the outcome of an
-- item that contains reported subitems.

ALTER TABLE checklist_instance_items
ADD COLUMN IF NOT EXISTS final_verdict TEXT;

ALTER TABLE checklist_instance_items
ADD COLUMN IF NOT EXISTS final_verdict_by UUID REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE checklist_instance_items
ADD COLUMN IF NOT EXISTS final_verdict_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_checklist_instance_items_final_verdict_at
ON checklist_instance_items (final_verdict_at DESC)
WHERE final_verdict IS NOT NULL;
