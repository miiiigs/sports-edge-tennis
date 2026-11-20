# Sports Edge Tennis | Competitor comparison and model-improvement research baseline

**Research as-of:** 2026-11-20 02:37 UTC (10:37 Asia/Manila). **Status:** research-only; no live model or betting policy change.  
**Source of operational truth:** Railway runner `80f4b045-a354-41df-a786-77725a4b497a` at SHA `a9a2ca5`; dashboard `2eb56642-6bec-40f9-9d43-92f0a61cea3d` at SHA `1eba401`. This document is not a substitute for an up-to-date DB/dashboard readback.

## 1. Live status, real denominator

- 20 Nov 02:02 UTC cron run succeeded; production batch: 2 successful date-scoped slates, one unavailable (22 Nov API-Tennis success envelope `result=NULL`), 261 fixtures, 26 targets, 7 Manager consensuses, 4 OddsPapi attempts, 72 normalized quotes, one Type A qualification.
- Health = **WARNING/FRESH**, not green end-to-end: `completed_runs=1 failed_slates=1 partial_slate_failure=1`. Do not convert `NULL` to an empty slate, reissue old picks, or infer missing winners.
- Current diagnosed pending results = 18 (14 NOT_STARTED, 4 AWAITING_RESULT; three rescheduled). Four checked in result refresh; no terminal found or unsafe forced settlement. A distinct settlement summary says one pending *within 549 settlement-eligible decisions*, not one pending among all Model Picks.
- 19 Nov original aligned selections: 16 total, 13 won, 1 lost, 2 pending, 0 void. Settled accuracy **13/14 = 92.9% provisional**. Manager first-production subset: 4W/1L, n=5.
- 15–19 Nov >=70% confidence selection research (not deployed): 64 original, 34 retained, 24W/7L/3VOID, **77.4% of 31 settled**, coverage **34/64 = 53.1%**. Days 15 and 16 had no genuine original picks. Not a five-day operational win-rate acceptance.

## 2. Existing October 24–27 internal retrospective ablation

| Candidate | Graded W-L | Voids | Accuracy (settled) | Original pick coverage |
|---|---:|---:|---:|---:|
| All original aligned | 58–33 | 7 | 63.7% | 98/98 = 100% |
| Confidence >=70% | 34–14 | 3 | 70.8% | 51/98 = 52.0% |
| Confidence >=80% | 14–5 | 1 | 73.7% | 20/98 = 20.4% |
| Known surface only | 51–28 | 6 | 64.6% | 85/98 = 86.7% |
| A1/A2 close agreement | 13–9 | 1 | 59.1% | 23/98 = 23.5% |

These are retrospective overlapping cohorts, not an independent randomized experiment. A >=80% filter trades away 79.6% of original coverage and yields a wide uncertainty interval (Wilson 95% approx. 51.2–88.2%, n=19). Do not activate based on retrospective optimization.

## 3. Competitor signal observed, not comparable H2H yet

An immutable first-seen public TennisData.App capture at **2026-11-19 06:18:43 UTC** contains two predictions: Ben Shelton over Rafael Jodar at 64%, and Alexander Zverev over Arthur Fils at 73%. ATP Finals official reporting on 19 November lists **Shelton** and **Zverev** as winners. Thus these two illustrative calls are **2/2 winners**; they are *not* a representative estimate of vendor accuracy. Source outcome reporting:
- https://www.nittoatpfinals.com/en/news/shelton-jodar-nitto-atp-finals-2026-thursday
- https://www.nittoatpfinals.com/en/news/zverev-fils-nitto-atp-finals-2026-thursday

The existing first-capture matcher returned **2 captured / 0 canonical matched / 0 original Sports Edge same-fixture forecasts**. Both remain `CANONICAL_FIXTURE_PENDING` pending an identity + draw/timing check. No legitimate same-match win-rate, Brier, log loss or ROI difference can be calculated. Do not bridge with a similar-looking fixture or invent a historical Sports Edge prediction.

**Rights:** The saved capture includes `rights=authorized_public_export`, but that string is a self-assertion, **not** a commercial-use license or independent publication proof. TennisData.App's terms restrict scraping, reverse engineering, and supporting a competing product/dataset, so do not automate vendor ingestion or feed its forecasts to Sports Edge without written permission:
https://tennisdata.app/terms/

TennisPredictions.ai advertises CSV archives, but its terms restrict materials to personal, noncommercial use absent prior written permission. BananaPoint.ai advertises freely downloadable CSV/XLSX hit-rate archives; **free to view/download does not prove permission to reuse commercially**. Permission/licensing comes first.
- https://tennispredictions.ai/progress
- https://tennispredictions.ai/terms-and-conditions
- https://bananapoint.ai/track-record

