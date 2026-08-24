# Point-In-Time Semantics

## Why this exists

Historical tennis backtests fail quietly when timestamps are conflated. The committed schema
therefore separates source observation time, model-valid time, ingestion time, and prediction
lock time.

## Canonical timestamps

### `match_start_at`

The scheduled or actual fixture start in UTC.

### `source_observed_at`

When the source made the fact observable, or when Sports Edge captured it at the source
boundary when the provider exposes no explicit source timestamp.

This is the timestamp that protects historical reconstruction from using data that did not
exist yet.

### `as_of`

The latest moment at which the stored record is valid for model consumption.

The universal model query rule is:

```text
as_of <= T
```

for any model or backtest evaluation time `T`.

### `ingested_at`

When Sports Edge inserted the row into its own database.

This must never substitute for `source_observed_at` during historical replay because delayed
historical ingest is normal and does not mean the source fact was unknown at the original
time.

### `created_at`

Database row creation time for immutable internal auditing and ordering.

### `updated_at`

Used only on mutable master-data tables such as canonical players, aliases, and tournament
metadata.

### `prediction_locked_at`

The time after which the prediction input state is frozen for publication or evaluation.

Predictions and decisions must not be evaluated as if later market or source updates were
available before this lock time.

For predictions and decisions:

- `prediction_locked_at >= as_of`
- `created_at >= as_of`
- `created_at` may fall before or after prediction_locked_at

`created_at` is therefore an audit timestamp, not a replay boundary.
In other words: created_at may fall before or after prediction_locked_at.

## Reconstruction rules

To answer:

> What could Sports Edge have known about Player A vs Player B at time T?

future queries must:

1. Resolve player identity only from canonical players, aliases, source IDs, and identity
   resolution events available with `as_of <= T`.
2. Select the latest `match_versions` rows for relevant fixtures where `as_of <= T`.
3. Exclude `match_result_observations` whose `source_observed_at` or `as_of` is later than
   `T`.
4. Select only `match_stat_observations`, `odds_snapshots`, and `rating_snapshots` with
   `as_of <= T`.
5. Exclude any prediction or decision data whose `prediction_locked_at` is later than `T`.

## Leakage boundaries

### Results

Sports results live in `match_result_observations`, not in `matches` or `match_versions`.
That makes it possible to query fixture identity without accidentally reading post-match
outcomes.

### Odds

Odds are append-only in `odds_snapshots`. Later prices do not overwrite earlier prices.

### Predictions

Predictions are immutable agent outputs without bookmaker price fields. Agent storage does not
encourage market leakage.

### Decisions and CLV

Manager decisions, settlement, and CLV are separate downstream layers. They never backfill
agent prediction rows.
