# Brian foundation verification, 11 November 2026, 17:03 PHT

**Data source:** Railway production tennis-live-runner deploy log, read
2026-11-11 09:03:52 UTC; Nov5 counts last available
2026-11-10 15:04:48 UTC. These are *aggregated PostgreSQL-backed runner
diagnostics*, NOT a complete signed match-ID SQL export or authenticated
dashboard acceptance. Never claim all date rows were queried in one
database transaction from these older logs.

## P0 Nov9 issue: no missing aggregate selection; IDs not yet certified

Existing >=70% research subset **always has 28 retained**:
- Nov10 previous: 16W/5L/2 VOID/5 PENDING = 28.
- Nov11 current: 17W/7L/3 VOID/1 PENDING = 28.
Original, unfiltered Nov9 ledger: 61 picks, 32W/21L/4 VOID/4
PENDING as of Nov11 17:03 PHT.
Prior WhatsApp summaries wrongly omitted VOID counts and falsely
presented 26 / 25 as *total* selections.

We can infer net 1 additional WIN, 2 additional LOSSES, 1 additional
VOID, 4 fewer PENDING in the selected cohort. We **cannot yet prove
specific transitions or all IDs** from aggregated logs. Read-only
`ops/brian_nov09_selection_audit.py` now produces all 28 match IDs,
player names, original issue time, PIT original kickoff, retained
probability, previous and new W/L/V/P, result source and ingestion
timestamps, and any changed status. Hard fail if identities changed or
selected side/probability/issue time changed. NO real SQL run yet.

## 3 reporting windows: as-of log observations, PROVISIONAL

1. **FIXED November 5-9**: Original 124 selected:
   69W/45L/5 VOID/5 PENDING = **60.5% of 114 graded**.
   Existing >=70% research 56 retained:
   35W/16L/3 VOID/2 PENDING = **68.6% of 51 graded**;
   **45.2%** issuance coverage. Manager-only *separate*
   69: 35W/29L/3 VOID/2 PENDING = **54.7% of 64 graded**.
   Nov5 row was last emitted Nov10 23:04 PHT, later dates refreshed
   Nov11 17:03 PHT. This is not a single DB snapshot; don't sign off
   until single-transaction report is generated.
2. **LATEST rolling Nov6-10**: Original 127:
   74W/42L/6 VOID/5 PENDING = **63.8% of 116 graded**.
   Existing >=70% research 58:
   38W/15L/3 VOID/2 PENDING = **71.7% of 53 graded**;
   45.7% coverage. Manager-only *separate*
   67: 38W/24L/3 VOID/2 PENDING = **61.3% of 62 graded**.
   Snapshot last observed Nov11 17:03 PHT.
3. **CUMULATIVE from Oct27**, through last completed Manila date:
   **NOT CERTIFIED** from the currently exposed aggregate log window.
   Do not invent totals. A single read-only production DB query is
   required, sharing exact original/filter definitions and one
   source timestamp.

`ops/brian_three_window_report.py` and
`dashboard/three_window_evidence.py` now produce all 3 windows
including each day, last data read instant, separate Manager cohort,
accuracy=W/(W+L), first-issued count/coverage and provisional status
within ONE read-only REPEATABLE READ DB transaction. Code is COMMITTED
in draft audit PR, not deployed. Both the protected HTML and API
also expose the same three-window report when deployed.

## Latest 42 pending, not all classified correctly yet

Nov11 17:03 PHT: 42 = 34 NOT_STARTED based on stored future
schedule (not independent provider certification), 1 AWAITING_RESULT
(<6h), 1 NO_API_TENNIS_LINK, 3 PROVIDER_FIXTURE_NOT_REOBSERVED,
3 TERMINAL_FIXTURE_WITHOUT_RESULT. 10 RESCHEDULED flags overlap.
Seven individually emitted actionable records:

| Canonical match ID | Latest fixture status | Reason | Approx. age since latest start |
|---|---|---|---|
| 130903 | SCHEDULED | No API-Tennis match mapping | 1092h / 45.5d |
| 131712 | SCHEDULED | Provider fixture not reobserved | 886h / 36.9d |
| 133119 | WALKOVER | Terminal without canonical result | 221h / 9.2d |
| 133361 | SCHEDULED | Provider fixture not reobserved | 74.8h / 3.1d |
| 133505 | WALKOVER | Terminal without canonical result | 60.2h / 2.5d |
| 133347 | SCHEDULED | Provider fixture not reobserved; rescheduled | 31.9h |
| 133377 | WALKOVER | Terminal without canonical result; rescheduled | 7.1h |

Player names, true externally verified status, full old/new pending
transitions and the 34 future-dated IDs are **NOT available from the
runner's aggregate/actionable-only log output**. Do not invent them.

## Underlying cause and recurrence

