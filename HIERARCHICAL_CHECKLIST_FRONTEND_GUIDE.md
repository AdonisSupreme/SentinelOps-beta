# Hierarchical Checklist System - Frontend Implementation Guide

**Status:** READY FOR IMPLEMENTATION  
**Date:** February 2026  
**Effort Level:** MAXIMUM ⚡⚡⚡

---

## Executive Summary

The checklist system now supports **hierarchical items with subitems**. This document provides the complete frontend architecture to implement the new modal flow where items can contain subitems that must be completed sequentially.

**Key Feature:** When a user starts working on an item that has subitems, the UI sequentially presents each subitem in its own modal for completion, then shows the parent item's summary with all subitem statuses before allowing item completion.

---

## Architecture Overview

### Checklist Item Lifecycle

```
ITEM CREATION
    ↓
USER VIEWS ITEM (Timeline)
    ↓
USER CLICKS ITEM → ITEM ACTIONS MODAL OPENS
    ↓
[NO SUBITEMS?]
    ├→ YES: User completes/skips/fails item directly
    │   ↓
    │   Item Actions Modal closes
    │   Item status updates in timeline
    │
    └→ NO: User clicks "Start Work" button
        ↓
        ITEM STATUS → IN_PROGRESS
        ↓
        SUBITEM WORKFLOW BEGINS
        ↓
        [SEQUENTIAL SUBITEM PROCESSING]
        ├→ SUBITEM MODAL #1 (Show first pending subitem with actions)
        │   ├→ User completes/skips/fails subitem
        │   ├→ Next subitem modal appears
        │   └→ Repeat...
        │
        └→ ALL SUBITEMS DONE
            ↓
            ITEM ACTIONS MODAL REOPENS
            (Now shows: Subitem statuses + who did them + Complete button)
            ↓
            User can now mark item as COMPLETED
            ↓
            Item Actions Modal closes
            ↓
            Next item in timeline becomes available
```

---

## API Contract

### 1. Start Working on Item
**Endpoint:** `POST /checklists/instances/{instance_id}/items/{item_id}/start-work`

**Request:**
```json
{}
```

**Response:**
```json
{
  "item_id": "uuid",
  "item_title": "Check IDC System Status",
  "item_status": "IN_PROGRESS",
  "has_subitems": true,
  "subitems": [
    {
      "id": "uuid",
      "instance_item_id": "uuid",
      "title": "Check System A Health Metrics",
      "description": "Verify CPU, Memory, Disk utilization",
      "item_type": "ROUTINE",
      "is_required": true,
      "severity": 2,
      "sort_order": 0,
      "status": "PENDING",
      "completed_by": null,
      "completed_at": null,
      "skipped_reason": null,
      "failure_reason": null,
      "created_at": "2026-02-13T08:00:00Z"
    }
    // ... additional subitems
  ],
  "next_subitem": {
    "id": "uuid",
    "instance_item_id": "uuid",
    "title": "Check System A Health Metrics",
    "description": "Verify CPU, Memory, Disk utilization",
    "item_type": "ROUTINE",
    "is_required": true,
    "severity": 2,
    "sort_order": 0,
    "status": "PENDING",
    "created_at": "2026-02-13T08:00:00Z"
  },
  "subitem_count": 3,
  "completed_subitem_count": 0,
  "subitem_status": "PENDING"
}
```

### 2. Get Item Subitems (Full Details)
**Endpoint:** `GET /checklists/instances/{instance_id}/items/{item_id}/subitems`

**Response:**
```json
{
  "item_id": "uuid",
  "subitems": [ /* full subitem array */ ],
  "next_subitem": { /* first pending subitem */ },
  "stats": {
    "total": 3,
    "completed": 0,
    "skipped": 0,
    "failed": 0,
    "pending": 3,
    "in_progress": 0,
    "all_actioned": false,
    "status": "PENDING"
  }
}
```

### 3. Update Subitem Status
**Endpoint:** `PATCH /checklists/instances/{instance_id}/items/{item_id}/subitems/{subitem_id}`

**Request:**
```json
{
  "status": "COMPLETED",  // or "SKIPPED", "FAILED"
  "reason": "System down for maintenance",  // Required for SKIPPED/FAILED
  "comment": "Rebooting system"
}
```

