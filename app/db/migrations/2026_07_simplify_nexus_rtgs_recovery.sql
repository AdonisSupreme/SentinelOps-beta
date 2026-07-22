-- Remove the retired peer/file-evidence contract from the RTGS ledgers.
-- Historical JSON payloads and action rows remain immutable audit records.

ALTER TABLE nexus_rtgs_case
    DROP COLUMN IF EXISTS file_state;

-- Older installations may contain copy action history. Keep those audit rows,
-- while the application contract now emits regeneration actions only.
ALTER TABLE nexus_rtgs_action
    DROP CONSTRAINT IF EXISTS nexus_rtgs_action_action_check;
