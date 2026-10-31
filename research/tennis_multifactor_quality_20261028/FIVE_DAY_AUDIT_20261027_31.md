# October 27–31, 2026 five-day aligned Model Picks — chronological quality-gate audit

**Status:** Research-only. No changes to issued predictions, results, betting rules, probabilities, market odds, settlement or the production dashboard. This document records the observed five-day test against pre-match features, including a strictly prior-history training gate.

## Sources and frozen cohort

- Live original aligned Model Picks CTE: earliest qualifying production Manager consensus with >=60% confidence or A1/A2 same-side with A3 abstention that exceeded Manager spread gate. The latter is tracking-only, not approved market bets. Date is original match start in Asia/Manila, not settlement time.
- Canonical export: `ops/calibration_dataset.sql` on PR #273 head `81315e947046812171dcfe38f39b0a414ea61b94`; query was read-only on production PostgreSQL; 945 rows total, 101 original aligned picks on Oct27–31. The source export includes actual observation timestamps to avoid training on late-known outcomes. Only aggregate metrics were logged; no private row data, API credentials or prices exported.
- One-time Railway isolated read-only research job: `858c2293-e412-4633-9212-c32c32e70669`. Live production runner / dashboard were not redeployed.
- Training gate defined by `research/tennis_multifactor_quality_20261028/quality.py`; frozen cutoff `2026-10-26T16:00:00+00:00` (Oct27 start in Manila). 755 prior-observed graded training picks. No Oct27–31 outcomes used to select a candidate.
- Win rate is WON / (WON + LOST). VOID and PENDING remain visible but are not graded. A day with pending results cannot pass final acceptance. Small-N and zero-pick days are not evidence of performance.

## Five-day actual scorecard

| Manila match date | Original picks | Won | Lost | Void | Pending | Graded win rate | Acceptance |
|---|---:|---:|---:|---:|---:|---:|---|
| Oct27 | 41 | 31 | 8 | 2 | 0 | 79.5% | MET |
| Oct28 | 13 | 9 | 3 | 1 | 0 | 75.0% | MET |
| Oct29 | 21 | 11 | 7 | 3 | 0 | 61.1% | BELOW |
| Oct30 | 12 | 6 | 4 | 2 | 0 | 60.0% | BELOW |
| Oct31 | 14 | 5 | 5 | 0 | 4 | 50.0% | UNSETTLED |
| **Combined** | **101** | **62** | **27** | **8** | **4** | **69.7%** | **NOT ACCEPTED** |

Oct31 results may evolve as four pending fixtures settle, but even if all four are wins, Oct31's fixed cohort would finish 9–5 (64.3%), below 73%. Thus the original population cannot achieve five consecutive >=73% days, independent of what the remaining four do.

## Fixed-rule read-only results (not deployed)

| Predeclared rule | Oct27 W–L | Oct28 W–L | Oct29 W–L | Oct30 W–L | Oct31 W–L (+ pending) | Coverage of original 101 | Conclusion |
|---|---|---|---|---|---|---|---|
| Original | 31–8 | 9–3 | 11–7 | 6–4 | 5–5 (+4P) | 101/101 | Live five-day target failed |
| Raw >=70% | 20–3 | 8–1 | 6–4 | 4–3 | 1–2 (+3P) | 59/101 | Training-selected candidate; failed last 3 days |
| Raw >=80% | 6–2 | 3–0 | 2–2 | 1–2 | 0–1 (+2P) | 22/101 | Failed and sharply reduced coverage |
| Known surface | 31–8 | 9–3 | 11–7 | 6–4 | 5–5 (+4P) | 101/101 | No discriminatory value |
| A1/A2 agreement gap <5 points | 5–1 | 4–0 | 3–4 | 2–1 | 0–0 (no picks) | 22/101 | Failed Oct29, zero Oct31 |
| >=70% & A1/A2 gap <10 points | 6–2 | 6–0 | 5–3 | 4–0 | 0–0 (+1P) | 28/101 | Higher pooled rate but failed Oct29 and no graded Oct31 |
| Multi-factor balanced (confidence >=76% Challenger, otherwise >=70%; agreement <10 points, supported surface/tier) | 5–2 | 4–0 | 5–2 | 4–0 | 0–0 (+1P) | 24/101 | Oct27/29 71.4%; tiny sample on Oct28/30; no Oct31 scored |
| Multi-factor strict (>=80%, agreement <5 points, supported surface/tier) | 2–0 | 2–0 | 1–2 | 1–0 | 0–0 (no picks) | 8/101 | Very low coverage, failed Oct29, zero Oct31 |
| Tier guard (>=80% Challenger, >=70% other eligible tier) | 12–3 | 3–0 | 3–2 | 2–2 | 0–1 (+2P) | 31/101 | Failed Oct29–31, low N |

