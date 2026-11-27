# Independent monitoring adapter — implementation and activation status

**Date:** 27 November 2026
**Branch / PR:** `research/official-census-daily-waterfall-20261125` (PR #391)
**Status:** implemented and tested in isolation. **NOT ACTIVATED.** No external
account was provisioned, no paid service was enabled, no heartbeat was sent and
no production database was written.
**Evidence:** [`monitoring_acceptance_20261127.json`](../research/evidence/monitoring_acceptance_20261127.json)
**Code:** `src/sports_edge_tennis/ops/monitoring.py`,
`ops/validation/monitoring_cycle.py`,
`ops/validation/monitoring_acceptance_20261127.py`
**Tests:** `tests/test_independent_monitoring.py` (29 tests)

## 1. Why a second monitor is needed

The 13–16 November 2026 outage produced **no failed rows**: the scheduler simply
stopped triggering executions. Railway documents the mechanism that can produce
this: a scheduled service must exit, and *"if a previous execution is still
running when the next scheduled execution is due, Railway will skip the new cron
job."* The platform-side trigger cause of the November gap remains unexplained,
but the failure mode is now directly observable from outside the job.

The existing `capture_health_watchdog.py` checks internal heartbeat rows. It is
necessary but not sufficient:

- if the scheduler never starts the job, no row exists to check;
- if the notifier itself is down, the watchdog cannot page;
- if the database is unreachable, the watchdog cannot report at all.

This adapter adds an **independently hosted dead-man's switch**. The service is
not in the job's process, host or database.

## 2. Monitored work streams

One check per stream. A stream is a class of expected work, not a database
table.

| Stream | Required durable evidence | Success means |
|---|---|---|
| `FIXTURE_DISCOVERY` | `match_versions` / `ingest_runs` | the scheduled fixture window was discovered and persisted |
| `FORECAST_GENERATION` | `prediction_runs` / `predictions` | every eligible target has an on-time completed forecast |
| `BOOKMAKER_ACQUISITION` | provider odds ledger | prices for the eligible markets were acquired and persisted |
| `INDEPENDENT_ARCHIVAL` | external archive object | the issued forecast and price were archived outside the producing DB |
| `OFFICIAL_SETTLEMENT` | official result observations | the official result was persisted |
| `BOOKMAKER_SETTLEMENT` | settlement ledger | the bookmaker market settled separately from the official result |
| `PROSPECTIVE_CANDIDATE` | candidate ledger | the prospective candidate was recorded before the fixture start |

`BOOKMAKER_ACQUISITION` may legitimately persist **zero** rows for a slate with
no eligible market. That is only success when the stage explicitly acknowledges
an empty expectation (`acknowledge_empty=True`). An unacknowledged zero is not
success.

## 3. Success contract

A success signal is allowed **only** when all of the following hold:

1. the run started and finished before its deadline;
2. every required stage is `COMPLETED`;
3. every required stage has `durable_verified=True` with a named verification
   method;
4. `expected_rows` is known and equal to `persisted_rows`;
5. an empty stage expectation is explicitly acknowledged.

An empty eligible slate is successful only when fixture discovery and
reconciliation both completed (`discovered=0`, `reconciled=0`,
`reconciliation_completed=True`). Otherwise it is a partial run.

## 4. Classification

| Observed state | Outcome | Exit | Signal |
|---|---|---|---|
| no start evidence for the slot | `JOB_NEVER_TRIGGERED` | 1 | failure |
| started and crashed / errored | `JOB_STARTED_FAILED` | 1 | failure |
| no finish before the deadline | `JOB_TIMED_OUT` | 1 | failure |
| finished late | `JOB_TIMED_OUT` (late finish recorded) | 1 | failure |
| finished but a required stage is missing / unverified / short | `JOB_COMPLETED_PARTIALLY` | 1 | failure |
| all required stages durably complete | `JOB_COMPLETED_SUCCESSFULLY` | 0 | success |
| zero eligible after completed discovery + reconciliation | `NO_ELIGIBLE_FIXTURES` | 0 | success |
| heartbeat service unreachable / rejected | `MONITORING_SERVICE_UNAVAILABLE` | 2 | none delivered |
| before the deadline | `IN_PROGRESS` (non-terminal) | 0 | none |

The monitor distinguishes a **job failure** from a **monitoring failure**. A
green job with an unavailable monitor exits 2, not 0. It never reports success
it could not deliver.

## 5. Heartbeat protocol

Compatible with Healthchecks.io and any equivalent service:

- success: `GET /<uuid>` or `GET /<ping-key>/<slug>`
- start: `.../start`
- failure: `.../fail`
- log: `POST .../log`
- exit status: `.../<0-255>`

Only HTTP 200/201 with body `OK` is accepted. A `200` with an empty body is the
documented "check not found" response and is rejected. `404` and `409` fail
immediately; `429`, `408`, `5xx` and transport errors retry with linear backoff
(maximum five attempts). No signal is ever converted to success after a failure.

## 6. Scheduler non-execution test

The test uses an **independent simulated schedule** derived from the cron
expression, not from observed rows:

```text
cron            0 * * * *
window          2026-11-12T23:00Z .. 2026-11-17T00:00Z
executions      only the three real heartbeat boundaries
result          98 expected slots, 3 success, 95 JOB_NEVER_TRIGGERED
first missing   2026-11-13T00:00Z
last missing    2026-11-16T22:00Z
```

The simulated schedule is independent of any database. Removing every observed
row cannot hide the gap, because the denominator comes from the schedule.

## 7. Acceptance matrix

Fourteen scenarios are executed by
`ops/validation/monitoring_acceptance_20261127.py`. All pass.

| Scenario | Result | What it proves |
|---|---|---|
| `SCHEDULER_NON_EXECUTION_95_SLOTS` | PASS | 95 silent slots are classified, not hidden |
| `HEALTHY_HEARTBEAT` | PASS | success heartbeat on durable completion |
| `APPLICATION_CRASH` | PASS | crash is `JOB_STARTED_FAILED` |
| `TIMEOUT` | PASS | open run past deadline is `JOB_TIMED_OUT` |
| `PARTIAL_COMPLETION` | PASS | 8/10 persisted rows is partial, no success |
| `LATE_COMPLETION` | PASS | late finish does not backfill the missed slot |
| `EMPTY_SLATE_VERIFIED` | PASS | zero eligible is success only after reconciliation |
| `EMPTY_SLATE_UNVERIFIED` | PASS | unverified empty slate is partial |
| `DUPLICATE_AND_RECOVERY` | PASS | one incident, suppressed duplicate, one recovery |
| `NOTIFICATION_FAILURE_AND_RETRY` | PASS | failed delivery does not advance state; retried |
| `DURABLE_STATE_RESTART` | PASS | duplicate suppression survives restart |
| `MONITOR_UNAVAILABLE_DISTINCT` | PASS | job success + monitor outage exits 2 |
| `MONITORING_DISABLED` | PASS | disabled monitoring never claims delivery |
| `INDEPENDENT_SCHEDULE_DERIVATION` | PASS | 98 slots derived from cron |

Durable state is written atomically (`write` + `fsync` + `os.replace`) and
contains only the active incident fingerprint, timestamps, last action and
delivery error. It contains no credentials, prices, picks or player data.

## 8. Deployment configuration (prepared, not applied)

Environment variables are server-side only. The repository contains no check
UUID, ping key, slug or notifier URL.

```text
TENNIS_HEALTHCHECKS_ENABLED=1
TENNIS_HEALTHCHECKS_BASE_URL=https://hc-ping.com
TENNIS_HEALTHCHECKS_PING_KEY=<server-side project ping key>
TENNIS_HEALTHCHECKS_LIVE_RUNNER_SLUG=tennis-live-runner
TENNIS_HEALTHCHECKS_FIXTURE_DISCOVERY_SLUG=tennis-fixture-discovery
TENNIS_HEALTHCHECKS_FORECAST_GENERATION_SLUG=tennis-forecast-generation
TENNIS_HEALTHCHECKS_BOOKMAKER_ACQUISITION_SLUG=tennis-bookmaker-acquisition
TENNIS_HEALTHCHECKS_INDEPENDENT_ARCHIVAL_SLUG=tennis-independent-archival
TENNIS_HEALTHCHECKS_OFFICIAL_SETTLEMENT_SLUG=tennis-official-settlement
TENNIS_HEALTHCHECKS_BOOKMAKER_SETTLEMENT_SLUG=tennis-bookmaker-settlement
TENNIS_HEALTHCHECKS_PROSPECTIVE_CANDIDATE_SLUG=tennis-prospective-candidate
```

Suggested check configuration (owner approves final values):

- schedule type: **Cron**, expression `0 * * * *`, timezone **UTC**;
- grace time: **45 minutes**;
- period is not used with a cron schedule;
- one check per stream, tagged `prod`, `tennis`, `p1`;
- notification channels: at least two independent channels;
- repeated reminders every hour while down.

The monitor itself must run **outside `tennis-live-runner`**, for example a
separate Railway cron service with its own `railway.toml`:

```toml
[deploy]
startCommand = "python ops/validation/monitoring_cycle.py --slot $TENNIS_MONITOR_SLOT --receipt /data/receipts/$TENNIS_MONITOR_SLOT.json --stream LIVE_RUNNER --state-path /data/monitoring-state.json"
cronSchedule = "*/15 * * * *"
restartPolicyType = "NEVER"
```

`/data` must be a persistent volume. Without it, duplicate suppression and
recovery tracking cannot survive a restart. The slot value is supplied by the
scheduler wrapper; a small wrapper is required because Railway cron does not
template the scheduled time into the command. That wrapper is a deployment
task, not committed here.

## 9. Owner approval procedure

No step below has been performed.

1. **Choose the provider.** Healthchecks.io or an equivalent independently
   hosted service. Confirm data residency, retention, notification channels,
   uptime SLA and price.
2. **Create the check(s).** One per stream. Do not use auto-provisioning
   (`create=1`); a typo must fail, not silently create a check.
3. **Store credentials server-side.** Only in the hosting provider's secret
   store. Never in Git, logs, screenshots or the state file.
4. **Provide durable state.** A writable volume, or an equivalent durable
   key-value store, for the state file and receipts.
5. **Approve thresholds.** Cron expression, grace time, reminder cadence and
   the `--run-timeout-minutes` value must match the deployed pipeline.
6. **Rehearse on a test endpoint.** Send: one success; one failure; one
   duplicate failure (suppressed); one recovery; one 500 response (exit 2 and
   preserved state); one unreachable host (exit 2).
7. **Approve the on-call route.** Named destination, escalation, and an owner
   for out-of-hours response.
8. **Activate one stream at a time.** Start with `FIXTURE_DISCOVERY`, confirm a
   full weekly cycle, then add the others.
9. **Record activation.** Commit a dated activation record naming the checks,
   the owner, the thresholds and the rollback step. Do not commit secrets.

## 10. What this does not do

- It does not repair or explain the Railway trigger failure.
- It does not prove official fixture coverage.
- It does not replace the PostgreSQL read-only capture watchdog; the two are
  complementary.
- It does not send a notification, create an account, incur cost or write to
  production in this branch.

## 11. Activation status

```text
activation_status              NOT_ACTIVATED
external_account_provisioned   false
paid_services_activated        false
heartbeats_sent                 0
provider_calls                  0
database_writes                 0
```
