# Brian 21 November case-study-first tennis R&D plan and zero-spend provider gate

Internal research reference. Source URLs are public; model claims and published percentages below are **not Brazzy365 outcomes**. This note supplies falsifiable experiments, not licences, approved products or production instructions. No dataset or source code from third-party research has been imported.

## Immediately binding decision
- **No Cito $299 Business CSV, or any other newly paid data/API subscription, without Brian's explicit approval after useful sample fields, overlaps and rights have been proven.**
- $1,000/month is an **approval ceiling for valuable information**, not current spend, an authorized purchase or a requirement to spend. It replaces the older $200 *exploratory additional allocation* as a potential spend ceiling, not as an automatic operational change. Existing subscribed services are separate.
- Steps: inventory our actual gap -> obtain truly free sample -> verify schema, identities, time precision, real overlapping ATP and Challenger matches and incremental unique information -> confirm training/storage/retention/redistribution rights in writing -> price/value decision. **Current Cito public sample contents have not been retrieved or tested.**
- Offline sample field auditor now in src/sports_edge_tennis/validation/provider_free_sample_audit.py; synthetic tests are not live vendor sample validation. No keys, network, DB writes, raw source redistribution or expenses.

## Study 1: Wang and Drekic (2026), paper and original study repository
- Journal article DOI https://doi.org/10.1177/22150218251412670 ; original research implementation https://github.com/c567wang/Point-Based-Tennis-Prediction .
- Study uses 2011–2022 ATP and WTA data; evaluation concentrated on major events and three earlier-year train/one later-year test panels, NOT our Challenger production population. Data and results must not be transferred as validated agent settings.
- Journal Appendix B tests 1-year vs 3-year histories and four levels of common-opponent surface filtering. No-surface-filter common-opponent approach led 26 of 36 same-horizon comparisons, but only 2 comparisons reached p<.05; multiple comparisons and coverage differences require caution. Some individual opponent-adjusted configurations did *better* with 1y than 3y. History length effects are not uniformly positive.
- Consent/vote ensembles reached roughly 70% on some test sets with material coverage selection; papers' denominators/test populations differ from ours. Use risk vs **full official eligible population** and proper scores, not solely selective hit rates.
- The authors' repository README attributes source data to Jeff Sackmann and explicitly marks included data CC BY-NC-SA 4.0 (non-commercial). Source code and data licensing must each be assessed independently. **Do not copy bundled data into commercial Brazzy365.** Public methodology may inform an independently implemented comparison where rights allow.

### A3 experiment design (registered before outcome access)
Read actual current A3 config first: default lookback_days 1460 (4 years), half_life_days 730 (2 years), direct_raw_support 2, direct_effective_support 2.5, normal_network_shared_support 5, normal_network_q_support 8, network_only_shared_support 8, network_only_q_support 16, signal_floor 0.12, activation_probability_floor .53. Production Agent3Config __post_init__ LOCKS core parameters. Never mutate this policy to conduct research; create a separately versioned shadow runner.

Compare same originally eligible, chronological original fixtures for:
  A3-0 Existing exact production config (4y and normal/network-only gates).
  A3-1 One-year history, same thresholds (a shorter window than existing).
  A3-2 Three-year history, same thresholds (also shorter than existing).
  A3-3 Four-year history, surface-weighted vs fully filtered vs unfiltered common-opponent support.
  A3-4 Opponent-adjusted broad-history information with **independent** opponent serve/return features, explicitly checking A2 redundancy.
  A3-5 Hierarchical empirical-Bayes/shrinkage model: account for effective sample size and shrink sparse matchup differences toward *pre-match* overall rating, with uncertainty and abstention when unidentifiable.
  A3-6 Unknown surface recovery from reliable source tournament metadata; use strictly already-available identity, never guess or impute a claimed true surface.
For each variant show raw target denominator, predicted/abstained, support levels A/B/C/D, pre-issue timestamps, n settled, winners & losses, Brier, logloss, confidence calibration, ATP vs CH, and changes in actual Manager counterfactual false-veto/admitted-loss counts. Use identical match cohorts and equal-coverage comparisons.
Existing independently evaluated evidence 2026-10-15..11-20: A3 predicted 7/1683 original first-run targets (0.42%), abstained 1676. PR348 actual original Manager 295 issued, 168W-99L settled267. B guarded 413 issued,259W-117L settled376, C raw average 775 issued465W-237L settled702; **not the same selected fixtures**, so no B/C superiority claim. Prototype Manager C is NOT calibrated.

## Study 2: Pronocast 15 Nov 2026, newcomers and rating uncertainty
- https://www.pronocast.com/en/blog/new-players-tennis-elo-overrated .
- In this source's records, *fewer than ten* prior archive matches when assigned Elo favourite: roughly **32% wins in n352**. Broader self-defined newcomer cohort (<20 archive matches) showed 41.1% winner rate for n665 favourite cases, vs 63.1% expected comparable experienced subset. This depends on **their archive, sample selection and Elo initialization**, not universal newcomer ability.
- Our question: Are our newcomer favourites miscalibrated conditional on prior competition strength, ITF provenance, Challenger promotion, rank-age and effective Elo sample size? Never force every newcomer lower; test initialized/uncertainty aware ratings.
- Pre-register age bands prior recorded 0,1–9,10–19,20–49,50+ and independent ATP/CH cohorts. PIT prior-match count measured only from outcomes known before the prediction. Compare existing A1 initialization, rank/tier prior, Glicko uncertainty and hierarchical ranking prior with equal calibration/validation windows. A challenger ITF entrant may be unobserved, not inherently weak.

