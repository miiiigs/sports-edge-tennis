# Brian pending-result verification: November 10, 2026, 22:05 PHT

**Source:** production Railway `tennis-live-runner` logs captured
2026-11-10T14:05:34Z. This is an **aggregate/6-actionable-log audit**,
not yet a full per-match export of all 45 cases. The original live model
and settlement truth have not been edited.

## Total 45 awaiting outcome

| Exclusive reason category in existing live diagnostic | Count | Current reported age bucket |
|---|---:|---|
| NOT_STARTED (latest scheduled time still future) | 37 | NOT_STARTED (not aged by completed-match delay) |
| AWAITING_RESULT, start recent | 2 | <6 hours since latest start |
| NO_API_TENNIS_LINK | 1 | >72 hours |
| PROVIDER_FIXTURE_NOT_REOBSERVED | 3 | 1 >72 h, 1 24-72 h, 1 6-24 h |
| TERMINAL_FIXTURE_WITHOUT_RESULT (both WALKOVER) | 2 | 1 >72 h, 1 24-72 h |
| **Total** | **45** | 37 upcoming; 2 <6 h; 1 6-24 h; 2 24-72 h; 3 >72 h |

There are **16 RESCHEDULED flags**, potentially overlapping any reason
class, not 16 additional fixtures. No standalone
`FIXTURE_POSTPONED` reason was in this snapshot, but this does NOT
prove there are no cancellations or stale provider statuses. A full
case-by-case read-only audit of the 37 purported NOT_STARTED fixtures
is still required. In current source code (before research fix),
a stale future start can override an already-terminal provider status.
Research correction and tests are in draft PR #291; not live/accepted.

## Six cases requiring active verification

The age is approximate **elapsed time from the latest scheduled match start**
at 22:05 PHT, **not** the lifetime of the open prediction or settlement job.

| Match ID | Observed status and issue | Latest start (UTC) | Approx. age | Next verification |
|---|---|---|---|---|
| 130903 | SCHEDULED, no canonical API-Tennis event link | Sep 26 21:05 | 44d 17h | Verify exact provider match identity and terminal result |
| 131712 | SCHEDULED, provider fixture last seen Oct 05 10:01Z | Oct 05 11:00 | 36d 3h | Search alternate authoritative history and reconcile event |
| 133119 | WALKOVER, no winner result | Nov 02 04:00 | 8d 10h | Obtain authoritative walkover details; never assume winner |
| 133361 | SCHEDULED, provider fixture not re-observed | Nov 08 06:15 | 2d 7h | Recheck live event and authoritative terminal status |
| 133505 | WALKOVER, no winner result | Nov 08 20:50 | 1d 17h | Obtain authoritative walkover details |
| 133347 | SCHEDULED, RESCHEDULED, not re-observed | Nov 10 01:10 | 12h 55m | Verify rescheduled start and whether match actually played |

The latest refresh completed a same-event identity check for **five**
terminal results and persisted five results with **zero provider
errors and zero unsafe matches**; earlier hourly refresh persisted two
more, reducing the aggregate from 52 to 45. This is evidence that the
collector works on certain matches, **not** proof the remaining cases
have correct final classifications.

## Required complete audit before claiming accurate results

1. On an authorized machine with read-only production database access,
   run `python -m sports_edge_tennis.live.pending_result_diagnostics
   --lookback-days 90 --limit 2000 --json-out /secure/pending_20261110.json`.
   This creates a private owner-only JSON file with every match ID,
   timestamps, provider status, flags and history. **Do not commit the
   full JSON or expose database credentials.**
2. Partition all rows by latest provider status
   (SCHEDULED/POSTPONED/CANCELLED/COMPLETED/RETIRED/WALKOVER),
   not just by timestamp. For each match, display original pick date,
   latest scheduled start, hours since latest observed, result
   observation/ingestion status, and provider IDs.
3. Query authoritative provider for the six actionable records,
   including two winnerless walkovers. Reconcile result only with
   verified identity and authoritative winner/void information.
4. Run old-then-new aggregate snapshots with exact same cutoff and
   monitor original/qualified/Manager W/L/V/P reconciliation; neither
   pending nor VOID may count as a loss.
5. Produce Brian's signed-off **complete** pending ledger including
   individual age, reason and ownership. Until then, report 37 as
   'latest scheduled future start; status not yet individually audited'.

## Old and new pending statements

At 19:03 PHT runner snapshot **52**: 44 `WAIT_NOT_STARTED_OR_POSTPONED`,
2 `WAIT_FOR_AUTHORITATIVE_TERMINAL`, 6 actionable. At 22:05 PHT
snapshot **45**: 37 `NOT_STARTED`, 2 `AWAITING_RESULT`,
6 actionable. The seven reduction is consistent with the two
plus five terminal results captured in intervening refresh cycles.

**No 73% claim** follows from reducing pending cases. Original win-rate
and Guide Odds/A4 accuracy must be evaluated only after the independent
settlement and policy provenance checks.
