# Agent 2 Calibration Shadow - Generated Tables

- Experiment: `a2_calibration_shadow_20261021`
- Selected calibrator: `temperature`
- Selection protocol: a2_calibration_shadow_protocol_v1 (test outcomes used: False)

## Populations

| Population | n | Seasons | First cohort | Last cohort |
| --- | ---: | --- | --- | --- |
| fit_2019_2022 | 34433 | {2019: 10329, 2020: 4231, 2021: 8908, 2022: 10965} | 2019-01-13T23:59:59+00:00 | 2022-12-04T23:59:59+00:00 |
| validation_2022 | 10965 | {2022: 10965} | 2022-01-09T23:59:59+00:00 | 2022-12-04T23:59:59+00:00 |
| test_2023_2025 | 36512 | {2023: 11865, 2024: 12414, 2025: 12233} | 2023-01-07T23:59:59+00:00 | 2025-11-30T23:59:59+00:00 |
| test_selected | 36512 | {2023: 11865, 2024: 12414, 2025: 12233} | 2023-01-07T23:59:59+00:00 | 2025-11-30T23:59:59+00:00 |
| test_eligible | 40040 | {2023: 12942, 2024: 13682, 2025: 13416} | 2023-01-07T23:59:59+00:00 | 2025-11-30T23:59:59+00:00 |

## Validation candidate selection

| Candidate | Rank | Pooled n | Brier | Log loss | ECE | Accuracy | Delta log loss vs identity |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| identity | 0 | 19873 | 0.2375 | 0.6701 | 0.0388 | 0.5986 | 0.000000 |
| temperature | 1 | 19873 | 0.2350 | 0.6621 | 0.0084 | 0.5986 | -0.008081 |
| platt | 2 | 19873 | 0.2349 | 0.6620 | 0.0057 | 0.5986 | -0.008162 |
| isotonic_500 | 4 | 19873 | 0.2350 | 0.6620 | 0.0041 | 0.5986 | -0.008098 |
| isotonic_200 | 5 | 19873 | 0.2350 | 0.6621 | 0.0061 | 0.5986 | -0.008073 |
| isotonic_100 | 6 | 19873 | 0.2350 | 0.6623 | 0.0062 | 0.5986 | -0.007847 |
| isotonic_50 | 7 | 19873 | 0.2350 | 0.6623 | 0.0071 | 0.5986 | -0.007886 |
| piecewise_linear | 3 | 19873 | 0.2349 | 0.6619 | 0.0031 | 0.5986 | -0.008207 |

Selected `temperature` because it was within 0.0002 pooled log loss of the best candidate (`piecewise_linear`) and has the lowest complexity rank among tied candidates.

## Test: baseline versus selected calibrator

| Metric | Baseline | Baseline 95% CI | Selected | Selected 95% CI | Paired delta | Paired 95% CI |
| --- | ---: | --- | ---: | --- | ---: | --- |
| accuracy | 0.6040 | 0.5990 to 0.6093 | 0.6040 | 0.5990 to 0.6093 | 0.000000 | 0.000000 to 0.000000 |
| brier | 0.2354 | 0.2339 to 0.2369 | 0.2338 | 0.2328 to 0.2350 | -0.001521 | -0.001996 to -0.001049 |
| log_loss | 0.6654 | 0.6619 to 0.6692 | 0.6597 | 0.6575 to 0.6622 | -0.005710 | -0.007026 to -0.004436 |
| ece | 0.0293 | 0.0252 to 0.0345 | 0.0153 | 0.0112 to 0.0211 | -0.013943 | -0.023061 to -0.005049 |

A negative paired delta for Brier, log loss or ECE is an improvement. Accuracy is unchanged because the calibrator is monotone and constrained to keep the selected side.

## Test: reliability by cumulative confidence band

| Method | Band | n | Mean predicted | Observed | Gap | 95% Wilson |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| baseline | >= 0.60 | 19465 | 0.7079 | 0.6576 | -0.0503 | 0.6509 to 0.6642 |
| baseline | >= 0.70 | 8368 | 0.7914 | 0.7187 | -0.0727 | 0.7090 to 0.7282 |
| baseline | >= 0.80 | 3172 | 0.8705 | 0.7673 | -0.1031 | 0.7523 to 0.7817 |
| baseline | >= 0.90 | 874 | 0.9433 | 0.8444 | -0.0989 | 0.8189 to 0.8669 |
| selected | >= 0.60 | 12788 | 0.6765 | 0.6860 | 0.0095 | 0.6779 to 0.6939 |
| selected | >= 0.70 | 3561 | 0.7719 | 0.7588 | -0.0131 | 0.7444 to 0.7725 |
| selected | >= 0.80 | 926 | 0.8616 | 0.8402 | -0.0215 | 0.8152 to 0.8624 |
| selected | >= 0.90 | 197 | 0.9331 | 0.8782 | -0.0550 | 0.8251 to 0.9167 |

