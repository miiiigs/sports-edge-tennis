# Agent 2 Historical Lookback Shadow — Research Report

- **Trello:** `RKtcJTro`
- **Branch:** `research/a2-form-lookback-shadow-20261019`
- **Experiment date:** 2026-10-19
- **Scope:** research and shadow evaluation only. No production model activation, no live
  strategy change, no schema change, no production database mutation, no merge or deployment.
- **Current Tennis development PRs #235 and #236 were not modified.**

## Recommendation

**REJECT the 365-day and 730-day history restrictions.** They reduce selection coverage and
materially worsen Brier score, log loss and calibration. They are especially damaging for
players returning from long inactivity.

**REJECT the 1460-day window as a replacement for full-career history.** It is statistically
indistinguishable from baseline on accuracy, Brier and log loss, loses a small amount of
coverage, and has slightly worse expected calibration error (ECE). There is no measured
benefit that justifies changing the production history policy.

**Keep the existing full-career Agent 2 baseline and pursue calibration and prospective
evidence instead.** The baseline itself is overconfident at high confidence; that is a
separate, more promising problem than truncating history.

---

## 1. Hypothesis and baseline definition

### Hypothesis

Restricting Agent 2's player history to a recent window improves predictive performance and
confidence calibration without an unacceptable loss of selection coverage. A shorter window
should remove stale form and reduce overconfidence; a too-short window should starve
thinly-active players.

### Baseline

The existing Agent 2 hierarchical Markov model, unchanged:

- model version `agent2_hierarchical_markov_v1`;
- locked default `Agent2Config` (serve/return shrinkage constants, minimum 5 prior usable
  matches, minimum 250 prior service and return points);
- full-career history: every visible prior match contributes to the player and population
  accumulators;
- same scoring-rule resolver, same surface handling, same two-sided stat preprocessing.

The only experimental change is the history window used to build the player/population state.

### Decision rule

A variant would be a credible replacement only if, on the untouched test split and the common
prediction population, it improved Brier score and log loss with a paired 95% interval that
excluded zero, improved or preserved calibration, and preserved coverage/accuracy. No variant
met that bar.

---

## 2. Historical date range and dataset provenance

| Item | Value |
| --- | --- |
| Source | Production Tennis PostgreSQL database, read through Railway SSH |
| Access | `SET default_transaction_read_only = on`; no writes |
| Experiment seasons | 2016-2025 |
| Extraction cutoff | `2026-01-01T00:00:00+00:00` |
| Frozen rows | 117,713 |
| Completed rows | 113,152 |
| Eligible matches | 111,649 |
| Tournament cohorts | 2,202 |
| Frozen dataset SHA-256 | `5dceb73fdfda3ae247e72ae2a1f98ac7e63069a0a49743b07a9297b39eb6b1d5` |
| Eligible population SHA-256 | `e8607d5e80d7d1fdf7d827a60bdf3de5b46622afd283eb6ecf624b37de1c17c4` |
| Extraction manifest | `artifacts/frozen_manifest.json` |
| Full aggregate analysis | `artifacts/analysis.json` |

The frozen JSONL (117,713 rows, about 120 MB) is intentionally **not committed**. The extractor
and its manifest make the snapshot reproducible:

```powershell
python research/a2_form_lookback/extract_dataset.py > tmp/a2_frozen_20261019.jsonl
```

The extractor selects, for each match:

- the latest point-in-time-visible result observation at the cutoff;
- the latest point-in-time-visible fixture version at the cutoff;
- the latest coherent two-row `RAW_SOURCE` stat cohort at the cutoff.

All variants share these exact selected rows, player IDs, winner, timestamps and stats.

### Important provenance note

The `_manifest.git_sha` field is `e1cb79b107081c70cedc3437db3cd447a94063cf`, the remote
service's `TENNIS_REPLAY_GIT_SHA`, because the extractor ran through the app service. The
research extractor SQL was executed from branch commit `9392752`. The remote SHA is recorded
for audit completeness; no production code was changed or deployed.

---

## 3. Starting and final Git SHAs

