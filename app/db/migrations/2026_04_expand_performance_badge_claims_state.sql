-- Persist unlock state and notification timestamps for performance badges.

ALTER TABLE IF EXISTS user_performance_badge_claims
    ADD COLUMN IF NOT EXISTS unlocked_at TIMESTAMPTZ;

ALTER TABLE IF EXISTS user_performance_badge_claims
    ADD COLUMN IF NOT EXISTS unlock_notified_at TIMESTAMPTZ;

ALTER TABLE IF EXISTS user_performance_badge_claims
    ALTER COLUMN claimed_at DROP NOT NULL;

ALTER TABLE IF EXISTS user_performance_badge_claims
    ALTER COLUMN claimed_at DROP DEFAULT;

UPDATE user_performance_badge_claims
SET unlocked_at = COALESCE(unlocked_at, claimed_at)
WHERE claimed_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_user_performance_badge_claims_user_unlock_state
    ON user_performance_badge_claims (user_id, unlocked_at DESC, claimed_at DESC);