## Test: reliability by 5-point bin

| Method | Bin | n | Mean predicted | Observed | Gap |
| --- | --- | ---: | ---: | ---: | ---: |
| baseline | 0.50-0.55 | 8978 | 0.5248 | 0.5186 | -0.0062 |
| baseline | 0.55-0.60 | 8069 | 0.5742 | 0.5700 | -0.0042 |
| baseline | 0.60-0.65 | 6428 | 0.6239 | 0.6013 | -0.0227 |
| baseline | 0.65-0.70 | 4669 | 0.6736 | 0.6256 | -0.0480 |
| baseline | 0.70-0.75 | 3132 | 0.7235 | 0.6858 | -0.0377 |
| baseline | 0.75-0.80 | 2064 | 0.7730 | 0.6938 | -0.0792 |
| baseline | 0.80-0.85 | 1404 | 0.8233 | 0.7336 | -0.0897 |
| baseline | 0.85-0.90 | 894 | 0.8733 | 0.7450 | -0.1283 |
| baseline | 0.90-0.95 | 522 | 0.9240 | 0.8372 | -0.0868 |
| baseline | 0.95-1.00 | 352 | 0.9720 | 0.8551 | -0.1169 |
| selected | 0.50-0.55 | 13587 | 0.5245 | 0.5336 | 0.0091 |
| selected | 0.55-0.60 | 10137 | 0.5733 | 0.5951 | 0.0219 |
| selected | 0.60-0.65 | 6040 | 0.6224 | 0.6389 | 0.0165 |
| selected | 0.65-0.70 | 3187 | 0.6724 | 0.6938 | 0.0214 |
| selected | 0.70-0.75 | 1713 | 0.7228 | 0.7227 | -0.0001 |
| selected | 0.75-0.80 | 922 | 0.7728 | 0.7440 | -0.0288 |
| selected | 0.80-0.85 | 456 | 0.8238 | 0.8224 | -0.0014 |
| selected | 0.85-0.90 | 273 | 0.8732 | 0.8425 | -0.0307 |
| selected | 0.90-0.95 | 148 | 0.9217 | 0.8649 | -0.0569 |
| selected | 0.95-1.00 | 49 | 0.9676 | 0.9184 | -0.0492 |

## Test: threshold crossings

| Threshold | Baseline n | Selected n | Delta | Baseline share | Selected share | Selected threshold preserving baseline n |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.60 | 19465 | 12788 | -6677 | 0.5331 | 0.3502 | 0.5649 |
| 0.70 | 8368 | 3561 | -4807 | 0.2292 | 0.0975 | 0.6331 |
| 0.80 | 3172 | 926 | -2246 | 0.0869 | 0.0254 | 0.7094 |
| 0.90 | 874 | 197 | -677 | 0.0239 | 0.0054 | 0.8045 |

## Test: high-confidence bands

| Band | n | Wins | Losses | Baseline mean | Selected mean | Observed | Baseline gap | Selected gap |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| >= 0.60 | 19465 | 12800 | 6665 | 0.7079 | 0.6439 | 0.6576 | -0.0503 | 0.0137 |
| >= 0.70 | 8368 | 6014 | 2354 | 0.7914 | 0.7087 | 0.7187 | -0.0727 | 0.0100 |
| >= 0.80 | 3172 | 2434 | 738 | 0.8705 | 0.7801 | 0.7673 | -0.1031 | -0.0128 |
| >= 0.90 | 874 | 738 | 136 | 0.9433 | 0.8652 | 0.8444 | -0.0989 | -0.0208 |

## Test: subgroup Brier and ECE

