# Section and module access

SentinelOps now resolves module access from the user's organizational section and applies the existing role rules to operations inside those modules. The extraction pipelines, monitoring engine, task workflows, session tokens and business API paths retain their existing implementations.

## Authorization rules

- `ADMIN` is explicitly the system administrator: it can administer access and use every **active** module regardless of its own section.
- `MANAGER` and `USER` receive only active modules assigned to their active section. Their existing action permissions still apply; a grant does not promote a user to a manager or administrator.
- An inactive module is unavailable to everyone until an administrator reactivates it. An inactive or missing section grants no modules to a manager or user.
- New sections start without grants. New users without sections have no business workspaces. Account settings and the manual remain available to authenticated users; user/access administration remains administrator-only.
- Assigning a module to another section shares it. Removing one assignment does not remove any other section's access. Existing business record scopes within a module are unchanged.

`GET /api/v1/me/access` returns the role, section and effective module keys calculated from the database. Client-supplied roles, sections and module lists never authorize a request.

## Workspace inventory

The canonical registry is `app/access/modules.json` (30 modules). Small presentation components reuse their owning workspace's key.

| Page / area | Module keys |
| --- | --- |
| Checklists and templates | `checklists.execution`, `checklists.templates` |
| Task Manager | `task_manager.tasks` (task board and its analytics) |
| Database statistics | `database.statistics` |
| Performance | `performance.dashboard` |
| Team and personal schedule | `team.scheduling`, `team.schedule` |
| Network Sentinel | `network_sentinel.monitoring`, `network_sentinel.outage_history`, `network_sentinel.configuration` |
| Trustlink | `trustlink.daily_extraction`, `trustlink.run_history`, `trustlink.manual_run`, `trustlink.configuration`, `trustlink.rtgs` |
| Nexus | `nexus.dashboard`, `nexus.incidents`, `nexus.services`, `nexus.agents`, `nexus.databases`, `nexus.clusters`, `nexus.flows`, `nexus.dependencies`, `nexus.onboarding`, `nexus.rollover`, `nexus.sops` |
| Reports | `reports.crb`, `reports.hovering`, `reports.hovering_settings` |
| Funds Custody | `funds_custody.workspace` |

The home dashboard reuses these modules. Navigation keeps a page visible when at least one permitted workspace is on it. Tabs, cards, links and data requests are filtered independently. Direct restricted routes or workspace query parameters display an authorization error. When no home modules are available, the user lands on their first available page.

### Shared resources

Some existing APIs support more than one workspace:

- Network service identities/configuration support monitoring, history and configuration. Shared JSON responses omit monitoring samples/status when monitoring is unavailable and omit outage/event history when history is unavailable. File response transports are preserved.
- Nexus service catalog records support Services, Databases and Onboarding. Service investigation endpoints also support Incidents. Other Nexus workspace endpoints have their own module keys. The fabric overview is an independently assigned aggregate view.
- Trustlink daily access can inspect/download today's run; historical run IDs require run-history access. The shared checklist WebSocket filters event types and historical Trustlink updates before delivery.
- Notifications are filtered by the related module before pagination, unread counts and WebSocket delivery. Account/system notifications remain available.

Existing machine-agent authentication and standalone administration credentials stay in their original dependencies; user module checks do not replace them.

## Administrator workflow

Open **Section & Module Access** from the administrator profile menu, or visit `/access`.

1. Select **Section**, select the section, and choose modules grouped by functional area. Search and group selection/clearing are available.
2. Save assignments. The same module can be selected for several sections.
3. Select **Module** to inspect and edit the reverse mapping using the same junction records.
4. Create or deactivate sections, activate/deactivate modules, inspect a user's effective access, and view recent access audit events in this workspace.
5. Use the existing `/users` screen to assign roles and sections. Its create/edit form previews inherited modules.

No user-by-user module grants are needed. Role restrictions still apply to controls shown inside an assigned workspace.

### API surface

