-- 2026-02-08: Smart Shift Patterns & Days Off Management
-- Enables bulk scheduling, recurring patterns, and days off tracking

BEGIN;

-- Shift Patterns: Define recurring scheduling rules (e.g., "Mon-Fri Morning, Weekends Off")
CREATE TABLE IF NOT EXISTS shift_patterns (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    section_id UUID NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
    pattern_type TEXT NOT NULL CHECK (pattern_type IN ('FIXED', 'ROTATING', 'CUSTOM')),
    metadata JSONB NOT NULL DEFAULT '{}',
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Pattern Days: Define which shift applies on which days
-- Example: Pattern 1, Day 0 (Monday) → MORNING shift
CREATE TABLE IF NOT EXISTS shift_pattern_days (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    pattern_id UUID NOT NULL REFERENCES shift_patterns(id) ON DELETE CASCADE,
    day_of_week INTEGER NOT NULL CHECK (day_of_week BETWEEN 0 AND 6), -- 0=Sunday, 1=Monday, ..., 6=Saturday
    shift_id INTEGER REFERENCES shifts(id) ON DELETE SET NULL,
    is_off_day BOOLEAN DEFAULT FALSE,
    metadata JSONB,
    UNIQUE (pattern_id, day_of_week)
);

-- User Days Off: Track planned time off (vacation, sick leave, etc.)
CREATE TABLE IF NOT EXISTS user_days_off (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    reason TEXT,
    status TEXT DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'CANCELLED')),
    approved_by UUID REFERENCES users(id),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- User Shift Assignments: Link users to shift patterns + explicit assignments
CREATE TABLE IF NOT EXISTS user_shift_assignments (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    shift_pattern_id UUID REFERENCES shift_patterns(id) ON DELETE SET NULL,
    assigned_by UUID REFERENCES users(id),
    start_date DATE NOT NULL,
    end_date DATE, -- NULL means ongoing
    notes TEXT,
    status TEXT DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'INACTIVE', 'SUSPENDED')),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Exception Overrides: Allow individual date overrides for a pattern
CREATE TABLE IF NOT EXISTS shift_exceptions (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    exception_date DATE NOT NULL,
    shift_id INTEGER REFERENCES shifts(id) ON DELETE SET NULL,
    is_day_off BOOLEAN DEFAULT FALSE,
    reason TEXT,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (user_id, exception_date)
);

-- User Schedule View: Denormalized view for quick queries (materialized view)
-- This view combines patterns, assignments, days off, and exceptions for a user's schedule
CREATE OR REPLACE VIEW v_user_schedule_view AS
WITH user_assignments AS (
    SELECT 
        usa.user_id,
        usa.shift_pattern_id,
        usa.start_date,
        COALESCE(usa.end_date, CURRENT_DATE + INTERVAL '2 years') as end_date,
        usa.status
    FROM user_shift_assignments usa
    WHERE usa.status = 'ACTIVE'
)
SELECT DISTINCT
    u.id as user_id,
    u.first_name,
    u.last_name,
    u.email,
    generate_series(
        CURRENT_DATE,
        CURRENT_DATE + INTERVAL '90 days',
        INTERVAL '1 day'
    )::DATE as scheduled_date,
    EXTRACT(DOW FROM generate_series(
        CURRENT_DATE,
        CURRENT_DATE + INTERVAL '90 days',
        INTERVAL '1 day'
    ))::INTEGER as day_of_week
FROM users u
LEFT JOIN user_assignments ua ON u.id = ua.user_id
WHERE u.is_active = TRUE;

-- Enhanced scheduled_shifts with pattern tracking
ALTER TABLE scheduled_shifts
    ADD COLUMN IF NOT EXISTS pattern_id UUID REFERENCES shift_patterns(id),
    ADD COLUMN IF NOT EXISTS assignment_id UUID REFERENCES user_shift_assignments(id),
    ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS from_bulk_assign BOOLEAN DEFAULT FALSE;

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_shift_patterns_section ON shift_patterns(section_id);
CREATE INDEX IF NOT EXISTS idx_shift_pattern_days_pattern ON shift_pattern_days(pattern_id);
CREATE INDEX IF NOT EXISTS idx_user_days_off_dates ON user_days_off(user_id, start_date, end_date);
CREATE INDEX IF NOT EXISTS idx_user_shift_assignments_user ON user_shift_assignments(user_id);
CREATE INDEX IF NOT EXISTS idx_user_shift_assignments_pattern ON user_shift_assignments(shift_pattern_id);
CREATE INDEX IF NOT EXISTS idx_shift_exceptions_user_date ON shift_exceptions(user_id, exception_date);
CREATE INDEX IF NOT EXISTS idx_scheduled_shifts_pattern ON scheduled_shifts(pattern_id);
CREATE INDEX IF NOT EXISTS idx_scheduled_shifts_assignment ON scheduled_shifts(assignment_id);