| State | SHA |
| --- | --- |
| Starting `origin/main` (verified after fetch) | `b6b7e50` |
| Harness / test code freeze before holdout run | `9392752` |
| Report commit (this file) | See `git rev-parse HEAD` on the branch; the final branch HEAD includes this report |
| Remote service SHA used only for DB access | `e1cb79b` |

The local branch was created from `b6b7e50` after fast-forwarding `main` to current
`origin/main`. PR branches #235 and #236 were not checked out, modified or merged.

---

## 4. Sample inclusion and exclusion rules

A match is in the frozen population when all of the following hold:

1. Season 2016-2025.
2. `outcome_status = COMPLETED` and `winner_side IN (1, 2)`.
3. Tour scope `ATP_MAIN` or `CHALLENGER`.
4. Supported competition tier and a scoring rule resolvable by the production
   `resolve_scoring_rule`.
5. Surface present in the selected fixture version and one of `HARD`, `CLAY`, `GRASS`,
   `CARPET`.
6. Exactly two coherent `RAW_SOURCE` stat rows matching the two participants, with positive
   service points and coherent serve/return totals.
7. A tournament-edition start date, used only for the inactivity proxy.

Exclusions from the 117,713 extracted rows:

| Reason | Matches |
| --- | ---: |
| Non-completed / no valid winner | 4,561 |
| Incoherent or zero-point two-sided stats | 1,326 |
| Unsupported scoring rule (e.g. NextGen Finals) | 177 |
| **Eligible** | **111,649** |

### Chronological splits

| Split | Seasons | Matches | Use |
| --- | --- | ---: | --- |
| Burn-in | 2016-2018 | 34,478 | history only; never scored |
| Validation | 2019-2022 | 37,131 | exploratory comparison |
| **Test (untouched holdout)** | **2023-2025** | **40,040** | primary evidence |

The harness code was frozen at `9392752` before the test split was analysed. Validation was
inspected first; no code or metric definition changed after that inspection.

### Population composition (test split)

| Dimension | Test-split composition |
| --- | --- |
| Surface | HARD 20,588; CLAY 17,390; GRASS 2,014; CARPET 48 |
| Tour | CHALLENGER 28,297; ATP_MAIN 11,743 |
| Tier | Challenger 28,297; ATP Tour 6,115; Grand Slam 2,777; Masters 1000 2,851 |
| Season | 2023: 12,942; 2024: 13,682; 2025: 13,416 |
| All eligible splits (context) | HARD 59,406; CLAY 46,623; GRASS 5,524; CARPET 96; CHALLENGER 76,311; ATP_MAIN 35,338 |

---

## 5. Source-code and data-quality issues found

### 5.1 The historical corpus cannot support a per-match pre-match backtest

This is the central limitation.

- **0 of 117,713** experiment rows (2016-2025) have a trustworthy
  `match_versions.match_start_at`.
- Only 2026 has start times (14,740 matches). Of those, only **1,962** have any fixture
  version visible at least 6 hours before start, and only **1,492** of those have a surface.
- The stored fixture, result and stat evidence for 2016-2025 becomes visible at the
  tournament-end boundary.

The experiment is therefore a **tournament-cohort replay**, not a per-match pre-match replay:

- all matches in one tournament edition share one prediction cohort;
- the cohort is the maximum visible timestamp of the selected fixture, result and stat rows;
- all matches in the edition are predicted before any result from that edition is applied;
- a match enters history only after its fixture, result and two-sided stats are visible.

This is a valid offline history-window comparison under a conservative information set, but it
is **not** evidence of the accuracy of genuinely forward-issued Model Picks. Historical replay
results and forward Model Picks must remain separate.

### 5.2 Missing match dates and late result visibility

Because match dates are absent, the window is defined on **evidence-visibility time** (the only
trustworthy timestamp). A 365-day window means "results that became visible in the previous 365
days", not "matches played in the previous 365 days". For the Sackmann corpus these are similar
because results are visible at tournament end, but they are not identical. The report does not
claim a match-date window.

### 5.3 Surfaces

