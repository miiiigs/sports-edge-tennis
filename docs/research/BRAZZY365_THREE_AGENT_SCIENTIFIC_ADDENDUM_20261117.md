# Four-paper scientific addendum: A1 / A2 / market-gate architecture
**Date:** 2026-11-17 · **Source:** Owner-supplied 7-page \`Brazzy365_Three_Agent_Scientific_Research.pdf\` plus direct publisher pages · **Status:** RESEARCH ONLY

## Why this matters
Brian's new research advocates **two distinct outcome forecasters** (overall/surface strength and opponent-adjusted serve/return Markov) plus a **third green-light checking real market price**. This is a valid candidate, not an empirical proof of superiority. Sports Edge already has a different legacy contract: **A1 surface Elo**, **A2 hierarchical Markov**, **A3 intransitivity** as the third predictive agent, **Manager consensus**, and a separate **Market value evaluator**. Therefore **DO NOT rename/replace \`AGENT_3_INTRANSITIVITY\` or overwrite its historical predictions**. Implement the proposed "two predictions + market gate" as a **separate versioned shadow decision policy**, using the **existing market layer**.

Inspected \`main\` code:
- \`src/sports_edge_tennis/agents/agent2_hierarchical_markov.py\` already includes historical data shrinkage. Assess precise formulas, source freshness and tour support before any new A2 implementation.
- \`src/sports_edge_tennis/agents/agent3_intransitivity.py\` is a winner model, NOT market pricing.
- \`src/sports_edge_tennis/manager/consensus.py\` requires three canonical predictive-agent results; do not feed a market approval into this interface.
- \`src/sports_edge_tennis/market/evaluator.py\` and \`pricing.py\` already perform market eligibility, power de-vig and model-price edge.
- PR #291 already explores A3 abstention / A4 reliability and Guide Odds point-in-time prioritization. Compare and reuse; no parallel production redesign.

## Four studies: directly verified publisher findings and limits

1. **Kovalchik (2016)**, *Searching for the GOAT of tennis win prediction*, Journal of Quantitative Analysis in Sports. 11 published methods on **2,395 2014 ATP singles fixtures**; regression models with rankings and a FiveThirtyEight Elo variant were strongest with one year of prior history. Lower-rank cohort is substantially harder (e.g. the paper's comparison shows FiveThirtyEight one-year accuracy approximately **75% top-30 vs 59% outside the top-30**). Bookmaker predictions were a reference. **Implication:** A1 prior-only overall/surface ratings, ranking-band diagnostics, Challenger-specific uncertainty. The study is **ATP 2014**, not transferable Challenger validation.
   Publisher: https://doi.org/10.1515/jqas-2015-0059

2. **Gollub (2021)**, *Forecasting serve performance in professional tennis matches*, Journal of Sports Analytics. **7,622 ATP tour matches (2014–16)**; Efron–Morris small-sample estimation, opponent-adjusted serve/return and Elo-informed serving all improved **serve-parameter forecasts** relative to conventional Barnett–Clarke. In the paper's Table 2, **Klaassen–Magnus Elo-informed variant beat tested Barnett–Clarke variants on match accuracy/log loss** (69.1–69.7% by year). **Implication:** A2 ablation of (i) existing hierarchical/Markov, (ii) opponent-adjusted + shrinkage, (iii) Elo-informed point model, with separate evaluation of serve RMSE and winner log loss. An Elo-informed A2 may be stronger but **less independent of A1**; measure correlated errors. Never use final-match service data pre-match. Challenger serve-stat missingness needs explicit abstention.
   Publisher: https://doi.org/10.3233/JSA-200345

3. **Wang & Drekic (2026)**, *Boosting markovian tennis prediction: Ensembling and point-specific methods for match outcome and duration*, Journal of Sports Analytics, online **19 Jan 2026**. ATP/WTA data 2011–22; tested 2014/2018/2022 major-event subsets. Consensus of 5–6 related Markov variants achieved about **70.9% mean accuracy** at roughly **15–25% coverage**, while majority methods traded some accuracy for higher coverage. This is **NOT a three-agent Sports Edge evaluation**, **NOT a Challenger test**, **NOT a measured betting edge**, and **NOT a recommendation that unanimity is universally optimal**.
   Publisher: https://doi.org/10.1177/22150218251412670

4. **Wilkens (2021)**, *Sports prediction and betting models in the machine learning age: The case of tennis*, Journal of Sports Analytics. **25,204 ATP + 13,755 WTA singles matches** from 2010–19. Forecast models usually did not beat bookmaker probabilities; returns mostly negative over longer horizons, highly volatile, even when ensembles occasionally appeared promising. **Implication:** Never promote strategy from hit rate alone; compare same-fixture probabilistic scores and realistic odds-based outcomes.
   Publisher: https://doi.org/10.3233/JSA-200463

These are valid studies; their quoted returns/metrics are research-population results, not independently reproduced Sports Edge results. No authorized raw data or bookmaker execution audit was performed.

## Versioned experimental policies (do NOT change production)
| ID | Predictive side | Market approval | Candidate question |
| --- | --- | --- | --- |
| O | Original published A1+A2+A3 Manager/v1 policy and historical Market | As originally issued | **Immutable reference**; reconcile all picks/pending/void |
| B | A1 and A2 **agree** (A3 predictive outcome ignored for selection **only in shadow**) | Separate existing market evaluator, **fresh quote**, calculated model EV > conservative uncertainty allowance | Does two independent forecasts + price beat O in accuracy and coverage/ROI? |
| C | Prior-only calibrated A1/A2 mixture, locked weights | Same gate, same quotes | Does calibrated stacking lower paired log loss? |
| D | Three-predictor majority 2-of-3 shadow selection | Same independent gate | Does extra coverage compensate for more errors? |
| E | Original first-issued winner/prediction, unchanged | Existing market gate shadow evaluation | Is complexity necessary? |

**Important:** "majority 2-of-3" is only meaningful when all 3 genuine predictive agents exist. A price gate cannot vote for a player. Market-approved decisions must have verified both outcomes and a contemporaneously available **two-way offered quote**, source identity, freshness and no provider conflict. If OddsPapi is restricted, record \`MARKET_UNAVAILABLE\` and preserve *all* model-only predictions; no fictitious odds and no "pass" inferred.

## Research-only test contracts and decision gates
1. Freeze **all** eligible men's ATP and ATP Challenger singles fixture IDs before results. Include actual tour, surface, match format and original/changed match start.
2. Use **first-issued immutable predictions**, input feature availability <= cutoff, actual publication timestamp, matching player orientation, model version/commit and original selected side. Do not rewrite prior errors/voids.
3. Record A1/A2/A3 raw probabilities, A3 abstention, A4 research context, market quotes, provider source snapshot timestamps and independent current-vs-close quote. If player identity or odds missing, **ABSTAIN / NO COVERAGE** in the market policy, not a fabricated win.
4. Run **chronological walk-forward** with prior-only settled outcomes to fit A1/A2 shrinkage or stacking. For the Gollub variants, compare serve RMSE/point accuracy **and** ATP/Challer winner log loss against A2 as-is. Check error correlations for A1 vs A2 (including Elo-informed A2).
5. Use common-eligible-match **paired Brier/log loss** as primary evidence; compare baseline no-vig bookmaker and surface Elo. Report calibrated hit rates, sample sizes, ATP vs Challenger, ranking bands, odds deciles, retained coverage, selected count, average offered odds and hypothetical flat-stake ROI/CLV only when quotes exist.
6. Compare 3-of-3, 2-of-3, A1+A2+market, prior-only calibrated mixture+market and unchanged original+market. Measure **win rate vs coverage** and model-agreement/error dependence, not only high threshold.
7. Use tournament-week clustered bootstrap and predeclared thresholds; require future sample and baseline improvement, reproducible artifacts and Brian/owner approval before any promotion.
8. Original A3, Manager, Veto, runner, dashboard, betting thresholds, provider subscriptions, selection and settle rules **unchanged in this addendum**.

## Open blockers
- Historical first-issued fixture reconciliation and pending/void audit across *all* dates not yet independently executed against live DB.
- Active Railway branch/SHA and migration state not verified here. GitHub \`main\` is behind the Oct/Nov stacked drafts.
- OddsPapi had a 403 subscription restriction as of PR #291's Nov 10 notes; current resolution not established.
- No genuine live controlled ATP/Challenger study yet; **no accuracy/ROI improvements claimed**.

**Requested disposition:** add these study hypotheses to GitHub issue #304; keep PR #306 documentation-only, and evaluate in existing V2/A4 research branches after lineage confirmation.