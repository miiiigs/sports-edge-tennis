# Pre-issue pilot connection, strict qualification and readiness (2026-11-27)

Research/engineering record for the worker branch on PR #392. The worker head
`f234a26` and central integration PR #394 head `9343a756` were reconciled
before editing. PR #394 already contained superset versions of two overlapping
modules; these were adopted verbatim rather than reimplemented:

- `src/sports_edge_tennis/live/preissue_acquisition.py`
- `src/sports_edge_tennis/validation/api_tennis_acquisition_plan.py`
- `src/sports_edge_tennis/validation/preissue_pilot_calendar.py` (new to this
  worker branch)
- their upstream tests.

No migration was allocated. Production cron, provider modes, Manager policy and
request ledger configuration are unchanged. No billable request was made.

Reproduce:

```
python -m sports_edge_tennis.validation.preissue_pilot_rehearsal \
  --output docs/research/evidence/preissue_pilot_rehearsal_20261127.json
python ops/validation/preissue_pilot_readiness_20261127.py \
  --output docs/research/evidence/preissue_pilot_readiness_real_20261127.json
```

## 1. Runtime connection

`src/sports_edge_tennis/live/preissue_pilot.py` is the inactive execution path.
It is not imported by the live runner.

Implemented:

| Requirement | Implementation |
|---|---|
| atomic allowance reservation | `ReservationStore`; `InMemoryReservationStore`, `SqliteReservationStore` (BEGIN IMMEDIATE), and `PostgresOddspapiReservationStore` wrapping the existing `reserve_attempt` |
| concurrent worker exclusion | injected `WorkerLease`; `InMemoryLease` and `PostgresWorkerLease` (`pg_try_advisory_lock`) |
| request deduplication | deterministic `PilotRequest.dedupe_key`; in-cycle set plus durable unique `(dedupe_key, retry_sequence)` |
| bounded timeouts | policy timeout passed to transport; completion buffer enforced before and after the call |
| retry accounting | one reservation per retry sequence; transport errors record failure; retries stop at `max_attempts` or deadline |
| partial failure | every request returns a terminal result and the cycle continues |
| durable observation timestamps | `PilotObservation` carries received/observed/persisted clocks; `JsonObservationSink` fsyncs JSONL, production sink boundary is injected |
| pre-issue and T−60 enforcement | request deadline `min(issue, start−60m)`; late responses are persisted but never qualified |

A missing entitlement, missing authorization reference, missing allowance,
deadline miss, duplicate reservation or worker conflict returns before the
transport is called. `execute_pilot` makes zero provider calls in those paths.

The production transport boundary is injected. The real OddsPapi adapter must
use the existing ledger binding; this work does not execute it.

## 2. Mapping and qualification

### Strict paired lineage

`src/sports_edge_tennis/validation/paired_qualification_audit.py` replaces the
older five-minute timestamp-pairing for strict evidence. A pair qualifies only
when both sides share:

- the same canonical match, bookmaker, provider and source system;
- the same `raw_odds_observation_id` **and** `source_snapshot_id`;
- OPEN status on both sides;
- observation and ingestion at or before the prediction lock;
- observation inside the six-hour freshness window;
- provider identity mapping created at or before the observation;
- a live-observed payload, not a historical reconstruction;
- pre-start observation, valid prices and a plausible de-vig total.

Single-sided, mixed-bookmaker, mixed-provider, different-observation,
different-snapshot, suspended, late, backfilled, late-ingested, identity-after-
acquisition, retrospective and post-start cases have explicit rejection codes.

### Measured production audit

| Provider | Candidate market groups | Qualified | Status |
|---|---:|---:|---|
| ODDSPAPI | 11,288 | **0** | all rejected late observation |
| API_TENNIS | 50,586 | **0** | all rejected late observation |

The zero is not inferred: every stored quote remains later than the first Agent
1 lock. The audit now also proves the paired raw lineage is complete where a
pair exists.

### Identity continuity

For 522 ODDSPAPI-priced fixtures:

| Provider mapping relative to first price | Fixtures |
|---|---:|
| Mapping existed before first price | 20 |
| Mapping recorded after first price | **502** |

A later mapping cannot support original-time attribution even if the raw price
looks early.

### Actual competitive start

No match-level actual/competitive-start column is stored. The only `started_at`
columns are ingestion and prediction-run clocks. `match_start_at` remains a
scheduled start and is not relabelled.

### Tournament edition mapping

- ODDSPAPI `tournament_source_ids`: **0**.
- Cached ODDSPAPI editions: **27**.
- Cached editions with `end_date IS NULL`: **27/27**.

The independent mapping/edition gate therefore remains closed.

## 3. Controlled isolated rehearsal

Evidence: `docs/research/evidence/preissue_pilot_rehearsal_20261127.json`.

The rehearsal runs the real pilot executor and strict qualifier with a
deterministic transport. It produces:

| Measure | Value |
|---|---:|
| Simulated HTTP attempts | 9 |
| Atomic reservations | 9 |
| Durable observation records | 8 |
| Captured two-sided observations | 3 |
| Original-time qualified | 3 |
| Production provider calls / writes / spend | 0 / 0 / 0 |

Sequence demonstrated in the happy path:

```
discovery request
 -> identity resolution (canonical match 1, ODDSPAPI + API-Tennis ids, DIRECT)
 -> atomic reservations (3)
 -> simultaneous provider responses
 -> normalization to paired lineage
 -> strict original-time qualification (2)
 -> forecast issuance (p1=0.62)
```

Failure scenarios all fail closed:

- **late response**: persisted with `LATE_RESPONSE`, zero qualified, forecast
  abstains;
- **crash/reset**: `PROVIDER_ERROR`, zero qualified;
- **exhausted budget**: second request `BUDGET_EXHAUSTED` before transport;
- **missing prices**: `NO_TWO_SIDED_PRICE`, zero qualified;
- **rescheduled fixture**: `DEADLINE_MISSED` before transport;
- **lost acknowledgement**: `ACKNOWLEDGEMENT_LOST`, not captured, not qualified;
- **retrospective reconstruction**: `REJECTED_RETROSPECTIVE`, not qualified.

The rehearsal asserts that no later observation can be promoted to original-time
evidence.

## 4. Real acquisition readiness

Read-only snapshot (next 14 days):

- Eligible fixtures: **0 ATP, 23 Challenger**.
- Dual-mapped fixtures: 7; with OddsPapi tournament id: 3.
- Balanced 160 target: **0/80 ATP and 23/80 Challenger**, shortfall 80 and 57.
  No competition substitution.
- Observed batch cadence: 48 runs in 7 days, median gap 3,416.6 s (~57 min).

Pilot calendar dry run under the real gates:

| Outcome | Fixtures |
|---|---:|
| Dual mapping unavailable | 20 |
| Authorization or allowance blocked | 2 |
| No feasible slot/cycle cap | 1 |
| Planned but not executed | **0** |

Entitlements:

- API-Tennis: `ENTITLEMENT_ONLY` — historical access proven, current quota
  unproven.
- OddsPapi: `UNVERIFIED` in this probe (no owner-approved current quota
  evidence supplied).

No owner authorization reference was supplied, so zero requests were planned
for execution.

## 5. Delivery and outstanding approvals

Actual measured totals for this work:

- actual provider requests: **0**
- actual request reservations: **0**
- genuine price observations: **0**
- original-time qualifications: **0**

Required before a real pilot can execute:

1. Owner-approved API-Tennis current quota/entitlement and request allowance.
2. Owner-approved OddsPapi remaining allowance from the ledger.
3. A non-empty owner authorization reference.
4. Independent ODDSPAPI tournament mapping and edition end dates.
5. A balanced cohort; off-season currently has 0 ATP fixtures.
6. A production transport adapter wired to the existing ledger binding; this
   worker branch provides the inactive executor and stores but does not execute
   a billable request.

No market superiority, profitability, 90% mapping or 80% T−60 coverage is
claimed. Those remain unachieved targets.
