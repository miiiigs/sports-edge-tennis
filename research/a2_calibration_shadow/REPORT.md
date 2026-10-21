# Agent 2 Point-in-Time Probability Calibration Shadow - Research Report

- **Experiment:** `a2_calibration_shadow_20261021`
- **Draft research PR:** https://github.com/miiiigs/sports-edge-tennis/pull/248
- **Branch:** `research/tennis-agent2-calibration-shadow-20261021`
- **Starting `origin/main` SHA:** `b6b7e506687172a0b08441bb006013d131a19fcd`
- **Approved comparator:** draft research PR #239, head
  `eec591921367cd81736e9feba1b52011657f1512`
- **Frozen dataset SHA-256:**
  `5dceb73fdfda3ae247e72ae2a1f98ac7e63069a0a49743b07a9297b39eb6b1d5`
- **Scope:** isolated offline research and draft PR only. No production model
  change, no Manager threshold change, no A1/A2 reweighting, no betting-logic
  change, no schema change, no database write, no merge, no deployment.

## Recommendation

**Select temperature scaling as the best-supported calibration method, but use
it only in a forward shadow. Hold production activation.**

- Temperature scaling with slope `0.643925` (refit on 2019-2022) improves the
  frozen 2023-2025 test probability quality:
  - Brier `0.235360 -> 0.233838` (paired delta `-0.001521`, 95% CI
    `-0.001996` to `-0.001049`);
  - log loss `0.665448 -> 0.659738` (paired delta `-0.005710`, 95% CI
    `-0.007026` to `-0.004436`);
  - ECE `0.029271 -> 0.015328` (paired delta `-0.013943`, 95% CI
    `-0.023061` to `-0.005049`).
- Directional accuracy, selected winners, ranking and raw prediction coverage
  are unchanged by construction and by measurement.
- The improvement is real but modest in Brier/log-loss terms and concentrated
  in Challengers, thin-history players, grass and the return-from-inactivity
  cohort. ATP Tour and Masters 1000 do not show statistically clear Brier
  gains.
- High-confidence display coverage falls substantially. At a 0.80 display gate
  the count falls from 3,172 to 926 (about -71%); at 0.90 it falls from 874 to
  197 (about -77%). Production gate thresholds must not be changed on this
  evidence.
- Manager gate eligibility cannot be fully evaluated because the frozen A2
  record has no historical A1 or A3 predictions. The A2 component never falls
  below the 0.5 floor, but the three-agent spread can move by up to `0.0972`,
  which is larger than the 0.08 gate. A full three-agent historical replay is
  required before any production decision.
- Reject isotonic, Platt and piecewise-linear calibrators as production
  recommendations. They are marginally better on the test split but add
  parameters and tail instability without a material validation advantage.
  They may be revisited only in a pre-registered forward shadow.
- A global layer is sufficient for now. Group-specific temperature scaling
  improves overall Brier by only `0.000045` to `0.000068` with confidence
  intervals that include zero. Conditional surface/tier calibration is a
  future research question, not a current recommendation.

**Decision: ACCEPT FOR FORWARD SHADOW; HOLD PRODUCTION.**

---

## 1. Question and pre-registered decision rule

### Question

Can a strictly point-in-time calibration layer improve Agent 2's probability
honesty on unseen data without weakening accuracy, ranking or selection
coverage?

### Pre-registered selection protocol

The protocol was frozen before the test split was evaluated. The selection code
never receives test outcomes.

1. Fit each candidate on 2019-2020 and score 2021.
2. Fit each candidate on 2019-2021 and score 2022.
3. Pool the 2021 and 2022 observations (`n = 19,873`).
4. Choose the candidate with the lowest pooled log loss.
5. A candidate within `0.0002` pooled log loss of the best is considered tied.
   Among tied candidates, prefer the lowest complexity rank.
6. The selected candidate must also improve pooled Brier and ECE over identity.
7. Only then refit the selected candidate on 2019-2022 and evaluate the frozen
   2023-2025 test population once.

The margin `0.0002` is a minimum material improvement for added complexity, not
a test-derived tuning parameter. Under this rule, temperature is within
`0.000126` of the best validation log loss and is the simplest candidate.

### Candidate complexity order

`identity` < `temperature` < `platt` < `piecewise_linear` < `isotonic_500` <
`isotonic_200` < `isotonic_100` < `isotonic_50`.

### Why these methods

- **Temperature scaling** has one parameter, is strictly monotone, preserves
  the selected side, and is the least data-hungry correction for global
  overconfidence.
- **Platt scaling** adds an intercept. It can represent asymmetric
  miscalibration but introduces a player-side bias in the symmetric
  player-1/player-2 representation.
- **Isotonic regression** is flexible but produces many blocks, is weaker in the
  sparse high-confidence tail, and is more sample-sensitive.
- **Piecewise linear** is a smoother low-parameter nonparametric compromise but
  still adds six parameters and a tail extrapolation choice.
- **Identity** is the no-change control.

---

## 2. Provenance and starting SHA

| Item | Value |
| --- | --- |
| Starting `origin/main` SHA | `b6b7e506687172a0b08441bb006013d131a19fcd` |
| Reference PR #239 head | `eec591921367cd81736e9feba1b52011657f1512` |
| PR #239 harness code SHA | `9392752` |
| Frozen dataset SHA-256 | `5dceb73fdfda3ae247e72ae2a1f98ac7e63069a0a49743b07a9297b39eb6b1d5` |
| Frozen prediction-record SHA-256 | `7fbf4575339b14bfd58c22308f0495ea32b4d8d033e7208c595c581f77365e63` |
| Test prediction-population SHA-256 | `af995c66c2369fd98e7393f21fe39182f2624ce84bc47ededbc468e19b228482` |
| Test eligible-population SHA-256 | `8742ffe3561b7faba304ac0df01b2bd615ce192d64a5f415416d2b1f5d74f859` |
| Frozen dataset cutoff | `2026-01-01T00:00:00+00:00` |
| Source database access | read-only, `default_transaction_read_only = on` |

