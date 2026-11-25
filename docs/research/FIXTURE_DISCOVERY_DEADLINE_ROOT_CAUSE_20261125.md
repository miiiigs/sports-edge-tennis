# Fixture discovery deadline gap: root cause and safeguards — 25 November 2026

**Branch / PR:** `research/official-census-daily-waterfall-20261125` (PR #391)
**Verified starting head:** `4fc8d8352eea8d65dd1a27a0a82303cc097ca7af`
**Execution mode:** authorised read-only PostgreSQL (`default_transaction_read_only=on`), no provider calls, no scraping, no writes, no migrations, no deployment.
**Evidence JSON:** `docs/research/evidence/fixture_discovery_deadline_root_cause_20261125.json`
**Execution script:** `ops/validation/fixture_discovery_deadline_root_cause_20261125.py`
**Watchdog:** `ops/validation/capture_health_watchdog.py`
**Lifecycle library:** `src/sports_edge_tennis/validation/fixture_lifecycle.py`

## Exact execution

The DSN was resolved in memory from the linked Railway Postgres service and supplied through `TENNIS_DATABASE_URL`; no credential is printed or committed. The script sets a read-only transaction.

```powershell
$env:TENNIS_DATABASE_URL = <authorised read-only DSN resolved in memory>
python ops/validation/fixture_discovery_deadline_root_cause_20261125.py \
  --out-json docs/research/evidence/fixture_discovery_deadline_root_cause_20261125.json
```

The script reproduces the 53-fixture cohort, joins every first version, first ingest run, result and target, and reconstructs the 14–25 November reschedule and selection populations.

## 1. The 53 fixtures are retrospective discoveries, not late refreshes

The cohort definition is the same as the earlier waterfall: latest stored version scheduled 14–16 November UTC, main draw (`qualifying_flag = false`), and no version whose `as_of`, `source_observed_at` and `ingested_at` are all at or before `start − 30 minutes`.

| Measure | Result |
|---|---:|
| Stored fixtures in cohort | **53** |
| By UTC day | 16 on 14 Nov, 9 on 15 Nov, 28 on 16 Nov |
| Ever in `prediction_run_targets` | **0** |
| Distinct first ingest runs | **1** (`128518`) |
| First `source_observed_at` | `2026-11-17T08:16:24.621460Z` (all 53) |
| First `ingested_at` range | `2026-11-17T08:16:27.019455Z` – `08:16:35.382274Z` |
| First version status | 49 COMPLETED, 2 RETIRED, 1 WALKOVER, 1 CANCELLED |
| Ingest lag after scheduled start | **8.61 h – 79.19 h** |
| Versions per fixture | 1 (15 fixtures), 2 (10), 3 (28) |
| Distinct starts per fixture | 1 (all 53) |
| PIT-visible at `start − 30m` | **0** |
| Retrospective-discovery flag | **53 / 53** |

Every first version was created by ingest run `128518`: `API_TENNIS` / `api_tennis_scheduled_fixtures`, status `PARTIAL`, started `2026-11-17T08:16:24.621460Z`, 1,025 rows seen, 243 inserted, 782 rejected (all `out_of_scope_event_type`/status rejections in the run summary). The companion result refresh `128519` inserted 222 terminal results and the stats run `128520` inserted 462 rows in the same second.

The crucial detail is that the provider observation time is the fetch time. `_source_observed_at` returns `received_at`; it does not preserve a provider event-update clock. For these 53 rows the first "observation" is therefore *already* 2–3 days after the scheduled start and already carries a completed/cancelled result. A pre-match version was structurally impossible, not merely late.

## 2. The ingestion and capture stream actually stopped

### Scheduled-fixture ingestion runs

| UTC day | COMPLETED | PARTIAL | FAILED |
|---|---:|---:|---:|
| 10 Nov | 53 | 54 | 20 |
| 11 Nov | 25 | 53 | 14 |
| 12 Nov | 18 | 48 | 16 |
| **13 Nov** | **0** | **0** | **0** |
| **14 Nov** | **0** | **0** | **0** |
| **15 Nov** | **0** | **0** | **0** |
| **16 Nov** | **0** | **0** | **0** |
| 17 Nov | 27 | 51 | 23 |
| 18 Nov | 26 | 54 | 20 |

There were no `api_tennis_scheduled_fixtures` runs at all for four consecutive days. There were also no completed prediction runs on 13–16 November.

### Capture-health heartbeats

All timestamps below are UTC. The earlier draft mixed a Manila-local timestamp
with a UTC timestamp; the corrected boundaries are shown here and in
`docs/research/evidence/railway_scheduler_gap_evidence_20261125.json`.

| Last heartbeat before gap | `2026-11-12T23:01:16.517974Z`, `ERROR`, `live_batch_status=2` |
|---|---|
| Next heartbeat | `2026-11-16T23:05:34.209277Z`, `ERROR`, `live_batch_status=2` |
| First healthy heartbeat | `2026-11-17T00:03:28.724249Z`, `HEALTHY` |
| Elapsed silence | **4 days 0 hours 4 minutes** |
| Expected top-of-hour slots, 12 Nov 23:00Z – 17 Nov 00:00Z | **98** |
| Slots with an execution | **3** (12 Nov 23:00, 16 Nov 23:00, 17 Nov 00:00) |
| **Missed hourly executions** | **95** (13 Nov 00:00Z – 16 Nov 22:00Z) |

The "13 Nov 07:01" in the earlier draft was **13 November 07:01 Manila time**,
which is `2026-11-12T23:01:16Z`. The 95 missed intervals are correct, but only
when counted against the expected top-of-hour slots: the 16 November 23:00Z
slot was served by the manual redeploy at 23:03Z, so it is not one of the
missed slots.

The 13–16 November silence produced **no request-failure rows** because no
request was attempted and no cycle wrote a health row. This is the exact failure
mode the old health check could not see: it evaluated zero targets only if a
cycle ran. A stopped scheduler is silently healthy in a table written only by
that scheduler.

### Railway scheduler and deployment evidence

Read-only Railway CLI evidence (deployment list, service status, historical
logs) was added to the investigation:

| Evidence | Result |
|---|---|
| Live-runner deployments between 2026-11-08 and 2026-11-16T23:03Z | **None** |
| Last deployment before the gap | `49ac64b1…`, 2026-11-02T06:04Z, commit `5a7ec89` |
| Recovery redeploy | `0ec94e4d…`, **2026-11-16T23:03:11Z**, commit `5a7ec89` |
| `railway.toml` at the deployed commit and current head | `cronSchedule = "0 * * * *"`, unchanged |
| Scheduled-fixture ingest runs by UTC day | 11 Nov 92, 12 Nov 82, **13–16 Nov 0**, 17 Nov 101 |
| Last application log line before the gap | `2026-11-12T23:01:21Z`, `PRODUCTION_LOCK_RELEASED` |
| Application log lines during the gap | **0** |
| First execution after the gap | 2026-11-16T23:05:34Z, within two minutes of the redeploy |

This rules out a deployment or cron-config change at the start of the gap. The
last execution ended, released its production lock, and then the platform
produced no further execution — no container log, no ingest run, no heartbeat.
The next execution began only after a human redeploy. The cause is therefore a
**scheduler non-execution** that required an external restart. Why Railway's
cron trigger stopped is not exposed by the CLI deployment list or the retained
application logs, and no platform incident record was available to this
read-only session; that residual platform question is recorded as unresolved,
not guessed.

The first post-recovery hourly run at 07:01Z fetched a normal 408-row slate.
The 1,025-row run at **08:16Z** is not on an hourly slot boundary and the 08:01Z
hourly cycle had already fetched 406 rows, so 08:16Z is an out-of-band catch-up
(operator-triggered or deferred), not an hourly refresh. It persisted
already-terminal events and could not restore pre-match coverage.

### Cause summary

1. **Primary:** Railway cron executions stopped after the
   2026-11-12T23:00Z cycle and did not fire for 95 expected hourly slots
   (2026-11-13T00:00Z – 2026-11-16T22:00Z). No deployment, config change or
   restart occurred at the start of the gap, and the runner wrote no logs, so
   this was scheduler non-execution rather than an application crash or hang.
2. **Recovery:** a manual redeploy at 2026-11-16T23:03:11Z started the
   2026-11-16T23:00Z slot; the first healthy heartbeat is 2026-11-17T00:03Z.
3. **Secondary:** the first post-recovery observations were fetch-time
   observations of already-terminal events, so the 14–16 November fixtures
   could not become pre-match targets. The out-of-band 08:16Z catch-up did not
   restore pre-match coverage.
4. **Not the cause:** a provider outage with recorded request failures, a late
   hourly refresh, missing provider fixture IDs or canonical matches, a
   deployment/cron-config change during the gap, or an application crash loop.
5. **Unresolved:** why Railway's cron trigger stopped is not exposed by the
   read-only CLI evidence available here. It is recorded as an external
   scheduler failure, not attributed to the application.


## 3. Safeguards implemented

### Fail-closed pre-match target selection

`select_prematch_targets` in `src/sports_edge_tennis/validation/fixture_lifecycle.py` is now used by `run_live_mvp`. A fixture is targetable only when:

- its provider status is `SCHEDULED`;
- it has a scheduled start; and
- `start − now >= 30 minutes`.

Fixtures with an unknown start, a started match or a terminal status are excluded and logged as `LIVE_TARGET_EXCLUDED ...`. The `ApiTennisIngestedMatch` and `SportradarIngestedMatch` outcomes now carry the persisted scheduled start so the guard works for both fixture providers. This would have prevented a retrospective `SCHEDULED` backfill from becoming a new forecast; the 53 escaped only because their provider status was already terminal.

### Capture-cadence watchdog

`ops/validation/capture_health_watchdog.py` checks the expected hourly cadence of:

- successful/partial `api_tennis_scheduled_fixtures` runs;
- `LIVE_BATCH_RUNTIME` health rows; and
- completed non-experimental prediction runs.

It derives the expected top-of-hour execution slots from the schedule (not from the runs that happened to be recorded), so a report states how many expected executions are missing rather than only that a gap exists. It reports duplicate executions in one slot and executions outside the window instead of counting them as healthy coverage. It exits non-zero on `NO_HEARTBEAT` or any gap beyond 1.5× the expected interval, and returns a distinct code if a configured notification fails. It is deliberately a separate process: a watchdog hosted inside the live runner cannot detect the runner being stopped. Database access is read-only; it performs no writes and no provider calls.

Notifications are new-incident only:

- one incident notification per distinct failing-dataset set;
- an unchanged incident is suppressed, so the watchdog does not page every interval;
- one recovery notification when the failing set becomes empty;
- `--state-path` persists the suppression state across process restarts.

```powershell
python ops/validation/capture_health_watchdog.py \
  --window-hours 6 --expected-minutes 60 \
  --state-path capture-watchdog-state.json \
  --out-json capture-health.json
```

Set `TENNIS_CAPTURE_WATCHDOG_WEBHOOK_URL` (or pass `--webhook-url`) only when an
external notifier is authorised. `--no-notify` evaluates and persists state
without sending. No webhook is configured or called by this repository.

Regression coverage: `tests/test_capture_health_watchdog.py` checks the exact
12–16 November boundaries (98 expected slots, 3 served, 95 missed), a silent
runner with no rows, duplicate executions in one slot, out-of-window
executions, incident deduplication, a changed incident set, one-time recovery
and a notifier-failure exit code.

### Lifecycle traceability

`build_fixture_timeline` retains the **original scheduled start**, every observed start/status change, the first source observation and ingestion, PIT visibility against the original deadline, and an explicit retrospective flag. `reconcile_snapshot_changes` distinguishes created, unchanged, updated, rescheduled, postponed, explicit removal, complete-snapshot absence and incomplete-snapshot absence. Absence is never converted to a void.

Regression coverage includes missing, updated, removed, postponed and rescheduled fixtures, plus retrospective discovery, the 30-minute target guard and silent-scheduler gaps.

## 4. Reschedule traceability (456/674 and now 474/674)

The 14–25 November internal stored cohort is **674 fixtures**. At the earlier 25 November snapshot, 456 had more than one distinct scheduled start. On the current read, the same cohort has **474** with more than one start and 653 with more than one version:

| Distinct starts | Matches (current) |
|---:|---:|
| 1 | 200 |
| 2 | 166 |
| 3 | 122 |
| 4 | 85 |
| 5 | 41 |
| 6 | 30 |
| 7 | 13 |
| 8 | 4 |
| 9 | 5 |
| 10 | 3 |
| 11 | 4 |
| 12 | 1 |

The 18-fixture increase is expected: new schedule versions continue to arrive for recent fixtures. It is not a correction of the earlier count. The append-only `match_versions` rows preserve every observed start, so the original start remains the first version's start and the latest start is the last observable version. The evidence includes the highest-change fixtures with their ordered `(start, status, as_of)` observations.

A changed start is not a cancellation. The cohort contains explicit `CANCELLED` and `POSTPONED` statuses; no absent fixture is inferred void.

## 5. Distinct selection populations

The following are not interchangeable and are reported separately for the 14–25 November window:

| Population | Definition | Count |
|---|---|---:|
| Original Manager consensus | first completed non-experimental run per match, `manager_status = CONSENSUS` | **78** |
| Tracking-only selections | earliest aligned selection, `A1_A2_WIDE_SPREAD` | **105** |
| Published/aligned Model Picks | earliest aligned selection of either type, on time | **163** |
| `decision_settlements` | actually settled Type A decisions | **11** |
| Completed result observations | any stored completed/retired/walkover/defaulted result | **598** |

Overlaps are recorded explicitly:

- all 105 tracking-only selections are inside the published/aligned population;
- 23 Manager-consensus matches have no published/aligned pick (below the 60% floor or not on time);
- 108 published picks have no Manager consensus at the same first run (tracking-only cases or a different run selection);
- the 11 settled decisions are a small subset of 163 published picks; most picks are tracking/paper populations and are not staking decisions;
- 598 completed results are a tournament-result population, not a selection population.

No two populations are treated as equivalent and no void is inferred.

## 6. Limits

- This is still an **internal stored-record** census. It is not an independent official ATP/Challenger denominator; official discovery recall remains unknown.
- The catch-up run's payload is the only evidence of which past events the provider returned. It cannot prove that no other official fixtures existed.
- The 18 additional reschedules since the earlier snapshot mean counts are time-varying; any future count must state its as-of cutoff.
- The watchdog detects cadence failure only if it is scheduled outside the live runner. Deployment and scheduling remain an operational action, not part of this read-only PR; no notifier is configured here.
- The platform-level reason the Railway cron trigger stopped is unknown. The evidence establishes non-execution, not the internal Railway fault.
- Notification delivery itself is not exercised against a real external endpoint; only the decision/deduplication logic and the state round-trip are tested.

## Verdict

The 53-fixture coverage gap is explained by a **four-day silent scheduler non-execution**: the last cycle on 12 November 2026 ended, released its lock and wrote an error heartbeat; 95 expected hourly executions then did not happen; a manual redeploy on 16 November restarted the service; and the first observations after recovery were fetch-time observations of already-terminal events. It is not a provider request failure, late refresh or missing fixture identity, and no deployment or cron-config change occurred at the start of the gap. Why the platform trigger stopped remains unresolved from the available read-only evidence. The safeguards added here fail closed on post-start targets, make capture silence and missed executions detectable from outside the runner with incident/recovery deduplication, and preserve original starts and every subsequent schedule/status change. Manager consensus, tracking-only selections, published Model Picks and settlement are reconciled as distinct populations.

## Evidence hashes

```text
docs/research/evidence/fixture_discovery_deadline_root_cause_20261125.json
SHA256 E5E84ECFE0B5A6B018AB96161ACE66FF8FC5FDA527B26FADD811DBB895794428

docs/research/evidence/railway_scheduler_gap_evidence_20261125.json
SHA256 B78C25B711626682F2C92993E4C1458A6F67C0A02DFD34B1DFDF57053AAE8EBE
```
