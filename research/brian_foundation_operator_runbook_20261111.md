# Sports Edge Tennis foundation acceptance - read-only operator runbook

Date: 11 November 2026. Branch: `audit/tennis-foundation-ledger-20261111`.

**Safety:** Run ONLY in a trusted terminal with authorized Railway Postgres
read-only credential already in `TENNIS_DATABASE_URL`. DO NOT copy URL,
passwords or output files containing match-level records into GitHub,
WhatsApp, shared logs or a public URL. These scripts use DB READ ONLY and
fail on mismatched counts; no settlement/status write is attempted.

The current Railway connector provides variable *names* but not their
values and does not expose an SQL command. No production audit has been
run through this connector. Owner/operator access is a real gate.

## Operator commands

From this repository's branch checkout:

```bash
export PYTHONPATH=src:.
mkdir -p /secure/tennis-brian-audit
chmod 700 /secure/tennis-brian-audit

# 1. November 9, all original 61 and all qualified 28, with voids
python ops/brian_nov09_selection_audit.py \
  --before 2026-11-10T14:05:38+00:00 \
  --after 2026-11-11T09:03:52+00:00 \
  --out /secure/tennis-brian-audit/nov09_selection_changes.json

# 2. Historical net 52 -> 43. This must identify new and settled
#    match IDs or fail rather than pretending 9 were settled.
python ops/brian_pending_transition.py \
  --before 2026-11-10T11:03:34+00:00 \
  --after 2026-11-10T15:04:44+00:00 \
  --expected-before 52 --expected-after 43 \
  --out /secure/tennis-brian-audit/pending_52_to_43.json

# 3. All pending match IDs, players, provider timestamps, ages,
#    schedule flags, failure reasons and actionable next steps
python ops/brian_pending_inventory.py \
  --lookback-days 90 \
  --out /secure/tennis-brian-audit/all_pending_cases.json

# 4. Fixed Nov5-9, rolling, cumulative from Oct27, SAME DB transaction
python ops/brian_three_window_report.py \
  --as-of-date 2026-11-11 \
  --out /secure/tennis-brian-audit/three_windows.json

# 5. Manager match-by-match kept/rejected/switched
python ops/brian_manager_matched.py \
  --start 2026-11-05 --end 2026-11-09 \
  --as-of 2026-11-11T09:03:52+00:00 \
  --out /secure/tennis-brian-audit/manager_matched.json

# 6. Focused tests (may be run in local isolated virtualenv
#    with development/test dependencies, independent of GitHub Actions)
python -m pytest -q \
  tests/test_brian_foundation_reporting.py \
  tests/test_brian_pending_inventory.py \
  tests/test_brian_pending_transition.py \
  tests/test_brian_manager_matched.py
```

All outputs are private owner-only filesystem files (0600). Run one
report with a changed as-of on a NEW filename; never overwrite and
silently replace Brian's first snapshot. If a query fails, record
the error, root cause and timestamp instead of manufacturing totals.

**Expected check 1:** 28 qualified picks both snapshot times.
Early 16W/5L/2 VOID/5P vs later 17W/7L/3 VOID/1P. Every original
match ID and selected side must be unchanged. Individual source-backed
status transitions must be listed.

**Expected check 2:** The dynamic snapshot counts must satisfy
opening + NEW pending - EXIT pending = closing. A net nine-record
difference is not necessarily nine settlements. If query cohorts
or lookback differ from live diagnostics, STOP and reconcile scopes.

**Expected check 3:** Every pending match includes names, source
status, original and current scheduled times, last provider
observation, age, independent verification flag and specific owner
action. A future stored start is UNVERIFIED until rechecked.

**Expected check 4:** All three reports have one database snapshot
time, same original/qualified definitions and correct W/(W+L)
denominator, VOID/PENDING tracked separately, and PROVISIONAL where
unresolved. In the current production dashboard the new three-window
panel is NOT live; require a fresh Railway source deployment at the
updated branch SHA, successful health and authenticated HTML/API to
database parity.

**Expected check 5:** Manager vs Original match IDs and issue times;
W/L/V/P for accepted, rejected, different-side and Manager-only
cohorts. No unmatched standalone percentage can be causal evidence.

## Current blockers and escalation

- Railway team resource creation paused over unpaid shared-workspace
  invoice; 2026-11-03 failed $29.32 subscription email. Owner must
  check [Railway billing](https://railway.app/account/billing) and
  allocate only Sports Edge usage; do not assign the full shared
  workspace charge to Brian without itemizing it.
- GitHub 2026-11-03 notice: failed billing, canceled past-due paid
  subscriptions, restricted metered usage. Exact balance unknown.
  Owner checks [GitHub billing](https://github.com/settings/billing),
  confirms runner eligibility, then reruns CI. A **committed test is
  not an executed test**.
- OddsPapi still HTTP403 SUBSCRIPTION_RESTRICTED, prior estimate $90
  monthly not confirmed renewal invoice. Original winner prediction
  and API-Tennis result settlement should proceed without it.
- Result ingestion: standard bounded refresh is only 3 UTC days.
  Older cases need separate identity-safe recovery, overdue alerts and
  authoritative void/walkover handling. Do not silently widen provider
  windows, merge ambiguous matches or fabricate terminal results.

## Deliverable acceptance in order

1. Nov9 all 28 selected rows, full original 61 and immutable
   W/L/V/P transition evidence.
2. Pending as-of 52->43 full match-ID transitions; current 42-case
   per-fixture provider-audit inventory, independently recheck the
   stored future-scheduled cases.
3. One-transacted three-window report fixed Nov5-9, rolling and
   cumulative.
4. Fresh Railway deployed SHA, page/API/DB row-level parity and
   GitHub/local focused tests recorded with exact SHA and pass/fail.
5. Matched Original/Manager benchmark.
6. ONLY after foundation acceptance: fix and freeze the A3/A4
   prospective version under >=30 days, >=200 graded per candidate,
   >=40% eligible original issue coverage. No 73% claim yet.

**No production write, status change, release, subscription payment,
independent A3/A4 accuracy or authoritative per-match audit is
claimed by this runbook alone.**
