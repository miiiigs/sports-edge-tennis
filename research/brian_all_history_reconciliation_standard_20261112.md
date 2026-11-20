# Sports Edge Tennis - ALL-DAY Model Picks reconciliation standard
Date: 2026-11-12. Status: IMPLEMENTED IN PR #294, NOT YET LIVE OR DB-VERIFIED.

## Brian's corrected P0 requirement

This is NOT a November 9-only repair and NOT a rolling-five-day-only
analysis. The operating requirement applies to ALL recorded Manila
calendar days and every first-issued Original aligned Model Pick.

Every issued selection remains traceable by canonical `match_id`,
selected winner, first-issued time, stored confidence and original
match-day bucket regardless of later postponement or schedule change.

For every historical calendar day, including dates with zero picks,
the following invariant MUST always hold:

    selections = WON + LOST + VOID + PENDING

Voids may NEVER be omitted from totals. Today's rolling comparison is
just one secondary view; it cannot replace the original day's cohort,
the lifetime total, or a pending-results audit from past weeks.

## Exact settlement rules

- WON or LOST: only a canonical, persisted, source-backed COMPLETED
  match_result_observation with an identified winning side.
- VOID: only a canonical persisted result observation whose outcome
  status is RETIRED, WALKOVER or CANCELLED, using the published
  provider-neutral settlement policy. This does not infer a
  bookmaker-specific result.
- PENDING: no valid canonical terminal observation. It stays included
  in the original daily total even if the latest fixture appears
  cancelled, the schedule is stale, or the event is very old.
- INVESTIGATION / OVERDUE / POSTPONED: operational substatus of
  PENDING with reason, age, source timestamp and next action. This
  status is NOT treated as a loss or removed from the ledger.

**DO NOT automatically void a fixture simply because its start date
passed, it is older than a provider query window, or the provider
stopped reporting it.** An authoritative cancelled/retired/walkover
result is needed. If no reliable source ever arrives, preserve
PENDING with a permanent manual exception record, rather than
inventing an outcome.

## New implementation in draft PR #294

- `src/sports_edge_tennis/dashboard/lifetime_reconciliation.py`
  is the all-days canonical read model. It has **NO 60- or 90-day
  history cutoff** and reads every original first-issued aligned
  selection, including its earliest pre-issuance fixture identity
  and time, all result-source observations known by the query
  snapshot, latest stored provider fixture state and last observation.
- The module returns daily and lifetime results, including:
  total selections, W/L/V/P, pooled graded accuracy, research >=70%
  subset, covered selection volume, original-day buckets, zero-pick
  dates, all match IDs, all unresolved cases, pending reason and
  action, long-standing (>30d) alert and PIT/source-timestamp caveats.
- Schedules stale more than 24h are distinguished from fresh future
  starts. Provider-unverified future starts cannot be presented
  as verified unplayed matches. Overdue >24h and >72h cases and
  mapping/terminal-result gaps require targeted follow-up.
- `compare_all_history_with_existing_daily` checks EVERY Oct27+
  legacy date and cohort against the SAME transaction's lifetime
  rows. If any day differs, the result becomes
  `BLOCKED_LEGACY_DAILY_MISMATCH` with exact day/metric differences.
  The system must not quietly present mismatched figures as verified.
- The protected `/api/dashboard/all-history-reconciliation` route
  emits all records and returns **409** on any historical-day mismatch,
  or **503** if the DB audit cannot run. It is registered in the
  production dashboard application with existing access controls.
- The Model Pick Results page now has an all-history lifetime
  scorecard and collapsible **every-day** table. All old pending
  selections, even those outside the normal recovery window, appear
  in its pending breakdown with player names, original/latest
  schedule, last provider observation and action needed.
- `ops/brian_all_history_audit.py` emits a private immutable
  JSON+SHA256 report from a single READ ONLY REPEATABLE READ
  PostgreSQL snapshot, covering every original match ID and day.
- `tests/test_lifetime_reconciliation.py` includes synthetic
  source-backed voids, unmatched cancelled/walkover fixtures which
  MUST remain pending, >90-day old cases, stale future starts,
  zero-pick dates, duplicate/missing match protection, post-as-of
  observation rejection, original-day preservation after schedule
  changes, and daily mismatch detection.

## Scope and sources

The all-history Original cohort is the existing canonical aligned
Model Picks selection set. Manager-only and Type A decisions are
separate populations; do not silently add or double-count those
under Original. The independent Manager comparison and Type A
settlement ledger remain separate audit tasks.

The provider does not determine which historical day a pick was
originally issued into after the fact. First-issue visible schedule
is used, otherwise the issued-at date is recorded with
`PIT_START_OR_ISSUE_UNVERIFIED`. New match start/status is reported
separately. The >=70% filter is retrospective research and must not
be mislabeled as actually live selected historical picks.

## Before calling this FIXED

1. Execute `ops/brian_all_history_audit.py` with authorized
   READ ONLY Railway PostgreSQL access; record row count,
   checksum, mismatches and full unresolved per-ID export.
2. Reobserve overdue/old cases against authoritative provider
   fixtures, reconcile canonical match mappings. Any missing
   source remains PENDING + REQUIRES_REVIEW, not VOID.
3. Pass unit, SQLite-free PostgreSQL integration, API security,
   Jinja render and existing dashboard parity tests at exact SHA.
4. Only then merge and deploy a FRESH Railway dashboard build.
   Record new Railway deployment ID/commit SHA and authenticated
   page/JSON/DB equality. Never redeploy the old dashboard image
   as proof of fixing reporting.
5. Set a sustainable scheduled retry/backoff/escalation for old
   results once provider quota and mapping tests pass; no unsafe
   automatic winnerless-walkover grade.

**Actual at authorship:** CI jobs were failing before test steps,
production source-build remained stalled on the Nov7 dashboard SHA,
and linked Railway access did not provide a SQL command or database
secret. No full DB export, passed CI, live endpoint test or
production deployment is claimed. The latest live runner is still
collecting recent source observations; that is distinct from full
historical settlement.
