# Challenger Model Pick Calibration — Research Report (2026-10-13)

Read-only research. No production, Railway, repository, policy, or parameter changes were made, and no provider APIs were called. All numbers come from forward-issued production data only: `predictive_agent_result_v1`, COMPLETED, finalized, `experiment_id IS NULL` (`PRODUCTION_RUN_PREDICATE`). Each population keeps the first production evaluation per match, so there is no hindsight replacement.

## Executive summary

- **Overall level bias on Challenger is small. Only A1's is statistically clear.** Gap = hit rate minus mean predicted p; negative means overconfident.
  - A1: -3.4 pt, 95% CI [-6.0, -0.8], n=1227. The day-cluster CI also excludes 0.
  - A2: -2.2 pt [-5.4, +1.2], n=788.
  - Manager consensus: -3.7 pt [-10.0, +2.8], n=216.
  - Aligned Model Picks (consensus >=0.60 plus wide-spread): -1.3 pt [-5.5, +2.7], n=459.
- **The overconfidence is concentrated at high confidence (p >= 0.80). This is a slope problem, not a level problem.**
  - Full-period Challenger gaps at p >= 0.80:
    - A1: -11.1 pt [-17.3, -5.2], n=188.
    - A2: -12.0 pt [-20.8, -4.0], n=101.
    - Manager consensus: -16.5 pt [-30.1, -3.4], n=44.
    - Aligned: -9.8 pt [-19.4, -0.9], n=84.
  - Cox calibration slopes are below 1 for A1 (0.62 [0.42, 0.84]) and A2 (0.51 [0.28, 0.78]).
  - The pattern is the same on the untouched holdout (on or after 2026-09-30):
    - A1 at p >= 0.80: -14.0 pt [-24.6, -4.4], n=79.
    - A2: -19.2 pt [-32.7, -6.7], n=48.
    - Aligned: -21.0 pt [-37.0, -6.5], n=38.
    - Manager consensus: -33.2 pt [-53.7, -13.8], n=24.
  - However, for consensus and aligned the development-period high-confidence band was well calibrated (+3.5 and -0.5 pt). Their high-confidence miss is not stable over time.
- **Recalibration fitted on development does not robustly improve the holdout.**
  - For A1 and A2, Platt and shrinkage cut holdout log loss by about 0.009 to 0.011, but every paired-bootstrap CI includes 0. This holds at the 60%, 65% and 70% splits.
  - Fitted values on Challenger development: A1 shrink k=0.74 (Platt slope 0.65); A2 k=0.78.
  - For Manager consensus, every development-fitted recalibrator made the holdout significantly **worse** (Platt dLogLoss +0.057 [+0.023, +0.095]). The development slope was 1.46; the holdout slope was about 0.
  - For aligned picks, the global-fit Platt gives a tiny gain at the 65% split (-0.006 [-0.011, -0.002]) and at the 70% split. It gives none at the 60% split (+0.001). This is not robust.
  - Isotonic overfits badly (aligned holdout dLogLoss +0.14; consensus +0.80).
  - Tier-specific fits are not reliably better than global fits. ATP holdouts are too small (26 to 96) to judge.
- **Deep point-in-time support does not explain the gap, and it does not make it worse.**
  - A naive live-ID-only history count seemed to show deep support doing worse (A1 deep minus shallow: -6.7 pt). That count is confounded: live IDs only have history ingested since about September 2026.
  - With bridge-aware depth (`identity_pit_v1`: 688 of 868 pick players resolve, almost all via TENNIS_DATA aliases), there is no sign that deeper support increases overconfidence.
    - A1 deep (>=100 prior matches) minus shallow: +1.5 pt [-4.4, +7.6].
    - A2: +7.9 pt [+1.0, +14.8], so deep support is *better* calibrated.
  - The high-confidence miss remains among deep-support A1 picks: n=33, hit 72.7% against mean p 85.4%, gap -12.6 pt [-28.4, +2.3]. This is consistent with a slope issue that does not depend on support depth.
- **Conclusion:** there is statistically robust evidence of **high-confidence (>=0.80) overconfidence for Challenger A1 and A2** in both development and holdout. There is **no robust holdout evidence that any tested recalibration improves overall proper scores**. The data covers about 5 weeks (2026-09-08 to 2026-10-13) with one model version per agent. Sample sizes are below the repository's own calibrator guidance (`CALIBRATION_TARGET_SCORED=1000`, `CALIBRATION_TARGET_HIGH_CONFIDENCE=200`) for every population except A1 overall (1227 scored, 188 at >=0.80). Recommendation: **research only. No production parameter change without independently validated evidence.**

## Method notes

- **Populations:**
  - Manager consensus: first production CONSENSUS per match (`first_production_model_picks_cte` semantics), any probability.
  - Aligned Model Picks: `aligned_production_model_picks_cte`, floor 0.60, split by `aligned_pick_type`.
  - A1/A2/A3: first production PREDICTED row per agent per match, `predictions.experiment_id IS NULL`.
- **Outcomes:** latest terminal `match_result_observations` row (COMPLETED/RETIRED/WALKOVER/CANCELLED). Only COMPLETED rows with `winner_side` in (1,2) are scored; RETIRED, WALKOVER and CANCELLED are excluded.
- **Tier:** `tournaments.competition_tier`. CHALLENGER is compared with ATP, which pools ATP_TOUR and MASTERS_1000. Point-in-time `match_versions.competition_tier` and `tour_scope` agree for every Challenger row. The qualifying flag comes from the point-in-time `match_versions.qualifying_flag`.
- **Metrics:**
  - ECE uses 10 equal-width bins over [0.5, 1.0]; every probability is the selected side's, so p >= 0.5.
  - Bootstrap uses 4000 resamples (iid over matches); a day-cluster block bootstrap is also reported for tier-level gaps.
  - Holdout score differences use a paired bootstrap.