## Study 3: 2026 controlled Elo/ML comparison
- https://doi.org/10.3390/analytics5030022 : 133,138 **elite ATP event** retained matches across temporal panels, not a proven Challenger dataset.
- Enhanced Elo 65.87%, best classical ML 66.30%, hybrid Elo+ML 67.52%. This gives a controlled hypothesis that modest well-chosen Elo-derived features may help more than deep networks. Reproduce a simple regularised model **after** valid PIT rating/new-player features; the paper's 67.52% is not our target accuracy guarantee.

## Existing Brazzy365 frozen filter
- PR341 candidate historical 75/25 Elo/Markov blend with >=65% confidence: **167W-63L =72.61% among 230 settled, 49.25% of its paired evaluated cohort**, exploratory and previously inspected. Parameter lock, commit, feature, calibration and original timestamps must remain immutable for forward test, with 50/50 control.
- PR345/346 later retrospective validation-frozen cutoff analysis: 40%-training-band selected 194 of 519 held-out original co-issued pairs =37.38% conditional paired coverage, 134W-46L =74.44% of n180 settled, 14 ungraded. This remains **retrospective previously inspected evidence** and 37.38% is not full official eligible slate.
- Historical 2016–25 database ledger: n117,713 actual result records all with versions ingested after result timestamps (PR347/350). Cannot convert archive replay into originally issued pre-match forecasts; only careful historical source-valid reconstruction can create a defensible retrospective simulation. Not valid to repeat an unproven "~14k at 75%" claim.

## Cito public sample acceptance checklist
Provider public descriptions: https://citoapi.com/tennis-csv-dataset/ ; free sample URLs:
- https://api.citoapi.com/api/v1/exports/sample/tennis/matches.csv
- https://api.citoapi.com/api/v1/exports/sample/tennis/players.csv
The listed URLs were *not accessible from our current research execution environment*. No sample data were downloaded or parsed, and any actual schema/coverage remains **UNVERIFIED**.

When accessible and terms permit, run locally:
python -m sports_edge_tennis.validation.provider_free_sample_audit --matches-csv /local/permitted/matches.csv --players-csv /local/permitted/players.csv
Auditor reports observed headers, filled-cell percentages, actual timestamp granularity, duplicates, SHA256 and only **upper bounds** on two-sided field completeness. Does NOT falsely declare point-in-time truth, real overlap, commercial permissions, price coverage or historical quality from a sample.
Required independent cross-provider overlap report: a minimum 50 manually inspected unmatched original Model Picks when available; exact stable player/fixture identity on same event, competition tier, date, tournament and side; actual quote same bookmaker both sides <=original issue and actual ingestion; honest missing/late/retention/no-market codes. Do not pay to fix mapping until proved.
Cito commercial-service permission is not necessarily third-party historical data IP permission; request written commercial AI model training, derived features, retention, bulk exports, post-cancellation and source-rights coverage (https://citoapi.com/terms/).
Cito marketing describes "opening/closing prediction-market odds where traded"; NOT interchangeable with pre-issue Pinnacle or Bet365 bookmaker odds.

## OddsPapi reference and zero-cost gate
- https://oddspapi.io/en/docs/get-historical-odds documents GET /v4/historical-odds, three bookmakers maximum, original createdAt, active flags, 5s cooldown, and claims history since Jan 2026. It does **not** prove our account can retrieve every past Challenger or that source snapshots were actually ingested before historical original picks.
- Existing measured true same-issue two-sided odds overlap: 20/1179 settled original Model Picks; OddsPapi explicit match IDs 183/1325 issued. Investigate deterministic mapping and sample account entitlement only with permission, never secrets in artifacts, no API charge assumption as an entitlement guarantee.
- Distinguish archived createdAt <= prediction timestamp (vendor could have offered earlier) from ingestion <= actual original publication (we **actually observed** it). Keep these separate market comparators.

## Research decision gates
1. Short-term: original A1/A2 paired forecasts and A3 support audits + same-fixture Manager false-veto, no new paid sources.
2. Verification: replace synthetic sample tests with genuine free provider CSV samples and 50 reviewed canonical overlaps, with commercial rights confirmed before data import.
3. Scientific: predeclared chronological validation/test separation, negative controls including full label-shuffle retrain, player-order swap, results A4 truth, source dates, independent bookmaker on precisely matched odds.
4. Forward test: at least 30 days/200 unique settled for *checkpoint* and aim 1000+ frozen selections to infer sustainability; ATP/CH separately, false-veto winners AND losses, Brier/logloss/coverage/Wilson intervals. No change to production Manager until evidence and explicit release approval.

**Immediate no-purchase instruction supersedes feature advertising and marketing tables.**
