-- 2026-02-08: Initialize standard shifts (MORNING, AFTERNOON, NIGHT)
-- This ensures shifts exist for scheduler and creates the mapping needed for checklist instances

-- Delete existing shifts first (clean slate)
DELETE FROM shifts WHERE LOWER(name) IN ('morning', 'afternoon', 'night');

-- Insert MORNING shift
INSERT INTO shifts (name, start_time, end_time, timezone, color, metadata) 
VALUES ('MORNING', '07:00', '15:00', 'UTC', '#00f2ff', '{"shift_type": "MORNING", "description": "Morning Shift", "display_order": 1}');

-- Insert AFTERNOON shift
INSERT INTO shifts (name, start_time, end_time, timezone, color, metadata) 
VALUES ('AFTERNOON', '15:00', '23:00', 'UTC', '#00ff88', '{"shift_type": "AFTERNOON", "description": "Afternoon Shift", "display_order": 2}');

-- Insert NIGHT shift
INSERT INTO shifts (name, start_time, end_time, timezone, color, metadata) 
VALUES ('NIGHT', '23:00', '07:00', 'UTC', '#ff00ff', '{"shift_type": "NIGHT", "description": "Night Shift", "display_order": 3}');

-- Add a helpful index for joining scheduled_shifts to checklist instances by date
CREATE INDEX IF NOT EXISTS idx_scheduled_shifts_date_shift_id ON scheduled_shifts(date, shift_id);
