# Australian Open 2027 P0 format readiness — November 25, 2026

Status: IMPLEMENTED IN DRAFT, NOT VERIFIED OR PRODUCTION APPROVED. No deployment, new provider calls, data purchases, database mutations or changes to original published selections.

## Problem and evidence
Runner branch baseline ad65894f553d550d647341bf601e1ef92851b95a: Agent 2 already supports BO3/BO5 and edition-specific final-set rules. The AO main-draw resolver stops at 2026; API-Tennis source A defaults to BO3 if format is absent. Its legacy Q-prefix qualifying fallback also confuses QF with qualifying. The October 15 audit noted BO3 Slam rows; present database prevalence is not yet measured. Official AO scoring explanation https://ausopen.com/articles/news/guide-scoring-tennis describes men's BO5 and final-set 10-point tiebreak at 6-6 since 2019. The edition-specific 2027 rulebook must still be confirmed before release. Qualifying is a separate research scope and not enabled for Agent 2 by this change.

The 25 November census now measures the stored defect: 128 AO 2026 main-draw
rows are best-of-3, 115 qualifying rows carry stripped QF/SF/F labels, 40
2016–2025 main-draw QF rows are misflagged as qualifying, and 2026 is split
across editions 2248/2249 with NULL surface and end date. The code cause of the
QF flag is `startswith("Q")` in the Sackmann parser; the 2026 BO3 rows come from
the API-Tennis missing-format default. Future-ingestion fixes, the isolated BO5
rehearsal and the no-rewrite boundary are recorded in
[`AO_2026_DEFECT_RESOLUTION_AND_2027_REHEARSAL_20261125.md`](AO_2026_DEFECT_RESOLUTION_AND_2027_REHEARSAL_20261125.md).

## Candidate and tests
On a separate draft branch, only the exact Australian Open 2027 men's ATP scope and recognized main-draw rounds receive the BO5 deciding-set TB10 candidate. Explicit contradictory format/tier/qualifying fields and unknown round codes reject rather than silently guessing. Q1/Q2/Q3 classify as BO3 but scoring resolver remains unsupported for 2027 qualifying. Other editions and tournaments retain previous rules. Regression tests cover main draw, qualifying, conflicting fields, non-AO behavior, Markov match-length differences and equal-player symmetry. Synthetic cases do not establish actual provider coverage.

## Definition of done / required verification
1. Confirm official AO 2027 format and qualifying rules; freeze source evidence, scope and owner decision.
2. Execute full PostgreSQL CI, inspect logs, independently review exact branch diff and live ingestion paths (including reconciliation and historical source variants).
3. Authorised read-only real fixture audit: exact AO provider tournament IDs/aliases, rounds, competing flags, format and versions, known historical BO3 errors, Jan ATP fixtures, publication eligibility, odds identity and source visibility. Do not automatically rewrite historical versions. Identify and resolve unsupported aliases/rounds via reviewed mappings.
4. Chronological historical AO rehearsal using legally usable records, edition-specific scoring and genuinely available features; compare original and correct format on matched rows with Brier/logloss/accuracy/coverage and explicit exclusions. January 2027 is prospective, not historical.
5. Establish runner/dashboard compatibility, independent archive and settlement, fail-closed scope, controlled deployment/rollback, owner approval. No qualifying, United Cup or AO production-scope expansion merely because this draft exists.

No forecasting uplift has been measured. Do not promote on CI alone.

## Verification status (25 November 2026)

| Required verification | Status | Evidence |
|---|---|---|
| Official AO 2027 format and qualifying rules | **Partially verified** — general Slam format confirmed; 2027 edition-specific rulebook not located | [`AO_2027_REGULATION_VERIFICATION_20261125.md`](AO_2027_REGULATION_VERIFICATION_20261125.md) |
| Full PostgreSQL CI at the exact head | Pending the pushed head | PR #389 checks |
| Read-only real fixture audit | Measured for stored AO 2016–2026 and January discovery; exact IDs, root cause and dependency analysis in [`AO_ROOT_CAUSE_AND_DEPENDENCY_20261126.md`](AO_ROOT_CAUSE_AND_DEPENDENCY_20261126.md); no official 2027 draw | [`AO_HISTORICAL_FORMAT_CENSUS_EXECUTION_20261125.md`](AO_HISTORICAL_FORMAT_CENSUS_EXECUTION_20261125.md) |
| Chronological historical AO rehearsal | **Blocked** — zero original AO pre-match forecasts | [`AO_HISTORICAL_FORMAT_CENSUS_EXECUTION_20261125.md`](AO_HISTORICAL_FORMAT_CENSUS_EXECUTION_20261125.md) |
| Realistic Agent 2 support | Real stored-history support rehearsal executed; synthetic 2027 pairing, not a forecast | [`AO_2026_DEFECT_RESOLUTION_AND_2027_REHEARSAL_20261125.md`](AO_2026_DEFECT_RESOLUTION_AND_2027_REHEARSAL_20261125.md) |
| Rescheduling rehearsal | Append-only reschedule preserved original start/PIT; guard tested | `ao_2027_reschedule_rehearsal_20261125.json` |
| Runner/dashboard compatibility | Runner BO5 regression; dashboard read model structurally tournament-agnostic | January acceptance report |
| January acceptance | Conditional; owner approval, rules and real slate outstanding | [`AO_2027_JANUARY_READINESS_ACCEPTANCE_20261125.md`](AO_2027_JANUARY_READINESS_ACCEPTANCE_20261125.md) |
