# Hierarchical Checklist System - Quick Reference Guide

**For Frontend Developers - Quick Lookup**

---

## 📌 Quick Facts

- **Item can have:** 0-unlimited subitems
- **Subitem workflow:** Sequential (one at a time)
- **Subitem statuses:** PENDING, IN_PROGRESS, COMPLETED, SKIPPED, FAILED
- **Modal count:** 2-4 modals (ItemActions + N×SubitemActions + Summary)
- **API calls:** Minimum 5 calls per item with subitems

---

## 🎯 Modal State Machine

```
CLOSED
  ↓ click item
ITEM_ACTIONS (has_subitems=false)
  ↓ user action
CLOSED

CLOSED
  ↓ click item  
ITEM_ACTIONS (has_subitems=true)
  ↓ click "Start Work"
SUBITEM_ACTION (1/n)
  ↓ user action
SUBITEM_ACTION (2/n)
  ↓ user action
... (repeat)
SUBITEM_ACTION (n/n)
  ↓ user action & all_subitems_done=true
COMPLETION_SUMMARY
  ↓ click "Complete Item"
CLOSED
```

---

## 🔗 API Quick Reference

### Endpoint 1: Start Item Work
```
POST /checklists/instances/{iid}/items/{item_id}/start-work

RESPONSE KEY FIELDS:
- has_subitems: boolean
- next_subitem: Subitem | null
- subitem_count: number
- completed_subitem_count: number
- subitem_status: PENDING | IN_PROGRESS | COMPLETED | ...
```

### Endpoint 2: Complete Subitem
```
PATCH /checklists/instances/{iid}/items/{item_id}/subitems/{sid}

BODY:
{
  "status": "COMPLETED" | "SKIPPED" | "FAILED",
  "reason": "string (required for SKIPPED/FAILED)",
  "comment": "string (optional)"
}

RESPONSE KEY FIELDS:
- subitem_id: string
- status: string (updated)
- next_subitem: Subitem | null ← CRITICAL!
- all_subitems_done: boolean ← CRITICAL!
- stats.completed: number
- stats.total: number
```

### Endpoint 3: Get Summary
```
GET /checklists/instances/{iid}/items/{item_id}/completion-summary

RESPONSE KEY FIELDS:
- subitems: array
  ├─ each with status, completed_by, completed_at
- stats: { total, completed, skipped, failed }
- summary.can_complete_item: boolean
```

### Endpoint 4: Complete Item
```
PATCH /checklists/instances/{iid}/items/{item_id}

BODY:
{
  "status": "COMPLETED"
}
```

---

## 💾 State Interface (TypeScript)

```typescript
// Core types
interface Subitem {
  id: string;
  instance_item_id: string;
  title: string;
  description?: string;
  status: 'PENDING' | 'IN_PROGRESS' | 'COMPLETED' | 'SKIPPED' | 'FAILED';
  completed_by?: User;
  completed_at?: string;  // ISO 8601
  skipped_reason?: string;
  failure_reason?: string;
  severity: 1 | 2 | 3 | 4 | 5;
  sort_order: number;
  created_at: string;
}

// Modal state
type ModalState =
  | { type: 'CLOSED' }
  | { 
      type: 'ITEM_ACTIONS';
      itemId: string;
      itemTitle: string;
      hasSubitems: boolean;
      subitems?: Subitem[];
    }
  | {
      type: 'SUBITEM_ACTION';
      subitemId: string;
      currentIndex: number;  // 0-based
      totalCount: number;
      subitem: Subitem;
    }
  | {
      type: 'COMPLETION_SUMMARY';
      itemId: string;
      subitems: Subitem[];
      canCompleteItem: boolean;
    };

// Working state
interface WorkingState {
  itemId: string;
  subitems: Subitem[];
  completedSubitems: string[];  // IDs
  skippedSubitems: string[];
  failedSubitems: string[];
}
```

---

## 🎬 Implementation Pseudocode

