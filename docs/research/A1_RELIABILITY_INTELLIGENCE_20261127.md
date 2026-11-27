# Agent 1 reliability intelligence — separate shadow, no Agent 3 rebuild

**Status:** research only. `can_promote=false`. No production Agent 1, Agent 2, Agent 3,
Manager, Model Pick, odds, publication or settlement change. No migration, deployment,
provider call, new spend or historical model training. The frozen Agent 1 thresholds
(`0.668060`, `0.648708`), probability implementation and selected winner are unchanged.

**Date:** 2026-11-27
**Branch:** `research/a1-identity-bridge-forward-shadow-20261124`
**PR:** #388
**Reconciled PR head:** `559df57c225a3c7d2c0321aa860244f89031327e`
**Integration reference:** PR #394, head `9343a756db496fc0730e773a79ece9c782d91854`
**Module:** `src/sports_edge_tennis/validation/a1_reliability_shadow.py`
**Runner:** `ops/validation/a1_reliability_research_20261127.py`
**Tests:** `tests/test_a1_reliability_shadow.py`

## 0. Bottom line

The reliability layer is feasible, but it is **weak and not independently validated** on the
available historical population.

- **Feature audit.** Most core reliability features are genuinely observable before issuance:
  original A1 confidence, overall/surface rating separation and divergence, overall and
  surface match counts, identity method/candidate count/bridged flag, recovered matches,
  original-vs-alternative probability delta, inactivity, and tournament context. Recent
  90-day counts, per-fixture surface transitions and Agent 2 support are **not** in the frozen
  export; A2 is missing for **3,202/3,202** strict pre-match forecasts.
- **A1_RELIABILITY_SHADOW_V1** is implemented as a separate, deterministic score. It never
  changes the original Agent 1 probability, selected side or threshold. It produces an
  outcome-free capture and a separate settlement record.
- **Actual R0/R1 result (development-reused).** R1 improves the original-confidence ranking
  by only **+0.16 pp at 40%**, **+0.35 pp at 45%** and **+0.31 pp at 50%** coverage. Player and
  tournament-proxy cluster intervals all include zero. AURC improves from 0.29362 to 0.28975;
  AUGRC from 0.16174 to 0.16080; error-detection AUROC is **0.576**. This is a small
  reordering effect, not an established forecasting improvement.
- **R2/R3/R4 are synthetic only.** Commercial training rights are not established, so no
  error model was fitted on the historical corpus. On a deterministic synthetic experiment
  with a known error process, R2 improves over confidence by **+2.59 pp**, R3 by **+0.37 pp**
  and R4 (with A2) by **+4.81 pp** at 45% coverage. These validate the pipeline; they are not
  forecasting evidence.
- **Agent 3 is untouched.** The new layer is separate, research-only and cannot replace or
  relax Agent 3.

## 1. Reconciliation and scope

The remote PR head was already `559df57…`; no later worker commit was present. PR #394's
integration head `9343a75…` contains `559df57…` as an ancestor and adds unrelated forward
evidence, collector and migration work. This study is developed on PR #388 and does **not**
merge #394, so #394 remains the downstream combined-integration reference. No existing commit
is rewritten.

The task boundaries are enforced in code and evidence:

| Boundary | Status |
|---|---|
| Agent 1 probability / winner / thresholds | unchanged |
| Agent 3 implementation | not modified, not replaced |
| Production writes | none |
| Historical model training | none |
| Synthetic model training | R2/R3/R4 only |
| New provider calls / spend | none |
| `can_promote` | false |

## 2. Phase 1 — evidence and feasibility audit

### 2.1 Confirmed findings carried forward

The prior marginal study is not re-run or reinterpreted. Its confirmed results are:

- pooled marginal cohort: **59/101 (58.42%)**;
- **37/42** marginal errors involve favourites with at least 20 historical matches;
- favourite 0–4 matches: **33.3%** across 99 fixtures;
- favourite without observable history: **8.3%** across 36 fixtures;
- the shrinkage candidate failed its predefined acceptance requirement;
- no individual marginal factor survived multiplicity correction.

