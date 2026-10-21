# Tennis Model Error Forensics - 2026-10-21

Read-only research into why the live Tennis Manager models issue incorrect
high-confidence picks. No production model, Manager rule, database row, or
prediction was changed.

## Scope

- **Primary live population:** first production Manager `CONSENSUS` evaluation
  per match, probability >= 60%.
- **Separate populations:** A1/A2 wide-spread tracking, actual Value Bets,
  Market Veto-blocked decisions, and the historical Agent 2 replay from PR #239.
- **Scoring:** only `COMPLETED` results with `winner_side IN (1, 2)` are scored.
  `RETIRED`, `WALKOVER`, and `CANCELLED` are excluded from accuracy/Brier/log
  loss and reported separately.
- **No point-in-time leakage:** a pick is eligible only if its evaluation and
  finalization precede `match_start_at`, and history uses only results visible
  before the pick.

## Key method

The production database stores one Manager row per hourly evaluation. The live
window contains **4,825 Manager CONSENSUS rows for only 346 matches** - an
average of 13.9 reruns per match. Scoring those rows as independent predictions
would overweight a single match by up to 41x. This harness therefore freezes the
**first** production evaluation per match, which is the population the dashboard
scores, and never substitutes a later, more confident rerun.

## Commands

The extractor must run where the production database hostname resolves (the
Railway service or another host inside the project network). It is read-only:

```powershell
$env:TENNIS_DATABASE_URL = "<production DSN>"
python research/tennis_model_error_forensics_20261021/extract_live_picks.py `
  --git-sha (git rev-parse HEAD) > tmp/tennis_forensics_20261021.jsonl
```

In this investigation the extractor was piped through a read-only Railway SSH
session because the database hostname is internal. The executed SQL is the same.

```powershell
python research/tennis_model_error_forensics_20261021/analyze.py `
  --dataset tmp/tennis_forensics_20261021.jsonl `
  --output-dir tmp/tennis_forensics_out
python -m pytest tests/test_tennis_model_error_forensics.py -q
ruff check research/tennis_model_error_forensics_20261021 tests/test_tennis_model_error_forensics.py
```

The frozen JSONL is intentionally not committed. The extractor, dataset SHA-256,
artifact hashes and source-audit counts are in
`artifacts/frozen_manifest.json`.

## Outputs

| File | Contents |
| --- | --- |
| `artifacts/frozen_manifest.json` | dataset hash, cutoff, source counts, artifact hashes |
| `artifacts/analysis.json` | full aggregate metrics, bands, breakdowns, case studies |
| `artifacts/tables.md` | human-readable accuracy/calibration tables |
| `artifacts/case_studies.md` | all high-confidence incorrect picks with evidence |
| `REPORT.md` | findings, case studies, recommendations, blockers |

## Safety

- `SET default_transaction_read_only = on` in every database session.
- No production writes, schema changes, deployments, merges, or live-model
  changes.
- No paid provider calls and no provider-spend increase.
- No secrets or raw provider payloads are written.
- Historical replay and forward-issued Model Picks are never pooled.
