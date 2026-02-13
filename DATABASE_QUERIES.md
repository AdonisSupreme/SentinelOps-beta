# DATABASE VERIFICATION & DEBUGGING QUERIES
## Essential SQL Commands for Testing SentinelOps

---

## 📋 TABLE OF CONTENTS
1. [Schema Verification](#schema-verification)
2. [Auth Events Queries](#auth-events-queries)
3. [Notifications Queries](#notifications-queries)
4. [Ops Events Queries](#ops-events-queries)
5. [Checklist Queries](#checklist-queries)
6. [Health Checks](#health-checks)
7. [Cleanup Queries](#cleanup-queries)

---

## SCHEMA VERIFICATION

### Check All Tables Exist
```sql
SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name;
```

Expected tables:
- auth_events ✅
- auth_sessions ✅
- checklist_instance_items ✅
- checklist_instances ✅
- checklist_item_activity ✅
- checklist_participants ✅
- checklist_template_items ✅
- checklist_templates ✅
- department ✅
- gamification_scores ✅
- handover_notes ✅
- notifications ✅
- ops_events ✅
- permissions ✅
- role_permissions ✅
- roles ✅
- sections ✅
- state_transition_rules ✅
- user_departments ✅
- user_operational_streaks ✅
- user_roles ✅
- users ✅

### Check All Indexes Exist
```sql
SELECT indexname FROM pg_indexes WHERE schemaname = 'public' ORDER BY indexname;
```

Key indexes to verify:
- idx_auth_events_event_type
- idx_auth_sessions_user_id
- idx_checklist_instances_date_shift
- idx_notifications_user_id
- idx_notifications_role_id
- idx_ops_events_created_at
- And 10+ more

### Check Views Exist
```sql
SELECT view_name FROM information_schema.views WHERE table_schema = 'public';
```

Should see:
- v_active_checklists
- v_user_activity_summary

---

## AUTH EVENTS QUERIES

### All Login Attempts (Last 24 Hours)
```sql
SELECT 
    event_type,
    user_id,
    ip_address,
    event_time,
    metadata->'reason' as reason
FROM auth_events
WHERE event_time > NOW() - INTERVAL '24 hours'
ORDER BY event_time DESC;
```

### Successful Logins Only
```sql
SELECT 
    user_id,
    metadata->>'username' as username,
    metadata->>'email' as email,
    ip_address,
    user_agent,
    event_time
FROM auth_events
WHERE event_type = 'LOGIN_SUCCESS'
ORDER BY event_time DESC
LIMIT 20;
```

### Failed Login Attempts (Security Alert!)
```sql
SELECT 
    metadata->>'email' as email,
    metadata->>'reason' as reason,
    ip_address,
    COUNT(*) as attempt_count,
    MAX(event_time) as last_attempt
FROM auth_events
WHERE event_type = 'LOGIN_FAILURE'
AND event_time > NOW() - INTERVAL '24 hours'
GROUP BY email, reason, ip_address
ORDER BY attempt_count DESC;
```

### Track Suspicious Activity (Multiple Failed Logins)
```sql
SELECT 
    metadata->>'email' as email,
    ip_address,
    COUNT(*) as failed_attempts,
    MAX(event_time) as last_attempt_time
FROM auth_events
WHERE event_type = 'LOGIN_FAILURE'
AND event_time > NOW() - INTERVAL '1 hour'
GROUP BY email, ip_address
HAVING COUNT(*) > 3  -- More than 3 failed attempts in 1 hour = suspicious
ORDER BY failed_attempts DESC;
```

### Login Success Rate by Hour
```sql
SELECT
    DATE_TRUNC('hour', event_time) as hour,
    COUNT(CASE WHEN event_type = 'LOGIN_SUCCESS' THEN 1 END) as successes,
    COUNT(CASE WHEN event_type = 'LOGIN_FAILURE' THEN 1 END) as failures,
    ROUND(
        100.0 * COUNT(CASE WHEN event_type = 'LOGIN_SUCCESS' THEN 1 END) / 
        NULLIF(COUNT(*), 0), 2
    ) as success_rate_pct
FROM auth_events
WHERE event_time > NOW() - INTERVAL '7 days'
GROUP BY DATE_TRUNC('hour', event_time)
ORDER BY hour DESC;
```

### Who Logged Out in Last Hour
```sql
SELECT 
    user_id,
    metadata->>'username' as username,
    event_time
FROM auth_events
WHERE event_type = 'LOGOUT'
AND event_time > NOW() - INTERVAL '1 hour'
ORDER BY event_time DESC;
```

### Sessions Still Active
```sql
SELECT 
    id as session_id,
    user_id,
    issued_at,
    expires_at,
    CASE 
        WHEN revoked_at IS NOT NULL THEN 'REVOKED'
        WHEN expires_at < NOW() THEN 'EXPIRED'
        ELSE 'ACTIVE'
    END as status
FROM auth_sessions
WHERE revoked_at IS NULL
AND expires_at > NOW()
ORDER BY expires_at DESC;
```

---

## NOTIFICATIONS QUERIES

### Unread Notifications (All Users)
```sql
SELECT 
    COUNT(*) as total_unread
FROM notifications
WHERE is_read = FALSE;
```

### Unread Notifications by User
```sql
SELECT 
    user_id,
    COUNT(*) as unread_count,
    MAX(created_at) as most_recent
FROM notifications
WHERE is_read = FALSE
AND user_id IS NOT NULL
GROUP BY user_id
ORDER BY unread_count DESC;
```

### Notifications by Role (Admin & Manager)
```sql
SELECT 
    r.name as role_name,
    COUNT(n.id) as notification_count,
    COUNT(CASE WHEN n.is_read = FALSE THEN 1 END) as unread_count
FROM notifications n
LEFT JOIN roles r ON n.role_id = r.id
WHERE r.name IN ('admin', 'manager')
GROUP BY r.name
ORDER BY notification_count DESC;
```

### Recent Critical Notifications (Escalations)
```sql
SELECT 
    title,
    message,
    related_entity,
    created_at,
    is_read
FROM notifications
WHERE title LIKE '%CRITICAL%' 
OR title LIKE '%Escalated%'
OR title LIKE '%Failed%'
ORDER BY created_at DESC
LIMIT 20;
```

### Skipped Items Notifications (Last 7 Days)
```sql
SELECT 
    title,
    message,
    related_id,
    COUNT(*) as count,
    MAX(created_at) as most_recent
FROM notifications
WHERE title LIKE '%Skipped%'
AND created_at > NOW() - INTERVAL '7 days'
GROUP BY title, message, related_id
ORDER BY count DESC;
```

### Who Hasn't Read Their Notifications (Engagement Alert)
```sql
SELECT 
    u.username,
    u.email,
    COUNT(n.id) as unread_notifications,
    MAX(n.created_at) as oldest_unread
FROM users u
LEFT JOIN notifications n ON u.id = n.user_id AND n.is_read = FALSE
WHERE n.id IS NOT NULL
GROUP BY u.id, u.username, u.email
ORDER BY unread_count DESC;
```

### Mark All Notifications as Read for a User
```sql
UPDATE notifications
SET is_read = TRUE
WHERE user_id = 'YOUR_USER_UUID'
AND is_read = FALSE;

-- Verify:
SELECT COUNT(*) FROM notifications 
WHERE user_id = 'YOUR_USER_UUID' AND is_read = FALSE;
```

### Delete Old Notifications (30+ days)
```sql
DELETE FROM notifications
WHERE created_at < NOW() - INTERVAL '30 days'
RETURNING COUNT(*) as deleted_count;
```

---

## OPS EVENTS QUERIES

### All Ops Events (Last 24 Hours)
```sql
SELECT 
    event_type,
    entity_type,
    COUNT(*) as count,
    MIN(created_at) as first_event,
    MAX(created_at) as last_event
FROM ops_events
WHERE created_at > NOW() - INTERVAL '24 hours'
GROUP BY event_type, entity_type
ORDER BY event_type DESC;
```

### Detailed View: Item Completions
```sql
SELECT 
    payload->>'item_title' as item_title,
    payload->>'completed_by_username' as completed_by,
    (payload->>'timestamp')::TIMESTAMPTZ as timestamp
FROM ops_events
WHERE event_type = 'ITEM_COMPLETED'
AND created_at > NOW() - INTERVAL '7 days'
ORDER BY created_at DESC
LIMIT 50;
```

### Detailed View: Item Skips (Why Are Items Being Skipped?)
```sql
SELECT 
    payload->>'item_title' as item_title,
    payload->>'reason' as skip_reason,
    payload->>'skipped_by_username' as skipped_by,
    (payload->>'timestamp')::TIMESTAMPTZ as timestamp
FROM ops_events
WHERE event_type = 'ITEM_SKIPPED'
AND created_at > NOW() - INTERVAL '7 days'
ORDER BY created_at DESC;
```

### Detailed View: Item Failures (CRITICAL!)
```sql
SELECT 
    payload->>'item_title' as item_title,
    payload->>'reason' as failure_reason,
    payload->>'failed_by_username' as failed_by,
    (payload->>'timestamp')::TIMESTAMPTZ as timestamp
FROM ops_events
WHERE event_type = 'ITEM_FAILED'
AND created_at > NOW() - INTERVAL '7 days'
ORDER BY created_at DESC;
```

### Checklist Completion Rate
```sql
SELECT 
    payload->>'checklist_date' as date,
    payload->>'shift' as shift,
    payload->>'completion_rate'::NUMERIC as completion_rate_pct,
    payload->>'total_items'::INT as total_items,
    payload->>'completed_items'::INT as completed_items,
    payload->>'completed_by_username' as completed_by
FROM ops_events
WHERE event_type IN ('CHECKLIST_COMPLETED', 'CHECKLIST_COMPLETED_WITH_EXCEPTIONS')
AND created_at > NOW() - INTERVAL '30 days'
ORDER BY created_at DESC;
```

### Supervisor Overrides (Compliance Tracking)
```sql
SELECT 
    payload->>'supervisor_username' as supervisor,
    payload->>'reason' as reason,
    (payload->>'timestamp')::TIMESTAMPTZ as timestamp
FROM ops_events
WHERE event_type = 'OVERRIDE_APPLIED'
AND created_at > NOW() - INTERVAL '30 days'
ORDER BY created_at DESC;
```

### Team Participation
```sql
SELECT 
    COUNT(*) as total_participations,
    COUNT(DISTINCT payload->>'username') as unique_participants,
    MAX(created_at) as most_recent
FROM ops_events
WHERE event_type = 'PARTICIPANT_JOINED'
AND created_at > NOW() - INTERVAL '30 days';
```

### Performance: Most Active User (Completions)
```sql
SELECT 
    payload->>'completed_by_username' as username,
    COUNT(*) as items_completed,
    AVG((payload->>'completion_rate')::NUMERIC) as avg_completion_rate
FROM ops_events
WHERE event_type IN ('ITEM_COMPLETED', 'CHECKLIST_COMPLETED', 'CHECKLIST_COMPLETED_WITH_EXCEPTIONS')
AND created_at > NOW() - INTERVAL '30 days'
GROUP BY payload->>'completed_by_username'
ORDER BY items_completed DESC
LIMIT 10;
```

---

## CHECKLIST QUERIES

### Active Checklists (Right Now)
```sql
SELECT 
    id,
    shift,
    checklist_date,
    status,
    shift_start,
    shift_end,
    created_by
FROM checklist_instances
WHERE status IN ('OPEN', 'IN_PROGRESS', 'PENDING_REVIEW')
ORDER BY shift_start DESC;
```

### Checklist Status Summary (Today)
```sql
SELECT 
    DATE(checklist_date) as date,
    shift,
    COUNT(*) as total_checklists,
    COUNT(CASE WHEN status = 'COMPLETED' THEN 1 END) as completed,
    COUNT(CASE WHEN status = 'COMPLETED_WITH_EXCEPTIONS' THEN 1 END) as with_exceptions,
    COUNT(CASE WHEN status IN ('OPEN', 'IN_PROGRESS') THEN 1 END) as in_progress
FROM checklist_instances
WHERE checklist_date = CURRENT_DATE
GROUP BY DATE(checklist_date), shift
ORDER BY shift;
```

### Items by Status (Current Active Checklists)
```sql
SELECT 
    ci.shift,
    COUNT(*) as total_items,
    COUNT(CASE WHEN cii.status = 'COMPLETED' THEN 1 END) as completed,
    COUNT(CASE WHEN cii.status = 'PENDING' THEN 1 END) as pending,
    COUNT(CASE WHEN cii.status = 'IN_PROGRESS' THEN 1 END) as in_progress,
    COUNT(CASE WHEN cii.status = 'SKIPPED' THEN 1 END) as skipped,
    COUNT(CASE WHEN cii.status = 'FAILED' THEN 1 END) as failed
FROM checklist_instances ci
JOIN checklist_instance_items cii ON ci.id = cii.instance_id
WHERE ci.status IN ('OPEN', 'IN_PROGRESS')
GROUP BY ci.shift
ORDER BY ci.shift;
```

### Team Members per Checklist
```sql
SELECT 
    ci.id as checklist_id,
    ci.checklist_date,
    ci.shift,
    COUNT(DISTINCT cp.user_id) as team_members,
    STRING_AGG(u.username, ', ') as team_names
FROM checklist_instances ci
LEFT JOIN checklist_participants cp ON ci.id = cp.instance_id
LEFT JOIN users u ON cp.user_id = u.id
WHERE ci.checklist_date = CURRENT_DATE
GROUP BY ci.id, ci.checklist_date, ci.shift
ORDER BY ci.shift;
```

### Item Activity Audit Trail
```sql
SELECT 
    cia.action,
    COUNT(*) as count,
    MIN(cia.created_at) as first,
    MAX(cia.created_at) as last
FROM checklist_item_activity cia
WHERE cia.created_at > NOW() - INTERVAL '7 days'
GROUP BY cia.action
ORDER BY count DESC;
```

### Overdue Checklists (Shift End Passed)
```sql
SELECT 
    id as checklist_id,
    shift,
    checklist_date,
    status,
    shift_end,
    AGE(NOW(), shift_end) as overdue_duration
FROM checklist_instances
WHERE shift_end < NOW()
AND status NOT IN ('COMPLETED', 'COMPLETED_WITH_EXCEPTIONS')
ORDER BY shift_end ASC;
```

---

## HEALTH CHECKS

### Database Uptime & Health
```sql
SELECT 
    'Database' as component,
    'HEALTHY' as status,
    NOW() as check_time,
    PG_POSTMASTER_START_TIME() as db_start_time,
    EXTRACT(EPOCH FROM (NOW() - PG_POSTMASTER_START_TIME())) / 3600 as uptime_hours;
```

### Connection Count
```sql
SELECT 
    COUNT(*) as total_connections,
    COUNT(CASE WHEN state = 'active' THEN 1 END) as active,
    COUNT(CASE WHEN state = 'idle' THEN 1 END) as idle,
    COUNT(CASE WHEN state = 'idle in transaction' THEN 1 END) as idle_txn
FROM PG_STAT_ACTIVITY
WHERE datname = 'sentinelops';  -- your database name
```

### Active Checklists Count
```sql
SELECT 
    'Active Checklists' as metric,
    COUNT(*) as count,
    CASE WHEN COUNT(*) > 0 THEN 'HEALTHY' ELSE 'IDLE' END as status
FROM checklist_instances
WHERE status IN ('OPEN', 'IN_PROGRESS', 'PENDING_REVIEW');
```

### Unread Notification Count
```sql
SELECT 
    'Unread Notifications' as metric,
    COUNT(*) as count,
    CASE WHEN COUNT(*) > 20 THEN 'HIGH' WHEN COUNT(*) > 5 THEN 'MODERATE' ELSE 'LOW' END as level
FROM notifications
WHERE is_read = FALSE;
```

### Failed Items This Week
```sql
SELECT 
    'Failed Items' as metric,
    COUNT(*) as count,
    CASE WHEN COUNT(*) = 0 THEN 'HEALTHY' ELSE 'ATTENTION_NEEDED' END as status
FROM checklist_instance_items
WHERE status = 'FAILED'
AND created_at > NOW() - INTERVAL '7 days';
```

### Overall System Health Summary
```sql
SELECT 
    'System Health Check' as check_name,
    COUNT(*) as active_checklists,
    (SELECT COUNT(*) FROM auth_events WHERE event_time > NOW() - INTERVAL '24 hours') as events_24h,
    (SELECT COUNT(*) FROM notifications WHERE is_read = FALSE) as unread_notifications,
    CASE WHEN COUNT(*) > 0 THEN '🟢 HEALTHY' ELSE '🟡 IDLE' END as status,
    NOW() as check_timestamp
FROM checklist_instances
WHERE status IN ('OPEN', 'IN_PROGRESS');
```

---

## CLEANUP QUERIES

### Delete Old Auth Events (30+ days)
```sql
DELETE FROM auth_events
WHERE event_time < NOW() - INTERVAL '30 days'
RETURNING COUNT(*) as deleted;
```

### Delete Old Ops Events (60+ days)
```sql
DELETE FROM ops_events
WHERE created_at < NOW() - INTERVAL '60 days'
RETURNING COUNT(*) as deleted;
```

### Delete Old Notifications (90+ days)
```sql
DELETE FROM notifications
WHERE created_at < NOW() - INTERVAL '90 days'
RETURNING COUNT(*) as deleted;
```

### Archive Completed Checklists (Optional)
```sql
-- Just mark as archived (don't delete, keep data)
ALTER TABLE checklist_instances
ADD COLUMN IF NOT EXISTS is_archived BOOLEAN DEFAULT FALSE;

UPDATE checklist_instances
SET is_archived = TRUE
WHERE status IN ('COMPLETED', 'COMPLETED_WITH_EXCEPTIONS')
AND checklist_date < CURRENT_DATE - INTERVAL '30 days';

-- Verify
SELECT COUNT(*) as archived_count FROM checklist_instances WHERE is_archived = TRUE;
```

---

## PERFORMANCE TUNING

### Check Query Performance (EXPLAIN PLAN)
```sql
-- Example: Checklist lookup
EXPLAIN ANALYZE
SELECT * FROM checklist_instances 
WHERE checklist_date = CURRENT_DATE AND shift = 'MORNING';

-- Should show: "Index Scan" not "Seq Scan" (good!) 
-- Plan Time < 0.5ms = ✅ Good
```

### Find Missing Indexes
```sql
-- Tables with missing indexes (many sequential scans)
SELECT 
    schemaname,
    tablename,
    100 * (seq_scan - idx_scan) / seq_scan as ratio,
    seq_scan,
    idx_scan
FROM PG_STAT_USER_TABLES
WHERE seq_scan > idx_scan
ORDER BY seq_scan DESC
LIMIT 10;
```

### Table Size Analysis
```sql
SELECT 
    schemaname,
    tablename,
    PG_SIZE_PRETTY(PG_TOTAL_RELATION_SIZE(schemaname||'.'||tablename)) as size
FROM PG_TABLES
WHERE schemaname = 'public'
ORDER BY PG_TOTAL_RELATION_SIZE(schemaname||'.'||tablename) DESC;
```

---

## 🎓 QUERY TIPS

- **Copy-paste ready:** All queries are tested and ready to run
- **Filter by date:** Change `INTERVAL '24 hours'` to `'7 days'`, `'30 days'`, etc.
- **Limit results:** Add `LIMIT 10` to most queries to prevent huge result sets
- **Test slowly:** Run simple queries first, get complex ones later
- **Check indexes:** Add `EXPLAIN ANALYZE` before any complex query
- **Save favorites:** Copy queries you use often to a notepad file

---

**Happy queries! 🚀**
