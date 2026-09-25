# Current System Replay

Replay ID: `CURRENT_SYSTEM_REPLAY_20260910_V1`

## Purpose

Answer one question safely:

> What would the CURRENT Sports Edge Tennis system have produced from a
> historical date onward if it had been operating then using only information
> legitimately available at each historical decision time?

## Two separate truths

| Truth | Meaning | Mutability |
| --- | --- | --- |
| ORIGINAL LIVE | What production actually generated historically | immutable |
| `CURRENT_SYSTEM_REPLAY_20260910_V1` | Synthetic replay of current logic on point-in-time historical inputs | immutable artifact |

Replay picks are never bets actually issued historically. Replay and original
production results are never blended into one statistic.

## Package layout

```text
src/sports_edge_tennis/replay/
    contracts.py       replay schemas + literals (PIT status, classifications)
    pit.py             point-in-time enforcement primitives (fail closed)
    classification.py  model-pick classification + funnel outcomes
    engine.py          pure replay pipeline (no database access)
    first_qualified.py first-qualified-per-sporting-match accounting + metrics
    manifest.py        replay manifest + reproducibility hashing
    artifacts.py       immutable artifact store (never overwrite)
    loader.py          read-only Postgres PIT loader (official rerun path)
    serde.py           deterministic serialization/hashing
    selftest.py        synthetic determinism harness (NOT production replay)
    cli.py             command-line runner
```

## Replay pipeline

For each historical sporting match the engine executes the current logic in
order, reusing production functions directly:

1. agent history snapshots are built at the blind prediction cutoff;
2. Agent 1 surface Elo (`predict_from_replay`, current default config);
3. Agent 2 hierarchical Markov (`predict`, current default config);
4. Agent 3 intransitivity (`predict`, current default config);
5. Manager v1 (`manager.consensus.evaluate_consensus`);
6. Market v1 (`market.evaluator.evaluate_market_match`) including quarter-Kelly;
7. Veto v1 (`veto.service.evaluate_veto_context`, kill-only);
8. first-qualified-per-sporting-match accounting;
9. post-prediction scoring from the actual result.

Odds never enter A1/A2/A3. The result is used only for scoring.

## Point-in-time enforcement

Two independent layers:

1. **Loader layer.** Every read uses
   `source_observed_at <= cutoff AND as_of <= cutoff`, with `ingested_at`
   deliberately excluded. The loader reuses the same PIT modules as live
   (`runner.targets`, `data.agent*_history`, `market.persistence`,
   `veto.service`).
2. **Engine layer (defense in depth).** `replay.pit` re-checks odds visibility,
   decision-time ordering, result visibility, and loader proofs. A fact
   timestamped after the cutoff raises `PointInTimeViolation` (hard fail). A
   fact that was never captured is marked `DATA_NOT_AVAILABLE_PIT`; it is never
   inferred or substituted with current data.

If PIT availability cannot be proven, replay abstains / excludes the match and
records why.

## Storage / artifact model

Replay writes **no production decision rows** and requires **no migration**. It
emits an immutable directory per execution:

```text
<artifacts-root>/<replay_id>__<reproducibility_sha256[:16]>/
    manifest.json
    model_picks.json
    value_bets.json
    exclusions.json
    opportunities.json
    SHA256SUMS
```

A second write to the same execution directory fails with
`ReplayArtifactExistsError`; a previous replay is never overwritten silently.
New replay versions create new directories.

Rationale for artifacts over new tables: replay rows must never be mistaken for
real production decisions, `predictions`/`decisions` are append-only and
lineage-bound to prediction runs, and the replay population is still moving while
the historical recovery lane reconciles coverage. Versioned artifacts keep the
two truths physically separate with zero schema risk.

## Manifest

`ReplayManifest` records the replay ID, schema version, git SHA, generation
time, historical date range, input snapshot identifiers, agent/manager/market/
veto/staking versions, run/matches-considered counts, exclusion reason counts,
model-pick and value-bet counts, PIT-incomplete cases, missing historical odds,
identity failures, surface failures, `DATA_NOT_AVAILABLE_PIT` count, and a
`ledger_sha256`.

