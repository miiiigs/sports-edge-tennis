# Tennis Model Prediction Failure Investigation

**Date:** 2026-10-21  
**Researcher role:** independent Tennis quantitative research engineer and model-failure investigator  
**Scope:** read-only research, offline analysis, synthetic tests. No production model, Manager rule, live prediction, database row, schema, deployment, or provider spend was changed.

## 0. Provenance and starting state

| Item | Value |
| --- | --- |
| Repository | `miiiigs/sports-edge-tennis` |
| Research branch | `research/tennis-model-error-forensics-20261021` |
| Draft PR | [#247](https://github.com/miiiigs/sports-edge-tennis/pull/247) |
| Branch base (`origin/main` at start) | `b6b7e506687172a0b08441bb006013d131a19fcd` |
| Cited Agent 2 PR #239 head | `eec591921367cd81736e9feba1b52011657f1512` |
| Production database | Tennis PostgreSQL, read-only Railway session |
| Extract cutoff | `2026-10-21T03:30:00+00:00` |
| Frozen dataset schema | `tennis_model_error_forensics_v1` |
| Frozen dataset SHA-256 | `ea3809af885643a08dfc15e9eb62d0d6e9ea8a9ed7190002c3f2d552810f281e` |
| Frozen rows | 973 (346 Manager picks, 553 A1/A2 tracking, 67 Value Bets, 7 Veto-blocked) |

The database session used `SET default_transaction_read_only = on`. The extractor and analysis are in `research/tennis_model_error_forensics_20261021/`. The frozen JSONL is intentionally not committed; the extractor and `artifacts/frozen_manifest.json` make it reproducible.

**Important:** this is a live, forward-issued population from 2026-09-09 to 2026-10-21. It is not the 2016-2025 Agent 2 historical replay in PR #239. The two populations are reported separately and are never pooled.

---

## 1. Executive answer

The evidence does **not** support a single global "the model is badly calibrated" conclusion. It supports a more specific one:

1. **The live Manager is not a three-agent model.** Agent 3 abstained on **30,691 of 30,863** evaluations (99.44%). Every one of the 4,825 Manager `CONSENSUS` rows was a **two-agent** A1/A2 consensus. There were **zero** 3/3 consensus picks in the live window. The intended independent third check is effectively absent.
2. **The Manager's overall calibration is acceptable, but high confidence is not.** Across 212 scored Manager Model Picks at >=60%, accuracy was **72.17%** versus a mean predicted **72.94%** (gap -0.77pp), with Brier **0.2064**, log loss **0.6083**, ECE **0.0933**. At >=80%, accuracy was **71.43%** versus **86.80%** predicted (gap **-15.37pp**, 95% CI for accuracy 58.52-81.58%, p=0.0007). The 80-89% band was the worst: **68.29%** actual versus **84.25%** predicted (gap **-15.96pp**, p=0.0050).
3. **High-confidence errors are concentrated, not uniformly spread.** Among >=80% picks:
   - coherent tournament label + pre-pick sharp price: **16/17 (94.1%)**;
   - coherent label + no pre-pick sharp: **12/16 (75.0%)**;
   - tournament label/qualifying mismatch + pre-pick sharp: **8/13 (61.5%)**;
   - mismatch + no pre-pick sharp: **4/10 (40.0%)**.
   A blanket calibration layer would therefore overcorrect the one subgroup that is already performing well.
4. **Model-market disagreement is the clearest failure signal.** When the model picked the Pinnacle underdog, it won **10/23 (43.5%)** versus **67.96%** predicted (p=0.012). When the model was **20pp+ above** the pre-pick sharp implied probability, it won **9/24 (37.5%)** versus **72.72%** predicted (p=0.0001). Value Bets, which are selected on positive model-versus-market edge, won **28/63 (44.4%)** versus **70.30%** predicted (gap **-25.85pp**, p<0.0001); the published subset won **22/49 (44.9%)**. This is adverse selection from overstated edge.
5. **Tournament label/qualifying semantics are a strong empirical cohort marker.** Picks in tournaments whose canonical name contains "Qualification" but whose `qualifying_flag` is false (or vice versa) won **52/85 (61.2%)** versus **72.63%** predicted (gap **-11.45pp**, p=0.018), with Brier **0.2754** and ECE **0.2223**. At >=80%, the mismatch cohort won **12/23 (52.2%)** versus **86.94%** predicted (gap **-34.77pp**, p=7.5e-07), while the coherent >=80% cohort won **28/33 (84.8%)** versus **86.70%** predicted. For the sampled events, the provider's own metadata shows `raw_event_qualification=False` while the raw tournament name contains "Qualification", so this is a source-naming/semantics problem that needs reconciliation, not necessarily a model bug.
6. **Long inactivity and thin player history are not the main live failure modes.** Picks whose player had played within 0-7 days won **67.5%** (77/114); picks after >180 days won 2/2; picks with <5 prior visible matches won **78.1%** (25/32). This is consistent with PR #239's rejection of hard 365/730-day history windows. The 53-day-inactivity case in the case studies is an exception, not the rule.
7. **The live sample is small and the subgroup findings are exploratory.** There are only 237 unique Manager picks at >=60%, 212 scored, and 56 scored at >=80%. Confidence intervals are wide; subgroup p-values are not corrected for multiple comparisons. No claim of improvement is made.

---

## 2. Assignment A - valid prediction population

### 2.1 Why row-level scoring is invalid

The production database stores a Manager evaluation on every hourly run. In the live window:

| Item | Count |
| --- | ---: |
| Manager `CONSENSUS` rows in DB | 4,825 |
| Unique matches represented | 346 |
| Average consensus rows per match | 13.9 |
| Maximum reruns observed for one match | 41 |
| Unique first picks with probability >=60% | 237 |

Scoring 4,825 rows as independent predictions would count one match up to 41 times and would let a later, more confident rerun replace the original pick. This analysis therefore freezes the **first production Manager `CONSENSUS` evaluation per match**, ordered by `as_of`, then `manager_consensus_result_id`. This is the same "first pick" convention used by the dashboard's canonical aligned Model Picks reader.

### 2.2 Populations and eligibility

| Population | Rows | Treatment |
| --- | ---: | --- |
| Manager raw consensus evaluations | 4,825 | not independent; not scored |
| **Manager Model Picks (unique first evaluation, >=60%)** | **237** | **primary population** |
| Manager unique first evaluations (all probabilities) | 346 | appendix / context |
| A1/A2 wide-spread tracking | 553 | research only; never a Value Bet |
| Actual Value Bets | 67 | separate; overlaps Manager picks |
| - published before match | 52 | `published_at` present |
| - pre-publication-boundary ledger | 15 | not published at the time |
| Market Veto-blocked | 7 | separate from Value Bets |
| Historical Agent 2 replay (PR #239) | 36,512 predictions / 40,040 eligible | separate offline replay |

**Manager Model Pick eligibility:**

1. `manager_status = CONSENSUS`, `selected_side IN (1,2)`.
2. `prediction_contract_version = predictive_agent_result_v1`, completed and finalized run, `experiment_id IS NULL`.
3. First such evaluation per `match_id`.
4. `consensus_probability >= 0.60` for the primary population.
5. Point-in-time match version visible at `picked_at`; `picked_at < match_start_at`; `prediction_finalized_at < match_start_at`.
6. Scored only if the latest canonical result is `COMPLETED` with `winner_side IN (1,2)`.
7. `RETIRED`, `WALKOVER`, `CANCELLED` are excluded from accuracy/Brier/log loss and reported separately.
8. No future information: a history result enters the player-history features only if its `as_of` is at or before the pick and its match started before the pick.

### 2.3 Live versus replay, and forward issuance

- **Live Manager picks:** 346 unique first evaluations. All 346 had `picked_at` before `match_start_at` and `prediction_finalized_at` before `match_start_at`. Zero results were observed at or before the pick. Zero picks were made at or after the start.
- **Publication caveat:** model-only picks do not have a separate `published_at` event. Their forward issuance is proven by the pre-start, finalized Manager evaluation, not by a dashboard publication log. Value Bets do have `published_at`; 52/67 were published, and 15 are pre-publication-boundary ledger entries.
- **Replay:** the historical Agent 2 replay is a tournament-cohort replay, not a per-match pre-match replay. It is not combined with live picks. No historical replay rows exist in the live `prediction_runs` table (`experiment_id IS NULL` for all 1,418 runs in the window).

### 2.4 Result and identity data quality

| Check | Result |
| --- | --- |
| Manager unique picks with a match start | 346/346 |
| Result observed before or at pick | 0 |
| Picked or finalized after start | 0 |
| Matches with two result observations | 66/346 |
| Result observations that disagree | 0 |
| Manager picks with missing surface | 26/346 (12/237 at >=60%) |
| Identity bridge events on case-study players | 0 |
| Alias collisions on case-study players | 0 |

The production result truth is single-provider for most matches. In the case studies, each result is one API-TENNIS observation with an explicit scoreline; no conflicting observation exists. That is the system's authoritative result truth, but it is not an independent second-provider verification.

---

## 3. Assignment B - incorrect high-confidence predictions

There are **16** settled `COMPLETED` losses at >=80% in the primary population. All 16 are in the artifact `artifacts/case_studies.md`. Twelve are detailed here. Every case below has:

- a pre-start first Manager evaluation;
- a point-in-time match version;
- a canonical `COMPLETED` result with scoreline;
- no result lineage conflict;
- Agent 3 abstained (true of every live Manager consensus pick).

### 3.1 Case 1 - match 131505, C. Taberner vs S. Pieri

- **Date / tournament:** 2026-10-02, Bari (Italy) - Qualification, Challenger, clay, best of 3, QF.
- **Picked:** C. Taberner at **92.77%**; **actual winner:** S. Pieri, 2-1.
- **Agents:** A1 92.96%, A2 92.59%, A3 **abstained** (`INSUFFICIENT_MATCHUP_SUPPORT`); 2 active, spread 0.0037.
- **History before the match:** Taberner 17 prior matches (52.9%), 8 on clay (62.5%), 10 with stats; Pieri 39 prior matches (46.2%), 4 on clay (50.0%).
- **Market:** pre-pick Pinnacle had Taberner at 1.2732 (75.58% normalized) and Pieri at 3.94; model was **+17.19pp** above sharp.
- **Data quality:** tournament name contains "Qualification" but `qualifying_flag=false`; provider raw metadata also has `raw_event_qualification=False`. 27 match versions.
- **Suspected reason:** model overconfidence relative to the sharp market, in a tournament-label mismatch cohort, with no A3 check.
- **Evidence:** verified pre-pick sharp prices; verified agent status; verified tournament label/flag; verified result.
- **Confidence:** market/identity facts **VERIFIED**; causal link to the loss **PLAUSIBLE**.

### 3.2 Case 2 - match 131988, L. J. Rodriguez vs T. Cigarran

- **Date / tournament:** 2026-10-12, Santa Cruz 2, Challenger, clay, best of 3, Q1.
- **Picked:** L. J. Rodriguez at **91.56%**; **actual winner:** T. Cigarran, 0-2.
- **Agents:** A1 89.20%, A2 93.92%, A3 **abstained**; spread 0.0472.
- **History:** Rodriguez 24 prior matches (45.8%), 7 on clay (57.1%), 8 with stats; Cigarran 35 prior matches (51.4%), 4 on clay (50.0%).
- **Market:** no pre-pick sharp snapshot.
- **Data quality:** `qualifying_flag=true` while the tournament name does not contain "Qualification"; 22 match versions.
- **Suspected reason:** high confidence without a pre-pick market check; no A3 check.
- **Evidence:** verified agent probabilities, history, result; verified absence of pre-pick sharp.
- **Confidence:** data facts **VERIFIED**; causal link **PLAUSIBLE**.

### 3.3 Case 3 - match 131620, B. Zhukayev vs Y. Demin

- **Date / tournament:** 2026-10-05, Wuning 3 (China) - Qualification, Challenger, hard, best of 3, R32.
- **Picked:** B. Zhukayev at **90.31%**; **actual winner:** Y. Demin, 0-2.
- **Agents:** A1 90.71%, A2 89.90%, A3 **abstained**; spread 0.0081.
- **History:** Zhukayev 25 prior matches (48.0%) but **0 prior surface matches and 0 prior stat matches**; last match 53 days earlier. Demin only 6 prior matches (33.3%), 1 on hard.
- **Market:** no pre-pick sharp snapshot.
- **Data quality:** tournament name contains "Qualification" but `qualifying_flag=false`; 26 match versions.
- **Suspected reason:** long inactivity combined with no visible surface/stat history for the picked player; the model had little surface-specific evidence for a 90% claim.
- **Evidence:** verified 53-day gap, zero surface/stat history, verified result.
- **Confidence:** data facts **VERIFIED**; causal link **PLAUSIBLE**. This is an exception: the population-level inactivity analysis does not show long inactivity as a general failure mode.

### 3.4 Case 4 - match 131841, A. Santillan vs K. Saitoh

- **Date / tournament:** 2026-10-09, Wuning 3 (China) - Qualification, Challenger, hard, best of 3, QF.
- **Picked:** A. Santillan at **88.62%**; **actual winner:** K. Saitoh, 2-1.
- **Agents:** A1 88.33%, A2 88.90%, A3 **abstained**; spread 0.0057.
- **History:** Santillan 35 prior matches (42.9%), 8 on hard (50.0%); Saitoh only 6 prior matches but **5 wins (83.3%) and 5/6 on hard (83.3%)**.
- **Market:** no pre-pick sharp snapshot.
- **Data quality:** tournament name contains "Qualification" but `qualifying_flag=false`; opponent has a short but unusually strong record.
- **Suspected reason:** insufficient opponent-strength adjustment: the opponent's small sample was extremely strong on the surface, yet the model priced the opponent at 11.4%.
- **Evidence:** verified opponent record and surface form; verified result.
- **Confidence:** data facts **VERIFIED**; causal link **PLAUSIBLE**.

### 3.5 Case 5 - match 131534, V. Sachko vs S. Pieri

- **Date / tournament:** 2026-10-03, Bari (Italy) - Qualification, Challenger, clay, best of 3, SF.
- **Picked:** V. Sachko at **87.34%**; **actual winner:** S. Pieri, 1-2.
- **Agents:** A1 89.86%, A2 84.81%, A3 **abstained**; spread 0.0504, close to the 0.06 A3-abstention gate.
- **History:** Sachko 49 prior matches (51.0%), 8 on clay (75.0%); Pieri 41 prior matches (48.8%), 6 on clay (66.7%).
- **Market:** no pre-pick sharp snapshot.
- **Data quality:** tournament label/flag mismatch; near-threshold A1/A2 spread.
- **Suspected reason:** overconfidence when the two active agents are close to their disagreement limit; the underlying records are roughly even (51.0% vs 48.8% overall), which does not support 87.3%.
- **Evidence:** verified probabilities and spread; verified records; verified result.
- **Confidence:** data facts **VERIFIED**; causal link **PLAUSIBLE**.

### 3.6 Case 6 - match 132404, K. Samrej vs Y. Demin

- **Date / tournament:** 2026-10-19, Guangzhou, Challenger, hard, best of 3, Q1.
- **Picked:** K. Samrej at **86.91%**; **actual winner:** Y. Demin, 0-2.
- **Agents:** A1 84.76%, A2 89.05%, A3 **abstained**; spread 0.0429.
- **History:** Samrej 28 prior matches (60.7%), 5 on hard (60.0%); Demin 9 prior matches (44.4%), 4 on hard (50.0%). Both had played earlier the same day.
- **Market:** pre-pick Pinnacle had Samrej at 1.781 (53.14% normalized) and Demin at 2.02; the model was **+33.76pp** above sharp. This was close to a market coin-flip.
- **Data quality:** `qualifying_flag=true` while the name does not contain "Qualification".
- **Suspected reason:** the model's edge is overwhelmingly rejected by the sharp market; the model treated a near-even match as 87%.
- **Evidence:** verified pre-pick sharp prices, verified result.
- **Confidence:** market fact **VERIFIED**; causal link **PLAUSIBLE**.

### 3.7 Case 7 - match 131634, Y. Takahashi vs K. Saitoh

- **Date / tournament:** 2026-10-05, Wuning 3 (China) - Qualification, Challenger, hard, best of 3, F.
- **Picked:** Y. Takahashi at **84.52%**; **actual winner:** K. Saitoh, 2-1.
- **Agents:** A1 83.37%, A2 85.67%, A3 **abstained**; spread 0.0230.
- **History:** Takahashi 25 prior matches (40.0%) but only **2 surface matches and 2 stat matches**; Saitoh only 3 prior matches (66.7%), 3 on hard.
- **Market:** no pre-pick sharp snapshot.
- **Data quality:** tournament label/flag coherent, but both players have very thin surface/stat histories.
- **Suspected reason:** the model issued 84.5% from thin surface/stat samples; opponent sample was also tiny.
- **Evidence:** verified history depth and result.
- **Confidence:** data facts **VERIFIED**; causal link **PLAUSIBLE**.

### 3.8 Case 8 - match 131834, D. Dzumhur vs A. Shelbayh

- **Date / tournament:** 2026-10-08, Villena, Challenger, clay, best of 3, R16.
- **Picked:** D. Dzumhur at **84.14%**; **actual winner:** A. Shelbayh, 1-2.
- **Agents:** A1 86.97%, A2 81.32%, A3 **abstained**; spread 0.0565.
- **History:** Dzumhur 46 prior matches (43.5%) but only **1 surface match and 3 stat matches**; Shelbayh 54 prior matches (50.0%), 17 with stats.
- **Market:** no pre-pick sharp snapshot.
- **Data quality:** near-threshold spread; thin surface/stat history for the picked player.
- **Suspected reason:** surface-specific evidence was almost absent for a player priced at 84.1%, while the opponent had a much deeper record.
- **Evidence:** verified history depth, spread, result.
- **Confidence:** data facts **VERIFIED**; causal link **PLAUSIBLE**.

### 3.9 Case 9 - match 131327, S. Mitsui vs O. Okonkwo

- **Date / tournament:** 2026-09-28, Columbus, Challenger, surface **missing**, best of 3, F.
- **Picked:** S. Mitsui at **84.02%**; **actual winner:** O. Okonkwo, 2-0.
- **Agents:** A1 83.20%, A2 84.84%, A3 **abstained**; spread 0.0164.
- **History:** Mitsui only 5 prior matches (60.0%), 4 on the recorded surface, 1 stat match; Okonkwo 6 prior matches (50.0%) but had not played for **106 days**.
- **Market:** pre-pick Pinnacle had Mitsui at 1.271 (74.38% normalized); model was **+9.64pp** above sharp.
- **Data quality:** surface missing at both match and edition level; tournament label/flag mismatch; thin history.
- **Suspected reason:** missing surface and thin data, plus a model edge over sharp. The surface-dependent agents could not be fully validated.
- **Evidence:** verified missing surface, verified pre-pick sharp, verified result.
- **Confidence:** data facts **VERIFIED**; causal link **UNKNOWN/PLAUSIBLE** because the surface was unavailable to the model but the exact effect cannot be isolated.

### 3.10 Case 10 - match 131444, R. Jodar vs D. Vallejo

- **Date / tournament:** 2026-10-01, Tokyo, ATP Tour, hard, best of 3, R32.
- **Picked:** R. Jodar at **83.82%**; **actual winner:** D. Vallejo, 2-0.
- **Agents:** A1 82.42%, A2 85.23%, A3 **abstained**; spread 0.0281.
- **History:** Jodar 64 prior matches (75.0%) but only **1 surface match, 0 surface wins, 5 stat matches**; Vallejo 62 prior matches (66.1%), 5 on hard (60.0%), 8 with stats.
- **Market:** no pre-pick sharp snapshot.
- **Data quality:** coherent identity; surface-specific history is very thin for the picked player.
- **Suspected reason:** the overall record was strong, but the hard-court record was essentially absent; the model appears to have over-weighted overall form.
- **Evidence:** verified overall vs surface history; verified result.
- **Confidence:** data facts **VERIFIED**; causal link **PLAUSIBLE**.

### 3.11 Case 11 - match 131790, G. Cadenasso vs M. Giunta

- **Date / tournament:** 2026-10-07, Palermo (Italy) - Qualification, Challenger, clay, best of 3, R16.
- **Picked:** G. Cadenasso at **83.31%**; **actual winner:** M. Giunta, 0-2.
- **Agents:** A1 86.17%, A2 80.44%, A3 **abstained**; spread 0.0572, close to the 0.06 gate.
- **History:** Cadenasso 29 prior matches (48.3%), 4 on clay (50.0%); Giunta 24 prior matches (50.0%), 8 on clay (62.5%).
- **Market:** no pre-pick sharp snapshot.
- **Data quality:** tournament label/flag mismatch; near-threshold spread.
- **Suspected reason:** overconfidence with near-limit agent disagreement and only modest clay evidence.
- **Evidence:** verified probabilities/spread, records, result.
- **Confidence:** data facts **VERIFIED**; causal link **PLAUSIBLE**.

### 3.12 Case 12 - match 131554, T. Ichikawa vs B. Bayldon

- **Date / tournament:** 2026-10-04, Wuning 3 (China) - Qualification, Challenger, hard, best of 3, SF.
- **Picked:** T. Ichikawa at **83.06%**; **actual winner:** B. Bayldon, 2-0.
- **Agents:** A1 81.44%, A2 84.68%, A3 **abstained**; spread 0.0325.
- **History:** Ichikawa 11 prior matches (54.5%), 4 on hard (50.0%), 4 stat matches; **Bayldon had zero visible prior completed matches**.
- **Market:** no pre-pick sharp snapshot.
- **Data quality:** tournament label/flag coherent; opponent has no visible history in the canonical database.
- **Suspected reason:** the opponent had no visible history, so the model could not adjust for opponent strength; it still issued 83.1%.
- **Evidence:** verified zero opponent history and result.
- **Confidence:** data fact **VERIFIED**; causal link **PLAUSIBLE** (absence of evidence is not proof of opponent strength, but it is a real input-coverage gap).

### 3.13 Recurring failure patterns

The 16 high-confidence losses carry these descriptive flags:

| Flag | High-confidence losses (n=16) | All Manager picks >=60% (n=237) |
| --- | ---: | ---: |
| Agent 3 abstained | 16 | 237 |
| Tournament label / qualifying mismatch | 11 | 90 |
| Pre-pick sharp missing | 10 | 98 |
| Thin stat history (<5) | 7 | 103 |
| Thin surface history (<3) | 7 | 109 |
| Model >10pp above sharp | 5 | 55 |
| Opponent no visible history | 2 | 33 |
| Opponent strong surface form | 2 | 18 |
| Surface missing | 1 | 12 |

The two strongest *discriminating* flags are the tournament label/qualifying mismatch and the absence of a pre-pick sharp reference. Agent 3 abstention is universal, so it explains the structural environment but does not discriminate between wins and losses.

No case is classified as a data-lineage error: every case-study result is a completed result with an explicit scoreline, and no case has conflicting result observations.

---

## 4. Assignment C - statistical error analysis

### 4.1 Manager Model Picks, >=60%, unique first evaluation per match

| Band | Eligible | Scored | Wins | Losses | Mean p | Actual | Gap | Accuracy 95% CI | Brier | Log loss | ECE | p vs mean p |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |
| 60-69 | 108 | 98 | 67 | 31 | 0.6418 | 0.6837 | +0.0419 | 0.5862-0.7673 | 0.2220 | 0.6367 | 0.0802 | 0.3872 |
| 70-79 | 66 | 58 | 46 | 12 | 0.7437 | 0.7931 | +0.0494 | 0.6723-0.8775 | 0.1644 | 0.5107 | 0.0494 | 0.3886 |
| 80-89 | 44 | 41 | 28 | 13 | 0.8425 | 0.6829 | **-0.1596** | 0.5302-0.8044 | 0.2416 | 0.7026 | 0.1596 | **0.0050** |
| 90+ | 19 | 15 | 12 | 3 | 0.9375 | 0.8000 | **-0.1375** | 0.5481-0.9295 | 0.1707 | 0.5428 | 0.1668 | **0.0279** |
| **Overall** | **237** | **212** | **153** | **59** | **0.7294** | **0.7217** | **-0.0077** | **0.6578-0.7777** | **0.2064** | **0.6083** | **0.0933** | 0.8004 |
| **>=80%** | **63** | **56** | **40** | **16** | **0.8680** | **0.7143** | **-0.1537** | **0.5852-0.8158** | **0.2226** | **0.6598** | **0.1615** | **0.0007** |

ECE is over ten 5-point bins from 0.50 to 1.00. The overall ECE is 0.0933 because the low/mid bands are underconfident and the high bands are overconfident; the errors do not cancel in a way that makes the high-confidence claim safe.

For context, all 346 unique first evaluations (including <60%) scored 204/313 (65.18%) at mean 67.26% (gap -2.08pp), Brier 0.2207, ECE 0.0788.

### 4.2 The high-confidence overconfidence is concentrated

At >=80%, the two risk factors interact:

| Cohort | Scored | Wins | Actual | Mean p | Gap |
| --- | ---: | ---: | ---: | ---: | ---: |
| Coherent tournament label + pre-pick sharp | 17 | 16 | 0.9412 | 0.8661 | +0.0751 |
| Coherent label + no pre-pick sharp | 16 | 12 | 0.7500 | 0.8680 | -0.1180 |
| Mismatch label + pre-pick sharp | 13 | 8 | 0.6154 | 0.8663 | -0.2509 |
| Mismatch label + no pre-pick sharp | 10 | 4 | 0.4000 | 0.8733 | -0.4733 |

This is the single most important decomposition in the investigation. It means:

- the model is not uniformly overconfident at >=80%;
- the coherent, market-checked subgroup is already close to or better than its stated probability;
- the failure is concentrated where the tournament identity is ambiguous and/or the market check is absent.

### 4.3 Surface, tier, and tournament

| Group | Scored | Wins | Mean p | Actual | Gap | Brier | ECE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Clay | 105 | 73 | 0.7255 | 0.6952 | -0.0302 | 0.2132 | 0.0699 |
| Hard | 96 | 73 | 0.7349 | 0.7604 | +0.0255 | 0.1922 | 0.1747 |
| Surface missing | 11 | 7 | 0.7188 | 0.6364 | -0.0824 | 0.2656 | 0.2721 |
| ATP Tour | 20 | 18 | 0.7362 | 0.9000 | +0.1638 | 0.1450 | 0.2944 |
| Challenger | 192 | 135 | 0.7287 | 0.7031 | -0.0256 | 0.2128 | 0.0850 |
| Qualification-labelled mismatch | 85 | 52 | 0.7263 | 0.6118 | **-0.1145** | 0.2754 | 0.2223 |
| Coherent label | 127 | 101 | 0.7315 | 0.7953 | +0.0638 | 0.1602 | 0.0961 |

Tournament-level examples: Bari (Qualification) 4/9; Palermo (Qualification) 3/8; Florence (Qualification) 1/4; Maia 2/6; Wuning 3 (Qualification) 9/14; Cali 3/6. These cells are small and should be treated as cohort markers, not proof of a tournament-specific model defect. There were no grass or carpet picks and no best-of-five matches in the live window, so those strata are not evaluable here.

### 4.4 History depth and inactivity

| Group | Scored | Wins | Mean p | Actual | Gap |
| --- | ---: | ---: | ---: | ---: | ---: |
| <5 prior matches | 32 | 25 | 0.7122 | 0.7813 | +0.0691 |
| 5-9 prior matches | 8 | 5 | 0.7490 | 0.6250 | -0.1240 |
| 10-19 | 15 | 12 | 0.7715 | 0.8000 | +0.0285 |
| 20-49 | 115 | 82 | 0.7366 | 0.7130 | -0.0236 |
| 50+ | 42 | 29 | 0.7040 | 0.6905 | -0.0136 |
| Inactivity 0-7 days | 114 | 77 | 0.7306 | 0.6754 | -0.0551 |
| Inactivity 8-30 days | 53 | 41 | 0.7485 | 0.7736 | +0.0251 |
| Inactivity 31-90 days | 13 | 10 | 0.6976 | 0.7692 | +0.0716 |
| Inactivity >180 days | 2 | 2 | 0.7138 | 1.0000 | +0.2862 |

**There is no evidence here that long inactivity or thin history causes the high-confidence losses.** The opposite is closer to true in this small window: very short rest (0-7 days) is the weakest group. Case 3 is a genuine long-inactivity exception, not a population-level rule. This supports PR #239's rejection of hard 365/730-day history windows.

### 4.5 Agent agreement

| A1/A2 probability spread | Scored | Wins | Mean p | Actual | Gap | p |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| <0.02 | 82 | 53 | 0.7298 | 0.6463 | -0.0835 | 0.0888 |
| 0.02-0.04 | 57 | 46 | 0.7313 | 0.8070 | +0.0757 | 0.1971 |
| 0.04-0.06 | 73 | 54 | 0.7275 | 0.7397 | +0.0122 | 0.8148 |

Tight A1/A2 agreement is **not** a reliability signal: the tightest-spread bucket has the worst accuracy. This is consistent with correlated model error and with the absence of an independent A3 check. It argues against treating low spread as extra certainty.

### 4.6 Market comparison

Only genuine pre-pick sharp prices were used: latest `source_system='SHARP'`, `bookmaker_code='PINNACLE'` snapshot at or before `picked_at`, with both sides normalized to remove the booksum. Guide Odds and post-pick prices were not used.

| Group | Eligible | Scored | Wins | Mean p | Actual | Gap | Brier | ECE | p |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Model picked market favourite | 116 | 107 | 83 | 0.7303 | 0.7757 | +0.0454 | 0.1799 | 0.0944 | 0.2902 |
| Model picked market underdog | 23 | 23 | 10 | 0.6796 | 0.4348 | **-0.2449** | 0.2941 | 0.2533 | **0.0119** |
| No pre-pick sharp | 98 | 82 | 60 | 0.7422 | 0.7317 | -0.0105 | 0.2164 | 0.1643 | 0.8282 |
| Model below sharp | 49 | 46 | 39 | 0.6880 | 0.8478 | +0.1599 | 0.1447 | 0.1599 | 0.0193 |
| Model 0-5pp above sharp | 20 | 18 | 16 | 0.7241 | 0.8889 | +0.1648 | 0.1204 | 0.2427 | 0.1177 |
| Model 5-10pp above sharp | 15 | 14 | 11 | 0.7461 | 0.7857 | +0.0396 | 0.1723 | 0.0895 | 0.7335 |
| Model 10-20pp above sharp | 29 | 28 | 18 | 0.7571 | 0.6429 | -0.1142 | 0.2310 | 0.1652 | 0.1587 |
| Model 20pp+ above sharp | 26 | 24 | 9 | 0.7272 | 0.3750 | **-0.3522** | 0.3462 | 0.3602 | **0.0001** |

The market comparison is available for 139/237 Manager picks. The missing 98 are not random: they are mostly earlier evaluations before a sharp snapshot existed. At >=80%, the missing-sharp subgroup is materially worse (16/26, 61.5%) than the with-sharp subgroup (24/30, 80.0%).

### 4.7 Actual Value Bets (separate population)

Value Bets are the first `BET` decision per match, selected by the production market/value layer. They are not the same population as Manager Model Picks.

| Group | Eligible | Scored | Wins | Mean p | Actual | Gap | Brier | ECE | p |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| All Value Bets | 67 | 63 | 28 | 0.7030 | 0.4444 | **-0.2585** | 0.2891 | 0.2598 | **<0.0001** |
| Published Value Bets | 52 | 49 | 22 | 0.7077 | 0.4490 | -0.2587 | 0.2913 | 0.2603 | 0.0001 |
| Pre-publication-boundary | 15 | 14 | 6 | 0.6865 | 0.4286 | -0.2579 | 0.2794 | 0.3014 | 0.0375 |
| Claimed edge 10pp+ | 59 | 56 | 24 | 0.7101 | 0.4286 | **-0.2815** | 0.2980 | 0.2815 | **<0.0001** |
| Claimed edge 5-10pp | 8 | 7 | 4 | 0.6463 | 0.5714 | -0.0748 | 0.2184 | 0.1878 | 0.6788 |

This is the most commercially important finding: the Value Bet layer is selecting the model's largest claimed edges, and those are exactly the predictions that lose most often. The model's edge estimate is not merely noisy; it is adverse at the top of its range.

### 4.8 A1/A2 wide-spread tracking (research only)

| Band | Eligible | Scored | Wins | Mean p | Actual | Gap | Brier | ECE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 60-69 | 266 | 235 | 148 | 0.6510 | 0.6298 | -0.0212 | 0.2338 | 0.0212 |
| 70-79 | 203 | 179 | 132 | 0.7427 | 0.7374 | -0.0052 | 0.1949 | 0.0273 |
| 80-89 | 80 | 66 | 52 | 0.8422 | 0.7879 | -0.0543 | 0.1718 | 0.0543 |
| 90+ | 4 | 3 | 2 | 0.9202 | 0.6667 | -0.2535 | 0.2752 | 0.2535 |
| **Overall** | **553** | **483** | **334** | **0.7128** | **0.6915** | **-0.0213** | **0.2112** | **0.0294** |

The wide-spread tracking population is much better calibrated than the Manager consensus population at high confidence (80-89 gap -5.4pp versus -16.0pp), even though it is the population the Manager rejects for excessive A1/A2 disagreement. This is counterintuitive and important: it is consistent with the Manager's tight-spread filter not selecting the safer high-confidence cases. It is not a controlled comparison, and the tracking population is research-only and never a Value Bet, but it is a useful control.

### 4.9 Historical Agent 2 replay (PR #239), separate

| Metric | Historical A2 replay | Live Manager (this report) |
| --- | ---: | ---: |
| Population | 2023-2025 tournament-cohort replay | 2026-09-09 to 2026-10-21 forward picks |
| Predictions | 36,512 | 237 unique first picks >=60% |
| Accuracy | 60.40% | 72.17% |
| Brier | 0.2354 | 0.2064 |
| ECE | 0.0293 | 0.0933 |
| >=80% actual vs predicted | 76.73% vs 87.05% (gap -10.31pp) | 71.43% vs 86.80% (gap -15.37pp) |
| >=90% actual vs predicted | 84.44% vs 94.33% (gap -9.89pp) | 80.00% vs 93.75% (gap -13.75pp) |

Both populations show high-confidence overconfidence; the live magnitude is larger but the live sample is much smaller. The populations are not comparable in accuracy because the Manager consensus rule and the live player mix differ. They must not be pooled.

### 4.10 Statistical caveats

- The 237-pick Manager population is small. 95% CIs are wide (overall accuracy 65.8-77.8%; >=80% accuracy 58.5-81.6%).
- Subgroup cells (especially high-confidence mismatch/no-sharp) are 10-23 matches. Their p-values are exploratory and not corrected for multiple comparisons.
- The same data were used to discover the cohorts and estimate their performance. These are hypotheses for a pre-registered held-out or forward test, not confirmed effects.
- The market comparison is limited to matches with a pre-pick Pinnacle snapshot. The 98 without one are a selected subgroup (earlier evaluations), not a random sample.
- No adjustment was made for match-level clustering beyond taking one pick per match.

---

## 5. Assignment D - top three evidence-supported weaknesses and experiments

### Experiment 1 - Reconcile tournament identity/qualifying semantics and recover missing surface

**What is going wrong.** The model's high-confidence errors are concentrated in tournaments whose canonical name contains "Qualification" while the match's `qualifying_flag` is false, or vice versa. This cohort is 90/237 Manager picks and wins 61.2% versus 72.6% predicted; at >=80% it wins 12/23 (52.2%) versus 86.9% predicted. Twelve Manager picks also have no surface at the point-in-time match version, and those picks win 7/11 (63.6%) versus 71.9% predicted. Provider metadata confirms the raw tournament name and `raw_event_qualification` are not semantically aligned.

**Evidence.** See sections 4.2, 4.3, 4.6; `artifacts/tables.md`; case studies 1, 3, 4, 5, 6, 9, 11.

**Proposed change (research only).** Build a read-only reconciliation audit that maps each canonical tournament to its provider `raw_tournament_name`, `raw_event_qualification`, `raw_tournament_round`, `raw_surface` and edition surface. Decide, with source-owner approval, whether the "Qualification" events are qualifying draws or main draws; normalize canonical names and flags; recover missing surface only when the source evidence is unambiguous and PIT-valid. Then run a shadow replay with corrected fields.

**What it affects.** Data integrity; possibly tournament/tier/surface features; cohort stratification; selection coverage. It is primarily a data correction, not a threshold change.

**What could become worse.** Renaming or re-flagging tournaments can split or merge historical player histories and change surface/tier features. Recovering a surface from a non-PIT source can leak information. A correction that looks better on the affected cohort could degrade the coherent cohort. Coverage may fall if ambiguous events are excluded.

**How to test.** Freeze the current cohort; produce a correction manifest with source evidence; run the unchanged baseline against the corrected fields in shadow; compare on the common match set with paired bootstrap; report accuracy, Brier, log loss, ECE, coverage and each affected tournament. Promote only after source verification, PIT audit and independent review.

**Promotion evidence.** The correction is source-verified and PIT-safe; the corrected cohort's calibration gap and Brier improve with a paired 95% CI excluding zero; the coherent cohort does not regress; no match changes participants or winner; coverage loss is explicitly accepted.

### Experiment 2 - Market-availability and market-disagreement gate / shrinkage

**What is going wrong.** The model's largest claimed edges are its worst predictions. Value Bets win 44.4% versus 70.3% predicted; claimed-edge 10pp+ picks win 42.9% versus 71.0%. Manager picks that select the Pinnacle underdog win 43.5% versus 68.0% predicted. Manager picks 20pp+ above sharp win 37.5% versus 72.7%. At >=80%, picks with no pre-pick sharp win 61.5% versus 87.0%, while picks with a pre-pick sharp win 80.0%.

**Evidence.** See sections 4.2, 4.6, 4.7; case studies 1, 6, 9.

**Proposed change (research only).** Test two pre-registered variants against the unchanged baseline:
1. **Market-availability gate:** do not publish/score a high-confidence model pick until a valid pre-pick sharp reference exists; keep the pick in a research ledger meanwhile.
2. **Market shrinkage:** blend the model logit toward the sharp de-vigged logit with a fixed, pre-registered weight, or cap the claimed edge at a pre-registered maximum. Do not use Guide Odds or post-pick prices.

**What it affects.** Calibration and selection coverage; likely raises abstention; may improve Brier/log loss/ROI on retained picks.

**What could become worse.** Coverage falls materially: 98/237 Manager picks have no pre-pick sharp, and 26/56 high-confidence picks have no pre-pick sharp. Pinnacle can be wrong, stale or suspended. Shrinking to market can erase genuine edges and makes the model dependent on one book. A gate fitted on this window would be retrospective.

**How to test.** Pre-register the gate/shrinkage weight; evaluate on a chronological holdout (for example fit/decide on 2026-09-09 to 2026-10-05, test 2026-10-06 to 2026-10-21) and then in a genuine forward shadow. Report retained coverage, accuracy on retained picks, Brier/log loss, calibration, and realized one-unit ROI/CLV. Compare on the common population as well as the retained population.

**Promotion evidence.** Forward, not retrospective: retained picks improve Brier/log loss and calibration with a paired 95% CI excluding zero; ROI/CLV improves; coverage loss is acceptable and pre-agreed; performance is stable across ATP/Challenger and surfaces; no dependence on a single stale snapshot.

### Experiment 3 - Repair Agent 3 participation/coverage and calibrate the two-agent regime

**What is going wrong.** The Manager's intended third independent check is not operating. Agent 3 predicted only 172 of 30,863 evaluations (0.56%) and abstained on 30,691 (99.44%). All 4,825 Manager consensus rows were 2-agent; there were zero 3/3 consensus picks. A3's support diagnostics show 7,799 `INSUFFICIENT_MATCHUP_SUPPORT` abstentions, 478 `UNKNOWN_SURFACE`, and only 36 predictions over 637 matches. Separately, tight A1/A2 agreement is not a reliability signal: spread <0.02 wins 64.6% versus 73.0% predicted, worse than wider-spread buckets.

**Evidence.** See sections 4.5 and 5; `artifacts/frozen_manifest.json` source counts. The 140 evaluations with all three agents active all ended `NO_BET`; none became a consensus pick.

**Proposed change (research only).** Two tracks:
1. **Coverage repair:** expand the shared-opponent/identity graph and surface resolution so A3 can participate on more matches, with a pre-registered support threshold and uncertainty treatment; do not simply lower the threshold to force participation.
2. **Two-agent calibration:** fit a conditional calibrator or uncertainty penalty for the 2-agent regime, using A3-abstention, A1/A2 spread, and data-quality/market-availability indicators as features. The repository already contains research-only calibrator candidates in `research/a2_calibration_shadow/`; reuse them rather than building a new calibrator. Evaluate a global calibrator only as a control, because the coherent + with-sharp subgroup is already well calibrated (16/17).

**What it affects.** Abstention/coverage; probability calibration; potentially accuracy if A3 adds genuine information.

**What could become worse.** A3 may add noise if its support is thin. Lowering support thresholds can create false confidence. A conditional calibrator can overfit small subgroups. A global calibrator could hurt the coherent/with-sharp picks (94.1% actual vs 86.6% predicted). More agent participation could reduce coverage if the Manager then rejects more matches for disagreement.

**How to test.** Pre-register the support/coverage change and the calibration features; use a chronological holdout and a genuine forward shadow; report A3 participation, 3/3 vs 2/3 performance, Brier/log loss/ECE, coverage, and subgroup calibration. Promote only if A3 participation increases without degrading the coherent subgroup and calibration improves out of sample.

**Promotion evidence.** Forward evaluation shows improved Brier/log loss and ECE, stable or improved coverage, no degradation of the coherent/with-sharp subgroup, and no increase in high-confidence losses.

---

## 6. Required data corrections

1. **Tournament identity and qualifying semantics.** Reconcile canonical tournament names with provider `raw_tournament_name`, `raw_event_qualification`, `raw_tournament_round` and edition metadata. Decide whether "Qualification" events are qualifying or main draws; normalize names/flags. This is a source-owner decision, not an automatic rename.
2. **Missing surface.** 12 Manager picks >=60% (26/346 including <60%) have no PIT surface. Recover from unambiguous provider metadata or exclude the match from surface-dependent models; do not infer a surface from a later source.
3. **Explicit model-pick publication event.** Model-only picks have no `published_at`; forward issuance is proven by pre-start `picked_at`/`prediction_finalized_at`. Add a publication timestamp or event so forward issuance is auditable without inference.
4. **Agent-level decision lineage.** `decision_agents` has 0 rows. Agent contributions are in `predictions`, but not linked to the decision row. Persist the A1/A2/A3 contribution snapshot with the decision.
5. **A1 model-state snapshots.** `rating_snapshots` is empty. Persist the rating/state used at prediction time so A1 input sufficiency can be audited.
6. **A3 support diagnostics coverage.** Diagnostics exist only from 2026-10-08 and only for 637 matches. Backfill or persist the support/gate evidence for every abstention; 2,433 older consensus abstentions have a blank reason.
7. **Result observation deduplication/lineage.** 66/346 Manager matches have two result observations. They agree today, but scoring should explicitly use a frozen, deduplicated result lineage and fail closed on disagreement.
8. **Value Bet publication boundary.** 15/67 Value Bets are pre-publication-boundary ledger entries. Keep them separate from genuinely published picks in every performance report.
9. **Canonical first-pick view.** Add a read-only view/materialized view for "first production evaluation per match" so dashboards and research cannot accidentally score hourly reruns as independent picks.
10. **Historical match start times.** Pre-2026 `match_start_at` is missing. This cannot be fixed retroactively from the current corpus; it remains a blocker for true per-match pre-match replay.

---

## 7. Missing evidence and blockers

- **Small live sample.** 237 Manager picks, 212 scored, 56 scored at >=80%. The high-confidence confidence interval is wide. Do not treat the subgroup p-values as confirmatory.
- **No genuine 3/3 consensus.** Agent 3 does not participate in the live consensus, so the intended three-agent design cannot be evaluated.
- **No agent input snapshots.** Rating/state and support inputs are not persisted, so causal attribution is limited to observable proxies (history depth, surface, market, identity).
- **No explicit model-pick publication log.** The pre-start finalized evaluation is the best available proof of forward issuance.
- **Market coverage is incomplete.** Only 139/237 Manager picks have a pre-pick sharp snapshot; 98 do not. There are no historical 2016-2025 prices. Guide Odds cannot substitute for original prices.
- **Single-provider result truth.** Case-study results are canonical API-TENNIS observations; no independent second-provider cross-check was available.
- **No injury/news data.** Inactivity is a proxy, not verified injury or return-to-play information.
- **Historical replay is not a per-match pre-match replay.** PR #239's tournament-cohort replay is separate and cannot be blended with live picks.
- **No best-of-five evaluation.** Every live pick in this window is best-of-three; the best-of-three versus best-of-five failure mode cannot be tested here.
- **No WTA or future-season evidence.** This is men's ATP/Challenger only.

---

## 8. Tests and validation

| Check | Result |
| --- | --- |
| `python -m pytest tests/test_tennis_model_error_forensics.py -q` | 14 passed |
| `ruff check research/tennis_model_error_forensics_20261021 tests/test_tennis_model_error_forensics.py` | All checks passed |
| `python -m py_compile ...` | Passed |
| Extractor run against production read-only session | Passed; 973 frozen rows |
| Dataset SHA-256 | `ea3809af885643a08dfc15e9eb62d0d6e9ea8a9ed7190002c3f2d552810f281e` |
| Leakage checks | 0 results before pick; 0 picks/finalizations after start; 0 missing start; 0 disagreeing result observations |
| Full repository test suite | Not run; this was a focused research change |
| PR checks | GitGuardian passed; `postgres-foundation` was still pending when this report was finalized (no failure recorded) |

Tests cover band boundaries, ECE, Brier/log loss, Wilson intervals, calibration p-values, history/inactivity buckets, market agreement, cause flags, case-study selection, future-information exclusion in history features, and manifest parsing.

---

## 9. Realistic next development estimates

| Work item | Estimate |
| --- | --- |
| Read-only tournament identity/surface correction audit | 2-3 days, plus 1 week review/source-owner sign-off |
| Market-availability gate + shrinkage shadow implementation | 3-5 days coding/testing; 4 weeks forward accumulation |
| A3 coverage repair + conditional calibration | 1-2 weeks analysis/implementation; 4-6 weeks forward accumulation |
| High-confidence confidence interval tightening to roughly +/-5pp | about 30 weeks at the current live pickup rate (56 scored >=80% in 6 weeks); about 17 weeks for roughly +/-7pp |
| End-to-end pre-registered prospective evaluation | 8-12 weeks for an interim forward read; 30+ weeks for a +/-5pp high-confidence estimate |

Do not shorten these estimates by reusing the discovery window as the evaluation window.

---

## 10. Bottom line

The models are not failing uniformly. They are failing in identifiable places:

- high-confidence 80-89% picks, especially where the tournament label/qualifying semantics are inconsistent;
- picks issued without a pre-pick sharp reference;
- picks where the model claims a large edge over the sharp market;
- a Manager consensus that is structurally two-agent because Agent 3 abstains almost always.

The most defensible next step is not another global threshold change. It is to freeze the current baseline, correct the tournament/surface data with source evidence, and run pre-registered shadow tests of (a) a market-availability/edge gate and (b) a conditional calibration or A3-coverage repair. No improvement should be claimed until a held-out or genuine forward evaluation demonstrates it.
