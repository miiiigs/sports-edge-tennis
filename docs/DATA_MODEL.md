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

## T-ED1 schema design

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

## T-ED2 schema implementation

Recommended migration name:

```text
0005_append_only_enforcement_and_lock_guards
```

T-ED2 does not redesign the approved `0004` lineage model. It adds the PostgreSQL
mechanisms that make the historical contracts mechanically enforceable on a populated valid
`0004` database.

### Core objective

T-ED1 established relational truth. T-ED2 establishes mutation truth:

- which rows are permanently insert-only
- which rows have a limited lifecycle update window
- which deletes must always fail
- when prediction writes must stop mechanically
- how finalization stays atomic and race-safe under concurrent transactions

### Current `0004` finalization contradiction

Current `0004` enforces:

```text
(prediction_run_id, prediction_locked_at)
    ->
(prediction_run_id, prediction_finalized_at)
```

through `fk_predictions_prediction_run_finalization`.

In `0004`, that FK is non-deferrable and therefore checked immediately. As written, a
prediction insert can succeed only if the parent run row already carries the same
`prediction_finalized_at` value.

A naive T-ED2 guard such as:

```text
if prediction_runs.prediction_finalized_at is not null then reject prediction insert
```

would therefore make valid prediction inserts impossible, because the same committed-state
timestamp that satisfies the FK would also look "already finalized" to the guard.

### Chosen T-ED2 finalization protocol

T-ED2 keeps the committed-state equality rule from `0004`, and `0005` implements the write
protocol as follows:

- `fk_predictions_prediction_run_finalization` becomes `DEFERRABLE INITIALLY IMMEDIATE`
  rather than immediate-only.
- The explicit prediction-finalization transaction must issue:

```text
SET CONSTRAINTS fk_predictions_prediction_run_finalization DEFERRED
```

  before inserting the prediction set.
- That same transaction must lock the target `prediction_runs` row with
  `SELECT ... FOR UPDATE` before any prediction insert for that run.
- Prediction rows for a run may be persisted only inside that explicit finalization
  transaction. There is no valid standalone pre-finalization prediction persistence path
  under T-ED2.
- After acquiring the run-row lock, the transaction must verify both
  `run_status = 'RUNNING'` and `prediction_finalized_at IS NULL`.
- The transaction may then bulk insert the full prediction set with a common intended
  `prediction_locked_at = T`.
- The transaction must then update the same `prediction_runs` row to set
  `prediction_finalized_at = T`.
- Commit succeeds only if every inserted prediction now matches the finalized run timestamp.

The required transaction order is:

```text
1. BEGIN
2. SET CONSTRAINTS fk_predictions_prediction_run_finalization DEFERRED
3. SELECT prediction_runs ... FOR UPDATE
4. verify run_status = 'RUNNING'
5. verify prediction_finalized_at IS NULL
6. bulk insert complete prediction set with prediction_locked_at = T
7. UPDATE prediction_runs
      SET prediction_finalized_at = T
      WHERE prediction_run_id = ...
8. optionally perform the terminal RUNNING -> COMPLETED transition in the same transaction
9. COMMIT
```

This design keeps the committed row state identical to the approved T-ED1 contract while
making it possible for T-ED2 to reject late prediction inserts after finalization commits.

### Why the committed state is safe

- A `BEFORE ROW` prediction lock guard will lock the parent `prediction_runs` row with
  `SELECT ... FOR UPDATE`.
- If the locked row is not `RUNNING`, the insert is rejected immediately.
- If the locked row already has `prediction_finalized_at IS NOT NULL`, the insert is
  rejected immediately.
- Only a `RUNNING` and unfinalized run row is eligible to continue into the authorized
  finalization transaction.
- Because the insert trigger and the finalization update lock the same parent run row,
  concurrent finalization and insert attempts serialize on that row.
- If the finalization transaction acquires the row lock first, any later insert waits and
  then sees the committed finalized timestamp and fails.
