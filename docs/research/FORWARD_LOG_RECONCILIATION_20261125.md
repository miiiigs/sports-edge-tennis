# Forward evidence reconciliation — November 25 2026

## Status
Implemented in draft. Bounded actual Railway log export preserved; verification via CI pending. No database writes, paid calls, deployment or model changes.

## Evidence
Retrieved existing Railway deployment logs for 2026-11-25 00:00–04:00 UTC. Four successful batch markers, four heartbeats, six summaries and six distinct candidate match IDs. Three original-Manager selections, two each for guarded B, mean C and equal-logit blend. Not settled Model Picks or official eligible coverage. Capture timestamps are original service-log timestamps; retrieval and repository commit occur later. No claim of independent contemporaneous receipt or object-lock storage.

## Execute
`python -m sports_edge_tennis.validation.forward_log_reconciliation docs/research/forward_captures/railway_forward_20261125_0000_0400Z.json`

Tests replay the real preserved source and reject omitted records, altered content, missing summaries, capped results and late receipts. Bounded exports must cover complete batch cycles; missing entire cycles cannot be inferred from log counts alone. Reconcile scheduled job executions and official fixture/first-issued ledger independently.

## Definition of done for central archive
This is the first bounded real-log reconciliation increment, not completion. Next: external independently timestamped append-only collector at issue time, original immutable match-version/agent configuration and run-linked feature evidence, monitored expected capture vs archive receipts, separate settlement events and daily matched policy scorecards. Storage/settlement/official denominator remain unverified. Never backdate or treat log replay as a new forecast. No candidate activation before owner approval and prospective acceptance gates.

## Full-day follow-up (November 24 UTC)
Preserved the entire 00:00–24:00 UTC log export: 24 successful batch markers and 24 heartbeats, 50 summaries/attempts (50 successful, 0 failed), 51 distinct candidate IDs (50 Challenger, 1 ATP_MAIN). Original-as-issued research selections 5, selective original 4, guarded B 10, C 7, equal-logit 12. Earliest receipt-to-start margin approximately 88 minutes. Structural checks independently found no chronology, duplicate, summary-count or heartbeat discrepancy. Initial four-hour PR revision passed CI 45540794124; new full-day digest regression and whole-hour gap tests await CI. Observed hourly completions are not proof of an independent scheduler census or complete eligible-match coverage.

Indicative sample pace ONLY, assuming this single day's selection rate repeats and every selected match settles: original 5/day -> 100/200/500/1000 in 20/40/100/200 days; B 10/day -> 10/20/50/100; C 7/day -> 15/29/72/143 (ceil); equal-logit 12/day -> 9/17/42/84 (ceil). Not actual settled rates, not #386's new frozen candidates, not a December calendar forecast. ATP/Ch seasonality, voids, overlaps, weekends and narrower scope can materially lengthen collection. Do not weaken thresholds to reach sample targets. Minimum 30-day evaluation remains separate.
