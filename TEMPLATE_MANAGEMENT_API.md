# Template Management API Documentation

Complete API reference for hierarchical checklist template management with nested items and subitems.

## Overview

The Template Management API provides comprehensive CRUD operations for creating, reading, updating, and deleting checklist templates with hierarchical structure (Template → Items → Subitems).

**Base URL:** `/checklists`

**Authorization:** All endpoints require authentication and `MANAGE_TEMPLATES` capability (admin bypass applies)

---

## Collection: Template Operations

### GET /templates
List all checklist templates with optional filtering

**Query Parameters:**
- `shift` (string, optional): Filter by shift type - `MORNING` | `AFTERNOON` | `NIGHT`
- `active_only` (boolean, default: true): Only return active templates
- `section_id` (UUID, optional): Scope templates to section (non-admins auto-scoped)

**Response:** 200 OK
```json
[
  {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "name": "Morning Shift Checklist",
    "description": "Standard morning operations checklist",
    "shift": "MORNING",
    "is_active": true,
    "created_by": "user-123",
    "created_at": "2024-02-26T08:00:00Z",
    "section_id": null,
    "items": [
      {
        "id": "item-1",
        "title": "System Startup",
        "description": "Start all systems",
        "item_type": "GATEWAY_ROUTING",
        "is_required": true,
        "scheduled_time": null,
        "sort_order": 1,
        "subitems": []
      }
    ]
  }
]
```

---

### GET /templates/{template_id}
Retrieve a specific template by ID

**Path Parameters:**
- `template_id` (UUID): Template identifier

**Response:** 200 OK (same as single item from GET /templates)

**Error Responses:**
- 404 Not Found: Template does not exist
- 403 Forbidden: Insufficient permissions (non-admin with different section)

---

### POST /templates
Create a new checklist template with nested items and subitems

**Request Body:**
```json
{
  "name": "Evening Shift Checklist",
  "shift": "AFTERNOON",
  "description": "Standard evening operations",
  "is_active": true,
  "section_id": null,
  "items": [
    {
      "title": "Site Walkthrough",
      "description": "Inspect all operational areas",
      "item_type": "SITE_INSPECTION",
      "is_required": true,
      "severity": "MEDIUM",
      "sort_order": 1,
      "subitems": [
        {
          "title": "Check security gates",
          "description": "Verify all gates operational",
          "item_type": "SECURITY_CHECK",
          "is_required": true,
          "severity": "HIGH",
          "sort_order": 1
        },
        {
          "title": "Inspect lighting",
          "description": "Check all exterior lighting",
          "item_type": "INSPECTION",
          "is_required": false,
          "severity": "LOW",
          "sort_order": 2
        }
      ]
    },
    {
      "title": "System Checkout",
      "description": "Prepare systems for night shift",
      "item_type": "SYSTEM_CONFIGURATION",
      "is_required": true,
      "severity": "HIGH",
      "sort_order": 2,
      "subitems": []
    }
  ]
}
```

**Response:** 201 Created
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440001",
  "action": "created",
  "message": "Template 'Evening Shift Checklist' created successfully with 2 items",
  "template": {
    "id": "550e8400-e29b-41d4-a716-446655440001",
    "name": "Evening Shift Checklist",
    "shift": "AFTERNOON",
    "description": "Standard evening operations",
    "is_active": true,
    "created_by": "user-123",
    "created_at": "2024-02-26T09:15:00Z",
    "section_id": null,
    "items": [
      {
        "id": "item-2",
        "title": "Site Walkthrough",
        "description": "Inspect all operational areas",
        "item_type": "SITE_INSPECTION",
        "is_required": true,
        "severity": "MEDIUM",
        "sort_order": 1,
        "subitems": [
          {
            "id": "subitem-1",
            "title": "Check security gates",
            "description": "Verify all gates operational",
            "item_type": "SECURITY_CHECK",
            "is_required": true,
            "severity": "HIGH",
            "sort_order": 1
          },
          {
            "id": "subitem-2",
            "title": "Inspect lighting",
            "description": "Check all exterior lighting",
            "item_type": "INSPECTION",
            "is_required": false,
            "severity": "LOW",
            "sort_order": 2
          }
        ]
      },
      {
        "id": "item-3",
        "title": "System Checkout",
        "description": "Prepare systems for night shift",
        "item_type": "SYSTEM_CONFIGURATION",
        "is_required": true,
        "severity": "HIGH",
        "sort_order": 2,
        "subitems": []
      }
    ]
  }
}
```

**Error Responses:**
- 400 Bad Request: Invalid item_type or missing required fields
- 403 Forbidden: Insufficient permissions

---

### PUT /templates/{template_id}
Update a template

**Request Body:**
```json
{
  "name": "Evening Shift Checklist v2",
  "shift": "AFTERNOON",
  "description": "Updated evening operations",
  "is_active": true
}
```

**Response:** 200 OK
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440001",
  "action": "updated",
  "message": "Template updated successfully",
  "template": { /* full template object */ }
}
```

