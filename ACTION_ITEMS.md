# CRITICAL ACTION ITEMS - IMMEDIATE NEXT STEPS
## SentinelOps DB Migration - Your Checklist

**Status:** All infrastructure complete ✅  
**Next:** Integration & Testing ⚙️  
**Timeline:** 4-6 hours to production-ready

---

## 🔴 CRITICAL FILE: MIGRATION GUIDE
**Location:** `MIGRATION_GUIDE.md`  
**Read first:** YES - This explains everything

---

## ✅ PART 1: DATABASE SETUP (15 minutes)

### Action 1.1: Run Migration
```bash
cd c:\Users\ashumba\Documents\Sentinel\SentinelOps-beta
psql -U your_username -d sentinelops -f app/db/migrations/final_alignment.sql
```

**Verify it worked:**
```sql
-- Check new indexes exist
\d auth_events
\d checklist_instances  
\d ops_events

-- Should see new indexes listed
```

### Action 1.2: Verify Database Connection
```sql
-- Test connection
SELECT version();

-- Check roles exist (should see: admin, manager, user)
SELECT name FROM roles;

-- Check department/section structure
SELECT COUNT(*) FROM department;
SELECT COUNT(*) FROM sections;
```

---

## ⚙️ PART 2: UPDATE CHECKLIST ROUTER (2-3 hours)

**File:** `app/checklists/router.py`

### Action 2.1: Replace Imports
At the top of router.py, CHANGE from:
```python
from app.checklists.unified_service import UnifiedChecklistService
```

TO:
```python
from app.checklists.db_service import ChecklistDBService
from app.checklists.schemas import ChecklistInstanceResponse
```

### Action 2.2: Update Endpoint: GET /templates
FIND this in router.py (around line 60):
```python
# OLD CODE - using file-based service
templates = []
template_dir = Path(__file__).parent / "templates"
for shift_dir in template_dir.iterdir():
    for template_file in shift_dir.glob("*.json"):
        template_data = json.load(template_file)
        ...
```

REPLACE WITH:
```python
# NEW CODE - using DB service
from app.checklists.db_service import ChecklistDBService

shift_value = shift if shift else None
templates = ChecklistDBService.list_templates(
    shift=shift_value,
    active_only=active_only
)
return templates
```

### Action 2.3: Update Endpoint: POST /instances
FIND this in router.py (around line 135):
```python
# OLD CODE
result = await UnifiedChecklistService.create_checklist_instance(
    checklist_date=data.checklist_date,
    shift=data.shift.value,
    ...
)
```

REPLACE WITH:
```python
# NEW CODE
from uuid import UUID

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

### Action 2.4: Update Endpoint: PUT /instances/{id}/items/{item_id}
FIND this in router.py (around line 250):
```python
# OLD CODE
await UnifiedChecklistService.update_item_status(...)
```

REPLACE WITH:
```python
# NEW CODE
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
    "effects": {
        "auto_notified_admins": data.status in [ItemStatus.SKIPPED, ItemStatus.FAILED]
    }
}
```

**KEY INSIGHT:** When you update item status to SKIPPED or FAILED, the new service automatically:
- ✅ Logs the activity
- ✅ Logs the ops event
- ✅ Notifies admin/manager
- ✅ Creates the notification in DB

### Action 2.5: Update Other Endpoints
Refer to detailed examples in MIGRATION_GUIDE.md for:
- GET /instances/{id}
- POST /instances/{id}/join (add participant)
- PATCH /instances/{id}/status (checklist status)
- Any others you use

---

## 🧪 PART 3: TEST AUTH EVENTS (30 minutes)

### Action 3.1: Test Login Logging
1. Open your app
2. Try to login with valid credentials
3. Check database:

```sql
SELECT * FROM auth_events 
WHERE event_type = 'LOGIN_SUCCESS' 
ORDER BY event_time DESC LIMIT 1;
```

Should see:
- `event_type: LOGIN_SUCCESS`
- `user_id: [your user ID]`
- `ip_address: 127.0.0.1` (or your IP)
- `user_agent: Mozilla/5.0...` (your browser)
- `metadata: {"username": "...", "email": "...", ...}`

### Action 3.2: Test Failed Login
1. Try wrong password
2. Check database:

```sql
SELECT * FROM auth_events 
WHERE event_type = 'LOGIN_FAILURE' 
ORDER BY event_time DESC LIMIT 1;
```

Should see:
- `event_type: LOGIN_FAILURE`
- `user_id: NULL` (because auth failed)
- `metadata: {"email": "...", "reason": "Invalid credentials"}`

### Action 3.3: Test Logout
1. Login successfully
2. Click logout
3. Check database:

```sql
SELECT * FROM auth_events 
WHERE event_type = 'LOGOUT' 
ORDER BY event_time DESC LIMIT 1;
```

Should see:
- `event_type: LOGOUT`
- `user_id: [your user ID]`
- `metadata: {"username": "..."}`

**✅ If all three show up, auth event logging is working!**

---

## 📢 PART 4: TEST NOTIFICATIONS (30 minutes)

### Action 4.1: Create a Checklist Instance
```bash
# Via your UI or API:
POST /checklists/instances
{
    "checklist_date": "2026-02-06",
    "shift": "MORNING"
}
```

Response should include instance ID. Keep it.

### Action 4.2: Skip an Item
```bash
#Via your UI or API:
PUT /checklists/instances/{instance_id}/items/{item_id}
{
    "status": "SKIPPED",
    "reason": "Equipment maintenance required"
}
```

### Action 4.3: Verify Notifications in DB
```sql
-- This is the magic test!
SELECT 
    id, title, user_id, role_id, message, 
    created_at, is_read
