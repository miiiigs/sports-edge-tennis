# Agent 1 marginal forecasting weaknesses — marginal cohorts, newcomer favourites, ATP/Challenger and information reliability

**Status:** research only. `can_promote=false`. No production Agent 1, Agent 2, Agent 3,
Manager, Model Pick, odds, publication or settlement change. No migration applied, no
deployment, no provider call, no new spend and no new commercial training data. The frozen
primary (`0.668060`) and secondary (`0.648708`) thresholds and the probability
implementation are untouched.

**Date:** 2026-11-27
**Branch:** `research/a1-identity-bridge-forward-shadow-20261124`
**PR:** #388
**Reconciled head:** `786b01f6e69eaff2d9a7c7ce5082d1eb58f09410` (remote head unchanged at fetch)**Code:** `src/sports_edge_tennis/validation/a1_marginal_research.py`,
`ops/validation/a1_marginal_research_20261127.py`
**Tests:** `tests/test_a1_marginal_research.py`
**Manifest:** `docs/research/evidence/a1_marginal_research_manifest_20261127.json`

## 0. Bottom line

The frozen cohorts reproduce exactly. On the untouched 2026-11-01..25 holdout:

| Cohort | n | W | Accuracy | Wilson 95% | Mean conf. | Calibration gap |
|---|---:|---:|---:|---|---:|---:|
| Conservative 40% (`>=0.683286`) | 375 | 294 | 78.40% | 74.0–82.3% | 0.7730 | −1.10 pp |
| Marginal 40→45% (`0.668060..0.683286`) | 45 | 27 | **60.00%** | 45.5–73.0% | 0.6753 | **+7.53 pp** |
| Primary 45% (`>=0.668060`) | 420 | 321 | 76.43% | 72.1–80.2% | 0.7626 | −0.17 pp |
| Marginal 45→50% (`0.648708..0.668060`) | 56 | 32 | **57.14%** | 44.1–69.2% | 0.6582 | **+8.67 pp** |
| Secondary 50% (`>=0.648708`) | 476 | 353 | 74.16% | 70.0–77.9% | 0.7503 | +0.87 pp |
| Below secondary | 549 | 304 | 55.37% | 51.2–59.5% | 0.5698 | +1.61 pp |

The two marginal slices are **58.42% (59/101)** pooled and overconfident by **8.17 pp**.
Against the conservative core they lose **19.98 pp** (95% CI −30.47 to −9.83). The
retrospective 3,223 ranking shows the same shape: 66.46% in the 40→45% band and 55.90% in the
45→50% band.

The central finding is a **separation of two different weaknesses**:

1. **The marginal 45–50% weakness is not the newcomer-favourite problem.** 88% of marginal
   errors (37/42) come from favourites with **20+ observed matches**, and exactly half
   (21/42) from favourites with **100+ matches**. Only 5/42 marginal errors involve a
   favourite with fewer than 20 matches. The marginal band is a genuinely close-match,
   low-separation cohort, not a thin-history cohort.
2. **The newcomer-favourite problem is real and much larger, but it lives outside the
   marginal band.** A favourite with 0–4 observed matches wins 33.3% (99 fixtures; +27.1 pp
   overconfident). A favourite with **no observable history at all** wins **8.3%** (36
   fixtures; +52.6 pp). The identity bridge helps the recoverable part of this group
   (+7.2 pp on 83 fixtures), but the improvement is not statistically established (95% CI
   −8.0 to +22.5) and it does nothing for players with no history anywhere.

Other results:

- **ATP vs Challenger.** The crude Challenger deficit is −5.10 pp (95% CI −9.15 to −0.84).
  Adjusting for confidence band, round or rating gap narrows it to roughly −2.4 to −3.0 pp;
  adjusting for experience does **not** explain it (−9.4 pp). On the 501 eligible
  pre-forecast market quotes the market also shows a Challenger gap (−6.64 pp, 95% CI
  −16.50 to +5.55), so a competition component is present; but the model's gap on the same
  fixtures is −12.96 pp (95% CI −22.64 to −0.81), roughly twice as large, and the market is
  4.8 pp better in Challenger while the ATP comparison is inconclusive. The Challenger deficit
  is model/information-specific on top of a competition component, not a pure classification
  effect. The ATP market sample is retrospective and small (68).
- **Existing uncertainty mechanisms.** Match counts affect the learning rate (dynamic K) and
  surface counts affect the blend, but identity reliability, inactivity and overall/surface
  divergence do not enter the probability. Overall calibration is good; the exceptions are
  the low-history favourite (a level bias) and a composite 3+ risk-factor group (56.9%,
  +8.1 pp overconfident full; 52.7%, +10.8 pp on the holdout). A log-odds shrinkage candidate
  is **not justified** (development slope 2.25, not <0.95). A reliability flag is reported as
  a diagnostic only.
- **Inactivity and surface transitions do not explain the marginal errors.** 181d+ inactivity
  is *stronger* (72.7%) than 0–7d (63.7%). A favourite's first match on a new surface is
  66.7% (n=102), essentially the same as continuing (66.4%). The marginal errors sit in
  continuing-surface close matches. Underdog surface transitions are associated with a
  *higher* favourite win rate (79.1% first-on-new-surface, n=86), not a lower one.
- **Identity ledger.** 20 deterministic players (16 highest effective-rating changes + the 4
  multi-live-id models). All pass internal source-consistency checks; 2 persisted-bridge and
  193 ambiguous slots are reported separately; no alias-to-multiple-model collision and no
  model-to-multiple-alias split was found in the settled population. Three selected players
  carry a same-day duplicate-pair flag. The 16,105-match census and the 16,104-row extract
  differ by one cutoff record; the per-slot census closes the five-slot reporting gap at
  7,552/7,552 method records.

## 1. Scope, freeze and method

The remote branch head at fetch was `786b01f6e69eaff2d9a7c7ce5082d1eb58f09410`; no later
authorized worker commit was present. This change is additive and does not rewrite any
existing commit.

### 1.1 Frozen policies reproduced from development only

The thresholds are the confidence of the requested coverage rank in
2026-09-10..2026-10-31 (2,198 fixtures). No November outcome enters them.

| Target | Development rank | Threshold | Holdout selections | Holdout wins |
|---|---:|---:|---:|---:|
| Conservative 40% | 879 | 0.683286 | 375 | 294 |
| Primary 45% | 989 | 0.668060 | 420 | 321 |
| Secondary 50% | 1,099 | 0.648708 | 476 | 353 |

The four additional winners required by the secondary candidate to reach 75% would be
`ceil(0.75 × 476) − 353 = 4`. This document does **not** search for those four winners. It
describes the population they sit in.

### 1.2 Evidence classification