The 12,403 latest-version null-surface rows are all in the 2026 season and are outside the
experiment. Every extracted 2016-2025 row has a surface, so surface exclusion is not a
material issue for this experiment. 2026 is not.

### 5.4 Duplicate results and stats

| Issue | Count | Handling |
| --- | ---: | --- |
| Matches with two result observations | 567 | latest result at cutoff; all variants share |
| Matches with four RAW_SOURCE stat rows | 389 | latest coherent two-row cohort; all variants share |
| Matches with six RAW_SOURCE stat rows | 1 | latest coherent two-row cohort; all variants share |

Using the latest selected observation can delay a match's entry into history or use a corrected
stat row. This is a shared limitation, not a variant-specific bias.

### 5.5 Identity

- 6,133 players.
- At least 40 duplicate canonical names and 40 normalized `API_TENNIS` alias collisions were
  observed.
- No source-player-ID collisions were found.
- One model identity bridge event exists (2026-09-18), outside the historical evaluation
  period.

The experiment uses stored model player IDs consistently. Identity was **not** re-resolved
point-in-time. Some player histories may be split or merged; absolute accuracy may therefore be
optimistic or pessimistic. Because every variant sees the same IDs, the relative comparison is
still controlled.

### 5.6 Fixture-version corrections

The latest visible fixture version at the cutoff is used. Post-hoc corrections to surface or
participants could be reflected in earlier predictions. This is shared by all variants.

### 5.7 Retirements, walkovers and cancellations

Across all years the database contains 4,413 `RETIRED`, 623 `WALKOVER` and 477 `CANCELLED`
result observations. The experiment uses completed matches only. This avoids ambiguous
settlement and partial-match stat accumulation, but it excludes a real part of the population.

### 5.8 Bookmaker prices

Valid match-winner prices in the canonical database begin on **2026-09-09**. There are 126,402
snapshots over 1,550 matches, all in 2026. There are **no valid 2016-2025 historical prices**
for the experiment period. A same-fixture bookmaker-favourite baseline was therefore not
evaluable and was not fabricated.

### 5.9 Tournament sample variation

2020 has 4,480 eligible matches versus 10,873 in 2019 and 9,632 in 2021. Short-window variants
have less history in the COVID-affected period; this is reflected in the season-level results.

### 5.10 Existing recency harness

The repository already contained `validation/agent2_recency.py`, which the experiment reuses
for the exact inverse state update. That module's cohort definition uses only result visibility.
The research harness strengthens it by including fixture and stat visibility in the cohort,
grouping at tournament level, and scoring calibration/coverage. No production code was changed.

---

## 6. Experiment design

### Four history policies

| Variant | History at prediction time |
| --- | --- |
| `baseline` | all visible prior results |
| `365` | results visible in the previous 365 days |
| `730` | results visible in the previous 730 days |
| `1460` | results visible in the previous 1460 days |

All variants use:

- the same 111,649 eligible matches and the same player IDs;
- the same tournament cohorts and prediction timestamps;
- the same model configuration and scoring-rule resolver;
- the same preprocessing and outcome definitions;
- the same burn-in, validation and test boundaries;
- the same bootstrap seed (`20261019`) and 1,000 resamples.

The only difference is the active history set. Odds and results never enter the model as
features. Results are used only for post-prediction scoring; same-tournament results are not
applied before predicting that tournament.

### Metrics

- eligible, attempted, predicted, abstained, settled W/L;
- raw accuracy;
- Brier score and log loss;
- expected calibration error over ten 5-point bins from 0.50 to 1.00;
- reliability at `>= 0.70`, `>= 0.80`, `>= 0.90`, plus each 5-point bin;
- coverage overall and by surface, tier, tour, season, history bucket, inactivity and
  qualifying/main draw;
- match-level bootstrap 95% intervals and paired bootstrap differences against baseline on the
  common-prediction population.

---

## 7. Results — accuracy, calibration and coverage together

### 7.1 Test split, complete eligible population

`test = 2023-2025`, `n = 40,040` eligible matches per variant.

