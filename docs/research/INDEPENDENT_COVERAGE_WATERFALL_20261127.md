# Independent coverage waterfall — November 2026 contract and result

**Date:** 27 November 2026
**Branch / PR:** `research/official-census-daily-waterfall-20261125` (PR #391)
**Mode:** reuse of the committed read-only coverage refresh. **No fresh database
query, no provider call, no scraping, no write.**
**Evidence:** [`independent_coverage_waterfall_20261127.json`](../research/evidence/independent_coverage_waterfall_20261127.json)
**Source:** `internal_coverage_waterfall_refresh_20261125T1740Z.json`
**Code:** `src/sports_edge_tennis/validation/independent_coverage_waterfall.py`,
`ops/validation/independent_coverage_waterfall_20261127.py`
**Tests:** `tests/test_independent_coverage_waterfall.py` (8 tests)

## 1. The denominator rule

An internal stored-fixture ratio is **not** official coverage. No licensed
complete ATP/Challenger fixture export or permitted official feed has been
supplied. Therefore:

```text
official eligible fixtures     UNKNOWN
external coverage              UNKNOWN
internal discovered fixtures   reported separately
```

Undiscovered official fixtures, unmapped official fixtures and failed or missing
forecasts are **not** removed from the external denominator. They cannot be
enumerated, which is exactly why external coverage is unknown rather than a
number.

## 2. Two tracks, never merged

A fixture and a selection are different populations. A fixture can be evaluated
and settled without ever being selected.

**Fixture track**

```text
DISCOVERED -> MAPPED -> EVALUATED
```

**Selection track**

```text
SELECTED -> INDEPENDENTLY_ARCHIVED -> PUBLISHED
         -> BOOKMAKER_QUALIFIED -> SETTLED_SELECTION
```

Settlement of a **fixture** is not a funnel stage after evaluation; all
discovered fixtures settle whether or not they were evaluated. Settlement of a
**selection** is a distinct downstream event and is reported only in the
selection track.

## 3. Result from the committed 14–26 November evidence

Window: `2026-11-14T00:00Z` to `2026-11-26T00:00Z`. Source report as-of
`2026-11-25T17:39:59Z`.

### Fixture track

| Stage | Count | Missing from previous | Coverage of previous |
|---|---:|---:|---:|
| Official independently eligible | **UNKNOWN** | unknown | **unknown** |
| Discovered (internal stored) | 677 | n/a | n/a |
| Mapped | 677 | 0 | 100.0% |
| Evaluated (Agent 1 produced a probability) | 489 | 188 | 72.2% |

The 188 fixtures between `MAPPED` and `EVALUATED` are real missing forecasts in
the internal population. They are **not** removed from the external denominator;
they are exactly the kind of loss the census must surface.

### Fixture track by tour

| Tour | Discovered | Mapped | Evaluated |
|---|---:|---:|---:|
| ATP main | 20 | 20 | 15 |
| Challenger | 657 | 657 | 474 |

### Fixture track by draw stage

| Draw | Discovered | Mapped | Evaluated |
|---|---:|---:|---:|
| Main draw | 427 | 427 | 366 |
| Qualifying | 250 | 250 | 123 |

Qualifying is reported separately and is not included in any main-draw coverage
claim.

### Selection track

| Stage | Count | Status |
|---|---:|---|
| Selected (Manager consensus) | 80 | counted |
| Independently archived | **UNKNOWN** | no per-selection archive ledger in the committed evidence |
| Published | **UNKNOWN** | no per-selection publication ledger in the committed evidence |
| Bookmaker qualified | **UNKNOWN** | no per-selection bookmaker ledger in the committed evidence |
| Settled selection | **UNKNOWN** | no per-selection settlement ledger in the committed evidence |

The earlier report records **161 aligned Model Picks** in the window, but the
committed aggregate rows do not carry a per-selection archive/publication/
bookmaker/settlement chain. Reporting `161` as any of those stages would be an
inference, so those stages are `UNKNOWN`.

### Fixture settlement (not a selection stage)

| Item | Count | Status |
|---|---:|---|
| Settled completed fixtures | 582 | internal completed-result evidence |
| Official settlement | 582 | internal only, not an independent official feed |
| Bookmaker settlement | **UNKNOWN** | no bookmaker grading ledger in the committed evidence |

## 4. Daily table

The evidence JSON contains every daily row with its fixture-track stages. The
key rows:

| UTC day | Tour | Draw | Stored | PIT visible | Evaluated | Selected | Settled |
|---|---|---|---:|---:|---:|---:|---:|
| 2026-11-14 | ATP | Main | 1 | 0 | 0 | 0 | 1 |
| 2026-11-14 | Challenger | Main | 15 | 0 | 0 | 0 | 13 |
| 2026-11-14 | Challenger | Qualifying | 3 | 0 | 0 | 0 | 3 |
| 2026-11-15 | Challenger | Main | 7 | 0 | 0 | 0 | 6 |
| 2026-11-15 | Challenger | Qualifying | 81 | 0 | 0 | 0 | 72 |
| 2026-11-16 | Challenger | Main | 26 | 0 | 0 | 0 | 25 |
| 2026-11-16 | Challenger | Qualifying | 43 | 0 | 0 | 0 | 42 |
| 2026-11-17 | Challenger | Main | 75 | 73 | 69 | 13 | 68 |
| 2026-11-24 | Challenger | Main | 74 | 73 | 73 | 11 | 67 |
| 2026-11-25 | Challenger | Main | 56 | 55 | 56 | 10 | 12 |

The 14–16 November block remains the discovery gap: fixtures exist in the
database but no version was PIT-visible before the 30-minute deadline.

## 5. Missing at every stage

For each stage the evidence records:

- `count`;
- `missing_from_previous`;
- `missing_identified` (fixture IDs when a per-fixture export exists);
- `missing_unidentified_count` (when only aggregates exist);
- `unknown_reason` for an unavailable stage;
- `coverage_of_previous`;
- `coverage_of_official` (only when a complete official total exists).

A `None` stage breaks the chain. Downstream counts become `None`; they are never
rendered as zero.

## 6. Contract enforced by tests

- unknown is not zero;
- undiscovered fixtures are not removed from the denominator;
- failed forecasts are not removed from the denominator;
- fixtures and selections are distinct;
- qualifying and main draw are separated;
- official and bookmaker settlement are distinct;
- a later stage `True` with an earlier stage unknown or false fails closed;
- duplicate fixture IDs fail closed;
- a complete official export is the only path to a numeric external coverage.

## 7. Remaining external dependencies

1. A licensed, complete, independently verifiable ATP/Challenger fixture export
   (or an authorised official API feed) for the window.
2. A per-selection archive ledger with an external object identity.
3. A per-selection publication ledger with a timestamp.
4. A per-selection bookmaker qualification ledger with bookmaker and price.
5. A per-selection bookmaker settlement ledger, distinct from the official
   result.
6. Coordination with PR #394 on the census contract; this module is additive and
   does not change the existing `licensed_fixture_census` interface.