---

### DELETE /templates/{template_id}
Archive (soft delete) a template

**Response:** 200 OK
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440001",
  "action": "deleted",
  "message": "Template 'Evening Shift Checklist v2' archived successfully"
}
```

**Notes:**
- Soft delete (archives template, doesn't permanently remove)
- Sets `is_active = false`
- Existing template instances continue to function

---

## Collection: Template Item Operations

### POST /templates/{template_id}/items
Add a new item to a template

**Path Parameters:**
- `template_id` (UUID): Parent template

**Request Body:**
```json
{
  "title": "Equipment Check",
  "description": "Verify all equipment operational",
  "item_type": "EQUIPMENT_INSPECTION",
  "is_required": true,
  "severity": "MEDIUM",
  "notify_before_minutes": 15,
  "sort_order": 3,
  "subitems": [
    {
      "title": "Check compressor",
      "description": "Test compressor operation",
      "item_type": "EQUIPMENT_INSPECTION",
      "is_required": true,
      "severity": "HIGH",
      "sort_order": 1
    }
  ]
}
```

**Response:** 201 Created
```json
{
  "id": "item-4",
  "template_id": "550e8400-e29b-41d4-a716-446655440001",
  "action": "created",
  "message": "Item 'Equipment Check' added with 1 subitems",
  "item": {
    "id": "item-4",
    "title": "Equipment Check",
    "description": "Verify all equipment operational",
    "item_type": "EQUIPMENT_INSPECTION",
    "is_required": true,
    "severity": "MEDIUM",
    "sort_order": 3,
    "subitems": [
      {
        "id": "subitem-3",
        "title": "Check compressor",
        "description": "Test compressor operation",
        "item_type": "EQUIPMENT_INSPECTION",
        "is_required": true,
        "severity": "HIGH",
        "sort_order": 1
      }
    ]
  }
}
```

---

### PUT /templates/{template_id}/items/{item_id}
Update a template item

**Path Parameters:**
- `template_id` (UUID): Parent template
- `item_id` (UUID): Item to update

**Request Body:**
```json
{
  "title": "Equipment Check v2",
  "description": "Verify all equipment operational - updated",
  "item_type": "EQUIPMENT_INSPECTION",
  "is_required": true,
  "severity": "HIGH",
  "sort_order": 3
}
```

**Response:** 200 OK
```json
{
  "id": "item-4",
  "template_id": "550e8400-e29b-41d4-a716-446655440001",
  "action": "updated",
  "message": "Item updated successfully"
}
```

---

### DELETE /templates/{template_id}/items/{item_id}
Remove an item from template (cascades to subitems)

**Response:** 200 OK
```json
{
  "id": "item-4",
  "template_id": "550e8400-e29b-41d4-a716-446655440001",
  "action": "deleted",
  "message": "Item deleted successfully"
}
```

**Notes:**
- Cascades delete to all subitems
- Items in active instances are NOT affected

---

## Collection: Template Subitem Operations

### POST /templates/{template_id}/items/{item_id}/subitems
Add a new subitem to a template item

**Path Parameters:**
- `template_id` (UUID): Parent template
- `item_id` (UUID): Parent item

**Request Body:**
```json
{
  "title": "Check air filter",
  "description": "Inspect and clean if needed",
  "item_type": "MAINTENANCE",
  "is_required": true,
  "severity": "MEDIUM",
  "sort_order": 2
}
```

**Response:** 201 Created
```json
{
  "id": "subitem-4",
  "item_id": "item-4",
  "action": "created",
  "message": "Subitem 'Check air filter' added successfully",
  "subitem": {
    "id": "subitem-4",
    "title": "Check air filter",
    "description": "Inspect and clean if needed",
    "item_type": "MAINTENANCE",
    "is_required": true,
    "severity": "MEDIUM",
    "sort_order": 2
  }
}
```

---

### PUT /templates/{template_id}/items/{item_id}/subitems/{subitem_id}
Update a template subitem

**Path Parameters:**
- `template_id` (UUID): Parent template
- `item_id` (UUID): Parent item
- `subitem_id` (UUID): Subitem to update

**Request Body:**
```json
{
  "title": "Check air filter - CRITICAL",
  "description": "Inspect and replace if damaged",
  "item_type": "MAINTENANCE",
  "is_required": true,
  "severity": "HIGH",
  "sort_order": 2
}
```

**Response:** 200 OK
```json
{
  "id": "subitem-4",
  "item_id": "item-4",
  "action": "updated",
  "message": "Subitem updated successfully"
}
```

---

### DELETE /templates/{template_id}/items/{item_id}/subitems/{subitem_id}
Remove a subitem from template item

**Path Parameters:**
- `template_id` (UUID): Parent template
- `item_id` (UUID): Parent item
- `subitem_id` (UUID): Subitem to delete

**Response:** 200 OK
```json
{
  "id": "subitem-4",
  "item_id": "item-4",
  "action": "deleted",
  "message": "Subitem deleted successfully"
}
```

---

## Data Types

### ShiftType (enum)
- `MORNING`
- `AFTERNOON`
- `NIGHT`

### ChecklistItemType (enum)
- `GATEWAY_ROUTING`
- `DATA_VALIDATION`
- `SECURITY_CHECK`
- `SYSTEM_CONFIGURATION`
- `SITE_INSPECTION`
- `EQUIPMENT_INSPECTION`
- `PROTOCOL_COMPLIANCE`
- `MAINTENANCE`
- `INSPECTION`
- `RECORD_KEEPING`

### Severity (enum)
- `LOW` - Minor/informational
- `MEDIUM` - Standard operational requirement
- `HIGH` - Critical/blocking

### ItemStatus (enum)
- `PENDING` - Not yet started
- `IN_PROGRESS` - Currently being worked
- `COMPLETED` - Finished successfully
- `FAILED` - Failed to complete
- `BLOCKED` - Cannot proceed due to dependency

---

## Authorization Rules

### Permission Scoping
- **Admins**: Full access to all templates across all sections
- **Non-Admins**: Can only access/modify templates in their assigned section

### Required Capabilities
- All template mutation operations (POST, PUT, DELETE) require `MANAGE_TEMPLATES` capability

### Section Auto-Scoping
- When creating templates as non-admin, template is automatically assigned to user's section
- Cannot override section_id unless admin

---

## Error Handling

### Standard Error Response
```json
{
  "detail": "Template not found"
}
```

### Status Codes
- `200 OK` - Successful read or update
- `201 Created` - Successfully created resource
- `400 Bad Request` - Invalid input (missing fields, invalid enum values)
- `403 Forbidden` - Insufficient permissions
- `404 Not Found` - Resource not found
- `500 Internal Server Error` - Server error

---

## Audit Trail

All template operations are logged with ops events including:
- Event Type: `TEMPLATE_CREATED`, `TEMPLATE_UPDATED`, `TEMPLATE_DELETED`, etc.
- User Information: ID, username
- Entity Information: Template ID, names
- Timestamp: ISO 8601 format
- Payload: Operation-specific data

---

## Usage Examples

### Create Complete Template with Items and Subitems
```bash
curl -X POST http://localhost:8000/checklists/templates \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Night Shift Operations",
    "shift": "NIGHT",
    "description": "Complete night shift procedures",
    "is_active": true,
    "items": [
      {
        "title": "Opening Procedures",
        "description": "Initial night shift setup",
        "item_type": "SYSTEM_CONFIGURATION",
        "is_required": true,
        "severity": "HIGH",
        "sort_order": 1,
        "subitems": [
          {
            "title": "Activate monitoring",
            "description": "Start surveillance systems",
            "item_type": "SECURITY_CHECK",
            "is_required": true,
            "severity": "HIGH",
            "sort_order": 1
          },
          {
            "title": "Verify team presence",
            "description": "Confirm all staff on duty",
            "item_type": "RECORD_KEEPING",
            "is_required": true,
            "severity": "MEDIUM",
            "sort_order": 2
          }
        ]
      }
    ]
  }'