This is **corrected retrospective research on a development-reused population**. It is not
the stored production forecast, not a strict point-in-time replay of every request identity,
and not prospective validation. The identity bridge and surface handling were developed with
knowledge of the whole period. The 3,223-row experiment was measured under the pre-v2
permissive-equivalent resolver; migration `0025` (v3) is not applied, so this is not a v3
result.

### 1.3 Statistical conventions

- Wilson score intervals for single proportions.
- Newcombe hybrid score intervals (method 10) for differences of proportions.
- Haldane–Anscombe odds ratios with Woolf log intervals for 2×2 tables.
- Cohen's *d* (error minus correct) for continuous factors, with an analytic normal interval.
- A calendar-day block bootstrap utility (2,000 reps, seed 20261127) is implemented for
  dependence-aware intervals, but the reported cohort intervals are Wilson/Newcombe because
  the primary quantities are single-proportion and two-proportion comparisons.
- Benjamini–Hochberg q-values across the exploratory factor screen. No factor is
  confirmatory; the marginal samples are 45 and 56 selections.
- Missing values are reported, never imputed.

### 1.4 Data and restrictions

Sources are the committed sanitised population
(`a1_selective_population_20261126.csv`, 3,223 rows), the local reproduction ledger
(`tmp/ledger_full.jsonl`, 3,776 E3 targets), the observed API result history
(`tmp/api_rating_history.json`, 3,958 matches from 2026-09-01) and the bridge visibility
extract (`tmp/bridge_visibility.json`, 16,104 rows). The local extracts are reproduction
artifacts, not new licensed data.

The historical rating corpus is predominantly **Sackmann-associated** and its commercial
training rights are **not verified**. This study does not fit new parameters on it, does not
import new historical data and does not expand restricted use. PR #351's no-spend
sample/rights acceptance gate remains the gate for any future commercial training use.

The market benchmark is restricted to quotes that existed **at or before the forecast** and
are at most 24 hours old (`0 <= market_age_hours <= 24`): 501 of 1,610 quoted rows. Another
1,092 rows are post-forecast quotes and 17 have no usable age; all are excluded from every
benchmark and factor. A sensitivity using all quotes is reported only to show the size of the
look-ahead distortion.

### 1.5 External evidence alignment

These studies are cited as context, not imported as data. Their populations, rating
implementations and definitions differ from ours; the table records agreement and disagreement
without pooling samples.

| External finding | This study | Interpretation |
|---|---|---|
| Pronocast newcomer study (95,100 matches, 2022–2026): newcomer favourite 41.1% vs 63.1% expected (665 matches); under-5 21.7%, 5–9 51.6%, 10–19 51.1%; newcomer underdog raises the experienced favourite to 85.6% vs 78.5%. | Favourite 0–4 **33.3%** (99), 5–9 **52.3%** (44), 10–19 **60.9%** (133); underdog 0–4 **80.6%** (248). | Same direction and same monotonic shape. Our 0–4 effect is smaller than their under-5 effect, and our 10–19 group is less severe. Our ATP low-history sample is 1–2 fixtures, so we cannot compare tours. |
| Pronocast Challenger study (86,865 matches): equal-probability ATP/Challenger gap **+0.2 pp** (95% CI −0.6 to +1.0); market beats Elo by 2.3 pp (CH) and 3.2 pp (ATP). | Crude model gap **−5.10 pp**; after confidence/round/rating-gap adjustment **−2.4 to −3.0 pp**; market beats the model by **4.8 pp** in CH on 433 eligible quotes. | Different model and population; not directly comparable. Their result is Elo-only and finds no tier effect at equal probability; ours finds a residual model-specific gap after adjustment. The market remains stronger in CH. |
| Pronocast surface study (36,164 matches): global Elo **−3.6 pp** after a switch; surface Elo **−0.3 pp** (CI −1.3 to +0.8); favourites after a switch **−4.2 pp**, underdogs **+3.4 pp**; first/second match effect, gone by the third or fourth. | First match on a new surface for the favourite **66.7%** (102) vs **66.4%** continuing; marginal cohort first-on-new-surface n=4. | Consistent with the average switch effect being absorbed by surface Elo. Our marginal sample cannot test the favourite-specific residual. We do not add a surface-transition penalty. |
| Sackmann/Tennis Abstract layoff study (ATP Elo ≥1900, layoffs ≥8 weeks): first match back overestimated by ~25%; 8–10 weeks ~17%, 30–52 weeks ~50%; an 8-week break is worth ~100 Elo, a year ~150; post-layoff multiplier 1.5 for 20 matches. | 181d+ inactivity is **stronger** (72.7%, n=209) than 0–7d (63.7%); 31–90d 63.8%. | Apparent contradiction is a **measurement** issue: our inactivity is time since the last rating-corpus match and can be long because lower-level history is absent, not because the player was injured. Without verified layoffs we cannot test the injury effect and must not add a universal penalty. |
| Pronocast season study (38,071 matches): January **+0.1 pp**, October–November **+0.3 pp**, June **−2.5 pp** (June grass −3.5 pp); no start/end effect. | Population is September–November 2026 only; January and June are absent. | The literature does not support a January penalty; we did not add one. Off-season is **blocked** in our data. |
| Glicko (Glickman; Yue et al. 2022, PLOS ONE): rating deviation, time factor and opponent RD explicitly represent uncertainty; Glicko gives a small accuracy edge (~1 pp over ~70%) in Grand Slam data. | Dynamic K and surface weight are present; RD, identity confidence, inactivity and overall/surface divergence are not explicit. A simple shrinkage candidate is not justified (dev slope 2.25). | Glicko is a principled alternative but not supported as a replacement by this evidence. The audit identifies which reliability signals are missing; it does not license a new rating system. |

## 2. Phase 1 — exact marginal cohorts and factor screen

### 2.1 Frozen cohorts

The exact counts are in §0 and in
`docs/research/evidence/a1_marginal_cohorts_20261127.csv` (476 unique holdout selections,
labelled `core_40` / `marginal_40_45` / `marginal_45_50`).

Marginal cohort composition:

| Attribute | 40→45 (n=45) | 45→50 (n=56) | Core 40 (n=375) |
|---|---:|---:|---:|
| ATP / Challenger | 3 / 42 | 5 / 51 | 50 / 325 |
| Mean confidence | 0.6753 | 0.6582 | 0.7730 |
| Mean absolute Elo gap | 127.2 | 113.8 | 222.0 |
| Mean favourite matches | 200.2 | 209.7 | 222.0 |
| Mean underdog matches | 147.4 | 187.1 | 122.8 |
| Mean min surface matches | 23.1 | 47.3 | 31.6 |
| Surface missing | 6 (13.3%) | 7 (12.5%) | 42 (11.2%) |
| `side_changed` | 5 (11.1%) | 10 (17.9%) | 19 (5.1%) |
| Any ambiguous identity | 5 (11.1%) | 6 (10.7%) | 22 (5.9%) |
| Favourite no observable history | 7 (15.6%) | 2 (3.6%) | 38 (10.1%) |
| Favourite <10 matches | 5 (11.1%) | 1 (1.8%) | 17 (4.5%) |
| Eligible market quote | 3 (6.7%) | 1 (1.8%) | 33 (8.8%) |

