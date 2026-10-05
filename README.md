# Sports Edge Tennis

Independent men's tennis probability and pricing platform.

## Purpose

An independent men's tennis probability and pricing platform, developed in a dedicated
repository, runtime, and database, fully separated from the football platform.

## Current State

PostgreSQL schema foundation, approved 2016-2025 historical corpus acquisition/local ingest/PIT
validation, Agent 1 Surface Elo implementation, and the first genuine 2019-2025 out-of-sample
walk-forward validation are now committed in-repo. Independent quantitative review is still
required before merge or any stronger acceptance claim.

## Not Yet Implemented

- historical ingest
- serve/return
- Markov
- intransitivity
- manager
- veto
- price scanner
- paper mode
- production

## Development Principle

```text
Understand
-> Investigate
-> Implement
-> Validate
-> Independent Review
-> Git
-> Release Safety
```

Unfinished functionality is not marketed as complete.

## Unified dashboard auth boundary

The shared Tennis/Football shell added under `dashboard/unified_routes.py`
(`/overview`, `/tennis*`, `/football*`) is registered on the same FastAPI `app`
object, so it is *compatible* with the `owner_access` middleware. It is only
actually protected when the deployed process is started through the
`owner_access` wrapper with `TENNIS_DASHBOARD_AUTH_ENABLED` enabled.

The repository `Procfile` starts the ordinary `dashboard.app:app` entrypoint,
which does not apply the owner guard. "The new routes inherit auth" therefore
means compatibility, **not** that production is protected. Enabling and proving
dashboard authentication on the deployed start command is tracked as a separate
deployment blocker (`docs/BLOCKERS.md`, BLK-7) and must be done before Football
credentials or live Football data are exposed through this host.

The Football read-only token (`SPORTS_EDGE_CLIENT_API_TOKEN`) is not implemented
in this repository. The `SPORTS_EDGE_FOOTBALL_COOKIE` fallback is transitional
and testing-only; it must not become the production integration mechanism.

## Layout

```text
specs/   canonical requirement artifacts and source manifest
docs/    governance, status, architecture, traceability
db/      PostgreSQL migrations, schema snapshot, and database foundation docs
src/sports_edge_tennis/   package source
tests/   isolation, architecture, and schema-foundation tests
```
