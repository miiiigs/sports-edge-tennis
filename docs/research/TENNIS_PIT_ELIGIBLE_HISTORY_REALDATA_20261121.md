# PIT fixture denominator and legacy historical provenance | 21 Nov 2026

**Status: independently executed read-only research. No production release, no claim of full eligible ATP/Challenger coverage.** This file summarizes the original first-run PIT census and old historical-source limitation.

## Exact execution

- Code PR #347 merged SHA 3ac3ab08c6848edbb06724de963fe856714a1a79, PostgreSQL Foundation CI 44865204089 SUCCESS.
- Source at fixed PR head 76e4fcc289bbaa425d6eb976e9112ca4d15ad0a2; module src/sports_edge_tennis/validation/pit_eligible_denominator_provenance.py.
- Railway pinned one-off read-only deployment 21fcdc36-97bc-4b16-be89-2605de893b23, then temporary service permanently deleted.
- Window start 2026-10-15T00:00:00Z, end 2026-11-21T00:00:00Z, report as-of 2026-11-21T03:30:00Z. Actual observation and ingestion timestamps all must be <= match-start minus 30m to qualify PIT fixture history. All records are from already-stored database observations, no new API requests.
- Recent fixture census SHA-256 1364e351351bbd0a48880ec4159aa364070a25eab60d9e712936d51c9594bb77.
- Legacy 2016–25 result audit SHA-256 adc156f09bc78bfdc4316fcd252df2c0e7833877b9eb7ba351c3f88eb3a82b1f.

## Current original fixture/selection census (stored, not a provider-independent eligible slate)

| Category | ATP main | Challenger | Both |
|---|---:|---:|---:|
| Stored latest-as-of calendar fixtures | 346 | 1,552 | 1,898 |
| In-calendar PIT version visible >=30min before fixture start | 339 | 1,349 | 1,688 |
| PIT status SCHEDULED, including qualifying | 327 | 1,322 | 1,649 |
| PIT SCHEDULED qualifying | 86 | 367 | 453 |
| PIT SCHEDULED main draw **proxy** | 241 | 955 | 1,196 |
| First completed nonexperimental production run targeting main proxy >=30min pre-match | 241 | 955 | 1,196 |
| First-run A1 and A2 both predicted, main proxy | 136 | 839 | 975 |
| First-run A3 predicted, main proxy | 4 | 3 | 7 |
| First-run Manager CONSENSUS, main proxy | 28 | 185 | 213 |
| On-time dashboard-aligned Model Pick, main proxy | 62 | 482 | 544 |
| Pick with subsequently safe COMPLETED result | 60 | 457 | 517 |
| Pick still ungraded/noncompleted | 2 | 25 | 27 |

Mutually exclusive per-fixture classification over 1,898:
- ON_TIME_ALIGNED_MODEL_PICK: 544.
- NO_ON_TIME_ALIGNED_MODEL_PICK: 652.
- QUALIFYING_REPORTED_SEPARATELY: 453.
- PIT_NOT_SCHEDULED: 39.
- NO_TRUSTED_IN_WINDOW_PIT_FIXTURE: 210.

No selections were silently dropped. The 210 non-PIT rows are not treated as viable contemporaneous fixtures, the 453 qualifying fixtures remain explicit, and 39 PIT non-SCHEDULED cases remain separate.

**Severe denominator limitation:** all 1,196 main-draw PIT SCHEDULED rows already have an on-time completed original production run. This implies the currently available PIT-version ledger largely describes the *targets the pipeline already processed*. Therefore:
- **544/1,196 = 45.48% is conditional dashboard-aligned pick retention among PIT-visible, already-targeted main-draw rows**, NOT empirically validated 45.48% of every eligible official ATP and Challenger match.
- On same conditional basis ATP 62/241 = 25.73%, Challenger 482/955 = 50.47%.
- To calculate genuine full-slate coverage we need an independent fixture-source attendance ledger (all provider-eligible ATP and CH singles, whether processed or missed), its timestamped failures and qualifying inclusion policy. Retroactive provider backfills must not convert previously invisible fixtures into pre-match eligible records.
- Aligned Model Pick is the dashboard/read-model first-issued population, not proven external alert delivery and not identical to market-qualified Value Bets.

Source IDs over the 1,898 stored fixture rows: 1,898 API_TENNIS IDs and 314 ODDS PAPI IDs. Mapping presence does not establish complete two-sided contemporary bookmaker odds or access/licensing.

## 2016–2025 historical corpus provenance

The raw historical result census returned exactly **117,713 distinct canonical matches**:
- COMPLETED 113,152; RETIRED 3,939; WALKOVER 622.
- Stored original winner side 1: 64,903; side 2: 52,810. These are storage-side counts, **not model predictions**; orientation imbalance is a test target, not proof of winner-column leakage.
- With the legacy evaluator's strict result-as_of match-version join, **117,713/117,713 have no matching original fixture-start metadata at that result cutoff**. This is a reconstruction/provenance blocker; it does not mean the historical rows lack a match version entirely, nor that the outcomes were incorrect. A follow-up version-timestamp census is required to distinguish missing versions from versions stamped later.
- The legacy evaluator's build_prediction_target sets prediction_boundary to observation.visibility_at, the result visibility timestamp, rather than 30min or another frozen horizon *before the target fixture's start*. This produces results-availability-ordered replay, not verified genuine pre-match reconstruction.
- A controlled two-match negative regression test was added to PR #347. An earlier match with a late result can be evaluated after a later-played match with a faster result, and its Elo history can include that later match. This demonstrates the **mechanism of possible future-match leakage in the legacy evaluation method**, not confirmed prevalence in all historical production matches.
- The earlier "approximately 14,000 historical predictions at ~75%" claim **cannot be reproduced from these stored result counts**. No matching immutable run manifest, model version, pre-match feature timestamp snapshot, publication eligibility denominator, exact selected fixture IDs or approved commercial-use data rights have been established in this audit. Do not present 117,713 raw results as 117,713 pre-match forecasts.

