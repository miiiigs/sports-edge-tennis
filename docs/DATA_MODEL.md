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
  - Ambiguous cases keep candidate player IDs and never silently collapse to one player.

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
  - Provenance bridge from source match identifiers or deterministic source hashes to the
    normalized match identity and version.

### Results and statistics

- `match_result_observations`
  - Separate append-only result layer. Pre-match match identity is not the same thing as
    post-match outcome state.
- `match_stat_observations`
  - Raw or normalized source statistics by player, match, and provenance.
  - Stores source-level service and return inputs without converting them into model
    outputs.

### Market observations

- `odds_snapshots`
  - Append-only odds history.
  - Raw bookmaker observations only.
  - Market identity is explicit through `market_type`, `selection_type`, and optional
    `line_value`.

### Ratings and model outputs

- `rating_snapshots`
  - Immutable player rating states by metric, surface, model version, feature version, and
    `as_of`.
- `predictions`
  - Immutable agent-level outputs.
  - Explicit `ABSTAINED` support.
  - No market price fields exist in this contract.

### Manager, decisions, settlement, and CLV

- `decisions`
  - Separate manager/price layer.
  - Records `BET`, `NO_BET`, and `VETOED`.
  - Distinguishes `TYPE_A_MODEL_EDGE` from `TYPE_B_PRICE_DISPERSION`.
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
