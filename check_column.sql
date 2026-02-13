-- Check if created_at column exists in checklist_instances table
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'checklist_instances' AND column_name = 'created_at';

-- If it doesn't exist, add it
ALTER TABLE checklist_instances ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now();
