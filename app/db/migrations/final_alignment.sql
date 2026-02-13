-- File: app/db/migrations/final_alignment.sql
-- Purpose: Final database schema adjustments for full DB-driven architecture
-- This ensures perfect alignment between DB schema and app logic

-- ===================================================================
-- 1. VERIFY CORE TABLES EXIST (these should exist from initial setup)
-- ===================================================================

-- Verify checklist_instance_items has all required fields
ALTER TABLE checklist_instance_items
ADD COLUMN IF NOT EXISTS template_item_key TEXT;  -- For backward compatibility if needed


-- ===================================================================
-- 2. ADD MISSING INDEXES FOR PERFORMANCE
-- ===================================================================

-- Checklist queries
CREATE INDEX IF NOT EXISTS idx_checklist_instances_template_id 
    ON checklist_instances(template_id);

CREATE INDEX IF NOT EXISTS idx_checklist_template_items_template_id 
    ON checklist_template_items(template_id);

-- Item queries
CREATE INDEX IF NOT EXISTS idx_instance_items_instance_id 
    ON checklist_instance_items(instance_id);

CREATE INDEX IF NOT EXISTS idx_instance_items_template_item_id 
    ON checklist_instance_items(template_item_id);

-- Activity queries
CREATE INDEX IF NOT EXISTS idx_activity_user_id 
    ON checklist_item_activity(user_id);

CREATE INDEX IF NOT EXISTS idx_activity_created_at_desc 
    ON checklist_item_activity(created_at DESC);

-- Participant queries
CREATE INDEX IF NOT EXISTS idx_participants_instance_id 
    ON checklist_participants(instance_id);

CREATE INDEX IF NOT EXISTS idx_participants_user_id 
    ON checklist_participants(user_id);

-- Notification queries
CREATE INDEX IF NOT EXISTS idx_notifications_user_id 
    ON notifications(user_id);

CREATE INDEX IF NOT EXISTS idx_notifications_role_id 
    ON notifications(role_id);

-- Auth queries
CREATE INDEX IF NOT EXISTS idx_auth_sessions_user_id 
    ON auth_sessions(user_id);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_revoked 
    ON auth_sessions(revoked_at);

CREATE INDEX IF NOT EXISTS idx_auth_events_user_id 
    ON auth_events(user_id);

CREATE INDEX IF NOT EXISTS idx_auth_events_event_type 
    ON auth_events(event_type);

-- Ops events queries
CREATE INDEX IF NOT EXISTS idx_ops_events_entity_id 
    ON ops_events(entity_id);

CREATE INDEX IF NOT EXISTS idx_ops_events_event_type 
    ON ops_events(event_type);

-- ===================================================================
-- 3. ADD CHECKLIST ITEM ERROR/COMPLETION DETAILSFor better activity tracking
-- ===================================================================

-- Verify activity table can log all transitions
-- Table already supports this via activity_action ENUM

-- ===================================================================
-- 4. ENSURE STATE RULES ARE POPULATED CORRECTLY
-- ===================================================================

-- Verify state_transition_rules are configured with correct role IDs
-- This query helps identify manager/admin roles for state rules

-- Get role IDs for manager and admin
SELECT id, name FROM roles WHERE name IN ('manager', 'admin');

ALTER TABLE state_transition_rules ADD COLUMN allows_roles BOOLEAN;

-- Insert missing state transition rules for CHECKLIST_ITEM (if needed)
INSERT INTO state_transition_rules (
    entity_type, from_status, to_status, 
    allows_roles, requires_comment, requires_approval, cooldown_seconds
) VALUES
-- Item transitions
    ('CHECKLIST_ITEM', 'PENDING', 'IN_PROGRESS', NULL, FALSE, FALSE, 0),
    ('CHECKLIST_ITEM', 'IN_PROGRESS', 'COMPLETED', NULL, FALSE, FALSE, 0),
    ('CHECKLIST_ITEM', 'IN_PROGRESS', 'SKIPPED', NULL, TRUE, FALSE, 0),
    ('CHECKLIST_ITEM', 'IN_PROGRESS', 'FAILED', NULL, TRUE, FALSE, 0),
    ('CHECKLIST_ITEM', 'PENDING', 'SKIPPED', NULL, TRUE, FALSE, 0),
    ('CHECKLIST_ITEM', 'PENDING', 'FAILED', NULL, TRUE, FALSE, 0)
ON CONFLICT (entity_type, from_status, to_status) DO NOTHING;

-- ===================================================================
-- 5. ENSURE DEPARTMENTS AND SECTIONS ARE PROPERLY CONNECTED
-- ===================================================================

-- Verify department/section structure
SELECT COUNT(*) as department_count FROM department;
SELECT COUNT(*) as section_count FROM sections;
SELECT COUNT(*) as dept_section_links FROM department_sections;