- If an unauthorized direct prediction insert acquires the row lock first while the row is
  still `RUNNING` and unfinalized, it is still not a valid persistence path; unless that
  same transaction explicitly defers the FK and establishes the parent finalization key, the
  initially-immediate FK prevents it from committing.
- If a competing authorized finalization transaction acquires the row lock first, it becomes
  the only transaction allowed to persist the coherent prediction set for that run.
- No committed transaction can therefore insert a prediction after the run has become
  mechanically finalized, and no isolated prediction insert is treated as a valid competing
  finalizer.

An empty completed slate remains valid. In that case the finalization transaction may lock
the run row and set `prediction_finalized_at` without inserting any prediction rows.

### T-ED2 mutation classification

| Table | Classification | INSERT | UPDATE | DELETE | Lock/finalization rule | T-ED2 scope | Reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `prediction_runs` | `CONTROLLED_LIFECYCLE_TED2` | allowed | only approved lifecycle fields and transitions | rejected | parent run row is the concurrency lock anchor for prediction finalization | yes | run history is durable, but status/finalization/ended timestamps advance within one row |
| `predictions` | `STRICT_APPEND_ONLY_TED2` | allowed only before parent run finalization commits and only through the chosen finalization protocol | rejected | rejected | no insert once parent `prediction_finalized_at` is committed; committed rows stay immutable | yes | prediction truth must never be rewritten |
| `decision_policy_versions` | `STRICT_APPEND_ONLY_TED2` | allowed | rejected | rejected | none | yes | any policy change must create a new version row |
| `agent_config_versions` | `STRICT_APPEND_ONLY_TED2` | allowed | rejected | rejected | none | yes | replay identity must never be rewritten in place |
| `decisions` | `CONTROLLED_LIFECYCLE_TED2` | allowed | all fields immutable except `published_at` may transition `NULL -> timestamptz` once | rejected | no decision lock added in T-ED2 | yes | decision rows are historical truth, but the existing `published_at` field needs one narrow bridge until T-ED4 |
| `decision_agents` | `STRICT_APPEND_ONLY_TED2` | allowed | rejected | rejected | no late-insert closure in T-ED2 because no decision-finalization contract exists yet | yes | inserted lineage rows must never be rewritten; timing closure is deferred explicitly |
| `rating_snapshots` | `STRICT_APPEND_ONLY_TED2` | allowed | rejected | rejected | none | yes | rating snapshots are already documented as immutable point-in-time states |
| `odds_snapshots` | `STRICT_APPEND_ONLY_TED2` | allowed | rejected | rejected | none | yes | odds corrections are represented as new snapshots, not overwrites |
| `match_versions` | `STRICT_APPEND_ONLY_TED2` | allowed | rejected | rejected | none | yes | versioned match truth is append-only by design |
| `match_source_ids` | `STRICT_APPEND_ONLY_TED2` | allowed | rejected | rejected | none | yes | source-to-stable-match mapping is historical provenance rather than mutable fixture state |
| `match_version_sources` | `STRICT_APPEND_ONLY_TED2` | allowed | rejected | rejected | none | yes | source evidence attached to a match version is inherently append-oriented provenance |
| `match_result_observations` | `STRICT_APPEND_ONLY_TED2` | allowed | rejected | rejected | none | yes | results are observation history, not mutable fixture state |
| `match_stat_observations` | `STRICT_APPEND_ONLY_TED2` | allowed | rejected | rejected | none | yes | stats are historical observations and already modeled append-only |
| `decision_settlements` | `DEFER_TO_LATER_PHASE` | unchanged in `0005` | unchanged in `0005` | unchanged in `0005` | none in T-ED2 | no | T-ED3 owns the append-only settlement redesign |
| `decision_clv` | `DEFER_TO_LATER_PHASE` | unchanged in `0005` | unchanged in `0005` | unchanged in `0005` | none in T-ED2 | no | T-ED3 owns the append-only CLV redesign |
| `ingest_runs` | `CONTROLLED_LIFECYCLE_TED2` | allowed | exactly one terminal `RUNNING -> COMPLETED|PARTIAL|FAILED|CANCELLED` update writes finished_at, counters, and error_summary; terminal rows freeze | rejected | no prediction lock semantics | yes | current runtime creates a `RUNNING` row and finishes it with one terminal update |
| `players` | `MUTABLE_REFERENCE_DATA` | allowed | allowed | unchanged in T-ED2 | none | no | canonical reference data may still need correction |
| `player_source_ids` | `STRICT_APPEND_ONLY_TED2` | allowed | rejected | rejected | none | yes | source-to-player identity mapping is provenance, not mutable master state |
| `player_aliases` | `MUTABLE_REFERENCE_DATA` | allowed | allowed | unchanged in T-ED2 | none | no | repository write path legitimately extends `last_observed_at` |
| `identity_resolution_events` | `STRICT_APPEND_ONLY_TED2` | allowed | rejected | rejected | none | yes | resolution events are historical audit rows |
| `identity_resolution_candidates` | `STRICT_APPEND_ONLY_TED2` | allowed | rejected | rejected | none | yes | candidate evidence attached to an identity-resolution event should not be rewritten in place |
| `tournaments` | `MUTABLE_REFERENCE_DATA` | allowed | allowed | unchanged in T-ED2 | none | no | canonical tournament reference data may still be corrected |
| `tournament_source_ids` | `STRICT_APPEND_ONLY_TED2` | allowed | rejected | rejected | none | yes | source-to-canonical tournament mapping is historical identity provenance |
| `tournament_editions` | `MUTABLE_REFERENCE_DATA` | allowed | allowed | unchanged in T-ED2 | none | no | current repository legitimately fills missing surface/date metadata in place |
| `matches` | `STRICT_APPEND_ONLY_TED2` | allowed | rejected | rejected | none | yes | stable match identity rows should not be rewritten in place |
| `data_health_checks` | `STRICT_APPEND_ONLY_TED2` | allowed | rejected | rejected | none | yes | health checks are snapshot history, not mutable status rows |