The regular same-day pending refresh in
`src/sports_edge_tennis/live/pending_result_refresh.py`
restricts `DEFAULT_WINDOW_DAYS=MAX_WINDOW_DAYS=3` and at most 50
default targets. That is intentional to protect canonical match identity,
but **old unmatched cases from September and October will not clear
through this lane**. Some issues are provider-originated (winnerless
walkovers, missing fixture updates); others are integration tasks
(missing event ID, stale scheduled match, no older retry/alert).
The runner recently logged 0 provider transport errors on recent
refreshes; that does not prove all results were collected.

Fix acceptance:
- Record exact match identity and latest provider fixture state for
  EVERY pending selection, source observation and ingestion timestamp.
- Retry only matched unresolved aged fixtures with bounded provider
  windows; fail closed on ambiguous identity and never infer a winner.
- Distinguish postponed/rescheduled/cancelled/retired/walkover correctly.
- Escalate >24h unresolved to an operator; alert after >72h and flag
  missed provider observations. Record manual corrections immutably,
  with canonical match ID, source and who/when/why.
- Reconcile audited daily, fixed and cumulative reports with the
  protected dashboard API after genuine fresh-source deployment.
This remediation is NOT YET production-tested or complete.

## A3/A4 are PAUSED for release

Historical >=70% was designed on pre-Oct27 training outcomes and
merged via PR #284 on 2026-10-31; prospective Nov5-10 matches are
later than cutoff but remain retrospectively filtered. First-issued
score fields exist in production CTE. Full per-match
`picked_at < PIT kickoff` and unchanged model config cannot be
certified from aggregate logs alone.

Manager-only standalone win rates do not give matched incremental
effect: separate read-only `ops/brian_manager_matched.py` exists on
draft PR #291; needs production matching by fixture ID. A3 risk and
A4 timing fix are draft research with no certified accuracy gain.
Earlier ranking PR #257 found 866 eligible but 0 verified official
point-in-time rankings; do NOT backfill modern rankings as if historic.
Odds may be missing or stamped later; don't infer past prices.

## Actionable blocked services / actual billing

- **Railway**: $29.32 failed shared-workspace subscription charge,
  email Nov3; resource creation paused Nov8 due unpaid invoice.
  This is workspace-wide, NOT confirmed entirely attributable
  to Sports Edge Tennis. Operator: inspect [Railway Billing]
  (https://railway.app/account/billing) and pay/allocate actual invoice.
  Dashboard PR #292 merged; production still serves
  SHA `d8d43c46fb7506961cef6a692dfb7de30bc5f538`
  on deployment `4fdb29c3-49d2-44b6-b68c-60727664472d`.
  No new source-build deployment detected. Billing is a plausible
  blocker, **not confirmed sole root cause**; project owner must
  repair GitHub source trigger/rebuild and verify new SHA+health+auth.
- **GitHub**: email Nov3 confirms failed account payments,
  canceled past-due subscriptions and restricted metered usage.
  Actions jobs fail *before any steps execute*, job logs unavailable
  `BlobNotFound`. Exact unpaid amount UNKNOWN. Repository account
  owner: check [GitHub Billing](https://github.com/settings/billing),
  restore eligible Actions plan and run tests. Billing may contribute;
  detailed per-job failure annotations unavailable from connector.
- **OddsPapi**: persistent `HTTP403 SUBSCRIPTION_RESTRICTED`
  as of Nov11 17:03 PHT. Actual renewal invoice and entitlement
  UNKNOWN; approx $90/mo previously estimated, not approved price.
  Required only for real Guide Odds/market prioritization,
  NOT original prediction outcome or API-Tennis result collection.
- API-Tennis budget previously ~$80/mo and user indicated covered;
  no newly verified outstanding charge. Railway PostgreSQL is
  the verified Sports Edge data source, so a generic Supabase
  $25/mo estimate is NOT a verified Tennis obligation.
- **Development payment** must be negotiated separately. Historical
  $800/mo arrangement is not a hosting invoice and not proof
  of completion; discuss payment/deliverables and 15-20h/week option.
  User's actual new-job hours have NOT been supplied.

## Exact access limitation and next verifiable steps

Connected Railway OAuth interface exposes service variable names,
including `TENNIS_DATABASE_URL`, but WITHHOLDS values and offers
no raw PostgreSQL SQL action. Connected Supabase projects are
different projects, not Tennis. Hence we can check source code,
production runner logs and branch/deployment IDs but **cannot
execute production SQL in the present connection**. An authorized
Railway operator must run the read-only scripts inside a trusted
environment, or grant a secure read-only connector. Do NOT send
passwords in WhatsApp/GitHub.

- First: operator runs private Nov9 audit and pending-transition audit;
  returns signed-off evidence files (IDs/status transitions).
- Second: inspect/restore result sources and older recovery alerts.
- Third: restore Railway deployment; run automated tests, compare
  HTML/API and same read-only DB snapshot.
- Fourth: matched Manager and Original attribution.
- Only after all these: freeze A3/A4 and start prospective 30 days,
  200 graded per candidate, >=40% coverage, with exact version hash.
  A 73-75% target is NOT demonstrated.

**No prospective A3/A4 result, no complete named 52->43 transition,
no authenticated fresh dashboard acceptance, and no fully passed
GitHub Actions tests have been established.**
