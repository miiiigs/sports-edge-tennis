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

| Last heartbeat before gap | `2026-11-13T07:01:16Z`, `ERROR`, `live_batch_status=2` |
|---|---|
| Next heartbeat | `2026-11-16T23:05:34Z`, `ERROR`, `live_batch_status=2` |
| Detectable gap | **95 missing hourly intervals** |
| Recovery | `2026-11-17T00:03:28Z`, `HEALTHY` |
| First scheduled-fixture runs after recovery | `07:02`, then the catch-up bulk run at `08:16` |

The 13–16 November silence produced **no request-failure rows** because no request was attempted and no cycle wrote a health row. This is the exact failure mode the old health check could not see: it evaluated zero targets only if a cycle ran. A stopped scheduler is silently healthy in a table written only by that scheduler.

### Why the catch-up did not restore pre-match coverage

The first post-gap scheduled-fixture run at 08:16 was a larger catch-up payload (1,025 rows versus the usual 300–500), not an hourly slate refresh. The provider payload already contained the 14–16 November events in terminal states. Persisting them created new canonical matches with a single post-start version and a result observation. The live runner's own target list only includes `SCHEDULED` matches, so these were never targeted; in fact none of the 53 ever appears in `prediction_run_targets`.

The cause is therefore:

1. **Primary:** the live batch scheduler stopped from 13 Nov 07:01 to 16 Nov 23:05 UTC (about 3 days 16 hours).
2. **Secondary:** when it resumed, the first scheduled-fixture fetch was a retrospective catch-up whose observation clock is the fetch time, so historical events entered the database as new post-start versions with results.
3. **Not the cause:** a provider outage with recorded request failures, a late hourly refresh, missing provider fixture IDs, or missing canonical matches.

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

It exits non-zero on `NO_HEARTBEAT` or any gap beyond 1.5× the expected interval. It is deliberately a separate process: a watchdog hosted inside the live runner cannot detect the runner being stopped. It performs no writes and no provider calls.

```powershell
python ops/validation/capture_health_watchdog.py --window-hours 6 --expected-minutes 60
```

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
- The watchdog detects cadence failure only if it is scheduled outside the live runner. Deployment and scheduling remain an operational action, not part of this read-only PR.

## Verdict

The 53-fixture coverage gap is fully explained: a silent 3-day-16-hour stop of the live batch scheduler, followed by a retrospective catch-up that persisted already-completed events with fetch-time observation clocks. It is not a provider request failure, late refresh or missing fixture identity. The safeguards added here fail closed on post-start targets, make capture silence detectable from outside the runner, and preserve original starts and every subsequent schedule/status change. Manager consensus, tracking-only selections, published Model Picks and settlement are now reconciled as distinct populations.

## Evidence hashes

```text
docs/research/evidence/fixture_discovery_deadline_root_cause_20261125.json
SHA256 E5E84ECFE0B5A6B018AB96161ACE66FF8FC5FDA527B26FADD811DBB895794428
```