The marginal cohorts are mechanically close matches: the model's own confidence is capped by
a small Elo gap. `side_changed` is 2.9× more prevalent in the pooled marginal cohort than in
the core (14.9% vs 5.1%; OR 3.27; p=0.0018), and overall/surface divergence ≥100 Elo is 1.35×
more prevalent (43.6% vs 32.3%; OR 1.62; p=0.045). Model-vs-market disagreement is **not**
enriched on the eligible pre-forecast subset (2.0% vs 1.1%; p=0.61), and neither is low
history (favourite <10: 5.9% vs 4.5%; p=0.60). These are composition facts, not causal claims.

### 2.2 Retrospective 40–45% and 45–50% bands

| Band | n | W | Accuracy | Wilson 95% | Mean conf. | Gap | vs 40% top |
|---|---:|---:|---:|---|---:|---:|---|
| Top 40% | 1,289 | 1,000 | 77.58% | 75.2–79.8% | 0.7728 | −0.30 pp | — |
| 40→45% | 161 | 107 | 66.46% | 58.9–73.3% | 0.6719 | +0.73 pp | −11.12 pp (−19.03 to −3.89) |
| 45→50% | 161 | 90 | 55.90% | 48.2–63.3% | 0.6538 | +9.48 pp | −21.68 pp (−29.70 to −13.87) |
| Below 50% | 1,612 | 919 | 57.01% | — | 0.5709 | +0.08 pp | — |

The retrospective 45→50 band is the weakest slice in the whole experiment. Its +9.48 pp
overconfidence is the clearest calibration failure, and it is not a small-sample artefact of
the November holdout (n=161).

### 2.3 Factor screen — what is associated with errors

Screen on all 3,223 settled fixtures (corrected probabilities). Factors with the largest
error association:

| Factor | n present | Error present | Error absent | Risk diff. | OR (95%) | BH q |
|---|---:|---:|---:|---:|---|---:|
| Model disagrees with market | 108 | 59.3% | 33.5% | +25.8 pp | 2.89 | <0.001 |
| Favourite <10 observed matches | 143 | 60.8% | 33.1% | +27.7 pp | 3.14 | <0.001 |
| Elo gap <50 | 830 | 45.9% | 30.3% | +15.6 pp | 1.95 | <0.001 |
| Favourite no historical candidate | 271 | 50.9% | 32.8% | +18.1 pp | 2.12 | <0.001 |
| Elo gap ≥200 | 645 | 16.4% | 38.8% | −22.4 pp | 0.31 | <0.001 |
| Underdog <10 observed matches | 380 | 21.1% | 36.1% | −15.1 pp | 0.47 | <0.001 |
| Favourite recovered history | 2,779 | 32.4% | 46.6% | −14.2 pp | 0.55 | <0.001 |
| Side changed by bridge | 547 | 41.5% | 32.9% | +8.6 pp | 1.45 | <0.001 |
| Favourite surface history <5 | 562 | 40.9% | 33.0% | +8.0 pp | 1.41 | 0.001 |
| Any inactivity >90d | 323 | 27.9% | 35.1% | −7.2 pp | 0.72 | 0.018 |

A short favourite history is the strongest *information* predictor of error after market
disagreement. A short underdog history and a recovered favourite history are **protective**
(the favourite wins more often). Long inactivity is also protective in this population, which
is the opposite of a naive layoff penalty.

Within the pooled marginal cohort (n=101, 42 errors), the same factors are present but the
sample is too small for multiplicity control:

| Factor | n present | Error present | Error absent | Risk diff. | OR | Fisher p | BH q |
|---|---:|---:|---:|---:|---|---:|---:|
| Favourite no historical candidate | 9 | 77.8% | 38.0% | +39.7 pp | 5.70 | 0.032 | 0.348 |
| Side changed by bridge | 15 | 66.7% | 37.2% | +29.5 pp | 3.38 | 0.046 | 0.348 |
| Favourite <10 observed matches | 6 | 66.7% | 40.0% | +26.7 pp | 3.00 | 0.230 | 0.553 |
| Underdog <10 observed matches | 14 | 14.3% | 46.0% | −31.7 pp | 0.20 | 0.039 | 0.348 |
| Favourite recovered history | 90 | 38.9% | 63.6% | −24.7 pp | 0.36 | 0.193 | 0.520 |
| Surface missing | 13 | 53.8% | 39.8% | +14.1 pp | 1.77 | 0.377 | 0.707 |
| Minimum surface history <5 | 37 | 37.8% | 43.8% | −5.9 pp | 0.78 | 0.676 | 0.845 |
| Any inactivity >90d | 10 | 30.0% | 42.9% | −12.9 pp | 0.57 | 0.516 | 0.774 |

No marginal-pool factor survives BH at q<0.05. The three largest signals — favourite
no-history, bridge-induced side change and a short favourite history — are hypotheses for
prospective monitoring, not selection rules. They are also rare: 9, 15 and 6 fixtures
respectively. Model-vs-market disagreement is a strong general error predictor on the
eligible pre-forecast subset (108 fixtures, 59.3% error, q<0.001) but is **not** enriched in
the marginal cohort (2.0% vs 1.1%; p=0.61).