### `prediction_runs` lifecycle contract

Fields that become immutable immediately after row creation:

- `prediction_run_id`
- `as_of`
- `started_at`
- `experiment_id`
- `parent_prediction_run_id`
- `parent_relation_type`
- `created_at`

Fields that remain lifecycle-mutable under T-ED2:

- `run_status`
- `prediction_finalized_at`
- `ended_at`

Allowed transition rules:

- `RUNNING -> RUNNING` is allowed only when `prediction_finalized_at` changes from `NULL` to
  a timestamp, `ended_at` remains `NULL`, `run_status` remains `RUNNING`, and every
  immutable identity field remains unchanged.
- `RUNNING -> COMPLETED` is allowed exactly once, requires `ended_at` to be set in the same
  row version, and requires `prediction_finalized_at` either to already be non-null and
  unchanged or to move from `NULL` to a timestamp in the same terminal update.
- `RUNNING -> FAILED` is allowed exactly once, requires `ended_at` to be set, and may leave
  `prediction_finalized_at` either `NULL`, already-set and unchanged, or set from `NULL` to
  a timestamp in the same terminal update if finalization and failure are recorded
  atomically.
- `RUNNING -> CANCELLED` is allowed exactly once, requires `ended_at` to be set, and may
  leave `prediction_finalized_at` either `NULL`, already-set and unchanged, or set from
  `NULL` to a timestamp in the same terminal update if finalization and cancellation are
  recorded atomically.

Forbidden transition rules:

- terminal -> `RUNNING`
- `COMPLETED -> FAILED`
- `COMPLETED -> CANCELLED`
- `FAILED -> COMPLETED`
- `FAILED -> CANCELLED`
- `CANCELLED -> COMPLETED`
- `CANCELLED -> FAILED`
- clearing or changing `prediction_finalized_at`
- clearing or changing `ended_at`
- changing any immutable field after insert

Delete rule:

