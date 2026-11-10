# Brian acceptance plan (proposed milestones, 10 November 2026 PHT)

**Severity P0: statistics and pending reconciliation must precede model deployment.**
These are proposed operator delivery targets, **not guaranteed completion
times**: lack of authorized database access, official provider evidence or
GitHub Actions runner will block sign-off. Produce a timestamped BLOCKED
manifest rather than a falsely green report whenever a gate cannot be met.

| Target (PHT) | Owner | Artifact / acceptance condition | Blocker |
|---|---|---|---|
| Wed Nov 11, end of day | Development + result-settlement owner | A private complete pending ledger: every match ID, first issued time, latest schedule/status, exact elapsed hours, provider ID, last observation, root-cause label, required operator action; old total=45 tracked | Authorized read-only PostgreSQL export; provider event access |
| Thu Nov 12, end of day | Development + independent QA | Date-by-date Original/Manager/≥70% scoreboard, point-in-time first-issued selected side, settled W/L/V/P, exact 6-case follow-up, discrepancy list (including Brian mobile dashboard), provider-source references. Any unsourced fixture stays PENDING and report says PROVISIONAL | Pending authoritative outcomes, authenticated dashboard, export |
| Fri Nov 13, end of day | Research owner | Same-cohort **exploratory** A3/A4 comparison with matched original pick IDs, train/pick/observation cutoffs, sample/coverage, W/L/V/P, proper scoring and missing-data flags; explicitly state that Nov5–9 are NOT independent holdout since model design used them | Read-only original ledger, odds lineage, executable tests |
| Sat Nov 14, end of day | Release owner + independent QA | Fix GitHub Actions eligibility, run focused tests, Postgres tests and documented authorized dry-run; pre-register immutable A3/A4 candidate coefficients/features, coverage guard and reporting contract | Owner Actions/billing/policy action, code review and test execution |
| Sun Nov 15, earliest possible | Release owner | Begin **prospective shadow logging** with candidate/hash stored before kickoff, parallel original and pre-registered qualified, same match-date reconciliation; no official Model Pick replacement | Only if Nov14 gates actually pass. Otherwise publish revised date and BLOCKED status |
| After >=50 scored prospective candidate selections and >=5 fully resolved daily cohorts | Independent QA + Brian | First independent assessment. Win rates must be totals W/(W+L), with confidence intervals, missed winners, avoided losses, coverage, pending and daily sample adequacy. **No 73% guarantee** | Future matches must actually occur/settle |

## Ownership and requests

- **Development:** create safely replayable, read-only reports; investigate
  canonical identity and authoritative results; no inferred winners/VOID.
- **Brian/account owner:** confirm appropriate **read-only Postgres export
  access** or arrange an authorized administrator to run the full existing
  `pending_result_diagnostics --json-out` report. Never email a DB password
  or put credentials in a public issue.
- **Brian/account owner:** renew/reinstate **OddsPapi** subscription,
  currently HTTP403 SUBSCRIPTION_RESTRICTED. This is needed for genuine
  historical/latest Guide Odds context, **NOT** for basic API-Tennis result
  settlement or original A1/A2 winner selection. No guaranteed provider
  activation date; test the restored provider endpoint as soon as access
  exists and publish observed coverage.
- **GitHub repository owner:** check Actions billing, account/runner
  eligibility and restrictions for issue #287, or approve isolated
  equivalent environment and capture exact SHA/test count. Current
  research CI fails before even reaching pytest; no passing claims.
- **Brian:** share the **date and match IDs/screenshot** if the logged-in
  dashboard disagrees with daily report (without login secrets); authenticated
  mobile/results API parity remains a separate acceptance gate #289.
- **Provider/operator:** verify the 6 actionable matches, especially two
  winnerless walkovers requiring an authoritative winner/void determination.

## Baseline facts (Nov 10 22:05 PHT)

- Original aligned Nov5–9: 124 published, **64 W / 41 L / 4 V / 15 P**;
  105 graded = **61.0%**.
- Existing >=70% first-issued confidence research subset: 56 retained,
  **34 W / 14 L / 2 V / 6 P**; 48 graded = **70.8%**,
  45.2% selection coverage. It retained 34 of 64 known winners and 14 of
  41 known losses; removed 30 winners and 27 losses (52.6% graded).
  These are different-sized selected cohorts; apparent +9.9 percentage
  points is NOT a verified model gain.
- Independently defined first-production Manager-only: 69 picks,
  **35 W / 27 L / 2 V / 5 P**, 62 graded = **56.5%**.
- Nov9 filtered had **16 W / 5 L / 2 V / 5 P** after another result was
  ingested; **76.2% PROVISIONAL**. If every remaining pending selection
  becomes graded, it can end from 16/26=**61.5%** to 21/26=**80.8%**.
  The earlier snapshot (15/5/6) had range 57.7–80.8%; both are legitimate
  timestamped stages.
- Latest all-pending: 45 = 37 NOT_STARTED based on future scheduled
  timestamps, 2 AWAITING_RESULT under 6 h, and 6 actionable
  (ages detailed in PENDING_AUDIT_20261110_2205PHT.md). 16 of 45 have
  overlapping RESCHEDULED flag. EXACT age/status of all 37 requires
  per-record export and reclassification; do not assert all are definitely
  postponed, unfinished or truly upcoming.
- A3 redesigned and A4 reliability model are **research code in draft
  PR #291**; no official V3 live predictions, no graded A3/A4 same-date
  scorecard, no independent A4 accuracy lift.
- **Leakage admission:** initial PR #291 A4 replay trained against
  match-day cutoff, potentially too late for picks issued before that day;
  V2 calibrated walk-forward shared this problem. Corrected in research
  with a per-pick cutoff `min(match_day_midnight, first_issued_at)` and
  tests. Original production Manager, original historical W/L and static
  >=70% filter do not use this research calibrator; their figures are
  unchanged. There has been **no real per-match V2/A4 calibrated report
  to rerun**, so effects on modeled accuracy are unknown until one is
  run with private first-issued source exports.
- For a fair independent test, any November 5–9 period examined in
  development is **not untouched holdout**. Lock candidate rules before
  future fixtures and publish pre-kickoff prediction timestamps.
