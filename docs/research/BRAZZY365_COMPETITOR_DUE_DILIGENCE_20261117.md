# Brazzy365 competitor AI due diligence — research-only integration plan
**Reviewed:** 2026-11-17 · **Product:** Sports Edge Tennis · **Scope:** men's ATP and ATP Challenger pre-match singles only.

## Scope, evidence, and caveats
This is an independent review of the 25-page *Brazzy365 AI Tennis Intelligence & Development Blueprint* supplied by Brian, **not** a source-code audit of Brazzy365 or a verified competitor performance certification. The source PDF's 20 vendor entries are a market watchlist, not 20 proven tennis prediction products. The 10 candidate GitHub projects are not all reproducible from available data. No paid product, authenticated competitor forecast archive, Sports Edge production database extract, or bookmaker bet execution record was available. **Do not infer a 73–75% win rate or profitability from this research.**

Review evidence tiers: regulator or peer-reviewed source; direct live product or published code; unverified vendor/self-reported performance; independent research proposal. No third-party code or licensed data is incorporated in this change.

## Verified technical opportunities
| Source | Evidence as inspected | Adapt, independently validate, or reject |
| --- | --- | --- |
| TennisBrain | Public filters, odds comparison, probabilities; vendor claims not independent accuracy | Adapt UI patterns only, with quote freshness |
| TennisPredictions.ai | Public highlighted picks/grades; 81% banker claim is vendor self-report | Require full denominator, sealed pre-match archive and offered odds |
| BananaPoint | Downloadable historical results claimed by site; archive authenticity not certified | Compare daily pre-match captured versions, not retroactive totals |
| Pronocast | Live pricing page shows Free and **€9.99/mo Pro**; PDF's free-beta status is outdated | Preserve pre-match timing and source traces |
| TennisData.App | Metered odds/statistics/prediction API, Starter €24.99/mo, prediction/stat request 30 units | Seven-day licensed ATP/Ch coverage pilot, no assumption of unrestricted reuse |
| coenensam/tennis-match-predictor | MIT code, separate NC source data; adaptive surface Elo, prior-only rolling serve/return; author reports XGB loses to Pinnacle close on 2,409 same matches | Independent Elo shrinkage and market encompassing test, not copy data |
| Volodymyr4K/market-efficiency-lab | MIT code; code exposes fold-prior walk-forward and cluster/bootstrap statistics, author reports 75,044 odds-linked samples with sharp close superior | Reproducible no-vig market benchmark, uncertainty, multiple-test discipline |
| zachdcali/tennis-prediction | Public README, private actual pipeline/dataset; author reports 65.7% model vs 66.8% close on 629 resolved | Learn publication/heartbeat concept; **cannot scrape private source** |
| ulpati/atp-tennis-predictor | Actual repository **code licence CC BY-NC-SA**, not just data; author claims leakage guards | Treat implementation as NC, learn concepts only |
| Nickan-ShabanNejad/ATP-Probabilistic-Model-for-Forecasting-Tennis-Match-Outcomes | Self-reported odds-based negative realized ROI despite expected EV | Reject unverified profitability from confidence/hit rate |
| jackkli9/tennis_prediction_project | Source code separates stable occurrence IDs from scheduled times; price-leakage rejection tests and strict rights register | Apply repeated-fixture/alias safeguards and source licence gates |

Other named platforms in the PDF remain a watchlist. Claims about their prediction algorithms are **not verified**.

## Sports Edge-specific assessment
Inspected GitHub repo \`miiiigs/sports-edge-tennis\` default \`main\` at SHA \`b6b7e506687172a0b08441bb006013d131a19fcd\`, dated 17 Oct 2026. Actual deployed Railway SHA, connected database migration state, and 17 Nov first-issued production ledger were **not** independently accessed. The existing architecture already includes A1 surface Elo, A2 hierarchical serve/return Markov, A3 intransitivity, Manager, point-in-time fields, calibration monitoring, CLV and result-coverage monitoring. Avoid duplicating these. In \`docs/research/CHALLENGER_CALIBRATION_REPORT_20261013.md\`, high-confidence Challenger overconfidence is documented for A1/A2; retuned calibration does not reliably improve unseen holdout.

Existing **draft-only** research lanes must be reviewed before any new modeling:
- PR #286 V2 quality gates and loss analysis
- PR #291 A3-risk / A4 reliability / point-in-time Guide Odds prioritization
- PR #274 pending match triage
- PR #260 winnerless walkovers, with migration approval/backup gates

PR #291 documents OddsPapi HTTP 403 subscription restriction as of November 10; this is not verified resolved. No fresh odds, CLV, or positive betting edge can be inferred.

## Proposed first experiment: original offline competitor comparison
A tested original prototype and 7 synthetic tests are included in the accompanying downloadable research bundle delivered to the user; **they have not yet been imported into this GitHub branch**. Do not present this document-only PR as a deployed feature. The prototype accepts only permissioned CSV fixture and captured forecast snapshots, explicitly scores publication time and capture time **before** fixed match cutoff (default 30 minutes), rejects ambiguous duplicates and unknown matches, keeps NO COVERAGE and unfinished/void match statuses, and reports per-tour coverage, Brier, log loss, hit rate and paired matched-fixture comparisons. SHA-256 identifies input files but is **not** evidence of third-party earlier publication.

### Acceptance gates before source import / live execution
1. Identify active Railway runner SHA, source branch and production migration state; do not merge into wrong lineage.
2. Reconcile **every** original first-issued pick by match ID: new/remaining/settled/void/pending, preserving all historical days and authorized source observations.
3. Complete rights approval for each captured competitor archive, quote feed, and any source used in commercial evaluation.
4. Lock future ATP/Ch singles fixture universe independent of predictions; preserve missing forecasts and non-final matches.
5. Obtain authenticated *before-start* snapshots with captured time, original publication timestamp, source, digest, participant orientation, publisher/version, and real contemporaneous offered odds.
6. Compare existing A1/A2/Manager, proposed V2/A4 and competitors **only on shared eligible fixtures**; distinguish coverage from conditional accuracy.
7. Compute paired log loss and Brier against as-of surface Elo and same-cutoff no-vig bookmaker market; bootstrap by tournament-week, quantify uncertainty and odds sensitivity.
8. Maintain research-only status. No changes to Manager picks, thresholds, settlement, stakes, production odds or dashboard until owner sign-off and meaningful out-of-time improvement.

## Sources
- Original PDF: *Brazzy365 AI Tennis Intelligence & Development Blueprint*, 17 Nov 2026, pp. 1–25, user supplied.
- Peer-reviewed benchmark: https://www.mdpi.com/2813-2203/5/3/22
- Vendor pages: https://tennisbrain.ai/pricing ; https://tennispredictions.ai/ ; https://bananapoint.ai/track-record ; https://www.pronocast.com/en/pricing ; https://tennisdata.app/tennis-api/
- Public code: https://github.com/coenensam/tennis-match-predictor ; https://github.com/Volodymyr4K/market-efficiency-lab ; https://github.com/zachdcali/tennis-prediction ; https://github.com/ulpati/atp-tennis-predictor ; https://github.com/jackkli9/tennis_prediction_project
- Rights and UK tipster guidance: https://www.asa.org.uk/advice-online/betting-and-gaming-tipsters.html
- Tracking issue: #304

**Status: documentation and offline local prototype only. No production release, verified comparator ROI, or independently graded live comparison.**