Continuous factors within the marginal pool: errors have a **smaller surface Elo gap**
(SMD −0.43), **more experienced underdogs** (SMD +0.41), a **smaller absolute surface rating
gap** (SMD −0.38), **fewer favourite matches** (SMD −0.32) and a **higher minimum surface
count** (SMD +0.29, driven by the 45→50 band's 47.3 mean). The favourite's own match count is
only mildly lower in errors (172 vs 229).

### 2.4 Marginal cohort versus core

| Factor | Marginal pool | Core 40 | Difference | OR | p |
|---|---:|---:|---:|---|---:|
| Side changed | 14.9% | 5.1% | +9.8 pp | 3.27 | 0.0018 |
| Favourite overall/surface divergence ≥100 | 43.6% | 32.3% | +11.3 pp | 1.62 | 0.045 |
| Inactivity missing | 6.9% | 2.4% | +4.5 pp | 3.03 | 0.054 |
| Any ambiguous identity | 10.9% | 5.9% | +5.0 pp | 1.96 | 0.119 |
| Favourite <10 matches | 5.9% | 4.5% | +1.4 pp | 1.33 | 0.600 |
| Favourite no observable history | 8.9% | 10.1% | −1.2 pp | 0.87 | 0.852 |
| Surface missing | 12.9% | 11.2% | +1.7 pp | 1.17 | 0.603 |
| Model disagrees with market | 2.0% | 1.1% | +0.9 pp | 1.87 | 0.612 |
| Elo gap (mean) | 119.8 | 222.0 | SMD −1.48 | — | — |
| Confidence (mean) | 0.67 | 0.77 | SMD −1.82 | — | — |
| Underdog matches (mean) | 169.4 | 122.8 | SMD +0.28 | — | — |

The marginal cohort is selected on exactly the dimensions the model uses to express
confidence: a smaller Elo gap and lower confidence. Its residual risk factors are secondary
and sparse. Low history and market disagreement are **not** over-represented.

### 2.5 Answer to Phase 1

The factors disproportionately associated with the marginal cohort are **bridge-induced side
change** and **overall/surface divergence**, with missing inactivity and ambiguous identity as
weaker signals. Within the marginal cohort, errors concentrate on **favourites with no usable
history, side changes, low surface experience and missing surface**, but these are rare (6–15
fixtures) and none survives multiplicity control. Low history is not enriched in the band, and
market disagreement is not enriched after removing post-forecast quotes. The dominant
explanation of the *marginal band itself* is that it is a close-match cohort with a small model
edge, not an information-poor cohort.

## 3. Phase 2 — newcomer-favourite investigation

### 3.1 Favourite experience (full settled population)

| Favourite observed matches | n | W | Accuracy | Wilson 95% | Mean conf. | Gap | Brier | Log loss | ATP / CH |
|---|---:|---:|---:|---|---:|---:|---:|---:|---:|
| 0–4 | 99 | 33 | **33.3%** | 24.9–43.1% | 0.6045 | **+27.1 pp** | 0.3016 | 0.8094 | 1 / 98 |
| 5–9 | 44 | 23 | 52.3% | 37.9–66.4% | 0.6228 | +10.0 pp | 0.2425 | 0.6747 | 1 / 43 |
| 10–19 | 133 | 81 | 60.9% | 52.5–68.7% | 0.6329 | +2.4 pp | 0.2224 | 0.6337 | 2 / 131 |
| 20–49 | 423 | 277 | 65.5% | 60.8–69.9% | 0.6480 | −0.7 pp | 0.2182 | 0.6229 | 16 / 407 |
| 50–99 | 401 | 255 | 63.6% | 58.8–68.2% | 0.6567 | +2.1 pp | 0.2170 | 0.6210 | 28 / 373 |
| 100+ | 2,123 | 1,448 | 68.2% | 66.2–70.1% | 0.6694 | −1.2 pp | 0.2070 | 0.6013 | 529 / 1,594 |

The 0–4 group is a severe, systematic failure: the model says 60.4%, the observed rate is
33.3%, and the Brier score is 0.302 versus 0.207 for 100+. The 5–9 group is also overconfident.
The ATP sample in the low-history bands is essentially empty (1–2 fixtures), so this is a
Challenger phenomenon in the observed data; that does not prove it is absent on ATP.

Holdout-only check (identical band definitions):

| Favourite band | n | Accuracy | Mean conf. | Gap |
|---|---:|---:|---:|---:|
| 0–4 | 64 | 37.5% | 0.6057 | +23.1 pp |
| 5–9 | 16 | 62.5% | 0.6181 | −0.7 pp |
| 10–19 | 45 | 57.8% | 0.6021 | +2.4 pp |
| 20–49 | 148 | 65.5% | 0.6637 | +0.8 pp |
| 50–99 | 125 | 62.4% | 0.6559 | +3.2 pp |
| 100+ | 627 | 67.3% | 0.6603 | −1.3 pp |

The 0–4 failure replicates chronologically. The 5–9 reversal does not; n=16.

Same fixtures, both histories (bands are defined on the corrected history, so the
classification is identical and only the probability version changes):

| Corrected favourite band | n | Original acc. | Corrected acc. | Original Brier | Corrected Brier |
|---|---:|---:|---:|---:|---:|
| 0–4 | 99 | 39.4% | 33.3% | 0.3195 | 0.3016 |
| 5–9 | 44 | 47.7% | 52.3% | 0.2629 | 0.2425 |
| 10–19 | 133 | 58.6% | 60.9% | 0.2443 | 0.2224 |
| 20–49 | 423 | 60.5% | 65.5% | 0.2465 | 0.2182 |
| 50–99 | 401 | 60.6% | 63.6% | 0.2308 | 0.2170 |
| 100+ | 2,123 | 65.3% | 68.2% | 0.2213 | 0.2070 |

The bridge improves accuracy and Brier in every band except the remaining 0–4 band, where
the corrected probability is slightly worse (33.3% vs 39.4%) because the recovered players
are removed from that band. The 0–4 band after correction is the hard residual, not the
recoverable part.

### 3.2 Information states

| State (full settled) | n | Accuracy | Mean conf. | Gap | Original accuracy |
|---|---:|---:|---:|---:|---:|
| Both established, recovered history | 2,681 | 65.1% | 0.6582 | +0.7 pp | 62.1% |
| Both established, no recovery | 71 | 59.2% | 0.6678 | +7.6 pp | 60.6% |
| Both limited | 41 | 58.5% | 0.6139 | +2.9 pp | 56.1% |
| Favourite limited, underdog established | 62 | 43.5% | 0.6082 | +17.3 pp | 46.8% |
| **Favourite no observable history** | **36** | **8.3%** | 0.6093 | **+52.6 pp** | 19.4% |
| Underdog limited, favourite established | 254 | 81.9% | 0.7084 | −11.1 pp | 76.8% |
| Underdog no observable history | 77 | 87.0% | 0.6854 | −18.5 pp | 77.9% |
| Both no observable history | 1 | 0.0% | 0.5000 | +50.0 pp | 0.0% |

The holdout repeats the pattern: favourite no observable history 13.6% (22 fixtures),
favourite limited 50.0% (26), underdog limited 82.3% (96), underdog no history 79.2% (24).

A zero corrected history is **not** treated as proof of inexperience. The strict
favourite-no-observable-history state has 36 fixtures (3 wins, 8.3%). Including the one
both-no-history fixture and the two ambiguous-identity favourites that still carry zero match
history gives 39 zero-history favourites; of these:

- 11 favourites appear in the observed recent API result history but not in the rating
  corpus: **18.2% accuracy** — lower-level or unrecorded history is absent from our sources.
- 28 favourites do not appear in either source: **10.7% accuracy** — inexperience is
  plausible but not established. No claim of genuine inexperience is made.

### 3.3 Does the identity bridge resolve the newcomer-favourite problem?

On the identical 3,223 fixtures, the bridge moves 83 favourites from an original 0–9 band to
a corrected 20+ band:

| Group | n | Corrected accuracy | Original accuracy | Paired difference (95% CI) | McNemar p |
|---|---:|---:|---:|---|---:|
| Original limited → corrected established | 83 | 57.8% | 50.6% | **+7.2 pp** (−8.0 to +22.5) | 0.44 |
| Original limited → corrected limited | 143 | 39.2% | 42.0% | −2.8 pp (−10.0 to +4.4) | 0.57 |

Recovery classes on the full population:

| Class | n | Corrected acc. | Original acc. |
|---|---:|---:|---:|
| Any recovered history | 3,078 | 65.9% | 62.9% |
| Favourite recovered history | 2,779 | 67.6% | 63.6% |
| No recovered history | 145 | 60.0% | 59.3% |
| Any ambiguous identity | 189 | 61.9% | 60.3% |
| Any no-history slot | 559 | 61.0% | 62.1% |

The direction is consistent: the bridge recovers a real class of fragmented identities and
improves those fixtures. The effect is not statistically established on 83 fixtures, and it
cannot help a player who has no history in either namespace. **The bridge has partly resolved
the recoverable newcomer-favourite problem; it has not resolved the no-history problem.**

### 3.4 Why this does not explain the marginal band

Marginal pool by favourite experience:

| Favourite matches | n | W | Accuracy | Errors | Share of marginal errors |
|---|---:|---:|---:|---:|---:|
| 0–4 | 6 | 2 | 33.3% | 4 | 9.5% |
| 10–19 | 3 | 2 | 66.7% | 1 | 2.4% |
| 20–49 | 13 | 5 | 38.5% | 8 | 19.0% |
| 50–99 | 14 | 6 | 42.9% | 8 | 19.0% |
| 100+ | 65 | 44 | 67.7% | 21 | 50.0% |

88% of marginal errors come from favourites with 20+ matches and 50% from 100+. The
newcomer-favourite effect is a **different population** from the 45–50% marginal band.

## 4. Phase 3 — ATP versus Challenger controlled comparison

### 4.1 Crude and adjusted

| Population | ATP n / acc. | Challenger n / acc. | Difference (CH−ATP) | 95% CI |
|---|---:|---:|---:|---|
| All settled | 577 / 69.84% | 2,646 / 64.74% | **−5.10 pp** | −9.15 to −0.84 |
| November holdout | 106 / 66.98% | 919 / 63.76% | −3.22 pp | −12.02 to +6.67 |
| Secondary 50% | 58 / 77.59% | 418 / 73.68% | −3.90 pp | −13.77 to +8.98 |
| Conservative 40% | 50 / 78.00% | 325 / 78.46% | +0.46 pp | −9.95 to +14.33 |

Direct standardization to the pooled common-support weights:

| Adjustment | Standardized ATP | Standardized CH | CH−ATP | MH OR | Common cells |
|---|---:|---:|---:|---:|---:|
| Confidence band | 67.6% | 65.2% | **−2.4 pp** | 0.89 | 5/5 |
| Tournament round | 67.5% | 64.8% | **−2.7 pp** | 0.86 | 7/8 |
| Rating-gap band | 68.1% | 65.1% | **−3.0 pp** | 0.86 | 4/4 |
| Identity completeness | 69.5% | 64.8% | −4.7 pp | 0.81 | 3/3 |
| Surface experience | 69.7% | 64.9% | −4.8 pp | 0.82 | 4/4 |
| Inactivity | 71.3% | 64.4% | −6.8 pp | 0.77 | 4/6 |
| Experience | 73.8% | 64.5% | **−9.4 pp** | 0.78 | 5/6 |

Adjustment for closeness (confidence, round, rating gap) removes about half of the crude
deficit. Adjustment for experience makes it larger, not smaller: ATP's advantage is not a
composition effect from playing more experienced players. Identity completeness and surface
experience explain little. Inactivity adjustment is limited by sparse ATP cells.

Common support is the binding limitation: 577 ATP fixtures against 2,646 Challenger fixtures.
The experience standardization drops to 5/6 cells (94.6% of Challenger rows); inactivity uses
4/6 cells (95.9% of Challenger rows). Cells without ATP support are reported, not extrapolated.

### 4.2 Market benchmark

The benchmark uses only quotes that existed at or before the forecast and are at most 24
hours old. Of 1,610 quoted rows, 501 are eligible (ATP 68, Challenger 433); 1,092 are
post-forecast and excluded, and 17 have no usable age.

| Tier | n | Model acc. | Market acc. | Model−market (95% CI) | McNemar p |
|---|---:|---:|---:|---|---:|
| ATP | 68 | 76.47% | 75.00% | +1.47 pp (−7.17 to +10.11) | 1.00 |
| Challenger | 433 | 63.51% | 68.36% | **−4.85 pp** (−9.33 to −0.37) | 0.044 |

The market's own tier gap on the eligible set is **−6.64 pp** (95% CI −16.50 to +5.55); the
model's tier gap on the same fixtures is **−12.96 pp** (95% CI −22.64 to −0.81). The market
does show a Challenger penalty, but the model's is about twice as large.

On the selected secondary set only Challenger is eligible (37 fixtures): model 64.9% versus
market 81.1% (−16.2 pp, 95% CI −28.1 to −4.3, p=0.031). On the November holdout, 83
Challenger fixtures are eligible and none are ATP: model 59.0% versus market 77.1% (−18.1 pp,
95% CI −28.7 to −7.4, p=0.003). **No ATP secondary selection has a November quote**, so the
prospective ATP market comparison is impossible; the ATP result is retrospective
(September–October only, n=68).

Leakage sensitivity: using all 1,610 quotes (including 1,092 post-forecast rows) gives ATP
+4.58 pp, Challenger −4.97 pp and a market tier gap of −1.66 pp. That is **not** a valid
pre-forecast benchmark; it is reported only to show how much the post-forecast quotes distort
the comparison. All primary factor and market results in this document use the eligible set.

### 4.3 Answer to Phase 3

Challenger's weaker performance has **both** a competition component and a model-specific
component:

- The eligible market also shows a Challenger gap (−6.6 pp), so Challenger is genuinely a
  harder forecasting population for the available information, not only for this model.
- The model's gap on the same fixtures is about twice the market's (−13.0 pp vs −6.6 pp), and
  the market is 4.8 pp better in Challenger. The model's residual deficit is not fully
  explained by confidence/round/rating-gap adjustment (2.4–3.0 pp survive), and experience
  adjustment widens it.
- The ATP market sample is 68 eligible fixtures and the ATP difference is inconclusive
  (+1.5 pp). The ATP evidence is thin (577 fixtures, 50–58 selected, no November quotes).
  Do not introduce separate ATP/Challenger models on this evidence.

## 5. Phase 4 — rating uncertainty and existing Elo implementation

### 5.1 What Agent 1 already does

| Mechanism | Implementation | Used in probability? |
|---|---|---|
| Overall match count | Dynamic K: `K(n) = 250/(n+5)^0.4` | Yes, through the learning rate |
| Recent match count | No explicit recency window | No (K declines monotonically with total count) |
| Surface-specific experience | `w = min(surface_matches,10)/10 × 0.5`; `effective = (1−w)·overall + w·surface` | Yes |
| Rating initialization | 1500; K(0)=131.9, K(10)=49.4, K(100)=22.2 | Yes |
| Opponent strength | Elo update against opponent rating | Yes, implicitly |
| Identity-resolution confidence | Method and candidate count are recorded | **No** |
| Time since last match | Inactivity is recorded | **No** |
| Overall vs surface divergence | Only through the blend weight | **No explicit uncertainty term** |

The model carries a point estimate. Match counts change how fast the rating moves; surface
counts change the blend. None of the reliability signals enters the probability as an
explicit term.

### 5.2 Calibration by uncertainty proxy

| Proxy / stratum | n | Accuracy | Mean conf. | Gap | Brier | ECE | Calibration slope |
|---|---:|---:|---:|---:|---:|---:|---:|
| Favourite 0–4 matches | 99 | 33.3% | 0.6045 | +27.1 pp | 0.3016 | 0.271 | 0.00* |
| Favourite 5–9 | 44 | 52.3% | 0.6228 | +10.0 pp | 0.2425 | 0.123 | 1.32 |
| Favourite 100+ | 2,123 | 68.2% | 0.6694 | −1.2 pp | 0.2070 | 0.015 | 0.91 |
| Identity ambiguous | 189 | 61.9% | 0.6679 | +4.9 pp | 0.2275 | 0.051 | 0.94 |
| Identity no-history | 544 | 61.0% | 0.6561 | +4.6 pp | 0.2198 | 0.047 | 1.26 |
| Identity complete | 2,490 | 66.9% | 0.6614 | −0.8 pp | 0.2114 | 0.017 | 0.93 |
| Inactivity 181d+ | 209 | 72.7% | 0.6899 | −3.7 pp | 0.1917 | 0.049 | 0.90 |
| Inactivity 0–7d | 1,517 | 63.7% | 0.6458 | +0.9 pp | 0.2211 | 0.018 | 0.99 |
| Divergence ≥100 Elo | 1,018 | 66.0% | 0.6644 | +0.4 pp | 0.2162 | 0.031 | 0.78 |
| Overall min matches 0–4 | 327 | 68.8% | 0.6644 | −2.4 pp | 0.1997 | 0.026 | 1.12 |

\*The favourite 0–4 slope is unstable/degenerate because the group is small and the event
rate is low; the **level** bias, not the slope, is the robust finding.

The key distinction: **identity completeness does not change the probability much** (0.656
vs 0.661) **but changes the accuracy by 5.9 pp** (61.0% vs 66.9%). The estimated probability
and the reliability of the supporting information are not the same object, and the current
model does not encode the difference.

### 5.3 Reliability flag (diagnostic only)

A pre-registered flag marks a forecast when any of: favourite <5 observed matches, any
unresolved identity, minimum surface history <5, or favourite overall/surface divergence
≥100 Elo. The single flag does **not** discriminate:

| Scope | Flagged n | Flagged acc. | Unflagged acc. | Error RD (95% CI) |
|---|---:|---:|---:|---|
| Development | 1,078 | 66.0% | 66.8% | +0.8 pp (−3.1 to +4.8) |
| Holdout | 606 | 64.9% | 63.0% | −1.8 pp (−7.8 to +4.1) |
| Full | 1,684 | 65.6% | 65.8% | +0.2 pp (−3.1 to +3.5) |

Only the **count** of simultaneous risk factors separates:

| Risk-factor count | n | Accuracy | Gap | Brier |
|---|---:|---:|---:|---:|
| 0 | 1,539 | 65.8% | −0.1 pp | 0.2158 |
| 1 | 821 | 68.1% | −1.7 pp | 0.2040 |
| 2 | 582 | 66.2% | +1.1 pp | 0.2099 |
| 3 | 267 | 57.7% | +7.4 pp | 0.2319 |
| 4 | 14 | 42.9% | +21.0 pp | 0.3683 |

3+ factors: 281 fixtures, 56.9% accuracy, +8.1 pp gap, Brier 0.239; on the holdout, 110
fixtures, 52.7%, +10.8 pp. **But only 12 of the 101 marginal selections and 5 of their 42
errors carry 3+ factors.** The reliability flag therefore does **not** explain the marginal
band either.

### 5.4 Research candidate

A narrow log-odds shrinkage candidate (favourite <10 matches and confidence in [0.60,0.75))
was pre-registered on the development period. The fitted calibration slope is **2.25**, not
below the 0.95 justification threshold, and the development sample is only 25 fixtures.
`justified=false`; no adjustment was evaluated or recommended. The holdout log-odds slope for
the broader favourite 0–4 group flips sign (dev +2.32, holdout −0.65), confirming that a
slope-based penalty would be unstable. **No uncertainty-aware probability candidate is
justified by this evidence.** A reliability marker is defensible as a monitoring/eligibility
diagnostic; a universal low-history penalty is not.

Glicko and Glicko-2 represent reliability explicitly through rating deviation and a time
factor (Glickman; Yue et al. 2022). The audit finds the same missing signals, but the data do
not support replacing Agent 1 with Glicko. If a reliability mechanism is developed later, it
must be separately versioned, fitted on earlier data and evaluated chronologically, as the
frozen policies were.

## 6. Phase 5 — inactivity and surface transitions

### 6.1 Inactivity

| Max inactivity | All n | All acc. | Holdout n / acc. | Marginal n / acc. |
|---|---:|---:|---:|---:|
| 0–7d | 1,517 | 63.7% | 548 / 63.3% | 58 / 60.3% |
| 8–30d | 1,084 | 67.1% | 273 / 65.2% | 28 / **50.0%** |
| 31–90d | 298 | 63.8% | 106 / 60.4% | 5 / 60.0% |
| 91–180d | 114 | 71.1% | 46 / 67.4% | 4 / 75.0% |
| 181d+ | 209 | **72.7%** | 51 / **72.5%** | 6 / 66.7% |
| Missing | 1 | 0.0% | 1 / 0.0% | 0 / — |

Long inactivity is **not** a penalty in this population; 181d+ is the strongest band. The
marginal band's worst inactivity bucket is 8–30d (50.0%, n=28), not a long layoff. A universal
January or layoff penalty would be wrong. The apparent 8–30d weakness is small and not
adjusted for opponent strength.

This does **not** contradict the Sackmann/Tennis Abstract injury result, which is for verified
ATP 1900+ layoffs: our inactivity is time since the last rating-corpus match and can be long
because lower-level history is absent, not because the player was injured. Without verified
absence records we cannot test the injury effect. The correct conclusion is that database
inactivity is not a clean layoff proxy, not that layoffs are harmless.

The bridge changes the favourite's inactivity by a mean of **−0.05 days** (median 0); only
0.13% of recovered-history favourites have a shorter corrected inactivity. Recovered history
is mostly older history, not recent activity.

### 6.2 Surface transitions

Observed API result history begins 2026-09-01, so the classification is left-censored. 2,929
of 3,223 fixtures (90.9%) have at least one prior observed match for the favourite.

| Favourite state | n | Accuracy | Mean conf. | Brier |
|---|---:|---:|---:|---:|
| Continuing on surface | 1,876 | 66.4% | 0.6647 | 0.2103 |
| First match on new surface | 102 | 66.7% | 0.6597 | 0.2161 |
| Second on surface | 342 | 62.6% | 0.6540 | 0.2195 |
| Third on surface | 276 | 63.4% | 0.6477 | 0.2252 |
| No prior observed match | 294 | 60.9% | 0.6617 | 0.2265 |
| Unknown surface | 333 | 70.6% | 0.6570 | 0.2058 |

Underdog transitions point the other way:

| Underdog state | n | Accuracy |
|---|---:|---:|
| Continuing | 1,630 | 60.9% |
| First on new surface | 86 | **79.1%** |
| Second on surface | 403 | 66.3% |
| Third on surface | 375 | 65.1% |
| No prior observed match | 427 | 80.8% |

A favourite's first match on a new surface is **not** worse than continuing (66.7% vs
66.4%). The marginal cohort's errors are concentrated in continuing-surface matches (70 of
101 marginal fixtures; 60.0% accuracy). Transitions are not the explanation. The underdog
first-on-new-surface result is a separate, hypothesis-generating signal (n=86), not a policy.
This is consistent with the Pronocast surface study: surface Elo absorbs the average switch
penalty (−3.6 pp on global Elo vs −0.3 pp on surface Elo). Their favourite-specific residual
(−4.2 pp after a switch) is not testable here because only 4 marginal fixtures are a
favourite's first match on a new surface.

