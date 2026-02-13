-- 2026-02-07: Add departments, sections, department_sections, shifts, scheduled_shifts
BEGIN;

-- Departments
CREATE TABLE IF NOT EXISTS department (
    id SERIAL PRIMARY KEY,
    department_name TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Sections
CREATE TABLE IF NOT EXISTS sections (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    section_name TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    manager_id UUID NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sections_name ON sections(LOWER(section_name));

-- Join table: department <-> sections
CREATE TABLE IF NOT EXISTS department_sections (
    department_id INTEGER NOT NULL REFERENCES department(id) ON DELETE CASCADE,
    section_id UUID NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
    PRIMARY KEY (department_id, section_id)
);

-- Attach users to departments & sections (single membership)
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS department_id INTEGER REFERENCES department(id),
    ADD COLUMN IF NOT EXISTS section_id UUID REFERENCES sections(id);

-- Roles mapping (if not present)
CREATE TABLE IF NOT EXISTS roles (
    name TEXT PRIMARY KEY,
    description TEXT
);

CREATE TABLE IF NOT EXISTS user_roles (
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    role_name TEXT REFERENCES roles(name) ON DELETE CASCADE,
    PRIMARY KEY (user_id, role_name)
);

INSERT INTO roles (name, description) VALUES
    ('admin','Full system administrator'),
    ('manager','Section manager'),
    ('user','Regular operator')
ON CONFLICT (name) DO NOTHING;

-- Shifts + scheduled shifts
CREATE TABLE IF NOT EXISTS shifts (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    start_time TIME NOT NULL,
    end_time TIME NOT NULL,
    timezone TEXT DEFAULT 'UTC',
    color TEXT,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS scheduled_shifts (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    shift_id INTEGER REFERENCES shifts(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    start_ts TIMESTAMPTZ,
    end_ts TIMESTAMPTZ,
    assigned_by UUID REFERENCES users(id),
    status TEXT DEFAULT 'ASSIGNED',
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (shift_id, user_id, date)
);

CREATE INDEX IF NOT EXISTS idx_scheduled_shifts_by_user_date ON scheduled_shifts(user_id, date);
CREATE INDEX IF NOT EXISTS idx_scheduled_shifts_by_date ON scheduled_shifts(date);

-- Link checklists to sections
ALTER TABLE checklist_templates
    ADD COLUMN IF NOT EXISTS section_id UUID REFERENCES sections(id);

ALTER TABLE checklist_instances
    ADD COLUMN IF NOT EXISTS section_id UUID REFERENCES sections(id);

ALTER TABLE checklist_participants
    ADD COLUMN IF NOT EXISTS participant_section_id UUID REFERENCES sections(id);

CREATE INDEX IF NOT EXISTS idx_checklist_templates_section ON checklist_templates(section_id);
CREATE INDEX IF NOT EXISTS idx_checklist_instances_section ON checklist_instances(section_id);

-- Helpful view scoped by section
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

-- Indexes
CREATE INDEX IF NOT EXISTS idx_users_department ON users(department_id);
CREATE INDEX IF NOT EXISTS idx_users_section ON users(section_id);

COMMIT;

-- Data seeding for requested departments & sections
BEGIN;
INSERT INTO department (department_name) VALUES ('ICT') ON CONFLICT (department_name) DO NOTHING;
INSERT INTO department (department_name) VALUES ('Digital Transformation') ON CONFLICT (department_name) DO NOTHING;

INSERT INTO sections (section_name) VALUES
  ('SysOps'), ('SupportOps'), ('Infrastructure'), ('Helpdesk'), ('Information Security'),
  ('Digital Dev'), ('AI & Data'), ('Projects'), ('Issuing & Acquiring')
ON CONFLICT (LOWER(section_name)) DO NOTHING;

WITH d AS (
  SELECT id, department_name FROM department WHERE department_name IN ('ICT','Digital Transformation')
),
s AS (
  SELECT id, section_name FROM sections WHERE section_name IN
    ('SysOps','SupportOps','Infrastructure','Helpdesk','Information Security',
     'Digital Dev','AI & Data','Projects','Issuing & Acquiring')
)
INSERT INTO department_sections (department_id, section_id)
SELECT d.id, s.id
FROM d
CROSS JOIN s
WHERE
  (d.department_name = 'ICT' AND s.section_name IN ('SysOps','SupportOps','Infrastructure','Helpdesk','Information Security'))
  OR
  (d.department_name = 'Digital Transformation' AND s.section_name IN ('Digital Dev','AI & Data','Projects','Issuing & Acquiring'))
ON CONFLICT DO NOTHING;
COMMIT;