The frozen dataset SHA and row counts match PR #239. The baseline test metrics
reproduce PR #239 exactly (Brier, log loss, ECE and accuracy all within `1e-6`;
the maximum absolute difference was `2.8e-17` for ECE).

### What was not done

- Agent 2 was not retrained.
- The production `Agent2Config` was not changed.
- No production prediction function, Manager rule, A1/A2 weight, market logic,
  stake, settlement or threshold was modified.
- No bookmaker price was used.
- No post-issuance price or ROI claim is made.

---

## 3. Exact chronological populations

The frozen records are tournament-cohort replays. They are not genuine
forward-issued Model Picks: all matches in one tournament edition share one
prediction cohort, and results become visible at the tournament-end boundary.
This is the same limitation as the approved PR #239 comparator.

| Population | Seasons | Predicted | Eligible | Coverage | First cohort | Last cohort | Use |
| --- | --- | ---: | ---: | ---: | --- | --- | --- |
| Calibration fit | 2019-2021 | 23,468 | 24,985 | 0.939 | 2019-01-13T23:59:59Z | 2021-12-19T23:59:59Z | 2022 rolling fit |
| Calibration fit | 2019-2022 | 34,433 | 37,131 | 0.927 | 2019-01-13T23:59:59Z | 2022-12-04T23:59:59Z | Final refit |
| Validation | 2021 | 8,908 | 9,632 | 0.925 | 2021-01-17T23:59:59Z | 2021-12-19T23:59:59Z | Rolling selection |
| Validation | 2022 | 10,965 | 12,146 | 0.903 | 2022-01-09T23:59:59Z | 2022-12-04T23:59:59Z | Rolling selection |
| Test | 2023-2025 | 36,512 | 40,040 | 0.912 | 2023-01-07T23:59:59Z | 2025-11-30T23:59:59Z | Untouched holdout |

Test season composition:

| Season | Eligible | Predicted | Abstained |
| --- | ---: | ---: | ---: |
| 2023 | 12,942 | 11,865 | 1,077 |
| 2024 | 13,682 | 12,414 | 1,268 |
| 2025 | 13,416 | 12,233 | 1,183 |
| **Total** | **40,040** | **36,512** | **3,528** |

Test abstract composition (predicted population):

- Surface: HARD 18,709; CLAY 15,812; GRASS 1,945; CARPET 46.
- Tier: CHALLENGER 24,917; ATP_TOUR 6,007; GRAND_SLAM 2,751; MASTERS_1000 2,837.
- Tour: CHALLENGER 24,917; ATP_MAIN 11,595.

The test population and eligibility are unchanged by calibration. The
calibrator cannot add or remove a prediction; it only changes its probability.

### Burn-in calibration training gap

PR #239 recorded predictions only for the validation and test splits. The
burn-in seasons 2016-2018 were used only as model history and have no frozen
per-match prediction records. This is not a leakage problem, because the
experiment does not need them: 23,468 calibration observations from 2019-2021
and 19,873 independent validation observations from 2021-2022 are sufficient
for one-parameter temperature scaling.

If a future experiment needs burn-in calibration evidence, the leakage-safe
plan is:

1. Use the frozen dataset SHA above and the PR #239 harness at code SHA
   `9392752`.
2. Add a research-only option to record predictions for the `burn_in` split.
3. Replay the deterministic model without changing `Agent2Config`.
4. Fit only on 2016-2018, validate on 2019-2022, and keep 2023-2025 sealed.
5. Record the new prediction-record SHA and never combine burn-in predictions
   with forward-issued Model Picks.

---

## 4. Baseline and candidate metrics

### 4.1 Validation selection (2019-2021 fit, 2021-2022 evaluation)

Pooled validation population: `n = 19,873` (2021: 8,908; 2022: 10,965).
A negative delta is an improvement for Brier, log loss and ECE. Accuracy is
unchanged for every candidate.

| Candidate | Rank | Brier | Log loss | ECE | Accuracy | Delta log loss vs identity | Delta Brier vs identity | Delta ECE vs identity |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| identity | 0 | 0.237490 | 0.670143 | 0.038758 | 0.598601 | 0.000000 | 0.000000 | 0.000000 |
| temperature | 1 | 0.234961 | 0.662062 | 0.008439 | 0.598601 | -0.008081 | -0.002529 | -0.030319 |
| platt | 2 | 0.234934 | 0.661982 | 0.005743 | 0.598601 | -0.008162 | -0.002556 | -0.033015 |
| piecewise_linear | 3 | 0.234904 | 0.661936 | 0.003109 | 0.598601 | -0.008207 | -0.002586 | -0.035649 |
| isotonic_500 | 4 | 0.234986 | 0.662046 | 0.004120 | 0.598601 | -0.008098 | -0.002505 | -0.034638 |
| isotonic_200 | 5 | 0.235003 | 0.662070 | 0.006088 | 0.598601 | -0.008073 | -0.002487 | -0.032670 |
| isotonic_100 | 6 | 0.235040 | 0.662297 | 0.006154 | 0.598601 | -0.007847 | -0.002450 | -0.032604 |
| isotonic_50 | 7 | 0.234975 | 0.662258 | 0.007091 | 0.598601 | -0.007886 | -0.002515 | -0.031667 |

Temperature's pooled validation deltas versus identity:

