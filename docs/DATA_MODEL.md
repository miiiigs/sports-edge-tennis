# Data Model

## Purpose

This document describes the committed tennis data foundation that future ingestion,
modelling, pricing, and evaluation work must build on.

The design is PostgreSQL-first, append-only for historical observations and model outputs,
and fail-closed around player identity and point-in-time reconstruction.

## Layers

```text
SOURCE / RAW PROVENANCE
        |
        v
NORMALIZED DOMAIN DATA
        |
        v
MODEL FEATURES / RATINGS
        |
        v
MODEL OUTPUTS
        |
        v
MANAGER / PRICE DECISIONS
        |
        v
RESULTS / EVALUATION
```

Raw observations never get overwritten by model outputs. Model outputs never overwrite
raw observations.

## Entity overview

### Ingestion and provenance

- `ingest_runs`
  - Tracks source, dataset, counters, status, source file/version metadata, and compact
    error summaries.

### Player identity

- `players`
  - Canonical internal player identity with immutable surrogate key.
- `player_source_ids`
  - One source identifier maps to one canonical player. Duplicate source IDs per source are
    rejected.
- `player_aliases`
  - Stores observed textual variants with provenance and observation windows.
- `identity_resolution_events`
  - Explicitly records `RESOLVED`, `UNMATCHED`, and `AMBIGUOUS` outcomes.
  - Ambiguous cases never silently collapse to one player.
- `identity_resolution_candidates`
  - Stores candidate canonical players with foreign-key integrity.
  - `UNMATCHED` events carry zero candidates.
  - `AMBIGUOUS` events must retain at least two candidates.
  - `RESOLVED` events remain explicit on the event row and do not retain candidate rows.

### Tournament and match identity

- `tournaments`
  - Stable canonical tournament identity across seasons.
- `tournament_source_ids`
  - Stable provider-to-tournament mapping.
- `tournament_editions`
  - Season/year-specific event edition metadata.
- `matches`
  - Stable internal match identity.
- `match_versions`
  - Append-only normalized match state over time.
  - Stores participants, surface, tier, start time, qualifying flag, round, status, and
    temporal fields.
- `match_source_ids`
  - Stable source-to-match identity mapping for provider match identifiers or deterministic
    source hashes.
- `match_version_sources`
  - Append-only provenance for each normalized `match_versions` row.
  - Preserves which source record produced which normalized version without overloading the
    stable identity bridge.

### Results and statistics

- `match_result_observations`
  - Separate append-only result layer. Pre-match match identity is not the same thing as
    post-match outcome state.
- `match_stat_observations`
  - Raw or normalized source statistics by player, match, and provenance.
  - Stores source-level service and return inputs without converting them into model
    outputs.
  - Logical idempotency is `(source_system, source_record_id/hash, match_id, player_id,
    observation_kind, source_observed_at)`, which allows one provider record to fan out to
    multiple player rows while still rejecting exact duplicates.

### Market observations

- `odds_snapshots`
  - Append-only odds history.
  - Raw bookmaker observations only.
  - Market identity is explicit through `market_type`, `selection_type`, and optional
    `line_value`.
  - Logical idempotency is `(source_system, source_snapshot_id/hash, bookmaker_code, match_id,
    market_type, selection_type, line_value, source_observed_at)`, with PostgreSQL `NULLS NOT
    DISTINCT` semantics on nullable `line_value`.

### Ratings and model outputs

- `rating_snapshots`
  - Immutable player rating states by metric, surface, model version, feature version, and
    `as_of`.
  - Overall ratings use `surface IS NULL`; surface-specific ratings use native enum values.
  - PostgreSQL `NULLS NOT DISTINCT` uniqueness preserves one logical overall row per identity
    without enum-to-text casts.
- `predictions`
  - Immutable agent-level outputs.
  - Explicit `ABSTAINED` support.
  - No market price fields exist in this contract.

### Manager, decisions, settlement, and CLV

- `decisions`
  - Separate manager/price layer.
  - Records `BET`, `NO_BET`, and `VETOED`.
  - Distinguishes `TYPE_A_MODEL_EDGE` from `TYPE_B_PRICE_DISPERSION`.
  - Side-labelled selections must remain internally coherent: `PLAYER_1` / `SIDE_1` pair only
    with `selection_side = 1`, and `PLAYER_2` / `SIDE_2` pair only with `selection_side = 2`.
- `decision_agents`
  - Per-agent participation record for each decision.
- `decision_settlements`
  - Separate settlement boundary from sports results.
- `decision_clv`
  - Future CLV storage contract with price-taken and closing references.

### Data health