### 6.3 Blocked parts of Phase 5

- **Normal off-season break:** blocked. The population covers September–November 2026; no
  June–August or January fixtures are present.
- **Documented withdrawals/retirements:** blocked. Retirements, walkovers, cancellations and
  defaults are excluded from the settled population by construction (`OUTCOME_RETIRED` 125,
  `OUTCOME_WALKOVER` 14, `OUTCOME_CANCELLED` 135, `OUTCOME_DEFAULTED` 1,
  `OUTCOME_NOT_OBSERVED_AT_FREEZE` 278). No withdrawal record is present to audit.
- **Apparent inactivity from missing lower-level history:** partially observable. A
  zero-history favourite usually has no recorded inactivity at all (36 of 37), so an empty
  history cannot be read as a layoff either.

## 7. Phase 6 — deterministic identity audit

### 7.1 Sampling rule

Mandatory: the four model identities that receive more than one live id. Then the highest
maximum absolute effective-rating change across settled slots, ties by model id ascending,
until 20 unique model identities. **No outcome field enters the rule.** The sample is stable
under a full winner-label flip (tested).

### 7.2 Twenty-player ledger

| Model | Alias | Live ids | Matches before→after | Eff. rating before→after | max |Δp| | Recovered | Flag |
|---|---|---|---|---:|---:|---:|---:|---|
| 3918 | kouame m | 4318 | 5→46 | 1397.7→1821.9 | 0.485 | 43 | |
| 4043 | balshaw f | 4303 | 2→49 | 1422.2→1846.2 | 0.483 | 49 | |
| 3207 | gea a | 4796 | 101→162 | 1638.2→2037.4 | 0.196 | 61 | |
| 3388 | demin y | 4491 | 15→33 | 1320.9→1714.1 | 0.380 | 18 | |
| 3834 | schoenhaus m | 4540 | 7→33 | 1339.5→1674.7 | 0.478 | 35 | |
| 3041 | kumstat j | 5670 | 16→45 | 1616.3→1946.1 | 0.410 | 35 | |
| 3748 | pieri s | 4582 | 4→47 | 1361.3→1689.6 | 0.438 | 46 | |
| 3094 | cigarran t | 4868 | 22→63 | 1145.8→1464.2 | 0.450 | 42 | |
| 3429 | dietrich d | 5282 | 17→49 | 1651.0→1966.0 | 0.479 | 32 | dup-pair |
| 2872 | legout t | 5655 | 18→47 | 1489.8→1802.3 | 0.405 | 31 | |
| 4183 | manzano j c m | 4287 | 2→40 | 1392.2→1702.6 | 0.380 | 39 | dup-pair |
| 3787 | caniato c a | 5116 | 19→61 | 1511.5→1819.4 | 0.463 | 52 | dup-pair |
| 2986 | staeheli l | 5338 | 3→25 | 1368.1→1669.3 | 0.455 | 27 | |
| 3292 | giunta m | 4402 | 19→48 | 1405.6→1706.7 | 0.311 | 35 | |
| 4230 | tabata r | 5204 | 4→22 | 1391.4→1682.7 | 0.355 | 19 | |
| 3413 | romano f | 4577 | 34→91 | 1343.7→1631.1 | 0.296 | 59 | |
| 4143 | friend j d h | 4364, 5948 | 15→54 | 1755.6→1841.6 | 0.144 | 53 | multi-live |
| 3029 | maloney p | 5811, 5988 | 63→76 | 1558.4→1614.7 | 0.286 | 14 | multi-live |
| 3503 | xiao l | 4456, 6027 | 34→44 | 1402.9→1365.9 | 0.111 | 10 | multi-live |
| 3343 | dhamne m | 4421, 6141 | 14→59 | 1528.3→1564.0 | 0.120 | 47 | multi-live |

