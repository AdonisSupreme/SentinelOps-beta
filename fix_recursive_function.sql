-- Fix recursive function issue
-- This script identifies and removes problematic functions/triggers

-- 1. Check for any functions that might cause recursion
SELECT 
    proname as function_name,
    prosrc as source_code
FROM pg_proc 
WHERE proname LIKE '%checklist%' OR proname LIKE '%shift%';

-- 2. Check for triggers on checklist_instances table
SELECT 
    tgname as trigger_name,
    tgrelid::regclass as table_name,
    tgfoid::regproc as function_name
FROM pg_trigger 
WHERE tgrelid = 'checklist_instances'::regclass;

-- 3. Drop any problematic functions (UNCOMMENT AFTER VERIFYING)
-- DROP FUNCTION IF EXISTS create_shift_checklist_instance() CASCADE;

-- 4. Drop any problematic triggers on checklist_instances (UNCOMMENT AFTER VERIFYING)  
-- DROP TRIGGER IF EXISTS problematic_trigger_name ON checklist_instances;

-- 5. Check current checklist_instances table structure
\d checklist_instances
