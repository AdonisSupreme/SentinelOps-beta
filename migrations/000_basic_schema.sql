-- Basic database schema for checklist functionality
-- This creates the essential tables needed for the checklist system

-- Create enum types if they don't exist
DO $$ BEGIN
    CREATE TYPE shift_type AS ENUM (
        'MORNING',
        'AFTERNOON',
        'NIGHT'
    );
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE checklist_status AS ENUM (
        'OPEN',
        'IN_PROGRESS',
        'PENDING_REVIEW',
        'COMPLETED',
        'COMPLETED_WITH_EXCEPTIONS',
        'INCOMPLETE'
    );
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE checklist_item_type AS ENUM (
        'ROUTINE',
        'TIMED',
        'SCHEDULED_EVENT',
        'CONDITIONAL',
        'INFORMATIONAL'
    );
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE item_status AS ENUM (
        'PENDING',
        'IN_PROGRESS',
        'COMPLETED',
        'SKIPPED',
        'FAILED'
    );
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE activity_action AS ENUM (
        'STARTED',
        'COMPLETED',
        'COMMENTED',
        'ACKNOWLEDGED',
        'SKIPPED',
        'ESCALATED'
    );
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Create checklist_templates table
CREATE TABLE IF NOT EXISTS checklist_templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    description TEXT,
    shift shift_type NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    version INTEGER NOT NULL DEFAULT 1,
    created_by UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (name, shift, version)
);

-- Create checklist_template_items table (for backward compatibility)
CREATE TABLE IF NOT EXISTS checklist_template_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    template_id UUID NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    item_type checklist_item_type NOT NULL,
    is_required BOOLEAN NOT NULL DEFAULT TRUE,
    scheduled_time TIME,
    notify_before_minutes INTEGER,
    severity INTEGER DEFAULT 1,
    sort_order INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (template_id) REFERENCES checklist_templates(id) ON DELETE CASCADE
);

-- Create checklist_instances table
CREATE TABLE IF NOT EXISTS checklist_instances (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    template_id UUID,
    checklist_date DATE NOT NULL,
    shift shift_type NOT NULL,
    shift_start TIMESTAMPTZ NOT NULL,
    shift_end TIMESTAMPTZ NOT NULL,
    status checklist_status NOT NULL DEFAULT 'OPEN',
    created_by UUID,
    closed_by UUID,
    closed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Create checklist_instance_items table with template_item_key
CREATE TABLE IF NOT EXISTS checklist_instance_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    instance_id UUID NOT NULL,
    template_item_key TEXT NOT NULL,
    status item_status NOT NULL DEFAULT 'PENDING',
    completed_by UUID,
    completed_at TIMESTAMPTZ,
    skipped_reason TEXT,
    failure_reason TEXT,
    FOREIGN KEY (instance_id) REFERENCES checklist_instances(id) ON DELETE CASCADE,
    UNIQUE (instance_id, template_item_key)
);

-- Create checklist_participants table
CREATE TABLE IF NOT EXISTS checklist_participants (
    instance_id UUID NOT NULL,
    user_id UUID NOT NULL,
    joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (instance_id, user_id),
    FOREIGN KEY (instance_id) REFERENCES checklist_instances(id) ON DELETE CASCADE
);

-- Create ops_events table
CREATE TABLE IF NOT EXISTS ops_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id UUID NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_checklist_instances_date_shift ON checklist_instances(checklist_date, shift);
CREATE INDEX IF NOT EXISTS idx_checklist_instance_items_instance ON checklist_instance_items(instance_id);
CREATE INDEX IF NOT EXISTS idx_checklist_instance_items_key ON checklist_instance_items(template_item_key);
CREATE INDEX IF NOT EXISTS idx_checklist_participants_instance ON checklist_participants(instance_id);
CREATE INDEX IF NOT EXISTS idx_ops_events_entity ON ops_events(entity_type, entity_id);

-- Insert a basic morning template for testing
INSERT INTO checklist_templates (id, name, description, shift, is_active, version, created_by)
VALUES (
    gen_random_uuid(),
    'Morning Shift – Core Banking & Digital Operations',
    'Basic morning shift checklist template',
    'MORNING',
    true,
    1,
    NULL
) ON CONFLICT DO NOTHING;

-- Insert some basic template items for testing
INSERT INTO checklist_template_items (id, template_id, title, description, item_type, is_required, severity, sort_order)
SELECT 
    gen_random_uuid(),
    ct.id,
    'System Uptime Check',
    'Check and report system uptime status',
    'ROUTINE',
    true,
    5,
    100
FROM checklist_templates ct 
WHERE ct.shift = 'MORNING' AND ct.is_active = true
LIMIT 1 ON CONFLICT DO NOTHING;