Full per-player evidence, including candidate counts, evidence source, name source, rating
delta ranges and verification flags, is in
`docs/research/evidence/a1_identity_player_ledger_20261127.json`.

Internal verification result for all 20: one alias, one live id (except the four multi-live
models), `candidate_count=1`, `method=ALIAS`, `evidence=tennis_data_alias_pit`,
`name_source=PIT_CANONICAL_NAME`, and corrected matches never below original matches. All
are source-identifier-consistent. This is **not** external identity verification: no web
scouting or new licensed source was used. The highest review priority is the five 300–424
Elo jumps (3918, 4043, 3207, 3388, 3834), because a wrong merge there would move a forecast
most. Three selected players (3429, 4183, 3787) carry the same-day duplicate-pair flag.

### 7.3 Method and duplicate-merge risk

- 2 live slots resolve by `PERSISTED_BRIDGE`; both are listed in the supplementary evidence.
- 193 settled slots are `AMBIGUOUS` (candidate_count=2) and fail closed. The top 10 by
  rating change are listed.
- **No alias maps to more than one model id** and **no model id carries more than one alias**
  in the settled population. The four multi-live merges are the only many-to-one identities.
- 6 same-day player-pair duplicate groups exist in the settled population. They are flagged,
  not resolved; the replay deduplicates by `match_id`.