- `data_health_checks`
  - Freshness, coverage, unmatched-count, and status snapshot per source/dataset check.

## Identity and idempotency

### Canonical identities

- Internal player, tournament, edition, and match identities use surrogate keys.
- Names are never primary keys.

### Source identity rules

- Stable source IDs are unique per source where available.
- When a provider lacks a stable source record identifier, deterministic hashes are stored
  at the provenance edge and used as fallback uniqueness anchors.
- Fuzzy matching is never used as an idempotency key.

### Deterministic source hash contract

All fallback provenance hashes use the repository-wide contract below before any provider
adapter exists:

- Algorithm: SHA-256
- Recipe version: `SE_SOURCE_HASH_V1`
- Serialization envelope: compact JSON encoded as UTF-8
- Canonical shape:

```json
{
  "version": "SE_SOURCE_HASH_V1",
  "source_system": "SOURCE_NAME",
  "fields": [
    ["field_name_a", "value_a"],
    ["field_name_b", null]
  ]
}
```

- Field ordering: `fields` must be sorted by canonical field name ascending
- Null representation: JSON `null`
- Unicode normalization: NFKC before serialization
- Whitespace normalization: trim leading/trailing whitespace and collapse internal whitespace
  runs to a single ASCII space for text fields
- Case policy: preserve case after upstream canonicalization; do not silently lower-case at the
  hash layer
- Timestamp representation: UTC RFC 3339 with microseconds and trailing `Z`
- Decimal representation: plain string notation, no exponent, no leading `+`, trailing zeros
  removed, decimal point removed when the number is integral
- Digest encoding: lowercase hexadecimal

## Mutable vs immutable

### Mutable master data

- `players`
- `player_aliases`
- `tournaments`
- `tournament_editions`

These may be corrected over time, but the historical operational truth still lives in the
append-only provenance and versioned observation tables.

### Immutable historical records

- `ingest_runs`
- `match_versions`
- `match_result_observations`
- `match_stat_observations`
- `odds_snapshots`
- `rating_snapshots`
- `predictions`
- `decisions`
- `decision_agents`
- `decision_settlements`
- `decision_clv`
- `data_health_checks`

## Point-in-time rule

For historical reconstruction at time `T`, future code must query only records where:

```text
as_of <= T
```

and apply match/result/prediction lock boundaries as described in
[POINT_IN_TIME.md](/C:/dev/sports-edge-tennis/docs/POINT_IN_TIME.md).

## T-ED0 locked lineage contracts

The following contracts are now frozen in documentation even though the runtime does not yet
implement them:

- Full-slate execution is required. The system must evaluate every scheduled in-scope Tennis
  singles match, not only later-qualified bets.
- Every predictive-agent output must survive regardless of disagreement, `NO_BET`, veto,
  stale market, or publication outcome.
- Historical prediction identity must be exact and replayable by run, agent, config,
  versions, and lock time.
- `NO_BET` is first-class internal history, not missing data.
- Market/value logic remains downstream of sporting prediction.

## T-ED1 planned schema design

Recommended migration name:

```text
0004_prediction_runs_and_policy_versions
```

This design composes additively with the merged `0001` to `0003` foundation. It adds run,
policy, config, and lineage identity without rewriting the Day 5 schema baseline.

Chosen run model:

- One run represents one full-slate Tennis evaluation pass.
- The run scope is therefore `SLATE`, not per-match.
- Match-level execution bookkeeping is intentionally deferred to T-ED5. T-ED1 only adds the
  identity needed for run, config, policy, and lineage.

Run lifecycle:

```text
RUNNING
  -> PREDICTIONS_FINALIZED
  -> COMPLETED

RUNNING
  -> FAILED

RUNNING
  -> CANCELLED

COMPLETED
  -> SUPERSEDED
```

An empty slate is a valid `COMPLETED` run with zero resulting prediction rows.

### `prediction_runs`

Purpose:

- First-class identity for one coherent full-slate Tennis execution.
- The row anchors reruns, finalization time, and prediction grouping without relying on
  timestamp proximity.

Columns:

- `prediction_run_id BIGINT NOT NULL`
- `run_scope TEXT NOT NULL`
  Intended value set in T-ED1: `SLATE`
- `run_mode TEXT NOT NULL`
  Intended value set in T-ED1: `BACKTEST`, `PAPER`, `REPLAY`
- `run_status TEXT NOT NULL`
  Intended value set in T-ED1: `RUNNING`, `PREDICTIONS_FINALIZED`, `COMPLETED`, `FAILED`,
  `CANCELLED`, `SUPERSEDED`
