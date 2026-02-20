# Frontend Implementation Gap Analysis & TODO List

## Investigation Complete - Missing Implementations Identified

### Current Status
✅ **Backend:** 100% Complete (10 endpoints, 12 service methods, full CRUD)
❌ **Frontend:** 0% Complete (No UI components created)

---

## Missing Frontend Components

### 1. Service Layer Missing Methods (5 methods needed)
**File:** `src/services/checklistApi.ts`
- [ ] `createTemplate(data)` - POST /templates
- [ ] `updateTemplate(templateId, data)` - PUT /templates/{id}
- [ ] `deleteTemplate(templateId)` - DELETE /templates/{id}
- [ ] `addTemplateItem(templateId, data)` - POST /templates/{id}/items
- [ ] `addTemplateSubitem(templateId, itemId, data)` - POST /templates/{id}/items/{itemId}/subitems

### 2. React Components Missing (4 major components)
**Directory:** `src/components/checklist/`

#### Component 1: TemplateBuilder.tsx
- [ ] Hierarchical form for creating templates
- [ ] Template name, shift type, description fields
- [ ] Dynamic item list with add/remove buttons
- [ ] Nested subitems form within each item
- [ ] Form validation
- [ ] Submit button calling `createTemplate()`

#### Component 2: TemplateList.tsx
- [ ] Grid displaying all templates
- [ ] Columns: Name, Shift, Item Count, Subitem Count, Created By, Created Date
- [ ] Filter by shift type
- [ ] Edit button → launch TemplateEditor modal
- [ ] Delete button with confirmation
- [ ] View details button

#### Component 3: TemplateEditor.tsx (Modal)
- [ ] Similar to TemplateBuilder but for editing existing templates
- [ ] Load existing template data
- [ ] Add/remove/edit items and subitems
- [ ] Submit calls `updateTemplate()`

#### Component 4: SubitemModal.tsx
- [ ] Modal for managing subitems within an item
- [ ] List subitems with edit/delete buttons
- [ ] Add subitem button
- [ ] Inline edit capability
- [ ] Save/Cancel actions

### 3. Pages Missing (1 page)
**Directory:** `src/pages/`

#### Page: TemplateManagerPage.tsx
- [ ] Main page for template management
- [ ] Header with "Create New Template" button
- [ ] Tabs or toggle between "View Templates" and "Create Template"
- [ ] When viewing: show TemplateList component
- [ ] When creating: show TemplateBuilder component
- [ ] Breadcrumb navigation
- [ ] Loading states and error messaging

### 4. Types/Contracts Missing (6 types)
**File:** `src/contracts/api.types.ts` or new file

- [ ] `CreateChecklistTemplateRequest`
- [ ] `UpdateChecklistTemplateRequest`
- [ ] `CreateTemplateItemRequest`
- [ ] `CreateTemplateSubitemRequest`
- [ ] `TemplateListResponse`
- [ ] `TemplateMutationResponse`

### 5. Routes/Navigation Missing
**File:** `src/App.tsx` or routing configuration

- [ ] Add route: `/templates` → TemplateManagerPage
- [ ] Add navigation menu item for "Template Manager"
- [ ] Add sidebar/menu link

---

## What Already Exists (Reusable)

✅ `src/services/checklistApi.ts` - Already has `getTemplates()` method
✅ `src/components/checklist/` - Has existing checklist components to reference for styling
✅ `src/pages/ChecklistsPage.tsx` - Can reference page layout patterns
✅ `src/types/` - Existing TypeScript types to extend
✅ API base setup in `src/services/api.ts` - Already configured with axios

---

## Implementation Order (Next Chat)

### Phase 1: Backend Integration (1-2 hours)
1. Add 5 missing methods to `checklistApi.ts`
2. Create/update TypeScript types for requests/responses

### Phase 2: Core Components (3-4 hours)
1. Create TemplateList component (display templates)
2. Create TemplateBuilder component (create templates)
3. Create TemplateEditor component (edit templates)
4. Create SubitemModal component (manage subitems)

### Phase 3: Pages & Routing (1-2 hours)
1. Create TemplateManagerPage
2. Add routes to App.tsx
3. Add navigation menu items
4. Add loading/error states

### Phase 4: Testing & Styling (1-2 hours)
1. Test all CRUD operations
2. Add CSS/styling
3. Test nested form interactions
4. Test authorization (non-admins should only see their section templates)

---

## Detailed Component Requirements

