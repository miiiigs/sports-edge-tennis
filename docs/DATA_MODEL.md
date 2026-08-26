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
- `0004` does not add a match-run table.
- The run is slate-scoped by contract, so T-ED1 omits `run_scope` rather than pretending
  multiple scopes already exist.
- `run_mode` is also omitted because it is not required for the T-ED1 lineage contract.
- Match-level execution bookkeeping is intentionally deferred to T-ED5. T-ED1 only adds the
  identity needed for run, config, policy, and lineage.
- Execution outcome, prediction finalization, rerun origin, and any future supersession
  semantics remain separate concepts.

Run lifecycle:

```text
RUNNING
  -> COMPLETED

RUNNING
  -> FAILED

RUNNING
  -> CANCELLED
```

`prediction_finalized_at` records when the prediction set became logically closed. It is not
an execution status.

An empty slate is a valid `COMPLETED` run with zero resulting prediction rows.

Run origin:

- Optional parent linkage records why a child run exists.
- T-ED1 only documents `RETRY` and `RECALCULATION`.
- Parent relation semantics describe origin only, not downstream supersession authority.
- If a later phase needs authoritative supersession between completed runs, it must be added
  separately in a forward migration rather than overloaded into `run_status` or
  `parent_relation_type`.

### `prediction_runs`

Purpose:

- First-class identity for one coherent full-slate Tennis execution.
- The row anchors reruns, finalization time, and prediction grouping without relying on
  timestamp proximity.

Columns:

- `prediction_run_id BIGINT NOT NULL`
- `run_status TEXT NOT NULL`
  Intended value set in T-ED1: `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`
- `as_of TIMESTAMPTZ NOT NULL`
- `started_at TIMESTAMPTZ NOT NULL`
- `prediction_finalized_at TIMESTAMPTZ NULL`
- `ended_at TIMESTAMPTZ NULL`
- `experiment_id TEXT NULL`
- `parent_prediction_run_id BIGINT NULL`
- `parent_relation_type TEXT NULL`
  Intended value set in T-ED1: `RETRY`, `RECALCULATION`
- `created_at TIMESTAMPTZ NOT NULL`

Primary key:

- `prediction_run_id`

Foreign keys:

- `parent_prediction_run_id -> prediction_runs.prediction_run_id`

Unique constraints:

- none beyond the primary key in T-ED1, except any referenced composite unique keys needed
  to support foreign-key enforcement such as `(prediction_run_id, as_of)`

Check constraints:

- `run_status IN ('RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED')`
- `prediction_finalized_at IS NULL OR prediction_finalized_at >= started_at`
- `prediction_finalized_at IS NULL OR prediction_finalized_at >= as_of`
- `ended_at IS NULL OR ended_at >= started_at`
- `prediction_finalized_at IS NULL OR ended_at IS NULL OR ended_at >= prediction_finalized_at`
- `run_status = 'RUNNING'` implies `ended_at IS NULL`
- `run_status IN ('COMPLETED', 'FAILED', 'CANCELLED')` implies `ended_at IS NOT NULL`
- `parent_prediction_run_id` and `parent_relation_type` are both null or both non-null
- `parent_relation_type IS NULL OR parent_relation_type IN ('RETRY', 'RECALCULATION')`
- `parent_prediction_run_id IS NULL OR parent_prediction_run_id <> prediction_run_id`

Indexes:

- index on `(run_status, started_at DESC)`
- index on `(parent_prediction_run_id)`

Lifecycle:

- Written by the future Tennis orchestrator / slate generator.
- One new row per initial run, retry, or recalculation.
- A rerun always creates a new row. Existing runs are never rewritten into the new truth.

Immutability expectations:

- The run row is a lifecycle record, so status/timestamps may advance within the same row
  until terminal state.
- Post-terminal mutation guards and post-finalization prediction-write rejection are deferred
  to T-ED2.

### Legacy-row migration precondition