| Variant | Predicted | Abstained | Coverage (95% CI) | Accuracy (95% CI) | Brier (95% CI) | Log loss (95% CI) | ECE (95% CI) |
| --- | ---: | ---: | --- | --- | --- | --- | --- |
| baseline | 36,512 | 3,528 | 0.912 (0.909-0.915) | 0.6040 (0.5990-0.6091) | 0.2354 (0.2337-0.2370) | 0.6654 (0.6615-0.6693) | 0.0293 (0.0255-0.0342) |
| 365 | 34,352 | 5,688 | 0.858 (0.854-0.861) | 0.5841 (0.5788-0.5893) | 0.2446 (0.2428-0.2464) | 0.6872 (0.6829-0.6915) | 0.0712 (0.0663-0.0764) |
| 730 | 35,645 | 4,395 | 0.890 (0.887-0.893) | 0.5981 (0.5930-0.6032) | 0.2383 (0.2366-0.2399) | 0.6716 (0.6678-0.6752) | 0.0451 (0.0411-0.0501) |
| 1460 | 36,143 | 3,897 | 0.903 (0.900-0.906) | 0.6035 (0.5985-0.6086) | 0.2359 (0.2343-0.2375) | 0.6667 (0.6627-0.6706) | 0.0343 (0.0310-0.0398) |

The 365-day window loses **5.4 percentage points of coverage** and **2.0 percentage points of
raw accuracy**, with worse Brier, log loss and calibration. The 730-day window loses 2.2 points
of coverage and 0.6 points of accuracy, and is still worse on Brier, log loss and calibration.
The 1460-day window loses 0.9 points of coverage and is essentially baseline on accuracy,
Brier and log loss, but has worse calibration.

### 7.2 Common-prediction population

The common population is the set of matches predicted by all four variants: **n = 34,352**.
This is also the 365-day predicted set, because its predictions are a subset of the other
variants.

| Variant | Accuracy | Brier | Log loss | ECE |
| --- | ---: | ---: | ---: | ---: |
| baseline | 0.5997 | 0.2364 | 0.6668 | 0.0273 |
| 365 | 0.5841 | 0.2446 | 0.6872 | 0.0712 |
| 730 | 0.5963 | 0.2388 | 0.6722 | 0.0445 |
| 1460 | 0.6006 | 0.2367 | 0.6675 | 0.0328 |

### 7.3 Paired differences versus baseline (common population)

A positive Brier/log-loss/ECE difference is worse. A positive accuracy difference is better.

| Variant | n common | Delta accuracy (95% CI) | Delta Brier (95% CI) | Delta log loss (95% CI) | Delta ECE (95% CI) |
| --- | ---: | --- | --- | --- | --- |
| 365 | 34,352 | -0.0156 (-0.0210, -0.0102) | +0.0081 (+0.0067, +0.0096) | +0.0204 (+0.0168, +0.0238) | +0.0439 (+0.0381, +0.0486) |
| 730 | 34,352 | -0.0034 (-0.0078, +0.0010) | +0.0024 (+0.0014, +0.0033) | +0.0055 (+0.0032, +0.0077) | +0.0171 (+0.0125, +0.0219) |
| 1460 | 34,352 | +0.0009 (-0.0025, +0.0044) | +0.0003 (-0.0004, +0.0009) | +0.0007 (-0.0007, +0.0021) | +0.0054 (+0.0022, +0.0095) |

The 365-day and 730-day degradations are large, consistent and statistically clear. The
1460-day window is not better: its Brier/log-loss/accuracy differences include zero, while its
calibration is significantly worse.

### 7.4 Confidence reliability

Cumulative bands on the test split:

| Variant | Band | n | Mean predicted | Observed | Gap |
| --- | --- | ---: | ---: | ---: | ---: |
| baseline | >= 0.70 | 8,368 | 0.7914 | 0.7187 | 0.0727 |
| baseline | >= 0.80 | 3,172 | 0.8705 | 0.7673 | 0.1031 |
| baseline | >= 0.90 | 874 | 0.9433 | 0.8444 | 0.0989 |
| 365 | >= 0.70 | 10,835 | 0.7918 | 0.6689 | 0.1228 |
| 365 | >= 0.80 | 4,228 | 0.8644 | 0.7233 | 0.1411 |
| 365 | >= 0.90 | 1,008 | 0.9354 | 0.7956 | 0.1398 |
| 730 | >= 0.70 | 9,481 | 0.7886 | 0.6970 | 0.0916 |
| 730 | >= 0.80 | 3,488 | 0.8664 | 0.7600 | 0.1063 |
| 730 | >= 0.90 | 867 | 0.9382 | 0.8443 | 0.0940 |
| 1460 | >= 0.70 | 8,902 | 0.7901 | 0.7100 | 0.0801 |
| 1460 | >= 0.80 | 3,333 | 0.8682 | 0.7651 | 0.1032 |
| 1460 | >= 0.90 | 878 | 0.9408 | 0.8474 | 0.0934 |

All variants are overconfident. The baseline predicts about 94.3% for its `>= 0.90` band but
wins about 84.4% of those matches. The 365-day window makes this worse (79.6% observed at a
93.5% mean prediction). Truncating history does not solve calibration; it amplifies it.

Full 5-point calibration bins are in `artifacts/tables.md`.

### 7.5 Surface, tier and tour

Test split, complete population:

| Variant | Surface | n predicted | Coverage | Accuracy | Brier | ECE |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| baseline | CLAY | 15,812 | 0.909 | 0.6068 | 0.2344 | 0.0270 |
| baseline | HARD | 18,709 | 0.909 | 0.6044 | 0.2354 | 0.0293 |
| baseline | GRASS | 1,945 | 0.966 | 0.5784 | 0.2431 | 0.0791 |
| 365 | CLAY | 14,944 | 0.859 | 0.5859 | 0.2430 | 0.0644 |
| 365 | HARD | 17,473 | 0.849 | 0.5834 | 0.2449 | 0.0734 |
| 365 | GRASS | 1,891 | 0.939 | 0.5769 | 0.2536 | 0.1057 |
| 730 | CLAY | 15,481 | 0.890 | 0.6036 | 0.2371 | 0.0372 |
| 730 | HARD | 18,195 | 0.884 | 0.5952 | 0.2386 | 0.0485 |
| 730 | GRASS | 1,923 | 0.955 | 0.5809 | 0.2451 | 0.0892 |
| 1460 | CLAY | 15,689 | 0.902 | 0.6104 | 0.2347 | 0.0290 |
| 1460 | HARD | 18,470 | 0.897 | 0.6004 | 0.2362 | 0.0384 |
| 1460 | GRASS | 1,938 | 0.962 | 0.5789 | 0.2435 | 0.0853 |

| Variant | Tier | n predicted | Coverage | Accuracy | Brier | ECE |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| baseline | CHALLENGER | 24,917 | 0.881 | 0.6050 | 0.2361 | 0.0320 |
| baseline | ATP_TOUR | 6,007 | 0.982 | 0.5996 | 0.2357 | 0.0179 |
| baseline | GRAND_SLAM | 2,751 | 0.991 | 0.6125 | 0.2292 | 0.0398 |
| baseline | MASTERS_1000 | 2,837 | 0.995 | 0.5968 | 0.2339 | 0.0267 |
| 365 | CHALLENGER | 22,899 | 0.809 | 0.5786 | 0.2473 | 0.0735 |
| 365 | ATP_TOUR | 5,924 | 0.969 | 0.5876 | 0.2429 | 0.0653 |
| 365 | GRAND_SLAM | 2,718 | 0.979 | 0.6118 | 0.2304 | 0.0736 |
| 365 | MASTERS_1000 | 2,811 | 0.986 | 0.5941 | 0.2393 | 0.0634 |
| 730 | CHALLENGER | 24,095 | 0.852 | 0.5960 | 0.2404 | 0.0491 |
| 730 | ATP_TOUR | 5,976 | 0.977 | 0.5919 | 0.2378 | 0.0442 |
| 730 | GRAND_SLAM | 2,745 | 0.988 | 0.6197 | 0.2258 | 0.0537 |
| 730 | MASTERS_1000 | 2,829 | 0.992 | 0.6087 | 0.2332 | 0.0330 |
| 1460 | CHALLENGER | 24,558 | 0.868 | 0.6034 | 0.2374 | 0.0363 |
| 1460 | ATP_TOUR | 5,998 | 0.981 | 0.5950 | 0.2357 | 0.0327 |
| 1460 | GRAND_SLAM | 2,750 | 0.990 | 0.6164 | 0.2269 | 0.0473 |
| 1460 | MASTERS_1000 | 2,837 | 0.995 | 0.6102 | 0.2322 | 0.0300 |

