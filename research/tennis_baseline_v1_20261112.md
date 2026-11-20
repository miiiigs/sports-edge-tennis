# Sports Edge Tennis: baseline v1.0 (P0, release-gated)
Date: 2026-11-12 (Asia/Manila)
Status: **IMPLEMENTED IN DRAFT, NOT VERIFIED OR DEPLOYED**

## Source of truth

All Model Picks and results are identified by canonical `match_id`
and immutable first-issued selected side and timestamp. Every official
result must be traceable to `match_result_observations` with source
observed/ingested times; the latest provider fixture status alone never
creates a winner. Results are divided into:

- **WON:** authoritative COMPLETED winner matches first-issued side
- **LOST:** authoritative COMPLETED winner differs from first-issued side
- **VOID:** documented cancellation/retirement/walkover per existing
  settlement policy; never silently omitted
- **PENDING:** no acceptable canonical terminal observation yet
- **QUARANTINED:** contradictory source identity/timestamp or ambiguous
  winner; not counted as WON/LOST and must generate an alert

Each historical pick must have first-issue timestamp < latest **PIT**
scheduled kickoff. Any unproven kickoff, player identity, historical
ranking, serve/return stat or bookmaker quote is flagged UNVERIFIED,
not backfilled from today's records.

## Three always-visible scorecards

1. **Fixed 2026-11-05..2026-11-09:** remain in published view indefinitely;
   new settlements update WITH a new as-of stamp, never replace this
   fixed cohort with a moving five-day window.
2. **Latest five completed Manila calendar dates:** chronological
   moving cohort, including zero-pick days; label PROVISIONAL until all
   results are resolved.
3. **Cumulative since 2026-10-27 through yesterday:** immutable
   start date; **not a selection-date-shifted retrospective 'new V3'**.

All use identical first-issued Original and retained >=70% research
subset. Manager-only has its own separately defined first-issued
cohort and is NOT silently treated as a subset of Original.

For every arm+day: picks, W/L/VOID/PENDING and
`WON/(WON+LOST)` pooled accuracy, issuance
coverage=`candidate picks / eligible Original picks`, policy/model
version, time queried in UTC and Manila, last provider observation,
PIT validity, validation state and pending best/worst ranges where
useful. No fabricated A3/A4 scored rows.

**Hard invariant:** picks = W+L+VOID+PENDING. Every pick ID is unique
inside a reporting arm and date. Any failure displays a BLOCKED/error
state; it may not silently substitute stale cached totals.

## Pending classification and recurrence prevention

- Scheduled future start is `STORED_NOT_PROVIDER_VERIFIED` until a
  fresh provider check confirms it; never claim future=unplayed.
- `NOT_STARTED` / `AWAITING_RESULT`: ordinary operational states
- `POSTPONED` / `RESCHEDULED`: preserve initial and latest starts
- `TERMINAL_FIXTURE_WITHOUT_RESULT`: winnerless walkover/retirement/
  cancellation - require independent official evidence
- `NO_API_TENNIS_LINK`: event identity mapping issue
- `PROVIDER_FIXTURE_NOT_REOBSERVED`: stale schedule or missing source
- `RESULT_DELAYED`: result feed/ingestion retry required

The regular short-window provider refresh is 3 UTC days based on the
**latest persisted fixture start date**, NOT first prediction date.
Match can fall out of provider query on day 4 if still unresolved.
The initial repair preserves that writer and adds persistent
*read-only* overdue audit, identity-safe historical PROBE scheduler
(up to 1 request/day, 10 targets, 90-day lookback), and manual review
alerts. Probe can be enabled only after passing quota/identity tests;
no automatic result write to old fixtures until authoritative
provider payload and rescheduled fixture tests pass.

Record correction evidence: old/new fixture/result status, selected
side, canonical match, provider IDs, source timestamps, observed
by/admin and reason; never delete first-issued picks.

## Acceptance gates (all required to mark V1 BASELINE VERIFIED)

| Gate | Required evidence |
|---|---|
| Identity | exact Nov9 28 retained and all Original 61 IDs, first-issued side and PIT timestamp; all status transitions including void |
| Result reconciliation | opening P + new P - left P = closing P by match ID, with source time, for 52->43 and current cohorts |
| External verification | every pending match player/start/latest provider state/last refreshed/age/action; provider-unverified labels truthful |
| Comparability | same database as-of for fixed, rolling and cumulative; W/L/V/P and denominator/coverage validated |
| Model provenance | >=70% cutoff chosen pre-Oct27 as training research, merged Oct31, later shadow replay NOT represented as live issued filter |
| Manager | same-fixture kept/rejected/switched and Manager-only results, no unpaired causal claim |
| Timing | train cutoff <= first issuance; input feature provenance per model, no post-issuance rankings/stats/odds leak |
| CI | automated focused+Postgres tests EXECUTED and passed with commit SHA, not merely written |
| Release | new Railway deployed SHA, health 200, protected page/API match audited DB and no stale-data fallback |
| Forward | A3/A4 only after foundation, pre-reg >=30 days + >=200 graded per arm + >=40% eligible Original coverage; new version on rule changes |

## Blocker status at authorship

- Connected Railway read tools expose variable names only, no
  credentials or raw SQL execution. A trusted DB operator must run
  private read-only commands; no password in chats or tickets.
- GitHub Actions jobs continue failing before test steps (run
  43000408986 attempt 2). Project/account billing eligibility
  requires owner investigation. This is NOT a test pass.
- Railway accepted a dashboard source refresh 2026-11-12, but latest
  observed live image still SHA d8d43c46 (Nov7). Do not call new
  dashboard code live until new deployment exists.
- OddsPapi latest provider behavior is HTTP429 RATE_LIMIT (Nov12
  12:06 PHT), after earlier HTTP403 SUBSCRIPTION_RESTRICTED.
  Must inspect quota/plan/retry behavior, not assume renewal alone fixes.
- Shared-workspace Railway failed invoice USD 29.32 was observed in
  earlier account billing email; not established as 100% Tennis
  allocation or confirmed root cause of zero new deployments.
