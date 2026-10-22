# Tennis as-of ranking research — October 22, 2026

**Draft / research only. No production writes, thresholds, staking, migrations or dashboard edits.**

## Verified evidence and blockers

- Railway Oct 22 production run 1448: 33 targets, 7 Manager consensus, zero issued BETs. 2 slates successful, Oct 24 slate failed on invalid API-Tennis result shape. Live OddsPapi call reported HTTP 403 (FORBIDDEN), and no new offered quote was received. Seven selected matches had no local Pinnapi sharp mapping.
- Oct 22 05:05Z pending-result diagnostics: 39 total = 25 not started + 14 historically overdue. Among overdue: 9 no longer observed, 4 terminal fixture without usable result (including walkover 132211), 1 without API-Tennis identity; all fourteen outside current recovery window. The hourly pending refresh reported checked=0.
- Issue #255: OddsPapi event mapping absent for 132424 and 132525. Absence of cached mapping does NOT prove provider unlisted; do not infer price.
- Issue #256: Oct 21 days.picks=6 Manager-all-probability vs 16 aligned detail. Never combine Manager primary and A1/A2 research tracking without explicit cohort labels.
- PR #250 froze Oct 19–21: official Manager >=60% 20 picks, 17 completed (9-8); aligned 88 rows (67 completed, 42-25), including research-only. Those are different cohorts and must not be compared as identical.
- Database migration 0001 has Elo rating_snapshots, **not** historical official ATP/WTA ranking snapshots. Consequently **no real ranking band performance is verified**. All as-of official ranking fields must remain NULL pending authoritative historical archives with source-published dates.

## Cohort contract

1. Production run requires completed status, prediction finalized, non-experiment contract.
2. First-qualified per (population, canonical sporting match), ordered by original Manager as_of and stable row ID. Manager >=60% and A1/A2 spread-only tracking are separate strata; do not add their scores together.
3. Original prediction timestamp is immutable. Match metadata joins only versions with as_of, source_observed_at and ingested_at <= pick time.
4. Agent 1 and Agent 2 direction and probability come from the same original prediction_run_id and match_id. Agent 3 participation is visible but does not confer consensus on research-only rows.
5. Official ATP/WTA ranking is acceptable only when an independently verified dated source was **published at or before original pick time**, even if an archival copy was retrieved later. Do not substitute latest rankings, Elo or inferred rank.
6. Score only completed matches with a verified winner. Leave retirement/walkover/cancellation separate until approved grading policy.
7. Issued bookmaker odds must be identified as **issued decision price**, not mixed with post-issue Guide Odds. Never calculate historical ROI from Guide Odds.
8. Use Wilson 95% intervals, Brier, calibration gap, ECE and per-band missingness. Treat n<30 in a band as exploratory. Require prospective holdout and multiple-comparison controls before proposing any filter.
9. Keep issue #256 customer reporting correction out of PR #254 release lane. This branch contains *only isolated research contract and synthetic checks*, no API/HTML alterations.

## Ranking bands

1–100; 101–200; 201–400; 401–600; 601+; MISSING_OR_UNRANKED. Compare selected player's published ranking band, absolute ranking difference, surface, tier, qualification and confidence. Score Manager/A1/A2 separately.

## Production verification required before real ranking conclusions

Authorized operator runs the standalone SELECT-only extractor with default_transaction_read_only=on and REPEATABLE READ, then joins independently verified historical ranking archives offline. Freeze cutoff, SQL hash, expected row counts, cohort overlaps, source provenance and one-sporting-match-per-population uniqueness. No production queries were executed by this PR.

## Integration

- Do not modify or merge into draft dashboard PR #254.
- Follow issue #255 for OddsPapi event mapping investigation; simultaneously investigate live 403 via approved provider support/credentials.
- Follow issue #256 for labeled API series, separately tested against mixed Manager/tracking days and UTC vs PHT.
- In deployment/security hold, no deploy, no schema changes and no model adjustments.

