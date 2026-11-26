# Evidence-foundation schema requirements and rollback contract (2026-11-26)

Handoff to central integration (PR #390) and DeepSeek 1. No migration was
applied to production in this workstream. This document records the schema
contract, lock safeguards and evidence-preserving rollback behaviour that the
central archive must honour.

## 1. Reserved migration numbers

- `0023_prediction_feature_snapshots`
- `0024_oddspapi_identity_history`

These numbers are reserved by integration PR #392. The central archive PR #390
must not allocate or reuse 0023/0024. If PR #390 needs earlier numbers, it must
coordinate a manifest renumbering; do not create a second implementation of
either table.

Production ledger at the last authorised read: highest contiguous
`0022_defaulted_outcome`; 0023/0024 absent. Production is PostgreSQL 18.6.

## 2. Required objects for 0023

`0023_prediction_feature_snapshots` depends on:

- `prediction_runs(prediction_run_id)`, `matches(match_id)`,
  `match_versions(match_version_id)`, `players(player_id)`,
  `agent_config_versions(agent_config_version_id)`, `predictions`;
- `agent_code` enum, `tennis_surface` enum;
- `raise_append_only_mutation()` trigger function (from 0005);
- unique index `uq_predictions_lineage_identity` on
  `predictions(prediction_id, prediction_run_id, match_id, agent_code)`;
- a new unique index `uq_match_versions_id_orientation` on
  `match_versions(match_version_id, player_1_id, player_2_id)`, used by the
  composite orientation foreign key.

The snapshot row is one per `prediction_id` (`UNIQUE`) and is append-only. It
stores only values genuinely computed at prediction time. `rating_uncertainty`
is NULL unless both a value and a named source are supplied. `identity_complete`
is a history-completeness flag (both players have at least one prior match), not
a provider identity claim.

Runtime contract:

- insert the snapshot in the same transaction as the prediction;
- a snapshot failure rolls back the prediction, run finalization and the rest of
  the matrix;
- code must not run before the migration: persistence is fail-closed without
  the table.

## 3. Required objects for 0024

`0024_oddspapi_identity_history` depends on `matches(match_id)` and
`raise_append_only_mutation()`. It is append-only. Every cache upsert and
invalidation records the values in force at that moment, so a point-in-time
identity can be read with `recorded_at <= T`. The mutable
`oddspapi_event_identity_cache` remains the live lookup; history is the
reproducible evidence.

Read contract: `live/oddspapi_identity_history.identity_at_cutoff()` fails
closed with `IdentityHistoryUnavailable` when 0024 is absent. Evidence code must
not silently fall back to the mutable current row without labelling the method.

## 4. Migration lock safeguards

- Set `lock_timeout` (5–10 s) and `statement_timeout` (120 s) in the migration
  session. Production defaults are `lock_timeout=0` and
  `statement_timeout=1min`; a queued DDL lock could otherwise wait indefinitely.
- Apply 0023 before 0024, in one reviewed window.
- 0023 creates `uq_match_versions_id_orientation` inside its transaction. On the
  real-volume clone (233.6k `match_versions`) this took **428.6 ms** for the
  index and **588.8 ms** for the whole transaction; 0024 took **5.7 ms**. It
  takes a `SHARE` lock on `match_versions` for the build: reads proceed, writes
  pause briefly.
- Abort on lock timeout and retry in a quieter window; do not lower
  `lock_timeout` to force it through.
- Verify 0 blocking sessions and 0 duplicate
  `(match_version_id, player_1_id, player_2_id)` rows before the build.

## 5. Evidence-preserving application rollback

Ordinary application rollback is a **code** rollback, not a schema rollback:

1. deploy the pre-integration image; it has zero references to either evidence
   table (verified by `git grep` on `ad65894`);
2. old code continues to run against the migrated schema and simply stops
   writing snapshots/history;
3. captured rows are not deleted by a code rollback.

Schema down migrations are a separate, destructive operation. They now refuse
to drop a non-empty evidence table:

```
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM prediction_feature_snapshots) THEN
        RAISE EXCEPTION 'refusing to drop prediction_feature_snapshots: evidence rows present; export or archive first';
    END IF;
END
$$;
```

and the equivalent guard for `oddspapi_event_identity_history`. Empty tables
still roll back cleanly for disposable test databases. To roll back a populated
schema deliberately: export/archive first, then truncate/drop under explicit
owner approval. Do not drop collected evidence during an ordinary application
rollback.

Tests:

- `test_down_migration_refuses_to_drop_collected_snapshot_evidence` — inserts a
  real prediction + snapshot, applies the down migration, expects the guard
  exception, and verifies the row and table still exist.
- `test_0024_down_migration_refuses_to_drop_collected_history` — same for the
  identity history.
- Full schema-guard, integration and snapshot-persistence set: **18 passed** on
  local PostgreSQL 16 with the real manifest.

## 6. Runtime parity

- The snapshot write adds one row and reads existing identifiers; it does not
  rewrite prediction status, side, probability, representation, model/feature
  versions or Manager inputs.
- `Agent1FeatureContext` is captured by reference to the immutable
  `PlayerRatingState` pair before prediction; advancing the replay later cannot
  mutate it.
- `identity_complete` remains a history-completeness flag and is not conflated
  with OddsPapi/player-source identity.
- `rating_snapshots` remains empty (no runtime writer); `rating_uncertainty`
  must stay NULL unless a producer supplies a value and source.

## 7. Known schema/documentation drift

- `db/schema/0003…0024` declare `match_versions.match_start_at TIMESTAMPTZ NOT
  NULL`, but migration 0003 intentionally executes `DROP NOT NULL`; production
  is nullable with 117,713 NULL SACKMANN rows. The migration chain and
  production agree; the cumulative snapshots are stale on nullability. This does
  not affect 0023/0024 but should be corrected separately.
- Production has 15 additional runtime-created tables not in the manifest
  (`decision_close_evidence`, `shadow_strategy_*`, `type_b_*`, etc.). A
  manifest-only rebuild is not a complete application schema; backup/restore
  must be physical/logical of the whole database.
- A full chain downgrade below 0022 is blocked by
  `0022_defaulted_outcome.down.sql` by design ("forward-only"). Downgrade of
  this integration is bounded at 0023/0024.

## 8. Release checklist (owner approval required)

0. Record approval and a migration window.
1. Back up and restore-test to an isolated PostgreSQL 18 instance; verify row
   counts, fingerprints and the 15 runtime tables.
2. Run the ledger probe; require 0022 contiguous and 0023/0024 absent.
3. Run the readiness probe; require 0 blocking sessions, 0 duplicate lineage
   rows, prerequisites present.
4. Apply 0023 with `lock_timeout='5s'`, `statement_timeout='120s'`.
5. Apply 0024 and verify both append-only triggers.
6. Deploy code only after the migrations.
7. First live slate: every `AGENT_1_SURFACE_ELO` prediction has exactly one
   snapshot; run COMPLETED; Manager counts reconcile; capture audit reports the
   expected (currently late) sequence.
8. Rollback: code first; then 0024 down, 0023 down. Export identity history
   before an intentional schema drop. Do not attempt below 0022.

## 9. What central integration must not do

- Do not renumber or duplicate 0023/0024 in PR #390.
- Do not make evidence tables mutable or cascade-delete them.
- Do not drop the evidence tables as part of an application rollback.
- Do not treat a missing OddsPapi normalized row as proof the provider had no
  price.
- Do not infer independent OddsPapi tournament mappings from the cache's own
  tournament id; require an independent mapping and an edition window.
- Do not backfill `rating_uncertainty`, career start or inactivity from partial
  data.