- Brier: `-0.002529` (95% CI `-0.003111` to `-0.002028`)
- Log loss: `-0.008081` (95% CI `-0.009545` to `-0.006254`)
- ECE: `-0.030319` (95% CI `-0.036398` to `-0.020294`)
- Accuracy: `0.000000` (95% CI `0.000000` to `0.000000`)

Piecewise linear had the lowest pooled validation log loss (`0.661936`).
Temperature's `0.662062` is `0.000126` worse, inside the `0.0002` materiality
margin, so the pre-registered tie-break selects the simpler temperature model.
The same rule rejects Platt (two parameters), isotonic (tail instability and
more blocks) and piecewise linear (six parameters).

### 4.2 Final test (2019-2022 fit, 2023-2025 evaluation)

Selected method: temperature scaling, slope `0.6439254782294896`.
Test population: `n = 36,512`.

| Metric | Baseline | Baseline 95% CI | Temperature | Temperature 95% CI | Paired delta | Paired 95% CI |
| --- | ---: | --- | ---: | --- | ---: | --- |
| Accuracy | 0.604048 | 0.598953 to 0.609280 | 0.604048 | 0.598953 to 0.609280 | 0.000000 | 0.000000 to 0.000000 |
| Brier | 0.235360 | 0.233899 to 0.236888 | 0.233838 | 0.232793 to 0.234977 | -0.001521 | -0.001996 to -0.001049 |
| Log loss | 0.665448 | 0.661948 to 0.669200 | 0.659738 | 0.657494 to 0.662188 | -0.005710 | -0.007026 to -0.004436 |
| ECE | 0.029271 | 0.025197 to 0.034522 | 0.015328 | 0.011190 to 0.021074 | -0.013943 | -0.023061 to -0.005049 |

Relative changes:

- Brier: `-0.65%`.
- Log loss: `-0.86%`.
- ECE: `-47.6%`.
- Accuracy: `0.00%`.

Cox calibration:

| Method | Intercept | Slope |
| --- | ---: | ---: |
| Baseline | 0.078691 | 0.599106 |
| Temperature | 0.078691 | 0.930396 |

The baseline slope of `0.599` is the signature of strong overconfidence. The
calibrated slope of `0.930` is close to the ideal `1.0`. The intercept is
unchanged because temperature scaling preserves the logit centre.

### 4.3 Stability and season results

Fit on 2019-2021 only (no 2022) gives slope `0.645509` and essentially the same
test result:

| Metric | Baseline | Temperature (2019-2021 fit) | Paired delta | Paired 95% CI |
| --- | ---: | ---: | ---: | --- |
| Brier | 0.235360 | 0.233834 | -0.001525 | -0.001999 to -0.001056 |
| Log loss | 0.665448 | 0.659731 | -0.005716 | -0.007028 to -0.004448 |
| ECE | 0.029271 | 0.015199 | -0.014072 | -0.023231 to -0.005367 |
| Accuracy | 0.604048 | 0.604048 | 0.000000 | 0.000000 to 0.000000 |

Season-level test results:

| Season | n | Brier delta | Brier 95% CI | Log loss delta | Log loss 95% CI | ECE delta | ECE 95% CI |
| --- | ---: | ---: | --- | ---: | --- | ---: | --- |
| 2023 | 11,865 | -0.001357 | -0.002185 to -0.000538 | -0.005104 | -0.007373 to -0.002835 | -0.012615 | -0.026573 to +0.000967 |
| 2024 | 12,414 | -0.000925 | -0.001694 to -0.000102 | -0.003997 | -0.006128 to -0.001741 | -0.008575 | -0.019937 to +0.005821 |
| 2025 | 12,233 | -0.002285 | -0.003153 to -0.001478 | -0.008035 | -0.010466 to -0.005771 | -0.025313 | -0.037076 to -0.011740 |

Every season improves Brier and log loss with a paired interval excluding zero.
The ECE improvement is directionally positive in every season but is not
individually significant in 2023 or 2024. The 2025 effect is the largest.

---

## 5. Reliability tables

### 5.1 Cumulative confidence bands (test)

| Method | Band | n | Mean predicted | Observed | Gap | 95% Wilson |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| Baseline | >= 0.60 | 19,465 | 0.7079 | 0.6576 | -0.0503 | 0.6509 to 0.6642 |
| Baseline | >= 0.70 | 8,368 | 0.7914 | 0.7187 | -0.0727 | 0.7090 to 0.7282 |
| Baseline | >= 0.80 | 3,172 | 0.8705 | 0.7673 | -0.1031 | 0.7523 to 0.7817 |
| Baseline | >= 0.90 | 874 | 0.9433 | 0.8444 | -0.0989 | 0.8189 to 0.8669 |
| Temperature | >= 0.60 | 12,788 | 0.6765 | 0.6860 | +0.0095 | 0.6779 to 0.6939 |
| Temperature | >= 0.70 | 3,561 | 0.7719 | 0.7588 | -0.0131 | 0.7444 to 0.7725 |
| Temperature | >= 0.80 | 926 | 0.8616 | 0.8402 | -0.0215 | 0.8152 to 0.8624 |
| Temperature | >= 0.90 | 197 | 0.9331 | 0.8782 | -0.0550 | 0.8251 to 0.9167 |

The cumulative bands after calibration are different populations: a match that
was above 0.90 under the baseline is not necessarily above 0.90 after
calibration. The same-cohort view is in section 9.

### 5.2 Five-point bins (test)