T-ED1 must fail closed for already-populated lineage tables.

Before migration `0004_prediction_runs_and_policy_versions` adds required lineage columns,
the migration must verify that all of the following tables contain zero rows:

- `predictions`
- `decisions`
- `decision_agents`

If any of those tables are non-empty, `0004` must abort with a clear PostgreSQL error that
exact run, policy, and config lineage cannot be reconstructed safely from current data.

Forbidden shortcuts:

- synthesizing historical runs
- inventing policy versions
- inferring lineage from `as_of`, `prediction_locked_at`, or timestamp proximity
- creating bootstrap/default configs to backfill legacy rows silently

A populated environment requires a separately reviewed archival or deterministic backfill
plan before `0004` can run.

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
- Acts as the authoritative replay/configuration identity referenced by predictions.

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
- referenced unique key equivalent to `(agent_config_version_id, agent_code, model_version,
  feature_version)`

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
- `calibration_version` may be null when a predictive agent does not use a calibration
  artifact.

Immutability expectations:

- Immutable once a prediction references it.
- Mechanical update/delete rejection is deferred to T-ED2.

Why JSON is appropriate here:

- Agent-specific configuration differs materially across Elo, Markov, and Intransitivity.
- T-ED1 needs exact identity and replay, not a premature relational encoding of every future
  model parameter.
- `config_payload` must be a JSON object containing replay-relevant parameters not promoted
  into shared typed columns.
- `parameter_schema_version` defines how `config_payload` is interpreted.
- Any replay-relevant payload change requires a new immutable `agent_config_versions` row.

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
- add a composite FK equivalent to `(prediction_run_id, as_of) ->
  prediction_runs(prediction_run_id, as_of)` so all predictions in one run share the run's
  common blind cutoff
- add a composite FK equivalent to `(agent_config_version_id, agent_code, model_version,
  feature_version) -> agent_config_versions(...)`
- expose a referenced unique key equivalent to `(prediction_id, prediction_run_id, match_id,
  agent_code)` for downstream `decision_agents` lineage

Reason:

- a prediction must belong to one exact full-slate execution
- a rerun must be able to create a new prediction set without mutating prior rows
- historical replay needs exact agent-config identity beyond `model_version` and
  `feature_version`
- one coherent run may not carry multiple variants for the same agent and match

Compatibility:

- keep existing `model_version`, `feature_version`, `experiment_id`, `walk_forward_fold`,
  `variant_id`, `as_of`, and `prediction_locked_at`
- `model_version` and `feature_version` remain useful immutable audit columns, but
  `agent_config_versions` is authoritative for replay identity
- `experiment_id`, `walk_forward_fold`, and `variant_id` remain descriptive audit metadata;
  they do not authorize multiple same-agent same-match variants inside one run
- any experiment variant, recalculation, changed `as_of`, or changed predictive
  configuration requires a new `prediction_run_id`
- the future write path should persist one finalized prediction set per run, so
  `predictions.prediction_locked_at = prediction_runs.prediction_finalized_at`

### `decisions`

Changes:

- add `prediction_run_id BIGINT NOT NULL`
- add `decision_policy_version_id BIGINT NOT NULL`
- expose a referenced unique key equivalent to `(decision_id, prediction_run_id, match_id)`
  for downstream `decision_agents` lineage
- keep `decision_id` as the immutable decision identity and do not add a unique rule that
  collapses all evaluations for one match and run into a single row

Reason:

- the manager decision must reference the exact full-slate run it evaluated
- the decision must reference the exact historical policy version in force at the time
- downstream market/value/veto evaluation may legitimately run again later against the same
  sporting prediction set

Compatibility:

- keep existing market, consensus, devig, edge, reason, and publication fields
- `decisions` remains the first-class internal truth for `BET`, `NO_BET`, and `VETOED`
- the same `match_id` and `prediction_run_id` may therefore produce multiple immutable
  decisions with different `decision_id`, `as_of`, market facts, or downstream outcomes
