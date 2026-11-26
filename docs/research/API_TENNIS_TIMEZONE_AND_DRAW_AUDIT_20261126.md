# API-Tennis timezone, draw and quota audit — 26 November 2026

**Branch / PR:** `research/official-census-daily-waterfall-20261125` (PR #391)
**Mode:** code audit and deterministic local tests. **Zero provider calls, zero
spend, zero database writes.** The `get_draw` entitlement is **not** verified by
this work.
**Tests:** `tests/test_api_tennis_timezone.py`

## 1. Documented provider behaviour

The public API-Tennis documentation (version 2.9.5) states:

- `get_fixtures` accepts an optional `timezone` in tz format and its **default
  is `Europe/Berlin`**;
- `get_draw` accepts the same `timezone` parameter with the same
  `Europe/Berlin` default, plus `include_qualification=1` to include
  qualification/wild-card brackets;
- `event_date` / `event_time` are returned as date and clock time, not UTC
  offsets.

A parser that assumes Berlin time is UTC would shift every kickoff by +01:00 in
winter and +02:00 in summer. A parser that assumes a fixed +01:00 would also be
wrong for seven months of the year. This is why the request timezone, not only
the parser, must be pinned.

## 2. Request audit

| Path | Request timezone | Result |
|---|---|---|
| `live/transport.fetch_api_tennis_fixtures` (live batch) | explicit `UTC` | correct; now a named constant and validated |
| `live/history_refresh.fetch_api_tennis_fixture_window` | explicit `UTC` | correct; now shares the same validated constant |
| `live/transport.fetch_api_tennis_draw` | previously **absent** (provider default Berlin) | **fixed**: now explicit `UTC` |
| `get_draw` qualification flag | `include_qualification=1` | already present; preserved |
| Parser `_match_start_at` | rejects any timezone offset; combines naive date/time as UTC | correct only because every caller now requests UTC |

A non-UTC `source_timezone` is now rejected **before** any HTTP request, so a
future caller cannot silently request Berlin data and feed it to the UTC parser.

## 3. Storage and DST verification

- `match_versions.match_start_at` is `TIMESTAMPTZ`; the adapter returns an
  aware UTC `datetime`. `tests/test_api_tennis_timezone.py` asserts the tzinfo
  and zero UTC offset.
- Europe/Berlin spring-forward (2026-03-29): 01:30 CET = 00:30Z, 03:30 CEST =
  01:30Z. The 02:30 wall time does not exist.
- Europe/Berlin fall-back (2026-10-25): 02:30 occurs twice; `fold=0` = 00:30Z
  (CEST), `fold=1` = 01:30Z (CET).
- Australian Open local final (2027-01-31 19:30 AEDT) = **08:30Z**. The January
  tournament is in AEDT (+11), not AEST (+10).
- These are regression tests of the conversion semantics, not provider
  observations.

## 4. Rescheduling, original schedule and first-issue cutoff

- `fixture_lifecycle.build_fixture_timeline` retains the **first observed**
  scheduled start separately from the latest start, records every observed
  start/status change and marks retrospective discovery. A reschedule is never
  converted into a cancellation or a new identity.
- `select_prematch_targets` targets a fixture only when it is `SCHEDULED`, has a
  start and `start - now >= 30 minutes`. The boundary is tested: exactly 30
  minutes is eligible; 29m59s is excluded; a started fixture is excluded.
- The watchdog's overdue query uses the same `start - 30 minutes` deadline and
  requires a completed, non-experimental agent prediction whose
  `prediction_locked_at` is at or before that deadline. A late or backdated run
  does not satisfy it.
- Missed fixtures remain missed: the watchdog reports them and never writes a
  target, a prediction, or a backdated observation.

## 5. `get_draw` and `include_qualification` entitlement

The request path exists and is bounded (one request, one season, one
tournament, 10-second timeout). **No live call was made in this work.** The
public documentation shows `include_qualification=1`, but documentation is not
proof that our current subscription tier returns qualification brackets. The
following remain unverified until an owner-authorised, single, ledgered probe:

- whether `include_qualification=1` is honoured on the current account;
- whether the AO tournament key resolves to the same identity as the fixture
  feed;
- whether the draw feed is `draw_feed` or `reconstructed_from_results` for the
  2027 edition before the draw is published;
- whether the response includes all rounds or only played rounds.

## 6. Can provider data reconcile the draw population?

A single `get_draw` snapshot can reconcile:

| Population | Single snapshot evidence | Limit |
|---|---|---|
| Seeds | numeric `seed` on a player | seeded player may still withdraw |
| Qualifiers | `seed = "Q"` | no proof of which qualifying match produced the player |
| Wild cards | `seed = "WC"` | no reason code |
| Lucky losers | `seed = "LL"` if provider uses that token | not guaranteed by the documented sample |
| Byes | `status = "BYE"` and one empty player slot | does not prove a later replacement |
| Undecided future rounds | `status = "TBA"` / empty slots | fills in over time |
| Bracket structure | `draw_key`, `match_number`, `next_slot_key` | corrected draws need a second snapshot |
| Winner progression | `winner_player_key` | not a settlement authority |

Offline summariser: `live/api_tennis_draw.summarize_draw`. It classifies numeric
seeds, `Q`, `WC`, `LL`, byes and TBD slots and records `source`. It deliberately
sets `REPLACEMENT_DETECTION = "REQUIRES_DRAW_DIFF"`: late replacements,
withdrawals, lucky-loser promotions and official draw corrections cannot be
proven from one snapshot. They require two observations with timestamps and an
explicit diff, which this branch does not perform.

## 7. Commercial quota coordination

- This work made **zero** provider calls. It does not consume API-Tennis quota
  and creates no duplicate request stream.
- The existing live runner and history refresh remain the only callers of
  `get_fixtures`; no new scheduled caller was added.
- If the owner authorises a 2027 AO draw probe, it should be one request issued
  by the commercial data worker (or under its explicit budget/ledger), not by a
  second independent script. The probe should record request time, tournament
  key, season, `include_qualification`, response source and response hash in the
  existing provider ledger before any parsing or storage.
- No request should be retried automatically; a failed probe is evidence, not a
  reason to fan out.

## 8. Remaining blockers

1. Owner-authorised entitlement check for `get_draw` + `include_qualification`.
2. One-time AO 2027 draw capture, coordinated with the commercial worker.
3. A two-snapshot draw diff before claiming lucky-loser or replacement
   reconciliation.
4. Official ATP/Challenger fixture denominator remains unavailable; this does
   not change the timezone conclusion.