| Method | Bin | n | Mean predicted | Observed | Gap |
| --- | --- | ---: | ---: | ---: | ---: |
| Baseline | 0.50-0.55 | 8,978 | 0.5248 | 0.5186 | -0.0062 |
| Baseline | 0.55-0.60 | 8,069 | 0.5742 | 0.5700 | -0.0042 |
| Baseline | 0.60-0.65 | 6,428 | 0.6239 | 0.6013 | -0.0227 |
| Baseline | 0.65-0.70 | 4,669 | 0.6736 | 0.6256 | -0.0480 |
| Baseline | 0.70-0.75 | 3,132 | 0.7235 | 0.6858 | -0.0377 |
| Baseline | 0.75-0.80 | 2,064 | 0.7730 | 0.6938 | -0.0792 |
| Baseline | 0.80-0.85 | 1,404 | 0.8233 | 0.7336 | -0.0897 |
| Baseline | 0.85-0.90 | 894 | 0.8733 | 0.7450 | -0.1283 |
| Baseline | 0.90-0.95 | 522 | 0.9240 | 0.8372 | -0.0868 |
| Baseline | 0.95-1.00 | 352 | 0.9720 | 0.8551 | -0.1169 |
| Temperature | 0.50-0.55 | 13,587 | 0.5245 | 0.5336 | +0.0091 |
| Temperature | 0.55-0.60 | 10,137 | 0.5733 | 0.5951 | +0.0219 |
| Temperature | 0.60-0.65 | 6,040 | 0.6224 | 0.6389 | +0.0165 |
| Temperature | 0.65-0.70 | 3,187 | 0.6724 | 0.6938 | +0.0214 |
| Temperature | 0.70-0.75 | 1,713 | 0.7228 | 0.7227 | -0.0001 |
| Temperature | 0.75-0.80 | 922 | 0.7728 | 0.7440 | -0.0288 |
| Temperature | 0.80-0.85 | 456 | 0.8238 | 0.8224 | -0.0014 |
| Temperature | 0.85-0.90 | 273 | 0.8732 | 0.8425 | -0.0307 |
| Temperature | 0.90-0.95 | 148 | 0.9217 | 0.8649 | -0.0569 |
| Temperature | 0.95-1.00 | 49 | 0.9676 | 0.9184 | -0.0492 |

Temperature removes most of the mid-range overconfidence but leaves a residual
high-confidence tail gap: the calibrated 0.90-0.95 bin predicts `0.9217` and
observes `0.8649` (gap `-0.0569`). This is the main remaining calibration
weakness and is the strongest argument for future tail-specific or conditional
research.

### 5.3 Reproducible chart data

- `artifacts/calibration_curves.json` - reliability points for validation 2022
  and test.
- `artifacts/reliability_test.csv` - long-form reliability table.
- `artifacts/calibration_reliability.svg` and `.png` - reliability diagrams.
- `artifacts/tables.md` - all generated tables.

---

## 6. Statistical uncertainty and sample sizes

- Test predictions: `36,512`; paired bootstrap resampling is match-level with
  `1,000` replications and seed `20261021`.
- All paired intervals exclude zero for Brier, log loss and ECE. Accuracy is
  exactly unchanged, so its paired interval is `[0, 0]`.
- The overall test intervals are tight: Brier baseline `0.233899-0.236888`,
  calibrated `0.232793-0.234977`.
- Season intervals are wider but all Brier and log-loss intervals exclude zero.
- Surface/tier intervals are wider still. CARPET (`n = 46`) is too small to
  support any conclusion. GRASS (`n = 1,945`) has a clear Brier/ECE improvement
  but is a small and distinctive surface.
- ATP_TOUR (`n = 6,007`) and MASTERS_1000 (`n = 2,837`) have confidence
  intervals that include zero for Brier and ECE. The aggregate gain is driven
  by CHALLENGER (`n = 24,917`).
- The 0.90 cumulative band has only `197` calibrated predictions. Its observed
  rate (`0.8782`) has a Wilson interval of `0.8251-0.9167`. Do not over-read the
  tail.
- Multiple metrics and subgroups were examined. The overall Brier/log-loss
  result is large, consistent across seasons and supported by a clear Cox slope
  change. Subgroup differences are exploratory.

---

## 7. Subgroup and conditional assessment

### 7.1 Surface

| Surface | n | Baseline Brier | Temperature Brier | Delta Brier | 95% CI | Baseline ECE | Temperature ECE | Delta ECE |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| HARD | 18,709 | 0.2354 | 0.2339 | -0.001498 | -0.002206 to -0.000787 | 0.0293 | 0.0161 | -0.013184 |
| CLAY | 15,812 | 0.2344 | 0.2334 | -0.000979 | -0.001674 to -0.000243 | 0.0270 | 0.0212 | -0.005757 (CI includes zero) |
| GRASS | 1,945 | 0.2431 | 0.2370 | -0.006052 | -0.008500 to -0.003904 | 0.0791 | 0.0320 | -0.047081 |
| CARPET | 46 | 0.2365 | 0.2308 | -0.005667 | -0.019749 to +0.007940 | 0.1976 | 0.1697 | -0.027874 (small sample) |

### 7.2 Competition tier

| Tier | n | Baseline Brier | Temperature Brier | Delta Brier | 95% CI | Baseline ECE | Temperature ECE | Delta ECE | 95% CI |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- |
| CHALLENGER | 24,917 | 0.2361 | 0.2341 | -0.002036 | -0.002635 to -0.001514 | 0.0320 | 0.0164 | -0.015649 | -0.025919 to -0.007005 |
| ATP_TOUR | 6,007 | 0.2357 | 0.2356 | -0.000161 | -0.001293 to +0.000973 | 0.0179 | 0.0226 | +0.004641 | -0.015360 to +0.019431 |
| GRAND_SLAM | 2,751 | 0.2292 | 0.2275 | -0.001729 | -0.003667 to +0.000248 | 0.0398 | 0.0144 | -0.025433 | -0.036570 to +0.000428 |
| MASTERS_1000 | 2,837 | 0.2339 | 0.2342 | +0.000324 | -0.001130 to +0.001759 | 0.0267 | 0.0186 | -0.008132 | -0.026975 to +0.014237 |