**Response:**
```json
{
  "subitem_id": "uuid",
  "status": "COMPLETED",
  "next_subitem": {
    // Next pending subitem, or null if all done
    "id": "uuid",
    "title": "Check System B Health Metrics",
    // ... subitem details
  },
  "all_subitems_done": false,  // true when the last subitem is actioned
  "subitems": [ /* all subitems */ ],
  "stats": {
    "total": 3,
    "completed": 1,
    "skipped": 0,
    "failed": 0,
    "pending": 2,
    "in_progress": 0,
    "all_actioned": false,
    "status": "IN_PROGRESS"
  }
}
```

### 4. Get Item Completion Summary
**Endpoint:** `GET /checklists/instances/{instance_id}/items/{item_id}/completion-summary`

**Response:**
```json
{
  "item_id": "uuid",
  "has_subitems": true,
  "subitems": [
    {
      "id": "uuid",
      "title": "Check System A Health Metrics",
      "status": "COMPLETED",
      "completed_by": {
        "id": "uuid",
        "username": "john_doe",
        "email": "john@example.com"
      },
      "completed_at": "2026-02-13T08:05:30Z",
      // ... other fields
    },
    // ... all subitems
  ],
  "stats": {
    "total": 3,
    "completed": 2,
    "skipped": 1,
    "failed": 0
  },
  "summary": {
    "all_completed": false,
    "all_actioned": true,
    "status": "COMPLETED_WITH_EXCEPTIONS",
    "can_complete_item": false  // false because one was skipped
  }
}
```

---

## Frontend Component Structure

### Modal Hierarchy

```
ChecklistTimeline
  ├─ ChecklistItemCard (in timeline view)
  │   └─ onClick → ItemActionsModal
  │
  ├─ ItemActionsModal (Parent)
  │   ├─ [NO SUBITEMS Path]
  │   │   ├─ Display item details
  │   │   ├─ Quick action buttons (Complete, Skip, Fail)
  │   │   └─ Close on action
  │   │
  │   ├─ [WITH SUBITEMS Path - Initial]
  │   │   ├─ Display item title
  │   │   ├─ Display "Start Work" button
  │   │   └─ Initially hide subitem UI
  │   │
  │   └─ [WITH SUBITEMS Path - Working State]
  │       ├─ Show subitem progress indicator (X/Y subitems done)
  │       └─ BEHIND MODAL: SubitemActionModal
  │           ├─ Display current subitem details
  │           ├─ Subitem action buttons
  │           ├─ On action → Call update-subitem endpoint
  │           ├─ On response:
  │           │   ├─ If next_subitem exists → Show next SubitemActionModal
  │           │   ├─ If all_subitems_done is true:
  │           │   │   ├─ Close SubitemActionModal
  │           │   │   └─ Update parent ItemActionsModal
  │           │   │       ├─ Call completion-summary endpoint
  │           │   │       ├─ Display subitem statuses
  │           │   │       ├─ Show who completed each
  │           │   │       └─ Show "Complete Item" button
  │           │   └─ Else → Continue with sequential flow
  │
  ├─ SubitemActionModal (Child - Sequential)
  │   ├─ Display subitem info
  │   ├─ Show action buttons
  │   ├─ Handle completion
  │   └─ Close/transition based on response
  │
  └─ ItemCompletionSummaryModal (Shows after all subitems done)
      ├─ Display all subitem statuses
      ├─ Show who actioned each
      ├─ Display timestamp/metadata
      ├─ Show "Complete Item" button if allowed
      └─ On completion → Return to timeline
```

---

## Modal Flow Specifications

### 1. Item Actions Modal (No Subitems)

**Trigger:** User clicks item in timeline

**Display:**
- Item title and description
- Item type badge
- Action buttons:
  - ✅ Complete
  - ⏭️ Skip (with reason field)
  - ❌ Fail (with reason field)
  - 🚪 Close

**Behavior:**
- User clicks action → Item updates immediately
- Modal closes
- Timeline updates with new item status

---

### 2. Item Actions Modal (With Subitems - Initial State)

**Trigger:** User clicks item in timeline that has subitems

