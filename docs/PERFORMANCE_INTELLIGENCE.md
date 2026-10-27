# Performance Intelligence: scoring revision

## Behavior

- Points describe recorded contribution volume. They are not a staff rating.
- Reliability combines task timeliness (28), contributed-run quality (27), elapsed-shift participation (12), and timed-start discipline (18). Available weights are normalized; absent evidence is not treated as a failed outcome.
- Run quality combines clean completion (65%) and on-time closure (35%). This is a shared run outcome, not attribution of an exception to an individual.
- Fewer than ten task, closed-run or timed-start observations is a limited sample. Rates include observation counts.
- Undated tasks earn execution credit but no on-time bonus. Only elapsed, non-cancelled shifts with recorded end timestamps enter adherence.
- Only known starters receive timed-start attribution. Legacy records without a known starter are excluded rather than blamed on whoever completed them.
- Passive checklist membership and scheduled assignments are not contribution days. Run-quality credit requires a completed item or subitem in that run.
- Calendar-day consistency and streaks no longer change grade or points. Current overdue work is shown separately and does not rewrite past points. Individual late-event deductions remain; escalating repeat-lateness deductions are removed.
- Equal scoring results share a rank; inactive users are unranked. The current operator remains visible even outside the top eight ranks.

## Achievements

Eleven families each have Amateur, Skilled and Expert levels. Each level lists numerical requirements, all of which must pass. Progress reflects the least-complete requirement, not an average that hides a failed gate. Levels are collected in order.

Shift-specialty awards count only completions within the recorded shift window; late backfills retain normal execution credit. Timed-start credit requires a known starter and a start within five minutes either side of the scheduled time. Starts earlier than that do not earn on-time credit and are not mislabeled as late.

Night Owl example: 12 night actions across 2 runs; 36 across 6; 72 across 12. Tempo Builder requires 15 / 45 / 90 contribution days, without a consecutive-day requirement.

Eligibility uses year-to-date activity, in operational shift dates for checklist work and the configured business time zone for tasks and handovers. Stored unlocks and claims survive a year reset. Existing badge keys remain level 1; new levels use `<family>_level_2` and `<family>_level_3` in the existing text-key claims table.

## Deployment

Deploy the core API and frontend together. No new database migration is required beyond the existing performance badge-claims migrations. Existing claims remain intact, but recalculated points and rates can change because the scoring rules now exclude unsupported credit and duplicate deductions. No completed work or task/checklist workflow is rewritten.

Old `/performance#badge-forge` email links still open Achievements. Mail delivery configuration is unchanged.

## Validation

The isolated backend regression suite exercises eligibility, zero evidence, deadline denominators, days off, late deductions, ties, legacy claims, level sequencing and SQL selection constraints. Frontend tests cover missing evidence, level collection and refresh failure. Browser layout checks use synthetic data and intercept APIs; they do not execute operational actions or require a production login.