```typescript
// Hook for starting item work
async function handleStartWork(itemId: string) {
  const response = await POST(`/items/${itemId}/start-work`, {});
  
  const {
    has_subitems,
    next_subitem,
    subitem_count,
    subitems
  } = response;
  
  if (!has_subitems) {
    // Show quick-action modal, close on action
    setModalState({
      type: 'ITEM_ACTIONS',
      hasSubitems: false
    });
  } else {
    // Show first subitem
    setWorkingState({
      itemId,
      subitems,
      completed: [],
      skipped: [],
      failed: []
    });
    
    setModalState({
      type: 'SUBITEM_ACTION',
      subitemId: next_subitem.id,
      currentIndex: 0,
      totalCount: subitem_count,
      subitem: next_subitem
    });
  }
}

// Hook for completing subitem
async function handleSubitemAction(
  subitemId: string,
  status: 'COMPLETED' | 'SKIPPED' | 'FAILED',
  reason?: string
) {
  const response = await PATCH(
    `/subitems/${subitemId}`,
    { status, reason }
  );
  
  const {
    next_subitem,
    all_subitems_done,
    stats
  } = response;
  
  // Update working state
  updateWorkingState(status, subitemId);
  
  if (all_subitems_done) {
    // Fetch and show summary
    const summary = await GET(`/completion-summary`);
    setModalState({
      type: 'COMPLETION_SUMMARY',
      subitems: summary.subitems,
      canCompleteItem: summary.summary.can_complete_item
    });
  } else if (next_subitem) {
    // Show next subitem
    const newIndex = stats.completed + stats.skipped + stats.failed;
    setModalState({
      type: 'SUBITEM_ACTION',
      subitemId: next_subitem.id,
      currentIndex: newIndex,
      totalCount: stats.total,
      subitem: next_subitem
    });
  }
}

// Hook for completing item
async function handleCompleteItem(itemId: string) {
  await PATCH(`/items/${itemId}`, { status: 'COMPLETED' });
  
  // Close all modals
  setModalState({ type: 'CLOSED' });
  
  // Refresh timeline
  refreshChecklist();
}
```

---

## 🎨 UI Component Skeleton

```tsx
// ItemActionsModal.tsx
export function ItemActionsModal({ itemId, onClose }) {
  const [subitems, setSubitems] = useState([]);
  const [isWorking, setIsWorking] = useState(false);
  
  const handleStart = async () => {
    const data = await startWork(itemId);
    setSubitems(data.subitems);
    setIsWorking(true);
    // Next: show SubitemModal
  };
  
  return (
    <Modal>
      {!isWorking ? (
        <>
          <h2>Item Title</h2>
          {subitems.length > 0 ? (
            <button onClick={handleStart}>Start Work</button>
          ) : (
            <>
              <button onClick={() => completeItem()}>Complete</button>
              <button onClick={() => skipItem()}>Skip</button>
            </>
          )}
        </>
      ) : (
        <SubitemSequence 
          subitems={subitems}
          onComplete={() => setIsWorking(false)}
        />
      )}
    </Modal>
  );
}

// SubitemSequence.tsx (handles sequential modal flow)
export function SubitemSequence({ subitems, onComplete }) {
  const [index, setIndex] = useState(0);
  const [showSummary, setShowSummary] = useState(false);
  
  const handleSubitemAction = async (status, reason) => {
    await updateSubitem(subitems[index].id, status, reason);
    
    // API tells us if all done
    const response = await getSubitemStats();
    
    if (response.all_subitems_done) {
      setShowSummary(true);
    } else {
      setIndex(index + 1);
    }
  };
  
  if (showSummary) {
    return <CompletionSummary onComplete={onComplete} />;
  }
  
  return (
    <SubitemModal
      subitem={subitems[index]}
      current={index + 1}
      total={subitems.length}
      onAction={handleSubitemAction}
    />
  );
}

// CompletionSummary.tsx
export function CompletionSummary({ onComplete }) {
  const [subitems, setSubitems] = useState([]);
  
  useEffect(() => {
    // Fetch summary on mount
    getCompletionSummary().then(set Subitems);
  }, []);
  
  return (
    <Modal>
      <h2>All Subitems Completed</h2>
      
      <table>
        <tbody>
          {subitems.map(s => (
            <tr key={s.id}>
              <td>{s.title}</td>
              <td>{s.status}</td>
              <td>{s.completed_by?.username}</td>
              <td>{formatTime(s.completed_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      
      <button onClick={handleCompleteItem}>Complete Item</button>
    </Modal>
  );
}
```

