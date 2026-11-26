# AO match-format dependency matrix — why 128 corrected BO3 rows moved 0 of 3,284 A1 forecasts

**Date:** 26 November 2026
**Branch / PR:** `workstream/ao-2027-format-readiness-20261125` (PR #389)
**Mode:** deterministic offline counterfactual. No database, no provider call, no
production write, no historical rewrite, no forecast change.
**Evidence:** [`ao_match_format_dependency_matrix_20261126.json`](evidence/ao_match_format_dependency_matrix_20261126.json)
**Reproduction:** `ops/validation/ao_match_format_dependency_matrix_20261126.py`

## Answer

The zero result is not a population accident and not a missing-history quirk.
It follows directly from the feature dependency:

| Pathway | Reads `best_of_sets` in its probability? | Effect of BO3 → BO5, all else equal |
|---|---|---|
| Agent 1 Surface Elo | **No** — validates `best_of_sets > 0`, then uses rating and surface only | **0.000000** probability delta |
| Agent 2 Hierarchical Markov | **Yes** — selects the scoring rule and match length | One Markov conversion; **+0.034906** for the controlled pair below |
| Manager consensus | **No** — unweighted mean of active agent probabilities | Exactly `1/3` of the Agent 2 delta; no second conversion |

Correcting the 2026 AO history label therefore changes only a stored field that
Agent 1 never reads. The 3,284 settled A1 targets are 2026-09-10 onward, so AO
2026 is not even an evaluation target; it can only be a historical observation.
The previously measured BO3→BO5-only replay result (0 changed probabilities out
of 3,284) is the expected consequence, not an anomaly.

## Controlled counterfactual

Identical players, Elo/ratings, history, surface, prediction cutoff and model
configuration. Only `best_of_sets` changes. Agent 2 serve inputs are held at
`p1 = 0.65`, `p2 = 0.62`; Agent 1 uses the same six-match hard-court history in
both arms; Manager receives the unchanged Agent 1 and Agent 3 probabilities
`0.660000` and `0.670000`.

| Object | BO3 | BO5 | Delta |
|---|---:|---:|---:|
| Agent 1 win probability | 0.887214 | 0.887214 | **0.000000** |
| Agent 2 match probability | 0.6465911619… | 0.6814968606… | **0.0349056986…** |
| Manager consensus | 0.6588637206… | 0.6704989535… | **0.0116352328…** |

The Manager delta equals the arithmetic mean effect
`(0 + 0.0349056986… + 0) / 3` exactly. If Manager applied a second set-to-match
adjustment, the delta would not equal the mean effect.

## Independent-set reference

For an independent set-win probability of 0.60:

- BO3: `p²(3 − 2p) = 0.648`
- BO5: `Σ_{k=3..5} C(5,k) p^k (1−p)^(5−k) = 0.68256`

This is a mathematical reference only. It is **not** an instruction that every
agent must convert set probabilities independently. Agent 2 resolves the match
from point-level serve/return and scoring rules, so its BO5/BO3 ratio need not
match the independent-set ratio.

## Duplicate-adjustment prevention

The production paths have one, and only one, match-length conversion:

- Agent 1 stores and validates `best_of_sets` but does not use it in
  `expected_score`, rating updates or `predict_from_replay`.
- Agent 2 passes the scoring rule into the single `match_probability` recursion;
  games, tiebreaks, sets and the match are resolved in that one model.
- `manager/consensus.py` contains no match-format logic. It validates agent
  contracts, checks spread/direction/floor and computes the mean.
- The regression
  `test_manager_adds_no_second_match_format_adjustment` asserts the Manager
  delta is exactly the mean of the Agent 2 delta.
- The regression
  `test_agent2_is_format_sensitive_through_exactly_one_markov_conversion`
  asserts the BO5−BO3 effect stays in the calibrated narrow range and is
  player-swap symmetric.

## Why AO 2026 contributes zero Agent 2 / Manager effect

AO 2026 has **zero** `match_stat_observations`, so no AO 2026 row can enter the
Agent 2 serve/return fit. AO 2026 also has zero `prediction_run_targets`,
`predictions`, `manager_consensus_results`, `decisions` and publications. The
format correction cannot move an AO-origin forecast because no AO-origin
forecast exists. The 40 main-draw 2016–2025 QF rows are already `best_of=5`; the
qualifying-flag defect is diagnostic and removes no A2 training row.

## Experiment implications

| Experiment | Reconsider for BO3? | Reason |
|---|---|---|
| A1 identity-bridge retrospective / forward shadow | **No** | A1 probability is format-invariant; only the separate NULL-surface defect moves it |
| A1 real-label-shuffle negative control | **No** | Same invariant; surface sensitivity is a separate bounded question |
| A2 Grand Slam support / BO5 rehearsal | **No** | 2026 AO has zero A2 stats and was never an AO target |
| Manager C / Challenger experiments | **No** | Challenger scope, no AO fixture |
| AO historical format census | **No** | It is the measurement, not a forecast experiment |

## Reproduction

```powershell
$env:PYTHONPATH = "src"
python ops/validation/ao_match_format_dependency_matrix_20261126.py `
  --out-json docs/research/evidence/ao_match_format_dependency_matrix_20261126.json

python -m pytest tests/test_ao_2027_format_readiness.py -q
```

The script imports the unchanged production Agent 1, Agent 2 and Manager
modules. It opens no database connection and calls no provider.

## Non-claims

- This does not validate any 2027 AO result; January 2027 is prospective.
- This does not correct stored 2026 history or change any issued forecast.
- The independent-set values are a reference, not a model result.
- The separate NULL-surface metadata defect remains open and is not re-scored
  here as a format effect.
