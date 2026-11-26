# Capture watchdog — owner release and alert-rehearsal checklist

**Date:** 26 November 2026
**Branch / PR:** `research/official-census-daily-waterfall-20261125` (PR #391)
**Status:** code and isolated rehearsals complete; external scheduling, notifier,
persistent state path and owner approval are **not** done.
**Boundary:** no production deployment, no paid infrastructure, no live
notification, no provider call, no database write.

## 1. What the owner is approving

The watchdog is a read-only process that runs **outside** `tennis-live-runner`.
It queries the production PostgreSQL database for three independent streams:

| Dataset | Table | What silence means |
|---|---|---|
| `fixture_ingestion` | `ingest_runs` (`API_TENNIS` scheduled fixtures, `COMPLETED`/`PARTIAL`) | discovery stopped or the provider adapter stopped persisting |
| `live_batch_health` | `data_health_checks` (`LIVE_BATCH_RUNTIME`) | the live batch is not running or not writing its heartbeat |
| `completed_prediction_runs` | `prediction_runs` (`COMPLETED`, non-experiment) | the prediction runner is down or not finalising runs |

It derives the expected hourly slots from the schedule, not from observed rows,
so the 13–16 November 2026 gap is reported as **95 missed hourly executions**
even though the missing runs left no rows.

It is not a model, a pick source or a provider client. It cannot create a
prediction, a decision, a publication or a result.

## 2. Isolated rehearsal results

Run from the PR #391 worktree:

```powershell
python -m pytest tests/test_capture_health_watchdog.py tests/test_fixture_lifecycle.py -q
# 30 passed
```

| Scenario | How it is isolated | Expected result |
|---|---|---|
| Missing execution | synthetic heartbeat list skips the 13 Nov 00:00–16 Nov 22:00 slots | `GAP`, 95 missing, exit 1 |
| Duplicate execution | two heartbeats in one hourly slot | one matched slot, one duplicate slot, not two runs |
| Runner outage | zero rows in `completed_prediction_runs` while the other streams are present | report still built; `completed_prediction_runs=NO_HEARTBEAT`; other datasets measured |
| Notifier failure | `_post_webhook` raises | distinct exit code **2**; state records the failure but does **not** advance the incident fingerprint, so the next run retries the alert |
| Duplicate alert | same failing dataset set on the next run with persisted state | `NONE` / `DUPLICATE_INCIDENT_SUPPRESSED` |
| Recovery alert | failing set becomes empty | one `RECOVERY`; next healthy run is `HEALTHY_NO_CHANGE` |
| State restart | state file written, loaded by a new process | duplicate alert still suppressed |
| Post-gap backdate | fixture started during the outage, another inside the 30-minute lead, one future fixture | only the future fixture is eligible; started fixtures are excluded, never backfilled |

The duplicate-prediction guard is PostgreSQL-backed and already covered by
`tests/test_full_slate_runner.py::test_duplicate_target_rejected_without_run`
(PostgreSQL foundation CI). A restart cannot create a second prediction for the
same target.

## 3. Dry-run commands (no notification, no write to the database)

```powershell
$env:TENNIS_DATABASE_URL = <authorised read-only DSN>
python ops/validation/capture_health_watchdog.py `
  --window-hours 2 `
  --expected-minutes 60 `
  --state-path .\watchdog-state.rehearsal.json `
  --out-json .\watchdog-report.json `
  --no-notify
```

Use a **dedicated rehearsal state path**. `--no-notify` still advances the
deduplication state by design, so pointing a dry run at the production state
file would suppress the first real alert.

Exit codes:

- `0` — healthy.
- `1` — a dataset failed (report still written).
- `2` — notification delivery failed (state still written; do not retry blindly).

The first dry run creates the state file. Run it twice with the same synthetic
window to prove the second run suppresses the duplicate incident.

## 4. External schedule

The watchdog must not run inside `tennis-live-runner`. If the runner is stopped,
a runner-hosted watchdog is stopped too.

Required owner choice (one of):

1. A separate Railway cron service that runs the same image with the watchdog
   command.
2. An independent uptime/monitor service that can execute the command in a
   container or call a small read-only endpoint.
3. An operator workstation scheduler only as a temporary manual control.

Recommended starting thresholds, subject to owner approval:

- schedule every 15 minutes;
- `--window-hours 2 --expected-minutes 60`;
- alert after one full missed hour;
- keep the 30-minute pre-match deadline unchanged.

Thresholds must match the deployed `0 * * * *` Railway cron. A 6-hour window
with hourly checks detects an outage only after the window tail ages; it is a
safe default, not a tight SLA.

## 5. Persistent state

`--state-path` must point at a writable path that survives process restarts
(a Railway volume or equivalent). Without it the process still alerts, but it
cannot suppress duplicates across runs.

The state file contains only:

- `active_fingerprint` — sorted failing dataset names, or `null`;
- `last_action`;
- `last_observed_at`;
- `last_detail`.

It contains no credentials, prices, picks or player data.

## 6. Notifier

Set `TENNIS_CAPTURE_WATCHDOG_WEBHOOK_URL` (or `--webhook-url`) only after the
owner names the destination and on-call route. The repository does not contain a
notifier URL and does not call one.

Rehearsal before enabling a real endpoint:

1. Send one synthetic incident payload to a test endpoint.
2. Confirm one message, not one per dataset.
3. Send the same synthetic payload again; confirm suppression.
4. Send a healthy payload; confirm one recovery message.
5. Send healthy again; confirm silence.
6. Make the endpoint return HTTP 500; confirm exit code 2 and preserved state.
7. Only then point the variable at the authorised on-call destination.

## 7. Monitoring when the prediction runner is unavailable

The watchdog reads PostgreSQL, not the runner process. With the runner stopped:

- `completed_prediction_runs` becomes `NO_HEARTBEAT`;
- `live_batch_health` becomes `NO_HEARTBEAT` or `STALE`;
- `fixture_ingestion` may still be healthy if discovery is separate;
- the report is still produced and the notifier still fires.

If PostgreSQL itself is unavailable, the watchdog cannot report. Add a separate
external database-reachability check; do not treat a watchdog connection error
as a healthy run.

## 8. Recovery acceptance

After a controlled runner restart:

- [ ] one recovery notification, not one per dataset or per run;
- [ ] no duplicate prediction for an already-targeted fixture;
- [ ] no fixture whose original start passed during the outage becomes a new
      target;
- [ ] the original scheduled start and original PIT visibility are retained;
- [ ] the first post-recovery run is marked with its real wall-clock time, not a
      backdated capture time;
- [ ] the next two scheduled watchdog runs are `HEALTHY_NO_CHANGE`.

## 9. Rollback

Keep the previous runner image and a one-command redeploy. The watchdog is
additive and read-only; removing it restores the previous silence risk but does
not change predictions. The target guard is additive too; removing it restores
retrospective-target risk.

## 10. Owner sign-off

- [ ] External schedule approved and created (no paid infrastructure without
      approval).
- [ ] Persistent state path approved and writable.
- [ ] Notifier destination and on-call route approved.
- [ ] Test alert, duplicate suppression, recovery and notifier-failure rehearsal
      completed.
- [ ] Thresholds approved against the deployed cron.
- [ ] Rollback command and owner rehearsed.
- [ ] Production database certificate/tunnel access restricted to the watchdog
      service.
- [ ] PostgreSQL foundation CI green at the exact pushed head.
- [ ] Owner accepts that the underlying Railway trigger failure is still
      unexplained and this release only makes a recurrence detectable.

## 11. Explicit non-claims

- No independent official fixture denominator exists. Internal coverage is not
  official tour coverage.
- No live notification has been sent by this repository.
- The 95 missed executions are established from the schedule and the stored
  heartbeat boundary; the platform-side trigger cause remains unknown.
- The watchdog does not prevent a Railway scheduler fault. It detects it.
