# Identity provenance v2 — implemented schema (migration 0025)

**Status:** implemented in `db/migrations/0025_identity_provenance_v2.{up,down}.sql`.
**Applied to production:** no. **Activation:** no. `can_promote=false`.

**Migration numbering:** `0023` (Manager C feature snapshots) and `0024` (OddsPapi
identity history) are owned by integration PR #392. This branch imports that train and
allocates the next free version, `0025`, to identity provenance. Do not renumber.

**Date:** 2026-11-25 (design) · 2026-11-26 (implementation)
**Related code:** `src/sports_edge_tennis/data/model_identity.py`,
`src/sports_edge_tennis/data/identity_provenance.py`,
`src/sports_edge_tennis/data/identity_runtime.py`,
`src/sports_edge_tennis/replay/identity_pit.py`

## 1. Problem

The live→history identity bridge is a read-time mapping over reference data that was
mutable in place:

| Table | Former mutability | Former PIT control | Residual gap |
|---|---|---|---|
| `players` | `canonical_name` updated in place | `updated_at <= as_of` heuristic | a mutated name could not be reconstructed |
| `player_aliases` | linkage could be re-pointed out of band | `created_at`, `first_observed_at`, ingest visibility | re-pointing was undetectable and untimestamped |
| `model_identity_bridge_events` | append-only, caller-dated `activated_at` | `created_at`, `evidence_as_of`, `activated_at` | closed in code |

Migration 0025 closes the first two with append-only history and a linkage guard, and
adds the durable runtime record.

## 2. Implemented objects

### 2.1 `player_name_versions`

Append-only canonical-name history. There is **no `valid_to` column**: closing a row
would be an UPDATE, which contradicts append-only history. The effective interval is
`[valid_from, next valid_from)` and is derived at read time.

```sql
CREATE TABLE player_name_versions (
    player_name_version_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    player_id BIGINT NOT NULL REFERENCES players(player_id),
    canonical_name TEXT NOT NULL CHECK (btrim(canonical_name) <> ''),
    valid_from TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ingest_run_id BIGINT REFERENCES ingest_runs(ingest_run_id),
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    CHECK (recorded_at >= valid_from - INTERVAL '1 second')
);
CREATE UNIQUE INDEX uq_player_name_versions_player_valid_from
    ON player_name_versions (player_id, valid_from);
```

- Backfill: one row per existing player with `valid_from = GREATEST(created_at, updated_at)`.
  This is the earliest time the **current** value is provably true. Using `created_at`
  alone would assert that a post-creation rename was always the name. In the observed
  production corpus `updated_at = created_at` for every player, so this is not lossy
  there; if a rename did occur, the backfill is conservative.
- New player: `AFTER INSERT ON players` writes the initial version.
- Rename: `AFTER UPDATE OF canonical_name` writes a new version at `NEW.updated_at`.
  `recorded_at` is the real write clock, so a backdated rename is visible as such.
- UPDATE/DELETE on the version table is rejected by the shared append-only trigger.
- Resolver compatibility: when no version is visible at the cutoff, a player row that was
  **never updated** (`updated_at = created_at`) still supplies its current name, exactly
  as the pre-0025 policy allowed. A renamed row (`updated_at > created_at`) does not: it
  fails closed unless an older visible version exists. The legacy "row created after the
  cutoff but never updated" channel therefore remains; it is bounded by source-id and
  history visibility, not by the name gate.

### 2.2 `player_alias_versions`

Append-only alias linkage history. Every alias insertion writes one row at
`created_at`; the guard below prevents later linkage mutation.

```sql
CREATE TABLE player_alias_versions (
    player_alias_version_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    player_alias_id BIGINT NOT NULL,
    player_id BIGINT NOT NULL REFERENCES players(player_id),
    source_system TEXT NOT NULL,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    first_observed_at TIMESTAMPTZ,
    valid_from TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ingest_run_id BIGINT REFERENCES ingest_runs(ingest_run_id),
    actor TEXT NOT NULL,
    reason TEXT NOT NULL
);
CREATE INDEX idx_player_alias_versions_pit
    ON player_alias_versions (normalized_alias, valid_from DESC, player_id);
```