- `as_of TIMESTAMPTZ NOT NULL`
- `started_at TIMESTAMPTZ NOT NULL`
- `prediction_finalized_at TIMESTAMPTZ NULL`
- `completed_at TIMESTAMPTZ NULL`
- `experiment_id TEXT NULL`
- `parent_prediction_run_id BIGINT NULL`
- `parent_relation_type TEXT NULL`
  Intended value set in T-ED1: `RETRY`, `SUPERSEDES`, `RECALCULATION`, `EXPERIMENT`
- `created_at TIMESTAMPTZ NOT NULL`

Primary key:

- `prediction_run_id`

Foreign keys:

- `parent_prediction_run_id -> prediction_runs.prediction_run_id`

Unique constraints:

- none beyond the primary key in T-ED1

Check constraints:

- `run_scope = 'SLATE'`
- `prediction_finalized_at IS NULL OR prediction_finalized_at >= as_of`
- `completed_at IS NULL OR completed_at >= started_at`
- `prediction_finalized_at IS NULL OR completed_at IS NULL OR completed_at >= prediction_finalized_at`
- `parent_prediction_run_id` and `parent_relation_type` are both null or both non-null

Indexes:

- index on `(run_status, started_at DESC)`
- index on `(parent_prediction_run_id)`

Lifecycle:

- Written by the future Tennis orchestrator / slate generator.
- One new row per initial run, retry, recalculation, or explicit experiment.
- A rerun always creates a new row. Existing runs are never rewritten into the new truth.

Immutability expectations:

- The run row is a lifecycle record, so status/timestamps may advance within the same row
  until terminal state.
- Post-terminal mutation guards and post-finalization prediction-write rejection are deferred
  to T-ED2.

### `decision_policy_versions`

Purpose:

- Immutable historical manager-policy storage.
- Protects Tennis C1-C4 and related thresholds from being treated as mutable global config.

Columns:

- `decision_policy_version_id BIGINT NOT NULL`
- `policy_key TEXT NOT NULL`
  Intended T-ED1 value for the canonical path: `TENNIS_MANAGER`
- `policy_version TEXT NOT NULL`
- `consensus_mode TEXT NOT NULL`
  Intended value set in T-ED1: `UNANIMOUS_ACTIVE_AGENTS`
- `allow_agent3_abstention BOOLEAN NOT NULL`
- `max_probability_spread NUMERIC(8, 6) NOT NULL`
- `min_active_agent_probability NUMERIC(8, 6) NOT NULL`
- `requires_data_sufficiency BOOLEAN NOT NULL`
- `min_recent_matches_with_serve_stats INTEGER NOT NULL`
- `odds_feed_max_staleness_minutes INTEGER NOT NULL`
- `match_data_max_staleness_hours INTEGER NOT NULL`
- `ratings_max_staleness_hours INTEGER NOT NULL`
- `fixture_feed_max_staleness_hours INTEGER NOT NULL`
- `atp_main_all_agents_min_edge_pct NUMERIC(8, 6) NOT NULL`
- `atp_main_agent3_abstain_min_edge_pct NUMERIC(8, 6) NOT NULL`
- `challenger_min_edge_pct NUMERIC(8, 6) NOT NULL`
- `default_devig_method TEXT NOT NULL`
  Intended value set in T-ED1: `POWER`, `SHIN`
- `sharp_reference_policy_code TEXT NOT NULL`
- `veto_policy_code TEXT NOT NULL`
- `publication_requires_min_acceptable_odds BOOLEAN NOT NULL`
- `publish_retractions_required BOOLEAN NOT NULL`
- `created_at TIMESTAMPTZ NOT NULL`

Primary key:

- `decision_policy_version_id`

Foreign keys:

- none in T-ED1

Unique constraints:

- `(policy_key, policy_version)`

Check constraints:

- `consensus_mode = 'UNANIMOUS_ACTIVE_AGENTS'`
- `allow_agent3_abstention = TRUE`
- `max_probability_spread >= 0 AND max_probability_spread <= 1`
- `min_active_agent_probability >= 0 AND min_active_agent_probability <= 1`
- integer threshold fields are `>= 0`
- edge thresholds are `>= 0`
- `default_devig_method IN ('POWER', 'SHIN')`

Indexes:

- unique index on `(policy_key, policy_version)`

Lifecycle:

- Written only when the canonical Tennis manager policy changes.
- A new version row is required for any change to consensus, spread, floor, freshness,
  threshold, veto-policy, or publication-rule behavior.

Immutability expectations:

- Immutable once a decision references it.
- Mechanical update/delete rejection is deferred to T-ED2.

Typed-vs-JSON rule:

- Core Tennis rules are typed columns because they are enforceable and must not drift.
- `sharp_reference_policy_code` and `veto_policy_code` remain versioned identifiers rather
  than uncontrolled JSON because their detailed runtime implementation belongs to later
  phases.

### `agent_config_versions`

Purpose:

- Immutable identity for the exact predictive-agent configuration that produced one stored
  prediction.
- Captures replay-critical configuration without prematurely hard-coding unfinished agent
  mathematics into the relational schema.

Columns:

- `agent_config_version_id BIGINT NOT NULL`
- `agent_code agent_code NOT NULL`
- `config_version TEXT NOT NULL`
- `model_version TEXT NOT NULL`
- `feature_version TEXT NOT NULL`
- `calibration_version TEXT NULL`
- `parameter_schema_version TEXT NOT NULL`
- `config_payload JSONB NOT NULL`
- `created_at TIMESTAMPTZ NOT NULL`

Primary key:

- `agent_config_version_id`

Foreign keys:

- none in T-ED1

Unique constraints:

- `(agent_code, config_version)`

Check constraints:

- `config_payload` must be a JSON object
- required text version fields must be non-empty

Indexes:

- unique index on `(agent_code, config_version)`
- index on `(agent_code, created_at DESC)`

Lifecycle:

- Written when a predictive agent gains a new calibration, parameter set, abstention
  threshold, or comparable replay-relevant configuration.
- A new version row is required for any agent-affecting change that should be reproducible in
  historical audit.

Immutability expectations:

- Immutable once a prediction references it.
- Mechanical update/delete rejection is deferred to T-ED2.

Why JSON is appropriate here:

- Agent-specific configuration differs materially across Elo, Markov, and Intransitivity.
- T-ED1 needs exact identity and replay, not a premature relational encoding of every future
  model parameter.
- The JSON payload is therefore controlled by `parameter_schema_version` and anchored by the
  immutable row identity.

### No `prediction_locks` table in T-ED1

T-ED1 does not need a separate `prediction_locks` table.

Reason:

- `prediction_runs.prediction_finalized_at` plus immutable prediction rows is sufficient to
  establish the logical prediction freeze boundary.
- This solves run identity and lineage without importing cryptographic hash-chain complexity
  that is not yet required by the canonical Tennis sources.

Deferred work:

- hard rejection of post-finalization prediction inserts under the same run
- mechanical immutability triggers
- tamper-evident hash chains

Those belong to T-ED2 or later.

## Existing table changes for T-ED1

### `predictions`

Changes:

- add `prediction_run_id BIGINT NOT NULL`
- add `agent_config_version_id BIGINT NOT NULL`
- replace the current logical identity anchor with a run-centric uniqueness rule:
  `(prediction_run_id, match_id, agent_code)`
- add a supporting composite key path so `agent_code` and `agent_config_version_id` can be
  validated together

Reason:

- a prediction must belong to one exact full-slate execution
- a rerun must be able to create a new prediction set without mutating prior rows
- historical replay needs exact agent-config identity beyond `model_version` and
  `feature_version`

Compatibility:

- keep existing `model_version`, `feature_version`, `experiment_id`, `walk_forward_fold`,
  `variant_id`, `as_of`, and `prediction_locked_at`
- existing row-level version fields remain useful audit columns even after the config FK is
  introduced
- the future write path should persist prediction rows only when the run's prediction set is
  finalized, so `predictions.prediction_locked_at` can equal
  `prediction_runs.prediction_finalized_at` without row updates

### `decisions`

Changes:

- add `prediction_run_id BIGINT NOT NULL`
- add `decision_policy_version_id BIGINT NOT NULL`
- extend decision identity so reruns are separated by explicit run identity rather than only
  by duplicated version strings and timestamps

Reason:

- the manager decision must reference the exact full-slate run it evaluated
- the decision must reference the exact historical policy version in force at the time

Compatibility:

- keep existing market, consensus, devig, edge, reason, and publication fields
- `decisions` remains the first-class internal truth for `BET`, `NO_BET`, and `VETOED`
- market evaluation run/config is deferred to later phases unless a real prerequisite emerges

### `decision_agents`

Changes:

- add `prediction_id BIGINT NOT NULL`
- keep `decision_id` + `agent_code` as the primary key
- add unique `(decision_id, prediction_id)`
- add an FK from `prediction_id` to `predictions.prediction_id`
- add a composite key path so `agent_code` must match the linked prediction's `agent_code`

Reason:

- duplicated `proposed_selection_side` and `proposed_probability` are not enough for exact
  lineage
- the manager must reference the actual prediction row it consumed

Compatibility:

- retain `proposed_selection_side` and `proposed_probability` as denormalized audit copies for
  convenient inspection
- exact same-match and same-run consistency cannot be enforced by a simple check constraint
  alone and must be guarded by FK design plus a small relational trigger or insert path in the
  future `0004` implementation

Enforcement split:

- foreign keys guarantee the referenced prediction exists
- composite key paths guarantee agent-code alignment
- same-run and same-match alignment must be enforced in T-ED1 implementation with a
  constraint trigger or equivalent write path because PostgreSQL `CHECK` constraints cannot
  look at other rows
- post-decision and post-finalization immutability remains T-ED2

## T-ED1 schema delta matrix

| Object | Current state | T-ED1 change | Reason | Enforcement | Migration risk |
| --- | --- | --- | --- | --- | --- |
| `predictions` | immutable agent rows with version and point-in-time fields, but no explicit run or config FK | add `prediction_run_id` and `agent_config_version_id`; make uniqueness run-centric | exact replay and rerun-safe identity | FK, unique index, later write-path invariant on finalized insert timing | medium: existing unique index must be replaced carefully |
| `decisions` | manager rows contain duplicated consensus and market facts, but no explicit run or policy FK | add `prediction_run_id` and `decision_policy_version_id` | exact run lineage and historical policy recovery | FK, updated unique/index strategy, future runtime invariant checks | medium: identity/index changes must preserve current decision semantics |
| `decision_agents` | per-agent decision participation with duplicated probability/side, but no prediction FK | add `prediction_id`, uniqueness on `(decision_id, prediction_id)`, and agent-alignment key path | exact prediction-to-decision lineage | FK plus future constraint trigger for same-match and same-run alignment | medium: cross-row enforcement cannot be done by simple check alone |
| `prediction_runs` | does not exist | new table | full-slate execution identity, reruns, finalization time | PK, self-FK, status/scope checks, indexes | low |
| `decision_policy_versions` | does not exist | new table | immutable Tennis manager-policy history | PK, unique `(policy_key, policy_version)`, typed checks | low |
| `agent_config_versions` | does not exist | new table | immutable replay-critical agent configuration identity | PK, unique `(agent_code, config_version)`, JSON-object check | low |
| `prediction_locks` | does not exist | no table added in T-ED1 | avoid unnecessary lock-hash complexity in `0004` | documented non-addition; use `prediction_runs.prediction_finalized_at` | low |

## Planned `0004` validation

Migration tests:

- fresh DB path: `0001 -> 0002 -> 0003 -> 0004`
- existing DB path: `0001 -> 0002 -> 0003`, then upgrade to `0004`
- manifest ordering and snapshot determinism checks updated for the new migration

Constraint tests:

- valid `prediction_runs` lifecycle rows insert cleanly
- invalid run scope or illegal timestamp ordering fails
- invalid policy values that weaken Tennis C1-C4 fail
- invalid `agent_config_versions.config_payload` shape fails

Lineage tests:

- each prediction belongs to the intended run
- each decision references the intended run and policy version
- each `decision_agents` row references the exact prediction used
- wrong-agent linkage fails
- wrong-match or wrong-run linkage fails

Abstention tests:

- Agent 3 abstention remains representable on `predictions`
- Agent 3 abstention can still participate safely in decision lineage

`NO_BET` tests:

- predictions remain queryable when the decision is `NO_BET`
- vetoed or unpublished outcomes do not delete or overwrite prediction truth

Point-in-time tests:

- historical replay can recover run, predictions, configs, policy version, and the correct
  `as_of`
- decision `as_of` remains separate from prediction-run `as_of`

PostgreSQL integration tests:

- extend the disposable PostgreSQL 16 lifecycle suite rather than creating a doc-only fake
  test path
- add relational tests for run insertion, policy references, and prediction-to-decision
  lineage

## Phase boundary

Included in future T-ED1 implementation:

- run identity
- policy/version identity
- agent-config identity
- prediction-to-decision lineage
- logical prediction finalization timestamp

Deferred to T-ED2:

- reject update/delete on lineage tables
- reject post-finalization writes mechanically
- other immutability triggers

Deferred to T-ED3:

- append-only settlement observation history
- append-only CLV observation history

Deferred to T-ED4:

- odds lifecycle expansion
- publication-state expansion
- operator-facing publication/audit separation details beyond current decision storage

Deferred to T-ED5:

- full-slate execution runtime/storage contract beyond the run identity itself
- match-level execution bookkeeping

Deferred to predictive-agent phases:

- Agent 1 implementation
- Agent 2 implementation
- Agent 3 implementation
- manager runtime implementation
- veto runtime implementation
