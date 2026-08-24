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
