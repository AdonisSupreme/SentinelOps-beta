CREATE INDEX IF NOT EXISTS idx_checklist_instances_operational_day
    ON checklist_instances(checklist_date, section_id, shift);

CREATE INDEX IF NOT EXISTS idx_checklist_participants_instance_user
    ON checklist_participants(instance_id, user_id);

CREATE INDEX IF NOT EXISTS idx_handover_notes_from_instance
    ON handover_notes(from_instance_id);

CREATE INDEX IF NOT EXISTS idx_notifications_user_unread
    ON notifications(user_id)
    WHERE is_read = FALSE;

CREATE INDEX IF NOT EXISTS idx_notifications_role_unread
    ON notifications(role_id)
    WHERE is_read = FALSE;
