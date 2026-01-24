-- Migration: Replace template_item_id with template_item_key
-- This migration transitions from database-based template items to file-based templates

-- Step 1: Add the new template_item_key column
ALTER TABLE checklist_instance_items 
ADD COLUMN template_item_key TEXT;

-- Step 2: Populate template_item_key from existing template_item_id
-- This assumes we have the template_item_id -> key mapping available
-- For now, we'll create a temporary mapping based on existing data

-- Create a temporary mapping table
CREATE TEMP TABLE template_item_key_mapping AS
SELECT 
    cti.id as template_item_id,
    cti.title,
    -- Create a key from title (lowercase, replace spaces with underscores)
    lower(regexp_replace(cti.title, '[^a-zA-Z0-9\s]', '', 'g')) as generated_key
FROM checklist_template_items cti;

-- Update existing instance items with generated keys
UPDATE checklist_instance_items cii
SET template_item_key = (
    SELECT generated_key 
    FROM template_item_key_mapping tim 
    WHERE tim.template_item_id = cii.template_item_id
)
WHERE template_item_key IS NULL;

-- Step 3: Make the new column NOT NULL after ensuring all rows have values
-- First, handle any NULL values that might remain
UPDATE checklist_instance_items 
SET template_item_key = 'unknown_item_' || id::text
WHERE template_item_key IS NULL;

-- Now make it NOT NULL
ALTER TABLE checklist_instance_items 
ALTER COLUMN template_item_key SET NOT NULL;

-- Step 4: Drop the old foreign key constraint
ALTER TABLE checklist_instance_items 
DROP CONSTRAINT IF EXISTS checklist_instance_items_template_item_id_fkey;

-- Step 5: Drop the old column
ALTER TABLE checklist_instance_items 
DROP COLUMN template_item_id;

-- Step 6: Add index on the new column for performance
CREATE INDEX idx_checklist_instance_items_template_key 
ON checklist_instance_items(template_item_key);

-- Step 7: Update unique constraint to use the new column
ALTER TABLE checklist_instance_items 
DROP CONSTRAINT IF EXISTS checklist_instance_items_instance_id_template_item_id_key;

ALTER TABLE checklist_instance_items 
ADD CONSTRAINT checklist_instance_items_instance_item_key_unique 
UNIQUE (instance_id, template_item_key);

-- Note: The checklist_template_items table can now be deprecated
-- It should not be used for new operations
