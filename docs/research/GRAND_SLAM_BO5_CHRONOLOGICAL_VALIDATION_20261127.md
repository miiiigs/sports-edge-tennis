# Chronological Grand Slam best-of-five validation — methodology and synthetic result

**Date:** 27 November 2026
**Branch / PR:** `workstream/ao-2027-format-readiness-20261125` (PR #389)
**Status:** executable methodology and synthetic validation complete. **Real-data
result BLOCKED** by the absence of a commercially authorised historical corpus.
**Mode:** no provider call, no database write, no purchase, no production change.
**Code:** `src/sports_edge_tennis/validation/grand_slam_bo5_chronology.py`,
`ops/validation/grand_slam_bo5_chronology_20261127.py`
**Tests:** `tests/test_grand_slam_bo5_chronology.py` (17 tests)
**Evidence:** [`grand_slam_bo5_chronology_20261127.json`](evidence/grand_slam_bo5_chronology_20261127.json)

## 1. What is already established

- Agent 1 is format-insensitive. Correcting 229 completed AO 2026 history rows
  from BO3 to BO5 changed **zero** of 3,284 Agent 1 probabilities.
- Agent 2 converts through a point → game → set → match Markov model and does
  depend on `best_of_sets`. In the controlled AO 2026 counterfactual, the
  format change moved Agent 2 by `+0.0349056986...` and Manager by exactly one
  third of that, with no second adjustment.
- Therefore the only legitimate places for a BO5 effect are a genuinely
  set-level input, Agent 2's Markov conversion, or a historically fitted
  calibration — never a free "Grand Slam favourite" multiplier.

## 2. The six arms

| Arm | Definition | Evidence class | Fitted on history |
|---|---|---|---|
| A | Existing Agent 1 probability, format-insensitive | `FORMAT_INSENSITIVE` | no |
| B | Exact independent-set transform from a **set-win** probability | `THEORETICAL_SET_LEVEL` | no |
| C | Logistic recalibration of A, fitted on earlier Slams | `HISTORICAL_CALIBRATION` | yes |
| D | Agent 2's existing BO5 Markov forecast | `MARKOV_MODEL` | no |
| E | Logit blend of A and D, weight fitted on earlier Slams | `SPECIALIST_COMBINATION` | yes |
| F | Manager C probability where it exists | `MANAGER_REFERENCE` | no |

B and E are the only arms that can express a format effect. B is exact only if
the input is a set-win probability. E is a fitted specialist combination, not a
second format conversion.

## 3. Probability semantics guard

The published theory is an i.i.d.-set identity:

```text
BO3(p) = p^2 (3 - 2p)
BO5(p) = p^3 (10 - 15p + 6p^2)
```

At `p = 0.60`: BO3 = 0.648, BO5 = 0.68256 (+3.46 points). This is the only
"inflation" allowed, and it is not a fitted constant.

The code **rejects** a match-winning probability passed as a set-winning
probability. Converting one requires `allow_iid_assumption=True`, returns the
label `ASSUMPTION_DEPENDENT`, and is never reported as a theoretical result.
The transform is monotone, fixes 0.5, and is symmetric:
`BO5(1-p) = 1 - BO5(p)`.

## 4. Chronological design

```text
sort all Grand Slam editions by edition_start
for each evaluation edition E after the first four:
    training = every edition with start < E.start
    C  <- logistic recalibration fitted on training labels
    E  <- blend weight fitted on training labels
    evaluate A..F on E
```

Every fold asserts `max(training.start) < test.start`. No test label enters any
fit. Qualifying is a separate draw stage (BO3) and is not merged into the main
draw. Main-draw and qualifying format are distinct by construction.

Metrics: accuracy, Brier, log loss, 10-bin expected calibration error and a
reliability table; coverage per arm; paired cluster bootstrap resampling
**editions** (not matches) for uncertainty.

## 5. Synthetic validation result

The generator is an i.i.d. set model with a known BO5 label, a deliberately
under-confident and slightly biased Agent 1, a noisy serve-level Agent 2, a
set-level source available for 80% of matches, and Manager C available for 60%.
Twelve editions, 90 matches each; eight evaluation editions, 720 evaluation
matches.

| Arm | n | Coverage | Brier | Log loss | Accuracy | ECE |
|---|---:|---:|---:|---:|---:|---:|
| A | 720 | 100.0% | 0.1808 | 0.5391 | 72.9% | 0.0441 |
| B | 563 | 78.2% | **0.1663** | **0.5036** | **75.7%** | 0.0395 |
| C | 720 | 100.0% | 0.1786 | 0.5327 | 72.9% | **0.0286** |
| D | 720 | 100.0% | 0.1763 | 0.5233 | 72.2% | 0.0427 |
| E | 560 | 77.8% | 0.1732 | 0.5134 | 72.9% | 0.0339 |
| F | 437 | 60.7% | 0.1760 | 0.5272 | 71.9% | 0.0593 |

Paired cluster bootstrap versus A (arm minus A; negative is better):

| Arm | Brier Δ (95% CI) | Log loss Δ (95% CI) |
|---|---|---|
| B | −0.0130 (−0.0189, −0.0065) | −0.0318 (−0.0444, −0.0180) |
| C | −0.0022 (−0.0042, −0.0002) | −0.0066 (−0.0125, −0.0018) |
| D | −0.0044 (−0.0082, −0.0007) | −0.0158 (−0.0246, −0.0065) |
| E | −0.0043 (−0.0095, **+0.0012**) | −0.0157 (−0.0294, −0.0027) |
| F | −0.0073 (−0.0106, −0.0042) | −0.0180 (−0.0253, −0.0089) |

The synthetic checks all pass:

```text
B beats A on Brier                         true
C beats A on Brier                         true
D beats A on Brier                         true
E beats A on Brier                         true
B coverage is bounded below 100%           true
A has full coverage                        true
all metrics finite                         true
theoretical transform is not inflation     true
```

The E Brier interval just includes zero. That is reported, not hidden. The
methodology does not claim a specialist combination is always better; it
requires a paired interval that excludes zero before any such claim.

## 6. How to read the synthetic result

- It validates the **machinery**: chronological splits, fitting on earlier
  editions only, coverage accounting, calibration metrics, paired uncertainty,
  and the semantics guard.
- It does **not** estimate a real Grand Slam effect. The data-generating process
  is chosen, not observed. `is_empirical_evidence` is `false` in the evidence.
- The real empirical benchmark remains the published Pronocast result: the ATP
  Slam favourite wins 70.8% against 67.4% expected from BO3 Elo (+3.4 points,
  z = 3.65), while the WTA Slam favourite wins 70.5% against 67.7% (+2.8) over
  three sets. The difference between tours is +0.6 points (95% CI −2.0 to
  +3.1), so the evidence supports a Grand Slam effect first, not a pure format
  effect. The later-round split (+8.7 vs +1.7 points) is post-hoc on 581 matches
  and must not be used as a production rule.

## 7. Real-data blocker

```text
status  BLOCKED_NO_COMMERCIALLY_AUTHORIZED_HISTORICAL_CORPUS
```

Reasons:

1. The largest stored corpus is Sackmann-derived (115,678 rows in the PR #351
   procurement audit) under **CC BY-NC-SA**, which is non-commercial and needs a
   separate commercial licence.
2. No signed commercial licence, vendor export or authorised official historical
   feed is present.
3. The 2016–2026 AO rows are retrospective history, not original pre-match
   forecasts; the original AO forecast cohort is zero.
4. This session has no authorised read-only production database access and makes
   no provider call.

What would unblock a real run:

- a commercially licensed Grand Slam corpus with pre-match probabilities, or the
  inputs needed to recompute them;
- an authorised read-only historical extract under a commercial licence;
- an owner-approved provider probe coordinated with PR #351.

The CLI refuses a dataset whose declared rights are not
`COMMERCIAL_AUTHORIZED` and exits 3 with the blocker recorded. It cannot
accidentally produce a real-data claim from an unlicensed file.

## 8. Coordination with the Agent 2 worker

- D is Agent 2's **existing** forecast. No new Markov experiment, recalibration
  or serve/return feature is introduced here.
- E uses only the existing Agent 1 and Agent 2 outputs and a single constrained
  blend weight in `[0, 1]`. It adds no free intercept and no favourite boost.
- If the Agent 2 worker changes the Markov engine, re-run this harness with the
  new D column; the arms and chronology do not need to change.
- No production probability, publication or Manager decision was modified.

## 9. Decision

**Do not deploy a format adjustment.** The code path is ready for a licensed
re-run, but there is no real-data evidence in this branch that a new adjustment
beats the existing Agent 2 conversion. The correct production posture remains:
Agent 1 unchanged, Agent 2's Markov conversion as the only format effect, and
no arbitrary favourite inflation.