The ATP Tour and Masters 1000 results are the main caution. ATP Tour ECE moves
slightly in the wrong direction, and Masters 1000 Brier moves slightly in the
wrong direction; both intervals include zero. A global layer fitted mostly on
Challengers is not demonstrably optimal for the ATP main tour.

### 7.3 Tour, season, history and inactivity

| Dimension | Group | n | Delta Brier | 95% CI | Delta ECE |
| --- | --- | ---: | ---: | --- | ---: |
| Tour | CHALLENGER | 24,917 | -0.002036 | -0.002635 to -0.001514 | -0.015649 |
| Tour | ATP_MAIN | 11,595 | -0.000415 | -0.001240 to +0.000430 | -0.006182 |
| Season | 2023 | 11,865 | -0.001357 | -0.002185 to -0.000538 | -0.012615 |
| Season | 2024 | 12,414 | -0.000925 | -0.001694 to -0.000102 | -0.008575 |
| Season | 2025 | 12,233 | -0.002285 | -0.003153 to -0.001478 | -0.025313 |
| History | 5-9 prior | 2,075 | -0.006099 | -0.008790 to -0.003660 | -0.040137 |
| History | 10-19 prior | 2,938 | -0.006491 | -0.008480 to -0.004430 | -0.046047 |
| History | 20-49 prior | 6,247 | -0.001912 | -0.003340 to -0.000730 | -0.016378 |
| History | 50+ prior | 25,252 | -0.000470 | -0.000980 to +0.000020 | -0.003740 |
| Inactivity | return >= 180d | 1,305 | -0.004850 | -0.008230 to -0.001630 | -0.034670 |
| Inactivity | active < 180d | 33,819 | -0.001425 | -0.001990 to -0.000940 | -0.012950 |
| Draw | qualifying | 14,199 | -0.002280 | -0.003110 to -0.001480 | -0.022880 |
| Draw | main draw | 22,313 | -0.001040 | -0.001610 to -0.000410 | -0.009650 |

The calibrated layer helps most where the baseline is most overconfident:
thin-history players, returning players and grass. It provides little
measurable Brier gain for players with 50+ prior matches, whose baseline
calibration was already relatively good (`ECE 0.0206`).

### 7.4 Conditional temperature scaling (exploratory)

Group-specific temperature slopes were fitted on 2019-2022 and evaluated on the
test split. This is exploratory and was not used for selection. Groups with
fewer than 500 training rows fall back to the global slope.

| Scheme | Global Brier | Conditional Brier | Delta vs global | 95% CI | Global ECE | Conditional ECE | Delta ECE | 95% CI |
| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- |
| Tier-specific | 0.233838 | 0.233794 | -0.000045 | -0.000134 to +0.000041 | 0.015328 | 0.014915 | -0.000413 | -0.001637 to +0.000731 |
| Surface-specific | 0.233838 | 0.233771 | -0.000068 | -0.000146 to +0.000004 | 0.015328 | 0.015036 | -0.000292 | -0.001580 to +0.000282 |
| Tour-specific | 0.233838 | 0.233810 | -0.000028 | -0.000105 to +0.000046 | 0.015328 | 0.015557 | +0.000229 | -0.001186 to +0.001512 |

Fitted tier slopes: ATP_TOUR `0.740`, CHALLENGER `0.600`, GRAND_SLAM `0.671`,
MASTERS_1000 `0.776`. Fitted surface slopes: CLAY `0.652`, GRASS `0.488`,
HARD `0.666`. The ATP Tour and Masters groups would prefer less shrinkage than
Challengers, but the overall conditional gain is not material and every
overall interval includes zero.

**Conclusion:** a global temperature layer is sufficient for the current
evidence. Surface/tier-specific calibration is a legitimate future research
question, especially for grass and the ATP main tour, but it should not be
introduced now.

---

## 8. Coverage, threshold crossings and selection impact

### 8.1 Raw prediction coverage

| Item | Value |
| --- | ---: |
| Test eligible matches | 40,040 |
| Baseline predicted | 36,512 |
| Temperature predicted | 36,512 |
| Baseline abstained | 3,528 |
| Temperature abstained | 3,528 |
| Raw prediction coverage | 0.911888 |
| Coverage change | 0.000000 |

Calibration cannot change eligibility or abstention because it is applied after
a prediction exists.

### 8.2 Display-threshold crossings

Counts are over the 36,512 predicted test matches. "Share" is the share of
predictions. The equivalent threshold is the calibrated threshold that would
retain the baseline count.

| Threshold | Baseline n | Temperature n | Delta | Baseline share | Temperature share | Calibrated threshold preserving baseline n |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.60 | 19,465 | 12,788 | -6,677 | 0.5331 | 0.3502 | 0.5649 |
| 0.70 | 8,368 | 3,561 | -4,807 | 0.2292 | 0.0975 | 0.6331 |
| 0.80 | 3,172 | 926 | -2,246 | 0.0869 | 0.0254 | 0.7094 |
| 0.90 | 874 | 197 | -677 | 0.0239 | 0.0054 | 0.8045 |

If coverage is expressed over all 40,040 eligible test matches:

| Threshold | Baseline share of eligible | Temperature share of eligible |
| ---: | ---: | ---: |
| 0.60 | 0.4861 | 0.3194 |
| 0.70 | 0.2090 | 0.0889 |
| 0.80 | 0.0792 | 0.0231 |
| 0.90 | 0.0218 | 0.0049 |

This is the central product trade-off. If the product continues to display only
matches above a raw 0.80 or 0.90 threshold, calibrated coverage collapses. If
the product wants to preserve the baseline volume, it must lower the display
threshold to approximately `0.709` for a former 0.80 gate and `0.805` for a
former 0.90 gate. That is a product decision, not a model change, and it must
not be made without a forward shadow.