-- ===================================================================
-- 6. ADD METADATA COLUMNS FOR ENHANCED LOGGING (OPTIONAL)
-- ===================================================================

-- Add metadata to checklist_instances for extra tracking
ALTER TABLE checklist_instances
ADD COLUMN IF NOT EXISTS metadata JSONB;

-- Add success_metrics to instances (for gamification)
ALTER TABLE checklist_instances
ADD COLUMN IF NOT EXISTS completion_time_seconds INTEGER;

ALTER TABLE checklist_instances
ADD COLUMN IF NOT EXISTS exception_count INTEGER DEFAULT 0;

-- ===================================================================
-- 7. CREATE VIEWS FOR COMMON QUERIES (PERFORMANCE)
-- ===================================================================

-- View: Current active checklists for dashboard
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

-- View: User activity summary for leaderboard
CREATE OR REPLACE VIEW v_user_activity_summary AS
SELECT 
    u.id,
    u.username,
    u.email,
    COUNT(DISTINCT ca.id) as total_activities,
    COUNT(DISTINCT CASE WHEN ca.action = 'COMPLETED' THEN ca.id END) as completed_count,
    COUNT(DISTINCT CASE WHEN ca.action = 'SKIPPED' THEN ca.id END) as skipped_count,
    COUNT(DISTINCT CASE WHEN ca.action = 'ESCALATED' THEN ca.id END) as escalated_count,
    MAX(ca.created_at) as last_activity
FROM users u
LEFT JOIN checklist_item_activity ca ON u.id = ca.user_id
GROUP BY u.id, u.username, u.email
ORDER BY total_activities DESC;

-- ===================================================================
-- 8. ENSURE ALL FOREIGN KEY REFERENCES ARE CORRECT
-- ===================================================================

-- Verify foreign key constraints
-- These should all pass if initial schema was created correctly

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_templates_created_by'
    ) THEN
        ALTER TABLE checklist_templates
        ADD CONSTRAINT fk_templates_created_by
        FOREIGN KEY (created_by)
        REFERENCES users(id)
        ON DELETE SET NULL;
    END IF;
END
$$;

	
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_instances_created_by'
    ) THEN
        ALTER TABLE checklist_instances
        ADD CONSTRAINT fk_instances_created_by
        FOREIGN KEY (created_by)
        REFERENCES users(id)
        ON DELETE SET NULL;
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class t ON c.conrelid = t.oid
        WHERE c.conname = 'fk_instances_closed_by'
          AND t.relname = 'checklist_instances'
    ) THEN
        ALTER TABLE checklist_instances
        ADD CONSTRAINT fk_instances_closed_by
        FOREIGN KEY (closed_by)
        REFERENCES users(id)
        ON DELETE SET NULL;
    END IF;
END
$$;


DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class t ON c.conrelid = t.oid
        WHERE c.conname = 'fk_instance_items_completed_by'
          AND t.relname = 'checklist_instance_items'
    ) THEN
        ALTER TABLE checklist_instance_items
        ADD CONSTRAINT fk_instance_items_completed_by
        FOREIGN KEY (completed_by)
        REFERENCES users(id)
        ON DELETE SET NULL;
    END IF;
END
$$;

-- ===================================================================
-- 9. VERIFY TIMESTAMP COLUMNS USE UTC TIMEZONE
-- ===================================================================

-- All timestamp columns should default to now()::timestamptz for UTC
-- Verify via: SELECT column_name, data_type FROM information_schema.columns 
--            WHERE table_name = 'table_name' AND data_type LIKE '%timestamp%'

-- ===================================================================
-- 10. GRANT APPROPRIATE PERMISSIONS (if using role-based access in DB)
-- ===================================================================

-- Example: If using separate database roles
-- GRANT SELECT ON ALL TABLES IN SCHEMA public TO app_user;
-- GRANT INSERT, UPDATE ON checklist_instances TO app_user;
-- GRANT INSERT ON auth_events, ops_events, notifications TO app_user;

-- ===================================================================
-- FINAL VERIFICATION QUERY
-- ===================================================================

-- Check that all key tables have data
SELECT 
    'users' as table_name,
    COUNT(*) as row_count
FROM users
UNION ALL
SELECT 'roles', COUNT(*) FROM roles
UNION ALL
SELECT 'checklist_templates', COUNT(*) FROM checklist_templates
UNION ALL
SELECT 'checklist_instances', COUNT(*) FROM checklist_instances
UNION ALL
SELECT 'auth_sessions', COUNT(*) FROM auth_sessions
UNION ALL
SELECT 'notifications', COUNT(*) FROM notifications
UNION ALL
SELECT 'ops_events', COUNT(*) FROM ops_events
ORDER BY table_name;