### 2.3 `player_aliases` linkage immutability

`guard_player_alias_identity()` rejects any UPDATE that changes a column other than
`last_observed_at`/`updated_at`, and rejects a non-monotonic `last_observed_at`. The
pipeline's `ON CONFLICT ... DO UPDATE` only refreshes those two columns, so normal
traffic is unaffected.

A reviewed repair may opt in for one transaction:

```sql
SET LOCAL tennis.identity_reference_maintenance = 'on';
```

That escape hatch is session-scoped and must be recorded by the repair itself. It is
the only way to re-point an existing alias; a new linkage should be a new reviewed row.

### 2.4 `identity_resolution_provenance`

One row per player per resolution event.

```sql
CREATE TABLE identity_resolution_provenance (
    identity_resolution_provenance_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    prediction_run_id BIGINT NOT NULL REFERENCES prediction_runs(prediction_run_id),
    match_id BIGINT NOT NULL REFERENCES matches(match_id),
    match_version_id BIGINT NOT NULL REFERENCES match_versions(match_version_id),
    predicted_at TIMESTAMPTZ NOT NULL,
    original_player_id BIGINT NOT NULL REFERENCES players(player_id),
    model_player_id BIGINT NOT NULL REFERENCES players(player_id),
    resolution_method TEXT NOT NULL,
    evidence TEXT NOT NULL,
    name_source TEXT NOT NULL,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    matched_alias TEXT,
    bridge_event_id BIGINT REFERENCES model_identity_bridge_events(...),
    visible_from TIMESTAMPTZ,
    reference_policy TEXT NOT NULL,
    reference_sha256 TEXT NOT NULL CHECK (length(reference_sha256) = 64),
    record_sha256 TEXT NOT NULL CHECK (length(record_sha256) = 64),
    source_record_sha256 TEXT CHECK (
        source_record_sha256 IS NULL OR length(source_record_sha256) = 64
    ),
    capture_source TEXT NOT NULL CHECK (
        capture_source IN ('LIVE_RUNTIME', 'FORWARD_SHADOW')
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (visible_from IS NULL OR visible_from <= predicted_at)
);
CREATE UNIQUE INDEX uq_identity_resolution_provenance_key
    ON identity_resolution_provenance (
        prediction_run_id, match_id, original_player_id
    );
```

The writer at `data/identity_runtime.py` validates the match version, run lineage and
visibility before inserting. The same key with a different digest fails closed as a
lineage conflict. The reader verifies every digest.

## 3. Resolver migration path

1. **Done (PR #388):** strict gates + reference digest + forward capture.
2. **Done (0025, not applied):** version tables, alias guard, durable provenance.
3. **Done in code:** resolver reads `player_name_versions` and
   `player_alias_versions` when the tables exist; on a pre-0025 schema it falls back to
   the legacy current-table gates.
4. **Next:** apply 0025 after 0023/0024, re-run the reference-policy audit under
   `identity_reference_pit_v3`, and wire the provenance writer into the activated
   prediction transaction. No backfill of past runs.

## 4. Backfill rules

- Never invent a pre-ingestion `valid_from`. Backfill uses
  `GREATEST(players.created_at, players.updated_at)` for names and
  `player_aliases.created_at` for aliases. The name rule is deliberately conservative:
  if a rename happened, the current value is not asserted to predate `updated_at`.
- Never backdate a version to make a historical replay resolve more matches. A failed
  PIT resolution is evidence, not a defect to patch.
- Backfill is idempotent by the unique `(player_id, valid_from)` /
  `(player_alias_id, valid_from)` indexes.

## 5. Acceptance status

| Check | Status |
|---|---|
| Up/down migration parity | implemented; local PostgreSQL test |
| Cumulative schema snapshot regenerated | yes (`db/schema/0025_identity_provenance_v2.sql`) |
| Manifest entry after 0024 | yes |
| Append-only triggers verified | local regression tests |
| Alias linkage guard verified | local regression tests |
| Version-table resolver equivalence | local regression tests |
| No production activation | confirmed |
| Production migration applied | **no** |