---

## 🔑 Critical Implementation Points

### ✅ DO:
- [ ] Call `start-work` endpoint when user clicks "Start"
- [ ] Check `has_subitems` in response to determine path
- [ ] Show `next_subitem` in modal
- [ ] On subitem action, check `all_subitems_done` flag
- [ ] Only complete item after all subitems are "actioned" (done/skipped/failed)
- [ ] Store `completed_by` user info for audit trail
- [ ] Show progress: "X of Y subitems done"

### ❌ DON'T:
- [ ] Allow user to skip "Start Work" if item has subitems
- [ ] Show subitem directly - always use `next_subitem` from API
- [ ] Close SubitemModal before `all_subitems_done` is true
- [ ] Allow item completion if `all_subitems_done` is false
- [ ] Show all subitems at once - sequential flow only
- [ ] Hardcode subitem count - use API response
- [ ] Refresh timeline until item is fully completed

---

## 🚨 Error Handling

```typescript
// Network errors
try {
  const response = await updateSubitem(...);
} catch (error) {
  showError(`Network error: ${error}`);
  // Keep modal open, allow retry
}

// Validation errors
if (!reason && status !== 'COMPLETED') {
  showError('Reason required for skip/fail');
  // Highlight reason field
}

// State mismatch
if (response.all_subitems_done && response.next_subitem) {
  // Log error, call completion-summary to sync
  console.error('State mismatch detected');
}
```

---

## 📱 Testing Checklist

- [ ] Render ItemActionsModal with no subitems → shows quick actions
- [ ] Render ItemActionsModal with subitems → shows "Start Work"
- [ ] Click "Start Work" → SubitemModal shows first subitem
- [ ] Complete subitem → second SubitemModal shows
- [ ] Skip subitem → stats update, next shows
- [ ] Complete last subitem → `all_subitems_done` triggers summary
- [ ] Summary shows correct count and completed_by info
- [ ] Click "Complete Item" → calls PATCH endpoint
- [ ] Keyboard: ESC from summary → warning, not instant close
- [ ] Mobile: Modals responsive to screen size

---

## 📊 Data Validation

| Field | Validation |
|-------|-----------|
| `status` | Must be COMPLETED, SKIPPED, or FAILED |
| `reason` | Max 1000 chars, required for SKIPPED/FAILED |
| `comment` | Max 2000 chars, optional |
| `sort_order` | Integer, >= 0 |
| `severity` | Integer, 1-5 |
| `completed_at` | ISO 8601 datetime, no future |

---

## 🔔 Key Response Fields Always Check

```typescript
// Always present in subitem update response
response.next_subitem      // Could be null (last item)
response.all_subitems_done // Boolean - controls flow!
response.stats.total       // Total subitems for item
response.stats.completed   // How many done so far
response.stats.all_actioned // All done/skipped/failed?
```

---

## 🎯 Performance Tips

1. **Avoid:** Fetching all subitems details on modal open
   → Use `get_subitems` endpoint as backup only

2. **Optimize:** Cache subitem list after `start-work` call
   → Reuse for progress indicator

3. **Lazy load:** Completion summary only when `all_subitems_done=true`
   → Don't pre-fetch while working through subitems

4. **Debounce:** Action buttons while API call in flight
   → Prevent double-submission

---

## 📚 File References

| Need | Location |
|------|----------|
| Full API docs | `HIERARCHICAL_CHECKLIST_FRONTEND_GUIDE.md` |
| Complete overview | `SUBITEMS_IMPLEMENTATION_COMPLETE.md` |
| Database schema | `app/db/migrations/2026_02_add_checklist_subitems.sql` |
| Backend code | `app/checklists/db_service.py` |
|  Backend routes | `app/checklists/router.py` |
| Type definitions | `app/checklists/schemas.py` |

---

## 🚀 Getting Started

1. Read the full guide: `HIERARCHICAL_CHECKLIST_FRONTEND_GUIDE.md`
2. Review API contract section (detailed examples)
3. Implement modal components using pseudocode above
4. Test each modal independently first
5. Test full flow end-to-end
6. Reference this guide while coding

**Time estimate:** 2-3 days for experienced React developer

---

**Happy coding! 🎉**
