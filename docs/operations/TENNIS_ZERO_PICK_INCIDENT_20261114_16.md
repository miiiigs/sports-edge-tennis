# November 14–16, 2026 zero-pick incident closure

## Confirmed production evidence

The original ledger contains zero Model Picks for November 14, 15 and 16, and
the forensic database audit found no prediction runs, ingestion jobs or
pre-match source observations on those dates.

Railway deployment history now closes the operational trigger one level
further:

- Previous `tennis-live-runner` deployment
  `49ac64b1-42f4-4bbe-9c21-1176d53a3fcd` was removed at
  **2026-11-13 23:08:34.991 UTC**.
- The next live-runner deployment
  `0ec94e4d-efc8-40c0-b154-619d086728d6` was created at
  **2026-11-16 23:03:11.621 UTC**.

Therefore there was no active live-runner deployment across the operational
window that generated the November 14–16 source/prediction gap.

The available deployment record does **not** identify why the previous
deployment was removed. Billing, credentials, manual action or a Railway
platform cause must not be asserted without an audit record.

## Historical integrity

No November 14–16 predictions will be reconstructed and represented as
pre-match. Later provider fixtures/results may be used only for coverage
forensics and research explicitly labelled after-the-fact.

## Forward controls

- Dashboard source-attendance diagnostics expose calendar-day source,
  ingestion-run and prediction-run attendance.
- The hourly production batch runs the seven-completed-day attendance
  watchdog and logs explicit gaps.
- A zero-pick date is no longer interpreted as a model abstention until source
  and pipeline attendance are checked.
- The dashboard remains an independent visibility surface even when a runner
  cycle is absent.

This incident is classified as **pipeline unavailable / no active runner
deployment**, not model underperformance.
