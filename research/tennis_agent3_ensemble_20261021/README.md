# Agent 3 investigation and A1/A2/A3 ensemble shadow

Experiment: `tennis_agent3_ensemble_20261021`

This is isolated research. It does not activate a production model, change
Manager thresholds or weights, change betting logic or stakes, write production
prediction/Manager rows, or touch Lane 1/Lane 2 dashboard recovery work.

## What is here

| File | Purpose |
| --- | --- |
| `extract.py` | Read-only PostgreSQL extraction of A3 evaluations, A3 support diagnostics and first-Manager-per-match ensemble rows. |
| `analysis.py` | Agent 3 coverage, abstention classification, redundancy and incremental-contribution analysis. |
| `policy.py` | Research-only mirror of the production Manager v1/v2 policy, tested for exact agreement with `evaluate_consensus`. |
| `ensemble_shadow.py` | Applies the frozen PR #248 temperature slope to Agent 2 only and compares hypothetical Manager decisions with issued decisions. |
| `forward_shadow.py` | Append-only, idempotent forward shadow ledger collector. |
| `artifacts/analysis.json` | Aggregate Agent 3 evidence. |
| `artifacts/tables.md` | Human-readable Agent 3 evidence. |
| `artifacts/ensemble_shadow.json` | Aggregate calibration impact. |
| `artifacts/ensemble_tables.md` | Human-readable calibration impact. |
| `artifacts/preregistration.json` | Pre-registered forward acceptance rule and rejection triggers. |
| `REPORT.md` | Findings, limitations, release blockers and decision. |

The raw JSONL extracts are intentionally not committed. They are reproducible
from `extract.py`; the extraction manifest records counts, cutoff, query hashes
and file SHA-256 values.

## Reproduction

Provide a read-only database DSN through `TENNIS_DATABASE_URL` or
`DATABASE_URL`. Never commit the DSN.

```bash
python -m research.tennis_agent3_ensemble_20261021.extract \
  --output-dir tmp/lane4/extract \
  --cutoff 2026-10-21T23:59:59+00:00

python -m research.tennis_agent3_ensemble_20261021.analysis \
  --extract-dir tmp/lane4/extract \
  --artifacts-dir research/tennis_agent3_ensemble_20261021/artifacts

python -m research.tennis_agent3_ensemble_20261021.ensemble_shadow \
  --extract-dir tmp/lane4/extract \
  --artifacts-dir research/tennis_agent3_ensemble_20261021/artifacts
```

Forward shadow, append-only and deduped on
`(match_id, prediction_run_id)`:

```bash
python -m research.tennis_agent3_ensemble_20261021.extract \
  --output-dir tmp/lane4/forward_extract

python -m research.tennis_agent3_ensemble_20261021.forward_shadow \
  --extract-dir tmp/lane4/forward_extract \
  --shadow-path tmp/lane4/forward_shadow_rows.jsonl \
  --state-path tmp/lane4/forward_shadow_state.json
```

## Tests

```bash
python -m pytest tests/test_tennis_agent3_ensemble.py -q
```

The policy test compares the research mirror against the production
`evaluate_consensus` function for all structural branches. The shadow tests
assert that decisions do not read outcome fields and that append-only collection
is idempotent.

## Safety boundaries

- Source database session is set to `default_transaction_read_only = on`.
- No production prediction, Manager, policy, odds, stake or settlement row is
  written.
- The calibration slope is frozen from PR #248's 2019-2022 refit. It is not
  refitted on live outcomes.
- Outcomes are joined only for retrospective scoring; they are never inputs to
  a shadow decision.
- Production activation remains prohibited.
