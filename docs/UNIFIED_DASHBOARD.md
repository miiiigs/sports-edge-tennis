# Unified Sports Edge Dashboard

Status: client-facing UI integration only. This document contains no credentials.

## Architecture

```text
Sports Edge Unified UI (this repository, Tennis runtime)
|
+-- Tennis UI  -- existing Tennis backend + TENNIS_DATABASE_URL
|
+-- Football UI -- server-side read-only client
                  -- existing independent Football backend/runtime/database
```

Only the client-facing UI experience is merged. The Tennis and Football systems
remain operationally independent: no model code, database, worker, scheduler,
provider ingestion, staking, settlement or Railway runtime is merged. Tennis and
Football accounting and bankrolls are never combined.

## Routes

| Route | Purpose |
|---|---|
| `/` | Redirects to `/overview` |
| `/overview` | Launchpad with one independent card per sport |
| `/tennis` | Existing Tennis Today/Picks experience |
| `/tennis/performance` | Existing Tennis performance page |
| `/tennis/history` | Existing Tennis value-bet history |
| `/tennis/system` | Client-facing Tennis operational evidence |
| `/football` | Football Picks (Asian Handicap + Corners) |
| `/football/performance` | Football performance (independent accounts) |
| `/football/history` | Paginated settled Football bets |
| `/football/system` | Client-facing Football feed/pipeline status |

Existing Tennis URLs remain available and unchanged: `/today`, `/value`,
`/bet-history`, `/value-bet-results`, `/model-pick-results`, `/performance`,
`/history`.

## Football integration

* Module: `src/sports_edge_tennis/dashboard/football_client.py`.
* Transport: server-side `requests` GET only, bounded timeout, no retries.
* The browser never calls Football; no Football HTML is embedded or iframed.
* Raw Football JSON is adapted into explicit presentation view models before it
  reaches a template. The **model view** (probability, fair price) and the **bet
  decision** (qualifying price, edge, stake when available) stay separate.
* Failure is isolated: an unreachable, slow, erroring, unauthorised or malformed
  Football response renders a "Football temporarily unavailable" state. Tennis
  is unaffected and never returns a whole-site 500 because Football is down.

### Configuration

| Variable | Meaning |
|---|---|
| `SPORTS_EDGE_FOOTBALL_BASE_URL` | Football base URL. Empty/unset shows a clean "not configured" state. |
| `SPORTS_EDGE_CLIENT_API_TOKEN` | Optional read-only Bearer token (server-side only). |
| `SPORTS_EDGE_FOOTBALL_COOKIE` | Optional raw Cookie header for session-based auth. |
| `SPORTS_EDGE_FOOTBALL_TIMEOUT_SECONDS` | Request timeout (default 4.0s). |

Secrets and the Football base URL are never rendered into HTML or browser
JavaScript.

### Read-only allowlist

Only these GET paths may be requested: `/health`, `/api/system/status`,
`/api/candidates`, `/api/performance`, `/api/predictions`, `/api/analysis`,
`/api/fixtures/next`, `/api/fixtures/today`, `/api/fixtures/live`,
`/api/fixtures/upcoming`.

State-changing endpoints are never proxied, including
`POST /api/fixtures/refresh` and all Football auth POSTs.

### Authentication requirement (follow-up)

As of 2026-10-05 the Football production deployment has authentication enabled
(`GET /api/*` returns `401`; `/health` is public) and exposes no read-only API
token. There is no static server-to-server credential today. Providing a
read-only integration credential on the Football side - preferred model
`SPORTS_EDGE_CLIENT_API_TOKEN`, GET-only allowlist, no mutating/admin/refresh
routes - is a **Football-repository change** and is therefore out of scope for
this branch. Until it exists, the Football pages render the honest degraded
state while the rest of the unified UI works normally.
