# Tennis pending-result recovery: 17 actionable matches

**Snapshot:** 2026-10-29 21:03:44 UTC (2026-10-30 05:03:44 Asia/Manila), last inspected production runner before this work.  
**Source:** production `PENDING_RECOVERY_TRIAGE` and `PENDING_RESULT_ITEM` logs.  
**Environment:** Railway `production`, project `c8eda2f4-0b73-43e3-a9e4-186e9e2b4190`, runner `4bf96d81-43e4-4a25-b162-cf20d86ce63b`.  
**Manifest:** [actionable_17.csv](actionable_17.csv). Read-only evidence: **not** confirmed outcomes, not a settlement authorization.

At snapshot, **26 total pending** = **17 actionable** + **1 awaiting authoritative terminal** + **8 not-started/postponed**. Compared with 47 on Oct 28, pending declined through normal result reconciliation. Count can change at any subsequent run; do not treat this document as live data.

| Blocker | Canonical match IDs | Next action |
|---|---|---|
| `BACKUP_GATED_WINNERLESS_WALKOVER` (4) | **132211, 132533, 132535, 132664** | Confirm provider WALKOVER terminal identity and restorable production PostgreSQL backup; apply protected migration 0021 from **existing draft PR #260** before deploying its code. Settlement should be **VOID**, no inferred winner or loss; check append-only idempotency and the all-aligned historical result counts after release. **No DB mutation was authorized/performed by this manifest.** |
| `VERIFY_HISTORICAL_PROVIDER_EVENT` (9) | **117860, 130363, 130686, 130717, 131527, 131536, 131637, 131636, 131712** | Day-scoped API-Tennis read-only probe for each recorded fixture date, then compare returned **event ID** and canonical player pair/tournament/time. If unobserved, leave PENDING; don't mark VOID or LOST based solely on age. |
| `VERIFY_TERMINAL_WINNER_EVIDENCE` (3) | **131036, 131307, 131446** | Provider fixture says `COMPLETED` but persisted result lacks verified winner. Match **131036** has no API-Tennis event link, so the source-link problem must be reviewed first; do not infer selected side as winner. |
| `REVIEW_CANONICAL_API_TENNIS_ID` (1) | **130903** | Resolve missing API-Tennis link using authoritative two-player identity, tournament, point-in-time and latest start, with ambiguity fail-closed. Do not create a duplicate sporting match or attach a guessed source event. |

**Type A protected exposure:** `130717` (historical provider event) and `132211` (walkover). These are prioritized but not given artificial results.

## Read-only provider probes (bounded)

The existing CLI uses a production batch lock and **at most one API-Tennis fixture-window call per invocation**, with `--window-days 1`. These commands are **instructions for an authenticated operator**, not executed probes and not proof of provider data. Use the existing secured environment's DB connection and API-Tennis key. Run sequentially, observing quota/error responses:

```bash
python -m sports_edge_tennis.live.pending_result_refresh --as-of 2026-09-09T23:59:59+00:00 --window-days 1 --dry-run
python -m sports_edge_tennis.live.pending_result_refresh --as-of 2026-09-15T23:59:59+00:00 --window-days 1 --dry-run
python -m sports_edge_tennis.live.pending_result_refresh --as-of 2026-09-20T23:59:59+00:00 --window-days 1 --dry-run
python -m sports_edge_tennis.live.pending_result_refresh --as-of 2026-09-21T23:59:59+00:00 --window-days 1 --dry-run
python -m sports_edge_tennis.live.pending_result_refresh --as-of 2026-09-26T23:59:59+00:00 --window-days 1 --dry-run
python -m sports_edge_tennis.live.pending_result_refresh --as-of 2026-09-28T23:59:59+00:00 --window-days 1 --dry-run
python -m sports_edge_tennis.live.pending_result_refresh --as-of 2026-10-01T23:59:59+00:00 --window-days 1 --dry-run
python -m sports_edge_tennis.live.pending_result_refresh --as-of 2026-10-02T23:59:59+00:00 --window-days 1 --dry-run
python -m sports_edge_tennis.live.pending_result_refresh --as-of 2026-10-03T23:59:59+00:00 --window-days 1 --dry-run
python -m sports_edge_tennis.live.pending_result_refresh --as-of 2026-10-05T23:59:59+00:00 --window-days 1 --dry-run
```

The `--as-of` timestamp is an **upper bound on eligible scheduled match starts**, not a statement that the historic API payload was available at that time. A result can remain absent because the provider no longer exposes an old event or because the canonical identity is ambiguous. These probes may **not** see a fixture moved to a later UTC date; first use recorded `latest_start_utc_date`, and if evidence suggests later rescheduling, investigate that **specific** day without a broad scan. The `--dry-run` path must write zero result records, and should log `PENDING_RESULT_REFRESH_DRY_RUN writes=0`. Do not remove `--dry-run` merely because `terminal_found` is positive.

## Production release gate

1. Recheck latest `PENDING_RECOVERY_TRIAGE` and the exact original aligned daily ledger to avoid acting on cases that have already settled. Log the current count and as-of time.
2. For each candidate: authenticate provider result, canonical source event, identities, status and timestamp. Refuse ambiguous/conflicting outcomes.
3. For the four walkovers specifically, confirm a **restorable PostgreSQL backup**, migration version, current table constraints, deployment rollback and operations approval. **Existing PR #260** holds migration 0021, parser updates and database regression tests; it is still draft on a nonproduction base, not automatically safe to merge or ship. Production's currently deployed runner code lacks 0021.
4. Apply schema migration 0021 first, verify it, then release matching runner code and run **bounded** recovery for validated target days. The DOWN migration refuses rollback after null-winner walkovers are recorded; plan application rollback separately.
5. Recheck each `match_id` result and both `/api/dashboard/model-pick-results` and `/today?date=...&tz=philippines`. Preserve W/L/VOID/PENDING truth and 73%-target denominator, with before/after counts and source evidence.
6. If backups, provider evidence, credentials, or required access are missing, leave the cases PENDING and report the exact blocker.

**Never** invent a match winner, mark an old scheduled fixture as a loss, silently convert missing evidence into a void, or re-score original picks to optimize the 73% target. No live model/odds rules change.