The newcomer problem is therefore **not** assumed to explain marginal forecasting errors. The
reliability layer is evaluated on its own.

### 2.2 Available point-in-time features and missingness

Measured on the **3,202 strict pre-match** A1 forecasts (3,223 settled minus 21 issued at or
after the listed start):

| Feature | Available | Missing | PIT rule |
|---|---:|---:|---|
| Original A1 confidence | 3,202 (100%) | 0 | original issue snapshot |
| Overall rating separation | 3,202 (100%) | 0 | effective ratings at issue |
| Surface rating separation | 2,954 (92.3%) | 248 | surface ratings at issue |
| Overall/surface divergence | 2,954 (92.3%) | 248 | surface ratings at issue |
| Historical overall match counts | 3,202 (100%) | 0 | prior visible matches |
| Recent 90-day match counts | 0 (0%) | 3,202 | not in frozen export |
| Surface-specific experience | 3,202 (100%) | 0 | prior visible surface matches |
| Identity method / candidate count | 3,202 (100%) | 0 | as-of resolution |
| Recovered history (corrected − original) | 3,202 (100%) | 0 | as-of bridge |
| Inactivity, favourite | 3,164 (98.8%) | 38 | last visible rating-corpus match |
| Inactivity, underdog | 3,125 (97.6%) | 77 | last visible rating-corpus match |
| Surface transition state | 0 (0%) in export | 3,202 | local history only |
| Forecast stability (original vs bridged) | 3,202 (100%) | 0 | as-of alternative replay |
| Agent 2 specialist support | 0 (0%) | 3,202 | absent from frozen export |
| Tournament context (tour/surface/round/qualifying) | 3,202 (100%) | 0 | fixture metadata |

Favourite history bands: 0–4 **99**, 5–9 **44**, 10–19 **132**, 20–49 **420**, 50–99 **400**,
100+ **2,107**. Identity methods for the favourite: `ALIAS` **2,851**,
`NO_HISTORICAL_CANDIDATE` **268**, `AMBIGUOUS` **81**, `PERSISTED_BRIDGE` **2**. Surface is
missing for **402** fixtures; round is unknown for **171**; **992** are qualifying.

The **21** excluded rows are the prior audit's at/after-start forecasts (0–42 seconds for 20,
42.9 minutes for one). A reliability layer must not score a forecast issued after the match
started. The prior marginal experiment still uses the 3,223 settled population; this layer uses
3,202.

**Genuinely observable before issuance:** the original probability and side; ratings and
counts; identity resolution; recovered history; inactivity; the original-vs-bridged
probability delta; tour/surface/round/qualifying.

**Not available or not safely usable:** recent 90-day counts, per-fixture surface transitions
from the frozen export, Agent 2 support/disagreement, and decimal bookmaker prices. These are
reported as missing, not imputed as if observed.

### 2.3 Why this is a separate layer

The reliability score is not a probability. It does not estimate a new winner and it does not
change the Agent 1 estimate. It answers a different question: *how much should we trust the
information supporting this already-issued forecast?* The score is deterministic and
inspectable; there is no fitted coefficient in R1.

## 3. Phase 2 — A1_RELIABILITY_SHADOW_V1

### 3.1 Target and separation

- **Target:** whether the **original** Agent 1 selected winner is correct.
- **Preserved:** original `p1`, selected side, confidence and both frozen thresholds.
- **Separate:** reliability score, tier, risk points and selection score.
- **Not Agent 3:** no Agent 3 input, output, abstention reason or policy is read or changed.

### 3.2 Feature contract

`A1_RELIABILITY_FEATURES_V1` is a frozen 34-field contract with a type, requiredness, PIT rule
and missingness policy for each field. The complete schema is emitted in
`a1_reliability_feature_audit_20261127.json`.

Every feature must satisfy `feature_as_of <= prediction_as_of`. Outcome keys are rejected by
an allowlist; `winner_side`, `y`, `correct`, `result`, `outcome`, `score`, `settled_at` and
similar fields cannot enter a capture.