- The full three-way collision (same model id on both sides) remains zero.

### 7.4 The 16,105-match attribution census

- The DB census at the E3 cutoff reports **16,105** changed-attribution history matches.
- The row-level visibility extract has **16,104** unique match ids (1,234 players receiving
  recovered matches; 1,260 live players affected).
- The one-record difference is a cutoff artefact: the extract was generated with a later
  wall-clock cutoff. It is bounded and does not affect any fixture or forecast.
- 48 of 16,104 rows (0.30%) have a mapping first visible after the history observation;
  reverting those late mappings changes Brier by −0.000034 to +0.000081 and accuracy by
  0.0–0.10 pp (prior audit), so the bridge result is not driven by late mappings.
- The full match-level trace is committed as
  `a1_history_attribution_matches_20261127.csv` (match id, history as-of, visible-from, late
  flag). No names or player ids are in that file.

**Commercial provenance:** the historical corpus is predominantly Sackmann-associated.
Commercial training rights are not verified. No new data was imported, no new parameters
were fitted, and no restricted use was expanded. The 16k rows are already-ingested
production history. PR #351 remains open and draft; it authorises *considering* spending but
requires free-sample, overlap and written rights acceptance before any purchase. This study
makes no purchase, imports no sample and adds no provider call. PR #351's no-spend,
sample-and-rights acceptance gate remains the authority for any future commercial use.