Other possible evaluation partners are TennisData.App's documented predictions API and MatchMind's commercial API. Obtain written evaluation rights, version semantics, historical pre-publication timestamps and costs before purchases or automated integration.
- https://tennisdata.app/tennis-api/docs/
- https://tennismatchmind.com/api.html

## 4. What is safe to integrate into our AI now

**A1 player strength**: compare our frozen surface-Elo baseline against independently implemented ranking uncertainty, surface-specific adjustment, inactivity decay and opponent-strength strata. No leakage of results, future rankings or mutable historical player identities.

**A2 serve/return**: derive opponent-adjusted service and return probabilities (surface- and recency-aware), use shrinkage for sparse/Challenger history, and compute match win probability from a point/game/set Markov abstraction where point-in-time features exist. Measure gains beyond A1, not a redundant third vote.

**A3/Manager**: compare a frozen A1/A2 consensus and a calibrated combination on the exact same matches. Report ablations by rank band, Challenger vs ATP, surface, draw, evidence depth, player inactivity, and strong-vs-weak opponent disparity. Keep odds outside predictive agents; market-value gate remains distinct and is never proof of pick accuracy.

Three example **MIT-licensed code** repositories for architecture and test study (verify actual file licenses before copying; MIT notices required):
- https://github.com/urwishpatel2003/tennis-engine/blob/main/LICENSE
- https://github.com/coenensam/tennis-match-predictor/blob/main/LICENSE
- https://github.com/saiompatro/tennis-match-predictor/blob/main/LICENSE

**Critical data licensing:** Several such repos draw on Jeff Sackmann ATP/WTA data with CC BY-NC-SA 4.0 terms, **not automatically authorized for commercial Sports Edge use**. Separate code license from data rights, and use only our independently rights-cleared point-in-time history or get a commercial grant.

Costs: research implementation and offline evaluations using existing licensed infrastructure/data need no new vendor subscription; production odds budget must remain capped. Commercial competitor archives/APIs may require a negotiated paid license; price/entitlement not confirmed.

## 5. Benchmark acceptance contract, frozen before experimentation

1. Cohort: original first-issued non-experiment ATP/Challenger predictions; original `prediction_finalized_at` and source `captured_at` at least 30 min before recorded point-in-time fixture start. Split by tournament edition; match/date and player identity deterministic and immutable.
2. Exact same canonical fixture ID, verified draw, participants, start and probability orientation. No joining by name alone, no retrospective prediction creation. Unknown provider rights or publication proof means not externally validated.
3. Grade only completed match outcomes with an authoritative winner. DEFAULTED, WALKOVER, RETIRED, CANCELLED and unresolved outcomes contribute to issuance/coverage/void accounting, not a won/lost numerator. Preserve opening+entered−resolved=closing pending ledger.
4. Compare each candidate to the unchanged original on **identical settled fixtures**: paired hit rates, Brier, log loss and tournament-cluster uncertainty. Separately show volume/coverage, both independent and shared; include rejected/unmatched rows. ROI only where genuine point-in-time price exists.
5. Target >=30 calendar days of genuinely prospective shadow operation and >=200 settled comparisons per candidate, with Brian's agreed 40% coverage definition. Neither sample count nor a retrospective fit alone validates a live rule. Version and preregister the candidate, avoid look-ahead training, quarantine late features.
6. Only after independent confirmatory evidence and explicit release authorization may a proposed model/Manager selection policy move to a separate production PR with full CI, safe deployment and live monitoring.

The repository research comparator `src/sports_edge_tennis/validation/competitor_forward_audit.py` already enforces signed-up rights categories, strict point-in-time cutoff and identity, source version, settled-only metrics and paired per-tour comparison. PR #329 adds pooled **same-fixture shared-population** metrics, proper source-specific coverage denominators, paired Brier/log-loss deltas and a 200-match/30-day **size-only** gate. It is a scoring harness, not a new live AI model or evidence of competitor superiority.

## Next tracked actions

- Confirm provider Nov22 NULL semantics via provider documentation and natural cron; keep health WARN until full availability.
- Close pending only on verified provider outcome with match identity and settlement semantics; reconcile latest dashboard vs DB by day.
- Request authorized vendor evaluation/history access or use independently consented data; don't scrape restricted sites.
- Implement frozen A1/Elo and A2/opponent-adjusted serve-return shadow candidates with preregistered comparison, tests, zero impact on live Manager until independent forward evidence.
