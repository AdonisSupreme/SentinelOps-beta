# Template Management - Quick Implementation Guide

Developer guide for integrating hierarchical template management into the frontend.

## Quick Start

### 1. Setup Template Service

Create `src/services/templateService.ts`:

```typescript
import { API_BASE_URL } from '@/config';

export interface Template {
  id: string;
  name: string;
  shift: 'MORNING' | 'AFTERNOON' | 'NIGHT';
  description: string;
  is_active: boolean;
  created_by: string;
  created_at: string;
  section_id?: string;
  items: TemplateItem[];
}

export interface TemplateItem {
  id: string;
  title: string;
  description: string;
  item_type: string;
  is_required: boolean;
  severity: 'LOW' | 'MEDIUM' | 'HIGH';
  sort_order: number;
  notify_before_minutes?: number;
  subitems: TemplateSubitem[];
}

export interface TemplateSubitem {
  id: string;
  title: string;
  description: string;
  item_type: string;
  is_required: boolean;
  severity: 'LOW' | 'MEDIUM' | 'HIGH';
  sort_order: number;
}

class TemplateService {
  private baseUrl = `${API_BASE_URL}/checklists`;

  async listTemplates(filters?: {
    shift?: string;
    activeOnly?: boolean;
    sectionId?: string;
  }): Promise<Template[]> {
    const params = new URLSearchParams();
    if (filters?.shift) params.append('shift', filters.shift);
    if (filters?.activeOnly !== undefined) params.append('active_only', String(filters.activeOnly));
    if (filters?.sectionId) params.append('section_id', filters.sectionId);

    const response = await fetch(`${this.baseUrl}/templates?${params}`, {
      method: 'GET',
      credentials: 'include',
    });

    if (!response.ok) throw new Error(`Failed to list templates: ${response.status}`);
    return response.json();
  }

  async getTemplate(templateId: string): Promise<Template> {
    const response = await fetch(`${this.baseUrl}/templates/${templateId}`, {
      method: 'GET',
      credentials: 'include',
    });

    if (!response.ok) throw new Error(`Failed to get template: ${response.status}`);
    return response.json();
  }

  async createTemplate(data: {
    name: string;
    shift: 'MORNING' | 'AFTERNOON' | 'NIGHT';
    description: string;
    is_active: boolean;
    section_id?: string;
    items?: any[];
  }): Promise<Template> {
    const response = await fetch(`${this.baseUrl}/templates`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
      credentials: 'include',
    });

    if (!response.ok) throw new Error(`Failed to create template: ${response.status}`);
    const result = await response.json();
    return result.template;
  }

  async updateTemplate(templateId: string, data: {
    name?: string;
    shift?: string;
    description?: string;
    is_active?: boolean;
  }): Promise<Template> {
    const response = await fetch(`${this.baseUrl}/templates/${templateId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
      credentials: 'include',
    });

    if (!response.ok) throw new Error(`Failed to update template: ${response.status}`);
    const result = await response.json();
    return result.template;
  }

  async deleteTemplate(templateId: string): Promise<void> {
    const response = await fetch(`${this.baseUrl}/templates/${templateId}`, {
      method: 'DELETE',
      credentials: 'include',
    });

    if (!response.ok) throw new Error(`Failed to delete template: ${response.status}`);
  }

  // Item Management
  async addTemplateItem(templateId: string, data: {
    title: string;
    description: string;
    item_type: string;
    is_required: boolean;
    severity: string;
    notify_before_minutes?: number;
    sort_order: number;
    subitems?: any[];
  }): Promise<TemplateItem> {
    const response = await fetch(`${this.baseUrl}/templates/${templateId}/items`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
      credentials: 'include',
    });

    if (!response.ok) throw new Error(`Failed to add item: ${response.status}`);
    const result = await response.json();
    return result.item;
  }

  async updateTemplateItem(templateId: string, itemId: string, data: Partial<TemplateItem>): Promise<void> {
    const response = await fetch(`${this.baseUrl}/templates/${templateId}/items/${itemId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
      credentials: 'include',
    });

    if (!response.ok) throw new Error(`Failed to update item: ${response.status}`);
  }

  async deleteTemplateItem(templateId: string, itemId: string): Promise<void> {
    const response = await fetch(`${this.baseUrl}/templates/${templateId}/items/${itemId}`, {
      method: 'DELETE',
      credentials: 'include',
    });

    if (!response.ok) throw new Error(`Failed to delete item: ${response.status}`);
  }

  // Subitem Management
  async addSubitem(templateId: string, itemId: string, data: {
    title: string;
    description: string;
    item_type: string;
    is_required: boolean;
    severity: string;
    sort_order: number;
  }): Promise<TemplateSubitem> {
    const response = await fetch(`${this.baseUrl}/templates/${templateId}/items/${itemId}/subitems`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
      credentials: 'include',
    });

    if (!response.ok) throw new Error(`Failed to add subitem: ${response.status}`);
    const result = await response.json();
    return result.subitem;
  }

  async updateSubitem(templateId: string, itemId: string, subitemId: string, data: Partial<TemplateSubitem>): Promise<void> {
    const response = await fetch(`${this.baseUrl}/templates/${templateId}/items/${itemId}/subitems/${subitemId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
      credentials: 'include',
    });

    if (!response.ok) throw new Error(`Failed to update subitem: ${response.status}`);
  }

  async deleteSubitem(templateId: string, itemId: string, subitemId: string): Promise<void> {
    const response = await fetch(`${this.baseUrl}/templates/${templateId}/items/${itemId}/subitems/${subitemId}`, {
      method: 'DELETE',
      credentials: 'include',
    });

    if (!response.ok) throw new Error(`Failed to delete subitem: ${response.status}`);
  }
}