- no `prediction_runs` row may be deleted in T-ED2, including failed and cancelled runs
- terminal rows with `prediction_finalized_at IS NULL` remain permanently closed to
  prediction inserts because `run_status <> 'RUNNING'`

### `predictions`

Chosen contract:

- `INSERT` allowed only as part of the explicit prediction-set finalization transaction
- there is no valid standalone pre-finalization prediction persistence path
- the `BEFORE INSERT` guard must lock the parent run row and require
  `run_status = 'RUNNING'` and `prediction_finalized_at IS NULL`
- `UPDATE` always rejected
- `DELETE` always rejected
- `INSERT` against any terminal run always rejected, even if `prediction_finalized_at` is
  still `NULL`
- `INSERT` after committed finalization always rejected

Direct SQL implication:

- a standalone prediction insert that does not explicitly defer
  `fk_predictions_prediction_run_finalization` and establish the matching parent
  `(prediction_run_id, prediction_finalized_at)` key in the same transaction cannot commit
- the implemented `0005` migration does not treat an isolated prediction insert as a valid
  competing finalizer

The existing T-ED1 lineage trigger remains. It is still needed to defend direct SQL writes
and to preserve the distinction between relational lineage correctness and immutability
protection.

### `ingest_runs`

Chosen contract:

- `INSERT` allowed
- `DELETE` always rejected
- `RUNNING -> RUNNING` state-changing updates rejected because the current repository does
  not incrementally update counters or `error_summary` before terminalization
- terminal rows freeze completely after their first terminal update

Immutable fields after row creation:

- `ingest_run_id`
- `source_system`
- `source_dataset`
- `started_at`
- `source_version`
- `source_file_name`
- `source_file_hash`
- `created_at`

Lifecycle fields that may be written on the terminal update:

- `finished_at`
- `status`
- `rows_seen`
- `rows_inserted`
- `rows_updated`
- `rows_rejected`
- `rows_unmatched`
- `error_summary`

Allowed transition rules:

- `RUNNING -> COMPLETED` exactly once, with `finished_at` moving from `NULL` to a
  timestamp and final counters plus `error_summary` written in the same update
- `RUNNING -> PARTIAL` exactly once, with `finished_at` moving from `NULL` to a timestamp
  and final counters plus `error_summary` written in the same update
- `RUNNING -> FAILED` exactly once, with `finished_at` moving from `NULL` to a timestamp
  and final counters plus `error_summary` written in the same update
- `RUNNING -> CANCELLED` exactly once, with `finished_at` moving from `NULL` to a
  timestamp and final counters plus `error_summary` written in the same update

Forbidden transition rules:

- any terminal status -> any other status
- any terminal rewrite of `finished_at`, counters, or `error_summary`
- any update to immutable source-identity fields after insert
- any state-changing `RUNNING -> RUNNING` update

Current repository compatibility:

- `start_ingest_run()` inserts the initial `RUNNING` row
- `finish_ingest_run()` performs one terminal update that sets `finished_at`, `status`,
  counters, and `error_summary`
- no current runtime path incrementally mutates `ingest_runs` while status remains
  `RUNNING`

### `decision_policy_versions`

Chosen contract:

- `INSERT` allowed
- `UPDATE` rejected from creation onward
- `DELETE` rejected from creation onward

No editable draft state is introduced. Any meaningful policy change must create a new
version row.

### `agent_config_versions`

Chosen contract:

- `INSERT` allowed
- `UPDATE` rejected from creation onward
- `DELETE` rejected from creation onward

No editable draft state is introduced. Any replay-relevant configuration change must create
a new version row.

### `decisions`

Chosen contract:

- `INSERT` allowed
- `UPDATE` rejected for every field except `published_at`
- `DELETE` rejected
- `published_at` may transition from `NULL` to a timestamp exactly once
- `published_at` may not be cleared or rewritten

This keeps the current schema coherent without inventing a full publication lifecycle.
T-ED2 does not add new publication states, retraction rules, or operator-facing publication
ledgers. Those remain T-ED4 work.