- a new prediction run is required when sporting truth changes; a new decision row is
  sufficient when only market/value/veto evaluation changes

### `decision_agents`

Changes:

- add `prediction_run_id BIGINT NOT NULL`
- add `match_id BIGINT NOT NULL`
- add `prediction_id BIGINT NULL`
- keep `decision_id` + `agent_code` as the primary key
- add a composite FK equivalent to `(decision_id, prediction_run_id, match_id) ->
  decisions(decision_id, prediction_run_id, match_id)`
- add a composite FK equivalent to `(prediction_id, prediction_run_id, match_id, agent_code)
  -> predictions(prediction_id, prediction_run_id, match_id, agent_code)` when
  `prediction_id` is present

Reason:

- duplicated `proposed_selection_side` and `proposed_probability` are not enough for exact
  lineage
- the manager must reference the actual prediction row it consumed
- the intentional denormalization lets PostgreSQL prove same-run, same-match, and same-agent
  invariants without trusting application code

Compatibility:

- retain `proposed_selection_side` and `proposed_probability` as denormalized audit copies for
  convenient inspection
- preserve the current `agent_participation_status` domain and lock the following semantics:
  - `ACTIVE` requires `prediction_id IS NOT NULL`, `proposed_selection_side IS NOT NULL`, and
    `proposed_probability IS NOT NULL`
  - `ABSTAINED` requires `prediction_id IS NOT NULL`, `proposed_selection_side IS NULL`, and
    `proposed_probability IS NULL`
  - `INACTIVE` requires `prediction_id IS NULL`, `proposed_selection_side IS NULL`, and
    `proposed_probability IS NULL`

Enforcement split:

- composite foreign keys guarantee the decision link, prediction link, run alignment, match
  alignment, and agent alignment
- local `CHECK` constraints enforce the nullability rules for `ACTIVE`, `ABSTAINED`, and
  `INACTIVE`
- a narrow T-ED1 lineage constraint trigger enforces cross-row status and audit-copy
  alignment that plain `CHECK` constraints cannot express:
  - `ACTIVE` must link to a `PREDICTED` prediction and audit copies must equal the linked
    prediction values
  - `ABSTAINED` must link to an `ABSTAINED` prediction and audit copies must remain null
  - `INACTIVE` must not link to any prediction and audit copies must remain null
- this constraint trigger is part of lineage integrity, not mechanical immutability
- post-decision and post-finalization immutability remains T-ED2

## T-ED1 schema delta matrix

| Object | Current state | T-ED1 change | Reason | Enforcement | Migration risk |
| --- | --- | --- | --- | --- | --- |
| `predictions` | immutable agent rows with version and point-in-time fields, but no explicit run or config FK | add `prediction_run_id` and `agent_config_version_id`; make uniqueness run-centric and align prediction `as_of` with run `as_of` | exact replay and rerun-safe identity | composite FK, unique index, and audit-copy alignment to authoritative config rows | high: legacy populated environments must fail fast before adding required lineage columns |
| `decisions` | manager rows contain duplicated consensus and market facts, but no explicit run or policy FK | add `prediction_run_id` and `decision_policy_version_id`; expose composite decision lineage key while preserving multiple decisions per match and run | exact run lineage and historical policy recovery without collapsing legitimate market reevaluations | FK, composite referenced key, future runtime invariant checks | high: legacy populated environments must fail fast before adding required lineage columns |
| `decision_agents` | per-agent decision participation with duplicated probability/side, but no prediction FK | add `prediction_run_id`, `match_id`, and nullable `prediction_id`; enforce composite decision/prediction lineage and participation semantics | exact prediction-to-decision lineage | composite FKs, local checks, and a narrow lineage constraint trigger | high: legacy populated environments must fail fast before adding required lineage columns |
| `prediction_runs` | does not exist | new table | full-slate execution identity, reruns, and finalization time | PK, self-FK, status/timestamp checks, indexes | low |
| `decision_policy_versions` | does not exist | new table | immutable Tennis manager-policy history | PK, unique `(policy_key, policy_version)`, typed checks | low |
| `agent_config_versions` | does not exist | new table | immutable replay-critical agent configuration identity | PK, unique `(agent_code, config_version)`, composite referenced key, JSON-object check | low |
| `prediction_locks` | does not exist | no table added in T-ED1 | avoid unnecessary lock-hash complexity in `0004` | documented non-addition; use `prediction_runs.prediction_finalized_at` | low |

