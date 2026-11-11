# Brian: 7-point acceptance and blocker register, 11 Nov 2026 PHT

**Source version:** this file in research PR #291. **Not a production
release.** No A3/A4 lift or 73%-75% goal is demonstrated.

## 1. Pending reconciliation P0

Observed runner diagnostic timeline:
- 2026-11-10 19:03 PHT: 52 pending.
- 2026-11-10 23:04 PHT: 43 pending.
- 2026-11-11 06:06 PHT: 32 pending.
- 2026-11-11 08:06 PHT: 45 pending.

This is a dynamically changing population. **52 -> 43 is net -9, not
proof that nine named fixtures settled.** The logs record aggregate
terminal/ingest counts, not complete event-level additions/exits. The
latest 45 by stored fixture classification: 37 future-scheduled,
2 recently awaiting results, 6 actionable. Nine have overlapping
RESCHEDULED flags. These are NOT independent provider confirmations
that all future-scheduled fixtures truly are unplayed.

Match-level owner output: run
`ops/brian_pending_transition.py` with immutable as-of timestamps,
authorized read-only DB, expected 52 and 43. Result must explicitly
list new pending fixture IDs, every previously-pending fixture that
left PENDING, its terminal/void source observation + ingestion time,
the original Manila match date, and whether it appears in Nov5-9.
**The output does not exist yet** because DB access/execution is not
verified. If counts do not reconcile, flag mismatch and investigate,
not modify figures to fit the logs.

## 2. >=70% filter provenance / limitations

- In PR #284, merged **2026-10-31 18:39 UTC**, the pre-selected rule
  `PIT_CONFIDENCE_GE70_V1` used 755 earlier graded historical picks,
  with the 70% confidence cutoff chosen from results observed before
  Oct27. The threshold WAS selected with reference to earlier
  training outcomes/other candidate gates, so its training performance
  is in-sample and not independent. October24-27 70/80 thresholds
  also appear in research PR #273.
- November5-9 had not occurred at that rule freeze. Original
  probabilities are persisted first-issued pre-match via canonical
  aligned SQL. We still need individual evidence that every shadow
  candidate was reproducible from its first-issued timestamp before
  kickoff and that the threshold was continuously enforced unchanged.
- **As of Nov10 22:05 PHT:** >=70% qualified group 34W-14L,
  2 void, 6 pending, **70.8%** on 48 graded, 56/124=45.2%
  issuance coverage, below 73%; evidence for prior frozen research
  filter only, NOT A4.
- **Later Nov11 morning Nov9 match-date snapshot:** >=70% on Nov9 is
  17W-7L 3 void 1 pending = **70.8% of graded**; best if the one
  pending becomes a win 18/25=**72.0%**, below 73%. Prior
  15W5L and 16W5L were provisional observations, never final.

## 3. Manager and fair paired contribution

Manager's actual code requires A1/A2/A3 active same-side directional
agreement, per-agent probability minimum and spread within fixed gate,
with A3's abstention permitted only under the corresponding gate.
When accepted, confidence is the arithmetic mean of active agents.
When rejected, it produces NO_BET. The extended original aligned
view also counts an A1/A2 same-side wide-spread *tracking-only*
cohort that Manager has rejected, so simple Manager vs Original
standalone W-L is NOT a matched model-effect estimate.

Read-only `ops/brian_manager_matched.py` now defines exact
match-ID populations: same side both, different side both, Original
only, Manager only, with first-issued timestamp and W/L/V/P for
both. **Code committed, live DB evaluation not yet run**. Do not
claim Manager improved or damaged the accuracy until verified
match-paired evidence exists.

## 4. A3/A4 chronology and independent test

No independent untouched historical date set has been certified.
Nov13 deliverable is **exploratory DEVELOPMENT ANALYSIS ONLY**,
not independently valid A4 accuracy. Research Nov5-9 influenced
model design. True independent evidence starts when a fixed
prospective version is registered before future match kickoff.

A4 and V2 leakage correction now fits at
`min(Manila match-day midnight, original first-issued pick timestamp)`
and requires every training result's observed+source-observed+ingested
timestamps before that cutoff. Example: 2026-11-01 08:00Z first
pick, 2026-11-01 16:00Z match-day cutoff, 12:00Z result ingested:
result MUST be excluded. Synthetic assertions exist
`tests/test_a4_quality_shadow.py` and `tests/test_v2_walkforward.py`,
but **not executed/passed due upstream CI failure**, so release is held.

## 5. Actionable blockers

- **GitHub Actions** (developer/repo owner): Run
  https://github.com/miiiigs/sports-edge-tennis/actions/runs/42841297270,
  job 129916908690, failed within seconds with **zero job steps**.
  Job log GET returned 404 XML `BlobNotFound`. There are two
  check-run annotations, but their contents are not accessible from
  current connection. Billing, policy and provisioning root cause
  **NOT established**; developer to inspect UI/check annotations,
  Actions settings, runner eligibility and billing, or open GitHub
  support request. Prevents certified unit/SQL integration tests
  and model-release sign-off. **No confirmed new bill.**