The 365-day window's largest coverage loss is in Challengers (88.1% to 80.9%) and on
hard/clay (about 6 points). The 1460-day window remains close to baseline everywhere, but does
not improve any surface or tier.

### 7.6 Players returning after long inactivity

The return cohort is fixed across variants: **2,433 test matches** where at least one player's
previous match (independent of the variant window) was at least 180 days earlier. This is an
inactivity proxy, not verified injury data.

| Variant | Predicted | Coverage | Accuracy (95% CI) | Brier (95% CI) | ECE |
| --- | ---: | ---: | --- | --- | ---: |
| baseline | 1,305 | 0.536 | 0.6529 (0.6266-0.6782) | 0.2272 (0.2165-0.2380) | 0.0727 |
| 365 | 504 | 0.207 | 0.5317 (0.4881-0.5749) | 0.2699 (0.2500-0.2871) | 0.1524 |
| 730 | 889 | 0.365 | 0.6119 (0.5795-0.6434) | 0.2389 (0.2253-0.2524) | 0.0804 |
| 1460 | 1,120 | 0.460 | 0.6268 (0.5981-0.6546) | 0.2304 (0.2181-0.2426) | 0.0732 |

This is the clearest failure mode. A 365-day window leaves only 20.7% coverage for returning
players, and the predictions it does issue are materially worse: accuracy falls from 65.3% to
53.2% and Brier rises from 0.227 to 0.270. The baseline's return cohort accuracy is actually
higher than its overall accuracy, so full history carries useful information for these players.

### 7.7 Activity / history-sample conditions

The history-bucket composition is not fixed across variants because the window changes how much
history a player has. On the test split:

| Variant | Bucket | n predicted | Accuracy | Brier | ECE |
| --- | --- | ---: | ---: | ---: | ---: |
| baseline | 5-9 prior matches | 2,075 | 0.6689 | 0.2199 | 0.0729 |
| baseline | 50+ prior matches | 25,252 | 0.5923 | 0.2377 | 0.0206 |
| 365 | 5-9 prior matches | 3,175 | 0.6104 | 0.2386 | 0.0955 |
| 365 | 50+ prior matches | 11,111 | 0.5801 | 0.2426 | 0.0624 |
| 730 | 5-9 prior matches | 2,380 | 0.6496 | 0.2271 | 0.0799 |
| 730 | 50+ prior matches | 21,585 | 0.5898 | 0.2393 | 0.0382 |
| 1460 | 5-9 prior matches | 2,139 | 0.6583 | 0.2228 | 0.0707 |
| 1460 | 50+ prior matches | 24,136 | 0.5926 | 0.2379 | 0.0267 |

The 365-day window forces 1,087 additional matches into the thin-history bucket (3,188 eligible
versus 2,101 baseline) and performs worse there. The comparison is descriptive because the
bucket membership shifts with the window; it is not a fixed common population.

### 7.8 Season stability

| Variant | 2023 accuracy | 2024 accuracy | 2025 accuracy |
| --- | ---: | ---: | ---: |
| baseline | 0.6051 | 0.6063 | 0.6008 |
| 365 | 0.5892 | 0.5826 | 0.5806 |
| 730 | 0.5999 | 0.6011 | 0.5933 |
| 1460 | 0.6025 | 0.6061 | 0.6019 |

The direction is stable across all three test seasons. There is no season in which a short
window improves accuracy, Brier or calibration.

### 7.9 Validation versus test