| Dimension | Group | n | Baseline Brier | Selected Brier | Delta Brier | Baseline ECE | Selected ECE | Delta ECE | Small sample |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| surface | CARPET | 46 | 0.2365 | 0.2308 | -0.005667 | 0.1976 | 0.1697 | -0.027874 | yes |
| surface | CLAY | 15812 | 0.2344 | 0.2334 | -0.000979 | 0.0270 | 0.0212 | -0.005757 | no |
| surface | GRASS | 1945 | 0.2431 | 0.2370 | -0.006052 | 0.0791 | 0.0320 | -0.047081 | no |
| surface | HARD | 18709 | 0.2354 | 0.2339 | -0.001498 | 0.0293 | 0.0161 | -0.013184 | no |
| competition_tier | ATP_TOUR | 6007 | 0.2357 | 0.2356 | -0.000161 | 0.0179 | 0.0226 | 0.004641 | no |
| competition_tier | CHALLENGER | 24917 | 0.2361 | 0.2341 | -0.002036 | 0.0320 | 0.0164 | -0.015649 | no |
| competition_tier | GRAND_SLAM | 2751 | 0.2292 | 0.2275 | -0.001729 | 0.0398 | 0.0144 | -0.025433 | no |
| competition_tier | MASTERS_1000 | 2837 | 0.2339 | 0.2342 | 0.000324 | 0.0267 | 0.0186 | -0.008132 | no |
| tour_scope | ATP_MAIN | 11595 | 0.2337 | 0.2333 | -0.000415 | 0.0234 | 0.0172 | -0.006182 | no |
| tour_scope | CHALLENGER | 24917 | 0.2361 | 0.2341 | -0.002036 | 0.0320 | 0.0164 | -0.015649 | no |
| season_year | 2023 | 11865 | 0.2349 | 0.2335 | -0.001357 | 0.0304 | 0.0178 | -0.012615 | no |
| season_year | 2024 | 12414 | 0.2342 | 0.2333 | -0.000925 | 0.0279 | 0.0193 | -0.008575 | no |
| season_year | 2025 | 12233 | 0.2370 | 0.2347 | -0.002285 | 0.0356 | 0.0103 | -0.025313 | no |
| history_bucket | 10_19 | 2938 | 0.2328 | 0.2263 | -0.006491 | 0.0699 | 0.0239 | -0.046047 | no |
| history_bucket | 20_49 | 6247 | 0.2324 | 0.2305 | -0.001912 | 0.0343 | 0.0179 | -0.016378 | no |
| history_bucket | 50_plus | 25252 | 0.2377 | 0.2372 | -0.000470 | 0.0206 | 0.0168 | -0.003740 | no |
| history_bucket | 5_9 | 2075 | 0.2199 | 0.2138 | -0.006099 | 0.0729 | 0.0327 | -0.040137 | no |
| inactivity_bucket | active_lt_180d | 33819 | 0.2357 | 0.2343 | -0.001425 | 0.0283 | 0.0153 | -0.012950 | no |
| inactivity_bucket | no_prior_history | 1388 | 0.2342 | 0.2334 | -0.000734 | 0.0376 | 0.0240 | -0.013648 | no |
| inactivity_bucket | return_ge_180d | 1305 | 0.2272 | 0.2224 | -0.004850 | 0.0727 | 0.0380 | -0.034675 | no |
| qualifying_bucket | main_draw | 22313 | 0.2362 | 0.2351 | -0.001037 | 0.0267 | 0.0170 | -0.009653 | no |
| qualifying_bucket | qualifying | 14199 | 0.2341 | 0.2318 | -0.002281 | 0.0367 | 0.0138 | -0.022883 | no |

## Test: product and ranking impact

- Predicted winners changed: `0`
- Pairwise ranking inversions: `0`
- Spearman rank correlation (raw versus calibrated): `1.000000`
- A2 calibrated probabilities below the Manager 0.5 floor: `0`
- Mean A2 probability change: `-0.0420`; max absolute change: `0.0972`
- Consensus mean shift if A1/A3 are unchanged: `-0.0140`
- Three-agent spread change bound if A1/A3 are unchanged: `0.0972`
- Raw prediction coverage: `0.9119` (36512/40040); unchanged by calibration.

## Leakage checks

- `fit_years`: `[2019, 2022]`
- `test_years`: `[2023, 2025]`
- `fit_test_year_overlap`: `False`
- `test_rows_in_fit`: `0`
- `test_outcomes_used_for_selection`: `False`
- `selection_file_certifies_unused_test`: `True`
- `duplicate_match_ids`: `0`
- `baseline_matches_pr239`: `{'pass': True, 'checks': {'predicted': {'actual': 36512, 'expected': 36512, 'absolute_difference': 0.0, 'tolerance': 0.0, 'pass': True}, 'accuracy': {'actual': 0.6040479842243646, 'expected': 0.6040479842243646, 'absolute_difference': 0.0, 'tolerance': 1e-06, 'pass': True}, 'brier': {'actual': 0.23535950080287593, 'expected': 0.23535950080287593, 'absolute_difference': 0.0, 'tolerance': 1e-06, 'pass': True}, 'log_loss': {'actual': 0.6654477796981432, 'expected': 0.6654477796981432, 'absolute_difference': 0.0, 'tolerance': 1e-06, 'pass': True}, 'ece': {'actual': 0.029270838902278724, 'expected': 0.029270838902278697, 'absolute_difference': 2.7755575615628914e-17, 'tolerance': 1e-06, 'pass': True}}}`
- `monotone_mapping_check`: `{'grid_points': 501, 'nondecreasing': True, 'within_0_5_to_1': True, 'minimum': 0.5, 'maximum': 0.9999999812548869}`
- `selection_side_changes`: `0`
- `records_sha256`: `7fbf4575339b14bfd58c22308f0495ea32b4d8d033e7208c595c581f77365e63`
- `test_population_sha256`: `af995c66c2369fd98e7393f21fe39182f2624ce84bc47ededbc468e19b228482`