**Display:**
- Item title
- **"This item has X subitems"** notice
- "Start Work" button (primary action)
- Item description/metadata
- 🚪 Close button

**Behavior:**
- User clicks "Start Work"
  - Request: `POST /checklists/instances/{instance_id}/items/{item_id}/start-work`
  - Item status updates to IN_PROGRESS
  - Modal transitions to "working state"
  - First SubitemActionModal appears on top

---

### 3. Item Actions Modal (With Subitems - Working State)

**Display When:** Item is IN\_PROGRESS with subitems

**Display Elements:**
- Item title (locked - not interactive)
- Progress indicator: **"Subitem X of Y"** (e.g., "1 of 3")
- Progress bar: Visual representation of completion
- Persistent "Back" button (disabled/warning only - prevents accidental exit)
- SubitemActionModal floating above (z-index)

**Note:** This modal should appear muted/in background while SubitemActionModal is active.

---

### 4. Subitem Action Modal (Sequential)

**Trigger:** After item "Start Work" or automatic transition from previous subitem

**Display:**
- Subitem number indicator: **"Subitem X of Y"**
- Subitem title and description
- Severity badge
- Required indicator (if applicable)
- Action buttons:
  - ✅ Complete
  - ⏭️ Skip (with reason field)
  - ❌ Fail (with reason field)
  - 💬 Add comment (optional)

**Behavior - On Action:**

1. User clicks action button
2. Request: `PATCH /checklists/instances/{instance_id}/items/{item_id}/subitems/{subitem_id}`
3. Backend returns:
   - Current subitem status updated
   - `next_subitem` field (null if last one)
   - `all_subitems_done` flag
4. Frontend logic:
   ```
   if response.all_subitems_done:
       Close SubitemActionModal
       Call GET completion-summary endpoint
       Update parent Item Actions Modal
       Show subitem statuses in parent modal
       Show "Complete Item" button
   else if response.next_subitem is not null:
       Transition SubitemActionModal to next subitem
       Display animation/transition
   else:
       Close SubitemActionModal (shouldn't happen if all_subitems_done is correct)
   ```

---

### 5. Item Completion Summary Modal

**Trigger:** After all subitems are actioned (all_subitems_done = true)

**Display:**
- Item title
- **"All Subitems Completed"** header
- Subitem completion table/list:
  ```
  | Subitem Title | Status | Completed By | Time | Reason |
  |---|---|---|---|---|
  | System A Health Check | ✅ COMPLETED | John Doe | 08:05 | - |
  | System B Health Check | ⏭️ SKIPPED | Jane Smith | 08:10 | System down |
  | System C Health Check | ✅ COMPLETED | John Doe | 08:15 | - |
  ```
- Summary statistics:
  - Total subitems
  - Completed count
  - Skipped count
  - Failed count
- **Primary Action Button:**
  - If all completed → "✅ Complete Item"
  - If any failed → "⚠️ Item Complete (With Exceptions)" + warning message
  - If any skipped (no failures) → "Complete Item"
- 🚪 Close button (goes back to timeline)

**Behavior:**
- User clicks "Complete Item"
  - Request: `PATCH /checklists/instances/{instance_id}/items/{item_id}`
  - Payload: `{ "status": "COMPLETED" }`
  - Item status updates to COMPLETED
  - Modal closes
  - Timeline updates
  - Next item becomes available

---

## State Management Patterns

### React Component State (Suggested)

```typescript
interface ChecklistItemWorkingState {
  itemId: string;
  itemStatus: 'IN_PROGRESS';
  subitems: Subitem[];
  currentSubitemIndex: number;  // 0-based
  completedCount: number;
  skippedCount: number;
  failedCount: number;
  pendingSubitems: Subitem[];  // Remaining to action
}

interface SubitemWorkingState {
  subitemId: string;
  title: string;
  description: string;
  status: 'PENDING' | 'IN_PROGRESS' | 'COMPLETED' | 'SKIPPED' | 'FAILED';
  reason?: string;
  completedBy?: User;
  completedAt?: DateTime;
}

// Modal state machine
type ModalState = 
  | { type: 'CLOSED' }
  | { type: 'ITEM_ACTIONS', itemId: string, hasSubitems: boolean }
  | { type: 'SUBITEM_ACTION', subitemId: string, currentIndex: number, total: number }
  | { type: 'COMPLETION_SUMMARY', itemId: string, subitems: Subitem[] };
```