export const templateService = new TemplateService();
```

### 2. Create Template Builder Component

Create `src/components/TemplateBuilder.tsx`:

```typescript
import React, { useState, useCallback } from 'react';
import { Template, TemplateItem, TemplateSubitem, templateService } from '@/services/templateService';
import './TemplateBuilder.css';

interface TemplateBuilderProps {
  onTemplateSaved?: (template: Template) => void;
  initialTemplate?: Template;
}

export const TemplateBuilder: React.FC<TemplateBuilderProps> = ({
  onTemplateSaved,
  initialTemplate,
}) => {
  const [template, setTemplate] = useState<Partial<Template>>(
    initialTemplate || {
      name: '',
      shift: 'MORNING',
      description: '',
      is_active: true,
      items: [],
    }
  );

  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleTemplateSave = useCallback(async () => {
    setIsLoading(true);
    setError(null);

    try {
      if (!template.name || !template.shift) {
        setError('Template name and shift are required');
        return;
      }

      const savedTemplate = await templateService.createTemplate({
        name: template.name,
        shift: template.shift,
        description: template.description || '',
        is_active: template.is_active ?? true,
        items: template.items || [],
      });

      onTemplateSaved?.(savedTemplate);
    } catch (err) {
      setError(`Failed to save template: ${err instanceof Error ? err.message : 'Unknown error'}`);
    } finally {
      setIsLoading(false);
    }
  }, [template, onTemplateSaved]);

  const addItem = useCallback(() => {
    setTemplate((prev) => ({
      ...prev,
      items: [
        ...(prev.items || []),
        {
          id: `temp-${Date.now()}`,
          title: '',
          description: '',
          item_type: 'GATEWAY_ROUTING',
          is_required: true,
          severity: 'MEDIUM',
          sort_order: (prev.items?.length || 0) + 1,
          subitems: [],
        } as any,
      ],
    }));
  }, []);

  const removeItem = useCallback((itemIndex: number) => {
    setTemplate((prev) => ({
      ...prev,
      items: prev.items?.filter((_, i) => i !== itemIndex),
    }));
  }, []);

  const updateItem = useCallback((itemIndex: number, updates: Partial<TemplateItem>) => {
    setTemplate((prev) => ({
      ...prev,
      items: prev.items?.map((item, i) => (i === itemIndex ? { ...item, ...updates } : item)),
    }));
  }, []);

  const addSubitem = useCallback((itemIndex: number) => {
    setTemplate((prev) => ({
      ...prev,
      items: prev.items?.map((item, i) =>
        i === itemIndex
          ? {
              ...item,
              subitems: [
                ...(item.subitems || []),
                {
                  id: `temp-${Date.now()}`,
                  title: '',
                  description: '',
                  item_type: 'PLATFORM_OPERATIONS',
                  is_required: false,
                  severity: 'LOW',
                  sort_order: (item.subitems?.length || 0) + 1,
                } as any,
              ],
            }
          : item
      ),
    }));
  }, []);

  const removeSubitem = useCallback((itemIndex: number, subitemIndex: number) => {
    setTemplate((prev) => ({
      ...prev,
      items: prev.items?.map((item, i) =>
        i === itemIndex
          ? {
              ...item,
              subitems: item.subitems?.filter((_, j) => j !== subitemIndex),
            }
          : item
      ),
    }));
  }, []);

  const updateSubitem = useCallback(
    (itemIndex: number, subitemIndex: number, updates: Partial<TemplateSubitem>) => {
      setTemplate((prev) => ({
        ...prev,
        items: prev.items?.map((item, i) =>
          i === itemIndex
            ? {
                ...item,
                subitems: item.subitems?.map((subitem, j) =>
                  j === subitemIndex ? { ...subitem, ...updates } : subitem
                ),
              }
            : item
        ),
      }));
    },
    []
  );

  return (
    <div className="template-builder">
      <div className="template-header">
        <h2>Template Builder</h2>
      </div>

      {error && <div className="error-message">{error}</div>}

      <div className="template-form">
        <div className="form-group">
          <label>Template Name *</label>
          <input
            type="text"
            value={template.name || ''}
            onChange={(e) => setTemplate((prev) => ({ ...prev, name: e.target.value }))}
            placeholder="e.g., Morning Shift Checklist"
          />
        </div>

        <div className="form-group">
          <label>Shift *</label>
          <select
            value={template.shift || 'MORNING'}
            onChange={(e) =>
              setTemplate((prev) => ({ ...prev, shift: e.target.value as any }))
            }
          >
            <option value="MORNING">Morning</option>
            <option value="AFTERNOON">Afternoon</option>
            <option value="NIGHT">Night</option>
          </select>
        </div>

        <div className="form-group">
          <label>Description</label>
          <textarea
            value={template.description || ''}
            onChange={(e) => setTemplate((prev) => ({ ...prev, description: e.target.value }))}
            placeholder="Describe the purpose of this template..."
            rows={3}
          />
        </div>

        <div className="form-group">
          <label>
            <input
              type="checkbox"
              checked={template.is_active ?? true}
              onChange={(e) => setTemplate((prev) => ({ ...prev, is_active: e.target.checked }))}
            />
            Active
          </label>
        </div>
      </div>

      <div className="items-section">
        <div className="section-header">
          <h3>Checklist Items</h3>
          <button onClick={addItem} className="btn-secondary">
            + Add Item
          </button>
        </div>

        <div className="items-list">
          {template.items?.map((item, itemIndex) => (
            <div key={itemIndex} className="item-card">
              <div className="item-header">
                <input
                  type="text"
                  value={item.title || ''}
                  onChange={(e) =>
                    updateItem(itemIndex, { title: e.target.value })
                  }
                  placeholder="Item title"
                  className="item-title-input"
                />
                <button
                  onClick={() => removeItem(itemIndex)}
                  className="btn-danger"
                >
                  ✕
                </button>
              </div>

              <textarea
                value={item.description || ''}
                onChange={(e) =>
                  updateItem(itemIndex, { description: e.target.value })
                }
                placeholder="Item description"
                rows={2}
              />

              <div className="item-properties">
                <select
                  value={item.item_type || ''}
                  onChange={(e) => updateItem(itemIndex, { item_type: e.target.value })}
                >
                  <option value="GATEWAY_ROUTING">Gateway Routing</option>
                  <option value="DATA_VALIDATION">Data Validation</option>
                  <option value="SECURITY_CHECK">Security Check</option>
                  <option value="SYSTEM_CONFIGURATION">System Configuration</option>
                </select>

                <select
                  value={item.severity || ''}
                  onChange={(e) => updateItem(itemIndex, { severity: e.target.value as any })}
                >
                  <option value="LOW">Low</option>
                  <option value="MEDIUM">Medium</option>
                  <option value="HIGH">High</option>
                </select>

                <label>
                  <input
                    type="checkbox"
                    checked={item.is_required ?? true}
                    onChange={(e) => updateItem(itemIndex, { is_required: e.target.checked })}
                  />
                  Required
                </label>
              </div>

              <div className="subitems-section">
                <div className="subitems-header">
                  <h4>Subitems</h4>
                  <button
                    onClick={() => addSubitem(itemIndex)}
                    className="btn-secondary btn-small"
                  >
                    + Add Subitem
                  </button>
                </div>

                <div className="subitems-list">
                  {item.subitems?.map((subitem, subitemIndex) => (
                    <div key={subitemIndex} className="subitem-card">
                      <div className="subitem-header">
                        <input
                          type="text"
                          value={subitem.title || ''}
                          onChange={(e) =>
                            updateSubitem(itemIndex, subitemIndex, { title: e.target.value })
                          }
                          placeholder="Subitem title"
                          className="subitem-title-input"
                        />
                        <button
                          onClick={() => removeSubitem(itemIndex, subitemIndex)}
                          className="btn-danger btn-small"
                        >
                          ✕
                        </button>
                      </div>

                      <textarea
                        value={subitem.description || ''}
                        onChange={(e) =>
                          updateSubitem(itemIndex, subitemIndex, { description: e.target.value })
                        }
                        placeholder="Subitem description"
                        rows={1}
                      />

                      <div className="subitem-properties">
                        <label>
                          <input
                            type="checkbox"
                            checked={subitem.is_required ?? false}
                            onChange={(e) =>
                              updateSubitem(itemIndex, subitemIndex, {
                                is_required: e.target.checked,
                              })
                            }
                          />
                          Required
                        </label>

                        <select
                          value={subitem.severity || ''}
                          onChange={(e) =>
                            updateSubitem(itemIndex, subitemIndex, {
                              severity: e.target.value as any,
                            })
                          }
                        >
                          <option value="LOW">Low Priority</option>
                          <option value="MEDIUM">Medium Priority</option>
                          <option value="HIGH">High Priority</option>
                        </select>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="builder-actions">
        <button
          onClick={handleTemplateSave}
          disabled={isLoading}
          className="btn-primary"
        >
          {isLoading ? 'Saving...' : 'Save Template'}
        </button>
      </div>
    </div>
  );
};
```

### 3. Create Template Manager Page

Create `src/pages/TemplateManager.tsx`:

```typescript
import React, { useState, useEffect } from 'react';
import { Template, templateService } from '@/services/templateService';
import { TemplateBuilder } from '@/components/TemplateBuilder';
import { TemplateList } from '@/components/TemplateList';

export const TemplateManager: React.FC = () => {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [selectedShift, setSelectedShift] = useState<string | undefined>(undefined);
  const [showBuilder, setShowBuilder] = useState(false);
  const [isLoading, setIsLoading] = useState(false);

  const loadTemplates = async () => {
    setIsLoading(true);
    try {
      const data = await templateService.listTemplates({
        shift: selectedShift,
        activeOnly: true,
      });
      setTemplates(data);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadTemplates();
  }, [selectedShift]);

  const handleTemplateSaved = () => {
    setShowBuilder(false);
    loadTemplates();
  };

  return (
    <div className="template-manager">
      <header className="page-header">
        <h1>Template Manager</h1>
        <button
          onClick={() => setShowBuilder(!showBuilder)}
          className="btn-primary"
        >
          {showBuilder ? 'View Templates' : '+ Create Template'}
        </button>
      </header>

      {showBuilder ? (
        <TemplateBuilder onTemplateSaved={handleTemplateSaved} />
      ) : (
        <>
          <div className="filter-bar">
            <select
              value={selectedShift || ''}
              onChange={(e) => setSelectedShift(e.target.value || undefined)}
            >
              <option value="">All Shifts</option>
              <option value="MORNING">Morning</option>
              <option value="AFTERNOON">Afternoon</option>
              <option value="NIGHT">Night</option>
            </select>
          </div>

          <TemplateList
            templates={templates}
            isLoading={isLoading}
            onRefresh={loadTemplates}
          />
        </>
      )}
    </div>
  );
};
```

## Integration Patterns

### Pattern 1: Template Selection Modal

When deploying a checklist instance, users select a template:

```typescript
async function deployChecklist(shiftType: string, teamId: string) {
  const templates = await templateService.listTemplates({ shift: shiftType });
  
  // Show template selection modal
  const selectedTemplate = await showTemplateSelectionModal(templates);
  
  // Create instance from template
  const instance = await checklistService.createInstance({
    template_id: selectedTemplate.id,
    shift: shiftType,
    team_id: teamId,
  });
  
  return instance;
}
```

### Pattern 2: Real-time Template Editing

For advanced users, allow in-place template editing:

```typescript
async function updateItemInTemplate(
  templateId: string,
  itemId: string,
  updates: Partial<TemplateItem>
) {
  await templateService.updateTemplateItem(templateId, itemId, updates);
  
  // If template is currently in use, notify affected instances
  const instances = await getInstancesUsingTemplate(templateId);
  instances.forEach((instance) => {
    notifyInstanceUpdate(instance.id);
  });
}
```

### Pattern 3: Template Cloning

Create variants of existing templates:

```typescript
async function cloneTemplate(sourceTemplateId: string, newName: string) {
  const source = await templateService.getTemplate(sourceTemplateId);
  
  const cloned = await templateService.createTemplate({
    name: newName,
    shift: source.shift,
    description: `Clone of ${source.name}`,
    is_active: true,
    items: source.items.map((item) => ({
      ...item,
      id: undefined, // Let backend generate new IDs
    })),
  });
  
  return cloned;
}
```

## State Management Integration (Redux)

If using Redux:

```typescript
// slices/templateSlice.ts
import { createSlice, createAsyncThunk } from '@reduxjs/toolkit';
import { templateService, Template } from '@/services/templateService';

export const fetchTemplates = createAsyncThunk(
  'templates/fetchTemplates',
  async (filters: { shift?: string; activeOnly?: boolean }) => {
    return templateService.listTemplates(filters);
  }
);

export const createTemplate = createAsyncThunk(
  'templates/createTemplate',
  async (data: any) => {
    return templateService.createTemplate(data);
  }
);

const templateSlice = createSlice({
  name: 'templates',
  initialState: {
    items: [] as Template[],
    loading: false,
    error: null as string | null,
  },
  extraReducers: (builder) => {
    builder
      .addCase(fetchTemplates.pending, (state) => {
        state.loading = true;
        state.error = null;
      })
      .addCase(fetchTemplates.fulfilled, (state, action) => {
        state.items = action.payload;
        state.loading = false;
      })
      .addCase(fetchTemplates.rejected, (state, action) => {
        state.loading = false;
        state.error = action.error.message || 'Failed to fetch templates';
      })
      .addCase(createTemplate.fulfilled, (state, action) => {
        state.items.push(action.payload);
      });
  },
});

export default templateSlice.reducer;
```

## Error Handling

```typescript
try {
  const template = await templateService.createTemplate(data);
  // Handle success
} catch (error) {
  if (error instanceof Error) {
    if (error.message.includes('401')) {
      // Handle authentication error
      redirectToLogin();
    } else if (error.message.includes('403')) {
      // Handle permission error
      showErrorMessage('You do not have permission to create templates');
    } else if (error.message.includes('400')) {
      // Handle validation error
      showErrorMessage('Invalid template data');
    } else {
      // Handle generic error
      showErrorMessage(`Error: ${error.message}`);
    }
  }
}
```

## Best Practices

1. **Validation**: Validate all user input before sending to backend
2. **Loading States**: Show loading indicators during API calls
3. **Error Recovery**: Provide clear error messages and retry options
4. **Optimistic Updates**: Update UI optimistically before API confirmation
5. **Caching**: Cache template list to reduce API calls
6. **Permissions**: Check user permissions before showing edit UI
7. **Audit Trail**: Display who created/modified templates and when

## Next Steps

1. Implement `TemplateList` component to display templates
2. Add template preview/preview modal
3. Implement soft-delete confirmation UI
4. Add template versioning UI
5. Create template import/export functionality

---

*Quick Implementation Guide - Template Management System*
*Ready for developer onboarding*
