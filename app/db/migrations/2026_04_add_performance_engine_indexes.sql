-- Support the unified performance command deck queries.

CREATE INDEX IF NOT EXISTS idx_checklist_instance_items_completed_by_date
    ON checklist_instance_items (completed_by, completed_at DESC)
    WHERE completed_by IS NOT NULL AND status = 'COMPLETED';

CREATE INDEX IF NOT EXISTS idx_checklist_instance_subitems_completed_by_date
    ON checklist_instance_subitems (completed_by, completed_at DESC)
    WHERE completed_by IS NOT NULL AND status = 'COMPLETED';

CREATE INDEX IF NOT EXISTS idx_handover_notes_created_by_date
    ON handover_notes (created_by, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_handover_notes_resolved_by_date
    ON handover_notes (resolved_by, resolved_at DESC)
    WHERE resolved_by IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_tasks_assigned_completed_at
    ON tasks (assigned_to_id, completed_at DESC)
    WHERE deleted_at IS NULL AND status = 'COMPLETED';

CREATE INDEX IF NOT EXISTS idx_tasks_assigned_due_open
    ON tasks (assigned_to_id, due_date)
    WHERE deleted_at IS NULL AND status IN ('ACTIVE', 'IN_PROGRESS', 'ON_HOLD');