### 3.3 Deterministic score

Risk points are fixed before evaluation:

| Indicator | Points |
|---|---:|
| Favourite matches 0–4 / 5–9 / 10–19 / 20+ | 3 / 2 / 1 / 0 |
| Minimum surface matches <5 | 1 |
| Identity unresolved (`AMBIGUOUS`/`NO_HISTORICAL_CANDIDATE`) or multi-candidate | 2 / 1 |
| Forecast unstable: side changed or \|Δp\| ≥0.10 | 2 |
| Favourite overall/surface divergence ≥100 Elo | 1 |
| Absolute effective rating gap <50 | 1 |
| Inactivity missing | 0.5 (neutral) |
| Surface rating missing | 0.5 (neutral) |
| Surface context missing | 0.5 (neutral) |

`reliability_score = 1 − risk_points / 11`, clipped to [0,1].
Tiers: **HIGH** ≤2 points, **MEDIUM** 3–5, **LOW** >5.
R1 selection score: `confidence − 0.005 × risk_points`, clipped to [0.5,1]. The 0.5 pp
per-point coefficient is deliberately smaller than one confidence band; reliability can
reorder near-neighbours but cannot dominate the Agent 1 probability.

Missing optional context contributes a neutral 0.5 point and is listed in
`missing_features`. It is never treated as a clean signal.

### 3.4 Prospective evidence format

A capture contains only pre-outcome fields: capture id, prediction/match ids, prediction as-of,
scheduled start, captured-at, original probability/side, risk points, reliability score/tier,
selection score, indicator contributions, missing features, feature snapshot, snapshot hash,
and `outcome_included=false`, `production_changed=false`, `can_promote=false`. Capture time
must satisfy `prediction_as_of <= captured_at < match_start_at`.

Settlement is a separate record keyed by `capture_id`. It adds `winner_side`, `a1_correct` and
`settled_at`; it cannot mutate the capture and cannot be written back into the capture file.

## 4. Phase 3 — chronological validation

### 4.1 Split discipline and leakage control

- Base model (Agent 1) is already frozen; it is not refitted here.
- R1 is a fixed rule, so its weights are not learned from outcomes.
- R2/R3/R4 use an explicit train/calibration/test split **only on synthetic data**.
- Threshold calibration is separated from model fitting: R2/R3 probabilities are Platt-calibrated
  on a calibration split, then the selection threshold is fixed and applied to test.
- `feature_as_of` must not be later than `prediction_as_of`; outcome keys are rejected; the
  capture format has no post-match fields. Identity revisions are consumed from the as-of
  resolution record; no current-state name table or post-match rating is read.
- The actual R0/R1 evaluation is on the 3,202 strict pre-match subset of the previously
  examined 3,223 settled population. It is labelled
  **`AUTHORIZED_DEVELOPMENT_REUSED_NOT_INDEPENDENT`** everywhere.

### 4.2 R0 vs R1 — actual authorized evaluation

Original A1 (not corrected bridge) confidence ranking, 3,202 strict pre-match fixtures:

| Coverage | R0 selections | R0 wins | R0 accuracy | R1 wins | R1 accuracy | R1−R0 |
|---:|---:|---:|---:|---:|---:|---:|
| 40% | 1,280 | 926 | 72.34% | 928 | 72.50% | **+0.16 pp** |
| 45% | 1,440 | 1,033 | 71.74% | 1,038 | 72.08% | **+0.35 pp** |
| 50% | 1,601 | 1,127 | 70.39% | 1,132 | 70.71% | **+0.31 pp** |

Paired cluster intervals (R1−R0 accuracy) all include zero:

| Coverage | Player-cluster 95% CI | Tournament-proxy 95% CI |
|---:|---|---|
| 40% | −0.59 to +0.77 pp | −0.55 to +0.73 pp |
| 45% | −0.33 to +0.84 pp | −0.31 to +0.85 pp |
| 50% | −0.25 to +0.85 pp | −0.25 to +0.88 pp |

