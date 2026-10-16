# Project Status

Last updated: 2026-09-21

## 2026-09-19 provider-cutover truth

This section supersedes older provider/topology statements below where they conflict.

Current production remains deliberately pinned to the legacy authority while the target cutover is validated:

```text
PRODUCTION DATA AUTHORITY: API_TENNIS
PRODUCTION ODDS AUTHORITY: LEGACY_SPLIT (PinnAPI sharp + API-Tennis offered)
PRODUCTION MANAGER POLICY: tennis_manager_consensus/v1
TARGET DATA AUTHORITY: SPORTRADAR_V3
TARGET ODDS AUTHORITY: ODDSPAPI_TENNIS
TARGET MANAGER POLICY: tennis_manager_consensus/v2 (strict active 3/3)
TARGET ACTIVATION: BLOCKED / NOT YET AUTHORIZED
```

Railway `tennis-live-runner`, `tennis-validation-runner`, and `tennis-dashboard` are now sourced from canonical `main`. The live runner remains hourly and the validation runner remains daily at 00:10 UTC. The live and validation services are explicitly pinned with `TENNIS_DATA_PROVIDER_MODE=API_TENNIS`, `TENNIS_ODDS_PROVIDER_MODE=LEGACY_SPLIT`, and `TENNIS_MANAGER_POLICY_VERSION=v1`.

> 2026-10-15 audit correction: `tennis-validation-runner` is scheduled `10 8 * * *` (08:10 UTC), not 00:10; no run occurred on 2026-10-11 while the schedule changed (the checkpoint window covered that day on 2026-10-12). `tennis-live-runner` odds mode is now `ODDSPAPI_TENNIS` (since 2026-10-14 08:45Z). Manager remains `v1`, which accepts an Agent 3 abstention: every production BET to date is 2 active + 1 abstaining. See `docs/research/TENNIS_PRODUCTION_AUDIT_20261015.md`.

PR #162 prepares, but does not activate, the target stack. It includes:

- strict Manager v2 while preserving immutable v1 replay;
- paired provider-authority gates that forbid a half-cutover;
- fail-closed Sportradar fixture parsing and exact cross-provider match/player reconciliation;
- overlap-safe Sportradar canonical ingestion that prevents duplicate match creation;
- OddsPapi fixture/player reconciliation plus provider-neutral odds normalization, preferring its Betradar/Sportradar bridge where available;
- explicit OddsPapi mappings for `pinnacle+30` -> SHARP/PINNACLE and `1xbet` / `unibet` / `bet365` -> OFFERED;
- a dormant target live path that forbids legacy provider calls when target mode is selected;
- bounded Sportradar terminal-result and final serve/return-stat refresh;
- a provider-aware history-refresh dispatcher.

The previously planned Goalserve odds target is superseded by the purchased OddsPapi Tennis plan. PR #169 prepares the replacement in shadow mode. Target activation remains fail-closed until the live OddsPapi account is shadow-validated for fixture coverage, identity orientation, Match Winner market `121`, Pinnacle +30-second price parity, offered-book coverage, and request-budget behavior; the latest PostgreSQL suite must also be green, migration 0014 verified in production, and the cutover runbook executed deliberately.

The verified API-Tennis historical Agent 2 regime remains append-only historical evidence. Sportradar evidence is prospective/provider-versioned and must not rewrite source observation timestamps or reinterpret prior v1 Manager decisions.
## Current milestone

`PRODUCTION SOURCE HANDOFF + PROSPECTIVE CLV ACTIVATION`

The final combined Tennis implementation is now canonical on `main`. The predictive/model build, Git source-of-truth reconciliation, Veto, owner-auth code, and prospective closing-price / CLV lifecycle are complete in source. Remaining work is operational: point Railway services at canonical `main`, confirm database authority, leave CLV activation unset until that handoff is verified, and enable owner authentication only after the owner bootstrap is ready.

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

PROSPECTIVE CLOSING-PRICE EVIDENCE: IMPLEMENTED
TYPE A CLV CALCULATION / REPORTING: IMPLEMENTED
CLV PROSPECTIVE ACTIVATION: NOT YET ENABLED IN RAILWAY

RAILWAY SOURCE RECONCILIATION: PENDING MANUAL BRANCH-SELECTOR CHANGES
AUTHORITATIVE POSTGRES SERVICE: MUST BE CONFIRMED BEFORE ANY DB RETIREMENT
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

The final stabilization candidate PR #58 passed its full PostgreSQL CI gate and was merged to `main`. The prospective closing-price / CLV lifecycle then passed the full repository PostgreSQL gate and was merged through PR #65 at merge commit `5f3e76f845e7532c1703bc203b03883c96937df8`. The former dashboard and live-runner feature branches are now rollback/history branches, not product source-of-truth branches.

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

After Type A decisions exist, the hourly runner can also perform a bounded prospective close-finalization pass when `TENNIS_CLV_ACTIVATION_AT` is explicitly configured. That pass reuses already-persisted odds snapshots, waits at least two hours after scheduled start, selects genuine pre-start closing evidence, and does not add provider polling.

Type A and Type B remain deliberately separate:

- Type A requires model/Manager eligibility and then evaluates value against market truth.
- Veto can only kill an otherwise-qualified Type A BET. It never creates or rescues one.
- Type B scans the full canonical in-scope scheduled slate independently of model consensus.
- Type B signals and zero-signal scans are persisted separately rather than fabricating Manager consensus rows.
- Prospective CLV V1 applies to Type A decisions only and does not silently relabel Type B output.

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

## Prospective closing-price / CLV lifecycle

The source implementation is complete and merged, but prospective capture is deliberately not activated in Railway yet.

Key properties:

- explicit activation boundary via `TENNIS_CLV_ACTIVATION_AT`
- no activation value means no CLV ledger creation/finalization work from the live runner
- one append-only `decision_close_evidence` record per eligible Type A decision
- BET/VETOED and NO_BET decisions can all receive genuine closing-market evidence
- NO_BET rows do not receive invented selected sides or fake prices
- executable CLV requires real offered-price lineage plus a complete compatible same-book pre-start close
- sharp close remains reference evidence
- post-start/in-play observations are rejected as closing prices
- two-hour finalization delay protects schedule/status corrections without changing the close cutoff
- repeated hourly finalization is idempotent
- CLV margin/booksum arithmetic is canonicalized to the persisted 8-decimal precision
- dashboard reporting exposes close coverage, priced-decision CLV coverage, missing/partial counts, and mean/median CLV when available

This is evidence infrastructure, not proof of profitability.

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

### 3. Prospective CLV activation

Do not set `TENNIS_CLV_ACTIVATION_AT` until the dashboard/live/validation services have been deliberately reconciled to canonical `main` and the authoritative database reference has been confirmed.

When those gates are complete, set one explicit timezone-aware ISO-8601 activation timestamp on the live runner. That timestamp is the prospective boundary: only Type A decisions created at or after it are eligible. Do not move the boundary backward to manufacture historical prospective evidence.

After activation, verify the first eligible finalization through the live-runner `CLV_CLOSE` log and the read-only dashboard/API coverage reporting before interpreting any CLV sample.

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
- do not activate prospective CLV until canonical Railway source and database authority are verified
- do not delete either PostgreSQL service until connection authority is proven
- do not delete or rewrite append-only historical, odds, decision, Veto, Type B, or closing-line evidence
