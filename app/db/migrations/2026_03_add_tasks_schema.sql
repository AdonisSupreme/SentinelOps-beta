-- Migration: Add Task Management System
-- Date: March 2026
-- Purpose: Implement comprehensive task management with proper relationships and constraints

-- =====================================================
-- CUSTOM ENUM TYPES
-- =====================================================
-- Following existing project pattern for enum types

CREATE TYPE task_type AS ENUM (
    'PERSONAL',
    'TEAM', 
    'DEPARTMENT',
    'SYSTEM'
);

CREATE TYPE task_priority AS ENUM (
    'LOW',
    'MEDIUM',
    'HIGH',
    'CRITICAL'
);

CREATE TYPE task_status AS ENUM (
    'DRAFT',
    'ACTIVE',
    'IN_PROGRESS',
    'COMPLETED',
    'CANCELLED',
    'ON_HOLD'
);

CREATE TYPE task_comment_type AS ENUM (
    'COMMENT',
    'STATUS_UPDATE',
    'ASSIGNMENT_CHANGE',
    'SYSTEM_UPDATE'
);

CREATE TYPE task_action AS ENUM (
    'CREATED',
    'UPDATED',
    'ASSIGNED',
    'UNASSIGNED',
    'COMPLETED',
    'CANCELLED',
    'REOPENED'
);

-- =====================================================
-- MAIN TASKS TABLE
-- =====================================================
-- Core task management table with comprehensive fields

CREATE TABLE IF NOT EXISTS tasks (
    -- Primary identification
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Basic task information
    title VARCHAR(200) NOT NULL,
    description TEXT,
    
    -- Classification fields using custom enums
    task_type task_type NOT NULL DEFAULT 'PERSONAL',
    priority task_priority NOT NULL DEFAULT 'MEDIUM',
    status task_status NOT NULL DEFAULT 'ACTIVE',
    
    -- Assignment relationships
    assigned_to_id UUID REFERENCES users(id) ON DELETE SET NULL,
    assigned_by_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    
    -- Organizational relationships
    department_id integer REFERENCES department(id) ON DELETE SET NULL,
    section_id UUID REFERENCES sections(id) ON DELETE SET NULL,
    
    -- Time tracking fields
    due_date TIMESTAMPTZ,
    estimated_hours DECIMAL(5,2) CHECK (estimated_hours IS NULL OR estimated_hours > 0),
    actual_hours DECIMAL(5,2) CHECK (actual_hours IS NULL OR actual_hours >= 0),
    completion_percentage INTEGER DEFAULT 0 CHECK (completion_percentage >= 0 AND completion_percentage <= 100),
    
    -- Metadata and categorization
    tags TEXT[], -- PostgreSQL array for tags
    parent_task_id UUID REFERENCES tasks(id) ON DELETE SET NULL,
    is_recurring BOOLEAN DEFAULT FALSE,
    recurrence_pattern TEXT,
    
    -- Audit fields following existing pattern
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ, -- Soft delete
    
    -- Constraints
    CONSTRAINT tasks_no_self_parent CHECK (parent_task_id IS NULL OR parent_task_id != id),
    CONSTRAINT tasks_completion_logic CHECK (
        (status = 'COMPLETED' AND completed_at IS NOT NULL) OR 
        (status != 'COMPLETED' AND completed_at IS NULL)
    )
);

-- =====================================================
-- TASK COMMENTS TABLE
-- =====================================================
-- Track all task communications and updates

CREATE TABLE IF NOT EXISTS task_comments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    content TEXT NOT NULL,
    comment_type task_comment_type NOT NULL DEFAULT 'COMMENT',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =====================================================
-- TASK ATTACHMENTS TABLE
-- =====================================================
-- File attachments for tasks

CREATE TABLE IF NOT EXISTS task_attachments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    filename VARCHAR(255) NOT NULL,
    file_path VARCHAR(500) NOT NULL,
    file_size BIGINT NOT NULL CHECK (file_size > 0),
    mime_type VARCHAR(100) NOT NULL,
    uploaded_by_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =====================================================
-- TASK HISTORY TABLE
-- =====================================================
-- Comprehensive audit trail for all task changes

CREATE TABLE IF NOT EXISTS task_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    action task_action NOT NULL,
    old_values JSONB,
    new_values JSONB,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =====================================================
-- PERFORMANCE INDEXES
-- =====================================================
-- Optimized for common query patterns

