# Project Status

Last updated: 2026-09-11

## Current milestone

`FINAL STABILIZATION / SOURCE-OF-TRUTH RECONCILIATION`

The Tennis implementation has moved substantially beyond the older Phase 2B status that previously lived in this file. The current objective is no longer to build the three predictive agents. It is to reconcile the production dashboard and live-runner lineages, close the remaining operational/spec gaps, and leave one maintainable canonical branch.

## Current implementation truth

```text
TENNIS REPOSITORY: ACTIVE
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
TYPE B DURABLE APPEND-ONLY LEDGER: IMPLEMENTED
PINNACLE SHARP REFERENCE / RECONCILIATION: IMPLEMENTED
OFFERED BOOKMAKER ODDS: IMPLEMENTED
CLIENT DASHBOARD: IMPLEMENTED
SCHEDULED LIVE RUNNER: IMPLEMENTED
LIVE MODEL CHECKPOINT / DELTA ADVANCEMENT: IMPLEMENTED

TENNIS VETO RUNTIME: NOT COMPLETE
TENNIS INJURY / MEDICAL-TIMEOUT FEED: NOT CONNECTED
TENNIS OWNER AUTHENTICATION: NOT IMPLEMENTED
CLOSING-PRICE / CLV LIFECYCLE: NOT YET CLOSED END-TO-END
CANONICAL MAIN RECONCILIATION: IN PROGRESS
```

## Production topology before final reconciliation

Production currently uses separate source branches:

- dashboard: `feat/tennis-client-dashboard`
- scheduled live runner: `feat/tennis-live-scheduled-runner`
- validation/history refresh runner: `feat/tennis-live-scheduled-runner`

The final stabilization candidate is:

- branch: `integration/final-stabilization-livebase-2026-09`
- PR: `#58`

That candidate starts from the current live-runner lineage and overlays only the dashboard files changed after the original live-runner branch point. This avoids forcing the divergent Git history together while preserving the current code from both production surfaces.

No Railway service should be retargeted to the integration branch until the combined CI gate is green and the candidate is independently reviewed.

## Current live architecture

The bounded scheduled path is:

`API-Tennis fixtures -> canonical ingest -> Agent 1/2/3 -> Manager C1-C4 -> Pinnacle sharp normalization -> offered bookmaker odds -> Type A value evaluation + independent Type B price-dispersion evaluation -> persisted dashboard truth`

Type A and Type B remain deliberately separate:

- Type A requires model/Manager eligibility and then evaluates value against market truth.
- Type B scans the full canonical in-scope scheduled ATP/Challenger slate independently of model consensus.
- Type B signals and zero-signal scans are persisted separately rather than fabricating Manager consensus rows.

## Remaining spec gaps

### 1. Kill-only Veto

The original build spec defines a Veto as a kill-only layer, never a fourth predictive vote. The full runtime is not yet implemented.

Specified triggers include:

- announced withdrawal, retirement, or walkover
- injury reported in the previous 72 hours
- retirement or medical timeout in the player's previous match
- first match back after an absence of at least six weeks
- previous match ending within 18 hours of the scheduled start

Some of those conditions can be derived from canonical match/result history, while injury and medical-timeout truth require a trustworthy timestamped source. Missing external evidence must fail closed rather than be inferred or fabricated.

### 2. Access control

The Tennis dashboard does not yet have the owner-only authentication layer already available in the Football product. Authentication should be added after the canonical branch is stabilized so it is implemented once against the final dashboard source.

### 3. Closing-price / CLV evidence

Current sharp/offered pricing infrastructure supports value decisions, but the project still needs a deliberately closed prospective closing-price capture and CLV reporting lifecycle before making claims about durable live market edge.

### 4. Canonical branch and runbooks

Before handoff, `main`, the production service sources, status documentation, migration/runbook instructions, and the authoritative PostgreSQL service must all point to one clearly documented source of truth.

## Railway operational state

The production Railway project contains:

- `tennis-dashboard`
- `tennis-live-runner`
- `tennis-validation-runner`
- PostgreSQL services

The live runner is scheduled hourly with a bounded three-day horizon. Provider retries/polling are intentionally bounded, and the production path contains overlap protection and incremental model-state caching to avoid repeated full-history replays.

A final infrastructure audit must document which PostgreSQL service is authoritative before any unused database service is removed. Do not delete either database merely because two services are visible.

## Historical validation baseline

The original approved Sackmann ATP corpus contains:

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

Those historical metrics remain model-validation evidence. They are not proof of prospective profitability or live CLV.

## Hard stabilization rules

Until the final stabilization work is closed:

- do not loosen Agent, Manager, Type A, or Type B thresholds to increase pick volume
- do not fabricate missing injury, medical-timeout, closing-price, or bookmaker evidence
- do not treat a Manager consensus as a value bet unless Market truth qualifies it
- do not treat a Type B price-dispersion signal as predictive-agent consensus
- do not retarget Railway services to a branch that has not passed its CI/review gate
- do not delete or rewrite append-only historical, odds, decision, or Type B evidence