The tournament-proxy cluster is `date|tour|tier|surface`; the frozen export has no tournament
identifier, so this is a proxy, not a true tournament cluster.

### 4.3 R2/R3/R4 — synthetic-only

The synthetic experiment has 6,000 rows, a known error process, and a 3,600/1,200/1,200
train/calibration/test split. A2 is present for 89.75% of training rows, so the R4 coverage
gate is met in synthetic data.

| Arm | Model | Features | Test n | Selected | Accuracy | Baseline | Δ baseline | Error AUROC |
|---|---|---|---:|---:|---:|---:|---:|---:|
| R0 | confidence only | original confidence | 1,200 | 540 | 67.96% | 67.96% | — | — |
| R2 | ridge logistic | reliability features, no A2 | 1,200 | 540 | **70.56%** | 67.96% | **+2.59 pp** | 0.725 |
| R3 | gradient-boosted stumps | reliability features, no A2 | 1,200 | 540 | 68.33% | 67.96% | +0.37 pp | 0.623 |
| R4 | ridge logistic | reliability + A2 | 1,200 | 540 | **72.78%** | 67.96% | **+4.81 pp** | 0.782 |

R3 passes the pre-registered sample-size gate (3,600 training rows, >200 errors) but does not
outperform R2; there is no evidence to prefer boosting. R4's gain is synthetic and depends on
A2 being present; A2 is 0% available in the real export, so R4 is **blocked** for actual use.

These are methodology validation results, not claims of forecasting improvement.

### 4.4 Commercial-rights constraint

The historical corpus is predominantly Sackmann-associated and commercial training rights are
not verified. No R2/R3/R4 model was fitted on it. The actual experiment is limited to the
fixed R1 rule. This is why the report separates actual results from synthetic results.

## 5. Phase 4 — equivalent-coverage comparison

### 5.1 Risk–coverage and error detection

| Metric | R0 confidence | R1 reliability-adjusted |
|---|---:|---:|
| AURC | 0.29362 | **0.28975** |
| AUGRC (NeurIPS-2024 finite-pool, tie-averaged) | 0.16174 | **0.16080** |
| Error-detection AUROC (risk points) | — | **0.576** |
| Errors in full population | 1,193 | 1,193 |

The risk–coverage curve is committed as
`a1_reliability_risk_coverage_20261127.csv` (3,202 ranks per ranking).

### 5.2 Reliability calibration

| Tier | n | Errors | Error rate | Accuracy | Mean confidence | Mean reliability |
|---|---:|---:|---:|---:|---:|---:|
| HIGH (≤2 points) | 1,817 | 583 | 32.1% | 67.9% | 0.701 | 0.920 |
| MEDIUM (3–5) | 1,112 | 476 | 42.8% | 57.2% | 0.660 | 0.670 |
| LOW (>5) | 273 | 134 | 49.1% | 50.9% | 0.630 | 0.391 |

The tier ordering is monotone. The score has real but weak discrimination; it is not a
calibrated error probability.

### 5.3 Retained, removed and added selections

| Coverage | Retained | Removed | Removed acc. | Added | Added acc. |
|---:|---:|---:|---:|---:|---:|
| 40% | 1,249 | 31 | 54.8% | 31 | 61.3% |
| 45% | 1,411 | 29 | 51.7% | 29 | 69.0% |
| 50% | 1,572 | 29 | 48.3% | 29 | 65.5% |

The R1 movement is a small near-boundary swap, not a broad rejection of fixtures. Equal
coverage is preserved; the comparison does not improve accuracy by rejecting more matches.

### 5.4 ATP and Challenger

| Coverage | R0 ATP | R0 CH | R1 ATP | R1 CH |
|---:|---:|---:|---:|---:|
| 40% | 282 / 75.5% | 998 / 71.4% | 284 / 75.7% | 996 / 71.6% |
| 45% | 310 / 75.2% | 1,130 / 70.8% | 312 / 75.3% | 1,128 / 71.2% |
| 50% | 340 / 75.3% | 1,261 / 69.1% | 341 / 75.1% | 1,260 / 69.5% |