-- Primary task queries
CREATE INDEX IF NOT EXISTS idx_tasks_assigned_to ON tasks(assigned_to_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_assigned_by ON tasks(assigned_by_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_type ON tasks(task_type) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_due_date ON tasks(due_date) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_updated_at ON tasks(updated_at) WHERE deleted_at IS NULL;

-- Organizational queries
CREATE INDEX IF NOT EXISTS idx_tasks_department ON tasks(department_id);
CREATE INDEX IF NOT EXISTS idx_task_section ON sections(id);

-- Composite indexes for common filter combinations
CREATE INDEX IF NOT EXISTS idx_tasks_status_assigned ON tasks(status, assigned_to_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_department_status ON tasks(department_id, status) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_section_status ON sections(id);
CREATE INDEX IF NOT EXISTS idx_tasks_priority_status ON tasks(priority, status) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_type_status ON tasks(task_type, status) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_due_status ON tasks(due_date, status) WHERE deleted_at IS NULL;

-- Hierarchical task relationships
CREATE INDEX IF NOT EXISTS idx_tasks_parent_task ON tasks(parent_task_id) WHERE deleted_at IS NULL;

-- Tag search optimization (PostgreSQL GIN index for array)
CREATE INDEX IF NOT EXISTS idx_tasks_tags_gin ON tasks USING GIN(tags) WHERE deleted_at IS NULL;

-- Comments indexes
CREATE INDEX IF NOT EXISTS idx_task_comments_task ON task_comments(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_task_comments_user ON task_comments(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_task_comments_type ON task_comments(comment_type, created_at);

-- Attachments indexes
CREATE INDEX IF NOT EXISTS idx_task_attachments_task ON task_attachments(task_id, uploaded_at);
CREATE INDEX IF NOT EXISTS idx_task_attachments_user ON task_attachments(uploaded_by_id, uploaded_at);

-- History indexes
CREATE INDEX IF NOT EXISTS idx_task_history_task ON task_history(task_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_task_history_user ON task_history(user_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_task_history_action ON task_history(action, timestamp DESC);

-- =====================================================
-- VIEWS FOR COMMON QUERIES
-- =====================================================

-- Task summary view with aggregated data
CREATE OR REPLACE VIEW task_summary AS
SELECT 
    t.id,
    t.title,
    t.status,
    t.priority,
    t.task_type,
    t.assigned_to_id,
    t.due_date,
    t.completion_percentage,
    t.created_at,
    t.updated_at,
    t.deleted_at,
    
    -- Aggregated counts
    (SELECT COUNT(*) FROM task_comments tc WHERE tc.task_id = t.id) as comments_count,
    (SELECT COUNT(*) FROM task_attachments ta WHERE ta.task_id = t.id) as attachments_count,
    (SELECT COUNT(*) FROM tasks st WHERE st.parent_task_id = t.id AND st.deleted_at IS NULL) as subtasks_count,
    
    -- Assignment info
    u.username as assigned_to_username,
    u.first_name as assigned_to_first_name,
    u.last_name as assigned_to_last_name,
    
    -- Overdue flag
    CASE 
        WHEN t.due_date < now() AND t.status NOT IN ('COMPLETED', 'CANCELLED') THEN TRUE
        ELSE FALSE
    END as is_overdue
    
FROM tasks t
LEFT JOIN users u ON t.assigned_to_id = u.id
WHERE t.deleted_at IS NULL;

-- User task analytics view
CREATE OR REPLACE VIEW user_task_analytics AS
SELECT 
    u.id as user_id,
    u.username,
    u.first_name,
    u.last_name,
    
    -- Task counts
    COUNT(CASE WHEN t.assigned_to_id = u.id THEN 1 END) as assigned_tasks,
    COUNT(CASE WHEN t.assigned_to_id = u.id AND t.status = 'COMPLETED' THEN 1 END) as completed_tasks,
    COUNT(CASE WHEN t.assigned_to_id = u.id AND t.status IN ('ACTIVE', 'IN_PROGRESS') THEN 1 END) as current_workload,
    COUNT(CASE WHEN t.assigned_to_id = u.id AND t.due_date < now() AND t.status NOT IN ('COMPLETED', 'CANCELLED') THEN 1 END) as overdue_tasks,
    
    -- Performance metrics
    CASE 
        WHEN COUNT(CASE WHEN t.assigned_to_id = u.id THEN 1 END) > 0 THEN
            ROUND((COUNT(CASE WHEN t.assigned_to_id = u.id AND t.status = 'COMPLETED' THEN 1 END)::DECIMAL / 
                   COUNT(CASE WHEN t.assigned_to_id = u.id THEN 1 END)) * 100, 2)
        ELSE 0
    END as completion_rate,
    
    -- Average completion time (in hours)
    CASE 
        WHEN COUNT(CASE WHEN t.assigned_to_id = u.id AND t.status = 'COMPLETED' AND t.completed_at IS NOT NULL THEN 1 END) > 0 THEN
            ROUND(AVG(EXTRACT(EPOCH FROM (t.completed_at - t.created_at)) / 3600), 2)
        ELSE NULL
    END as avg_completion_hours
    
FROM users u
LEFT JOIN tasks t ON u.id = t.assigned_to_id AND t.deleted_at IS NULL
GROUP BY u.id, u.username, u.first_name, u.last_name;

-- =====================================================
-- TRIGGERS FOR AUTOMATIC UPDATES
-- =====================================================

-- Update updated_at timestamp on task changes
CREATE OR REPLACE FUNCTION update_task_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    
    -- Auto-set completion timestamp
    IF OLD.status != 'COMPLETED' AND NEW.status = 'COMPLETED' THEN
        NEW.completed_at = now();
        NEW.completion_percentage = 100;
    ELSIF OLD.status = 'COMPLETED' AND NEW.status != 'COMPLETED' THEN
        NEW.completed_at = NULL;
    END IF;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_tasks_updated_at
    BEFORE UPDATE ON tasks
    FOR EACH ROW
    EXECUTE FUNCTION update_task_updated_at();

-- Auto-create history entries for task changes
CREATE OR REPLACE FUNCTION log_task_changes()
RETURNS TRIGGER AS $$
BEGIN
    -- Log creation
    IF TG_OP = 'INSERT' THEN
        INSERT INTO task_history (task_id, user_id, action, new_values)
        VALUES (NEW.id, NEW.assigned_by_id, 'CREATED', 
                jsonb_build_object('title', NEW.title, 'status', NEW.status, 'priority', NEW.priority));
        RETURN NEW;
    END IF;
    
    -- Log updates
    IF TG_OP = 'UPDATE' THEN
        -- Only log if there are actual changes to important fields
        IF OLD.title IS DISTINCT FROM NEW.title OR
           OLD.description IS DISTINCT FROM NEW.description OR
           OLD.status IS DISTINCT FROM NEW.status OR
           OLD.priority IS DISTINCT FROM NEW.priority OR
           OLD.assigned_to_id IS DISTINCT FROM NEW.assigned_to_id OR
           OLD.due_date IS DISTINCT FROM NEW.due_date THEN
           
            INSERT INTO task_history (task_id, user_id, action, old_values, new_values)
            VALUES (
                NEW.id, 
                COALESCE(NEW.assigned_to_id, NEW.assigned_by_id), 
                'UPDATED',
                jsonb_build_object(
                    'title', OLD.title, 'status', OLD.status, 'priority', OLD.priority,
                    'assigned_to_id', OLD.assigned_to_id, 'due_date', OLD.due_date
                ),
                jsonb_build_object(
                    'title', NEW.title, 'status', NEW.status, 'priority', NEW.priority,
                    'assigned_to_id', NEW.assigned_to_id, 'due_date', NEW.due_date
                )
            );
        END IF;
        RETURN NEW;
    END IF;
    
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_task_history
    AFTER INSERT OR UPDATE ON tasks
    FOR EACH ROW
    EXECUTE FUNCTION log_task_changes();

-- =====================================================
-- MIGRATION NOTES
-- =====================================================
-- This migration implements a comprehensive task management system:
-- 
-- 1. Custom enum types for type safety and consistency
-- 2. Main tasks table with comprehensive fields and constraints
-- 3. Supporting tables for comments, attachments, and history
-- 4. Optimized indexes for common query patterns
-- 5. Views for analytics and summary data
-- 6. Triggers for automatic timestamp updates and audit logging
-- 
-- Features implemented:
-- - Soft delete support with deleted_at timestamp
-- - Hierarchical task relationships via parent_task_id
-- - Comprehensive audit trail with task_history
-- - Tag support using PostgreSQL arrays
-- - Performance optimized with strategic indexes
-- - Automatic completion timestamp management
-- - Overdue task detection in views
-- - User analytics for performance tracking
--
-- No down migration provided as this is a forward-only schema change