Multiple decisions for the same `match_id` and `prediction_run_id` remain allowed. T-ED2
does not change that T-ED1 contract.

### `decision_agents`

Chosen contract:

- `INSERT` allowed
- `UPDATE` rejected
- `DELETE` rejected

Late-insert closure is deferred explicitly. T-ED2 does not add a `decision_finalized_at`
field or another hidden completion flag merely to make `decision_agents` timing-closed.

Historical consequence:

- T-ED2 prevents rewriting or deleting an existing participation row.
- T-ED2 does not yet prevent a new aligned participation row from being inserted later for a
  previously incomplete decision if the primary key still allows it.
- Publication cannot serve as the universal closure boundary because `NO_BET` and `VETOED`
  decisions may remain internal and unpublished.
- That remaining closure problem needs a future explicit decision-lifecycle contract rather
  than an ad hoc hidden guard. Until that future decision-lifecycle or T-ED4-adjacent
  design exists, downstream features must not assume decision-agent membership is
  timing-closed.

The T-ED1 lineage trigger remains in place. T-ED2 immutability triggers add a separate
historical-mutation concern rather than replacing lineage validation.

### Existing historical tables included in T-ED2

Included for mechanical append-only enforcement in `0005`:

- `rating_snapshots`
- `odds_snapshots`
- `match_versions`
- `match_source_ids`
- `match_version_sources`
- `match_result_observations`
- `match_stat_observations`
- `player_source_ids`
- `identity_resolution_events`
- `identity_resolution_candidates`
- `tournament_source_ids`
- `matches`
- `data_health_checks`

Reason:

- each is already documented as historical state or provenance
- current repository write paths insert new rows rather than updating those rows in place
- later corrections already have append-only representations through new versions,
  observations, or snapshots
- after these additions, every current `0004` schema table is explicitly classified in the
  T-ED2 mutation matrix

### Deferred tables

Deferred from T-ED2:

- `decision_settlements`
- `decision_clv`

Reason:

- the current one-row-per-decision placeholders belong to the future T-ED3 settlement/CLV
  architecture
- mechanically freezing those placeholder tables in `0005` would constrain the later phase
  before its append-only replacement model is designed

### `0005` implemented forward shape

Implemented `0005` contents:

- alter `fk_predictions_prediction_run_finalization` to `DEFERRABLE INITIALLY IMMEDIATE`
- add shared append-only blocker function(s) for tables with simple reject-all
  `UPDATE`/`DELETE` behavior
- add table-specific lifecycle guard functions for:
  - `prediction_runs`
  - `ingest_runs`
  - `decisions`
- prediction insert lock checking against `prediction_runs`
- lock prediction persistence to the explicit finalization transaction and reject direct
  standalone inserts as unsupported persistence attempts
- add trigger definitions only

Implemented non-changes:

- no new tables
- no new columns
- no new indexes
- no new bypass flags
- manifest advances to include `0005`
- schema snapshot regenerates to include `0005`

### Trigger design

Implemented shared pattern:

- ordinary `BEFORE ROW` triggers for reject-all immutable `UPDATE`/`DELETE` cases because
  they fail early, emit clear table-specific errors, and simplify rollback

Implemented table-specific triggers:

- `prediction_runs`
  - `BEFORE UPDATE`
  - table-specific lifecycle validator
  - enforces immutable identity fields plus allowed lifecycle transitions
- `prediction_runs`
  - `BEFORE DELETE`
  - table-specific delete blocker or shared append-only delete blocker
  - preserves durable run history
- `predictions`
  - `BEFORE INSERT`
  - table-specific parent-run lock guard
  - locks parent run row and requires `run_status = 'RUNNING'` plus
    `prediction_finalized_at IS NULL`
- `predictions`
  - `BEFORE UPDATE`
  - shared append-only blocker
- `predictions`
  - `BEFORE DELETE`
  - shared append-only blocker
- `decision_policy_versions`
  - `BEFORE UPDATE OR DELETE`
  - shared append-only blocker
