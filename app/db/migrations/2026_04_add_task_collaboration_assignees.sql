-- Migration: Task collaboration assignees
-- Purpose: support multi-user assignment on a single task while preserving legacy assigned_to_id behavior.

CREATE TABLE IF NOT EXISTS task_assignees (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    assigned_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT task_assignees_task_user_unique UNIQUE (task_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_task_assignees_task_id
    ON task_assignees(task_id, assigned_at DESC);

CREATE INDEX IF NOT EXISTS idx_task_assignees_user_id
    ON task_assignees(user_id, assigned_at DESC);

INSERT INTO task_assignees (task_id, user_id, assigned_by_id, assigned_at)
SELECT
    t.id,
    t.assigned_to_id,
    t.assigned_by_id,
    COALESCE(t.updated_at, t.created_at, now())
FROM tasks t
WHERE t.assigned_to_id IS NOT NULL
ON CONFLICT (task_id, user_id) DO NOTHING;
