# Point-In-Time Semantics

## Why this exists

Historical tennis backtests fail quietly when timestamps are conflated. The committed schema
therefore separates source observation time, model-valid time, ingestion time, and prediction
lock time.

## Canonical timestamps

### `match_start_at`

The scheduled or actual fixture start in UTC when the source exposes a trustworthy match
date or time.

This field may remain `NULL` for historical source rows whose event timing is genuinely
unknown. In that case Sports Edge preserves tournament identity separately and does not
fabricate a fixture timestamp.

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

## T-ED1 run-level semantics

The planned T-ED1 lineage design adds run-level timing without collapsing the existing
timestamp meanings.

### `prediction_runs.as_of`

The common blind-input cutoff for one coherent full-slate Tennis evaluation pass.

For T-ED1, the intended invariant is:

- every prediction in a run uses the same `as_of` as that run
- later decision rows may use a later `as_of` because market and veto inputs arrive after
  prediction finalization

### `prediction_runs.started_at`

When the run began execution.

This is operational timing, not the model replay boundary.

### `prediction_runs.prediction_finalized_at`

The logical lock time for the prediction set produced by one run.

For future T-ED1 implementation, the intended invariant is:

- `prediction_runs.prediction_finalized_at >= prediction_runs.started_at`
- `prediction_runs.prediction_finalized_at >= prediction_runs.as_of`
- `predictions.prediction_locked_at = prediction_runs.prediction_finalized_at`

That gives one explicit prediction-freeze time without requiring a separate
`prediction_locks` table in `0004`.

Prediction finalization is not itself an execution status. A run may be `RUNNING`,
`COMPLETED`, `FAILED`, or `CANCELLED` independently of whether
`prediction_finalized_at` has been recorded.

## T-ED2 finalization mechanics

T-ED2 must make the prediction freeze boundary mechanical, not only documentary.

The chosen design keeps the committed-state invariant:

- `predictions.prediction_locked_at = prediction_runs.prediction_finalized_at`

but changes how that state is reached safely:

- the finalization FK from `predictions` to `prediction_runs` becomes `DEFERRABLE
  INITIALLY IMMEDIATE`
- the finalization transaction explicitly defers that FK, locks the parent
  `prediction_runs` row with `SELECT ... FOR UPDATE`, inserts the complete prediction set
  with one common intended lock timestamp `T`, sets `prediction_runs.prediction_finalized_at
  = T`, and then commits
- prediction rows are not a valid standalone pre-finalization persistence path under T-ED2;
  a direct `INSERT` that does not defer the FK and establish the matching parent
  finalization key in the same transaction cannot commit
- after that commit, later prediction inserts for the same run must fail mechanically

This matters for concurrency:

- if the finalization transaction holds the run-row lock first, later prediction inserts
  wait and then see the committed finalized timestamp, so they fail
- if an unauthorized direct prediction `INSERT` reaches the run-row lock first while the run
  is still `RUNNING` and unfinalized, it is still not part of the supported write protocol;
  without the same transaction deferring the FK and establishing the matching parent key,
  the initially-immediate FK prevents it from committing
- if a competing authorized finalization transaction reaches the run-row lock first, it
  becomes the only transaction allowed to insert the coherent prediction set for that run

The result is one coherent committed prediction set per finalized run without requiring a
separate `prediction_locks` table or an application-only honor system.

Terminal but unfinalized runs remain permanently closed:

- `FAILED` + `prediction_finalized_at IS NULL`
- `CANCELLED` + `prediction_finalized_at IS NULL`

Those rows still represent completed historical execution outcomes and may not accept later
prediction inserts merely because the finalization timestamp is `NULL`.

### `prediction_runs.ended_at`

When the full-slate run reached a terminal execution outcome.

For future T-ED1 implementation, the intended invariant is:

- `prediction_runs.ended_at >= prediction_runs.started_at`
- `prediction_runs.ended_at >= prediction_runs.prediction_finalized_at` when both values
  exist
- `prediction_runs.ended_at IS NULL` while `run_status = 'RUNNING'`
- `prediction_runs.ended_at IS NOT NULL` when `run_status IN ('COMPLETED', 'FAILED',
  'CANCELLED')`

An empty slate is still a completed run. "No qualifying bets today" is domain truth, not a
failed execution state.

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
6. When T-ED1 is implemented, resolve historical prediction lineage through
   `prediction_runs`, `agent_config_versions`, `decision_policy_versions`, and
   `decision_agents.prediction_id`, not by timestamp proximity.
7. When T-ED2 is implemented, treat committed `prediction_runs.prediction_finalized_at` as
   the mechanical close boundary for further prediction inserts under that run.

## Leakage boundaries

### Results

Sports results live in `match_result_observations`, not in `matches` or `match_versions`.
That makes it possible to query fixture identity without accidentally reading post-match
outcomes.

Sackmann rows without explicit `match_date` are a special case. Their `tourney_date`
represents the tournament start, not the actual match day. Sports Edge therefore keeps
`match_start_at` unknown for those rows and delays match, result, and stat visibility until
a conservative tournament-end boundary derived from current source scope:

- Grand Slam and ATP main-draw rows: second Sunday after the source `tourney_date`
- Challenger rows: end of the source tournament week

If exact timing cannot be justified safely, visibility moves later rather than earlier.

### Odds

Odds are append-only in `odds_snapshots`. Later prices do not overwrite earlier prices.

### Predictions

Predictions are immutable agent outputs without bookmaker price fields. Agent storage does not
encourage market leakage.

### Decisions and CLV

Manager decisions, settlement, and CLV are separate downstream layers. They never backfill
agent prediction rows.

For future T-ED1 replay, decisions remain their own historical layer:

- the run tells us which full-slate execution produced the candidate prediction set
- the prediction rows tell us what each predictive agent said
- the policy version tells us which manager thresholds and freshness rules governed the
  evaluation
- the decision row records the downstream market/value/veto outcome without overwriting the
  agent truth