### 8.3 Selected winners and ranking

| Item | Value |
| --- | ---: |
| Selected-side changes | 0 |
| Pairwise ranking inversions | 0 |
| Spearman rank correlation | 1.000000 |
| Unique baseline probability values | 34,329 |
| Unique calibrated probability values | 34,329 |
| Accuracy change | 0.000000 |

Temperature is strictly monotone and never crosses 0.5, so every selected
winner, every within-match ranking and every cross-match ordering is preserved.
Calibration changes probability honesty, not directional decisions.

### 8.4 Manager gate impact

The production Manager policy uses a `0.5` individual-agent probability floor
and a `0.08` three-agent spread gate (or `0.06` when Agent 3 abstains). The
frozen A2 record does not contain historical A1 or A3 predictions, so the
three-agent spread and consensus cannot be reconstructed.

| Item | Value |
| --- | ---: |
| Manager A2 probability floor | 0.500000 |
| Calibrated A2 probabilities below floor | 0 |
| Mean A2 probability change | -0.042048 |
| Median change | -0.037924 |
| 95th percentile absolute change | 0.093535 |
| Maximum absolute change | 0.097220 |
| Consensus mean shift if A1/A3 unchanged | -0.014016 |
| Spread change bound if A1/A3 unchanged | 0.097220 |
| Full three-agent gate evaluable | no |

The A2 floor is never newly violated. However, the worst-case A2 change
(`0.0972`) is larger than the 0.08 spread gate. Depending on whether A2 was the
minimum or maximum agent, a calibrated A2 could either narrow or widen the
three-agent spread. **Manager gate eligibility could change, and this record
cannot quantify it.** A full three-agent historical replay is required before
production integration.

### 8.5 Apparent model edge

No historical bookmaker prices are used, and no ROI or CLV claim is made. At a
fixed decimal price `d`, the apparent edge `p * d - 1` changes by
`(p_calibrated - p_baseline) * d`. With a mean A2 change of `-0.0420` and a
maximum change of `-0.0972`, apparent edges shrink at every fixed price. Fair
odds (`1 / p`) lengthen correspondingly. This is an expected consequence of
removing overconfidence, not evidence of betting profitability.

### 8.6 New abstention thresholds

No new abstention threshold is required for raw predictions, whose coverage is
unchanged. New display or gating thresholds are required only if the product
wants to preserve the old high-confidence volume. The equivalent thresholds in
section 8.2 are descriptive, not recommendations.

---

## 9. Failure analysis: high-confidence losses

### 9.1 Same-cohort view

The following table keeps the baseline high-confidence cohort fixed and asks
what the calibrated probability would have been for those same matches.

| Baseline band | n | Wins | Losses | Observed | Baseline mean | Temperature mean | Baseline gap | Temperature gap |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| >= 0.60 | 19,465 | 12,800 | 6,665 | 0.6576 | 0.7079 | 0.6439 | -0.0503 | +0.0137 |
| >= 0.70 | 8,368 | 6,014 | 2,354 | 0.7187 | 0.7914 | 0.7087 | -0.0727 | +0.0100 |
| >= 0.80 | 3,172 | 2,434 | 738 | 0.7673 | 0.8705 | 0.7801 | -0.1031 | -0.0128 |
| >= 0.90 | 874 | 738 | 136 | 0.8444 | 0.9433 | 0.8652 | -0.0989 | -0.0208 |

Calibration nearly closes the aggregate gap in each baseline cohort. It does
not change a single loss into a win.

### 9.2 Surprise losses and remaining tail

- There are `136` losses in the baseline `>= 0.90` cohort, `15.56%` of that
  cohort.
- Their mean baseline probability was `0.9403`; the calibrated mean is
  `0.8595`.
- Across all `14,457` test losses, the mean baseline probability was `0.6135`
  and the calibrated mean is `0.5765`.
- The calibrated `>= 0.90` band has only `197` matches. Its mean prediction is
  `0.9331`, observed win rate `0.8782`, gap `-0.0550` (Wilson `0.8251-0.9167`).
  The calibrated 0.90-0.95 bin predicts `0.9217` and observes `0.8649`.

So calibration substantially improves probability honesty but does not fully
repair the extreme tail. The residual tail gap is evidence that a global
one-parameter transform is not a complete solution.

### 9.3 Separation of effects

| Effect | Finding |
| --- | --- |
| Probability honesty | Improved: Brier, log loss and ECE all improve with paired intervals excluding zero. |
| Selection decisions | No winner changes. Manager gate and display-gate decisions could change; the Manager effect is not fully evaluable. |
| Genuine ranking or directional accuracy | No improvement. Accuracy is identical to baseline. No directional claim is made. |
| Remaining model needs | The 136 high-confidence losses and the residual 0.90+ tail require better underlying features or data, not just a post-hoc probability map. |

The improvement is concentrated in thin-history and returning players
(Brier deltas `-0.0061` and `-0.0049`), which suggests that part of the observed
overconfidence is an uncertainty/sample-size problem. Calibration corrects the
symptom; it does not add information about injuries, fatigue, schedule, travel,
surface-specific form, matchup structure or identity resolution.

---

## 10. Leakage checks and integrity

