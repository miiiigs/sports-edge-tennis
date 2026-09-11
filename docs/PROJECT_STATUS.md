# Project Status

Last updated: 2026-09-12

## Current milestone

`PRODUCTION SOURCE HANDOFF + PROSPECTIVE EVIDENCE`

The final combined Tennis implementation is now canonical on `main`. The predictive/model build and Git source-of-truth reconciliation are complete. Remaining work is operational: point Railway services at canonical `main`, confirm database authority, enable owner authentication only after the owner bootstrap is ready, and close the prospective closing-price / CLV lifecycle without changing locked betting logic.

## Current implementation truth

```text
TENNIS REPOSITORY: ACTIVE
CANONICAL MAIN: COMPLETE
CANONICAL MAIN MERGE SHA: 76062c77d73747a0445158c370eab3f3f9c70d90
FINAL COMBINED CI GATE: PASSED

TENNIS POSTGRESQL FOUNDATION: COMPLETE
TENNIS HISTORICAL ATP CORPUS: COMPLETE (2016-2025 baseline)
TENNIS RECENT API-TENNIS RESULT REFRESH: IMPLEMENTED
TENNIS LIVE FIXTURE INGESTION: IMPLEMENTED
TENNIS LIVE ODDS INGESTION: IMPLEMENTED

AGENT 1 SURFACE ELO: IMPLEMENTED
AGENT 2 HIERARCHICAL MARKOV / BARNETT-CLARKE: IMPLEMENTED
AGENT 3 INTRANSITIVITY: IMPLEMENTED

FULL-SLATE RUNNER: IMPLEMENTED
MANAGER C1-C4 CONSENSUS: IMPLEMENTED
TYPE A MODEL-EDGE MARKET ENGINE: IMPLEMENTED
TYPE B PRICE-DISPERSION SCANNER: IMPLEMENTED
TYPE B DURABLE APPEND-ONLY LEDGER: IMPLEMENTED + PRODUCTION VERIFIED
PINNACLE SHARP REFERENCE / RECONCILIATION: IMPLEMENTED
OFFERED BOOKMAKER ODDS: IMPLEMENTED
CLIENT DASHBOARD: IMPLEMENTED
SCHEDULED LIVE RUNNER: IMPLEMENTED
LIVE MODEL CHECKPOINT / DELTA ADVANCEMENT: IMPLEMENTED

TENNIS VETO V1: IMPLEMENTED (KILL-ONLY)
TENNIS VETO AUDIT LEDGER: IMPLEMENTED
TENNIS INJURY / MEDICAL-TIMEOUT FEED: NOT CONNECTED; EXPLICITLY UNAVAILABLE
TENNIS OWNER AUTHENTICATION: IMPLEMENTED; DISABLED BY DEFAULT
TENNIS AUTH BOOTSTRAP / ROLLBACK RUNBOOK: IMPLEMENTED

RAILWAY SOURCE RECONCILIATION: PENDING MANUAL BRANCH-SELECTOR CHANGES
AUTHORITATIVE POSTGRES SERVICE: MUST BE CONFIRMED BEFORE ANY DB RETIREMENT
CLOSING-PRICE / CLV LIFECYCLE: NOT YET CLOSED END-TO-END
```

## Canonical source

Repository:

```text
miiiigs/sports-edge-tennis
```

Canonical branch:

```text
main
```

Canonical reconciliation merge:

```text
76062c77d73747a0445158c370eab3f3f9c70d90
```

The final stabilization candidate PR #58 passed its full PostgreSQL CI gate and was merged to `main`. The former dashboard and live-runner feature branches are now rollback/history branches, not product source-of-truth branches.

## Current production topology before Railway source reconciliation

Railway production still uses the previous source branches:

- `tennis-dashboard` -> `feat/tennis-client-dashboard`
- `tennis-live-runner` -> `feat/tennis-live-scheduled-runner`
- `tennis-validation-runner` -> `feat/tennis-live-scheduled-runner`

The connected Railway control surface can inspect these sources and update runtime configuration but does not currently expose an action for changing an existing service's GitHub branch. Do not simulate that operation by force-moving the old feature branch refs.

The manual Railway source change should therefore be limited to changing each service's branch selector to `main`, one service at a time, with the existing start command and rollback point preserved.

The live runner remains hourly with a bounded three-day horizon.

Known-good rollback points before Railway source reconciliation:

- dashboard: `c8a6cbb17a8055e9891e4567a088212aaaf2069d`
- live runner / validation lineage: `09f058f0512ecd95f8011a49fbb2b21af84022ea`

Do not delete either PostgreSQL service while database authority is being reconciled.

## Verified live baseline

The durable Type B build has already run successfully in production on the pre-canonical live-runner lineage.

A verified post-deploy live batch reported:

- 276 fixtures fetched across the bounded horizon
- 8 prediction targets
- 1 Manager consensus
- 16 full-slate price-scanner candidates
- 1 Pinnacle board request
- 62 normalized offered quotes across 12 bookmakers
- 1 Type A value-qualified BET
- 0 Type B signals on that scan
- Type B scan ledger persisted successfully with `scan_run_id=1`

A zero Type B result is valid evidence, not a failure. The scanner records the scan rather than lowering its threshold to manufacture output.

## Current live architecture

The bounded scheduled path is:

`API-Tennis fixtures -> canonical ingest -> Agent 1/2/3 -> Manager C1-C4 -> Pinnacle sharp normalization -> offered bookmaker odds -> Type A value evaluation -> kill-only Veto -> actionable Type A state`

In parallel:

`full canonical scheduled ATP/Challenger slate -> independent Type B price-dispersion evaluation -> append-only Type B scan/signal ledger`

Type A and Type B remain deliberately separate:

- Type A requires model/Manager eligibility and then evaluates value against market truth.
- Veto can only kill an otherwise-qualified Type A BET. It never creates or rescues one.
- Type B scans the full canonical in-scope scheduled slate independently of model consensus.
- Type B signals and zero-signal scans are persisted separately rather than fabricating Manager consensus rows.

## Veto evidence boundary

Veto V1 uses only evidence the current canonical store can support safely.

Supported checks include:

- current fixture is no longer scheduled
- fixture observation staleness
- retirement in a previous PIT-visible match
- first match after an absence of at least six weeks
- conservative tight-turnaround checks from canonical match timing
- five-set previous match with a short turnaround
- adverse sharp-price movement greater than the locked threshold when a genuine earlier baseline exists

The following are not fabricated when the source evidence is absent:

- injury report within 72 hours
- prior-match medical timeout
- exact previous-match end timestamp
- exact previous-match duration above 3h15
- any odds-move claim when no generation-time baseline exists

Unavailable checks are recorded as unavailable in the Veto audit layer rather than silently inferred.

## Owner authentication

Owner-only Tennis dashboard authentication is implemented but remains disabled by default.

Properties:

- isolated PostgreSQL schema: `sports_edge_auth`
- PBKDF2-SHA256 salted password hashes
- opaque hashed server-side session tokens
- session expiry
- HttpOnly / SameSite cookies; Secure by default
- explicit owner email allowlist
- public signup disabled by default
- no guest mode
- `/healthz` remains public
- no provider calls and no writes to canonical model/market tables from auth routes

Production enablement must follow `docs/TENNIS_DASHBOARD_AUTH.md` and requires the authorized owner email to be confirmed first.

## Remaining operational work

### 1. Railway source reconciliation

Change source branches one service at a time:

1. `tennis-dashboard` -> `main`
2. verify deploy + `/healthz` + read-only pages
3. `tennis-live-runner` -> `main`
4. verify the next bounded hourly execution
5. `tennis-validation-runner` -> `main`
6. verify its bounded command remains unchanged

Preserve existing start commands, schedule, environment variables, and rollback SHAs during these branch-selector changes.

### 2. PostgreSQL authority

Two PostgreSQL services are visible in Railway. `Postgres-Sdqz` exposes a 5 GB persistent volume through the current connector while `Postgres` does not expose a mounted volume in that view. This is useful topology evidence but is not sufficient evidence to delete either service.

The connected Railway OAuth surface redacts variable values/reference targets. Confirm which PostgreSQL service the three `TENNIS_DATABASE_URL` variables actually reference before retiring anything.

### 3. Closing-price / CLV evidence

The schema already contains `decision_clv`, but the project still needs a deliberately closed prospective closing-price capture and reporting lifecycle before making claims about durable live market edge.

## Historical validation baseline

The approved historical baseline contains:

- `117,713` matches
- `117,713` results
- `235,426` stat rows
- `4,277` players
- `617` tournaments

Agent 1's first genuine leakage-safe walk-forward validation used a `2016-2018` warm-up and `2019-2025` out-of-sample period. The completed-match evaluation sample was `77,458` matches with:

- log loss: `0.631662567958`
- Brier: `0.220795125669`
- accuracy: `0.642735417904`
- ECE: `0.025221101565`

Those are historical model-validation metrics, not proof of prospective profitability or live CLV.

## Hard stabilization rules

- do not loosen Agent, Manager, Type A, Type B, or Veto thresholds to increase output
- do not fabricate missing injury, MTO, duration, closing-price, or bookmaker evidence
- do not treat Manager consensus as a value bet unless Market truth qualifies it
- do not treat Type B dispersion as predictive-agent consensus
- do not enable owner auth until the owner bootstrap path is confirmed
- do not delete either PostgreSQL service until connection authority is proven
- do not delete or rewrite append-only historical, odds, decision, Veto, or Type B evidence