FROM notifications 
WHERE title LIKE '%Skipped%'
ORDER BY created_at DESC LIMIT 5;
```

Should see:
- `title: "⚠️ Checklist Item Skipped"`
- `message: "Item 'XYZ' was skipped on 2026-02-06 (MORNING shift)..."`
- `role_id: [one or more role IDs]` (admin and manager roles)
- `user_id: NULL` (because it's role-targeted, not user-targeted)
- `is_read: FALSE`

**Do this for FAILED items too:**
```bash
PUT /checklists/instances/{instance_id}/items/{item_id}
{
    "status": "FAILED",
    "reason": "Critical issue found during inspection"
}
```

Then check:
```sql
SELECT title FROM notifications 
WHERE title LIKE '%CRITICAL%' 
ORDER BY created_at DESC LIMIT 1;
```

Should see: `"🚨 CRITICAL: Checklist Item Escalated"`

**✅ If notifications appear in DB, notification system is working!**

---

## ✨ PART 5: TEST OPS EVENTS (20 minutes)

### Action 5.1: Complete a Checklist
After skipping/failing items, complete the checklist:

```bash
PATCH /checklists/instances/{instance_id}/status
{
    "status": "COMPLETED",
    "comment": "Shift completed with noted exceptions"
}
```

### Action 5.2: Verify Ops Events in DB
```sql
-- See all ops events from today
SELECT 
    id, event_type, entity_type, entity_id, 
    payload->'user_id' as user_id,
    created_at
FROM ops_events 
WHERE created_at > NOW() - INTERVAL '2 hours'
ORDER BY created_at DESC;
```

Should see event sequence:
1. `CHECKLIST_CREATED` - when instance was created
2. `ITEM_SKIPPED` - when item was skipped
3. `ITEM_FAILED` - when item failed
4. `CHECKLIST_COMPLETED` - when completed

Each event's `payload` JSON contains rich context:
- User who performed action
- Item/checklist details
- Timestamp
- Reason (for skip/fail)

**✅ If events appear as sequence, ops event logging is working!**

---

## 🚀 PART 6: FINAL SANITY CHECKS

Before going to production, verify:

### Check 1: Frontend Still Works
- [ ] Login page loads
- [ ] Can login
- [ ] Dashboard shows checklists
- [ ] Can create new checklist
- [ ] Can update items
- [ ] Can see notifications
- [ ] Can logout

**Important:** No frontend changes needed! If it worked before with file-based service, it works now with DB-backed service.

### Check 2: No Console Errors
```
# Check Python logs:
# Should NOT see any ERROR or EXCEPTION messages

# Check browser console (F12):
# Should NOT see any 404s or 500s
```

### Check 3: Database Queries Are Fast
```sql
-- Should all complete in < 100ms

-- Get instance with all data
SELECT * FROM checklist_instances WHERE id = 'xxx' LIMIT 1;

-- Get user notifications  
SELECT * FROM notifications WHERE user_id = 'xxx' LIMIT 50;