**Chronological selection:** the *training-only* gate selected raw >=70%, with 288W/96L = 75.0% across 384 graded eligible training picks (50.9% training coverage), a +5.46 percentage-point training lift over the original 755-graded pre-Oct27 baseline. This candidate did **not** meet the daily >=73% target on October 29, 30 or 31. Proposed multi-factor balanced/strict/tier guards did not meet the training minimum 45% coverage, so no improved version was justified for live release.

The >=70% + agreement gap <10 variant has 21W/5L = 80.8% combined *graded* in the five-day retrospective, but only 28/101 (27.7%) pick coverage, one pending pick, Oct29 62.5%, and zero graded Oct31. It cannot certify five consecutive days. Reporting its pooled 80.8% alone would be misleading.

## Technical root causes and action

1. **Different eligibility buckets:** the October 31 original aligned ledger contained 14 A1/A2 wide-spread tracking picks and zero Manager picks >=60%. A separate Manager-only ledger included two sub-threshold consensus selections (56.68% and 54.36%), which legitimately do not qualify under the fixed original aligned rules. No dashboard cohort should be rewritten to imply those were qualified.
2. **Mean probability hides disagreement:** e.g., A1=90.7% and A2=58.4% can average to apparently strong confidence; the Manager abstention-on-spread exists specifically to prevent presenting that as robust consensus. Keep abstention intact. Pure >=70/80 confidence was demonstrably insufficient. The fact that even multi-factor abstention fails some days rules out a proven 73% fix from these tested simple rules.
3. **OddsPapi HTTP 403 `SUBSCRIPTION_RESTRICTED`:** OddsPapi is downstream from predictive-agent / Manager probability calculations; it blocks Guide Odds, bookmaker quote collection, price comparisons and some market-value decisions, **not** the probability forecast itself. A pricing-source issue is not proof of bad model predictions. No fabricated odds or unapproved substitutes.
4. **Further legitimate R&D:** use source-frozen match identities, model lineage, pre-match surface/competition tier, recent form vs full history and absence/limited surface experience, opponent-strength context (ATP vs Challenger), probabilistic calibration (reliability/Brier/log loss), A3 abstention patterns and A1/A2 spread. Evaluate equal-calendar, equal-match bookmaker-favourite baseline once authenticated historic price data exists. Compare full unfiltered baseline, qualified shadow, retained coverage, missed winners, avoided losses and all pending/voids. Require adequate samples and *genuinely forward* five fully settled days after policy is fixed. Fail closed if history missing; ensure no leakage.
5. **Engineering order:** preserve current production model / 73% historical acceptance baseline; finish authoritative pending settlements, restore OddsPapi subscription and correct event/player-side Guide Odds, add transparent tracking-vs-qualified labels and data-quality diagnostics, then run versioned shadow-only quality gate. No changes to historic predictions, no retroactive exclusion of October 29–31 losses, no reclassification to meet KPI.

**Decision:** keep the current live model unchanged. Reject a simple >=70% live switch based on this untouched holdout, and do not claim any of the tested alternatives achieves 73% every day. Continue a prospective versioned calibration/quality-gate experiment with controlled release only if justified by results and explicitly accepted.