| Check | Result |
| --- | --- |
| Fit years versus test years | 2019-2022 versus 2023-2025; disjoint |
| Test rows in fit | 0 |
| Test outcomes used for selection | no |
| Selection file certifies unused test | yes |
| Duplicate match IDs | 0 |
| Baseline reproduces PR #239 | yes; all five checks pass |
| Baseline Brier absolute difference | 0.0 |
| Baseline log-loss absolute difference | 0.0 |
| Baseline ECE absolute difference | 2.8e-17 |
| Baseline accuracy absolute difference | 0.0 |
| Baseline predicted-count difference | 0 |
| Monotone mapping grid | 501 points; nondecreasing; all values in [0.5, 1.0] |
| Selection-side changes | 0 |
| Frozen dataset SHA matches PR #239 | yes |
| Test prediction-population SHA | `af995c66c2369fd98e7393f21fe39182f2624ce84bc47ededbc468e19b228482` |

The selection code path receives only 2019-2021 rows. A regression test flips
every test outcome and asserts that all candidate validation metrics and the
selected method are unchanged. The test split was not used to fit, tune or
choose the calibrator.

One caveat: the test outcomes were known from the approved PR #239 comparator
before this experiment began. The test split is therefore not globally pristine
in the sense of a never-before-seen dataset. It is pristine with respect to
calibration fitting and candidate selection, which is the relevant leakage
boundary for this question. The forward shadow is still required for genuinely
unseen, forward-issued evidence.

---

## 11. Best-supported method and decision

### Best-supported method

**Temperature scaling on the selected-side probability:**

```text
p_calibrated = sigmoid(0.6439254782294896 * logit(p_raw))
```

For the player-1/player-2 representation, use the symmetric extension:

```text
if p1_raw >= 0.5: p1_calibrated = f(p1_raw)
else:             p1_calibrated = 1 - f(1 - p1_raw)
```

This preserves `p1 + p2 = 1`, never changes the selected side, and preserves
ranking.

### Why temperature wins

- One parameter; easy to audit and monitor.
- Best validation result among the one-parameter candidates.
- Within `0.000126` pooled log loss of the best candidate, inside the
  pre-registered materiality margin.
- Stable slopes across fit windows: `0.645509` (2019-2021) and `0.643925`
  (2019-2022).
- Strictly monotone; no winner, ranking or raw coverage change.
- Substantial and statistically clear test improvement in Brier, log loss and
  ECE.

### Why not isotonic, Platt or piecewise linear

- They are marginally better on the exploratory test table
  (`piecewise_linear` Brier `0.233696`, ECE `0.010036`;
  `isotonic_500` Brier `0.233726`, ECE `0.009865`; temperature Brier
  `0.233838`, ECE `0.015328`).
- Those differences are small, were not selected prospectively, and are not a
  valid reason to deviate from the pre-registered protocol.
- They add parameters, tail extrapolation choices and instability in the sparse
  high-confidence region.
- Their pooled validation advantage over temperature is at most `0.000126` log
  loss, below the materiality margin.
- If a future forward shadow wants the best possible ECE, a pre-registered
  piecewise or isotonic arm can be added. It is not the current recommendation.

### Decision

- **ACCEPT temperature scaling for a forward shadow only.**
- **HOLD production activation.**
- **REJECT production use of isotonic, Platt and piecewise-linear calibrators**
  on the current evidence.
- **REJECT any production Manager threshold, A1/A2 weight or betting-logic
  change.**
- **Do not claim ROI, CLV or directional-accuracy improvement.**

---

## 12. Remaining risks and forward-shadow requirements

### Remaining risks

1. **Tournament-cohort replay, not forward-issued picks.** The frozen baseline
   predicts a whole tournament edition at its visibility cohort. Live
   probabilities may have a different distribution and a different optimal
   slope.
2. **Manager gate not evaluable.** Historical A1/A3 predictions are absent. The
   three-agent spread and consensus impact cannot be measured here.
3. **No historical prices.** No ROI, CLV or market-edge claim is possible.
4. **Aggregate gains are concentrated in Challengers.** ATP Tour and Masters
   1000 do not show clear Brier gains. A global slope may be suboptimal for the
   ATP main tour.
5. **Residual high-confidence tail.** The calibrated 0.90-0.95 bin still
   overpredicts by about 5.7 points. A global temperature layer does not fully
   fix the tail.
6. **Threshold-volume trade-off.** High-confidence display coverage falls
   substantially (about -34% at 0.60, -57% at 0.70, -71% at 0.80 and -77% at
   0.90). Product behavior must be specified before integration.
7. **Slope drift.** The slope is estimated on 2019-2022. Future rule changes,
   data-pipeline changes, tour mix or player-population changes could shift it.
8. **Small subgroups.** CARPET (46), GRASS (1,945), the calibrated 0.90+ band
   (197) and some tier cells are small. Subgroup conclusions are exploratory.

### Forward-shadow requirements

1. Capture genuine pre-match Agent 2 probabilities, A1/A2/A3 predictions,
   Manager decisions, eligibility, abstention reasons and outcomes.
2. Freeze the production baseline and the temperature slope `0.643925` as the
   shadow candidate. Do not refit on forward outcomes until the shadow is
   complete.
3. Evaluate baseline versus temperature on the same matches, with paired
   Brier/log-loss/ECE intervals, reliability by band, threshold counts, Manager
   gate pass rate, coverage and abstention.
4. Preserve the current Manager threshold, A1/A2 weights and betting logic.
   Do not activate a calibrated probability in production.
5. Require at least `1,000` scored picks and `200` high-confidence picks before
   a production-readiness review, consistent with the existing calibration
   monitor targets.
6. Monitor slope drift. If a refitted forward slope falls outside roughly
   `0.55-0.75`, pause and investigate the distribution change.
7. Pre-register any conditional, surface-specific or tail-specific calibrator
   before evaluating it. Do not choose it from forward subgroup wins.
8. Keep historical replay evidence separate from forward Model Pick records.

---

## 13. Reproducibility and artifacts

### Code

- `research/a2_calibration_shadow/calibrators.py` - monotone calibrators and
  fitting.
