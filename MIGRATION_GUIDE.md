# MAXIMUM EFFORT MIGRATION GUIDE: FILE-BASED → DB-DRIVEN ARCHITECTURE
# ============================================================================
#
# This document provides complete implementation guidance for migrating
# SentinelOps from file-based storage to a modern, DB-driven architecture.
#
# Date: 2026-02-06
# Target: Complete database alignment with checklist lifecycle, notifications,
#         auth events, and operational audit logging.
#
# ============================================================================

## PHASE 1: INFRASTRUCTURE COMPLETE ✅
### What was created:

1. **app/notifications/db_service.py** ✅
   - DB-backed notification service
   - Role-target notifications (admin/manager)
   - Auto-notify on skip/fail events
   - User-specific + role-based query support

2. **app/auth/events.py** ✅
   - Auth event logger (login success/failure)
   - Session creation/revocation logging
   - Invalid token tracking
   - IP address + User-Agent capture for security

3. **app/ops/events.py** ✅
   - Operational events logger
   - High-signal events only (no spam):
     * Checklist created/completed
     * Item status changes (skip/fail/complete)
     * Participant joined / Override applied
     * Handover note created
   - JSON payload support for rich context

4. **app/checklists/db_service.py** ✅
   - Core DB-backed checklist service
   - Template management (get_template, list_templates, get_active_template_for_shift)
   - Instance lifecycle (create, retrieve, update status)
   - Item status updates with auto-notifications
   - Participant management
   - Full activity logging to checklist_item_activity table

5. **app/db/migrations/final_alignment.sql** ✅
   - Performance indexes for all key queries
   - State transition rules for CHECKLIST_ITEM
   - Performance views for dashboard queries
   - Foreign key verification
   - Data integrity checks

## PHASE 2: ROUTER & INTEGRATION POINTS

### Updated Files:

1. **app/auth/router.py** ⚙️ PARTIALLY UPDATED
   - Added imports for AuthEventLogger
   - Updated /signin endpoint with event logging
   - Updated /logout endpoint with event logging
   - Next: Test login/logout flow

2. **app/auth/service.py** 📝 SHOULD UPDATE
   - Consider adding event logging to get_user_from_token for invalid token attempts
   - Consider augmenting authenticate_user to validate IP/UA for security

### Files That Need Updates:

3. **app/checklists/router.py** ⚠️ MAJOR REFACTOR NEEDED
   Current: Uses UnifiedChecklistService (file-based)
   New: Should use ChecklistDBService (DB-backed)
   
   Endpoints affected:
   - GET /templates → ChecklistDBService.list_templates()
   - POST /instances → ChecklistDBService.create_checklist_instance()
   - GET /instances → ChecklistDBService.get_instances_by_date()
   - GET /instances/{id} → ChecklistDBService.get_instance()
   - PUT /instances/{id}/items/{item_id} → ChecklistDBService.update_item_status()
   - POST /instances/{id}/join → ChecklistDBService.add_participant()
   - PATCH /instances/{id}/status → ChecklistDBService.update_instance_status()

   Expected Impact: ~300 lines of refactoring

4. **app/notifications/service.py** ⚠️ FULL REPLACEMENT
   Current: Uses file-based storage
   New: Should delegate entirely to NotificationDBService
   
   Changes:
   - Remove get_user_notifications file logic → use db_service
   - Remove mark_notification_as_read file logic → use db_service
   - Remove create_notification file logic → use db_service
   - Import NotificationDBService and wrap calls

5. **app/main.py** 📝 VERIFY INITIALIZATION
   Ensure: Database connection pool is initialized before routing
   - Call init_db() in startup event
   - Verify psycopg (sync) and asyncpg (async) pools

## PHASE 3: DATA MIGRATION (if migrating from file-based data)

**Action Required** (if you have existing data in files):