## Exploratory test comparison of all candidates

| Candidate | Brier | Log loss | ECE | Delta Brier vs baseline | Delta log loss vs baseline | Delta ECE vs baseline |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| identity | 0.2354 | 0.6654 | 0.0293 | 0.000000 | 0.000000 | 0.000000 |
| temperature | 0.2338 | 0.6597 | 0.0153 | -0.001521 | -0.005710 | -0.013943 |
| platt | 0.2337 | 0.6595 | 0.0106 | -0.001623 | -0.005958 | -0.018660 |
| isotonic_500 | 0.2337 | 0.6595 | 0.0099 | -0.001633 | -0.005982 | -0.019406 |
| isotonic_200 | 0.2338 | 0.6595 | 0.0108 | -0.001607 | -0.005916 | -0.018491 |
| isotonic_100 | 0.2338 | 0.6596 | 0.0109 | -0.001591 | -0.005877 | -0.018372 |
| isotonic_50 | 0.2338 | 0.6596 | 0.0106 | -0.001584 | -0.005863 | -0.018630 |
| piecewise_linear | 0.2337 | 0.6594 | 0.0100 | -0.001664 | -0.006063 | -0.019235 |

This table is exploratory. The primary comparison is the pre-registered selected candidate in the baseline-versus-selected table above.

## Exploratory conditional temperature scaling

Group-specific temperature slopes fitted on 2019-2022, with a global-slope fallback for groups with fewer than 500 training rows. This is not part of the pre-registered selection.

| Dimension | Group | n | Slope | Baseline Brier | Global Brier | Conditional Brier | Baseline ECE | Global ECE | Conditional ECE | Conditional minus global Brier (95% CI) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| competition_tier | ATP_TOUR | 6007 | 0.740 | 0.2357 | 0.2356 | 0.2352 | 0.0179 | 0.0226 | 0.0133 | -0.000698 to -0.000054 |
| competition_tier | CHALLENGER | 24917 | 0.600 | 0.2361 | 0.2341 | 0.2342 | 0.0320 | 0.0164 | 0.0196 | 0.000032 to 0.000182 |
| competition_tier | GRAND_SLAM | 2751 | 0.671 | 0.2292 | 0.2275 | 0.2274 | 0.0398 | 0.0144 | 0.0151 | -0.000266 to 0.000065 |
| competition_tier | MASTERS_1000 | 2837 | 0.776 | 0.2339 | 0.2342 | 0.2336 | 0.0267 | 0.0186 | 0.0072 | -0.001221 to -0.000086 |
| surface | CARPET | 46 | 0.644 | 0.2365 | 0.2308 | 0.2308 | 0.1976 | 0.1697 | 0.1697 | 0.000000 to 0.000000 |
| surface | CLAY | 15812 | 0.652 | 0.2344 | 0.2334 | 0.2334 | 0.0270 | 0.0212 | 0.0205 | -0.000050 to -0.000013 |
| surface | GRASS | 1945 | 0.488 | 0.2431 | 0.2370 | 0.2365 | 0.0791 | 0.0320 | 0.0166 | -0.001770 to 0.000551 |
| surface | HARD | 18709 | 0.666 | 0.2354 | 0.2339 | 0.2338 | 0.0293 | 0.0161 | 0.0137 | -0.000101 to -0.000007 |
| tour_scope | ATP_MAIN | 11595 | 0.722 | 0.2337 | 0.2333 | 0.2330 | 0.0234 | 0.0172 | 0.0099 | -0.000521 to -0.000133 |
| tour_scope | CHALLENGER | 24917 | 0.600 | 0.2361 | 0.2341 | 0.2342 | 0.0320 | 0.0164 | 0.0196 | 0.000032 to 0.000182 |