### 7.5 Five-slot provenance gap and strict per-slot census

The 3,778-target reference-policy artifact declares 7,551 method-classified player slots for
7,556 slots. The 3,776-target ledger is fully classified at **7,552/7,552**; adding the two
new fixtures (four slots) and subtracting the five unclassified slots gives 7,551. The gap is
a **reporting aggregate gap**, not five missing fixtures.

A per-slot census in the v3 format is committed for the 3,776 ledger:
`a1_identity_slot_census_20261127.csv` (7,552 rows: 6,558 resolved, 768 no-candidate,
226 ambiguous). It emits one record per slot, including `NO_CANDIDATE` and `AMBIGUOUS`, which
is the structural closure requirement. The 3,778 snapshot is not stored per-slot offline, so
the five exact slots cannot be named. The policy measured is the **pre-v2
permissive-equivalent resolver**; v3 is not applied, and this is not a v3 policy result.

## 8. Validation and reproducibility

Committed artifacts (all hashed in the manifest):

| Artifact | Rows / size | Purpose |
|---|---|---|
| `a1_marginal_cohorts_20261127.csv` | 476 | exact frozen cohorts |
| `a1_marginal_cohorts_20261127.json` | — | Phase 1 metrics, screens, profiles, missingness |
| `a1_retrospective_bands_20261127.csv` | 1,611 | retrospective 40/45/50 bands |
| `a1_newcomer_favourite_20261127.json` | — | Phase 2 experience, states, bridge |
| `a1_atp_challenger_controlled_20261127.json` | — | Phase 3 standardization, market |
| `a1_uncertainty_audit_20261127.json` | — | Phase 4 mechanisms, calibration, flag, candidate |
| `a1_inactivity_surface_20261127.json` | — | Phase 5 inactivity, transitions, blocked parts |
| `a1_identity_player_ledger_20261127.json` | 20 players | Phase 6 ledger and census summary |
| `a1_identity_slot_census_20261127.csv` | 7,552 | per-slot v3-format census |
| `a1_history_attribution_matches_20261127.csv` | 16,104 | match-level attribution trace |
| `a1_marginal_research_manifest_20261127.json` | — | hashes, sources, validation |

Reproduction:

```powershell
python ops/validation/a1_marginal_research_20261127.py
python -m pytest -q tests/test_a1_marginal_research.py
```

The runner is offline. It does not connect to PostgreSQL, call a provider, fit on the
November holdout or write production state. The local ledger/history extracts are required
for Phases 2/5/6; without them the runner still emits Phase 1/3/4 and marks the rest blocked.

## 9. Completed versus blocked

| Phase | Status | Notes |
|---|---|---|
| 1 — exact marginal cohorts and factor screen | **Completed** | Exact 45/27 and 56/32; retrospective 161/107 and 161/90; full factor screen with FDR. |
| 2 — newcomer favourite | **Completed** | Full and holdout bands; states; original-vs-corrected identical fixtures; bridge resolution. |
| 3 — ATP/Challenger controlled | **Completed with common-support limits** | 577 ATP; no ATP November market quotes. |
| 4 — uncertainty audit | **Completed** | Candidate not justified; reliability flag diagnostic only. |
| 5 — inactivity and surface | **Completed in-window; off-season and withdrawals blocked** | No June–August/January data; retirements excluded. |
| 6 — 20-player identity audit | **Completed for internal source consistency** | External identity verification not performed; 16,104/16,105 cutoff reconciliation. |
| 7 — validation and delivery | **Completed** | Tests, manifest, report; PR pushed; CI verified. |

## 10. Decision

**`can_promote = false`.** Nothing here justifies changing the frozen primary or secondary
policy, applying v3, activating the bridge, or deploying a model. The evidence says:

1. The marginal 45–50% weakness is a **close-match, low-separation** problem, not a
   newcomer problem. It cannot be fixed by filtering on low history without discarding the
   experienced close matches that produce half the errors.
2. The newcomer-favourite problem is **real and severe** for 0–4 and no-history favourites,
   but it is a different population. It is a candidate for an **eligibility/reliability
   gate**, not a universal probability penalty. The bridge helps the recoverable subset but
   does not establish a stable correction.
3. Challenger's deficit has **both** a competition component (the eligible market also shows
   a −6.6 pp tier gap) and a **model-specific** component (the model's gap is −13.0 pp and the
   market is 4.8 pp better in Challenger). The ATP evidence is too thin, and November ATP has
   no market quotes.
4. No uncertainty-aware probability candidate is justified. The reliability flag is a
   monitoring diagnostic; it does not separate the marginal band.

Remaining blockers are unchanged: no independent prospective receipt, v3 unapplied, market
stronger on the quoted subset, 193 ambiguous slots, 4 multi-live merges, 21 at/after-start
forecasts (one material), no ATP forward sample, and no owner approval or rollback sign-off.

The next scientifically useful step is **prospective**: collect first-issued bridged
selections with an independent wall-clock archive, and monitor the 3+ risk-factor group and
the no-history favourite group as separate reliability cohorts. Do not tune a filter against
the four historical winners.