### API Call Sequence

```typescript
// Step 1: User clicks item
const handleStartWork = async (itemId: string) => {
  const response = await fetch(
    `/checklists/instances/${instanceId}/items/${itemId}/start-work`,
    { method: 'POST' }
  );
  const data = await response.json();
  
  setModalState({
    type: 'SUBITEM_ACTION',
    subitemId: data.next_subitem.id,
    currentIndex: 0,
    total: data.subitem_count
  });
};

// Step 2: User completes a subitem
const handleCompleteSubitem = async (subitemId: string, reason?: string) => {
  const response = await fetch(
    `/checklists/instances/${instanceId}/items/${itemId}/subitems/${subitemId}`,
    {
      method: 'PATCH',
      body: JSON.stringify({
        status: 'COMPLETED',
        reason,
        comment: userComment
      })
    }
  );
  const data = await response.json();
  
  if (data.all_subitems_done) {
    // Get completion summary
    const summaryResponse = await fetch(
      `/checklists/instances/${instanceId}/items/${itemId}/completion-summary`
    );
    const summary = await summaryResponse.json();
    
    setModalState({
      type: 'COMPLETION_SUMMARY',
      itemId: itemId,
      subitems: summary.subitems
    });
  } else if (data.next_subitem) {
    // Show next subitem
    setModalState({
      type: 'SUBITEM_ACTION',
      subitemId: data.next_subitem.id,
      currentIndex: data.stats.completed + data.stats.skipped + data.stats.failed,
      total: data.stats.total
    });
  }
};

// Step 3: User completes item after all subitems done
const handleCompleteItem = async (itemId: string) => {
  await fetch(
    `/checklists/instances/${instanceId}/items/${itemId}`,
    {
      method: 'PATCH',
      body: JSON.stringify({ status: 'COMPLETED' })
    }
  );
  
  setModalState({ type: 'CLOSED' });
  refreshTimeline();
};
```

---

## UI/UX Considerations

### Visual Hierarchy

```
BOLD: Item/Subitem Title
│
├─ LARGE: Current subtitle (subitem X of Y)
├─ Medium: Description/details
├─ Medium-Small: Action buttons
├─ Small: Metadata (created by, timestamp)
└─ Tiny: Severity badges, required indicators
```

### Color Coding

- **Blue:** Primary actions (Complete)
- **Yellow/Orange:** Warnings (Skip, Review)
- **Red:** Destructive (Fail, Error)
- **Gray:** Disabled/Info
- **Green:** Success/Completed

### Progress Indication

**During subitem workflow:**
```
Progress: ████░░░░░░ (4/10 subitems done)
```

**Subitem numbering:**
```
Subitem 4 of 10
Next: Database Consistency Check
```

### Accessibility Requirements

- 🔊 Keyboard navigation support
- 🎯 Clear focus indicators
- 📝 Proper labeling of all interactive elements
- 🌈 Color not sole indicator (use icons/text too)
- ♿ ARIA labels for modal states
- ⌨️ ESC key to close (with warning if working)

---

## Error Handling

### Network Errors

**Scenario:** Update subitem fails

**Handling:**
```typescript
try {
  const response = await updateSubitem(...);
  if (!response.ok) {
    showErrorToast(`Failed to update subitem: ${response.statusText}`);
    // Keep modal open, allow retry
  }
} catch (error) {
  showErrorToast(`Network error: ${error.message}`);
  // Show retry button
}
```

### Validation Errors

**Scenario:** Missing required field (reason for skip)

**Handling:**
- Highlight reason field in red
- Show inline error message
- Disable action button until fixed

### State Mismatch

**Scenario:** Backend returns unexpected state

**Handling:**
```typescript
// If all_subitems_done is true but next_subitem is not null
// OR if stats don't match subitem array length
// → Log error and call completion-summary to sync state
```

---

## Implementation Checklist

- [ ] Update ItemActionsModal component to support subitems
- [ ] Create SubitemActionModal component
- [ ] Create ItemCompletionSummaryModal component
- [ ] Implement modal state machine
- [ ] Add API hooks for:
  - `useStartWorkOnItem()`
  - `useUpdateSubitem()`
  - `useGetCompletionSummary()`
