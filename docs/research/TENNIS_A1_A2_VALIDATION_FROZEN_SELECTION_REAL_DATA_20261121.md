# Brazzy365: validation-frozen A1/A2 selection thresholds — actual historical data, 21 Nov 2026

**Research only. No live Model Picks, Manager, calibration, data records, odds, or production configurations changed. This is NOT an independent untouched or prospective evaluation.**

## Execution identity

- Code: src/sports_edge_tennis/validation/a1_a2_validation_frozen_selection.py (merged research PR #345).
- Git SHA: d0cd2e04f7ba12d9a05ce801ecfb7514926fe95c.
- CI: PostgreSQL Foundation workflow 44863682494, successful.
- Railway pinned one-off deployment: ac09dc66-dd34-41c4-9bad-8c8e6ad892f6. Read-only database session and PostgreSQL session-level write fence; isolated, ephemeral service deleted after collecting results.
- Query as-of: 2026-11-21T03:30:00Z.
- Validation: 2026-10-15T00:00:00Z to strictly before 2026-11-04T00:00:00Z. Earlier outcomes must have a recorded known-at timestamp before the holdout boundary. Holdout begins 2026-11-04T00:00:00Z.
- Dataset SHA-256: 01996ce9e4d229927f823f4b32a030cf3cb8c5593c71d65e00b6e7c019c4349b.
- Run invocation: python -m sports_edge_tennis.validation.a1_a2_validation_frozen_selection --as-of-utc 2026-11-21T03:30:00Z --validation-start-utc 2026-10-15T00:00:00Z --holdout-start-utc 2026-11-04T00:00:00Z, with the existing restricted production DSN set in TENNIS_DATABASE_URL and PGOPTIONS=-c default_transaction_read_only=on.

## Frozen source populations

- 2,361 first-pair source rows. Exclusions: 17 late/invalid original prediction issuances; 4 missing PIT timestamps; 18 unsafe result/winner timelines. Total **39 excluded**.
- **2,322** first co-issued A1/A2 original predictions survive PIT normalization. This is a **paired research population**, not the full ATP/Challenger eligible match universe.
- **664** chronologically earlier validation forecasts with outcomes known before the holdout boundary were used to fit each confidence quantile. Holdout outcomes do not enter threshold fitting.
- **519** holdout original paired forecasts issued, **471 settled** with valid winner and **48 ungraded, including pending/void**. Holdout tour split: **31 ATP and 440 Challenger settled**.
- No market data, A3 or Manager outcomes, competitor predictions, odds, or full eligible match denominator were included.

## No-threshold benchmark: SAME 471 settled matches

| Original model | Winner accuracy | Brier | Log loss |
|---|---:|---:|---:|
| A1 surface Elo | 296W–175L = 62.85% | 0.233895 | 0.664308 |
| A2 Markov | 284W–187L = 60.30% | 0.234373 | 0.658267 |
| Equal log-odds blend | 295W–176L = 62.63% | 0.225625 | 0.641477 |

- The blend **loses one correct winner** relative to A1 on this same holdout, despite better Brier (**−0.008269; ~3.54% relative**) and log loss (**−0.022831; ~3.44% relative**). Do **not** equate better calibration with more winners.
- All-471 same-fixture tournament-cluster 95% CI for Brier improvement A1-minus-blend: **[0.001567, 0.015601]**. Historical sample already inspected elsewhere, conditional uncertainty, no commercial competitor claim.

## Cutoffs FROZEN using only earlier validation confidence distributions

These target fractions refer to the **earlier settled validation cohort**. Applied unchanged to all later issued paired forecasts; achieved coverage differs from the target because of prediction-distribution drift and pending/void inclusion. No holdout outcomes, labels or future confidence quantiles were used to fit cutoffs. Cutoffs must still be prospectively frozen before future live testing.

| Validation target | A1 issued / 519 | A1 settled | A1 accuracy | Equal-blend issued / 519 | Equal-blend settled | Equal-blend accuracy |
|---|---:|---:|---:|---:|---:|---:|
| 25% | 105 (20.23%) | 69W–27L (96) | 71.88% | 103 (19.85%) | 73W–22L (95) | **76.84%** |
| 40% | 184 (35.45%) | 123W–47L (170) | **72.35%** | 194 (37.38%) | 134W–46L (180) | **74.44%** |
| 50% | 236 (45.47%) | 158W–62L (220) | 71.82% | 246 (47.40%) | 161W–66L (227) | **70.93%** |
| 75% | 376 (72.45%) | 227W–123L (350) | 64.86% | 367 (70.71%) | 226W–112L (338) | 66.86% |
| 100% | 519 (100%) | 296W–175L (471) | 62.85% | 519 (100%) | 295W–176L (471) | 62.63% |

- A1 validation-40% confidence threshold: **0.721449**, holding **184/519 issued (35.45%)**, **123W/47L = 72.35% settled**, 14 ungraded.
- Blend validation-40% threshold: **0.6711558673472298**, holding **194/519 issued (37.38%)**, **134W/46L = 74.44% settled**, 14 ungraded.
- Blend validation-50% threshold: **0.6444154722767155**, holding **246/519 issued (47.40%)**, **161W/66L = 70.93% settled**, 19 ungraded.
- Blend validation-25% threshold: **0.7278277980454337**, holding **103/519 issued (19.85%)**, **73W/22L = 76.84% settled**, 8 ungraded.

These are **not 37–47% of all ATP/Challenger eligible fixtures**; they are selections out of the 519 A1/A2 paired issued research fixtures. Different models select different fixtures, so apparent differences in selected-group win rates are **not paired head-to-head accuracy comparisons**.

## Same-selected-match analysis guards against false inference

| Frozen validation fraction | Both A1 & blend selected and settled | A1 wins | Blend wins | Paired A1-minus-blend Brier improvement and 95% cluster CI |
|---|---:|---:|---:|---|
| 25% | 64 | 48 | 48 | +0.013953 [0.000137, 0.031041] |
| 40% | 128 | 99 | 99 | +0.002862 [−0.007725, 0.013748] |
| 50% | 180 | 131 | 132 | +0.010398 [0.002864, 0.017968] |
| 75% | 301 | 204 | 206 | +0.009870 [0.003719, 0.016356] |
| 100% | 471 | 296 | 295 | +0.008269 [0.001567, 0.015601] |

At the promising validation-40% criterion **A1 and the blend identified exactly the same number of winners (99/128) on their overlapping selections**. The higher blend selected-group accuracy is not proof that blend probabilities independently improve winner selection at equal true fixture coverage. Confidence intervals are conditional on the particular historical tournament cohort; repeated players, multiple comparisons, previously seen holdout dates and class imbalance remain concerns.

## Scientific verdict and next priorities

- **The 40% validation-quantile blend has a promising descriptive 74.44% settled accuracy at 37.38% of *paired issued* coverage.** No conclusion of sustainability at 40–50% of total eligible ATP+Challenger matches, or competitive odds, is warranted. Small ATP subgroup (31), 180 settled selected and historical holdout reuse preclude approval for production. The 50% validation target fell to 70.93% on 227 settled selections.
- **Calibration and hit rate diverge** on 471 matched fixtures: equal blend improves Brier/log loss but loses one correct winner versus A1. Research should distinguish probability-calibration gains from winner selection gains and avoid manipulating thresholds.
- **Future priority:** preserve existing live picks; freeze research model/threshold and evaluate prospectively for at least 30 days and 200 unique settled original selections per candidate, with all eligible fixtures in the denominator. Use same-match, same-cutoff comparisons and ATP/Challenger stratification with adequate samples.
- **Other P0 priorities remain:** provenance/possible leakage in the earlier claimed ~14,000-match/75% historical benchmark; full available-fixture denominator; true A3/Manager A–E and 26→7→1 settlement/publication funnel; real same-issue bookmaker quote coverage; authorised, timestamped competitor comparisons; commercial licences for serve/return data. No cost or API purchase authorized by this experiment.
- **Operational:** one-off Railway execution deleted after logs were retained; no live prediction-policy updates or permanent new infrastructure.

Related: PR #343 (read-only paired model benchmark), PR #344 (first real-data independence report), PR #345 (chronological threshold-freeze code and tests), https://trello.com/c/jvlNgP4E.