- **Railway dashboard** (developer/Railway workspace admin): PR #292
  merged SHA `1147b91a1a656f2184ae9f1deeaaa7903944f25d`,
  latest watched source commit `f5f163e43db20f444155bafcbe7f3a09950c70e7`.
  Production service still runs deployment
  `4fdb29c3-49d2-44b6-b68c-60727664472d`, SHA
  `d8d43c46fb7506961cef6a692dfb7de30bc5f538`
  from Nov7. Source patch accepted/triggered but no NEW build or deploy
  recorded. Exact reason unknown; reconnect GitHub deploy webhook /
  permission, force a FRESH SOURCE BUILD at the current branch SHA
  (not an image redeploy), then require deployment SUCCESS + /healthz
  200, protected 401 without login, and authenticated HTML/JSON
  per-date counts/43/45 status reconciliation. **Not live yet.**
  No confirmed extra cost.
- **OddsPapi** (Brian/account payer): latest 2026-11-11 08:06 PHT
  `HTTP_403 FORBIDDEN SUBSCRIPTION_RESTRICTED`.
  This disables real Guide Odds/market-priority evidence, **not**
  API-Tennis settlement, original Elo/Markov predictions or historical
  results reports. Provider publicly offers custom plans; previous
  project budget discussed approx **USD 90/month**, but actual
  invoice, access and renewal price must be confirmed before payment.
  Only Brian/account payer need authorize the actual subscription;
  developer then checks account status and genuine 2-sided markets.
- **DB export / provider matches** (developer + authorized DB operator):
  need one sanctioned **read-only** Railway PostgreSQL export
  (or operator-executed scripts) to confirm additions and settlements,
  all 45 statuses/ages, and Manager matched counterfactual.
  Never ask Brian to send a database password or copy secrets in chat.

## 6. Preregistered forward test

Protocol: `FORWARD_TEST_PROTOCOL_DRAFT_20261111.md`.
At least **30 consecutive Manila days**, **>=200 fully graded
WON/LOST selections per tested candidate**, **>=15 days with
>=5 graded candidate picks**, and **>=40% coverage** of eligible
Original picks. Extend the period if these aren't met. At 50
graded picks release *interim operational* scorecard only, no claim
of model gain. Every pick timestamp must precede kickoff; hash
immutable code, features, thresholds, fit cutoff, trained coefficients
and source snapshot at first issuance. Original, existing >=70%,
A3-only, A4-only and combined A3+A4 stay parallel; odds are only
context. Any rule change creates a new named version/cohort, never
pooled. Primary accuracy = total W / (W+L) on fully graded picks,
plus 95% CI, coverage, skipped winners and avoided losses.
Nov15 is the earliest possible **first prospective data collection**
date, never date of demonstrated improvement.

## 7. Delivery / verification checklist

- **Nov11 (developer + authorized DB operator)**: private
  `pending_52_to_43_transitions.json` with all IDs/status transitions,
  new-pending count, day/cohort link, exact result provider timestamps,
  age/status of each current pending. Brian gets redacted audit
  summary and exact reconciled formula. Acceptance = both as-of
  counts reconcile or marked BLOCKED with discrepancy. Without DB
  access this remains incomplete.
- **Nov12 (developer + Railway owner)**: signed source-as-of W/L/V/P
  Original/qualified/Manager and per-day pending discrepancy file;
  fresh Railway deployment ID/commit/status; authenticated dashboard
  vs API row-level parity with evidence. Acceptance = no unexplained
  mismatches; otherwise clearly FAIL/BLOCKED.
- **Nov13 (research owner)**: matched Manager decision report and
  A3/A4 per-match PIT historical exploratory comparison including
  training cutoff, matched records, W/L/V/P, coverage, avoidance,
  missed winners, calibrations; mark NOT INDEPENDENT if no verified
  untouched dates. Acceptance = reproducible filenames/checksum,
  no invented A4 gains; if absent block.
- **Nov14 (release/repo owner)**: GitHub or approved equivalent
  reproducible test result with exact SHA, step output, test counts,
  failed tests; prospective frozen protocol/model artifact checksum
  and non-hindsight issuance check. Acceptance = tests PASS and
  freeze recorded before kickoff or NO-GO.
- **Nov15 (conditional earliest)**: first append-only shadow candidate
  timestamped before kickoff alongside unchanged Original and >=70%.
  Evidence = issued match IDs, model hashes, timestamp/fixture
  proofs, plus daily W/L/V/P reporting. **No improvement demonstrated.**
  If any release gate missing, mark BLOCKED and reset start date.

No claim that any of these future deliverables is already completed.