- `research/a2_calibration_shadow/metrics.py` - metrics, reliability, Cox
  calibration and paired bootstrap.
- `research/a2_calibration_shadow/harness.py` - selection, test, subgroups,
  threshold/product impact and artifact generation.
- `research/a2_calibration_shadow/plot_calibration.py` - reliability chart.
- `tests/test_a2_calibration_shadow.py` - 11 focused tests, including a
  test-outcome-flip leakage test.

### Commands

```powershell
python research/a2_calibration_shadow/harness.py `
  --records tmp/a2_form_lookback/records_baseline.jsonl `
  --output-dir tmp/a2_calibration_shadow `
  --artifacts-dir research/a2_calibration_shadow/artifacts `
  --frozen-dataset tmp/a2_frozen_20261019.jsonl `
  --mode select --reps 1000

python research/a2_calibration_shadow/harness.py `
  --records tmp/a2_form_lookback/records_baseline.jsonl `
  --output-dir tmp/a2_calibration_shadow `
  --artifacts-dir research/a2_calibration_shadow/artifacts `
  --frozen-dataset tmp/a2_frozen_20261019.jsonl `
  --mode test --reps 1000

python research/a2_calibration_shadow/plot_calibration.py
python -m pytest tests/test_a2_calibration_shadow.py -q
```

### Artifacts

| Artifact | Contents |
| --- | --- |
| `artifacts/selection.json` | Frozen selection protocol, fold metrics, selected method, refit parameters |
| `artifacts/analysis.json` | Full test metrics, intervals, subgroups, threshold, product and failure analysis |
| `artifacts/candidates_validation.json` | All candidate validation metrics |
| `artifacts/candidates_test.json` | Exploratory all-candidate test metrics |
| `artifacts/frozen_manifest.json` | Dataset/record hashes, populations, baseline verification |
| `artifacts/reliability_test.csv` | Reproducible reliability data |
| `artifacts/calibration_curves.json` | Validation and test reliability curves |
| `artifacts/calibration_reliability.svg` | Reliability chart (SVG) |
| `artifacts/calibration_reliability.png` | Reliability chart (PNG) |
| `artifacts/tables.md` | Generated human-readable tables |
| `artifacts/run_manifest.json` | Code SHA, command, seeds and artifact hashes |

### CI / validation results

- `python -m pytest tests/test_a2_calibration_shadow.py -q`: **11 passed**.
- `python -m pytest tests/test_a2_calibration_shadow.py tests/test_agent2_hierarchical_markov.py -q`: **56 passed**.
- `python -m ruff check research/a2_calibration_shadow tests/test_a2_calibration_shadow.py`: **All checks passed**.
- Baseline reproduction: all five PR #239 checks pass.

### Safety statement

- No production model activation.
- No live strategy, Manager, A1/A2, Market, Veto, staking or settlement change.
- No production database mutation.
- No schema change.
- No bookmaker prices.
- No merge or deployment.
- Historical replay results remain separate from genuine forward-issued Model
  Picks.

---

## Appendix A - Exact candidate test table (exploratory)

| Candidate | Brier | Log loss | ECE | Accuracy | Delta Brier | Delta log loss | Delta ECE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| identity | 0.235360 | 0.665448 | 0.029271 | 0.604048 | 0.000000 | 0.000000 | 0.000000 |
| temperature | 0.233838 | 0.659738 | 0.015328 | 0.604048 | -0.001521 | -0.005710 | -0.013943 |
| platt | 0.233736 | 0.659490 | 0.010611 | 0.604048 | -0.001623 | -0.005958 | -0.018660 |
| piecewise_linear | 0.233696 | 0.659385 | 0.010036 | 0.604048 | -0.001664 | -0.006063 | -0.019235 |
| isotonic_500 | 0.233726 | 0.659466 | 0.009865 | 0.604048 | -0.001633 | -0.005982 | -0.019406 |
| isotonic_200 | 0.233752 | 0.659532 | 0.010780 | 0.604048 | -0.001607 | -0.005916 | -0.018491 |
| isotonic_100 | 0.233768 | 0.659571 | 0.010898 | 0.604048 | -0.001591 | -0.005877 | -0.018372 |
| isotonic_50 | 0.233776 | 0.659585 | 0.010641 | 0.604048 | -0.001584 | -0.005863 | -0.018630 |

This table is exploratory. The primary evidence is the pre-registered selected
temperature model in section 4.2.

---

## Appendix B - Exact test paired intervals

| Metric | Paired delta | 95% percentile interval |
| --- | ---: | --- |
| Accuracy | 0.000000 | 0.000000 to 0.000000 |
| Brier | -0.001521 | -0.001996 to -0.001049 |
| Log loss | -0.005710 | -0.007026 to -0.004436 |
| ECE | -0.013943 | -0.023061 to -0.005049 |

Bootstrap: `1,000` match-level resamples, seed `20261021`. Subgroup bootstrap:
`500` resamples, same seed. The accuracy interval is exactly `[0, 0]` because
the calibration mapping never changes the selected side.

---

## Appendix C - Validation 2022 detail

Fit on 2019-2021, slope `0.645509`.

| Metric | Baseline | Temperature | Paired delta | Paired 95% CI |
| --- | ---: | ---: | ---: | --- |
| Brier | 0.237260 | 0.234679 | -0.002581 | -0.003503 to -0.001735 |
| Log loss | 0.669045 | 0.661235 | -0.007810 | -0.010265 to -0.005486 |
| ECE | 0.040648 | 0.007805 | -0.032843 | -0.040222 to -0.017392 |
| Accuracy | 0.597355 | 0.597355 | 0.000000 | 0.000000 to 0.000000 |

This is the chronologically later validation fold used for selection. It is
consistent with the test direction and magnitude.
