# Network Sentinel refresh

## Rollout

Deploy the updated SentinelOps frontend and SentinelOps-beta core API together, then restart the core API so the monitoring engine uses the new probe loop. No database migration or configuration change is required. Existing callers of the command-center endpoint still receive metrics by default.

## Changes

- Independent ICMP and TCP checks run concurrently. Probe interpretation, thresholds, configured targets and authorization remain in the existing code paths.
- Stream delivery no longer drops updates from different services during the old global throttle window. Heartbeats, reconnect backoff and polling fallback keep the workspace connected.
- The initial fleet request skips the expensive historical aggregate. Background requests populate historical availability. Live events update individual readings without reloading the entire fleet on every probe.
- Investigations scope their aggregate queries to the selected service. Evidence reads start at the end of log files, stop at the requested limit and run outside the API event loop.
- Service selection rejects late responses from previously selected services. Older snapshots cannot replace newer readings; recovered values clear obsolete failure reasons and latencies.
- The workspace has compact fleet cards, status filters, probe age, evidence tabs and accessible dialogs. Manual checks show their own result without overwriting scheduled-monitor status.

## Verification

- Nine frontend regression tests cover stream bursts, reconnects, access-denied closure, heartbeat loss, recovery values, out-of-order responses, service switching, filters, dialog closure and manual-probe results.
- Six backend tests cover concurrent and disabled probes, adjacent stream messages, subscription cleanup, UTF-8 log tails, date windows, malformed timestamps and service-scoped investigation SQL.
- The authenticated local workspace displayed 19 services and changing live readings. Desktop dark-theme inspection found and corrected compressed cards and an oversized guide trigger.
- The browser connection became unavailable before final mobile and light-theme visual verification. These remain rollout checks; automated tests do not establish visual correctness or production performance.
- No live services were created, edited, disabled or deleted during verification. Production has not been deployed from this task.

## Smoke checks after deployment

1. Open Network Sentinel and confirm the engine and stream connection state, recent probe timestamps and expected service count.
2. Switch quickly between services; confirm the heading, evidence and URL stay aligned.
3. Inspect Signal, Timeline and Evidence; download retained evidence and compare its date range.
4. On an approved test target, run Check now and verify the separate manual result.
5. Disconnect and restore the browser network; verify reconnect/polling recovery.
6. Check light/dark themes and narrow screens, then open and cancel the editor without saving.