## Follow-up source-timestamp explanation: actual run, PR #350

A second isolated read-only Railway execution, deployment 7bf7c96a-7e02-473c-82cc-2a7a47a5daa5, using source PR #350 at SHA e416eb561ac13fbce6a6e5cd729d909e12a74dcf, distinguished absent match-version rows from retrospective imports. Dataset digest 5750daf187f28ad8fe9ee814482926c4a7e2460ad5f347aeeaa37536f0328d83.

**Genuine source finding:**
- 117,713/117,713 historical results have one or more match-version rows stored by the November 21, 2026 report cutoff. They are not missing from today's database.
- 117,713/117,713 have EVERY version's actual ingested_at later than the historical result_as_of timestamp. Therefore NONE of the versions was already ingested in our database before its original result timestamp.
- 117,713/117,713 have zero strictly pre-match-ingested versions in the repository, across each year 2016–2025.
- Example canonical match_id 1, result_as_of 2016-01-17T23:59:59Z, match-version as_of also 2016-01-17T23:59:59Z, but actual version ingested_at 2026-08-31T08:18:46Z. This is **historical backfill**, not an immutable prediction we made in 2016.

**Important nuance:** Later local ingestion does not invalidate all retrospective backtesting. A properly documented historical replay can still use archival data that was genuinely publicly available by the pre-match cutoff. But the raw 2016–2025 database rows by themselves do NOT establish that fact, nor supply an immutable original prediction/publication record. A new validated reconstruction must recover the actual public source-availability dates, prevent result/winner-order features, hold out future information and verify code/config/labels and training chronology. Legacy result-visibility replay is not this reconstruction.

No accuracy/ROI conclusion is drawn from these version counters and no production changes are required. Source PR #350 pending CI at the time of initial data collection.

## Correct next steps

1. Source-ledger independent missed-run census and provider attendance per day, ATP/CH/qualifying, including 14–16 November no-picks and source outages. Never synthesize retroactive Model Picks.
2. Identify canonical original artifact/manifests for the claimed historical 14k/75% sample. Lock actual train, calibration, validation and untouched test windows; enforce player swap, *retrained shuffled-label* negative controls and feature-availability timestamps.
3. Read-only reconciliation of historical results' stored match_versions with min as_of, source_observed_at and ingested_at; determine whether late stamps or true missing rows explain the 117,713 nonjoinable cases. Legal permission for source data is a separate prerequisite.
4. Compare original Manager with held-out 2-agent and averaging alternatives on identical matches, at identical selection coverage and genuinely available odds. PR #348 already audits A3 abstention frequency and three descriptive shadow Manager alternatives.
5. Restore true market-only favourite comparison only if more than 20 original settled matches can be paired with point-in-time two-sided bookmaker odds. Avoid closing-price leakage.
6. Prospective frozen winner-model candidates at least 30 days/200 unique settled each; no deployment without Brian's approval.

This is **source integrity and denominator evidence**, not a claim of sustainable 72–75%, full leakage clearance or better production AI. Relevant Trello master https://trello.com/c/jvlNgP4E; production live runner remains on its previous release commit.

## Formal retirement of the "~14,000 at ~75%" claim — 25 Nov 2026

The provenance audit is now complete:
`docs/research/evidence/legacy_75pct_claim_audit_20261125.json`
(module `src/sports_edge_tennis/validation/legacy_75pct_claim_audit.py`, read-only).

Fresh read-only execution against production on 2026-11-25:

- 134,383 stored matches, 129,045 safely `COMPLETED` results.
- 117,713 pre-2026 match-version rows exist, and **0** were ingested before their own match
  start. Every pre-2026 version carries a single ingest day: **2026-08-31**.
- The largest reproducible original-forecast cohort is **3,375 settled matches at 62.52%**
  accuracy — 12.48 percentage points below 75%.
- The only stored quantity near 14,000 is `trained_history_count = 14,229` in the 2026-11-23
  label-shuffle artifact over a 623-match holdout. That is a training-corpus row count, not a
  prediction sample.
- None of the required artifacts exists: immutable run manifest, exact selected fixture-id set,
  pre-match feature-timestamp snapshot, post-start outcome join, commercial-use rights.

**Verdict: `UNREPRODUCIBLE`. Formal recommendation: `RETIRE`.**

The claim must not be cited in any commercial, marketing, investor, model-quality or
"historical accuracy" context. If referenced at all it must be labelled an unreproducible
legacy internal claim with this audit attached. Use instead the reproducible original-forecast
cohort above and the forward activation gate in
`docs/research/A1_IDENTITY_BRIDGE_FORWARD_SHADOW_20261124.md` (≥200 settled first-issued
selections over ≥30 days, paired proper-score intervals entirely below zero).