-- Get recent events
SELECT * FROM ops_events ORDER BY created_at DESC LIMIT 100;

-- Get auth events
SELECT * FROM auth_events ORDER BY event_time DESC LIMIT 100;
```

### Check 4: Verify Roles Setup
```sql
-- Should see exactly 3 roles
SELECT id, name FROM roles;

-- Output should show:
-- | id | name |
-- | ... | admin |
-- | ... | manager |
-- | ... | user |

-- These role IDs are used in notify_admin_and_managers()
```

---

## 🎯 FINAL PRODUCTION READINESS

Once all 6 parts pass, you're ready! Checklist:

- [ ] Database migration ran successfully
- [ ] No errors in `final_alignment.sql`
- [ ] Router updated with ChecklistDBService
- [ ] Auth events appear in DB (login/logout)
- [ ] Notifications appear on skip/fail
- [ ] Ops events appear in sequence
- [ ] Frontend works without changes
- [ ] No console errors
- [ ] All query times < 100ms
- [ ] Roles configured (admin, manager, user)

**🎉 Once all are checked, you're PRODUCTION READY!**

---

## 📊 QUERIES FOR MONITORING (Save These!)

```sql
-- Dashboard: Unread notification count per user
SELECT user_id, COUNT(*) as unread_count
FROM notifications
WHERE is_read = FALSE
GROUP BY user_id
ORDER BY unread_count DESC;

-- Dashboard: Recent ops events
SELECT event_type, entity_type, COUNT(*) as event_count
FROM ops_events
WHERE created_at > NOW() - INTERVAL '24 hours'
GROUP BY event_type, entity_type
ORDER BY event_count DESC;

-- Dashboard: Auth events
SELECT event_type, COUNT(*) as count
FROM auth_events
WHERE event_time > NOW() - INTERVAL '24 hours'
GROUP BY event_type;

-- Health check: Active checklists
SELECT shift, COUNT(*) as active_count
FROM checklist_instances
WHERE status IN ('OPEN', 'IN_PROGRESS')
GROUP BY shift;
```

---

## 🆘 IF SOMETHING BREAKS

### Nothing's in notifications table
1. Verify `admin` and `manager` roles exist: `SELECT * FROM roles;`
2. Check app logs for errors during update_item_status
3. Manually test: `INSERT INTO notifications (...) VALUES (...)`

### Auth events not appearing
1. Check: `/signin` endpoint is hitting updated code
2. Check: `AuthEventLogger.log_login_success()` is being called
3. Check app logs for: "✅ Login success:" message

### Ops events not appearing
1. Check: `ChecklistDBService.update_item_status()` is being called
2. Check app logs for: "📊 Event logged:" message
3. Verify connection to DB is working

### Everything broken
**Rollback Plan:**
1. Keep file-based services as fallback
2. Revert router imports to `UnifiedChecklistService`
3. Data is safely in DB (won't be lost)
4. Debug from there with logs

---

## 📞 QUICK REFERENCE

**Key Files Modified:**
- `app/auth/router.py` ✅ (login/logout logging added)
- `app/notifications/service.py` ✅ (now uses DB backend)
- `app/checklists/router.py` ⚙️ (needs updating)

**Key Files Created:**
- `app/auth/events.py` ✅ (AuthEventLogger)
- `app/ops/events.py` ✅ (OpsEventLogger)
- `app/notifications/db_service.py` ✅ (NotificationDBService)
- `app/checklists/db_service.py` ✅ (ChecklistDBService)

**Documentation:**
- `IMPLEMENTATION_SUMMARY.md` ✅ (what's done)
- `MIGRATION_GUIDE.md` ✅ (detailed guide)
- This file (YOUR ACTION ITEMS)

---

## ✨ YOU GOT THIS! 🚀

You have all the tools. Just:
1. Run the migration
2. Update the router (follow examples in MIGRATION_GUIDE.md)
3. Test each part
4. Deploy

The system is rock-solid. No shortcuts. Just methodical execution.

**Questions about any step?** Check MIGRATION_GUIDE.md - it has examples!

---

**Timeline:** 4-6 hours to production  
**Complexity:** Medium (mostly copy-paste updates)  
**Risk Level:** Low (all data stays in DB, can rollback)

**You're building something SPECTACULAR here. Let's make it happen!** 🎯