`reproducibility_sha256` excludes wall-clock metadata, so the same replay version
plus the same input snapshot produces an identical hash.

## P1A — Model Picks ledger

One row per Manager evaluation with full lineage: replay/run identity, sporting
match and provider event IDs, decision/prediction timestamps, A1/A2/A3 state,
side, probability, config and abstain reason, Manager version/outcome/reason,
selected side, consensus probability, classification, PIT/identity/surface
status, and the later outcome (`WIN`/`LOSS`/`VOID`/`CANCELLED`/`PENDING`).

Model Pick metrics are count/wins/losses/voids/pending/hit-rate. No units or P&L
are attached to the Model Pick ledger.

## P1B — Value Bets ledger

A strict subset of Manager-qualified Model Picks that also passed Market + Veto.
Records bookmaker, price source, market observation time, offered price,
reference (sharp) price and observation time, edge, Market/Veto output, stake
fraction and staking rule, actual result, one-unit units, ROI basis, and CLV.

CLV is emitted only when PIT-valid historical closing/reference evidence
genuinely exists; otherwise `clv_status = NOT_AVAILABLE_PIT`. Missing odds are
marked `DATA_NOT_AVAILABLE_PIT`; prices are never inferred.

Units use the production ledger's one-unit convention: `WIN -> price - 1`,
`LOSS -> -1`, `VOID`/`CANCELLED -> 0`, `PENDING -> None`; ROI uses settled bets
as the denominator.

## First-qualified semantics

A sporting match enters a ledger once, at its first qualification, ordered by
`(decision_as_of, run_sequence)`. This covers the same match across multiple
hourly runs, a Model Pick that precedes a later Value Bet, odds moving after
first qualification, later additional qualifications, cancelled/walkover/retired
matches, and provider-linking changes.

## Model Pick classification preparation

The ledger carries a `classification`:

- `MANAGER_QUALIFIED` (current Model Pick);
- `A1_A2_AGREE_WIDE_SPREAD_NOT_MANAGER_QUALIFIED` (primary models agree, spread
  too wide, tracking only);
- `NOT_MANAGER_QUALIFIED`.

Category 2 is not a Value Bet. No Manager behaviour change is implemented. The
wide-spread reporting lane is separate; the schema only preserves clean future
segmentation.

## Funnel separation

Replay distinguishes where opportunities disappear: data availability failures
(`DATA_NOT_AVAILABLE_PIT`), agent abstention, Manager rejection, market
rejection, veto rejection, insufficient edge, and qualified Value Bet.

## Interaction with the historical recovery lane

While the historical recovery lane is still reconciling deterministic coverage,
numerical output is labelled:

`PROVISIONAL - HISTORICAL RECOVERY SNAPSHOT NOT FROZEN`

After the history snapshot is accepted and frozen:

1. freeze the input snapshot;
2. rerun the exact same replay code/version;
3. generate official `CURRENT_SYSTEM_REPLAY_20260910_V1`;
4. compare against the provisional run and explain data-driven differences.

No policy may be tuned between provisional and official replay.

## Official rerun

```powershell
$env:DATABASE_URL = "<production read-only connection string>"
python -m sports_edge_tennis.replay.cli `
    --start-date 2026-09-10 `
    --end-date 2026-09-24 `
    --match-ids-file tmp/replay_match_ids.txt `
    --artifacts-root artifacts/replay `
    --label official
```

`--label official` removes the provisional label. The command only reads the
database.

## Determinism self-test

`python -m sports_edge_tennis.replay.selftest <dir>` runs a fixed synthetic
snapshot with no database. It validates the full pipeline and determinism. Its
numbers are excluded from any performance claim.

## Limits

- The official rerun requires a database connection; it is not exercised by the
  offline self-test.
- `derive_run_specs_from_match_days` plans one coherent pass per UTC match day
  using match-start-minus-lead cutoffs. Exact historical hourly-run replication
  with a stored fixture schedule is a follow-up refinement.
- Historical sharp (Pinnacle) closing/reference coverage is generally absent from
  the archived corpus, so many Value-Bet candidates are correctly reported as
  `DATA_NOT_AVAILABLE_PIT` rather than priced.
