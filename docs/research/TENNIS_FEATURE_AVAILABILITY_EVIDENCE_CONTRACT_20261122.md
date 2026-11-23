# Brazzy365 research evidence and historical feature-availability contract
November 22, 2026. Version \`TENNIS_FEATURE_AVAILABILITY_EVIDENCE_V1\`.

## Important: this is the *contract*, not a completed provenance audit

The source module \`src/sports_edge_tennis/validation/evidence_feature_availability.py\` defines strict machine-checkable input and labels, with regression tests. Creating this contract does **not** assert existing models already satisfy every feature's publication, ingestion and timestamp provenance. Unassessed old results remain RED/unverified.

**GREEN: original, actually issued before the match.** Preserve original immutable prediction row, source ID, model/commit/dataset, cutoff, published issue time, feature publication reference, original provider observation and database ingestion timestamps *no later than cutoff*, and start minus 30-minute safety fence. A new reconstructed prediction can never be GREEN even if it uses only valid historical results.

**AMBER: time-safe reconstructed historical backtest.** A match played in 2018 is legitimate history for a hypothetical 2020 forecast even if our new database imported the archived results in 2026. It needs independent contemporaneous or licensed archival evidence that underlying results were knowable before the hypothetical cutoff, plus chronological training/validation/calibration splits, feature publication fences, a configuration frozen **before running the retrospective evaluation**, source/dataset/commit IDs, and explicit BACKTEST labelling. Do not falsely interpret 2026 ingestion as evidence the underlying 2018 result was unavailable in 2020.

**RED: unverified historical claim or demonstrably unsafe.** Unreconstructed 14k/~75% legacy result; historic data with unknown original publication date; future ranking or target-match postmatch stats; winner-first labels exposed as predictors; post-issue odds; repeated tuning on final test; missing model version or original issue record; A4 or Manager trained using results not available at relevant cutoff. RED can become AMBER if reproducible retrospective simulation and full feature timeline are established, never automatically GREEN.

## Feature availability inventory (requirements, not verified coverage)

| Feature family | Event timestamp | Earliest knowable time evidence | Original database clocks required | Replay restrictions |
|---|---|---|---|---|
| Match result / Elo input | Match completion | Official terminal result publication / contemporaneous licensed archive | Source observed and ingested before original prediction | Historical result must precede simulated match; opponent order cannot reveal target |
| Player ranking / rating | Ranking list period | Exact first publication of weekly ranking, not subsequent revision | Observed & ingested time | Historical ranking snapshots; no future latest-ranking join |
| Serve/return statistics | Prior match completed | Historical stat publication or dated licensed source | Observed & ingested time | Opponent-adjusted estimates from known prior matches only, never target match |
| Common-opponent graph | Historical opponent matches | All edge results independently known | Graph input source and ingestion cutoff | Never future or retrospectively added opponents with unproven availability |
| Tournament surface | Official tournament designation | Dated announcement or licensed historical metadata | Original provider observation + ingestion | Correct unknown vs documented surface; no silent guesses |
| Bookmaker probability | Quote's actual market observation | Original bookmaker/source timestamp, paired both sides from same bookmaker | Observed, source as-of, ingested before prediction issue | Retrospective closing odds must not become original bookmaker input |
| Verified contextual research | Interview, announcement, official withdrawal | Exact original publication or archived timestamp | Collected and ingested before prediction cutoff | Deterministic player identity; avoid inferred injury or future announcements |

Time fields must be explicit UTC-aware instants. Store source ID, feature version, full citation/reference, event time, earliest knowable at source, provider observed time, ingested time and predicted match cutoff. If clock unknown, mark it unknown; never use the match date as a fabricated observation timestamp.

## Immediate application checklist

1. Audit A1, A2, A3, Manager and A4 feature ingestion and temporal joins against the contract. Inventory every feature, who supplied it, independent historical publication proof, event vs observed/ingested time, and known missingness. Set FAIL CLOSED on any proof gap before reporting validated backtest accuracy.
2. Independently rerun label-shuffle **training**, player-order reversal, future-ranking/postmatch-stat and calibration/Manager/A4 test/validation leakage checks, not merely a shuffled-evaluation-label sanity check.
3. Reconstruct original 14k legacy experiment artifacts or keep RED. Preserve immutable GREEN first-issued production prediction rows in separate cohorts from AMBER reconstructed research.
4. Frozen research candidates: previous 74.44% of 180 settled from a 194 selected / 519 co-issued cohort; older 72.61% from 230 settled; A/B/C Manager risk-coverage. All are historical exploratory results, not sustainable 72-75% forward results.
5. Keep the first-run target count and provider-discovered fixture count separate from independently verified *official* eligible singles fixtures. Without authorised independent source and an actual matched census, full-slate coverage remains unknown.
6. No data or production policy purchasing/promotion. Failure under a P0 clock gate is a research blocker, not a reason to backdate observations.

## Reproducibility

- Source: \`src/sports_edge_tennis/validation/evidence_feature_availability.py\`
- Tests: \`tests/test_evidence_feature_availability.py\`
- No migrated schema, training data, market policy or production prediction records are changed by this PR.
- Experiments must retain Git SHA, cohort hash, training/calibration/test times, frozen parameters, all inspected variants, original issue cutoff, result known-at time, unmatched eligibility and attribution of exclusions. The scientific release gate remains **independent future match testing**.