| Split | Variant | Coverage | Accuracy | Brier | Log loss | ECE |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| validation | baseline | 0.927 | 0.5979 | 0.2373 | 0.6691 | 0.0392 |
| validation | 365 | 0.857 | 0.5926 | 0.2442 | 0.6889 | 0.0764 |
| validation | 730 | 0.895 | 0.5987 | 0.2391 | 0.6743 | 0.0542 |
| validation | 1460 | 0.920 | 0.5977 | 0.2369 | 0.6681 | 0.0413 |
| test | baseline | 0.912 | 0.6040 | 0.2354 | 0.6654 | 0.0293 |
| test | 365 | 0.858 | 0.5841 | 0.2446 | 0.6872 | 0.0712 |
| test | 730 | 0.890 | 0.5981 | 0.2383 | 0.6716 | 0.0451 |
| test | 1460 | 0.903 | 0.6035 | 0.2359 | 0.6667 | 0.0343 |

The validation and test results agree on direction and relative magnitude.

---

## 8. Statistical uncertainty and sample-size limitations

- Test split: 40,040 eligible matches; 36,512 baseline predictions. The overall confidence
  intervals are tight (for example, baseline accuracy 0.5990-0.6091).
- Paired bootstrap on 34,352 common predictions is the appropriate comparison for the model
  change. The 365/730 Brier, log-loss and ECE differences have confidence intervals entirely
  above zero (worse). The 1460 accuracy/Brier/log-loss differences include zero; its ECE
  difference is entirely above zero (worse).
- Return cohort: 2,433 eligible, but only 504 predictions under 365 days; the 365/730 return
  comparisons are directionally strong but have wider intervals.
- Grass (about 1,945 baseline predictions), Carpet (46) and some tier/season cells are small.
  Subgroup results are exploratory; the primary evidence is the test-split overall and common
  population.
- This is one chronological holdout (2023-2025), men's ATP/Challenger only. It does not
  establish WTA, future-season or forward-issued performance.
- Multiple metrics and cohorts were examined. The large, consistent 365/730 effects across
  validation and test are unlikely to be a multiple-comparison artifact, but small subgroup
  differences should not be treated as confirmatory.

---

## 9. Invalid comparisons, blocked analyses and failed variants

### Not valid / not evaluable

1. **Per-match pre-match backtest (2016-2025).** Blocked by missing `match_start_at` and
   tournament-end visibility. Do not interpret this report as pre-match Model Pick accuracy.
2. **Genuine 2026 forward shadow.** Only 1,962 of 14,740 2026 matches have any pre-match
   version and 1,492 have a surface; this is too small for a four-variant comparison and was
   not run. A real prospective shadow needs to accumulate after the data pipeline records
   forward fixtures.
3. **Bookmaker-favourite baseline.** No 2016-2025 prices; the 2026 odds window starts
   2026-09-09 and is outside the experiment period.
4. **Point-in-time identity.** Stored model IDs are used. Identity bridge/resolution events
   were not replayed at each historical timestamp. The relative comparison is controlled, but
   the absolute level could change with PIT identity.
5. **Same-fixture closing prices/CLV.** Not available for the historical period; no CLV claim
   is made.

### No failed variants

All four variants completed on the same 111,649-match population and produced 77,171
evaluation records each. There were no crashes, missing required matches or variant-specific
data substitutions.

### Do not blend with forward Model Picks

These are synthetic historical replays. They are not bets or Model Picks that were actually
issued, and they must not be merged with genuine forward-issued Model Pick records.

---

## 10. Recommendation

### REJECT

Reject 365-day and 730-day history restrictions:

- coverage falls from 91.2% to 85.8% / 89.0%;
- Brier worsens by +0.0081 / +0.0024 on the common population;
- log loss worsens by +0.0204 / +0.0055;
- ECE worsens by +0.0439 / +0.0171;
- returning players are hit hardest (365-day coverage 20.7%, accuracy 53.2%).

Reject 1460 days as a replacement:

- accuracy, Brier and log loss are statistically indistinguishable from baseline;
- ECE is significantly worse (+0.0054, 95% CI +0.0022 to +0.0095);
- coverage is slightly lower (90.3% vs 91.2%);
- there is no measured benefit to justify a policy change.