```

### List Templates by Shift
```bash
curl -X GET "http://localhost:8000/checklists/templates?shift=NIGHT&active_only=true" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### Update Template Metadata
```bash
curl -X PUT http://localhost:8000/checklists/templates/{template_id} \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Night Shift Operations - Updated",
    "description": "Complete night shift procedures with additional safety checks",
    "is_active": true
  }'
```

### Add Subitem to Existing Item
```bash
curl -X POST http://localhost:8000/checklists/templates/{template_id}/items/{item_id}/subitems \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Check emergency protocols",
    "description": "Review and acknowledge emergency procedures",
    "item_type": "PROTOCOL_COMPLIANCE",
    "is_required": true,
    "severity": "HIGH",
    "sort_order": 3
  }'
```

---

## Implementation Status

### Completed
✅ Template CRUD operations (GET, POST, PUT, DELETE)
✅ Item management within templates
✅ Subitem management within items
✅ Nested creation (single call creates template + items + subitems)
✅ Authorization and permission scoping
✅ Ops event logging for audit trail
✅ Comprehensive error handling

### In Progress
🔄 Frontend template builder UI
🔄 Template duplication endpoint
🔄 Template versioning system

### Future Enhancements
⏳ Template sharing between sections
⏳ Template import/export functionality
⏳ Template preview before deployment
⏳ Bulk template operations

---

*Last Updated: February 26, 2024*
*API Version: 1.0*
