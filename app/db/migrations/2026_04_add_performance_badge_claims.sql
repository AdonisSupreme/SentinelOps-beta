-- Persist claimed performance badges so unlocked milestones can be collected.

CREATE TABLE IF NOT EXISTS user_performance_badge_claims (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    badge_key TEXT NOT NULL,
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, badge_key)
);

CREATE INDEX IF NOT EXISTS idx_user_performance_badge_claims_user_claimed
    ON user_performance_badge_claims (user_id, claimed_at DESC);