- `agent_config_versions`
  - `BEFORE UPDATE OR DELETE`
  - shared append-only blocker
- `decisions`
  - `BEFORE UPDATE`
  - table-specific validator allowing only one-way `published_at`
- `decisions`
  - `BEFORE DELETE`
  - shared append-only blocker
- `decision_agents`
  - `BEFORE UPDATE OR DELETE`
  - shared append-only blocker
- `rating_snapshots`, `odds_snapshots`, `match_versions`, `match_result_observations`,
  `match_stat_observations`, `player_source_ids`, `identity_resolution_events`,
  `identity_resolution_candidates`, `tournament_source_ids`, `match_source_ids`,
  `match_version_sources`, `matches`, `data_health_checks`
  - `BEFORE UPDATE OR DELETE`
  - shared append-only blocker
- `ingest_runs`
  - `BEFORE UPDATE`
  - table-specific lifecycle validator allowing only one terminal `RUNNING -> terminal`
    update with final counters and `error_summary`
- `ingest_runs`
  - `BEFORE DELETE`
  - blocker preserving ingest history

The existing T-ED1 lineage constraint triggers on `decision_agents` and `predictions`
remain unchanged in principle. Redundant execution is acceptable because lineage correctness
and immutability are separate concerns.

### Existing-data migration safety

Unlike `0004`, `0005` is expected to run on a populated valid database.

Precondition analysis:

- `0004` already enforces the run timestamp shape, policy/config constraints, prediction
  alignment, and decision-agent lineage rules that T-ED2 depends on
- no destructive cleanup or bootstrap backfill should be required
- the main schema change with existing data impact is FK recreation to make the prediction
  finalization FK deferrable; valid `0004` data remains valid under that weaker timing rule

### `0005 DOWN`

Rollback target:

- exact `0004_prediction_runs_and_policy_versions` enforcement level

Rollback design:

- may run on a populated database because it removes only enforcement, not data
- drops T-ED2 triggers in dependency-safe order
- drops T-ED2 helper functions after dependent triggers are removed
- recreates `fk_predictions_prediction_run_finalization` with the original immediate-only
  behavior from `0004`
- does not drop or rewrite historical rows
- permits clean re-UP afterward

### Implementation validation plan

Migration chain:

```text
0001 -> 0002 -> 0003 -> 0004 -> 0005
```

Required PostgreSQL 16 coverage:

- populated valid `0004` database upgrades cleanly to `0005`
- `UPDATE predictions` rejected
- `DELETE predictions` rejected
- `UPDATE/DELETE decision_policy_versions` rejected
- `UPDATE/DELETE agent_config_versions` rejected
- every allowed `prediction_runs` lifecycle transition succeeds
- every meaningful forbidden `prediction_runs` transition fails
- `prediction_finalized_at` can be set exactly once
- `prediction_finalized_at` cannot be cleared or changed
- valid explicit finalization transaction succeeds
- direct standalone prediction insert outside the explicit finalization transaction fails
- prediction insert against terminal-but-unfinalized `FAILED` and `CANCELLED` runs fails
- late prediction insert after committed finalization fails through direct SQL
- multi-connection concurrency test covers finalization vs late insert
- concurrent finalization attempts serialize correctly on the parent run row
- terminal run races cannot rewrite terminal history
- `UPDATE decisions` is rejected except for one-way `published_at`
- invalid `published_at` rewrites fail
- `UPDATE/DELETE decision_agents` rejected
- `ingest_runs` allows only the documented `RUNNING -> terminal` transitions and rejects
  state-changing `RUNNING -> RUNNING` updates
- included historical and provenance tables reject direct SQL `UPDATE` and `DELETE`
- `0005 DOWN` restores exact `0004` protection level
- clean re-UP succeeds after DOWN
- concurrency tests use real separate connections, deterministic synchronization, and
  bounded `lock_timeout` / `statement_timeout` or equivalent harness timeouts so CI fails
  cleanly rather than hanging indefinitely
