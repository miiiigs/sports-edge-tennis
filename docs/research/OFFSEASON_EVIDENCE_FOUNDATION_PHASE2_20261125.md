# Off-season evidence foundation - phases 1-4 execution (2026-11-25)

Integration branch `integration/offseason-evidence-foundation-20261125`
(PR #392). Research/engineering record only: no production migration, no
deployment, no provider request, no purchase, no change to forecasting policy.
The frozen Manager C envelope, token and policy constants are unchanged.

## 1. Phase 1 - isolated PostgreSQL chain

Disposable PostgreSQL 16.15 database (`offseason_evidence_20261125`, Docker,
local only). Script:
`ops/validation/production_schema_ledger_probe.py` for read-only schema
inventory; the chain itself was applied from `db/migrations/manifest.json`.

| Check | Result |
|---|---|
| Full manifest forward (0001-0024) | 44 tables |
| `prediction_feature_snapshots` + `oddspapi_event_identity_history` | present |
| Append-only triggers on both evidence tables | present, blocks UPDATE and DELETE |
| Runtime prediction persisted through `persist_and_finalize_prediction_matrix` | 3 predictions, run COMPLETED |
| Snapshot insert with reversed player orientation | rejected (composite FK) |
| Snapshot with `rating_uncertainty` but no source | rejected (CHECK) |
| Snapshot `rating_uncertainty` NULL, source NULL | accepted and stored as NULL |
| DOWN 0024 then 0023 | clean, dependent rows removed |
| Re-UP 0023 then 0024 | clean, pre-existing predictions preserved |
| DOWN 0022 | blocked by its explicit forward-only guard, schema unchanged |
| Cumulative `db/schema/0024` vs migrated schema | columns, constraints and indexes identical |
| Populated 0022 -> 0023 -> 0024 upgrade | existing `matches`/`match_versions` rows preserved, orientation index created |

The full chain cannot be downgraded to 0001: `0022_defaulted_outcome.down.sql`
raises `Migration 0022 is forward-only: DEFAULTED is a distinct canonical
sporting outcome`. That is pre-existing, documented behaviour, not introduced
here. Downgrade of this integration is therefore bounded at 0023/0024.

### Production ledger (read-only)

Attempted, blocked:

- `railway run` injects only `TENNIS_DATABASE_URL`, whose host is
  `postgres-sdqz.railway.internal`. It is not resolvable off the Railway
  private network (`getaddrinfo failed`).
- `railway ssh -s tennis-live-runner` refuses: `Your service's container is
  not running (status: exited)`. Restarting or deploying it is out of scope.

A ready-to-run, read-only probe was added:
`ops/validation/production_schema_ledger_probe.py`. It sets
`default_transaction_read_only=on`, `REPEATABLE READ READ ONLY`, prints only
object presence and evidence-row counts, and never prints DSNs or values. It
was validated against the disposable database (24/24 signatures, contiguous
through 0024). An operator with private-network access must run it before any
release; this session could not.

## 2. Phase 2 - runtime feature persistence

Migration 0023 now enforces lineage in the database instead of by convention:

- `match_version_id` and `agent_config_version_id` are required and foreign-keyed;
- `(prediction_id, prediction_run_id, match_id, agent_code)` references the
  exact immutable prediction row (`uq_predictions_lineage_identity`);
- `(match_version_id, player_1_id, player_2_id)` references the bound match
  version, backed by the new `uq_match_versions_id_orientation` unique index, so
  a reversed snapshot is impossible;
- `rating_uncertainty` requires a non-null, non-blank source (the previous
  definition accepted a NULL source because `btrim(NULL)` is NULL).

Wiring:

- `agents/agent1_surface_elo.py::resolved_player_states()` is now the single
  resolver used by both `predict_from_replay` and the caller, so captured state
  cannot diverge from evaluated state. Missing players still resolve to the
  exact cold-start default that the model uses.
- `runner/full_slate.py::_run_agent1` captures `Agent1FeatureContext` from those
  two frozen `PlayerRatingState` objects *before* prediction, and binds
  `target.match_version_id` and the resolved `agent_config_version_id`.
- `data/prediction_persistence.py::persist_and_finalize_prediction_matrix`
  inserts the snapshot immediately after each prediction inside the existing
  `conn.transaction()`. A snapshot failure rolls back the prediction, the run
  finalization and every other matrix row; verified by test.
- Capture is by reference to immutable dataclasses, not by replaying afterwards.
  Advancing the replay later changes a new dict, never the captured context
  (test `test_request_time_state_survives_a_later_replay_advance`).

Cold-start handling: `surface_match_count()` returns a genuine `0` when a
player has no surface history, so `surface_known=true` remains saveable. Surface
ratings that were never computed stay NULL; the blended effective rating is not
stored as if it were a raw surface rating.

`identity_complete` remains a *history-completeness* flag (both players have at
least one prior match in the shared pool). It is deliberately not conflated
with OddsPapi/player-source identity verification, which is tracked separately
by `replay/identity_pit.py` and the identity history.

Original forecast parity: the snapshot adds a row and reads existing config
identifiers; it does not rewrite prediction status, side, probability,
representation, model/feature versions or Manager inputs. Tests assert the
persisted prediction fields equal the snapshot metadata for the same row.

Frozen Manager manifest: `tests/test_frozen_manager_manifest.py` pins the
archived RFC 3161 token SHA256
(`1d27027d...78ab`), both envelope hashes, the generation time, the
freeze/evaluation/settlement boundaries and `MANAGER_C_ORIGINAL_SOURCE_V1` /
`MANAGER_C_FORWARD_ABLATION_V1` groups and targets. All pass.

## 3. Phase 3 - OddsPapi provenance and ordering

### Atomic append-only history

`live/oddspapi_identity_cache.py` now wraps history insert + cache upsert and
history insert + cache delete in one transaction. Injected trigger failures
prove both directions roll back: no history row without the cache change, no
delete without the invalidation record. The `to_regclass` fail-open guard is
retained, with its comment corrected to migration 0024.

`partition_cached_identities(..., invalidate_stale=False)` is a new read-only
mode for audits. The live default is unchanged (it still invalidates), but an
audit no longer has to delete state to measure it.

### Reproducible PIT history read

New `live/oddspapi_identity_history.py`:

- visible rows are only those with `recorded_at <= T` (insert time, never
  writable by the caller);
- the latest visible event wins; `UPSERTED` = warm, `INVALIDATED` = cold,
  no event = `NEVER_RECORDED`;
- results carry `oddspapi_identity_history_pit_v1`;
- bulk summarisation and per-match history listing included;
- evidence reads fail closed with `IdentityHistoryUnavailable` if 0024 is not
  applied.

Limitation: `recorded_at` is the transaction timestamp, which is at or before
commit. A row can therefore become visible marginally earlier than its commit.
Writers run after the cutoff in the normal cycle, which makes this conservative;
it is documented rather than papered over.

`validation/odds_identity_market_export.py` now prefers the append-only history
for the "warm at cutoff" counter (`identity_history_warm_at_cutoff`), falling
back to the mutable current row only when 0024 is absent, and reports which
method was used. The rule set and both tolerances are unchanged.

### Tournament mappings and edition end dates

No new independent mapping was created or inferred. The existing evidence
stands: `tournament_source_ids` has zero `ODDSPAPI` rows; the export path only
accepts an independent `ODDSPAPI` source mapping **and** a populated edition
window (`tournament_editions.end_date`). All three implicated editions have
`end_date IS NULL`, so the gate cannot pass. The cache's own
`oddspapi_tournament_id` is deliberately not treated as independent proof.
Obtaining verified mappings requires a reviewed producer plus edition dates
from an authoritative source; that is an owner decision, not an inference.

### 40-60 minute start differences

Reproduced from the archived deep dive (8/8 fixtures, -40 to -60 minutes,
0 inside 30 minutes). Code inspection explains the inconsistency without
changing it:

- discovery reconciliation accepts `ODDSPAPI_DISCOVERY_MAX_START_DELTA_MINUTES
  = 60` (`live/batch.py`);
- the evidence audit and cache invalidation use 30 minutes
  (`odds_identity_market_funnel.MAX_DRIFT`, `DEFAULT_MAX_START_DRIFT`);
- the reconciler default is 120 minutes.

These are three different semantics for the same field (provider session time
vs canonical match start). No tolerance was loosened. Resolving it needs an
approved scheduling-semantics study plus authoritative start data.

### Prospective capture sequence

`validation/prospective_capture_sequence.py` defines and measures the safe
order: identity recorded -> price observed and ingested -> prediction locked.
`audit_run_capture()` is read-only and classifies each fixture as
`PRE_CUTOFF_COMPLETE`, `IDENTITY_LATE+PRICE_LATE`, `IDENTITY_MISSING+...`, etc.
Crucially, a quote observed before the cutoff but ingested after it counts as
late. The archived 47-second same-cycle identity is therefore classified as
late, not as coverage.

This supplies the measurement contract; it does not reorder the live cycle.
Moving identity resolution ahead of first issue is a runtime behaviour change
that needs its own approval and a provider-quota plan.

### Additional qualifying prices

**Measured: 0 new verified pairs.** No production database, raw provider
payload or licensed quote rows were accessible from this session, and
purchasing or fetching is out of scope. The previously documented
counterfactual 18-pair figure remains a labelled counterfactual (missing
mapping + start drift removed) and is not reported as recovery.

## 4. Phase 4 - acceptance and operations

Deployment ordering (only after approval):

0. Take and restore-test a production backup first; an existing Railway
   volume is not evidence of recoverability.
1. Verify the production schema ledger with the probe; record the highest
   contiguous version.
2. Apply `0023` then `0024` in one reviewed window. Both are additive. `0023`
   creates `uq_match_versions_id_orientation` on `match_versions` inside its
   transaction; it takes a share lock on that table for the build. Expect a
   brief write pause proportional to table size.
3. Deploy code. Code must not run before the migrations: snapshot persistence
   is deliberately fail-closed and will roll back predictions if the table is
   absent.
4. Run one live slate and confirm: every `AGENT_1_SURFACE_ELO` prediction has
   exactly one snapshot; run status COMPLETED; Manager counts reconcile;
   `audit_run_capture` reports the expected (likely late) sequence.

Rollback:

1. Roll back code first (to the pre-integration image). Predictions then stop
   writing snapshots; nothing else changes.
2. `0024_oddspapi_identity_history.down.sql` drops the history table.
3. `0023_prediction_feature_snapshots.down.sql` drops the snapshot table and
   `uq_match_versions_id_orientation`.
4. Do **not** attempt to roll back below 0022: its down migration refuses by
   design. No migration in this release mutates existing rows, so a schema
   rollback loses only the new evidence tables (forward-only history is not
   reconstructible afterwards; export first if it matters).

## 5. Validation evidence

- GitHub Actions `postgres-foundation`: **2456 passed, 0 failed** in
  233.32 s on the fix commit after the schema-parity correction. This run
  includes the psql-backed PostgreSQL integration suite that cannot run
  without a local client.
- Local targeted set (feature-snapshot unit/persistence/atomicity, schema
  guards, integration manifest, identity cache/history, capture sequence,
  frozen manifest): 59 passed; the schema-guard file was re-run after the
  CI-driven correction (7 passed).
- Isolated chain harness: every check listed in section 1 is true, including
  the populated 0022 -> 0024 upgrade.
- The full local suite was attempted but abandoned on Windows because each
  database test performs a full 40-table TRUNCATE and the shared Docker
  instance made a single-process run exceed the time budget; CI is the
  authoritative full-suite result above.

## 6. Remaining blockers

**Update (2026-11-26):** the production ledger was subsequently verified through
an authorised read-only SSH-tunnel session. Highest contiguous migration is
`0022_defaulted_outcome`; 0023/0024 are absent. See
`OFFSEASON_EVIDENCE_FOUNDATION_PRODUCTION_READINESS_20261126.md` for the probe
result, production volumes, backup/restore evidence and the controlled release
checklist. The blockers below remain unless that document says otherwise.

1. Production ledger unverified: private-network-only database, exited
   service. Owner must run the probe or grant read-only access.
2. Independent OddsPapi tournament mappings and edition end dates still do not
   exist in the data; do not fabricate them.
3. Provider vs canonical start semantics unresolved (three tolerances).
4. Identity resolution still happens after first issue in the live cycle.
   The measurement path exists; the reordering does not.
5. Only forward evidence: historical predictions cannot be backfilled
   truthfully, and the mutable cache cannot reconstruct deleted identities.
6. Migration 0023 index build is a brief write pause on `match_versions`.