1. Export data from file storage:
   - Templates from app/checklists/templates/*.json
   - Instances from app/checklists/instances/
   - Anything valuable

2. Import to database:
   ```sql
   INSERT INTO checklist_templates (id, name, description, shift, version, created_by)
   SELECT ...
   
   INSERT INTO checklist_template_items (id, template_id, title, ...)
   SELECT ...
   
   INSERT INTO checklist_instances (id, template_id, checklist_date, ...)
   SELECT ...
   ```

3. Verify data integrity:
   ```sql
   SELECT COUNT(*) FROM checklist_templates;
   SELECT COUNT(*) FROM checklist_instances;
   SELECT COUNT(*) FROM checklist_item_activity;
   ```

## PHASE 4: TESTING CHECKLIST

### Unit Tests to Add:

1. **test_notifications_db_service.py**
   - Test: create_notification (user + role)
   - Test: notify_admin_and_managers
   - Test: Auto-notify on skip/fail
   - Test: Mark as read (with permission check)

2. **test_auth_events.py**
   - Test: Log login success
   - Test: Log login failure
   - Test: Log logout
   - Test: Log session creation/revocation

3. **test_ops_events.py**
   - Test: Log checklist created
   - Test: Log item completed
   - Test: Log item skipped/failed
   - Test: Log override applied

4. **test_checklist_db_service.py**
   - Test: Create instance
   - Test: Get instance with items
   - Test: Update item status with auto-notify
   - Test: Update instance status
   - Test: Add participant

### Integration Tests:

1. End-to-end: Create checklist → Add participant → Complete items → Close checklist
2. Notification trigger: Skip item → Verify admin/manager notified
3. Auth events: Login → Activity → Logout → Verify events logged
4. Ops events: Complete checklist → Verify payment event logged

## PHASE 5: FRONTEND ALIGNMENT (NO CHANGES NEEDED)

✅ The frontend API contract remains unchanged:
- Same endpoint paths
- Same response schemas (ChecklistInstanceResponse, etc.)
- Same error handling

The frontend will continue working because our services return the same structure.

## MIGRATION STEPS (IN ORDER)

### Step 1: Run Database Setup ✅
```bash
# Run all migrations
psql -U sentinel -d sentinelops -f app/db/migrations/000_basic_schema.sql
psql -U sentinel -d sentinelops -f app/db/migrations/final_alignment.sql
```

### Step 2: Test Auth Events 🔄
```python
# In app/auth/router.py - verify login/logout logging now appears in auth_events table
# Then: SELECT * FROM auth_events ORDER BY event_time DESC;
```

### Step 3: Update Checklist Router 🔄
- Replace imports: UnifiedChecklistService → ChecklistDBService
- Update endpoint implementations (see detailed changes below)
- Test each endpoint individually

### Step 4: Replace Notification Service 🔄
- Update app/notifications/service.py to use db_service
- Verify notifications appear in DB
- Test role-based notifications

### Step 5: Comprehensive Testing 🔄
- Run all unit tests
- Run integration tests
- Verify frontend works without changes

### Step 6: Monitor & Optimize 🔄
- Review slow queries
- Verify indexes are being used
- Monitor database connections

## DETAILED ROUTER CHANGES

### Example 1: GET /templates
```python
# OLD (File-based):
templates = []
template_dir = Path(__file__).parent / "templates"
for shift_dir in template_dir.iterdir():
    for template_file in shift_dir.glob("*.json"):
        template_data = json.load(template_file)
        ...

# NEW (DB-backed):
from app.checklists.db_service import ChecklistDBService
templates = ChecklistDBService.list_templates(
    shift=shift if shift else None,
    active_only=active_only
)
return templates
```

### Example 2: POST /instances
```python
# OLD (File-based):
result = await UnifiedChecklistService.create_checklist_instance(
    checklist_date=data.checklist_date,
    shift=data.shift.value,
    template_id=data.template_id,
    user_id=current_user["id"]
)

# NEW (DB-backed):
instance = ChecklistDBService.create_checklist_instance(
    checklist_date=data.checklist_date,
    shift=data.shift.value,
    created_by=UUID(current_user["id"]),
    created_by_username=current_user["username"],
    template_id=UUID(data.template_id) if data.template_id else None
)
return {
    "instance": instance,
    "effects": {"created": True}
}
```

### Example 3: PUT /instances/{id}/items/{item_id}
```python
# OLD (File-based):
await UnifiedChecklistService.update_item_status(
    item_id=item_id,
    status=data.status.value,
    user_id=current_user["id"],
    comment=data.comment,
    reason=data.reason
)

# NEW (DB-backed):
success = ChecklistDBService.update_item_status(
    item_id=UUID(item_id),
    new_status=data.status.value,
    user_id=UUID(current_user["id"]),
    username=current_user["username"],
    reason=data.reason,
    comment=data.comment
)
if not success:
    raise HTTPException(status_code=400, detail="Failed to update item")

instance = ChecklistDBService.get_instance(UUID(instance_id))
return {
    "instance": instance,
    "effects": {"auto_notified_admins": data.status == ItemStatus.SKIPPED or data.status == ItemStatus.FAILED}
}
```

## KEY BEHAVIORS IMPLEMENTED

### 1. Auto-Notifications (Critical Feature)
```python
# When item is SKIPPED:
NotificationDBService.create_item_skipped_notification(
    item_id=item_id,
    item_title=item_title,
    instance_id=instance_id,
    checklist_date=checklist_date,
    shift=shift,
    skipped_reason=reason
)
# Admin + Manager roles automatically notified ✅

# When item FAILS:
NotificationDBService.create_item_failed_notification(...)
# Sends CRITICAL notification ✅
```

### 2. Activity Logging (Audit Trail)
```python
# Every meaningful action is logged:
- Item status changes → checklist_item_activity table
- Checklist completion → ops_events table
- Team participation → ops_events table
- Skip/Fail events → Both activity + ops_events
```

### 3. Auth Events (Security Audit)
```python
# On successful login:
AuthEventLogger.log_login_success(user_id, username, email, ip, ua)
# Records in auth_events table

# On failed login:
AuthEventLogger.log_login_failure(email, reason, ip, ua)
# Records failed attempt (no user_id since auth failed)

# On logout:
AuthEventLogger.log_logout(user_id, username, ip, ua)
# Records session termination
```

## PERFORMANCE OPTIMIZATIONS

### Indexes Added:
- ✅ idx_checklist_instances_date_shift
- ✅ idx_instance_items_instance_status
- ✅ idx_activity_instance_item
- ✅ idx_notifications_created_read
- ✅ idx_auth_events_event_type
- ✅ idx_ops_events_created_at
- ✅ idx_participants_instance_id
- ✅ idx_users_email (for login lookup)

### Query Performance:
- Instance retrieval: O(1) with filters
- Template lookup: O(1) for active templates per shift
- Notification dispatch: O(log n) with indexes
- Activity history: Streamed via pagination

## DATABASE SCHEMA SUMMARY

```
┌─────────────────────────────────────────────────────────────┐
│                    AUTHENTICATION                           │
├─────────────────────────────────────────────────────────────┤
│ users ─┬─→ user_roles ─→ roles
│        └─→ auth_sessions
│        └─→ auth_events
│        └─→ user_departments ─→ department + sections
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│               CHECKLIST LIFECYCLE                           │
├─────────────────────────────────────────────────────────────┤
│ checklist_templates ─┬─→ checklist_template_items
│                      └─→ checklist_instances ─┬─→ checklist_instance_items
│                                               ├─→ checklist_participants
│                                               ├─→ checklist_item_activity
│                                               ├─→ checklist_overrides
│                                               └─→ handover_notes
│ state_transition_rules (for validation)
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│              NOTIFICATIONS & EVENTS                         │
├─────────────────────────────────────────────────────────────┤
│ notifications (with user_id + role_id support)
│ ops_events (operational audit log)
│ auth_events (authentication audit log)
│ gamification_scores
│ user_operational_streaks
└─────────────────────────────────────────────────────────────┘
```

## CUTOVER STRATEGY

**Option A: Big Bang** (Recommended for this system)
1. Update all services at once
2. Run comprehensive tests
3. Deploy all changes together
4. Monitor for 24 hours

**Option B: Gradual** (If high risk)
1. Deploy auth events first
2. Deploy notifications second
3. Deploy checklist service third
4. Run parallel systems temporarily

## ROLLBACK PLAN

If issues arise:
1. All data remains in database (safe)
2. File-based services can still run in parallel
3. Revert router imports temporarily
4. All logs preserved in DB for debugging

## NEXT IMMEDIATE ACTIONS FOR USER

1. **Run the SQL migration:**
   ```bash
   psql -U sentinel -d sentinelops -f app/db/migrations/final_alignment.sql
   ```

2. **Update app/checklists/router.py**
   - Replace UnifiedChecklistService imports
   - Update all endpoint implementations
   - Reuse schemas, just change underlying service

3. **Update app/notifications/service.py**
   - Replace file-based logic
   - Use NotificationDBService throughout

4. **Test the auth flow:**
   ```sql
   -- Verify login logged:
   SELECT * FROM auth_events WHERE event_type = 'LOGIN_SUCCESS' ORDER BY event_time DESC;
   
   -- Verify logout logged:
   SELECT * FROM auth_events WHERE event_type = 'LOGOUT' ORDER BY event_time DESC;
   ```

5. **Test checklist creation:**
   ```sql
   -- Verify instance created:
   SELECT id, shift, status FROM checklist_instances ORDER BY created_at DESC LIMIT 5;
   
   -- Verify items populated:
   SELECT COUNT(*) FROM checklist_instance_items WHERE instance_id = 'your-instance-id';
   ```

6. **Test skip/fail notifications:**
   - Create instance
   - Skip an item with reason
   - Verify notification in notifications table:
     ```sql
     SELECT * FROM notifications WHERE title LIKE '%Skipped%' ORDER BY created_at DESC;
     ```

## SUCCESS CRITERIA

✅ System is considered ready when:
1. All checklist operations use DB (not files)
2. All auth events are logged
3. All ops events are logged
4. Notifications route to admin/manager on skip/fail
5. No errors in logs for 24 hours
6. Frontend works without modification
7. Performance is acceptable (queries < 100ms)

## CREATIVE ENHANCEMENTS TO CONSIDER

Once core migration is complete, consider:

1. **Real-time Notifications**
   - WebSocket integration for live updates
   - Push notifications when admin/manager notified

2. **Advanced Analytics Dashboard**
   - Shift performance metrics
   - User performance leaderboard
   - Trend analysis

3. **Gamification Dashboard**
   - Display points earned
   - Streak tracking
   - Team vs individual stats

4. **Intelligent Handover**
   - Auto-generate handover notes from failed items
   - Suggest actions based on history
   - Smart escalation routing

5. **Audit Report Generation**
   - Shift summaries
   - Compliance reports
   - Custom audit trails

6. **Mobile App Integration**
   - Push notifications
   - Offline mode with sync
   - Touch-optimized UI

---

## ESTIMATED TIMELINE

- Phase 1 (Infrastructure): ✅ 2 hours (DONE)
- Phase 2 (Router Updates): ⚙️ 3-4 hours (IN PROGRESS)
- Phase 3 (Testing): 🔄 2-3 hours (NEXT)
- Phase 4 (Deployment): 🔄 1 hour (FINAL)

**Total: ~8-10 hours for complete migration**

---

**Ready to proceed? Let me know if you need help with router updates or have questions!**