All administration endpoints require the authenticated `ADMIN` role.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/me/access` | Current effective access |
| GET | `/api/v1/admin/access` | Sections, modules and assignments for both perspectives |
| PUT | `/api/v1/admin/sections/{id}/modules` | Replace this section's `module_ids` |
| PUT | `/api/v1/admin/modules/{id}/sections` | Replace this module's `section_ids` |
| POST | `/api/v1/admin/sections` | Create a section |
| PATCH | `/api/v1/admin/sections/{id}` | Set section name and active status |
| PATCH | `/api/v1/admin/modules/{id}` | Set module active status |
| GET | `/api/v1/admin/users/{id}/access` | Inspect inherited access |
| GET | `/api/v1/admin/access/audit` | Latest 100 access audit events |

Unchanged user management endpoints handle role and section assignments. Assignment IDs are validated server-side. A unique `(section_id, module_id)` key prevents duplicates. Administration writes from both perspectives are serialized inside database transactions.

## Persistence, enforcement and audit

The additive migration creates `access_modules`, `section_modules` and `access_migrations`, and adds `sections.is_active`. It reuses existing `users.section_id`, `sections`, `roles` and `user_roles`.

`app/access/policy.py` resolves all effective keys with one joined query. Both API applications use `ModuleAccessMiddleware` before operational handlers. Missing/invalid user authentication returns 401; missing module entitlement returns 403. Existing handler role checks remain in force. `require_module_access(key)` is available for new main-API handlers.

There is no persistent server permission cache. Changes affect subsequent HTTP requests and outgoing WebSocket events. Authentication resolves permissions using the same database connection; middleware and handler dependencies reuse that identity only within the current HTTP request. Synchronous authentication queries run off the async event loop. Network responses bypass projection/buffering when the user has both monitoring and outage-history access.

Sign-in and profile responses include effective access, so the frontend can render the permitted workspace immediately without a separate access request or loading screen. Restored sessions use workspace skeletons. The frontend refreshes after an access save, every 30 seconds while visible, and on focus if stale. Concurrent refreshes are deduplicated. Identical permissions and temporary refresh failures preserve the UI. Real permission changes clear operational views while retaining the application shell, theme, configuration and notification providers. Data already delivered cannot be withdrawn from a client, so enforcement always occurs on the server as well.

The performance and appearance follow-up requires updated APIs and frontend but no additional migration. The frontend retains a `/me/access` fallback for older sign-in/profile responses during rollout. Access administration loads operator lists and audit history only when those views are opened. Its styles are scoped, and saved/system theme selection is applied before paint.

Legacy Template Manager text-visibility overrides are now scoped to `.stm-template-manager-page`; they no longer force unrelated dark-theme buttons, status colors and secondary text to white.

Assignment, removal, role/section changes and activation changes write to the existing `ops_events` audit stream with `entity_type='ACCESS_MANAGEMENT'`. Payloads include the actor, affected IDs and old/new values. Business writes and their audit inserts commit atomically; an audit insert failure rolls back the access change. Migration events identify their actor as `migration`.

## Rollout and compatibility defaults

**The migration has not been applied to the live SentinelOps database by this implementation task.** It was exercised against a separate local PostgreSQL test cluster.

Apply `app/db/migrations/2026_09_section_module_access.sql` to the intended SentinelOps database **before** deploying either updated API. Deploy the main API, Nexus API and frontend together so all three use the same registry. The schema changes are additive and can be applied while the previous application release remains running.

Example using the deployment's existing PostgreSQL connection configuration (no credentials should be committed):

```powershell
psql -v ON_ERROR_STOP=1 -f app/db/migrations/2026_09_section_module_access.sql
```

The one-time initial policy preserves current access:

- Existing sections receive previously general workspaces.
- Nexus, Reports, RTGS and Funds Custody grants initially retain the prior restricted section UUID, `7bd4144d-68d8-4ac3-897d-245941612daf`.
- Existing users without a section are placed in a **Legacy access migration** section with the general grants. Account IDs, roles and credentials are preserved.
- Before rollout, compare the migration's restricted-section seed with the deployment's old `NEXUS_ALLOWED_SECTION_IDS` setting. A deployment that customized that setting must seed the corresponding historical section set. That setting no longer governs user module authorization after rollout; its existing notification-routing use is retained.
- The migration ledger prevents a rerun from restoring removed grants. Registry upserts do not reactivate disabled modules.

After deployment, sign in as an administrator, inspect `/access`, then narrow each section to its desired assignments. Verify a representative manager and user can still complete their existing workflows within their assigned modules.

If reverting the application release, retain the additive tables and audit records. Reverting to the old APIs also restores their old access behavior; it is not a security-preserving rollback of the new policy. Do not drop user or section records to roll back this release.

## Registry maintenance and validation

Edit the canonical registry/policy under `SentinelOps-beta/app/access`. From the repository group root:

```powershell
python SentinelOps-beta/scripts/sync_access.py
python SentinelOps-beta/scripts/sync_access.py --check
```

The script distributes the portable policy to `sentinelops-ai/app/access` and the registry to `SentinelOps/src/policies/modules.json`. Commit/release all generated copies together. Future modules need a new additive seed migration, endpoint mapping and frontend guards; new grants should be intentional rather than added by replaying the original migration.

Validation provided with this change:

- `SentinelOps-beta/tests/test_module_access.py`: roles and entitlements, shared grants, revocation, inactive/missing sections and modules, API denial, unchanged role boundaries, route inventory, response projection, WebSocket authentication/revocation/history filtering, notifications and contract synchronization.
- `SentinelOps-beta/scripts/verify_access_integration.py`: real PostgreSQL migration replay, both administration perspectives, real JWT sessions, existing user updates, notification counts, disabled accounts, audit events and forced audit-write rollback. It refuses any database outside the dedicated `.tmp/sentinel-access-pg` cluster on port 55439; it is not a production test command.
- `SentinelOps/src/contexts/AccessContext.test.tsx`: partial visibility, navigation, direct forbidden links and role/page rules.
- `SentinelOps/src/pages/AccessManagementPage.test.tsx`: grouped assignment and shared ownership from both perspectives.
- Existing Nexus `tests/test_nexus.py` regression suite and the React production build.

Python test dependencies are listed in `SentinelOps-beta/requirements-test.txt`. Run frontend checks with the existing npm scripts. The separate Nexus light-agent test suite needs its missing `nexus_light_agent` package before it can be collected in this checkout.
