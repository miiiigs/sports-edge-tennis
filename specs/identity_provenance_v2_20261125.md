# Identity provenance v2 — reserved schema proposal (coordinate with PR #392)

**Status:** design only. **No migration file is added by PR #388.**
**Reserved numbers:** `0023` (Manager C feature snapshots) and `0024` (OddsPapi
identity history) are owned by integration PR #392. This proposal must be allocated a
number **after 0024** by #392 or by the next integration train. Do not create
`db/migrations/0025_*.sql` from this branch.

**Date:** 2026-11-25
**Related code:** `src/sports_edge_tennis/data/model_identity.py`,
`src/sports_edge_tennis/data/identity_provenance.py`,
`src/sports_edge_tennis/replay/identity_pit.py`

## 1. Problem

The live→history identity bridge is a read-time mapping. It reads reference data that
is *mutable in place*:

| Table | Mutable surface | Current PIT control | Residual gap |
|---|---|---|---|
| `players` | `canonical_name`, `updated_at` | reuse only when `updated_at <= as_of` or `updated_at = created_at` | no name history, so a mutated name cannot be reconstructed |
| `player_aliases` | `alias`/`player_id` in place; `updated_at` on every re-observation | `created_at`, `first_observed_at`, ingest-run visibility | an out-of-band re-point is undetectable and untimestamped |
| `model_identity_bridge_events` | append-only, but `activated_at` is caller-supplied | `created_at`, `evidence_as_of`, `activated_at` | fine once `created_at` is gated (done in code) |

PR #388 closes every channel that can be closed without a schema change and records a
deterministic reference digest with each capture, so out-of-band drift is *detectable*
after the fact. Exact reconstruction still needs versioned reference history.

## 2. Proposed objects (allocate after 0024)

### 2.1 `player_name_versions`

Append-only version history of canonical player names.

```sql
CREATE TABLE player_name_versions (
    player_name_version_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    player_id BIGINT NOT NULL REFERENCES players(player_id),
    canonical_name TEXT NOT NULL CHECK (btrim(canonical_name) <> ''),
    valid_from TIMESTAMPTZ NOT NULL,
    valid_to TIMESTAMPTZ,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ingest_run_id BIGINT REFERENCES ingest_runs(ingest_run_id),
    actor TEXT NOT NULL CHECK (btrim(actor) <> ''),
    reason TEXT NOT NULL CHECK (btrim(reason) <> ''),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);
CREATE UNIQUE INDEX uq_player_name_versions_current
    ON player_name_versions (player_id) WHERE valid_to IS NULL;
CREATE INDEX idx_player_name_versions_lookup
    ON player_name_versions (player_id, valid_from, valid_to);

CREATE TRIGGER trg_player_name_versions_append_only_mutation
BEFORE UPDATE OR DELETE ON player_name_versions
FOR EACH ROW EXECUTE FUNCTION raise_append_only_mutation();
```

A rename closes the open version (`valid_to`) and inserts a new open version in the
same transaction. Backfill: one open row per existing player with
`valid_from = players.created_at` (never earlier, never invented).

### 2.2 `player_alias_versions`

Append-only alias linkage history. Identity columns are immutable per version.

```sql
CREATE TABLE player_alias_versions (
    player_alias_version_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    player_alias_id BIGINT NOT NULL,           -- logical alias row, no FK: row may be replaced
    player_id BIGINT NOT NULL REFERENCES players(player_id),
    source_system TEXT NOT NULL CHECK (btrim(source_system) <> ''),
    normalized_alias TEXT NOT NULL CHECK (btrim(normalized_alias) <> ''),
    valid_from TIMESTAMPTZ NOT NULL,
    valid_to TIMESTAMPTZ,
    first_observed_at TIMESTAMPTZ,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ingest_run_id BIGINT REFERENCES ingest_runs(ingest_run_id),
    actor TEXT NOT NULL CHECK (btrim(actor) <> ''),
    reason TEXT NOT NULL CHECK (btrim(reason) <> ''),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);
CREATE INDEX idx_player_alias_versions_pit
    ON player_alias_versions (normalized_alias, valid_from, valid_to);
CREATE INDEX idx_player_alias_versions_player
    ON player_alias_versions (player_id, valid_from, valid_to);

CREATE TRIGGER trg_player_alias_versions_append_only_mutation
BEFORE UPDATE OR DELETE ON player_alias_versions
FOR EACH ROW EXECUTE FUNCTION raise_append_only_mutation();
```

### 2.3 Tighten `player_aliases` in place

Convert the mutable table into a value-immutable one: only a monotonic
`last_observed_at` / `updated_at` refresh remains allowed.

