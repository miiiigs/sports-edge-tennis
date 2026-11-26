# Production readiness for evidence migrations 0023/0024 (2026-11-26)

Controlled release review. **No production migration was applied, no deployment
was made, no provider request, purchase or forecasting-policy change occurred.**
All production access was an authorised read-only session over Railway's SSH
tunnel (`railway connect Postgres-Sdqz --tunnel-only`), with
`default_transaction_read_only=on` and `REPEATABLE READ READ ONLY`. Credentials
are not recorded here.

Probe commit: `8b74a7f` (integration PR #392); OddsPapi companion probe:
`ac85f34`.

## 1. Ledger probe result (authorised read-only)

`python ops/validation/production_schema_ledger_probe.py` succeeded against the
production database (saved output:
`docs/research/evidence/production_schema_ledger_probe_20261126.json`):

| Item | Result |
|---|---|
| Signatures present | 22 / 24 |
| Highest contiguous version | `0022_defaulted_outcome` |
| `0023_prediction_feature_snapshots` | absent |
| `0024_oddspapi_event_identity_history` | absent |
| Explicit migration ledger table | none (objects are the de-facto ledger) |
| Append-only triggers on the new tables | none (tables absent) |

The previous session could not reach the database (`postgres-sdqz.railway.internal`
is private and the runner container was exited). The SSH tunnel path now works
and is the route used here. Database host resolution remains private; nothing was
opened publicly.

## 2. Production environment and volumes

`ops/validation/production_migration_readiness_probe.py` (new, read-only;
saved output:
`docs/research/evidence/production_migration_readiness_20261126.json`):

- PostgreSQL **18.6** (the disposable CI chain is 16; the full chain was also
  re-applied on 18 here).
- Database size **857,470,655 bytes** (~818 MiB) at the saved probe snapshot.
- Role: `postgres`, superuser via the tunnel, `CREATE` on `public`.
- Session defaults: `lock_timeout=0`, `statement_timeout=1min`,
  `maintenance_work_mem=64MB`, `max_locks_per_transaction=64`,
  `max_parallel_maintenance_workers=2`.
- At probe time: 0 other sessions, 0 sessions in transaction, 0 blocking locks,
  0 replication slots.

| Table | Rows | Size |
|---|---|---|
| `match_versions` | 233,634 | 56 MB |
| `predictions` | 171,657 | 57 MB |
| `prediction_runs` | 2,631 | 960 kB |
| `manager_consensus_results` | 57,219 | 10 MB |
| `match_result_observations` | 134,719 | 28 MB |
| `matches` | 134,393 | — |
| `players` | 6,256 | — |
| `tournament_editions` | 2,579 | — |
| `agent_config_versions` | 3 | — |
| `rating_snapshots` | **0** | 32 kB |

Counts are from the saved probe snapshot. Production is live and advances hourly;
the first read (minutes earlier) saw 233,558 match versions and 171,540
predictions. Predictions per agent: 57,219 each for `AGENT_1_SURFACE_ELO`,
`AGENT_2_HIERARCHICAL_MARKOV`, `AGENT_3_INTRANSITIVITY`.

## 3. Migration dependencies and lock surface

`0023` needs:

- `uq_predictions_lineage_identity` on
  `(prediction_id, prediction_run_id, match_id, agent_code)` — present, correct
  columns, zero duplicate rows, zero predictions without a run.
- `agent_config_versions` — present (3 rows).
- `players`, `matches`, `prediction_runs` — present.
- `raise_append_only_mutation()` — present.
- `match_versions` unique on `match_version_id`; zero duplicate
  `(match_version_id, player_1_id, player_2_id)` rows.

`match_versions` lock surface: 2 foreign keys reference it, 1 non-internal
trigger, 0 dependent views, 4 existing indexes. `CREATE UNIQUE INDEX` inside the
0023 transaction takes a `SHARE` lock on the ~233.6k-row table for the build. It
blocks writes, not reads. Measured on a real-volume clone: **428.6 ms** for the
index, **588.8 ms** for the whole 0023 transaction; 0024 took **5.7 ms**.

`lock_timeout=0` in production means a queued DDL lock can wait indefinitely and
block later queries behind it. The release must set an explicit
`lock_timeout` (5–10 s) in the migration session and abort on timeout.

## 4. Schema fidelity and drift

Compared the production clone against a fresh PostgreSQL 18 chain applied from
`db/migrations/manifest.json`:

- All **44 manifest tables** match exactly on columns, data types, nullability,
  defaults, constraints and indexes.
- Production has **15 additional tables** created by runtime code, not by the
  manifest: `decision_close_evidence`, `decision_veto_audits`,
  `history_refresh_checkpoints`, `live_model_state_checkpoints`,
  `paper_execution_performance_v1`, `paper_executions`, `shadow_strategy_clv`,
  `shadow_strategy_observation_odds`, `shadow_strategy_observations`,
  `shadow_strategy_positions`, `shadow_strategy_registry`,
  `shadow_strategy_runs`, `shadow_strategy_settlements`, `type_b_scan_runs`,
  `type_b_value_signals`. A manifest-only rebuild is therefore not a complete
  application schema; backup/restore must always be physical/logical of the
  whole database.
- **Documentation drift (pre-existing):** `db/schema/0003…0024` declare
  `match_versions.match_start_at TIMESTAMPTZ NOT NULL`, but migration 0003
  intentionally executes `DROP NOT NULL`, and production is nullable with
  117,713 NULL SACKMANN rows. The migration chain and production agree; the
  cumulative schema snapshots are stale on nullability. The integration test
  compares columns/constraints/indexes but not nullability, so it did not catch
  this. This does not affect 0023/0024 but should be corrected separately.

## 5. Isolated backup and restoration

- Logical dump: `pg_dump -Fc -Z6` over the read-only tunnel, **114.7 MB**.
- Restored into a disposable PostgreSQL **18.6** container (`prodclone`):
  59 tables, 171,540 predictions, 233,558 match versions,
  134,718 result observations — identical to production.
- Fingerprints identical before and after:
  - predictions `c717dc8030305a4898e59731dd2e1390`
  - match versions `ac99db47b0112929b23058285e9f3b21`
- Seeded one real-lineage snapshot row and one identity-history row, dumped the
  migrated clone, restored into a second database (`prodclone2`), and verified
  both evidence rows and both append-only triggers survived. This proves newly
  captured evidence is included in backup/restore; it does not prove the evidence
  existed in production (it did not; the tables were absent).

## 6. Isolated migration and rollback tests on real data

On the restored production clone:

1. Applied `0023` then `0024` with `lock_timeout=5s`, `statement_timeout=120s`.
   Both committed; existing fingerprints unchanged.
2. Inserted a snapshot for an existing `AGENT_1_SURFACE_ELO` prediction using its
   exact run, match, agent, bound match version and resolved config. Accepted.
3. Reverse player orientation: rejected by the composite FK.
4. `rating_uncertainty` without a source: rejected by the CHECK.
5. All test inserts rolled back; production data untouched.
6. `0024` down then `0023` down: clean; fingerprints unchanged; only the new
   table, index and triggers removed.
7. Re-applied 0023/0024: clean.
8. `0022` down still refuses by design ("forward-only"); downgrade is bounded at
   0023/0024, as documented.

The full chain (0001–0024) also applied cleanly to an **empty PostgreSQL 18**
database: 44 tables, both evidence tables, both append-only triggers.

## 7. Application rollback compatibility

- The pre-integration commit `ad65894` (base branch head) has **zero references**
  to `prediction_feature_snapshots` or `oddspapi_event_identity_history` in
  `src/` (`git grep`).
- 0023/0024 only add objects; they alter no existing table, row, constraint or
  index (verified by fingerprints and object diffs).
- Old code therefore runs unchanged against the migrated schema and simply never
  writes snapshots/history. New code is the reverse: snapshot persistence is
  fail-closed, so it must not run before the migration.
- Rolling code back stops new snapshots; it does not delete captured ones. Dropping
  0024/0023 later destroys forward-only evidence, so export first if it matters.

## 8. Runtime recheck (unchanged behaviour)

On the local PostgreSQL test database:

- `tests/test_prediction_feature_snapshots.py`,
  `tests/test_prediction_feature_snapshot_persistence.py`,
  `tests/test_prediction_target_persistence.py`,
  `tests/test_frozen_manager_manifest.py`: **29 passed**.
- `tests/test_full_slate_runner.py`: **30 passed**.
- `tests/test_offseason_evidence_schema_guards.py`,
  `tests/test_offseason_evidence_foundation_integration.py`: **9 passed**.
- Capture sequence incl. a new complete pre-cutoff rehearsal: **6 passed**.

These re-exercise request-time capture by reference, snapshot/prediction
atomicity (a failed snapshot rolls back the prediction, run finalization and the
rest of the matrix), exact composite lineage, reversed-orientation rejection,
cold-start handling, and frozen Manager manifest/weights/boundary parity.

## 9. Controlled release checklist (owner approval required)

0. **Approval and window.** Record owner approval; announce a migration window;
   confirm no other deploy/migration is running. Do not proceed if the ledger
   probe shows 0023/0024 already partially applied.
1. **Backup first.** `pg_dump -Fc` plus a restore test to an isolated PostgreSQL
   18 instance. Verify row counts, the two fingerprints above, and that the 15
   runtime tables are present. A Railway volume is not a backup.
2. **Ledger.** Run `ops/validation/production_schema_ledger_probe.py`; require
   highest contiguous `0022_defaulted_outcome` and both new tables absent.
3. **Readiness.** Run `ops/validation/production_migration_readiness_probe.py`;
   require 0 blocking sessions, 0 duplicate lineage rows, prerequisites present;
   record table sizes.
4. **Apply 0023** in a reviewed session with `SET lock_timeout='5s'` and
   `SET statement_timeout='120s'`. Expect a sub-second-to-few-second index build
   and a brief write pause on `match_versions`. Abort on lock timeout; retry in a
   quieter window.
5. **Apply 0024.** Verify both triggers and both tables.
6. **Deploy code** only after the migrations. New code is fail-closed without the
   tables.
7. **First live slate verification.** Every `AGENT_1_SURFACE_ELO` prediction has
   exactly one snapshot; run status `COMPLETED`; Manager counts reconcile;
   `audit_run_capture` reports the expected (currently late) sequence; no
   prediction field changed.
8. **Rollback.** Code first. Then `0024` down, then `0023` down. Do not attempt
   below 0022. Export identity history before dropping it.

## 10. Remaining blockers (unchanged)

1. Identity resolution still runs after first issue in the live cycle
   (see `ODDSPAPI_CAPTURE_EVIDENCE_20261126.md`); the measurement path exists,
   the reordering does not.
2. Independent OddsPapi tournament mappings do not exist (0 rows), and 27/27
   cached tournaments have no edition end date; 360/2,579 editions lack one.
3. Provider-vs-canonical start semantics remain unresolved; the full measured
   drift is −60…+60 minutes, median −10, not a single fixed offset.
4. No ODDSPAPI rows exist in the normalised `odds_snapshots`; 10,795 raw
   observations are stored but none is a qualified canonical price.
5. Historical `match_start_at` is NULL for 117,713 SACKMANN versions; inactivity
   and career-length research must not infer dates for them.
6. Migration 0023's index build is a brief write pause on `match_versions`; it is
   small at current volume but must be timed.
