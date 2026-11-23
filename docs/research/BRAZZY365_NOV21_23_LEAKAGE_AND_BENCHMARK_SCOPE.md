# Brazzy365 / Sports Edge Tennis — Brian's 21–23 Nov research mission

**Prepared:** 2026-11-20 UTC. **Requested handoff:** Monday night, 2026-11-23 (Brian extended the suggested 48-hour research window).  
**Branch:** `research/tennis-leakage-swap-null-audit-20261120`. **Live winner policies:** frozen; no changes authorized.
**Research priorities:** 45% AI/model performance; 35% competitor evidence; 15% historical testing; 5% infrastructure.

## Confirmed results, independent denominators

- November 15–19 >=70% research filter: **34 retained = 24 won + 7 lost + 3 void**, original 64 selections. Graded accuracy 24/31 = 77.4%; retention 34/64 = 53.1%; **research-only**. Nothing is missing.
- The historical **26 prediction targets / 7 Manager consensus / 1 market-qualified** figures refer to different processing stages. Manager selection and market qualification must be audited as separate funnels. The remaining 6 Manager selections cannot automatically be described as rejected Model Picks; do not imply that market-gating is required for match-winner publication without checking actual source code and event records.
- Most recent previously verified production cycle (Nov 20 11:06 UTC, production `a9a2ca5`): 298 fixtures, 26 targets, 5 Manager consensus, 70 offered quotes, 2 market-qualified, 3/3 slates and healthy. **These are not the same cohort/window as Brian's 26/7/1**.
- November 17–19 **original**: 48W, 12L, 4 VOID (80.0% of 60 graded). November 18 alone 70.0%, breaking five successive days >=73%. As-of earlier Nov20 dashboard DB parity test, Nov20 6W–1L–5 pending of 12 picks, strictly provisional.
- A1 calibration research, PR #330: 574 first-issued graded historical holdout, 17-day span; Brier .2280939 → .2261894, log loss .6524313 → .6437993, winner accuracy **unchanged 64.29%**.
- Independent A1/A2 co-issued research, PR #332: 464 graded first-issued matches, 17-day holdout, 433 Challenger/31 ATP. Same-fixture equal-log-odds blend vs A1: Brier .2339261 → .2250774 (3.78% lower); log loss .6645409 → .6403363 (3.64% lower); winner accuracy **62.93% → 62.72%**. **No evidence of better match winner selection yet**. Candidate versions remain research-only. Cohorts PR330 and PR332 are different and must not be directly pooled.

## Mandatory leakage audit matrix

| Control | Status as of 20 Nov | Objective / next required evidence |
|---|---|---|
| Agent 1 and Agent 2 player-order swap | **New regression tests in this PR; awaiting CI and DB experiment** | Same named player's probability after all inputs and histories are swapped must remain identical up to rounding |
| Randomized-outcome negative control | **New deterministic within-tour label permutation in this PR** | Preserve P1 base rate and tour mix; real-match association should vanish. Even a passing null is NOT a full feature-leakage certificate |
| Winner/loser input columns | **Needs provider/feature lineage code review** | Verify no winner-first CSV columns or match result winner-side enter pre-start resolver or agents |
| Future rankings, player identity and match results | Existing strict as_of/source-observed/ingested checks in A1/A2 first-issue validation; **full lineage audit not complete** | Query sampled source features and version history and reconstruct as-of snapshots |
| Post-match serve statistics and recent form | **Needs PIT join audit** | Confirm serve/return and other features were both observed and ingested before each prediction issued_at; missing ≠ zero |
| Closing odds leakage | Existing agent compute modules intended market-blind; **full input lineage not certified** | Verify closed/closing odds never feed independent A1/A2/A3 or pre-start Manager scoring |
| Duplicate matches, player identities, provider joins | Existing canonical matching in pipeline; **full cross-provider sample audit pending** | Count conflicting IDs and duplicates over historical window |
| VOID, DEFAULTED, WALKOVER, RETIRED | Explicit support and exclusion in prior PR #318/#328; **training inclusion needs separate check** | Latest terminal result only, never a winnerless or awarded VOID graded as a played win |
| Train/test same-match overlap; A4/Manager hindsight | Existing date-filtered shadow holdouts; **full code+dataset check pending** | Fixture-unique chronological training, validation, untouched test and full timestamp constraints |

**Do not certify all 14,000 claimed historical matches without checking the actual current database counts, event provenance, licensing and pre-match feature availability.** Current validated PR332 pair data: 2,353 source co-issued rows; 2,315 accepted timestamp/identity pairs; 464 settled in its chosen 17-day test window. That is not equivalent to a complete 14k-match historical research dataset.