-- Create system predefined patterns
INSERT INTO shift_patterns (name, description, section_id, pattern_type, created_by, metadata)
VALUES 
    (
        'Standard Weekday Morning',
        'Monday-Friday Morning (07:00-15:00), Weekends Off',
        (SELECT id FROM sections LIMIT 1),
        'FIXED',
        NULL,
        '{"shift_type": "WEEKDAY_MORNING", "display_color": "#00f2ff"}'::JSONB
    ),
    (
        'Standard Rotating 3-Shift',
        'Rotates through MORNING → AFTERNOON → NIGHT every 3 days',
        (SELECT id FROM sections LIMIT 1),
        'ROTATING',
        NULL,
        '{"shift_type": "ROTATING_3SHIFT", "cycle_days": 3, "display_color": "#00ff88"}'::JSONB
    ),
    (
        'Weekend Night Coverage',
        'Friday Night, Saturday Night, Sunday Night (23:00-07:00)',
        (SELECT id FROM sections LIMIT 1),
        'FIXED',
        NULL,
        '{"shift_type": "WEEKEND_NIGHT", "display_color": "#ff00ff"}'::JSONB
    )
ON CONFLICT DO NOTHING;

-- Populate shift_pattern_days for predefined patterns so they show schedule immediately
-- Standard Weekday Morning: Mon-Fri Morning, Sat-Sun Off
INSERT INTO shift_pattern_days (pattern_id, day_of_week, shift_id, is_off_day)
SELECT sp.id, days.day_of_week, days.shift_id, days.is_off_day
FROM (VALUES
    (1, (SELECT id FROM shifts WHERE LOWER(name) = 'morning'), FALSE),
    (2, (SELECT id FROM shifts WHERE LOWER(name) = 'morning'), FALSE),
    (3, (SELECT id FROM shifts WHERE LOWER(name) = 'morning'), FALSE),
    (4, (SELECT id FROM shifts WHERE LOWER(name) = 'morning'), FALSE),
    (5, (SELECT id FROM shifts WHERE LOWER(name) = 'morning'), FALSE),
    (6, NULL::INTEGER, TRUE),
    (0, NULL::INTEGER, TRUE)
) AS days(day_of_week, shift_id, is_off_day)
CROSS JOIN shift_patterns sp
WHERE sp.name = 'Standard Weekday Morning'
ON CONFLICT (pattern_id, day_of_week) DO NOTHING;

-- Standard Rotating 3-Shift
INSERT INTO shift_pattern_days (pattern_id, day_of_week, shift_id, is_off_day)
SELECT sp.id, days.day_of_week, days.shift_id, days.is_off_day
FROM (VALUES
    (1, (SELECT id FROM shifts WHERE LOWER(name) = 'morning'), FALSE),
    (2, (SELECT id FROM shifts WHERE LOWER(name) = 'afternoon'), FALSE),
    (3, (SELECT id FROM shifts WHERE LOWER(name) = 'night'), FALSE),
    (4, (SELECT id FROM shifts WHERE LOWER(name) = 'morning'), FALSE),
    (5, (SELECT id FROM shifts WHERE LOWER(name) = 'afternoon'), FALSE),
    (6, (SELECT id FROM shifts WHERE LOWER(name) = 'night'), FALSE),
    (0, NULL::INTEGER, TRUE)
) AS days(day_of_week, shift_id, is_off_day)
CROSS JOIN shift_patterns sp
WHERE sp.name = 'Standard Rotating 3-Shift'
ON CONFLICT (pattern_id, day_of_week) DO NOTHING;

-- Weekend Night Coverage: Fri/Sat/Sun Night, Mon-Thu Off
INSERT INTO shift_pattern_days (pattern_id, day_of_week, shift_id, is_off_day)
SELECT sp.id, days.day_of_week, days.shift_id, days.is_off_day
FROM (VALUES
    (1, NULL::INTEGER, TRUE),
    (2, NULL::INTEGER, TRUE),
    (3, NULL::INTEGER, TRUE),
    (4, NULL::INTEGER, TRUE),
    (5, (SELECT id FROM shifts WHERE LOWER(name) = 'night'), FALSE),
    (6, (SELECT id FROM shifts WHERE LOWER(name) = 'night'), FALSE),
    (0, (SELECT id FROM shifts WHERE LOWER(name) = 'night'), FALSE)
) AS days(day_of_week, shift_id, is_off_day)
CROSS JOIN shift_patterns sp
WHERE sp.name = 'Weekend Night Coverage'
ON CONFLICT (pattern_id, day_of_week) DO NOTHING;

COMMIT;

-- Summary of enhancements:
-- ✨ shift_patterns: Define recurring schedules (weekday morning, rotating, etc.)
-- ✨ shift_pattern_days: Map which shift applies on which day of week
-- ✨ user_days_off: Track vacation, sick leave, personal time
-- ✨ user_shift_assignments: Link users to patterns for a date range
-- ✨ shift_exceptions: Override individual dates (e.g., "John off on 2/14")
-- ✨ Intelligent scheduler can auto-generate bulk assignments