The ATP and Challenger changes are within one or two selections; no tier-specific reliability
claim is supported.

### 5.5 Market context

The frozen export has **501** eligible pre-forecast market rows (0 ≤ age ≤ 24h). Decimal prices
are not in the sanitized export, so only market-implied probabilities and `1/p` implied prices
are reported:

| Tier | n | Mean market probability | Mean implied decimal (1/p) |
|---|---:|---:|---:|
| ATP | 68 | 0.573 | 2.346 |
| Challenger | 433 | 0.513 | 2.555 |

`1/p` is **not** a quoted decimal price and is not used in selection.

### 5.6 Fixed-volume versus prospective threshold

The tables above are **retrospective fixed-volume rankings**: choose the top k rows. A live
policy would freeze a score threshold and accept the resulting volume. An expanding-origin
walk-forward (threshold from earlier weeks, applied to the next week) gives:

| Coverage target | R0 n / accuracy / achieved coverage | R1 n / accuracy / achieved coverage |
|---:|---|---|
| 40% | 836 / 71.41% / 39.42% | 821 / 71.62% / 38.71% |
| 45% | 930 / 70.86% / 43.85% | 922 / 71.37% / 43.47% |
| 50% | 1,043 / 69.51% / 49.17% | 1,024 / 70.31% / 48.28% |

The walk-forward is also development-reused and not independent. R1 is slightly more accurate
at lower achieved coverage; the difference is not established.

## 6. Phase 5 — delivery

### 6.1 Research framework

- `a1_reliability_shadow.py`: frozen contract, deterministic score, capture/settlement,
  risk–coverage/AURC/AUGRC/AUROC, cluster intervals, synthetic R2/R3/R4 utilities.
- `a1_reliability_research_20261127.py`: offline runner; no database, provider or production
  access.
- `test_a1_reliability_shadow.py`: 15 regression tests covering contract, scoring,
  missingness, capture integrity, leakage rejection, metrics, synthetic gates and committed
  evidence.

### 6.2 Evidence artifacts

| Artifact | Contents |
|---|---|
| `a1_reliability_feature_audit_20261127.json` | 33-feature contract, availability, missingness, PIT status |
| `a1_reliability_actual_20261127.json` | R0/R1 fixed-volume, risk–coverage, AUROC, tiers, cluster CIs, walk-forward, market context |
| `a1_reliability_synthetic_20261127.json` | R2/R3/R4 synthetic training and test results |
| `a1_reliability_scores_20261127.csv` | 3,202 per-fixture scores |
| `a1_reliability_risk_coverage_20261127.csv` | 6,404 rank rows |
| `a1_reliability_manifest_20261127.json` | hashes, thresholds, research flags |

All hashes are LF-normalized and verified.

### 6.3 Validation

- New suite: **16 passed**.
- Combined with the prior marginal/selective suites: **47 passed** locally.
- `ruff` clean.
- Exact-head PostgreSQL Foundation CI and GitGuardian checks are required after push.

## 7. Decision

**`can_promote=false`.** The controlled research direction is feasible, but the evidence does
not justify a production reliability agent:

1. R1's actual gain is +0.16/+0.35/+0.31 pp at 40/45/50% coverage, with cluster intervals
   spanning zero. It is a small boundary reordering, not a demonstrated improvement.
2. The reliability score has weak error discrimination (AUROC 0.576) and monotone but
   uncalibrated tiers.
3. R2/R3/R4 cannot be trained on the historical corpus until commercial rights are
   established. Synthetic validation is not forecasting evidence.
4. Agent 2 support is absent from the frozen export; R4 is not testable on real data.
5. Agent 3 is untouched and remains the production reliability/intransitivity mechanism.

The next legitimate step is a **prospective, outcome-free capture** of A1_RELIABILITY_SHADOW_V1
records with an independent timestamp, followed by separate settlement. Only after sufficient
first-issued records and rights clearance should R2/R3/R4 be trained and compared. Do not
replace Agent 3 or change the frozen Agent 1 policies on this evidence.