### TemplateBuilder Form Structure
```
Template Details
  - Name (text input, required)
  - Shift (select: MORNING/AFTERNOON/NIGHT)
  - Description (textarea)
  - Active (checkbox)
  - Section (select, only if admin)

Items List
  [ + Add Item button ]
  
  For Each Item:
    - Title (text input)
    - Description (textarea)
    - Item Type (select)
    - Is Required (checkbox)
    - Severity (select: LOW/MEDIUM/HIGH)
    - Sort Order (number)
    
    [ + Add Subitem button ]
    
    Subitems List:
      For Each Subitem:
        - Title (text input)
        - Description (textarea)
        - Item Type (select)
        - Is Required (checkbox)
        - Severity (select)
        - Sort Order (number)
        - [Delete button]
    
    [ Delete Item button ]

[Save Template] [Cancel]
```

### Data Flow for Create Template
```
User fills form → Click Save
→ Validate data
→ Call checklistApi.createTemplate(data)
→ POST /api/v1/checklists/templates
→ Receive TemplateMutationResponse
→ Show success toast
→ Navigate to TemplateList or clear form
```

---

## API Endpoints to Integrate

All endpoints already exist on backend:

**Template Management:**
- `GET /checklists/templates` - List (already has method)
- `GET /checklists/templates/{id}` - Get one (NEEDS method)
- `POST /checklists/templates` - Create (NEEDS method)
- `PUT /checklists/templates/{id}` - Update (NEEDS method)
- `DELETE /checklists/templates/{id}` - Delete (NEEDS method)

**Template Items:**
- `POST /checklists/templates/{id}/items` - Add item (NEEDS method)
- `PUT /checklists/templates/{id}/items/{itemId}` - Update item (NEEDS method)
- `DELETE /checklists/templates/{id}/items/{itemId}` - Delete item (NEEDS method)

**Template Subitems:**
- `POST /checklists/templates/{id}/items/{itemId}/subitems` - Add subitem (NEEDS method)
- `PUT /checklists/templates/{id}/items/{itemId}/subitems/{subitemId}` - Update subitem (NEEDS method)
- `DELETE /checklists/templates/{id}/items/{itemId}/subitems/{subitemId}` - Delete subitem (NEEDS method)

---

## Key Design Patterns to Follow

1. **Forms:** Use controlled components with React state
2. **Validation:** Validate on blur and on submit
3. **Loading:** Show spinners during API calls
4. **Errors:** Show error toasts and messages
5. **Authorization:** Check user permissions before showing edit/delete buttons
6. **Nesting:** Use component composition for nested forms (ItemForm inside TemplateBuilder, SubitemForm inside ItemForm)
7. **State:** Consider using Context or local state (avoid Redux for simplicity)

---

## Files to Create/Modify (Next Chat)

### Create Files:
1. `src/components/checklist/TemplateBuilder.tsx`
2. `src/components/checklist/TemplateBuilder.css`
3. `src/components/checklist/TemplateList.tsx`
4. `src/components/checklist/TemplateList.css`
5. `src/components/checklist/TemplateEditor.tsx`
6. `src/components/checklist/TemplateEditor.css`
7. `src/components/checklist/SubitemModal.tsx`
8. `src/components/checklist/SubitemModal.css`
9. `src/pages/TemplateManagerPage.tsx`
10. `src/pages/TemplateManagerPage.css`

### Modify Files:
1. `src/services/checklistApi.ts` - Add 5 new methods
2. `src/contracts/api.types.ts` - Add template types
3. `src/App.tsx` - Add routes
4. Navigation/Menu component - Add link to templates

---

## Quick Checklist for Next Chat

Before starting:
- [ ] Read this entire document
- [ ] Understand the backend API structure (10 endpoints, 3-level hierarchy)
- [ ] Identify all files to create/modify
- [ ] Understand form nesting requirements
- [ ] Review existing checklist components for pattern reference

Start with:
- [ ] Add methods to checklistApi.ts (5 methods)
- [ ] Add TypeScript types
- [ ] Build TemplateList component first (simplest)
- [ ] Build TemplateBuilder component (most complex)
- [ ] Create page and wire everything together

---

## Notes for Next Agent

- Backend is 100% complete and verified
- All 10 API endpoints exist and are fully functional
- Database supports 3-level hierarchy (Template → Items → Subitems)
- Authorization checks already implemented on backend
- Frontend needs to respect: admin vs non-admin permissions, section-scoped access
- Component should support nested array manipulation in React forms
- Consider using a form library like react-hook-form for complex nested forms

---

**Status:** Ready for Frontend Implementation  
**Estimated Time:** 8-10 hours for complete implementation  
**Complexity:** Medium-High (nested forms, CRUD operations, state management)