## Planned `0004` validation

Migration tests:

- fresh DB path: `0001 -> 0002 -> 0003 -> 0004`
- existing DB path: `0001 -> 0002 -> 0003`, then upgrade to `0004`
- non-empty `predictions`, `decisions`, or `decision_agents` abort `0004` clearly and leave
  legacy rows untouched
- manifest ordering and snapshot determinism checks updated for the new migration

Run lifecycle tests:

- valid `prediction_runs` lifecycle rows insert cleanly
- valid `RUNNING`, `COMPLETED`, `FAILED`, and `CANCELLED` rows insert cleanly
- empty-slate `COMPLETED` run is allowed
- invalid terminal timestamp ordering fails
- `prediction_finalized_at` remains independent from execution status
- `SUPERSEDED` is rejected as an execution status

Run origin tests:

- valid `RETRY` parent linkage inserts cleanly
- valid `RECALCULATION` parent linkage inserts cleanly
- invalid parent relation values fail
- self-parent linkage fails
- supersession is not encoded through execution status or parent relation type

Policy/config tests:

- invalid policy values that weaken Tennis C1-C4 fail
- invalid `agent_config_versions.config_payload` shape fails
- predictions reject config links whose `agent_code`, `model_version`, or `feature_version`
  disagree with the referenced config row

Prediction identity tests:

- each prediction belongs to the intended run
- prediction/run `as_of` mismatch fails
- duplicate `(prediction_run_id, match_id, agent_code)` fails
- another variant of the same agent and match requires a separate run

Decision identity tests:

- each decision references the intended run and policy version
- the same `match_id` and `prediction_run_id` may produce multiple immutable `decision_id`
  rows

Decision-agent lineage tests:

- each `decision_agents` row references the exact prediction used
- wrong-agent linkage fails
- wrong-match or wrong-run linkage fails
- `ACTIVE` linked to an abstained prediction fails
- `ABSTAINED` linked to a predicted result fails
- `ACTIVE` audit-copy mismatch fails
- `ABSTAINED` non-null audit values fail

Abstention tests:

- Agent 3 abstention remains representable on `predictions`
- Agent 3 abstention can still participate safely in decision lineage
- `ABSTAINED` decision-agent rows remain traceable to the exact abstained prediction row

Inactive tests:

- `INACTIVE` may exist without a `prediction_id`
- `INACTIVE` requires null audit proposal values

`NO_BET` tests:

- predictions remain queryable when the decision is `NO_BET`
- multiple agent predictions remain linked and queryable when the decision is `NO_BET`
- vetoed or unpublished outcomes do not delete or overwrite prediction truth

Point-in-time tests:

- historical replay can recover run, predictions, configs, policy version, and the correct
  `as_of`
- decision `as_of` remains separate from prediction-run `as_of`

Finalization tests:

- `predictions.prediction_locked_at = prediction_runs.prediction_finalized_at`
- the chosen lock/finalization relationship validates cleanly for finalized runs

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
- relational correctness enforcement through foreign keys, composite foreign keys, uniqueness,
  local checks, and the lineage constraint trigger used for cross-row audit-copy alignment

Deferred to T-ED2:

- reject update/delete on lineage tables
- reject post-finalization writes mechanically
- reject mutation of immutable policy/config rows mechanically
- other append-only immutability triggers

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
