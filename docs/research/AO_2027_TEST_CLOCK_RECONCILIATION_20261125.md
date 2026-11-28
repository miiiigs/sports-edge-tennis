# AO 2027 synthetic test-clock reconciliation — 25 November 2026

**Branch / PR:** `workstream/ao-2027-format-readiness-20261125` (PR #389)
**Starting head:** `f6ee17b28820f95fd63534aa15fa6d6de1b58c28`
**Mode:** test-only and isolated local PostgreSQL. No production change.

## What broke

The AO 2027 full-slate regression created a January 2027 fixture and ran it with
`as_of = 2027-01-17`, then passed a clock of `2027-01-17T09:01Z` to the runner.
The runner accepted the request, but PostgreSQL rejected the predictions:

```text
new row for relation "predictions" violates check constraint "predictions_check2"
DETAIL: created_at 2026-11-25 ... as_of 2027-01-17 ...
```

The schema constraint is `created_at >= as_of`. A row physically inserted on
25 November 2026 cannot claim an `as_of` in January 2027. A test-only future
clock therefore cannot make a future-dated prediction row valid, and weakening
that constraint would remove a real append-only/future-date guard.

## Correction

The regression was corrected by moving the **observation and run clocks into the
real past**, not by relaxing the guard:

- the synthetic fixture still starts on 2027-01-18;
- its `match_versions.as_of`/`source_observed_at`/`ingested_at` are
  `2026-11-20T08:00Z`;
- the run `as_of` is `2026-11-20T09:00Z`;
- the deterministic runner clock is `2026-11-20T09:01Z`.

This is a genuine pre-match run of a future fixture observed in November 2026.
The production future-date guard (`request.as_of` must not exceed the runner
clock, and `created_at >= as_of`) remains fully enforced. The test still asserts
the persisted `best_of_sets = 5`, `R128`, `GRAND_SLAM`, `HARD` and the
`BO5_DECIDER_TB10_AT_6_6` scoring rule.

## Verification

```text
python -m pytest "tests/test_full_slate_runner.py::test_bo5_australian_open_target_runs_full_slate_and_keeps_format" -q
1 passed
```

The full PostgreSQL foundation suite at the pushed head is the release gate.
The previous `f6ee17b` clock-only fix is superseded by this test-data correction;
no production guard was relaxed.
