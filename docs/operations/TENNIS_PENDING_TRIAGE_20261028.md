# Read-only pending-result action routing

Operator states from pre-existing `load_pending_result_diagnostics`, no new paid API requests and no DB writes. Each pending Model Pick is assigned exactly one operational next step. Avoid conflating legitimate future matches with completion/mapping failures.

- NOT_STARTED or POSTPONED: wait for actual match play and provider terminal update.
- AWAITING_RESULT or RESULT_DELAYED: await authoritative terminal observation; do not infer winner.
- TERMINAL_FIXTURE_WITHOUT_RESULT, fixture status WALKOVER: requires existing PR #260 plus migration 0021 backup and restore verification. Never infer winner; VOID only after canonical terminal.
- TERMINAL_FIXTURE_WITHOUT_RESULT, status COMPLETED: source-winner evidence/manual reconciliation required; no invented result.
- NO_API_TENNIS_LINK: guarded canonical sporting-match identity mapping verification.
- PROVIDER_FIXTURE_NOT_REOBSERVED: if outside bounded 3-day refresh, historical verified source event recovery; if recent, targeted provider event check.

Live log `PENDING_RECOVERY_TRIAGE` and `PENDING_RECOVERY_ACTION` names match IDs but never raw player names, provider credentials, wagers or inferred results. Fail-isolated after the existing diagnostics query; original 73% denominator and results unchanged.

The October28 03:05Z case set includes 17 actionable old records: four walkovers, three other terminal fixtures lacking result, nine not-reobserved and one missing API link. The separate Type A settlement population has 22 pending decisions, not the same as the actionable count. The 32 NOT_STARTED and 1 AWAITING_RESULT in the most recent monitor are not counted as system defects.

**No producer release of migration 0021 without a verified restore-tested backup.** The fact that a Railway Postgres volume exists is not evidence of recoverability.
