# Pending-result diagnostics

Read-only, match-level reasons for every production pick (Manager CONSENSUS,
aligned A1/A2 wide-spread, settlement-eligible Type A BET) with no terminal
`match_result_observations` row. Same pick population as
`live/pending_result_refresh.py` (`pending_pick_matches_ctes()`).

Surfaces:

- Hourly log, after `PENDING_RESULT_REFRESH`: `PENDING_RESULT_DIAGNOSTICS total=… reasons=… ages=… flags=…`
  plus one `PENDING_RESULT_ITEM` line per actionable pick (max 25).
- `GET /api/dashboard/pending-results?lookback_days=60` (behind production Basic auth).
- CLI: `python -m sports_edge_tennis.live.pending_result_diagnostics` (read-only transaction).

It never writes, voids or forces a result.

## Reasons (one per pick, in precedence order)

| Reason | Meaning |
|---|---|
| `NOT_STARTED` | Latest fixture start is in the future. |
| `TERMINAL_FIXTURE_WITHOUT_RESULT` | Fixture feed reports a terminal status but no parseable terminal result was persisted (e.g. no winner). |
| `NO_API_TENNIS_LINK` | No API-Tennis event key is attached, so recovery cannot look it up. |
| `PROVIDER_FIXTURE_NOT_REOBSERVED` | Started ≥6h ago and no provider has re-observed the fixture for ≥6h (event dropped/replaced upstream). |
| `AWAITING_RESULT` | Started <6h ago and still observed. |
| `RESULT_DELAYED` | Started ≥6h ago, still observed, still non-terminal. |

Flags: `RESCHEDULED` (latest start differs ≥60 min from the point-in-time start the
dashboard uses to date the pick), `OUTSIDE_RECOVERY_WINDOW` (older than the 3 UTC
dates the hourly recovery covers; it will not be retried automatically),
`TYPE_A_BET`.

Age buckets are hours since latest start: `NOT_STARTED`, `LT_6H`, `6H_24H`, `24H_72H`, `GT_72H`.

## 2026-10-13 investigation (Brian: six 2026-10-12 picks pending)

Read-only production query, 2026-10-13 ~15:30Z:

- Dashboard 2026-10-12 (Asia/Manila): 28 aligned picks, 20 W / 4 L / 4 VOID / **0 pending**.
  Original Manager ledger: 13 picks, 6 W / 7 L, 0 pending.
- The six longest-pending 2026-10-12 picks were all **Jinan Challenger** fixtures first
  listed at a 02:00Z placeholder start and **rescheduled by the provider to 2026-10-13**
  (06:00–09:20Z). Results were persisted by the hourly recovery within ~1h of each match
  ending. Not an ingestion, identity or settlement defect; the gap was that nothing
  showed the reschedule.

| match_id | Match | Pick | First listed | Played (latest start) | Result persisted | Outcome |
|---|---|---|---|---|---|---|
| 131930 | D. Sweeny v B. Tomic | Tomic | 10-12 02:00Z | 10-13 06:45Z | 10-13 07:01Z | RETIRED → VOID |
| 131932 | E. Ymer v M. Bellucci | Bellucci | 10-12 02:00Z | 10-13 08:10Z | 10-13 10:05Z | WON |
| 131931 | A. Walton v M. Cilic | Cilic | 10-12 02:00Z | 10-13 08:25Z | 10-13 11:05Z | WON |
| 131923 | A. Kachmazov v J. D. Hara Friend | Hara Friend | 10-12 02:00Z | 10-13 09:20Z | 10-13 12:06Z | WON |
| 131927 | T. Machac v A. Muller | Machac | 10-12 02:00Z | 10-13 10:25Z | 10-13 12:06Z | WON |
| 131924 | M. Kecmanovic v A. Shevchenko | Kecmanovic | 10-12 02:00Z | 10-13 11:10Z | 10-13 13:09Z | LOST |

Older picks still pending (13, 2026-09-09 → 2026-10-05) are all `OUTSIDE_RECOVERY_WINDOW`:
9 `PROVIDER_FIXTURE_NOT_REOBSERVED`, 3 `TERMINAL_FIXTURE_WITHOUT_RESULT`
(131036, 131307, 131446), 1 `NO_API_TENNIS_LINK` (130903). Settlement's
`pending_results=10` is ten hourly Type A BET decisions on one match, 130717
(K. Smith v E. Winter, 2026-09-21), whose provider event was last observed before start.
These stay pending until a genuine terminal observation exists.
