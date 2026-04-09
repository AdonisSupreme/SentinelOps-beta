-- Migration: Task Center query acceleration
-- Purpose: support the most common Task Center list patterns with index-friendly access paths

CREATE INDEX IF NOT EXISTS idx_tasks_assigned_to_created_active
    ON tasks(assigned_to_id, created_at DESC)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_tasks_assigned_to_due_active
    ON tasks(assigned_to_id, due_date ASC)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_tasks_assigned_by_created_active
    ON tasks(assigned_by_id, created_at DESC)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_tasks_department_created_active
    ON tasks(department_id, created_at DESC)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_tasks_section_created_active
    ON tasks(section_id, created_at DESC)
    WHERE deleted_at IS NULL;
