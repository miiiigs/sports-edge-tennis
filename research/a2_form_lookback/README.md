# Agent 2 recent-history lookback shadow

Research-only experiment for Trello card `RKtcJTro`.  Nothing here is imported by
the production runner, dashboard, providers, or database.

## Question

Does restricting Agent 2's player history to a recent window improve predictive
quality, calibration and coverage compared with full-career history?

| Variant | History included at prediction time |
| --- | --- |
| `baseline` | all visible prior results |
| `365` | results visible in the previous 365 days |
| `730` | results visible in the previous 730 days |
| `1460` | results visible in the previous 1460 days |

All variants use the same eligible matches, frozen model config, scoring rules,
preprocessing, cohorts and outcomes.  Only the history window changes.

## Why this is not a per-match pre-match backtest

The canonical 2016-2025 Sackmann corpus does **not** have trustworthy
`match_versions.match_start_at` values.  A per-match replay at
`match_start_at - 6h` cannot be reconstructed from the stored evidence.  The
corpus also exposes fixture/result/stat rows at the tournament-end boundary.

The experiment therefore uses a **tournament-cohort replay**:

- every match in one tournament edition shares one prediction cohort;
- the cohort is the maximum visible timestamp of the selected fixture, result and
  stat evidence for that edition;
- all matches in the edition are predicted before any result from that edition is
  applied;
- a match enters history only after its fixture, result and two-sided raw stats
  are all visible.

This is an offline history-window evaluation.  It is **not** evidence of
genuinely forward-issued Model Pick accuracy.

## Frozen population and splits

Dataset extraction is read-only against the production Tennis database.

- Seasons: 2016-2025.
- Tour: `ATP_MAIN`, `CHALLENGER`.
- Surface present in the selected fixture version.
- `outcome_status = COMPLETED` and `winner_side IN (1, 2)`.
- Exactly two coherent RAW_SOURCE stat rows matching the two participants.
- A scoring rule resolvable by the production `resolve_scoring_rule`.
- Same `match_id`, player IDs, winner, timestamps and stats in every variant.

Chronological boundaries:

| Split | Seasons | Use |
| --- | --- | --- |
| `burn_in` | 2016-2018 | history only; never scored |
| `validation` | 2019-2022 | exploratory comparison |
| `test` | 2023-2025 | untouched holdout; analysed after the harness commit |

No random seed is used for the model (deterministic).  Bootstrap resampling uses
seed `20261019` and is reported with the metric.

## Metrics

Per variant and split, on both the complete eligible population and the
common-prediction population:

- eligible, attempted, predicted, abstained, settled W/L;
- raw accuracy, Brier score, log loss, expected calibration error (10 bins from
  0.50 to 1.00);
- reliability at >= 0.70, >= 0.80, >= 0.90 and per 5-point bin;
- selection coverage overall and by surface, tier, tour, season, history-sample
  bucket, long-inactivity return and qualifying/main draw;
- match-level bootstrap 95% confidence intervals, and paired bootstrap
  differences against baseline on the common-prediction population.

A bookmaker-favourite baseline is **not** reported: valid historical match-winner
prices in the canonical database begin on 2026-09-09, outside the frozen
2016-2025 experiment period.  Prices are never inferred.

## Long-inactivity cohort

A match is a "return from long inactivity" case when either player's previous
match (across the full historical population, independent of the current
variant's window) was at least 180 days before the current tournament start.
This is an inactivity proxy, not verified injury data.

## Reproduce

```powershell
# 1. Read-only extraction (requires DATABASE_URL or TENNIS_DATABASE_URL)
#    Set A2_RESEARCH_GIT_SHA when piping through a remote service so the
#    manifest records the research branch SHA, not the deployed service SHA.
$env:A2_RESEARCH_GIT_SHA = (git rev-parse HEAD)
python research/a2_form_lookback/extract_dataset.py > tmp/a2_frozen_20261019.jsonl

# 2. Four deterministic variant replays (parallel by default)
python research/a2_form_lookback/harness.py `
  --dataset tmp/a2_frozen_20261019.jsonl `
  --output-dir tmp/a2_form_lookback `
  --mode run --reps 1000

# 3. Analysis (or use --mode run-and-analyze)
python research/a2_form_lookback/harness.py `
  --dataset tmp/a2_frozen_20261019.jsonl `
  --output-dir tmp/a2_form_lookback `
  --mode analyze --reps 1000

# 4. Tests
python -m pytest tests/test_a2_form_lookback.py -q
```

The frozen JSONL is intentionally not committed.  The `_manifest` line records
its SHA-256, extraction time, source database, row counts and cutoff.  The report
records the same values so the run can be audited or repeated.

## Research dependencies

`numpy` is required for bootstrap intervals.  It is not a production dependency:

```powershell
python -m pip install -r research/a2_form_lookback/requirements.txt
```

## Safety

- No production model activation.
- No live strategy change.
- No paid API calls.
- No production database mutation (`SET default_transaction_read_only = on`).
- No schema change.
- Replay results are kept physically separate from genuinely forward-issued Model
  Picks.
