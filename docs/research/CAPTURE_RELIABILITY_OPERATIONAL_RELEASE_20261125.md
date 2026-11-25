# Capture-reliability operational release readiness — 25 November 2026

**Branch / PR:** `research/official-census-daily-waterfall-20261125` (PR #391)
**Mode:** read-only production census plus isolated local tests. No production
deployment, scheduler change, provider call, purchase or database write.
**Related evidence:**
[`FIXTURE_DISCOVERY_DEADLINE_ROOT_CAUSE_20261125.md`](FIXTURE_DISCOVERY_DEADLINE_ROOT_CAUSE_20261125.md)

## What is being released

This workstream does not release a model or expand tournament scope. It changes
the live runner and adds an independent monitor:

1. **Fail-closed pre-match targeting** in
   `src/sports_edge_tennis/validation/fixture_lifecycle.py`, used by
   `run_live_mvp`. A target must be `SCHEDULED`, have a scheduled start, and
   start at least 30 minutes after `as_of`. Unknown, started or terminal
   fixtures are excluded with an explicit reason.
2. **Independent capture watchdog**
   (`ops/validation/capture_health_watchdog.py`). It is read-only, runs outside
   the live runner, derives expected hourly slots from the schedule, and can
   send one incident notification, suppress duplicates and send one recovery
   notification.
3. **Lifecycle traceability** (`build_fixture_timeline`,
   `reconcile_snapshot_changes`, `reconcile_selection_populations`) so original
   starts, every later schedule/status change, PIT visibility and distinct
   selection populations are reconstructed rather than inferred.
4. **Scheduler-gap evidence**
   (`docs/research/evidence/railway_scheduler_gap_evidence_20261125.json`) with
   the corrected UTC boundaries and Railway deployment/log evidence.

## Verified behaviour

| Requirement | Evidence | Status |
|---|---|---|
| Retrospective fixtures cannot become new targets | `select_prematch_targets`; `tests/test_fixture_lifecycle.py::test_prematch_target_selection_fails_closed` | PASS |
| A terminal fixture is excluded even if labelled `SCHEDULED` | status guard and `NOT_SCHEDULED` exclusion | PASS |
| Original scheduled start survives a reschedule | `test_rescheduled_fixture_keeps_both_clocks_and_change_history` | PASS |
| A later version cannot create a retroactive PIT window | `test_reschedule_cannot_retroactively_create_a_pit_version` | PASS |
| Absence is never converted to a void | `test_missing_fixture_is_explicit_and_not_void`, `test_complete_snapshot_absence_is_still_not_a_void` | PASS |
| Silent scheduler with zero rows is detected | `test_capture_gap_detects_no_heartbeat_and_stale_tail`, `test_silent_runner_is_detected_without_any_runner_written_rows` | PASS |
| Expected hourly slots are independent of recorded runs | `test_expected_slots_are_derived_from_the_schedule_not_from_runs` | PASS |
| 12–16 November reconciliation is exact | 98 expected slots, 3 served, **95 missed** (`test_production_gap_is_95_missed_hourly_executions`) | PASS |
| Duplicate execution in one slot is flagged | `test_duplicate_execution_in_one_slot_is_flagged_not_counted_as_two_runs` | PASS |
| Duplicate notification is suppressed | `test_incident_notification_is_sent_once_and_recovery_is_sent_once` | PASS |
| Recovery notification is sent once | same test | PASS |
| State survives process restart | `test_state_round_trip_suppresses_duplicate_notifications` | PASS |
| Duplicate prediction target is rejected before a run | `tests/test_full_slate_runner.py::test_duplicate_target_rejected_without_run` | PASS (PostgreSQL CI) |
| Runner refuses an incomplete full-slate outcome | `tests/test_full_slate_runner.py` failure-state regressions | PASS (PostgreSQL CI) |

Commands executed locally:

```text
python -m pytest tests/test_capture_health_watchdog.py tests/test_fixture_lifecycle.py -q
25 passed
```

The PostgreSQL foundation workflow remains the release gate for the
`select_prematch_targets` integration and the full-slate runner. The workflow
must be green at the exact pushed head before release.

## Release gates

Controlled operational release is **conditionally ready**, subject to all of:

1. **Owner approval** of the live-target behaviour change and the watchdog
   schedule.
2. **External scheduling** of the watchdog outside `tennis-live-runner`, for
   example a separate Railway cron/service or an independent uptime monitor.
   A watchdog inside the runner cannot detect the runner being stopped.
3. **Notifier configuration** through
   `TENNIS_CAPTURE_WATCHDOG_WEBHOOK_URL` (or `--webhook-url`) to an authorised
   destination, with a test alert and a documented on-call route. No notifier is
   configured or called by this repository.
4. **Persistent state path** for duplicate suppression, with a writable volume
   or equivalent; without it, repeated pages are possible.
5. **Alert rehearsal:** one synthetic missed-execution incident, one duplicate
   suppression, one recovery, and one notifier-failure exit code (2).
6. **Threshold confirmation:** `--window-hours` and `--expected-minutes` must
   match the deployed `0 * * * *` schedule. The current defaults (6 hours, 60
   minutes) are a starting point, not an approved operating threshold.
7. **Rollback:** keep the previous runner image and a documented one-command
   redeploy. The target guard is additive; removing it restores the previous
   behaviour but also restores retrospective-target risk.
8. **Independent official coverage is still unknown.** This release improves
   detection and fail-closed behaviour; it does not create an official fixture
   denominator and must not be reported as official coverage.

## Not ready / blocked

- The platform reason the Railway cron trigger stopped on 12 November is not
  available from the read-only evidence. The release mitigates silence; it does
  not repair the platform fault.
- Notification delivery to a real endpoint is not exercised here.
- The watchdog schedule itself is an operational action and is not part of this
  PR.
- No claim is made that coverage gaps cannot recur. The watchdog makes a
  recurrence detectable; it does not prevent it.

## Decision

**Ready for controlled operational release of the fail-closed target guard and
the monitoring code, after approval and after the external schedule, notifier,
state path and alert rehearsal are in place.** Not released by this PR. Current
production behaviour is unchanged until an authorised deployment.

## Evidence hashes

```text
docs/research/evidence/railway_scheduler_gap_evidence_20261125.json
SHA256 B78C25B711626682F2C92993E4C1458A6F67C0A02DFD34B1DFDF57053AAE8EBE

docs/research/evidence/fixture_discovery_deadline_root_cause_20261125.json
SHA256 E5E84ECFE0B5A6B018AB96161ACE66FF8FC5FDA527B26FADD811DBB895794428
```