- [ ] Add progress tracking UI
- [ ] Implement keyboard navigation
- [ ] Add error handling for all API calls
- [ ] Add loading states and spinners
- [ ] Implement animations for modal transitions
- [ ] Test keyboard accessibility
- [ ] Test on mobile devices
- [ ] Add telemetry/logging for subitem actions
- [ ] Document in Storybook/component library

---

## Example Component Pseudo-Code

```typescript
// ItemActionsModal.tsx
export const ItemActionsModal: React.FC<Props> = ({
  itemId,
  instanceId,
  onClose,
  onComplete
}) => {
  const [modalState, setModalState] = useState<ModalState>({ type: 'ITEM_ACTIONS' });
  const { startWork } = useStartWorkOnItem();
  
  const handleStartWork = async () => {
    const response = await startWork(itemId, instanceId);
    setModalState({
      type: 'SUBITEM_ACTION',
      subitemId: response.next_subitem.id,
      current: 0,
      total: response.subitem_count
    });
  };
  
  if (modalState.type === 'ITEM_ACTIONS') {
    return <ItemActionsView onStartWork={handleStartWork} onClose={onClose} />;
  }
  
  if (modalState.type === 'SUBITEM_ACTION') {
    return (
      <SubitemModal
        subitemId={modalState.subitemId}
        currentIndex={modalState.current}
        total={modalState.total}
        onSubitemComplete={(nextSubitem, allDone) => {
          if (allDone) {
            setModalState({ type: 'COMPLETION_SUMMARY' });
          } else if (nextSubitem) {
            setModalState({
              type: 'SUBITEM_ACTION',
              subitemId: nextSubitem.id,
              current: modalState.current + 1,
              total: modalState.total
            });
          }
        }}
      />
    );
  }
  
  if (modalState.type === 'COMPLETION_SUMMARY') {
    return <CompletionSummaryModal onComplete={handleCompleteItem} />;
  }
};
```

---

## Performance Optimization Tips

1. **Lazy load subitem details** - Only fetch full details when showing modal
2. **Memoize subitem arrays** - Use useMemo to avoid unnecessary re-renders
3. **Debounce rapid clicks** - Prevent double-submission of forms
4. **Cache completion summary** - Reuse last fetch if available
5. **Optimize animations** - Use CSS transforms, not layout shifts
6. **Virtual scrolling** - For large subitem lists in summary view

---

## Testing Strategy

### Unit Tests
- Modal state transitions
- Progress calculation
- Error message formatting

### Integration Tests
- Full subitem workflow (create → start → complete all → finish)
- Keyboard navigation through subitem sequence
- API error recovery

### E2E Tests (Cypress/Playwright)
- User can complete item with 3 subitems
- User can skip subitems with reasons
- Summary shows correct counts and names
- Back button prevents accidental exit with warning

---

## Deployment Checklist

- [ ] Backend migration applied (2026_02_add_checklist_subitems.sql)
- [ ] Backend services tested
- [ ] API endpoints tested with Postman/Insomnia
- [ ] Frontend components implemented
- [ ] Modal flow tested end-to-end
- [ ] Error cases tested
- [ ] Accessibility tested (keyboard, screen reader)
- [ ] Mobile responsiveness verified
- [ ] Performance metrics collected (modal open time, action time)
- [ ] Documentation updated
- [ ] Team trained on new workflow
- [ ] Gradual rollout to 10% users
- [ ] Monitor error logs and telemetry
- [ ] Full rollout after 24h validation

---

## Summary

This hierarchical checklist system provides:

✅ **Sequential subitem workflow** - Users complete one subitem at a time  
✅ **Visual progress tracking** - Progress bars and X/Y counters  
✅ **Completion auditing** - Track who completed each subitem and when  
✅ **Flexible actions** - Complete, skip, or fail with reasons  
✅ **Compliance ready** - Full audit trail for regulatory requirements  

The modal flow is intuitive, non-linear, and provides clear visual feedback at every step.

**Next Steps:**
1. Implement the modal components
2. Test with β users
3. Gather feedback
4. Optimize based on usage patterns
5. Deploy to production