### Keep

Keep the full-career Agent 2 baseline. It has the best combination of coverage, Brier/log loss
and calibration in this experiment.

### More evidence before any future policy change

The most promising improvement is **calibration**, not history truncation. The baseline is
overconfident at high confidence (94.3% predicted vs 84.4% observed at `>= 0.90`). A separate
calibration experiment (temperature scaling or isotonic/binned calibration, validated
chronologically) should be run before changing the model or its history policy.

---

## 11. Next experiments worth conducting

1. **Calibration layer, not window truncation.** Fit temperature scaling or a monotone
   calibrator on pre-2023 data, validate on 2023-2024, and test on 2025. Report ECE,
   reliability at 70/80/90 and Brier/log loss. This addresses the observed overconfidence
   directly.
2. **Genuine prospective shadow.** After the data pipeline records forward fixtures and
   pre-match versions, run baseline vs 365/730/1460 on genuinely forward-issued Model Picks.
   Do not use tournament-end replay as a substitute.
3. **Injury/return-specific handling.** Do not truncate history for returning players. Test
   inactivity features, uncertainty shrinkage or a return-specific prior instead.
4. **Soft recency weighting.** Test exponential half-lives or sample-size-aware weighting
   rather than hard cutoffs. Hard windows create coverage cliffs and overconfidence.
5. **Surface/tier-specific sensitivity.** If a window is revisited, pre-register
   surface/tier cohorts with adequate sample and paired tests; do not select a window from
   exploratory subgroup wins.
6. **Identity PIT and duplicate cleanup.** Resolve model identity at each historical cutoff
   and clean duplicate canonical names/aliases, then rerun the relative comparison.
7. **Bookmaker baseline when historical prices exist.** Add a same-fixture favourite baseline
   only after genuine pre-match prices are captured; never infer prices.
8. **Closing-line/CLV evidence.** Keep separate from this accuracy experiment; CLV requires
   genuine captured prices and is not available here.

---

## 12. Reproducibility and artifacts

### Code

- `research/a2_form_lookback/extract_dataset.py` — read-only SQL extraction and manifest.
- `research/a2_form_lookback/harness.py` — tournament-cohort replay, metrics, bootstrap.
- `research/a2_form_lookback/README.md` — design and commands.
- `tests/test_a2_form_lookback.py` — synthetic eligibility, window, inverse-state, metrics and
  end-to-end tests.

### Commands

```powershell
python research/a2_form_lookback/extract_dataset.py > tmp/a2_frozen_20261019.jsonl
python research/a2_form_lookback/harness.py `
  --dataset tmp/a2_frozen_20261019.jsonl `
  --output-dir tmp/a2_form_lookback `
  --mode run --reps 1000
python research/a2_form_lookback/harness.py `
  --dataset tmp/a2_frozen_20261019.jsonl `
  --output-dir tmp/a2_form_lookback `
  --mode analyze --reps 1000
python -m pytest tests/test_a2_form_lookback.py -q
```

### Committed evidence

| Artifact | Contents |
| --- | --- |
| `artifacts/frozen_manifest.json` | dataset hash, rows, cutoff, population hash, split counts |
| `artifacts/run_manifest.json` | branch/SHA provenance, variant runtimes, record counts |
| `artifacts/data_quality.json` | coverage, missing timestamps, duplicate records, odds, identity |
| `artifacts/analysis.json` | full aggregate metrics, intervals, cohorts and reliability |
| `artifacts/cohort_cis.json` | return-cohort, Challenger, thin-history and surface intervals |
| `artifacts/tables.md` | human-readable validation/test tables |

The 54 MB per-variant prediction records and the 120 MB frozen JSONL remain in ignored `tmp/`.
They can be regenerated from the extractor and harness.

### Safety statement

- No production model activation.
- No live strategy change.
- No paid API calls.
- No production database mutation; extraction used a read-only session.
- No schema modification.
- No merge or deployment.
- Historical replay results are kept separate from genuinely forward-issued Model Picks.
