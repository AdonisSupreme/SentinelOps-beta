-- Preserve who first engaged checklist work so performance scoring can
-- attribute timed-start discipline fairly without guessing from review state.

ALTER TABLE checklist_instance_items
ADD COLUMN IF NOT EXISTS started_by UUID;

ALTER TABLE checklist_instance_subitems
ADD COLUMN IF NOT EXISTS started_by UUID;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_instance_items_started_by'
    ) THEN
        ALTER TABLE checklist_instance_items
        ADD CONSTRAINT fk_instance_items_started_by
        FOREIGN KEY (started_by)
        REFERENCES users(id)
        ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_instance_subitems_started_by'
    ) THEN
        ALTER TABLE checklist_instance_subitems
        ADD CONSTRAINT fk_instance_subitems_started_by
        FOREIGN KEY (started_by)
        REFERENCES users(id)
        ON DELETE SET NULL;
    END IF;
END $$;

WITH earliest_item_starts AS (
    SELECT DISTINCT ON (activity.instance_item_id)
        activity.instance_item_id,
        activity.user_id
    FROM checklist_item_activity activity
    WHERE activity.action = 'STARTED'
    ORDER BY activity.instance_item_id, activity.created_at ASC
)
UPDATE checklist_instance_items item
SET started_by = earliest.user_id
FROM earliest_item_starts earliest
WHERE item.id = earliest.instance_item_id
  AND item.started_by IS NULL;

UPDATE checklist_instance_items
SET started_by = completed_by
WHERE started_by IS NULL
  AND started_at IS NOT NULL
  AND completed_by IS NOT NULL;

UPDATE checklist_instance_subitems
SET started_by = completed_by
WHERE started_by IS NULL
  AND started_at IS NOT NULL
  AND completed_by IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_checklist_instance_items_started_by_date
    ON checklist_instance_items (started_by, started_at DESC)
    WHERE started_by IS NOT NULL AND started_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_checklist_instance_subitems_started_by_date
    ON checklist_instance_subitems (started_by, started_at DESC)
    WHERE started_by IS NOT NULL AND started_at IS NOT NULL;
