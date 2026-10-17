# Blockers

This log records current blockers. Append-only.

## BLK-1

No tennis historical data pipeline exists yet (Sackmann + odds archive ingestion). This
blocks almost all Week 3 tennis modeling work. Scheduled for a later controlled phase.

## BLK-2

Tennis database is not provisioned. Database design/provisioning is a separate controlled
phase and must not be performed during repository bootstrap.

## BLK-3

Live sharp-reference provider is unresolved. Blocked until a later scoped task.

## BLK-4

No disposable local PostgreSQL runtime was available in this task environment (`psql`
missing; Docker daemon unavailable). The schema foundation was therefore validated
statically rather than by applying migrations to an empty local database.
Superseded on 2026-08-24 by a committed GitHub Actions PostgreSQL 16 validation gate;
local disposable runtime still remains unavailable in this workstation environment.

## BLK-5

BLK-1 is now partially cleared for Agent 1 historical validation. A local-only Sackmann ATP
results pipeline exists for acquisition verification, dry-run parsing, disposable PostgreSQL
ingest, PIT boundary checks, and Agent 1 extractor probing.

Historical odds ingestion is still not implemented, so broader Week 3 modeling and any
market-aware validation remain blocked on a later controlled phase.

## BLK-6

BLK-4 is cleared inside the dedicated historical-validation worktree. A local PostgreSQL 16
runtime is now available for disposable validation runs, and committed migrations `0001`
through `0005` were applied successfully during the historical ingest verification flow.

This does not change the production authorization boundary or convert database provisioning
into an open-ended task.

## BLK-7

Unified Sports Edge dashboard production authentication is not yet verified.

The unified routes (`/overview`, `/tennis*`, `/football*`) are registered on the single
shared FastAPI `app` object and are compatible with the `owner_access` middleware, but
they are only actually protected when the deployed process is started through the
`owner_access` wrapper *and* `TENNIS_DASHBOARD_AUTH_ENABLED` is enabled.

The repository `Procfile` currently starts the ordinary
`sports_edge_tennis.dashboard.app:app` entrypoint, which does **not** apply the owner guard.
"New routes inherit auth" therefore means compatibility, not production protection.

Before Football integration credentials or live Football data are exposed through the
unified Tennis host, the production start command and auth configuration must be explicitly
verified/enabled. This is a deployment blocker and is intentionally out of scope for the
unified-dashboard implementation PR; Railway is not modified here.