## Verified scientific direction and limits

1. Kovalchik 2016, DOI 10.1515/jqas-2015-0059: studied 2,395 ATP singles matches; Elo and rank-based approaches are strong simple baselines. **Action:** retain untuned surface Elo comparator, require proper same-fixture validation. https://www.degruyterbrill.com/document/doi/10.1515/jqas-2015-0059/html
2. Angelini, Candila, De Angelis 2022, DOI 10.1016/j.ejor.2021.04.038 (publisher PII S0377221721003234): Weighted Elo updates include past **scoreline margin**. **Action:** prove historical set scores were available before the *next* prediction; scoreline is not a pre-match feature for the match being graded. https://www.sciencedirect.com/science/article/pii/S0377221721003234
3. Gollub 2021, DOI 10.3233/JSA-200345: serve-point forecasts and opponent adjustments, potentially independent of Elo. **Action:** data availability report (point/tour/surface and PIT missingness) before implementing opponent-adjusted A2. https://journals.sagepub.com/doi/10.3233/JSA-200345
4. Bunker et al., published online 2023 / volume 2024, DOI 10.1177/17543371231212235: compared Elo, WElo and machine learning under expanding historical windows, important for reproducibility. https://journals.sagepub.com/doi/full/10.1177/17543371231212235
5. Wilkens 2021, DOI 10.3233/JSA-200463: historical betting odds generally contain significant predictive information, and apparent profitable strategy claims face practical uncertainty. **Action:** market-implied benchmark in a distinct track; do not reintroduce Value Bets into Model Picks. https://journals.sagepub.com/doi/full/10.3233/JSA-200463
6. Kovalchik & Reid, within-match calibration 2019 (online 2017), PII S0169207017301395: Elo/serve-point and update techniques are interesting, but reported improvements are **in-play** and cannot be presented as pre-match gains. https://www.sciencedirect.com/science/article/pii/S0169207017301395
7. Dynamic graph bookmakers' odds paper 2026, DOI 10.1098/rsos.252512: forecasts **bookmakers' probabilities**, a different target from independently predicting tennis winners. Test only as an optional market-information control. https://doi.org/10.1098/rsos.252512

Competitor repos to license-check and independently reproduce:
- https://github.com/jackkli9/tennis_prediction_project — author claims calibrated ATP+Challenger walk-forward results, depends on external datasets, verify license.
- https://github.com/ulpati/atp-tennis-predictor — author claims 66.3% on 19,086 unseen matches, NOT independently replicated. Verify exact feature cutoff/ordering and dataset rights.
- https://github.com/rorybunker/tennis-elo-vs-ml — scientific Elo/WElo/ML research implementation; verify repo and data license separately.

Vendor model predictions still have **zero authorized, canonically matched Sports Edge same-fixture pairs** in the existing saved comparator. Publicly viewing/reading a competitor site is not a commercial API license; paid/automatic ingestion requires explicit access rights and documented source timestamps.

## Design gates / reproducibility

- Freeze source commit for model, feature and prediction, odds and Manager versions. Source dataset query/retained rows hashed, random seed 20261120, timezone UTC, no future-sourced joins.
- Two tracks: (A) independent tennis-only forecast (no bookmaker odds), (B) research-only market-informed calibrated probability using contemporaneous paired bookmaker odds after de-vig. Both against identical fixture/settlement cohort.
- Full funnel: fixture ingestion -> canonical identity -> target -> each agent result/abstention -> all-three -> Manager consensus -> publication -> confidence research-only retention -> independent Market/odds qualification. Use **non-overlapping denominators** for each step and export every reason code. Do not assume Market gates Model Picks.
- For new candidates require genuine chronological out-of-sample training, calibration, holdout and subsequent forward capture; reserve untouched final test, bootstrap by tournaments or players; ≥30 days and ≥200 settled candidate fixtures + agreed >=40% coverage before production selection changes.
- Rerun and report **failed as well as successful** experiments. Research → hypothesis → experiment → independent validation → production decision.

## Monday deliverable cutline

This document is an **initial frozen scope and status**, not a completed 21-item audit or a promise that automated weekend experiments have been scheduled. Before treating the task as complete: (1) negative-control CI and read-only real-data run, (2) actual feature-lineage audit, (3) full funnel audit and publisher-vs-market separation, (4) earliest-issued representative benchmark with data/version hashes, (5) tested advanced Elo and A2 availability candidates, (6) written competitor rights check. Deadline discussed: **Monday night, 23 November 2026**; no live model-rule changes without approval.
