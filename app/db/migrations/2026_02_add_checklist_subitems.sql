-- Migration: Add Hierarchical Subitems to Checklist System
-- Date: February 2026
-- Purpose: Enable checklist items to have subitems that need to be completed sequentially

-- =====================================================
-- CHECKLIST TEMPLATE SUBITEMS
-- =====================================================
-- Stores subitems defined in checklist templates
-- These are structural definitions that copy to instances
CREATE TABLE IF NOT EXISTS checklist_template_subitems (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    template_item_id UUID NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    item_type checklist_item_type NOT NULL DEFAULT 'ROUTINE',
    is_required BOOLEAN NOT NULL DEFAULT TRUE,
    scheduled_time TIME,
    notify_before_minutes INTEGER,
    severity INTEGER DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (template_item_id) REFERENCES checklist_template_items(id) ON DELETE CASCADE,
    UNIQUE (template_item_id, sort_order)
);

-- =====================================================
-- CHECKLIST INSTANCE SUBITEMS
-- =====================================================
-- Stores subitems for actual checklist instance items
-- Mirrors the structure of checklist_instance_items but linked to parent items
CREATE TABLE IF NOT EXISTS checklist_instance_subitems (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    instance_item_id UUID NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    item_type checklist_item_type NOT NULL DEFAULT 'ROUTINE',
    is_required BOOLEAN NOT NULL DEFAULT TRUE,
    status item_status NOT NULL DEFAULT 'PENDING',
    completed_by UUID,
    completed_at TIMESTAMPTZ,
    skipped_reason TEXT,
    failure_reason TEXT,
    severity INTEGER DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (instance_item_id) REFERENCES checklist_instance_items(id) ON DELETE CASCADE,
    UNIQUE (instance_item_id, sort_order)
);

-- =====================================================
-- INDICES FOR PERFORMANCE
-- =====================================================
CREATE INDEX IF NOT EXISTS idx_checklist_template_subitems_template_item_id 
    ON checklist_template_subitems(template_item_id);

CREATE INDEX IF NOT EXISTS idx_checklist_template_subitems_sort_order 
    ON checklist_template_subitems(template_item_id, sort_order);

CREATE INDEX IF NOT EXISTS idx_checklist_instance_subitems_instance_item_id 
    ON checklist_instance_subitems(instance_item_id);

CREATE INDEX IF NOT EXISTS idx_checklist_instance_subitems_status 
    ON checklist_instance_subitems(instance_item_id, status);

CREATE INDEX IF NOT EXISTS idx_checklist_instance_subitems_created_by 
    ON checklist_instance_subitems(completed_by);

-- =====================================================
-- VIEW: Get completion status for items with subitems
-- =====================================================
-- Shows parent item with subitem completion stats
CREATE OR REPLACE VIEW item_subitem_status AS
SELECT 
    cii.id as item_id,
    cii.instance_id,
    COUNT(cis.id) as total_subitems,
    COUNT(CASE WHEN cis.status = 'COMPLETED' THEN 1 END) as completed_subitems,
    COUNT(CASE WHEN cis.status = 'SKIPPED' THEN 1 END) as skipped_subitems,
    COUNT(CASE WHEN cis.status = 'FAILED' THEN 1 END) as failed_subitems,
    COUNT(CASE WHEN cis.status IN ('PENDING', 'IN_PROGRESS') THEN 1 END) as pending_subitems,
    CASE 
        WHEN COUNT(cis.id) = 0 THEN NULL -- No subitems
        WHEN COUNT(cis.id) = COUNT(CASE WHEN cis.status = 'COMPLETED' THEN 1 END) THEN 'COMPLETED'
        WHEN COUNT(cis.id) = COUNT(CASE WHEN cis.status IN ('COMPLETED', 'SKIPPED') THEN 1 END) THEN 'COMPLETED_WITH_EXCEPTIONS'
        WHEN COUNT(CASE WHEN cis.status IN ('COMPLETED', 'SKIPPED', 'FAILED') THEN 1 END) > 0 THEN 'IN_PROGRESS'
        ELSE 'PENDING'
    END as subitems_status
FROM checklist_instance_items cii
LEFT JOIN checklist_instance_subitems cis ON cii.id = cis.instance_item_id
WHERE cis.id IS NOT NULL -- Only for items that have subitems
GROUP BY cii.id, cii.instance_id;

-- =====================================================
-- FUNCTION: Copy template subitems to instance subitems
-- =====================================================
-- Called when an instance is created to populate subitems from template
CREATE OR REPLACE FUNCTION copy_template_subitems_to_instance(
    p_instance_item_id UUID,
    p_template_item_id UUID
)
RETURNS void AS $$
BEGIN
    -- Copy subitems from template to instance
    INSERT INTO checklist_instance_subitems (
        instance_item_id,
        title,
        description,
        item_type,
        is_required,
        status,
        severity,
        sort_order
    )
    SELECT
        p_instance_item_id,
        cts.title,
        cts.description,
        cts.item_type,
        cts.is_required,
        'PENDING',
        cts.severity,
        cts.sort_order
    FROM checklist_template_subitems cts
    WHERE cts.template_item_id = p_template_item_id
    ORDER BY cts.sort_order;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- FUNCTION: Get total completion percentage including subitems
-- =====================================================
-- Calculates overall completion considering parent items and their subitems
CREATE OR REPLACE FUNCTION get_checklist_completion_with_subitems(
    p_instance_id UUID
)
RETURNS NUMERIC AS $$
DECLARE
    v_total_actionable INTEGER;
    v_completed INTEGER;
    v_completion_percent NUMERIC;
BEGIN
    -- Count total actionable items and subitems
    SELECT 
        COUNT(cii.id) +
        COALESCE(SUM(
            SELECT COUNT(*)
            FROM checklist_instance_subitems cis
            WHERE cis.instance_item_id = cii.id
        ), 0) as total,
        COUNT(CASE WHEN cii.status = 'COMPLETED' THEN 1 END) +
        COALESCE(SUM(
            SELECT COUNT(*)
            FROM checklist_instance_subitems cis
            WHERE cis.instance_item_id = cii.id AND cis.status = 'COMPLETED'
        ), 0) as completed
    INTO v_total_actionable, v_completed
    FROM checklist_instance_items cii
    WHERE cii.instance_id = p_instance_id;
    
    -- Calculate percentage
    IF v_total_actionable = 0 THEN
        v_completion_percent := 0;
    ELSE
        v_completion_percent := ROUND((v_completed::NUMERIC / v_total_actionable::NUMERIC) * 100, 2);
    END IF;
    
    RETURN v_completion_percent;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- MIGRATION NOTES
-- =====================================================
-- This migration adds support for hierarchical checklist items:
-- 
-- 1. checklist_template_subitems: Defines subitems at template level
--    - Allows reusable subitem definitions
--    - Linked to parent template items
-- 
-- 2. checklist_instance_subitems: Instance-level subitems
--    - Each instance item can have multiple subitems
--    - Each subitem tracks its own status independently
--    - Statuses: PENDING, IN_PROGRESS, COMPLETED, SKIPPED, FAILED
-- 
-- Workflow:
-- 1. Admin defines template items with subitems
-- 2. When instance is created, subitems are copied from template
-- 3. User starts working on parent item
-- 4. Backend returns subitems for that item
-- 5. User completes/skips/fails each subitem sequentially
-- 6. Once all subitems are actioned, parent item shows subitem statuses
-- 7. Parent item can then be marked complete (if all subitems are done)
--
-- No down migration provided as this is a forward-only schema change
