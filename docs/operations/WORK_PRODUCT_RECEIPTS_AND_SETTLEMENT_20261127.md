# Work-product receipts and settlement scheduling — operational connection

**Date:** 27 November 2026
**Branch / PR:** `research/official-census-daily-waterfall-20261125` (PR #391)
**Status:** implemented and tested in isolation. No production activation, no
provider call, no purchase, no database write outside test transactions.
**Code:**
`src/sports_edge_tennis/ops/work_product_receipts.py`,
`src/sports_edge_tennis/ops/settlement_schedule.py`,
`ops/validation/publish_work_product_receipt.py`,
`ops/validation/settlement_schedule_dryrun.py`
**Tests:** `tests/test_work_product_receipts.py` (11),
`tests/test_work_product_receipts_postgres.py` (3, PostgreSQL CI),
`tests/test_settlement_schedule.py` (14)
**Integration coordination:** PR #394 (`9343a75`), which owns the
`forward_*` ledger, archive dispatch and stored-official settlement adapter.

## 1. Why the receipt publisher exists

The monitoring adapter could classify a run, but it read a receipt that did not
exist. This publisher is the missing producer. It verifies the **persisted
database rows** for each of the seven stages and writes
`TENNIS_MONITORING_RECEIPT_V1` only when every required stage matches.

It never trusts:

- a process exit code;
- a log line;
- a caller-supplied row count;
- a zero-row result without an explicit acknowledgement.

## 2. Stage verification

| Stage | Expected evidence | Persisted evidence | Failure |
|---|---|---|---|
| Fixture discovery | `ingest_runs.rows_inserted + rows_updated` for `API_TENNIS` scheduled fixtures in the window | persisted `match_versions` linked to those runs, in-scope tours/draw | partial ingest, row-count mismatch, zero rows without acknowledgement |
| Forecast execution | `prediction_run_targets` for the completed run | targets with three distinct agent predictions locked at or before `start - 30m` | missing/late agent, empty targets, non-completed run |
| Bookmaker acquisition | `decisions` with `decision_status='BET'` | a non-null market price and a `decision_odds_snapshots` row | BET without a persisted price |
| Independent archival | `forward_originals` for the experiment | latest `ARCHIVE` delivery event `DELIVERED`/`RECONCILED` | missing table/manifest or unacknowledged originals |
| Official settlement | BET decisions with a visible result observation | `decision_settlements` non-pending and bound to the visible observation | visible result unsettled, or bound to a stale observation |
| Bookmaker settlement | only under explicitly approved rules | `forward_settlement_events` with `pathway='BOOKMAKER'` | approved rule without a graded ledger |
| Prospective candidate | `forward_fixture_expectations` | matching `forward_originals` | predeclared candidate not executed |

If a required support table does not exist, the stage is `UNVERIFIED`, never
zero. If no bookmaker rule is approved, the bookmaker stage is explicitly
`EMPTY_ACKNOWLEDGED` with `NO_APPROVED_BOOKMAKER_RULES`; no result is inferred.

An empty eligible slate is successful only when discovery is explicitly
acknowledged (`--acknowledge-empty-discovery`) and every stage has a zero
expectation. The monitoring adapter then classifies `NO_ELIGIBLE_FIXTURES`.

## 3. CLI

```powershell
$env:TENNIS_DATABASE_URL = <authorised read-only DSN>
python ops/validation/publish_work_product_receipt.py `
  --slot 2027-01-17T00:00:00Z `
  --window-start 2027-01-16T00:00:00Z `
  --window-end 2027-01-18T00:00:00Z `
  --prediction-run-id 123 `
  --experiment-id <frozen experiment> `
  --cutoff 2027-01-18T00:00:00Z `
  --as-of 2027-01-18T00:00:00Z `
  --receipt-path /data/receipts/2027-01-17T00:00:00Z.json
```

The connection runs with `default_transaction_read_only=on`. The only write is
the receipt file. Exit `0` means every stage verified; `1` means at least one
stage is missing and the receipt cannot signal success.

## 4. Actual test results

| Test group | Result |
|---|---|
| Receipt unit (all seven, partial, zero, stale, archive, bookmaker, no tables) | 11 passed |
| Receipt PostgreSQL integration (real rows, run, predictions, price, result, settlement, forward ledger) | 3 tests; skipped without DSN, run in PostgreSQL Foundation CI |
| Settlement scheduler (orientation, source, reschedule, stale, duplicate, correction, cancellation, bookmaker rule) | 14 passed |

The PostgreSQL test creates only the `forward_*` tables it reads, so it does not
depend on PR #394's migration history. It proves the SQL against actual rows,
including the three-agent on-time forecast and the result/settlement binding.

## 5. Settlement scheduling pathway (Phase 2)

`settlement_schedule.py` is scheduler-neutral. It returns a plan, never writes.

**Sporting pathway**

- source must be explicitly approved with a reference;
- source participants must match the canonical pair;
- result cannot precede the scheduled/actual start (except a cancellation);
- a reschedule after the result blocks settlement;
- an outcome older than the approved stale window blocks;
- `COMPLETED` grades WON/LOST from the official winner;
- `RETIRED`/`WALKOVER`/`DEFAULTED` grade VOID; `CANCELLED` grades CANCELLED;
- a newer revision produces an append-only correction with an explicit
  predecessor, never a rewrite;
- the same revision is not settled twice.

**Bookmaker pathway**

- only an explicitly approved `(bookmaker, rule_version) -> reference` is
  accepted;
- the grade must be supplied explicitly;
- retirement/walkover/default are never converted into a bookmaker grade;
- no rule, no grade, or a stale result blocks.

This is deliberately separate from PR #394's
`official_settlement_adapter.settle_stored_official_result`, which performs the
database write. The planner decides eligibility; the adapter remains the single
writer. A future scheduler can call the planner for every unresolved decision
and pass only `SETTLE_SPORTING`/`SETTLE_BOOKMAKER`/`CORRECT_SETTLEMENT` plans to
the adapter.

Dry run:

```powershell
python ops/validation/settlement_schedule_dryrun.py --input-json candidates.json
```

Exit `0` when no plan is blocked; `1` when any plan is blocked.

## 6. Coordination with PR #394

- The publisher reads, but never creates or migrates, `forward_originals`,
  `forward_delivery_events`, `forward_fixture_expectations` and
  `forward_settlement_events`.
- It does not import PR #394-only modules, so PR #391 remains independently
  testable and mergeable.
- PR #394's archive dispatch, receipt store and official settlement adapter
  remain the source of truth for their writes.
- No migration was added to PR #391. If both branches merge, the receipt
  publisher needs no schema change; it uses `to_regclass` and fails closed when a
  table is absent.

## 7. Remaining blockers

1. Wire the receipt publisher into the deployed pipeline after each work cycle.
2. Provide a durable `/data/receipts` volume and the monitoring service.
3. Approve the official result source and participant-order contract.
4. Approve bookmaker rules before any bookmaker settlement.
5. Owner approval for activation and cost.

## 8. Explicit non-claims

- No production receipt was published.
- No settlement was written.
- No provider was called and no quota was consumed.
- No bookmaker result was inferred.
- The PostgreSQL integration tests are the only place real rows are used, and
  only inside CI's test database.