```sql
CREATE OR REPLACE FUNCTION guard_player_alias_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF (to_jsonb(NEW) - ARRAY['last_observed_at', 'updated_at'])
       IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['last_observed_at', 'updated_at']) THEN
        RAISE EXCEPTION
            'player_aliases identity linkage is immutable under identity_provenance_v2; '
            'insert a player_alias_versions row instead.';
    END IF;
    IF NEW.last_observed_at < OLD.last_observed_at THEN
        RAISE EXCEPTION 'player_aliases.last_observed_at must be monotonic.';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_player_aliases_identity_guard
BEFORE UPDATE ON player_aliases
FOR EACH ROW EXECUTE FUNCTION guard_player_alias_mutation();
```

Effect on the resolver: once this exists, `player_aliases.updated_at` becomes a
*refresh* marker only and the current default gate
(`created_at`/`first_observed_at`) is provably sufficient. The optional
`alias_refresh_gate` can then be retired.

### 2.4 `identity_resolution_provenance`

The durable runtime record. One row per player per resolution event.

```sql
CREATE TABLE identity_resolution_provenance (
    identity_resolution_provenance_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    prediction_run_id BIGINT REFERENCES prediction_runs(prediction_run_id),
    match_id BIGINT NOT NULL REFERENCES matches(match_id),
    match_version_id BIGINT NOT NULL REFERENCES match_versions(match_version_id),
    predicted_at TIMESTAMPTZ NOT NULL,          -- the resolution as_of
    live_player_id BIGINT NOT NULL REFERENCES players(player_id),
    model_player_id BIGINT NOT NULL REFERENCES players(player_id),
    method TEXT NOT NULL,
    evidence TEXT NOT NULL,
    name_source TEXT NOT NULL,
    candidate_count INTEGER NOT NULL DEFAULT 0 CHECK (candidate_count >= 0),
    matched_alias TEXT,
    bridge_event_id BIGINT REFERENCES model_identity_bridge_events(model_identity_bridge_event_id),
    visible_from TIMESTAMPTZ,
    reference_policy TEXT NOT NULL,
    reference_sha256 TEXT NOT NULL CHECK (length(reference_sha256) = 64),
    record_sha256 TEXT NOT NULL CHECK (length(record_sha256) = 64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (method IN ('HISTORICAL_ID','PERSISTED_BRIDGE','ALIAS','AMBIGUOUS',
                      'NO_CANDIDATE','NO_HISTORICAL_CANDIDATE','NO_PLAYER'))
);
CREATE UNIQUE INDEX uq_identity_resolution_provenance_key
    ON identity_resolution_provenance (prediction_run_id, match_id, live_player_id);
CREATE INDEX idx_identity_resolution_provenance_match
    ON identity_resolution_provenance (match_id, predicted_at);

CREATE TRIGGER trg_identity_resolution_provenance_append_only_mutation
BEFORE UPDATE OR DELETE ON identity_resolution_provenance
FOR EACH ROW EXECUTE FUNCTION raise_append_only_mutation();
```

This is the table that makes item 3 of the December mission durable in production. Until
it exists, the JSONL capture record in
`src/sports_edge_tennis/validation/a1_identity_bridge_forward_shadow.py` is the only
carrier of the same fields.

## 3. Resolver migration path (code, no schema dependency)

1. **Now (PR #388):** strict reference gates + `identity_reference_sha256` in every
   capture; optional provider-alias key tier; refresh gate for measurement only.
2. **After 2.3 lands:** drop `alias_refresh_gate`; alias keys become provably
   value-immutable.
3. **After 2.1/2.2 land:** resolve canonical names and aliases from the version tables
   at `as_of` instead of the current-state tables. The `reference_sha256` then becomes a
   *verification* digest rather than the only evidence.
4. **After 2.4 lands:** the observer writes provenance rows in the same transaction as
   the prediction, and the JSONL archive becomes a portable export rather than the
   primary record.

## 4. Backfill rules

- Never invent a pre-ingestion `valid_from`. Backfill uses the earliest provable
  timestamp: `players.created_at`, `player_aliases.first_observed_at` or
  `player_aliases.created_at`.
- Never backdate a version to make a historical replay resolve more matches. A failed
  PIT resolution is evidence, not a defect to patch.
- Backfill is idempotent and records `ingest_run_id` + `actor`.

## 5. Acceptance for the migration PR

- Up/down migration parity in the PostgreSQL Foundation CI.
- Cumulative schema snapshot regenerated; manifest entry added **after 0024**.
- Append-only triggers verified by a mutation-rejection test.
- Resolver equivalence test: version-table resolution reproduces current-state
  resolution for cutoffs where no mutation has occurred.
- No production activation in the same PR.
