# January 2027 isolated operational rehearsal — results

**Date:** 27 November 2026
**Branch / PR:** `workstream/ao-2027-format-readiness-20261125` (PR #389)
**Status:** 13 scenarios executed in isolation. One is explicitly blocked on a
draw diff; none is a fiction. No production activation.
**Mode:** synthetic fixtures only. No provider call, database write, purchase,
deployment or forecast claim.
**Code:** `src/sports_edge_tennis/validation/january_rehearsal.py`
**Tests:** `tests/test_january_rehearsal.py` (5)
**Evidence:** [`january_2027_rehearsal_20261127.json`](evidence/january_2027_rehearsal_20261127.json)

## 1. Scenario results

| # | Scenario | Result | What it proves |
|---|---|---|---|
| 1 | First eligible ATP fixture | PASS | Brisbane ATP 250 main draw is eligible at start − 30 min, BO3 |
| 2 | First eligible Challenger fixture | PASS | Canberra Challenger 125 main draw is eligible |
| 3 | AO best-of-five main draw | PASS | R128 is BO5, `GRAND_SLAM`, forecast-enabled |
| 4 | AO qualifying classification | PASS | Q1 is BO3 and forecast-disabled; kept separate |
| 5 | Withdrawal and replacement | PASS | original `(101,102)` and current `(103,102)` remain distinct |
| 6 | Lucky loser | **BLOCKED** | `LL` token recognised; promotion needs two authorised draw snapshots |
| 7 | Rescheduled start | PASS | original, revised and scheduled clocks retained; no backdate |
| 8 | Provider failure | PASS | failed discovery is not `NO_ELIGIBLE_FIXTURES` |
| 9 | Missing fixture | PASS | remains in the external denominator; external coverage stays unknown |
| 10 | Archive unavailable | PASS | an unverified archive stage cannot produce a successful receipt |
| 11 | Settlement correction | PASS | newer result appends a correction with a predecessor; no rewrite |
| 12 | Scheduler non-execution | PASS | one skipped hourly slot is detected from the schedule |
| 13 | No universal January penalty | PASS | normal seasonal return labelled, `forecast_adjustment = 0.0` |

`all_passed` is true because the only non-PASS is an explicit blocker, not a
silent failure.

## 2. Preserved unknowns

| Item | Status |
|---|---|
| AO 2027 edition-specific rulebook | `NOT_LOCATED` |
| 2027 entries published | `false` |
| 2027 draws published | `false` |
| AO qualifying exact first match | `UNKNOWN_PENDING_OFFICIAL_ORDER_OF_PLAY` |
| `get_draw` / `include_qualification` entitlement | `UNVERIFIED_NO_PROVIDER_CALL` |
| Lucky-loser / late replacement | requires a two-snapshot draw diff (not implemented) |

No entry, fixture, seed, lucky loser or rulebook confirmation is invented.

## 3. What the rehearsal does not show

- It is not a 2027 draw, entry list or forecast.
- It does not prove archive availability, provider entitlement or bookmaker
  grading.
- It does not execute a real settlement; it proves the correction shape is
  append-only.
- It does not claim the 2027 edition rulebook matches 2026.

## 4. Decision

January scope and edge-case handling are **ready for an owner-gated rehearsal**
against the real provider slate when the provider publishes it. The release
remains blocked on the edition-specific rulebook, published draws, the draw
entitlement and owner approval. No universal January or inactivity penalty was
introduced.
