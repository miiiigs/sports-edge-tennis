# Brian P0 reliability remediation - 12 Nov 2026, 15:03 PHT

**Evidence vs work state are kept separate.** Unverified data cannot be
promoted to a proved win, void or complete operational recovery.

## Source and live findings

Railway hourly production runner (original production build unchanged),
2026-11-12T07:03Z:
- 27 pending = 20 NOT_STARTED from stored schedule (not provider-reverified)
  + 3 terminal winnerless WALKOVER
  + 3 provider events not re-observed
  + 1 missing canonical API-Tennis event map.
- Seven actionable unique match IDs:
  130903 missing API event key;
  131712, 133361, 133347 provider not re-observed;
  133119, 133505, 133377 WALKOVER with no usable canonical result.
- Five `OUTSIDE_RECOVERY_WINDOW`, eight `RESCHEDULED` flags
  overlap categories. No "all future fixtures definitely unplayed" claim.
- Nov9 original still **61=33W/21L/4 VOID/3 PENDING** (61.1%
  of 54 graded); research >=70% group still
  **28=17W/7L/3 VOID/1 PENDING** (70.8% of 24 graded).
  Different groups. No A3/A4 accuracy proof.
- OddsPapi currently **HTTP429 RATE_LIMIT**; no reliable offered
  bookmaker lines. API-Tennis settlement recovery remains independent.
- Live dashboard is **still Nov7 SHA d8d43c46**, source PR
  changes are not verified live. Prior Railway source refresh produced
  no new deploy. GitHub Actions PostgreSQL Foundation fails before any
  steps. Railway PostgreSQL variables accessible by name but no direct
  SQL connector to execute privileged audits.

## Additional actual implementation in P0 draft PR #294 (this pass)

1. **Protected Nov9 per-match verification route**
   `/api/dashboard/nov9-audit` added to production app, guarded by
   existing dashboard HTTP Basic middleware. It reads immutable
   first-issued picks, original picked-at time, original selected side,
   PIT kickoff, first-issued confidence, player names, provider
   result status/winner/source-observed+ingested times and latest
   stored fixture state. A separate pre-issuance ingested fixture
   is required to certify PIT kickoff and player identities, never
   a later roster/schedule. Strictly compares each match-ID W/L/V/P
   and >=70% subset to the date summary. Errors => 503 (fail closed),
   not an accepted missing selection. **Implemented, NOT live-tested.**

2. **Comprehensive pending detail view**
   Adds players, first-issued timestamp, original PIT start,
   latest provider-observed schedule/status, age, last observation,
   specific reason, next required action and explicit NOT REVERIFIED
   flag to all pending rows. Uses one read-only transaction with
   fixed/rolling/cumulative reporting. **Committed, NOT live-tested.**

3. **Historical pending count denominator fix**
   `ops/brian_pending_transition.py` previously used fixed 2026-09-01
   `--since`, while live diagnostics use rolling **60 days** from
   each as-of snapshot. Default now computes the same independently
   anchored 60-day population in both snapshots and emits exact
   new cases + exits and source times. It MUST still be run and
   matched to the 52->43 historical dates; code alone is NOT evidence.

4. **Older event recovery**
   `ops/brian_aged_provider_probe.py` is a controlled *manual,
   read-only API-Tennis dry-run* for one older UTC match date (3-90d),
   max 10 canonical target fixtures and one provider request per
   invocation; no results or schedules persisted. Automated replay
   is deliberately NOT enabled because a durable daily provider
   request lease/quotas and historical settlement integration
   tests are not yet passed. Initial daily planner and logging
   remain source only.

5. **Tests committed** for exact Nov9 first-issued identities,
   pre-issuance schedule, status/void integrity, source-time
   lookahead rejection, bounded historical dry-run, non-starvation,
   three reporting periods and per-fixture pending actions.
   NO passed GitHub Actions jobs or local PostgreSQL integration
   output available. No unauthorized writes made.

## Blockers by responsible owner

- **Brian / infra account owner:** identify and pay only itemized
  Tennis share of Railway charges; earlier $29.32 failure concerned
  a **shared** Railway workspace and is not safely attributable
  100% to Tennis. Github billing exact balance unknown; account owner
  check Actions eligibility. Check OddsPapi request quota/renewal
  price before payment. *The developer has stated they cannot
  front hosting/subscription charges right now.* Keep development
  fees separate from infrastructure.
- **Developer / repo owner:** resolve failing jobs/annotations,
  locally execute Python+PostgreSQL tests when an authenticated
  environment is available, inspect provider 429 backoff, repair
  Railway source integration and request fresh source build.
- **Authorized Railway DB operator:** run read-only existing
  `ops/brian_baseline_bundle.py`, Nov9 audit and historical
  pending transition with no passwords copied to messages.
  Protected Nov9 API becomes an alternative once new source
  deployment is actually live.
- **Independent production QA:** require new deployed SHA, protected
  401 without authentication, authenticated page/JSON/DB row
  parity, complete 52->43 match-ID reconciliation and accurate
  provider-unverified labels before declaring work VERIFIED.

## Release decision

**HOLD** P0 PR merge and new automatic old-result writer until
tests/database parity pass. Do not change original winner selection,
A1/A2/A3 Manager or promote A4 research. A healthy runner is
not evidence of complete results. The 73%-75% long-term win-rate
target is not demonstrated.
