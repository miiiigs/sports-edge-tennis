# Tennis pending-result recovery — operator protocol (2026-10-23)

This patch permits provider-confirmed WALKOVER with no winner to be persisted as an append-only result graded VOID. It does not infer outcomes from the age of a pending pick.

## Deployment gates

1. Obtain a restorable production PostgreSQL backup, Railway security and credential clearance, and explicit production-migration approval. Migration 0021 only relaxes WALKOVER winner constraint; its DOWN refuses to reverse while legitimate null-winner results exist.
2. Apply and verify migration 0021 **before** any live runner starts using this code. The patch does not automatically migrate production. Do not ship the runner unless schema readiness is verified.
3. Validate API-Tennis event IDs, original and latest timestamps, canonical match identity and terminal fixture evidence. Oct22 logs identified research-only match_id 132533 (event 12172898) and 132535 (12172976) as WALKOVER without persisted result; never infer a completed or losing outcome merely from the status.
4. Use authenticated /api/dashboard/pending-results?lookback_days=7 and /api/dashboard/model-pick-results?date=2026-10-22, with the selected UTC or Asia/Manila calendar zone recorded, to classify **all five** reported pending picks.
5. Existing CLI dry-run for one target UTC day:
   `python -m sports_edge_tennis.live.pending_result_refresh --as-of 2026-10-22T23:59:59+00:00 --window-days 1 --dry-run`.
   The CLI uses a production lock and a bounded provider request. Inspect terminal_found, provider_errors, unsafe, unresolved and canonical IDs.
6. Repeat that *same bounded window without* --dry-run only with verified backup, approved migration, authenticated source evidence and operator approval; validate append-only result count, settlement idempotency, and before/after W/L/VOID/PENDING. Never run broad unbounded paid provider scans.
7. If the provider outcome remains uncertain or identity ambiguous, leave the pick PENDING and escalate for authoritative review. No fabricated winners, changed bets, or retrospective model policies.
8. Preserve old runner deployment for rollback; do not roll back the schema under valid winnerless walkovers. Historical repair scripts from draft PRs 245 and 246 remain independently approval-gated.

This file is a runbook; it does not attest that five pending results have been settled.
