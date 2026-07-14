BEGIN;

ALTER TABLE public.checklist_instance_items
ADD COLUMN IF NOT EXISTS created_at timestamptz;

UPDATE public.checklist_instance_items cii
SET created_at = COALESCE(
  (
    SELECT MIN(cia.created_at)
    FROM public.checklist_item_activity cia
    WHERE cia.instance_item_id = cii.id
  ),
  cii.started_at,
  cii.completed_at,
  now()
)
WHERE cii.created_at IS NULL;

ALTER TABLE public.checklist_instance_items
ALTER COLUMN created_at SET DEFAULT now();

ALTER TABLE public.checklist_instance_items
ALTER COLUMN created_at SET NOT NULL;

COMMIT;