- **Split:** by UTC match date. Development is match date <= 2026-09-29 and holdout is >= 2026-09-30 (through 2026-10-13). The cut is where 65% of scored Challenger A1 predictions fall in development; the same cut is used for every population. Sensitivity runs at the 60% split (cut 2026-09-29) and 70% split (cut 2026-10-02) are in `sens_0.60.md` and `sens_0.70.md`.
- **A3:** only 9 production predictions (8 scored), so it is not analysed.
- **Pick-threshold counts** in section 3 (n p' >= 0.60/0.70/0.80) are descriptive only. They are not a policy proposal.
- **Scripts and data** are in this directory: `extract.py`, `extract2.py`, `extract3.py` (read-only extraction), `analyze.py`, `analyze2.py`, and the CSV extracts. No credentials are stored.

## 1. Data description

Match-date range (UTC; match_start_at, fallback picked_at): 2026-09-08 to 2026-10-13.

| Population | Tier | Draw | Total | COMPLETED (scored) | RETIRED | WALKOVER | CANCELLED | Pending |
|---|---|---|---|---|---|---|---|---|
| MANAGER_CONSENSUS (first consensus, any p) | CHALLENGER | MD | 187 | 162 | 7 | 0 | 4 | 14 |
| MANAGER_CONSENSUS (first consensus, any p) | CHALLENGER | Q | 58 | 54 | 2 | 0 | 1 | 1 |
| MANAGER_CONSENSUS (first consensus, any p) | ATP | MD | 20 | 18 | 0 | 0 | 1 | 1 |
| MANAGER_CONSENSUS (first consensus, any p) | ATP | Q | 6 | 5 | 0 | 0 | 0 | 1 |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | CHALLENGER | MD | 401 | 352 | 18 | 0 | 11 | 20 |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | CHALLENGER | Q | 122 | 107 | 8 | 0 | 5 | 2 |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | ATP | MD | 52 | 47 | 2 | 0 | 1 | 2 |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | ATP | Q | 13 | 12 | 0 | 0 | 0 | 1 |
| ALIGNED: MANAGER_CONSENSUS type | CHALLENGER | MD | 127 | 109 | 5 | 0 | 4 | 9 |
| ALIGNED: MANAGER_CONSENSUS type | CHALLENGER | Q | 38 | 35 | 2 | 0 | 1 | 0 |
| ALIGNED: MANAGER_CONSENSUS type | ATP | MD | 15 | 13 | 0 | 0 | 1 | 1 |
| ALIGNED: MANAGER_CONSENSUS type | ATP | Q | 2 | 1 | 0 | 0 | 0 | 1 |
| ALIGNED: A1_A2_WIDE_SPREAD type | CHALLENGER | MD | 274 | 243 | 13 | 0 | 7 | 11 |
| ALIGNED: A1_A2_WIDE_SPREAD type | CHALLENGER | Q | 84 | 72 | 6 | 0 | 4 | 2 |
| ALIGNED: A1_A2_WIDE_SPREAD type | ATP | MD | 37 | 34 | 2 | 0 | 0 | 1 |
| ALIGNED: A1_A2_WIDE_SPREAD type | ATP | Q | 11 | 11 | 0 | 0 | 0 | 0 |
| A1 (first production prediction) | CHALLENGER | MD | 1070 | 909 | 37 | 0 | 16 | 108 |
| A1 (first production prediction) | CHALLENGER | Q | 359 | 318 | 15 | 0 | 7 | 19 |
| A1 (first production prediction) | ATP | MD | 341 | 190 | 4 | 0 | 54 | 93 |
| A1 (first production prediction) | ATP | Q | 68 | 64 | 3 | 0 | 0 | 1 |
| A2 (first production prediction) | CHALLENGER | MD | 712 | 616 | 26 | 0 | 15 | 55 |
| A2 (first production prediction) | CHALLENGER | Q | 191 | 172 | 11 | 0 | 5 | 3 |
| A2 (first production prediction) | ATP | MD | 90 | 82 | 3 | 0 | 2 | 3 |
| A2 (first production prediction) | ATP | Q | 27 | 26 | 0 | 0 | 0 | 1 |
| A3 (first production prediction) | CHALLENGER | MD | 5 | 4 | 1 | 0 | 0 | 0 |
| A3 (first production prediction) | ATP | MD | 4 | 4 | 0 | 0 | 0 | 0 |

## 2. Calibration by tier (all scored, full period)

Gap = hit rate - mean predicted (negative = overconfident). CI: bootstrap 95% (iid over matches) and day-cluster block bootstrap. ECE: 10 equal-width bins over [0.5, 1.0].

| Population | Tier | n | Hit | Mean p | Gap | 95% CI (iid) | 95% CI (day-cluster) | P_boot(gap>=0) | Brier | LogLoss | ECE |
|---|---|---|---|---|---|---|---|---|---|---|---|
| MANAGER_CONSENSUS (first consensus, any p) | CHALLENGER | 216 | 0.644 | 0.680 | -0.037 | [-0.100, +0.028] | [-0.102, +0.023] | 0.1313 | 0.2254 | 0.6482 | 0.113 |
| MANAGER_CONSENSUS (first consensus, any p) | ATP | 23 | 0.826 | 0.671 | +0.155 | [-0.018, +0.307] | [-0.038, +0.281] | 0.9610 | 0.1843 | 0.5630 | 0.269 |
| MANAGER_CONSENSUS (first consensus, any p) | ALL | 239 | 0.661 | 0.679 | -0.018 | [-0.078, +0.041] | [-0.084, +0.041] | 0.2807 | 0.2215 | 0.6400 | 0.103 |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | CHALLENGER | 459 | 0.706 | 0.719 | -0.013 | [-0.055, +0.027] | [-0.055, +0.029] | 0.2680 | 0.2081 | 0.6093 | 0.054 |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | ATP | 59 | 0.797 | 0.726 | +0.071 | [-0.042, +0.172] | [-0.110, +0.195] | 0.8980 | 0.1777 | 0.5448 | 0.106 |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | ALL | 518 | 0.716 | 0.719 | -0.003 | [-0.042, +0.034] | [-0.052, +0.040] | 0.4377 | 0.2047 | 0.6019 | 0.054 |
| ALIGNED: MANAGER_CONSENSUS type | CHALLENGER | 144 | 0.729 | 0.736 | -0.007 | [-0.082, +0.067] | [-0.079, +0.055] | 0.4405 | 0.2056 | 0.6079 | 0.115 |
| ALIGNED: MANAGER_CONSENSUS type | ATP | 14 | 0.857 | 0.739 | +0.118 | [-0.121, +0.294] | [-0.130, +0.298] | 0.8662 | 0.1717 | 0.5407 | 0.305 |
| ALIGNED: MANAGER_CONSENSUS type | ALL | 158 | 0.741 | 0.736 | +0.005 | [-0.067, +0.075] | [-0.062, +0.063] | 0.5633 | 0.2026 | 0.6019 | 0.119 |
| ALIGNED: A1_A2_WIDE_SPREAD type | CHALLENGER | 315 | 0.695 | 0.711 | -0.016 | [-0.067, +0.034] | [-0.078, +0.048] | 0.2760 | 0.2093 | 0.6099 | 0.042 |
| ALIGNED: A1_A2_WIDE_SPREAD type | ATP | 45 | 0.778 | 0.722 | +0.056 | [-0.071, +0.173] | [-0.108, +0.172] | 0.8205 | 0.1795 | 0.5461 | 0.107 |
| ALIGNED: A1_A2_WIDE_SPREAD type | ALL | 360 | 0.706 | 0.712 | -0.007 | [-0.054, +0.041] | [-0.067, +0.053] | 0.3975 | 0.2056 | 0.6019 | 0.037 |
| A1 (first production prediction) | CHALLENGER | 1227 | 0.621 | 0.655 | -0.034 | [-0.060, -0.008] | [-0.064, -0.003] | 0.0060 | 0.2294 | 0.6547 | 0.040 |
| A1 (first production prediction) | ATP | 254 | 0.724 | 0.718 | +0.007 | [-0.050, +0.060] | [-0.051, +0.068] | 0.5935 | 0.1956 | 0.5772 | 0.071 |
| A1 (first production prediction) | ALL | 1481 | 0.639 | 0.666 | -0.027 | [-0.051, -0.003] | [-0.052, -0.002] | 0.0152 | 0.2236 | 0.6414 | 0.037 |
| A2 (first production prediction) | CHALLENGER | 788 | 0.626 | 0.647 | -0.022 | [-0.054, +0.012] | [-0.051, +0.009] | 0.1027 | 0.2318 | 0.6586 | 0.043 |
| A2 (first production prediction) | ATP | 108 | 0.602 | 0.630 | -0.028 | [-0.118, +0.059] | [-0.175, +0.098] | 0.2737 | 0.2215 | 0.6342 | 0.113 |
| A2 (first production prediction) | ALL | 896 | 0.623 | 0.645 | -0.022 | [-0.054, +0.009] | [-0.055, +0.010] | 0.0845 | 0.2305 | 0.6556 | 0.033 |
| A3 (first production prediction) | CHALLENGER | 4 | n<10, not reported | | | | | | | | |
| A3 (first production prediction) | ATP | 4 | n<10, not reported | | | | | | | | |
| A3 (first production prediction) | ALL | 8 | n<10, not reported | | | | | | | | |

### Reliability by confidence band (CHALLENGER, scored)

| Population | Band | n | Mean p | Hit | Gap | 95% CI (iid) |
|---|---|---|---|---|---|---|
| MANAGER_CONSENSUS (first consensus, any p) | 0.5-0.6 | 65 | 0.552 | 0.477 | -0.075 | [-0.195, +0.041] |
| MANAGER_CONSENSUS (first consensus, any p) | 0.6-0.7 | 67 | 0.642 | 0.701 | +0.059 | [-0.058, +0.166] |
| MANAGER_CONSENSUS (first consensus, any p) | 0.7-0.8 | 40 | 0.744 | 0.750 | +0.006 | [-0.137, +0.129] |
| MANAGER_CONSENSUS (first consensus, any p) | 0.8-0.9 | 32 | 0.844 | 0.688 | -0.157 | [-0.315, -0.002] |
| MANAGER_CONSENSUS (first consensus, any p) | 0.9-1.0+ | 12 | 0.939 | 0.750 | -0.189 | [-0.436, +0.042] |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | 0.6-0.7 | 221 | 0.647 | 0.652 | +0.005 | [-0.058, +0.067] |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | 0.7-0.8 | 154 | 0.745 | 0.753 | +0.009 | [-0.060, +0.081] |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | 0.8-0.9 | 69 | 0.843 | 0.768 | -0.075 | [-0.176, +0.016] |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | 0.9-1.0+ | 15 | 0.936 | 0.733 | -0.202 | [-0.458, -0.006] |
| ALIGNED: MANAGER_CONSENSUS type | 0.6-0.7 | 65 | 0.642 | 0.708 | +0.066 | [-0.050, +0.175] |
| ALIGNED: MANAGER_CONSENSUS type | 0.7-0.8 | 37 | 0.746 | 0.757 | +0.011 | [-0.131, +0.144] |
| ALIGNED: MANAGER_CONSENSUS type | 0.8-0.9 | 30 | 0.845 | 0.733 | -0.112 | [-0.278, +0.047] |
| ALIGNED: MANAGER_CONSENSUS type | 0.9-1.0+ | 12 | 0.939 | 0.750 | -0.189 | [-0.436, +0.045] |
| ALIGNED: A1_A2_WIDE_SPREAD type | 0.6-0.7 | 156 | 0.649 | 0.628 | -0.021 | [-0.095, +0.057] |
| ALIGNED: A1_A2_WIDE_SPREAD type | 0.7-0.8 | 117 | 0.744 | 0.752 | +0.008 | [-0.073, +0.086] |
| ALIGNED: A1_A2_WIDE_SPREAD type | 0.8-0.9 | 39 | 0.841 | 0.795 | -0.047 | [-0.173, +0.077] |
| ALIGNED: A1_A2_WIDE_SPREAD type | 0.9-1.0+ | 3 | 0.920 | 0.667 | -0.254 | n<5 |
| A1 (first production prediction) | 0.5-0.6 | 485 | 0.532 | 0.524 | -0.009 | [-0.056, +0.034] |
| A1 (first production prediction) | 0.6-0.7 | 308 | 0.648 | 0.630 | -0.018 | [-0.071, +0.034] |
| A1 (first production prediction) | 0.7-0.8 | 246 | 0.749 | 0.703 | -0.046 | [-0.103, +0.011] |
| A1 (first production prediction) | 0.8-0.9 | 154 | 0.847 | 0.747 | -0.100 | [-0.171, -0.035] |
| A1 (first production prediction) | 0.9-1.0+ | 34 | 0.926 | 0.765 | -0.162 | [-0.310, -0.019] |
| A2 (first production prediction) | 0.5-0.6 | 340 | 0.549 | 0.559 | +0.010 | [-0.044, +0.062] |
| A2 (first production prediction) | 0.6-0.7 | 231 | 0.645 | 0.654 | +0.009 | [-0.053, +0.071] |
| A2 (first production prediction) | 0.7-0.8 | 116 | 0.745 | 0.655 | -0.089 | [-0.173, -0.004] |
| A2 (first production prediction) | 0.8-0.9 | 73 | 0.843 | 0.699 | -0.144 | [-0.253, -0.046] |
| A2 (first production prediction) | 0.9-1.0+ | 28 | 0.948 | 0.893 | -0.055 | [-0.174, +0.048] |

### Reliability by confidence band (ATP, scored)

| Population | Band | n | Mean p | Hit | Gap | 95% CI (iid) |
|---|---|---|---|---|---|---|
| MANAGER_CONSENSUS (first consensus, any p) | 0.5-0.6 | 8 | 0.545 | 0.750 | +0.205 | [-0.058, +0.457] |
| MANAGER_CONSENSUS (first consensus, any p) | 0.6-0.7 | 5 | 0.632 | 1.000 | +0.368 | [+0.353, +0.383] |
| MANAGER_CONSENSUS (first consensus, any p) | 0.7-0.8 | 5 | 0.745 | 1.000 | +0.255 | [+0.234, +0.277] |
| MANAGER_CONSENSUS (first consensus, any p) | 0.8-0.9 | 5 | 0.838 | 0.600 | -0.238 | [-0.644, +0.168] |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | 0.6-0.7 | 24 | 0.654 | 0.833 | +0.179 | [+0.010, +0.311] |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | 0.7-0.8 | 22 | 0.736 | 0.773 | +0.036 | [-0.144, +0.212] |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | 0.8-0.9 | 13 | 0.842 | 0.769 | -0.072 | [-0.311, +0.156] |
| ALIGNED: A1_A2_WIDE_SPREAD type | 0.6-0.7 | 19 | 0.660 | 0.789 | +0.130 | [-0.066, +0.295] |
| ALIGNED: A1_A2_WIDE_SPREAD type | 0.7-0.8 | 18 | 0.734 | 0.722 | -0.012 | [-0.233, +0.163] |
| ALIGNED: A1_A2_WIDE_SPREAD type | 0.8-0.9 | 8 | 0.844 | 0.875 | +0.031 | [-0.234, +0.174] |
| A1 (first production prediction) | 0.5-0.6 | 58 | 0.547 | 0.621 | +0.074 | [-0.051, +0.198] |
| A1 (first production prediction) | 0.6-0.7 | 65 | 0.653 | 0.708 | +0.055 | [-0.056, +0.162] |
| A1 (first production prediction) | 0.7-0.8 | 55 | 0.751 | 0.745 | -0.006 | [-0.124, +0.102] |
| A1 (first production prediction) | 0.8-0.9 | 45 | 0.845 | 0.733 | -0.112 | [-0.242, +0.016] |
| A1 (first production prediction) | 0.9-1.0+ | 31 | 0.930 | 0.903 | -0.027 | [-0.152, +0.069] |
| A2 (first production prediction) | 0.5-0.6 | 50 | 0.548 | 0.460 | -0.088 | [-0.220, +0.050] |
| A2 (first production prediction) | 0.6-0.7 | 33 | 0.640 | 0.636 | -0.004 | [-0.179, +0.149] |
| A2 (first production prediction) | 0.7-0.8 | 14 | 0.742 | 0.929 | +0.187 | [+0.031, +0.274] |
| A2 (first production prediction) | 0.8-0.9 | 11 | 0.830 | 0.727 | -0.103 | [-0.377, +0.166] |

### Challenger qualifying vs main draw (scored)

| Population | Draw | n | Hit | Mean p | Gap | 95% CI |
|---|---|---|---|---|---|---|
| MANAGER_CONSENSUS (first consensus, any p) | MAIN_DRAW | 162 | 0.623 | 0.682 | -0.059 | [-0.135, +0.012] |
| MANAGER_CONSENSUS (first consensus, any p) | QUALIFYING | 54 | 0.704 | 0.674 | +0.029 | [-0.105, +0.156] |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | MAIN_DRAW | 352 | 0.705 | 0.716 | -0.011 | [-0.060, +0.034] |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | QUALIFYING | 107 | 0.710 | 0.728 | -0.018 | [-0.104, +0.063] |
| ALIGNED: MANAGER_CONSENSUS type | MAIN_DRAW | 109 | 0.725 | 0.740 | -0.015 | [-0.101, +0.067] |
| ALIGNED: MANAGER_CONSENSUS type | QUALIFYING | 35 | 0.743 | 0.722 | +0.021 | [-0.147, +0.167] |
| ALIGNED: A1_A2_WIDE_SPREAD type | MAIN_DRAW | 243 | 0.695 | 0.705 | -0.009 | [-0.067, +0.048] |
| ALIGNED: A1_A2_WIDE_SPREAD type | QUALIFYING | 72 | 0.694 | 0.731 | -0.037 | [-0.144, +0.063] |
| A1 (first production prediction) | MAIN_DRAW | 909 | 0.615 | 0.651 | -0.036 | [-0.066, -0.005] |
| A1 (first production prediction) | QUALIFYING | 318 | 0.638 | 0.666 | -0.028 | [-0.080, +0.024] |
| A2 (first production prediction) | MAIN_DRAW | 616 | 0.622 | 0.641 | -0.019 | [-0.059, +0.019] |
| A2 (first production prediction) | QUALIFYING | 172 | 0.640 | 0.671 | -0.031 | [-0.105, +0.040] |

## 3. Chronological development vs holdout

Split on match date (UTC): development = match date <= 2026-09-29; holdout = match date >= 2026-09-30 (through 2026-10-13). Cut = first day at which >= 65% of scored Challenger A1 predictions fall in development; the same cut is applied to every population. All fits use development rows only.

### A1 (first production prediction)

Challenger dev n=831 (hit 0.605, mean p 0.641); Challenger holdout n=396 (hit 0.654, mean p 0.685); global dev n=989; ATP holdout n=96.

Fitted on Challenger dev: Platt a=+0.030, b=0.650; shrink k=0.743; isotonic blocks=17. Fitted on global dev: Platt a=+0.065, b=0.719; shrink k=0.835.

Challenger holdout:

| Method | Fit on | n | Mean p' | Hit | Gap | Brier | LogLoss | ECE | dLogLoss vs none [95% CI] | dBrier vs none [95% CI] | n p'>=0.80 | hit (p'>=0.80) | mean p' (p'>=0.80) | n p'>=0.60 | n p'>=0.70 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| none | - | 396 | 0.685 | 0.654 | -0.031 | 0.2220 | 0.6431 | 0.069 | +0.0000 [+0.0000, +0.0000] | +0.0000 [+0.0000, +0.0000] | 79 | 0.722 | 0.862 | 281 | 167 |
| platt | Challenger dev | 396 | 0.637 | 0.654 | +0.017 | 0.2208 | 0.6339 | 0.067 | -0.0091 [-0.0250, +0.0058] | -0.0012 [-0.0065, +0.0036] | 20 | 0.650 | 0.834 | 228 | 104 |
| shrink | Challenger dev | 396 | 0.638 | 0.654 | +0.016 | 0.2200 | 0.6320 | 0.054 | -0.0111 [-0.0276, +0.0045] | -0.0020 [-0.0071, +0.0029] | 13 | 0.692 | 0.819 | 238 | 115 |
| isotonic | Challenger dev | 396 | 0.606 | 0.654 | +0.048 | 0.2241 | 0.6401 | 0.090 | -0.0029 [-0.0267, +0.0205] | +0.0021 [-0.0063, +0.0106] | 2 | 1.000 | 1.000 | 252 | 67 |
| platt | Global dev | 396 | 0.656 | 0.654 | -0.002 | 0.2205 | 0.6341 | 0.056 | -0.0090 [-0.0208, +0.0016] | -0.0016 [-0.0051, +0.0017] | 37 | 0.703 | 0.838 | 261 | 128 |
| shrink | Global dev | 396 | 0.655 | 0.654 | -0.001 | 0.2200 | 0.6329 | 0.055 | -0.0101 [-0.0216, +0.0007] | -0.0020 [-0.0053, +0.0011] | 40 | 0.700 | 0.830 | 260 | 138 |
| isotonic | Global dev | 396 | 0.624 | 0.654 | +0.031 | 0.2242 | 0.6403 | 0.081 | -0.0028 [-0.0222, +0.0158] | +0.0022 [-0.0047, +0.0089] | 30 | 0.667 | 0.813 | 145 | 116 |

ATP-tier holdout (global fit vs none; Challenger-only fit applied out-of-tier for reference):

| Method | Fit on | n | Mean p' | Hit | Gap | Brier | LogLoss | ECE |
|---|---|---|---|---|---|---|---|---|
| none | - | 96 | 0.719 | 0.646 | -0.073 | 0.2307 | 0.6650 | 0.125 |
| platt | Global dev | 96 | 0.685 | 0.646 | -0.039 | 0.2249 | 0.6428 | 0.083 |
| shrink | Global dev | 96 | 0.683 | 0.646 | -0.037 | 0.2248 | 0.6431 | 0.112 |
| platt | Challenger dev | 96 | 0.664 | 0.646 | -0.019 | 0.2234 | 0.6383 | 0.091 |
| shrink | Challenger dev | 96 | 0.663 | 0.646 | -0.017 | 0.2232 | 0.6380 | 0.064 |

- Raw Challenger dev gap: -0.036 (95% CI [-0.068, -0.002])
- Raw Challenger holdout gap: -0.031 (95% CI [-0.077, +0.014])

### A2 (first production prediction)

Challenger dev n=450 (hit 0.609, mean p 0.645); Challenger holdout n=338 (hit 0.648, mean p 0.650); global dev n=509; ATP holdout n=49.

Fitted on Challenger dev: Platt a=+0.001, b=0.709; shrink k=0.783; isotonic blocks=18. Fitted on global dev: Platt a=-0.029, b=0.787; shrink k=0.818.

Challenger holdout:

| Method | Fit on | n | Mean p' | Hit | Gap | Brier | LogLoss | ECE | dLogLoss vs none [95% CI] | dBrier vs none [95% CI] | n p'>=0.80 | hit (p'>=0.80) | mean p' (p'>=0.80) | n p'>=0.60 | n p'>=0.70 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| none | - | 338 | 0.650 | 0.648 | -0.003 | 0.2320 | 0.6614 | 0.073 | +0.0000 [+0.0000, +0.0000] | +0.0000 [+0.0000, +0.0000] | 48 | 0.688 | 0.880 | 188 | 99 |
| platt | Challenger dev | 338 | 0.615 | 0.648 | +0.033 | 0.2301 | 0.6519 | 0.059 | -0.0096 [-0.0233, +0.0030] | -0.0019 [-0.0064, +0.0023] | 23 | 0.783 | 0.867 | 152 | 61 |
| shrink | Challenger dev | 338 | 0.618 | 0.648 | +0.030 | 0.2296 | 0.6515 | 0.078 | -0.0099 [-0.0241, +0.0033] | -0.0024 [-0.0065, +0.0016] | 20 | 0.750 | 0.840 | 159 | 67 |
| isotonic | Challenger dev | 338 | 0.556 | 0.648 | +0.092 | 0.2425 | 0.7768 | 0.024 | +0.1154 [-0.0019, +0.2635] | +0.0104 [-0.0021, +0.0233] | 5 | 1.000 | 1.000 | 193 | 19 |
| platt | Global dev | 338 | 0.618 | 0.648 | +0.030 | 0.2308 | 0.6541 | 0.082 | -0.0073 [-0.0181, +0.0031] | -0.0012 [-0.0049, +0.0024] | 30 | 0.733 | 0.865 | 153 | 68 |
| shrink | Global dev | 338 | 0.623 | 0.648 | +0.025 | 0.2298 | 0.6521 | 0.083 | -0.0093 [-0.0222, +0.0024] | -0.0023 [-0.0059, +0.0012] | 24 | 0.792 | 0.847 | 162 | 73 |
| isotonic | Global dev | 338 | 0.558 | 0.648 | +0.090 | 0.2445 | 0.7812 | 0.048 | +0.1198 [+0.0047, +0.2653] | +0.0124 [-0.0005, +0.0260] | 5 | 1.000 | 1.000 | 182 | 19 |

ATP-tier holdout (global fit vs none; Challenger-only fit applied out-of-tier for reference):

| Method | Fit on | n | Mean p' | Hit | Gap | Brier | LogLoss | ECE |
|---|---|---|---|---|---|---|---|---|
| none | - | 49 | 0.649 | 0.592 | -0.058 | 0.2285 | 0.6494 | 0.104 |
| platt | Global dev | 49 | 0.615 | 0.592 | -0.024 | 0.2269 | 0.6451 | 0.105 |
| shrink | Global dev | 49 | 0.622 | 0.592 | -0.030 | 0.2273 | 0.6458 | 0.110 |
| platt | Challenger dev | 49 | 0.612 | 0.592 | -0.020 | 0.2274 | 0.6462 | 0.114 |
| shrink | Challenger dev | 49 | 0.617 | 0.592 | -0.025 | 0.2273 | 0.6459 | 0.098 |

- Raw Challenger dev gap: -0.036 (95% CI [-0.081, +0.009])
- Raw Challenger holdout gap: -0.003 (95% CI [-0.052, +0.047])

### MANAGER_CONSENSUS (first consensus, any p)

Challenger dev n=128 (hit 0.641, mean p 0.664); Challenger holdout n=88 (hit 0.648, mean p 0.704); global dev n=140; ATP holdout n=11.

Fitted on Challenger dev: Platt a=-0.384, b=1.464; shrink k=1.086; isotonic blocks=17. Fitted on global dev: Platt a=-0.236, b=1.433; shrink k=1.149.

Challenger holdout:

| Method | Fit on | n | Mean p' | Hit | Gap | Brier | LogLoss | ECE | dLogLoss vs none [95% CI] | dBrier vs none [95% CI] | n p'>=0.80 | hit (p'>=0.80) | mean p' (p'>=0.80) | n p'>=0.60 | n p'>=0.70 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| none | - | 88 | 0.704 | 0.648 | -0.056 | 0.2513 | 0.7220 | 0.174 | +0.0000 [+0.0000, +0.0000] | +0.0000 [+0.0000, +0.0000] | 24 | 0.542 | 0.874 | 67 | 41 |
| platt | Challenger dev | 88 | 0.695 | 0.648 | -0.047 | 0.2641 | 0.7789 | 0.174 | +0.0569 [+0.0227, +0.0949] | +0.0129 [+0.0038, +0.0221] | 26 | 0.577 | 0.909 | 54 | 41 |
| shrink | Challenger dev | 88 | 0.722 | 0.648 | -0.074 | 0.2567 | 0.7548 | 0.158 | +0.0328 [+0.0089, +0.0607] | +0.0054 [+0.0012, +0.0099] | 26 | 0.577 | 0.899 | 68 | 44 |
| isotonic | Challenger dev | 88 | 0.649 | 0.648 | -0.001 | 0.2756 | 1.5197 | 0.126 | +0.7977 [+0.2540, +1.4762] | +0.0244 [-0.0012, +0.0520] | 17 | 0.588 | 0.992 | 63 | 30 |
| platt | Global dev | 88 | 0.718 | 0.648 | -0.070 | 0.2626 | 0.7820 | 0.179 | +0.0600 [+0.0223, +0.1027] | +0.0113 [+0.0037, +0.0194] | 30 | 0.600 | 0.902 | 59 | 45 |
| shrink | Global dev | 88 | 0.735 | 0.648 | -0.087 | 0.2612 | 0.8005 | 0.174 | +0.0785 [+0.0211, +0.1522] | +0.0100 [+0.0024, +0.0182] | 29 | 0.586 | 0.911 | 71 | 45 |
| isotonic | Global dev | 88 | 0.673 | 0.648 | -0.025 | 0.2725 | 1.5152 | 0.122 | +0.7932 [+0.2544, +1.4558] | +0.0213 [-0.0019, +0.0441] | 26 | 0.577 | 0.926 | 63 | 40 |

- Raw Challenger dev gap: -0.023 (95% CI [-0.100, +0.053])
- Raw Challenger holdout gap: -0.056 (95% CI [-0.160, +0.049])

### ALIGNED MODEL PICKS (MC>=0.60 + wide-spread)

Challenger dev n=260 (hit 0.692, mean p 0.718); Challenger holdout n=199 (hit 0.724, mean p 0.719); global dev n=293; ATP holdout n=26.

Fitted on Challenger dev: Platt a=-0.150, b=1.024; shrink k=0.932; isotonic blocks=16. Fitted on global dev: Platt a=+0.084, b=0.884; shrink k=0.978.

Challenger holdout:

| Method | Fit on | n | Mean p' | Hit | Gap | Brier | LogLoss | ECE | dLogLoss vs none [95% CI] | dBrier vs none [95% CI] | n p'>=0.80 | hit (p'>=0.80) | mean p' (p'>=0.80) | n p'>=0.60 | n p'>=0.70 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| none | - | 199 | 0.719 | 0.724 | +0.004 | 0.2110 | 0.6239 | 0.097 | +0.0000 [+0.0000, +0.0000] | +0.0000 [+0.0000, +0.0000] | 38 | 0.658 | 0.868 | 199 | 99 |
| platt | Challenger dev | 199 | 0.694 | 0.724 | +0.030 | 0.2135 | 0.6275 | 0.116 | +0.0036 [-0.0045, +0.0112] | +0.0026 [-0.0009, +0.0057] | 33 | 0.606 | 0.865 | 165 | 71 |
| shrink | Challenger dev | 199 | 0.704 | 0.724 | +0.019 | 0.2101 | 0.6176 | 0.123 | -0.0063 [-0.0149, +0.0013] | -0.0009 [-0.0031, +0.0012] | 31 | 0.581 | 0.854 | 188 | 88 |
| isotonic | Challenger dev | 199 | 0.647 | 0.724 | +0.077 | 0.2282 | 0.7653 | 0.107 | +0.1414 [+0.0164, +0.3215] | +0.0172 [+0.0055, +0.0292] | 25 | 0.640 | 0.868 | 156 | 49 |
| platt | Global dev | 199 | 0.716 | 0.724 | +0.008 | 0.2095 | 0.6178 | 0.094 | -0.0062 [-0.0112, -0.0017] | -0.0015 [-0.0027, -0.0004] | 35 | 0.629 | 0.858 | 199 | 96 |
| shrink | Global dev | 199 | 0.715 | 0.724 | +0.009 | 0.2106 | 0.6215 | 0.113 | -0.0024 [-0.0053, +0.0002] | -0.0003 [-0.0010, +0.0003] | 37 | 0.649 | 0.862 | 194 | 94 |
| isotonic | Global dev | 199 | 0.673 | 0.724 | +0.051 | 0.2279 | 0.7647 | 0.092 | +0.1408 [+0.0166, +0.3193] | +0.0169 [+0.0060, +0.0276] | 40 | 0.650 | 0.863 | 166 | 68 |

ATP-tier holdout (global fit vs none; Challenger-only fit applied out-of-tier for reference):

| Method | Fit on | n | Mean p' | Hit | Gap | Brier | LogLoss | ECE |
|---|---|---|---|---|---|---|---|---|
| none | - | 26 | 0.748 | 0.692 | -0.056 | 0.2182 | 0.6282 | 0.180 |
| platt | Global dev | 26 | 0.741 | 0.692 | -0.049 | 0.2165 | 0.6238 | 0.153 |
| shrink | Global dev | 26 | 0.743 | 0.692 | -0.050 | 0.2175 | 0.6260 | 0.143 |
| platt | Challenger dev | 26 | 0.725 | 0.692 | -0.032 | 0.2168 | 0.6232 | 0.125 |
| shrink | Challenger dev | 26 | 0.731 | 0.692 | -0.039 | 0.2161 | 0.6224 | 0.126 |

- Raw Challenger dev gap: -0.026 (95% CI [-0.082, +0.030])
- Raw Challenger holdout gap: +0.004 (95% CI [-0.061, +0.067])

## 4. Point-in-time support depth (Challenger, scored)

Support = min over the two players of COMPLETED match_result_observations rows (distinct matches, any tier/source in the DB) whose first COMPLETED observation as_of is strictly before the pick timestamp. Proxy only: A1 runtime history depth is not persisted (a1_diagnostic marks it unavailable).

| Population | Support bucket (min prior matches) | n | Hit | Mean p | Gap | 95% CI |
|---|---|---|---|---|---|---|
| A1 (first production prediction) | quartiles of min-prior: 0 / 13 / 29 | 1227 | | | | |
| A1 (first production prediction) | 0-9 | 547 | 0.616 | 0.635 | -0.019 | [-0.059, +0.021] |
| A1 (first production prediction) | 10-29 | 377 | 0.658 | 0.674 | -0.016 | [-0.064, +0.031] |
| A1 (first production prediction) | 30-59 | 302 | 0.583 | 0.669 | -0.087 | [-0.144, -0.031] |
| A1 (first production prediction) | 60-99 | 1 | n<10 | | | |
| A1 (first production prediction) | deep (>=30) vs shallow (<30): n 303/924 | | | | deep -0.085 [-0.141, -0.028]; shallow -0.018; diff -0.067 | diff CI [-0.131, -0.004] |
| A2 (first production prediction) | quartiles of min-prior: 7 / 22 / 34 | 788 | | | | |
| A2 (first production prediction) | 0-9 | 229 | 0.651 | 0.675 | -0.024 | [-0.086, +0.037] |
| A2 (first production prediction) | 10-29 | 293 | 0.659 | 0.651 | +0.008 | [-0.046, +0.061] |
| A2 (first production prediction) | 30-59 | 265 | 0.566 | 0.619 | -0.053 | [-0.113, +0.007] |
| A2 (first production prediction) | 60-99 | 1 | n<10 | | | |
| A2 (first production prediction) | deep (>=30) vs shallow (<30): n 266/522 | | | | deep -0.052 [-0.111, +0.008]; shallow -0.006; diff -0.045 | diff CI [-0.120, +0.027] |
| MANAGER_CONSENSUS (first consensus, any p) | quartiles of min-prior: 5 / 19 / 34 | 216 | | | | |
| MANAGER_CONSENSUS (first consensus, any p) | 0-9 | 72 | 0.681 | 0.703 | -0.023 | [-0.124, +0.079] |
| MANAGER_CONSENSUS (first consensus, any p) | 10-29 | 77 | 0.662 | 0.696 | -0.034 | [-0.138, +0.066] |
| MANAGER_CONSENSUS (first consensus, any p) | 30-59 | 66 | 0.576 | 0.638 | -0.063 | [-0.187, +0.056] |
| MANAGER_CONSENSUS (first consensus, any p) | 60-99 | 1 | n<10 | | | |
| MANAGER_CONSENSUS (first consensus, any p) | deep (>=30) vs shallow (<30): n 67/149 | | | | deep -0.056 [-0.183, +0.066]; shallow -0.028; diff -0.027 | diff CI [-0.179, +0.115] |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | quartiles of min-prior: 6 / 19 / 32 | 459 | | | | |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | 0-9 | 147 | 0.721 | 0.735 | -0.014 | [-0.088, +0.057] |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | 10-29 | 173 | 0.775 | 0.720 | +0.054 | [-0.008, +0.117] |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | 30-59 | 139 | 0.604 | 0.699 | -0.095 | [-0.174, -0.018] |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | deep (>=30) vs shallow (<30): n 139/320 | | | | deep -0.095 [-0.178, -0.017]; shallow +0.023; diff -0.117 | diff CI [-0.213, -0.022] |


## 2b. Calibration slope (Cox: logit(P(win)) = a + b*logit(p); b<1 = over-dispersed / overconfident at extremes)

| Population | Tier | Period | n | a | b | b 95% CI |
|---|---|---|---|---|---|---|
| A1 (first production prediction) | CHALLENGER | full | 1227 | +0.072 | 0.623 | [0.419, 0.837] |
| A1 (first production prediction) | CHALLENGER | dev | 831 | +0.030 | 0.650 | [0.402, 0.918] |
| A1 (first production prediction) | CHALLENGER | holdout | 396 | +0.197 | 0.535 | [0.180, 0.951] |
| A1 (first production prediction) | ATP | full | 254 | +0.383 | 0.591 | [0.244, 1.019] |
| A1 (first production prediction) | ATP | dev | 158 | +0.528 | 0.732 | [0.245, 1.372] |
| A1 (first production prediction) | ATP | holdout | 96 | +0.115 | 0.472 | [-0.058, 1.166] |
| A2 (first production prediction) | CHALLENGER | full | 788 | +0.184 | 0.507 | [0.279, 0.781] |
| A2 (first production prediction) | CHALLENGER | dev | 450 | +0.001 | 0.709 | [0.372, 1.124] |
| A2 (first production prediction) | CHALLENGER | holdout | 338 | +0.392 | 0.314 | [0.045, 0.699] |
| A2 (first production prediction) | ATP | full | 108 | -0.342 | 1.447 | [0.442, 3.084] |
| A2 (first production prediction) | ATP | dev | 59 | -0.560 | 2.321 | [0.563, 5.320] |
| A2 (first production prediction) | ATP | holdout | 49 | -0.298 | 1.066 | [-0.037, 3.236] |
| MANAGER_CONSENSUS (first consensus, any p) | CHALLENGER | full | 216 | +0.151 | 0.544 | [0.150, 1.069] |
| MANAGER_CONSENSUS (first consensus, any p) | CHALLENGER | dev | 128 | -0.384 | 1.464 | [0.784, 2.485] |
| MANAGER_CONSENSUS (first consensus, any p) | CHALLENGER | holdout | 88 | +0.665 | -0.056 | [-0.736, 0.532] |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | CHALLENGER | full | 459 | +0.474 | 0.412 | [0.016, 0.886] |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | CHALLENGER | dev | 260 | -0.150 | 1.024 | [0.465, 1.752] |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | CHALLENGER | holdout | 199 | +1.078 | -0.113 | [-0.665, 0.447] |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | ATP | full | 59 | +1.786 | -0.406 | [-1.960, 1.390] |

## 3b. Raw high-confidence (p>=0.80) gap, Challenger, development vs holdout

| Population | Period | n | Mean p | Hit | Gap | 95% CI (iid) |
|---|---|---|---|---|---|---|
| A1 (first production prediction) | dev | 109 | 0.861 | 0.771 | -0.091 | [-0.171, -0.015] |
| A1 (first production prediction) | holdout | 79 | 0.862 | 0.722 | -0.140 | [-0.246, -0.044] |
| A1 (first production prediction) | full | 188 | 0.861 | 0.750 | -0.111 | [-0.173, -0.052] |
| A2 (first production prediction) | dev | 53 | 0.865 | 0.811 | -0.054 | [-0.170, +0.043] |
| A2 (first production prediction) | holdout | 48 | 0.880 | 0.688 | -0.192 | [-0.327, -0.067] |
| A2 (first production prediction) | full | 101 | 0.872 | 0.752 | -0.120 | [-0.208, -0.040] |
| MANAGER_CONSENSUS (first consensus, any p) | dev | 20 | 0.865 | 0.900 | +0.035 | [-0.110, +0.141] |
| MANAGER_CONSENSUS (first consensus, any p) | holdout | 24 | 0.874 | 0.542 | -0.332 | [-0.537, -0.138] |
| MANAGER_CONSENSUS (first consensus, any p) | full | 44 | 0.870 | 0.705 | -0.165 | [-0.301, -0.034] |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | dev | 46 | 0.853 | 0.848 | -0.005 | [-0.113, +0.093] |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | holdout | 38 | 0.868 | 0.658 | -0.210 | [-0.370, -0.065] |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | full | 84 | 0.860 | 0.762 | -0.098 | [-0.194, -0.009] |

## 4b. Bridge-aware point-in-time support (identity_pit_v1 resolution)

Pick players: 868; resolved to a historical model identity: 688 (evidence: {'player_source_ids': 4, 'tennis_data_alias_pit': 683, 'model_identity_bridge_events': 1}).
Support per player = completed matches known before pick time for the live id plus, when the PIT resolver maps the live id to a different historical id (and the resolution was valid at that pick time), the historical id. Depth = min over the two players.

| Population | Depth bucket (min bridge-aware prior matches) | n | Hit | Mean p | Gap | 95% CI |
|---|---|---|---|---|---|---|
| A1 (first production prediction) | quartiles 14 / 42 / 114 | 1227 | | | | |
| A1 (first production prediction) | 0-29 | 486 | 0.628 | 0.650 | -0.022 | [-0.067, +0.018] |
| A1 (first production prediction) | 30-99 | 392 | 0.617 | 0.676 | -0.059 | [-0.103, -0.012] |
| A1 (first production prediction) | 100-199 | 210 | 0.619 | 0.652 | -0.033 | [-0.100, +0.031] |
| A1 (first production prediction) | 200-399 | 126 | 0.611 | 0.622 | -0.011 | [-0.095, +0.067] |
| A1 (first production prediction) | 400-inf | 13 | 0.615 | 0.598 | +0.018 | [-0.261, +0.278] |
| A1 (first production prediction) | deep (>=100) vs shallow: n 349/878 | | | | deep -0.023 [-0.076, +0.026]; shallow -0.039; diff +0.015 | diff CI [-0.044, +0.076]; deep & p>=0.80: n 33, hit 0.727 vs mean p 0.854, gap -0.126 [-0.284, +0.023] |
| A1 (first production prediction) | deep (>=200) vs shallow: n 139/1088 | | | | deep -0.008 [-0.087, +0.072]; shallow -0.037; diff +0.029 | diff CI [-0.052, +0.113] |
| A1 (first production prediction) | deep (>=100), dev | 203 | 0.591 | 0.621 | -0.030 | [-0.098, +0.038] |
| A1 (first production prediction) | deep (>=100), holdout | 146 | 0.651 | 0.665 | -0.014 | [-0.090, +0.059] |
| A2 (first production prediction) | quartiles 34 / 72 / 151 | 788 | | | | |
| A2 (first production prediction) | 0-29 | 151 | 0.682 | 0.720 | -0.037 | [-0.109, +0.039] |
| A2 (first production prediction) | 30-99 | 325 | 0.603 | 0.663 | -0.060 | [-0.113, -0.007] |
| A2 (first production prediction) | 100-199 | 186 | 0.618 | 0.609 | +0.009 | [-0.060, +0.075] |
| A2 (first production prediction) | 200-399 | 112 | 0.625 | 0.577 | +0.048 | [-0.046, +0.138] |
| A2 (first production prediction) | 400-inf | 14 | 0.643 | 0.560 | +0.083 | [-0.180, +0.315] |
| A2 (first production prediction) | deep (>=100) vs shallow: n 312/476 | | | | deep +0.026 [-0.022, +0.080]; shallow -0.053; diff +0.079 | diff CI [+0.010, +0.148] |
| A2 (first production prediction) | deep (>=200) vs shallow: n 126/662 | | | | deep +0.052 [-0.035, +0.136]; shallow -0.036; diff +0.087 | diff CI [-0.007, +0.181] |
| A2 (first production prediction) | deep (>=100), dev | 166 | 0.596 | 0.598 | -0.001 | [-0.074, +0.071] |
| A2 (first production prediction) | deep (>=100), holdout | 146 | 0.651 | 0.593 | +0.058 | [-0.020, +0.136] |
| MANAGER_CONSENSUS (first consensus, any p) | quartiles 31 / 54 / 129 | 216 | | | | |
| MANAGER_CONSENSUS (first consensus, any p) | 0-29 | 52 | 0.692 | 0.743 | -0.051 | [-0.180, +0.079] |
| MANAGER_CONSENSUS (first consensus, any p) | 30-99 | 91 | 0.615 | 0.695 | -0.080 | [-0.182, +0.018] |
| MANAGER_CONSENSUS (first consensus, any p) | 100-199 | 42 | 0.667 | 0.635 | +0.031 | [-0.114, +0.166] |
| MANAGER_CONSENSUS (first consensus, any p) | 200-399 | 28 | 0.607 | 0.597 | +0.010 | [-0.168, +0.185] |
| MANAGER_CONSENSUS (first consensus, any p) | 400-inf | 3 | n<10 | | | |
| MANAGER_CONSENSUS (first consensus, any p) | deep (>=100) vs shallow: n 73/143 | | | | deep +0.027 [-0.079, +0.131]; shallow -0.069; diff +0.096 | diff CI [-0.031, +0.231] |
| MANAGER_CONSENSUS (first consensus, any p) | deep (>=200) vs shallow: n 31/185 | | | | deep +0.020 [-0.157, +0.183]; shallow -0.046; diff +0.067 | diff CI [-0.112, +0.247] |
| MANAGER_CONSENSUS (first consensus, any p) | deep (>=100), dev | 46 | 0.652 | 0.603 | +0.049 | [-0.092, +0.178] |
| MANAGER_CONSENSUS (first consensus, any p) | deep (>=100), holdout | 27 | 0.630 | 0.641 | -0.011 | [-0.196, +0.168] |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | quartiles 31 / 61 / 116 | 459 | | | | |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | 0-29 | 107 | 0.729 | 0.752 | -0.023 | [-0.118, +0.059] |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | 30-99 | 208 | 0.697 | 0.727 | -0.030 | [-0.094, +0.034] |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | 100-199 | 96 | 0.677 | 0.695 | -0.017 | [-0.109, +0.069] |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | 200-399 | 43 | 0.767 | 0.661 | +0.107 | [-0.030, +0.231] |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | 400-inf | 5 | n<10 | | | |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | deep (>=100) vs shallow: n 144/315 | | | | deep +0.020 [-0.056, +0.094]; shallow -0.028; diff +0.047 | diff CI [-0.040, +0.134] |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | deep (>=200) vs shallow: n 48/411 | | | | deep +0.094 [-0.035, +0.213]; shallow -0.025; diff +0.119 | diff CI [-0.015, +0.248] |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | deep (>=100), dev | 72 | 0.653 | 0.686 | -0.033 | [-0.140, +0.074] |
| ALIGNED MODEL PICKS (MC>=0.60 + wide-spread) | deep (>=100), holdout | 72 | 0.750 | 0.677 | +0.073 | [-0.037, +0.172] |

Note on section 4 (live-ID-only support): superseded by 4b. Live player IDs carry history only from live ingestion (~Sep 2026), so 'depth' there mostly proxies elapsed time/player activity, not real support.
