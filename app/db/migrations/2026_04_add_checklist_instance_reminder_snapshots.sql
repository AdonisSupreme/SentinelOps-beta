-- 2026-04-04: Freeze reminder-driving fields on checklist instances
-- This makes reminder behavior immutable once a shift instance is created.

ALTER TABLE checklist_instance_items
ADD COLUMN IF NOT EXISTS scheduled_time TIME WITHOUT TIME ZONE,
ADD COLUMN IF NOT EXISTS notify_before_minutes INTEGER,
ADD COLUMN IF NOT EXISTS scheduled_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS remind_at TIMESTAMPTZ;

ALTER TABLE checklist_instance_subitems
ADD COLUMN IF NOT EXISTS scheduled_time TIME WITHOUT TIME ZONE,
ADD COLUMN IF NOT EXISTS notify_before_minutes INTEGER,
ADD COLUMN IF NOT EXISTS scheduled_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS remind_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS checklist_instance_scheduled_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    instance_item_id UUID NOT NULL REFERENCES checklist_instance_items(id) ON DELETE CASCADE,
    template_event_id UUID REFERENCES checklist_scheduled_events(id) ON DELETE SET NULL,
    event_datetime TIMESTAMPTZ NOT NULL,
    notify_before_minutes INTEGER NOT NULL DEFAULT 30,
    remind_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_instance_items_remind_at
    ON checklist_instance_items (remind_at)
    WHERE remind_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_instance_subitems_remind_at
    ON checklist_instance_subitems (remind_at)
    WHERE remind_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_instance_scheduled_events_remind_at
    ON checklist_instance_scheduled_events (remind_at);

CREATE INDEX IF NOT EXISTS idx_instance_scheduled_events_instance_item
    ON checklist_instance_scheduled_events (instance_item_id);

WITH item_snapshots AS (
    SELECT
        cii.id AS instance_item_id,
        cti.scheduled_time,
        cti.notify_before_minutes,
        CASE
            WHEN cti.scheduled_time IS NULL THEN NULL
            WHEN ci.shift_end > ci.shift_start
                 AND cti.scheduled_time < (ci.shift_start AT TIME ZONE 'UTC')::time
                THEN date_trunc('day', ci.shift_start) + interval '1 day' + (cti.scheduled_time - time '00:00')
            ELSE date_trunc('day', ci.shift_start) + (cti.scheduled_time - time '00:00')
        END AS resolved_scheduled_at
    FROM checklist_instance_items cii
    JOIN checklist_instances ci ON ci.id = cii.instance_id
    JOIN checklist_template_items cti ON cti.id = cii.template_item_id
)
UPDATE checklist_instance_items cii
SET scheduled_time = snapshot.scheduled_time,
    notify_before_minutes = snapshot.notify_before_minutes,
    scheduled_at = snapshot.resolved_scheduled_at,
    remind_at = CASE
        WHEN snapshot.resolved_scheduled_at IS NULL THEN NULL
        ELSE snapshot.resolved_scheduled_at - (COALESCE(snapshot.notify_before_minutes, 0) * interval '1 minute')
    END
FROM item_snapshots snapshot
WHERE snapshot.instance_item_id = cii.id
  AND snapshot.scheduled_time IS NOT NULL
  AND (
      cii.scheduled_time IS NULL
      OR cii.notify_before_minutes IS NULL
      OR cii.scheduled_at IS NULL
      OR cii.remind_at IS NULL
  );

WITH subitem_snapshots AS (
    SELECT
        cis.id AS instance_subitem_id,
        cts.scheduled_time,
        cts.notify_before_minutes,
        CASE
            WHEN cts.scheduled_time IS NULL THEN NULL
            WHEN ci.shift_end > ci.shift_start
                 AND cts.scheduled_time < (ci.shift_start AT TIME ZONE 'UTC')::time
                THEN date_trunc('day', ci.shift_start) + interval '1 day' + (cts.scheduled_time - time '00:00')
            ELSE date_trunc('day', ci.shift_start) + (cts.scheduled_time - time '00:00')
        END AS resolved_scheduled_at
    FROM checklist_instance_subitems cis
    JOIN checklist_instance_items cii ON cii.id = cis.instance_item_id
    JOIN checklist_instances ci ON ci.id = cii.instance_id
    JOIN checklist_template_subitems cts
      ON cts.template_item_id = cii.template_item_id
     AND cts.sort_order = cis.sort_order
)
UPDATE checklist_instance_subitems cis
SET scheduled_time = snapshot.scheduled_time,
    notify_before_minutes = snapshot.notify_before_minutes,
    scheduled_at = snapshot.resolved_scheduled_at,
    remind_at = CASE
        WHEN snapshot.resolved_scheduled_at IS NULL THEN NULL
        ELSE snapshot.resolved_scheduled_at - (COALESCE(snapshot.notify_before_minutes, 0) * interval '1 minute')
    END
FROM subitem_snapshots snapshot
WHERE snapshot.instance_subitem_id = cis.id
  AND snapshot.scheduled_time IS NOT NULL
  AND (
      cis.scheduled_time IS NULL
      OR cis.notify_before_minutes IS NULL
      OR cis.scheduled_at IS NULL
      OR cis.remind_at IS NULL
  );

INSERT INTO checklist_instance_scheduled_events (
    id,
    instance_item_id,
    template_event_id,
    event_datetime,
    notify_before_minutes,
    remind_at,
    created_at
)
SELECT
    gen_random_uuid(),
    cii.id,
    cse.id,
    cse.event_datetime,
    COALESCE(cse.notify_before_minutes, 30),
    cse.event_datetime - (COALESCE(cse.notify_before_minutes, 30) * interval '1 minute'),
    now()
FROM checklist_instance_items cii
JOIN checklist_scheduled_events cse ON cse.template_item_id = cii.template_item_id
LEFT JOIN checklist_instance_scheduled_events cise
  ON cise.instance_item_id = cii.id
 AND (
     cise.template_event_id = cse.id
     OR (
         cise.template_event_id IS NULL
         AND cise.event_datetime = cse.event_datetime
         AND cise.notify_before_minutes = COALESCE(cse.notify_before_minutes, 30)
     )
 )
WHERE cise.id IS NULL;
